from __future__ import annotations

from dataclasses import dataclass
from math import ceil
from typing import Any

from bestseller.services.length_stability_gate import CHINESE_CHAPTER_HARD_MIN_WORDS
from bestseller.services.writing_profile import is_english_language
from bestseller.settings import AppSettings, apply_runtime_llm_profile


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

    settings = apply_runtime_llm_profile(settings)
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

    settings = apply_runtime_llm_profile(settings)
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
    if "minimax-m2" in model and "highspeed" in model:
        return 0
    if "minimax-m2" in model or "minimax-m1" in model:
        return 6000
    return 0


def model_output_token_ceiling(model_name: str | None) -> int | None:
    """Best-known high output cap for provider/model families we tune for."""

    model = (model_name or "").strip().lower()
    if "minimax-m3" in model:
        return 32768
    if "minimax-m2" in model:
        return 32768
    if "minimax" in model:
        return 16384
    return None


def model_min_output_tokens(model_name: str | None) -> int | None:
    """Per-model floor for completion budget on PROSE roles, to prevent truncation.

    Fixed role caps cause large cross-model variance: a cap that fits DeepSeek may
    truncate a reasoning model (MiniMax-M3) whose thinking tokens share the output
    budget. We floor prose-role output per model so a full chapter never gets cut
    off. Models stop at finish_reason="stop" when done, so a generous floor only
    provides headroom (it does not waste tokens). None => keep the role's own cap.
    """

    model = (model_name or "").strip().lower()
    if "minimax-m3" in model:
        return 16000  # reasoning(adaptive) headroom + full chapter prose
    if "minimax" in model:
        return 8192
    if "deepseek" in model:
        return 8192
    if "mimo" in model or "xiaomi" in model:
        return 8192
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
    project: Any | None = None,
) -> RewriteLengthBand:
    """Compute a model-aware safe rewrite length band for a chapter.

    `direction` controls the correction style:
    - ``over``: tightening on overflow
    - ``under``: expansion needed
    - ``normal``: balanced in-band tuning
    """

    policy = project_word_target_policy(project, settings)
    hard_min = int(policy.chapter_min)
    hard_max = int(policy.chapter_max)
    if not is_english_language(language):
        hard_min = max(hard_min, CHINESE_CHAPTER_HARD_MIN_WORDS)
        hard_max = max(hard_max, hard_min)
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
        expansion_buffer = 0
        if not is_english_language(language):
            expansion_buffer = min(220, max(0, hard_max - hard_target))
        safe_min = hard_target + expansion_buffer
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
        if model_safe_max >= hard_min:
            safe_max = min(safe_max, model_safe_max)
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


def project_word_target_policy(project: Any, settings: AppSettings) -> WordTargetPolicy:
    """Apply an explicit per-project chapter band when the project declares one."""

    base = word_target_policy(settings)
    metadata = getattr(project, "metadata_json", None)
    override = metadata.get("words_per_chapter") if isinstance(metadata, dict) else None
    if not isinstance(override, dict):
        return base
    chapter_min = _positive_int(override.get("min"))
    chapter_target = _positive_int(override.get("target"))
    chapter_max = _positive_int(override.get("max"))
    if (
        chapter_min is None
        or chapter_target is None
        or chapter_max is None
        or not chapter_min <= chapter_target <= chapter_max
    ):
        return base
    return WordTargetPolicy(
        chapter_min=chapter_min,
        chapter_target=chapter_target,
        chapter_max=chapter_max,
        scene_min=base.scene_min,
        scene_target=base.scene_target,
        scene_max=base.scene_max,
    )


# The zh prose models empirically UNDER-produce vs the per-scene target. Observed
# on qwen3.7-plus (custom-xuanhuan-1782291202): chapters land 77-90% of target,
# scenes as low as 59%. The old normalize logic assumed a +10-20% OVERSHOOT, so it
# accepted an LLM-proposed 2200 target whose ~80%-realized length (~1760) breaches
# the 1800 hard floor → recurring CHAPTER_TOO_SHORT churn + repair exhaustion (and
# the short drafts also drop hook anchors → HOOK_ECHO_MISSING). We floor the WRITING
# target so even ~75% realization clears the hard floor with margin. This raises the
# target the writer AIMS at; it does NOT touch the gate's hard floor
# (CHINESE_CHAPTER_HARD_MIN_WORDS), so it adds margin instead of moving the bar.
_PRODUCTION_FLOOR_REALIZATION = 0.75


def _floor_safe_chapter_target(policy: WordTargetPolicy) -> int:
    """Smallest chapter writing target whose ~75%-realized length still clears the
    zh hard floor. Capped at ``chapter_max`` so it can never breach the ceiling."""

    safe = ceil(CHINESE_CHAPTER_HARD_MIN_WORDS / _PRODUCTION_FLOOR_REALIZATION)
    return min(safe, policy.chapter_max)


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

    policy = project_word_target_policy(project, settings)
    floor_safe = _floor_safe_chapter_target(policy)
    project_average = project_average_chapter_words(project)
    candidate = project_average if project_average is not None else policy.chapter_target
    if policy.chapter_min <= candidate <= policy.chapter_max:
        return max(candidate, floor_safe)
    return max(max(policy.chapter_min, min(policy.chapter_target, policy.chapter_max)), floor_safe)


def normalize_chapter_word_target(raw_target: Any, project: Any, settings: AppSettings) -> int:
    policy = project_word_target_policy(project, settings)
    floor_safe = _floor_safe_chapter_target(policy)
    parsed = _positive_int(raw_target)
    if parsed is not None and policy.chapter_min <= parsed <= policy.chapter_max:
        # The chapter WRITING goal must aim at ``chapter_target``, not at
        # ``chapter_max``. ``chapter_max`` is only the hard publication ceiling.
        # LLM-proposed outlines routinely echo the max (e.g. 3500) into
        # target_word_count; if accepted verbatim, per-scene allocation
        # (target / scene_count) leaves ZERO headroom for the model's natural
        # +10–20% overshoot, so the chapter blows past the cap and needs 5–7
        # whole-chapter re-rolls to converge. Aiming at the target instead
        # keeps scenes small enough that a normal overshoot still lands inside
        # the band.
        #
        # BUT a too-LOW proposal is just as harmful: a 2-scene/2200 outline whose
        # ~80%-realized length grazes the 1800 floor churns on CHAPTER_TOO_SHORT.
        # Floor the target so realistic under-production still clears the floor.
        return max(min(parsed, policy.chapter_target), floor_safe)
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


def allocate_scene_word_targets(
    chapter_target: Any,
    scene_count: int,
    settings: AppSettings,
) -> tuple[int, ...]:
    """Allocate an exact chapter budget across its scenes.

    The chapter target is the authoritative contract. Legacy scene bounds are
    widened only when the exact chapter total cannot otherwise fit, mirroring
    :func:`authoritative_book_word_targets` at the book level.
    """

    count = max(1, int(scene_count or 1))
    total = max(1, int(chapter_target or effective_chapter_word_target(None, settings)))
    policy = word_target_policy(settings)
    average_floor = total // count
    average_ceiling = ceil(total / count)
    return allocate_book_word_targets(
        total,
        count,
        chapter_min=min(policy.scene_min, average_floor),
        chapter_max=max(policy.scene_max, average_ceiling),
    )


def allocate_book_word_targets(
    total_word_count: int,
    chapter_count: int,
    *,
    policy: WordTargetPolicy | None = None,
    weights: list[float] | tuple[float, ...] | None = None,
    chapter_min: int | None = None,
    chapter_max: int | None = None,
) -> tuple[int, ...]:
    """Allocate an exact whole-book target across bounded chapters.

    The minimum is assigned first, then the remaining words are apportioned by
    weights. Largest-remainder ordering (fraction descending, chapter index
    ascending) makes remainder placement stable across runs and processes.
    """

    count = int(chapter_count)
    total = int(total_word_count)
    if count <= 0 or total < 0:
        raise ValueError("chapter_count and total_word_count must be positive")
    if policy is not None:
        minimum = int(policy.chapter_min)
        maximum = int(policy.chapter_max)
    else:
        minimum = int(chapter_min if chapter_min is not None else 1)
        maximum = int(chapter_max if chapter_max is not None else 2**31 - 1)
    if minimum < 0 or maximum < minimum:
        raise ValueError("invalid chapter word bounds")
    if total < minimum * count or total > maximum * count:
        raise ValueError("book word target cannot fit chapter bounds")
    if weights is None:
        normalized = [1.0] * count
    else:
        if len(weights) != count or any(float(weight) < 0 for weight in weights):
            raise ValueError("weights must match chapter_count and be non-negative")
        normalized = [float(weight) for weight in weights]
        if not any(normalized):
            normalized = [1.0] * count

    allocations = [minimum] * count
    remaining = total - minimum * count
    capacities = [maximum - minimum] * count
    while remaining:
        active = [i for i, capacity in enumerate(capacities) if capacity > 0]
        if not active:
            raise ValueError("book word target cannot fit chapter bounds")
        weight_sum = sum(normalized[i] for i in active)
        if weight_sum <= 0:
            for i in active:
                normalized[i] = 1.0
            weight_sum = float(len(active))
        shares = {i: remaining * normalized[i] / weight_sum for i in active}
        increments = {i: min(capacities[i], int(shares[i])) for i in active}
        used = sum(increments.values())
        if used:
            for i, increment in increments.items():
                allocations[i] += increment
                capacities[i] -= increment
            remaining -= used
        if not remaining:
            continue
        # Place at most one residual word per active bucket in this round. The
        # previous implementation repeatedly selected chapter 1 after each
        # recomputation, concentrating all equal-share remainders there.
        remainder_order = sorted(
            active,
            key=lambda i: (shares[i] - int(shares[i]), -i),
            reverse=True,
        )
        for chosen in remainder_order:
            if not remaining:
                break
            if capacities[chosen] <= 0:
                continue
            allocations[chosen] += 1
            capacities[chosen] -= 1
            remaining -= 1
    return tuple(allocations)


def authoritative_book_word_targets(project: Any, settings: AppSettings) -> tuple[int, ...]:
    """Allocate the project's exact total, widening legacy chapter bounds if needed.

    Creation-time totals are authoritative.  Some legacy books intentionally
    target chapters longer or shorter than the current global defaults; those
    projects must not lose words merely because a later configuration changed.
    """

    total = _positive_int(getattr(project, "target_word_count", None))
    count = _positive_int(getattr(project, "target_chapters", None))
    if total is None or count is None:
        return ()
    policy = project_word_target_policy(project, settings)
    average_floor = total // count
    average_ceiling = ceil(total / count)
    return allocate_book_word_targets(
        total,
        count,
        chapter_min=min(policy.chapter_min, average_floor),
        chapter_max=max(policy.chapter_max, average_ceiling),
    )


allocate_whole_book_word_targets = allocate_book_word_targets
allocate_chapter_word_targets = allocate_book_word_targets
