"""点开欲判官的构造/解析契约（2026-08-11）。

判官本身要先被榜单数据验证（scripts/validate_hook_pull_judge.py，真机），
这里只钉确定性部分：prompt 必须携带锚例刻度与四种定罪结构、解析必须
容忍模型噪声、锚例与验证集不得重叠。
"""

from __future__ import annotations

import pytest
import yaml

from bestseller.services.hook_pull_judge import (
    anchor_texts,
    build_hook_pull_messages,
    parse_hook_pull_verdict,
)

pytestmark = pytest.mark.unit


# ── prompt 构造：锚定评分 + 用户定罪的四结构 ──────────────────────────────


def test_desire_prompt_carries_anchor_scale_without_defect_axes() -> None:
    """欲望通道只评拉力。v6/v7 真机定罪：缺陷清单塞进同一次调用后判官
    看什么都像病（11 本在榜书被扣 passive），所以缺陷归检察官通道。"""

    system, user = build_hook_pull_messages(
        title="凡骨试题", hook="一介凡骨誓要登仙", genre="东方玄幻", channel="男频"
    )
    # 锚例刻度：强锚（真实榜单）与弱锚（定罪改写）都必须在场
    assert "领证三天" in user and "凡骨" in user
    assert "修伞的老匠人" in user  # 被动主角弱锚
    # 渴望优先协议：goal_quote 引文接地 + 答不出赢什么 → 压到 4
    assert "goal_quote" in user
    assert "赢什么" in user and "总分不得超过 4" in user
    assert "《凡骨试题》" in user  # 书名括号闭合（曾有 » 笔误）
    assert "男频" in system
    # 缺陷轴不许出现在欲望通道里（注意力稀释=全员扣帽子）
    for axis in ("logic_break", "hollow_twist", "genre_mismatch"):
        assert axis not in user, axis


def test_defect_prompt_carries_all_axes_and_demands_evidence() -> None:
    from bestseller.services.hook_pull_judge import build_hook_defect_messages

    system, user = build_hook_defect_messages(
        title="凡骨试题", hook="一介凡骨誓要登仙", genre="东方玄幻"
    )
    for axis in (
        "passive", "irony_only", "stilted",
        "logic_break", "hollow_twist", "genre_mismatch",
    ):
        assert axis in user, axis
    assert "evidence" in user
    assert "逐字" in system or "逐字" in user


def test_prompt_reflects_channel() -> None:
    system, _ = build_hook_pull_messages(
        title="x", hook="y", genre="现言脑洞", channel="女频"
    )
    assert "女频" in system


# ── 解析：模型噪声不许变成假分数 ──────────────────────────────────────────


def test_parse_extracts_score_flags_craving() -> None:
    raw = (
        "好的，评分如下：\n"
        '{"goal_quote":"要把假画案翻过来","craving":"想看他把假画案翻成成名局",'
        '"flags":["passive"],"score":3,"reason":"主角被逼着行动"}'
    )
    parsed = parse_hook_pull_verdict(raw)
    assert parsed is not None
    assert parsed["score"] == 3.0
    assert parsed["flags"] == ["passive"]
    assert "翻成成名局" in parsed["craving"]


def test_parse_clamps_out_of_range_scores() -> None:
    assert parse_hook_pull_verdict('{"score": 37}')["score"] == 10.0
    assert parse_hook_pull_verdict('{"score": -2}')["score"] == 0.0


@pytest.mark.parametrize(
    "raw",
    ["", "不是JSON", '{"reason":"没有score"}', '{"score":"很高"}', "[1,2,3]"],
)
def test_parse_rejects_unusable_payloads(raw: str) -> None:
    assert parse_hook_pull_verdict(raw) is None


# ── 防泄漏：锚例（进 prompt）与验证集（评 prompt）必须无交集 ──────────────


def test_eval_set_does_not_overlap_judge_anchors() -> None:
    spec = yaml.safe_load(open("config/hook_pull_eval.yaml", encoding="utf-8"))
    anchors = anchor_texts()
    assert len(anchors) == 7  # 4 强（含中锚） + 3 弱
    for group in ("positives", "negatives", "controls"):
        for item in spec[group]:
            for anchor in anchors:
                assert item["hook"][:20] not in anchor, item["title"]
                assert anchor[:20] not in item["hook"], item["title"]


def test_eval_set_shape() -> None:
    spec = yaml.safe_load(open("config/hook_pull_eval.yaml", encoding="utf-8"))
    assert len(spec["positives"]) >= 20, "榜单正样本要够统计"
    assert len(spec["negatives"]) >= 5, "用户定罪负样本"
    assert spec["controls"], "用户认可对照条"
    for group in ("positives", "negatives", "controls"):
        for item in spec[group]:
            assert item["hook"].strip() and item["genre"].strip(), item


# ── 定罪句式的确定性检出（LLM flag 3 采样只抓到 1 次，句法归句法）──────────


from bestseller.services.hook_pull_judge import detect_condemned_hook_structures


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("黑雨落在谁身上，谁就在三天内说出一个秘密。", "whoever_rule"),
        ("凡是在桥上回头的人，都会看见自己最想忘掉的一天。", "whoever_rule"),
        ("他给谁照，谁就欠他一盏长明灯。", "whoever_rule"),
        ("捞多了鱼死，捞少了客人走。", "symmetric_rule"),
        ("打水多了井会哭，打水少了全镇渴。", "symmetric_rule"),
        ("底片上亡者开口就是要走，闭口就是要留。", "symmetric_rule"),
    ],
)
def test_condemned_structures_fire(text: str, expected: str) -> None:
    assert expected in detect_condemned_hook_structures(text)


@pytest.mark.parametrize(
    "text",
    [
        # 《时停起手》边界样本：今天/明天差一字但不是极性对立
        "他每天能静止时间一分钟，今天不用，明天还能累积叠加。",
        # 方位/范围列举是人类惯用对偶，100 本榜单扫出的 3 个误报全在这类
        "上到领导，下到军嫂，所有人都劝他离婚。",
        "人前殊途陌路，人后颠鸾倒凤。",
        "昔日天骄被挚爱诬陷，狱中熬过七年；踏出监狱那天，整个世界才猛然惊觉。",
    ],
)
def test_human_idioms_do_not_fire(text: str) -> None:
    assert detect_condemned_hook_structures(text) == []


# ── 定罪结构在选题层沉底（比撞车更深，且绝不清空池）──────────────────────


from bestseller.services import concept_tournament as ct


def _rank_item(index: int, **overrides: object) -> dict:
    item: dict = {
        "index": index,
        "domain": f"域{index}",
        "freshness": 8.0,
        "click_seed": 8.0,
        "character_logic": 8.0,
        "action_seed": 8.0,
        "promise_survival": 8.0,
        "genre_fidelity": 8.0,
        "ai_assembly": 0.0,
        "dumb_cost": False,
        "after_opening_promise": "开局之后仍有承诺",
        "action_families": ["行动一", "行动二", "行动三"],
        "growth_surface": "持续积累面",
    }
    item.update(overrides)
    return item


def test_condemned_structure_sinks_below_market_collision() -> None:
    ranking = [
        _rank_item(0, condemned_structure=["whoever_rule"]),
        _rank_item(1, market_collision=[{"title": "撞", "overlap": 0.3}]),
        _rank_item(2),
    ]
    picked = ct._select_raw_ideas_for_expansion(
        ranking, raw_floor=7.0, progression_floor=5.0, limit=3
    )
    assert [item["index"] for item in picked] == [2, 1, 0]


def test_all_condemned_still_returns_candidates() -> None:
    """全命中也要给展开位——池永远不清空。"""

    ranking = [
        _rank_item(i, condemned_structure=["symmetric_rule"]) for i in range(3)
    ]
    picked = ct._select_raw_ideas_for_expansion(
        ranking, raw_floor=7.0, progression_floor=5.0, limit=2
    )
    assert len(picked) == 2


def test_parser_accepts_the_second_batch_axes() -> None:
    """2026-08-11 用户二批定罪：施受不匹配/落点自明/题材不贴合三轴。"""

    parsed = parse_hook_pull_verdict(
        '{"score":3,"flags":["logic_break: 施受不匹配","hollow_twist","genre_mismatch"]}'
    )
    assert parsed is not None
    assert parsed["flags"] == ["logic_break", "hollow_twist", "genre_mismatch"]


# ── 引文核对：goal_quote 必须真在原文里（把「禁脑补」变成确定性校验）────────


def test_fabricated_goal_quote_voids_the_sample() -> None:
    raw = '{"goal_quote":"誓要登顶武道","craving":"登顶","flags":[],"score":8}'
    assert parse_hook_pull_verdict(raw, source_text="他只想安静修伞。") is None


def test_verbatim_goal_quote_passes_and_supports_craving() -> None:
    raw = '{"goal_quote":"努力活下去","craving":"活下来变强","flags":[],"score":7}'
    parsed = parse_hook_pull_verdict(
        raw, source_text="他被困游戏，只能不断挖矿冲级加点，努力活下去！"
    )
    assert parsed is not None and parsed["craving"] == "活下来变强"


def test_missing_goal_quote_strips_the_craving() -> None:
    raw = '{"goal_quote":"","craving":"翻身逆袭","flags":["passive"],"score":3}'
    parsed = parse_hook_pull_verdict(raw, source_text="随便什么原文")
    assert parsed is not None and parsed["craving"] == ""


# ── 检察官通道：定罪必须有逐字证据 ────────────────────────────────────────


from bestseller.services.hook_pull_judge import parse_hook_defect_verdict

_SRC = "《试题》 职业玩家因为腱鞘炎被医院劝退，赌一把登录新游。"


def test_defect_vote_with_verbatim_evidence_convicts() -> None:
    raw = '{"hits":[{"axis":"logic_break","evidence":"被医院劝退"}]}'
    assert parse_hook_defect_verdict(raw, source_text=_SRC) == ["logic_break"]


def test_defect_vote_with_fabricated_evidence_is_discarded() -> None:
    raw = '{"hits":[{"axis":"logic_break","evidence":"被学校开除"}]}'
    assert parse_hook_defect_verdict(raw, source_text=_SRC) == []


def test_defect_vote_with_empty_evidence_is_discarded() -> None:
    raw = '{"hits":[{"axis":"passive","evidence":""}]}'
    assert parse_hook_defect_verdict(raw, source_text=_SRC) == []


def test_genre_mismatch_conviction_allows_empty_evidence() -> None:
    raw = '{"hits":[{"axis":"genre_mismatch","evidence":""}]}'
    assert parse_hook_defect_verdict(raw, source_text=_SRC) == ["genre_mismatch"]


def test_unknown_axis_and_bad_payloads_are_rejected() -> None:
    assert parse_hook_defect_verdict(
        '{"hits":[{"axis":"乱写","evidence":"被医院劝退"}]}', source_text=_SRC
    ) == []
    assert parse_hook_defect_verdict("不是JSON", source_text=_SRC) is None
    assert parse_hook_defect_verdict('{"没有hits":1}', source_text=_SRC) is None


def test_clean_verdict_returns_no_hits() -> None:
    assert parse_hook_defect_verdict('{"hits":[]}', source_text=_SRC) == []


# ── 维持式处境模板（2026-08-11 三批定罪：身份+宿命+日常维持≠故事）──────────


@pytest.mark.parametrize(
    "text",
    [
        "天生阴命的天文台观测员，命里注定要通过镜头看见不该被看见的东西，"
        "他只好一边守着山顶台，一边把每一帧「错片」从档案里抹掉。",
        "夜班司机命中注定要接那些打不到车的客人。",
        "他只好一边跑车一边把后座的「乘客」一个个送走。",
    ],
)
def test_routine_setup_template_fires(text: str) -> None:
    assert "routine_setup" in detect_condemned_hook_structures(text)


@pytest.mark.parametrize(
    "text",
    [
        # 事件句里的同时动作是合法的（没有宿命/无奈标记）
        "他一边跑一边喊，把整条街的人都喊醒了。",
        # 真实榜单钩子（含宿命话题但不用模板句式）
        "算命的说他是天生妖胎，命里带十八道劫，每道劫都是一只成了气候的恶鬼。",
    ],
)
def test_routine_setup_spares_events_and_board_hooks(text: str) -> None:
    assert "routine_setup" not in detect_condemned_hook_structures(text)


def test_author_pitch_arm_demands_a_landed_event() -> None:
    """铁律四【事件先行】：类别级要求进 prompt，模板 token 只在检测器（种词铁律）。"""

    _, user = ct._build_raw_idea_pool_messages(
        genre="悬疑灵异", sub_genre="民俗怪谈", count=12,
        audience_orientation="男频", prompt_arm="author_pitch",
    )
    assert "事件先行" in user and "当场兑现" in user
    # 种词检查：定罪模板的 token 不许出现在生成 prompt 里
    for token in ("命里注定", "命中注定", "一边"):
        assert token not in user, f"种词泄漏: {token}"


# ── 「每X就Y」机制条款（2026-08-12 四批：与谁A谁就B同族，0/100人类基线）────


@pytest.mark.parametrize(
    "text",
    [
        "他给游戏写的废稿被系统当现实，他每补一行剧情，门外就消失一个人。",
        "每敲三下门，屋里就多一个人影。",
    ],
)
def test_per_action_rule_fires(text: str) -> None:
    assert "per_action_rule" in detect_condemned_hook_structures(text)


@pytest.mark.parametrize(
    "text",
    [
        # 每+时间间隔是人类惯用复现叙述（全量语料唯一误报样本，必须放过）
        "被诅咒的人，每过一段时间，就要强行被拉入血门之后的可怕世界。",
        "他每天能静止时间一分钟，今天不用，明天还能累积叠加。",
    ],
)
def test_per_action_rule_spares_time_recurrence(text: str) -> None:
    assert "per_action_rule" not in detect_condemned_hook_structures(text)
