"""Unit tests for the book-level imagery_system lever (pure, no LLM)."""

# ruff: noqa: RUF001

from __future__ import annotations

from bestseller.services.quality_levers.imagery_system import (
    build_imagery_designer_prompt,
    extract_imagery_system,
    load_imagery_system_config,
    parse_imagery_artifact,
    render_imagery_system_block,
)

CONFIG = load_imagery_system_config()

_PAYLOAD = {
    "theme_core": "借出去的运，记得清却偿不还",
    "images": [
        {"name": "黑纹账本", "carrier": "掌心顺腕骨蹿的黑线", "emotion_fn": "迟来的羞愧", "theme_fn": "代价可记账却偿不清"},
        {"name": "旧手机", "carrier": "裂痕里卡住的未发消息", "emotion_fn": "亏欠", "theme_fn": "想说的话永远迟一步"},
        {"name": "电弧", "carrier": "二号杆塔的螺栓", "emotion_fn": "灼痛", "theme_fn": "力量与代价同源"},
        {"name": "多余的", "carrier": "第四个意象应被截断", "emotion_fn": "x", "theme_fn": "y"},
    ],
}


def test_config_loads():
    assert CONFIG.designer_system
    assert "古风" in CONFIG.strong_genres
    assert "都市" in CONFIG.careful_genres


def test_parse_caps_to_three_images():
    art = parse_imagery_artifact(_PAYLOAD)
    assert len(art.images) == 3  # 4th dropped
    assert art.theme_core.startswith("借出去")
    assert art.images[0].name == "黑纹账本"
    assert art.images[0].carrier == "掌心顺腕骨蹿的黑线"


def test_extract_from_bible():
    bible = {"imagery_system": _PAYLOAD, "other": 1}
    art = extract_imagery_system(bible)
    assert len(art.images) == 3
    # Missing key → empty (falsy) artifact, render no-ops.
    assert not extract_imagery_system({"no_imagery": True})


def test_render_empty_artifact_is_blank():
    assert render_imagery_system_block(artifact=None) == ""
    empty = parse_imagery_artifact({"images": []})
    assert render_imagery_system_block(artifact=empty) == ""


def test_render_lists_images_and_recall_instruction():
    art = parse_imagery_artifact(_PAYLOAD)
    block = render_imagery_system_block(artifact=art, genre_terms=("悬疑",), chapter_number=1)
    assert "黑纹账本" in block
    assert "掌心顺腕骨蹿的黑线" in block
    assert "进一层" in block  # the "advance meaning" instruction
    assert "本章可优先回返" in block  # rotation spotlight
    # strong (non-careful) genre → the generic 成像 guard.
    assert "成像" in block


def test_render_modern_genre_warns_this_world_objects():
    art = parse_imagery_artifact(_PAYLOAD)
    block = render_imagery_system_block(artifact=art, genre_terms=("都市异能",), chapter_number=2)
    assert "本世界的物" in block
    assert "古风意象" in block


def test_designer_prompt_formats_premise_and_genre():
    system, user = build_imagery_designer_prompt(
        premise="一个电工借运成神，借出去的运要记账偿还。", genre="都市异能"
    )
    assert "意象系统设计师" in system
    assert "JSON" in system
    assert "都市异能" in user
    assert "借运成神" in user
