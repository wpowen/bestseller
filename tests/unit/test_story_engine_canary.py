from __future__ import annotations

from copy import deepcopy

from bestseller.domain.story_engine import StoryEngineMaturity, canonical_json_hash
from bestseller.services.story_engine_canary import (
    CanaryEvidenceSource,
    aggregate_story_engine_canary_cells,
    evaluate_story_engine_canary_cell,
)


def _receipt(chapter: int) -> dict[str, object]:
    pre_state = {
        "pressure": {"category": "exposure", "value": chapter - 1}
    }
    post_state = {"pressure": {"category": "exposure", "value": chapter}}
    return {
        "artifact_type": "story_transition_receipt_v1",
        "schema_version": "1.0",
        "verdict": "matched",
        "blocking_codes": [],
        "chapter_number": chapter,
        "pre_state_hash": canonical_json_hash(pre_state),
        "post_state": post_state,
        "post_state_hash": canonical_json_hash(post_state),
        "replay_passed": True,
        "receipt": {
            "choice_id": f"choice-{chapter}",
            "observed_action": f"主角执行第{chapter}种具体行动",
            "observed_transitions": [
                {
                    "key": "pressure",
                    "category": "exposure",
                    "before": chapter - 1,
                    "operator": "set",
                    "after": chapter,
                    "evidence": f"第{chapter}章证据",
                    "monotonic": "non_decreasing",
                }
            ],
            "opponent_counteraction": (
                f"对手执行第{chapter}种反制" if chapter <= 8 else ""
            ),
            "new_obligations": ([f"义务-{chapter}"] if chapter > 8 else []),
            "evidence_quotes": [f"第{chapter}章证据"],
            "fingerprint": f"fingerprint-{chapter}",
        },
        "_meta": {"projection_hash": f"fingerprint-{chapter}"},
    }


def _sample() -> list[dict[str, object]]:
    return [_receipt(chapter) for chapter in range(1, 11)]


def test_fixture_cell_can_pass_structure_but_cannot_claim_live_canary() -> None:
    report = evaluate_story_engine_canary_cell(
        _sample(),
        genre="悬疑",
        seed="seed-1",
        evidence_source=CanaryEvidenceSource.FIXTURE,
        engine_prompt_tokens=10_400,
        legacy_prompt_tokens=10_000,
        engine_hard_failures=0,
        legacy_hard_failures=0,
    )

    assert report.structure_passed is True
    assert report.release_status == "PASS_FIXTURE"
    assert report.maturity is StoryEngineMaturity.SHADOW_VALIDATED
    assert report.canary_ready is False
    assert report.receipt_replay_rate == 1.0
    assert report.chapter_reset_count == 0
    assert report.concrete_state_delta_coverage == 1.0
    assert report.opponent_or_obligation_coverage == 1.0
    assert report.prompt_token_ratio == 1.04
    assert "LIVE_CANARY_EVIDENCE_REQUIRED" in report.blocking_codes


def test_canary_cell_detects_state_reset_and_repeated_choice_pattern() -> None:
    receipts = _sample()
    receipts[5]["pre_state_hash"] = "stale-pre-state"
    receipts[4]["receipt"]["fingerprint"] = "fingerprint-1"  # type: ignore[index]
    receipts[5]["receipt"]["fingerprint"] = "fingerprint-1"  # type: ignore[index]

    report = evaluate_story_engine_canary_cell(
        receipts,
        genre="都市",
        seed="seed-2",
        evidence_source=CanaryEvidenceSource.LIVE,
        engine_prompt_tokens=10_000,
        legacy_prompt_tokens=10_000,
        engine_hard_failures=0,
        legacy_hard_failures=0,
    )

    assert report.structure_passed is False
    assert report.canary_ready is False
    assert report.chapter_reset_count == 1
    assert report.repeated_choice_fingerprint_count == 2
    assert "CANARY_CHAPTER_RESET" in report.blocking_codes
    assert "CANARY_REPEATED_CHOICE_FINGERPRINT" in report.blocking_codes


def test_canary_cell_rejects_prompt_regression_above_five_percent() -> None:
    report = evaluate_story_engine_canary_cell(
        _sample(),
        genre="玄幻",
        seed="seed-1",
        evidence_source=CanaryEvidenceSource.LIVE,
        engine_prompt_tokens=10_600,
        legacy_prompt_tokens=10_000,
        engine_hard_failures=0,
        legacy_hard_failures=0,
    )

    assert report.prompt_token_ratio == 1.06
    assert "CANARY_PROMPT_TOKEN_REGRESSION" in report.blocking_codes
    assert report.canary_ready is False


def test_three_genre_two_seed_fixture_cohort_stays_blocked_from_cutover() -> None:
    cells = []
    for genre in ("玄幻", "都市", "悬疑"):
        for seed in ("seed-1", "seed-2"):
            cells.append(
                evaluate_story_engine_canary_cell(
                    deepcopy(_sample()),
                    genre=genre,
                    seed=seed,
                    evidence_source=CanaryEvidenceSource.FIXTURE,
                    engine_prompt_tokens=10_000,
                    legacy_prompt_tokens=10_000,
                    engine_hard_failures=0,
                    legacy_hard_failures=0,
                )
            )

    cohort = aggregate_story_engine_canary_cells(cells)

    assert cohort.cell_count == 6
    assert cohort.genre_count == 3
    assert cohort.minimum_seeds_per_genre == 2
    assert cohort.structure_passed is True
    assert cohort.canary_ready is False
    assert cohort.maturity is StoryEngineMaturity.SHADOW_VALIDATED
    assert cohort.release_status == "PASS_FIXTURE_BLOCKED_LIVE_CANARY"
    assert "LIVE_CANARY_EVIDENCE_REQUIRED" in cohort.blocking_codes


def test_complete_live_cohort_can_reach_canary_validated() -> None:
    cells = [
        evaluate_story_engine_canary_cell(
            _sample(),
            genre=genre,
            seed=seed,
            evidence_source=CanaryEvidenceSource.LIVE,
            engine_prompt_tokens=10_000,
            legacy_prompt_tokens=10_000,
            engine_hard_failures=0,
            legacy_hard_failures=0,
        )
        for genre in ("玄幻", "都市", "悬疑")
        for seed in ("seed-1", "seed-2")
    ]

    cohort = aggregate_story_engine_canary_cells(cells)

    assert cohort.canary_ready is True
    assert cohort.maturity is StoryEngineMaturity.CANARY_VALIDATED
    assert cohort.release_status == "PASS_LIVE_CANARY"
