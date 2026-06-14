"""Versioned genre Skill profile snapshots.

This module is the small coordination layer between existing genre-aware
surfaces: research skills, prompt packs, review profiles, threshold profiles,
and methodology lineage.  It records a deterministic project-level snapshot so
new books have a stable strategy anchor while legacy books without the metadata
continue to behave exactly as before.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from bestseller.services.genre_profile_thresholds import resolve_thresholds
from bestseller.services.genre_review_profiles import resolve_genre_review_profile
from bestseller.services.prompt_packs import resolve_prompt_pack
from bestseller.services.skills_loader import load_skills_for_genre

GENRE_SKILL_PROFILE_METADATA_KEY = "genre_skill_profile"
GENRE_SKILL_PROFILE_VERSION = "2026-06-14.v1"


class GenreSkillProfileActivation(BaseModel):
    model_config = ConfigDict(frozen=True)

    scope: Literal["new_project"] = "new_project"
    gate_mode: Literal["audit_only", "warn", "strict"] = "audit_only"
    affects_legacy_projects: bool = False


class GenreSkillLineagePolicy(BaseModel):
    model_config = ConfigDict(frozen=True)

    selection_owner: Literal["planner"] = "planner"
    downstream_policy: Literal["consume_snapshot"] = "consume_snapshot"
    max_cards: int = Field(default=6, ge=1)
    max_tokens: int = Field(default=900, ge=1)


class GenreSkillStageBindings(BaseModel):
    model_config = ConfigDict(frozen=True)

    research: tuple[str, ...] = ("research_skill_keys",)
    planning: tuple[str, ...] = ("prompt_pack_key", "methodology_lineage")
    drafting: tuple[str, ...] = ("prompt_pack_key", "methodology_lineage")
    review: tuple[str, ...] = ("review_profile_key", "threshold_profile_key", "methodology_lineage")
    repair: tuple[str, ...] = ("methodology_lineage", "repair_playbooks")


class GenreSkillProfileSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)

    version: str = GENRE_SKILL_PROFILE_VERSION
    profile_key: str
    genre: str
    sub_genre: str | None = None
    research_skill_keys: tuple[str, ...] = ()
    prompt_pack_key: str | None = None
    review_profile_key: str
    threshold_profile_key: str
    activation: GenreSkillProfileActivation = Field(
        default_factory=GenreSkillProfileActivation
    )
    lineage_policy: GenreSkillLineagePolicy = Field(default_factory=GenreSkillLineagePolicy)
    stage_bindings: GenreSkillStageBindings = Field(default_factory=GenreSkillStageBindings)

    def to_metadata(self) -> dict[str, Any]:
        payload = self.model_dump(mode="json")
        payload["research_skill_keys"] = list(self.research_skill_keys)
        return payload


def resolve_genre_skill_profile(
    genre: str,
    sub_genre: str | None = None,
    *,
    prompt_pack_key: str | None = None,
) -> GenreSkillProfileSnapshot:
    """Resolve a compact project-level Skill strategy snapshot.

    The resolver only points at existing systems; it does not activate strict
    gates or alter prompt behavior by itself.
    """

    pack = resolve_prompt_pack(prompt_pack_key, genre=genre, sub_genre=sub_genre)
    review_profile = resolve_genre_review_profile(
        genre,
        sub_genre,
        genre_preset_key=pack.key if pack is not None else prompt_pack_key,
    )
    thresholds = resolve_thresholds(review_profile.category_key)
    skills = load_skills_for_genre(genre, sub_genre)
    research_skill_keys = tuple(skill.key for skill in skills)
    profile_key = (
        pack.key
        if pack is not None
        else review_profile.category_key
        if review_profile.category_key != "default"
        else thresholds.id
    )
    return GenreSkillProfileSnapshot(
        profile_key=profile_key,
        genre=genre,
        sub_genre=sub_genre,
        research_skill_keys=research_skill_keys,
        prompt_pack_key=pack.key if pack is not None else None,
        review_profile_key=review_profile.category_key,
        threshold_profile_key=thresholds.id,
    )


def attach_genre_skill_profile(
    metadata: Mapping[str, Any] | None,
    profile: GenreSkillProfileSnapshot,
) -> dict[str, Any]:
    """Return metadata with a serialized profile attached, without mutating input."""

    updated = dict(metadata or {})
    updated[GENRE_SKILL_PROFILE_METADATA_KEY] = profile.to_metadata()
    return updated


def genre_skill_profile_from_metadata(
    metadata: Mapping[str, Any] | None,
) -> GenreSkillProfileSnapshot | None:
    raw = (metadata or {}).get(GENRE_SKILL_PROFILE_METADATA_KEY)
    if not isinstance(raw, Mapping):
        return None
    try:
        return GenreSkillProfileSnapshot.model_validate(raw)
    except ValueError:
        return None


__all__ = [
    "GENRE_SKILL_PROFILE_METADATA_KEY",
    "GENRE_SKILL_PROFILE_VERSION",
    "GenreSkillLineagePolicy",
    "GenreSkillProfileActivation",
    "GenreSkillProfileSnapshot",
    "GenreSkillStageBindings",
    "attach_genre_skill_profile",
    "genre_skill_profile_from_metadata",
    "resolve_genre_skill_profile",
]
