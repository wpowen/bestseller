from __future__ import annotations

from types import SimpleNamespace

import pytest

from bestseller.services.planning_kernel import (
    build_prewrite_repair_directives,
    build_project_planning_kernel,
    evaluate_prewrite_readiness,
    persist_project_planning_kernel,
)

pytestmark = pytest.mark.unit


def _project(**metadata):
    return SimpleNamespace(
        slug="xianxia-plan",
        title="道种试炼",
        genre="仙侠升级",
        sub_genre="宗门逆袭",
        target_chapters=120,
        metadata_json=metadata,
    )


def _book_spec() -> dict[str, object]:
    return {
        "series_engine": {
            "core_serial_engine": "低位修士用道种规则和资源账反制宗门压力。",
            "reader_promise": "每批章节兑现一次资源翻盘或规则发现。",
            "first_three_chapter_hook": "开局给出废灵根、道种异动和三月大考。",
            "chapter_ending_hook_strategy": "用资源、倒计时、敌人动作或道种异常收尾。",
            "payoff_rhythm": "3-6章小兑现，30章内大阶段兑现。",
        },
        "protagonist": {
            "name": "宁尘",
            "decision_policy": {"core_rule": "先保命，再换取可复用资源。"},
        },
    }


def _world_spec() -> dict[str, object]:
    return {
        "rules": [
            {"rule_name": "道种吸收", "description": "只能吸收残页余韵，过量会伤经脉。"}
        ],
        "factions": [
            {"name": "杂役峰", "goal": "维持配给秩序"},
            {"name": "丹房", "goal": "垄断筑基丹"},
        ],
        "power_system": {"realms": ["炼气", "筑基", "金丹"]},
    }


def _cast_spec() -> dict[str, object]:
    return {
        "protagonist": {
            "name": "宁尘",
            "decision_policy": {"core_rule": "先保命，再换取可复用资源。"},
        },
        "supporting_cast": [
            {"name": "苏瑶", "role": "rival", "relationship_to_protagonist": "互相试探"},
            {"name": "陆沉", "role": "broker", "relationship_to_protagonist": "资源交易"},
        ],
    }


def _volume_plan() -> list[dict[str, object]]:
    return [
        {
            "volume_number": 1,
            "conflict_phase": "survival",
            "primary_force_name": "杂役峰执事",
            "volume_climax": "大考前夜用残页规则反制栽赃。",
            "core_payoff": "低位反制",
            "foreshadowing_planted": ["废灵根旧案"],
        },
        {
            "volume_number": 2,
            "conflict_phase": "resource_war",
            "primary_force_name": "丹房管事",
            "volume_climax": "用灵草账本换取秘境名额。",
            "core_payoff": "资源翻盘",
            "foreshadowing_paid_off": ["废灵根旧案第一层"],
        },
        {
            "volume_number": 3,
            "conflict_phase": "faction_intrigue",
            "primary_force_name": "内门监察",
            "volume_climax": "以秘境阵眼暴露二十年前真相。",
            "core_payoff": "信息解谜",
            "reader_hook_to_next": "道种被内门古碑认出。",
        },
        {
            "volume_number": 4,
            "conflict_phase": "betrayal",
            "primary_force_name": "旧盟友陆沉",
            "volume_climax": "陆沉背叛逼宁尘公开一项新规则。",
            "core_payoff": "身份误判利用",
            "reader_hook_to_next": "祖师残页出现第二枚账印。",
        },
    ]


def test_prewrite_readiness_accepts_rich_planning_kernel() -> None:
    project = _project(
        story_facets={
            "setting": "外门杂役峰与三月秘境",
            "narrative_drive": "低位反制",
            "trope_tags": ["凡人流", "资源账", "宗门生存"],
        },
        benchmark_works=["凡人修仙传结构对标"],
    )

    kernel = build_project_planning_kernel(
        project,
        book_spec=_book_spec(),
        world_spec=_world_spec(),
        cast_spec=_cast_spec(),
        volume_plan=_volume_plan(),
    )
    report = evaluate_prewrite_readiness(kernel)

    assert report.passed is True
    assert report.score >= 80
    assert report.blocking_findings == ()
    assert report.capability_snapshot["benchmark_alignment"] is True
    assert report.capability_snapshot["volume_differentiation"] is True


def test_prewrite_readiness_blocks_thin_generic_planning() -> None:
    kernel = build_project_planning_kernel(
        _project(),
        book_spec={},
        world_spec={},
        cast_spec={},
        volume_plan=[],
    )
    report = evaluate_prewrite_readiness(kernel)
    codes = {finding.code for finding in report.blocking_findings}

    assert report.passed is False
    assert "benchmark_alignment_missing" in codes
    assert "unique_hook_missing" in codes
    assert "series_engine_missing" in codes


def test_prewrite_readiness_blocks_repeated_volume_pressure() -> None:
    project = _project(
        story_facets={"setting": "外门杂役峰", "narrative_drive": "低位反制"},
        benchmark_works=["凡人修仙传结构对标"],
    )
    repeated = [
        {
            "volume_number": index,
            "conflict_phase": "survival",
            "primary_force_name": "丹房管事",
            "volume_climax": f"第{index}次丹房压迫",
            "core_payoff": f"资源翻盘{index}",
        }
        for index in range(1, 5)
    ]
    kernel = build_project_planning_kernel(
        project,
        book_spec=_book_spec(),
        world_spec=_world_spec(),
        cast_spec=_cast_spec(),
        volume_plan=repeated,
    )
    report = evaluate_prewrite_readiness(kernel)
    codes = {finding.code for finding in report.blocking_findings}

    assert report.passed is False
    assert "volume_differentiation_missing" in codes
    directives = build_prewrite_repair_directives(report)
    assert any("更换主压力源" in item for item in directives)


def test_persist_planning_kernel_promotes_ranking_profile_to_metadata(tmp_path) -> None:
    profile = tmp_path / "xianxia-plan" / "story-bible" / "ranking-capability-profile.md"
    profile.parent.mkdir(parents=True)
    profile.write_text("# 榜单级能力 Profile\n\n- 每 5 章验证一次道种规则。", encoding="utf-8")
    project = _project(story_facets={"setting": "外门杂役峰", "narrative_drive": "低位反制"})

    payload = persist_project_planning_kernel(
        project,
        book_spec=_book_spec(),
        world_spec=_world_spec(),
        cast_spec=_cast_spec(),
        volume_plan=_volume_plan(),
        output_base_dir=tmp_path,
    )

    assert payload["prewrite_readiness_report"]["passed"] is True
    assert payload["prewrite_repair_directives"] == []
    assert project.metadata_json["planning_kernel"]["benchmark"]["ranking_profile_present"] is True
    assert "道种规则" in project.metadata_json["ranking_capability_profile_block"]
