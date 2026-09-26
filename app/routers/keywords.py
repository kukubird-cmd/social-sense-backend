import uuid
from typing import List
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.database import get_db
from app.models import Keyword, Company, ScrapedData
from app.schemas import KeywordCreate, KeywordResponse, KeywordUpdate
from app.services.billing_service import create_keyword_for_company

router = APIRouter(prefix="/api/companies", tags=["Keywords & Billing"])


@router.post(
    "/{company_id}/keywords", 
    response_model=KeywordResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Add a new tracked keyword with billing limit check"
)
async def add_keyword(
    company_id: uuid.UUID,
    keyword_in: KeywordCreate,
    db: AsyncSession = Depends(get_db)
):
    """
    Creates a new tracked keyword for a company.
    Enforces maximum active keywords tier limit.
    If company active keywords >= max_keywords, returns HTTP 403 Forbidden.
    """
    new_keyword = await create_keyword_for_company(db, company_id, keyword_in)
    return new_keyword


@router.get(
    "/{company_id}/keywords", 
    response_model=List[KeywordResponse],
    summary="List all tracked keywords for a company"
)
async def list_company_keywords(
    company_id: uuid.UUID,
    db: AsyncSession = Depends(get_db)
):
    stmt = select(Keyword).where(
        Keyword.company_id == company_id, 
        Keyword.is_active.is_(True)
    ).order_by(Keyword.created_at.desc())
    
    result = await db.execute(stmt)
    keywords = result.scalars().all()
    return keywords


@router.patch(
    "/keywords/{keyword_id}",
    response_model=KeywordResponse,
    summary="Update keyword properties (such as topic context or platform flags)"
)
@router.patch(
    "/{company_id}/keywords/{keyword_id}",
    response_model=KeywordResponse,
    summary="Update keyword properties for a company"
)
async def update_keyword(
    keyword_id: uuid.UUID,
    update_data: KeywordUpdate,
    company_id: uuid.UUID = None,
    db: AsyncSession = Depends(get_db)
):
    stmt = select(Keyword).where(Keyword.id == keyword_id)
    if company_id:
        stmt = stmt.where(Keyword.company_id == company_id)
    result = await db.execute(stmt)
    keyword = result.scalar_one_or_none()

    if not keyword:
        raise HTTPException(status_code=404, detail="Keyword not found.")

    if update_data.topic_context is not None:
        keyword.topic_context = update_data.topic_context.strip()
    if update_data.is_active is not None:
        keyword.is_active = update_data.is_active
    if update_data.platform_flags is not None:
        keyword.platform_flags = update_data.platform_flags.model_dump()

    await db.commit()
    await db.refresh(keyword)
    return keyword


@router.delete(
    "/keywords/{keyword_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Deactivate a keyword"
)
@router.delete(
    "/{company_id}/keywords/{keyword_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Deactivate a keyword for a company"
)
async def delete_keyword(
    keyword_id: uuid.UUID,
    company_id: uuid.UUID = None,
    db: AsyncSession = Depends(get_db)
):
    stmt = select(Keyword).where(Keyword.id == keyword_id)
    result = await db.execute(stmt)
    keyword = result.scalar_one_or_none()

    if not keyword:
        raise HTTPException(status_code=404, detail="Keyword not found.")

    keyword.is_active = False
    await db.commit()
    return None


@router.get(
    "/{company_id}/quota",
    summary="Get company quota and billing info"
)
async def get_company_quota(
    company_id: uuid.UUID,
    db: AsyncSession = Depends(get_db)
):
    stmt = select(Company).where(Company.id == company_id)
    result = await db.execute(stmt)
    company = result.scalar_one_or_none()
    if not company:
        raise HTTPException(status_code=404, detail="Company not found.")

    kw_stmt = select(Keyword).where(Keyword.company_id == company_id, Keyword.is_active.is_(True))
    kw_res = await db.execute(kw_stmt)
    active_kws = kw_res.scalars().all()

    return {
        "company_id": str(company.id),
        "name": company.name,
        "max_keywords": company.max_keywords,
        "active_count": len(active_kws),
        "billing_status": company.billing_status,
        "test_mode_unlimited": False
    }


@router.get(
    "/{company_id}/scraped-data",
    summary="Get scraped social media posts for a company or specific keyword"
)
async def list_company_scraped_data(
    company_id: uuid.UUID,
    keyword_id: uuid.UUID = None,
    limit: int = 1000,
    db: AsyncSession = Depends(get_db)
):
    """
    Returns real social media data scraped by Apify for this company's keywords.
    When keyword_id is provided, strictly isolates records to that keyword only.
    Includes platform, original post URL, comment/text, sentiment, metrics, and timestamp.
    """
    stmt = (
        select(ScrapedData, Keyword.keyword_string)
        .join(Keyword, ScrapedData.keyword_id == Keyword.id)
        .where(Keyword.company_id == company_id)
    )
    if keyword_id:
        stmt = stmt.where(ScrapedData.keyword_id == keyword_id)

    stmt = stmt.order_by(ScrapedData.timestamp.desc()).limit(limit)
    result = await db.execute(stmt)
    records = []
    for scraped, kw_str in result.all():
        d = scraped.to_dict()
        d["keyword_string"] = kw_str
        records.append(d)
    return records
