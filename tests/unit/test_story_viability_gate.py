from copy import deepcopy
import json
from pathlib import Path

from bestseller.services.story_viability import evaluate_story_viability

FIXTURES = Path(__file__).parents[1] / "fixtures" / "story_viability"


def load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def test_rejects_thin_single_joke_at_36_chapters() -> None:
    result = evaluate_story_viability(load("thin_36.json"))
    assert result["passed"] is False
    assert result["estimated_chapter_ceiling"] < 36
    assert {"STATIC_LOOP_REPETITION", "MISSING_RELATIONAL_ENGINE"} <= set(result["blocking_codes"])


def test_rejects_thin_single_joke_at_60_chapters() -> None:
    result = evaluate_story_viability(load("thin_60.json"))
    assert result["passed"] is False
    assert result["estimated_chapter_ceiling"] < 60
    assert "MISSING_COST_TRADEOFF" in result["blocking_codes"]


def test_rejects_thin_single_joke_at_120_chapters() -> None:
    result = evaluate_story_viability(load("thin_120.json"))
    assert result["passed"] is False
    assert result["estimated_chapter_ceiling"] < 120
    assert "MISSING_PROGRESSION_ENGINE" in result["blocking_codes"]


def test_accepts_cross_axis_management_upgrade_conflict_relationship_engine() -> None:
    result = evaluate_story_viability(load("healthy_management_upgrade.json"))
    assert result["passed"] is True
    assert result["blocking_codes"] == []
    assert result["estimated_chapter_ceiling"] >= 60
    assert result["report"]["loop_validation"]["valid_loops"] == 4
    assert result["report"]["engine_axes"] == ["经营", "升级", "冲突", "关系"]


def test_returns_structured_evidence_for_missing_loop_fields() -> None:
    result = evaluate_story_viability({"target_chapters": 36, "chapter_loop": [{"goal": "做事"}]})
    assert "INCOMPLETE_CAUSAL_LOOP" in result["blocking_codes"]
    assert result["report"]["loop_validation"]["missing_fields"]


def test_low_seriality_judge_and_missing_endgame_cannot_be_hidden_by_field_counts() -> None:
    story = deepcopy(load("healthy_management_upgrade.json"))
    story["story_engine"].pop("endgame_direction")
    story["seriality_judge"] = {"renewability": 4, "unit_density": 5, "promise_survival": 3}
    result = evaluate_story_viability(story)
    assert result["passed"] is False
    assert {
        "LOW_SERIALITY_RENEWABILITY",
        "LOW_SERIALITY_PROMISE_SURVIVAL",
        "MISSING_ENDGAME_DIRECTION",
    } <= set(result["blocking_codes"])


def test_low_seriality_judge_is_advisory_when_deterministic_design_is_complete() -> None:
    story = deepcopy(load("healthy_management_upgrade.json"))
    story["seriality_judge"] = {"renewability": 4, "unit_density": 5, "promise_survival": 3}
    result = evaluate_story_viability(story)
    assert result["passed"] is True
    assert result["blocking_codes"] == []
    assert "LOW_SERIALITY_RENEWABILITY" in result["report"]["seriality_findings"]


def test_partial_seriality_judge_is_fail_closed() -> None:
    story = deepcopy(load("healthy_management_upgrade.json"))
    story["seriality_judge"] = {"renewability": 8}
    result = evaluate_story_viability(story)
    assert result["passed"] is False
    assert "UNKNOWN_SERIALITY_JUDGE" in result["blocking_codes"]


def test_seriality_proof_without_deterministic_capacity_report_is_rejected() -> None:
    result = evaluate_story_viability(
        {
            "repeatable_story_unit": "主角选择后改变资源; 对手按新状态反制",
            "renewal_sources": ["行动后果", "对手学习"],
            "accumulation_tracks": ["资源状态", "关系状态"],
            "phase_transitions": ["1-12章", "13-30章", "31-50章"],
            "opposing_ecology": ["竞争关卡", "旧贵族"],
            "endgame_direction": "公开规则与个人垄断之间作出终局选择",
        },
        target_chapters=50,
    )

    assert result["passed"] is False
    assert "MISSING_CAPACITY_REPORT" in result["blocking_codes"]
