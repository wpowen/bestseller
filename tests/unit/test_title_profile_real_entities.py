"""书名生成器必须拿到真主角名与真器物（2026-08-21 真机定罪）。

conception 组装 title_profile 时把 ``main_characters`` 写死成
``[{"name": "主角"}]``——书名生成器因此永远不知道主角叫什么，
`_resolve_identity` 只好退回 tags，于是 65 个候选全是把分类标签塞模板。

修法：用与身份层同源的确定性抽取（concept_entities）从已批准构思里取名，
拿不到就保持原样（不制造新的失败模式）。
"""

from __future__ import annotations

import inspect

import pytest

from bestseller.services import conception

pytestmark = pytest.mark.unit


def test_conception_no_longer_hardcodes_the_protagonist_placeholder():
    src = inspect.getsource(conception.run_conception_pipeline)
    assert "_title_profile_protagonist" in src, (
        "title_profile 的主角名必须从构思推导，不能写死成「主角」"
    )


def test_title_profile_protagonist_prefers_real_name():
    resolve = conception._title_profile_protagonist
    logline = "通灵百家巷里最怂的十九岁温符徒温迟，靠一口百年蒸灵锅守着早市摊。"
    assert resolve(metadata={}, premise=logline, is_en=False) == "温迟"


def test_explicit_creation_name_wins():
    resolve = conception._title_profile_protagonist
    got = resolve(
        metadata={"creation_protagonist_name": "沈小禾", "creation_protagonist_source": "user"},
        premise="通灵百家巷里最怂的温符徒温迟，靠一口锅守着摊。",
        is_en=False,
    )
    assert got == "沈小禾", "用户显式选定的名字优先"


def test_llm_inferred_name_does_not_beat_the_concept_prose():
    """真机案：creation_protagonist_name 是 LLM 从旧候选猜的沈小禾，
    而定稿构思写的是温迟——不能让猜来的名字盖过构思正文。"""
    resolve = conception._title_profile_protagonist
    got = resolve(
        metadata={
            "creation_protagonist_name": "沈小禾",
            "creation_protagonist_source": "llm_premise_identity_resolution",
        },
        premise="通灵百家巷里最怂的十九岁温符徒温迟，靠一口百年蒸灵锅守着早市摊。",
        is_en=False,
    )
    assert got == "温迟"


def test_falls_back_to_placeholder_when_nothing_extractable():
    resolve = conception._title_profile_protagonist
    assert resolve(metadata={}, premise="没有任何身份词的一句话。", is_en=False) == "主角"
    assert resolve(metadata={}, premise="", is_en=True) == "Protagonist"
