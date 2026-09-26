import uuid
from datetime import datetime
from typing import Dict, Any
from sqlalchemy import (
    Column, 
    String, 
    Integer, 
    Boolean, 
    DateTime, 
    ForeignKey, 
    Text, 
    JSON
)
from sqlalchemy.orm import relationship
from app.database import Base, GUID


class User(Base):
    __tablename__ = "users"

    id = Column(GUID, primary_key=True, default=uuid.uuid4, index=True)
    company_id = Column(GUID, ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True)
    email = Column(String(255), unique=True, nullable=False, index=True)
    hashed_password = Column(String(255), nullable=False)
    role = Column(String(50), default="client", nullable=False) # "admin", "client"
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Relationships
    company = relationship("Company", back_populates="users")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": str(self.id),
            "company_id": str(self.company_id),
            "email": self.email,
            "role": self.role,
            "is_active": self.is_active,
            "created_at": self.created_at.isoformat() if self.created_at else None
        }


class Company(Base):
    __tablename__ = "companies"

    id = Column(GUID, primary_key=True, default=uuid.uuid4, index=True)
    name = Column(String(255), nullable=False)
    max_keywords = Column(Integer, default=1, nullable=False)
    billing_status = Column(String(50), default="active", nullable=False) # active, past_due, canceled
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Relationships
    keywords = relationship("Keyword", back_populates="company", cascade="all, delete-orphan")
    users = relationship("User", back_populates="company", cascade="all, delete-orphan")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": str(self.id),
            "name": self.name,
            "max_keywords": self.max_keywords,
            "billing_status": self.billing_status,
            "created_at": self.created_at.isoformat() if self.created_at else None
        }


class Keyword(Base):
    __tablename__ = "keywords"

    id = Column(GUID, primary_key=True, default=uuid.uuid4, index=True)
    company_id = Column(GUID, ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True)
    keyword_string = Column(String(255), nullable=False)
    
    # Platform flags: booleans for {"tiktok": true, "instagram": true, "twitter": true, "reddit": true, "youtube": true}
    platform_flags = Column(
        JSON, 
        nullable=False, 
        default=lambda: {
            "tiktok": True,
            "instagram": True,
            "twitter": True,
            "reddit": True,
            "youtube": True
        }
    )
    topic_context = Column(Text, nullable=True, default="")
    is_active = Column(Boolean, default=True, nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Relationships
    company = relationship("Company", back_populates="keywords")
    scraped_data = relationship("ScrapedData", back_populates="keyword", cascade="all, delete-orphan")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": str(self.id),
            "company_id": str(self.company_id),
            "keyword_string": self.keyword_string,
            "topic_context": self.topic_context or "",
            "platform_flags": self.platform_flags,
            "is_active": self.is_active,
            "created_at": self.created_at.isoformat() if self.created_at else None
        }


class ScrapedData(Base):
    __tablename__ = "scraped_data"

    id = Column(GUID, primary_key=True, default=uuid.uuid4, index=True)
    keyword_id = Column(GUID, ForeignKey("keywords.id", ondelete="CASCADE"), nullable=False, index=True)
    platform = Column(String(50), nullable=False, index=True) # tiktok, instagram, twitter, reddit, youtube
    post_url = Column(Text, nullable=False)
    comment_text = Column(Text, nullable=False)
    engagement_metrics = Column(JSON, nullable=False, default=dict) # {"likes": 0, "shares": 0, "comments": 0, "views": 0}
    sentiment_score = Column(String(20), nullable=False, index=True) # Positive, Neutral, Negative, Crisis
    is_owned_media = Column(Boolean, default=False, nullable=True)
    timestamp = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)

    # Relationships
    keyword = relationship("Keyword", back_populates="scraped_data")

    def to_dict(self) -> Dict[str, Any]:
        metrics = self.engagement_metrics or {}
        author = metrics.get("author") or "User"
        is_owned = self.is_owned_media if self.is_owned_media is not None else metrics.get("is_owned_media", False)
        return {
            "id": str(self.id),
            "keyword_id": str(self.keyword_id),
            "platform": self.platform,
            "post_url": self.post_url,
            "comment_text": self.comment_text,
            "author": author,
            "is_owned_media": bool(is_owned),
            "engagement_metrics": self.engagement_metrics,
            "sentiment_score": self.sentiment_score,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None
        }
