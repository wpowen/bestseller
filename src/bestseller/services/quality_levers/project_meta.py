"""Pydantic schemas + extractor for the ``ProjectModel.metadata`` shape.

``ProjectModel.metadata`` is an open ``dict[str, object]`` so the API
can carry forward arbitrary keys (see :mod:`bestseller.domain.project`
``ProjectCreate``). The quality-levers integration relies on a few
specific keys living there:

* ``target_platform`` — usually mirrors ``writing_profile.market.platform_target``
* ``style_anchors`` — list of anchor ids picked from ``prose_style_anchors.yaml``
* ``chapter_positions`` — ``{chapter_number: [position_id, …]}``
* ``character_profiles`` — project-specific overrides keyed by character_id
* ``rejection_history`` — list of past platform rejections with mapped causes

This module gives the integration layer a typed view over those
fields *without* requiring a database migration: the values live in
the JSON ``metadata`` column today.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class RejectionHistoryEntry:
    """One past rejection captured under ``metadata.rejection_history``."""

    date: str
    platform: str
    reason_text: str
    parsed_causes: tuple[str, ...]
    affected_chapters: tuple[int, ...]


@dataclass(frozen=True)
class QualityLeversProjectMeta:
    """Strongly-typed view over the quality-levers slice of ``ProjectModel.metadata``."""

    target_platform: str | None
    style_anchors: tuple[str, ...]
    chapter_positions: dict[int, tuple[str, ...]]
    character_profile_ids: tuple[str, ...]
    character_profiles: tuple[dict[str, Any], ...]
    rejection_history: tuple[RejectionHistoryEntry, ...]
    emotion_driven_kernel: dict[str, Any] | None = None

    def positions_for_chapter(self, chapter_number: int) -> tuple[str, ...]:
        """Lookup positions for a chapter by integer or string key."""

        return self.chapter_positions.get(chapter_number, ())


def _coerce_str_tuple(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        cleaned = value.strip()
        return (cleaned,) if cleaned else ()
    if isinstance(value, (list, tuple)):
        return tuple(
            str(item).strip()
            for item in value
            if str(item).strip()
        )
    return ()


def _coerce_int_tuple(value: object) -> tuple[int, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    out: list[int] = []
    for item in value:
        try:
            out.append(int(item))
        except (TypeError, ValueError):
            continue
    return tuple(out)


def _coerce_positions(value: object) -> dict[int, tuple[str, ...]]:
    if not isinstance(value, dict):
        return {}
    out: dict[int, tuple[str, ...]] = {}
    for raw_key, raw_value in value.items():
        try:
            key = int(raw_key)
        except (TypeError, ValueError):
            continue
        positions = _coerce_str_tuple(raw_value)
        if positions:
            out[key] = positions
    return out


def _coerce_rejection_history(value: object) -> tuple[RejectionHistoryEntry, ...]:
    if not isinstance(value, list):
        return ()
    entries: list[RejectionHistoryEntry] = []
    for entry in value:
        if not isinstance(entry, dict):
            continue
        entries.append(
            RejectionHistoryEntry(
                date=str(entry.get("date") or "").strip(),
                platform=str(entry.get("platform") or "").strip(),
                reason_text=str(entry.get("reason_text") or "").strip(),
                parsed_causes=_coerce_str_tuple(entry.get("parsed_causes")),
                affected_chapters=_coerce_int_tuple(entry.get("affected_chapters")),
            )
        )
    return tuple(entries)


def _coerce_character_profile_ids(value: object) -> tuple[str, ...]:
    """Extract character_ids from either a list or a dict.

    ``metadata.character_profiles`` may be:

    * ``list[str]`` of ids referencing sample_profiles in the YAML
    * ``dict[id -> profile_dict]`` for project-specific overrides
    """

    if isinstance(value, list):
        ids: list[str] = []
        for item in value:
            if isinstance(item, dict):
                raw_id = item.get("character_id") or item.get("id")
                cleaned = str(raw_id).strip() if raw_id is not None else ""
            else:
                cleaned = str(item).strip()
            if cleaned:
                ids.append(cleaned)
        return tuple(ids)
    if isinstance(value, dict):
        return tuple(str(k).strip() for k in value.keys() if str(k).strip())
    return ()


def _coerce_character_profiles(value: object) -> tuple[dict[str, Any], ...]:
    """Extract project-local character profile dictionaries."""

    profiles: list[dict[str, Any]] = []
    if isinstance(value, dict):
        for raw_id, raw_profile in value.items():
            if not isinstance(raw_profile, dict):
                continue
            profile = dict(raw_profile)
            profile.setdefault("character_id", str(raw_id).strip())
            profiles.append(profile)
    elif isinstance(value, list):
        for raw_profile in value:
            if isinstance(raw_profile, dict):
                profiles.append(dict(raw_profile))
    return tuple(profiles)


def _coerce_mapping(value: object) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    return dict(value)


def extract_quality_levers_meta(
    metadata: Mapping[str, Any] | None,
) -> QualityLeversProjectMeta:
    """Coerce ``ProjectModel.metadata`` into the typed quality-levers view.

    Returns an "empty" :class:`QualityLeversProjectMeta` when the input
    is ``None`` or lacks any quality-levers keys — callers can use
    truthy attributes to detect presence.
    """

    data = dict(metadata or {})
    target_platform = str(data.get("target_platform") or "").strip() or None
    return QualityLeversProjectMeta(
        target_platform=target_platform,
        style_anchors=_coerce_str_tuple(data.get("style_anchors")),
        chapter_positions=_coerce_positions(data.get("chapter_positions")),
        character_profile_ids=_coerce_character_profile_ids(
            data.get("character_profiles")
        ),
        character_profiles=_coerce_character_profiles(data.get("character_profiles")),
        rejection_history=_coerce_rejection_history(data.get("rejection_history")),
        emotion_driven_kernel=_coerce_mapping(data.get("emotion_driven_kernel")),
    )
