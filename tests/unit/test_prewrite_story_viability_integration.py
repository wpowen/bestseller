# ruff: noqa: RUF001
from __future__ import annotations

import json
from pathlib import Path

from bestseller.services.planning_kernel import (
    build_project_planning_kernel,
    decide_prewrite_readiness_gate,
    evaluate_prewrite_readiness,
)

FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "story_viability"


def _fixture(name: str) -> dict:
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


def _project(story: dict, *, target_chapters: int) -> dict:
    return {
        "slug": "cross-genre-story-viability",
        "title": "测试书名",
        "genre": "异世大陆",
        "sub_genre": "异世大陆",
        "target_chapters": target_chapters,
        "metadata": {
            "story_viability_input": story,
            "benchmark_works": ["结构对标样本"],
            "story_facets": {"setting": "边地城镇", "narrative_drive": "经营升级"},
        },
    }


def _project_without_viability(*, legacy: bool = False) -> dict:
    metadata = {
        "benchmark_works": ["结构对标样本"],
        "story_facets": {"setting": "边地城镇", "narrative_drive": "经营升级"},
        "strict_story_viability": True,
    }
    if legacy:
        metadata["legacy_story_viability"] = True
    return {
        "slug": "missing-story-viability",
        "title": "测试书名",
        "genre": "异世大陆",
        "sub_genre": "异世大陆",
        "target_chapters": 60,
        "metadata": metadata,
    }


def _book_spec() -> dict:
    return {
        "series_engine": {
            "reader_promise": "看主角把边地据点经营成区域秩序节点",
            "chapter_hook_strategy": "每轮反制逼出下一选择",
            "payoff_rhythm": "每三章短兑现，每卷状态质变",
        },
        "protagonist": {"name": "沈川", "decision_policy": {"default": "先验证再承诺"}},
    }


def _world_spec() -> dict:
    return {"rules": [{"name": "通行权"}], "factions": [{"name": "商会"}]}


def _cast_spec() -> dict:
    return {
        "protagonist": {"name": "沈川", "decision_policy": {"default": "先验证再承诺"}},
        "supporting_cast": [{"name": "许禾"}],
    }


def _volumes() -> list[dict]:
    return [
        {
            "volume_number": 1,
            "conflict_phase": "survival",
            "primary_force_name": "地方行会",
            "volume_climax": "保住第一条商路",
        },
        {
            "volume_number": 2,
            "conflict_phase": "expansion",
            "primary_force_name": "边军统制",
            "volume_climax": "取得区域通行权",
        },
    ]


def test_prewrite_gate_blocks_structurally_thin_story_before_drafting() -> None:
    kernel = build_project_planning_kernel(
        _project(_fixture("thin_60.json"), target_chapters=60),
        book_spec=_book_spec(),
        world_spec=_world_spec(),
        cast_spec=_cast_spec(),
        volume_plan=_volumes(),
    )

    report = evaluate_prewrite_readiness(kernel)
    decision = decide_prewrite_readiness_gate(report, mode="block")

    assert report.capability_snapshot["story_viability"] is False
    assert "story_capacity_insufficient" in decision.blocking_codes
    assert decision.should_block is True


def test_story_capacity_failure_cannot_be_downgraded_to_warn_only() -> None:
    kernel = build_project_planning_kernel(
        _project(_fixture("thin_60.json"), target_chapters=60),
        book_spec=_book_spec(),
        world_spec=_world_spec(),
        cast_spec=_cast_spec(),
        volume_plan=_volumes(),
    )

    report = evaluate_prewrite_readiness(kernel)
    decision = decide_prewrite_readiness_gate(report, mode="warn")

    assert "story_capacity_insufficient" in decision.blocking_codes
    assert decision.should_block is True
    assert decision.reason == "concept_contract_invariant_failed"


def test_prewrite_gate_accepts_cross_axis_renewable_story_engine() -> None:
    kernel = build_project_planning_kernel(
        _project(_fixture("healthy_management_upgrade.json"), target_chapters=60),
        book_spec=_book_spec(),
        world_spec=_world_spec(),
        cast_spec=_cast_spec(),
        volume_plan=_volumes(),
    )

    report = evaluate_prewrite_readiness(kernel)

    assert report.capability_snapshot["story_viability"] is True
    assert all(f.code != "story_capacity_insufficient" for f in report.blocking_findings)


def test_strict_new_project_without_viability_input_is_blocked() -> None:
    kernel = build_project_planning_kernel(_project_without_viability())
    report = evaluate_prewrite_readiness(kernel)
    decision = decide_prewrite_readiness_gate(report, mode="block")

    assert "story_capacity_insufficient" in decision.blocking_codes
    assert decision.should_block is True
    assert "MISSING_STORY_VIABILITY_INPUT" in report.blocking_findings[0].evidence["blocking_codes"]


def test_legacy_project_must_opt_in_to_bypass_missing_viability_input() -> None:
    kernel = build_project_planning_kernel(_project_without_viability(legacy=True))
    report = evaluate_prewrite_readiness(kernel)

    assert all(f.code != "story_capacity_insufficient" for f in report.blocking_findings)


def test_declared_v2_contract_automatically_requires_viability_evidence() -> None:
    project = _project_without_viability()
    project["metadata"].pop("strict_story_viability")
    project["metadata"].update(
        {
            "concept_contract_version": "2",
            "concept_contract_required": True,
        }
    )

    kernel = build_project_planning_kernel(project)
    report = evaluate_prewrite_readiness(kernel)

    assert any(
        finding.code == "story_capacity_insufficient"
        for finding in report.blocking_findings
    )
