from __future__ import annotations

import pytest

from bestseller.services.identity_guard import (
    CharacterIdentity,
    _character_is_alive,
    _identity_row_should_enter_registry,
    _manifest_first_token_counts,
    _manifest_identity_aliases,
    _upsert_manifest_identity,
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


def test_character_alive_status_dead_without_death_chapter_is_not_treated_dead() -> None:
    character = type(
        "CharacterStub",
        (),
        {
            "alive_status": "dead",
            "death_chapter_number": None,
        },
    )()

    assert _character_is_alive(character, {}) is True


def test_character_alive_status_dead_with_death_chapter_is_treated_dead() -> None:
    character = type(
        "CharacterStub",
        (),
        {
            "alive_status": "deceased",
            "death_chapter_number": 19,
        },
    )()

    assert _character_is_alive(character, {}) is False


def test_identity_registry_skips_placeholder_relational_rows() -> None:
    character = type(
        "CharacterStub",
        (),
        {
            "name": "His father",
        },
    )()

    assert _identity_row_should_enter_registry(
        character,
        {"placeholder": True},
        {},
    ) is False


def test_identity_registry_keeps_placeholder_with_locked_gender() -> None:
    character = type(
        "CharacterStub",
        (),
        {
            "name": "Kade",
        },
    )()

    assert _identity_row_should_enter_registry(
        character,
        {"placeholder": True, "gender": "male"},
        {},
    ) is True


def test_manifest_identity_overlay_adds_unique_short_alias_to_existing_row() -> None:
    manifest = [
        {
            "name": "Kade Mercer",
            "role": "protagonist",
            "gender": "male",
            "pronoun_set_zh": "他",
            "pronoun_set_en": "he/him",
        },
        {
            "name": "Maya Mercer",
            "role": "family",
            "gender": "female",
            "pronoun_set_zh": "她",
            "pronoun_set_en": "she/her",
        },
    ]
    aliases = _manifest_identity_aliases(manifest[0], _manifest_first_token_counts(manifest))
    registry = _upsert_manifest_identity(
        [
            CharacterIdentity(name="Kade", role="supporting"),
            CharacterIdentity(name="Kade Mercer", role="protagonist"),
        ],
        CharacterIdentity(
            name="Kade Mercer",
            aliases=tuple(aliases),
            gender="male",
            pronoun_set_zh="他",
            pronoun_set_en="he/him",
            role="protagonist",
        ),
    )

    kade_mercer = next(item for item in registry if item.name == "Kade Mercer")
    assert "Kade" in kade_mercer.aliases
    assert kade_mercer.gender == "male"


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


def test_validate_zh_skips_object_pronoun_after_aspect_particle() -> None:
    registry = [
        CharacterIdentity(name="林渊", gender="male", pronoun_set_zh="他"),
        CharacterIdentity(name="镜中女人", gender="female", pronoun_set_zh="她"),
    ]
    text = "林渊盯着铜镜，然后他看见了她。雾气中走出一个女人的轮廓。"

    violations = validate_scene_text_identity(
        text,
        registry,
        language="zh-CN",
        participant_names=["林渊", "镜中女人"],
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


def test_validate_zh_skips_cross_gender_possessive_object_after_break_free_action() -> None:
    registry = [
        CharacterIdentity(name="宁尘", gender="male", pronoun_set_zh="他"),
        CharacterIdentity(name="墨离", gender="nonbinary", pronoun_set_zh="ta"),
    ]
    text = "宁尘没有挣开她的手。他低头看着那枚玉简。墨离的脸色在风里发白。"

    violations = validate_scene_text_identity(
        text,
        registry,
        language="zh-CN",
        participant_names=["宁尘"],
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


def test_validate_zh_allows_dead_character_quoted_memory() -> None:
    registry = [
        CharacterIdentity(
            name="母亲",
            gender="female",
            pronoun_set_zh="她",
            is_alive=False,
        ),
    ]
    text = "母亲说过：“姜家的东西，碰不得。”苏砚将这句话按回心底。"

    violations = validate_scene_text_identity(
        text,
        registry,
        language="zh-CN",
        participant_names=["苏砚"],
    )

    assert violations == []


def test_validate_zh_allows_dead_character_memory_scene_speech() -> None:
    registry = [
        CharacterIdentity(
            name="林正淳",
            gender="male",
            pronoun_set_zh="他",
            is_alive=False,
        ),
    ]
    text = (
        "林渊看到的是任守明死前最后一秒的记忆画面。"
        "他当时还笑他爸，说这扇子废了。林正淳说，墨渍在扇面上反而是镇邪的。"
    )

    violations = validate_scene_text_identity(
        text,
        registry,
        language="zh-CN",
        participant_names=["林渊"],
        chapter_number=59,
    )

    assert violations == []


def test_validate_zh_allows_dead_character_years_ago_recollection() -> None:
    registry = [
        CharacterIdentity(
            name="林正淳",
            gender="male",
            pronoun_set_zh="他",
            is_alive=False,
        ),
    ]
    text = "十三年前他见过最后一面，在父亲书房。林正淳说那是太爷爷留下的东西。"

    violations = validate_scene_text_identity(
        text,
        registry,
        language="zh-CN",
        participant_names=["林渊"],
        chapter_number=59,
    )

    assert violations == []


def test_validate_zh_skips_speaker_label_dialogue_pronoun() -> None:
    registry = [
        CharacterIdentity(
            name="苏砚",
            gender="male",
            pronoun_set_zh="他",
        ),
    ]
    text = "苏砚：她留下了后手。\n\n院外的死寂持续了数息。苏砚没动。"

    violations = validate_scene_text_identity(
        text,
        registry,
        language="zh-CN",
        participant_names=["苏砚"],
    )

    assert violations == []


def test_validate_zh_blocks_dead_character_present_speech() -> None:
    registry = [
        CharacterIdentity(
            name="母亲",
            gender="female",
            pronoun_set_zh="她",
            is_alive=False,
        ),
    ]
    text = "火光里，母亲说道：“跟我走。”苏砚怔在原地。"

    violations = validate_scene_text_identity(
        text,
        registry,
        language="zh-CN",
        participant_names=["苏砚"],
    )

    assert any(v.violation_type == "dead_alive" for v in violations)


def test_validate_zh_allows_dead_character_mirror_manifestation() -> None:
    registry = [
        CharacterIdentity(
            name="林正淳",
            gender="male",
            pronoun_set_zh="他",
            is_alive=False,
            death_chapter_number=52,
        ),
    ]
    text = (
        "林正淳的眼球是两块打磨过的镜片，映出林渊苍白的脸。"
        "“别怕。”林正淳的头动了一下，像是想摇头，"
        "“这具身体还没死透。眼睛换了，嗓子还能用几天。”"
        "镜子里的倒影又笑了一次。林渊后退半步。"
        "林正淳说：“别信他。”"
    )

    violations = validate_scene_text_identity(
        text,
        registry,
        language="zh-CN",
        participant_names=["林渊"],
        chapter_number=64,
    )

    assert violations == []


def test_validate_zh_allows_future_death_character_before_death_chapter() -> None:
    registry = [
        CharacterIdentity(
            name="柳如是",
            gender="female",
            pronoun_set_zh="她",
            is_alive=False,
            death_chapter_number=30,
        ),
    ]
    text = "火光里，柳如是说道：“我来找一样东西。”"

    violations = validate_scene_text_identity(
        text,
        registry,
        language="zh-CN",
        participant_names=["柳如是"],
        chapter_number=20,
    )

    assert violations == []


def test_validate_zh_blocks_dead_character_after_death_chapter() -> None:
    registry = [
        CharacterIdentity(
            name="柳如是",
            gender="female",
            pronoun_set_zh="她",
            is_alive=False,
            death_chapter_number=19,
        ),
    ]
    text = "火光里，柳如是说道：“我来找一样东西。”"

    violations = validate_scene_text_identity(
        text,
        registry,
        language="zh-CN",
        participant_names=["柳如是"],
        chapter_number=20,
    )

    assert any(v.violation_type == "dead_alive" for v in violations)


def test_validate_zh_allows_dead_character_in_death_chapter() -> None:
    registry = [
        CharacterIdentity(
            name="柳如是",
            gender="female",
            pronoun_set_zh="她",
            is_alive=False,
            death_chapter_number=20,
        ),
    ]
    text = "柳如是在终章前说道：“我会守住这里。”"

    violations = validate_scene_text_identity(
        text,
        registry,
        language="zh-CN",
        participant_names=["柳如是"],
        chapter_number=20,
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


def test_validate_en_skips_new_sentence_pronoun_with_competing_character() -> None:
    registry = [
        CharacterIdentity(name="Victor Kane", gender="male", pronoun_set_en="he/him"),
        CharacterIdentity(name="Elena Vasquez", gender="female", pronoun_set_en="she/her"),
    ]
    text = (
        "Victor Kane finishes whatever ritual he is conducting three levels below us. "
        "She turns back to the console, hands shaking."
    )

    violations = validate_scene_text_identity(
        text,
        registry,
        language="en",
        participant_names=["Victor Kane", "Elena Vasquez"],
    )

    assert violations == []


def test_validate_en_skips_later_paragraph_pronoun_with_competing_character() -> None:
    registry = [
        CharacterIdentity(name="Victor Kane", gender="male", pronoun_set_en="he/him"),
        CharacterIdentity(name="Elena Vasquez", gender="female", pronoun_set_en="she/her"),
    ]
    text = (
        "Victor Kane's ancient entity stirred toward whatever came next.\n\n"
        "\"I'm asking you to help me save them.\" I met her eyes."
    )

    violations = validate_scene_text_identity(
        text,
        registry,
        language="en",
        participant_names=["Victor Kane", "Elena Vasquez"],
    )

    assert violations == []


def test_validate_zh_skips_pronoun_after_recalled_scene_colon() -> None:
    registry = [
        CharacterIdentity(name="宁尘", gender="male", pronoun_set_zh="他"),
    ]
    text = (
        "宁尘跟上，脑中却反复回放那一幕：她的目光里没有敌意，只有审视。"
    )

    violations = validate_scene_text_identity(
        text,
        registry,
        language="zh-CN",
        participant_names=["宁尘"],
    )

    assert violations == []


def test_validate_zh_skips_pronoun_owned_by_duifang_anchor() -> None:
    registry = [
        CharacterIdentity(name="沈青崖", gender="male", pronoun_set_zh="他"),
    ]
    text = (
        "沈青崖下意识后仰半寸，却发现对方的目光根本不在自己脸上——"
        "她看着的是虚空。"
    )

    violations = validate_scene_text_identity(
        text,
        registry,
        language="zh-CN",
        participant_names=["沈青崖"],
    )

    assert violations == []


def test_validate_zh_skips_possessive_object_after_discovery_verb() -> None:
    registry = [
        CharacterIdentity(name="沈青崖", gender="male", pronoun_set_zh="他"),
        CharacterIdentity(name="白芷萱", gender="female", pronoun_set_zh="她"),
    ]
    text = (
        "白芷萱抬手，纤细的指尖在沈青崖眼前划过一道弧线。"
        "沈青崖下意识后仰，却发现她的目光根本不在自己脸上。"
    )

    violations = validate_scene_text_identity(
        text,
        registry,
        language="zh-CN",
        participant_names=["沈青崖", "白芷萱"],
    )

    assert violations == []


def test_validate_zh_skips_possessive_after_shunzhe_object_phrase() -> None:
    registry = [
        CharacterIdentity(name="林渊", gender="male", pronoun_set_zh="他"),
    ]
    text = (
        "雾气中走出一个女人的轮廓。林渊感到一阵刺痛，"
        "像有什么东西正顺着她的目光往他身体里钻。"
    )

    violations = validate_scene_text_identity(
        text,
        registry,
        language="zh-CN",
        participant_names=["林渊"],
    )

    assert violations == []


def test_validate_en_skips_gendered_noun_behind_character_reference() -> None:
    registry = [
        CharacterIdentity(name="Kade Mercer", gender="male", pronoun_set_en="he/him"),
        CharacterIdentity(name="Zoe Chen", gender="female", pronoun_set_en="she/her"),
    ]
    text = (
        "The woman behind Kade Mercer was still shaking, still clutching her "
        "briefcase like a lifeline. Kade looked at the fracture."
    )

    violations = validate_scene_text_identity(
        text,
        registry,
        language="en",
        participant_names=["Kade Mercer", "Zoe Chen"],
    )

    assert violations == []


def test_validate_en_skips_name_inside_dialogue_before_speaker_pronoun() -> None:
    registry = [
        CharacterIdentity(name="Kade Mercer", gender="male", pronoun_set_en="he/him"),
    ]
    text = (
        "“You could be Kade Mercer again. Clean. Pure. Human.” "
        "Her voice hardened around the last word."
    )

    violations = validate_scene_text_identity(
        text,
        registry,
        language="en",
        participant_names=["Kade Mercer"],
    )

    assert violations == []


def test_validate_en_skips_object_pronoun_after_male_subject() -> None:
    registry = [
        CharacterIdentity(name="Kade Mercer", aliases=("Kade",), gender="male", pronoun_set_en="he/him"),
    ]
    text = "Kade looked at her. Really looked, past the old grief."

    violations = validate_scene_text_identity(
        text,
        registry,
        language="en",
        participant_names=["Kade Mercer"],
    )

    assert violations == []


def test_validate_en_skips_later_proper_name_subject() -> None:
    registry = [
        CharacterIdentity(name="Kade Mercer", aliases=("Kade",), gender="male", pronoun_set_en="he/him"),
    ]
    text = "Kade finally turned. Zoe stood five feet back, her hands at her sides."

    violations = validate_scene_text_identity(
        text,
        registry,
        language="en",
        participant_names=["Kade Mercer"],
    )

    assert violations == []


def test_validate_en_skips_found_proper_name_subject() -> None:
    registry = [
        CharacterIdentity(name="Kade Mercer", aliases=("Kade",), gender="male", pronoun_set_en="he/him"),
    ]
    text = "Kade found Mira Vance in the operations room. She didn't look up."

    violations = validate_scene_text_identity(
        text,
        registry,
        language="en",
        participant_names=["Kade Mercer"],
    )

    assert violations == []


def test_validate_en_skips_possessive_proper_name_owner() -> None:
    registry = [
        CharacterIdentity(name="Kade Mercer", aliases=("Kade",), gender="male", pronoun_set_en="he/him"),
    ]
    text = "Kade didn't answer. His hand was still in Zoe's. Her crystal was fading."

    violations = validate_scene_text_identity(
        text,
        registry,
        language="en",
        participant_names=["Kade Mercer"],
    )

    assert violations == []


def test_validate_en_skips_subordinate_subject_after_watch_verb() -> None:
    registry = [
        CharacterIdentity(name="Kade Mercer", aliases=("Kade",), gender="male", pronoun_set_en="he/him"),
    ]
    text = "Kade watched until she disappeared around the corner."

    violations = validate_scene_text_identity(
        text,
        registry,
        language="en",
        participant_names=["Kade Mercer"],
    )

    assert violations == []


def test_validate_en_skips_possessive_after_across_preposition() -> None:
    registry = [
        CharacterIdentity(name="Kade Mercer", aliases=("Kade",), gender="male", pronoun_set_en="he/him"),
    ]
    text = "Kade stopped at the edge of the table. The screens cast light across her face."

    violations = validate_scene_text_identity(
        text,
        registry,
        language="en",
        participant_names=["Kade Mercer"],
    )

    assert violations == []


def test_validate_en_skips_single_name_shook_subject() -> None:
    registry = [
        CharacterIdentity(name="Kade Mercer", aliases=("Kade",), gender="male", pronoun_set_en="he/him"),
    ]
    text = "Kade said he understood. Zoe shook her head."

    violations = validate_scene_text_identity(
        text,
        registry,
        language="en",
        participant_names=["Kade Mercer"],
    )

    assert violations == []


def test_validate_en_skips_vocative_name_in_italic_dialogue() -> None:
    registry = [
        CharacterIdentity(name="Marcus Cole", aliases=("Marcus",), gender="male", pronoun_set_en="he/him"),
    ]
    text = "*Marcus*, she said. *The thing at the school. I remembered something.*"

    violations = validate_scene_text_identity(
        text,
        registry,
        language="en",
        participant_names=["Marcus Cole"],
    )

    assert violations == []


def test_validate_en_skips_name_used_as_organization_modifier() -> None:
    registry = [
        CharacterIdentity(name="Victor Kane", gender="male", pronoun_set_en="he/him"),
    ]
    text = "Sophie had been registered in the Victor Kane Network database since she was nine years old."

    violations = validate_scene_text_identity(
        text,
        registry,
        language="en",
        participant_names=["Victor Kane"],
    )

    assert violations == []


def test_validate_en_skips_relative_clause_after_object_noun() -> None:
    registry = [
        CharacterIdentity(name="Kade Mercer", aliases=("Kade",), gender="male", pronoun_set_en="he/him"),
    ]
    text = "Kade looked at the tablet she'd pushed back toward him."

    violations = validate_scene_text_identity(
        text,
        registry,
        language="en",
        participant_names=["Kade Mercer"],
    )

    assert violations == []


def test_validate_en_skips_indefinite_pronoun_relative_clause() -> None:
    registry = [
        CharacterIdentity(name="Mira Vance", aliases=("Mira",), gender="female", pronoun_set_en="she/her"),
    ]
    text = "Mira as villain, complicated by something he hadn't anticipated."

    violations = validate_scene_text_identity(
        text,
        registry,
        language="en",
        participant_names=["Mira Vance"],
    )

    assert violations == []


def test_validate_en_skips_speaker_pronoun_after_dialogue() -> None:
    registry = [
        CharacterIdentity(name="Kade Mercer", aliases=("Kade",), gender="male", pronoun_set_en="he/him"),
    ]
    text = "Kade didn't flinch. “I've got reason to be.” She gestured at the space between them."

    violations = validate_scene_text_identity(
        text,
        registry,
        language="en",
        participant_names=["Kade Mercer"],
    )

    assert violations == []


def test_validate_en_skips_name_as_prepositional_object() -> None:
    registry = [
        CharacterIdentity(name="Kade Mercer", aliases=("Kade",), gender="male", pronoun_set_en="he/him"),
    ]
    text = "Zoe looked at Kade, forty-four lights dancing behind her eyes."

    violations = validate_scene_text_identity(
        text,
        registry,
        language="en",
        participant_names=["Kade Mercer"],
    )

    assert violations == []


def test_validate_en_skips_competing_unspecified_gender_name_hint() -> None:
    registry = [
        CharacterIdentity(name="Kade Mercer", aliases=("Kade",), gender="male", pronoun_set_en="he/him"),
        CharacterIdentity(name="Zoe Chen", gender="unknown"),
    ]
    text = (
        "Zoe stood at the cracked window, watching Kade pace three steps left. "
        "She'd counted every pass. Kade's pacing didn't break stride. "
        "He dropped his voice on the last word, just like she remembered."
    )

    violations = validate_scene_text_identity(
        text,
        registry,
        language="en",
        participant_names=["Kade Mercer", "Zoe Chen"],
    )

    assert violations == []


def test_validate_en_skips_relative_memory_clause_after_name() -> None:
    registry = [
        CharacterIdentity(name="Kade Mercer", aliases=("Kade",), gender="male", pronoun_set_en="he/him"),
        CharacterIdentity(name="Zoe Chen", gender="unknown"),
    ]
    text = (
        "The Kade she remembered would have strategized, calculated, and found "
        "the angle that minimized visible panic."
    )

    violations = validate_scene_text_identity(
        text,
        registry,
        language="en",
        participant_names=["Kade Mercer", "Zoe Chen"],
    )

    assert violations == []


def test_validate_en_skips_embedded_clause_after_cognition_verb() -> None:
    registry = [
        CharacterIdentity(name="Victor Hale", aliases=("Victor",), gender="male", pronoun_set_en="he/him"),
        CharacterIdentity(name="Rowan Ashford", gender="female", pronoun_set_en="she/her"),
    ]
    text = "The documents were useless if Victor Hale already knew she'd taken them."

    violations = validate_scene_text_identity(
        text,
        registry,
        language="en",
        participant_names=["Victor Hale", "Rowan Ashford"],
    )

    assert violations == []


def test_validate_en_still_blocks_cross_sentence_gender_flip_without_owner() -> None:
    registry = [
        CharacterIdentity(name="Kade Mercer", aliases=("Kade",), gender="male", pronoun_set_en="he/him"),
    ]
    text = "Kade stopped in the doorway. She opened the file."

    violations = validate_scene_text_identity(
        text,
        registry,
        language="en",
        participant_names=["Kade Mercer"],
    )

    assert any(v.violation_type == "pronoun_mismatch" for v in violations)


def test_validate_en_skips_later_gendered_subject_after_character_reference() -> None:
    registry = [
        CharacterIdentity(name="Kade Mercer", gender="male", pronoun_set_en="he/him"),
        CharacterIdentity(name="Mira Vance", gender="female", pronoun_set_en="she/her"),
    ]
    text = (
        "That detail lodged in Kade Mercer's mind even as armed guards flanked "
        "the courtyard entrance, and the woman beneath the colonnade gestured "
        "toward the seat across from her."
    )

    violations = validate_scene_text_identity(
        text,
        registry,
        language="en",
        participant_names=["Kade Mercer", "Mira Vance"],
    )

    assert violations == []


def test_validate_en_skips_coordinated_clause_returning_to_prior_character() -> None:
    registry = [
        CharacterIdentity(name="Kade Mercer", gender="male", pronoun_set_en="he/him"),
        CharacterIdentity(name="Maya Mercer", gender="female", pronoun_set_en="she/her"),
    ]
    text = (
        "Kade Mercer moved toward something older, something Maya Mercer had "
        "locked away and he'd spent months learning to reach."
    )

    violations = validate_scene_text_identity(
        text,
        registry,
        language="en",
        participant_names=["Kade Mercer", "Maya Mercer"],
    )

    assert violations == []


def test_validate_en_skips_possessive_location_reference() -> None:
    registry = [
        CharacterIdentity(
            name="Kade Mercer",
            aliases=("Kade",),
            gender="male",
            pronoun_set_en="he/him",
        ),
    ]
    text = (
        "Elena Vance materialized from the doorway three meters to Kade's left, "
        "her gun already drawn. Kade turned toward Dominic."
    )

    violations = validate_scene_text_identity(
        text,
        registry,
        language="en",
        participant_names=["Kade Mercer"],
    )

    assert violations == []


def test_validate_en_skips_object_pronouns_in_mixed_gender_scene() -> None:
    registry = [
        CharacterIdentity(
            name="Kade Mercer",
            aliases=("Kade",),
            gender="male",
            pronoun_set_en="he/him",
        ),
        CharacterIdentity(
            name="Maya Mercer",
            aliases=("Maya",),
            gender="female",
            pronoun_set_en="she/her",
        ),
    ]
    text = (
        "Maya's hand convulsed in his grip. Her lips were still moving. "
        "Kade spun to see her clutching her head."
    )

    violations = validate_scene_text_identity(
        text,
        registry,
        language="en",
        participant_names=["Kade Mercer", "Maya Mercer"],
    )

    assert violations == []


def test_validate_empty_text_no_violations() -> None:
    registry = [CharacterIdentity(name="Test", gender="male")]
    violations = validate_scene_text_identity("", registry, language="zh-CN")
    assert violations == []


def test_validate_empty_registry_no_violations() -> None:
    violations = validate_scene_text_identity("Some text here", [], language="zh-CN")
    assert violations == []
