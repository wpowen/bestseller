"""数字事实一致性确定性对表（2026-08-18《九姓井口只认我》定罪）。

真机病灶：冠军卡同一字段 50 字内「陆家第七代」与「陆家这十八代」并存，
chief_editor 抓到但 advisory 零消费方，直通成稿。此类冲突纯规则可判：
同实体锚点 + 同单位 + 不同数值 = 矛盾。零杀权——发现喂给 canon 修复轮。
"""

from __future__ import annotations

import inspect

import pytest

from bestseller.services.concept_fact_consistency import (
    detect_numeric_fact_conflicts,
)

pytestmark = pytest.mark.unit


def test_catches_the_jiuxing_case():
    # 真机原文形状：同字段两个世代数
    fields = {
        "core_abnormality": (
            "井沿浮字'陆家第七代，还欠一次'……陆家这十八代都在替九姓压着"
            "一桩所有人都忘了的旧账"
        )
    }
    conflicts = detect_numeric_fact_conflicts(fields)
    assert len(conflicts) == 1
    c = conflicts[0]
    assert c.unit == "代"
    assert "第七代" in c.quote_a and "十八代" in c.quote_b


def test_cross_field_conflict():
    fields = {
        "premise": "陆家第七代守井人",
        "spine.who": "陆家十八代单传的守井人",
    }
    assert len(detect_numeric_fact_conflicts(fields)) == 1


def test_different_anchors_not_conflict():
    # 三年前爹死 vs 守了十八年：单位不同(年前 vs 年不收)且锚点不同——不许误报
    fields = {"premise": "爹三年前溺死；李家第十八代传人来抢井"}
    conflicts = detect_numeric_fact_conflicts(fields)
    assert conflicts == []


def test_same_value_not_conflict():
    fields = {"a": "陆家第七代", "b": "陆家七代单传"}
    assert detect_numeric_fact_conflicts(fields) == []


def test_bare_number_without_anchor_ignored():
    # 没有实体锚点的裸数字不参与（对不上责任主体，报了就是误伤）
    fields = {"a": "第七代传承。第十八代重现。"}
    # 「第七代」前无 CJK 锚点(句首) → 空锚点丢弃；「。第十八代」同理
    assert detect_numeric_fact_conflicts(fields) == []


def test_canon_repair_consumes_detector():
    from bestseller.services import conception

    src = inspect.getsource(conception._repair_canon_contradictions)
    assert "detect_numeric_fact_conflicts" in src, "确定性发现必须进 canon 修复轮"
    assert "det_before" in src and "det_after" in src, "采纳判定必须计入确定性冲突数"


def test_story_layer_revised_recheck_wired():
    # 白判修复：revised 卡必须复核，复核无改善不采纳
    from bestseller.services import concept_tournament

    src = inspect.getsource(concept_tournament.run_concept_tournament)
    assert "recheck_convicted" in src
    assert "recheck_no_improvement" in src


def test_director_must_adjudicate_review():
    from bestseller.services import conception

    src = inspect.getsource(conception._finalize_user_prompt)
    assert "必裁清单" in src and "review_adjudications" in src
    # 旧点击型规则不得回潮（v0 简介是冠军被误杀时的发货稿）
    assert "结尾留一个悬念钩子" not in src
    assert "陈述句或名场面截断" in src
