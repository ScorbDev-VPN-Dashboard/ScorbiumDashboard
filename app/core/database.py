from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from typing import AsyncGenerator
import sqlalchemy as sa

from app.core.config import config
from app.utils.log import log


def _build_dsn() -> str:
    db = config.database
    password = db.db_password.get_secret_value()
    return (
        f"postgresql+asyncpg://{db.db_user}:{password}"
        f"@{db.db_host}:{db.db_port}/{db.db_name}"
    )


engine = create_async_engine(
    _build_dsn(),
    echo=False,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
)

AsyncSessionFactory = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
    autocommit=False,
)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency — yields an async DB session."""
    async with AsyncSessionFactory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def init_db() -> None:
    """Verify DB connection on startup. Schema is managed by Alembic migrations."""
    import app.models  # noqa: F401 — ensure all models are imported

    async with engine.connect() as conn:
        await conn.execute(sa.text("SELECT 1"))
    log.info("✅ Database connection verified")


async def close_db() -> None:
    await engine.dispose()
    log.info("Database connection pool closed")
