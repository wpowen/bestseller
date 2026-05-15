"""Prose Style Anchors loader (``config/prose_style_anchors.yaml``).

Provides:

* per-anchor metadata (Lu Xun cold, Yan Leisheng, Jin Yong dialogue,
  Hemingway short, etc.)
* the cross-anchor anti-AI-voice baseline with its banned patterns
  (used by the shared detector module)
* prompt rendering for the writer
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache

from bestseller.services.quality_levers._loader import (
    as_dict,
    as_str,
    as_str_tuple,
    load_yaml,
)


_CONFIG_FILENAME = "prose_style_anchors.yaml"
_ANTI_AI_ANCHOR_ID = "anti_ai_voice"


@dataclass(frozen=True)
class BannedPattern:
    """One ``anti_ai_voice.banned_patterns`` entry."""

    pattern_id: str
    pattern: str
    replacement: str
    example_before: str = ""
    example_after: str = ""


@dataclass(frozen=True)
class StyleAnchor:
    """One ``anchors.<id>`` entry."""

    anchor_id: str
    display_name: str
    category: str
    description: str
    sentence_features: dict[str, str]
    vocabulary: dict[str, str]
    metaphor_type: str
    pov_treatment: str
    emotion_via: str
    when_to_use: tuple[str, ...]
    banned_patterns: tuple[BannedPattern, ...] = ()


@dataclass(frozen=True)
class ProseStyleAnchorsConfig:
    """Typed view over the YAML."""

    version: str
    selection_principles: tuple[str, ...]
    anchors: dict[str, StyleAnchor]


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------


def _flatten_dict_to_str(raw: object) -> dict[str, str]:
    """Coerce a nested dict into a ``str -> str`` map.

    For composite values (lists / nested dicts) we render a compact
    "; "-joined representation so downstream renderers can drop the
    payload directly into a prompt fragment.
    """

    data = as_dict(raw)
    out: dict[str, str] = {}
    for key, value in data.items():
        key_str = as_str(key)
        if not key_str:
            continue
        if isinstance(value, str):
            out[key_str] = value.strip()
        elif isinstance(value, (list, tuple)):
            out[key_str] = "; ".join(
                as_str(item) for item in value if as_str(item)
            )
        elif isinstance(value, dict):
            out[key_str] = "; ".join(
                f"{k}: {v}" for k, v in value.items() if as_str(v)
            )
        else:
            out[key_str] = as_str(value)
    return out


def _parse_banned_pattern(raw: object) -> BannedPattern | None:
    data = as_dict(raw)
    pattern_id = as_str(data.get("id"))
    if not pattern_id:
        return None
    pattern = data.get("pattern")
    if isinstance(pattern, (list, tuple)):
        pattern_text = " | ".join(as_str(item) for item in pattern if as_str(item))
    else:
        pattern_text = as_str(pattern)
    if not pattern_text:
        return None
    return BannedPattern(
        pattern_id=pattern_id,
        pattern=pattern_text,
        replacement=as_str(data.get("replacement")),
        example_before=as_str(data.get("example_before")),
        example_after=as_str(data.get("example_after")),
    )


def _parse_anchor(anchor_id: str, raw: object) -> StyleAnchor:
    data = as_dict(raw)
    banned_raw = data.get("banned_patterns")
    banned: list[BannedPattern] = []
    if isinstance(banned_raw, list):
        for entry in banned_raw:
            parsed = _parse_banned_pattern(entry)
            if parsed is not None:
                banned.append(parsed)
    return StyleAnchor(
        anchor_id=anchor_id,
        display_name=as_str(data.get("display_name"), default=anchor_id),
        category=as_str(data.get("category")),
        description=as_str(data.get("description")),
        sentence_features=_flatten_dict_to_str(data.get("sentence_features")),
        vocabulary=_flatten_dict_to_str(data.get("vocabulary")),
        metaphor_type=as_str(data.get("metaphor_type")),
        pov_treatment=as_str(data.get("pov_treatment")),
        emotion_via=as_str(data.get("emotion_via")),
        when_to_use=as_str_tuple(data.get("when_to_use")),
        banned_patterns=tuple(banned),
    )


@lru_cache(maxsize=1)
def load_prose_style_anchors() -> ProseStyleAnchorsConfig:
    """Return the typed view over ``prose_style_anchors.yaml``."""

    raw = load_yaml(_CONFIG_FILENAME)
    anchors_raw = as_dict(raw.get("anchors"))
    anchors: dict[str, StyleAnchor] = {}
    for anchor_id, anchor_raw in anchors_raw.items():
        canonical = as_str(anchor_id)
        if not canonical:
            continue
        anchors[canonical] = _parse_anchor(canonical, anchor_raw)
    return ProseStyleAnchorsConfig(
        version=as_str(raw.get("version")),
        selection_principles=as_str_tuple(raw.get("selection_principles")),
        anchors=anchors,
    )


def get_style_anchor(anchor_id: str) -> StyleAnchor | None:
    """Look up one anchor."""

    if not anchor_id:
        return None
    return load_prose_style_anchors().anchors.get(anchor_id)


def get_anti_ai_banned_patterns() -> tuple[BannedPattern, ...]:
    """Return the full banned-pattern list from the ``anti_ai_voice`` anchor."""

    anchor = get_style_anchor(_ANTI_AI_ANCHOR_ID)
    return anchor.banned_patterns if anchor is not None else ()


# ---------------------------------------------------------------------------
# Prompt rendering
# ---------------------------------------------------------------------------


def render_style_anchor_block(
    *,
    anchor_ids: tuple[str, ...] | list[str],
) -> str:
    """Render a writer-facing prompt fragment for a set of anchors.

    The ``anti_ai_voice`` baseline is always appended (regardless of
    whether the caller passed it explicitly), because it is non-optional.
    """

    config = load_prose_style_anchors()
    seen: set[str] = set()
    ordered: list[StyleAnchor] = []
    for raw_id in anchor_ids:
        identifier = as_str(raw_id)
        anchor = config.anchors.get(identifier)
        if anchor is None or identifier in seen:
            continue
        seen.add(identifier)
        ordered.append(anchor)
    baseline = config.anchors.get(_ANTI_AI_ANCHOR_ID)
    if baseline is not None and baseline.anchor_id not in seen:
        ordered.append(baseline)

    if not ordered:
        return ""

    lines: list[str] = ["【风格锚点】"]
    for anchor in ordered:
        lines.append(f"- {anchor.anchor_id} ({anchor.display_name})")
        if anchor.sentence_features:
            lines.append(
                "  句法: "
                + "; ".join(f"{k}={v}" for k, v in anchor.sentence_features.items())
            )
        if anchor.vocabulary:
            lines.append(
                "  词库: "
                + "; ".join(f"{k}={v}" for k, v in anchor.vocabulary.items())
            )
        if anchor.emotion_via:
            lines.append(f"  情绪靠: {anchor.emotion_via}")
        if anchor.banned_patterns:
            ban_lines = [
                f"{bp.pattern_id}: {bp.pattern} → {bp.replacement}"
                for bp in anchor.banned_patterns
            ]
            lines.append("  禁用模式 (出现一次扣分):")
            lines.extend("    " + line for line in ban_lines)
    return "\n".join(lines)
