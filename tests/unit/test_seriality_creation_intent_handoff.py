# ruff: noqa: RUF001, RUF002, RUF003
from __future__ import annotations

import json

import pytest

from bestseller.services import concept_tournament as ct

pytestmark = pytest.mark.unit


def _candidate() -> ct.ConceptCandidate:
    return ct.ConceptCandidate(
        dimension="intent-handoff",
        concept="林澄在旧剧场替陌生人排练道歉，逐步修复自己和姐姐的关系。",
        mechanism="每次代演都暴露一段关系误判，下一位委托人会受前次选择影响。",
        protagonist_identity="旧剧场的兼职提词员林澄",
        protagonist_private_desire="重新获得姐姐的信任",
        core_abnormality="她能在排练中听见对方没有说出口的半句话",
        opening_crisis="姐姐要求她在剧场关闭前完成最后一场代演",
        opponent_system="利用他人误解牟利的经纪公司",
        decision_proof="拒绝代演会让剧场立即关门，公开排练才能留下证据。",
        core_promise_invariant="用带喜感的关系错位推动真诚和解",
        repeatable_story_unit="接下委托、排练误解、主动改词、承担关系后果",
        unit_families=("亲友误读", "搭档拆台", "客户反悔", "公开救场"),
        progress_bar="剧场存续、姐妹信任与公开证据同步增长",
        role_ladder=("提词员", "代演者", "排练负责人", "剧场合伙人"),
        world_ladder=("后台", "整座剧场", "街区演出", "城市巡演"),
        unit_frequency="每2-4章完成一次关系选择",
        unit_count_estimate=30,
        renewal_sources=("前次选择的后果", "委托关系变化", "对手调整策略"),
        accumulation_tracks=("姐姐信任", "剧场伙伴", "公开证据"),
        phase_transitions=("第1-30章", "第31-60章", "第61-100章"),
        opposing_ecology=("经纪公司", "竞争剧场"),
        question_ladder=("能否保住剧场", "姐姐为何离开", "谁在操纵委托"),
        ch50="林澄公开改写巡演合同，迫使经纪公司改变策略",
        endgame_direction="姐妹共同接管剧场并公开经纪公司的操纵链",
    )


@pytest.mark.parametrize(
    ("genre", "sub_genre"),
    [
        ("都市", "轻喜剧女性成长"),
        ("历史", "市井群像诙谐传奇"),
    ],
)
def test_generation_repair_and_judge_receive_the_same_creation_intent(
    genre: str,
    sub_genre: str,
) -> None:
    authority = "用户原始创意：林澄以喜剧代演修复姐妹关系，不能改成复仇或权力扩张。"
    creation_intent = "INTENT_SENTINEL：用户明确选择轻松、喜剧、爽点满足。"
    shared = {
        "candidate": _candidate(),
        "genre": genre,
        "sub_genre": sub_genre,
        "chapter_count": 100,
        "authoritative_seed_concept": authority,
        "tone_preference": "light",
        "effect_skills": ("comedy_engine", "hype_satisfaction_engine"),
        "creation_intent_block": creation_intent,
    }

    prompts = [
        ct._build_seriality_messages(**shared)[1],
        ct._build_seriality_repair_messages(**shared, feedback="承载反馈")[1],
        ct._build_seriality_judge_messages(**shared)[1],
    ]

    for prompt in prompts:
        assert f"{genre}（{sub_genre}）" in prompt
        assert creation_intent in prompt
        assert "调性：轻松" in prompt
        assert "喜剧" in prompt
        assert "爽点满足" in prompt
        assert authority in prompt
        assert "用户上位创意事实边界" in prompt


def test_exploration_seed_never_becomes_seriality_source_authority() -> None:
    sentinel = "MODEL_EXPLORATION_SENTINEL：未被用户确认的近失候选"
    shared = {
        "candidate": _candidate(),
        "genre": "科幻",
        "sub_genre": "近未来职业群像",
        "chapter_count": 100,
        "seed_concept": sentinel,
        "authoritative_seed_concept": "",
        "tone_preference": "light",
        "effect_skills": ("comedy_engine",),
        "creation_intent_block": "用户选择近未来职业群像。",
    }

    generated = ct._build_seriality_messages(**shared)[1]
    repaired = ct._build_seriality_repair_messages(**shared, feedback="承载反馈")[1]

    assert sentinel not in generated
    assert sentinel not in repaired
    assert "用户上位创意事实边界" not in generated
    assert "用户上位创意事实边界" not in repaired


def test_short_horizon_contract_is_unchanged_when_intent_is_present() -> None:
    shared = {
        "candidate": _candidate(),
        "genre": "现实",
        "sub_genre": "轻喜剧女性成长",
        "chapter_count": 20,
        "tone_preference": "light",
        "effect_skills": ("comedy_engine",),
        "creation_intent_block": "用户明确选择20章有限闭环。",
    }

    system, generated = ct._build_seriality_messages(**shared)
    _, judged = ct._build_seriality_judge_messages(**shared)

    assert "有限篇幅网文结构总编" in system
    assert "两阶段发动机" in generated
    assert "不要生成第50章字段" in generated
    assert '"ch50":""' in generated
    assert "不得因不存在第50章而扣分" in judged


@pytest.mark.asyncio
async def test_tournament_threads_intent_to_all_seriality_builders(monkeypatch) -> None:
    captured: dict[str, list[dict[str, object]]] = {
        "generation": [],
        "repair": [],
        "judge": [],
    }
    originals = {
        "generation": ct._build_seriality_messages,
        "repair": ct._build_seriality_repair_messages,
        "judge": ct._build_seriality_judge_messages,
    }

    def capture(name: str):
        def wrapper(**kwargs):
            captured[name].append(dict(kwargs))
            return originals[name](**kwargs)

        return wrapper

    monkeypatch.setattr(ct, "_build_seriality_messages", capture("generation"))
    monkeypatch.setattr(ct, "_build_seriality_repair_messages", capture("repair"))
    monkeypatch.setattr(ct, "_build_seriality_judge_messages", capture("judge"))

    candidate = _candidate()
    candidate_payload = {
        "concept": candidate.concept,
        "mechanism": candidate.mechanism,
        "hook_question": "林澄能否把代演变成真正的和解？",
        "protagonist_identity": candidate.protagonist_identity,
        "protagonist_private_desire": candidate.protagonist_private_desire,
        "protagonist_flaw": "习惯替别人说完答案",
        "core_abnormality": candidate.core_abnormality,
        "opening_crisis": candidate.opening_crisis,
        "opponent_system": candidate.opponent_system,
        "decision_proof": candidate.decision_proof,
        "emotional_promise": "关系错位产生喜感，也留下可见的和解进展",
    }
    seriality_payload = {
        "core_promise_invariant": candidate.core_promise_invariant,
        "role_ladder": list(candidate.role_ladder),
        "world_ladder": list(candidate.world_ladder),
        "repeatable_story_unit": candidate.repeatable_story_unit,
        "unit_families": list(candidate.unit_families),
        "progress_bar": candidate.progress_bar,
        "unit_frequency": candidate.unit_frequency,
        "unit_count_estimate": candidate.unit_count_estimate,
        "renewal_sources": list(candidate.renewal_sources),
        "accumulation_tracks": list(candidate.accumulation_tracks),
        "phase_transitions": list(candidate.phase_transitions),
        "opposing_ecology": list(candidate.opposing_ecology),
        "forward_question_ladder": list(candidate.question_ladder),
        "ch50": candidate.ch50,
        "endgame_direction": candidate.endgame_direction,
    }
    expansion_calls = 0

    async def generate(_system: str, _user: str):
        return json.dumps(candidate_payload, ensure_ascii=False), None

    async def judge(_system: str, _user: str):
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
                "protagonist_agency": 8,
            }
        ), None

    async def expand(_system: str, _user: str):
        nonlocal expansion_calls
        expansion_calls += 1
        payload = dict(seriality_payload)
        if expansion_calls == 1:
            payload["forward_question_ladder"] = []
        return json.dumps(payload, ensure_ascii=False), None

    async def seriality_judge(_system: str, _user: str):
        return json.dumps(
            {
                "renewability": 8,
                "escalation": 8,
                "anti_reset": 8,
                "coherence": 8,
                "promise_survival": 8,
                "unit_density": 8,
            }
        ), None

    authority = "用户原始创意：林澄用喜剧代演修复姐妹关系。"
    creation_intent = "INTENT_SENTINEL：轻松、喜剧、爽点满足。"
    result = await ct.run_concept_tournament(
        None,
        None,
        genre="都市",
        sub_genre="轻喜剧女性成长",
        chapter_count=100,
        config={
            "enabled": True,
            "candidate_prompt_mode": "current",
            "n_candidates": 1,
            "dimension_pool": ["关系错位"],
            "control_candidates": 1,
            "winner_min": 0,
            "seriality_finalist_count": 1,
            "seriality_advisory_min_chapters": 8,
            "seriality_min_chapters": 200,
            "judge_hard_floors": {"catastrophe_floor": 0, "soft_miss_allowance": 20},
        },
        generator=generate,
        judge=judge,
        expander=expand,
        seriality_judge=seriality_judge,
        seed_concept=authority,
        authoritative_seed=True,
        tone_preference="light",
        effect_skills=("comedy_engine", "hype_satisfaction_engine"),
        creation_intent_block=creation_intent,
    )

    assert result.winner is not None, [
        (item.rejected_reason, item.seriality_report, item.seriality_judge)
        for item in result.candidates
    ]
    assert expansion_calls == 2
    # Repair delegates to the generation builder, so generation is captured for
    # both the initial proof and the repair's rebuilt base prompt.
    assert len(captured["generation"]) == 2
    assert len(captured["repair"]) == 1
    assert len(captured["judge"]) == 1
    for call in captured["generation"] + captured["repair"] + captured["judge"]:
        assert call["sub_genre"] == "轻喜剧女性成长"
        assert call["tone_preference"] == "light"
        assert call["effect_skills"] == ("comedy_engine", "hype_satisfaction_engine")
        assert call["creation_intent_block"] == creation_intent
        assert call["authoritative_seed_concept"] == authority
