"""W0 SSOT: chapter word band must match across config + gate constants."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from bestseller.services.chapter_length_gate import (
    DEFAULT_HARD_FLOOR_ZH_CHARS,
    DEFAULT_HARD_MAX_ZH_CHARS,
    DEFAULT_SOFT_WARNING_ZH_CHARS,
)
from bestseller.services.length_stability_gate import (
    CHINESE_CHAPTER_HARD_MAX_WORDS,
    CHINESE_CHAPTER_HARD_MIN_WORDS,
)
from bestseller.settings import load_settings

pytestmark = pytest.mark.unit

_REPO = Path(__file__).resolve().parents[2]


def test_default_yaml_words_per_chapter_matches_gate_constants() -> None:
    raw = yaml.safe_load((_REPO / "config" / "default.yaml").read_text(encoding="utf-8"))
    band = raw["generation"]["words_per_chapter"]
    assert band["min"] == DEFAULT_HARD_FLOOR_ZH_CHARS == 1800
    assert band["target"] == DEFAULT_SOFT_WARNING_ZH_CHARS == 2600
    assert band["max"] == DEFAULT_HARD_MAX_ZH_CHARS == 3500


def test_length_stability_hard_band_matches_chapter_length_gate() -> None:
    assert CHINESE_CHAPTER_HARD_MIN_WORDS == DEFAULT_HARD_FLOOR_ZH_CHARS
    assert CHINESE_CHAPTER_HARD_MAX_WORDS == DEFAULT_HARD_MAX_ZH_CHARS


def test_settings_words_per_chapter_matches_ssot() -> None:
    settings = load_settings(env={})
    w = settings.generation.words_per_chapter
    assert w.min == 1800
    assert w.target == 2600
    assert w.max == 3500


def test_writer_prompt_budget_tokens_default() -> None:
    settings = load_settings(env={})
    assert int(settings.generation.writer_prompt_budget_tokens) == 8000
    assert settings.generation.writer_prompt_ab_winner == "lean"
