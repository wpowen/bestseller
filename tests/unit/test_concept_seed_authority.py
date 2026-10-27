"""Regression coverage for seed provenance and caller-owned tournament boundaries."""

from __future__ import annotations

# ruff: noqa: RUF001, RUF002, RUF003 — Chinese fixtures are intentional.
import inspect
import json

import pytest

from bestseller.services import concept_tournament as ct

pytestmark = pytest.mark.unit


def test_engine_judge_does_not_promote_a_system_raw_pitch_to_user_authority() -> None:
    raw_pitch = "守山杂役听见护山阵求救，决定先救阵眼再查是谁动了手脚。"

    _, prompt = ct._build_engine_judge_messages(
        kernel={"protagonist_identity": "守山杂役"},
        genre="东方玄幻",
        sub_genre="男频",
        chapter_count=50,
        seed_concept=raw_pitch,
        authoritative_seed_concept="",
    )

    assert "本轮没有用户上位创意" in prompt
    assert f"候选原始创意={raw_pitch}" in prompt
    assert "用户上位创意=" not in prompt
    assert "不构成用户事实" in prompt


def test_tournament_only_threads_seed_as_authority_when_flag_is_true() -> None:
    source = inspect.getsource(ct.run_concept_tournament)

    assert (
        source.count("authoritative_seed_concept=seed_concept if authoritative_seed else \"\"")
        >= 5
    )


def _candidate_payload(label: str) -> dict[str, object]:
    return {
        "concept": f"{label}在宗门灵田里接管失控阵眼，当场逼退想夺权的长老。",
        "mechanism": "他能听见阵眼的缺口，并用现场灵石重新分配灵力。",
        "hook_question": "一个杂役凭什么让长老当场退让？",
        "protagonist_identity": "守山杂役",
        "protagonist_private_desire": "保住自己的住处和师妹的修炼名额",
        "protagonist_flaw": "过度相信自己能算清每一步",
        "core_abnormality": "能听见护山阵的求救",
        "opening_crisis": "长老要拆掉阵眼换走灵脉",
        "opponent_system": "夺权长老和依附他的执事",
        "decision_proof": "求助来不及，停手会失去阵眼和名额",
        "emotional_promise": "看底层修士用一门真本事逼高位者让步",
    }


@pytest.mark.asyncio
async def test_candidate_boundary_check_rejects_one_contender_and_keeps_alternative() -> None:
    payloads = [_candidate_payload("第一候选"), _candidate_payload("第二候选")]
    generated = iter(payloads)
    calls: list[str] = []

    async def generator(system: str, user: str):
        del system, user
        return json.dumps(next(generated), ensure_ascii=False), None

    async def judge(system: str, user: str):
        del system
        marker = next(label for label in ("第一候选", "第二候选") if label in user)
        calls.append(marker)
        return (
            json.dumps(
                {
                    "freshness": 8,
                    "click": 8,
                    "predictable": 2,
                    "character_logic": 8,
                    "mechanism_causality": 8,
                    "genre_fidelity": 8,
                    "plain_language": 8,
                    "story_motion": 8,
                }
            ),
            None,
        )

    boundary_calls: list[str] = []

    def boundary(candidate: ct.ConceptCandidate) -> tuple[str, ...]:
        boundary_calls.append(candidate.concept[:4])
        return ("TEST_BOUNDARY_CODE",) if "第一候选" in candidate.concept else ()

    result = await ct.run_concept_tournament(
        None,
        None,
        genre="东方玄幻",
        sub_genre="男频",
        chapter_count=1,
        config={
            "enabled": True,
            "candidate_prompt_mode": "current",
            "n_candidates": 2,
            "dimension_pool": ["题材原生行动"],
            "control_candidates": 1,
            "winner_min": 5.5,
            "judge_hard_floors": {"catastrophe_floor": 0, "soft_miss_allowance": 20},
            "judge_weights": {"freshness": 0.4, "click": 0.4, "unpredictability": 0.2},
        },
        generator=generator,
        judge=judge,
        candidate_boundary_check=boundary,
    )

    assert result.winner is not None
    assert "第二候选" in result.winner.concept
    rejected = next(candidate for candidate in result.candidates if "第一候选" in candidate.concept)
    assert rejected.rejected_reason == "建书选项/反污染门失败: TEST_BOUNDARY_CODE"
    assert boundary_calls.count("第二候选") == 2
    assert calls


@pytest.mark.asyncio
async def test_candidate_boundary_check_failure_is_a_stable_rejection() -> None:
    payload = _candidate_payload("唯一候选")

    async def generator(system: str, user: str):
        del system, user
        return json.dumps(payload, ensure_ascii=False), None

    async def judge(system: str, user: str):
        del system, user
        return (
            json.dumps(
                {
                    "freshness": 8,
                    "click": 8,
                    "predictable": 2,
                    "character_logic": 8,
                    "mechanism_causality": 8,
                    "genre_fidelity": 8,
                    "plain_language": 8,
                    "story_motion": 8,
                }
            ),
            None,
        )

    def boundary(candidate: ct.ConceptCandidate) -> tuple[str, ...]:
        del candidate
        raise RuntimeError("test boundary unavailable")

    result = await ct.run_concept_tournament(
        None,
        None,
        genre="东方玄幻",
        sub_genre="男频",
        chapter_count=1,
        config={
            "enabled": True,
            "candidate_prompt_mode": "current",
            "n_candidates": 2,
            "dimension_pool": ["题材原生行动"],
            "control_candidates": 1,
            "winner_min": 5.5,
            "judge_hard_floors": {"catastrophe_floor": 0, "soft_miss_allowance": 20},
            "judge_weights": {"freshness": 0.4, "click": 0.4, "unpredictability": 0.2},
        },
        generator=generator,
        judge=judge,
        candidate_boundary_check=boundary,
    )

    assert result.winner is None
    assert result.candidates
    assert all(
        candidate.rejected_reason == "建书选项/反污染门失败: CANDIDATE_BOUNDARY_CHECK_FAILED"
        for candidate in result.candidates
    )


@pytest.mark.asyncio
async def test_candidate_boundary_check_runs_again_after_seriality_expansion() -> None:
    payloads = [_candidate_payload("第一候选"), _candidate_payload("第二候选")]
    generated = iter(payloads)
    expanded_calls = 0

    async def generator(system: str, user: str):
        del system, user
        return json.dumps(next(generated), ensure_ascii=False), None

    async def judge(system: str, user: str):
        del system
        score = 9 if "第一候选" in user else 8
        return (
            json.dumps(
                {
                    "freshness": score,
                    "click": score,
                    "predictable": 2,
                    "character_logic": 8,
                    "mechanism_causality": 8,
                    "genre_fidelity": 8,
                    "plain_language": 8,
                    "story_motion": 8,
                }
            ),
            None,
        )

    async def expander(system: str, user: str):
        del system, user
        nonlocal expanded_calls
        expanded_calls += 1
        drifted = expanded_calls == 1
        payload = {
            "core_promise_invariant": "看杂役用阵法本事逼高位者让步",
            "repeatable_story_unit": "诊断阵眼、选择修法、应对对手反制",
            "unit_families": ["诊断", "交易", "公开冲突", "建设"]
            + (["BOUNDARY_DRIFT"] if drifted else []),
            "progress_bar": "外门阵眼→宗门阵网→州府灵脉",
            "unit_frequency": "每2-4章一次新的阵法冲突或线路事故",
            "unit_count_estimate": 180,
            "renewal_sources": ["各地阵眼故障持续产生冲突", "长老争夺阵网不断制造压力"],
            "accumulation_tracks": ["阵法权限等级", "主角控制的阵网节点"],
            "phase_transitions": [
                "第1-15章外门阵眼",
                "第16-35章宗门阵网",
                "第36-50章州府灵脉",
            ],
            "opposing_ecology": ["夺权长老", "垄断灵脉的宗门"],
            "mystery_ladder": ["谁改了阵眼", "谁在借阵夺权", "谁会先失去阵网"],
            "ch50": "宗门阵网争夺战",
            "endgame_direction": "决定灵脉由谁治理",
        }
        return json.dumps(payload, ensure_ascii=False), None

    def boundary(candidate: ct.ConceptCandidate) -> tuple[str, ...]:
        return ("POST_EXPANSION_BOUNDARY",) if "BOUNDARY_DRIFT" in candidate.unit_families else ()

    result = await ct.run_concept_tournament(
        None,
        None,
        genre="东方玄幻",
        sub_genre="男频",
        chapter_count=50,
        config={
            "enabled": True,
            "candidate_prompt_mode": "current",
            "n_candidates": 2,
            "dimension_pool": ["题材原生行动"],
            "control_candidates": 1,
            "winner_min": 5.5,
            "seriality_advisory_min_chapters": 8,
            "seriality_min_chapters": 200,
            "judge_hard_floors": {"catastrophe_floor": 0, "soft_miss_allowance": 20},
            "judge_weights": {"freshness": 0.4, "click": 0.4, "unpredictability": 0.2},
        },
        generator=generator,
        judge=judge,
        expander=expander,
        candidate_boundary_check=boundary,
    )

    assert expanded_calls == 2
    assert result.winner is not None
    assert "第二候选" in result.winner.concept
    rejected = next(candidate for candidate in result.candidates if "第一候选" in candidate.concept)
    assert rejected.rejected_reason == "建书选项/反污染门失败: POST_EXPANSION_BOUNDARY"
