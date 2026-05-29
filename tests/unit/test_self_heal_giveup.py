"""Tests for the self-heal no-progress give-up threshold.

Covers the pure helper ``_compute_heal_progress_state`` and the
``_project_self_heal_abandoned`` predicate that together stop the heal
loop from re-queueing (and burning LLM tokens on) projects that are
hard-blocked in planning and never advance.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from bestseller.worker.self_heal import (
    MAX_SELF_HEAL_NO_PROGRESS_ATTEMPTS,
    _compute_heal_progress_state,
    _project_self_heal_abandoned,
)


@pytest.mark.unit
class TestComputeHealProgressState:
    def test_first_ever_heal_resets_counter_not_abandoned(self) -> None:
        meta, abandoned = _compute_heal_progress_state({}, chapters_total=0)
        assert abandoned is False
        assert meta["self_heal_no_progress_attempts"] == 0
        assert meta["self_heal_last_chapters_total"] == 0

    def test_no_progress_increments_counter(self) -> None:
        meta = {"self_heal_last_chapters_total": 0, "self_heal_no_progress_attempts": 0}
        meta, abandoned = _compute_heal_progress_state(meta, chapters_total=0)
        assert abandoned is False
        assert meta["self_heal_no_progress_attempts"] == 1

    def test_progress_resets_counter(self) -> None:
        meta = {"self_heal_last_chapters_total": 2, "self_heal_no_progress_attempts": 3}
        meta, abandoned = _compute_heal_progress_state(meta, chapters_total=5)
        assert abandoned is False
        assert meta["self_heal_no_progress_attempts"] == 0
        assert meta["self_heal_last_chapters_total"] == 5

    def test_reaches_threshold_marks_abandoned(self) -> None:
        meta: dict = {}
        abandoned = False
        # First scan is a baseline (attempts=0); each subsequent no-progress
        # scan increments. So it takes MAX+1 scans to reach the threshold.
        for _ in range(MAX_SELF_HEAL_NO_PROGRESS_ATTEMPTS + 1):
            meta, abandoned = _compute_heal_progress_state(meta, chapters_total=0)
        assert abandoned is True
        assert meta["self_heal_abandoned"] is True
        assert meta["production_pause_reason"] == "self_heal_no_progress_giveup"
        assert meta["requires_human_review"] is True
        assert "self_heal_abandoned_at" in meta

    def test_below_threshold_not_abandoned(self) -> None:
        meta: dict = {}
        abandoned = False
        for _ in range(MAX_SELF_HEAL_NO_PROGRESS_ATTEMPTS - 1):
            meta, abandoned = _compute_heal_progress_state(meta, chapters_total=0)
        assert abandoned is False
        assert "self_heal_abandoned" not in meta

    def test_progress_then_stall_restarts_count(self) -> None:
        meta: dict = {}
        # 2 no-progress heals
        meta, _ = _compute_heal_progress_state(meta, chapters_total=0)
        meta, _ = _compute_heal_progress_state(meta, chapters_total=0)
        # first scan = baseline (0), second = +1
        assert meta["self_heal_no_progress_attempts"] == 1
        # a chapter lands → reset
        meta, abandoned = _compute_heal_progress_state(meta, chapters_total=1)
        assert meta["self_heal_no_progress_attempts"] == 0
        assert abandoned is False

    def test_custom_max_attempts(self) -> None:
        meta: dict = {}
        meta, abandoned = _compute_heal_progress_state(meta, chapters_total=0, max_attempts=1)
        # first call progresses (last is None) → attempts 0, not abandoned
        assert abandoned is False
        meta, abandoned = _compute_heal_progress_state(meta, chapters_total=0, max_attempts=1)
        assert abandoned is True

    def test_does_not_mutate_input_metadata(self) -> None:
        original = {"self_heal_last_chapters_total": 0, "other_key": "keep"}
        snapshot = dict(original)
        _compute_heal_progress_state(original, chapters_total=0)
        assert original == snapshot  # input untouched (pure)

    def test_preserves_unrelated_metadata(self) -> None:
        meta = {"premise": "x", "genre_key": "urban"}
        out, _ = _compute_heal_progress_state(meta, chapters_total=0)
        assert out["premise"] == "x"
        assert out["genre_key"] == "urban"

    def test_corrupt_counter_value_tolerated(self) -> None:
        meta = {"self_heal_last_chapters_total": 0, "self_heal_no_progress_attempts": "bad"}
        out, abandoned = _compute_heal_progress_state(meta, chapters_total=0)
        assert abandoned is False
        assert out["self_heal_no_progress_attempts"] == 1  # bad → treated as 0, +1

    def test_corrupt_last_total_tolerated(self) -> None:
        meta = {"self_heal_last_chapters_total": "bad"}
        out, abandoned = _compute_heal_progress_state(meta, chapters_total=0)
        # bad last → treated as None → progressed → reset
        assert abandoned is False
        assert out["self_heal_no_progress_attempts"] == 0


@pytest.mark.unit
class TestProjectSelfHealAbandoned:
    def test_abandoned_true(self) -> None:
        p = SimpleNamespace(metadata_json={"self_heal_abandoned": True})
        assert _project_self_heal_abandoned(p) is True

    def test_abandoned_false_when_absent(self) -> None:
        p = SimpleNamespace(metadata_json={})
        assert _project_self_heal_abandoned(p) is False

    def test_abandoned_false_when_metadata_none(self) -> None:
        p = SimpleNamespace(metadata_json=None)
        assert _project_self_heal_abandoned(p) is False

    def test_abandoned_false_when_metadata_not_dict(self) -> None:
        p = SimpleNamespace(metadata_json="garbage")
        assert _project_self_heal_abandoned(p) is False
