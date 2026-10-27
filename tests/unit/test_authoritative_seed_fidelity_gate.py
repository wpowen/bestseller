"""The concept tournament must preserve facts from an explicit story seed."""

# ruff: noqa: RUF001, RUF002, RUF003, I001 — Chinese fixtures and private
# contract helpers are intentional here.

from __future__ import annotations

import json

import pytest

from bestseller.services.concept_tournament import (
    ConceptCandidate,
    _authoritative_seed_fidelity_reasons,
    _candidate_hard_rejection_reason,
    run_concept_tournament,
)
from bestseller.services.book_design import extract_creation_protagonist_name


SEED = (
    "边城矿奴陆衡能看见废矿里尚未成形的灵脉走向；宗门要封矿活埋工友，"
    "他当众烧毁卖身契，带人抢先凿穿唯一生路，却挖出了各宗争夺的古战场入口。"
)


def _candidate(**updates: str) -> ConceptCandidate:
    values = {
        "dimension": "人际困局",
        "concept": "陆衡看见废矿里的灵脉走向，决定带工友凿出一条活路。",
        "mechanism": "陆衡能看见废矿里尚未成形的灵脉走向。",
        "protagonist_identity": "边城矿奴陆衡",
        "protagonist_private_desire": "带工友活着离开矿坑",
        "core_abnormality": "能看见废矿里尚未成形的灵脉走向",
        "opening_crisis": "宗门要封矿活埋工友，陆衡必须先凿穿生路",
        "opponent_system": "封矿的宗门",
        "decision_proof": "陆衡烧毁卖身契，带人抢先凿穿生路",
        "emotional_promise": "看底层矿奴靠自己的判断带人破局",
    }
    values.update(updates)
    return ConceptCandidate(**values)


def test_existing_creation_extractor_remains_the_only_name_authority() -> None:
    assert extract_creation_protagonist_name({"premise": "少年陆衡，带工友凿穿矿坑。"}) == "陆衡"
    assert extract_creation_protagonist_name({"premise": "落魄画师欧阳追，替死人画最后一张脸。"}) == "欧阳追"
    assert extract_creation_protagonist_name({"premise": "Alex, a mechanic, repairs a failed machine."}) == "Alex"


def test_authoritative_seed_rejects_a_renamed_repair_candidate() -> None:
    seed = "少年陆衡，能看见未成形的灵脉；宗门封矿时他带工友凿出一条生路。"
    reasons = _authoritative_seed_fidelity_reasons(
        _candidate(
            concept="纪衡看见废矿里的灵脉走向，带人凿出一条活路。",
            mechanism="纪衡能看见废矿里尚未成形的灵脉走向。",
            protagonist_identity="边城矿奴纪衡",
        ),
        seed,
    )
    assert "authoritative_protagonist_missing:陆衡" in reasons


def test_semantic_engine_drift_is_left_to_the_existing_seed_fidelity_judge() -> None:
    reasons = _authoritative_seed_fidelity_reasons(
        _candidate(
            mechanism="陆衡能听见宗门长老的心声。",
            core_abnormality="能听见别人的心声",
            opening_crisis="宗门要审判陆衡，必须找出内奸",
        ),
        "少年陆衡，能看见未成形的灵脉；宗门封矿时他带工友凿出一条生路。",
    )
    assert reasons == ()


def test_unknown_seed_identity_is_recorded_as_unverifiable() -> None:
    reasons = _authoritative_seed_fidelity_reasons(
        _candidate(), "一个矿工遇到异常灵脉，决定带工友逃出矿坑。"
    )
    assert reasons == ()


def test_non_authoritative_seed_keeps_existing_option_gate_behavior() -> None:
    reason = _candidate_hard_rejection_reason(
        _candidate(),
        seed_concept=SEED,
        tone_preference="",
        effect_skills=(),
        authoritative_seed_concept="",
    )
    assert reason is None


async def _run_actual_seed_gate_probe() -> tuple[object, list[str], list[str], int]:
    kernel = {
        "protagonist_identity": "边城矿奴陆衡",
        "protagonist_private_desire": "带工友活着离开矿坑",
        "protagonist_flaw": "只相信自己看见的路线",
        "core_abnormality": "能看见废矿里尚未成形的灵脉走向",
        "current_goal": "在封矿前凿出唯一生路",
        "effective_resistance": "宗门要封矿活埋工友",
        "failure_cost": "工友会被活埋",
        "success_cost": "烧毁卖身契后成为宗门追捕对象",
        "irreversible_change": "工友跟随陆衡离开矿坑并卷入古战场争夺",
        "reader_promise": "看底层矿奴靠判断带人破局",
        "difference_point": "灵脉尚未成形时就能被看见",
        "deformable_loop": "辨认灵脉、选择路线、应对宗门封堵",
        "opening_crisis": "宗门要封矿活埋工友",
        "emotional_promise": "陆衡带工友抢出生路",
        "expansion_axes": ["矿脉路线", "工友队伍", "宗门争夺"],
        "opposing_ecology": ["封矿宗门", "争夺古战场入口的各宗"],
        "scene_seeds": ["看见断脉", "抢挖生路", "应对封矿", "保护工友", "进入古战场"],
        "post_reveal_scene_seeds": ["各宗争夺", "路线反制", "公开选择"],
    }
    prompts: list[str] = []
    judge_prompts: list[str] = []
    premise_calls = 0

    async def generator(system: str, user: str):
        del system
        nonlocal premise_calls
        prompts.append(user)
        if '"ideas"' in user:
            return json.dumps({"ideas": [
                {"lane": "世界规则", "seed": "同一矿奴的灵脉开局"},
                {"lane": "职业处境", "seed": "同一矿奴的封矿危机"},
            ]}, ensure_ascii=False), None
        premise_calls += 1
        if premise_calls == 1:
            return json.dumps({"protagonist_identity": kernel["protagonist_identity"]}, ensure_ascii=False), None
        return json.dumps(kernel, ensure_ascii=False), None

    async def premise_judge(system: str, user: str):
        del system
        judge_prompts.append(user)
        axes = {
            "seed_fidelity": 3, "freshness": 9, "click_seed": 9,
            "source_protagonist_name": "陆衡",
            "seed_identity_source_quote": "边城矿奴陆衡",
            "candidate_identity_quote": kernel["protagonist_identity"],
            "identity_relation": "preserved",
            "action_conflict": 9, "reader_promise": 9, "character_choice": 9,
            "scene_generation": 9, "promise_survival": 9, "deformable_loop": 9,
            "post_reveal_engine": 9, "genre_fidelity": 9,
        }
        # Two generated ideas plus the preserved original control candidate.
        return json.dumps({"verdicts": [{"index": index, **axes} for index in range(3)]}, ensure_ascii=False), None

    result = await run_concept_tournament(
        None, None,
        genre="东方玄幻", sub_genre="东方玄幻", chapter_count=20,
        seed_concept=SEED, authoritative_seed=True,
        config={
            "enabled": True, "n_candidates": 2, "candidate_prompt_mode": "engine_first",
            "raw_idea_pool_multiplier": 1, "raw_idea_generation_batch_size": 2,
            "raw_idea_pool_topup_calls": 0, "story_layer_judge_enabled": False,
            "premise_card_min_survivors": 0,
        },
        generator=generator, premise_judge=premise_judge,
        judge=generator, seriality_judge=generator,
    )
    return result, prompts, judge_prompts, premise_calls


@pytest.mark.asyncio
async def test_actual_seed_reaches_repair_and_semantic_gate_before_promotion(monkeypatch) -> None:
    async def unexpected_provider_call(*args, **kwargs):
        pytest.fail("Unit probe must not call a real provider")
    monkeypatch.setattr("bestseller.services.llm.complete_text", unexpected_provider_call)
    result, prompts, judge_prompts, premise_calls = await _run_actual_seed_gate_probe()
    assert result.winner is None
    assert premise_calls >= 3, "initial cards plus bounded repair must have run"
    assert all(SEED in prompt for prompt in [*prompts, *judge_prompts])
    assert all("seed_fidelity" in rejection.get("failed_axes", []) for rejection in result.engine_rejections)
