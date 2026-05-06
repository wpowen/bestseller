"""APScheduler firing callbacks for ``book_generation_schedules``.

These callbacks live at module level (NOT nested) so that
``SQLAlchemyJobStore`` can pickle them by their dotted-path reference
and reload them after a scheduler restart. Each callback is a thin
wrapper that:

  1. POSTs the stored payload to the web service's
     ``/api/tasks/autowrite`` or ``/api/tasks/quickstart`` endpoint —
     the same path used when a user clicks "Start" in the UI.
  2. Records the resulting ``WebTaskState`` task_id (or the error) on
     the schedule row by calling ``services.book_generation_schedules.mark_fired``.

The scheduler runs in its own container and cannot import the synchronous
``WebTaskManager`` directly (it lives inside the web process and holds
in-memory state), so HTTP is the right boundary.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any
from urllib.parse import urljoin
from uuid import UUID

import httpx
from sqlalchemy import select

from bestseller.infra.db.models import BookGenerationScheduleModel
from bestseller.infra.db.session import get_server_session
from bestseller.services.book_generation_schedules import mark_fired


logger = logging.getLogger(__name__)


def _web_base_url() -> str:
    """Resolve the URL of the bestseller-web service inside the docker network."""
    return os.environ.get("BESTSELLER_WEB_BASE_URL", "http://web:8787").rstrip("/")


def _endpoint_for(task_type: str) -> str:
    if task_type == "autowrite":
        return "/api/tasks/autowrite"
    if task_type == "quickstart":
        return "/api/tasks/quickstart"
    raise ValueError(f"Unknown task_type {task_type!r}")


async def fire_book_generation_schedule(schedule_id_str: str) -> None:
    """One-shot APScheduler callback.

    ``schedule_id_str`` is a string (not a ``UUID``) because APScheduler's
    ``SQLAlchemyJobStore`` pickles arguments as JSON-serialisable types
    on some setups; passing a string survives every store backend.
    """
    schedule_id = UUID(schedule_id_str)
    logger.info("Firing book generation schedule %s", schedule_id)

    payload: dict[str, Any] | None = None
    task_type: str | None = None
    async with get_server_session() as session:
        row = await session.scalar(
            select(BookGenerationScheduleModel).where(
                BookGenerationScheduleModel.id == schedule_id
            )
        )
        if row is None:
            logger.warning("Schedule %s not found, skipping fire", schedule_id)
            return
        if row.status != "pending":
            logger.warning(
                "Schedule %s is in status %s (not pending), skipping fire",
                schedule_id,
                row.status,
            )
            return
        payload = dict(row.payload or {})
        task_type = row.task_type

    if payload is None or task_type is None:
        return  # Defensive — earlier branches would have already returned.

    base_url = _web_base_url()
    endpoint = _endpoint_for(task_type)
    url = urljoin(base_url + "/", endpoint.lstrip("/"))

    task_id: str | None = None
    error_message: str | None = None
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(url, json=payload)
            response.raise_for_status()
            data = response.json()
            task_id = str(data.get("task_id") or data.get("id") or "") or None
            logger.info(
                "Schedule %s fired -> task_id=%s (HTTP %s)",
                schedule_id,
                task_id,
                response.status_code,
            )
    except httpx.HTTPStatusError as exc:
        body_snippet = exc.response.text[:500] if exc.response is not None else ""
        error_message = (
            f"web returned HTTP {exc.response.status_code}: {body_snippet}"
        )
        logger.exception("Schedule %s fire failed (HTTP error)", schedule_id)
    except Exception as exc:  # noqa: BLE001
        error_message = f"{type(exc).__name__}: {exc}"
        logger.exception("Schedule %s fire failed (unexpected error)", schedule_id)

    async with get_server_session() as session:
        await mark_fired(
            session,
            schedule_id,
            task_id=task_id,
            error_message=error_message,
        )
        await session.commit()


__all__ = ["fire_book_generation_schedule"]
