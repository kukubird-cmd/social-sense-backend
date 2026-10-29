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

            def is_relevant(text_to_check: str, kw_str: str) -> bool:
                t_lower = (text_to_check or "").lower()
                k_clean = kw_str.replace("#", "").strip().lower()
                if not k_clean or not t_lower:
                    return False
                if k_clean in t_lower:
                    return True
                stop_words = {"the", "a", "an", "in", "at", "on", "of", "and", "to", "for", "with", "by", "is", "my", "your"}
                tokens = [t for t in k_clean.split() if t not in stop_words and len(t) >= 2]
                if not tokens:
                    return True
                if len(tokens) == 1:
                    return tokens[0] in t_lower
                if len(tokens) >= 2:
                    if f"{tokens[0]} {tokens[1]}" in t_lower:
                        return True
                matches = sum(1 for t in tokens if t in t_lower)
                return matches >= (1 if len(tokens) <= 2 else 2)

            # 3. Parse & filter for genuine relevance
            scraped_records = []
            irrelevant_count = 0
            for item in raw_items:
                try:
                    # Check parent post context for relevance
                    parent_text = (
                        str(item.get("text") or item.get("desc") or item.get("title") or item.get("caption") or "")
                    )
                    parent_is_relevant = is_relevant(parent_text, keyword.keyword_string)
                    
                    records = parse_apify_item_to_records(item, keyword_id)
                    for r in records:
                        # YouTube comment scrapers target videos matching the keyword query directly.
                        # Viewers rarely repeat the full keyword in short reactions ("Subscribed!", "Where is this?").
                        # Keep comments from keyword-targeted YouTube scraper runs, or any post/comment matching the keyword.
                        is_targeted_comment = (
                            r.platform == "youtube"
                            or item.get("datasetType") == "comment"
                            or "videoId" in item
                            or "commentId" in item
                        )
                        if is_targeted_comment or parent_is_relevant or is_relevant(r.comment_text, keyword.keyword_string):
                            scraped_records.append(r)
                        else:
                            irrelevant_count += 1
                except Exception as e:
                    logger.error(f"Error parsing item in dataset {dataset_id}: {e}")

            if irrelevant_count:
                logger.info(f"Filtered out {irrelevant_count} completely irrelevant algorithmic noise posts for keyword '{keyword.keyword_string}'")

            logger.info(f"Parsed {len(scraped_records)} relevant records from Apify dataset {dataset_id}")

            # 4. Multi-Comment Preserving Deduplication
            # Deduplicate by (post_url, comment_text) so multiple comments on the same post are NEVER dropped
            if scraped_records:
                from sqlalchemy import select as sa_select
                existing_keys_result = await db.execute(
                    sa_select(ScrapedData.post_url, ScrapedData.comment_text).where(ScrapedData.keyword_id == keyword_id)
                )
                existing_keys = {(row[0], (row[1] or "").strip()) for row in existing_keys_result.fetchall()}

                seen_batch = set()
                new_records = []
                for r in scraped_records:
                    key = (r.post_url, (r.comment_text or "").strip())
                    if key not in existing_keys and key not in seen_batch:
                        seen_batch.add(key)
                        new_records.append(r)

                duplicate_count = len(scraped_records) - len(new_records)
                if duplicate_count:
                    logger.info(f"Skipped {duplicate_count} duplicate items already in DB")
                scraped_records = new_records

            # 5. Save records to PostgreSQL
            if scraped_records:
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
