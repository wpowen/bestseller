"""Commercial concept scores are P1 evidence when explicitly configured."""

from __future__ import annotations

import json
import random

import pytest

from bestseller.services.concept_tournament import (
    _PROTECTED_HOOK_AXES,
    _parse_finite_hook_scores,
    is_commercial_gate_advisory,
    run_concept_tournament,
)


pytestmark = pytest.mark.unit


_PAYLOAD = {
    "concept": "修复师从损坏录音里复原一段会改变案件走向的原声。",
    "mechanism": "每次修复都暴露新的剪接痕迹，并迫使她在公开证据前选择下一处现场。",
    "hook_question": "谁在火灾前预先录下了死者的姓名？",
    "protagonist_identity": "旧城档案馆的录音修复师",
    "protagonist_private_desire": "保住母亲留下的原带",
    "protagonist_flaw": "过分相信物证而低估关系代价",
    "core_abnormality": "损坏录音里的声音能对应尚未公开的事实",
    "opening_crisis": "拆迁前夜她必须决定是否公开第一段原声",
    "opponent_system": "参与旧案掩盖的消防与开发利益链",
    "decision_proof": "公开会失去母亲的原带，沉默会错过爆破前的证据",
    "emotional_promise": "用具体修复行动逼近一场被掩盖的火灾",
    "core_promise_invariant": "每次修复都改变可追查的证据链",
}


def _config(mode: str) -> dict:
    return {
        "enabled": True,
        "commercial_gate_mode": mode,
        "candidate_prompt_mode": "current",
        "n_candidates": 2,
        "dimension_pool": ["录音修复", "旧城档案"],
        "cliche_seeds": {"generic": []},
        "winner_min": 5.5,
        "judge_hard_floors": {
            "catastrophe_floor": 2.0,
            "soft_miss_allowance": 0,
            "freshness": 7.0,
            "click": 7.0,
            "predictable_max": 6.0,
            "character_logic": 7.0,
            "mechanism_causality": 7.0,
            "genre_fidelity": 7.0,
            "plain_language": 7.0,
            "story_motion": 7.0,
        },
        "judge_weights": {
            "freshness": 0.4,
            "click": 0.4,
            "unpredictability": 0.2,
        },
    }


async def _generator(_system: str, _user: str):
    return json.dumps(_PAYLOAD, ensure_ascii=False), None


async def _commercially_weak_judge(_system: str, _user: str):
    return (
        json.dumps(
            {
                "freshness": 3,
                "click": 4,
                "predictable": 9,
                "character_logic": 8,
                "mechanism_causality": 8,
                "genre_fidelity": 8,
                "plain_language": 4,
                "story_motion": 8,
                "reason": "商业表达偏弱，但因果和题材仍成立",
            },
            ensure_ascii=False,
        ),
        None,
    )


def test_mode_defaults_to_strict_and_protects_causal_axes() -> None:
    assert not is_commercial_gate_advisory({})
    assert is_commercial_gate_advisory({"commercial_gate_mode": "advisory"})
    assert {
        "character_logic",
        "mechanism_causality",
        "story_motion",
        "genre_fidelity",
    } <= _PROTECTED_HOOK_AXES


def test_hook_scores_require_all_finite_bounded_fields_before_clamping() -> None:
    valid = {
        axis: 8
        for axis in (
            "freshness",
            "click",
            "predictable",
            "character_logic",
            "mechanism_causality",
            "genre_fidelity",
            "plain_language",
            "story_motion",
        )
    }
    assert _parse_finite_hook_scores(valid) is not None
    assert _parse_finite_hook_scores({**valid, "click": float("nan")}) is None
    assert _parse_finite_hook_scores({key: value for key, value in valid.items() if key != "click"}) is None
    assert _parse_finite_hook_scores({**valid, "freshness": 11}) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("independent", [False, True])
async def test_advisory_allows_low_commercial_hook_and_persists_quality_findings(independent, monkeypatch) -> None:
    from bestseller.services import concept_tournament as ct

    async def judge_factory(*args, **kwargs):
        return _commercially_weak_judge

    monkeypatch.setattr(ct, "_default_generator", judge_factory)
    result = await run_concept_tournament(
        None,
        None,
        genre="都市",
        sub_genre="悬疑",
        chapter_count=4,
        config={**_config("advisory"), "finalist_judge_model_key": "test-judge"},
        generator=_generator,
        judge=None if independent else _commercially_weak_judge,
        rng=random.Random(3),
    )

    assert result.winner is not None
    assert result.commercial_gate_mode == "advisory"
    findings = result.to_dict()["quality_findings"]
    assert findings
    assert {item["axis"] for item in findings} >= {"freshness", "click", "predictable"}
    assert result.winner.to_dict()["quality_findings"]


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", [{}, {"freshness": float("nan")}])
async def test_independent_review_cannot_promote_invalid_scores(invalid, monkeypatch):
    from bestseller.services import concept_tournament as ct

    async def bad_review(*_):
        return json.dumps(invalid), None

    async def judge_factory(*args, **kwargs):
        return bad_review if kwargs.get("template") == "concept_tournament_finalist_judge" else _commercially_weak_judge

    monkeypatch.setattr(ct, "_default_generator", judge_factory)

    result = await run_concept_tournament(
        None, None, genre="都市", sub_genre="悬疑", chapter_count=4,
        config={**_config("advisory"), "finalist_judge_model_key": "test-judge"}, generator=_generator,
        judge=None,
        rng=random.Random(3),
    )
    assert result.winner is None
    assert any("字段无效或缺失" in (c.rejected_reason or "") for c in result.candidates)


@pytest.mark.asyncio
async def test_strict_still_rejects_the_same_low_commercial_hook() -> None:
    result = await run_concept_tournament(
        None,
        None,
        genre="都市",
        sub_genre="悬疑",
        chapter_count=4,
        config=_config("strict"),
        generator=_generator,
        judge=_commercially_weak_judge,
        rng=random.Random(3),
    )

    assert result.winner is None
    assert result.candidates


@pytest.mark.asyncio
async def test_advisory_does_not_waive_low_causal_hook_axis() -> None:
    async def causal_failure_judge(_system: str, _user: str):
        raw, _ = await _commercially_weak_judge(_system, _user)
        payload = json.loads(raw)
        payload["mechanism_causality"] = 1
        return json.dumps(payload, ensure_ascii=False), None

    result = await run_concept_tournament(
        None,
        None,
        genre="都市",
        sub_genre="悬疑",
        chapter_count=4,
        config=_config("advisory"),
        generator=_generator,
        judge=causal_failure_judge,
        rng=random.Random(3),
    )

    assert result.winner is None


@pytest.mark.asyncio
async def test_engine_advisory_keeps_structural_card_and_records_commercial_miss() -> None:
    kernel = {
        "protagonist_identity": "旧城录音修复师",
        "protagonist_private_desire": "保住母亲原带",
        "protagonist_flaw": "过分相信物证",
        "core_abnormality": "损坏录音指向尚未公开的事实",
        "current_goal": "爆破前复原原声",
        "effective_resistance": "开发方要封存档案",
        "failure_cost": "证据随旧楼消失",
        "success_cost": "公开后失去工作",
        "irreversible_change": "证据链进入公开调查",
        "reader_promise": "用修复行动逼近旧案真相",
        "difference_point": "剪口纹路能锁定拼接设备",
        "deformable_loop": "修复录音、定位设备、追问证人",
        "opening_crisis": "拆迁前夜必须决定是否公开第一段原声",
        "emotional_promise": "每次修复都逼近封存者",
        "scene_seeds": ["复原底噪", "寻找设备", "追问消防员", "公开证据", "锁定剪辑者"],
        "post_reveal_scene_seeds": ["锁定剪辑者", "面对封口", "公开听证"],
        "expansion_axes": ["旧城档案", "火灾证人", "开发利益链"],
        "opposing_ecology": ["开发方", "封存档案的关系网"],
    }

    async def generator(_system: str, user: str):
        if '"ideas"' in user:
            return json.dumps(
                {
                    "ideas": [
                        {
                            "lane": "职业",
                            "seed": "修复师追查损坏录音。",
                            "graft": "录音修复×旧城档案",
                            "opening": "拆迁前夜复原第一段原声",
                            "why_it_keeps_moving": "每次修复都会指向新的证人",
                            "future_situations": ["复原底噪", "寻找设备", "追问证人"],
                        }
                    ]
                },
                ensure_ascii=False,
            ), None
        if "HOOK_DISTILL" in user:
            return json.dumps(
                {"hooks": [{"angle": "identity", "concept": _PAYLOAD["concept"]}]},
                ensure_ascii=False,
            ), None
        return json.dumps(kernel, ensure_ascii=False), None

    async def engine_judge(_system: str, user: str):
        if "verdicts" in user:
            verdict = {
                "index": 0,
                "seed_fidelity": 9,
                "freshness": 2,
                "click_seed": 2,
                "action_conflict": 9,
                "reader_promise": 9,
                "character_choice": 9,
                "scene_generation": 9,
                "promise_survival": 9,
                "deformable_loop": 9,
                "post_reveal_engine": 9,
                "genre_fidelity": 9,
            }
            return json.dumps({"verdicts": [verdict]}, ensure_ascii=False), None
        return json.dumps(
            {
                "freshness": 8,
                "click": 8,
                "predictable": 2,
                "character_logic": 8,
                "mechanism_causality": 8,
                "genre_fidelity": 8,
                "plain_language": 8,
                "story_motion": 8,
            },
            ensure_ascii=False,
        ), None

    config = {
        "enabled": True,
        "commercial_gate_mode": "advisory",
        "candidate_prompt_mode": "engine_first",
        "n_candidates": 1,
        "raw_idea_pool_multiplier": 1,
        "raw_idea_generation_batch_size": 1,
        "raw_idea_pool_topup_calls": 0,
        "premise_card_min_survivors": 0,
        "engine_judge_floor": 7,
        "seriality_finalist_count": 1,
        "judge_hard_floors": {"freshness": 7, "click": 7, "predictable_max": 6},
    }
    result = await run_concept_tournament(
        None,
        None,
        genre="都市",
        sub_genre="悬疑",
        chapter_count=4,
        seed_concept="",
        config=config,
        generator=generator,
        judge=engine_judge,
        premise_judge=engine_judge,
        rng=random.Random(3),
    )

    assert result.winner is not None
    assert any(
        item["stage"] == "premise_review" and "freshness" in item["failed_axes"]
        for item in result.quality_findings
    )
