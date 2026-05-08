from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, field_validator


def _clean_string_tuple(values: tuple[str, ...]) -> tuple[str, ...]:
    cleaned: list[str] = []
    for value in values:
        normalized = value.strip()
        if normalized and normalized not in cleaned:
            cleaned.append(normalized)
    return tuple(cleaned)


class BreakthroughCauseKind(StrEnum):
    RESOURCE = "resource"
    TECHNIQUE = "technique"
    INSIGHT = "insight"
    MENTOR = "mentor"
    ARTIFACT = "artifact"
    INJURY_RECOVERY = "injury_recovery"
    TRIAL = "trial"
    EXTERNAL_EVENT = "external_event"


class ProgressionBottleneck(BaseModel):
    key: str = Field(min_length=1, max_length=128)
    at_realm: str = Field(min_length=1, max_length=128)
    target_realm: str = Field(min_length=1, max_length=128)
    description: str = Field(min_length=1, max_length=2000)
    required_cause_kinds: tuple[BreakthroughCauseKind, ...] = Field(default_factory=tuple)
    required_resource_keys: tuple[str, ...] = Field(default_factory=tuple)
    severity: Literal["soft", "hard"] = "hard"

    @field_validator("required_resource_keys")
    @classmethod
    def normalize_resource_keys(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return _clean_string_tuple(values)


class PowerRealm(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    order: int = Field(ge=0)
    aliases: tuple[str, ...] = Field(default_factory=tuple)
    description: str | None = Field(default=None, max_length=2000)
    bottleneck_key: str | None = Field(default=None, max_length=128)

    @field_validator("aliases")
    @classmethod
    def normalize_aliases(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return _clean_string_tuple(values)


class PowerSystem(BaseModel):
    key: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=128)
    realms: tuple[PowerRealm, ...] = Field(default_factory=tuple)
    bottlenecks: tuple[ProgressionBottleneck, ...] = Field(default_factory=tuple)
    terminology_notes: tuple[str, ...] = Field(default_factory=tuple)

    @field_validator("terminology_notes")
    @classmethod
    def normalize_terminology_notes(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return _clean_string_tuple(values)

    @property
    def ordered_realms(self) -> tuple[PowerRealm, ...]:
        return tuple(sorted(self.realms, key=lambda realm: realm.order))


class Technique(BaseModel):
    key: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=128)
    required_realm: str | None = Field(default=None, max_length=128)
    unlocks_realms: tuple[str, ...] = Field(default_factory=tuple)
    costs: dict[str, int] = Field(default_factory=dict)
    limitation: str | None = Field(default=None, max_length=2000)

    @field_validator("unlocks_realms")
    @classmethod
    def normalize_unlocks_realms(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return _clean_string_tuple(values)


class Artifact(BaseModel):
    key: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=128)
    active: bool = True
    capabilities: tuple[str, ...] = Field(default_factory=tuple)
    unlocks_realms: tuple[str, ...] = Field(default_factory=tuple)
    known_limit: str | None = Field(default=None, max_length=2000)

    @field_validator("capabilities", "unlocks_realms")
    @classmethod
    def normalize_string_tuples(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return _clean_string_tuple(values)


class ResourceLedgerEntry(BaseModel):
    resource_key: str = Field(min_length=1, max_length=128)
    amount: int = Field(description="Signed delta. Positive means gained; negative means spent.")
    chapter_no: int = Field(ge=0)
    source: str = Field(min_length=1, max_length=512)
    reason: str | None = Field(default=None, max_length=2000)


class ResourceLedger(BaseModel):
    owner: str = Field(min_length=1, max_length=128)
    entries: tuple[ResourceLedgerEntry, ...] = Field(default_factory=tuple)


class OpportunityNode(BaseModel):
    key: str = Field(min_length=1, max_length=128)
    title: str = Field(min_length=1, max_length=256)
    introduced_chapter: int = Field(ge=0)
    expected_payoff_chapter: int | None = Field(default=None, ge=0)
    prerequisites: tuple[str, ...] = Field(default_factory=tuple)
    rewards: tuple[str, ...] = Field(default_factory=tuple)
    status: Literal["seeded", "available", "consumed", "expired"] = "seeded"

    @field_validator("prerequisites", "rewards")
    @classmethod
    def normalize_string_tuples(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return _clean_string_tuple(values)


class BreakthroughCause(BaseModel):
    kind: BreakthroughCauseKind
    ref_key: str | None = Field(default=None, max_length=128)
    detail: str = Field(min_length=1, max_length=2000)


class BreakthroughEvent(BaseModel):
    character_name: str = Field(min_length=1, max_length=128)
    from_realm: str = Field(min_length=1, max_length=128)
    to_realm: str = Field(min_length=1, max_length=128)
    chapter_no: int = Field(ge=0)
    causes: tuple[BreakthroughCause, ...] = Field(default_factory=tuple)


class ProgressionContext(BaseModel):
    system: PowerSystem
    character_realms: dict[str, str] = Field(default_factory=dict)
    resource_ledgers: dict[str, ResourceLedger] = Field(default_factory=dict)
    techniques: tuple[Technique, ...] = Field(default_factory=tuple)
    artifacts: tuple[Artifact, ...] = Field(default_factory=tuple)
    active_bottleneck: ProgressionBottleneck | None = None
