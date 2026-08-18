"""E 层编辑判官——设定质量判据的锁（2026-08-18 榜单市场调研蒸馏）。

判据来源：70 本在榜男频细读（docs/concept-quality-system-redesign-20260818.md）。
定罪本尊《九姓井口只认我》按 12 条判据 1.5/12：通行证/规则/循环/爽型四大件
系统性缺失，而全链没有任何判官在判——E 判官补展开层的设定质量轴。
铁律：不种词、证据引文、两票定罪交集、只有承重轴(e1/e2/e4)定罪、零杀权。
"""

from __future__ import annotations

import json

import pytest

from bestseller.services.concept_tournament import (
    _EXPANSION_EDITOR_AXES,
    _EXPANSION_EDITOR_CONVICTION_AXES,
    _build_expansion_editor_messages,
    _parse_story_layer_verdict,
)

pytestmark = pytest.mark.unit


_JIUXING_CARD = {
    # 《九姓井口只认我》事故卡的核心形状
    "protagonist_identity": "被三姓围逼的底层村民陆沉",
    "core_abnormality": "全村共用的老井只认他一家打水，井沿会浮字",
    "current_goal": "在一顿午饭的时间里顶住三姓逼迫，不交出井",
    "deformable_loop": "每轮井口还认不认陆家会随上一轮选择变形",
    "failure_cost": "祖宅被收，母亲幼妹流落",
    "success_cost": "井的旧账落到他头上",
    "reader_promise": "守住井并揭开井底真相",
    "opening_crisis": "赵家媳妇桶绳连断七根，全村停水一天就有人饿死",
    "difference_point": "井有意愿，只认一家",
}


def test_prompt_carries_axes_and_board_anchors():
    system, user = _build_expansion_editor_messages(
        _JIUXING_CARD, genre="东方玄幻", sub_genre="东方玄幻"
    )
    for axis in _EXPANSION_EDITOR_AXES:
        assert axis in user, f"判据缺轴 {axis}"
    # 判据必须锚定真实榜单书（判官读例证合法；例证绝不进生成 prompt）
    assert "聚宝仙盆" in user and "凡骨" in user
    assert "证据" in system and "只输出JSON" in system


def test_prompt_does_not_seed_disease_tokens():
    _, user = _build_expansion_editor_messages(
        _JIUXING_CARD, genre="东方玄幻", sub_genre="东方玄幻"
    )
    for token in ("打脸", "逆袭", "碾压", "扮猪吃虎", "跪", "求饶", "废柴流"):
        assert token not in user, f"判据 prompt 种词：{token}"


def test_conviction_only_on_load_bearing_axes():
    # e3/e6 是加分项：40% 在榜执行流没有它们照样活，拿加分项定罪会打死整池
    assert _EXPANSION_EDITOR_CONVICTION_AXES == {
        "e1_rule_demonstrable",
        "e2_constraint_plot",
        "e4_world_rule_first",
    }


def test_parser_with_editor_axes():
    raw = json.dumps(
        {
            "e1_rule_demonstrable": {"pass": False, "quote": "水就是水"},
            "e2_constraint_plot": {"pass": False, "plots": []},
            "e3_paradox_engine": {"pass": False, "quote": ""},
            "e4_world_rule_first": {
                "pass": False,
                "world_rule": "",
                "exception": "井只认陆家",
            },
            "e5_witness_slot": {"pass": True, "quote": "九姓村民都看着"},
            "e6_goal_quantified": {"pass": False, "quote": ""},
            "revise_direction": "给井接入世界体系并让打水产生收益差",
        },
        ensure_ascii=False,
    )
    verdict = _parse_story_layer_verdict(raw, axes=_EXPANSION_EDITOR_AXES)
    assert verdict is not None
    assert set(verdict["failed_axes"]) >= {
        "e1_rule_demonstrable",
        "e2_constraint_plot",
        "e4_world_rule_first",
    }
    # 定罪集 = 两票交集 ∩ 承重轴（e3/e6 即使 fail 也不进定罪）
    convicted = set(verdict["failed_axes"]) & _EXPANSION_EDITOR_CONVICTION_AXES
    assert "e3_paradox_engine" not in convicted
    assert "e6_goal_quantified" not in convicted


def test_missing_axis_is_unknown_not_fail():
    raw = json.dumps(
        {"e1_rule_demonstrable": {"pass": True, "quote": "放入一颗次日两颗"}},
        ensure_ascii=False,
    )
    verdict = _parse_story_layer_verdict(raw, axes=_EXPANSION_EDITOR_AXES)
    assert verdict is not None
    assert verdict["failed_axes"] == []
    assert verdict["axes"]["e4_world_rule_first"]["pass"] is None


def test_wiring_present_with_recheck():
    import inspect

    from bestseller.services import concept_tournament

    src = inspect.getsource(concept_tournament.run_concept_tournament)
    assert "expansion_editor_judge_enabled" in src
    assert "_build_expansion_editor_messages" in src
    assert src.count("recheck_no_improvement") >= 2, "S 与 E 两处复核都必须在"
