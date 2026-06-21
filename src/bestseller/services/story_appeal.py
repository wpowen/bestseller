"""Story / blurb appeal orchestrator — single source of truth for thresholds.

Loads ``config/story_appeal.yaml`` and combines the two evaluators:
  * :func:`bestseller.services.blurb_appeal_gate.evaluate_blurb_appeal` (det.)
  * :func:`bestseller.services.premise_appeal_judge.evaluate_premise_appeal` (LLM)

into a :class:`bestseller.domain.appeal.StoryAppealReport`.  Owns config
loading, genre-lexicon resolution (``canonicalize`` → genre-specific or
``generic`` fallback so *every* genre is covered by one yardstick), grading,
one-vote-veto gating, the meets-bar test, and the improvement feedback string
injected back into conception finalize on regeneration.
"""

from __future__ import annotations

from functools import lru_cache

# ruff: noqa: ANN401, RUF001 — Chinese labels + Any session/settings.
import logging
from pathlib import Path
from typing import Any

import yaml

from bestseller.domain.appeal import (
    AppealDimension,
    BlurbAppealVerdict,
    PremiseAppealVerdict,
    StoryAppealReport,
    grade_rank,
    min_grade,
)

logger = logging.getLogger(__name__)

_SHORT_FORM_MAX_CHAPTERS = 24


def _config_path() -> Path:
    return Path(__file__).resolve().parents[3] / "config" / "story_appeal.yaml"


@lru_cache(maxsize=1)
def load_story_appeal_config() -> dict[str, Any]:
    """Load ``config/story_appeal.yaml`` (cached). Empty dict if missing/bad."""

    path = _config_path()
    if not path.exists():
        logger.warning("story_appeal config not found at %s", path)
        return {}
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        logger.warning("Failed to load story_appeal config", exc_info=True)
        return {}


def is_appeal_enabled(config: dict[str, Any] | None = None) -> bool:
    cfg = config if config is not None else load_story_appeal_config()
    return bool(cfg.get("enabled", False)) if isinstance(cfg, dict) else False


@lru_cache(maxsize=64)
def _canonical_genre(genre: str | None, sub_genre: str | None) -> str:
    try:
        from bestseller.services.genre_taxonomy import canonicalize

        return canonicalize(genre, sub_genre) or ""
    except Exception:
        return ""


def resolve_genre_lexicon(
    genre: str | None, sub_genre: str | None = None
) -> dict[str, Any]:
    """Merge the ``generic`` lexicon with the canonical genre's overrides.

    A genre that has no dedicated block simply uses ``generic`` — so the
    appeal yardstick covers every genre in the taxonomy.
    """

    config = load_story_appeal_config()
    lexicons = config.get("lexicons", {}) if isinstance(config, dict) else {}
    generic = dict(lexicons.get("generic", {})) if isinstance(lexicons, dict) else {}

    canonical = _canonical_genre(genre, sub_genre)
    specific = lexicons.get(canonical, {}) if isinstance(lexicons, dict) else {}
    merged = dict(generic)
    if isinstance(specific, dict):
        merged.update(specific)
    return merged


def genre_signal_terms(genre: str | None, sub_genre: str | None = None) -> tuple[str, ...]:
    """Genre signal keywords from the judge genre context (for consistency check)."""

    try:
        from bestseller.services.judge_genre_context import (
            resolve_judge_genre_context,
        )

        ctx = resolve_judge_genre_context(genre=genre, sub_genre=sub_genre)
        return tuple(ctx.signal_keywords)
    except Exception:
        return ()


def grade_from_total(total: float, config: dict[str, Any] | None = None) -> str:
    cfg = config if config is not None else load_story_appeal_config()
    grades = cfg.get("grades", {}) if isinstance(cfg, dict) else {}
    recommend = float(grades.get("recommend", 80))
    consider = float(grades.get("consider", 65))
    if total >= recommend:
        return "recommend"
    if total >= consider:
        return "consider"
    return "pass"


def apply_premise_gating(
    dimensions: tuple[AppealDimension, ...],
    base_grade: str,
    *,
    config: dict[str, Any],
    is_long: bool,
) -> tuple[str, tuple[str, ...]]:
    """One-vote-veto: a critical dim below its floor caps the overall grade.

    Returns ``(gated_grade, caps)`` where ``caps`` lists the labels that fired.
    """

    rubric = config.get("premise_rubric", {}) if isinstance(config, dict) else {}
    by_key = {d.key: d for d in dimensions}
    gated = base_grade
    caps: list[str] = []
    for key, spec in rubric.items():
        if not isinstance(spec, dict):
            continue
        gate_below = spec.get("gate_below")
        gate_cap = spec.get("gate_cap")
        if gate_below is None or gate_cap is None:
            continue
        if spec.get("gate_long_only") and not is_long:
            continue
        dim = by_key.get(key)
        if dim is None:
            continue
        if dim.score < float(gate_below):
            new_grade = min_grade(gated, str(gate_cap))
            if grade_rank(new_grade) < grade_rank(gated):
                caps.append(str(spec.get("label", key)))
            gated = new_grade
    return gated, tuple(caps)


def meets_bar(
    premise: PremiseAppealVerdict,
    blurb: BlurbAppealVerdict,
    config: dict[str, Any] | None = None,
) -> bool:
    """Bestseller-grade bar — the same yardstick for every genre."""

    cfg = config if config is not None else load_story_appeal_config()
    bar = cfg.get("meets_bar", {}) if isinstance(cfg, dict) else {}
    premise_min = float(bar.get("premise_min", 75))
    blurb_min = float(bar.get("blurb_min", 70))
    forbid_gated_pass = bool(bar.get("forbid_gated_to_pass", True))

    if premise.total < premise_min or blurb.total < blurb_min:
        return False
    if forbid_gated_pass and premise.gated_grade == "pass":
        return False
    return True


def build_improvement_feedback(
    report: StoryAppealReport, config: dict[str, Any] | None = None
) -> str:
    """Concrete, token-capped feedback injected into conception finalize."""

    cfg = config if config is not None else load_story_appeal_config()
    budget = int(
        (cfg.get("regeneration", {}) or {}).get("feedback_token_budget", 600)
        if isinstance(cfg, dict)
        else 600
    )
    lines: list[str] = ["【上一稿吸引力评估反馈 — 必须针对性改进】"]
    lines.append(
        f"故事吸引力 {report.premise.total:.0f}/100（{report.premise.gated_grade}），"
        f"简介点击力 {report.blurb.total:.0f}/100（{report.blurb.grade}）。"
    )
    if report.premise.gating_caps:
        lines.append("致命短板（一票否决）：" + "、".join(report.premise.gating_caps))
    weak = sorted(
        report.premise.dimensions, key=lambda d: d.score
    )[:3]
    for d in weak:
        if d.score < 3.5:
            lines.append(f"- 故事·{d.label}（{d.score:.1f}/5）：{d.rationale}")
    for s in report.premise.suggestions[:3]:
        lines.append(f"- 建议：{s}")
    for s in report.blurb.suggestions[:4]:
        lines.append(f"- 简介：{s}")
    text = "\n".join(lines)
    # token budget ≈ chars/2 for CJK; cap conservatively.
    cap_chars = budget * 2
    return text[:cap_chars]


async def evaluate_story_appeal(
    session: Any,
    settings: Any,
    *,
    premise: str,
    synopsis: str,
    title: str,
    tags: list[str] | None,
    writing_profile: dict[str, Any] | None = None,
    genre: str | None,
    sub_genre: str | None = None,
    chapter_count: int = 0,
    project_slug: str | None = None,
    judge_model_key: str | None = None,
    platform: str | None = None,
    config: dict[str, Any] | None = None,
) -> StoryAppealReport:
    """Run both evaluators and combine. Never raises — fail-open to det. scores."""

    cfg = config if config is not None else load_story_appeal_config()
    lexicon = resolve_genre_lexicon(genre, sub_genre)
    terms = genre_signal_terms(genre, sub_genre)

    from bestseller.services.blurb_appeal_gate import evaluate_blurb_appeal

    blurb = evaluate_blurb_appeal(
        title=title,
        synopsis=synopsis,
        premise=premise,
        tags=tags,
        genre=genre,
        sub_genre=sub_genre,
        config=cfg,
        lexicon=lexicon,
        platform=platform,
        genre_terms=terms,
    )

    try:
        from bestseller.services.premise_appeal_judge import (
            evaluate_premise_appeal,
        )

        premise_verdict = await evaluate_premise_appeal(
            session,
            settings,
            premise=premise,
            synopsis=synopsis,
            title=title,
            tags=tags,
            writing_profile=writing_profile,
            genre=genre,
            sub_genre=sub_genre,
            chapter_count=chapter_count,
            project_slug=project_slug,
            judge_model_key=judge_model_key,
            config=cfg,
            lexicon=lexicon,
        )
    except Exception:
        logger.warning("premise appeal judge failed; using fallback", exc_info=True)
        premise_verdict = _empty_premise_verdict()

    bar = meets_bar(premise_verdict, blurb, cfg)
    overall = min_grade(premise_verdict.gated_grade, blurb.grade)
    return StoryAppealReport(
        genre=str(genre or ""),
        sub_genre=str(sub_genre or ""),
        canonical_genre=_canonical_genre(genre, sub_genre),
        premise=premise_verdict,
        blurb=blurb,
        meets_bar=bar,
        overall_grade=overall,
    )


def _empty_premise_verdict() -> PremiseAppealVerdict:
    return PremiseAppealVerdict(
        total=0.0, grade="pass", gated_grade="pass", dimensions=(), llm_used=False
    )


__all__ = [
    "apply_premise_gating",
    "build_improvement_feedback",
    "evaluate_story_appeal",
    "genre_signal_terms",
    "grade_from_total",
    "is_appeal_enabled",
    "load_story_appeal_config",
    "meets_bar",
    "resolve_genre_lexicon",
]
