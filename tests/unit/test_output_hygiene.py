"""Regression tests for output_hygiene placeholder detection.

These tests pin down the real-world false positives that previously caused
multi-volume Chinese novel pipelines to abort: the ``_ZH_PLACEHOLDER_PATTERNS``
regex used to greedy-match ``后补`` inside the natural-prose compound
``身后补了一句``, which blocked volume export and cascaded into a broken
progress bar.  The rewritten patterns only trigger in explicit placeholder
contexts (brackets, ``name:`` labels, or role-word + capitalised short code).
"""

from __future__ import annotations

import pytest

from bestseller.services.output_hygiene import (
    collect_unfinished_artifact_issues,
    normalize_quote_format,
)


# ---------------------------------------------------------------------------
# Chinese: natural prose that must NOT match placeholder patterns.
# ---------------------------------------------------------------------------

_ZH_CLEAN_PROSE = [
    # The exact Ch4 sentence from 道种破虚 that killed Vol 1 export:
    # "后" (behind) + "补" (supplement) is a perfectly normal compound.
    "他转身离开时，杂役在身后补了一句：师兄慢走。",
    # Other innocent "X后补" / "补Y" compounds.
    "先生点头之后补充了三句话。",
    "那名配角演员终于被提拔为正式主演，不再是替补。",
    # Role words adjacent to real Chinese names (not short codes).
    "他的盟友林惊雪递来一柄古剑。",
    "反派白玉京踏空而来，衣袂翻卷。",
    # "角色" / "人物" in diegetic use.
    "这本书的角色塑造非常饱满。",
    "他扮演了一个不起眼的配角，却演得入木三分。",
    # "占位" / "待定" as ordinary words, no brackets.
    "他暂时占位等待下一轮调整。",
]


@pytest.mark.unit
@pytest.mark.parametrize("text", _ZH_CLEAN_PROSE)
def test_zh_clean_prose_is_not_flagged(text: str) -> None:
    issues = collect_unfinished_artifact_issues(text, language="zh-CN")
    assert issues == [], f"Unexpected placeholder match for natural prose: {text!r} → {issues}"


# ---------------------------------------------------------------------------
# Chinese: explicit placeholder contexts that MUST match.
# ---------------------------------------------------------------------------

_ZH_PLACEHOLDER_CASES = [
    # Role word + short code — these must flag regardless of what follows
    # (the character class already excludes real name characters).
    "他遇到了盟友甲，两人并肩作战。",
    "盟友甲提前半小时到了旧仓库。",
    "反派丙从暗处现身。",
    "配角A说了一句关键台词。",
    "敌人B1率先出手。",
    # Bracketed placeholders — the second pattern.
    "出场人物：[待定]",
    "角色身份（占位）尚未确认。",
    "人物介绍【placeholder】",
    # Labelled placeholders — the third pattern.
    "姓名：待定",
    "名字: TBD",
    "名称：占位",
]


# Cases where the role-word prefix is followed by a real Chinese name
# (not a placeholder short code).  These must NOT match.
_ZH_ROLE_WORD_REAL_NAME_CASES = [
    "他的盟友林惊雪递来一柄古剑。",
    "反派白玉京踏空而来。",
    "盟友Alex站在门口。",        # Latin real name — lookahead must reject
]


@pytest.mark.unit
@pytest.mark.parametrize("text", _ZH_PLACEHOLDER_CASES)
def test_zh_placeholder_is_flagged(text: str) -> None:
    issues = collect_unfinished_artifact_issues(text, language="zh-CN")
    assert issues, f"Placeholder should have matched but did not: {text!r}"
    assert any("占位符" in msg or "临时命名" in msg for msg in issues), issues


@pytest.mark.unit
@pytest.mark.parametrize("text", _ZH_ROLE_WORD_REAL_NAME_CASES)
def test_zh_role_word_with_real_name_is_not_flagged(text: str) -> None:
    issues = collect_unfinished_artifact_issues(text, language="zh-CN")
    assert issues == [], f"Role word + real name falsely flagged: {text!r} → {issues}"


# ---------------------------------------------------------------------------
# English: natural prose that must NOT match placeholder patterns.
# ---------------------------------------------------------------------------

_EN_CLEAN_PROSE = [
    # Natural past-tense "was called" / "used to be named"
    "He used to be named Marcus Vance before the exile.",
    # Diegetic use of the word "placeholder" inside ordinary prose.
    "Eleven prototypes. And one placeholder. She counted them twice.",
    # Short lowercase words that the old IGNORECASE pattern matched on.
    "An ally he knew would never return stood at the gate.",
    "The enemy or the traitor — he could not tell which.",
    # "to be named" as a verb phrase.
    "The city has yet to be named, but the map is ready.",
    # Role words followed by a real name (not an uppercase short code).
    "Ally Marcus nodded and drew his blade.",
    "Villain Harrington loomed in the doorway.",
]


@pytest.mark.unit
@pytest.mark.parametrize("text", _EN_CLEAN_PROSE)
def test_en_clean_prose_is_not_flagged(text: str) -> None:
    issues = collect_unfinished_artifact_issues(text, language="en-US")
    assert issues == [], f"Unexpected placeholder match for natural prose: {text!r} → {issues}"


# ---------------------------------------------------------------------------
# English: explicit placeholder contexts that MUST match.
# ---------------------------------------------------------------------------

_EN_PLACEHOLDER_CASES = [
    "Ally A joined the fight at dawn.",
    "Enemy B1 retreated into the fog.",
    "Villain C stood on the parapet.",
    "The list read [placeholder] where the lead should be.",
    "Cast: <TBD>.",
    "Name: placeholder",
    "Character: TBD",
    "Role: to be named",
]


@pytest.mark.unit
@pytest.mark.parametrize("text", _EN_PLACEHOLDER_CASES)
def test_en_placeholder_is_flagged(text: str) -> None:
    issues = collect_unfinished_artifact_issues(text, language="en-US")
    assert issues, f"Placeholder should have matched but did not: {text!r}"
    assert any("placeholder" in msg.lower() for msg in issues), issues


# ---------------------------------------------------------------------------
# Guardrails for empty / whitespace-only input.
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize("text", ["", "   ", "\n\n\t "])
def test_empty_input_returns_no_issues(text: str) -> None:
    assert collect_unfinished_artifact_issues(text, language="zh-CN") == []
    assert collect_unfinished_artifact_issues(text, language="en-US") == []


# ---------------------------------------------------------------------------
# Contamination heuristic for Chinese drafts.
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_zh_contamination_heuristic_fires_on_heavy_english_scaffolding() -> None:
    # Build a body with >= 120 CJK chars, >= 35 Latin words, and >= 2
    # contamination markers — the three-way threshold for the ZH branch.
    cjk_block = "她缓缓抬起头，看着远处的山峦，心中涌起一股莫名的情绪。" * 6
    english_noise = (
        "chapter 1 scene 2 choice_text option a prompt: do it again. "
        "reader choice prompt: walk away. walkthrough step one. "
        + " ".join(f"word{i}" for i in range(40))
    )
    text = cjk_block + "\n" + english_noise
    issues = collect_unfinished_artifact_issues(text, language="zh-CN")
    assert any("污染" in msg or "翻译残留" in msg for msg in issues), issues


@pytest.mark.unit
def test_en_cjk_leakage_is_flagged() -> None:
    # English draft with ≥60 CJK characters should raise a leakage issue.
    cjk = "这段中文不该出现在英文正文里。" * 10
    text = f"The caravan rolled south. {cjk} Then they stopped."
    issues = collect_unfinished_artifact_issues(text, language="en-US")
    assert any("non-English" in msg or "leakage" in msg.lower() for msg in issues), issues


# ---------------------------------------------------------------------------
# Quote normalisation smoke tests — cheap guards against regressions in the
# companion helper shipped alongside the placeholder detector.
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_normalize_quote_format_english_converts_to_curly() -> None:
    result = normalize_quote_format('He said "hello" to her.', language="en-US")
    assert "\u201c" in result and "\u201d" in result


@pytest.mark.unit
def test_normalize_quote_format_chinese_default_curly() -> None:
    result = normalize_quote_format("他说「你好」然后离开。", language="zh-CN")
    assert "\u201c" in result and "\u201d" in result
    assert "「" not in result and "」" not in result


@pytest.mark.unit
def test_normalize_quote_format_chinese_corner_style() -> None:
    result = normalize_quote_format(
        "他说\u201c你好\u201d然后离开。", language="zh-CN", target_style="corner"
    )
    assert "「" in result and "」" in result
