import uuid
from typing import AsyncGenerator
from sqlalchemy import types
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import declarative_base
from app.config import settings

def create_initial_engine():
    """Initializes primary engine or falls back to SQLite immediately if dialect driver fails."""
    try:
        if settings.DATABASE_URL.startswith("sqlite"):
            return create_async_engine(settings.DATABASE_URL, connect_args={"check_same_thread": False})
        else:
            return create_async_engine(settings.DATABASE_URL, pool_size=10, max_overflow=5, pool_pre_ping=True)
    except Exception as e:
        import logging
        logging.getLogger("database").warning(f"Failed to create primary engine ({e}). Defaulting to SQLite.")
        return create_async_engine("sqlite+aiosqlite:///./socialsense.db", connect_args={"check_same_thread": False})


engine = create_initial_engine()

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    autocommit=False,
    autoflush=False,
    expire_on_commit=False,
    class_=AsyncSession
)

Base = declarative_base()


async def init_db():
    """Initializes DB schema with resilient 4-second timeout and automatic fallback to SQLite."""
    global engine, AsyncSessionLocal
    import logging
    import asyncio
    log = logging.getLogger("database")
    db_target = settings.DATABASE_URL.split("@")[-1] if "@" in settings.DATABASE_URL else settings.DATABASE_URL
    log.info(f"Connecting to database target: {db_target}")

    async def _setup(eng):
        async with eng.begin() as conn:
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

    try:
        await asyncio.wait_for(_setup(engine), timeout=4.0)
        log.info("Primary database connected and schema initialized successfully.")
    except Exception as e:
        log.warning(f"Primary database connection failed or timed out: {e}. Switching to SQLite.")
        sqlite_url = "sqlite+aiosqlite:///./socialsense.db"
        engine = create_async_engine(sqlite_url, connect_args={"check_same_thread": False})
        AsyncSessionLocal.configure(bind=engine)
        await _setup(engine)
        log.info("Fallback SQLite database initialized successfully.")


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
