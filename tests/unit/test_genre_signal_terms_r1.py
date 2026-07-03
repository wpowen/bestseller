"""R1 — deterministic gates de-hardcoded from one detective book's vocabulary.

Locks in: the genre-neutral signal-term source, and the common_sense_gate fixes that
removed the hardcoded detective cast names and broadened the object-signal check
beyond 铜钱/发烫.
"""

from __future__ import annotations

# ruff: noqa: RUF001, RUF002

from bestseller.services.common_sense_gate import evaluate_common_sense_gate
from bestseller.services.genre_signal_terms import resolve_genre_signal_terms


def test_signal_terms_derive_rule_terms_from_bible():
    terms = resolve_genre_signal_terms(
        genre="玄幻", sub_genre="升级流",
        story_bible={"power_system": {"terms": ["真元", "噬灵诀"]}},
    )
    assert "真元" in terms.rule_terms
    assert "噬灵诀" in terms.rule_terms


def test_signal_terms_no_bible_returns_profile_only():
    terms = resolve_genre_signal_terms(genre="都市言情")
    assert terms.rule_terms == ()  # no bible → no book-specific terms
    assert terms.category_key  # genre still resolved


def test_lay_rule_leak_detects_non_hardcoded_cast():
    # A detective book whose speaker is NOT one of the old hardcoded names still trips
    # the lay-character rule-knowledge-leak check (previously only fired for that book).
    txt = (
        "行里管这个叫「压契」。压契要按手印，压契过的人躲不掉，谁沾了谁压契。\n"
        "李明压低声音：“压契的规矩你不懂，下一个就该轮到我。”"
    )
    report = evaluate_common_sense_gate(txt, genre="悬疑探案", chapter_number=1)
    assert "lay_character_rule_knowledge_leak" in {f.code for f in report.findings}


def test_object_signal_overuse_broadened_sensory():
    # Broadened beyond 发烫: 发凉 ×3 on objects is the same single-sensory-tic anti-pattern.
    txt = "铜钱发凉。罗盘发凉。镜片发凉。"
    report = evaluate_common_sense_gate(txt, genre="悬疑探案", chapter_number=2)
    assert "object_signal_overuse" in {f.code for f in report.findings}


def test_object_signal_overuse_regression_original_still_caught():
    txt = "铜钱发烫。罗盘发烫。镜片发烫。"
    report = evaluate_common_sense_gate(txt, genre="悬疑探案", chapter_number=2)
    assert "object_signal_overuse" in {f.code for f in report.findings}
