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
    PromiseMadeExtraction,
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


# ── Lifecycle status extraction ──────────────────────────────────


def test_character_state_extraction_accepts_lifecycle_fields() -> None:
    """lifecycle_status + reason + exit_chapter should all parse."""
    state = CharacterStateExtraction(
        character_name="Shen Yan",
        lifecycle_status="missing",
        lifecycle_status_reason="Fell into the Void Rift in chapter 12",
        lifecycle_exit_chapter=25,
    )
    assert state.lifecycle_status == "missing"
    assert state.lifecycle_status_reason == "Fell into the Void Rift in chapter 12"
    assert state.lifecycle_exit_chapter == 25


def test_character_state_extraction_defaults_lifecycle_fields_to_none() -> None:
    """Lifecycle fields must default to None for backward compatibility."""
    state = CharacterStateExtraction(character_name="Test")
    assert state.lifecycle_status is None
    assert state.lifecycle_status_reason is None
    assert state.lifecycle_exit_chapter is None


def test_parse_feedback_payload_parses_lifecycle_status() -> None:
    """_parse_feedback_payload should round-trip lifecycle_status fields."""
    import json

    raw = json.dumps(
        {
            "character_states": [
                {
                    "character_name": "Shen Yan",
                    "lifecycle_status": "sealed",
                    "lifecycle_status_reason": "Imprisoned in the Blood Formation at chapter end",
                    "lifecycle_exit_chapter": 30,
                }
            ],
            "relationship_events": [],
            "arc_beat_updates": [],
            "clue_observations": [],
            "world_details": [],
            "canon_facts": [],
        }
    )

    payload = _parse_feedback_payload(raw)
    assert payload is not None
    assert len(payload.character_states) == 1
    cs = payload.character_states[0]
    assert cs.lifecycle_status == "sealed"
    assert cs.lifecycle_status_reason == "Imprisoned in the Blood Formation at chapter end"
    assert cs.lifecycle_exit_chapter == 30


def test_parse_feedback_payload_lifecycle_status_null_is_none() -> None:
    """lifecycle_status=null should parse as Python None."""
    import json

    raw = json.dumps(
        {
            "character_states": [
                {
                    "character_name": "Shen Yan",
                    "lifecycle_status": None,
                    "lifecycle_status_reason": None,
                    "lifecycle_exit_chapter": None,
                }
            ],
            "relationship_events": [],
            "arc_beat_updates": [],
            "clue_observations": [],
            "world_details": [],
            "canon_facts": [],
        }
    )

    payload = _parse_feedback_payload(raw)
    assert payload is not None
    cs = payload.character_states[0]
    assert cs.lifecycle_status is None
    assert cs.lifecycle_status_reason is None
    assert cs.lifecycle_exit_chapter is None


def test_feedback_extraction_schema_includes_lifecycle_fields() -> None:
    """The _OUTPUT_SCHEMA constant must advertise all three lifecycle fields."""
    from bestseller.services.feedback import _OUTPUT_SCHEMA

    assert "lifecycle_status" in _OUTPUT_SCHEMA
    assert "lifecycle_status_reason" in _OUTPUT_SCHEMA
    assert "lifecycle_exit_chapter" in _OUTPUT_SCHEMA


def test_system_prompt_zh_mentions_lifecycle_kinds() -> None:
    """Chinese system prompt must list all non-deceased offstage lifecycle kinds."""
    from bestseller.services.feedback import _SYSTEM_PROMPT_ZH

    for kind in ("missing", "sealed", "sleeping", "comatose", "exiled"):
        assert kind in _SYSTEM_PROMPT_ZH, f"_SYSTEM_PROMPT_ZH missing lifecycle kind: {kind}"


def test_system_prompt_en_mentions_lifecycle_kinds() -> None:
    """English system prompt must list all non-deceased offstage lifecycle kinds."""
    from bestseller.services.feedback import _SYSTEM_PROMPT_EN

    for kind in ("missing", "sealed", "sleeping", "comatose", "exiled"):
        assert kind in _SYSTEM_PROMPT_EN, f"_SYSTEM_PROMPT_EN missing lifecycle kind: {kind}"


# ── Promise extraction ───────────────────────────────────────────


def test_promise_made_extraction_accepts_all_fields() -> None:
    """PromiseMadeExtraction must parse all documented fields."""
    promise = PromiseMadeExtraction(
        promisor="Shen Yan",
        promisee="Lin Mei",
        content="I will avenge your father's death",
        kind="revenge",
        due_chapter=45,
    )
    assert promise.promisor == "Shen Yan"
    assert promise.promisee == "Lin Mei"
    assert promise.content == "I will avenge your father's death"
    assert promise.kind == "revenge"
    assert promise.due_chapter == 45


def test_promise_made_extraction_defaults() -> None:
    """kind and due_chapter default to None."""
    promise = PromiseMadeExtraction(
        promisor="A",
        promisee="B",
        content="I'll protect you",
    )
    assert promise.kind is None
    assert promise.due_chapter is None


def test_chapter_feedback_payload_includes_promises_made() -> None:
    """ChapterFeedbackPayload must carry a promises_made list."""
    payload = ChapterFeedbackPayload()
    assert hasattr(payload, "promises_made")
    assert payload.promises_made == []


def test_chapter_feedback_result_includes_promises_created() -> None:
    """ChapterFeedbackResult must track promises_created count."""
    result = ChapterFeedbackResult(
        project_id=uuid4(),
        chapter_id=uuid4(),
        chapter_number=5,
        promises_created=3,
    )
    assert result.promises_created == 3


def test_parse_feedback_payload_parses_promises_made() -> None:
    """_parse_feedback_payload round-trips the promises_made array."""
    raw = json.dumps(
        {
            "character_states": [],
            "relationship_events": [],
            "arc_beat_updates": [],
            "clue_observations": [],
            "world_details": [],
            "canon_facts": [],
            "promises_made": [
                {
                    "promisor": "Shen Yan",
                    "promisee": "Mentor Liu",
                    "content": "I will carry on the sect's legacy",
                    "kind": "fealty",
                    "due_chapter": None,
                },
                {
                    "promisor": "Lin Mei",
                    "promisee": "Brother Wei",
                    "content": "I will deliver your message to the Emperor",
                    "kind": "message",
                    "due_chapter": 20,
                },
            ],
        }
    )

    payload = _parse_feedback_payload(raw)
    assert payload is not None
    assert len(payload.promises_made) == 2
    p0, p1 = payload.promises_made
    assert p0.promisor == "Shen Yan"
    assert p0.kind == "fealty"
    assert p0.due_chapter is None
    assert p1.promisee == "Brother Wei"
    assert p1.kind == "message"
    assert p1.due_chapter == 20


def test_parse_feedback_payload_missing_promises_key_defaults_to_empty() -> None:
    """JSON without promises_made key should still parse cleanly."""
    raw = json.dumps(
        {
            "character_states": [],
            "relationship_events": [],
            "arc_beat_updates": [],
            "clue_observations": [],
            "world_details": [],
            "canon_facts": [],
        }
    )
    payload = _parse_feedback_payload(raw)
    assert payload is not None
    assert payload.promises_made == []


def test_output_schema_includes_promises_made() -> None:
    """The _OUTPUT_SCHEMA must document the promises_made field."""
    from bestseller.services.feedback import _OUTPUT_SCHEMA

    assert "promises_made" in _OUTPUT_SCHEMA
    assert "promisor" in _OUTPUT_SCHEMA
    assert "promisee" in _OUTPUT_SCHEMA
    assert "due_chapter" in _OUTPUT_SCHEMA
