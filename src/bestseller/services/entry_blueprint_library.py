from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
import json
from pathlib import Path
import re

_DIMENSION_PRIORITY = {
    "entry_blueprints": 100,
    "power_systems": 85,
    "device_templates": 80,
    "plot_patterns": 75,
    "scene_templates": 65,
    "anti_cliche_patterns": 60,
    "thematic_motifs": 50,
    "character_archetypes": 35,
}

_SOURCE_SPECIFIC_PATTERNS = (
    "named_artifacts",
    "named_techniques",
    "named_mount",
    "source names",
    "specific_",
    "具体开局",
    "具体组合",
    "具体法宝",
    "具体功法",
)

_PATH_OR_SECRET_RE = re.compile(r"(/Users/|/private/|[A-Za-z]:\\\\|source_ref|book title)", re.I)


@dataclass(frozen=True, slots=True)
class EntryBlueprint:
    """Abstract reusable entry-system design component.

    Blueprints are safe, anonymized mechanisms. They must not carry source
    names, concrete item names, or copyable story chains.
    """

    blueprint_id: str
    dimension: str
    name: str
    mechanism_summary: str
    applicable_genres: tuple[str, ...] = ()
    entry_types: tuple[str, ...] = ()
    required_fields: tuple[str, ...] = ()
    state_variables: tuple[str, ...] = ()
    valid_acquisition_patterns: tuple[str, ...] = ()
    required_cost_patterns: tuple[str, ...] = ()
    reader_rewards: tuple[str, ...] = ()
    anti_copy_boundaries: tuple[str, ...] = ()
    source_lineage: tuple[dict[str, object], ...] = ()
    tags: tuple[str, ...] = ()
    confidence: float = 0.0
    status: str = "active"
    content_json: dict[str, object] = field(default_factory=dict)


def _as_mapping(value: object) -> dict[str, object]:
    return dict(value) if isinstance(value, Mapping) else {}


def _as_sequence(value: object) -> list[object]:
    if value is None:
        return []
    if isinstance(value, list | tuple):
        return list(value)
    return [value]


def _text(value: object) -> str:
    if isinstance(value, str):
        return value.strip()
    if value is None:
        return ""
    if isinstance(value, bool):
        return ""
    return str(value).strip()


def _string_tuple(value: object) -> tuple[str, ...]:
    seen: set[str] = set()
    out: list[str] = []
    for raw in _as_sequence(value):
        text = _text(raw)
        if text and text not in seen:
            seen.add(text)
            out.append(text)
    return tuple(out)


def _float(value: object, default: float = 0.0) -> float:
    if isinstance(value, bool):
        return default
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return default
    return default


def _looks_source_specific(row: Mapping[str, object]) -> bool:
    haystack = " ".join(
        [
            _text(row.get("slug")),
            _text(row.get("name")),
            _text(row.get("narrative_summary")),
            json.dumps(_as_mapping(row.get("content_json")), ensure_ascii=False),
        ]
    )
    normalized = haystack.lower()
    if _PATH_OR_SECRET_RE.search(haystack):
        return True
    return any(pattern.lower() in normalized for pattern in _SOURCE_SPECIFIC_PATTERNS)


def _entry_types_for_dimension(
    dimension: str,
    content_json: Mapping[str, object],
) -> tuple[str, ...]:
    explicit = _string_tuple(content_json.get("entry_types"))
    if explicit:
        return explicit
    if dimension == "power_systems":
        return ("cultivation_method", "technique", "resource")
    if dimension == "device_templates":
        return ("artifact", "companion_asset", "motif")
    if dimension == "thematic_motifs":
        return ("motif",)
    if dimension == "scene_templates":
        return ("evidence", "resource", "identity")
    if dimension == "anti_cliche_patterns":
        return ("anti_cliche",)
    if dimension == "plot_patterns":
        return ("artifact", "resource", "identity", "evidence")
    return ()


def _lineage_from_content(
    content_json: Mapping[str, object],
    *,
    fallback_source_id: str | None,
    confidence: float,
) -> tuple[dict[str, object], ...]:
    source_ids = _string_tuple(content_json.get("distillation_source_ids"))
    if not source_ids and fallback_source_id:
        source_ids = (fallback_source_id,)
    return tuple({"source_id": source_id, "confidence": confidence} for source_id in source_ids)


def blueprint_from_material_row(
    row: Mapping[str, object],
    *,
    min_confidence: float = 0.6,
) -> EntryBlueprint | None:
    """Convert one reviewed active material row into an abstract blueprint."""

    if _text(row.get("status") or "active") != "active":
        return None
    confidence = _float(row.get("confidence"))
    if confidence < min_confidence:
        return None
    if _looks_source_specific(row):
        return None

    slug = _text(row.get("slug"))
    name = _text(row.get("name"))
    summary = _text(row.get("narrative_summary"))
    dimension = _text(row.get("dimension")) or "entry_blueprints"
    if not slug or not name or not summary:
        return None

    content_json = _as_mapping(row.get("content_json"))
    genres = tuple(
        item
        for item in (
            _text(row.get("genre")),
            _text(row.get("sub_genre")),
            *_string_tuple(content_json.get("applicable_genres")),
        )
        if item
    )
    source_id = _text(row.get("source_id")) or None

    return EntryBlueprint(
        blueprint_id=slug,
        dimension=dimension,
        name=name,
        mechanism_summary=summary,
        applicable_genres=genres,
        entry_types=_entry_types_for_dimension(dimension, content_json),
        required_fields=_string_tuple(content_json.get("required_fields")),
        state_variables=_string_tuple(content_json.get("state_variables")),
        valid_acquisition_patterns=_string_tuple(
            content_json.get("valid_acquisition_patterns")
            or content_json.get("valid_sources")
            or content_json.get("acquisition_patterns")
        ),
        required_cost_patterns=_string_tuple(
            content_json.get("required_cost_patterns")
            or content_json.get("required_cost")
            or content_json.get("costs")
        ),
        reader_rewards=_string_tuple(
            content_json.get("reader_rewards") or content_json.get("payoff_types")
        ),
        anti_copy_boundaries=_string_tuple(
            content_json.get("anti_copy_boundaries")
            or content_json.get("blocked_elements")
            or content_json.get("replacement_rule")
        ),
        source_lineage=_lineage_from_content(
            content_json,
            fallback_source_id=source_id,
            confidence=confidence,
        ),
        tags=_string_tuple(row.get("tags_json") or row.get("tags")),
        confidence=confidence,
        status="active",
        content_json=dict(content_json),
    )


def load_active_material_rows(paths: Sequence[Path]) -> list[dict[str, object]]:
    """Load active material JSONL rows from one or more paths."""

    rows: list[dict[str, object]] = []
    for path in paths:
        if not path.is_file():
            continue
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict) and _text(row.get("status") or "active") == "active":
                rows.append(row)
    return rows


def _keyword_score(blueprint: EntryBlueprint, keywords: Sequence[str]) -> int:
    if not keywords:
        return 0
    haystack = " ".join(
        [
            blueprint.name,
            blueprint.mechanism_summary,
            " ".join(blueprint.state_variables),
            " ".join(blueprint.tags),
        ]
    ).lower()
    return sum(1 for keyword in keywords if keyword and keyword.lower() in haystack)


def _genre_score(blueprint: EntryBlueprint, genre: str | None, sub_genre: str | None) -> int:
    candidates = {item.lower() for item in blueprint.applicable_genres if item}
    score = 0
    for value, points in ((genre, 30), (sub_genre, 25)):
        text = (value or "").strip().lower()
        if not text:
            continue
        if text in candidates:
            score += points
        elif any(text in candidate or candidate in text for candidate in candidates):
            score += points // 2
    return score


def select_entry_blueprints(
    rows: Sequence[Mapping[str, object] | EntryBlueprint],
    *,
    genre: str | None = None,
    sub_genre: str | None = None,
    story_keywords: Sequence[str] = (),
    limit: int = 12,
) -> list[EntryBlueprint]:
    """Select deterministic blueprint candidates for a new project."""

    blueprints: list[EntryBlueprint] = []
    for row in rows:
        if isinstance(row, EntryBlueprint):
            blueprints.append(row)
        else:
            blueprint = blueprint_from_material_row(row)
            if blueprint is not None:
                blueprints.append(blueprint)

    def _score(bp: EntryBlueprint) -> tuple[float, str]:
        score = 0.0
        score += _DIMENSION_PRIORITY.get(bp.dimension, 20)
        score += _genre_score(bp, genre, sub_genre)
        score += _keyword_score(bp, story_keywords) * 8
        score += bp.confidence * 10
        score += min(len(bp.state_variables), 4) * 2
        return (-score, bp.blueprint_id)

    return sorted(blueprints, key=_score)[: max(0, limit)]
