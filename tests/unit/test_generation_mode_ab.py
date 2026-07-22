from __future__ import annotations

import json

from bestseller.services.generation_mode_ab import (
    MODE_CHAPTER_FIRST,
    MODE_SCENE_BY_SCENE,
    GeneratedSample,
    PairwiseJudgement,
    build_chapter_first_prompts,
    build_default_cases,
    build_pairwise_judge_prompts,
    build_scene_prompts,
    parse_pairwise_judgement,
    score_generated_sample,
    summarize_experiment,
)


def test_default_cases_cover_three_non_suspense_chapter_functions() -> None:
    cases = build_default_cases()

    assert len(cases) == 3
    assert {case.chapter_function for case in cases} == {
        "opening_pressure",
        "relationship_conflict",
        "public_payoff",
    }
    assert all(case.genre == "现实经营" for case in cases)
    assert all(len(case.scene_beats) == 3 for case in cases)


def test_generated_sample_records_that_fake_data_is_not_a_live_fallback() -> None:
    sample = GeneratedSample.fake(
        case_id="c1",
        mode=MODE_CHAPTER_FIRST,
        deterministic_coverage=1.0,
    )

    assert sample.fallback_used is False


def test_two_modes_share_contract_but_expose_different_generation_units() -> None:
    case = build_default_cases()[0]
    scene_system, scene_user = build_scene_prompts(case, case.scene_beats[0], previous_tail="")
    chapter_system, chapter_user = build_chapter_first_prompts(case)

    for shared in (case.title, case.chapter_goal, *case.participants):
        assert shared in scene_user
        assert shared in chapter_user
    assert case.scene_beats[0].event in scene_user
    assert all(beat.event in chapter_user for beat in case.scene_beats)
    assert "当前生产单元" in scene_user
    assert "一次性写完整章" in chapter_user
    assert "弱场景逻辑地图" in chapter_user
    assert "不得照抄" in chapter_system
    assert "不提供句子、对白、描写或段落结构" in chapter_user
    assert "不要输出场景标题" in scene_system
    assert "不要输出场景标题" in chapter_system


def test_deterministic_score_tracks_ai_flavor_and_required_event_order() -> None:
    case = build_default_cases()[0]
    clean_text = (
        "林见夏把停电通知贴到玻璃门上。供电所的人收起梯子，限她今天补齐旧账。\n\n"
        "面团已经发过头，她拔掉展示柜，把冷藏黄油搬进邻店冰柜。母亲守着收款码，没接卖店中介的电话。\n\n"
        "她把婚宴订单拆成三批，先送不怕回温的咸点。第一车推出门时，催费单还压在案板下。"
    )
    ai_text = (
        "她深吸一口气，手腕微微发烫。她突然明白，这不是一次停电，而是命运给她的考验。"
        "\n\n场景二\n\n她很紧张。\n\n场景三\n\n事情终于迎来了转机。"
    )

    clean = score_generated_sample(clean_text, case)
    ai = score_generated_sample(ai_text, case)

    assert clean.required_event_coverage == 1.0
    assert clean.required_event_order == 1.0
    assert clean.visible_scene_heading_count == 0
    assert clean.ai_flavor_score < ai.ai_flavor_score
    assert ai.visible_scene_heading_count == 2


def test_case_specific_literal_debt_terms_do_not_count_as_debt_metaphor_ai_flavor() -> None:
    case = build_default_cases()[1]
    text = "林见夏翻到账本最后一页。欠条写着十二万，林望的签名压在还款日期下面。"

    score = score_generated_sample(text, case)

    assert "debt_metaphor_leak" not in score.ai_pattern_counts
    assert score.ai_flavor_score == 0


def test_pairwise_prompt_is_blind_and_position_swappable() -> None:
    case = build_default_cases()[0]
    left = "甲正文"
    right = "乙正文"
    system, forward = build_pairwise_judge_prompts(
        case,
        left_text=left,
        right_text=right,
        judge_label="judge-x",
        swapped=False,
    )
    _, backward = build_pairwise_judge_prompts(
        case,
        left_text=left,
        right_text=right,
        judge_label="judge-x",
        swapped=True,
    )

    assert MODE_SCENE_BY_SCENE not in system + forward + backward
    assert MODE_CHAPTER_FIRST not in system + forward + backward
    assert forward.index(left) < forward.index(right)
    assert backward.index(right) < backward.index(left)


def test_parse_pairwise_judgement_maps_swapped_scores_back_to_modes() -> None:
    raw = json.dumps(
        {
            "scores": {
                "A": {"anti_ai": 8, "logic": 7, "story": 6, "readability": 9},
                "B": {"anti_ai": 5, "logic": 6, "story": 7, "readability": 5},
            },
            "winner": "A",
            "evidence": {"anti_ai": "A少套话", "logic": "A承接清楚"},
            "risk_notes": [],
        },
        ensure_ascii=False,
    )

    normal = parse_pairwise_judgement("c1", "j1", raw, swapped=False)
    swapped = parse_pairwise_judgement("c1", "j1", raw, swapped=True)

    assert normal.mode_scores[MODE_SCENE_BY_SCENE]["anti_ai"] == 8
    assert swapped.mode_scores[MODE_CHAPTER_FIRST]["anti_ai"] == 8
    assert normal.winner == MODE_SCENE_BY_SCENE
    assert swapped.winner == MODE_CHAPTER_FIRST


def test_parse_pairwise_judgement_recovers_valid_scores_from_broken_outer_json() -> None:
    raw = (
        '{"scores":{"A":{"anti_ai":8,"logic":7,"story":6,"readability":9},'
        '"B":{"anti_ai":5,"logic":6,"story":7,"readability":5}} '
        '"winner":"A","evidence":{"logic":"missing comma before winner"}}'
    )

    result = parse_pairwise_judgement("c1", "j1", raw, swapped=False)

    assert result.winner == MODE_SCENE_BY_SCENE
    assert result.mode_scores[MODE_SCENE_BY_SCENE]["readability"] == 9
    assert result.risk_notes == (
        "outer JSON malformed; retained valid score objects only",
    )


def test_summary_requires_case_wins_margin_position_stability_and_coverage() -> None:
    cases = build_default_cases()
    samples: list[GeneratedSample] = []
    judgements: list[PairwiseJudgement] = []
    for case in cases:
        for mode, coverage in ((MODE_SCENE_BY_SCENE, 0.9), (MODE_CHAPTER_FIRST, 1.0)):
            samples.append(
                GeneratedSample.fake(
                    case_id=case.case_id,
                    mode=mode,
                    deterministic_coverage=coverage,
                )
            )
        for judge in ("j1", "j2"):
            for swapped in (False, True):
                judgements.append(
                    PairwiseJudgement.fake(
                        case_id=case.case_id,
                        judge_model=judge,
                        swapped=swapped,
                        winner=MODE_CHAPTER_FIRST,
                        chapter_first_score=8.2,
                        scene_by_scene_score=7.2,
                    )
                )

    result = summarize_experiment(cases, samples, judgements)

    assert result["decision"] == MODE_CHAPTER_FIRST
    assert result["case_wins"][MODE_CHAPTER_FIRST] == 3
    assert result["position_agreement_rate"] == 1.0
    assert result["position_agreement_by_judge"]["j1"]["agreement_rate"] == 1.0
    assert result["mode_weighted_scores_by_judge"]["j2"][MODE_CHAPTER_FIRST] == 8.2
    assert result["judgement_count"] == 12
    assert result["required_judgement_count"] == 12
    assert result["enough_judgements"] is True
