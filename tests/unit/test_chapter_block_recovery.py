from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest

from bestseller.services.chapter_block_recovery import (
    _latest_quality_report_is_clean,
    _ok_repair_residue_keys,
    _release_if_unblocked,
    summarize_block_recovery,
    BlockRecoveryReport,
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
