from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import settings

engine = create_async_engine(settings.database_url, pool_pre_ping=True, future=True)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def get_session() -> AsyncGenerator[AsyncSession]:
    async with AsyncSessionLocal() as session:
        yield session


@asynccontextmanager
async def worker_session() -> AsyncGenerator[AsyncSession]:
    """Session for Celery tasks, with its own short-lived engine.

    Each task runs its coroutine in a fresh event loop, and asyncpg connections cannot
    cross loops — reusing the module-level engine would hand the task a connection bound
    to a loop that is already closed.
    """
    task_engine = create_async_engine(settings.database_url, poolclass=NullPool, future=True)
    session_factory = async_sessionmaker(task_engine, expire_on_commit=False, class_=AsyncSession)
    try:
        async with session_factory() as session:
            yield session
    finally:
        await task_engine.dispose()
