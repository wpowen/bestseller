"""T7 验收: OverrideStore 落 DB — save/load 真实写读 OverrideContractModel."""
import asyncio
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest


def _make_store_with_rows():
    from datetime import datetime, timezone
    from bestseller.services.override_contract import (
        OverrideStore,
        OverrideContract,
        OverrideStatus,
        RationaleType,
    )
    store = OverrideStore()
    store._rows.append(
        OverrideContract(
            id=1,
            project_id="00000000-0000-0000-0000-000000000001",
            chapter_no=3,
            violation_code="LINE_GAP_OVER",
            rationale_type=RationaleType.ARC_TIMING,
            rationale_text="本章压转折",
            payback_plan="第12章主线大兑现",
            due_chapter=12,
            status=OverrideStatus.ACTIVE,
            created_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
        )
    )
    return store


@pytest.mark.asyncio
async def test_save_override_store_inserts_new_rows():
    """save_override_store 真实构造 SQLAlchemy 对象并加到 session。"""
    from bestseller.services.override_contract import (
        save_override_store,
        OverrideStore,
    )
    from uuid import UUID

    project = SimpleNamespace(
        id=UUID("00000000-0000-0000-0000-000000000001"),
    )

    # Mock session: scalars() returns no existing rows
    session = MagicMock()
    session.scalar = AsyncMock(return_value=None)
    session.add = MagicMock()

    store = _make_store_with_rows()
    inserted = await save_override_store(session, project=project, store=store)

    assert inserted == 1
    assert session.add.call_count == 1


@pytest.mark.asyncio
async def test_save_override_store_skips_existing_rows():
    """save_override_store 在 DB 已有同 key 行时跳过（幂等）。"""
    from bestseller.services.override_contract import save_override_store
    from uuid import UUID

    project = SimpleNamespace(
        id=UUID("00000000-0000-0000-0000-000000000001"),
    )

    # Mock session: scalars() returns an existing row
    existing = MagicMock()
    existing.id = 1
    session = MagicMock()
    session.scalar = AsyncMock(return_value=existing)
    session.add = MagicMock()

    store = _make_store_with_rows()
    inserted = await save_override_store(session, project=project, store=store)

    assert inserted == 0
    assert session.add.call_count == 0


@pytest.mark.asyncio
async def test_load_override_store_rebuilds_from_db():
    """load_override_store 从 DB 行重建 OverrideStore。"""
    from bestseller.services.override_contract import load_override_store
    from uuid import UUID

    project = SimpleNamespace(
        id=UUID("00000000-0000-0000-0000-000000000001"),
    )

    # Build a fake DB row
    fake_row = MagicMock()
    fake_row.id = 42
    fake_row.project_id = UUID("00000000-0000-0000-0000-000000000001")
    fake_row.chapter_no = 5
    fake_row.violation_code = "PAYOFF_DUE_UNRESOLVED"
    fake_row.rationale_type = "ARC_TIMING"
    fake_row.rationale_text = "planner omission"
    fake_row.payback_plan = "next chapter"
    fake_row.due_chapter = 10
    fake_row.status = "active"
    fake_row.created_at = "2026-06-01T00:00:00+00:00"

    # Mock the session.scalars() call — the result is awaited and then
    # list()-iterated, so it needs to be a real iterable (a list works).
    session = MagicMock()
    session.scalars = AsyncMock(return_value=[fake_row])

    store = await load_override_store(session, project=project)

    assert len(store._rows) == 1
    row = store._rows[0]
    assert row.chapter_no == 5
    assert row.violation_code == "PAYOFF_DUE_UNRESOLVED"
    assert row.is_active is True


@pytest.mark.asyncio
async def test_save_then_load_roundtrip_simulates_cross_session():
    """模拟两个 session: 第一个写 store, 第二个 load 能读到。"""
    from datetime import datetime, timezone
    from bestseller.services.override_contract import (
        OverrideContract,
        OverrideStatus,
        OverrideStore,
        RationaleType,
        load_override_store,
        save_override_store,
    )
    from uuid import UUID

    project_id = UUID("00000000-0000-0000-0000-000000000001")
    project = SimpleNamespace(id=project_id)

    # Session 1: write
    written_rows = []

    def capture_add(obj):
        written_rows.append(obj)

    session1 = MagicMock()
    session1.scalar = AsyncMock(return_value=None)  # no existing
    session1.add = MagicMock(side_effect=capture_add)

    store = OverrideStore()
    store._rows.append(
        OverrideContract(
            id=99,
            project_id=str(project_id),
            chapter_no=7,
            violation_code="LINE_GAP_OVER",
            rationale_type=RationaleType.ARC_TIMING,
            rationale_text="planner drift",
            payback_plan="ch 15",
            due_chapter=15,
            status=OverrideStatus.ACTIVE,
            created_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
        )
    )
    inserted = await save_override_store(session1, project=project, store=store)
    assert inserted == 1
    assert len(written_rows) == 1

    # Session 2: read — the captured row becomes a fake DB row
    class FakeRow:
        pass

    fake_row = FakeRow()
    fake_row.id = written_rows[0].id if hasattr(written_rows[0], "id") and written_rows[0].id else 99
    fake_row.project_id = written_rows[0].project_id
    fake_row.chapter_no = written_rows[0].chapter_no
    fake_row.violation_code = written_rows[0].violation_code
    fake_row.rationale_type = written_rows[0].rationale_type
    fake_row.rationale_text = written_rows[0].rationale_text
    fake_row.payback_plan = written_rows[0].payback_plan
    fake_row.due_chapter = written_rows[0].due_chapter
    fake_row.status = written_rows[0].status
    fake_row.created_at = "2026-06-01T00:00:00+00:00"

    session2 = MagicMock()
    session2.scalars = AsyncMock(return_value=[fake_row])

    rebuilt = await load_override_store(session2, project=project)
    assert len(rebuilt._rows) == 1
    assert rebuilt._rows[0].violation_code == "LINE_GAP_OVER"
    assert rebuilt._rows[0].due_chapter == 15
    assert rebuilt._rows[0].is_active is True
