from __future__ import annotations

from dataclasses import dataclass
from math import ceil
from typing import Any

from bestseller.settings import AppSettings
from bestseller.services.writing_profile import is_english_language


@dataclass(frozen=True)
class WordTargetPolicy:
    chapter_min: int
    chapter_target: int
    chapter_max: int
    scene_min: int
    scene_target: int
    scene_max: int


@dataclass(frozen=True)
class RewriteLengthBand:
    hard_min: int
    hard_target: int
    hard_max: int
    safe_min: int
    safe_max: int
    model_output_chars: int | None


def resolve_llm_role_max_tokens(settings: AppSettings, role: str = "writer") -> int | None:
    """Return configured max output tokens for a writer role if present."""

    llm_settings = getattr(settings, "llm", None)
    if llm_settings is None:
        return None
    role_settings = getattr(llm_settings, role, None)
    if role_settings is None:
        return None
    try:
        value = int(getattr(role_settings, "max_tokens"))
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def resolve_llm_role_model(settings: AppSettings, role: str = "writer") -> str | None:
    """Return the configured model name for an LLM role."""

    llm_settings = getattr(settings, "llm", None)
    if llm_settings is None:
        return None
    role_settings = getattr(llm_settings, role, None)
    if role_settings is None:
        return None
    value = getattr(role_settings, "model", None)
    return str(value).strip() if value else None


def model_reasoning_token_reserve(model_name: str | None) -> int:
    """Completion-token reserve for models that bill hidden thinking as output.

    MiniMax M2-style endpoints can spend a large part of ``max_tokens`` on
    hidden reasoning. If prose calls use a tight target-derived cap, the
    provider may return ``finish_reason='length'`` with little or no visible
    content. Keep this reserve centralized so generation and repair caps use
    the same policy.
    """

    model = (model_name or "").strip().lower()
    if "minimax-m2" in model or "minimax-m1" in model:
        return 6000
    return 0


def model_output_token_ceiling(model_name: str | None) -> int | None:
    """Best-known high output cap for provider/model families we tune for."""

    model = (model_name or "").strip().lower()
    if "minimax-m2" in model:
        return 32768
    if "minimax" in model:
        return 16384
    return None


def output_chars_for_token_limit(
    token_limit: int | None,
    *,
    language: str | None = None,
) -> int | None:
    """Convert a completion token limit to an approximate maximum Chinese/English chars."""

    if token_limit is None or int(token_limit) <= 0:
        return None
    is_en = is_english_language(language)
    ratio = 2.8 if is_en else 3.2
    floor = 1024 if is_en else 1536
    value = int(token_limit) - floor
    if value <= 0:
        return None
    return max(1, int(value / ratio))


def chapter_rewrite_length_band(
    settings: AppSettings,
    target_word_count: int | None,
    *,
    language: str | None = None,
    direction: str = "normal",
    role: str = "writer",
) -> RewriteLengthBand:
    """Compute a model-aware safe rewrite length band for a chapter.

    `direction` controls the correction style:
    - ``over``: tightening on overflow
    - ``under``: expansion needed
    - ``normal``: balanced in-band tuning
    """

    policy = word_target_policy(settings)
    hard_min = int(policy.chapter_min)
    hard_max = int(policy.chapter_max)
    hard_target = _positive_int(target_word_count)
    if hard_target is None:
        hard_target = int(policy.chapter_target)
    hard_target = max(hard_min, min(hard_target, hard_max))

    if direction == "over":
        lower_delta = max(180, int(round(hard_target * 0.11)))
        upper_delta = max(180, int(round(hard_target * 0.11)))
        safe_min = hard_target - lower_delta
        safe_max = hard_target + upper_delta
    elif direction == "under":
        safe_min = hard_target
        safe_max = hard_target + max(220, int(round(hard_target * 0.18)))
    else:
        safe_min = hard_target - max(220, int(round(hard_target * 0.12)))
        safe_max = hard_target + max(300, int(round(hard_target * 0.16)))

    safe_min = max(hard_min, safe_min)
    safe_max = min(hard_max, safe_max)

    model_output_chars = output_chars_for_token_limit(
        resolve_llm_role_max_tokens(settings, role=role),
        language=language,
    )
    if model_output_chars is not None:
        # Reserve a small narrative + markdown overhead before the actual chapter body.
        model_safe_max = max(1, model_output_chars - 120)
        safe_max = min(safe_max, model_safe_max)
        if safe_max < hard_min:
            safe_max = hard_min
        safe_min = max(hard_min, min(safe_min, safe_max))

    return RewriteLengthBand(
        hard_min=hard_min,
        hard_target=hard_target,
        hard_max=hard_max,
        safe_min=safe_min,
        safe_max=safe_max,
        model_output_chars=model_output_chars,
    )


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
