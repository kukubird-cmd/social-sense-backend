import os
from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Optional


from pydantic import field_validator


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

    @field_validator("APIFY_API_TOKEN", "ANTHROPIC_API_KEY", "GEMINI_API_KEY", mode="before")
    @classmethod
    def clean_api_keys(cls, v):
        if isinstance(v, str):
            cleaned = v.strip().replace("\r", "").replace("\n", "").replace('"', '').replace("'", "")
            return cleaned if cleaned else None
        return v

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        if self.DATABASE_URL.startswith("postgres://"):
            self.DATABASE_URL = self.DATABASE_URL.replace("postgres://", "postgresql+asyncpg://", 1)
        elif self.DATABASE_URL.startswith("postgresql://") and "+asyncpg" not in self.DATABASE_URL:
            self.DATABASE_URL = self.DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1)
        
        # Defensive double-sanitization
        if self.APIFY_API_TOKEN:
            self.APIFY_API_TOKEN = str(self.APIFY_API_TOKEN).strip().replace("\r", "").replace("\n", "").replace('"', '').replace("'", "")
        if self.GEMINI_API_KEY:
            self.GEMINI_API_KEY = str(self.GEMINI_API_KEY).strip().replace("\r", "").replace("\n", "").replace('"', '').replace("'", "")
    
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )


settings = Settings()
