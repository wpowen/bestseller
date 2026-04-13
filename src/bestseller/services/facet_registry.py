"""Facet Registry — loads dimension definitions, validates facets, provides legacy fallback.

This service manages the multi-dimensional classification configuration:
- Loads and caches `config/facets/dimensions.yaml`
- Loads and caches `config/facets/legacy_expansions.yaml`
- Validates StoryFacets values against known dimensions
- Queries existing project facets from DB (for anti-repetition)
"""

from __future__ import annotations

import logging
import random
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.domain.facets import (
    FacetDimension,
    FacetDimensionsCatalog,
    FacetDimensionValue,
    StoryFacets,
)

logger = logging.getLogger(__name__)

_CONFIG_DIR = Path(__file__).resolve().parents[3] / "config" / "facets"


# ──────────────────────────────────────────────────────────────────────
# Config Loading (cached)
# ──────────────────────────────────────────────────────────────────────


@lru_cache(maxsize=1)
def load_facet_dimensions() -> FacetDimensionsCatalog:
    """Load and cache the facet dimensions catalog from YAML config."""
    yaml_path = _CONFIG_DIR / "dimensions.yaml"
    if not yaml_path.exists():
        logger.warning("dimensions.yaml not found at %s, returning empty catalog", yaml_path)
        return FacetDimensionsCatalog()

    with yaml_path.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    dimensions: list[FacetDimension] = []
    for dim_data in raw.get("dimensions", []):
        values = [
            FacetDimensionValue(**v) for v in dim_data.get("values", [])
        ]
        dimensions.append(
            FacetDimension(
                name=dim_data["name"],
                label_zh=dim_data.get("label_zh", ""),
                label_en=dim_data.get("label_en", ""),
                description=dim_data.get("description", ""),
                required=dim_data.get("required", False),
                allow_freeform=dim_data.get("allow_freeform", False),
                max_values=dim_data.get("max_values", 1),
                values=values,
            )
        )

    catalog = FacetDimensionsCatalog(dimensions=dimensions)
    logger.info("Loaded %d facet dimensions with %d total values", len(dimensions), sum(len(d.values) for d in dimensions))
    return catalog


@lru_cache(maxsize=1)
def _load_legacy_expansions_raw() -> dict[str, dict[str, Any]]:
    """Load raw legacy expansion mappings from YAML."""
    yaml_path = _CONFIG_DIR / "legacy_expansions.yaml"
    if not yaml_path.exists():
        logger.warning("legacy_expansions.yaml not found at %s", yaml_path)
        return {}

    with yaml_path.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    return raw.get("expansions", {})


# ──────────────────────────────────────────────────────────────────────
# Validation
# ──────────────────────────────────────────────────────────────────────


def validate_story_facets(facets: StoryFacets) -> list[str]:
    """Validate StoryFacets against dimension definitions.

    Returns a list of warning messages (not errors — AI-generated values
    may be freeform and are allowed to extend beyond the known values).
    """
    warnings: list[str] = []
    catalog = load_facet_dimensions()

    # Check primary_genre against known values
    genre_dim = catalog.get_dimension("primary_genre")
    if genre_dim:
        valid_genres = {v.key for v in genre_dim.values}
        if facets.primary_genre not in valid_genres:
            warnings.append(
                f"primary_genre '{facets.primary_genre}' not in known values. "
                f"This is acceptable for AI-generated facets."
            )

    # Check tone
    tone_dim = catalog.get_dimension("tone")
    if tone_dim:
        valid_tones = {v.key for v in tone_dim.values}
        if facets.tone not in valid_tones:
            warnings.append(f"tone '{facets.tone}' not in known values.")

    # Check narrative_drive
    nd_dim = catalog.get_dimension("narrative_drive")
    if nd_dim:
        valid_drives = {v.key for v in nd_dim.values}
        if facets.narrative_drive not in valid_drives:
            warnings.append(f"narrative_drive '{facets.narrative_drive}' not in known values.")

    return warnings


# ──────────────────────────────────────────────────────────────────────
# Legacy Preset Expansion
# ──────────────────────────────────────────────────────────────────────


def expand_legacy_preset(genre_key: str) -> StoryFacets | None:
    """Expand a legacy genre preset key into StoryFacets.

    Returns None if the key is not found in legacy_expansions.yaml.
    """
    expansions = _load_legacy_expansions_raw()
    data = expansions.get(genre_key)
    if data is None:
        return None

    return StoryFacets(
        primary_genre=data.get("primary_genre", genre_key),
        language=data.get("language", "zh-CN"),
        sub_genres=tuple(data.get("sub_genres", [])),
        setting=data.get("setting", ""),
        tone=data.get("tone", "balanced"),
        power_system=data.get("power_system"),
        relationship_mode=data.get("relationship_mode", "no-cp"),
        narrative_drive=data.get("narrative_drive", "progression"),
        emotional_register=data.get("emotional_register", "balanced"),
        trope_tags=tuple(data.get("trope_tags", [])),
        platform_style=data.get("platform_style"),
        gender_channel=data.get("gender_channel"),
        generation_source="legacy",
    )


def expand_legacy_preset_with_variation(genre_key: str) -> StoryFacets | None:
    """Expand a legacy preset with random variation on trope_tags.

    This provides diversity even in fallback mode: keeps genre and
    narrative_drive fixed, but randomizes 1-2 trope tags from the pool.
    """
    base = expand_legacy_preset(genre_key)
    if base is None:
        return None

    catalog = load_facet_dimensions()
    trope_dim = catalog.get_dimension("trope_tags")
    if not trope_dim or not trope_dim.values:
        return base

    # Determine language-appropriate tags
    all_tags = [v.key for v in trope_dim.values]
    existing_tags = set(base.trope_tags)

    # Pick 1-2 random tags not already in the set
    available = [t for t in all_tags if t not in existing_tags]
    if not available:
        return base

    n_new = min(random.randint(1, 2), len(available))
    new_tags = random.sample(available, n_new)

    # Replace 1-2 of the existing tags (keep at least 2 original)
    current_tags = list(base.trope_tags)
    n_replace = min(n_new, max(0, len(current_tags) - 2))

    if n_replace > 0:
        indices_to_replace = random.sample(range(len(current_tags)), n_replace)
        for i, idx in enumerate(indices_to_replace):
            current_tags[idx] = new_tags[i]
        # Add remaining new tags
        current_tags.extend(new_tags[n_replace:])
    else:
        current_tags.extend(new_tags)

    # Cap at 8
    final_tags = tuple(current_tags[:8])

    return StoryFacets(
        primary_genre=base.primary_genre,
        language=base.language,
        sub_genres=base.sub_genres,
        setting=base.setting,
        tone=base.tone,
        power_system=base.power_system,
        relationship_mode=base.relationship_mode,
        narrative_drive=base.narrative_drive,
        emotional_register=base.emotional_register,
        trope_tags=final_tags,
        platform_style=base.platform_style,
        gender_channel=base.gender_channel,
        generation_source="legacy",
    )


# ──────────────────────────────────────────────────────────────────────
# Existing Facets Query (for anti-repetition)
# ──────────────────────────────────────────────────────────────────────


async def list_existing_facets(
    session: AsyncSession,
    *,
    primary_genre: str | None = None,
    limit: int = 20,
) -> list[StoryFacets]:
    """Query existing projects' StoryFacets from the database.

    Filters by primary_genre if provided, returns most recent N.
    Used by Story Architect Agent to avoid generating duplicate combinations.
    """
    # Query projects that have story_facets in their metadata
    query = text(
        "SELECT metadata->>'story_facets' as facets_json "
        "FROM projects "
        "WHERE metadata->>'story_facets' IS NOT NULL "
        "ORDER BY created_at DESC "
        "LIMIT :limit"
    )
    params: dict[str, Any] = {"limit": limit}

    try:
        result = await session.execute(query, params)
        rows = result.fetchall()
    except Exception:
        logger.warning("Failed to query existing facets from DB", exc_info=True)
        return []

    facets_list: list[StoryFacets] = []
    for row in rows:
        try:
            import json
            data = json.loads(row[0]) if isinstance(row[0], str) else row[0]
            facets = StoryFacets(**data)
            if primary_genre and facets.primary_genre != primary_genre:
                continue
            facets_list.append(facets)
        except Exception:
            continue

    return facets_list


# ──────────────────────────────────────────────────────────────────────
# Utility
# ──────────────────────────────────────────────────────────────────────


def get_trend_data_for_genre(genre_key: str) -> dict[str, Any]:
    """Extract trend data from existing GenrePreset for the Story Architect.

    This bridges the old preset system with the new facet system,
    providing market trend context to the AI agent.
    """
    from bestseller.services.writing_presets import get_genre_preset

    preset = get_genre_preset(genre_key)
    if preset is None:
        return {"trend_keywords": [], "trend_summary": None, "trend_score": 0}

    return {
        "trend_keywords": preset.trend_keywords,
        "trend_summary": preset.trend_summary,
        "trend_score": preset.trend_score,
        "recommended_platforms": preset.recommended_platforms,
        "recommended_audiences": preset.recommended_audiences,
    }


def get_dimensions_summary_for_ai(language: str = "zh-CN") -> str:
    """Generate a concise dimensions summary for the Story Architect prompt.

    Returns a structured text that tells the AI what dimensions and values
    are available, along with current heat scores.
    """
    catalog = load_facet_dimensions()
    lines: list[str] = []

    for dim in catalog.dimensions:
        if dim.name in ("primary_genre", "platform_style", "gender_channel"):
            continue  # These are handled separately

        label = dim.label_zh if language.startswith("zh") else dim.label_en
        lines.append(f"\n### {label} ({dim.name})")
        if dim.description:
            lines.append(f"  {dim.description}")

        # Show top values by heat score
        sorted_values = sorted(dim.values, key=lambda v: v.heat_score, reverse=True)
        top_values = sorted_values[:15]  # Show top 15

        for v in top_values:
            v_label = v.label_zh if language.startswith("zh") else (v.label_en or v.key)
            heat_indicator = "🔥" if v.heat_score >= 80 else ("📈" if v.heat_score >= 65 else "")
            lines.append(f"  - {v.key}: {v_label} {heat_indicator}")

        if len(sorted_values) > 15:
            lines.append(f"  ... and {len(sorted_values) - 15} more options")

    return "\n".join(lines)
