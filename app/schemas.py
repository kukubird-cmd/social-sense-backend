import uuid
from datetime import datetime
from typing import Optional, Dict, Any, List
from pydantic import BaseModel, Field


# -------------------------------------------------------------
# Company Schemas
# -------------------------------------------------------------
class CompanyBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    max_keywords: int = Field(default=5, ge=1)
    billing_status: str = Field(default="active")


class CompanyCreate(CompanyBase):
    pass


class CompanyResponse(CompanyBase):
    id: uuid.UUID
    created_at: datetime

    class Config:
        from_attributes = True


# -------------------------------------------------------------
# Keyword Schemas
# -------------------------------------------------------------
class PlatformFlags(BaseModel):
    tiktok: bool = True
    instagram: bool = True
    twitter: bool = True
    reddit: bool = True
    youtube: bool = True


class KeywordCreate(BaseModel):
    keyword_string: str = Field(..., min_length=1, max_length=255)
    topic_context: Optional[str] = Field(default="", description="Target context / intended meaning (e.g. 'Kedah Matriculation College')")
    platform_flags: Optional[PlatformFlags] = Field(default_factory=PlatformFlags)


class KeywordUpdate(BaseModel):
    topic_context: Optional[str] = None
    platform_flags: Optional[PlatformFlags] = None
    is_active: Optional[bool] = None


class KeywordResponse(BaseModel):
    id: uuid.UUID
    company_id: uuid.UUID
    keyword_string: str
    topic_context: Optional[str] = ""
    platform_flags: Dict[str, bool]
    is_active: bool
    created_at: datetime

    class Config:
        from_attributes = True


# -------------------------------------------------------------
# Scraped Data Schemas
# -------------------------------------------------------------
class ScrapedDataBase(BaseModel):
    platform: str
    post_url: str
    comment_text: str
    engagement_metrics: Dict[str, Any] = Field(default_factory=dict)
    sentiment_score: str = Field(..., description="Positive, Neutral, Negative, or Crisis")
    timestamp: Optional[datetime] = None


class ScrapedDataCreate(ScrapedDataBase):
    keyword_id: uuid.UUID


class ScrapedDataResponse(ScrapedDataBase):
    id: uuid.UUID
    keyword_id: uuid.UUID
    timestamp: datetime

    class Config:
        from_attributes = True


# -------------------------------------------------------------
# Apify Webhook Payload Schemas
# -------------------------------------------------------------
class ApifyEventData(BaseModel):
    actorId: Optional[str] = None
    actorRunId: Optional[str] = None
    defaultDatasetId: str


class ApifyWebhookPayload(BaseModel):
    userId: Optional[str] = None
    createdAt: Optional[str] = None
    eventType: Optional[str] = None
    eventData: ApifyEventData
