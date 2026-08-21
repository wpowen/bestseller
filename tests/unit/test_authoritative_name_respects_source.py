"""LLM 猜的名字不是「用户的选择」（2026-08-21 补接线）。

`book_design` 里已有正确判据 `_has_explicit_protagonist_choice`——它读
`creation_protagonist_source`，把 `llm_premise_identity_resolution` 判为
**非用户选择**（注释写明这条是 2026-08-14 为「沈絮 vs 沈絮(阿缨)」加的）。

但做决定的 `_authoritative_creation_protagonist_name` 不调用它，只是遍历
`_EXPLICIT_PROTAGONIST_KEYS` 返回第一个非空值。于是真机上 LLM 从**旧候选**
猜出来的「沈小禾」被当成用户选择，`authoritative_name == snapshot.protagonist.name`
恒成立 → 漂移检测永不触发 → 快照永不被顶替。

又一次「判据加了、做决定的函数没接」。
"""

from __future__ import annotations

import pytest

from bestseller.services.book_design import (
    _authoritative_creation_protagonist_name,
    _has_explicit_protagonist_choice,
)

pytestmark = pytest.mark.unit

_PREMISE = "通灵百家巷里最怂的十九岁温符徒温迟，靠一口百年蒸灵锅守着早市摊。"


def test_llm_inferred_name_is_not_authoritative():
    meta = {
        "premise": _PREMISE,
        "creation_protagonist_name": "沈小禾",
        "creation_protagonist_source": "llm_premise_identity_resolution",
    }
    assert _has_explicit_protagonist_choice(meta) is False
    assert _authoritative_creation_protagonist_name(meta) != "沈小禾", (
        "管线自己猜的名字不得充当创建边界证据"
    )


def test_user_chosen_name_stays_authoritative():
    meta = {
        "premise": _PREMISE,
        "creation_protagonist_name": "沈小禾",
        "creation_protagonist_source": "user",
    }
    assert _authoritative_creation_protagonist_name(meta) == "沈小禾"


def test_inferred_source_falls_through_to_the_concept_prose():
    meta = {
        "premise": _PREMISE,
        "creation_protagonist_name": "沈小禾",
        "creation_protagonist_source": "llm_premise_identity_resolution",
    }
    assert _authoritative_creation_protagonist_name(meta) == "温迟"


def test_no_name_anywhere_returns_empty():
    assert _authoritative_creation_protagonist_name({"premise": "没有身份词的一句话。"}) == ""
