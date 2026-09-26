import uuid
import logging
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query, BackgroundTasks, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.database import get_db, AsyncSessionLocal
from app.models import Keyword, ScrapedData
from app.schemas import ApifyWebhookPayload
from app.services.apify_service import fetch_apify_dataset_items, parse_apify_record, parse_apify_item_to_records
from app.websocket_manager import ws_manager

logger = logging.getLogger("apify_webhook")
router = APIRouter(prefix="/api/webhooks", tags=["Apify Webhooks"])


async def process_apify_dataset_task(dataset_id: str, keyword_id: uuid.UUID):
    """
    Background Task:
    1. Downloads items from Apify Dataset API.
    2. Runs sentiment and crisis classification.
    3. Persists records to PostgreSQL database.
    4. Broadcasts new entries in real-time to the company's WebSocket dashboard.
    """
    async with AsyncSessionLocal() as db:
        try:
            # 1. Fetch the associated keyword and company_id
            stmt = select(Keyword).where(Keyword.id == keyword_id)
            result = await db.execute(stmt)
            keyword = result.scalar_one_or_none()

            if not keyword:
                logger.error(f"Cannot process Apify dataset {dataset_id}: Keyword {keyword_id} not found.")
                return

            company_id_str = str(keyword.company_id)

            # 2. Fetch raw dataset items from Apify
            raw_items = await fetch_apify_dataset_items(dataset_id)
            logger.info(f"Fetched {len(raw_items)} items from Apify dataset {dataset_id} for keyword '{keyword.keyword_string}'")

            if not raw_items:
                logger.warning(f"Apify dataset {dataset_id} returned 0 items.")
                return

            # 3. Parse & run sentiment analysis
            scraped_records = []
            for item in raw_items:
                try:
                    records = parse_apify_item_to_records(item, keyword_id)
                    scraped_records.extend(records)
                except Exception as e:
                    logger.error(f"Error parsing item in dataset {dataset_id}: {e}")

            # 4. Save records to PostgreSQL
            db.add_all(scraped_records)
            await db.commit()

            # Refresh records for ID generation
            for r in scraped_records:
                await db.refresh(r)

            logger.info(f"Saved {len(scraped_records)} new records to database for company {company_id_str}")

            # 5. Instant Dashboard Broadcast via WebSockets
            broadcast_payload = {
                "event": "NEW_SCRAPED_DATA",
                "timestamp": str(uuid.uuid1()),
                "company_id": company_id_str,
                "keyword_id": str(keyword.id),
                "keyword_string": keyword.keyword_string,
                "records_count": len(scraped_records),
                "crisis_count": sum(1 for r in scraped_records if r.sentiment_score == "Crisis"),
                "records": [r.to_dict() for r in scraped_records]
            }

            await ws_manager.broadcast_to_company(
                company_id=company_id_str,
                message=broadcast_payload
            )

        except Exception as e:
            logger.exception(f"Unhandled failure in Apify background task for dataset {dataset_id}: {e}")


@router.post(
    "/apify",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Receive incoming Apify scraper run webhooks"
)
async def handle_apify_webhook(
    payload: ApifyWebhookPayload,
    background_tasks: BackgroundTasks,
    keyword_id: uuid.UUID = Query(..., description="UUID of the keyword being tracked")
):
    """
    Webhook Endpoint called by Apify when an actor run finishes.
    
    Query Parameter:
    - `keyword_id`: UUID of the tracked keyword in our database.
    
    Dispatches asynchronous background ingestion to respond to Apify within 100ms.
    """
    dataset_id = payload.eventData.defaultDatasetId
    if not dataset_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Payload missing defaultDatasetId."
        )

    logger.info(f"Received Apify webhook for keyword {keyword_id} with dataset {dataset_id}")

    # Offload heavy dataset download & processing to background task
    background_tasks.add_task(
        process_apify_dataset_task,
        dataset_id=dataset_id,
        keyword_id=keyword_id
    )

    return {
        "status": "accepted",
        "message": "Scraper payload queued for parsing and real-time broadcast.",
        "dataset_id": dataset_id,
        "keyword_id": str(keyword_id)
    }
