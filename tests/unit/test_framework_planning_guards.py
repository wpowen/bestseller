"""Framework guards: planning concurrency, book_spec scale, volume phases."""

from __future__ import annotations

import datetime as _dt
from types import SimpleNamespace
from uuid import uuid4

import pytest

from bestseller.services.planner import (
    _normalize_volume_plan_conflict_phases,
    _sanitize_book_spec_against_project_scale,
)
from bestseller.services.prompt_assembly import genre_wants_reaction_amplification

pytestmark = pytest.mark.unit


def test_sanitize_book_spec_rewrites_out_of_range_chapter_milestones() -> None:
    project = SimpleNamespace(target_chapters=20, genre="仙侠", language="zh-CN")
    book_spec = {
        "series_engine": {
            "selling_points": ["第50章中期炸点：围剿者放下剑"],
            "trope_keywords": ["无敌流", "食修仙侠", "沙雕系统"],
        },
        "protagonist": {
            "name": "姜炙",
            "golden_finger": "炼气初期锁死，没有面板、没有属性",
        },
        "themes": ["冲突从个体扩大到组织，姜炙被卷入势力间的", "高概念钩子自传播"],
    }
    out = _sanitize_book_spec_against_project_scale(project, book_spec)
    selling = " ".join(out["series_engine"]["selling_points"])
    assert "第50章" not in selling
    assert "第14章" in selling or "第" in selling  # rewritten into band
    tropes = out["series_engine"]["trope_keywords"]
    assert "无敌流" not in tropes
    assert "食修仙侠" in tropes
    # truncated theme dropped
    assert all(not t.endswith("的") for t in out["themes"])


def test_sanitize_book_spec_preserves_language_for_english_milestones() -> None:
    """English 'Chapter 50' must be rewritten to English, not mixed zh/en garbage.

    Regression: the milestone scrubber always emitted the Chinese
    "第{late}章" replacement regardless of which alternative in
    _CHAPTER_MILESTONE_RE matched, leaving English BookSpec copy with a
    stray Chinese chapter marker.
    """
    project = SimpleNamespace(target_chapters=20, genre="litrpg", language="en-US")
    book_spec = {
        "series_engine": {
            "selling_points": ["Chapter 50 mid-book twist: the hunter lowers his blade"],
            "trope_keywords": [],
        },
        "protagonist": {"name": "Kael"},
        "themes": [],
    }
    out = _sanitize_book_spec_against_project_scale(project, book_spec)
    selling = " ".join(out["series_engine"]["selling_points"])
    assert "Chapter 50" not in selling
    assert "第" not in selling
    assert "Chapter 14" in selling


def test_volume_plan_clamps_vol1_endgame_phase() -> None:
    plan = [
        {
            "volume_number": 1,
            "conflict_phase": "internal_reckoning",
            "chapter_count_target": 20,
        }
    ]
    out = _normalize_volume_plan_conflict_phases(plan, target_chapters=20)
    assert out[0]["conflict_phase"] == "survival"
    assert out[0]["_meta"]["conflict_phase_clamped"] is True


def test_volume_plan_single_short_keeps_legitimate_non_endgame_phase() -> None:
    """Single-volume ≤60ch book with a legitimate phase must not be stomped.

    Regression: an earlier version of this normalizer unconditionally forced
    every single-volume short book to ``assigned[0]``, even when the model
    had already planned a legitimate non-endgame phase like "betrayal".
    """
    plan = [
        {
            "volume_number": 1,
            "conflict_phase": "betrayal",
            "chapter_count_target": 40,
        }
    ]
    out = _normalize_volume_plan_conflict_phases(plan, target_chapters=40)
    assert out[0]["conflict_phase"] == "betrayal"
    assert "_meta" not in out[0]


def test_volume_plan_single_short_still_clamps_endgame_phase() -> None:
    plan = [
        {
            "volume_number": 1,
            "conflict_phase": "existential_threat",
            "chapter_count_target": 30,
        }
    ]
    out = _normalize_volume_plan_conflict_phases(plan, target_chapters=30)
    assert out[0]["conflict_phase"] != "existential_threat"
    assert out[0]["_meta"]["conflict_phase_clamped"] is True
    assert out[0]["_meta"]["conflict_phase_was"] == "existential_threat"


def test_volume_plan_multi_vol_keeps_later_phases() -> None:
    plan = [
        {"volume_number": 1, "conflict_phase": "survival", "chapter_count_target": 30},
        {"volume_number": 2, "conflict_phase": "betrayal", "chapter_count_target": 30},
        {
            "volume_number": 3,
            "conflict_phase": "internal_reckoning",
            "chapter_count_target": 30,
        },
    ]
    out = _normalize_volume_plan_conflict_phases(plan, target_chapters=90)
    assert out[0]["conflict_phase"] == "survival"
    assert out[2]["conflict_phase"] == "internal_reckoning"


def test_reaction_amplification_off_for_romance() -> None:
    assert genre_wants_reaction_amplification("romance", "slow-burn") is False
    assert genre_wants_reaction_amplification("仙侠", "升级") is True


class _FakeWorkflowRow:
    def __init__(self, workflow_type: str, updated_at: _dt.datetime | None) -> None:
        self.id = uuid4()
        self.workflow_type = workflow_type
        self.updated_at = updated_at


class _FakeSelectSession:
    """Fake AsyncSession: first execute() returns rows, second is the UPDATE."""

    def __init__(self, rows: list[_FakeWorkflowRow]) -> None:
        self._rows = rows
        self.executed: list[object] = []

    async def execute(self, stmt):  # noqa: ANN001
        self.executed.append(stmt)

        class _Result:
            def __init__(self, rows: list[_FakeWorkflowRow]) -> None:
                self._rows = rows

            def scalars(self):
                return self

            def all(self):
                return self._rows

        # Only the first (SELECT) call needs to return rows; the UPDATE
        # statement's return value is unused by the implementation.
        return _Result(self._rows if len(self.executed) == 1 else [])

    async def flush(self) -> None:
        return None


@pytest.mark.asyncio
async def test_cancel_stale_planning_workflows_cancels_dead_rows() -> None:
    """Rows with no heartbeat inside the stale window are marked FAILED."""
    from bestseller.services import planning_concurrency as pc

    stale_time = _dt.datetime.now(_dt.UTC) - _dt.timedelta(hours=4)
    rows = [
        _FakeWorkflowRow("generate_volume_plan", stale_time),
        _FakeWorkflowRow("generate_novel_plan", stale_time),
    ]
    session = _FakeSelectSession(rows)

    n = await pc.cancel_stale_planning_workflows(
        session,  # type: ignore[arg-type]
        uuid4(),
        reason="test",
    )
    assert n == 2
    assert len(session.executed) == 2  # select + update


@pytest.mark.asyncio
async def test_cancel_stale_planning_workflows_refuses_live_sibling() -> None:
    """A row with a fresh heartbeat must never be silently marked FAILED."""
    from bestseller.services import planning_concurrency as pc

    fresh_time = _dt.datetime.now(_dt.UTC) - _dt.timedelta(minutes=2)
    rows = [_FakeWorkflowRow("generate_volume_plan", fresh_time)]
    session = _FakeSelectSession(rows)

    with pytest.raises(pc.PlanningConflictError):
        await pc.cancel_stale_planning_workflows(
            session,  # type: ignore[arg-type]
            uuid4(),
            reason="test",
        )
    # Only the SELECT ran — no UPDATE was issued against the live row.
    assert len(session.executed) == 1


@pytest.mark.asyncio
async def test_cancel_stale_planning_workflows_excludes_autowrite() -> None:
    """autowrite_pipeline must never be a cancellable planning type.

    Regression: an earlier version of this guard put ``autowrite_pipeline``
    in the cancellable set, so re-planning volume N+1 could mark an
    in-progress *writing* run FAILED while the worker kept writing chapters
    underneath it.
    """
    from bestseller.services import planning_concurrency as pc

    assert "autowrite_pipeline" not in pc.PLANNING_WORKFLOW_TYPES


@pytest.mark.asyncio
async def test_assert_no_active_writing_pipeline_returns_active_row() -> None:
    from bestseller.services import planning_concurrency as pc

    expected = _FakeWorkflowRow("autowrite_pipeline", _dt.datetime.now(_dt.UTC))

    class _FakeScalarSession:
        async def scalar(self, stmt):  # noqa: ANN001
            return expected

    result = await pc.assert_no_active_writing_pipeline(
        _FakeScalarSession(),  # type: ignore[arg-type]
        uuid4(),
    )
    assert result is expected
