import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.config import settings
from app.database import engine, Base
from app.routers import keywords, webhooks, ws, scraper, chat, auth

# Configure structured logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifecycle event handler: initializes DB tables on startup."""
    logger.info("Initializing database schema...")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        try:
            from sqlalchemy import text
            await conn.execute(text("ALTER TABLE keywords ADD COLUMN topic_context TEXT DEFAULT ''"))
        except Exception:
            pass
        try:
            from sqlalchemy import text
            await conn.execute(text("ALTER TABLE scraped_data ADD COLUMN is_owned_media BOOLEAN DEFAULT 0"))
        except Exception:
            pass
    logger.info("Database schema initialized.")
    yield
    logger.info("Shutting down backend service...")
    await engine.dispose()


app = FastAPI(
    title="SocialSense AI - Keyword Tracking & Scraper Backend",
    description="Production-ready FastAPI backend integrating Apify, PostgreSQL, Sentiment Analysis, and WebSockets.",
    version="1.0.0",
    lifespan=lifespan
)

# CORS configuration for Next.js frontend communication
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # In production, lock down to your specific frontend domain
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount Routers
app.include_router(keywords.router)
app.include_router(webhooks.router)
app.include_router(ws.router)
app.include_router(scraper.router)
app.include_router(chat.router)
app.include_router(auth.router)


@app.get("/", tags=["Root"])
async def root():
    """Root entrypoint: returns friendly overview and link to documentation."""
    return {
        "status": "online",
        "service": "SocialSense AI - Production Backend",
        "documentation": "http://localhost:8000/docs",
        "health_check": "http://localhost:8000/health",
        "frontend_dashboard": "http://localhost:3000",
        "message": "Welcome! Visit /docs for the interactive Swagger API interface."
    }


@app.get("/health", tags=["System Health"])
async def health_check():
    return {
        "status": "healthy",
        "service": settings.APP_NAME,
        "database": "connected"
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=settings.PORT, reload=settings.DEBUG)
