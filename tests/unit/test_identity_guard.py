from __future__ import annotations

import pytest

from bestseller.services.identity_guard import (
    CharacterIdentity,
    build_identity_constraint_block,
    validate_scene_text_identity,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# CharacterIdentity construction
# ---------------------------------------------------------------------------

def test_character_identity_is_frozen() -> None:
    ci = CharacterIdentity(name="Alice", gender="female")
    with pytest.raises(AttributeError):
        ci.name = "Bob"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# build_identity_constraint_block
# ---------------------------------------------------------------------------

def _make_registry() -> list[CharacterIdentity]:
    return [
        CharacterIdentity(
            name="顾清衍",
            aliases=("Gu Qingyan",),
            gender="male",
            pronoun_set_zh="他",
            pronoun_set_en="he/him",
            physical_markers=("scar on left cheek",),
            power_baseline="筑基",
            is_alive=True,
            role="protagonist",
        ),
        CharacterIdentity(
            name="苏婉",
            aliases=("Su Wan",),
            gender="female",
            pronoun_set_zh="她",
            pronoun_set_en="she/her",
            is_alive=True,
            role="supporting",
        ),
        CharacterIdentity(
            name="张老三",
            gender="male",
            pronoun_set_zh="他",
            pronoun_set_en="he/him",
            is_alive=False,
            role="supporting",
        ),
    ]


def test_constraint_block_zh_includes_all_participants() -> None:
    block = build_identity_constraint_block(
        _make_registry(),
        language="zh-CN",
        participant_names=["顾清衍", "苏婉"],
    )
    assert "顾清衍" in block
    assert "苏婉" in block
    assert "张老三" not in block  # not a participant


def test_constraint_block_zh_includes_gender_and_pronoun() -> None:
    block = build_identity_constraint_block(
        _make_registry(),
        language="zh-CN",
    )
    assert "男性" in block
    assert "女性" in block
    assert "他" in block
    assert "她" in block


def test_constraint_block_en() -> None:
    block = build_identity_constraint_block(
        _make_registry(),
        language="en",
        participant_names=None,
    )
    assert "CHARACTER IDENTITY CONSTRAINTS" in block
    assert "he/him" in block
    assert "she/her" in block
    assert "DEAD" in block  # 张老三 is dead


def test_constraint_block_empty_registry() -> None:
    assert build_identity_constraint_block([], language="zh-CN") == ""


def test_constraint_block_no_matching_participants() -> None:
    block = build_identity_constraint_block(
        _make_registry(),
        language="zh-CN",
        participant_names=["不存在的角色"],
    )
    assert block == ""


def test_constraint_block_alias_matching() -> None:
    block = build_identity_constraint_block(
        _make_registry(),
        language="en",
        participant_names=["Gu Qingyan"],  # alias
    )
    assert "顾清衍" in block


# ---------------------------------------------------------------------------
# validate_scene_text_identity — Chinese pronoun checks
# ---------------------------------------------------------------------------

def test_validate_zh_correct_pronouns_no_violations() -> None:
    registry = [
        CharacterIdentity(name="顾清衍", gender="male", pronoun_set_zh="他"),
    ]
    text = "顾清衍走进了大殿，他的目光扫过四周，然后他缓缓坐下。"
    violations = validate_scene_text_identity(
        text, registry, language="zh-CN", participant_names=["顾清衍"],
    )
    assert len(violations) == 0


def test_validate_zh_gender_flip_detected() -> None:
    registry = [
        CharacterIdentity(name="顾清衍", gender="male", pronoun_set_zh="他"),
    ]
    # Using female pronoun for a male character
    text = "顾清衍走进大殿，她的目光扫过四周。她微微一笑。她转身离去。"
    violations = validate_scene_text_identity(
        text, registry, language="zh-CN", participant_names=["顾清衍"],
    )
    # Should detect pronoun mismatch
    assert len(violations) >= 1
    assert any(v.violation_type == "pronoun_mismatch" for v in violations)


def test_validate_checks_registered_mentions_outside_participant_list() -> None:
    registry = [
        CharacterIdentity(name="顾清衍", gender="male", pronoun_set_zh="他"),
        CharacterIdentity(name="苏婉", gender="female", pronoun_set_zh="她"),
    ]
    text = "顾清衍退出殿门。苏婉，他的目光却躲闪了一下。"

    violations = validate_scene_text_identity(
        text,
        registry,
        language="zh-CN",
        participant_names=["顾清衍"],
    )

    assert any(v.character_name == "苏婉" for v in violations)


def test_validate_zh_skips_pronoun_after_another_named_character() -> None:
    registry = [
        CharacterIdentity(name="顾清衍", gender="male", pronoun_set_zh="他"),
        CharacterIdentity(name="苏婉", gender="female", pronoun_set_zh="她"),
    ]
    text = "顾清衍看向苏婉，她点了点头。"

    violations = validate_scene_text_identity(
        text,
        registry,
        language="zh-CN",
        participant_names=["顾清衍", "苏婉"],
    )

    assert violations == []


def test_validate_zh_skips_object_pronoun_in_mixed_gender_scene() -> None:
    registry = [
        CharacterIdentity(name="顾清衍", gender="male", pronoun_set_zh="他"),
        CharacterIdentity(name="苏婉", gender="female", pronoun_set_zh="她"),
    ]
    text = "苏婉走近两步，目光从他脸上扫过。她抬手扣住剑柄。"

    violations = validate_scene_text_identity(
        text,
        registry,
        language="zh-CN",
        participant_names=["顾清衍", "苏婉"],
    )

    assert violations == []


def test_validate_zh_skips_dialogue_pronoun_reference() -> None:
    registry = [
        CharacterIdentity(name="顾清衍", gender="male", pronoun_set_zh="他"),
        CharacterIdentity(name="苏婉", gender="female", pronoun_set_zh="她"),
    ]
    text = "顾清衍压低声音：“我猜她会在你突破时动手。”苏婉没有反驳。"

    violations = validate_scene_text_identity(
        text,
        registry,
        language="zh-CN",
        participant_names=["顾清衍", "苏婉"],
    )

    assert violations == []


def test_validate_zh_skips_short_name_inside_longer_registered_name() -> None:
    registry = [
        CharacterIdentity(name="周沉", gender="male", pronoun_set_zh="他"),
        CharacterIdentity(name="周沉渊", gender="male", pronoun_set_zh="他"),
        CharacterIdentity(name="叶清漪", gender="female", pronoun_set_zh="她"),
    ]
    text = "周沉渊的脸色彻底沉下去。她没有退后。"

    violations = validate_scene_text_identity(
        text,
        registry,
        language="zh-CN",
        participant_names=["周沉渊", "叶清漪"],
    )

    assert violations == []


def test_validate_zh_skips_name_mentioned_as_object() -> None:
    registry = [
        CharacterIdentity(name="宁尘", gender="male", pronoun_set_zh="他"),
        CharacterIdentity(name="燕青", gender="female", pronoun_set_zh="她"),
    ]
    text = "燕青终于看见宁尘，她先是一愣，然后眼眶红了。"

    violations = validate_scene_text_identity(
        text,
        registry,
        language="zh-CN",
        participant_names=["宁尘", "燕青"],
    )

    assert violations == []


def test_validate_zh_skips_when_context_shifts_to_gendered_person() -> None:
    registry = [
        CharacterIdentity(name="叶长青", gender="male", pronoun_set_zh="他"),
    ]
    text = "叶长青给了他七天。这个女人，也给了他七天。少女的声音没有波动，她的手指向黑暗。"

    violations = validate_scene_text_identity(
        text,
        registry,
        language="zh-CN",
        participant_names=["叶长青"],
    )

    assert violations == []


def test_validate_en_correct_pronouns_no_violations() -> None:
    registry = [
        CharacterIdentity(name="Alice", gender="female", pronoun_set_en="she/her"),
    ]
    text = "Alice walked into the room. She looked around. Her eyes scanned the area."
    violations = validate_scene_text_identity(
        text, registry, language="en", participant_names=["Alice"],
    )
    assert len(violations) == 0


def test_validate_en_gender_flip_detected() -> None:
    registry = [
        CharacterIdentity(name="Alice", gender="female", pronoun_set_en="she/her"),
    ]
    text = "Alice walked into the room. He looked around. His eyes scanned the area. He sat down. He smiled."
    violations = validate_scene_text_identity(
        text, registry, language="en", participant_names=["Alice"],
    )
    assert len(violations) >= 1
    assert any(v.violation_type == "pronoun_mismatch" for v in violations)


def test_validate_empty_text_no_violations() -> None:
    registry = [CharacterIdentity(name="Test", gender="male")]
    violations = validate_scene_text_identity("", registry, language="zh-CN")
    assert violations == []


def test_validate_empty_registry_no_violations() -> None:
    violations = validate_scene_text_identity("Some text here", [], language="zh-CN")
    assert violations == []
