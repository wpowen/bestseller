"""L1 unit tests for the deterministic blurb click-power gate.

Guards: strong > weak discrimination, the per-dimension detectors (selling
triad, AI-template, spoiler ending, length envelope, genre signal), graceful
handling of empty input, and the grade ladder.
"""

from __future__ import annotations

import pytest

from bestseller.services.blurb_appeal_gate import evaluate_blurb_appeal

# ruff: noqa: RUF001, RUF003

_STRONG_TITLE = "我老婆是首富"
_STRONG_SYN = (
    "三年赘婿，受尽白眼。\n"
    "退婚宴上，岳父当众羞辱：三天内拿不出一个亿，就滚出林家。\n"
    "没人知道，他随手转出的，是足以买下整座城的隐藏身份。\n"
    "这一次，他要让所有看不起他的人，跪着求他签字。"
)
_STRONG_PREMISE = "被退婚的废物赘婿，其实是隐藏的商业帝国之主，三天对赌局里步步打脸翻盘。"
_STRONG_TAGS = ["赘婿", "打脸", "马甲", "都市", "逆袭"]

_WEAK_TITLE = "风云传说"
_WEAK_SYN = (
    "这是一个关于成长的故事。主角本以为生活很平凡，却没想到命运的齿轮开始转动，"
    "他将何去何从？一段不平凡的旅程就此展开，让我们拭目以待，敬请期待。"
)
_WEAK_PREMISE = "一个少年踏上修炼之路，历经磨难最终成为强者的故事。"
_WEAK_TAGS = ["玄幻", "成长"]


@pytest.mark.unit
def test_strong_blurb_beats_weak_by_wide_margin():
    strong = evaluate_blurb_appeal(
        title=_STRONG_TITLE, synopsis=_STRONG_SYN, premise=_STRONG_PREMISE,
        tags=_STRONG_TAGS, genre="都市", sub_genre="赘婿",
    )
    weak = evaluate_blurb_appeal(
        title=_WEAK_TITLE, synopsis=_WEAK_SYN, premise=_WEAK_PREMISE,
        tags=_WEAK_TAGS, genre="玄幻", sub_genre="升级",
    )
    assert strong.total > weak.total + 10
    assert 0.0 <= weak.total <= 100.0
    assert 0.0 <= strong.total <= 100.0


@pytest.mark.unit
def test_ai_template_phrases_are_penalized():
    v = evaluate_blurb_appeal(
        title="x", synopsis=_WEAK_SYN, premise="", tags=[], genre="玄幻",
    )
    anti = {d.key: d for d in v.dimensions}["anti_template"]
    assert anti.score <= 1.5  # 多处 AI 腔模板句
    assert any("反模板" in f or "原创" in f for f in v.findings)


@pytest.mark.unit
def test_missing_selling_triad_scores_low():
    # No identity / conflict / cost markers — pure abstract description.
    v = evaluate_blurb_appeal(
        title="某个故事", synopsis="他走在路上，看着天空，思考着人生的意义。",
        premise="", tags=[], genre="都市",
    )
    triad = {d.key: d for d in v.dimensions}["selling_triad"]
    assert triad.score <= 2.0


@pytest.mark.unit
def test_spoiler_ending_penalized_open_ending_rewarded():
    spoiled = evaluate_blurb_appeal(
        title="t", synopsis="他历经磨难，最终大结局里终成眷属，从此幸福地生活在一起。",
        premise="", tags=[], genre="都市",
    )
    open_ended = evaluate_blurb_appeal(
        title="t", synopsis="他握紧拳头，望着那扇门：门后等着他的，究竟是生路还是死局？",
        premise="", tags=[], genre="都市",
    )
    s = {d.key: d for d in spoiled.dimensions}["open_loop_end"]
    o = {d.key: d for d in open_ended.dimensions}["open_loop_end"]
    assert o.score > s.score


@pytest.mark.unit
def test_length_format_envelope_respected():
    too_short = evaluate_blurb_appeal(
        title="t", synopsis="短。", premise="", tags=[], genre="都市", platform="番茄小说",
    )
    lf = {d.key: d for d in too_short.dimensions}["length_format"]
    assert lf.score < 5.0


@pytest.mark.unit
def test_genre_lexicon_resolution_does_not_crash_unknown_genre():
    v = evaluate_blurb_appeal(
        title="t", synopsis=_STRONG_SYN, premise="", tags=["x"],
        genre="完全不存在的题材xyz", sub_genre=None,
    )
    assert 0.0 <= v.total <= 100.0
    assert len(v.dimensions) == 10


@pytest.mark.unit
def test_empty_input_does_not_raise():
    v = evaluate_blurb_appeal(title="", synopsis="", premise="", tags=None, genre=None)
    assert isinstance(v.total, float)
    assert v.grade in {"pass", "consider", "recommend"}


@pytest.mark.unit
def test_grade_ladder_thresholds():
    v = evaluate_blurb_appeal(
        title=_STRONG_TITLE, synopsis=_STRONG_SYN, premise=_STRONG_PREMISE,
        tags=_STRONG_TAGS, genre="都市", sub_genre="赘婿",
    )
    # grade must agree with the configured thresholds
    if v.total >= 80:
        assert v.grade == "recommend"
    elif v.total >= 65:
        assert v.grade == "consider"
    else:
        assert v.grade == "pass"


@pytest.mark.unit
def test_critical_floor_caps_when_both_hook_and_emotion_weak():
    # 点击命门全失（既无强钩又无强情绪）→ 即使表面维齐全也不得达标。
    # 真实案例《规则漏洞不保护我》：钩子3.0+情绪1.5 却被表面维堆到 85.2 通过。
    cerebral = (
        "合同签了，违约金二十万，他连饭都快吃不起。\n"
        "纪燃蹲在出租屋地板上数最后几张钞票，前同事在朋友圈庆祝升职。\n"
        "他闭上眼，看见那份合同的灰色字迹：第七条第三款，逻辑链断在实际损失举证四个字上。\n"
        "这是他第一次看见规则背后的裂缝。代价是当晚右耳失聪三小时。下一次用，会剥走什么？"
    )
    v = evaluate_blurb_appeal(title="x", synopsis=cerebral, premise=cerebral[:40],
                              tags=["都市异能", "规则怪谈"], genre="都市")
    dims = {d.key: d.score for d in v.dimensions}
    assert dims["hook_strength"] < 3.5 and dims["emotion_charge"] < 3.0  # 两命门皆弱
    assert v.total < 80  # 不得达标（此前会到 85）


@pytest.mark.unit
def test_critical_floor_not_triggered_when_emotion_carries():
    # 钩子信号平（声明式开篇）但情绪满 → 情绪扛起点击，不封顶。
    emotional = (
        "发薪日前一天他被裁了，房贷、孩子学费、父亲的手术费同时砸下来。\n"
        "所有人都等着看这个老实人垮掉，可他攥着最后三千块，做了个谁也没想到的决定。\n"
        "这一年，他要从最底层，亲手挣回所有人欠他的体面。"
    )
    v = evaluate_blurb_appeal(title="x", synopsis=emotional, premise=emotional[:40],
                              tags=["职场", "逆袭", "现实"], genre="现实")
    assert {d.key: d.score for d in v.dimensions}["emotion_charge"] >= 3.0
    assert not any("命门" in f for f in v.findings)  # 未被命门封顶


@pytest.mark.unit
def test_embodied_stakes_emotion_clears_floor_without_keywords():
    # show-don't-tell 悬疑/怪谈：靠两难+切肤+迫近+骇异承载情绪，无爽文情绪词。
    # 此前 emotion_charge≈1.5 被命门封顶 78；修复后具身通道应 ≥3.0。
    # (同 prose 层 scene-emotion-hook-scorer-punishes-showdonttell 的器械错。)
    embodied = (
        "凌晨三点，殡仪馆第七具遗体睁开了眼。\n"
        "他能补全死者没说完的那句话，让人回魂——代价是划走他自己等长的寿命。\n"
        "他攒下三百二十个时辰，只为撬开妹妹的工单。可签收人那栏，写着他自己的名字。\n"
        "他现在要决定：是让妹妹活，还是让自己有命去救她。"
    )
    v = evaluate_blurb_appeal(
        title="我替死神签收加班", synopsis=embodied, premise=embodied[:40],
        tags=["都市怪谈", "悬疑", "代价流"], genre="都市",
    )
    assert {d.key: d.score for d in v.dimensions}["emotion_charge"] >= 3.0


@pytest.mark.unit
def test_embodied_emotion_is_noop_safe_for_keyword_blurbs():
    # 既有爽文强稿(关键词通道已满)：max() 取较高者，分数不被降低。
    strong = evaluate_blurb_appeal(
        title=_STRONG_TITLE, synopsis=_STRONG_SYN, premise=_STRONG_PREMISE,
        tags=_STRONG_TAGS, genre="都市", sub_genre="赘婿",
    )
    assert {d.key: d.score for d in strong.dimensions}["emotion_charge"] >= 3.9


@pytest.mark.unit
def test_selling_triad_recognizes_nonshuang_identity_and_conflict():
    # 题材中立：身份=「殡仪馆夜班工/唯一能…的人」(非爽文身份词)，冲突=两难/迫近/骇异结构。
    # 此前 selling_triad=2.0(只认出代价)；修复后 ≥4.0(身份+冲突+代价≥2要素)。
    syn = (
        "凌晨三点，殡仪馆第七具遗体睁开了眼。\n"
        "李拙是全市唯一能看见死人「没说完的话」的人，补全它，死者就能回魂七小时——"
        "代价是从他自己寿命里划走等长的时辰。\n"
        "他攒下三百二十个时辰，只为撬开妹妹的工单。可签收人那一栏，写着他自己的名字。\n"
        "他只剩一个选择：让妹妹活，还是留着这条命去查清，那场火里到底是谁先松的手。"
    )
    v = evaluate_blurb_appeal(
        title="我替死神签收加班", synopsis=syn, premise=syn[:40],
        tags=["都市怪谈", "悬疑", "代价流"], genre="都市",
    )
    assert {d.key: d.score for d in v.dimensions}["selling_triad"] >= 4.0
    assert v.total >= 80.0  # 顶尖 show-don't-tell 简介应达标(此前 75.9 卡在 selling_triad)


@pytest.mark.unit
def test_selling_triad_noop_safe_for_shuang_blurb():
    # 爽文强稿三要素本就齐 → 仍 5.0，结构通道不降分。
    strong = evaluate_blurb_appeal(
        title=_STRONG_TITLE, synopsis=_STRONG_SYN, premise=_STRONG_PREMISE,
        tags=_STRONG_TAGS, genre="都市", sub_genre="赘婿",
    )
    assert {d.key: d.score for d in strong.dimensions}["selling_triad"] >= 5.0


@pytest.mark.unit
def test_selling_triad_setting_only_blurb_stays_weak():
    # 回归守卫：纯设定罗列(有题材氛围、无人物处境/冲突/代价)不应被结构通道误抬。
    setting_only = (
        "这是一个灵气复苏的世界，宗门林立，天材地宝遍地。\n"
        "广袤的大陆上流传着上古的传说，无数修士追逐着长生的奥秘。"
    )
    v = evaluate_blurb_appeal(title="苍穹界", synopsis=setting_only,
                              premise=setting_only[:40], tags=["玄幻"], genre="玄幻")
    assert {d.key: d.score for d in v.dimensions}["selling_triad"] <= 2.0


@pytest.mark.unit
def test_cerebral_cold_blurb_still_below_emotion_floor():
    # 回归守卫：烧脑但不抓人的稿(有代价词、无两难/切肤/迫近结构)仍须 < 3.0，
    # 不被具身通道误抬(否则重新引入命门要堵的 bug)。
    cerebral = (
        "合同签了，违约金二十万，他连饭都快吃不起。\n"
        "纪燃蹲在出租屋地板上数最后几张钞票，前同事在朋友圈庆祝升职。\n"
        "他闭上眼，看见那份合同的灰色字迹：第七条第三款，逻辑链断在实际损失举证四个字上。\n"
        "代价是当晚右耳失聪三小时。下一次用，会剥走什么？"
    )
    v = evaluate_blurb_appeal(title="x", synopsis=cerebral, premise=cerebral[:40],
                              tags=["都市异能", "规则怪谈"], genre="都市")
    assert {d.key: d.score for d in v.dimensions}["emotion_charge"] < 3.0
