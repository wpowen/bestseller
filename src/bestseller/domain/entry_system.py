from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def _clean_string_tuple(values: tuple[str, ...]) -> tuple[str, ...]:
    cleaned: list[str] = []
    for value in values:
        normalized = str(value).strip()
        if normalized and normalized not in cleaned:
            cleaned.append(normalized)
    return tuple(cleaned)


class EntryTier(StrEnum):
    PILLAR = "pillar"
    VOLUME = "volume"
    SUPPORTING = "supporting"
    MOTIF = "motif"


class EntryEventType(StrEnum):
    SEEDED = "seeded"
    INTRODUCED = "introduced"
    DISCOVERED = "discovered"
    ACQUIRED = "acquired"
    LEARNED = "learned"
    BONDED = "bonded"
    USED = "used"
    UPGRADED = "upgraded"
    SPENT = "spent"
    EXPOSED = "exposed"
    CONTESTED = "contested"
    DAMAGED = "damaged"
    SEALED = "sealed"
    LOST = "lost"
    RESTORED = "restored"
    PAID_OFF = "paid_off"
    DEPRECATED = "deprecated"


class EntryTypeDefinition(BaseModel, frozen=True):
    """Book-specific category definition for entries."""

    model_config = ConfigDict(extra="allow")

    type: str = Field(min_length=1, max_length=64)
    label: str = Field(min_length=1, max_length=128)
    allowed_roles: tuple[str, ...] = Field(default_factory=tuple)
    required_fields: tuple[str, ...] = Field(default_factory=tuple)
    forbidden_patterns: tuple[str, ...] = Field(default_factory=tuple)

    @field_validator("allowed_roles", "required_fields", "forbidden_patterns")
    @classmethod
    def _normalize_string_tuple(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return _clean_string_tuple(values)


class EntryGradeLevel(BaseModel, frozen=True):
    """One step in a book-specific grade ladder."""

    model_config = ConfigDict(extra="allow")

    key: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=128)
    capability_ceiling: str = Field(min_length=1, max_length=1000)
    promotion_trigger: str | None = Field(default=None, max_length=1000)
    promotion_cost: str | None = Field(default=None, max_length=1000)
    visibility_effect: str | None = Field(default=None, max_length=1000)
    narrative_consequence: str | None = Field(default=None, max_length=1000)


class EntryGradeLadder(BaseModel, frozen=True):
    """A valid progression ladder for entries in one book."""

    model_config = ConfigDict(extra="allow")

    ladder_key: str = Field(min_length=1, max_length=64)
    label: str = Field(min_length=1, max_length=128)
    levels: tuple[EntryGradeLevel, ...] = Field(default_factory=tuple)
    promotion_rule: str = Field(min_length=1, max_length=2000)
    applies_to: tuple[str, ...] = Field(default_factory=tuple)

    @field_validator("applies_to")
    @classmethod
    def _normalize_applies_to(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return _clean_string_tuple(values)

    @model_validator(mode="after")
    def _require_unique_levels(self) -> EntryGradeLadder:
        keys = [level.key for level in self.levels]
        if len(keys) != len(set(keys)):
            raise ValueError("entry grade ladder levels must have unique keys")
        return self


class CapabilityAxis(BaseModel, frozen=True):
    """Named axis that entries can affect."""

    model_config = ConfigDict(extra="allow")

    axis: str = Field(min_length=1, max_length=64)
    label: str = Field(min_length=1, max_length=128)
    meaning: str = Field(min_length=1, max_length=2000)
    valid_for: tuple[str, ...] = Field(default_factory=tuple)

    @field_validator("valid_for")
    @classmethod
    def _normalize_valid_for(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return _clean_string_tuple(values)


class EntryCostModel(BaseModel, frozen=True):
    """Default cost policy for power-changing entry events."""

    model_config = ConfigDict(extra="allow")

    default_cost_types: tuple[str, ...] = Field(default_factory=tuple)
    hard_rule: str = Field(
        default="Power-changing entry effects must pay a visible cost.",
        max_length=2000,
    )

    @field_validator("default_cost_types")
    @classmethod
    def _normalize_cost_types(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return _clean_string_tuple(values)


class EntryAcquisitionModel(BaseModel, frozen=True):
    """How entries can be introduced or earned."""

    model_config = ConfigDict(extra="allow")

    valid_sources: tuple[str, ...] = Field(default_factory=tuple)
    reader_visible_required: bool = True

    @field_validator("valid_sources")
    @classmethod
    def _normalize_sources(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return _clean_string_tuple(values)


class EntryLifecycle(BaseModel, frozen=True):
    """Allowed states and state-change policy for entries."""

    model_config = ConfigDict(extra="allow")

    states: tuple[str, ...] = (
        "seeded",
        "introduced",
        "available",
        "owned",
        "bonded",
        "upgraded",
        "public",
        "contested",
        "damaged",
        "spent",
        "sealed",
        "lost",
        "paid_off",
    )
    major_state_changes_require_chapter_event: bool = True

    @field_validator("states")
    @classmethod
    def _normalize_states(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return _clean_string_tuple(values)


class EntrySystemKernel(BaseModel, frozen=True):
    """Book-specific operating system for named story assets."""

    model_config = ConfigDict(extra="allow")

    version: int = Field(default=1, ge=1)
    kernel_key: str = Field(default="book_entry_system", min_length=1, max_length=128)
    genre_profile: str | None = Field(default=None, max_length=128)
    system_promise: str = Field(min_length=1, max_length=4000)
    taxonomy: tuple[EntryTypeDefinition, ...] = Field(default_factory=tuple)
    grade_ladders: tuple[EntryGradeLadder, ...] = Field(default_factory=tuple)
    capability_axes: tuple[CapabilityAxis, ...] = Field(default_factory=tuple)
    cost_model: EntryCostModel = Field(default_factory=EntryCostModel)
    acquisition_model: EntryAcquisitionModel = Field(default_factory=EntryAcquisitionModel)
    lifecycle: EntryLifecycle = Field(default_factory=EntryLifecycle)
    uniqueness_rules: tuple[str, ...] = Field(default_factory=tuple)
    anti_copy_rules: tuple[str, ...] = Field(default_factory=tuple)
    coverage_targets: dict[str, Any] = Field(default_factory=dict)

    @field_validator("uniqueness_rules", "anti_copy_rules")
    @classmethod
    def _normalize_rules(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return _clean_string_tuple(values)

    @model_validator(mode="after")
    def _require_taxonomy_and_unique_keys(self) -> EntrySystemKernel:
        type_keys = [item.type for item in self.taxonomy]
        if len(type_keys) != len(set(type_keys)):
            raise ValueError("entry taxonomy types must be unique")
        ladder_keys = [item.ladder_key for item in self.grade_ladders]
        if len(ladder_keys) != len(set(ladder_keys)):
            raise ValueError("entry grade ladder keys must be unique")
        return self

    @property
    def taxonomy_by_type(self) -> dict[str, EntryTypeDefinition]:
        return {item.type: item for item in self.taxonomy}

    @property
    def ladders_by_key(self) -> dict[str, EntryGradeLadder]:
        return {item.ladder_key: item for item in self.grade_ladders}


class EntryDefinition(BaseModel, frozen=True):
    """Concrete book-specific entry generated under an EntrySystemKernel."""

    model_config = ConfigDict(extra="allow")

    entry_id: str = Field(min_length=1, max_length=160)
    type: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=200)
    tier: EntryTier | str = EntryTier.SUPPORTING
    taxonomy_ref: str = Field(min_length=1, max_length=64)
    grade_ladder_ref: str | None = Field(default=None, max_length=64)
    current_grade: str | None = Field(default=None, max_length=64)
    owner: str | None = Field(default=None, max_length=200)
    visibility: str = Field(default="unknown", max_length=128)
    origin: str | None = Field(default=None, max_length=2000)
    capabilities: tuple[str, ...] = Field(default_factory=tuple)
    limits: tuple[str, ...] = Field(default_factory=tuple)
    costs: tuple[str, ...] = Field(default_factory=tuple)
    unlock_conditions: tuple[str, ...] = Field(default_factory=tuple)
    narrative_roles: tuple[str, ...] = Field(default_factory=tuple)
    allowed_uses: tuple[str, ...] = Field(default_factory=tuple)
    forbidden_uses: tuple[str, ...] = Field(default_factory=tuple)
    future_payoff_path: tuple[str, ...] = Field(default_factory=tuple)
    source_blueprint_ids: tuple[str, ...] = Field(default_factory=tuple)

    @field_validator(
        "capabilities",
        "limits",
        "costs",
        "unlock_conditions",
        "narrative_roles",
        "allowed_uses",
        "forbidden_uses",
        "future_payoff_path",
        "source_blueprint_ids",
    )
    @classmethod
    def _normalize_entry_tuples(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return _clean_string_tuple(values)

    @property
    def is_major(self) -> bool:
        return str(self.tier) in {EntryTier.PILLAR.value, EntryTier.VOLUME.value}

    @property
    def has_limits(self) -> bool:
        return bool(self.limits or self.costs or self.forbidden_uses)


class EntryRegistry(BaseModel, frozen=True):
    """Concrete entry list plus optional coverage metadata for one project."""

    model_config = ConfigDict(extra="allow")

    version: int = Field(default=1, ge=1)
    entries: tuple[EntryDefinition, ...] = Field(default_factory=tuple)
    coverage_matrix: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _require_unique_entry_ids(self) -> EntryRegistry:
        ids = [entry.entry_id for entry in self.entries]
        if len(ids) != len(set(ids)):
            raise ValueError("entry ids must be unique")
        return self

    @property
    def by_id(self) -> dict[str, EntryDefinition]:
        return {entry.entry_id: entry for entry in self.entries}


class EntryEvent(BaseModel, frozen=True):
    """Chapter-level state transition for an entry."""

    model_config = ConfigDict(extra="allow")

    chapter_number: int = Field(ge=0)
    scene_number: int | None = Field(default=None, ge=0)
    entry_id: str = Field(min_length=1, max_length=160)
    event_type: EntryEventType | str = Field(min_length=1, max_length=64)
    trigger: str = Field(min_length=1, max_length=2000)
    cost_paid: str | None = Field(default=None, max_length=2000)
    from_state: str | None = Field(default=None, max_length=128)
    to_state: str | None = Field(default=None, max_length=128)
    from_grade: str | None = Field(default=None, max_length=128)
    to_grade: str | None = Field(default=None, max_length=128)
    owner_after: str | None = Field(default=None, max_length=200)
    visibility_after: str | None = Field(default=None, max_length=128)
    summary: str | None = Field(default=None, max_length=2000)
    continuity_note: str | None = Field(default=None, max_length=2000)
    reader_visible: bool = True


class EntryStateSnapshot(BaseModel, frozen=True):
    """Reduced current state for entries after ledger events are applied."""

    model_config = ConfigDict(extra="allow")

    current_chapter: int = Field(default=0, ge=0)
    entry_states: dict[str, dict[str, Any]] = Field(default_factory=dict)
    stale_entry_ids: tuple[str, ...] = Field(default_factory=tuple)

    @field_validator("stale_entry_ids")
    @classmethod
    def _normalize_stale_ids(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return _clean_string_tuple(values)


class EntryMigrationChange(BaseModel, frozen=True):
    """One registry/kernel change that may require prose repair."""

    model_config = ConfigDict(extra="allow")

    entry_id: str = Field(min_length=1, max_length=160)
    change_type: str = Field(min_length=1, max_length=128)
    old: Any = None
    new: Any = None
    requires_story_patch: bool = False
    affected_chapters: tuple[int, ...] = Field(default_factory=tuple)


class EntryMigrationReport(BaseModel, frozen=True):
    """Versioned report explaining entry-system changes."""

    model_config = ConfigDict(extra="allow")

    migration_id: str = Field(min_length=1, max_length=160)
    reason: str = Field(min_length=1, max_length=2000)
    changes: tuple[EntryMigrationChange, ...] = Field(default_factory=tuple)
    required_repairs: tuple[str, ...] = Field(default_factory=tuple)

    @field_validator("required_repairs")
    @classmethod
    def _normalize_repairs(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return _clean_string_tuple(values)


EntryGateSeverity = Literal["info", "warning", "error", "critical"]
