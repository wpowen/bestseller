from __future__ import annotations

from typing import AsyncIterator
from contextlib import asynccontextmanager

import redis.asyncio as aioredis
from redis.asyncio import Redis

from bestseller.settings import AppSettings


_global_redis: Redis | None = None  # type: ignore[type-arg]


async def init_redis(settings: AppSettings) -> None:
    global _global_redis
    if _global_redis is not None:
        return  # Already initialized — prevent connection leaks from double-init
    _global_redis = aioredis.from_url(
        settings.redis.url,
        max_connections=settings.redis.pool_max_connections,
        socket_timeout=settings.redis.socket_timeout_seconds,
        socket_connect_timeout=settings.redis.socket_connect_timeout_seconds,
        decode_responses=True,
    )


async def shutdown_redis() -> None:
    global _global_redis
    if _global_redis is not None:
        await _global_redis.aclose()
        _global_redis = None


def get_redis_client() -> Redis:  # type: ignore[type-arg]
    if _global_redis is None:
        raise RuntimeError("Redis not initialized. Call init_redis() first.")
    return _global_redis


@asynccontextmanager
async def redis_scope(settings: AppSettings) -> AsyncIterator[Redis]:  # type: ignore[type-arg]
    """One-shot Redis connection for CLI / scripts (not for long-lived servers)."""
    client: Redis = aioredis.from_url(  # type: ignore[type-arg]
        settings.redis.url,
        decode_responses=True,
    )
    try:
        yield client
    finally:
        await client.aclose()
