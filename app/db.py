from __future__ import annotations

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import settings

engine = create_async_engine(
    settings.database_url,
    pool_size = 10, # 10 connections opened (default is 5)
    max_overflow = 20, # allowed 20 more under burst load
    pool_pre_ping=True, # in case of db restarts , test connection before using it
)

# factory that creates sessions. expire_on_commit=False means after a commit,
# our Python objects still have their data (important for async — don't want
# extra DB round-trips to re-read data we just wrote)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)

# all our model classes will inherit this base class, alembic will use this to discover tables/classes.
class Base(DeclarativeBase):
    pass

# fastapi opens a sessions before the route runs and closes it after
async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        yield session
