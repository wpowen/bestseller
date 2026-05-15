"""Unit tests for ``quality_levers.prose_style_anchors``."""

from __future__ import annotations

import pytest

from bestseller.services.quality_levers.prose_style_anchors import (
    get_anti_ai_banned_patterns,
    get_style_anchor,
    load_prose_style_anchors,
    render_style_anchor_block,
)


pytestmark = pytest.mark.unit


def test_load_prose_style_anchors_returns_expected_anchors() -> None:
    config = load_prose_style_anchors()
    ids = set(config.anchors.keys())
    assert {
        "lu_xun_cold",
        "yan_leisheng",
        "hemingway_short",
        "anti_ai_voice",
    } <= ids


def test_get_anti_ai_banned_patterns_includes_known_patterns() -> None:
    patterns = get_anti_ai_banned_patterns()
    ids = {bp.pattern_id for bp in patterns}
    assert {
        "parallel_action",
        "not_only_but_also",
        "looks_like_actually",
        "smooth_transition",
        "emotion_label",
    } <= ids


def test_lu_xun_cold_has_sentence_features() -> None:
    anchor = get_style_anchor("lu_xun_cold")
    assert anchor is not None
    assert anchor.sentence_features  # non-empty


def test_render_style_anchor_block_appends_anti_ai_baseline() -> None:
    block = render_style_anchor_block(
        anchor_ids=("lu_xun_cold", "yan_leisheng"),
    )
    assert "lu_xun_cold" in block
    assert "yan_leisheng" in block
    # baseline always appended
    assert "anti_ai_voice" in block
    assert "禁用模式" in block


def test_render_style_anchor_block_with_only_baseline() -> None:
    block = render_style_anchor_block(anchor_ids=())
    assert "anti_ai_voice" in block


def test_render_style_anchor_block_ignores_unknown_anchor() -> None:
    block = render_style_anchor_block(anchor_ids=("totally_unknown",))
    # Unknown ids are silently dropped but baseline still appended
    assert "totally_unknown" not in block
    assert "anti_ai_voice" in block
