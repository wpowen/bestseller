"""L1 unit tests for the story appeal orchestrator + config single-source.

Covers config integrity (weights sum 100), genre-lexicon resolution with
generic fallback (every genre covered), grading, meets-bar, gating, feedback
token-cap, and the no-op contract (disabled → ConceptionResult byte-identical).
"""

from __future__ import annotations

import json

import pytest

from bestseller.domain.appeal import (
    AppealDimension,
    BlurbAppealVerdict,
    PremiseAppealVerdict,
    StoryAppealReport,
    grade_rank,
    min_grade,
)
from bestseller.services.story_appeal import (
    build_improvement_feedback,
    genre_emotion_exemplars,
    grade_from_total,
    is_appeal_enabled,
    load_story_appeal_config,
    meets_bar,
    resolve_genre_lexicon,
)

# ruff: noqa: RUF001, RUF003, N806 — Chinese test fixtures, local DIM constant.


@pytest.mark.unit
def test_config_loads_and_weights_sum_to_100():
    cfg = load_story_appeal_config()
    assert cfg.get("enabled") is True
    pr = cfg["premise_rubric"]
    br = cfg["blurb_rubric"]
    assert len(pr) == 9
    assert len(br) == 11  # 10 原维 + 新增"新读者可懂度"(comprehensibility)
    assert sum(d["weight_long"] for d in pr.values()) == 100
    assert sum(d["weight_short"] for d in pr.values()) == 100
    assert sum(d["weight"] for d in br.values()) == 100


@pytest.mark.unit
def test_genre_lexicon_generic_fallback_for_unknown_genre():
    lex = resolve_genre_lexicon("不存在的题材zzz", None)
    # generic keys always present
    assert "high_arousal_emotion" in lex
    assert "template_blacklist" in lex


@pytest.mark.unit
def test_genre_lexicon_specific_override_for_known_genre():
    urban = resolve_genre_lexicon("都市", "赘婿")
    assert "red_ocean_tropes" in urban
    assert any("赘婿" in t for t in urban["red_ocean_tropes"])
    # generic keys still merged in
    assert "high_arousal_emotion" in urban


@pytest.mark.unit
@pytest.mark.parametrize(
    "genre",
    ["玄幻", "都市", "仙侠", "科幻", "末世", "纯爱", "悬疑", "女频", "历史", "游戏", "无限流"],
)
def test_every_genre_resolves_to_a_lexicon(genre):
    # "每题材达标" precondition: every genre resolves (own or generic) with the
    # full generic signal banks present.
    lex = resolve_genre_lexicon(genre, None)
    for key in ("high_arousal_emotion", "identity_markers", "cost_markers"):
        assert lex.get(key)


@pytest.mark.unit
def test_meets_story_bar_winrate_threshold():
    from bestseller.services.story_appeal import meets_story_bar

    cfg = load_story_appeal_config()
    bar = float(cfg["arena"]["story_winrate_min"])
    assert meets_story_bar(bar + 0.05, cfg) is True
    assert meets_story_bar(bar, cfg) is True
    assert meets_story_bar(bar - 0.05, cfg) is False


@pytest.mark.unit
def test_grade_from_total_ladder():
    assert grade_from_total(85) == "recommend"
    assert grade_from_total(70) == "consider"
    assert grade_from_total(50) == "pass"


@pytest.mark.unit
def test_meets_bar_is_blurb_anchored_premise_advisory():
    # Competitor-anchored design: 达标 gates on the reproducible deterministic
    # blurb gate (blurb_min); the LLM premise score is ADVISORY (premise_min=0).
    # 达标线 blurb_min=68：贴合真实榜单爆款简介分布(对标 floor=68)；黑话垃圾被可懂度封顶60仍不达标。
    cfg = load_story_appeal_config()
    assert float(cfg["meets_bar"]["blurb_min"]) == 68  # 数据校准的达标线(原80脱离现实已修)

    excellent_blurb = BlurbAppealVerdict(total=84, grade="recommend")
    mediocre_blurb = BlurbAppealVerdict(total=60, grade="pass")  # 黑话/弱稿被可懂度封顶到60级,不达标

    # 榜单级(≥68)简介达标，premise(advisory)不影响
    assert meets_bar(PremiseAppealVerdict(total=82, grade="recommend", gated_grade="recommend"),
                     excellent_blurb, cfg) is True
    assert meets_bar(PremiseAppealVerdict(total=40, grade="pass", gated_grade="pass"),
                     excellent_blurb, cfg) is True  # low LLM premise must NOT block (advisory)

    # 60 分的弱稿/黑话简介不达标，即使 premise 判官给高分
    assert meets_bar(PremiseAppealVerdict(total=90, grade="recommend", gated_grade="recommend"),
                     mediocre_blurb, cfg) is False


@pytest.mark.unit
def test_meets_bar_premise_min_enforced_when_configured():
    # If an operator opts premise back into the hard gate, it is honoured.
    cfg = {"meets_bar": {"blurb_min": 65, "premise_min": 75, "forbid_gated_to_pass": False}}
    good_blurb = BlurbAppealVerdict(total=78, grade="consider")
    assert meets_bar(PremiseAppealVerdict(total=80, grade="recommend", gated_grade="recommend"),
                     good_blurb, cfg) is True
    assert meets_bar(PremiseAppealVerdict(total=60, grade="pass", gated_grade="pass"),
                     good_blurb, cfg) is False


@pytest.mark.unit
def test_grade_helpers_ordering():
    assert grade_rank("recommend") > grade_rank("consider") > grade_rank("pass")
    assert min_grade("recommend", "pass") == "pass"
    assert min_grade("consider", "recommend") == "consider"


@pytest.mark.unit
def test_build_improvement_feedback_is_token_capped():
    cfg = load_story_appeal_config()
    from bestseller.domain.appeal import AppealDimension, StoryAppealReport

    dims = tuple(
        AppealDimension(key=f"d{i}", label=f"维度{i}", score=1.0, weight=10,
                        rationale="太弱了" * 50)
        for i in range(9)
    )
    premise = PremiseAppealVerdict(
        total=40, grade="pass", gated_grade="pass", dimensions=dims,
        suggestions=tuple("改进建议" * 30 for _ in range(5)),
        gating_caps=("概念强度/卖点",),
    )
    blurb = BlurbAppealVerdict(total=40, grade="pass",
                               suggestions=tuple("简介建议" * 30 for _ in range(5)))
    report = StoryAppealReport(
        genre="都市", sub_genre="赘婿", premise=premise, blurb=blurb,
        meets_bar=False, overall_grade="pass",
    )
    fb = build_improvement_feedback(report, cfg)
    budget = cfg["regeneration"]["feedback_token_budget"]
    assert len(fb) <= budget * 2
    # 反馈必须明示达标硬线 + 当前差距 + 具体重写要求(引导改到 80)
    assert "达标硬线" in fb
    assert "还差" in fb
    assert "首句" in fb  # 重写要求里的关键修法


@pytest.mark.unit
def test_no_op_contract_disabled_config_skips_evaluation():
    # When disabled, the conception wiring guards on is_appeal_enabled → the
    # block is skipped and ConceptionResult.story_appeal stays {} (byte-identical
    # to history).
    assert is_appeal_enabled({"enabled": False}) is False
    assert is_appeal_enabled({}) is False


@pytest.mark.unit
def test_conception_result_story_appeal_defaults_empty():
    from bestseller.services.conception import ConceptionResult

    r = ConceptionResult(
        writing_profile={}, premise="p", title="t",
        conception_log=[], llm_run_ids=[],
    )
    assert r.story_appeal == {}  # additive field defaults empty → no-op


@pytest.mark.unit
async def test_evaluate_story_appeal_combines_both_evaluators(monkeypatch):
    from bestseller.services import story_appeal as sa

    _DIM_KEYS = (
        "concept_strength", "novelty", "conflict_stakes", "emotional_value",
        "hook_suspense", "immersion", "sustainability", "audience_fit", "structure_pace",
    )

    class _FakeCompletion:
        content = json.dumps({
            "dimension_scores": dict.fromkeys(_DIM_KEYS, 4.6),
            "rationale": {}, "suggestions": [], "overall_comment": "ok",
        })
        llm_run_id = "run-x"

    async def fake_complete_text(session, settings, request):
        return _FakeCompletion()

    import bestseller.services.llm as llm_mod
    monkeypatch.setattr(llm_mod, "complete_text", fake_complete_text)

    report = await sa.evaluate_story_appeal(
        None, None,
        premise="被退婚的废物赘婿，其实是隐藏的商业帝国之主，三天对赌局里步步打脸翻盘。",
        synopsis="退婚宴上岳父羞辱他三天内拿不出一个亿就滚，没人知道他是隐藏的首富，这一次他要让所有人跪着求他。",
        title="我老婆是首富", tags=["赘婿", "打脸", "都市"],
        genre="都市", sub_genre="赘婿", chapter_count=500,
    )
    assert report.premise.total > 85
    assert report.blurb.total > 0
    assert report.overall_grade in {"pass", "consider", "recommend"}
    assert report.canonical_genre  # canonicalized
    d = report.to_dict()
    assert "premise" in d and "blurb" in d and d["meets_bar"] in {True, False}


# --- genre-aware emotional charge (玄幻 blurbs were blocked for urban-biased guidance) ---


def test_genre_emotion_exemplars_routes_per_genre():
    cfg = load_story_appeal_config()
    xh = genre_emotion_exemplars("玄幻", None, cfg)
    urban = genre_emotion_exemplars("都市", None, cfg)
    assert any("灭门" in t or "夺宝" in t for t in xh), xh
    assert "退婚" not in "".join(xh), "玄幻 must NOT get urban 退婚 guidance"
    assert any("退婚" in t for t in urban), urban
    assert xh != urban


def test_genre_emotion_exemplars_generic_fallback():
    cfg = load_story_appeal_config()
    unknown = genre_emotion_exemplars("不存在的题材xyz", None, cfg)
    assert unknown == genre_emotion_exemplars(None, None, cfg)
    assert len(unknown) >= 4  # never empty → guidance never degrades to nothing


def test_feedback_emotion_hint_points_at_this_books_own_material():
    """2026-08-01: the per-genre event menu left the repair feedback.

    A shared list of 玄幻 events (灭门/夺宝/绝境突破) steered every same-genre
    book toward the same beats. The hint now asks for the strongest event in
    THIS book's premise and conflict, and still must not cross-pollinate the
    urban 退婚/重生 vocabulary.
    """
    cfg = load_story_appeal_config()
    dims = (
        AppealDimension(key="emotion_charge", label="情绪强度", score=1.5, weight=8,
                        rationale="高唤起情绪0处"),
    )
    blurb = BlurbAppealVerdict(total=73.6, grade="consider", dimensions=dims)
    prem = PremiseAppealVerdict(total=0, grade="pass", gated_grade="pass")
    rep = StoryAppealReport(
        genre="玄幻", sub_genre="高武", premise=prem, blurb=blurb,
        meets_bar=False, overall_grade="consider", canonical_genre="xuanhuan",
    )
    fb = build_improvement_feedback(rep, cfg)
    assert "退婚" not in fb, "玄幻 feedback must not cross-pollinate urban 退婚"
    assert "本书自己最强的高唤起情绪事件" in fb, fb
    for framework_menu_item in ("灭门", "夺宝", "绝境突破"):
        assert framework_menu_item not in fb, fb


def test_genre_emotion_primary_dominates_drifted_subgenre():
    """A drifted/sub-flavor sub_genre must NOT flip the emotion palette.

    Regression: story_architect injected 悬疑 into a 仙侠 book's facets →
    _canonical_genre('仙侠升级','悬疑')='suspense' → 仙侠 blurb fed 命案/灭口追杀
    emotion words → couldn't hit the 仙侠 emotion bar → blocked at conception (no
    book). Primary genre must dominate.
    """
    cfg = load_story_appeal_config()
    xianxia = genre_emotion_exemplars("仙侠升级", "宗门逆袭", cfg)
    drifted = genre_emotion_exemplars("仙侠升级", "诡秘悬疑", cfg)
    assert drifted == xianxia, "悬疑 sub must not flip 仙侠 onto the suspense list"
    assert any("宗" in t or "道" in t or "仙" in t for t in drifted), drifted
    # a GENUINE suspense primary still resolves to suspense
    susp = genre_emotion_exemplars("悬疑推理", "灭口", cfg)
    assert any("命案" in t or "灭口" in t or "追杀" in t for t in susp), susp
