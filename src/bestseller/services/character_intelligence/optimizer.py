# ruff: noqa: RUF001
"""Non-destructive optimization for existing character profiles.

Existing books already have chapters, scenes, character ids, and continuity
state.  This service does not regenerate any of that.  It only enriches each
character's metadata-local ``character_engine_profile`` with the current
character intelligence strategy so future draft prompts can use the new
capability.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
import hashlib
import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.infra.db.models import CharacterModel, ProjectModel
from bestseller.services.character_intelligence.strategy import (
    character_strategy_from_project_metadata,
)
from bestseller.services.quality_levers.character_engine import (
    synthesize_character_engine_profile,
)

CHARACTER_INTELLIGENCE_PROFILE_VERSION = 1


def _as_mapping(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _text(value: object) -> str:
    return str(value).strip() if value is not None else ""


def _profile_is_current(profile: Mapping[str, Any], strategy: Mapping[str, Any]) -> bool:
    if not profile or not strategy:
        return False
    if profile.get("character_intelligence_version") != CHARACTER_INTELLIGENCE_PROFILE_VERSION:
        return False
    if not profile.get("strategy_source"):
        return False
    return all(
        isinstance(profile.get(key), dict)
        for key in (
            "agency_policy",
            "identity_pressure",
            "relationship_debt",
            "dialogue_function",
            "character_reward_contract",
        )
    )


def _strategy_fingerprint(strategy: Mapping[str, Any]) -> str:
    try:
        raw = json.dumps(strategy, ensure_ascii=False, sort_keys=True, default=str)
    except TypeError:
        raw = str(sorted(strategy.items()))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _project_optimization_is_current(
    metadata: Mapping[str, Any],
    strategy: Mapping[str, Any],
) -> bool:
    marker = _as_mapping(metadata.get("character_profile_optimization"))
    return (
        marker.get("version") == CHARACTER_INTELLIGENCE_PROFILE_VERSION
        and marker.get("scope") == "character_metadata_only"
        and marker.get("strategy_fingerprint") == _strategy_fingerprint(strategy)
    )


def _optimization_marker(
    strategy: Mapping[str, Any],
    *,
    characters_seen: int,
    profiles_optimized: int,
) -> dict[str, Any]:
    return {
        "version": CHARACTER_INTELLIGENCE_PROFILE_VERSION,
        "source": "character_intelligence_optimizer",
        "strategy_source": _text(strategy.get("source"))
        or "distillation_character_intelligence",
        "strategy_fingerprint": _strategy_fingerprint(strategy),
        "optimized_at": datetime.now(UTC).isoformat(),
        "preserved_existing_book_content": True,
        "scope": "character_metadata_only",
        "characters_seen": characters_seen,
        "profiles_optimized": profiles_optimized,
    }


def _cast_payload_from_character(character: CharacterModel) -> dict[str, Any]:
    meta = _as_mapping(getattr(character, "metadata_json", None))
    cast_entry = _as_mapping(meta.get("cast_entry"))
    payload: dict[str, Any] = dict(cast_entry)

    # Fill gaps from the stable row. This preserves the existing character id
    # and continuity state while making legacy rows optimizable even if they
    # predate ``metadata.cast_entry``.
    payload.setdefault("character_id", _text(getattr(character, "id", "")))
    payload.setdefault("name", _text(getattr(character, "name", "")) or "角色")
    payload.setdefault("role", _text(getattr(character, "role", "")) or "supporting")
    for key, attr in (
        ("age", "age"),
        ("background", "background"),
        ("goal", "goal"),
        ("fear", "fear"),
        ("flaw", "flaw"),
        ("strength", "strength"),
        ("secret", "secret"),
        ("arc_trajectory", "arc_trajectory"),
        ("arc_state", "arc_state"),
        ("power_tier", "power_tier"),
        ("core_wound", "core_wound"),
    ):
        value = getattr(character, attr, None)
        if value not in (None, "", [], {}):
            payload.setdefault(key, value)

    voice_profile = _as_mapping(getattr(character, "voice_profile_json", None))
    if voice_profile:
        payload.setdefault("voice_profile", voice_profile)
    moral_framework = _as_mapping(getattr(character, "moral_framework_json", None))
    if moral_framework:
        payload.setdefault("moral_framework", moral_framework)

    ip_anchor = _as_mapping(payload.get("ip_anchor"))
    quirks = list(getattr(character, "quirks_json", None) or [])
    sensory = list(getattr(character, "sensory_signatures_json", None) or [])
    objects = list(getattr(character, "signature_objects_json", None) or [])
    if quirks:
        ip_anchor.setdefault("quirks", quirks)
    if sensory:
        ip_anchor.setdefault("sensory_signatures", sensory)
    if objects:
        ip_anchor.setdefault("signature_objects", objects)
    if getattr(character, "core_wound", None):
        ip_anchor.setdefault("core_wound", character.core_wound)
    if ip_anchor:
        payload["ip_anchor"] = ip_anchor

    for key in (
        "psych_profile",
        "life_history",
        "social_network",
        "beliefs",
        "family_imprint",
        "villain_charisma",
        "inner_structure",
        "relationships",
        "gender",
        "pronoun_set_zh",
        "pronoun_set_en",
        "aliases",
    ):
        value = meta.get(key)
        if value not in (None, "", [], {}):
            payload.setdefault(key, value)
    return payload


def build_optimized_character_profile(
    character: CharacterModel,
    *,
    character_strategy: Mapping[str, Any],
) -> dict[str, Any]:
    """Build an optimized profile from an existing character row."""

    profile = synthesize_character_engine_profile(
        _cast_payload_from_character(character),
        character_strategy=character_strategy,
    )
    profile["character_intelligence_version"] = CHARACTER_INTELLIGENCE_PROFILE_VERSION
    return profile


async def optimize_project_character_profiles(
    session: AsyncSession,
    project: ProjectModel,
    *,
    force: bool = False,
    dry_run: bool = False,
) -> dict[str, int]:
    """Optimize existing characters without rebuilding book or chapter content.

    Only ``ProjectModel.metadata_json`` and ``CharacterModel.metadata_json`` may
    be touched. Chapter rows, scene rows, drafts, and continuity snapshots are
    intentionally outside this service's write scope.
    """

    project_meta = _as_mapping(getattr(project, "metadata_json", None))
    strategy = character_strategy_from_project_metadata(project_meta)
    counts = {
        "projects_seen": 1,
        "projects_optimized": 0,
        "project_strategy_created": 0,
        "characters_seen": 0,
        "profiles_optimized": 0,
        "profiles_skipped_current": 0,
        "profiles_without_strategy": 0,
        "legacy_profiles_preserved": 0,
        "projects_skipped_current": 0,
    }
    if not strategy:
        counts["profiles_without_strategy"] = 1
        return counts
    if not force and _project_optimization_is_current(project_meta, strategy):
        counts["projects_skipped_current"] = 1
        return counts

    if "character_strategy" not in project_meta:
        project_meta["character_strategy"] = strategy
        counts["project_strategy_created"] = 1
        if not dry_run:
            project.metadata_json = project_meta

    characters = list(
        await session.scalars(
            select(CharacterModel).where(CharacterModel.project_id == project.id)
        )
    )
    counts["characters_seen"] = len(characters)

    for character in characters:
        meta = _as_mapping(getattr(character, "metadata_json", None))
        current_profile = _as_mapping(meta.get("character_engine_profile"))
        if not force and _profile_is_current(current_profile, strategy):
            counts["profiles_skipped_current"] += 1
            continue

        optimized_profile = build_optimized_character_profile(
            character,
            character_strategy=strategy,
        )
        if current_profile and "character_engine_profile_legacy" not in meta:
            meta["character_engine_profile_legacy"] = current_profile
            counts["legacy_profiles_preserved"] += 1
        meta["character_engine_profile"] = optimized_profile
        meta["character_profile_optimization"] = _optimization_marker(
            strategy,
            characters_seen=1,
            profiles_optimized=1,
        )
        counts["profiles_optimized"] += 1
        if not dry_run:
            character.metadata_json = meta

    should_mark_project = bool(
        counts["profiles_optimized"]
        or counts["project_strategy_created"]
        or counts["profiles_skipped_current"]
    )
    if should_mark_project:
        counts["projects_optimized"] = int(bool(counts["profiles_optimized"]))
        project_meta["character_profile_optimization"] = _optimization_marker(
            strategy,
            characters_seen=counts["characters_seen"],
            profiles_optimized=counts["profiles_optimized"],
        )
        if not dry_run:
            project.metadata_json = project_meta
            await session.flush()
    return counts


__all__ = [
    "CHARACTER_INTELLIGENCE_PROFILE_VERSION",
    "build_optimized_character_profile",
    "optimize_project_character_profiles",
]
