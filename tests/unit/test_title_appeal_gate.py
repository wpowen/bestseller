"""L1 unit tests for the deterministic title click-power gate.

Anchored on the product-flagged real failure 《规则漏洞不保护我》 (an illogical,
limp book name that the old pipeline never gated at all) and on real bestseller
titles that must keep passing — so the gate separates click-worthy from not.
"""

# ruff: noqa: RUF001, RUF002 — Chinese punctuation/titles are the test subject.
from __future__ import annotations

from bestseller.domain.appeal import BlurbAppealVerdict, PremiseAppealVerdict
from bestseller.services.story_appeal import load_story_appeal_config, meets_bar
from bestseller.services.title_appeal_gate import evaluate_title_appeal

_CFG = load_story_appeal_config()


def _score(title: str) -> float:
    return evaluate_title_appeal(title, genre="urban", config=_CFG).total


# --- the product-flagged real failure must fail -----------------------------


def test_real_flagged_title_fails_bar():
    """《规则漏洞不保护我》 — user judged illogical + unclickable; must be < 80."""
    v = evaluate_title_appeal("规则漏洞不保护我", genre="urban", config=_CFG)
    assert v.total < 80, f"flagged bad title should fail, got {v.total}"
    coh = next(d for d in v.dimensions if d.key == "coherence")
    assert coh.score <= 2.5, "coherence 命门 should fire on the malformed claim"


def test_malformed_claim_abstraction_protects_person():
    """Abstraction as agent of a negated care-verb → coherence floor."""
    assert _score("数据不爱你") < 80
    assert _score("算法没救我") < 80


def test_positive_jargon_agent_not_false_failed():
    """系统逼我当皇帝 — jargon-as-agent but POSITIVE verb is a GOOD pattern; keep it."""
    assert _score("系统逼我当皇帝") >= 80


# --- real bestseller / strong titles must pass ------------------------------


def test_strong_titles_pass_bar():
    for title in (
        "诡秘之主",
        "全球高武",
        "我在废土捡垃圾成神",
        "神仙都是我招的",
        "系统逼我当皇帝",
    ):
        assert _score(title) >= 80, f"strong title should pass: {title}={_score(title)}"


def test_colon_subtitle_not_penalized():
    """Subtitle colon «规则怪谈：…» is a legit format, not a function-word leak."""
    assert _score("规则怪谈：我能看见死亡") >= 80


# --- defect detectors -------------------------------------------------------


def test_function_word_leak_penalized():
    """Chapter/template artifacts (· 【】 () ) in a book title are a defect."""
    assert _score("取证·义庄铜镜登记") < 80
    assert _score("狂飙·赘婿的逆袭") < 80


def test_cliche_stem_penalized():
    bad = evaluate_title_appeal("最强系统之绝世神医", genre="urban", config=_CFG)
    anti = next(d for d in bad.dimensions if d.key == "anti_generic")
    assert anti.score < 4.0, "red-ocean cliché stems should drag anti_generic"


def test_english_title_neutral_passthrough():
    """Non-CJK title: zh heuristics don't apply; must not be false-failed."""
    v = evaluate_title_appeal("The Quant Who Broke Wall Street", genre="urban", config=_CFG)
    assert v.language == "en"
    assert v.total >= 80


def test_empty_title_does_not_crash():
    v = evaluate_title_appeal("", genre="urban", config=_CFG)
    assert 0 <= v.total <= 100


# --- meets_bar AND-logic with the title gate --------------------------------


def _strong_blurb() -> BlurbAppealVerdict:
    return BlurbAppealVerdict(total=88.0, grade="recommend")


def _adv_premise() -> PremiseAppealVerdict:
    return PremiseAppealVerdict(total=0.0, grade="pass", gated_grade="pass")


def test_meets_bar_rejects_when_title_weak_even_if_blurb_strong():
    bad_title = evaluate_title_appeal("规则漏洞不保护我", genre="urban", config=_CFG)
    assert meets_bar(_adv_premise(), _strong_blurb(), _CFG, title=bad_title) is False


def test_meets_bar_passes_when_both_strong():
    good_title = evaluate_title_appeal("神仙都是我招的", genre="urban", config=_CFG)
    assert meets_bar(_adv_premise(), _strong_blurb(), _CFG, title=good_title) is True


def test_meets_bar_backcompat_when_title_none():
    """No title verdict (gate disabled / old call site) → title is ignored."""
    assert meets_bar(_adv_premise(), _strong_blurb(), _CFG, title=None) is True
