from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Annotated, AsyncIterator

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.infra.db.models import ApiKeyModel
from bestseller.infra.db.session import get_server_session
from bestseller.infra.redis import get_redis_client
from bestseller.settings import AppSettings, get_settings
from redis.asyncio import Redis


# ── Settings ──────────────────────────────────────────────────────────────────

def get_app_settings() -> AppSettings:
    return get_settings()


SettingsDep = Annotated[AppSettings, Depends(get_app_settings)]


# ── Database session ─────────────────────────────────────────────────────────

async def db_session() -> AsyncIterator[AsyncSession]:
    async with get_server_session() as session:
        yield session


SessionDep = Annotated[AsyncSession, Depends(db_session)]


# ── Redis ─────────────────────────────────────────────────────────────────────

def redis_client() -> Redis:  # type: ignore[type-arg]
    return get_redis_client()


RedisDep = Annotated[Redis, Depends(redis_client)]  # type: ignore[type-arg]


# ── API key authentication ────────────────────────────────────────────────────

_bearer = HTTPBearer(auto_error=True)


async def verify_api_key(
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(_bearer)],
    session: SessionDep,
) -> ApiKeyModel:
    raw_key = credentials.credentials
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()

    result = await session.execute(
        select(ApiKeyModel).where(
            ApiKeyModel.key_hash == key_hash,
            ApiKeyModel.is_active.is_(True),
        )
    )
    api_key = result.scalar_one_or_none()

    if api_key is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or inactive API key",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Track last usage time
    api_key.last_used_at = datetime.now(timezone.utc)

    return api_key


ApiKeyDep = Annotated[ApiKeyModel, Depends(verify_api_key)]
