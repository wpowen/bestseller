"""Selectable concept bundles for quickstart story creation."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ConceptTitleSeed(BaseModel, frozen=True):
    """Reader-facing title candidate with its selling angle."""

    model_config = ConfigDict(extra="ignore")

    text: str = Field(min_length=1, max_length=120)
    angle: str = Field(default="", max_length=160)
    reason: str = Field(default="", max_length=240)


class ConceptListingSeed(BaseModel, frozen=True):
    """Listing-page copy seed produced from the selected concept."""

    model_config = ConfigDict(extra="ignore")

    hook: str = Field(default="", max_length=240)
    blurb: str = Field(default="", max_length=500)
    bullets: tuple[str, ...] = Field(default_factory=tuple)
    tags: tuple[str, ...] = Field(default_factory=tuple)

    @field_validator("bullets", "tags", mode="before")
    @classmethod
    def _coerce_tuple(cls, value: object) -> tuple[str, ...]:
        return _coerce_str_tuple(value, limit=10)


class ConceptMaterialBrief(BaseModel, frozen=True):
    """Material-search and combination contract for one concept bundle."""

    model_config = ConfigDict(extra="ignore")

    dimensions: tuple[str, ...] = Field(default_factory=tuple)
    query_terms: tuple[str, ...] = Field(default_factory=tuple)
    combination_rules: tuple[str, ...] = Field(default_factory=tuple)
    novelty_guardrails: tuple[str, ...] = Field(default_factory=tuple)
    seed_examples: tuple[str, ...] = Field(default_factory=tuple)

    @field_validator(
        "dimensions",
        "query_terms",
        "combination_rules",
        "novelty_guardrails",
        "seed_examples",
        mode="before",
    )
    @classmethod
    def _coerce_tuple(cls, value: object) -> tuple[str, ...]:
        return _coerce_str_tuple(value, limit=16)


class ConceptStoryLoop(BaseModel, frozen=True):
    """Loop contract that downstream planning and chapter prompts can reuse."""

    model_config = ConfigDict(extra="ignore")

    opening_question: str = Field(default="", max_length=360)
    recurring_pressure: str = Field(default="", max_length=360)
    payoff_window_chapters: int = Field(default=5, ge=1, le=30)
    escalation_axis: tuple[str, ...] = Field(default_factory=tuple)
    per_chapter_contract: tuple[str, ...] = Field(default_factory=tuple)
    guardrails: tuple[str, ...] = Field(default_factory=tuple)

    @field_validator("escalation_axis", "per_chapter_contract", "guardrails", mode="before")
    @classmethod
    def _coerce_tuple(cls, value: object) -> tuple[str, ...]:
        return _coerce_str_tuple(value, limit=12)


class ConceptLabBundle(BaseModel, frozen=True):
    """A selectable, serializable concept contract for quickstart."""

    model_config = ConfigDict(extra="ignore")

    bundle_id: str = Field(min_length=1, max_length=160)
    genre_key: str = Field(min_length=1, max_length=120)
    creative_key: str = Field(default="", max_length=120)
    hook_spec: dict[str, Any] = Field(default_factory=dict)
    reader_promise: str = Field(default="", max_length=500)
    one_liner: str = Field(default="", max_length=500)
    title_seeds: tuple[ConceptTitleSeed, ...] = Field(default_factory=tuple)
    listing_seeds: tuple[ConceptListingSeed, ...] = Field(default_factory=tuple)
    material_brief: ConceptMaterialBrief = Field(default_factory=ConceptMaterialBrief)
    story_loop: ConceptStoryLoop = Field(default_factory=ConceptStoryLoop)
    methodology_targets: tuple[str, ...] = Field(default_factory=tuple)
    hype_targets: tuple[str, ...] = Field(default_factory=tuple)
    guardrails: tuple[str, ...] = Field(default_factory=tuple)
    source_mix: tuple[str, ...] = Field(default_factory=tuple)
    scores: dict[str, float] = Field(default_factory=dict)

    @field_validator(
        "methodology_targets",
        "hype_targets",
        "guardrails",
        "source_mix",
        mode="before",
    )
    @classmethod
    def _coerce_tuple(cls, value: object) -> tuple[str, ...]:
        return _coerce_str_tuple(value, limit=16)

    @field_validator("hook_spec", mode="before")
    @classmethod
    def _coerce_hook_spec(cls, value: object) -> dict[str, Any]:
        return dict(value) if isinstance(value, dict) else {}

    @field_validator("scores", mode="before")
    @classmethod
    def _coerce_scores(cls, value: object) -> dict[str, float]:
        if not isinstance(value, dict):
            return {}
        out: dict[str, float] = {}
        for key, item in value.items():
            try:
                out[str(key)] = float(item)
            except (TypeError, ValueError):
                continue
        return out


class ConceptLabCatalog(BaseModel, frozen=True):
    """Preview payload returned to the quickstart UI."""

    model_config = ConfigDict(extra="ignore")

    genre_key: str = Field(min_length=1, max_length=120)
    default_bundle_id: str = Field(default="", max_length=160)
    bundles: tuple[ConceptLabBundle, ...] = Field(default_factory=tuple)


def _coerce_str_tuple(value: object, *, limit: int) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        text = value.strip()
        return (text,) if text else ()
    if isinstance(value, list | tuple | set):
        out: list[str] = []
        for item in value:
            text = str(item or "").strip()
            if text and text not in out:
                out.append(text)
            if len(out) >= limit:
                break
        return tuple(out)
    text = str(value or "").strip()
    return (text,) if text else ()

