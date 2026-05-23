from __future__ import annotations

import pytest

from bestseller.services.dialogue_voice_profile import parse_dialogue_voice_profiles

pytestmark = pytest.mark.unit


def test_parse_explicit_voice_dna_with_regional_accent() -> None:
    text = """
# Cast

## 钱婆婆
外显能力：收阴账、看账页
读者承诺：民间老一辈的压迫感

```yaml
voice_dna:
  archetype: P1
  register: 七十年代农村老一辈
  voice_traits:
    - 用短句施压
    - 敏感问题用动作绕开
  lexical_strategy: 使用本地生活和行当词，可替换，不固定
  sentence_length_zh: [5, 12]
  pet_phrases: [账, 这笔, 毛头]
  body_tells: [袖口抹嘴角]
  regional_markers: [哩, 嘞]
  accent_profile: 西南乡音，轻口音
  interpretation_rules:
    - 方言词靠上下文解释
```
"""

    profiles = parse_dialogue_voice_profiles(text)

    assert len(profiles) == 1
    profile = profiles[0]
    assert profile.character_name == "钱婆婆"
    assert profile.archetype == "P1"
    assert "用短句施压" in profile.voice_traits
    assert "不固定" in profile.lexical_strategy
    assert profile.sentence_length_zh == (5, 12)
    assert "毛头" in profile.pet_phrases
    assert "哩" in profile.regional_markers
    assert "西南乡音" in profile.accent_profile
    assert profile.interpretation_rules == ("方言词靠上下文解释",)


def test_parse_missing_voice_dna_infers_framework_archetype() -> None:
    text = """
# Cast

## 王建业
外显能力：江湖小商人、中间人
读者承诺：会套近乎但怕担责
"""

    profiles = parse_dialogue_voice_profiles(text)

    assert len(profiles) == 1
    assert profiles[0].character_name == "王建业"
    assert profiles[0].archetype == "P3_middleman_merchant"
    assert profiles[0].voice_traits


def test_parse_english_cast_infers_framework_archetype() -> None:
    text = """
# Cast

## Mara Vale
Visible ability: starship broker, black-market fixer
Reader promise: fast social navigation, hides risk behind friendliness
"""

    profiles = parse_dialogue_voice_profiles(text)

    assert len(profiles) == 1
    assert profiles[0].character_name == "Mara Vale"
    assert profiles[0].archetype == "P3_middleman_merchant"
    assert any("money" in item or "trade" in item for item in profiles[0].voice_traits)
