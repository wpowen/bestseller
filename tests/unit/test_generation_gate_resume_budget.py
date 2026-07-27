"""A gate that cannot be satisfied must stop, not spin forever.

Field incident (2026-07-25, 《仇人膝上养帝王》): the book burned 118 LLM calls
and ~880k tokens between 17:28 and 19:51 without producing a single chapter,
looping:

    volume outline batch_4_6 fails OUTLINE_REUSED_PAYLOAD_ANCHOR@ch4
      → project paused, reason=volume_outline_gate_failed
      → self-heal sees that reason in _AUTO_RESUMABLE_GENERATION_GATE_REASONS
      → 60s cooldown elapses, pause cleared, autowrite re-queued
      → the same batch fails again … forever

Eight identical approved versions of batch 1-3 were regenerated, and concurrent
retries raced into ``PlanningConflictError``.

Two things made it unbounded:

1. No attempt counter on this path at all — unlike the sibling heal path
   (``self_heal.py`` heal-attempt bookkeeping) which gives up when repeated
   re-queues make no progress.
2. The clear step ``pop``s ``last_generation_gate_blocked_at``, so every
   failure looked like the first one and the cooldown restarted from zero.

Auto-resume is still right for a transient gate — the fix is a budget, not
removal.
"""

from __future__ import annotations

import datetime as _dt

import pytest

from bestseller.worker.self_heal import (
    GENERATION_GATE_MAX_AUTO_RESUMES,
    _project_has_stale_auto_resumable_generation_gate,
)


pytestmark = pytest.mark.unit


class _Project:
    def __init__(self, metadata: dict) -> None:
        self.slug = "custom-xuanhuan-1784971698"
        self.metadata_json = metadata
        self.status = "paused"


def _stale(**extra) -> dict:
    """Metadata for a gate pause that is past its cooldown."""

    blocked_at = _dt.datetime.now(_dt.UTC) - _dt.timedelta(hours=1)
    meta = {
        "last_generation_gate_reason": "volume_outline_gate_failed",
        "production_pause_reason": "volume_outline_gate_failed",
        "generation_gate_auto_retry_needed": True,
        "last_generation_gate_blocked_at": blocked_at.isoformat(),
    }
    meta.update(extra)
    return meta


def test_budget_constant_is_small_and_positive() -> None:
    assert 1 <= GENERATION_GATE_MAX_AUTO_RESUMES <= 5, (
        "a handful of retries is a transient-fault budget; more is a spin"
    )


def test_first_failure_still_auto_resumes() -> None:
    """Auto-resume must keep working — most gate failures ARE transient."""

    assert _project_has_stale_auto_resumable_generation_gate(_Project(_stale()))


def test_resumes_below_the_budget_are_allowed() -> None:
    project = _Project(
        _stale(generation_gate_auto_resume_count=GENERATION_GATE_MAX_AUTO_RESUMES - 1)
    )

    assert _project_has_stale_auto_resumable_generation_gate(project)


def test_stops_once_the_budget_is_spent() -> None:
    """THE loop. At the budget the project must stay paused for a human."""

    project = _Project(
        _stale(generation_gate_auto_resume_count=GENERATION_GATE_MAX_AUTO_RESUMES)
    )

    assert not _project_has_stale_auto_resumable_generation_gate(project), (
        "a gate that failed this many times will not pass on the next identical "
        "retry — 118 calls / 880k tokens were burned proving exactly that"
    )


def test_way_over_budget_still_stops() -> None:
    project = _Project(_stale(generation_gate_auto_resume_count=99))

    assert not _project_has_stale_auto_resumable_generation_gate(project)


def test_counter_is_incremented_when_the_pause_is_cleared() -> None:
    """Without this the budget can never be reached: the clear step also pops
    ``last_generation_gate_blocked_at``, so each failure looks like the first.
    """

    import inspect

    from bestseller.worker import self_heal

    source = inspect.getsource(
        self_heal._clear_auto_resumable_generation_gate_pause
    )

    assert "generation_gate_auto_resume_count" in source, (
        "clearing the pause must record that a resume was spent"
    )
    # And it must survive the key-purge loop that wipes the other gate keys.
    purge_start = source.index("for key in (")
    purge_block = source[purge_start : source.index("):", purge_start)]
    assert "generation_gate_auto_resume_count" not in purge_block, (
        "the counter must not be purged with the pause keys, or the budget "
        "resets to zero on every failure and the loop returns"
    )


def test_a_different_gate_reason_starts_a_fresh_budget() -> None:
    """The budget is per-problem: making progress and hitting a DIFFERENT gate
    is not the same as spinning on one unsatisfiable gate."""

    project = _Project(
        _stale(
            last_generation_gate_reason="story_bible_gate_failed",
            production_pause_reason="story_bible_gate_failed",
            generation_gate_auto_resume_count=GENERATION_GATE_MAX_AUTO_RESUMES,
            generation_gate_auto_resume_reason="volume_outline_gate_failed",
        )
    )

    assert _project_has_stale_auto_resumable_generation_gate(project), (
        "a new failure mode deserves its own retry budget"
    )
