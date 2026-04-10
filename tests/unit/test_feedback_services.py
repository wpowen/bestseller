from __future__ import annotations

import json
from uuid import uuid4

import pytest

from bestseller.domain.feedback import (
    ArcBeatUpdateExtraction,
    CanonFactExtraction,
    ChapterFeedbackPayload,
    ChapterFeedbackResult,
    CharacterStateExtraction,
    ClueObservationExtraction,
    RelationshipEventExtraction,
    WorldDetailExtraction,
)
from bestseller.services.feedback import _parse_feedback_payload

pytestmark = pytest.mark.unit


# ── _parse_feedback_payload ──────────────────────────────────────


def test_parse_feedback_payload_valid_json() -> None:
    raw = json.dumps(
        {
            "character_states": [
                {
                    "character_name": "Lin Mei",
                    "emotional_state": "determined",
                    "arc_state": "rising",
                    "power_tier": None,
                    "physical_state": "healthy",
                    "beliefs_gained": ["the prince is alive"],
                    "beliefs_invalidated": [],
                    "knowledge_gained": ["location of the artifact"],
                    "trust_changes": {"Zhang Wei": "increased"},
                }
            ],
            "relationship_events": [
                {
                    "character_a": "Lin Mei",
                    "character_b": "Zhang Wei",
                    "event_description": "Shared a meal",
                    "relationship_change": "trust increased",
                    "is_milestone": False,
                }
            ],
            "arc_beat_updates": [],
            "clue_observations": [],
            "world_details": [],
            "canon_facts": [
                {
                    "subject": "Jade Palace",
                    "predicate": "has_secret_entrance",
                    "value": "behind the waterfall",
                    "fact_type": "extracted",
                }
            ],
        },
        ensure_ascii=False,
    )

    payload = _parse_feedback_payload(raw)
    assert payload is not None
    assert len(payload.character_states) == 1
    assert payload.character_states[0].character_name == "Lin Mei"
    assert payload.character_states[0].emotional_state == "determined"
    assert payload.character_states[0].trust_changes == {"Zhang Wei": "increased"}
    assert len(payload.relationship_events) == 1
    assert payload.relationship_events[0].character_a == "Lin Mei"
    assert len(payload.canon_facts) == 1
    assert payload.canon_facts[0].subject == "Jade Palace"


def test_parse_feedback_payload_fenced_json() -> None:
    inner = json.dumps(
        {
            "character_states": [],
            "relationship_events": [],
            "arc_beat_updates": [
                {
                    "arc_code": "ARC-REVENGE",
                    "beat_order": 3,
                    "status": "completed",
                    "evidence": "The hero confronted the villain.",
                }
            ],
            "clue_observations": [],
            "world_details": [],
            "canon_facts": [],
        }
    )
    text = f"Here is the analysis:\n```json\n{inner}\n```\nEnd of analysis."

    payload = _parse_feedback_payload(text)
    assert payload is not None
    assert len(payload.arc_beat_updates) == 1
    assert payload.arc_beat_updates[0].arc_code == "ARC-REVENGE"
    assert payload.arc_beat_updates[0].status == "completed"


def test_parse_feedback_payload_empty_response() -> None:
    payload = _parse_feedback_payload("")
    assert payload is not None
    assert payload.character_states == []
    assert payload.relationship_events == []
    assert payload.arc_beat_updates == []
    assert payload.clue_observations == []
    assert payload.world_details == []
    assert payload.canon_facts == []


def test_parse_feedback_payload_missing_keys() -> None:
    """JSON with only some keys still parses; missing keys get defaults."""
    raw = json.dumps(
        {
            "character_states": [
                {
                    "character_name": "Zhang Wei",
                    "emotional_state": "angry",
                }
            ],
            "world_details": [
                {
                    "entity_type": "location",
                    "name": "Shadow Valley",
                    "detail": "Perpetual fog covers the valley floor.",
                }
            ],
        }
    )

    payload = _parse_feedback_payload(raw)
    assert payload is not None
    assert len(payload.character_states) == 1
    assert payload.character_states[0].character_name == "Zhang Wei"
    assert payload.character_states[0].arc_state is None
    assert payload.character_states[0].beliefs_gained == []
    assert len(payload.world_details) == 1
    assert payload.world_details[0].name == "Shadow Valley"
    # Missing top-level keys default to empty lists
    assert payload.relationship_events == []
    assert payload.clue_observations == []


# ── Domain model construction ────────────────────────────────────


def test_feedback_payload_model() -> None:
    char_state = CharacterStateExtraction(
        character_name="Hero",
        emotional_state="calm",
        knowledge_gained=["secret passage exists"],
    )
    rel_event = RelationshipEventExtraction(
        character_a="Hero",
        character_b="Mentor",
        event_description="Training completed",
        relationship_change="trust deepened",
        is_milestone=True,
    )
    arc_beat = ArcBeatUpdateExtraction(
        arc_code="ARC-GROWTH",
        beat_order=2,
        status="in_progress",
        evidence="Hero passed the first trial.",
    )
    clue_obs = ClueObservationExtraction(
        clue_code="CLU-PENDANT",
        action="planted",
        evidence="The pendant glowed when near the statue.",
    )
    world_detail = WorldDetailExtraction(
        entity_type="rule",
        name="Magic Suppression Zone",
        detail="No spells function within the inner sanctum.",
    )
    canon_fact = CanonFactExtraction(
        subject="Iron Gate",
        predicate="opens_with",
        value="blood of the royal line",
    )

    payload = ChapterFeedbackPayload(
        character_states=[char_state],
        relationship_events=[rel_event],
        arc_beat_updates=[arc_beat],
        clue_observations=[clue_obs],
        world_details=[world_detail],
        canon_facts=[canon_fact],
    )

    assert len(payload.character_states) == 1
    assert payload.character_states[0].character_name == "Hero"
    assert len(payload.relationship_events) == 1
    assert payload.relationship_events[0].is_milestone is True
    assert len(payload.arc_beat_updates) == 1
    assert payload.arc_beat_updates[0].status == "in_progress"
    assert len(payload.clue_observations) == 1
    assert payload.clue_observations[0].action == "planted"
    assert len(payload.world_details) == 1
    assert len(payload.canon_facts) == 1
    assert payload.canon_facts[0].fact_type == "extracted"


def test_chapter_feedback_result_model() -> None:
    project_id = uuid4()
    chapter_id = uuid4()
    llm_run_id = uuid4()

    result = ChapterFeedbackResult(
        project_id=project_id,
        chapter_id=chapter_id,
        chapter_number=7,
        character_states_updated=3,
        relationship_events_created=1,
        arc_beats_updated=2,
        clue_observations_applied=0,
        world_details_enriched=1,
        canon_facts_created=4,
        extraction_status="ok",
        llm_run_id=llm_run_id,
    )

    assert result.project_id == project_id
    assert result.chapter_id == chapter_id
    assert result.chapter_number == 7
    assert result.character_states_updated == 3
    assert result.relationship_events_created == 1
    assert result.arc_beats_updated == 2
    assert result.clue_observations_applied == 0
    assert result.world_details_enriched == 1
    assert result.canon_facts_created == 4
    assert result.extraction_status == "ok"
    assert result.llm_run_id == llm_run_id


def test_chapter_feedback_result_defaults() -> None:
    result = ChapterFeedbackResult(
        project_id=uuid4(),
        chapter_id=uuid4(),
        chapter_number=1,
    )
    assert result.character_states_updated == 0
    assert result.relationship_events_created == 0
    assert result.arc_beats_updated == 0
    assert result.clue_observations_applied == 0
    assert result.world_details_enriched == 0
    assert result.canon_facts_created == 0
    assert result.extraction_status == "ok"
    assert result.llm_run_id is None
