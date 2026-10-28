"""F0-02/03/04: actual Bible reader with IO rows, never a grade/adoption fixture."""

import pytest
from test_story_bible_services import build_project

from bestseller.infra.db import models as m
from bestseller.services import story_bible

from .f0_ordering_support import RowsSession, uid

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


def _scope(project, participants=()):
    chapter = m.ChapterModel(id=uid(800), project_id=project.id, chapter_number=60,
                             chapter_goal="remember the past", metadata_json={}, volume_id=None)
    scene = m.SceneCardModel(id=uid(801), project_id=project.id, chapter_id=chapter.id,
                            scene_number=1, participants=list(participants), metadata_json={})
    return chapter, scene


def _character(project, number, **changes):
    values = dict(id=uid(number), project_id=project.id, name=f"person-{number}", role="support",
                  alive_status="alive", metadata_json={}, knowledge_state_json={})
    values.update(changes)
    return m.CharacterModel(**values)


async def _read(project, characters, *, relationships=(), participants=()):
    chapter, scene = _scope(project, participants)
    return await story_bible.load_scene_story_bible_context(
        RowsSession({m.CharacterModel: characters, m.RelationshipModel: relationships}),
        project=project, chapter=chapter, scene=scene)


async def test_f0_02_relationship_projection_order_is_stable_without_reordering_authored_cast():
    project = build_project()
    authored = ["C", "A", "B"]
    characters = [_character(project, 100 + i, name=name,
                              id=story_bible.stable_character_id(project.id, name))
                  for i, name in enumerate(authored)]
    by_name = {character.name: character for character in characters}
    relations = [m.RelationshipModel(
        id=uid(i), project_id=project.id, character_a_id=by_name[a].id, character_b_id=by_name[b].id,
        relationship_type=f"relation-{i}", strength=0.8, tension_summary=f"tension-{i}",
        public_face=f"public-{i}", private_reality=f"private-{i}", metadata_json={})
        for i, a, b in [(1, "A", "B"), (2, "A", "C"), (3, "B", "C")]]
    outputs = []
    for arrival in (relations, list(reversed(relations))):
        result = await _read(project, characters, relationships=arrival, participants=authored)
        assert [row["name"] for row in result["participants"]] == authored
        outputs.append(result["relationships"])
    assert outputs[0] == outputs[1]
    assert [row["relationship_type"] for row in outputs[0]] == ["relation-1", "relation-2", "relation-3"]


@pytest.mark.parametrize("count", [3, 21])
async def test_f0_03_deceased_ties_are_stable_before_twenty_row_cap(count):
    project = build_project()
    rows = [_character(project, i, alive_status="deceased", death_chapter_number=50)
            for i in range(1, count + 1)]
    outputs = []
    for arrival in (rows, list(reversed(rows))):
        result = await _read(project, arrival)
        outputs.append([row["name"] for row in result["deceased_characters"]])
    assert outputs[0] == outputs[1] == [f"person-{i}" for i in range(1, min(count, 20) + 1)]


async def test_f0_03_deceased_recency_and_nulls_last_outrank_uuid():
    project = build_project()
    rows = [_character(project, 1, alive_status="deceased", death_chapter_number=5),
            _character(project, 2, alive_status="deceased", death_chapter_number=None),
            _character(project, 90, alive_status="deceased", death_chapter_number=59)]
    for arrival in (rows, list(reversed(rows))):
        result = await _read(project, arrival)
        # Native lifecycle logic excludes an unknown death chapter after the
        # SQL cap. Do not manufacture a NULL-death "ordering RED" for that row.
        assert [row["name"] for row in result["deceased_characters"]] == ["person-90", "person-1"]


@pytest.mark.parametrize("count", [3, 21])
async def test_f0_03_protected_ties_are_stable_before_twenty_row_cap(count):
    project = build_project()
    rows = [_character(project, i, death_chapter_number=80) for i in range(1, count + 1)]
    outputs = []
    for arrival in (rows, list(reversed(rows))):
        result = await _read(project, arrival)
        outputs.append([row["name"] for row in result["protected_characters"]])
    assert outputs[0] == outputs[1] == [f"person-{i}" for i in range(1, min(count, 20) + 1)]


async def test_f0_03_nearer_planned_death_precedes_smaller_uuid():
    project = build_project()
    rows = [_character(project, 1, death_chapter_number=80),
            _character(project, 90, death_chapter_number=61)]
    for arrival in (rows, list(reversed(rows))):
        result = await _read(project, arrival)
        assert [row["name"] for row in result["protected_characters"]] == ["person-90", "person-1"]


async def test_f0_04_restricted_filter_preserves_stable_native_row_order():
    project = build_project()
    rows = [_character(project, i, metadata_json={"lifecycle_status": {
        "kind": kind, "since_chapter": 2}})
        for i, kind in [(1, "missing"), (2, "sealed"), (3, "sleeping")]]
    rows.insert(1, _character(project, 99))  # Alive non-restricted row must remain absent.
    outputs = []
    for arrival in (rows, list(reversed(rows))):
        result = await _read(project, arrival)
        outputs.append([(row["name"], row["kind"]) for row in result["restricted_characters"]])
    assert outputs[0] == outputs[1] == [("person-1", "missing"), ("person-2", "sealed"), ("person-3", "sleeping")]
