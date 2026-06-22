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

# ruff: noqa: ANN401, RUF001, RUF003 — Chinese labels + Any session/settings.
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
    """Bestseller-grade bar — anchored to REAL competitors, the same for every genre.

    Calibrated against real bestseller blurbs vs slush with a cross-family judge
    (``scripts/calibrate_appeal_against_bestsellers.py``):

    * The deterministic blurb gate is the reproducible, bias-free, competitor-
      anchored signal (real-hit vs slush Δ≈+12; slush tops out ~64) → it is the
      hard gate (``blurb_min``, default 65).
    * The LLM premise score is ADVISORY only — its *absolute* value is unreliable
      (one prompt tweak swings it from rating slush 88 to rating a classic hit 38),
      so by default it does not gate (``premise_min: 0``, ``forbid_gated_to_pass:
      false``). The rigorous story-quality gate is pairwise-vs-competitor win-rate
      (future work), not an absolute LLM number.
    """

    cfg = config if config is not None else load_story_appeal_config()
    bar = cfg.get("meets_bar", {}) if isinstance(cfg, dict) else {}
    premise_min = float(bar.get("premise_min", 0))
    blurb_min = float(bar.get("blurb_min", 80))  # 产品硬线：低于 80 不通过
    forbid_gated_pass = bool(bar.get("forbid_gated_to_pass", False))

    if blurb.total < blurb_min:
        return False
    if premise_min > 0 and premise.total < premise_min:
        return False
    if forbid_gated_pass and premise.gated_grade == "pass":
        return False
    return True


def meets_story_bar(win_rate: float, config: dict[str, Any] | None = None) -> bool:
    """Story-quality gate: pairwise win-rate vs REAL bestsellers ≥ threshold.

    This is the trustworthy story-quality signal (relative blind comparison vs
    real competitors, cross-family judge) — used by the acceptance-time arena
    (``premise_appeal_arena``), not the absolute LLM premise score.
    """

    cfg = config if config is not None else load_story_appeal_config()
    arena = cfg.get("arena", {}) if isinstance(cfg, dict) else {}
    return float(win_rate) >= float(arena.get("story_winrate_min", 0.45))


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
    bar = cfg.get("meets_bar", {}) if isinstance(cfg, dict) else {}
    blurb_min = float(bar.get("blurb_min", 80))
    gap = blurb_min - report.blurb.total

    lines: list[str] = [
        "【上一稿不达标 — 必须按下面逐条重写简介(synopsis)，直到达标】",
        f"达标硬线：简介点击力 ≥ {blurb_min:.0f} 分。当前仅 {report.blurb.total:.0f} 分，"
        f"还差 {gap:.0f} 分。下面是最该补的几项(分越低越拖分)，请逐条改到位：",
    ]
    # 简介(blurb)是达标信号——按最弱维度排序，给诊断+具体修法，引导改到 80。
    blurb_dims = sorted(report.blurb.dimensions, key=lambda d: d.score)
    suggestions = list(report.blurb.suggestions)
    shown = 0
    for d in blurb_dims:
        if d.score >= 4.0 or shown >= 5:
            continue
        fix = _DIMENSION_FIX_HINT.get(d.key, "")
        line = f"- 简介·{d.label}（{d.score:.1f}/5）：{d.rationale}"
        if fix:
            line += f" → {fix}"
        lines.append(line)
        shown += 1
    if not shown:
        for s in suggestions[:4]:
            lines.append(f"- 简介：{s}")
    # 故事层(premise) 仅作 advisory 提示，不喧宾夺主。
    if report.premise.gating_caps:
        lines.append("故事层提醒（advisory）：" + "、".join(report.premise.gating_caps[:2]))
    lines.append(
        "重写要求：首句≤30字的强钩(疑问/反差/冲突)、卖点三要素齐(身份+冲突+代价)、"
        "高唤起情绪前置、长度80-140字、结尾留悬念不剧透、禁AI腔套话。"
    )
    text = "\n".join(lines)
    # token budget ≈ chars/2 for CJK; cap conservatively.
    cap_chars = budget * 2
    return text[:cap_chars]


# 各 blurb 维度的【可执行】重写指引（喂给 finalize 重生，让模型知道怎么补到 80）。
_DIMENSION_FIX_HINT: dict[str, str] = {
    "hook_strength": "把首句压到 30 字内，用一句疑问/反差/开局冲突瞬间抓人",
    "selling_triad": "补齐身份反差+开局冲突事件+失败代价三要素",
    "emotion_charge": "把退婚/背叛/重生/绝境等高唤起情绪事件提到最前",
    "length_format": "压到 80-140 字、分 2-4 段",
    "concreteness": "给主角具名或第一人称，加入具体数字/地点/物件",
    "open_loop_end": "结尾换成开放式悬念，绝不剧透结局",
    "anti_template": "删除'本以为/却没想到/何去何从'等 AI 腔套话",
    "differentiation": "点出一句别人没写过的独家设定",
    "genre_signal": "让书名/标签/简介题材信号一致",
    "adjective_thrift": "少用形容词，改强动词驱动",
}


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
    "meets_story_bar",
    "resolve_genre_lexicon",
]
