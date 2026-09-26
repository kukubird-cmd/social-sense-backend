import asyncio
import uuid
from app.database import AsyncSessionLocal, engine, Base
from app.models import Company, Keyword, ScrapedData


async def seed_demo_data():
    """Seeds an initial demo company and keywords into PostgreSQL."""
    print("Connecting to database and creating tables...")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with AsyncSessionLocal() as session:
        # Create a Demo Company with tier of 5 keywords
        demo_company = Company(
            id=uuid.UUID("11111111-1111-1111-1111-111111111111"),
            name="VoxEureka Demo Client (Apex Motors)",
            max_keywords=5,
            billing_status="active"
        )
        session.add(demo_company)
        await session.flush()

        # Create 2 initial tracked keywords
        keyword_1 = Keyword(
            id=uuid.UUID("22222222-2222-2222-2222-222222222222"),
            company_id=demo_company.id,
            keyword_string="#ApexMotorsMY",
            platform_flags={"tiktok": True, "instagram": True, "twitter": True, "reddit": True, "youtube": True},
            is_active=True
        )

        keyword_2 = Keyword(
            id=uuid.UUID("33333333-3333-3333-3333-333333333333"),
            company_id=demo_company.id,
            keyword_string="Apex Horizon EV",
            platform_flags={"tiktok": True, "instagram": True, "twitter": True, "reddit": False, "youtube": True},
            is_active=True
        )

        session.add_all([keyword_1, keyword_2])
        await session.commit()

        print(f"Successfully seeded:")
        print(f"  - Company ID: {demo_company.id} (Max Keywords: {demo_company.max_keywords})")
        print(f"  - Keyword 1: {keyword_1.keyword_string} (ID: {keyword_1.id})")
        print(f"  - Keyword 2: {keyword_2.keyword_string} (ID: {keyword_2.id})")
        print("Ready for WebSocket connection at: ws://localhost:8000/ws/11111111-1111-1111-1111-111111111111")


if __name__ == "__main__":
    asyncio.run(seed_demo_data())
