from __future__ import annotations

import pytest

from bestseller.services.drafts import _score_writer_candidate, _writer_prompt_mode_for_chapter
from bestseller.settings import load_settings

pytestmark = pytest.mark.unit


def test_writer_prompt_mode_ab_applies_to_first_three_then_lean() -> None:
    settings = load_settings(env={})
    settings.generation.writer_prompt_mode = "ab"
    settings.generation.writer_prompt_ab_until_chapter = 3
    settings.generation.writer_prompt_ab_winner = "lean"

    assert _writer_prompt_mode_for_chapter(settings, 1) == "ab"
    assert _writer_prompt_mode_for_chapter(settings, 3) == "ab"
    assert _writer_prompt_mode_for_chapter(settings, 4) == "lean"


def test_writer_prompt_mode_ab_empty_winner_defaults_to_lean() -> None:
    settings = load_settings(env={})
    settings.generation.writer_prompt_mode = "ab"
    settings.generation.writer_prompt_ab_until_chapter = 3
    settings.generation.writer_prompt_ab_winner = ""

    assert _writer_prompt_mode_for_chapter(settings, 10) == "lean"


def test_writer_prompt_mode_default_is_lean() -> None:
    settings = load_settings(env={})
    assert settings.generation.writer_prompt_mode in {"lean", "ab", "full"}
    # Production default after 2026-07 remediation is lean (not ab/full).
    assert settings.generation.writer_prompt_mode == "lean"
    assert _writer_prompt_mode_for_chapter(settings, 1) == "lean"
    assert _writer_prompt_mode_for_chapter(settings, 99) == "lean"


def test_writer_prompt_mode_explicit_full_is_stable_across_chapter_ranges() -> None:
    settings = load_settings(env={})
    settings.generation.writer_prompt_mode = "full"

    assert _writer_prompt_mode_for_chapter(settings, 1) == "full"
    assert _writer_prompt_mode_for_chapter(settings, 3) == "full"
    assert _writer_prompt_mode_for_chapter(settings, 4) == "full"
    assert _writer_prompt_mode_for_chapter(settings, 11) == "full"


def test_writer_prompt_mode_explicit_compiled_is_stable_across_chapter_ranges() -> None:
    settings = load_settings(env={})
    settings.generation.writer_prompt_mode = "compiled"

    assert _writer_prompt_mode_for_chapter(settings, 1) == "compiled"
    assert _writer_prompt_mode_for_chapter(settings, 3) == "compiled"
    assert _writer_prompt_mode_for_chapter(settings, 11) == "compiled"


def test_writer_prompt_mode_unknown_value_fails_closed_to_lean() -> None:
    settings = load_settings(env={})
    settings.generation.writer_prompt_mode = "legacy"

    assert _writer_prompt_mode_for_chapter(settings, 1) == "lean"
    assert _writer_prompt_mode_for_chapter(settings, 99) == "lean"


def test_writer_candidate_score_blocks_chinese_ta_pollution() -> None:
    clean = _score_writer_candidate("陆岑抬头，他看见规则变了。", target_word_count=20, language="zh-CN")
    polluted = _score_writer_candidate("陆岑抬头，ta看见规则变了。", target_word_count=20, language="zh-CN")

    assert polluted < clean - 50


def test_writer_candidate_score_penalizes_severe_overlength() -> None:
    target = 100
    near = _score_writer_candidate("字" * 100, target_word_count=target, language="zh-CN")
    bloated = _score_writer_candidate("字" * 200, target_word_count=target, language="zh-CN")
    assert bloated < near
