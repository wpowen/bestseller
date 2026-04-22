from __future__ import annotations

import re
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


_ROLE_MAX_LENGTH = 64
_ROLE_BREAK_SEPARATORS: tuple[str, ...] = (
    "—",
    " - ",
    ". ",
    ": ",
    ", ",
    "; ",
    "。 ",
    "：",
    "，",
    "；",
    "\n",
)
_ROLE_SENTENCE_PREFIXES: tuple[str, ...] = (
    "from ",
    "becomes ",
    "becoming ",
    "remains ",
    "must ",
    "cannot ",
    "needs to ",
)
_ROLE_SENTENCE_PREFIXES_ZH: tuple[str, ...] = ("从", "由", "必须", "需要")
_ROLE_SENTENCE_CONNECTORS: tuple[str, ...] = (
    " when ",
    " because ",
    " while ",
    " through ",
    " specifically ",
)
_AGE_UNKNOWN_MARKERS: tuple[str, ...] = (
    "unknown",
    "indeterminate",
    "ageless",
    "immortal",
    "timeless",
    "不详",
    "未知",
    "不确定",
)
_AGE_APPROX_PREFIX_OFFSETS: dict[str, int] = {
    "early": 2,
    "mid": 5,
    "late": 8,
}


def normalize_character_role_label(value: Any, *, fallback: str | None = None) -> Any:
    """Coerce a role-like value into a short label.

    LLM outputs sometimes stuff a full character-evolution sentence into the
    ``role`` field. This helper keeps the validator permissive enough to
    recover by trimming to the first clause break, while callers that need
    stricter semantics can combine it with ``is_safe_character_role_label``.
    """
    if not isinstance(value, str):
        return value
    text = value.strip()
    if not text:
        return fallback or text
    if len(text) <= _ROLE_MAX_LENGTH:
        return text
    for sep in _ROLE_BREAK_SEPARATORS:
        idx = text.find(sep)
        if 0 < idx <= _ROLE_MAX_LENGTH:
            return text[:idx].strip()
    if fallback:
        return fallback
    return text[:_ROLE_MAX_LENGTH].rstrip()


def is_safe_character_role_label(value: Any) -> bool:
    """Return whether ``value`` looks like a structural role label.

    Accepts compact labels such as ``ally`` / ``antagonist_lieutenant`` /
    ``Theo Blackwood's field operative (lower-tier antagonist)`` and rejects
    sentence-shaped arc descriptions that belong in metadata instead.
    """
    if not isinstance(value, str):
        return False
    text = value.strip()
    if not text or len(text) > _ROLE_MAX_LENGTH:
        return False
    if any(sep in text for sep in ("\n", "\r", "。", "；")):
        return False
    lower = text.lower()
    if lower.startswith(_ROLE_SENTENCE_PREFIXES) or text.startswith(_ROLE_SENTENCE_PREFIXES_ZH):
        return False
    if len(text) > 32 and any(connector in lower for connector in _ROLE_SENTENCE_CONNECTORS):
        return False
    return True


def normalize_character_age(value: Any) -> int | None:
    """Coerce age-like values into an integer when safe, else ``None``.

    ``cast_spec`` occasionally receives prose like ``late 40s`` or
    ``indeterminate (fae)``.  Approximate decade labels are normalized to a
    representative integer so downstream models stay structured; unbounded
    fantasy labels degrade to ``None`` instead of crashing validation.
    """
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if value.is_integer() else None
    if not isinstance(value, str):
        return None

    text = value.strip()
    if not text:
        return None
    lower = text.lower()
    if any(marker in lower for marker in _AGE_UNKNOWN_MARKERS):
        return None
    if text.isdigit():
        return int(text)

    decade_match = re.search(r"\b(early|mid|late)\s+(\d{2,3})s\b", lower)
    if decade_match:
        prefix = decade_match.group(1)
        decade = int(decade_match.group(2))
        return decade + _AGE_APPROX_PREFIX_OFFSETS[prefix]

    plain_decade_match = re.search(r"\b(\d{2,3})s\b", lower)
    if plain_decade_match:
        return int(plain_decade_match.group(1)) + 5

    precise_match = re.fullmatch(r".*?(\d{1,3})\s*(?:years?\s*old|yo)?", lower)
    if precise_match:
        return int(precise_match.group(1))
    return None


class WorldRuleInput(BaseModel):
    rule_id: str | None = Field(default=None, max_length=32)
    name: str = Field(min_length=1, max_length=4000)
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

    name: str = Field(min_length=1, max_length=4000)
    location_type: str = Field(default="location", alias="type", min_length=1, max_length=4000)
    atmosphere: str | None = None
    key_rules: list[str] = Field(default_factory=list)
    story_role: str | None = None


class FactionInput(BaseModel):
    name: str = Field(min_length=1, max_length=4000)
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
    character: str = Field(min_length=1, max_length=4000)
    type: str = Field(min_length=1, max_length=4000)
    tension: str | None = None


class CharacterKnowledgeStateInput(BaseModel):
    knows: list[str] = Field(default_factory=list)
    falsely_believes: list[str] = Field(default_factory=list)
    unaware_of: list[str] = Field(default_factory=list)


class CharacterVoiceProfileInput(BaseModel):
    """Per-character speech and behavioural fingerprint."""

    speech_register: str | None = None  # 文雅/口语/粗犷/书卷气/军事化/…
    verbal_tics: list[str] = Field(default_factory=list)  # 口头禅/标志性用语
    sentence_style: str | None = None  # 长句思辨型/短句利落型/碎片独白型/…
    emotional_expression: str | None = None  # 内敛/外放/反讽/冷幽默/沉默型/…
    mannerisms: list[str] = Field(default_factory=list)  # 标志性肢体语言/习惯动作
    internal_monologue_style: str | None = None  # 内心独白语气特征
    vocabulary_level: str | None = None  # 高/中/低/混合


class CharacterMoralFramework(BaseModel):
    """Per-character moral compass — what lines they will/won't cross."""

    core_values: list[str] = Field(default_factory=list)  # 核心信条
    lines_never_crossed: list[str] = Field(default_factory=list)  # 不可逾越的底线
    willing_to_sacrifice: str | None = None  # 愿意为目标牺牲什么


class CharacterIPAnchorInput(BaseModel):
    """Commercial-novel IP anchors — the 3-quirks-and-a-wound checklist.

    Commercial bestsellers make readers remember characters by giving each one
    concrete, sensory hooks: unusual quirks (at least three per protagonist),
    signature objects they carry, a distinctive sensory signature (smell,
    sound, touch), and a single core psychological wound that explains every
    irrational decision. Without these, even well-plotted protagonists blur
    into interchangeable archetypes — the root cause of historical bug #14
    ("protagonist has no memorable features").

    All lists are plain strings so Pydantic can validate loose LLM output; L2
    Bible Gate layers stricter checks on top (protagonist needs >=3 quirks,
    core_wound must be non-empty, etc.).
    """

    quirks: list[str] = Field(default_factory=list)
    sensory_signatures: list[str] = Field(default_factory=list)
    signature_objects: list[str] = Field(default_factory=list)
    core_wound: str | None = None


class CharacterInput(BaseModel):
    model_config = ConfigDict(extra="allow")

    name: str = Field(min_length=1, max_length=4000)
    role: str = Field(default="supporting", min_length=1, max_length=_ROLE_MAX_LENGTH)

    @field_validator("role", mode="before")
    @classmethod
    def _coerce_role_to_short_label(cls, v: Any) -> Any:
        return normalize_character_role_label(v)

    age: int | None = Field(default=None, ge=0)

    @field_validator("age", mode="before")
    @classmethod
    def _coerce_age_to_int(cls, v: Any) -> int | None:
        return normalize_character_age(v)

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
    voice_profile: CharacterVoiceProfileInput = Field(default_factory=CharacterVoiceProfileInput)
    moral_framework: CharacterMoralFramework = Field(default_factory=CharacterMoralFramework)
    ip_anchor: CharacterIPAnchorInput = Field(default_factory=CharacterIPAnchorInput)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ConflictMapInput(BaseModel):
    character_a: str = Field(min_length=1, max_length=4000)
    character_b: str = Field(min_length=1, max_length=4000)
    conflict_type: str = Field(min_length=1, max_length=4000)
    trigger_condition: str | None = None


class ConflictForceInput(BaseModel):
    """A named conflict force active during specific volumes of the story.

    Unlike a single antagonist, conflict forces represent the diverse
    challenges the protagonist faces at different stages of their journey:
    local bullies, political intrigue, betrayals, faction wars, etc.
    """

    name: str = Field(min_length=1, max_length=4000)
    force_type: Literal["character", "faction", "environment", "internal", "systemic"] = Field(
        description="character / faction / environment / internal / systemic",
    )
    active_volumes: list[int] = Field(
        default_factory=list,
        description="Volume numbers where this force is the primary threat. Empty = all volumes.",
    )
    threat_description: str | None = None
    relationship_to_protagonist: str | None = None
    escalation_path: str | None = None
    character_ref: str | None = Field(
        default=None,
        max_length=4000,
        description="Name of a character in supporting_cast when force_type is 'character'.",
    )


class CastSpecInput(BaseModel):
    protagonist: CharacterInput | None = None
    antagonist: CharacterInput | None = None
    antagonist_forces: list[ConflictForceInput] = Field(default_factory=list)
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
    volume_title: str = Field(alias="title", min_length=1, max_length=4000)
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
    voice_profiles_populated: int = 0
    moral_frameworks_populated: int = 0
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
    voice_profile: dict[str, Any] = Field(default_factory=dict)
    moral_framework: dict[str, Any] = Field(default_factory=dict)
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
