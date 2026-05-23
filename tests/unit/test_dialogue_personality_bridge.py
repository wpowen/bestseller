from __future__ import annotations

import pytest

from bestseller.services.dialogue_personality_bridge import (
    derive_personality_bound_dialogue_contract,
    render_dialogue_personality_bridge_block,
)

pytestmark = pytest.mark.unit


def test_personality_bridge_derives_dialogue_and_action_from_existing_profiles() -> None:
    participant = {
        "name": "林砚",
        "psych_profile": {
            "personality_label": "克制的修复者",
            "mbti": "INTJ",
            "enneagram": "Type 5",
            "attachment_style": "avoidant",
            "big_five": {
                "openness": 0.8,
                "conscientiousness": 0.75,
                "extraversion": 0.2,
                "agreeableness": 0.3,
                "neuroticism": 0.7,
            },
        },
        "moral_framework": {
            "core_values": ["兑现承诺", "保护弱者"],
            "lines_never_crossed": ["牺牲无辜者"],
        },
        "character_engine_profile": {
            "want_vs_need": {
                "want": "查清母亲失踪真相",
                "need": "承认自己需要同伴",
            },
            "three_layer_motivation": {
                "surface": "破案",
                "hidden": "证明自己没有被抛弃",
                "suppressed": "害怕再次相信别人",
            },
            "values_and_redlines": {
                "core_value": "承诺必须有代价",
                "absolute_no": ["利用孩子做诱饵"],
            },
            "unique_response_chain": {
                "被质疑": {
                    "step_1": "沉默并观察对方破绽",
                    "step_2": "用事实反问",
                    "step_3": "给出只够推进一步的信息",
                }
            },
            "voice_dna": {
                "sentence_length_preference": "短句",
                "vocabulary_register": "冷静技术词",
                "lie_pattern": "只省略关键动机",
            },
        },
    }

    contract = derive_personality_bound_dialogue_contract(
        participant,
        language="zh-CN",
    )

    assert contract["name"] == "林砚"
    assert any("隐喻" in rule for rule in contract["dialogue_rules"])
    assert any("先观察" in rule for rule in contract["dialogue_rules"])
    assert any("表层说出口的目标" in rule for rule in contract["dialogue_rules"])
    assert any("红线" in rule or "行动不能" in rule for rule in contract["action_rules"])
    assert "Big Five/OCEAN" in contract["inference_targets"]


def test_render_personality_bridge_block_avoids_fixed_phrase_matching() -> None:
    participants = [
        {
            "name": "Mara Vale",
            "psych_profile": {
                "mbti": "ENTP",
                "enneagram": "7w8",
                "attachment_style": "secure",
                "big_five": {
                    "openness": "high",
                    "conscientiousness": "low",
                    "extraversion": "high",
                },
            },
            "voice_profile": {
                "speech_register": "street-smart professional",
                "sentence_style": "fast reversals and clipped questions",
                "mannerisms": ["checks exits before joking"],
            },
        }
    ]

    block = render_dialogue_personality_bridge_block(
        participants,
        language="en",
    )

    assert "personality-bound dialogue/action contract" in block
    assert "Do not force exact catchphrases" in block
    assert "infer personality from dialogue, action, silence, and choices" in block
    assert "must say" not in block.lower()
    assert "exact word" not in block.lower()
