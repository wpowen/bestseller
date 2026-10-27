"""Ranking output is a JSON wire report before entering conception history."""
from __future__ import annotations

from copy import deepcopy
import json

import pytest

from bestseller.domain.appeal import AppealDimension, BlurbAppealVerdict, TitleAppealVerdict
from bestseller.services.conception_checkpoints import (
    build_conception_checkpoint,
    validate_conception_checkpoint,
)
from bestseller.services.ranking_packaging_gate import evaluate_ranking_candidate_packaging

pytestmark = pytest.mark.unit


def _verdicts():
    title = TitleAppealVerdict(total=88, grade="recommend", dimensions=tuple(
        AppealDimension(key, key, 4.0, 1.0) for key in ("hook_power", "graspable_subject")
    ))
    blurb = BlurbAppealVerdict(total=86, grade="recommend", dimensions=tuple(
        AppealDimension(key, key, 4.0, 1.0) for key in (
            "hook_strength", "emotion_charge", "concreteness", "comprehensibility",
            "open_loop_end", "genre_signal",
        )
    ))
    return title, blurb


def _state(report, title, synopsis):
    entry = {"agent": "final_packaging_promotion_audit", "quality": {"ranking_candidate": report},
             "feedback": {"ranking_candidate": deepcopy(report)}}
    return {
        "premise": synopsis, "title": title, "synopsis": synopsis,
        "writing_profile": {}, "story_spine": {}, "concept_contract": {}, "ctx": {},
        "tags": [], "commercial_brief": {}, "world_model_payload": {},
        "conception_log": [entry], "llm_run_ids": [], "chapter_count": 30, "genre_key": "fixture",
        "repair_quotas": {"final_packaging": 1},
        "last_failure": {"error_type": "RetainedFailure", "conception_log": [deepcopy(entry)]},
    }


@pytest.mark.parametrize(("title_text", "synopsis"), [
    ("铜城夜航", "工匠驾驶旧船穿过风暴，寻找失踪的船队。"),
    ("The Last Delivery", "A courier crosses a flooded city to deliver a missing witness's letter."),
])
@pytest.mark.parametrize(("persona", "effects", "status", "codes"), [
    ({"llm_used": True, "samples": 3, "clicks": 2, "avg_score": 7}, (), "PASS", []),
    ({"llm_used": True, "samples": 3, "clicks": 1, "avg_score": 9}, (), "BLOCKED",
     ["PERSONA_CLICK_RATE_BELOW_RANKING_FLOOR"]),
    ({"llm_used": False, "samples": 0}, (), "INCONCLUSIVE", ["PERSONA_EVIDENCE_UNAVAILABLE"]),
    ({"llm_used": True, "samples": 3, "clicks": 2, "avg_score": 7},
     ("hype_satisfaction_engine", "comedy_engine"), "BLOCKED", ["COMEDY_PACKAGING_REALIZATION_MISSING"]),
])
def test_real_ranking_wire_roundtrips_quality_feedback_and_failure_checkpoint(
    persona, effects, status, codes, title_text, synopsis,
):
    title, blurb = _verdicts()
    original_persona = deepcopy(persona)
    report = evaluate_ranking_candidate_packaging(
        title_verdict=title, blurb_verdict=blurb, persona_report=persona,
        selected_effects=effects, title_text=title_text, blurb_text=synopsis,
    )
    state = _state(report, title_text, synopsis)
    checkpoint = build_conception_checkpoint(state, input_hash="same-stable-input")
    restored = validate_conception_checkpoint(checkpoint, input_hash="same-stable-input")
    assert restored == state == json.loads(json.dumps(state, allow_nan=False))
    assert checkpoint == build_conception_checkpoint(deepcopy(state), input_hash="same-stable-input")
    assert report["status"] == status
    assert report["passed"] is (status == "PASS")
    assert report["blocking_codes"] == codes
    assert [finding["code"] for finding in report["findings"]] == codes
    assert report["metrics"]["selected_effects"] == list(effects)
    assert report["metrics"]["title_total"] == title.total
    assert report["metrics"]["blurb_total"] == blurb.total
    assert persona == original_persona
    assert restored["repair_quotas"]["final_packaging"] == 1
    legacy_containers = deepcopy(report)
    legacy_containers["blocking_codes"] = tuple(report["blocking_codes"])
    legacy_containers["findings"] = tuple(report["findings"])
    legacy_containers["metrics"]["selected_effects"] = tuple(report["metrics"]["selected_effects"])
    assert json.dumps(legacy_containers, ensure_ascii=False, sort_keys=True, allow_nan=False) == json.dumps(
        report, ensure_ascii=False, sort_keys=True, allow_nan=False,
    )


@pytest.mark.asyncio
async def test_actual_packaging_audit_emits_checkpoint_safe_ranking(monkeypatch):
    from bestseller.services import conception

    async def seed_judge(*args, **kwargs):
        return {"passed": True, "hard_conflicts": [], "repair_directives": []}, []

    async def persona_judge(*args, **kwargs):
        return {"llm_used": False, "samples": 0}, "Retained unavailable reader evidence"

    monkeypatch.setattr(conception, "_judge_final_packaging_seed_fidelity", seed_judge)
    monkeypatch.setattr(conception, "_persona_click_advisory", persona_judge)
    _, quality, _, feedback, run_ids = await conception._audit_final_packaging_candidate(
        object(), object(), concept_seed="", title="旧站最后一班车", premise="维修员追查失踪列车。",
        synopsis="维修员循着停运的轨道寻找失踪的乘客，却发现车站仍在接收新车票。",
        tags=[], genre="urban", sub_genre="mystery", language="zh-CN", target_platform="fanqie",
        config={"meets_bar": {"title_min": 0, "blurb_min": 0}}, ctx={"language": "zh-CN"},
        genre_intent_contract=None, writing_profile={}, story_spine={},
    )
    assert quality["ranking_candidate"] == feedback["ranking_candidate"]
    assert quality["ranking_candidate"]["status"] == "INCONCLUSIVE"
    state = _state(quality["ranking_candidate"], "旧站最后一班车", "维修员追查失踪列车。")
    state["conception_log"][0].update(quality=quality, feedback=feedback)
    assert validate_conception_checkpoint(build_conception_checkpoint(state, input_hash="retained"),
                                         input_hash="retained") == state
    assert run_ids == []
