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


class RiskTolerance(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class PressureResponse(StrEnum):
    OBSERVE = "observe"
    PREPARE = "prepare"
    BARGAIN = "bargain"
    RETREAT = "retreat"
    CONCEAL = "conceal"
    STRIKE_AFTER_CERTAINTY = "strike_after_certainty"
    PROTECT = "protect"
    INVESTIGATE = "investigate"


class PreferredTactic(BaseModel):
    key: str = Field(min_length=1, max_length=128)
    description: str = Field(min_length=1, max_length=1000)


class MoralBoundary(BaseModel):
    key: str = Field(min_length=1, max_length=128)
    description: str = Field(min_length=1, max_length=1000)
    absolute: bool = True


class ForbiddenBehavior(BaseModel):
    key: str = Field(min_length=1, max_length=128)
    description: str = Field(min_length=1, max_length=1000)


class DecisionPolicy(BaseModel):
    character_name: str = Field(min_length=1, max_length=128)
    archetype: str = Field(min_length=1, max_length=128)
    risk_tolerance: RiskTolerance = RiskTolerance.MEDIUM
    pressure_responses: tuple[PressureResponse, ...] = Field(default_factory=tuple)
    preferred_tactics: tuple[PreferredTactic, ...] = Field(default_factory=tuple)
    moral_boundaries: tuple[MoralBoundary, ...] = Field(default_factory=tuple)
    forbidden_behaviors: tuple[ForbiddenBehavior, ...] = Field(default_factory=tuple)
    high_risk_allowances: tuple[str, ...] = (
        "life_threat",
        "rare_resource_upside",
        "credible_escape_route",
        "protect_innocent",
        "strategic_necessity",
    )

    @field_validator("high_risk_allowances")
    @classmethod
    def normalize_allowances(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return _clean_string_tuple(values)


class DecisionEvent(BaseModel):
    character_name: str = Field(min_length=1, max_length=128)
    chapter_no: int = Field(ge=0)
    situation: str = Field(min_length=1, max_length=2000)
    action: str = Field(min_length=1, max_length=2000)
    risk_level: Literal["low", "medium", "high"] = "medium"
    motive_tags: tuple[str, ...] = Field(default_factory=tuple)
    tactic_tags: tuple[str, ...] = Field(default_factory=tuple)
    behavior_tags: tuple[str, ...] = Field(default_factory=tuple)
    violated_boundary_keys: tuple[str, ...] = Field(default_factory=tuple)
    has_credible_escape_route: bool = False
    is_life_threat: bool = False
    protects_innocent: bool = False
    public_vanity: bool = False

    @field_validator("motive_tags", "tactic_tags", "behavior_tags", "violated_boundary_keys")
    @classmethod
    def normalize_tags(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return _clean_string_tuple(values)


class DecisionFinding(BaseModel):
    code: str = Field(min_length=1, max_length=96)
    severity: Literal["info", "warning", "error"] = "error"
    message: str = Field(min_length=1)
    blocking: bool = True


class DecisionAudit(BaseModel):
    passed: bool
    findings: tuple[DecisionFinding, ...] = Field(default_factory=tuple)
