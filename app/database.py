import uuid
from typing import AsyncGenerator
from sqlalchemy import types
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import declarative_base
from app.config import settings

# Determine engine parameters based on database dialect
connect_args = {}
engine_kwargs = {"echo": False, "future": True}

if settings.DATABASE_URL.startswith("sqlite"):
    connect_args = {"check_same_thread": False}
    engine_kwargs["connect_args"] = connect_args
else:
    # PostgreSQL connection pool settings
    engine_kwargs.update({
        "pool_size": 20,
        "max_overflow": 10,
        "pool_pre_ping": True
    })

engine = create_async_engine(settings.DATABASE_URL, **engine_kwargs)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    autocommit=False,
    autoflush=False,
    expire_on_commit=False,
    class_=AsyncSession
)

Base = declarative_base()


class GUID(types.TypeDecorator):
    """
    Platform-independent GUID/UUID type.
    Uses PostgreSQL's native UUID type if available, otherwise CHAR(36).
    Ensures seamless compatibility with both local SQLite and production PostgreSQL.
    """
    impl = types.CHAR
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            return dialect.type_descriptor(PG_UUID(as_uuid=True))
        else:
            return dialect.type_descriptor(types.CHAR(36))

    def process_bind_param(self, value, dialect):
        if value is None:
            return value
        if dialect.name == "postgresql":
            return str(value)
        else:
            return str(value)

    def process_result_value(self, value, dialect):
        if value is None:
            return value
        if isinstance(value, uuid.UUID):
            return value
        return uuid.UUID(value)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency for yielding database session with auto-rollback on error."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
