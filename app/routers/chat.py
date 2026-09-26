import logging
import uuid
from typing import Optional
from pydantic import BaseModel
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.database import get_db
from app.models import ScrapedData, Keyword, Company
from app.services.gemini_service import chat_with_scraped_data, translate_and_summarize_post, call_gemini_api

logger = logging.getLogger("ai_chat")
router = APIRouter(prefix="/api/chat", tags=["AI Social Analyst Chatbot"])


class ChatRequest(BaseModel):
    message: str
    company_id: Optional[str] = None
    keyword_id: Optional[str] = None
    topic_context: Optional[str] = None


class TranslateRequest(BaseModel):
    text: str
    keyword: Optional[str] = "Brand Monitoring"


@router.post("", summary="Ask Gemini questions grounded on the scraped social media database")
async def chat_with_database(req: ChatRequest, db: AsyncSession = Depends(get_db)):
    """
    Retrieves recent scraped posts from the database and sends them to Gemini 
    as grounded context to provide intelligent, data-backed answers with topic disambiguation.
    """
    if not req.message.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, 
            detail="Message cannot be empty."
        )

    # 1. Detect platform intent from user prompt (e.g. "on ig", "on youtube", "tiktok")
    msg_lower = req.message.lower()
    target_platform = None
    if any(term in msg_lower for term in ["instagram", " on ig", " in ig", " ig ", "(ig)", "insta", "ig:"]):
        target_platform = "instagram"
    elif any(term in msg_lower for term in ["youtube", " on yt", " in yt", " yt ", "(yt)", "yt:"]):
        target_platform = "youtube"
    elif any(term in msg_lower for term in ["tiktok", " on tt", " in tt", " tt ", "(tt)", "tt:"]):
        target_platform = "tiktok"
    elif "reddit" in msg_lower:
        target_platform = "reddit"
    elif any(term in msg_lower for term in ["twitter", "on x", "in x", "tweets", "tweet", "x.com"]):
        target_platform = "twitter"

    # 2. Fetch scraped posts from database (strictly isolated by company)
    stmt = (
        select(ScrapedData, Keyword.keyword_string, Keyword.topic_context)
        .join(Keyword, ScrapedData.keyword_id == Keyword.id)
        .order_by(ScrapedData.timestamp.desc())
        .limit(1000)
    )

    if req.company_id:
        try:
            comp_uuid = uuid.UUID(req.company_id)
            stmt = stmt.where(Keyword.company_id == comp_uuid)
        except ValueError:
            pass

    active_keyword_str = None
    active_topic_context = (req.topic_context or "").strip() or None

    if req.keyword_id:
        try:
            kw_uuid = uuid.UUID(req.keyword_id)
            stmt = stmt.where(ScrapedData.keyword_id == kw_uuid)
            kw_stmt = select(Keyword).where(Keyword.id == kw_uuid)
            kw_res = await db.execute(kw_stmt)
            kw_obj = kw_res.scalar_one_or_none()
            if kw_obj:
                active_keyword_str = kw_obj.keyword_string
                if not active_topic_context and kw_obj.topic_context:
                    active_topic_context = kw_obj.topic_context.strip()
        except ValueError:
            pass

    result = await db.execute(stmt)
    all_fetched = []
    for scraped, kw_str, kw_topic in result.all():
        if not active_keyword_str:
            active_keyword_str = kw_str
        if not active_topic_context and kw_topic:
            active_topic_context = kw_topic.strip()
        d = scraped.to_dict()
        d["keyword_string"] = kw_str
        all_fetched.append(d)

    # 3. Strict Keyword Isolation: Ensure comments NEVER mix across different keywords
    if not req.keyword_id:
        matched_kw = None
        for item in all_fetched:
            kw_name = item.get("keyword_string", "").replace("#", "").strip().lower()
            if kw_name and len(kw_name) >= 3 and kw_name in msg_lower:
                matched_kw = item.get("keyword_string")
                break
        
        target_kw = matched_kw or active_keyword_str
        if target_kw:
            active_keyword_str = target_kw
            all_fetched = [r for r in all_fetched if r.get("keyword_string", "").lower() == target_kw.lower()]

    if not all_fetched:
        return {
            "reply": "No scraped social media posts were found in the database yet. Please run a scrape using the Scraper tab to collect live posts first!",
            "referenced_records_count": 0,
            "total_records_available": 0,
            "topic_options": []
        }

    # 4. Filter by platform if user specifically requested one
    if target_platform:
        platform_records = [r for r in all_fetched if r.get("platform") == target_platform]
        if not platform_records:
            available_platforms = sorted(list(set(r.get("platform", "") for r in all_fetched if r.get("platform"))))
            return {
                "reply": (
                    f"### No {target_platform.capitalize()} Data Found\n\n"
                    f"I searched the live database for **{active_keyword_str or 'your monitored keyword'}** on **{target_platform.capitalize()}**, "
                    f"but no {target_platform.capitalize()} posts have been scraped yet.\n\n"
                    f"**Available platforms in database:** {', '.join(p.capitalize() for p in available_platforms)}.\n\n"
                    f"*Tip:* Head over to the **Keywords & Scraper** tab to run an active scrape for {target_platform.capitalize()}!"
                ),
                "referenced_records_count": 0,
                "total_records_available": len(all_fetched),
                "topic_options": []
            }
        records = platform_records[:80]
    else:
        # Balanced stratified sampling across all available platforms
        by_platform: dict = {}
        for r in all_fetched:
            p = r.get("platform", "unknown")
            by_platform.setdefault(p, []).append(r)
        
        records = []
        for p, plist in by_platform.items():
            records.extend(plist[:16])  # up to 16 per platform = max 80 total evenly spread

    # 2. Call Gemini with the scraped records as context and active topic context
    try:
        response = await chat_with_scraped_data(
            user_query=req.message,
            scraped_records=records,
            keyword_filter=active_keyword_str,
            topic_context=active_topic_context
        )
        return response
    except Exception as e:
        logger.error(f"Gemini Chatbot Error: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Gemini AI processing error: {str(e)}"
        )


@router.post("/translate", summary="Translate a social media comment to English and analyze sentiment")
async def translate_comment(req: TranslateRequest):
    """
    Cleans up HTML entities, translates non-English text to English,
    and analyzes genuine sentiment using Gemini.
    """
    try:
        res = await translate_and_summarize_post(req.text, req.keyword or "Brand Monitoring")
        return res
    except Exception as e:
        logger.error(f"Translation Error: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Translation error: {str(e)}"
        )


@router.get("/health", summary="Check Gemini API status")
async def gemini_health():
    """Validates connectivity to Gemini AI models."""
    try:
        test_reply = await call_gemini_api("Say 'Connected' in one word.")
        return {
            "status": "online",
            "model_connected": True,
            "response_sample": test_reply.strip()
        }
    except Exception as e:
        return {
            "status": "error",
            "model_connected": False,
            "error": str(e)
        }
