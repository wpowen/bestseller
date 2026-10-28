"""F0-01/05/06/07 functional counterexamples; DB IO is a component fake only."""

from types import SimpleNamespace

import pytest
from test_interpersonal_promises import _FakeSession as PromiseSession
from test_interpersonal_promises import _Promise
from test_memory_recall import _Char, _Rel
from test_memory_recall import _FakeSession as RecallSession
from test_world_expansion_services import build_project

from bestseller.infra.db import models as m
from bestseller.services import (
    character_evolution,
    interpersonal_promises,
    memory_recall,
    world_expansion,
)

from .f0_ordering_support import RowsSession, uid

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@pytest.mark.parametrize("count", [3, 13])
@pytest.mark.parametrize("due", [None, 60])
async def test_f0_01_equal_urgency_membership_and_order_survive_reversed_read(count, due):
    rows = [_Promise(id=uid(i), project_id=uid(500), promisor_label=f"speaker-{i}",
                     promisee_label="listener", content=f"promise-{i}", due_chapter_number=due)
            for i in range(1, count + 1)]
    outputs = []
    for arrival in (rows, list(reversed(rows))):
        result = await interpersonal_promises.active_promises_for_chapter(
            PromiseSession(promises=arrival), project_id=uid(500), chapter_number=50)
        outputs.append([row.id for row in result])
    assert outputs[0] == outputs[1]
    assert outputs[0] == [uid(i) for i in range(1, min(count, 12) + 1)]


async def test_f0_01_deadline_priority_and_stale_filter_precede_cap():
    rows = [_Promise(id=uid(i), project_id=uid(500), promisor_label=label,
                     promisee_label="listener", content=label, due_chapter_number=due)
            for i, label, due in [(1, "open", None), (2, "future", 51),
                                  (80, "one-overdue", 49), (90, "five-overdue", 45),
                                  (99, "expired", 1)]]
    for arrival in (rows, list(reversed(rows))):
        result = await interpersonal_promises.active_promises_for_chapter(
            PromiseSession(promises=arrival), project_id=uid(500), chapter_number=50, limit=4)
        assert [row.content for row in result] == ["five-overdue", "one-overdue", "future", "open"]


async def test_f0_05_outer_character_roster_is_stable(monkeypatch):
    project_id = uid(500)
    characters = [m.CharacterModel(id=uid(i), project_id=project_id, name=f"observer-{i}",
                                  role="support", metadata_json={}) for i in range(1, 4)]

    async def snapshot_read(*args, **kwargs):
        # IO boundary only: no promoted/adopted/grade flags are synthesized.
        snapshot_number = 1000 + (kwargs["character_id"].int & 0xFFF) * 2
        return [SimpleNamespace(id=uid(snapshot_number + 1), chapter_number=5, arc_state="waiting",
                                emotional_state="calm", physical_state="rested", power_tier="low"),
                SimpleNamespace(id=uid(snapshot_number), chapter_number=4, arc_state="waiting",
                                emotional_state="calm", physical_state="rested", power_tier="low")]

    monkeypatch.setattr(character_evolution, "load_eligible_character_snapshots", snapshot_read)
    outputs = []
    for arrival in (characters, list(reversed(characters))):
        warnings = await character_evolution.detect_character_stagnation(
            RowsSession({m.CharacterModel: arrival}), project_id, 50)
        outputs.append([warning.character_name for warning in warnings])
    assert outputs == [["observer-1", "observer-2", "observer-3"]] * 2


def _recall_case(count):
    deceased = _Char(id=uid(100), name="Remembered", alive_status="deceased", death_chapter_number=100)
    survivors = [_Char(id=uid(200 + i), name=f"survivor-{i}") for i in range(1, count + 1)]
    relations = []
    for i, survivor in enumerate(survivors, 1):
        relation = _Rel(deceased.id, survivor.id, "ally", 0.9)
        relation.id = uid(i)  # Existing fake lacked the native persistent relation ID.
        relations.append(relation)
    return deceased, survivors, relations


@pytest.mark.parametrize("count", [3, 5])
async def test_f0_06_equal_memory_rank_is_stable_before_default_four_cue_cap(count):
    deceased, survivors, relations = _recall_case(count)
    characters = {row.id: row for row in [deceased, *survivors]}
    outputs = []
    for arrival in (relations, list(reversed(relations))):
        result = await memory_recall.compute_memory_recall_cues(
            RecallSession(deceased_rows=[deceased], rels_by_deceased={deceased.id: arrival},
                          characters_by_id=characters), project_id=uid(500), chapter_number=103)
        outputs.append([cue.survivor_name for cue in result])
    assert outputs[0] == outputs[1] == [f"survivor-{i}" for i in range(1, min(count, 4) + 1)]


async def test_f0_06_anchor_distance_then_strength_outrank_uuid():
    near = _Char(id=uid(90), name="Near", alive_status="deceased", death_chapter_number=100)
    farther = _Char(id=uid(1), name="Farther", alive_status="deceased", death_chapter_number=99)
    survivors = [_Char(id=uid(200 + i), name=name) for i, name in enumerate(["strong-near", "weak-near", "farther"])]
    strong, weak, far = [_Rel(dead.id, survivor.id, "ally", strength)
                         for dead, survivor, strength in [(near, survivors[0], 0.9),
                                                          (near, survivors[1], 0.7),
                                                          (farther, survivors[2], 1.0)]]
    strong.id, weak.id, far.id = uid(99), uid(2), uid(1)
    people = {row.id: row for row in [near, farther, *survivors]}
    for deaths in ([near, farther], [farther, near]):
        result = await memory_recall.compute_memory_recall_cues(
            RecallSession(deceased_rows=deaths, rels_by_deceased={near.id: [weak, strong], farther.id: [far]},
                          characters_by_id=people), project_id=uid(500), chapter_number=103, max_cues=2)
        assert [cue.survivor_name for cue in result] == ["strong-near", "weak-near"]


async def test_f0_07_tied_next_gate_is_one_stable_row():
    project = build_project()
    gates = [m.ExpansionGateModel(id=uid(i), project_id=project.id, gate_code=f"gate-{i}",
                                 label=f"gate-{i}", condition_summary="condition", unlocks_summary="new place",
                                 unlock_chapter_number=60, unlock_volume_number=3, metadata_json={})
             for i in (1, 2)]
    for arrival in (gates, list(reversed(gates))):
        result = await world_expansion.load_world_expansion_context(
            RowsSession({m.ExpansionGateModel: arrival}), project=project, volume_number=None, chapter_number=50)
        assert result["next_expansion_gate"]["gate_code"] == "gate-1"


async def test_f0_07_unlock_chapter_then_volume_precede_id():
    project = build_project()
    gates = [m.ExpansionGateModel(id=uid(i), project_id=project.id, gate_code=code,
                                 label=code, condition_summary="condition", unlocks_summary="new place",
                                 unlock_chapter_number=chapter, unlock_volume_number=volume, metadata_json={})
             for i, code, chapter, volume in [(1, "later-chapter", 61, 1),
                                               (2, "later-volume", 60, 4), (99, "earliest", 60, 3)]]
    for arrival in (gates, list(reversed(gates))):
        result = await world_expansion.load_world_expansion_context(
            RowsSession({m.ExpansionGateModel: arrival}), project=project, volume_number=None, chapter_number=50)
        assert result["next_expansion_gate"]["gate_code"] == "earliest"
