from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest

from bestseller.services.chapter_block_recovery import (
    BlockRecoveryReport,
    _latest_quality_report_is_clean,
    _ok_repair_residue_keys,
    _release_if_unblocked,
    _stale_block_residue_keys,
    attempt_release_stale_production_block,
    summarize_block_recovery,
)

pytestmark = pytest.mark.unit


def _quality_report(*, blocks_write: bool, blocking_codes: list[str] | None = None):
    return SimpleNamespace(
        blocks_write=blocks_write,
        report_json={"blocking_codes": blocking_codes or []},
        created_at=datetime.now(UTC),
    )


def test_latest_quality_report_clean_requires_no_write_block_or_codes() -> None:
    assert _latest_quality_report_is_clean(_quality_report(blocks_write=False, blocking_codes=[]))
    assert not _latest_quality_report_is_clean(
        _quality_report(blocks_write=True, blocking_codes=[])
    )
    assert not _latest_quality_report_is_clean(
        _quality_report(blocks_write=False, blocking_codes=["LENGTH_OVER"])
    )
    assert not _latest_quality_report_is_clean(None)


def test_release_if_unblocked_preserves_other_registered_gate_metadata() -> None:
    chapter = SimpleNamespace(
        id=uuid4(),
        chapter_number=3,
        production_state="blocked",
        metadata_json={},
    )
    metadata = {"blocked_by_write_safety_gate": True}

    state = _release_if_unblocked(chapter, metadata)  # type: ignore[arg-type]

    assert state == "blocked"
    assert chapter.production_state == "blocked"


def test_release_if_unblocked_sets_ok_without_registered_blocks() -> None:
    chapter = SimpleNamespace(
        id=uuid4(),
        chapter_number=3,
        production_state="blocked",
        metadata_json={},
    )
    metadata = {"retention_recovered_by_closure_loop": True}

    state = _release_if_unblocked(chapter, metadata)  # type: ignore[arg-type]

    assert state == "ok"
    assert chapter.production_state == "ok"


def test_ok_repair_residue_keys_only_reports_ok_chapter_residue() -> None:
    ok_chapter = SimpleNamespace(
        production_state="ok",
        metadata_json={
            "auto_repair_in_progress": True,
            "auto_repair_last_block_codes": ["CHAPTER_LENGTH_BLOCK_HIGH"],
            "autonomous_quality_retrofit_exhausted": True,
        },
    )
    blocked_chapter = SimpleNamespace(
        production_state="blocked",
        metadata_json={"auto_repair_in_progress": True},
    )

    assert _ok_repair_residue_keys(ok_chapter) == (  # type: ignore[arg-type]
        "auto_repair_in_progress",
        "auto_repair_last_block_codes",
        "autonomous_quality_retrofit_exhausted",
    )
    assert _ok_repair_residue_keys(blocked_chapter) == ()  # type: ignore[arg-type]


def test_stale_block_residue_keys_reports_blocked_chapter_residue() -> None:
    chapter = SimpleNamespace(
        production_state="blocked",
        metadata_json={
            "auto_repair_last_block_codes": ["LENGTH_OVER"],
            "quality_gate_block_codes": ["LENGTH_OVER"],
            "blocked_by_write_safety_gate": True,
        },
    )

    assert _stale_block_residue_keys(chapter) == (  # type: ignore[arg-type]
        "auto_repair_last_block_codes",
        "quality_gate_block_codes",
        "blocked_by_write_safety_gate",
    )


@pytest.mark.asyncio
async def test_attempt_release_stale_production_block_clears_unmarked_block(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    chapter = SimpleNamespace(
        id=uuid4(),
        project_id=uuid4(),
        chapter_number=9,
        production_state="blocked",
        metadata_json={},
    )

    async def no_pending(_session, _chapter):
        return ()

    async def no_quality(_session, _chapter):
        return None

    async def clear_scene(_session, _chapter):
        return 0

    async def flush():
        return None

    monkeypatch.setattr(
        "bestseller.services.chapter_block_recovery._pending_rewrite_task_count",
        no_pending,
    )
    monkeypatch.setattr(
        "bestseller.services.chapter_block_recovery._pending_rewrite_tasks",
        no_pending,
    )
    monkeypatch.setattr(
        "bestseller.services.chapter_block_recovery._latest_quality_report",
        no_quality,
    )
    monkeypatch.setattr(
        "bestseller.services.chapter_block_recovery._clear_scene_auto_repair_residue",
        clear_scene,
    )

    report = await attempt_release_stale_production_block(
        SimpleNamespace(flush=flush),
        chapter,  # type: ignore[arg-type]
    )

    assert report.recoverable is True
    assert report.reason == "no_active_block_signal"
    assert chapter.production_state == "ok"
    assert chapter.metadata_json["stale_production_block_released_by_closure_loop"] is True


@pytest.mark.asyncio
async def test_attempt_release_stale_production_block_keeps_active_quality_block(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    chapter = SimpleNamespace(
        id=uuid4(),
        project_id=uuid4(),
        chapter_number=17,
        production_state="blocked",
        metadata_json={"quality_gate_block_codes": ["CHAPTER_LENGTH_BLOCK_LOW"]},
    )

    async def no_pending(_session, _chapter):
        return ()

    async def blocked_quality(_session, _chapter):
        return _quality_report(
            blocks_write=True,
            blocking_codes=["CHAPTER_LENGTH_BLOCK_LOW"],
        )

    monkeypatch.setattr(
        "bestseller.services.chapter_block_recovery._pending_rewrite_task_count",
        no_pending,
    )
    monkeypatch.setattr(
        "bestseller.services.chapter_block_recovery._pending_rewrite_tasks",
        no_pending,
    )
    monkeypatch.setattr(
        "bestseller.services.chapter_block_recovery._latest_quality_report",
        blocked_quality,
    )

    report = await attempt_release_stale_production_block(
        SimpleNamespace(),
        chapter,  # type: ignore[arg-type]
    )

    assert report.recoverable is False
    assert report.reason == "latest_quality_still_blocking"
    assert chapter.production_state == "blocked"


@pytest.mark.asyncio
async def test_require_clean_report_keeps_block_without_quality_report(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Conservative mode (in-run repair sweep): a blocked chapter with NO
    quality report on record must NOT be released — it may be genuinely awaiting
    repair. Without require_clean_report it would be treated as stale."""
    chapter = SimpleNamespace(
        id=uuid4(),
        project_id=uuid4(),
        chapter_number=30,
        production_state="blocked",
        metadata_json={},
    )

    async def no_pending(_session, _chapter):
        return ()

    async def no_quality(_session, _chapter):
        return None

    monkeypatch.setattr(
        "bestseller.services.chapter_block_recovery._pending_rewrite_tasks",
        no_pending,
    )
    monkeypatch.setattr(
        "bestseller.services.chapter_block_recovery._latest_quality_report",
        no_quality,
    )

    report = await attempt_release_stale_production_block(
        SimpleNamespace(),
        chapter,  # type: ignore[arg-type]
        require_clean_report=True,
    )

    assert report.recoverable is False
    assert report.reason == "no_quality_report_on_record"
    assert chapter.production_state == "blocked"


@pytest.mark.asyncio
async def test_require_clean_report_still_releases_with_clean_report(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Conservative mode still releases a chapter whose latest report is CLEAN
    (the true stale case — re-passed but kept the blocked flag), e.g. ch2/6/7."""
    chapter = SimpleNamespace(
        id=uuid4(),
        project_id=uuid4(),
        chapter_number=6,
        production_state="blocked",
        metadata_json={},
    )

    async def no_pending(_session, _chapter):
        return ()

    async def clean_quality(_session, _chapter):
        return _quality_report(blocks_write=False, blocking_codes=[])

    async def clear_scene(_session, _chapter):
        return 0

    async def flush():
        return None

    monkeypatch.setattr(
        "bestseller.services.chapter_block_recovery._pending_rewrite_tasks",
        no_pending,
    )
    monkeypatch.setattr(
        "bestseller.services.chapter_block_recovery._latest_quality_report",
        clean_quality,
    )
    monkeypatch.setattr(
        "bestseller.services.chapter_block_recovery._clear_scene_auto_repair_residue",
        clear_scene,
    )

    report = await attempt_release_stale_production_block(
        SimpleNamespace(flush=flush),
        chapter,  # type: ignore[arg-type]
        require_clean_report=True,
    )

    assert report.recoverable is True
    assert report.reason == "latest_quality_clean"
    assert chapter.production_state == "ok"


@pytest.mark.asyncio
async def test_attempt_release_stale_production_block_supersedes_stale_pending_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    chapter = SimpleNamespace(
        id=uuid4(),
        project_id=uuid4(),
        chapter_number=18,
        production_state="blocked",
        metadata_json={"quality_gate_block_codes": ["OLD_CODE"]},
    )
    task = SimpleNamespace(status="pending", metadata_json={})

    async def pending_tasks(_session, _chapter):
        return (task,)

    async def clean_quality(_session, _chapter):
        return _quality_report(blocks_write=False, blocking_codes=[])

    async def clear_scene(_session, _chapter):
        return 0

    async def flush():
        return None

    monkeypatch.setattr(
        "bestseller.services.chapter_block_recovery._pending_rewrite_tasks",
        pending_tasks,
    )
    monkeypatch.setattr(
        "bestseller.services.chapter_block_recovery._latest_quality_report",
        clean_quality,
    )
    monkeypatch.setattr(
        "bestseller.services.chapter_block_recovery._clear_scene_auto_repair_residue",
        clear_scene,
    )

    report = await attempt_release_stale_production_block(
        SimpleNamespace(flush=flush),
        chapter,  # type: ignore[arg-type]
    )

    assert report.recoverable is True
    assert "superseded_pending_rewrite_tasks:1" in report.actions_taken
    assert chapter.production_state == "ok"
    assert task.status == "superseded"
    assert task.metadata_json["superseded_reason"] == "current_chapter_quality_clean"


def test_summarize_block_recovery_counts_recovered_and_blocked() -> None:
    reports = (
        BlockRecoveryReport(
            chapter_number=7,
            block_kind="retention",
            recoverable=True,
            actions_taken=("removed:retention_auto_repair_exhausted",),
            new_state="ok",
            reason="latest_quality_clean",
        ),
        BlockRecoveryReport(
            chapter_number=8,
            block_kind="outline_readiness",
            recoverable=False,
            actions_taken=(),
            new_state="blocked",
            reason="still_blocked",
            issue_codes_now=("OUTLINE_PENDING_REWRITE_TASK",),
        ),
    )

    summary = summarize_block_recovery(reports)

    assert summary["checked"] == 2
    assert summary["recovered"] == 1
    assert summary["still_blocked"] == 1
    assert summary["recovered_chapters"] == [7]
