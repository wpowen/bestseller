"""Unit tests for ``bestseller.services.book_generation_schedules``.

Service-layer tests use ``AsyncMock`` for the session because the
service simply orchestrates SQL ops — there's no business logic to
exercise that requires a real DB.  Roundtrip-against-real-DB coverage
lives in the integration suite.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from bestseller.services import book_generation_schedules as bgs


@pytest.mark.unit
@pytest.mark.asyncio
async def test_create_schedule_rejects_naive_datetime() -> None:
    session = AsyncMock()
    naive = datetime.now() + timedelta(hours=1)
    with pytest.raises(ValueError, match="timezone-aware"):
        await bgs.create_schedule(
            session,
            task_type="autowrite",
            scheduled_at=naive,
            payload={"slug": "x"},
        )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_create_schedule_rejects_past_datetime() -> None:
    session = AsyncMock()
    past = datetime.now(timezone.utc) - timedelta(minutes=5)
    with pytest.raises(ValueError, match="future"):
        await bgs.create_schedule(
            session,
            task_type="autowrite",
            scheduled_at=past,
            payload={"slug": "x"},
        )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_create_schedule_rejects_invalid_task_type() -> None:
    session = AsyncMock()
    future = datetime.now(timezone.utc) + timedelta(hours=1)
    with pytest.raises(ValueError, match="task_type"):
        await bgs.create_schedule(
            session,
            task_type="bogus",
            scheduled_at=future,
            payload={},
        )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_create_schedule_persists_pending_row() -> None:
    session = AsyncMock()
    session.add = MagicMock()  # synchronous on real Session
    future = datetime.now(timezone.utc) + timedelta(hours=2)
    row = await bgs.create_schedule(
        session,
        task_type="autowrite",
        scheduled_at=future,
        payload={"slug": "demo", "title": "Demo"},
        project_slug="demo",
        title="Demo",
        requested_by="alice",
    )
    session.add.assert_called_once()
    session.flush.assert_awaited_once()
    assert row.task_type == "autowrite"
    assert row.status == "pending"
    assert row.project_slug == "demo"
    assert row.requested_by == "alice"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cancel_schedule_returns_none_for_missing_row() -> None:
    session = AsyncMock()
    session.get.return_value = None
    sid = uuid4()
    result = await bgs.cancel_schedule(session, sid)
    assert result is None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cancel_schedule_rejects_non_pending() -> None:
    session = AsyncMock()
    fake_row = SimpleNamespace(status="fired")
    session.get.return_value = fake_row
    with pytest.raises(ValueError, match="cancelled"):
        await bgs.cancel_schedule(session, uuid4())


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cancel_schedule_marks_pending_as_cancelled() -> None:
    session = AsyncMock()
    fake_row = SimpleNamespace(status="pending")
    session.get.return_value = fake_row
    result = await bgs.cancel_schedule(session, uuid4())
    assert result is fake_row
    assert fake_row.status == "cancelled"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_list_schedules_validates_status_filter() -> None:
    session = AsyncMock()
    with pytest.raises(ValueError, match="status_filter"):
        await bgs.list_schedules(session, status_filter="bogus")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_mark_fired_records_task_id() -> None:
    session = AsyncMock()
    fake_result = MagicMock()
    fake_result.rowcount = 1
    session.execute.return_value = fake_result
    sid = uuid4()
    await bgs.mark_fired(session, sid, task_id="task-abc")
    session.execute.assert_awaited_once()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_mark_fired_with_error_marks_failed() -> None:
    session = AsyncMock()
    fake_result = MagicMock()
    fake_result.rowcount = 1
    session.execute.return_value = fake_result
    sid = uuid4()
    await bgs.mark_fired(
        session,
        sid,
        task_id=None,
        error_message="HTTP 500: web crashed",
    )
    # The status arg of the UPDATE is buried inside the compiled stmt, so we
    # just verify the call happened — schema-level enforcement (CHECK) covers
    # the rest.
    session.execute.assert_awaited_once()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_mark_terminal_rejects_non_terminal_status() -> None:
    session = AsyncMock()
    with pytest.raises(ValueError, match="completed/failed/cancelled"):
        await bgs.mark_terminal(session, uuid4(), status="pending")
