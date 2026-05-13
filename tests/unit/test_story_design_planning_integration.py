from __future__ import annotations

from bestseller.domain.enums import ArtifactType
from bestseller.services.planning_kernel import (
    build_project_planning_kernel,
    evaluate_prewrite_readiness,
)
from bestseller.services.truth_version import CORE_TRUTH_ARTIFACT_TYPES


def _readiness_ready_kernel() -> dict[str, object]:
    return {
        "target_chapters": 20,
        "benchmark": {
            "benchmark_works": ["品类标杆"],
            "comparables": [],
            "ranking_profile_present": False,
        },
        "creative_positioning": {"unique_hook": "资源账和关系债互相驱动"},
        "series_engine": {
            "reader_promise": "每章都有状态变化",
            "chapter_hook_strategy": "用选择后果收尾",
            "payoff_rhythm": "短兑现和长债务交替",
        },
        "foundation": {
            "has_book_spec": True,
            "has_world_spec": True,
            "has_cast_spec": True,
            "world_rule_count": 2,
            "supporting_cast_count": 2,
        },
        "volume_strategy": {
            "volume_count": 1,
            "conflict_phases": ["opening"],
            "primary_forces": ["宗门压力"],
            "unique_conflict_phase_count": 1,
            "unique_primary_force_count": 1,
            "escalation_anchor_count": 3,
        },
    }


def _story_design_payload() -> dict[str, object]:
    return {
        "version": 1,
        "shape": {
            "length_class": "long",
            "publication_mode": "web_serial",
            "outline_depth": "chapter",
            "primary_duties": ["forward_pull", "visible_system_change"],
            "ending_contract": "close current loop while opening next desire",
        },
        "reader_promise": "每章都让资源、关系或制度位置发生可见变化。",
        "premise_contract": {
            "unique_hook": "灵田产出与信任债绑定",
            "core_question": "主角能否把个人信任扩展成宗门制度?",
            "commercial_pull": "经营成果、关系债和规则漏洞互相兑现。",
            "forbidden_defaults": ["父母失踪", "神秘玉佩"],
        },
        "character_conflict_contracts": [
            {
                "character_key": "protagonist",
                "external_goal": "获得第一块灵田经营权",
                "internal_need": "学会授权",
                "pressure_source": "宗门收益考核",
                "choice_axis": "控制还是信任",
                "change_vector": "从独断到分权",
            }
        ],
        "world_conflict_contracts": [
            {
                "axis": "灵田规则",
                "rule": "信任债影响灵田产出",
                "visible_cost": "关系破裂会让产出下降",
                "escalation_path": "从个人关系扩展到宗门制度",
            }
        ],
        "structure_strategy": {
            "macro_strategy": "经营闭环逐步扩大",
            "chapter_engine": "每章推进一个资源账或关系账",
            "pacing_rule": "短兑现与长债务交替",
            "freshness_rule": "连续三章不得重复同一压力源",
        },
        "plot_tree": [
            {
                "key": "mainline",
                "line_type": "main",
                "label": "灵田经营权",
                "role": "驱动外部目标",
                "current_state": "没有资源入口",
                "target_state": "稳定产出",
                "failure_if_removed": "故事失去经营推进",
            }
        ],
        "beat_schedule": [
            {
                "chapter_range": "1-3",
                "duty": "建立资源账",
                "state_change": "从无资格到获得试运营资格",
                "payoff": "经营规则第一次兑现",
                "hook_or_aftereffect": "资格绑定隐藏债务",
            }
        ],
        "change_vectors": ["资源权限变化", "信任边界变化", "制度压力变化"],
    }


def test_story_design_kernel_is_core_planning_artifact() -> None:
    assert ArtifactType.STORY_DESIGN_KERNEL.value == "story_design_kernel"
    assert ArtifactType.STORY_DESIGN_KERNEL.value in CORE_TRUTH_ARTIFACT_TYPES


def test_prewrite_readiness_warns_when_story_design_kernel_missing() -> None:
    report = evaluate_prewrite_readiness(_readiness_ready_kernel())
    warning_codes = {finding.code for finding in report.warnings}

    assert report.passed is True
    assert "story_design_kernel_missing" in warning_codes
    assert report.capability_snapshot["story_design_kernel"] is False
    assert report.capability_snapshot["story_state_driven_planning"] is False


def test_project_planning_kernel_summarizes_valid_story_design_kernel() -> None:
    kernel = build_project_planning_kernel(
        project_metadata={
            "title": "灵田债",
            "genre": "基建经营",
            "target_chapters": 20,
            "benchmark_works": ["经营流标杆"],
            "unique_hook": "灵田产出与信任债绑定",
            "story_design_kernel": _story_design_payload(),
        },
        book_spec={
            "series_engine": {
                "reader_promise": "每章都有经营状态变化",
                "chapter_hook_strategy": "用债务或瓶颈收尾",
                "payoff_rhythm": "三章内小兑现",
            }
        },
        world_spec={"rules": [{"name": "信任债"}]},
        cast_spec={"supporting_cast": [{"name": "盟友"}]},
        volume_plan=[
            {
                "conflict_phase": "opening",
                "primary_force_name": "宗门压力",
                "core_payoff": "拿到灵田",
            }
        ],
    )

    story_design = kernel["story_design"]
    assert isinstance(story_design, dict)
    assert story_design["valid"] is True
    assert story_design["reader_promise"]
    assert story_design["plot_line_count"] == 1

    report = evaluate_prewrite_readiness(kernel)
    codes = {finding.code for finding in [*report.blocking_findings, *report.warnings]}
    assert "story_design_kernel_missing" not in codes
    assert report.capability_snapshot["story_design_kernel"] is True
    assert report.capability_snapshot["story_state_driven_planning"] is True
