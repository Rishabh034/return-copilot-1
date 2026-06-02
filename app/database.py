from typing import AsyncGenerator

import asyncpg
from fastapi import Request

from app.config import settings


async def create_db_pool() -> asyncpg.Pool:
    return await asyncpg.create_pool(
        dsn=settings.database_url,
        min_size=2,
        max_size=10,
        command_timeout=60,
    )


async def get_db(request: Request) -> AsyncGenerator[asyncpg.Connection, None]:
    """FastAPI dependency — acquires a connection from the pool, releases on exit."""
    async with request.app.state.db_pool.acquire() as conn:
        yield conn
