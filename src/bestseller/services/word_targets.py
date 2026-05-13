from __future__ import annotations

from dataclasses import dataclass
from math import ceil
from typing import Any

from bestseller.settings import AppSettings


@dataclass(frozen=True)
class WordTargetPolicy:
    chapter_min: int
    chapter_target: int
    chapter_max: int
    scene_min: int
    scene_target: int
    scene_max: int


def word_target_policy(settings: AppSettings) -> WordTargetPolicy:
    chapter = settings.generation.words_per_chapter
    scene = settings.generation.words_per_scene
    return WordTargetPolicy(
        chapter_min=int(chapter.min),
        chapter_target=int(chapter.target),
        chapter_max=int(chapter.max),
        scene_min=int(scene.min),
        scene_target=int(scene.target),
        scene_max=int(scene.max),
    )


def _positive_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def project_average_chapter_words(project: Any) -> int | None:
    total = _positive_int(getattr(project, "target_word_count", None))
    chapters = _positive_int(getattr(project, "target_chapters", None))
    if total is None or chapters is None:
        return None
    return max(1, round(total / chapters))


def effective_chapter_word_target(project: Any, settings: AppSettings) -> int:
    """Resolve the chapter target that planning, writing, and gates should share."""

    policy = word_target_policy(settings)
    project_average = project_average_chapter_words(project)
    candidate = project_average if project_average is not None else policy.chapter_target
    if policy.chapter_min <= candidate <= policy.chapter_max:
        return candidate
    return max(policy.chapter_min, min(policy.chapter_target, policy.chapter_max))


def normalize_chapter_word_target(raw_target: Any, project: Any, settings: AppSettings) -> int:
    policy = word_target_policy(settings)
    parsed = _positive_int(raw_target)
    if parsed is not None and policy.chapter_min <= parsed <= policy.chapter_max:
        return parsed
    return effective_chapter_word_target(project, settings)


def scene_word_target_for_chapter(
    chapter_target: Any,
    scene_count: int,
    settings: AppSettings,
) -> int:
    """Distribute chapter budget across scenes without making the chapter impossible.

    ``words_per_scene.max`` is a normal cap, but the chapter envelope is the
    stronger publication contract. If a chapter has too few scenes to satisfy
    the chapter target inside the scene cap, return the per-scene value needed
    to hit the chapter target instead of forcing a guaranteed short chapter.
    """

    policy = word_target_policy(settings)
    count = max(1, int(scene_count or 1))
    target = max(1, int(chapter_target or effective_chapter_word_target(None, settings)))
    per_scene = max(policy.scene_min, ceil(target / count))
    if per_scene > policy.scene_max and policy.scene_max * count >= target:
        return policy.scene_max
    return per_scene

