from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class WorldRuleInput(BaseModel):
    rule_id: str | None = Field(default=None, max_length=32)
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1)
    story_consequence: str | None = None
    exploitation_potential: str | None = None


class PowerSystemInput(BaseModel):
    name: str | None = None
    tiers: list[str] = Field(default_factory=list)
    acquisition_method: str | None = None
    hard_limits: str | None = None
    protagonist_starting_tier: str | None = None


class LocationInput(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: str = Field(min_length=1, max_length=200)
    location_type: str = Field(default="location", alias="type", min_length=1, max_length=100)
    atmosphere: str | None = None
    key_rules: list[str] = Field(default_factory=list)
    story_role: str | None = None


class FactionInput(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    goal: str | None = None
    method: str | None = None
    relationship_to_protagonist: str | None = None
    internal_conflict: str | None = None


class HistoryEventInput(BaseModel):
    event: str = Field(min_length=1)
    relevance: str | None = None


class WorldSpecInput(BaseModel):
    world_name: str | None = None
    world_premise: str | None = None
    rules: list[WorldRuleInput] = Field(default_factory=list)
    power_system: PowerSystemInput = Field(default_factory=PowerSystemInput)
    locations: list[LocationInput] = Field(default_factory=list)
    factions: list[FactionInput] = Field(default_factory=list)
    power_structure: str | None = None
    history_key_events: list[HistoryEventInput] = Field(default_factory=list)
    forbidden_zones: str | None = None


class CharacterRelationshipInput(BaseModel):
    character: str = Field(min_length=1, max_length=200)
    type: str = Field(min_length=1, max_length=100)
    tension: str | None = None


class CharacterKnowledgeStateInput(BaseModel):
    knows: list[str] = Field(default_factory=list)
    falsely_believes: list[str] = Field(default_factory=list)
    unaware_of: list[str] = Field(default_factory=list)


class CharacterInput(BaseModel):
    model_config = ConfigDict(extra="allow")

    name: str = Field(min_length=1, max_length=200)
    role: str = Field(default="supporting", min_length=1, max_length=64)
    age: int | None = Field(default=None, ge=0)
    background: str | None = None
    goal: str | None = None
    fear: str | None = None
    flaw: str | None = None
    strength: str | None = None
    secret: str | None = None
    arc_trajectory: str | None = None
    arc_state: str | None = None
    knowledge_state: CharacterKnowledgeStateInput = Field(default_factory=CharacterKnowledgeStateInput)
    power_tier: str | None = None
    relationships: list[CharacterRelationshipInput] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ConflictMapInput(BaseModel):
    character_a: str = Field(min_length=1, max_length=200)
    character_b: str = Field(min_length=1, max_length=200)
    conflict_type: str = Field(min_length=1, max_length=100)
    trigger_condition: str | None = None


class CastSpecInput(BaseModel):
    protagonist: CharacterInput | None = None
    antagonist: CharacterInput | None = None
    supporting_cast: list[CharacterInput] = Field(default_factory=list)
    conflict_map: list[ConflictMapInput] = Field(default_factory=list)

    @model_validator(mode="after")
    def normalize_roles(self) -> "CastSpecInput":
        if self.protagonist is not None:
            self.protagonist.role = "protagonist"
        if self.antagonist is not None:
            self.antagonist.role = "antagonist"
        return self

    def all_characters(self) -> list[CharacterInput]:
        items: list[CharacterInput] = []
        if self.protagonist is not None:
            items.append(self.protagonist)
        if self.antagonist is not None:
            items.append(self.antagonist)
        items.extend(self.supporting_cast)
        return items


class VolumePlanOpeningStateInput(BaseModel):
    protagonist_status: str | None = None
    protagonist_power_tier: str | None = None
    world_situation: str | None = None


class VolumePlanResolutionInput(BaseModel):
    protagonist_power_tier: str | None = None
    goal_achieved: bool | None = None
    cost_paid: str | None = None
    new_threat_introduced: str | None = None


class VolumePlanEntryInput(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    volume_number: int = Field(gt=0)
    volume_title: str = Field(alias="title", min_length=1, max_length=200)
    volume_theme: str | None = None
    word_count_target: float | int | str | None = None
    chapter_count_target: int | None = Field(default=None, ge=1)
    opening_state: VolumePlanOpeningStateInput = Field(default_factory=VolumePlanOpeningStateInput)
    volume_goal: str | None = None
    volume_obstacle: str | None = None
    volume_climax: str | None = None
    volume_resolution: VolumePlanResolutionInput = Field(default_factory=VolumePlanResolutionInput)
    key_reveals: list[str] = Field(default_factory=list)
    foreshadowing_planted: list[str] = Field(default_factory=list)
    foreshadowing_paid_off: list[str] = Field(default_factory=list)
    reader_hook_to_next: str | None = None


class StoryBibleMaterializationResult(BaseModel):
    workflow_run_id: UUID
    project_id: UUID
    applied_artifacts: list[str] = Field(default_factory=list)
    world_rules_upserted: int = 0
    locations_upserted: int = 0
    factions_upserted: int = 0
    characters_upserted: int = 0
    relationships_upserted: int = 0
    state_snapshots_created: int = 0
    volumes_upserted: int = 0
    world_backbones_upserted: int = 0
    volume_frontiers_upserted: int = 0
    deferred_reveals_upserted: int = 0
    expansion_gates_upserted: int = 0
    source_artifact_ids: dict[str, UUID] = Field(default_factory=dict)


class CharacterStateSnapshotRead(BaseModel):
    chapter_number: int = Field(ge=0)
    scene_number: int | None = Field(default=None, ge=0)
    arc_state: str | None = None
    emotional_state: str | None = None
    physical_state: str | None = None
    power_tier: str | None = None
    trust_map: dict[str, Any] = Field(default_factory=dict)
    beliefs: list[Any] = Field(default_factory=list)
    notes: str | None = None


class StoryBibleWorldRuleRead(BaseModel):
    rule_code: str = Field(min_length=1)
    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    story_consequence: str | None = None
    exploitation_potential: str | None = None


class StoryBibleLocationRead(BaseModel):
    name: str = Field(min_length=1)
    location_type: str = Field(min_length=1)
    atmosphere: str | None = None
    key_rule_codes: list[str] = Field(default_factory=list)
    story_role: str | None = None


class StoryBibleFactionRead(BaseModel):
    name: str = Field(min_length=1)
    goal: str | None = None
    method: str | None = None
    relationship_to_protagonist: str | None = None
    internal_conflict: str | None = None


class StoryBibleRelationshipRead(BaseModel):
    character_a: str = Field(min_length=1)
    character_b: str = Field(min_length=1)
    relationship_type: str = Field(min_length=1)
    strength: float
    public_face: str | None = None
    private_reality: str | None = None
    tension_summary: str | None = None
    established_chapter_no: int | None = None
    last_changed_chapter_no: int | None = None


class StoryBibleCharacterRead(BaseModel):
    name: str = Field(min_length=1)
    role: str = Field(min_length=1)
    goal: str | None = None
    fear: str | None = None
    flaw: str | None = None
    secret: str | None = None
    arc_trajectory: str | None = None
    arc_state: str | None = None
    power_tier: str | None = None
    is_pov_character: bool = False
    knowledge_state: dict[str, Any] = Field(default_factory=dict)
    latest_state: CharacterStateSnapshotRead | None = None


class WorldBackboneRead(BaseModel):
    title: str = Field(min_length=1)
    core_promise: str = Field(min_length=1)
    mainline_drive: str = Field(min_length=1)
    protagonist_destiny: str | None = None
    antagonist_axis: str | None = None
    thematic_melody: str | None = None
    world_frame: str | None = None
    invariant_elements: list[str] = Field(default_factory=list)
    stable_unknowns: list[str] = Field(default_factory=list)


class VolumeFrontierRead(BaseModel):
    volume_number: int = Field(ge=1)
    title: str = Field(min_length=1)
    frontier_summary: str = Field(min_length=1)
    expansion_focus: str | None = None
    start_chapter_number: int = Field(ge=1)
    end_chapter_number: int | None = Field(default=None, ge=1)
    visible_rule_codes: list[str] = Field(default_factory=list)
    active_locations: list[str] = Field(default_factory=list)
    active_factions: list[str] = Field(default_factory=list)
    active_arc_codes: list[str] = Field(default_factory=list)
    future_reveal_codes: list[str] = Field(default_factory=list)


class DeferredRevealRead(BaseModel):
    reveal_code: str = Field(min_length=1)
    label: str = Field(min_length=1)
    category: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    source_volume_number: int | None = Field(default=None, ge=1)
    reveal_volume_number: int = Field(ge=1)
    reveal_chapter_number: int = Field(ge=1)
    guard_condition: str | None = None
    status: str = Field(min_length=1)


class ExpansionGateRead(BaseModel):
    gate_code: str = Field(min_length=1)
    label: str = Field(min_length=1)
    gate_type: str = Field(min_length=1)
    condition_summary: str = Field(min_length=1)
    unlocks_summary: str = Field(min_length=1)
    source_volume_number: int | None = Field(default=None, ge=1)
    unlock_volume_number: int = Field(ge=1)
    unlock_chapter_number: int = Field(ge=1)
    status: str = Field(min_length=1)


class StoryBibleOverview(BaseModel):
    project_id: UUID
    project_slug: str = Field(min_length=1)
    title: str = Field(min_length=1)
    world_backbone: WorldBackboneRead | None = None
    world_rules: list[StoryBibleWorldRuleRead] = Field(default_factory=list)
    locations: list[StoryBibleLocationRead] = Field(default_factory=list)
    factions: list[StoryBibleFactionRead] = Field(default_factory=list)
    characters: list[StoryBibleCharacterRead] = Field(default_factory=list)
    relationships: list[StoryBibleRelationshipRead] = Field(default_factory=list)
    volume_frontiers: list[VolumeFrontierRead] = Field(default_factory=list)
    deferred_reveals: list[DeferredRevealRead] = Field(default_factory=list)
    expansion_gates: list[ExpansionGateRead] = Field(default_factory=list)
