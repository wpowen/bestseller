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
from copy import deepcopy
from datetime import UTC, datetime
import hashlib
import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.infra.db.models import CharacterModel, ProjectModel
from bestseller.services.character_intelligence.strategy import (
    ReferenceProjectionContext,
    adopted_character_strategy,
    normalize_character_strategy,
)
from bestseller.services.quality_levers.character_engine import (
    synthesize_character_engine_profile,
)

CHARACTER_INTELLIGENCE_PROFILE_VERSION = 1
CHARACTER_ENGINE_PROFILES_KEY = "character_engine_profiles"


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
    if profile.get("reference_strategy_fingerprint") != _strategy_fingerprint(strategy):
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

    # cast_entry is a historical projection. Current row facts must win both
    # during synthesis and fingerprinting, including an explicitly cleared value.
    payload["character_id"] = _text(getattr(character, "id", ""))
    payload["name"] = _text(getattr(character, "name", "")) or "角色"
    payload["role"] = _text(getattr(character, "role", "")) or "supporting"
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
            payload[key] = value
        else:
            payload.pop(key, None)

    voice_profile = _as_mapping(getattr(character, "voice_profile_json", None))
    if voice_profile:
        payload["voice_profile"] = voice_profile
    else:
        payload.pop("voice_profile", None)
    moral_framework = _as_mapping(getattr(character, "moral_framework_json", None))
    if moral_framework:
        payload["moral_framework"] = moral_framework
    else:
        payload.pop("moral_framework", None)

    ip_anchor = _as_mapping(payload.get("ip_anchor"))
    quirks = list(getattr(character, "quirks_json", None) or [])
    sensory = list(getattr(character, "sensory_signatures_json", None) or [])
    objects = list(getattr(character, "signature_objects_json", None) or [])
    for key, value in (("quirks", quirks), ("sensory_signatures", sensory),
                       ("signature_objects", objects), ("core_wound", character.core_wound)):
        if value:
            ip_anchor[key] = value
        else:
            ip_anchor.pop(key, None)
    if ip_anchor:
        payload["ip_anchor"] = ip_anchor
    else:
        payload.pop("ip_anchor", None)

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
            payload[key] = value
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
    profile["reference_strategy_fingerprint"] = _strategy_fingerprint(character_strategy)
    return profile


def record_character_engine_profile(
    project: object, character: object, profile: Mapping[str, Any], *, strategy: Mapping[str, Any],
) -> None:
    """Register a freshly built profile; never call this to bless a stored profile."""
    from bestseller.services.prompt_source_policy import (
        archive_metadata, content_hash, stamp_reference_cache, valid_reference_value,
    )
    current_strategy = adopted_character_strategy(project)
    if _strategy_fingerprint(strategy) != _strategy_fingerprint(current_strategy):
        raise ValueError("character profile generation used a stale or unadopted strategy")
    if str(character.project_id) != str(project.id):
        raise ValueError("character profile belongs to a different project")
    metadata = dict(project.metadata_json or {})
    catalog = valid_reference_value(project, CHARACTER_ENGINE_PROFILES_KEY)
    if not isinstance(catalog, dict):
        metadata = archive_metadata(metadata, (CHARACTER_ENGINE_PROFILES_KEY,),
                                    reason="stale_character_profile_catalog")
        catalog = {}
    else:
        catalog = deepcopy(catalog)
    character_meta = dict(character.metadata_json or {})
    old = character_meta.get("character_engine_profile")
    if old is not None and old != profile:
        history = dict(character_meta.get("superseded_character_engine_profiles") or {})
        history.setdefault(content_hash(old), deepcopy(old))
        character_meta["superseded_character_engine_profiles"] = history
    character_meta["character_engine_profile"] = deepcopy(dict(profile))
    character.metadata_json = character_meta
    catalog[str(character.id)] = {
        "policy": "character-profile.v1", "profile_hash": content_hash(profile),
        "cast_hash": content_hash(metadata.get("cast_spec")),
        "character_input_hash": content_hash(_cast_payload_from_character(character)),
        "strategy_fingerprint": _strategy_fingerprint(current_strategy),
    }
    metadata[CHARACTER_ENGINE_PROFILES_KEY] = catalog
    context = ReferenceProjectionContext(project, metadata)
    stamp_reference_cache(context, (CHARACTER_ENGINE_PROFILES_KEY,),
                          producer="character_profile_materializer")
    project.metadata_json = context.metadata_json


def validated_character_engine_profile(
    project: object | None, character_id: object, profile: object, *, character: object | None = None,
) -> dict[str, Any] | None:
    """Stored/packet profiles cannot authorize themselves by source labels or version."""
    if project is None or not getattr(project, "id", None):
        return None
    from bestseller.services.prompt_source_policy import content_hash, valid_reference_value
    current_strategy = adopted_character_strategy(project)
    catalog = valid_reference_value(project, CHARACTER_ENGINE_PROFILES_KEY)
    if not isinstance(profile, dict) or not isinstance(catalog, dict):
        return None
    receipt = catalog.get(str(character_id))
    if not isinstance(receipt, dict) or receipt.get("policy") != "character-profile.v1":
        return None
    if (receipt.get("profile_hash") != content_hash(profile)
        or receipt.get("cast_hash") != content_hash((project.metadata_json or {}).get("cast_spec"))
        or receipt.get("strategy_fingerprint") != _strategy_fingerprint(current_strategy)):
        return None
    if character is not None and (
        str(character.id) != str(character_id)
        or str(character.project_id) != str(project.id)
        or receipt.get("character_input_hash") != content_hash(_cast_payload_from_character(character))
    ):
        return None
    return deepcopy(profile)


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
    strategy = adopted_character_strategy(project)
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

    from bestseller.services.prompt_source_policy import (
        archive_metadata,
        stamp_reference_cache,
        valid_reference_value,
    )
    stored_strategy = valid_reference_value(project, "character_strategy")
    if (stored_strategy is None
        or normalize_character_strategy(stored_strategy) != strategy):
        project_meta = archive_metadata(
            project_meta, ("character_strategy",), reason="unverified_character_projection",
        )
        project_meta["character_strategy"] = strategy
        context = ReferenceProjectionContext(project, project_meta)
        stamp_reference_cache(context, ("character_strategy",), producer="strategy_compiler")
        project_meta = context.metadata_json
        counts["project_strategy_created"] = 1
        # Assign once below, after the optimization marker is complete. The
        # character SELECT may autoflush; in-place JSON mutations after that
        # would otherwise lose the final marker on a commit/reload.

    characters = list(
        await session.scalars(
            select(CharacterModel).where(CharacterModel.project_id == project.id)
        )
    )
    counts["characters_seen"] = len(characters)
    if (not force and _project_optimization_is_current(project_meta, strategy)
        and all(validated_character_engine_profile(
            project, character.id, (character.metadata_json or {}).get("character_engine_profile"),
            character=character,
        ) is not None for character in characters)):
        counts["projects_skipped_current"] = 1
        return counts

    profile_project = ReferenceProjectionContext(project, project_meta)

    for character in characters:
        meta = _as_mapping(getattr(character, "metadata_json", None))
        current_profile = _as_mapping(meta.get("character_engine_profile"))
        if (not force and _profile_is_current(current_profile, strategy)
            and validated_character_engine_profile(profile_project, character.id, current_profile,
                                                   character=character) is not None):
            counts["profiles_skipped_current"] += 1
            continue

        optimized_profile = build_optimized_character_profile(
            character,
            character_strategy=strategy,
        )
        if current_profile and "character_engine_profile_legacy" not in meta:
            meta["character_engine_profile_legacy"] = current_profile
            counts["legacy_profiles_preserved"] += 1
        character_view = ReferenceProjectionContext(character, meta)
        record_character_engine_profile(profile_project, character_view, optimized_profile,
                                        strategy=strategy)
        meta = character_view.metadata_json
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
        project_meta = profile_project.metadata_json
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
