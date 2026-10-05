import uuid
import logging
from typing import Optional, List
from pydantic import BaseModel
from fastapi import APIRouter, Depends, HTTPException, status, Header
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models import Company, User, Keyword
from app.routers.auth import hash_pw

logger = logging.getLogger("admin")
router = APIRouter(prefix="/api/admin", tags=["Admin & Tenant Monitor"])

# Default admin master key (can be overridden via environment variable)
ADMIN_SECRET_KEY = "admin2026!"


def verify_admin(x_admin_key: Optional[str] = Header(None)):
    """Verifies that the caller possesses the admin master password."""
    import os
    expected_key = os.getenv("ADMIN_MASTER_KEY", ADMIN_SECRET_KEY)
    if not x_admin_key or x_admin_key.strip() != expected_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Admin Master Key. Access denied."
        )
    return True


class AdminProvisionRequest(BaseModel):
    company_name: str
    email: str
    password: str
    bought_keyword: str
    topic_context: Optional[str] = ""
    max_keywords: Optional[int] = 1


class AdminUpdateStatusRequest(BaseModel):
    billing_status: str # "active", "suspended", "canceled"


@router.post("/verify", summary="Verify Admin Master Password")
async def verify_admin_login(is_admin: bool = Depends(verify_admin)):
    return {"status": "authenticated", "message": "Admin authorization verified."}


@router.get("/clients", summary="List all client workspaces (Zero-Knowledge: Excludes all scraped posts & data)")
async def list_clients(
    is_admin: bool = Depends(verify_admin),
    db: AsyncSession = Depends(get_db)
):
    """
    Returns only company tenant metadata and assigned keywords.
    PRIVACY GUARANTEE: Does NOT fetch or return any scraped social posts, 
    competitor data, or private user analytics.
    """
    stmt = (
        select(Company)
        .options(
            selectinload(Company.users),
            selectinload(Company.keywords)
        )
        .order_by(Company.created_at.desc())
    )
    result = await db.execute(stmt)
    companies = result.scalars().all()

    clients_list = []
    for comp in companies:
        primary_user = comp.users[0] if comp.users else None
        active_kws = [k for k in comp.keywords if k.is_active]
        
        clients_list.append({
            "id": str(comp.id),
            "name": comp.name,
            "billing_status": comp.billing_status,
            "max_keywords": comp.max_keywords,
            "active_keywords_count": len(active_kws),
            "created_at": comp.created_at.isoformat() if comp.created_at else None,
            "user": {
                "id": str(primary_user.id),
                "email": primary_user.email,
                "role": primary_user.role,
                "is_active": primary_user.is_active
            } if primary_user else None,
            "assigned_keywords": [
                {
                    "id": str(k.id),
                    "keyword_string": k.keyword_string,
                    "topic_context": k.topic_context,
                    "is_active": k.is_active,
                    "created_at": k.created_at.isoformat() if k.created_at else None
                }
                for k in comp.keywords
            ]
        })

    return {
        "status": "success",
        "total_clients": len(clients_list),
        "clients": clients_list
    }


@router.post("/provision", summary="Admin: Provision new client with their bought keyword")
async def admin_provision_client(
    req: AdminProvisionRequest,
    is_admin: bool = Depends(verify_admin),
    db: AsyncSession = Depends(get_db)
):
    """
    Provisions a new client company, user credentials, and their bought keyword.
    Strictly locks the workspace to the specified max_keywords (default 1).
    """
    clean_email = req.email.strip().lower()
    clean_company = req.company_name.strip()
    clean_kw = req.bought_keyword.strip()

    if not clean_email or not req.password or not clean_company or not clean_kw:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Company name, client email, password, and bought keyword are all required."
        )

    # 1. Check if user email already registered
    existing_user_stmt = select(User).where(User.email == clean_email)
    existing_user_res = await db.execute(existing_user_stmt)
    if existing_user_res.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"User with email '{clean_email}' already exists."
        )

    # 2. Create isolated Company with strict keyword plan
    new_company = Company(
        id=uuid.uuid4(),
        name=clean_company,
        max_keywords=max(1, req.max_keywords or 1),
        billing_status="active"
    )
    db.add(new_company)
    await db.flush()

    # 3. Create Client Login
    new_user = User(
        id=uuid.uuid4(),
        company_id=new_company.id,
        email=clean_email,
        hashed_password=hash_pw(req.password),
        role="client",
        is_active=True
    )
    db.add(new_user)

    # 4. Create the strictly authorized bought keyword
    created_kw = Keyword(
        id=uuid.uuid4(),
        company_id=new_company.id,
        keyword_string=clean_kw,
        topic_context=(req.topic_context or "").strip(),
        is_active=True
    )
    db.add(created_kw)

    await db.commit()
    await db.refresh(new_company)
    await db.refresh(new_user)
    await db.refresh(created_kw)

    logger.info(f"🔑 Admin provisioned client: '{clean_company}' ({clean_email}) with keyword: '{clean_kw}'")

    return {
        "status": "success",
        "message": f"Client '{clean_company}' successfully registered with keyword '{clean_kw}'!",
        "company": {
            "id": str(new_company.id),
            "name": new_company.name,
            "max_keywords": new_company.max_keywords,
            "billing_status": new_company.billing_status
        },
        "credentials": {
            "email": new_user.email,
            "password": req.password
        },
        "keyword": {
            "id": str(created_kw.id),
            "keyword_string": created_kw.keyword_string,
            "topic_context": created_kw.topic_context
        }
    }


@router.patch("/clients/{company_id}/status", summary="Admin: Suspend or Reactivate Client")
async def update_client_status(
    company_id: uuid.UUID,
    req: AdminUpdateStatusRequest,
    is_admin: bool = Depends(verify_admin),
    db: AsyncSession = Depends(get_db)
):
    stmt = select(Company).where(Company.id == company_id)
    res = await db.execute(stmt)
    comp = res.scalar_one_or_none()
    if not comp:
        raise HTTPException(status_code=404, detail="Company not found.")

    comp.billing_status = req.billing_status.strip().lower()
    await db.commit()
    await db.refresh(comp)

    return {
        "status": "success",
        "company_id": str(comp.id),
        "name": comp.name,
        "billing_status": comp.billing_status
    }


@router.delete("/clients/{company_id}", summary="Admin: Delete client workspace and cascade data")
async def delete_client_workspace(
    company_id: uuid.UUID,
    is_admin: bool = Depends(verify_admin),
    db: AsyncSession = Depends(get_db)
):
    stmt = select(Company).where(Company.id == company_id)
    res = await db.execute(stmt)
    comp = res.scalar_one_or_none()
    if not comp:
        raise HTTPException(status_code=404, detail="Company not found.")

    await db.delete(comp)
    await db.commit()

    return {"status": "success", "message": f"Client workspace '{comp.name}' deleted successfully."}
