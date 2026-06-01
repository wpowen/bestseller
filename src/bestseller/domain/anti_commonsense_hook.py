from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

HookVerdict = Literal["reject", "seed", "review", "expand"]


class HookMechanism(BaseModel):
    """A reusable anti-commonsense premise mechanism."""

    model_config = ConfigDict(frozen=True)

    key: str = Field(min_length=1, max_length=64)
    label: str = Field(min_length=1, max_length=120)
    base_desire_pool: tuple[str, ...] = Field(default_factory=tuple)
    reversal_template: str = Field(min_length=1)
    reward_pool: tuple[str, ...] = Field(default_factory=tuple)
    constraint_dimensions: tuple[str, ...] = Field(default_factory=tuple)
    anti_cheat_rules: tuple[str, ...] = Field(default_factory=tuple)
    cost_templates: tuple[str, ...] = Field(default_factory=tuple)
    misunderstanding_patterns: tuple[str, ...] = Field(default_factory=tuple)
    arc_escalation_axes: tuple[str, ...] = Field(default_factory=tuple)
    saturation_score: float = Field(default=0.35, ge=0.0, le=1.0)
    forbidden_overlaps: tuple[str, ...] = Field(default_factory=tuple)
    genres: tuple[str, ...] = Field(default_factory=tuple)

    @field_validator(
        "base_desire_pool",
        "reward_pool",
        "constraint_dimensions",
        "anti_cheat_rules",
        "cost_templates",
        "misunderstanding_patterns",
        "arc_escalation_axes",
        "forbidden_overlaps",
        "genres",
        mode="before",
    )
    @classmethod
    def _coerce_tuple(cls, value: object) -> tuple[str, ...]:
        if value is None:
            return ()
        if isinstance(value, str):
            return (value.strip(),) if value.strip() else ()
        if isinstance(value, list | tuple | set):
            return tuple(str(item).strip() for item in value if str(item).strip())
        return (str(value).strip(),) if str(value).strip() else ()


class HookSpec(BaseModel):
    """Structured premise contract that can be propagated downstream."""

    model_config = ConfigDict(frozen=True)

    mechanism_key: str = Field(min_length=1, max_length=64)
    genre: str = Field(default="", max_length=120)
    setting_locale: str | None = Field(default=None, max_length=120)
    protagonist_role: str | None = Field(default=None, max_length=120)
    base_desire: str = Field(min_length=1, max_length=240)
    reversal: str = Field(min_length=1, max_length=400)
    rewards: tuple[str, ...] = Field(default_factory=tuple)
    constraints: dict[str, str] = Field(default_factory=dict)
    anti_cheat: tuple[str, ...] = Field(default_factory=tuple)
    costs: tuple[str, ...] = Field(default_factory=tuple)
    misunderstanding: str | None = Field(default=None, max_length=400)
    arc_engine: tuple[str, ...] = Field(default_factory=tuple)
    one_liner: str = Field(min_length=1, max_length=240)
    core_rule: str = Field(min_length=1, max_length=500)

    @field_validator("rewards", "anti_cheat", "costs", "arc_engine", mode="before")
    @classmethod
    def _coerce_tuple(cls, value: object) -> tuple[str, ...]:
        if value is None:
            return ()
        if isinstance(value, str):
            return (value.strip(),) if value.strip() else ()
        if isinstance(value, list | tuple | set):
            return tuple(str(item).strip() for item in value if str(item).strip())
        return (str(value).strip(),) if str(value).strip() else ()

    @field_validator("constraints", mode="before")
    @classmethod
    def _coerce_constraints(cls, value: object) -> dict[str, str]:
        if not isinstance(value, dict):
            return {}
        return {
            str(key).strip(): str(item).strip()
            for key, item in value.items()
            if str(key).strip() and str(item).strip()
        }


class HookScore(BaseModel):
    model_config = ConfigDict(frozen=True)

    delta: int = Field(ge=0, le=10)
    reward: int = Field(ge=0, le=10)
    constraint: int = Field(ge=0, le=10)
    penalty: int = Field(ge=0, le=10)
    misunderstanding: int = Field(ge=0, le=10)
    expansion: int = Field(ge=0, le=10)
    learning_cost: int = Field(ge=1, le=10)
    h_norm: float = Field(ge=0.0, le=100.0)
    verdict: HookVerdict


class HookCandidate(BaseModel):
    model_config = ConfigDict(frozen=True)

    spec: HookSpec
    score: HookScore
    novelty_score: float = Field(ge=0.0, le=1.0)
    duplicate_risk: float = Field(default=0.0, ge=0.0, le=1.0)
    combined_rank: float = Field(ge=0.0, le=1.0)


class HookStrengthFinding(BaseModel):
    model_config = ConfigDict(frozen=True)

    code: str
    severity: str = "medium"
    message: str = ""
    path: str = ""
    repair_action: str = ""


class HookStrengthGateReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    findings: tuple[HookStrengthFinding, ...] = Field(default_factory=tuple)
    h_norm: float = Field(ge=0.0, le=100.0)
    passed: bool
    rewrite_suggestions: tuple[str, ...] = Field(default_factory=tuple)
    score: HookScore
    verdict: str

