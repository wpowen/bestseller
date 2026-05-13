# ruff: noqa: RUF001
from __future__ import annotations

from copy import deepcopy

from pydantic import ValidationError
import pytest

from bestseller.services.story_design_kernel import (
    StoryDesignKernel,
    render_story_design_kernel_prompt_block,
    story_design_kernel_from_dict,
    story_design_kernel_to_dict,
)

pytestmark = pytest.mark.unit


def _kernel_payload() -> dict[str, object]:
    return {
        "version": 1,
        "shape": {
            "length_class": "long",
            "publication_mode": "web_serial",
            "outline_depth": "chapter",
            "primary_duties": ["forward_pull", "relationship_state_shift"],
            "ending_contract": "close current loop while opening next desire",
        },
        "reader_promise": "每一章都让主角的选择改变关系、资源或局势。",
        "premise_contract": {
            "unique_hook": "修仙宗门的资源账由情感债驱动。",
            "core_question": "主角能否在不牺牲信任的前提下完成宗门扩张？",
            "commercial_pull": "升级、关系债、宗门经营三条线互相兑现。",
            "forbidden_defaults": ["父母失踪", "神秘玉佩自动开挂"],
        },
        "character_conflict_contracts": [
            {
                "character_key": "protagonist",
                "external_goal": "拿下第一块灵田的经营权",
                "internal_need": "学会把盟友当合作者而不是资源",
                "pressure_source": "宗门长老要求短期收益",
                "choice_axis": "信任换效率，还是控制换安全",
                "change_vector": "从独断到分权",
            }
        ],
        "world_conflict_contracts": [
            {
                "axis": "资源规则",
                "rule": "灵田产出与照料者之间的信任度绑定",
                "visible_cost": "关系破裂会让灵田枯萎",
                "escalation_path": "个人信任扩展到宗门制度",
            }
        ],
        "structure_strategy": {
            "macro_strategy": "卷内经营闭环，卷间扩大制度风险",
            "chapter_engine": "每章推进一个资源账或关系账",
            "pacing_rule": "短兑现与长债务交替",
            "freshness_rule": "连续三章不得重复同一压力源",
        },
        "plot_tree": [
            {
                "key": "mainline",
                "line_type": "main",
                "label": "灵田经营权主线",
                "role": "驱动外部目标",
                "current_state": "没有资源入口",
                "target_state": "建立第一处稳定产出",
                "failure_if_removed": "故事失去商业推进引擎",
            },
            {
                "key": "ally-trust",
                "line_type": "relationship",
                "label": "盟友信任线",
                "role": "制造选择代价",
                "current_state": "盟友只愿临时合作",
                "target_state": "形成可授权的协作关系",
                "dependency_on_mainline": "信任变化决定灵田经营是否稳定",
                "failure_if_removed": "主线变成单纯资源流水账",
            },
        ],
        "beat_schedule": [
            {
                "chapter_range": "1-3",
                "duty": "建立资源账与关系账",
                "state_change": "主角从拿不到灵田到获得试运营资格",
                "payoff": "读者看到经营规则第一次生效",
                "hook_or_aftereffect": "试运营资格绑定一个不可告人的盟友债务",
            }
        ],
        "change_vectors": ["资源权限变化", "信任边界变化", "制度压力变化"],
        "uniqueness_constraints": ["不得以亲人失踪作为默认驱动"],
        "reverse_outline_status": "not_started",
    }


def test_story_design_kernel_round_trips_and_renders_prompt_block() -> None:
    kernel = story_design_kernel_from_dict(_kernel_payload())

    assert isinstance(kernel, StoryDesignKernel)
    assert story_design_kernel_to_dict(kernel)["reader_promise"]

    block = render_story_design_kernel_prompt_block(kernel)

    assert "Story Design Kernel" in block
    assert "Reader promise" in block
    assert "Change vectors" in block
    assert "灵田经营权主线" in block


def test_subplots_must_depend_on_mainline() -> None:
    payload = deepcopy(_kernel_payload())
    plot_tree = payload["plot_tree"]
    assert isinstance(plot_tree, list)
    subplot = plot_tree[1]
    assert isinstance(subplot, dict)
    subplot["dependency_on_mainline"] = ""

    with pytest.raises(ValidationError):
        story_design_kernel_from_dict(payload)


def test_kernel_requires_at_least_one_main_plot_line() -> None:
    payload = deepcopy(_kernel_payload())
    plot_tree = payload["plot_tree"]
    assert isinstance(plot_tree, list)
    for node in plot_tree:
        assert isinstance(node, dict)
        node["line_type"] = "subplot"

    with pytest.raises(ValidationError):
        story_design_kernel_from_dict(payload)
