from __future__ import annotations

import pytest

from bestseller.domain.project import ProjectCreate
from bestseller.services.story_effect_skills import (
    STORY_EFFECT_SKILL_CATALOG_METADATA_KEY,
    STORY_EFFECT_SKILL_CATALOG_VERSION,
    STORY_EFFECT_SKILL_SELECTION_METADATA_KEY,
    render_selected_story_effect_skill_contracts,
    render_story_effect_skill_catalog_prompt_block,
    resolve_story_effect_skill_catalog,
    selected_story_effect_skill_keys,
    story_effect_skill_catalog_from_metadata,
)
from bestseller.services.writing_profile import (
    build_project_metadata,
    resolve_project_create_writing_profile,
)


EXPECTED_STORY_EFFECT_SKILL_KEYS = {
    "brainhole_engine",
    "comedy_engine",
    "emotional_payoff_engine",
    "relationship_chemistry_engine",
    "suspense_reveal_engine",
    "hype_satisfaction_engine",
    "moral_dilemma_engine",
    "system_payoff_engine",
    "tension_pressure_engine",
    "rhythm_pacing_engine",
    "twist_reversal_engine",
    "callback_motif_engine",
    "world_texture_engine",
    "wonder_awe_engine",
    "danger_action_engine",
    "dialogue_spark_engine",
    "healing_grief_engine",
    "romance_tenderness_engine",
}


def test_resolve_mythic_catalog_lists_compact_selectable_skills() -> None:
    catalog = resolve_story_effect_skill_catalog("都市神仙", "神仙招聘")

    assert catalog.version == STORY_EFFECT_SKILL_CATALOG_VERSION
    assert catalog.catalog_key == "mythic-workplace-effect-skills"
    assert catalog.activation.gate_mode == "audit_only"
    assert catalog.activation.affects_legacy_projects is False
    skill_keys = {skill.skill_key for skill in catalog.skills}
    assert skill_keys == EXPECTED_STORY_EFFECT_SKILL_KEYS
    assert len(catalog.skills) == 18


def test_mythic_stage_preferences_use_second_batch_defaults_without_romance() -> None:
    catalog = resolve_story_effect_skill_catalog("都市神仙", "神仙招聘")

    assert catalog.default_stage_preferences["opening"] == (
        "brainhole_engine",
        "comedy_engine",
        "world_texture_engine",
        "dialogue_spark_engine",
        "relationship_chemistry_engine",
    )
    assert catalog.default_stage_preferences["early_middle"] == (
        "tension_pressure_engine",
        "twist_reversal_engine",
        "callback_motif_engine",
        "hype_satisfaction_engine",
    )
    assert catalog.default_stage_preferences["middle_late"] == (
        "emotional_payoff_engine",
        "moral_dilemma_engine",
        "danger_action_engine",
        "system_payoff_engine",
    )
    assert catalog.default_stage_preferences["late"] == (
        "system_payoff_engine",
        "wonder_awe_engine",
        "healing_grief_engine",
        "callback_motif_engine",
    )
    all_default_skills = {
        skill_key
        for skill_keys in catalog.default_stage_preferences.values()
        for skill_key in skill_keys
    }
    assert "romance_tenderness_engine" not in all_default_skills


def test_catalog_prompt_is_short_router_not_full_contract() -> None:
    catalog = resolve_story_effect_skill_catalog("都市神仙", "神仙招聘")

    block = render_story_effect_skill_catalog_prompt_block(
        {STORY_EFFECT_SKILL_CATALOG_METADATA_KEY: catalog.to_metadata()},
        language="zh-CN",
    )

    assert "【故事效果 Skill 清单】" in block
    assert "brainhole_engine" in block
    assert "`selected_effect_skills`" in block
    assert "只展开并输出被选中 skill 的合同" in block
    assert "tension_pressure_engine" in block
    assert "rhythm_pacing_engine" in block
    assert "callback_motif_engine" in block
    assert "world_texture_engine" in block
    assert "防误用=" in block
    assert "【脑洞生成合同】" not in block
    assert "【张力压力合同】" not in block
    assert "【节奏调度合同】" not in block
    assert "【回环母题合同】" not in block
    assert "【世界质感合同】" not in block
    assert "成长安全门" not in block


def test_selected_contract_expansion_requires_explicit_selection() -> None:
    catalog = resolve_story_effect_skill_catalog("都市神仙", "神仙招聘")
    metadata = {STORY_EFFECT_SKILL_CATALOG_METADATA_KEY: catalog.to_metadata()}

    assert render_selected_story_effect_skill_contracts(metadata, language="zh-CN") == ""

    selected_metadata = {
        **metadata,
        STORY_EFFECT_SKILL_SELECTION_METADATA_KEY: {
            "chapter_outline": {
                "primary": "brainhole_engine",
                "secondary": "comedy_engine",
            }
        },
    }

    assert selected_story_effect_skill_keys(selected_metadata) == (
        "brainhole_engine",
        "comedy_engine",
    )
    block = render_selected_story_effect_skill_contracts(
        selected_metadata,
        language="zh-CN",
    )
    assert "【脑洞生成合同】" in block
    assert "`brainhole_contract`" in block


@pytest.mark.parametrize(
    ("skill_key", "heading", "contract_name"),
    (
        ("tension_pressure_engine", "【张力压力合同】", "`tension_pressure_contract`"),
        ("rhythm_pacing_engine", "【节奏调度合同】", "`rhythm_pacing_contract`"),
        ("callback_motif_engine", "【回环母题合同】", "`callback_motif_contract`"),
        ("world_texture_engine", "【世界质感合同】", "`world_texture_contract`"),
    ),
)
def test_selected_second_batch_skills_expand_only_when_selected(
    skill_key: str,
    heading: str,
    contract_name: str,
) -> None:
    catalog = resolve_story_effect_skill_catalog("都市神仙", "神仙招聘")
    metadata = {STORY_EFFECT_SKILL_CATALOG_METADATA_KEY: catalog.to_metadata()}

    assert heading not in render_selected_story_effect_skill_contracts(
        metadata,
        language="zh-CN",
    )

    selected_metadata = {
        **metadata,
        STORY_EFFECT_SKILL_SELECTION_METADATA_KEY: {
            "chapter_outline": {
                "primary": skill_key,
            }
        },
    }

    block = render_selected_story_effect_skill_contracts(
        selected_metadata,
        language="zh-CN",
    )
    assert heading in block
    assert contract_name in block
    assert "【脑洞生成合同】" not in block


def test_generic_second_batch_skills_expand_when_explicitly_selected() -> None:
    """Effects WITHOUT a dedicated renderer (romance/twist/wonder/danger) now also
    expand into a generic per-skill contract when explicitly selected.

    This was previously asserted to stay empty, encoding the old behaviour where
    only the four dedicated-renderer skills expanded. That premise was deliberately
    overturned by the "all 18 skills render a non-empty contract" change (see
    ``test_story_enhancer_contracts.test_all_18_skills_render_a_nonempty_contract``):
    a selected effect must actually deliver a contract, otherwise picking it does
    nothing and the prose stays bland. This test now pins the new truth at the
    selection-renderer level (mirroring the single-skill renderer guarantee).
    """
    catalog = resolve_story_effect_skill_catalog("都市神仙", "神仙招聘")
    metadata = {
        STORY_EFFECT_SKILL_CATALOG_METADATA_KEY: catalog.to_metadata(),
        STORY_EFFECT_SKILL_SELECTION_METADATA_KEY: {
            "chapter_outline": {
                "primary": "romance_tenderness_engine",
                "secondary": "twist_reversal_engine",
                "additional": ["wonder_awe_engine", "danger_action_engine"],
            }
        },
    }

    block = render_selected_story_effect_skill_contracts(metadata, language="zh-CN")

    assert block != ""
    # The explicitly-selected primary/secondary effects each cash a contract.
    assert "romance_tenderness_engine" in block
    assert "twist_reversal_engine" in block


def test_build_project_metadata_adds_story_effect_catalog_without_overriding_input() -> None:
    payload = ProjectCreate(
        slug="effect-catalog-book",
        title="Effect Catalog Book",
        genre="都市神仙",
        sub_genre="神仙招聘",
        target_word_count=120000,
        target_chapters=60,
        metadata={
            STORY_EFFECT_SKILL_CATALOG_METADATA_KEY: {
                "version": STORY_EFFECT_SKILL_CATALOG_VERSION,
                "catalog_key": "custom-effect-catalog",
                "genre": "custom",
                "skills": [],
            }
        },
    )
    writing_profile = resolve_project_create_writing_profile(payload)

    metadata = build_project_metadata(payload, writing_profile)

    assert metadata[STORY_EFFECT_SKILL_CATALOG_METADATA_KEY]["catalog_key"] == (
        "custom-effect-catalog"
    )


def test_catalog_round_trip_from_new_project_metadata() -> None:
    payload = ProjectCreate(
        slug="effect-catalog-roundtrip",
        title="Effect Catalog Roundtrip",
        genre="都市神仙",
        sub_genre="神仙招聘",
        target_word_count=120000,
        target_chapters=60,
    )
    writing_profile = resolve_project_create_writing_profile(payload)

    metadata = build_project_metadata(payload, writing_profile)
    catalog = story_effect_skill_catalog_from_metadata(metadata)

    assert catalog is not None
    assert catalog.catalog_key == "mythic-workplace-effect-skills"
    assert any(skill.skill_key == "brainhole_engine" for skill in catalog.skills)


@pytest.mark.unit
def test_cultivation_not_routed_to_mythic_workplace_effect_skills() -> None:
    """Twin of brainhole "仙" bug: bare 仙/修仙/仙侠 must NOT get the mythic-workplace
    (HR/comedy) effect-skills catalog, else cultivation books homogenize toward 债契/记账/HR."""
    for genre, sub in (("仙侠", "升级流"), ("修仙", "宗门经营"), ("仙侠升级", "宗门逆袭")):
        catalog = resolve_story_effect_skill_catalog(genre, sub)
        assert catalog.catalog_key != "mythic-workplace-effect-skills", (
            f"{genre}/{sub} 被误路由进 workplace/HR effect-skills"
        )
        assert catalog.catalog_key == "general-serial-effect-skills"
    # legit 招神/神仙HR still routes correctly
    assert resolve_story_effect_skill_catalog("都市神仙", "神仙招聘").catalog_key == (
        "mythic-workplace-effect-skills"
    )
