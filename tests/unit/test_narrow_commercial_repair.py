from __future__ import annotations

import json

import pytest

import bestseller.services.concept_tournament as concept_tournament
from bestseller.services.concept_tournament import (
    ConceptTournamentResult,
    _attempt_narrow_commercial_repair,
    _validate_narrow_commercial_failure_evidence,
    run_concept_tournament,
)

pytestmark = pytest.mark.unit


def _card():
    return {
        "protagonist_identity": "矿奴陆衡",
        "core_abnormality": "看见尚未成形的灵脉走向",
        "opening_crisis": "宗门要封矿活埋工友",
        "protagonist_private_desire": "救工友",
        "protagonist_flaw": "不肯求人",
        "current_goal": "凿穿生路",
        "effective_resistance": "宗门封矿",
        "failure_cost": "工友被活埋",
        "success_cost": "暴露能力",
        "irreversible_change": "公开反抗宗门",
        "reader_promise": "用灵脉能力破局",
        "difference_point": "灵脉走向会随人心变化",
        "deformable_loop": "每次凿穿都改变矿脉",
        "emotional_promise": "底层工友反抗",
        "scene_seeds": ["当众烧毁卖身契", "抢先凿穿唯一生路", "场面三", "场面四", "场面五"],
        "post_reveal_scene_seeds": ["揭示一", "揭示二", "揭示三"],
        "expansion_axes": ["矿脉", "宗门", "古战场"],
        "opposing_ecology": ["宗门", "各宗"],
    }


async def _fixture_hook_judge(system, user):
    """Keep tournament wiring tests fully offline.

    ``run_concept_tournament`` uses the injected judge for both the legacy hook
    judge and the story-layer review.  Omitting it makes ``using_default_judge``
    true, which constructs a real provider callback even when generation and
    premise judging are fixtures.  The story-layer parser may ignore this flat
    verdict; the hook judge still receives every required score so the test
    exercises the same downstream path without network/provider fallback.
    """

    del system, user
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
    ), "00000000-0000-4000-8000-000000000099"


def _authoritative_identity_evidence(card, seed, *, relation="same", source_name="陆衡"):
    """Deterministic judge evidence for the authoritative-seed contract."""
    return {
        "source_protagonist_name": source_name,
        "seed_identity_source_quote": seed,
        "candidate_identity_quote": card["protagonist_identity"],
        "identity_relation": relation,
    }


def test_narrow_evidence_requires_single_axis_field_quote_and_defect():
    verdict = {
        "freshness": 6,
        "failure_evidence": [
            {
                "axis": "freshness",
                "candidate_field": "difference_point",
                "quote": "灵脉走向会随人心变化",
                "defect": "机制只给抽象形容，没有连续可变的场景压力",
            }
        ],
    }
    ok, reason, evidence = _validate_narrow_commercial_failure_evidence(
        verdict, card=_card(), failed_axes=["freshness"], seed_fidelity_verified=True
    )
    assert ok is True
    assert reason == ""
    assert evidence["candidate_field"] == "difference_point"


@pytest.mark.parametrize(
    "mutate,expected",
    [
        (
            lambda v: v["failure_evidence"][0].update({"axis": "click_seed"}),
            "axis_mismatch",
        ),
        (
            lambda v: v["failure_evidence"][0].update({"candidate_field": "missing"}),
            "field_missing",
        ),
        (
            lambda v: v["failure_evidence"][0].update({"quote": "不存在的句子"}),
            "quote_not_found",
        ),
        (lambda v: v.update({"failure_evidence": []}), "evidence_missing"),
    ],
)
def test_narrow_evidence_rejects_unbounded_or_unverifiable_claims(mutate, expected):
    verdict = {
        "freshness": 6,
        "failure_evidence": [
            {
                "axis": "freshness",
                "candidate_field": "difference_point",
                "quote": "灵脉走向会随人心变化",
                "defect": "具体缺陷",
            }
        ],
    }
    mutate(verdict)
    ok, reason, _ = _validate_narrow_commercial_failure_evidence(
        verdict, card=_card(), failed_axes=["freshness"], seed_fidelity_verified=True
    )
    assert ok is False
    assert expected in reason


def test_high_score_card_without_failure_evidence_is_legal_when_no_axis_is_below_floor():
    verdict = {"freshness": 9}
    ok, reason, evidence = _validate_narrow_commercial_failure_evidence(
        verdict, card=_card(), failed_axes=[], seed_fidelity_verified=True
    )
    assert (ok, reason, evidence) == (False, "no_repair_needed", None)


@pytest.mark.parametrize("extra_score,allowed", [(7, True), (6, False), (None, False)])
def test_passing_axis_comment_does_not_block_corroborated_single_axis_repair(extra_score, allowed):
    target = {"axis": "post_reveal_engine", "candidate_field": "post_reveal_scene_seeds",
              "quote": "揭示三", "defect": "结尾事件没有承接此前行动", "minimal_fix": "接上前因"}
    verdict = {"post_reveal_engine": 6, "promise_survival": extra_score,
               "failure_evidence": [target, {"axis": "promise_survival", "candidate_field": "deformable_loop",
                                            "quote": "每次凿穿都改变矿脉", "defect": "额外建议"}]}
    ok, _, evidence = _validate_narrow_commercial_failure_evidence(
        verdict, card=_card(), failed_axes=["post_reveal_engine"], seed_fidelity_verified=True)
    assert ok is allowed
    if allowed:
        assert evidence["axis"] == "post_reveal_engine"
        assert len(verdict["failure_evidence"]) == 2


def test_duplicate_target_evidence_remains_ambiguous():
    item = {"axis": "freshness", "candidate_field": "difference_point",
            "quote": "灵脉走向会随人心变化", "defect": "具体缺陷"}
    assert not _validate_narrow_commercial_failure_evidence(
        {"freshness": 6, "failure_evidence": [item, dict(item)]},
        card=_card(), failed_axes=["freshness"], seed_fidelity_verified=True)[0]


@pytest.mark.asyncio
async def test_bounded_recheck_editor_rejudge_promotes_only_axis_scoped_edit():
    card = _card()
    axes = (
        "seed_fidelity",
        "freshness",
        "click_seed",
        "action_conflict",
        "reader_promise",
        "character_choice",
        "scene_generation",
        "promise_survival",
        "deformable_loop",
        "post_reveal_engine",
        "genre_fidelity",
    )
    initial = {axis: 8 for axis in axes}
    initial["freshness"] = 6
    calls: list[str] = []
    result = ConceptTournamentResult()

    async def judge(system: str, user: str):
        calls.append("judge:" + system[:20])
        if "单卡商业复核" in system:
            return json.dumps(
                {
                    **initial,
                    "failure_evidence": [
                        {
                            "axis": "freshness",
                            "candidate_field": "difference_point",
                            "quote": card["difference_point"],
                            "defect": "缺少连续变化",
                            "minimal_fix": "增加可变的场景压力",
                        }
                    ],
                },
                ensure_ascii=False,
            ), "00000000-0000-4000-8000-000000000001"
        revised = dict(initial)
        revised["freshness"] = 8
        return json.dumps(revised, ensure_ascii=False), "00000000-0000-4000-8000-000000000003"

    async def editor(system: str, user: str):
        calls.append("00000000-0000-4000-8000-000000000002")
        edited = dict(card)
        edited["difference_point"] = "灵脉走向会随人心变化，并在每次凿穿后重排"
        return json.dumps(
            {"card": edited}, ensure_ascii=False
        ), "00000000-0000-4000-8000-000000000002"

    revised, status = await _attempt_narrow_commercial_repair(
        card=card,
        dimension="lane",
        premise_seed="边城矿奴陆衡能看见灵脉走向",
        initial_verdict=initial,
        failed_axes=["freshness"],
        authoritative_seed_concept="",
        genre="玄幻",
        sub_genre="东方玄幻",
        chapter_count=20,
        intent_axes=(),
        engine_axes=axes,
        engine_floor=7,
        premise_judge_fn=judge,
        gen_fn=editor,
        result=result,
    )
    assert revised is not None
    assert status["status"] == "promoted"
    assert status["changed_fields"] == ["difference_point"] or set(status["changed_fields"]) == {
        "difference_point"
    }
    assert len(calls) == 3
    assert [str(run_id) for run_id in result.llm_run_ids] == [
        "00000000-0000-4000-8000-000000000001",
        "00000000-0000-4000-8000-000000000002",
        "00000000-0000-4000-8000-000000000003",
    ]


@pytest.mark.asyncio
async def test_bounded_repair_rejects_editor_touching_identity_and_keeps_original():
    card = _card()
    axes = ("seed_fidelity", "freshness")
    initial = {"seed_fidelity": 8, "freshness": 6}
    result = ConceptTournamentResult()

    async def judge(system: str, user: str):
        if "单卡商业复核" in system:
            return json.dumps(
                {
                    **initial,
                    "failure_evidence": [
                        {
                            "axis": "freshness",
                            "candidate_field": "difference_point",
                            "quote": card["difference_point"],
                            "defect": "缺少连续变化",
                        }
                    ],
                }
            ), "00000000-0000-4000-8000-000000000001"
        raise AssertionError("protected editor output must stop before rejudge")

    async def editor(system: str, user: str):
        edited = dict(card)
        edited["protagonist_identity"] = "另一个人"
        return json.dumps({"card": edited}), "00000000-0000-4000-8000-000000000002"

    revised, status = await _attempt_narrow_commercial_repair(
        card=card,
        dimension="lane",
        premise_seed="seed",
        initial_verdict=initial,
        failed_axes=["freshness"],
        authoritative_seed_concept="",
        genre="玄幻",
        sub_genre="东方玄幻",
        chapter_count=20,
        intent_axes=(),
        engine_axes=axes,
        engine_floor=7,
        premise_judge_fn=judge,
        gen_fn=editor,
        result=result,
    )
    assert revised is None
    assert status["reason"] == "editor_changed_protected_or_unrelated_field"
    assert [str(run_id) for run_id in result.llm_run_ids] == [
        "00000000-0000-4000-8000-000000000001",
        "00000000-0000-4000-8000-000000000002",
    ]


@pytest.mark.asyncio
async def test_multiple_failed_axes_never_spends_repair_call():
    result = ConceptTournamentResult()
    calls = []

    async def unexpected(*args):
        calls.append(args)
        raise AssertionError("multi-axis near-pass must stay unresolved")

    revised, status = await _attempt_narrow_commercial_repair(
        card=_card(),
        dimension="lane",
        premise_seed="seed",
        initial_verdict={},
        failed_axes=["freshness", "click_seed"],
        authoritative_seed_concept="",
        genre="玄幻",
        sub_genre="东方玄幻",
        chapter_count=20,
        intent_axes=(),
        engine_axes=("freshness",),
        engine_floor=7,
        premise_judge_fn=unexpected,
        gen_fn=unexpected,
        result=result,
    )
    assert revised is None
    assert status["reason"] == "multiple_or_noncommercial_failed_axes"
    assert calls == []


@pytest.mark.asyncio
async def test_batch_wiring_processes_only_highest_eligible_near_pass(monkeypatch):
    kernel = _card()
    kernel.update({"post_reveal_scene_seeds": ["揭示一", "揭示二", "揭示三"]})
    calls = []

    async def generator(system, user):
        if '"ideas"' in user:
            return json.dumps(
                {"ideas": [{"lane": "甲", "seed": "种子甲"}, {"lane": "乙", "seed": "种子乙"}]},
                ensure_ascii=False,
            ), "00000000-0000-4000-8000-000000000010"
        if "PREMISE_CARD" in user:
            return json.dumps(kernel, ensure_ascii=False), "00000000-0000-4000-8000-000000000011"
        return json.dumps(kernel, ensure_ascii=False), "00000000-0000-4000-8000-000000000012"

    axes = (
        "seed_fidelity",
        "freshness",
        "click_seed",
        "action_conflict",
        "reader_promise",
        "character_choice",
        "scene_generation",
        "promise_survival",
        "deformable_loop",
        "post_reveal_engine",
        "genre_fidelity",
    )

    async def premise_judge(system, user):
        calls.append(user)
        scores = {axis: 8 for axis in axes}
        scores["freshness"] = 6
        if "候选=" in user:
            return json.dumps(
                {"verdicts": [{**scores, "index": 0}, {**scores, "index": 1}]}, ensure_ascii=False
            ), "00000000-0000-4000-8000-000000000013"
        return json.dumps(scores), "00000000-0000-4000-8000-000000000014"

    async def spy(**kwargs):
        calls.append("narrow")
        return None, {"status": "unresolved", "reason": "test_spy"}

    monkeypatch.setattr(concept_tournament, "_attempt_narrow_commercial_repair", spy)
    result = await run_concept_tournament(
        None,
        None,
        genre="玄幻",
        sub_genre="东方玄幻",
        chapter_count=20,
        config={
            "enabled": True,
            "n_candidates": 2,
            "candidate_prompt_mode": "engine_first",
            "raw_idea_prompt_arm": "methodology",
            "raw_idea_pool_topup_calls": 0,
            "raw_idea_floor": 0,
            "raw_idea_progression_floor": 0,
            "premise_card_count": 2,
            "engine_judge_floor": 7,
            "dimension_pool": ["甲", "乙"],
            "premise_card_min_survivors": 0,
        },
        generator=generator,
        judge=_fixture_hook_judge,
        premise_judge=premise_judge,
    )
    assert calls.count("narrow") == 1
    assert result.premise_cards


def test_quote_cannot_be_stitched_across_list_items():
    card = _card()
    card["scene_seeds"] = ["先烧毁卖身契", "再凿穿生路"]
    verdict = {
        "failure_evidence": [
            {
                "axis": "freshness",
                "candidate_field": "scene_seeds",
                "quote": "先烧毁卖身契再凿穿生路",
                "defect": "顺序证据不存在",
            }
        ]
    }
    ok, reason, _ = _validate_narrow_commercial_failure_evidence(
        verdict, card=card, failed_axes=["freshness"], seed_fidelity_verified=True
    )
    assert ok is False
    assert reason == "quote_not_found"


@pytest.mark.asyncio
async def test_quote_evidence_gets_one_strict_correction_without_score_or_card_change():
    card = _card()
    axes = ("seed_fidelity", "freshness")
    initial = {"seed_fidelity": 8, "freshness": 6}
    calls: list[str] = []
    result = ConceptTournamentResult()

    async def judge(system, user):
        calls.append(system)
        if "单卡商业复核" in system:
            return (
                json.dumps(
                    {
                        **initial,
                        "failure_evidence": [
                            {
                                "axis": "freshness",
                                "candidate_field": "difference_point",
                                "quote": "灵脉走向随人心变化",
                                "defect": "引用缺少完整字段内容",
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                "00000000-0000-4000-8000-000000000071",
            )
        if "证据回执校正器" in system:
            return (
                json.dumps(
                    {
                        "failure_evidence": [
                            {
                                "axis": "freshness",
                                "candidate_field": "difference_point",
                                "quote": card["difference_point"],
                                "defect": "引用缺少完整字段内容",
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                "00000000-0000-4000-8000-000000000072",
            )
        return (
            json.dumps({"seed_fidelity": 8, "freshness": 8}),
            "00000000-0000-4000-8000-000000000074",
        )

    async def editor(system, user):
        del system, user
        edited = dict(card)
        edited["difference_point"] += "，并在每次凿穿后重排"
        return (
            json.dumps({"card": edited}, ensure_ascii=False),
            "00000000-0000-4000-8000-000000000073",
        )

    revised, status = await _attempt_narrow_commercial_repair(
        card=card,
        dimension="lane",
        premise_seed="seed",
        initial_verdict=initial,
        failed_axes=["freshness"],
        authoritative_seed_concept="",
        genre="玄幻",
        sub_genre="东方玄幻",
        chapter_count=20,
        intent_axes=(),
        engine_axes=axes,
        engine_floor=7,
        premise_judge_fn=judge,
        gen_fn=editor,
        result=result,
    )
    assert revised is not None
    assert status["status"] == "promoted"
    assert status["evidence_correction"]["attempts"] == 1
    assert status["evidence_correction"]["status"] == "accepted"
    assert [str(run_id) for run_id in result.llm_run_ids] == [
        "00000000-0000-4000-8000-000000000071",
        "00000000-0000-4000-8000-000000000072",
        "00000000-0000-4000-8000-000000000073",
        "00000000-0000-4000-8000-000000000074",
    ]
    # The editor callback is deliberately not added to this judge-call list:
    # recheck + one evidence correction + final rejudge.
    assert len(calls) == 3


@pytest.mark.asyncio
async def test_quote_evidence_correction_cannot_change_scores_or_promote_without_exact_quote():
    card = _card()
    result = ConceptTournamentResult()
    initial = {"seed_fidelity": 8, "freshness": 6}

    async def judge(system, user):
        del user
        if "单卡商业复核" in system:
            return (
                json.dumps(
                    {
                        **initial,
                        "failure_evidence": [
                            {
                                "axis": "freshness",
                                "candidate_field": "difference_point",
                                "quote": "灵脉走向随人心变化",
                                "defect": "需要连续变化",
                            }
                        ],
                    }
                ),
                "00000000-0000-4000-8000-000000000081",
            )
        assert "证据回执校正器" in system
        return (
            json.dumps(
                {
                    "freshness": 9,
                    "failure_evidence": [
                        {
                            "axis": "freshness",
                            "candidate_field": "difference_point",
                            "quote": "不存在的原文",
                            "defect": "需要连续变化",
                        }
                    ],
                }
            ),
            "00000000-0000-4000-8000-000000000082",
        )

    async def editor(*_args):
        raise AssertionError("invalid evidence must stop before editor")

    revised, status = await _attempt_narrow_commercial_repair(
        card=card,
        dimension="lane",
        premise_seed="seed",
        initial_verdict=initial,
        failed_axes=["freshness"],
        authoritative_seed_concept="",
        genre="玄幻",
        sub_genre="东方玄幻",
        chapter_count=20,
        intent_axes=(),
        engine_axes=("seed_fidelity", "freshness"),
        engine_floor=7,
        premise_judge_fn=judge,
        gen_fn=editor,
        result=result,
    )
    assert revised is None
    assert status["reason"] == "evidence_correction_scores_changed"
    assert status["evidence_correction"]["attempts"] == 1
    assert [str(run_id) for run_id in result.llm_run_ids] == [
        "00000000-0000-4000-8000-000000000081",
        "00000000-0000-4000-8000-000000000082",
    ]


@pytest.mark.parametrize(
    "mutation,expected_reason",
    [
        ("candidate_field", "evidence_correction_candidate_field_changed"),
        ("defect", "evidence_correction_defect_changed"),
        ("minimal_fix", "evidence_correction_minimal_fix_changed"),
        ("card", "evidence_correction_schema_extra_keys"),
    ],
)
@pytest.mark.asyncio
async def test_evidence_correction_freezes_everything_except_quote(mutation, expected_reason):
    card = _card()
    initial = {"seed_fidelity": 8, "freshness": 6}
    result = ConceptTournamentResult()

    async def judge(system, user):
        del user
        if "单卡商业复核" in system:
            evidence = {
                "axis": "freshness",
                "candidate_field": "difference_point",
                "quote": "灵脉走向随人心变化",
                "defect": "必须保留的缺陷",
                "minimal_fix": "必须保留的修复",
            }
            return json.dumps({**initial, "failure_evidence": [evidence]}), (
                "00000000-0000-4000-8000-000000000091"
            )
        evidence = {
            "axis": "freshness",
            "candidate_field": "difference_point",
            "quote": card["difference_point"],
            "defect": "必须保留的缺陷",
            "minimal_fix": "必须保留的修复",
        }
        if mutation == "candidate_field":
            evidence["candidate_field"] = "scene_seeds"
        elif mutation == "defect":
            evidence["defect"] = "换掉的缺陷"
        elif mutation == "minimal_fix":
            evidence["minimal_fix"] = "换掉的修复"
        payload = {"failure_evidence": [evidence]}
        if mutation == "card":
            payload["card"] = card
        return json.dumps(payload), "00000000-0000-4000-8000-000000000092"

    async def editor(*_args):
        raise AssertionError("frozen evidence must stop before editor")

    revised, status = await _attempt_narrow_commercial_repair(
        card=card,
        dimension="lane",
        premise_seed="seed",
        initial_verdict=initial,
        failed_axes=["freshness"],
        authoritative_seed_concept="",
        genre="玄幻",
        sub_genre="东方玄幻",
        chapter_count=20,
        intent_axes=(),
        engine_axes=("seed_fidelity", "freshness"),
        engine_floor=7,
        premise_judge_fn=judge,
        gen_fn=editor,
        result=result,
    )
    assert revised is None
    assert status["reason"] == expected_reason
    assert status["evidence_correction"]["attempts"] == 1


@pytest.mark.parametrize(
    "evidence,reason",
    [
        (
            {"source_protagonist_name": "陆衡", "seed_identity_source_quote": "边城矿奴陆衡"},
            "candidate_identity_quote_missing",
        ),
        (
            {
                "source_protagonist_name": "陆衡",
                "seed_identity_source_quote": "边城矿奴陆衡",
                "candidate_identity_quote": "矿奴周壑",
                "identity_relation": "replaced",
            },
            "candidate_identity_quote_not_in_protagonist_identity",
        ),
        (
            {
                "source_protagonist_name": "陆衡",
                "seed_identity_source_quote": "边城矿奴陆衡",
                "candidate_identity_quote": "矿奴陆衡",
                "identity_relation": "replaced",
            },
            "protagonist_identity_replaced",
        ),
    ],
)
def test_authoritative_seed_identity_missing_or_replaced_cannot_verify(evidence, reason):
    verdict = {"seed_fidelity": 9, **evidence}
    result = concept_tournament._validate_engine_seed_fidelity_evidence(
        verdict, authoritative_seed="边城矿奴陆衡能看见灵脉走向", kernel=_card()
    )
    assert result["status"] in {"unknown", "invalid"}
    assert result["reason"] == reason


@pytest.mark.asyncio
async def test_narrow_repair_accepts_authoritative_seed_only_with_verified_identity():
    card = _card()
    seed = "边城矿奴陆衡能看见灵脉走向"
    axes = ("seed_fidelity", "freshness")
    initial = {"seed_fidelity": 8, "freshness": 6, **_authoritative_identity_evidence(card, seed)}

    async def judge(system, user):
        del user
        if "单卡商业复核" in system:
            return json.dumps(
                {
                    **initial,
                    "failure_evidence": [
                        {
                            "axis": "freshness",
                            "candidate_field": "difference_point",
                            "quote": card["difference_point"],
                            "defect": "具体缺陷",
                        }
                    ],
                }
            ), "00000000-0000-4000-8000-000000000021"
        return json.dumps(
            {"seed_fidelity": 8, "freshness": 8, **_authoritative_identity_evidence(card, seed)}
        ), "00000000-0000-4000-8000-000000000023"

    async def editor(system, user):
        del system, user
        edited = dict(card)
        edited["difference_point"] += "，并在每次凿穿后重排"
        return json.dumps(
            {"card": edited}, ensure_ascii=False
        ), "00000000-0000-4000-8000-000000000022"

    revised, status = await _attempt_narrow_commercial_repair(
        card=card,
        dimension="lane",
        premise_seed=seed,
        initial_verdict=initial,
        failed_axes=["freshness"],
        authoritative_seed_concept=seed,
        genre="玄幻",
        sub_genre="东方玄幻",
        chapter_count=20,
        intent_axes=(),
        engine_axes=axes,
        engine_floor=7,
        premise_judge_fn=judge,
        gen_fn=editor,
        result=ConceptTournamentResult(),
    )
    assert revised is not None
    assert status["status"] == "promoted"
    assert status["recheck_seed_fidelity_evidence"]["status"] == "verified"
    assert status["final_seed_fidelity_evidence"]["status"] == "verified"


@pytest.mark.parametrize("phase", ["recheck", "editor", "rejudge"])
@pytest.mark.asyncio
async def test_narrow_repair_rejects_nil_or_invalid_run_id_at_each_phase(phase):
    card = _card()
    axes = ("seed_fidelity", "freshness")
    initial = {"seed_fidelity": 8, "freshness": 6}
    valid = {
        "recheck": "00000000-0000-4000-8000-000000000031",
        "editor": "00000000-0000-4000-8000-000000000032",
        "rejudge": "00000000-0000-4000-8000-000000000033",
    }
    bad = {
        "recheck": "00000000-0000-0000-0000-000000000000",
        "editor": None,
        "rejudge": "not-a-uuid",
    }

    async def judge(system, user):
        del user
        if "单卡商业复核" in system:
            return json.dumps(
                {
                    **initial,
                    "failure_evidence": [
                        {
                            "axis": "freshness",
                            "candidate_field": "difference_point",
                            "quote": card["difference_point"],
                            "defect": "具体缺陷",
                        }
                    ],
                }
            ), bad["recheck"] if phase == "recheck" else valid["recheck"]
        return json.dumps({"seed_fidelity": 8, "freshness": 8}), bad[
            "rejudge"
        ] if phase == "rejudge" else valid["rejudge"]

    async def editor(system, user):
        del system, user
        edited = dict(card)
        edited["difference_point"] += "，并在每次凿穿后重排"
        return json.dumps({"card": edited}), bad["editor"] if phase == "editor" else valid["editor"]

    revised, status = await _attempt_narrow_commercial_repair(
        card=card,
        dimension="lane",
        premise_seed="seed",
        initial_verdict=initial,
        failed_axes=["freshness"],
        authoritative_seed_concept="",
        genre="玄幻",
        sub_genre="东方玄幻",
        chapter_count=20,
        intent_axes=(),
        engine_axes=axes,
        engine_floor=7,
        premise_judge_fn=judge,
        gen_fn=editor,
        result=ConceptTournamentResult(),
    )
    assert revised is None
    assert status["reason"] == phase + "_run_id_invalid"


@pytest.mark.asyncio
async def test_editor_exception_preserves_recheck_raw_and_recheck_id():
    card = _card()
    raw = json.dumps(
        {
            "seed_fidelity": 8,
            "freshness": 6,
            "failure_evidence": [
                {
                    "axis": "freshness",
                    "candidate_field": "difference_point",
                    "quote": card["difference_point"],
                    "defect": "具体缺陷",
                }
            ],
        }
    )

    async def judge(system, user):
        del user
        if "单卡商业复核" in system:
            return raw, "00000000-0000-4000-8000-000000000041"
        raise AssertionError("rejudge must not run")

    async def editor(system, user):
        del system, user
        raise RuntimeError("fixture editor failure")

    revised, status = await _attempt_narrow_commercial_repair(
        card=card,
        dimension="lane",
        premise_seed="seed",
        initial_verdict={"seed_fidelity": 8, "freshness": 6},
        failed_axes=["freshness"],
        authoritative_seed_concept="",
        genre="玄幻",
        sub_genre="东方玄幻",
        chapter_count=20,
        intent_axes=(),
        engine_axes=("seed_fidelity", "freshness"),
        engine_floor=7,
        premise_judge_fn=judge,
        gen_fn=editor,
        result=ConceptTournamentResult(),
    )
    assert revised is None
    assert status["reason"] == "editor_exception"
    assert status["recheck_raw"] == raw
    assert status["recheck_run_id"] == "00000000-0000-4000-8000-000000000041"


@pytest.mark.parametrize(
    "mutation,reason",
    [
        ("delete", "editor_card_keys_changed"),
        ("none", "editor_field_type_changed"),
        ("reader_promise", "editor_changed_protected_or_unrelated_field"),
        ("current_goal", "editor_changed_protected_or_unrelated_field"),
    ],
)
@pytest.mark.asyncio
async def test_editor_cannot_delete_add_none_or_change_protected_fields(mutation, reason):
    card = _card()
    initial = {"seed_fidelity": 8, "freshness": 6}

    async def judge(system, user):
        del user
        if "单卡商业复核" in system:
            return json.dumps(
                {
                    **initial,
                    "failure_evidence": [
                        {
                            "axis": "freshness",
                            "candidate_field": "difference_point",
                            "quote": card["difference_point"],
                            "defect": "具体缺陷",
                        }
                    ],
                }
            ), "00000000-0000-4000-8000-000000000051"
        raise AssertionError("invalid editor output must stop before rejudge")

    async def editor(system, user):
        del system, user
        edited = dict(card)
        if mutation == "delete":
            del edited["difference_point"]
        elif mutation == "none":
            edited["difference_point"] = None
        else:
            edited[mutation] = edited[mutation] + "（篡改）"
        return json.dumps(
            {"card": edited}, ensure_ascii=False
        ), "00000000-0000-4000-8000-000000000052"

    revised, status = await _attempt_narrow_commercial_repair(
        card=card,
        dimension="lane",
        premise_seed="seed",
        initial_verdict=initial,
        failed_axes=["freshness"],
        authoritative_seed_concept="",
        genre="玄幻",
        sub_genre="东方玄幻",
        chapter_count=20,
        intent_axes=(),
        engine_axes=("seed_fidelity", "freshness"),
        engine_floor=7,
        premise_judge_fn=judge,
        gen_fn=editor,
        result=ConceptTournamentResult(),
    )
    assert revised is None
    assert status["reason"] == reason


@pytest.mark.asyncio
async def test_three_card_batch_repairs_only_highest_eligible_and_rebinds_final_review():
    base = _card()
    seeds = ("种子甲", "种子乙", "种子丙")
    identities = ("矿奴甲", "矿奴乙", "矿奴丙")
    repair_calls = []
    axes = (
        "seed_fidelity",
        "freshness",
        "click_seed",
        "action_conflict",
        "reader_promise",
        "character_choice",
        "scene_generation",
        "promise_survival",
        "deformable_loop",
        "post_reveal_engine",
        "genre_fidelity",
    )

    async def generator(system, user):
        del system
        if '"ideas"' in user:
            return json.dumps(
                {"ideas": [{"lane": "甲", "seed": seed} for seed in seeds]}, ensure_ascii=False
            ), "00000000-0000-4000-8000-000000000061"
        if "PREMISE_CARD" in user:
            card = dict(base)
            index = next(i for i, seed in enumerate(seeds) if seed in user)
            card["protagonist_identity"] = identities[index]
            card["difference_point"] = f"差异机制{index}"
            return json.dumps(card, ensure_ascii=False), "00000000-0000-4000-8000-000000000062"
        return json.dumps(
            {"concept": "fixture"}, ensure_ascii=False
        ), "00000000-0000-4000-8000-000000000063"

    async def premise_judge(system, user):
        del system
        if "候选=" not in user:
            return json.dumps(
                {"seed_fidelity": 8, "freshness": 8, "click_seed": 8}
            ), "00000000-0000-4000-8000-000000000064"
        scores = []
        for index in range(3):
            verdict = {axis: (10 if index == 0 else 8 if index == 1 else 7) for axis in axes}
            verdict["index"] = index
            verdict["freshness"] = 6
            verdict["click_seed"] = 6 if index == 0 else 8 - index
            verdict["failure_evidence"] = [
                {
                    "axis": "freshness",
                    "candidate_field": "difference_point",
                    "quote": f"差异机制{index}",
                    "defect": "具体缺陷",
                }
            ]
            scores.append(verdict)
        return json.dumps(
            {"verdicts": scores}, ensure_ascii=False
        ), "00000000-0000-4000-8000-000000000065"

    async def narrow_spy(**kwargs):
        card = kwargs["card"]
        repair_calls.append(card["protagonist_identity"])
        return dict(card), {
            "status": "promoted",
            "scores": {axis: 8 for axis in axes},
            "final_seed_fidelity_evidence": {"status": "not_applicable"},
            "final_verdict": {"seed_fidelity": 8, "freshness": 8, "click_seed": 8},
            "rejudge_run_id": "00000000-0000-4000-8000-000000000066",
            "edited_card_hash": concept_tournament._narrow_card_hash(card),
        }

    original = concept_tournament._attempt_narrow_commercial_repair
    concept_tournament._attempt_narrow_commercial_repair = narrow_spy
    try:
        result = await run_concept_tournament(
            None,
            None,
            genre="玄幻",
            sub_genre="东方玄幻",
            chapter_count=20,
            config={
                "enabled": True,
                "n_candidates": 3,
                "candidate_prompt_mode": "engine_first",
                "raw_idea_prompt_arm": "methodology",
                "raw_idea_pool_topup_calls": 0,
                "raw_idea_floor": 0,
                "raw_idea_progression_floor": 0,
                "premise_card_count": 3,
                "engine_judge_floor": 7,
                "dimension_pool": ["甲", "乙", "丙"],
                "premise_card_min_survivors": 0,
            },
            generator=generator,
            judge=_fixture_hook_judge,
            premise_judge=premise_judge,
        )
    finally:
        concept_tournament._attempt_narrow_commercial_repair = original

    assert repair_calls == ["矿奴乙"]
    selected = next(
        record
        for record in result.premise_cards
        if record["card"]["protagonist_identity"] == "矿奴乙"
    )
    review = selected["premise_review"]
    assert review["review_mode"] == "narrow_rejudge"
    assert review["run_id"] == "00000000-0000-4000-8000-000000000066"
    assert review["card_hash"] == concept_tournament._narrow_card_hash(selected["card"])
    assert review["run_id"] != review.get("batch_run_id")


@pytest.mark.asyncio
async def test_non_uuid_is_unresolved():
    card = _card()
    axes = ("seed_fidelity", "freshness")
    initial = {"seed_fidelity": 8, "freshness": 6}
    editor_calls = []

    async def judge(system, user):
        return json.dumps(
            {
                **initial,
                "failure_evidence": [
                    {
                        "axis": "freshness",
                        "candidate_field": "difference_point",
                        "quote": card["difference_point"],
                        "defect": "具体缺陷",
                    }
                ],
            }
        ), "not-a-uuid"

    async def editor(system, user):
        editor_calls.append(1)
        return json.dumps({"card": card}), "00000000-0000-4000-8000-000000000002"

    revised, status = await _attempt_narrow_commercial_repair(
        card=card,
        dimension="lane",
        premise_seed="seed",
        initial_verdict=initial,
        failed_axes=["freshness"],
        authoritative_seed_concept="",
        genre="玄幻",
        sub_genre="东方玄幻",
        chapter_count=20,
        intent_axes=(),
        engine_axes=axes,
        engine_floor=7,
        premise_judge_fn=judge,
        gen_fn=editor,
        result=ConceptTournamentResult(),
    )
    assert revised is None
    assert status["reason"] == "recheck_run_id_invalid"
    assert editor_calls == []


@pytest.mark.asyncio
async def test_rejudge_new_axis_failure_cannot_promote():
    card = _card()
    axes = ("seed_fidelity", "freshness", "click_seed")
    initial = {"seed_fidelity": 8, "freshness": 6, "click_seed": 8}

    async def judge(system, user):
        if "单卡商业复核" in system:
            return json.dumps(
                {
                    **initial,
                    "failure_evidence": [
                        {
                            "axis": "freshness",
                            "candidate_field": "difference_point",
                            "quote": card["difference_point"],
                            "defect": "具体缺陷",
                        }
                    ],
                }
            ), "00000000-0000-4000-8000-000000000011"
        return json.dumps(
            {"seed_fidelity": 8, "freshness": 8, "click_seed": 6}
        ), "00000000-0000-4000-8000-000000000013"

    async def editor(system, user):
        edited = dict(card)
        edited["difference_point"] += "，增加连续变化"
        return json.dumps({"card": edited}), "00000000-0000-4000-8000-000000000012"

    revised, status = await _attempt_narrow_commercial_repair(
        card=card,
        dimension="lane",
        premise_seed="seed",
        initial_verdict=initial,
        failed_axes=["freshness"],
        authoritative_seed_concept="",
        genre="玄幻",
        sub_genre="东方玄幻",
        chapter_count=20,
        intent_axes=(),
        engine_axes=axes,
        engine_floor=7,
        premise_judge_fn=judge,
        gen_fn=editor,
        result=ConceptTournamentResult(),
    )
    assert revised is None
    assert status["reason"] == "rejudge_no_improvement_or_new_failure"
    assert status["new_failed_axes"] == ["click_seed"]


@pytest.mark.asyncio
async def test_narrow_repair_rejects_reused_phase_uuid_without_duplicate_receipt():
    card = _card()
    axes = ("seed_fidelity", "freshness")
    initial = {"seed_fidelity": 8, "freshness": 6}
    reused = "00000000-0000-4000-8000-000000000071"
    calls = []
    result = ConceptTournamentResult()

    async def judge(system, user):
        calls.append((system, user))
        if "单卡商业复核" in system:
            return json.dumps(
                {
                    **initial,
                    "failure_evidence": [
                        {
                            "axis": "freshness",
                            "candidate_field": "difference_point",
                            "quote": card["difference_point"],
                            "defect": "具体缺陷",
                        }
                    ],
                }
            ), reused
        return json.dumps({"seed_fidelity": 8, "freshness": 8}), reused

    async def editor(system, user):
        del system, user
        edited = dict(card)
        edited["difference_point"] += "，增加连续变化"
        return json.dumps({"card": edited}), reused

    revised, status = await _attempt_narrow_commercial_repair(
        card=card,
        dimension="lane",
        premise_seed="seed",
        initial_verdict=initial,
        failed_axes=["freshness"],
        authoritative_seed_concept="",
        genre="玄幻",
        sub_genre="东方玄幻",
        chapter_count=20,
        intent_axes=(),
        engine_axes=axes,
        engine_floor=7,
        premise_judge_fn=judge,
        gen_fn=editor,
        result=result,
    )
    assert revised is None
    assert status["reason"] == "editor_run_id_reused"
    assert status["recheck_run_id"] == reused
    assert [str(run_id) for run_id in result.llm_run_ids] == [reused]
    assert status["recheck_prompt_hash"] != status["editor_prompt_hash"]
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_failed_unique_near_pass_is_not_rescued_by_survivor_quota(monkeypatch):
    card = _card()
    axes = (
        "seed_fidelity",
        "freshness",
        "click_seed",
        "action_conflict",
        "reader_promise",
        "character_choice",
        "scene_generation",
        "promise_survival",
        "deformable_loop",
        "post_reveal_engine",
        "genre_fidelity",
    )
    repair_calls = []

    async def generator(system, user):
        del system
        if '"ideas"' in user:
            return json.dumps(
                {"ideas": [{"lane": "唯一近通过", "seed": "种子甲"}]}, ensure_ascii=False
            ), "00000000-0000-4000-8000-000000000081"
        if "PREMISE_CARD" in user:
            return json.dumps(card, ensure_ascii=False), "00000000-0000-4000-8000-000000000082"
        return json.dumps(
            {"concept": "fixture"}, ensure_ascii=False
        ), "00000000-0000-4000-8000-000000000083"

    async def premise_judge(system, user):
        del system
        if "候选=" not in user:
            return json.dumps({axis: 8 for axis in axes}), "00000000-0000-4000-8000-000000000084"
        verdict = {axis: 8 for axis in axes}
        verdict["freshness"] = 6
        verdict["failure_evidence"] = [
            {
                "axis": "freshness",
                "candidate_field": "difference_point",
                "quote": card["difference_point"],
                "defect": "具体缺陷",
            }
        ]
        return json.dumps(
            {"verdicts": [{**verdict, "index": 0}]}, ensure_ascii=False
        ), "00000000-0000-4000-8000-000000000085"

    async def failed_repair(**kwargs):
        repair_calls.append(kwargs["card"]["protagonist_identity"])
        return None, {"status": "unresolved", "reason": "fixture_repair_failed"}

    monkeypatch.setattr(concept_tournament, "_attempt_narrow_commercial_repair", failed_repair)
    result = await run_concept_tournament(
        None,
        None,
        genre="玄幻",
        sub_genre="东方玄幻",
        chapter_count=20,
        config={
            "enabled": True,
            "n_candidates": 1,
            "candidate_prompt_mode": "engine_first",
            "raw_idea_prompt_arm": "methodology",
            "raw_idea_pool_topup_calls": 0,
            "raw_idea_pool_multiplier": 1,
            "raw_idea_floor": 0,
            "raw_idea_progression_floor": 0,
            "premise_card_count": 1,
            "engine_judge_floor": 7,
            "dimension_pool": ["唯一近通过"],
            "premise_card_min_survivors": 1,
        },
        generator=generator,
        judge=_fixture_hook_judge,
        premise_judge=premise_judge,
    )

    assert repair_calls == [card["protagonist_identity"]]
    assert result.winner is None
    assert result.premise_cards[0]["narrow_revision"]["reason"] == "fixture_repair_failed"
    assert any(
        "窄范围商业修复未确认，不晋升" in rejection["reason"]
        for rejection in result.engine_rejections
    )
