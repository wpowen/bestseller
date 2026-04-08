from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    status: str
    service: str = "bestseller-api"


@router.get("/health", response_model=HealthResponse, status_code=status.HTTP_200_OK)
async def health() -> HealthResponse:
    return HealthResponse(status="ok")


@router.get("/ready", response_model=HealthResponse, status_code=status.HTTP_200_OK)
async def ready() -> HealthResponse:
    """Liveness/readiness probe — checks DB and Redis connectivity."""
    from bestseller.infra.db.session import get_server_session  # noqa: PLC0415
    from bestseller.infra.redis import get_redis_client  # noqa: PLC0415
    from sqlalchemy import text  # noqa: PLC0415

    async with get_server_session() as session:
        await session.execute(text("SELECT 1"))

    redis = get_redis_client()
    pong = await redis.ping()
    if not pong:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Redis not reachable")

    return HealthResponse(status="ready")
