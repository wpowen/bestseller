from __future__ import annotations

import pytest

from bestseller.services.identity_guard import (
    CharacterIdentity,
    fix_zh_pronoun_mismatches,
    validate_scene_text_identity,
)

pytestmark = pytest.mark.unit


def _registry() -> list[CharacterIdentity]:
    return [
        CharacterIdentity(name="陆沉", gender="male", pronoun_set_zh="他"),
        CharacterIdentity(name="苏瑶", gender="female", pronoun_set_zh="她"),
    ]


def test_fixes_wrong_female_pronoun_for_male_character() -> None:
    text = "陆沉走进大殿，她的目光扫过四周。她微微一笑。"
    fixed, count = fix_zh_pronoun_mismatches(
        text, _registry(), participant_names=["陆沉"]
    )

    assert count >= 1
    revalidated = validate_scene_text_identity(
        fixed, _registry(), language="zh-CN", participant_names=["陆沉"]
    )
    assert not any(v.violation_type == "pronoun_mismatch" for v in revalidated)


def test_fix_preserves_text_length_and_other_content() -> None:
    text = "陆沉走进大殿，她的目光扫过四周。她微微一笑。"
    fixed, count = fix_zh_pronoun_mismatches(
        text, _registry(), participant_names=["陆沉"]
    )

    assert count >= 1
    assert len(fixed) == len(text)
    assert fixed.replace("他", "她") == text.replace("他", "她")


def test_does_not_touch_object_pronoun_of_other_character() -> None:
    # 苏瑶 is the nearest subject; "看着他" is an object reference to 陆沉.
    text = "苏瑶站在门口看着他，没有说话。"
    fixed, count = fix_zh_pronoun_mismatches(
        text, _registry(), participant_names=["陆沉", "苏瑶"]
    )

    assert count == 0
    assert fixed == text


def test_does_not_touch_pronouns_inside_dialogue() -> None:
    text = "陆沉低声说：「她不会再来了。」说完便离开。"
    fixed, count = fix_zh_pronoun_mismatches(
        text, _registry(), participant_names=["陆沉"]
    )

    assert count == 0
    assert fixed == text


def test_clean_text_is_untouched() -> None:
    text = "陆沉合上卷宗，他揉了揉眉心。苏瑶推门进来，她把工牌放在桌上。"
    fixed, count = fix_zh_pronoun_mismatches(
        text, _registry(), participant_names=["陆沉", "苏瑶"]
    )

    assert count == 0
    assert fixed == text


def test_unknown_gender_characters_are_skipped() -> None:
    registry = [CharacterIdentity(name="无名者", gender="unknown")]
    text = "无名者站起身，她笑了。"
    fixed, count = fix_zh_pronoun_mismatches(
        text, registry, participant_names=["无名者"]
    )

    assert count == 0
    assert fixed == text
