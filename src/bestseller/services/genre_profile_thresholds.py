"""Phase A2 — Per-genre numeric threshold profiles.

Adapted from lingfengQAQ/webnovel-writer's ``genre-profiles.md``. The
existing ``genre_review_profiles.py`` is prose + scoring-weight heavy; it
doesn't centralize the numeric knobs (hook baseline, coolpoint density,
strand max-gap, debt multiplier, etc.) that Phase B/C/D need. This module
adds that structured layer without touching the existing prose profiles.

The loader reads ``config/genre_profile_thresholds/<genre_id>.yaml`` and
caches the parsed dataclasses. Callers should access via
``resolve_thresholds(genre_id)`` which falls back to
``action-progression`` for unknown IDs.

Existing genre IDs (derived from genre_review_profiles.py):
    default
    action-progression
    relationship-driven
    suspense-mystery
    strategy-worldbuilding
    esports-competition
    female-growth-ncp
    base-building
    eastern-aesthetic

Pack-level overrides (e.g. ``config/prompt_packs/xianxia-upgrade-core.yaml``
fields like ``emotion_spring_min_chapters``) still take precedence — this
module is the structured default, not a replacement.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal, Mapping

import yaml


logger = logging.getLogger(__name__)


# Canonical location of per-genre YAML files. Resolved relative to the
# repo root at import time. Tests override by calling ``_reset_cache`` and
# setting ``_THRESHOLDS_DIR``.
_THRESHOLDS_DIR = (
    Path(__file__).resolve().parents[3] / "config" / "genre_profile_thresholds"
)


# Canonical genre IDs. Missing lookups fall back to the first value.
KNOWN_GENRE_IDS: tuple[str, ...] = (
    "action-progression",
    "relationship-driven",
    "suspense-mystery",
    "strategy-worldbuilding",
    "esports-competition",
    "female-growth-ncp",
    "base-building",
    "eastern-aesthetic",
)


DEFAULT_FALLBACK_GENRE = "action-progression"


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class HookConfig:
    """Cliffhanger baseline per genre.

    ``preferred_types`` draw from the 5-type taxonomy (webnovel-writer):
        危机 / 悬念 / 情绪 / 选择 / 渴望
    ``strength_baseline`` is the minimum quality bar for a chapter-end hook.
    ``transition_allowance`` caps how many chapters may run at weaker hooks
    (e.g. transitional / world-building segments)."""

    preferred_types: tuple[str, ...] = ()
    strength_baseline: Literal["strong", "medium", "weak"] = "medium"
    chapter_end_required: bool = True
    transition_allowance: int = 2


@dataclass(frozen=True)
class CoolpointConfig:
    """装逼打脸/爽感 patterns.

    ``density_per_chapter`` maps to the qualitative band; numeric targets
    are carried by ``hype_engine.HYPE_DENSITY_CURVE`` which remains the
    authoritative count source. ``combo_interval`` is the max chapters
    between combo moments; ``milestone_interval`` the max between
    big set-piece moments."""

    preferred_patterns: tuple[str, ...] = ()
    density_per_chapter: Literal["high", "medium", "low"] = "medium"
    combo_interval: int = 5
    milestone_interval: int = 20


@dataclass(frozen=True)
class MicropayoffConfig:
    """Small reader rewards per chapter (beyond the main cliffhanger).

    ``min_per_chapter`` is the floor for small satisfactions (a joke
    landing, a tiny reveal, a 'finally paid off' beat). ``transition_min``
    covers quieter chapters where even ``min_per_chapter`` is relaxed."""

    preferred_types: tuple[str, ...] = ()
    min_per_chapter: int = 2
    transition_min: int = 1


@dataclass(frozen=True)
class PacingThresholds:
    """Gap budgets Phase B consumes.

    ``strand_max_gap`` — max consecutive chapters any single narrative line
    can be *not-dominant* before Phase B flags. Our 4 lines:
        overt (明线) / undercurrent (暗线) / hidden (伏线) / core_axis (主题轴)
    ``stagnation_threshold`` — chapters without a beat-progression event.
    ``transition_max_consecutive`` — max chapters in a row tagged as
    transitional (low-heat) before pacing flags."""

    stagnation_threshold: int = 3
    strand_max_gap: Mapping[str, int] = field(
        default_factory=lambda: {
            "overt": 5,
            "undercurrent": 10,
            "hidden": 15,
            "core_axis": 20,
        }
    )
    transition_max_consecutive: int = 2


@dataclass(frozen=True)
class OverrideConfig:
    """Phase C override contract knobs."""

    allowed_rationale_types: tuple[str, ...] = (
        "TRANSITIONAL_SETUP",
        "LOGIC_INTEGRITY",
        "CHARACTER_CREDIBILITY",
        "WORLD_RULE_CONSTRAINT",
        "ARC_TIMING",
        "GENRE_CONVENTION",
        "EDITORIAL_INTENT",
    )
    debt_multiplier: float = 1.0
    payback_window_default: int = 5
    interest_rate_per_chapter: float = 0.10


@dataclass(frozen=True)
class GenreProfileThresholds:
    """Everything numeric about a genre, in one dataclass."""

    id: str
    name: str
    hook_config: HookConfig = field(default_factory=HookConfig)
    coolpoint_config: CoolpointConfig = field(default_factory=CoolpointConfig)
    micropayoff_config: MicropayoffConfig = field(default_factory=MicropayoffConfig)
    pacing_config: PacingThresholds = field(default_factory=PacingThresholds)
    override_config: OverrideConfig = field(default_factory=OverrideConfig)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "hook_config": {
                "preferred_types": list(self.hook_config.preferred_types),
                "strength_baseline": self.hook_config.strength_baseline,
                "chapter_end_required": self.hook_config.chapter_end_required,
                "transition_allowance": self.hook_config.transition_allowance,
            },
            "coolpoint_config": {
                "preferred_patterns": list(self.coolpoint_config.preferred_patterns),
                "density_per_chapter": self.coolpoint_config.density_per_chapter,
                "combo_interval": self.coolpoint_config.combo_interval,
                "milestone_interval": self.coolpoint_config.milestone_interval,
            },
            "micropayoff_config": {
                "preferred_types": list(self.micropayoff_config.preferred_types),
                "min_per_chapter": self.micropayoff_config.min_per_chapter,
                "transition_min": self.micropayoff_config.transition_min,
            },
            "pacing_config": {
                "stagnation_threshold": self.pacing_config.stagnation_threshold,
                "strand_max_gap": dict(self.pacing_config.strand_max_gap),
                "transition_max_consecutive": self.pacing_config.transition_max_consecutive,
            },
            "override_config": {
                "allowed_rationale_types": list(self.override_config.allowed_rationale_types),
                "debt_multiplier": self.override_config.debt_multiplier,
                "payback_window_default": self.override_config.payback_window_default,
                "interest_rate_per_chapter": self.override_config.interest_rate_per_chapter,
            },
        }


# ---------------------------------------------------------------------------
# YAML parsing
# ---------------------------------------------------------------------------


def parse_thresholds(data: Mapping[str, Any]) -> GenreProfileThresholds:
    """Parse a YAML-loaded dict into a ``GenreProfileThresholds``.

    Missing sub-sections fall back to the dataclass defaults. Unknown keys
    inside a sub-section are logged and ignored — typos should surface in
    the test that exercises each YAML file but should not crash the loader."""

    return GenreProfileThresholds(
        id=str(data.get("id", "unknown")),
        name=str(data.get("name", data.get("id", "Unknown"))),
        hook_config=_parse_hook(data.get("hook_config", {})),
        coolpoint_config=_parse_coolpoint(data.get("coolpoint_config", {})),
        micropayoff_config=_parse_micropayoff(data.get("micropayoff_config", {})),
        pacing_config=_parse_pacing(data.get("pacing_config", {})),
        override_config=_parse_override(data.get("override_config", {})),
    )


def _parse_hook(data: Mapping[str, Any]) -> HookConfig:
    return HookConfig(
        preferred_types=tuple(str(t) for t in data.get("preferred_types", ())),
        strength_baseline=_coerce_strength(data.get("strength_baseline", "medium")),
        chapter_end_required=bool(data.get("chapter_end_required", True)),
        transition_allowance=int(data.get("transition_allowance", 2)),
    )


def _parse_coolpoint(data: Mapping[str, Any]) -> CoolpointConfig:
    return CoolpointConfig(
        preferred_patterns=tuple(str(p) for p in data.get("preferred_patterns", ())),
        density_per_chapter=_coerce_density(data.get("density_per_chapter", "medium")),
        combo_interval=int(data.get("combo_interval", 5)),
        milestone_interval=int(data.get("milestone_interval", 20)),
    )


def _parse_micropayoff(data: Mapping[str, Any]) -> MicropayoffConfig:
    return MicropayoffConfig(
        preferred_types=tuple(str(t) for t in data.get("preferred_types", ())),
        min_per_chapter=int(data.get("min_per_chapter", 2)),
        transition_min=int(data.get("transition_min", 1)),
    )


def _parse_pacing(data: Mapping[str, Any]) -> PacingThresholds:
    defaults = {"overt": 5, "undercurrent": 10, "hidden": 15, "core_axis": 20}
    raw_gap = data.get("strand_max_gap") or {}
    gap = {**defaults}
    for k, v in raw_gap.items():
        if k in defaults:
            gap[k] = int(v)
        else:
            logger.warning("Unknown strand_max_gap key %r ignored", k)
    return PacingThresholds(
        stagnation_threshold=int(data.get("stagnation_threshold", 3)),
        strand_max_gap=gap,
        transition_max_consecutive=int(data.get("transition_max_consecutive", 2)),
    )


def _parse_override(data: Mapping[str, Any]) -> OverrideConfig:
    defaults = OverrideConfig()
    return OverrideConfig(
        allowed_rationale_types=tuple(
            str(r)
            for r in data.get("allowed_rationale_types", defaults.allowed_rationale_types)
        ),
        debt_multiplier=float(data.get("debt_multiplier", defaults.debt_multiplier)),
        payback_window_default=int(
            data.get("payback_window_default", defaults.payback_window_default)
        ),
        interest_rate_per_chapter=float(
            data.get("interest_rate_per_chapter", defaults.interest_rate_per_chapter)
        ),
    )


def _coerce_strength(value: Any) -> Literal["strong", "medium", "weak"]:
    s = str(value).lower()
    if s in ("strong", "medium", "weak"):
        return s  # type: ignore[return-value]
    return "medium"


def _coerce_density(value: Any) -> Literal["high", "medium", "low"]:
    s = str(value).lower()
    if s in ("high", "medium", "low"):
        return s  # type: ignore[return-value]
    return "medium"


# ---------------------------------------------------------------------------
# Cached loader
# ---------------------------------------------------------------------------


@lru_cache(maxsize=32)
def load_thresholds(genre_id: str) -> GenreProfileThresholds:
    """Load and cache thresholds for ``genre_id``.

    Falls back to ``DEFAULT_FALLBACK_GENRE`` if the requested file is
    missing. Emits a warning so silently using the fallback is visible."""

    path = _THRESHOLDS_DIR / f"{genre_id}.yaml"
    if not path.exists():
        if genre_id != DEFAULT_FALLBACK_GENRE:
            logger.warning(
                "Genre thresholds missing for %s; falling back to %s",
                genre_id,
                DEFAULT_FALLBACK_GENRE,
            )
            return load_thresholds(DEFAULT_FALLBACK_GENRE)
        # Fallback file itself is missing — return bare defaults so the
        # pipeline still has something workable.
        logger.warning(
            "Fallback genre thresholds file missing at %s; using dataclass defaults",
            path,
        )
        return GenreProfileThresholds(id=genre_id, name=genre_id)

    with path.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    return parse_thresholds(raw)


def resolve_thresholds(genre_id: str | None) -> GenreProfileThresholds:
    """Public entrypoint used by Phase B/C/D.

    ``genre_id=None`` always returns the fallback. The loader is already
    cached per-id; this wrapper just normalizes input."""

    gid = (genre_id or DEFAULT_FALLBACK_GENRE).strip() or DEFAULT_FALLBACK_GENRE
    return load_thresholds(gid)


def _reset_cache() -> None:
    """Test hook — clear the loader cache so YAML edits take effect."""

    load_thresholds.cache_clear()
