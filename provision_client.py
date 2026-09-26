"""
Client Provisioning CLI Tool for SocialSense AI
Quickly onboards a new paying client company with isolated login credentials and their assigned 1 tracked keyword.

Usage:
  python provision_client.py --company "Nike Malaysia" --email "client@nike.my" --password "Nike2026!" --keyword "Nike"
"""
import argparse
import asyncio
import uuid
import hashlib
from app.database import AsyncSessionLocal
from app.models import Company, User, Keyword
from sqlalchemy import select


def hash_pw(pw: str) -> str:
    return hashlib.sha256(pw.strip().encode("utf-8")).hexdigest()


async def provision(company_name: str, email: str, password: str, keyword: str, topic_context: str = ""):
    clean_email = email.strip().lower()
    clean_company = company_name.strip()
    clean_kw = keyword.strip()

    async with AsyncSessionLocal() as db:
        # Check if user already exists
        res = await db.execute(select(User).where(User.email == clean_email))
        if res.scalar_one_or_none():
            print(f"[ERROR] User with email '{clean_email}' already exists.")
            return

        # 1. Create isolated Company
        comp = Company(
            id=uuid.uuid4(),
            name=clean_company,
            max_keywords=1, # 1 Keyword Plan Enforced
            billing_status="active"
        )
        db.add(comp)
        await db.flush()

        # 2. Create User Login
        user = User(
            id=uuid.uuid4(),
            company_id=comp.id,
            email=clean_email,
            hashed_password=hash_pw(password),
            role="client",
            is_active=True
        )
        db.add(user)

        # 3. Create Assigned 1 Keyword
        kw = Keyword(
            id=uuid.uuid4(),
            company_id=comp.id,
            keyword_string=clean_kw,
            topic_context=topic_context.strip(),
            is_active=True
        )
        db.add(kw)

        await db.commit()

        print("\n" + "="*60)
        print("[SUCCESS] CLIENT PROVISIONED FOR PRODUCTION")
        print("="*60)
        print(f"Company Name   : {comp.name}")
        print(f"Company ID     : {comp.id}")
        print(f"Login Email    : {clean_email}")
        print(f"Password       : {password}")
        print(f"Assigned Term  : {clean_kw}")
        print(f"Plan Limit     : 1 Active Keyword Plan")
        print("="*60)
        print("Give the client their Email & Password. When they log in,")
        print("they will see only their company branding and their keyword!\n")


def main():
    parser = argparse.ArgumentParser(description="Provision new client for SocialSense AI")
    parser.add_argument("--company", required=True, help="Company / Brand Name")
    parser.add_argument("--email", required=True, help="Client login email")
    parser.add_argument("--password", required=True, help="Client login password")
    parser.add_argument("--keyword", required=True, help="Client tracked keyword")
    parser.add_argument("--topic", default="", help="Optional topic disambiguation context")

    args = parser.parse_args()
    asyncio.run(provision(args.company, args.email, args.password, args.keyword, args.topic))


if __name__ == "__main__":
    main()
