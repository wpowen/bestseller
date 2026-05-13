"""Story-shape routing for adaptive plot design.

The generation pipeline serves projects with very different commercial shapes:
short literary work, long serials, category fiction, and IP-oriented concepts.
This module turns project metadata into a compact routing contract so later
planning code can choose the right plot granularity and narrative duties.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

from pydantic import BaseModel, Field

LengthClass = Literal["short", "novella", "long", "very_long", "series"]
PublicationMode = Literal["web_serial", "commercial_book", "literary", "ip_development"]
OutlineDepth = Literal["scene", "chapter", "volume_chapter_scene"]


class StoryShape(BaseModel, frozen=True):
    """Compact story routing decision used by the story design kernel."""

    length_class: LengthClass = "long"
    publication_mode: PublicationMode = "web_serial"
    outline_depth: OutlineDepth = "chapter"
    primary_duties: list[str] = Field(default_factory=list)
    ending_contract: str = ""
    source_signals: dict[str, Any] = Field(default_factory=dict)


def derive_story_shape(
    project: object | Mapping[str, Any] | None = None,
    *,
    genre: str | None = None,
    sub_genre: str | None = None,
    target_chapters: int | str | None = None,
    target_word_count: int | str | None = None,
    audience: str | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> StoryShape:
    """Derive a story-shape contract from explicit values or a project object."""

    project_metadata = _project_metadata(project)
    merged_metadata: dict[str, Any] = {**project_metadata, **dict(metadata or {})}
    resolved_genre = _text(genre) or _text(_get(project, "genre"))
    resolved_sub_genre = _text(_sub_genre_or_project(sub_genre, project))
    resolved_audience = _text(audience) or _text(_get(project, "audience"))
    chapters = _int_or_none(target_chapters) or _int_or_none(_get(project, "target_chapters"))
    words = (
        _int_or_none(target_word_count)
        or _int_or_none(_get(project, "target_word_count"))
        or _int_or_none(_get(project, "target_total_words"))
    )

    length_class = _derive_length_class(chapters, words)
    publication_mode = _derive_publication_mode(
        genre=resolved_genre,
        sub_genre=resolved_sub_genre,
        audience=resolved_audience,
        metadata=merged_metadata,
    )
    outline_depth = _derive_outline_depth(length_class, publication_mode)
    duties = _derive_primary_duties(
        genre=resolved_genre,
        sub_genre=resolved_sub_genre,
        audience=resolved_audience,
        metadata=merged_metadata,
        publication_mode=publication_mode,
    )

    return StoryShape(
        length_class=length_class,
        publication_mode=publication_mode,
        outline_depth=outline_depth,
        primary_duties=duties,
        ending_contract=_derive_ending_contract(length_class, publication_mode),
        source_signals={
            "genre": resolved_genre,
            "sub_genre": resolved_sub_genre,
            "target_chapters": chapters,
            "target_word_count": words,
            "audience": resolved_audience,
            "metadata_keys": sorted(merged_metadata.keys()),
        },
    )


def _sub_genre_or_project(
    sub_genre: str | None,
    project: object | Mapping[str, Any] | None,
) -> object:
    return sub_genre if sub_genre is not None else _get(project, "sub_genre")


def _derive_length_class(chapters: int | None, words: int | None) -> LengthClass:
    if chapters is not None:
        if chapters <= 20:
            return "short"
        if chapters <= 60:
            return "novella"
        if chapters <= 180:
            return "long"
        if chapters <= 300:
            return "very_long"
        return "series"
    if words is not None:
        if words <= 50_000:
            return "short"
        if words <= 100_000:
            return "novella"
        if words <= 300_000:
            return "long"
        if words <= 700_000:
            return "very_long"
        return "series"
    return "long"


def _derive_publication_mode(
    *,
    genre: str,
    sub_genre: str,
    audience: str,
    metadata: Mapping[str, Any],
) -> PublicationMode:
    explicit = _normalize_publication_mode(
        metadata.get("publication_mode")
        or metadata.get("publishing_mode")
        or metadata.get("distribution_mode")
    )
    if explicit:
        return explicit

    haystack = _haystack(genre, sub_genre, audience, metadata)
    if _contains_any(haystack, ("literary", "文学", "严肃", "纯文学")):
        return "literary"
    if _contains_any(haystack, ("ip", "adaptation", "影视", "漫改", "剧集", "游戏改编")):
        return "ip_development"
    if _contains_any(haystack, ("commercial book", "出版", "kdp", "实体书", "单行本")):
        return "commercial_book"
    return "web_serial"


def _derive_outline_depth(
    length_class: LengthClass,
    publication_mode: PublicationMode,
) -> OutlineDepth:
    if publication_mode == "literary" or length_class == "short":
        return "scene"
    if length_class in {"very_long", "series"}:
        return "volume_chapter_scene"
    return "chapter"


def _derive_primary_duties(
    *,
    genre: str,
    sub_genre: str,
    audience: str,
    metadata: Mapping[str, Any],
    publication_mode: PublicationMode,
) -> list[str]:
    duties: list[str] = []
    if publication_mode == "web_serial":
        duties.extend(["forward_pull", "reader_payoff", "rolling_freshness"])
    elif publication_mode == "commercial_book":
        duties.extend(["reader_promise_integrity", "structural_payoff"])
    elif publication_mode == "literary":
        duties.extend(["theme_or_perception_turn", "scene_causality"])
    else:
        duties.extend(["set_piece_escalation", "visual_sequence_payoff"])

    haystack = _haystack(genre, sub_genre, audience, metadata)
    if _contains_any(haystack, ("仙", "修仙", "玄幻", "升级", "progression", "litrpg")):
        duties.append("measurable_progression")
    if _contains_any(haystack, ("悬疑", "推理", "探案", "mystery", "thriller", "detective")):
        duties.append("fair_play_clue_movement")
    if _contains_any(haystack, ("言情", "恋爱", "romance", "relationship", "slow-burn", "宫斗")):
        duties.append("relationship_state_shift")
    if _contains_any(haystack, ("种田", "基建", "经营", "base-building", "management")):
        duties.append("visible_system_change")
    if _contains_any(haystack, ("权谋", "争霸", "strategy", "历史")):
        duties.append("strategic_position_shift")
    if _contains_any(haystack, ("电竞", "游戏", "esport", "competition")):
        duties.append("match_state_swing")
    if _contains_any(haystack, ("东方美学", "国风", "水墨", "aesthetic")):
        duties.append("atmosphere_meaning_turn")
    return _dedupe(duties)


def _derive_ending_contract(
    length_class: LengthClass,
    publication_mode: PublicationMode,
) -> str:
    if publication_mode == "literary":
        return "land a thematic or perceptual turn"
    if length_class in {"very_long", "series"}:
        return "resolve the current unit while preserving the series promise"
    if publication_mode == "commercial_book":
        return "deliver structural payoff without closing every future possibility"
    return "close current loop while opening next desire"


def _project_metadata(project: object | Mapping[str, Any] | None) -> dict[str, Any]:
    metadata = _get(project, "metadata_json") or _get(project, "metadata")
    return dict(metadata) if isinstance(metadata, Mapping) else {}


def _get(project: object | Mapping[str, Any] | None, key: str) -> object | None:
    if project is None:
        return None
    if isinstance(project, Mapping):
        return project.get(key)
    return getattr(project, key, None)


def _int_or_none(value: object) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _text(value: object) -> str:
    return str(value).strip() if value is not None else ""


def _normalize_publication_mode(value: object) -> PublicationMode | None:
    raw = _text(value).lower().replace("-", "_").replace(" ", "_")
    direct: dict[str, PublicationMode] = {
        "web_serial": "web_serial",
        "serial": "web_serial",
        "web": "web_serial",
        "commercial_book": "commercial_book",
        "book": "commercial_book",
        "publishing": "commercial_book",
        "literary": "literary",
        "ip": "ip_development",
        "ip_development": "ip_development",
        "adaptation": "ip_development",
    }
    if raw in direct:
        return direct[raw]
    if "连载" in raw or "网文" in raw:
        return "web_serial"
    if "文学" in raw:
        return "literary"
    if "出版" in raw:
        return "commercial_book"
    if "影视" in raw or "改编" in raw:
        return "ip_development"
    return None


def _haystack(
    genre: str,
    sub_genre: str,
    audience: str,
    metadata: Mapping[str, Any],
) -> str:
    parts = [genre, sub_genre, audience]
    parts.extend(_flatten_metadata_text(metadata))
    return " ".join(part for part in parts if part).lower()


def _flatten_metadata_text(value: object) -> list[str]:
    if isinstance(value, Mapping):
        parts: list[str] = []
        for item in value.values():
            parts.extend(_flatten_metadata_text(item))
        return parts
    if isinstance(value, list | tuple | set):
        parts = []
        for item in value:
            parts.extend(_flatten_metadata_text(item))
        return parts
    text = _text(value)
    return [text] if text else []


def _contains_any(haystack: str, needles: tuple[str, ...]) -> bool:
    return any(needle.lower() in haystack for needle in needles)


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result
