"""书名淘汰赛（2026-08-21 真机 custom-xuanhuan-1787320762）。

用户定罪：「现在生成的书名不像一个完整的书名，更像是一些字符串的拼接。」
离线复现确认它就是拼接——见 services/title_tournament.py 模块文档串。

本套测试锁住三件事：
  1. 实体抽取不认任何具体书的私货（现有 `_resolve_object_token` 写死了
     「青囊/困魂镜/归墟会」这些上一本书的物件，永远找不到本书的蒸灵锅）；
  2. 候选 prompt 只给实体与句法骨架，不给题材词（不种词铁律）；
  3. 判官只挣排序权，确定性门才有否决权。
"""

from __future__ import annotations

import pytest

from bestseller.services.title_tournament import (
    TITLE_PATTERN_FAMILIES,
    TitleCandidate,
    apply_arena_verdict,
    build_title_arena_messages,
    build_title_candidate_messages,
    deterministic_title_defects,
    extract_title_entities,
    parse_title_candidates,
    select_title_winner,
    title_tournament_receipt,
    zh_length,
)


def test_conception_title_anchors_use_approved_hook_object_not_taxonomy() -> None:
    from bestseller.services.conception import _build_conception_title_anchor_groups

    groups = _build_conception_title_anchor_groups(
        concept_contract={
            "hook_card": {
                "protagonist": "商洛，废城挂名城主",
                "abnormality": "界碑残片：拍下后让来客耳中浮出报价",
                "story_motion": "每次报价成交都会扩大商路与对手反制",
                "opening_event": "狮人商队拒绝进城",
                "central_contradiction": "竞争据点与各族商队不断改变报价策略",
            }
        },
        protagonist="商洛",
        golden_finger="",
        premise="商洛穿越成废城城主，用旧界碑碎片向商队报价。",
        synopsis="狮人想绕路，却发现绕道价格更贵。",
    )

    assert "商洛" in groups["identity"]
    assert "界碑残片" in groups["object"]
    assert "异世大陆" not in {item for values in groups.values() for item in values}


def test_conception_title_anchors_do_not_turn_negated_mechanics_into_requirements() -> None:
    from bestseller.services.conception import _build_conception_title_anchor_groups

    groups = _build_conception_title_anchor_groups(
        concept_contract=None,
        protagonist="商洛",
        golden_finger="无显性系统/面板/血脉外挂，优势来自前世职业经验。",
        premise="商洛穿越成废城城主，拍下旧界碑向商队报价。",
        synopsis="他要把烂木门后的废城变成北线商路咽喉。",
    )

    flattened = {item for values in groups.values() for item in values}
    assert not {"系统", "面板", "血脉", "外挂"} & flattened


pytestmark = pytest.mark.unit

_LOGLINE = (
    "通灵百家巷里最怂的十九岁温符徒温迟，靠一口百年蒸灵锅守着父亲留下的早市摊，"
    "每天辰时开锅替坊民温符换零钱。"
)
_GOLDEN = "百年蒸灵锅——一只能听见、能说话、记仇、心情决定接单范围的老锅"
_TAGS = ["玄幻", "市井日常", "奇物养成", "男频", "番茄爽文"]


# ── 实体抽取 ────────────────────────────────────────────────────────────


def test_extracts_the_three_entities_from_real_concept():
    e = extract_title_entities(protagonist_name="主角", golden_finger=_GOLDEN, logline=_LOGLINE)
    assert e.protagonist == "温迟"
    assert e.object_name == "百年蒸灵锅"
    assert e.place == "通灵百家巷"


def test_placeholder_protagonist_is_not_taken_as_a_name():
    """conception 传的是写死的「主角」，不能当成人名。"""
    e = extract_title_entities(protagonist_name="主角", logline="没有角色词的一句话。")
    assert e.protagonist == ""


def test_place_must_start_a_clause():
    """不加这条会从「每天辰时开锅替坊民」中间切出「天辰时开锅替坊」当地名。"""
    e = extract_title_entities(logline=_LOGLINE)
    assert e.place == "通灵百家巷"


def test_object_phrase_stops_at_the_dash():
    e = extract_title_entities(golden_finger=_GOLDEN)
    assert e.object_name == "百年蒸灵锅"


def test_missing_material_yields_empty_not_garbage():
    e = extract_title_entities()
    assert e.is_empty


# ── 候选 prompt ────────────────────────────────────────────────────────


def test_candidate_prompt_carries_entities_and_forbids_tags():
    e = extract_title_entities(protagonist_name="主角", golden_finger=_GOLDEN, logline=_LOGLINE)
    _system, user = build_title_candidate_messages(entities=e, logline=_LOGLINE, genre_label="玄幻")
    assert "温迟" in user and "百年蒸灵锅" in user and "通灵百家巷" in user
    assert "不许使用题材名、分类标签或营销词" in user
    # 一个语法单位——这正是真机书名失守的地方
    assert "一个语法单位" in user
    for _key, label, _guide in TITLE_PATTERN_FAMILIES:
        assert label in user


def test_candidate_prompt_does_not_seed_genre_vocabulary():
    """不种词：prompt 里只许出现类别与正例，不许塞题材 token 词表。"""
    e = extract_title_entities(logline=_LOGLINE, golden_finger=_GOLDEN)
    _system, user = build_title_candidate_messages(entities=e, logline=_LOGLINE, genre_label="玄幻")
    for tag in ("市井日常", "奇物养成", "番茄爽文"):
        assert tag not in user


def test_title_families_use_forward_question_without_invented_mystery() -> None:
    families = {key: (label, guide) for key, label, guide in TITLE_PATTERN_FAMILIES}

    assert "suspense_question" not in families
    assert "forward_question" in families
    assert "不能为标题新增秘密历史" in families["forward_question"][1]


def test_candidate_prompt_carries_authoritative_seed_prohibitions():
    seed = (
        "主角重组商路规则，把废城经营成大陆交通中枢。"
        "禁止沿用收费站城主、界碑自动报价和纯粹收过路费设定。"
    )
    e = extract_title_entities(logline=_LOGLINE, golden_finger=_GOLDEN)

    _system, user = build_title_candidate_messages(
        entities=e,
        logline=_LOGLINE,
        genre_label="异世大陆",
        authoritative_seed=seed,
    )

    assert seed in user
    assert "用户明确禁止" in user
    assert "核心卖点" in user


# ── 确定性门（唯一有否决权的一层）────────────────────────────────────


def test_tag_as_entity_is_rejected():
    assert "ungrounded_tag" in deterministic_title_defects(
        "月照市井日常", tags=_TAGS, prose=_LOGLINE
    )


def test_grounded_title_passes():
    assert deterministic_title_defects("我的蒸灵锅比我还毒", tags=_TAGS, prose=_LOGLINE) == ()


def test_length_band_and_duplicate():
    assert "length_out_of_band" in deterministic_title_defects("锅", tags=[], prose=_LOGLINE)
    assert "duplicate" in deterministic_title_defects(
        "养鬼的胡大师", tags=[], prose=_LOGLINE, existing_titles=["养鬼的胡大师"]
    )


def test_zh_length_ignores_punctuation():
    assert zh_length("我的蒸灵锅，比我还毒") == 9


# ── 竞争与胜出 ────────────────────────────────────────────────────────


def test_judge_cannot_veto_only_reorder():
    """判官只挣排序权：被它 reject 的候选仍然留在池子里。"""
    rows = parse_title_candidates(
        {"candidates": [{"title": "甲", "family": "A"}, {"title": "乙", "family": "B"}]}
    )
    apply_arena_verdict(rows, {"pick": 2, "reject": 1})
    winner = select_title_winner(rows)
    assert winner is not None and winner.title == "乙"
    assert rows[0].survives, "被判官 reject 不等于被淘汰"


def test_deterministic_gate_does_veto():
    rows = parse_title_candidates({"candidates": [{"title": "甲"}, {"title": "乙"}]})
    rows[1].rejected_by = ("ungrounded_tag",)
    apply_arena_verdict(rows, {"pick": 2})  # 判官最爱「乙」
    winner = select_title_winner(rows)
    assert winner is not None and winner.title == "甲", "确定性门否决优先于判官偏好"


def test_all_rejected_falls_back_to_incumbent():
    rows = parse_title_candidates({"candidates": [{"title": "甲"}]})
    rows[0].rejected_by = ("ungrounded_tag",)
    winner = select_title_winner(rows, incumbent="旧书名")
    assert winner is not None and winner.title == "旧书名"


def test_ties_keep_candidate_order_so_results_are_reproducible():
    rows = parse_title_candidates({"candidates": [{"title": "甲"}, {"title": "乙"}]})
    assert select_title_winner(rows).title == "甲"


def test_receipt_records_who_lost_and_why():
    rows = parse_title_candidates({"candidates": [{"title": "甲"}, {"title": "乙"}]})
    rows[1].rejected_by = ("ungrounded_tag",)
    apply_arena_verdict(rows, {"pick": 1})
    receipt = title_tournament_receipt(rows, select_title_winner(rows))
    assert receipt["winner"] == "甲"
    assert receipt["survivor_count"] == 1
    assert receipt["rejected"] == [{"title": "乙", "by": ["ungrounded_tag"]}]


def test_arena_prompt_is_relative_not_absolute():
    """绝对分不可信，只用相对盲评（benchmark-arena-closure-plan 定案）。"""
    _system, user = build_title_arena_messages(
        titles=["甲", "乙", "丙"], logline=_LOGLINE, genre_label="玄幻"
    )
    assert "最想点开" in user and "最不想点" in user
    assert "评分" not in user and "打分" not in user


def test_parse_dedupes_and_skips_blanks():
    rows = parse_title_candidates(
        {"candidates": [{"title": "甲"}, {"title": "甲"}, {"title": ""}, {}]}
    )
    assert [row.title for row in rows] == ["甲"]
