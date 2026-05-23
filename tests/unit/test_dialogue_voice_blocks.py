from __future__ import annotations

import pytest

from bestseller.services.dialogue_voice_blocks import render_dialogue_voice_block
from bestseller.services.dialogue_voice_profile import parse_dialogue_voice_profiles

pytestmark = pytest.mark.unit


def test_render_dialogue_voice_block_surfaces_regional_and_negative_space() -> None:
    profiles = parse_dialogue_voice_profiles(
        """
# Cast

## 楼下大妈
原型：P9
"""
    )

    block = render_dialogue_voice_block(profiles, language="zh-CN")

    assert "对白声纹合同" in block
    assert "框架级硬约束" in block
    assert "地域/口音" in block
    assert "留白方式" in block
    assert "不强制照抄" in block
    assert "有意思" in block


def test_render_english_dialogue_voice_block_avoids_fixed_catchphrase_contract() -> None:
    profiles = parse_dialogue_voice_profiles(
        """
# Cast

## Mara Vale
voice_archetype: broker
"""
    )

    block = render_dialogue_voice_block(profiles, language="en")

    assert "Dialogue voice contract" in block
    assert "Do not mechanically reuse fixed catchphrases" in block
    assert "diction strategy" in block
    assert "zh chars" not in block
