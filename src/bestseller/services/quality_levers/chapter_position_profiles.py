"""Chapter Position Profiles loader (``config/chapter_position_profiles.yaml``).

Encodes the position-sensitive guardrails layered on top of the
existing ``writing_methodology`` rules:

* ``ChapterPositionProfile`` — one ``profiles.<id>`` entry
  (``first_chapter``, ``volume_opener``, ``volume_climax``, ...)
* ``SensitiveWindowAntiPatterns`` — the time-windowed banned patterns
  (``opening_window``, ``golden_three_window``,
  ``extended_opening_window``)

Two helpers do the heavy lifting for the pipeline glue:

* :func:`detect_chapter_positions` — assign the position tags for a
  given chapter based on outline metadata
* :func:`render_chapter_position_block` — prompt fragment for writer
  and critic
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from bestseller.services.quality_levers._loader import (
    as_dict,
    as_str,
    as_str_tuple,
    load_yaml,
)


_CONFIG_FILENAME = "chapter_position_profiles.yaml"


@dataclass(frozen=True)
class BannedAntiPattern:
    """One banned writing pattern inside a sensitive chapter window."""

    pattern_id: str
    name: str
    definition: str
    why_banned: str


@dataclass(frozen=True)
class SensitiveWindow:
    """One time-windowed banned-pattern bundle."""

    window_id: str
    chapter_range: str
    banned: tuple[BannedAntiPattern, ...]


@dataclass(frozen=True)
class SensitiveWindowAntiPatterns:
    """Full ``sensitive_window_anti_patterns`` section."""

    description: str
    windows: tuple[SensitiveWindow, ...]

    def windows_for_chapter(self, chapter_number: int) -> tuple[SensitiveWindow, ...]:
        """Return the windows whose ``chapter_range`` contains ``chapter_number``."""

        matched: list[SensitiveWindow] = []
        for window in self.windows:
            low, high = _parse_chapter_range(window.chapter_range)
            if low and high and low <= chapter_number <= high:
                matched.append(window)
        return tuple(matched)


@dataclass(frozen=True)
class ChapterPositionProfile:
    """One ``profiles.<id>`` entry from the YAML."""

    profile_id: str
    description: str
    detection_rules: tuple[str, ...]
    inherits_windows: tuple[str, ...]
    must_achieve: tuple[str, ...]
    must_avoid: tuple[str, ...]
    hard_gates: tuple[str, ...]
    weighted_checks: tuple[str, ...]
    rewrite_priority_order: tuple[str, ...]


@dataclass(frozen=True)
class ChapterPositionProfilesConfig:
    """Full typed view over ``chapter_position_profiles.yaml``."""

    version: str
    sensitive_anti_patterns: SensitiveWindowAntiPatterns
    profiles: dict[str, ChapterPositionProfile]


def _parse_chapter_range(text: str) -> tuple[int, int]:
    if not text:
        return (0, 0)
    cleaned = text.replace(" ", "")
    parts = cleaned.split("-")
    if len(parts) != 2:
        return (0, 0)
    try:
        return (int(parts[0]), int(parts[1]))
    except ValueError:
        return (0, 0)


def _parse_banned_pattern(raw: object) -> BannedAntiPattern | None:
    data = as_dict(raw)
    pattern_id = as_str(data.get("id"))
    if not pattern_id:
        return None
    return BannedAntiPattern(
        pattern_id=pattern_id,
        name=as_str(data.get("name")),
        definition=as_str(data.get("definition")),
        why_banned=as_str(data.get("why_banned")),
    )


def _parse_sensitive_window(raw: object) -> SensitiveWindow | None:
    data = as_dict(raw)
    window_id = as_str(data.get("window_id"))
    if not window_id:
        return None
    banned_raw = data.get("banned")
    banned: list[BannedAntiPattern] = []
    if isinstance(banned_raw, list):
        for entry in banned_raw:
            pattern = _parse_banned_pattern(entry)
            if pattern is not None:
                banned.append(pattern)
    return SensitiveWindow(
        window_id=window_id,
        chapter_range=as_str(data.get("chapter_range")),
        banned=tuple(banned),
    )


def _parse_sensitive_anti_patterns(raw: object) -> SensitiveWindowAntiPatterns:
    data = as_dict(raw)
    windows_raw = data.get("windows")
    windows: list[SensitiveWindow] = []
    if isinstance(windows_raw, list):
        for entry in windows_raw:
            window = _parse_sensitive_window(entry)
            if window is not None:
                windows.append(window)
    return SensitiveWindowAntiPatterns(
        description=as_str(data.get("description")),
        windows=tuple(windows),
    )


_ID_KEYS = ("id", "step", "window_id")
_BODY_KEYS = ("rule", "action", "name", "definition", "rubric")


def _stringify_must_clauses(raw: object) -> tuple[str, ...]:
    """Flatten ``must_achieve`` / ``must_avoid`` / ``rewrite_priority_order`` entries.

    They show up in YAML as either pure strings or dicts whose key sets
    vary across sections — ``{id, rule}``, ``{step, action}``,
    ``{id, rubric, weight}``, etc. We render them as ``"id: body"`` lines
    and fall back to ``"body"`` or ``"id"`` when only one half is present.
    """

    if isinstance(raw, str):
        cleaned = raw.strip()
        return (cleaned,) if cleaned else ()
    if not isinstance(raw, list):
        return ()
    items: list[str] = []
    for entry in raw:
        if isinstance(entry, str):
            cleaned = entry.strip()
            if cleaned:
                items.append(cleaned)
            continue
        if not isinstance(entry, dict):
            continue
        identifier = ""
        for key in _ID_KEYS:
            identifier = as_str(entry.get(key))
            if identifier:
                break
        body = ""
        for key in _BODY_KEYS:
            body = as_str(entry.get(key))
            if body:
                break
        if identifier and body:
            items.append(f"{identifier}: {body}")
        elif body:
            items.append(body)
        elif identifier:
            items.append(identifier)
    return tuple(items)


def _parse_profile(profile_id: str, raw: object) -> ChapterPositionProfile:
    data = as_dict(raw)
    return ChapterPositionProfile(
        profile_id=profile_id,
        description=as_str(data.get("description")),
        detection_rules=as_str_tuple(data.get("detection_rules")),
        inherits_windows=as_str_tuple(data.get("inherits_windows")),
        must_achieve=_stringify_must_clauses(data.get("must_achieve")),
        must_avoid=_stringify_must_clauses(data.get("must_avoid")),
        hard_gates=as_str_tuple(data.get("hard_gates")),
        weighted_checks=_stringify_must_clauses(data.get("weighted_checks")),
        rewrite_priority_order=_stringify_must_clauses(
            data.get("rewrite_priority_order")
        ),
    )


@lru_cache(maxsize=1)
def load_chapter_position_profiles() -> ChapterPositionProfilesConfig:
    """Return the typed view over ``chapter_position_profiles.yaml``."""

    raw = load_yaml(_CONFIG_FILENAME)
    profiles_raw = as_dict(raw.get("profiles"))
    profiles: dict[str, ChapterPositionProfile] = {}
    for profile_id, profile_raw in profiles_raw.items():
        canonical = as_str(profile_id)
        if not canonical:
            continue
        profiles[canonical] = _parse_profile(canonical, profile_raw)
    return ChapterPositionProfilesConfig(
        version=as_str(raw.get("version")),
        sensitive_anti_patterns=_parse_sensitive_anti_patterns(
            raw.get("sensitive_window_anti_patterns")
        ),
        profiles=profiles,
    )


def detect_chapter_positions(
    *,
    chapter_number: int,
    volume_number: int = 1,
    is_first_chapter_of_volume: bool = False,
    is_last_chapter_of_volume: bool = False,
    contains_first_powerup: bool = False,
    contains_first_villain_reveal: bool = False,
    is_first_unit_case: bool = False,
    is_major_twist: bool = False,
) -> tuple[str, ...]:
    """Return the position-profile tags applicable to one chapter.

    Mirrors the YAML ``detection_rules`` so the pipeline can label
    chapters without re-parsing the rule strings. The chapter-number
    rules take precedence over the volume-level flags so the very
    first chapter of book 1 is treated as ``first_chapter``, not
    ``volume_opener``.
    """

    positions: list[str] = []
    if chapter_number == 1:
        positions.append("first_chapter")
    if is_first_chapter_of_volume and volume_number > 1:
        positions.append("volume_opener")
    if is_last_chapter_of_volume:
        positions.append("volume_climax")
    if contains_first_powerup:
        positions.append("first_powerup_reveal")
    if contains_first_villain_reveal:
        positions.append("first_villain_reveal")
    if is_first_unit_case:
        positions.append("first_unit_case_chapter")
    if is_major_twist:
        positions.append("major_twist_chapter")
    return tuple(positions)


def render_chapter_position_block(
    *,
    positions: tuple[str, ...] | list[str],
    chapter_number: int,
) -> str:
    """Render a prompt fragment summarising the active position profiles.

    Empty ``positions`` → empty string. The window-level banned
    patterns are appended automatically based on ``chapter_number``.
    """

    config = load_chapter_position_profiles()
    active_profiles: list[ChapterPositionProfile] = []
    for raw_id in positions:
        identifier = as_str(raw_id)
        profile = config.profiles.get(identifier)
        if profile is not None:
            active_profiles.append(profile)

    windows = config.sensitive_anti_patterns.windows_for_chapter(chapter_number)
    if not active_profiles and not windows:
        return ""

    lines: list[str] = ["【章节位置档案】"]
    for profile in active_profiles:
        lines.append(f"- 位置 {profile.profile_id}")
        if profile.must_achieve:
            lines.append("  必达: " + "; ".join(profile.must_achieve))
        if profile.must_avoid:
            lines.append("  必避: " + "; ".join(profile.must_avoid))
        if profile.hard_gates:
            lines.append("  硬指标: " + "; ".join(profile.hard_gates))
    for window in windows:
        banned_names = [item.name for item in window.banned if item.name]
        if not banned_names:
            continue
        lines.append(
            f"- 时窗 {window.window_id} ({window.chapter_range}) 禁: "
            + "; ".join(banned_names)
        )
    return "\n".join(lines)
