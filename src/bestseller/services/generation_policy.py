from __future__ import annotations

from collections.abc import Mapping
from typing import Any


DEFAULT_NEW_PROJECT_GENERATION_UNIT_MODE = "chapter"
DEFAULT_NEW_PROJECT_PROSE_PROMPT_PROFILE = "lean"

_CHAPTER_MODE_ALIASES = frozenset({"chapter", "chapter_first", "chapter_hybrid"})
_SCENE_MODE_ALIASES = frozenset({"scene", "scene_by_scene"})
_LEAN_PROFILE_ALIASES = frozenset({"lean", "minimal"})
_FULL_PROFILE_ALIASES = frozenset({"full", "legacy"})


def normalize_generation_unit_mode(value: object) -> str | None:
    normalized = str(value or "").strip().lower()
    if normalized in _CHAPTER_MODE_ALIASES:
        return "chapter"
    if normalized in _SCENE_MODE_ALIASES:
        return "scene"
    return None


def normalize_prose_prompt_profile(value: object) -> str | None:
    normalized = str(value or "").strip().lower()
    if normalized in _LEAN_PROFILE_ALIASES:
        return "lean"
    if normalized in _FULL_PROFILE_ALIASES:
        return "full"
    return None


def apply_new_project_generation_policy(
    metadata: Mapping[str, Any] | None,
    *,
    generation_unit_mode: object = None,
    prose_prompt_profile: object = None,
) -> dict[str, Any]:
    """Stamp one canonical generation policy on every newly-created project.

    Explicit create-request values win, followed by values already present in
    metadata. Invalid or absent values receive the current new-project defaults.
    Existing project rows are untouched because this helper only runs at create.
    """

    result = dict(metadata or {})
    result["generation_unit_mode"] = (
        normalize_generation_unit_mode(generation_unit_mode)
        or normalize_generation_unit_mode(result.get("generation_unit_mode"))
        or DEFAULT_NEW_PROJECT_GENERATION_UNIT_MODE
    )
    result["prose_prompt_profile"] = (
        normalize_prose_prompt_profile(prose_prompt_profile)
        or normalize_prose_prompt_profile(result.get("prose_prompt_profile"))
        or DEFAULT_NEW_PROJECT_PROSE_PROMPT_PROFILE
    )
    return result


def generation_unit_preference_from_metadata(
    metadata: Mapping[str, Any] | None,
) -> bool | None:
    """Return chapter-first preference while preserving legacy project keys."""

    if not isinstance(metadata, Mapping):
        return None
    mode = normalize_generation_unit_mode(metadata.get("generation_unit_mode"))
    if mode is not None:
        return mode == "chapter"
    legacy = metadata.get("chapter_first_generation")
    if isinstance(legacy, bool):
        return legacy
    if metadata.get("generation_mode") == "chapter_first_single_pass":
        return True
    return None
