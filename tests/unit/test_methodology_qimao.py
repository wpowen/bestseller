"""Qimao signing-readiness methodology rules."""

from __future__ import annotations

import pytest

from bestseller.services.methodology import (
    get_qimao_regeneration_contract,
    get_qimao_signing_constraints,
    render_methodology_scene_rules,
    render_qimao_regeneration_contract,
    render_qimao_signing_rules,
)


pytestmark = pytest.mark.unit


def test_qimao_signing_constraints_load_from_config() -> None:
    constraints = get_qimao_signing_constraints()

    assert constraints.sample_words == 10000
    assert constraints.protagonist_focus_by_words == 100
    assert constraints.visible_conflict_by_words == 200
    assert constraints.core_conflict_by_words == 600
    assert constraints.emotional_hook_by_words == 2000
    assert constraints.mainline_clear_by_words == 6000
    assert any("主角" in rule for rule in constraints.first_chapter_rules)
    assert any("chapter_3" in rule for rule in constraints.golden_three_rules)
    assert constraints.per_chapter_loop_rules


def test_qimao_rules_render_only_for_matching_platform() -> None:
    block = render_methodology_scene_rules(
        chapter_number=1,
        is_opening=True,
        platform_target="七猫小说",
        language="zh-CN",
    )

    assert "七猫签约门槛" in block
    assert "前100字聚焦主角" in block
    assert "前三章" in block
    assert "前10000字" in block

    non_qimao = render_methodology_scene_rules(
        chapter_number=1,
        is_opening=True,
        platform_target="起点中文网",
        language="zh-CN",
    )
    assert "七猫签约门槛" not in non_qimao


def test_later_qimao_chapters_keep_per_chapter_loop() -> None:
    block = render_qimao_signing_rules(
        chapter_number=12,
        platform_target="qimao",
        language="zh-CN",
    )

    assert "七猫签约门槛" in block
    assert "每章无线风循环" in block
    assert "前10000字" not in block
    assert "前三章" not in block


def test_qimao_rules_skip_english_projects() -> None:
    assert (
        render_qimao_signing_rules(
            chapter_number=1,
            platform_target="七猫小说",
            language="en",
        )
        == ""
    )


def test_qimao_regeneration_contract_loads_from_config() -> None:
    contract = get_qimao_regeneration_contract()

    assert contract.target_platform == "七猫"
    assert "平台适配优先于作者自我表达。" in contract.non_negotiables
    assert contract.rejection_cause_map["weak_immersion"] == "代入感较弱"
    assert contract.rejection_cause_map["ordinary_entry"] == "开篇切入点比较普通"
    assert contract.regeneration_decision_order[0] == "先重选开篇事件"


def test_qimao_regeneration_contract_renders_for_qimao_only() -> None:
    block = render_qimao_regeneration_contract(
        platform_target="七猫小说",
        language="zh-CN",
        rejection_reasons=(
            "文笔还有待提升，代入感较弱，开篇的切入点比较普通，"
            "缺乏足够的吸引力，故事的叙述较为平淡。"
        ),
    )

    assert "七猫再生成合同" in block
    assert "先重选开篇事件" in block
    assert "weak_immersion" in block
    assert "ordinary_entry" in block
    assert "flat_narration" in block

    assert (
        render_qimao_regeneration_contract(
            platform_target="起点中文网",
            language="zh-CN",
        )
        == ""
    )
    assert (
        render_qimao_regeneration_contract(
            platform_target="qimao",
            language="en",
        )
        == ""
    )


def test_qimao_regeneration_contract_is_in_scene_rules() -> None:
    block = render_methodology_scene_rules(
        chapter_number=1,
        is_opening=True,
        platform_target="七猫",
        language="zh-CN",
        rejection_reasons="代入感较弱，故事的叙述较为平淡。",
    )

    assert "七猫签约门槛" in block
    assert "七猫再生成合同" in block
    assert "这不是润色任务" in block
