from __future__ import annotations

import datetime as dt

import pytest

from bestseller.services.outline_revision_recovery import (
    OutlineRecoveryError,
    rearm_outline_recovery_metadata,
)

pytestmark = pytest.mark.unit


def _metadata() -> dict[str, object]:
    return {
        "outline_replan_retry_exhausted": True,
        "outline_replan_retry_attempts": 5,
        "outline_replan_auto_retry_exhausted": True,
        "outline_replan_auto_retry_count": 3,
        "planning_status": "needs_replan",
        "outline_semantic_gate_status": "needs_replan",
        "requires_human_review": True,
        "production_paused": True,
    }


def test_rearm_clears_only_exhaustion_and_records_applied_revision() -> None:
    before = _metadata()
    after, result = rearm_outline_recovery_metadata(
        before,
        revision_report_id="report-1",
        source_revision="source-2",
        changed_sources=("book_spec", "world_spec"),
        now=dt.datetime(2026, 9, 5, tzinfo=dt.UTC),
    )

    assert result.rearmed is True
    assert after["outline_replan_retry_attempts"] == 4
    assert after["outline_replan_auto_retry_count"] == 2
    assert "outline_replan_auto_retry_exhausted" not in after
    assert "outline_replan_retry_exhausted" not in after
    assert "outline_replan_next_retry_at" not in after
    assert after["planning_status"] == "needs_replan"
    assert after["outline_semantic_gate_status"] == "needs_replan"
    assert after["requires_human_review"] is True
    assert after["production_paused"] is True
    assert after["outline_replan_last_manual_repair_revision"] == "report-1"
    assert after["outline_replan_rearm"] == {
        "revision_report_id": "report-1",
        "source_revision": "source-2",
        "changed_sources": ["book_spec", "world_spec"],
        "rearmed_at": "2026-09-05T00:00:00+00:00",
    }


def test_rearm_is_idempotent_for_same_revision() -> None:
    first, _ = rearm_outline_recovery_metadata(
        _metadata(),
        revision_report_id="report-1",
        source_revision="source-2",
        changed_sources=("world_spec",),
    )
    second, result = rearm_outline_recovery_metadata(
        first,
        revision_report_id="report-1",
        source_revision="source-2",
        changed_sources=("world_spec",),
    )

    assert result.rearmed is False
    assert result.reason == "already_rearmed"
    assert second == first


def test_rearm_rejects_non_exhausted_retry() -> None:
    with pytest.raises(OutlineRecoveryError, match="not exhausted"):
        rearm_outline_recovery_metadata(
            {"planning_status": "needs_replan"},
            revision_report_id="report-1",
            source_revision="source-2",
            changed_sources=("world_spec",),
        )


def test_rearm_rejects_empty_revision_identity() -> None:
    with pytest.raises(OutlineRecoveryError, match="identify report"):
        rearm_outline_recovery_metadata(
            _metadata(),
            revision_report_id="",
            source_revision="source-2",
            changed_sources=("world_spec",),
        )
