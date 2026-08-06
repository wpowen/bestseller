from __future__ import annotations

import json

import pytest

from bestseller.api.routers.tasks import get_task_status

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_task_status_treats_completed_attention_payload_as_incomplete() -> None:
    class _FakeRedis:
        async def lrange(self, *_args: object) -> list[str]:
            return [
                json.dumps(
                    {
                        "event_type": "completed",
                        "message": "completed",
                        "data": {
                            "requires_human_review": True,
                            "final_verdict": "attention",
                        },
                    }
                )
            ]

    response = await get_task_status("task-a", _FakeRedis(), object(), object())

    assert response.status == "incomplete"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "event_type",
    [
        # Auto-continue family must terminate (not hang as "running") — the
        # bug class that kept SSE open for 25h and left task cards stuck.
        "repairable_auto_continue",
        "repairable_auto_continue_pending",
        "repairable_auto_continue_deferred",
        "repairable_auto_continue_already_queued",
        "quality_closure_already_queued",
    ],
)
async def test_task_status_treats_auto_continue_events_as_incomplete(event_type: str) -> None:
    class _FakeRedis:
        async def lrange(self, *_args: object) -> list[str]:
            return [json.dumps({"event_type": event_type, "message": event_type, "data": {}})]

    response = await get_task_status("task-a", _FakeRedis(), object(), object())

    assert response.status == "incomplete"


@pytest.mark.asyncio
async def test_task_status_treats_skipped_archived_as_completed() -> None:
    class _FakeRedis:
        async def lrange(self, *_args: object) -> list[str]:
            return [
                json.dumps(
                    {
                        "event_type": "skipped_archived",
                        "message": "skipped_archived",
                        "data": {"status": "skipped_archived"},
                    }
                )
            ]

    response = await get_task_status("task-a", _FakeRedis(), object(), object())

    assert response.status == "completed"


@pytest.mark.asyncio
async def test_task_status_falls_back_to_meta_then_db_when_no_events() -> None:
    """Without progress events the status must not 404: fall back to the
    enqueue-time meta mapping, then to DB workflow state (P04)."""

    class _FakeRedis:
        def __init__(self) -> None:
            self._meta = {
                "project_slug": "my-book",
                "created_at": "0",
            }

        async def lrange(self, *_args: object) -> list[str]:
            return []

        async def hgetall(self, _key: str) -> dict[str, str]:
            return self._meta

    class _FakeSession:
        async def scalar(self, _stmt: object) -> object:
            class _Row:
                id = "proj-1"
                status = "running"

            return _Row()

    response = await get_task_status("task-a", _FakeRedis(), _FakeSession(), object())

    assert response.status == "running"


@pytest.mark.asyncio
async def test_task_status_returns_queued_when_meta_exists_but_no_db_row() -> None:
    """Freshly enqueued task with no DB workflow row yet must report queued."""

    class _FakeRedis:
        async def lrange(self, *_args: object) -> list[str]:
            return []

        async def hgetall(self, _key: str) -> dict[str, str]:
            return {"project_slug": "my-book", "created_at": "0"}

    class _FakeSession:
        async def scalar(self, _stmt: object) -> object:
            return None

    response = await get_task_status("task-a", _FakeRedis(), _FakeSession(), object())

    assert response.status == "queued"
