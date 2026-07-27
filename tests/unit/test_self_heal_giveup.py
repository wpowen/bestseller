"""Tests for bounded, fingerprint-aware self-heal convergence."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from bestseller.worker.self_heal import (
    MAX_SELF_HEAL_NO_PROGRESS_ATTEMPTS,
    _compute_heal_progress_state,
    _outline_replan_progress_fingerprint,
    _outline_replan_progress_rank,
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

    def test_reaches_threshold_stops_identical_machine_repair(self) -> None:
        meta: dict = {}
        abandoned = False
        # First scan is a baseline (attempts=0); each subsequent no-progress
        # scan increments. So it takes MAX+1 scans to reach the threshold.
        for _ in range(MAX_SELF_HEAL_NO_PROGRESS_ATTEMPTS + 1):
            meta, abandoned = _compute_heal_progress_state(meta, chapters_total=0)
        assert abandoned is True
        assert meta["self_heal_abandoned"] is True
        assert meta["self_heal_no_progress_escalated"] is True
        assert meta["self_heal_repair_strategy"] == "deep_machine_repair"
        assert meta["production_pause_reason"] == "self_heal_no_actionable_progress"
        assert meta["requires_machine_repair"] is True
        assert meta["requires_human_review"] is True
        assert "self_heal_no_progress_escalated_at" in meta

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
        assert meta["self_heal_no_progress_escalated"] is True
        assert meta["requires_human_review"] is True

    def test_changed_blocker_fingerprint_resets_stall_counter(self) -> None:
        meta, _ = _compute_heal_progress_state(
            {}, chapters_total=50, progress_fingerprint="repair:22,28"
        )
        meta, _ = _compute_heal_progress_state(
            meta, chapters_total=50, progress_fingerprint="repair:22,28"
        )
        assert meta["self_heal_no_progress_attempts"] == 1

    def test_first_fingerprint_establishes_new_repair_baseline(self) -> None:
        meta = {
            "self_heal_last_chapters_total": 0,
            "self_heal_no_progress_attempts": 4,
        }

        meta, abandoned = _compute_heal_progress_state(
            meta,
            chapters_total=0,
            progress_fingerprint="outline_replan:commercial-score-062",
        )

        assert abandoned is False
        assert meta["self_heal_no_progress_attempts"] == 0
        assert (
            meta["self_heal_last_progress_fingerprint"]
            == "outline_replan:commercial-score-062"
        )

        meta, abandoned = _compute_heal_progress_state(
            meta, chapters_total=50, progress_fingerprint="repair:40"
        )

        assert abandoned is False
        assert meta["self_heal_no_progress_attempts"] == 0
        assert meta["self_heal_last_progress_fingerprint"] == "repair:40"

    def test_same_rank_with_renamed_blockers_is_not_progress(self) -> None:
        meta, _ = _compute_heal_progress_state(
            {},
            chapters_total=0,
            progress_fingerprint="outline_replan:first-codes",
            progress_rank=(6200, -4, -1, 0),
        )
        meta, abandoned = _compute_heal_progress_state(
            meta,
            chapters_total=0,
            progress_fingerprint="outline_replan:renamed-codes",
            progress_rank=(6200, -4, -1, 0),
        )

        assert abandoned is False
        assert meta["self_heal_no_progress_attempts"] == 1

    def test_worse_rank_does_not_replace_best_or_reset_counter(self) -> None:
        meta, _ = _compute_heal_progress_state(
            {}, chapters_total=0, progress_rank=(6200, -4, -1, 0)
        )
        meta, _ = _compute_heal_progress_state(
            meta, chapters_total=0, progress_rank=(5200, -2, -1, 0)
        )

        assert meta["self_heal_no_progress_attempts"] == 1
        assert meta["self_heal_last_progress_rank"] == [6200, -4, -1, 0]

    def test_strictly_better_rank_resets_counter(self) -> None:
        meta, _ = _compute_heal_progress_state(
            {}, chapters_total=0, progress_rank=(5200, -8, -1, 0)
        )
        meta, _ = _compute_heal_progress_state(
            meta, chapters_total=0, progress_rank=(5200, -8, -1, 0)
        )
        assert meta["self_heal_no_progress_attempts"] == 1

        meta, abandoned = _compute_heal_progress_state(
            meta, chapters_total=0, progress_rank=(6200, -4, -1, 0)
        )

        assert abandoned is False
        assert meta["self_heal_no_progress_attempts"] == 0
        assert meta["self_heal_last_progress_rank"] == [6200, -4, -1, 0]

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
    def test_legacy_abandoned_without_fingerprint_is_not_a_runtime_stop(self) -> None:
        p = SimpleNamespace(metadata_json={"self_heal_abandoned": True})
        assert _project_self_heal_abandoned(p) is False
    def test_current_fingerprinted_abandonment_is_a_runtime_stop(self) -> None:
        p = SimpleNamespace(
            metadata_json={
                "self_heal_abandoned": True,
                "self_heal_abandoned_progress_fingerprint": "repair:22,28",
            }
        )
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


def test_outline_replan_fingerprint_tracks_llm_semantic_progress() -> None:
    def project(score: float, codes: list[str]) -> SimpleNamespace:
        return SimpleNamespace(
            metadata_json={
                "production_pause_reason": "outline_semantic_gate_failed",
                "outline_semantic_gate_report": {
                    "findings": [],
                    "promotion_allowed": False,
                    "llm_adjudication": {
                        "overall_score": score,
                        "issues": [{"code": code} for code in codes],
                    },
                },
            }
        )

    first = _outline_replan_progress_fingerprint(project(0.42, ["AGENCY_BREACH"]))
    improved = _outline_replan_progress_fingerprint(project(0.52, ["HOOK_DEBT"]))

    assert first != improved


def test_outline_replan_fingerprint_tracks_progressive_commercial_progress() -> None:
    def project(score: float, codes: list[str]) -> SimpleNamespace:
        return SimpleNamespace(
            metadata_json={
                "production_pause_reason": "volume_outline_gate_failed",
                "outline_commercial_last_failure": {
                    "overall_score": score,
                    "blocking_codes": codes,
                },
            }
        )

    first = _outline_replan_progress_fingerprint(
        project(0.55, ["OPENING_CONFLICT_MISSING"])
    )
    improved = _outline_replan_progress_fingerprint(
        project(0.825, ["LLM_DIMENSION_BELOW_THRESHOLD_KNOWLEDGE_BOUNDARY"])
    )

    assert first != improved


def test_outline_replan_progress_rank_ignores_blocker_renames_at_equal_quality() -> None:
    def project(score: float, codes: list[str]) -> SimpleNamespace:
        return SimpleNamespace(
            metadata_json={
                "outline_commercial_last_failure": {
                    "overall_score": score,
                    "blocking_codes": codes,
                }
            }
        )

    first = _outline_replan_progress_rank(
        project(0.62, ["AGENCY_BREACH", "HOOK_DEBT"])
    )
    renamed = _outline_replan_progress_rank(
        project(0.62, ["KNOWLEDGE_BOUNDARY", "STAKE_VAGUE"])
    )
    improved = _outline_replan_progress_rank(project(0.65, ["HOOK_DEBT"]))

    assert first == renamed
    assert improved > first


def test_outline_replan_progress_rank_uses_retained_best_failed_candidate() -> None:
    project = SimpleNamespace(
        metadata_json={
            "outline_commercial_last_failure": {
                "overall_score": 0.62,
                "blocking_codes": ["REGRESSED_A", "REGRESSED_B", "REGRESSED_C"],
                "recovery_baseline_score": 0.83,
                "recovery_blocking_codes": ["OPENING_PULL", "METHODOLOGY"],
            }
        }
    )

    assert _outline_replan_progress_rank(project)[:2] == (8300, -2)
