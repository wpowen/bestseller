from types import SimpleNamespace
from uuid import uuid4

import pytest

from bestseller.domain.project import WritingProfile
from bestseller.services.invariants import (
    LengthEnvelope,
    OpeningArchetype,
    ProjectInvariants,
    invariants_from_dict,
    invariants_to_dict,
    seed_invariants,
)
from bestseller.services.writing_presets import _synthesized_hype_block
from bestseller.services.writing_profile import (
    render_serial_fiction_guardrails,
    render_writing_profile_prompt_block,
    resolve_writing_profile,
    sanitize_genre_story_overrides,
)


@pytest.mark.parametrize("language", ["zh-CN", "en-US"])
@pytest.mark.parametrize("genre", ["自定义实验叙事", "现实百态", "仙侠"])
def test_unselected_content_stays_unspecified(language, genre):
    profile = resolve_writing_profile(None, genre=genre, language=language)
    assert profile.character.romance_mode in ("未指定", "unspecified")
    assert profile.character.relationship_tension in ("未指定", "unspecified")
    assert profile.serialization.opening_mandate in ("未指定", "unspecified")
    text = render_writing_profile_prompt_block(profile, language=language, mode="chapter")
    assert "即时危险" not in text
    assert "immediate danger" not in text
    assert "必须快速建立持续追读欲" not in text


def test_profile_object_only_contributes_explicit_fields():
    profile = resolve_writing_profile(
        WritingProfile(character={"romance_mode": "无感情线"}), genre="自定义实验叙事"
    )
    assert profile.character.romance_mode == "无感情线"
    assert profile.character.relationship_tension == "未指定"


def test_explicit_author_content_survives_including_old_default_values():
    content = {
        "character": {"romance_mode": "弱感情线"},
        "serialization": {"opening_mandate": "开篇必须出现即时危险。"},
    }
    profile = resolve_writing_profile(content, genre="自定义实验叙事")
    assert profile.character.romance_mode == "弱感情线"
    text = render_serial_fiction_guardrails(profile, language="zh-CN")
    assert "开篇必须出现即时危险。" in text


def test_automatic_pack_override_cannot_supply_story_or_commercial_contract():
    raw = {
        "market": {
            "opening_strategy": "主人公经营陶器店",
            "payoff_rhythm": "每章两次翻盘",
            "prompt_pack_key": "cozy-fantasy",
        },
        "serialization": {"opening_mandate": "开篇逃亡"},
        "character": {"romance_mode": "弱感情线"},
        "world": {"rule_hardness": "hard"},
    }
    clean = sanitize_genre_story_overrides(raw)
    assert clean == {
        "market": {"prompt_pack_key": "cozy-fantasy"},
        "world": {"rule_hardness": "hard"},
    }
    assert raw["market"]["opening_strategy"] == "主人公经营陶器店"


def test_cozy_pack_story_template_is_not_automatic_technique():
    from bestseller.services.prompt_packs import get_prompt_pack

    pack = get_prompt_pack("cozy-fantasy")
    assert pack.story_profile_overrides["market"]["opening_strategy"]
    assert "opening_strategy" not in pack.writing_profile_overrides["market"]
    profile = resolve_writing_profile(None, genre="种田仙侠")
    assert profile.market.opening_strategy == "未指定"
    assert "bookshop" not in render_writing_profile_prompt_block(profile, mode="chapter")


@pytest.mark.parametrize("pack", ["unknown-art", "cozy-fantasy", "xianxia-upgrade-core"])
def test_taxonomy_does_not_opt_author_into_effect_decks(pack):
    assert _synthesized_hype_block(SimpleNamespace(novel_category="custom", pack=pack)) == {}


def test_diversity_seed_and_roundtrip_allow_no_story_events():
    inv = seed_invariants(project_id=uuid4(), language="zh-CN", words_per_chapter=None)
    assert inv.opening_archetype_pool == ()
    assert invariants_from_dict(invariants_to_dict(inv)).opening_archetype_pool == ()
    direct = ProjectInvariants(uuid4(), "zh-CN", LengthEnvelope(1800, 2600, 3500))
    assert direct.opening_archetype_pool == ()
    assert direct.cliffhanger_policy.allowed_types == ()
    assert invariants_from_dict(invariants_to_dict(inv)).cliffhanger_policy.allowed_types == ()


def test_author_selected_archetype_retained_and_explicit_empty_honored():
    kw = dict(project_id=uuid4(), language="zh-CN", words_per_chapter=None, genre="治愈日常")
    inv = seed_invariants(**kw, overrides={"opening_archetype_pool": (OpeningArchetype.CRISIS,)})
    assert inv.opening_archetype_pool == (OpeningArchetype.CRISIS,)
    empty = seed_invariants(**kw, overrides={"opening_archetype_pool": ()})
    assert empty.opening_archetype_pool == ()


@pytest.mark.parametrize("language", ["zh-CN", "en-US"])
def test_guardrails_do_not_restore_removed_content(language):
    profile = resolve_writing_profile(None, genre="自定义实验叙事", language=language)
    text = render_serial_fiction_guardrails(profile, language=language, scope="chapter")
    for content in (
        "即时危险",
        "强迫读者",
        "Every scene needs",
        "immediate danger",
        "within the first 1500",
        "前 1500",
    ):
        assert content not in text
