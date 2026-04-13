"""Facet Review Blender — dynamic review weight mixing from StoryFacets dimensions.

Instead of mapping to one of 9 fixed genre review categories, this service
computes review weights by summing per-dimension deltas from a baseline of 1.0.

Formula: final_weight[dim] = clamp(1.0 + sum(all_deltas[dim]), 0.2, 2.5)
"""

from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from bestseller.domain.facets import StoryFacets
from bestseller.services.genre_review_profiles import (
    GenreReviewProfile,
    GenreReviewWeights,
)

logger = logging.getLogger(__name__)

_WEIGHTS_DIR = Path(__file__).resolve().parents[3] / "config" / "facets" / "review_weights"

_WEIGHT_MIN = 0.2
_WEIGHT_MAX = 2.5


# ──────────────────────────────────────────────────────────────────────
# Weight Delta Loading
# ──────────────────────────────────────────────────────────────────────


@lru_cache(maxsize=64)
def _load_weight_deltas(dimension: str, value: str) -> dict[str, float]:
    """Load weight deltas from config/facets/review_weights/{dimension}/{value}.yaml.

    Returns empty dict if file not found (graceful degradation).
    """
    path = _WEIGHTS_DIR / dimension / f"{value}.yaml"
    if not path.exists():
        return {}

    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not raw:
            return {}
        return raw.get("scene_weights", {})
    except Exception:
        logger.warning("Failed to load review weights %s/%s.yaml", dimension, value, exc_info=True)
        return {}


# ──────────────────────────────────────────────────────────────────────
# Blending Logic
# ──────────────────────────────────────────────────────────────────────


def blend_review_weights(facets: StoryFacets) -> GenreReviewWeights:
    """Compute blended GenreReviewWeights from all facet dimensions.

    Each dimension value can contribute positive or negative deltas
    to any weight field. Deltas are summed and clamped.
    """
    # Collect all applicable deltas
    all_deltas: list[dict[str, float]] = []

    # Narrative drive (strongest influence on review weights)
    deltas = _load_weight_deltas("narrative_drive", facets.narrative_drive)
    if deltas:
        all_deltas.append(deltas)

    # Tone
    deltas = _load_weight_deltas("tone", facets.tone)
    if deltas:
        all_deltas.append(deltas)

    # Emotional register
    deltas = _load_weight_deltas("emotional_register", facets.emotional_register)
    if deltas:
        all_deltas.append(deltas)

    # Relationship mode
    if facets.relationship_mode != "no-cp":
        deltas = _load_weight_deltas("relationship_mode", facets.relationship_mode)
        if deltas:
            all_deltas.append(deltas)

    # Power system
    if facets.power_system and facets.power_system != "none":
        deltas = _load_weight_deltas("power_system", facets.power_system)
        if deltas:
            all_deltas.append(deltas)

    # If no deltas found, return default weights
    if not all_deltas:
        return GenreReviewWeights()

    # Sum deltas per field and apply to baseline of 1.0
    sum_deltas: dict[str, float] = {}
    for delta_set in all_deltas:
        for field, value in delta_set.items():
            sum_deltas[field] = sum_deltas.get(field, 0.0) + value

    # Build final weights
    weight_fields = GenreReviewWeights.model_fields
    final: dict[str, float] = {}

    for field_name in weight_fields:
        baseline = 1.0 if field_name != "methodology_compliance" else 0.8
        delta = sum_deltas.get(field_name, 0.0)
        clamped = max(_WEIGHT_MIN, min(_WEIGHT_MAX, baseline + delta))
        final[field_name] = round(clamped, 3)

    return GenreReviewWeights(**final)


def build_facet_review_profile(facets: StoryFacets) -> GenreReviewProfile:
    """Build a complete GenreReviewProfile from StoryFacets.

    This creates a dynamic profile rather than selecting from 9 fixed categories.
    """
    weights = blend_review_weights(facets)

    # Generate descriptive name
    name_parts = [facets.primary_genre, facets.tone, facets.narrative_drive]
    name = " + ".join(name_parts)

    return GenreReviewProfile(
        category_key=f"facet-blended-{facets.primary_genre}",
        name=f"Facet-Blended: {name}",
        description=(
            f"Dynamically blended review profile for {facets.primary_genre} "
            f"with tone={facets.tone}, drive={facets.narrative_drive}."
        ),
        scene_weights=weights,
    )
