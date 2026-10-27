"""Loop-local Web ownership using the native worker lease; no dispatch authority.

Enter inside the Web execution thread's async runner, under its existing scope,
before creating/awaiting the pipeline coroutine. Existing task/operator checks
remain first in the paid cancellation chain. No client crosses event loops.
"""
from contextlib import asynccontextmanager
import logging
from uuid import uuid4

from redis.asyncio import Redis

from bestseller.services.progress_context import TaskCancelledError, _CANCELLATION_CHECKER

logger = logging.getLogger(__name__)


class WebPipelineOwnerUnavailable(TaskCancelledError):
    """Local owner admission failure, never a project financial STOP."""

    def __init__(self, reason):
        self.reason = reason
        super().__init__("Web pipeline owner unavailable: " + reason)


@asynccontextmanager
async def web_pipeline_owner(settings, slug):
    """`async with web_pipeline_owner(settings, slug): await runner()`.

    Only actual acquired ownership enters the body. Caller retains scope and
    operator controls; this helper adds no grant, resume or provider call.
    """
    from bestseller.worker import tasks

    if not isinstance(slug, str) or not slug or slug != slug.strip() or len(slug) > 64:
        raise ValueError("web_pipeline_owner_project_slug_required")
    try:
        client = Redis.from_url(
            settings.redis.url, decode_responses=True,
            socket_timeout=settings.redis.socket_timeout_seconds,
            socket_connect_timeout=settings.redis.socket_connect_timeout_seconds,
            max_connections=settings.redis.pool_max_connections,
        )
    except Exception:
        raise WebPipelineOwnerUnavailable("client_unavailable") from None
    try:
        token = uuid4().hex
        state = await tasks._acquire_pipeline_live(client, slug, token)
        if state != "acquired":
            raise WebPipelineOwnerUnavailable(state)
        async with tasks._pipeline_live_guard(client, slug, token, state):
            # Scoped private token follows rewrite_outline_dispatch's existing
            # contract; no mutation of the shared progress_context source.
            previous = _CANCELLATION_CHECKER.get()

            async def check():
                if previous is not None:
                    await previous()
                await tasks._assert_pipeline_live_owner()

            binding = _CANCELLATION_CHECKER.set(check)
            try:
                yield
            finally:
                _CANCELLATION_CHECKER.reset(binding)
    finally:
        try:
            await client.aclose(close_connection_pool=True)
        except Exception as error:
            # Preserve the body's failure/cancellation without logging URLs.
            logger.warning("Web owner client close failed (%s)", type(error).__name__)
