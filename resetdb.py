import asyncio
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text
from app.core.config import config


async def reset():
    engine = create_async_engine(config.database.dsn)
    async with engine.begin() as conn:
        await conn.execute(text("DROP SCHEMA public CASCADE"))
        await conn.execute(text("CREATE SCHEMA public"))
    await engine.dispose()
    print("✅ База очищена. Теперь запусти: alembic upgrade head")


asyncio.run(reset())
