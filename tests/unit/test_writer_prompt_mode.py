from __future__ import annotations

import pytest

from bestseller.services.drafts import _score_writer_candidate, _writer_prompt_mode_for_chapter
from bestseller.settings import load_settings

pytestmark = pytest.mark.unit


def test_writer_prompt_mode_ab_applies_to_first_three_then_full() -> None:
    settings = load_settings(env={})
    settings.generation.writer_prompt_mode = "ab"
    settings.generation.writer_prompt_ab_until_chapter = 3

    assert _writer_prompt_mode_for_chapter(settings, 1) == "ab"
    assert _writer_prompt_mode_for_chapter(settings, 3) == "ab"
    assert _writer_prompt_mode_for_chapter(settings, 4) == "full"


def test_writer_candidate_score_blocks_chinese_ta_pollution() -> None:
    clean = _score_writer_candidate("陆岑抬头，他看见规则变了。", target_word_count=20, language="zh-CN")
    polluted = _score_writer_candidate("陆岑抬头，ta看见规则变了。", target_word_count=20, language="zh-CN")

    assert polluted < clean - 50
