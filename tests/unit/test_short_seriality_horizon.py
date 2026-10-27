import json

import pytest

from bestseller.services.concept_tournament import (
    ConceptCandidate,
    _build_seriality_judge_messages,
    _build_seriality_messages,
    _seriality_audit,
    run_concept_tournament,
)


def _candidate(*, endgame: str = "目标章数内完成治理", ch50: str = "") -> ConceptCandidate:
    return ConceptCandidate(
        dimension="probe",
        concept="守山杂役接管失控阵眼，当场逼退夺权长老。",
        mechanism="听见阵眼缺口并重组灵力",
        repeatable_story_unit="诊断阵眼、选择修法、应对反制",
        progress_bar="外门阵眼到州府灵脉",
        question_ladder=("谁改阵眼", "谁借阵夺权", "谁先失网"),
        endgame_direction=endgame,
        unit_count_estimate=13,  # Enough for the 49-chapter case; this test isolates closure.
        unit_frequency="每2-4章一次",
        unit_families=("诊断", "修法", "反制", "治理"),
        renewal_sources=("阵眼故障", "长老反制"),
        accumulation_tracks=("阵眼权限", "门内声望"),
        phase_transitions=("第1-16章", "第17-32章", "第33-49章"),
        opposing_ecology=("夺权长老", "外门执事"),
        ch50=ch50,
    )


def test_short_horizon_uses_endgame_and_keeps_finite_closure_required():
    for chapters in (20, 49):
        assert _seriality_audit(_candidate(), target_chapters=chapters, genre="东方玄幻") is None
        assert "终局收束" in (_seriality_audit(_candidate(endgame=""), target_chapters=chapters, genre="东方玄幻") or "")
        assert "不适用第50章字段" in (
            _seriality_audit(_candidate(ch50="跨五站护送链"), target_chapters=chapters, genre="东方玄幻") or ""
        )
    assert _seriality_audit(_candidate(), target_chapters=50, genre="东方玄幻") == "答不出第50章在打什么"


def test_short_horizon_prompts_do_not_request_ch50():
    _system, short_prompt = _build_seriality_messages(
        candidate=_candidate(), genre="东方玄幻", chapter_count=20
    )
    assert "目标只有20章" in short_prompt
    assert "不要生成第50章字段" in short_prompt
    assert "不得要求跨卷产业或阵营阶段" in short_prompt
    assert "有限篇幅网文结构总编" in _system
    assert "有限闭环证明" in _system
    assert "两阶段发动机" in short_prompt
    assert '"ch50":""' in short_prompt

    _system, long_prompt = _build_seriality_messages(
        candidate=_candidate(), genre="东方玄幻", chapter_count=50
    )
    assert "ch50 必须仍在兑现同一个故事承诺" in long_prompt
    assert '"ch50":"第50章冲突"' in long_prompt
    assert "三级发动机" in long_prompt

    _system, judge_prompt = _build_seriality_judge_messages(candidate=_candidate(), chapter_count=20)
    assert "不得因不存在第50章而扣分" in judge_prompt


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("reported_ch50", "reported_endgame", "expected_reason"),
    [
        ("", "目标章数内完成治理", None),
        ("", "", "缺第20章内终局收束"),
        ("跨五站护送链至远坟残影", "目标章数内完成护送闭环", "目标20章不适用第50章字段"),
    ],
)
async def test_tournament_short_horizon_requires_valid_closure_payload(
    reported_ch50, reported_endgame, expected_reason
):
    base = {
        "concept": "守山杂役接管失控阵眼，当场逼退夺权长老。",
        "mechanism": "听见阵眼缺口并重组灵力",
        "hook_question": "杂役如何逼退长老？",
        "protagonist_identity": "守山杂役",
        "protagonist_private_desire": "保住住处和名额",
        "protagonist_flaw": "过度相信计算",
        "core_abnormality": "听见护山阵求救",
        "opening_crisis": "长老拆阵眼",
        "opponent_system": "夺权长老",
        "decision_proof": "停手就失去阵眼",
        "emotional_promise": "看底层修士逼高位者让步",
    }

    async def generator(_system, _user):
        return json.dumps(base, ensure_ascii=False), None

    async def judge(_system, _user):
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
            }
        ), None

    async def expander(_system, _user):
        return json.dumps(
            {
                "core_promise_invariant": "看杂役用真本事逼高位者让步",
                "repeatable_story_unit": "诊断阵眼、选择修法、应对反制",
                "unit_families": ["诊断", "交易", "公开冲突", "建设"],
                "progress_bar": "外门阵眼到州府灵脉",
                "unit_frequency": "每2-4章一轮阵法冲突",
                "unit_count_estimate": 2,
                "renewal_sources": ["阵眼故障", "长老争权", "盟友关系"],
                "accumulation_tracks": ["权限", "节点", "盟约"],
                "phase_transitions": ["第1-5章起势", "第6-10章反制", "第11-15章扩张", "第16-20章收束"],
                "opposing_ecology": ["长老", "宗门"],
                "mystery_ladder": ["谁改阵眼", "谁借阵夺权", "谁先失网"],
                "ch50": reported_ch50,
                "endgame_direction": reported_endgame,
            },
            ensure_ascii=False,
        ), None

    async def seriality_judge(_system, _user):
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

    result = await run_concept_tournament(
        None,
        None,
        genre="东方玄幻",
        sub_genre="男频",
        chapter_count=20,
        config={
            "enabled": True,
            "candidate_prompt_mode": "current",
            "n_candidates": 2,
            "dimension_pool": ["题材原生行动"],
            "control_candidates": 1,
            "winner_min": 0,
            "seriality_finalist_count": 1,
            "seriality_advisory_min_chapters": 8,
            "seriality_min_chapters": 200,
            "judge_hard_floors": {"catastrophe_floor": 0, "soft_miss_allowance": 20},
        },
        generator=generator,
        judge=judge,
        expander=expander,
        seriality_judge=seriality_judge,
    )

    if expected_reason is None:
        assert result.winner is not None
        assert result.winner.ch50 == ""
        assert result.winner.endgame_direction == reported_endgame
        return
    assert result.winner is None
    assert result.candidates
    assert all(expected_reason in (candidate.rejected_reason or "") for candidate in result.candidates), [
        candidate.rejected_reason for candidate in result.candidates
    ]
