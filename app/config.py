import os
from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Optional


class Settings(BaseSettings):
    APP_NAME: str = "SocialSense AI Backend"
    DEBUG: bool = True
    PORT: int = 8000
    
    # Database Settings:
    # Defaults to local async SQLite for zero-config immediate execution,
    # or override with PostgreSQL: postgresql+asyncpg://postgres:pass@localhost:5432/db
    DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./socialsense.db")
    
    # External APIs
    APIFY_API_TOKEN: Optional[str] = os.getenv("APIFY_API_TOKEN", None)
    ANTHROPIC_API_KEY: Optional[str] = os.getenv("ANTHROPIC_API_KEY", None)
    GEMINI_API_KEY: Optional[str] = os.getenv("GEMINI_API_KEY", None)

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )


settings = Settings()
