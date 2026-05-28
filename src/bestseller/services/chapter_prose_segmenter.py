from __future__ import annotations

# ruff: noqa: ANN401
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any


@dataclass(frozen=True)
class ProseSegment:
    scene_number: int
    text: str
    start_char_offset: int
    end_char_offset: int
    confidence: float


def segment_chapter_prose(
    chapter_text: str,
    scenes: list[Any] | tuple[Any, ...],
) -> tuple[ProseSegment, ...]:
    text = chapter_text or ""
    if not scenes:
        return ()
    cut_points = [_scene_cut_point(scene) for scene in scenes]
    positions: list[int | None] = []
    search_from = 0
    for cut_point in cut_points:
        if not cut_point:
            positions.append(None)
            continue
        exact = text.find(cut_point, search_from)
        if exact >= 0:
            positions.append(exact)
            search_from = exact + max(1, len(cut_point))
            continue
        fuzzy = _fuzzy_find(text, cut_point, start=search_from)
        if fuzzy is not None:
            positions.append(fuzzy)
            search_from = fuzzy + max(1, len(cut_point))
        else:
            positions.append(None)

    if any(position is not None for position in positions):
        return _segments_from_positions(text, scenes, positions)
    return _fallback_equal_segments(text, scenes)


def _segments_from_positions(
    text: str,
    scenes: list[Any] | tuple[Any, ...],
    positions: list[int | None],
) -> tuple[ProseSegment, ...]:
    count = len(scenes)
    resolved: list[int] = []
    for index, position in enumerate(positions):
        if position is None:
            resolved.append(int(len(text) * index / count))
        else:
            resolved.append(position)
    resolved = sorted(max(0, min(len(text), position)) for position in resolved)
    segments: list[ProseSegment] = []
    for index, scene in enumerate(scenes):
        start = resolved[index] if index < len(resolved) else int(len(text) * index / count)
        end = resolved[index + 1] if index + 1 < len(resolved) else len(text)
        if end < start:
            end = start
        confidence = 0.85 if positions[index] is not None else 0.5
        segments.append(
            ProseSegment(
                scene_number=int(getattr(scene, "scene_number", index + 1) or index + 1),
                text=text[start:end],
                start_char_offset=start,
                end_char_offset=end,
                confidence=confidence,
            )
        )
    return tuple(segments)


def _fallback_equal_segments(
    text: str,
    scenes: list[Any] | tuple[Any, ...],
) -> tuple[ProseSegment, ...]:
    count = len(scenes)
    segments: list[ProseSegment] = []
    for index, scene in enumerate(scenes):
        start = int(len(text) * index / count)
        end = int(len(text) * (index + 1) / count)
        segments.append(
            ProseSegment(
                scene_number=int(getattr(scene, "scene_number", index + 1) or index + 1),
                text=text[start:end],
                start_char_offset=start,
                end_char_offset=end,
                confidence=0.5,
            )
        )
    return tuple(segments)


def _scene_cut_point(scene: Any) -> str:
    metadata = getattr(scene, "metadata_json", None)
    values: list[Any] = []
    if isinstance(metadata, dict):
        values.extend(
            [
                metadata.get("cut_point"),
                (metadata.get("methodology_contract") or {}).get("cut_point")
                if isinstance(metadata.get("methodology_contract"), dict)
                else None,
                (metadata.get("scene_contract") or {}).get("cut_point")
                if isinstance(metadata.get("scene_contract"), dict)
                else None,
            ]
        )
    values.append(getattr(scene, "hook_requirement", None))
    for value in values:
        text = str(value or "").strip()
        if text:
            return text[:60]
    return ""


def _fuzzy_find(text: str, needle: str, *, start: int) -> int | None:
    if not text or not needle:
        return None
    span = max(len(needle), 8)
    best: tuple[float, int] = (0.0, -1)
    upper = max(start, len(text) - span)
    for index in range(start, upper + 1, max(1, span // 3)):
        candidate = text[index : index + span]
        ratio = SequenceMatcher(None, candidate, needle).ratio()
        if ratio > best[0]:
            best = (ratio, index)
    return best[1] if best[0] >= 0.6 else None


__all__ = ["ProseSegment", "segment_chapter_prose"]
