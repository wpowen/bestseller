from __future__ import annotations

import pytest

from bestseller.services.length_stability_gate import CHINESE_CHAPTER_HARD_MIN_WORDS
from bestseller.services.word_targets import chapter_rewrite_length_band
from bestseller.settings import load_settings


pytestmark = pytest.mark.unit


def test_chinese_chapter_rewrite_band_uses_commercial_hard_floor() -> None:
    settings = load_settings(env={})

    band = chapter_rewrite_length_band(
        settings,
        1800,
        language="zh-CN",
        direction="under",
        role="editor",
    )

    assert band.hard_min == CHINESE_CHAPTER_HARD_MIN_WORDS
    assert band.hard_target == CHINESE_CHAPTER_HARD_MIN_WORDS
    assert band.safe_min >= CHINESE_CHAPTER_HARD_MIN_WORDS + 200
    assert band.safe_max > CHINESE_CHAPTER_HARD_MIN_WORDS


def test_english_chapter_rewrite_band_keeps_configured_floor() -> None:
    settings = load_settings(env={})

    band = chapter_rewrite_length_band(
        settings,
        2200,
        language="en",
        direction="under",
        role="editor",
    )

    assert band.hard_min == int(settings.generation.words_per_chapter.min)
