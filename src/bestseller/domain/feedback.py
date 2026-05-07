from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class CharacterStateExtraction(BaseModel):
    """Extracted character state changes from chapter prose."""

    character_name: str
    emotional_state: str | None = None
    arc_state: str | None = None
    power_tier: str | None = None
    physical_state: str | None = None
    alive_status: str | None = None  # alive | injured | dying | deceased
    stance: str | None = None  # ally | enemy | neutral | conflicted | protagonist | rival
    stance_change_reason: str | None = None
    power_tier_downgrade_reason: str | None = None
    # Non-deceased offstage lifecycle states — written to
    # metadata_json.lifecycle_status (kind must be in
    # missing|sealed|sleeping|comatose|exiled).  A reason is required
    # or the field is silently dropped.
    lifecycle_status: str | None = None  # missing|sealed|sleeping|comatose|exiled
    lifecycle_status_reason: str | None = None  # one-sentence triggering event
    lifecycle_exit_chapter: int | None = None  # planned chapter when state ends
    beliefs_gained: list[str] = Field(default_factory=list)
    beliefs_invalidated: list[str] = Field(default_factory=list)
    knowledge_gained: list[str] = Field(default_factory=list)
    trust_changes: dict[str, str] = Field(default_factory=dict)


class RelationshipEventExtraction(BaseModel):
    """Extracted relationship event from chapter prose."""

    character_a: str
    character_b: str
    event_description: str
    relationship_change: str
    is_milestone: bool = False


class ArcBeatUpdateExtraction(BaseModel):
    """Observed arc beat completion or progression."""

    arc_code: str
    beat_order: int
    status: str = Field(min_length=1, max_length=32)  # completed | in_progress | failed
    evidence: str = ""


class ClueObservationExtraction(BaseModel):
    """Observed clue planting or payoff delivery."""

    clue_code: str
    action: str = Field(min_length=1, max_length=16)  # planted | paid_off
    evidence: str = ""


class WorldDetailExtraction(BaseModel):
    """New world detail revealed in prose."""

    entity_type: str = Field(min_length=1, max_length=32)  # location | rule | faction
    name: str
    detail: str


class CanonFactExtraction(BaseModel):
    """New canon fact established in prose."""

    subject: str
    predicate: str
    value: str
    fact_type: str = "extracted"


class PromiseMadeExtraction(BaseModel):
    """A new promise / oath / vow / debt established in the prose.

    Only "newly made" promises are extracted here.  Resolution
    (fulfilled / broken) is detected separately.
    """

    promisor: str  # name of the character making the promise
    promisee: str  # name of the character receiving the promise
    content: str   # what exactly was promised (one sentence)
    kind: str | None = None  # revenge|protection|message|fealty|debt|quest|deathbed
    due_chapter: int | None = None  # planned resolution chapter, if the prose mentions one


class ChapterFeedbackPayload(BaseModel):
    """Raw LLM extraction result before applying to DB."""

    character_states: list[CharacterStateExtraction] = Field(default_factory=list)
    relationship_events: list[RelationshipEventExtraction] = Field(default_factory=list)
    arc_beat_updates: list[ArcBeatUpdateExtraction] = Field(default_factory=list)
    clue_observations: list[ClueObservationExtraction] = Field(default_factory=list)
    world_details: list[WorldDetailExtraction] = Field(default_factory=list)
    canon_facts: list[CanonFactExtraction] = Field(default_factory=list)
    promises_made: list[PromiseMadeExtraction] = Field(default_factory=list)


class ChapterFeedbackResult(BaseModel):
    """Summary of what was extracted and applied from a chapter."""

    project_id: UUID
    chapter_id: UUID
    chapter_number: int
    character_states_updated: int = 0
    relationship_events_created: int = 0
    arc_beats_updated: int = 0
    clue_observations_applied: int = 0
    world_details_enriched: int = 0
    canon_facts_created: int = 0
    promises_created: int = 0
    extraction_status: str = "ok"  # ok | empty | parse_error | llm_error
    llm_run_id: UUID | None = None
