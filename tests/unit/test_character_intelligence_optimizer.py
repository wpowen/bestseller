from __future__ import annotations

from uuid import uuid4

import pytest

from bestseller.infra.db.models import CharacterModel, ProjectModel
from bestseller.services.character_intelligence.optimizer import (
    CHARACTER_INTELLIGENCE_PROFILE_VERSION,
    optimize_project_character_profiles,
)

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


def build_project() -> ProjectModel:
    project = ProjectModel(
        slug="legacy-book",
        title="Legacy Book",
        genre="science-fantasy",
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


@pytest.mark.asyncio
async def test_optimize_project_character_profiles_skips_after_project_marker() -> None:
    project = build_project()
    character = build_legacy_character(project.id)
    session = FakeSession([character])

    await optimize_project_character_profiles(session, project)
    second = await optimize_project_character_profiles(session, project)

    assert second["projects_skipped_current"] == 1
    assert session.scalars_calls == 1


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
    original_meta = dict(character.metadata_json)
    session = FakeSession([character])

    counts = await optimize_project_character_profiles(session, project, dry_run=True)

    assert counts["profiles_optimized"] == 1
    assert session.flush_calls == 0
    assert character.metadata_json == original_meta
    assert "character_profile_optimization" not in project.metadata_json
