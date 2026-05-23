from __future__ import annotations

import pytest

from bestseller.services.dialogue_voice_gate import (
    DIALOGUE_EXPLICIT_MARKERS_MISSING,
    DIALOGUE_LLM_DEFAULT,
    DIALOGUE_NO_NEGSPACE,
    DIALOGUE_PING_PONG,
    check_dialogue_voice,
)
from bestseller.services.dialogue_voice_profile import parse_dialogue_voice_profiles

pytestmark = pytest.mark.unit


def test_dialogue_voice_gate_blocks_generic_llm_dialogue_phrase() -> None:
    profiles = parse_dialogue_voice_profiles(
        """
# Cast

## 林渊
原型：P2
"""
    )
    chapter = "林渊按住罗盘，说：“看来这事不简单。”\n他没有再解释。"

    report = check_dialogue_voice(chapter, chapter_position=1, profiles=profiles)

    assert report.has_critical
    assert DIALOGUE_LLM_DEFAULT in {finding.code for finding in report.findings}


def test_dialogue_voice_gate_detects_flat_ping_pong_and_missing_negative_space() -> None:
    profiles = parse_dialogue_voice_profiles(
        """
# Cast

## 林渊
原型：P2

## 王建业
原型：P3
"""
    )
    chapter = (
        "林渊说：“先把门打开。”\n"
        "王建业说：“我真不知道啊。”\n"
        "林渊说：“先把灯关掉。”\n"
        "王建业说：“我真不知道啊。”\n"
    )

    report = check_dialogue_voice(chapter, chapter_position=2, profiles=profiles)
    codes = {finding.code for finding in report.findings}

    assert DIALOGUE_PING_PONG in codes
    assert DIALOGUE_NO_NEGSPACE in codes


def test_dialogue_voice_gate_supports_english_generic_phrase_detection() -> None:
    profiles = parse_dialogue_voice_profiles(
        """
# Cast

## Mara Vale
Visible ability: starship broker, black-market fixer
"""
    )
    chapter = 'Mara Vale said, "As you know, the port is closed."'

    report = check_dialogue_voice(
        chapter,
        chapter_position=1,
        profiles=profiles,
        language="en",
    )

    assert report.has_critical
    assert DIALOGUE_LLM_DEFAULT in {finding.code for finding in report.findings}


def test_dialogue_voice_gate_does_not_require_exact_catchphrase_by_default() -> None:
    profiles = parse_dialogue_voice_profiles(
        """
# Cast

## 钱婆婆
```yaml
voice_dna:
  archetype: P1
  pet_phrases: [账, 毛头]
  body_tells: [袖口抹嘴角]
```
"""
    )
    chapter = (
        "钱婆婆说：“门槛外站着。”\n"
        "钱婆婆说：“手别伸。”\n"
        "钱婆婆说：“听雨声。”\n"
        "她把旧本子合上，没有回答林渊的问题。"
    )

    report = check_dialogue_voice(chapter, chapter_position=3, profiles=profiles)

    assert DIALOGUE_EXPLICIT_MARKERS_MISSING not in {
        finding.code for finding in report.findings
    }
