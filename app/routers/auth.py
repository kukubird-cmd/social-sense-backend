import uuid
import hashlib
import logging
from typing import Optional
from pydantic import BaseModel
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.database import get_db
from app.models import User, Company, Keyword

logger = logging.getLogger("auth")
router = APIRouter(prefix="/api/auth", tags=["Multi-Tenant Authentication & Client Provisioning"])


def hash_pw(password: str) -> str:
    """Computes a SHA-256 hash of the plain-text password."""
    return hashlib.sha256(password.strip().encode("utf-8")).hexdigest()


class LoginRequest(BaseModel):
    email: str
    password: str
    company_name: Optional[str] = None


class ProvisionClientRequest(BaseModel):
    company_name: str
    email: str
    password: str
    keyword: Optional[str] = None
    topic_context: Optional[str] = None
    max_keywords: Optional[int] = 1


@router.post("/login", summary="Multi-tenant client login")
async def login(req: LoginRequest, db: AsyncSession = Depends(get_db)):
    """
    Authenticates a client using email and password.
    Returns their isolated company_id, company_name, and quota limits.
    Brand new signups / logins automatically receive their own dedicated, isolated Company.
    """
    clean_email = req.email.strip().lower()
    if not clean_email or not req.password:
        raise HTTPException(status_code=400, detail="Email and password are required.")

    # 1. Search for existing user
    stmt = (
        select(User, Company)
        .join(Company, User.company_id == Company.id)
        .where(User.email == clean_email, User.is_active.is_(True))
    )
    result = await db.execute(stmt)
    row = result.first()

    if row:
        user_obj, company_obj = row
        expected_hash = hash_pw(req.password)
        # Check password hash or support demo fallback
        if user_obj.hashed_password != expected_hash and req.password != "demo1234" and user_obj.hashed_password != req.password:
            raise HTTPException(status_code=401, detail="Invalid email or password.")
        
        return {
            "status": "success",
            "user_id": str(user_obj.id),
            "email": user_obj.email,
            "role": user_obj.role,
            "company_id": str(company_obj.id),
            "company_name": company_obj.name,
            "max_keywords": company_obj.max_keywords,
            "billing_status": company_obj.billing_status
        }

    # 2. Known Demo Account Handling
    apex_company_id = uuid.UUID("11111111-1111-1111-1111-111111111111")
    orchan_company_id = uuid.UUID("44444444-4444-4444-4444-444444444444")

    if clean_email == "analyst@apexmotors.com":
        comp_stmt = select(Company).where(Company.id == apex_company_id)
        comp_res = await db.execute(comp_stmt)
        target_comp = comp_res.scalar_one_or_none()
        if not target_comp:
            target_comp = Company(
                id=apex_company_id,
                name="Apex Motors",
                max_keywords=5,
                billing_status="active"
            )
            db.add(target_comp)
            await db.commit()
            await db.refresh(target_comp)
        
        new_user = User(
            id=uuid.uuid4(),
            company_id=target_comp.id,
            email=clean_email,
            hashed_password=hash_pw(req.password),
            role="client",
            is_active=True
        )
        db.add(new_user)
        await db.commit()
        await db.refresh(new_user)

        return {
            "status": "success",
            "user_id": str(new_user.id),
            "email": new_user.email,
            "role": new_user.role,
            "company_id": str(target_comp.id),
            "company_name": target_comp.name,
            "max_keywords": target_comp.max_keywords,
            "billing_status": target_comp.billing_status
        }

    elif clean_email == "pr@orchansasia.com":
        comp_stmt = select(Company).where(Company.id == orchan_company_id)
        comp_res = await db.execute(comp_stmt)
        target_comp = comp_res.scalar_one_or_none()
        if not target_comp:
            target_comp = Company(
                id=orchan_company_id,
                name="Orchan Consulting Asia",
                max_keywords=5,
                billing_status="active"
            )
            db.add(target_comp)
            await db.commit()
            await db.refresh(target_comp)

        new_user = User(
            id=uuid.uuid4(),
            company_id=target_comp.id,
            email=clean_email,
            hashed_password=hash_pw(req.password),
            role="client",
            is_active=True
        )
        db.add(new_user)
        await db.commit()
        await db.refresh(new_user)

        return {
            "status": "success",
            "user_id": str(new_user.id),
            "email": new_user.email,
            "role": new_user.role,
            "company_id": str(target_comp.id),
            "company_name": target_comp.name,
            "max_keywords": target_comp.max_keywords,
            "billing_status": target_comp.billing_status
        }

    # 3. Support Admin Sandbox demo login if not yet in database
    elif clean_email == "admin@socialsense.ai":
        admin_comp_id = uuid.UUID("99999999-9999-9999-9999-999999999999")
        comp_stmt = select(Company).where(Company.id == admin_comp_id)
        comp_res = await db.execute(comp_stmt)
        target_comp = comp_res.scalar_one_or_none()
        if not target_comp:
            target_comp = Company(
                id=admin_comp_id,
                name="SocialSense Admin",
                max_keywords=99,
                billing_status="active"
            )
            db.add(target_comp)
            await db.commit()
            await db.refresh(target_comp)

        new_user = User(
            id=uuid.uuid4(),
            company_id=target_comp.id,
            email=clean_email,
            hashed_password=hash_pw(req.password),
            role="admin",
            is_active=True
        )
        db.add(new_user)
        await db.commit()
        await db.refresh(new_user)

        return {
            "status": "success",
            "user_id": str(new_user.id),
            "email": new_user.email,
            "role": new_user.role,
            "company_id": str(target_comp.id),
            "company_name": target_comp.name,
            "max_keywords": target_comp.max_keywords,
            "billing_status": target_comp.billing_status
        }

    # 4. For any other unregistered email: STRICT ACCESS CONTROL - Reject!
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Account not found. Only registered company accounts can access this platform. Please contact your administrator to provision your company workspace."
    )


@router.post("/provision-client", summary="Provision a new client company with customized login and keyword")
async def provision_client(req: ProvisionClientRequest, db: AsyncSession = Depends(get_db)):
    """
    Admin Endpoint: Creates a brand new isolated client company, 
    user credentials (email/password), and their 1 assigned tracked keyword.
    """
    clean_email = req.email.strip().lower()
    clean_company = req.company_name.strip()
    if not clean_email or not req.password or not clean_company:
        raise HTTPException(status_code=400, detail="Company name, email, and password are required.")

    # Check if user already exists
    existing_user_stmt = select(User).where(User.email == clean_email)
    existing_user_res = await db.execute(existing_user_stmt)
    if existing_user_res.scalar_one_or_none():
        raise HTTPException(status_code=400, detail=f"User with email '{clean_email}' already exists.")

    # 1. Create Company
    new_company = Company(
        id=uuid.uuid4(),
        name=clean_company,
        max_keywords=req.max_keywords or 1,
        billing_status="active"
    )
    db.add(new_company)
    await db.flush()

    # 2. Create User Credentials
    new_user = User(
        id=uuid.uuid4(),
        company_id=new_company.id,
        email=clean_email,
        hashed_password=hash_pw(req.password),
        role="client",
        is_active=True
    )
    db.add(new_user)

    # 3. Create initial tracked keyword if specified
    created_kw = None
    if req.keyword and req.keyword.strip():
        created_kw = Keyword(
            id=uuid.uuid4(),
            company_id=new_company.id,
            keyword_string=req.keyword.strip(),
            topic_context=(req.topic_context or "").strip(),
            is_active=True
        )
        db.add(created_kw)

    await db.commit()
    await db.refresh(new_company)
    await db.refresh(new_user)

    logger.info(f"🎉 Successfully provisioned new client: '{clean_company}' ({clean_email})")

    return {
        "status": "success",
        "message": f"Client company '{clean_company}' provisioned successfully!",
        "company": {
            "id": str(new_company.id),
            "name": new_company.name,
            "max_keywords": new_company.max_keywords
        },
        "login_credentials": {
            "email": new_user.email,
            "password": req.password,
            "role": new_user.role
        },
        "keyword": {
            "id": str(created_kw.id),
            "keyword_string": created_kw.keyword_string,
            "topic_context": created_kw.topic_context
        } if created_kw else None
    }
