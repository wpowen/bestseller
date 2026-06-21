"""L1 unit tests for the deterministic blurb click-power gate.

Guards: strong > weak discrimination, the per-dimension detectors (selling
triad, AI-template, spoiler ending, length envelope, genre signal), graceful
handling of empty input, and the grade ladder.
"""

from __future__ import annotations

import pytest

from bestseller.services.blurb_appeal_gate import evaluate_blurb_appeal

# ruff: noqa: RUF001

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
