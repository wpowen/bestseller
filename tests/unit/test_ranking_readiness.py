# ruff: noqa: RUF001

from __future__ import annotations

from types import SimpleNamespace

import pytest

from bestseller.services.ranking_readiness import (
    build_listing_ip_readiness,
    build_listing_marketing_asset_pack,
    compute_prelaunch_text_assessment,
    compute_reader_behavior_assessment,
    evaluate_project_ranking_readiness,
    evaluate_ranking_readiness,
)

pytestmark = pytest.mark.unit


def test_prelaunch_text_assessment_uses_report_weights() -> None:
    scores = {
        "character": 5,
        "opening_hook": 5,
        "pacing_structure": 5,
        "conflict_reversal": 5,
        "world_consistency": 5,
        "language_voice": 5,
        "theme_depth": 5,
        "original_selling_point": 5,
        "ending_closure": 5,
        "ip_potential": 5,
    }

    assessment = compute_prelaunch_text_assessment(scores)

    assert assessment.score == 100
    assert assessment.findings == ()
    assert sum(item.weight for item in assessment.dimensions) == 100


def test_reader_behavior_assessment_scores_available_modules_only() -> None:
    assessment = compute_reader_behavior_assessment(
        {
            "first_chapter_completion_rate": 0.65,
            "first_three_arrival_rate": 45,
            "bookshelf_rate": 0.12,
        }
    )

    assert assessment.score == 100
    trial = next(item for item in assessment.modules if item.key == "trial")
    retention = next(item for item in assessment.modules if item.key == "retention")
    assert trial.score == 100
    assert retention.score is None
    assert "retention_behavior_data_missing" in {finding.code for finding in assessment.findings}


def test_reader_behavior_assessment_flags_weak_paid_conversion() -> None:
    assessment = compute_reader_behavior_assessment(
        {
            "first_pay_conversion_rate": 0.04,
            "paid_retention_rate": 0.35,
            "arppu": 15,
        }
    )

    codes = {finding.code for finding in assessment.findings}

    assert assessment.score is not None
    assert assessment.score < 70
    assert "first_pay_conversion_rate_below_target" in codes
    assert "arppu_below_target" in codes


def test_ranking_readiness_combines_text_and_behavior_60_40() -> None:
    scores = {
        "character": 4,
        "opening_hook": 4,
        "pacing_structure": 4,
        "conflict_reversal": 4,
        "world_consistency": 4,
        "language_voice": 4,
        "theme_depth": 4,
        "original_selling_point": 4,
        "ending_closure": 4,
        "ip_potential": 4,
    }
    report = evaluate_ranking_readiness(
        title="测试书",
        dimension_scores=scores,
        behavior_metrics={
            "first_chapter_completion_rate": 0.65,
            "first_three_arrival_rate": 0.45,
            "bookshelf_rate": 0.12,
        },
    )

    assert report.text_assessment.score == 80
    assert report.behavior_assessment.score == 100
    assert report.maturity_score == 88
    assert report.tier == "flagship"
    assert report.scoring_basis == "text_plus_behavior"


def test_ranking_readiness_caps_flagship_when_external_gate_fails() -> None:
    scores = {
        "character": 5,
        "opening_hook": 5,
        "pacing_structure": 5,
        "conflict_reversal": 5,
        "world_consistency": 5,
        "language_voice": 5,
        "theme_depth": 5,
        "original_selling_point": 5,
        "ending_closure": 5,
        "ip_potential": 5,
    }

    report = evaluate_ranking_readiness(
        title="青崖诡事",
        dimension_scores=scores,
        external_gate_findings=[
            {
                "code": "golden_three_hype_underpowered",
                "severity": "critical",
                "scope": "chapters.1_3.hype",
                "message": "前三章强爽点不足。",
                "suggestion": "重做黄金三章。",
                "evidence": {"strong_hype_count": 0},
            }
        ],
    )

    codes = {finding.code for finding in report.findings}
    assert report.text_assessment.score == 100
    assert report.passed is False
    assert report.tier == "immature"
    assert report.maturity_score == 69
    assert "external_gate_golden_three_hype_underpowered" in codes


def test_listing_marketing_asset_pack_produces_three_script_lengths() -> None:
    pack = build_listing_marketing_asset_pack(
        {
            "primary_title": "青囊不语问阴阳",
            "logline": "落魄风水师接下凶宅委托，却被困进一面以否认为食的困魂镜。",
            "tags": ["民俗悬疑", "规则怪谈"],
            "main_characters": [{"name": "林渊", "role": "主角"}],
            "reader_promise": ["规则破局", "镜债反制"],
        }
    )

    scripts = pack["short_video_scripts"]

    assert [item["duration_seconds"] for item in scripts] == [15, 45, 90]
    assert "青囊不语问阴阳" in scripts[0]["script"]


def test_ip_readiness_uses_story_rules_and_locations() -> None:
    story_bible = SimpleNamespace(
        world_rules=[SimpleNamespace(name="否认者先入账")],
        locations=[
            SimpleNamespace(name="十七栋凶宅"),
            SimpleNamespace(name="困魂镜"),
        ],
    )
    readiness = build_listing_ip_readiness(
        {
            "main_characters": [{"name": "林渊", "role": "主角", "appeal": "风水师"}],
            "promo_copy": ["子时之后，别照镜子。", "谁不认账，谁先入账。", "他见鬼先看方位。"],
            "tags": ["民俗悬疑"],
        },
        story_bible=story_bible,
    )

    assert readiness["status"] == "ready"
    assert "十七栋凶宅" in readiness["visual_motifs"]
    assert readiness["character_tags"][0]["name"] == "林渊"


def test_project_ranking_readiness_derives_project_evidence() -> None:
    project = SimpleNamespace(
        slug="qingnang",
        title="青囊不语问阴阳",
        genre="悬疑",
        sub_genre="民俗悬疑",
        language="zh-CN",
        target_chapters=80,
        metadata_json={
            "reader_promise": ["规则破局", "镜债反制"],
            "volume_plan": [{"volume_number": 1, "major_payoff": "救出父亲"}],
        },
        theme_statement="否认现实的人终会被现实入账。",
        dramatic_question="林渊能否逼所有人认账？",
    )
    writing_profile = {
        "market": {
            "opening_contract": "子时入镜，否认者先入账。",
            "chapter_hook_strategy": "每章尾部保留镜债升级。",
            "payoff_rhythm": "三章一小回收。",
        },
        "character": {"antagonist_mode": "镜债系统性对手"},
        "serialization": {
            "first_three_chapter_goal": "立人立局立钩。",
            "scene_drive_rule": "每场必须有目标阻碍升级。",
            "chapter_ending_rule": "章末留下新债。",
        },
        "style": {
            "prose_style": "commercial-web-serial",
            "sentence_style": "short-punchy",
            "tone_keywords": ["民俗", "悬疑"],
            "info_density": "lean",
        },
    }
    story_bible = SimpleNamespace(
        world_backbone=SimpleNamespace(core_promise="镜债规则", thematic_melody="认账"),
        characters=[
            SimpleNamespace(
                name="林渊",
                role="主角",
                goal="救父",
                fear="父亲永困镜中",
                flaw="习惯独自扛债",
                arc_trajectory="从否认到认账",
                voice_profile={"cadence": "冷硬"},
                moral_framework={"rule": "不让无辜者替债"},
            ),
            SimpleNamespace(name="王建业", role="反派"),
        ],
        world_rules=[SimpleNamespace(name="否认者先入账", story_consequence="否认债务会入镜")],
        locations=[SimpleNamespace(name="十七栋凶宅"), SimpleNamespace(name="困魂镜")],
        factions=[SimpleNamespace(name="张家")],
        relationships=[SimpleNamespace(character_a="林渊", character_b="王建业")],
        volume_frontiers=[SimpleNamespace(volume_number=1)],
        deferred_reveals=[SimpleNamespace(reveal_code="father_truth")],
    )
    listing = {
        "primary_title": "青囊不语问阴阳",
        "logline": "落魄风水师接下凶宅委托，却被困进一面以否认为食的困魂镜。",
        "short_intro": "林渊必须逼活人认账，才能从镜中夺回父亲线索。",
        "promo_copy": ["子时之后，别照镜子。", "谁不认账，谁先入账。", "他见鬼先看方位。"],
        "tags": ["悬疑", "民俗", "风水师", "规则怪谈", "凶宅"],
        "reader_promise": ["规则破局", "镜债反制"],
        "title_candidates": [{"title": f"标题{i}"} for i in range(20)],
        "main_characters": [{"name": "林渊", "role": "主角"}],
    }

    report = evaluate_project_ranking_readiness(
        project,
        writing_profile=writing_profile,
        story_bible=story_bible,
        listing_profile=listing,
        scorecard_quality_score=90,
        premium_gate_score=85,
    )

    assert report.passed is True
    assert report.tier in {"strong_project", "flagship"}
    assert report.marketing_assets["short_video_scripts"][0]["duration_seconds"] == 15
    assert report.ip_readiness["status"] == "ready"
