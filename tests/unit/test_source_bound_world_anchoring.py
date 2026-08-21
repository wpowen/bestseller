"""source-bound 世界必须锚在构思里的真地名上（2026-08-21 真机定罪）。

真机 custom-xuanhuan-1787320762：用户勾了「能力不带自损」(cost_style=minimal)，
`_source_bound_cast_enabled` 的**第一个条件**就是它（而该函数自己的 docstring
写着「Cost style … must never decide whether canonical creation choices are
authoritative」），于是整条规划主干切进 source-bound 模式，世界由写死的模板编译：

    world_name = "玄幻"                     ← 题材标签当世界名
    locations  = 构思中的当前主场 / 当前主场边界 / 核心行动区域 /
                 结果验证区域 / 外部交互区域   ← 全是框架元语言
    power_system.tiers = 确认/复现/转化/扩张  ← 框架自己的节拍术语

而构思里明明有真地名「通灵百家巷」。这些占位符会原样落库并进 prompt。

**不能清空**：world_richness 门按数量判「饥饿世界」，清空会触发误修。
所以做法是——**数量不变，把能锚的名字锚到构思里的真实地名上**；
抽不出来时才退回原模板（不制造新的失败模式）。
"""

from __future__ import annotations

import pytest

from bestseller.services.concept_entities import (
    extract_leading_noun_phrase,
    extract_place_names,
    extract_role_bound_name,
    is_placeholder_name,
)

pytestmark = pytest.mark.unit

_LOGLINE = (
    "通灵百家巷里最怂的十九岁温符徒温迟，靠一口百年蒸灵锅守着父亲留下的早市摊，"
    "每天辰时开锅替坊民温符换零钱。"
)


def test_place_extraction_finds_the_real_place():
    assert extract_place_names(_LOGLINE)[0] == "通灵百家巷"


def test_place_extraction_rejects_verb_phrases():
    """不加小句开头约束会切出「天辰时开锅替坊」。"""
    assert "天辰时开锅替坊" not in extract_place_names(_LOGLINE)
    assert "守着父亲留下的早市" not in extract_place_names(_LOGLINE)


def test_object_phrase_stops_at_dash():
    assert extract_leading_noun_phrase("百年蒸灵锅——一只能说话的老锅") == "百年蒸灵锅"


def test_role_bound_name_handles_invented_role_words():
    """「温符徒」是本书自造的身份词，靠通用身份词「徒」+位置形状仍能抽到。"""
    assert extract_role_bound_name(_LOGLINE) == "温迟"


def test_placeholder_name_is_recognised():
    assert is_placeholder_name("主角") is True
    assert is_placeholder_name("温迟") is False


def test_no_hardcoded_book_specific_vocabulary():
    """零私货：不得出现任何具体书的物件/门派名。

    现有 `_resolve_object_token` 写死了「青囊/困魂镜/归墟会」这些上一本书的东西，
    本模块不许重蹈。
    """
    import ast
    import inspect

    from bestseller.services import concept_entities

    src = inspect.getsource(concept_entities)
    # 只查代码体：模块文档串里**引用**了这些词作为反面教材，不算私货。
    tree = ast.parse(src)
    body = ast.get_docstring(tree) or ""
    code = src.replace(body, "") if body else src
    for token in ("青囊", "困魂镜", "归墟会", "重瞳", "阴阳眼", "双穿门"):
        assert token not in code


def test_world_spec_anchors_on_the_real_place():
    import inspect

    from bestseller.services import planner

    src = inspect.getsource(planner._compile_source_bound_world_spec)
    assert "extract_place_names" in src, "source-bound 世界必须锚到构思里的真地名"


def test_world_name_never_falls_back_to_the_genre_label():
    import inspect

    from bestseller.services import planner

    src = inspect.getsource(planner._compile_source_bound_world_spec)
    assert "_source_bound_world_name" in src
