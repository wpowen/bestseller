from __future__ import annotations

import hashlib
import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from bestseller.infra.db.session import init_db, shutdown_db
from bestseller.infra.redis import init_redis, shutdown_redis
from bestseller.settings import get_settings

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()

    # Startup
    logger.info("Initializing database pool…")
    await init_db(settings)

    logger.info("Initializing Redis pool…")
    await init_redis(settings)

    yield

    # Shutdown
    logger.info("Shutting down Redis…")
    await shutdown_redis()

    logger.info("Shutting down database pool…")
    await shutdown_db()


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title="BestSeller API",
        description="AI-powered novel generation — REST API",
        version="1.0.0",
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
    )

    # CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.api.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Routers
    from bestseller.api.routers import (  # noqa: PLC0415
        content,
        exports,
        health,
        pipelines,
        projects,
        publishing,
        tasks,
    )

    app.include_router(health.router)
    app.include_router(projects.router, prefix="/api/v1")
    app.include_router(pipelines.router, prefix="/api/v1")
    app.include_router(tasks.router, prefix="/api/v1")
    app.include_router(content.router, prefix="/api/v1")
    app.include_router(exports.router, prefix="/api/v1")
    app.include_router(publishing.router, prefix="/api/v1")

    return app


def main() -> None:
    import uvicorn  # noqa: PLC0415

    settings = get_settings()
    uvicorn.run(
        "bestseller.api.app:create_app",
        factory=True,
        host=settings.api.host,
        port=settings.api.port,
        reload=False,
    )


# Utility used by API key management CLI command
def hash_api_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode()).hexdigest()
