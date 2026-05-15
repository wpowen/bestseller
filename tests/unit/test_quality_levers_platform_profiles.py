"""Unit tests for ``quality_levers.platform_profiles``."""

from __future__ import annotations

import pytest

from bestseller.services.quality_levers.platform_profiles import (
    load_platform_profiles,
    parse_rejection_reason,
    render_platform_profile_block,
    resolve_platform_id,
)


pytestmark = pytest.mark.unit


def test_load_platform_profiles_returns_qimao() -> None:
    config = load_platform_profiles()

    assert "qimao" in config.platforms
    qimao = config.platforms["qimao"]
    assert qimao.display_name == "七猫中文网"
    assert qimao.pacing_preference.chapter_word_min == 2500
    assert qimao.pacing_preference.chapter_word_max == 4000
    assert qimao.opening_signing_gate.sample_words == 10000
    assert qimao.opening_signing_gate.hard_position_gates
    first_gate = qimao.opening_signing_gate.hard_position_gates[0]
    assert first_gate.position_words == 100
    assert "主角" in first_gate.rule


def test_load_platform_profiles_loads_pulse_words() -> None:
    config = load_platform_profiles()

    assert "心一沉" in config.pulse_words.body_signal
    assert "立刻" in config.pulse_words.internal_pulse
    assert config.pulse_words.all_words  # non-empty union


def test_load_platform_profiles_loads_opening_hook_bank() -> None:
    config = load_platform_profiles()

    ids = {hook.hook_id for hook in config.opening_hooks}
    assert {"countdown_threat", "humiliation_then_strike", "corpse_speaks"} <= ids
    strong_hooks = [hook for hook in config.opening_hooks if hook.strength >= 8]
    assert strong_hooks


def test_resolve_platform_id_accepts_chinese_and_synonyms() -> None:
    assert resolve_platform_id("七猫小说") == "qimao"
    assert resolve_platform_id("Qimao") == "qimao"
    assert resolve_platform_id("起点中文网") == "qidian"
    assert resolve_platform_id("番茄小说") == "tomato"
    assert resolve_platform_id("Tomato") == "tomato"
    assert resolve_platform_id("fanqie") == "tomato"
    assert resolve_platform_id(None) is None
    assert resolve_platform_id("  ") is None


def test_parse_rejection_reason_maps_to_internal_cause() -> None:
    # Exact match
    assert (
        parse_rejection_reason(
            platform="七猫", reason_text="开篇切入点比较普通"
        )
        == "ordinary_entry"
    )
    # Substring match (the platform sometimes appends "建议..." after the reason)
    assert (
        parse_rejection_reason(
            platform="七猫",
            reason_text="本作开篇切入点比较普通,建议调整",
        )
        == "ordinary_entry"
    )
    assert (
        parse_rejection_reason(platform="qimao", reason_text="缺乏足够吸引力")
        == "weak_attraction"
    )


def test_parse_rejection_reason_returns_none_for_unknown() -> None:
    assert parse_rejection_reason(platform=None, reason_text="anything") is None
    assert (
        parse_rejection_reason(platform="qimao", reason_text="this is unknown")
        is None
    )


def test_render_platform_profile_block_chapter_one_includes_signing_gate() -> None:
    block = render_platform_profile_block(
        platform="七猫", chapter_number=1, language="zh-CN"
    )

    assert "七猫中文网" in block
    assert "第一章签约门槛" in block
    assert "前100字" in block
    assert "前三章" in block


def test_render_platform_profile_block_later_chapter_omits_first_chapter_block() -> None:
    block = render_platform_profile_block(
        platform="qimao", chapter_number=12, language="zh-CN"
    )

    assert "七猫中文网" in block
    assert "第一章签约门槛" not in block
    assert "前三章" not in block
    # 节奏 still rendered for any chapter
    assert "节奏" in block


def test_render_platform_profile_block_skips_english() -> None:
    block = render_platform_profile_block(
        platform="七猫", chapter_number=1, language="en"
    )
    assert block == ""


def test_render_platform_profile_block_skips_unknown_platform() -> None:
    block = render_platform_profile_block(
        platform="unknown_site", chapter_number=1, language="zh-CN"
    )
    assert block == ""
