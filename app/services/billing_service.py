import uuid
from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from app.models import Company, Keyword
from app.schemas import KeywordCreate


# TEST_MODE_UNLIMITED closed for Production B2B mode
TEST_MODE_UNLIMITED = False


async def create_keyword_for_company(
    db: AsyncSession, 
    company_id: uuid.UUID, 
    keyword_in: KeywordCreate
) -> Keyword:
    """
    Enforces Keyword-Based Billing Tier Logic:
    1. Validates that the company exists and has an active billing status.
    2. Checks if keyword already exists for this company and returns it.
    3. Counts current active keywords for this company.
    4. Raises HTTP 403 Forbidden if count >= company.max_keywords (unless in TEST_MODE_UNLIMITED).
    5. Otherwise, creates and returns the new Keyword.
    """
    # 1. Fetch Company
    stmt = select(Company).where(Company.id == company_id)
    result = await db.execute(stmt)
    company = result.scalar_one_or_none()

    if not company:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Company with ID {company_id} does not exist."
        )

    if company.billing_status.lower() != "active":
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail=f"Company billing status is '{company.billing_status}'. Please update payment details."
        )

    # 2. Check if keyword already exists for this company (case-insensitive)
    clean_keyword = keyword_in.keyword_string.strip()
    existing_stmt = select(Keyword).where(
        Keyword.company_id == company_id,
        func.lower(Keyword.keyword_string) == clean_keyword.lower()
    )
    existing_res = await db.execute(existing_stmt)
    existing_kw = existing_res.scalar_one_or_none()

    if existing_kw:
        if keyword_in.topic_context is not None and keyword_in.topic_context.strip():
            existing_kw.topic_context = keyword_in.topic_context.strip()
        if not existing_kw.is_active:
            existing_kw.is_active = True
        await db.commit()
        await db.refresh(existing_kw)
        return existing_kw

    # 3. Count existing active keywords
    count_stmt = select(func.count(Keyword.id)).where(
        Keyword.company_id == company_id,
        Keyword.is_active.is_(True)
    )
    count_result = await db.execute(count_stmt)
    active_count = count_result.scalar_one()

    # 4. Enforce Limit (Testing Mode: bypassed when TEST_MODE_UNLIMITED is True)
    if not TEST_MODE_UNLIMITED and active_count >= company.max_keywords:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"Keyword quota limit reached ({active_count}/{company.max_keywords} active keywords). "
                f"Please upgrade your subscription tier to monitor additional keywords."
            )
        )

    # 5. Insert new Keyword
    new_keyword = Keyword(
        company_id=company_id,
        keyword_string=clean_keyword,
        topic_context=(keyword_in.topic_context or "").strip(),
        platform_flags=keyword_in.platform_flags.model_dump() if keyword_in.platform_flags else {
            "tiktok": True,
            "instagram": True,
            "twitter": True,
            "reddit": True,
            "youtube": True
        },
        is_active=True
    )

    db.add(new_keyword)
    await db.commit()
    await db.refresh(new_keyword)

    return new_keyword
