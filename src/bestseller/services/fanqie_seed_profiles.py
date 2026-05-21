"""Seed Fanqie market profiles for offline project bootstrapping."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import yaml

from bestseller.domain.fanqie_market import FanqieCategoryProfile, FanqieCraftProfile
from bestseller.services.fanqie_market_analyzer import build_craft_profile

DEFAULT_FANQIE_PROFILE_DIR = Path("config/market_profiles/fanqie")


def list_fanqie_seed_profile_keys(
    profile_dir: Path = DEFAULT_FANQIE_PROFILE_DIR,
) -> list[str]:
    """Return available seed profile keys."""

    if not profile_dir.exists():
        return []
    return sorted(path.stem for path in profile_dir.glob("*.yaml"))


def load_fanqie_seed_profile(
    profile_key: str,
    profile_dir: Path = DEFAULT_FANQIE_PROFILE_DIR,
) -> dict[str, Any]:
    """Load one seed profile YAML payload."""

    safe_key = profile_key.strip()
    if not safe_key:
        raise ValueError("profile_key is required.")
    path = profile_dir / f"{safe_key}.yaml"
    if not path.exists():
        available = ", ".join(list_fanqie_seed_profile_keys(profile_dir)) or "none"
        raise ValueError(
            f"Fanqie seed profile '{profile_key}' was not found. Available: {available}"
        )
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(payload, dict):
        raise ValueError(f"Fanqie seed profile '{profile_key}' must be a mapping.")
    if payload.get("profile_key") != safe_key:
        raise ValueError(f"Fanqie seed profile '{profile_key}' has mismatched profile_key.")
    return payload


def seed_profile_to_category_profile(
    payload: dict[str, Any],
    *,
    data_date: date | None = None,
) -> FanqieCategoryProfile:
    """Compile a seed YAML payload into the category-profile contract."""

    profile_key = str(payload.get("profile_key") or "").strip()
    category = str(payload.get("category") or "").strip()
    if not profile_key or not category:
        raise ValueError("Fanqie seed profile requires profile_key and category.")

    entry_pressure = _as_list(payload.get("entry_pressure_patterns"))
    advantage = _as_list(payload.get("advantage_patterns"))
    chapter_loop = _as_list(payload.get("chapter_loop"))
    style_controls = _as_list(payload.get("style_controls"))
    copy_boundaries = _as_list(payload.get("copy_boundaries"))
    anti_patterns = _as_list(payload.get("anti_patterns"))
    reader_promise = _as_list(payload.get("reader_promise"))

    return FanqieCategoryProfile(
        category=category,
        board_type="seed",
        channel="fanqie",
        data_date=data_date or date.today(),
        sample_size=0,
        reader_heat_stats={},
        dominant_settings=reader_promise,
        protagonist_archetypes=advantage,
        hook_patterns=entry_pressure,
        structure_patterns=chapter_loop,
        payoff_patterns=reader_promise + advantage,
        style_guidelines=style_controls,
        safety_notes=copy_boundaries + anti_patterns,
        evidence_profile_ids=[profile_key],
        confidence=0.6,
    )


def seed_profile_to_craft_profile(payload: dict[str, Any]) -> FanqieCraftProfile:
    """Compile a seed YAML payload into a prompt-ready craft profile."""

    category_profile = seed_profile_to_category_profile(payload)
    craft_profile = build_craft_profile(category_profile)
    copy_boundaries = _as_list(payload.get("copy_boundaries"))
    anti_patterns = _as_list(payload.get("anti_patterns"))
    return craft_profile.model_copy(
        update={
            "disallowed_copy_targets": _dedupe(
                [*craft_profile.disallowed_copy_targets, *copy_boundaries, *anti_patterns]
            ),
            "safety_boundary": "；".join(copy_boundaries)
            or craft_profile.safety_boundary,
        }
    )


def seed_profile_to_artifacts(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Return project metadata/artifact payloads for a seed profile."""

    category_profile = seed_profile_to_category_profile(payload)
    craft_profile = seed_profile_to_craft_profile(payload)
    summary = {
        "source": "fanqie_seed_profile",
        "profile_key": payload["profile_key"],
        "category": category_profile.category,
        "board_type": category_profile.board_type,
        "data_date": category_profile.data_date.isoformat(),
        "sample_size": category_profile.sample_size,
        "top_titles": [],
        "dominant_settings": category_profile.dominant_settings,
        "hook_patterns": category_profile.hook_patterns,
        "structure_patterns": category_profile.structure_patterns,
        "craft_confidence": craft_profile.confidence,
    }
    return {
        "summary": summary,
        "category_profile": category_profile.model_dump(mode="json"),
        "craft_profile": craft_profile.model_dump(mode="json"),
    }


def _as_list(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value else []
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()] if str(value).strip() else []


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        normalized = value.strip()
        if normalized and normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
    return result
