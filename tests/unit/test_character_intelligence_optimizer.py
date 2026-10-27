from __future__ import annotations

from copy import deepcopy
from uuid import uuid4

import pytest

from bestseller.infra.db.models import CharacterModel, ProjectModel
from bestseller.services.character_intelligence.optimizer import (
    CHARACTER_INTELLIGENCE_PROFILE_VERSION,
    optimize_project_character_profiles,
    validated_character_engine_profile,
)
from bestseller.services.character_intelligence.strategy import (
    AdoptedReferenceUnavailable,
    adopt_reference_projection,
)
from bestseller.services.prompt_source_policy import stamp_reference_cache, valid_reference_value

pytestmark = pytest.mark.unit


class FakeSession:
    def __init__(self, rows: list[object]) -> None:
        self.rows = rows
        self.scalars_calls = 0
        self.flush_calls = 0

    async def scalars(self, stmt: object) -> list[object]:
        self.scalars_calls += 1
        return self.rows

    async def flush(self) -> None:
        self.flush_calls += 1


def build_project(*, receipted: bool = True, adopted: bool = True) -> ProjectModel:
    project = ProjectModel(
        slug="legacy-book",
        title="Legacy Book",
        genre="science-fantasy",
        sub_genre="cross-system",
        language="zh-CN",
        audience="general",
        reader_contract_json={"core_fantasy": "通过主动选择解决身份与关系困境"},
        dramatic_question="如何找回航线证据并承担身份责任？",
        theme_statement="信任来自有代价的选择",
        target_word_count=100000,
        target_chapters=80,
        metadata_json={
            "character_strategy": {
                "required_axes": ["agency", "identity_pressure", "relationship_debt"],
                "state_variables": ["knowledge_asymmetry", "identity_debt"],
                "reader_reward_contracts": ["身份选择必须产生可见代价"],
                "agency_policy": {
                    "must_act_within_chapters": 3,
                    "default_problem_solving_modes": ["knowledge_application"],
                    "choice_with_cost_required": True,
                },
                "identity_pressure": {
                    "required_external_pressure": True,
                    "choice_axis": "predecessor_loyalty vs self_determination",
                    "debt_sources": ["宿主身份债"],
                },
                "relationship_policy": {
                    "reciprocal_commitment_required": True,
                    "track_axes": ["group_commitment"],
                },
            }
        },
    )
    project.id = uuid4()
    if receipted:
        stamp_reference_cache(project, ("character_strategy",), producer="strategy_compiler")
    if adopted:
        adopt_reference_projection(project, "character_strategy", adopted_by="fixture-author")
    return project


def build_legacy_character(project_id) -> CharacterModel:
    character = CharacterModel(
        id=uuid4(),
        project_id=project_id,
        name="沈砚",
        role="protagonist",
        goal="找回被篡改的航线证据",
        fear="再次害死搭档",
        secret="继承了原身未清的航道债",
        voice_profile_json={
            "verbal_tics": ["证据先说话"],
            "response_pattern_to_question": "先追问证据链再给结论",
        },
        metadata_json={
            "cast_entry": {
                "name": "沈砚",
                "role": "protagonist",
                "goal": "找回被篡改的航线证据",
                "relationships": [
                    {"character": "顾临", "type": "ally", "tension": "旧误会未解"}
                ],
            },
            "character_engine_profile": {
                "source": "cast_spec_fusion",
                "display_name": "沈砚",
                "voice_dna": {"signature_words": ["证据先说话"]},
            },
        },
    )
    return character


@pytest.mark.asyncio
async def test_optimize_project_character_profiles_enriches_existing_metadata_only() -> None:
    project = build_project()
    character = build_legacy_character(project.id)
    session = FakeSession([character])

    counts = await optimize_project_character_profiles(session, project)

    profile = character.metadata_json["character_engine_profile"]
    assert counts["profiles_optimized"] == 1
    assert counts["legacy_profiles_preserved"] == 1
    assert session.flush_calls == 1
    assert project.metadata_json["character_profile_optimization"]["scope"] == (
        "character_metadata_only"
    )
    assert character.metadata_json["character_engine_profile_legacy"]["display_name"] == "沈砚"
    assert profile["character_intelligence_version"] == CHARACTER_INTELLIGENCE_PROFILE_VERSION
    assert profile["strategy_source"] == "distillation_character_intelligence"
    assert profile["agency_policy"]["must_act_within_chapters"] == 3
    assert profile["identity_pressure"]["choice_axis"] == (
        "predecessor_loyalty vs self_determination"
    )
    assert profile["relationship_debt"]["active_relationships"][0]["target_id"] == "顾临"
    assert character.metadata_json["character_profile_optimization"][
        "preserved_existing_book_content"
    ] is True
    catalog = valid_reference_value(project, "character_engine_profiles")
    assert set(catalog) == {str(character.id)}
    assert validated_character_engine_profile(
        project, character.id, profile, character=character,
    ) == profile


@pytest.mark.asyncio
async def test_optimize_project_character_profiles_skips_after_project_marker() -> None:
    project = build_project()
    character = build_legacy_character(project.id)
    session = FakeSession([character])

    await optimize_project_character_profiles(session, project)
    first_project_meta = deepcopy(project.metadata_json)
    first_character_meta = deepcopy(character.metadata_json)
    writes_after_first = session.flush_calls
    second = await optimize_project_character_profiles(session, project)

    assert second["projects_skipped_current"] == 1
    assert second["profiles_optimized"] == 0
    assert second["projects_optimized"] == 0
    assert writes_after_first == 1
    assert session.flush_calls == writes_after_first
    assert project.metadata_json == first_project_meta
    assert character.metadata_json == first_character_meta
    assert validated_character_engine_profile(
        project, character.id, character.metadata_json["character_engine_profile"],
        character=character,
    ) == character.metadata_json["character_engine_profile"]


@pytest.mark.asyncio
@pytest.mark.parametrize("invalidated", ["missing_catalog_receipt", "changed_profile", "changed_character"])
async def test_project_marker_does_not_skip_invalidated_character_profile(invalidated: str) -> None:
    project = build_project()
    character = build_legacy_character(project.id)
    session = FakeSession([character])
    await optimize_project_character_profiles(session, project)
    writes_after_first = session.flush_calls
    if invalidated == "missing_catalog_receipt":
        project.metadata_json["prompt_reference_receipts"].pop("character_engine_profiles")
    elif invalidated == "changed_profile":
        character.metadata_json["character_engine_profile"]["agency_policy"]["must_act_within_chapters"] = 999
    else:
        character.goal = "保护已经公开作证的搭档"
        character.metadata_json["cast_entry"]["goal"] = character.goal
    assert validated_character_engine_profile(
        project, character.id, character.metadata_json["character_engine_profile"],
        character=character,
    ) is None

    repaired = await optimize_project_character_profiles(session, project)

    assert repaired["projects_skipped_current"] == 0
    assert repaired["profiles_optimized"] == 1
    assert repaired["projects_optimized"] == 1
    assert session.flush_calls == writes_after_first + 1
    profile = character.metadata_json["character_engine_profile"]
    assert profile["agency_policy"]["must_act_within_chapters"] == 3
    assert validated_character_engine_profile(
        project, character.id, profile, character=character,
    ) == profile
    repaired_project_meta = deepcopy(project.metadata_json)
    repaired_character_meta = deepcopy(character.metadata_json)

    repeated = await optimize_project_character_profiles(session, project)

    assert repeated["projects_skipped_current"] == 1
    assert repeated["profiles_optimized"] == 0
    assert session.flush_calls == writes_after_first + 1
    assert project.metadata_json == repaired_project_meta
    assert character.metadata_json == repaired_character_meta


@pytest.mark.asyncio
async def test_optimize_project_character_profiles_can_build_from_row_without_cast_entry() -> None:
    project = build_project()
    character = CharacterModel(
        id=uuid4(),
        project_id=project.id,
        name="祁镇",
        role="antagonist",
        goal="维持航道署记录权",
        fear="秩序崩塌",
        metadata_json={},
    )
    session = FakeSession([character])

    counts = await optimize_project_character_profiles(session, project)

    profile = character.metadata_json["character_engine_profile"]
    assert counts["profiles_optimized"] == 1
    assert profile["display_name"] == "祁镇"
    assert profile["role"] == "antagonist"
    assert profile["antagonist_misread_hooks"]["role_binding"] == (
        "must visibly recalculate after protagonist action"
    )


@pytest.mark.asyncio
async def test_optimize_project_character_profiles_dry_run_does_not_mutate_rows() -> None:
    project = build_project()
    character = build_legacy_character(project.id)
    original_meta = deepcopy(character.metadata_json)
    original_project_meta = deepcopy(project.metadata_json)
    session = FakeSession([character])

    counts = await optimize_project_character_profiles(session, project, dry_run=True)

    assert counts["profiles_optimized"] == 1
    assert session.flush_calls == 0
    assert character.metadata_json == original_meta
    assert project.metadata_json == original_project_meta
    assert "character_profile_optimization" not in project.metadata_json


@pytest.mark.asyncio
@pytest.mark.parametrize("receipted", [False, True])
async def test_optional_character_strategy_does_not_authorize_profile_rewrite(receipted: bool) -> None:
    project = build_project(receipted=receipted, adopted=False)
    character = build_legacy_character(project.id)
    before_project = deepcopy(project.metadata_json)
    before_character = deepcopy(character.metadata_json)
    session = FakeSession([character])

    counts = await optimize_project_character_profiles(session, project)

    assert counts["profiles_optimized"] == 0
    assert counts["projects_optimized"] == 0
    assert counts["profiles_without_strategy"] == 1
    assert session.flush_calls == 0
    assert project.metadata_json == before_project
    assert character.metadata_json == before_character


@pytest.mark.asyncio
async def test_required_character_strategy_without_current_receipt_blocks_rewrite() -> None:
    project = build_project()
    adopt_reference_projection(project, "character_strategy", adopted_by="fixture-author", required=True)
    project.metadata_json["prompt_reference_receipts"].pop("character_strategy")
    character = build_legacy_character(project.id)
    before_project = deepcopy(project.metadata_json)
    before_character = deepcopy(character.metadata_json)
    session = FakeSession([character])

    with pytest.raises(AdoptedReferenceUnavailable):
        await optimize_project_character_profiles(session, project)

    assert session.flush_calls == 0
    assert project.metadata_json == before_project
    assert character.metadata_json == before_character
