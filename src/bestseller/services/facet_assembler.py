"""Facet Assembler — dynamically assembles PromptPack fragments from StoryFacets.

Given a complete StoryFacets, this service:
1. Loads relevant YAML fragment files for each dimension value
2. Merges them in priority order (genre > narrative_drive > power_system > ...)
3. Returns a standard PromptPack compatible with existing pipeline

Missing fragments are silently skipped (graceful degradation).
"""

from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel

from bestseller.domain.facets import StoryFacets
from bestseller.services.prompt_packs import (
    ObligatoryScene,
    PromptPack,
    PromptPackFragments,
)

logger = logging.getLogger(__name__)

_FRAGMENTS_DIR = Path(__file__).resolve().parents[3] / "config" / "facets" / "fragments"

# Priority order for fragment merging (higher = applied later = overrides)
_MERGE_PRIORITY = [
    "genre",
    "narrative_drive",
    "power_system",
    "relationship_mode",
    "tone",
    "trope_tags",
]

# Maximum characters per fragment field to prevent prompt bloat
_MAX_FIELD_CHARS = 2000


# ──────────────────────────────────────────────────────────────────────
# Fragment Loading
# ──────────────────────────────────────────────────────────────��───────


class _FragmentData(BaseModel):
    """Raw data from a YAML fragment file."""

    scene_writer: str | None = None
    planner_book_spec: str | None = None
    planner_world_spec: str | None = None
    scene_review: str | None = None
    anti_patterns: list[str] | None = None
    obligatory_scenes: list[dict[str, Any]] | None = None
    structure_guidance: str | None = None
    emotion_engineering: str | None = None
    conflict_stakes: str | None = None
    hook_design: str | None = None
    core_loop: str | None = None
    dialogue_rules: str | None = None
    pacing_guidance: str | None = None
    character_design: str | None = None


@lru_cache(maxsize=128)
def _load_fragment(dimension: str, value: str) -> _FragmentData | None:
    """Load a single fragment YAML file. Returns None if not found."""
    path = _FRAGMENTS_DIR / dimension / f"{value}.yaml"
    if not path.exists():
        return None

    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not raw:
            return None
        return _FragmentData(**{k: v for k, v in raw.items() if k in _FragmentData.model_fields})
    except Exception:
        logger.warning("Failed to load fragment %s/%s.yaml", dimension, value, exc_info=True)
        return None


# ──────────────────────────────────────────────────────────────────────
# Assembly Logic
# ──────────────────────────────────────────────────────────────────────


def assemble_prompt_pack(facets: StoryFacets) -> PromptPack:
    """Assemble a complete PromptPack from StoryFacets dimensions.

    Loads fragments for each dimension value and merges them into a
    standard PromptPack that the existing pipeline can consume.
    """
    # Collect all fragments in priority order
    fragments_ordered: list[_FragmentData] = []
    all_anti_patterns: list[str] = []
    all_obligatory_scenes: list[ObligatoryScene] = []

    # 1. Genre fragment (highest base priority)
    genre_frag = _load_fragment("genre", facets.primary_genre)
    if genre_frag:
        fragments_ordered.append(genre_frag)

    # 2. Narrative drive
    nd_frag = _load_fragment("narrative_drive", facets.narrative_drive)
    if nd_frag:
        fragments_ordered.append(nd_frag)

    # 3. Power system
    if facets.power_system and facets.power_system != "none":
        ps_frag = _load_fragment("power_system", facets.power_system)
        if ps_frag:
            fragments_ordered.append(ps_frag)

    # 4. Relationship mode
    if facets.relationship_mode != "no-cp":
        rm_frag = _load_fragment("relationship_mode", facets.relationship_mode)
        if rm_frag:
            fragments_ordered.append(rm_frag)

    # 5. Tone
    tone_frag = _load_fragment("tone", facets.tone)
    if tone_frag:
        fragments_ordered.append(tone_frag)

    # 6. Trope tags (load each, but keep guidance short)
    for tag in facets.trope_tags[:5]:  # Limit to 5 to control prompt size
        tag_frag = _load_fragment("trope_tags", tag)
        if tag_frag:
            fragments_ordered.append(tag_frag)

    # Merge all fragments
    merged_fragments = _merge_fragments(fragments_ordered)

    # Collect anti_patterns and obligatory_scenes
    for frag in fragments_ordered:
        if frag.anti_patterns:
            all_anti_patterns.extend(frag.anti_patterns)
        if frag.obligatory_scenes:
            for scene_data in frag.obligatory_scenes:
                try:
                    all_obligatory_scenes.append(ObligatoryScene(**scene_data))
                except Exception:
                    pass

    # Deduplicate anti_patterns
    seen: set[str] = set()
    deduped_anti_patterns: list[str] = []
    for ap in all_anti_patterns:
        normalized = ap.strip().lower()
        if normalized not in seen:
            seen.add(normalized)
            deduped_anti_patterns.append(ap.strip())

    # Build PromptPack
    pack_name = _generate_pack_name(facets)
    return PromptPack(
        key=f"facet-assembled-{facets.primary_genre}",
        name=pack_name,
        description=f"Auto-assembled from facets: {facets.primary_genre}/{facets.tone}/{facets.narrative_drive}",
        version="auto",
        genres=[facets.primary_genre] + list(facets.sub_genres),
        tags=list(facets.trope_tags),
        anti_patterns=deduped_anti_patterns,
        fragments=merged_fragments,
        obligatory_scenes=all_obligatory_scenes,
    )


def _merge_fragments(fragments: list[_FragmentData]) -> PromptPackFragments:
    """Merge multiple fragment data into a single PromptPackFragments.

    Strategy: concatenate string fields with double newline separator.
    Each fragment ADDS guidance, never replaces.
    """
    merged: dict[str, str | None] = {}
    text_fields = [
        "scene_writer",
        "planner_book_spec",
        "planner_world_spec",
        "scene_review",
        "structure_guidance",
        "emotion_engineering",
        "conflict_stakes",
        "hook_design",
        "core_loop",
        "dialogue_rules",
        "pacing_guidance",
        "character_design",
    ]

    for field in text_fields:
        parts: list[str] = []
        for frag in fragments:
            value = getattr(frag, field, None)
            if value and value.strip():
                parts.append(value.strip())

        if parts:
            combined = "\n\n".join(parts)
            # Truncate if too long
            if len(combined) > _MAX_FIELD_CHARS:
                combined = combined[:_MAX_FIELD_CHARS] + "\n[...]"
            merged[field] = combined
        else:
            merged[field] = None

    return PromptPackFragments(**merged)


def _generate_pack_name(facets: StoryFacets) -> str:
    """Generate a human-readable pack name from facets."""
    parts = [facets.primary_genre, facets.tone, facets.narrative_drive]
    if facets.sub_genres:
        parts.append("+".join(facets.sub_genres[:2]))
    return " | ".join(parts)


# ──────────────────────────────────────────────────────────────────────
# Convenience: resolve PromptPack from facets (adapter for prompt_packs.py)
# ──────────────────────────────────────────────────────────────────────


def resolve_prompt_pack_from_facets(facets: StoryFacets) -> PromptPack:
    """Public API: given StoryFacets, return a standard PromptPack.

    This is the adapter function that bridges the new facet system
    with the existing planner/drafts/reviews pipeline.
    """
    return assemble_prompt_pack(facets)
