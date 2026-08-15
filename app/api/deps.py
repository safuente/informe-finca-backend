from collections.abc import AsyncGenerator
from typing import Annotated

from fastapi import Depends, HTTPException, Query, Request, status
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_session
from app.core.logger import get_logger

logger = get_logger(__name__)


async def get_db() -> AsyncGenerator[AsyncSession]:
    async for session in get_session():
        yield session


DbSession = Annotated[AsyncSession, Depends(get_db)]


async def get_redis(request: Request) -> Redis:
    return request.app.state.redis


RedisDep = Annotated[Redis, Depends(get_redis)]


class Pagination:
    def __init__(
        self,
        limit: int = Query(50, ge=1, le=200),
        offset: int = Query(0, ge=0),
    ) -> None:
        self.limit = limit
        self.offset = offset


PaginationDep = Annotated[Pagination, Depends(Pagination)]


def client_ip(request: Request) -> str:
    """Client IP behind the reverse proxy that terminates TLS on the VPS."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


class RateLimiter:
    """Fixed-window limiter per IP, backed by Redis.

    The point is not to protect us: it is to keep a scraper from hammering the Catastro
    OVC through our free preview. The OVC does not document its rate limits, and losing
    access to it would stop the product.
    """

    def __init__(self, name: str, limit: int, window_seconds: int = 3600) -> None:
        self.name = name
        self.limit = limit
        self.window_seconds = window_seconds

    async def __call__(self, request: Request, redis: RedisDep) -> None:
        key = f"ratelimit:{self.name}:{client_ip(request)}"
        try:
            count = await redis.incr(key)
            if count == 1:
                await redis.expire(key, self.window_seconds)
        except Exception:  # noqa: BLE001 — a broken Redis must not take the API down
            logger.warning("Rate limiter unavailable; letting the request through", exc_info=True)
            return
        if count > self.limit:
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS,
                "Demasiadas consultas desde esta IP. Inténtalo de nuevo en un rato.",
            )


preview_rate_limit = RateLimiter("preview", settings.preview_rate_limit)
