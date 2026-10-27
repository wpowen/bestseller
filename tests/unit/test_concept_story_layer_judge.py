"""P0 故事层判卡——判据措辞与定罪逻辑的锁。

背景：概念层判据只看「概念一句话」，展开层 wants/stakes/系列引擎零业务判据，
《丑石》三缺陷（防守型欲望/两头输赌注/金手指自我稀释）全部漏过。
本判官按爽文方法论 v2 写成结构判定，铁律：不种词、证据引文、两票定罪、零杀权。
"""

from __future__ import annotations

import json

from bestseller.services.concept_tournament import (
    _STORY_LAYER_AXES,
    _build_story_layer_judge_messages,
    _build_narrow_card_revision_context,
    _constrain_story_layer_repair,
    _parse_story_layer_verdict,
)


_SHIZHI_CARD = {
    # 《丑石》事故卡的核心字段（真机产物原文）
    "protagonist_identity": "鹿鸣砦最废的鉴石学徒石枳",
    "protagonist_private_desire": "真正想保住的是那间属于自己的小铺",
    "protagonist_flaw": "不敢冒尖",
    "current_goal": "三天之内把丑石切开，给师傅挣回脸面、抵清三年学费",
    "core_abnormality": "全砦只有他能听见石中物的诉求",
    "deformable_loop": "每开一次石头，身边就多一个能听石的人替他谈判",
    "failure_cost": "三年学费翻成利滚利，铺面被收走",
    "success_cost": "听得见石头这件事藏不住，要么被献祭要么沦为矿王的活鉴石器",
    "reader_promise": "替石头谈判的鉴石人",
    "emotional_promise": "翻身",
    "opening_crisis": "丑石追着他回了铺子，三天后开石大典",
    "difference_point": "石头有意愿，人替石头谈",
}


def test_prompt_carries_all_axes_and_card_fields():
    system, user = _build_story_layer_judge_messages(
        _SHIZHI_CARD, genre="东方玄幻", sub_genre="东方玄幻", audience_orientation="男"
    )
    for axis in _STORY_LAYER_AXES:
        assert axis in user, f"判据缺轴 {axis}"
    assert "保住的是那间属于自己的小铺" in user, "卡片字段没进 prompt"
    assert "证据" in system and "只输出JSON" in system


def test_prompt_does_not_seed_disease_tokens():
    """种词铁律：判据只写结构。把爽点 token 写进 prompt，所有书会长成一副样子。"""

    _, user = _build_story_layer_judge_messages(
        _SHIZHI_CARD, genre="东方玄幻", sub_genre="东方玄幻"
    )
    for token in ("打脸", "逆袭", "碾压", "扮猪吃虎", "跪", "求饶", "废柴流"):
        assert token not in user, f"判据 prompt 种词：{token}"


def test_prompt_accepts_protection_and_investigation_as_agency_in_both_languages():
    _, user = _build_story_layer_judge_messages(
        {"protagonist_private_desire": "保住清白", "current_goal": "查明伪造者"},
        genre="现实悬疑",
        sub_genre="现实悬疑",
    )
    assert "保护、查明、洗清、修复" in user
    assert "Protection, investigation, exoneration, repair" in user
    assert "不等于扩张、复仇" in user


def test_story_layer_uses_target_length_and_does_not_universalize_other_axes():
    _, user = _build_story_layer_judge_messages(
        {"current_goal": "查明真相", "deformable_loop": "逐案核验"},
        genre="现实悬疑",
        sub_genre="刑侦",
        target_chapters=20,
    )
    assert "目标篇幅：20章" in user
    assert "悲剧或高代价弧线也可以通过" in user
    assert "普通侦探、群像或无超能故事不要求能力独占" in user
    assert "终局谜底、有限次数或有限目标本身不是失败" in user
    assert "赢了也是灾难" not in user


def test_story_layer_repair_freezes_private_desire_and_world_fields():
    original = {
        "protagonist_identity": "档案员",
        "protagonist_private_desire": "保住二十三年的清白履历",
        "current_goal": "查清照片来源",
        "core_abnormality": "只能核验归档链",
        "opposing_ecology": ["队内调查者"],
        "success_cost": "必须公开核验方法",
    }
    model_repair = {
        **original,
        "protagonist_private_desire": "让伪造者职业生涯清零",
        "current_goal": "抓住伪造者并让其付出代价",
        "core_abnormality": "获得读心能力",
        "opposing_ecology": ["新的邪恶组织"],
        "success_cost": "失去工作",
    }
    repaired = _constrain_story_layer_repair(
        original, model_repair, ["s1_wants_aggression"]
    )
    assert repaired["protagonist_private_desire"] == original["protagonist_private_desire"]
    assert repaired["core_abnormality"] == original["core_abnormality"]
    assert repaired["opposing_ecology"] == original["opposing_ecology"]
    assert repaired["current_goal"] == model_repair["current_goal"]


def test_story_layer_revision_context_matches_allowed_field_boundary():
    story_context = _build_narrow_card_revision_context(
        card={"core_abnormality": "shared rule", "reader_promise": "solve cases"},
        failed_axes=["s3_exclusivity", "s5_three_second_pitch"],
        directions="repair",
        story_layer=True,
    )
    assert "【本次允许修改字段】" in story_context
    assert "core_abnormality" in story_context
    assert "reader_promise" in story_context
    assert "私人欲望" in story_context
    commercial_context = _build_narrow_card_revision_context(
        card={"reader_promise": "solve cases"},
        failed_axes=["reader_promise"],
        directions="repair",
    )
    assert "Agency repair" not in commercial_context
    assert "【本次允许修改字段】" not in commercial_context


def test_story_layer_repair_preserves_explicit_revenge_when_user_chose_it():
    original = {
        "protagonist_private_desire": "让杀害父亲的人付出代价",
        "current_goal": "找到凶手",
        "success_cost": "公开自己的身份",
    }
    model_repair = {
        **original,
        "protagonist_private_desire": "过安稳日子",
        "current_goal": "找到凶手并揭露证据",
    }
    repaired = _constrain_story_layer_repair(
        original, model_repair, ["s1_wants_aggression"]
    )
    assert repaired["protagonist_private_desire"] == original["protagonist_private_desire"]
    assert repaired["current_goal"] == model_repair["current_goal"]


def test_parser_extracts_fails_and_direction():
    raw = json.dumps(
        {
            "s1_wants_aggression": {"pass": False, "quote": "保住小铺"},
            "s2_stakes_upside": {
                "pass": False,
                "win_quote": "挣回脸面",
                "cost_quote": "要么被献祭",
            },
            "s3_exclusivity": {"pass": False, "quote": "多一个能听石的人"},
            "s4_promise_survival": {"pass": True, "quote": "每块石头不同"},
            "s5_three_second_pitch": {"pass": True, "pitch": "他要当众赢下矿王"},
            "revise_direction": "欲望改为进攻型",
        },
        ensure_ascii=False,
    )
    verdict = _parse_story_layer_verdict(raw)
    assert verdict is not None
    assert verdict["failed_axes"] == [
        "s1_wants_aggression",
        "s2_stakes_upside",
        "s3_exclusivity",
    ]
    assert verdict["revise_direction"] == "欲望改为进攻型"
    assert verdict["axes"]["s3_exclusivity"]["quote"] == "多一个能听石的人"


def test_parser_missing_axis_is_unknown_not_fail():
    """无杀权精神：判官漏轴 ≠ 定罪。"""

    raw = json.dumps(
        {"s1_wants_aggression": {"pass": True, "quote": "夺回矿权"}},
        ensure_ascii=False,
    )
    verdict = _parse_story_layer_verdict(raw)
    assert verdict is not None
    assert verdict["failed_axes"] == []
    assert verdict["axes"]["s2_stakes_upside"]["pass"] is None


def test_two_vote_conviction_intersection():
    """两票定罪 = 两次投票 failed_axes 的交集（判官单票噪声已定量不可信）。"""

    vote1 = {"failed_axes": ["s1_wants_aggression", "s3_exclusivity"]}
    vote2 = {"failed_axes": ["s3_exclusivity", "s4_promise_survival"]}
    convicted = sorted(
        set(vote1["failed_axes"]) & set(vote2["failed_axes"])
    )
    assert convicted == ["s3_exclusivity"]
