"""身份分裂不等于写法差异（2026-08-21 真机 custom-xuanhuan-1787320762）。

真机新书：淘汰赛跑了 4 轮候选 / 16 次判官，胜出的构思主角叫**温迟**——
logline、premise、synopsis 通篇都是温迟。而结构化身份层全是**沈小禾**：
protagonist / creation_protagonist_name / identity_manifest / cast_spec /
world_spec 的势力名（「沈小禾的当前行动单元」）。story_spine.protagonist 是空的。
**温迟只活在构思正文里，没有任何结构化字段承载它。**

一致性门**抓到了**：`advisory_codes=["protagonist_identity_mismatch"]`，
但 `blocks_production=false`、`passed=true`，书照建。

advisory 这条规则本身没错——它是 2026-08-14 为「沈絮 vs 沈絮(阿缨)」加的：
同一个人的两种写法不该停产。问题是它**连「两个完全不同的人」也一起放行**。

判据：规范名在最终构思正文（logline/premise/synopsis）里**出现过** →
写法差异，维持 advisory；**一次都没出现** → 身份分裂，不得 advisory。
"""

from __future__ import annotations

import pytest

from bestseller.services.book_design import _identity_mismatch_is_advisory

pytestmark = pytest.mark.unit

_PREMISE = "通灵百家巷里最怂的十九岁温符徒温迟，靠一口百年蒸灵锅守着父亲留下的早市摊。"


def _meta(canonical: str, *, source: str = "llm_premise_identity_resolution") -> dict:
    return {
        "premise": _PREMISE,
        "logline": _PREMISE,
        "synopsis": "温迟只想安安稳稳卖早点。",
        "creation_protagonist_name": canonical,
        "creation_protagonist_source": source,
    }


def test_name_absent_from_the_whole_concept_is_not_advisory():
    """真机案：沈小禾 在 logline/premise/synopsis 里一次都没出现。"""
    assert _identity_mismatch_is_advisory(_meta("沈小禾")) is False


def test_alias_rendering_variant_stays_advisory():
    """2026-08-14 案：沈絮 vs 沈絮(阿缨) 是同一个人，不该停产。"""
    meta = _meta("温迟")
    assert _identity_mismatch_is_advisory(meta) is True


def test_explicit_user_choice_is_never_advisory():
    """用户自己选的主角名出现分歧，一律不放行（既有行为不变）。"""
    assert _identity_mismatch_is_advisory(_meta("沈小禾", source="user")) is False
    assert _identity_mismatch_is_advisory(_meta("温迟", source="user")) is False


def test_no_concept_text_falls_back_to_advisory():
    """构思正文缺失时无法判断，退回旧行为（只报不停产），不制造新的停产。"""
    meta = {
        "creation_protagonist_name": "沈小禾",
        "creation_protagonist_source": "llm_premise_identity_resolution",
    }
    assert _identity_mismatch_is_advisory(meta) is True


def test_unnamed_concept_prose_does_not_become_a_false_identity_split():
    """规划器补出的名字缺席，不等于构思正文里存在另一个主角。

    真机 custom-xuanhuan-1788329469：构思正文始终只写“现代应急物流
    负责人/他/城主”，规划期随后把这个人命名为“沈确”。三份正文没有出现
    任何竞争姓名，却因规范名缺席 3/3 被判成身份分裂并停在 needs_replan。
    """

    meta = {
        "logline": "现代应急物流负责人坠入异世界死城，要在三十天内救活商路。",
        "premise": "现代应急物流与城市运营负责人穿越到边境废城，他只能改写流通规则。",
        "synopsis": "城里只剩老弱与空仓，这位新城主必须在效率、民生与盟约之间选择。",
        "creation_protagonist_name": "沈确",
        "creation_protagonist_source": "llm_premise_identity_resolution",
    }

    assert _identity_mismatch_is_advisory(meta) is True


def test_validation_report_uses_the_new_judgement():
    import inspect

    from bestseller.services import book_design

    src = inspect.getsource(book_design.validate_project_book_design)
    assert "_identity_mismatch_is_advisory" in src
