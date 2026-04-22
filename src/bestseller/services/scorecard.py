"""L8 NovelScorecard — the quality dashboard layer.

Aggregates evidence from every previous layer into a single 0-100 score
and a structured snapshot so humans (or dashboards) can triage a project
without re-running the whole pipeline. Plan §3 L8.

Data sources:

* ``ChapterModel`` + ``ChapterDraftVersionModel`` — chapter count, length
  distribution (bug #4 evidence).
* ``ChapterQualityReportModel`` — count of blocked chapters and
  per-violation frequencies (bugs #1, #2, #11, #12).
* ``ChapterAuditFindingModel`` — CHAPTER_GAP count for missing chapters
  (bug #3).
* ``DiversityBudgetModel`` — opening / cliffhanger entropy (bugs #5, #10)
  + vocab HHI (bug #7).

Each metric is computed in isolation in a pure helper so unit tests can
feed synthetic inputs without touching the DB.
"""

from __future__ import annotations

import logging
import math
import statistics
from collections import Counter
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, Iterable, Mapping, Sequence
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.infra.db.models import (
    ChapterAuditFindingModel,
    ChapterDraftVersionModel,
    ChapterModel,
    ChapterQualityReportModel,
    DiversityBudgetModel,
    NovelScorecardModel,
    ProjectModel,
)
from bestseller.services.diversity_budget import DiversityBudget
from bestseller.services.hype_engine import (
    HypeScheme,
    HypeType,
    hype_scheme_from_dict,
)


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data class.
# ---------------------------------------------------------------------------


# Legacy-project defaults for the hype dimension. A project written before
# the Reader Hype Engine landed has no hype assignments in the database; if
# we scored those at 0 the historical fleet would all drop 20 points
# overnight. Plan §Phase 3 "迁移策略" calls for median defaults (0.5 / 5.0 /
# 0.5) so legacy projects are treated as "average, unknown" rather than
# "catastrophically bad". The replay script produces the delta chart from
# these defaults.
LEGACY_HYPE_DISTRIBUTION_ENTROPY: float = 0.5
LEGACY_HYPE_INTENSITY_MEAN: float = 5.0
LEGACY_COMEDIC_BEAT_HIT_RATIO: float = 0.5


@dataclass(frozen=True)
class NovelScorecard:
    """Immutable snapshot of the project's quality posture."""

    project_id: UUID
    total_chapters: int
    missing_chapters: int
    chapters_blocked: int
    length_mean: float
    length_stddev: float
    length_cv: float  # stddev / mean — plan §8 target ≤ 0.10
    cjk_leak_chapters: int
    dialog_integrity_violations: int
    pov_drift_chapters: int
    opening_archetype_entropy: float  # normalized [0, 1]
    cliffhanger_entropy: float
    vocab_hhi: float  # Herfindahl index [0, 1]
    top_overused_words: tuple[tuple[str, int], ...]
    quality_score: float  # 0-100 blend
    # INFO-severity: sub-threshold CJK glyphs in English novels. Doesn't
    # feed into quality_score — shown for visibility so dashboards can
    # chase residual language leaks before they become block-level.
    cjk_residue_chapters: int = 0
    # ------------------------------------------------------------------
    # Reader Hype Engine metrics (Phase 2). Each feeds ``compute_quality_score``.
    # Defaults are 0 here but the driver (``compute_scorecard``) substitutes
    # the LEGACY_* medians when the project has no hype assignments so
    # historical projects don't drop 20 points overnight — see the
    # "migration strategy" section of the plan.
    # ------------------------------------------------------------------
    hype_distribution_entropy: float = 0.0  # Shannon-normalized [0, 1]
    hype_intensity_mean: float = 0.0  # [0, 10] averaged over assigned chapters
    hype_intensity_variance: float = 0.0
    humiliation_payoff_lag: float = 0.0  # Phase 3 setup→payoff avg gap
    comedic_beat_density: float = 0.0  # observed share of COMEDIC_BEAT hype
    comedic_beat_hit_ratio: float = 0.0  # observed / target, capped at 1.0
    hype_missing_chapters: int = 0  # chapters without assigned hype type
    golden_three_weak: bool = False  # any chapter 1-3 flagged WEAK

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_id": str(self.project_id),
            "total_chapters": self.total_chapters,
            "missing_chapters": self.missing_chapters,
            "chapters_blocked": self.chapters_blocked,
            "length_mean": round(self.length_mean, 2),
            "length_stddev": round(self.length_stddev, 2),
            "length_cv": round(self.length_cv, 4),
            "cjk_leak_chapters": self.cjk_leak_chapters,
            "cjk_residue_chapters": self.cjk_residue_chapters,
            "dialog_integrity_violations": self.dialog_integrity_violations,
            "pov_drift_chapters": self.pov_drift_chapters,
            "opening_archetype_entropy": round(self.opening_archetype_entropy, 4),
            "cliffhanger_entropy": round(self.cliffhanger_entropy, 4),
            "vocab_hhi": round(self.vocab_hhi, 4),
            "top_overused_words": [[w, c] for w, c in self.top_overused_words],
            "quality_score": round(self.quality_score, 2),
            "hype_distribution_entropy": round(self.hype_distribution_entropy, 4),
            "hype_intensity_mean": round(self.hype_intensity_mean, 3),
            "hype_intensity_variance": round(self.hype_intensity_variance, 3),
            "humiliation_payoff_lag": round(self.humiliation_payoff_lag, 2),
            "comedic_beat_density": round(self.comedic_beat_density, 4),
            "comedic_beat_hit_ratio": round(self.comedic_beat_hit_ratio, 4),
            "hype_missing_chapters": self.hype_missing_chapters,
            "golden_three_weak": self.golden_three_weak,
        }


# ---------------------------------------------------------------------------
# Pure helpers.
# ---------------------------------------------------------------------------


def normalized_entropy(counts: Mapping[Any, int]) -> float:
    """Return Shannon entropy scaled to [0, 1].

    Empty / single-symbol distributions return 0.0. Uniform distribution
    over N symbols returns 1.0. We normalize by ``log2(N)`` where N is
    the number of observed symbols (NOT the theoretical max) — this
    captures "given the symbols you did use, how evenly did you use
    them." Callers who want "how much of the full pool did you use" should
    multiply by ``len(symbols)/len(pool)``.
    """

    total = sum(counts.values())
    if total <= 0:
        return 0.0
    n = len([c for c in counts.values() if c > 0])
    if n <= 1:
        return 0.0
    h = -sum((c / total) * math.log2(c / total) for c in counts.values() if c > 0)
    max_h = math.log2(n)
    if max_h <= 0:
        return 0.0
    return max(0.0, min(1.0, h / max_h))


def herfindahl_index(counts: Mapping[Any, int]) -> float:
    """Return HHI for a count distribution, scaled to [0, 1].

    HHI = Σ sᵢ² where sᵢ is each element's share. Low = diverse, high =
    concentrated (one word dominates). Empty → 0.
    """

    total = sum(counts.values())
    if total <= 0:
        return 0.0
    return sum((c / total) ** 2 for c in counts.values() if c > 0)


def length_stats(
    lengths: Iterable[int],
) -> tuple[float, float, float]:
    """Return (mean, stddev, coefficient_of_variation).

    CV = stddev / mean (0 if mean is 0). This is the single plan §8
    headline metric (target ≤ 0.10).
    """

    sample = [l for l in lengths if l > 0]
    if not sample:
        return 0.0, 0.0, 0.0
    mean = statistics.fmean(sample)
    stddev = statistics.pstdev(sample) if len(sample) > 1 else 0.0
    cv = stddev / mean if mean else 0.0
    return mean, stddev, cv


def compute_quality_score(
    *,
    total_chapters: int,
    missing_chapters: int,
    chapters_blocked: int,
    length_cv: float,
    cjk_leak_chapters: int,
    dialog_integrity_violations: int,
    pov_drift_chapters: int,
    opening_archetype_entropy: float,
    cliffhanger_entropy: float,
    vocab_hhi: float,
    # ------------------------------------------------------------------
    # Hype-engine inputs (Phase 2). Optional so historical callers that
    # don't know about the engine still get a reasonable score — the
    # LEGACY_* constants are the "unknown, treat as average" substitutes
    # used by ``compute_scorecard`` when a project has no hype data.
    # ------------------------------------------------------------------
    hype_distribution_entropy: float = LEGACY_HYPE_DISTRIBUTION_ENTROPY,
    hype_intensity_mean: float = LEGACY_HYPE_INTENSITY_MEAN,
    comedic_beat_hit_ratio: float = LEGACY_COMEDIC_BEAT_HIT_RATIO,
    hype_missing_ratio: float = 0.0,
    golden_three_weak: bool = False,
) -> float:
    """Blend the metrics into a single 0-100 health score.

    Weights follow plan Phase 2 policy — 55% penalty / 25% diversity /
    20% hype engine. The hype axis can subtract up to 3 additional points
    when ``golden_three_weak`` is set (the "first impressions" override)
    so the score can dip below the nominal floor of 0 only via that
    channel; the final clamp keeps the public-facing value non-negative.
    """

    if total_chapters <= 0:
        return 0.0

    # Penalty ratios — 0 = perfect, 1 = every chapter broken.
    miss_ratio = min(1.0, missing_chapters / max(total_chapters, 1))
    block_ratio = min(1.0, chapters_blocked / max(total_chapters, 1))
    cjk_ratio = min(1.0, cjk_leak_chapters / max(total_chapters, 1))
    dialog_ratio = min(
        1.0, dialog_integrity_violations / max(total_chapters, 1)
    )
    pov_ratio = min(1.0, pov_drift_chapters / max(total_chapters, 1))
    # Length CV: > 0.30 → full penalty; linear below.
    length_penalty = min(1.0, length_cv / 0.30)

    # Diversity rewards — higher entropy / lower HHI = better.
    opening_reward = max(0.0, min(1.0, opening_archetype_entropy))
    cliff_reward = max(0.0, min(1.0, cliffhanger_entropy))
    # Vocab HHI is "bad when high" — invert. Anything above 0.15 (high
    # concentration) zeroes the reward.
    vocab_reward = max(0.0, 1.0 - (vocab_hhi / 0.15))

    # Hype rewards — each component clamps to [0, 1] then weighs into the
    # 20-point pool. ``hype_missing_ratio`` is 0 when every chapter has a
    # hype assignment, 1 when none do.
    hype_entropy_reward = max(0.0, min(1.0, hype_distribution_entropy))
    hype_intensity_reward = max(0.0, min(1.0, hype_intensity_mean / 10.0))
    comedic_reward = max(0.0, min(1.0, comedic_beat_hit_ratio))
    hype_coverage_reward = max(0.0, 1.0 - min(1.0, hype_missing_ratio))

    # Penalties: 55 points total.
    penalty_score = (
        16.0 * (1 - miss_ratio)
        + 12.0 * (1 - block_ratio)
        + 12.0 * (1 - cjk_ratio)
        + 7.0 * (1 - dialog_ratio)
        + 4.0 * (1 - pov_ratio)
        + 4.0 * (1 - length_penalty)
    )  # 55 total

    # Diversity rewards: 25 points total.
    diversity_score = (
        9.0 * opening_reward
        + 9.0 * cliff_reward
        + 7.0 * min(1.0, vocab_reward)
    )  # 25 total

    # Hype rewards: 20 points total, minus up to 3 for golden_three_weak.
    hype_score = (
        6.0 * hype_entropy_reward
        + 6.0 * hype_intensity_reward
        + 4.0 * comedic_reward
        + 4.0 * hype_coverage_reward
    )  # 20 total (before penalty)
    if golden_three_weak:
        hype_score -= 3.0

    return round(
        max(0.0, min(100.0, penalty_score + diversity_score + hype_score)), 2
    )


def _aggregate_hype_metrics(
    *,
    total_chapters: int,
    hype_types_by_chapter: Mapping[int, str],
    hype_intensities: Sequence[float],
    scheme: HypeScheme,
) -> tuple[float, float, float, float, float, int, int]:
    """Summarize per-chapter hype assignments into scorecard inputs.

    Returns ``(entropy, intensity_mean, intensity_variance, comedic_density,
    comedic_hit_ratio, missing_chapters, scored_chapters)``.

    * ``entropy`` — Shannon-normalized entropy of the HypeType distribution
      across assigned chapters. 0 when none assigned or only one type used.
    * ``intensity_mean`` / ``intensity_variance`` — over the populated rows.
    * ``comedic_density`` — COMEDIC_BEAT chapters / chapters with any hype
      assignment. Capped at 1.0.
    * ``comedic_hit_ratio`` — observed density / scheme target, capped at
      1.0. When the scheme's target is 0 we treat any observed comedic
      beat as "hit" (ratio = 1.0) so novels with no target aren't
      punished.
    * ``missing_chapters`` — count of chapters with no hype_type row.
    * ``scored_chapters`` — count of chapters that do have one; the
      driver uses this to decide between measured values and the legacy
      median substitutes.
    """

    scored = len(hype_types_by_chapter)
    missing = max(0, total_chapters - scored)
    if scored == 0:
        return 0.0, 0.0, 0.0, 0.0, 0.0, missing, 0

    type_counter: Counter[str] = Counter(hype_types_by_chapter.values())
    entropy = normalized_entropy(type_counter)

    if hype_intensities:
        intensity_mean = statistics.fmean(hype_intensities)
        intensity_var = (
            statistics.pvariance(hype_intensities)
            if len(hype_intensities) > 1
            else 0.0
        )
    else:
        intensity_mean = 0.0
        intensity_var = 0.0

    comedic_count = type_counter.get(HypeType.COMEDIC_BEAT.value, 0)
    comedic_density = comedic_count / scored
    target = scheme.comedic_beat_density_target
    if target <= 0:
        # No declared target → any observed comedic beat is treated as
        # full credit; zero observations stay at zero so the score still
        # reflects the absence.
        comedic_hit_ratio = 1.0 if comedic_count > 0 else 0.0
    else:
        comedic_hit_ratio = min(1.0, comedic_density / target)

    return (
        entropy,
        intensity_mean,
        intensity_var,
        comedic_density,
        comedic_hit_ratio,
        missing,
        scored,
    )


def _chapter_gap_count(
    chapter_numbers: Sequence[int], expected_count: int | None = None
) -> int:
    """Count missing chapters inside the sequence.

    Missing = gap between successive chapter_no values plus (if
    ``expected_count`` is given) shortfall from the expected total.
    """

    if not chapter_numbers:
        return 0 if expected_count is None else max(0, expected_count)
    ordered = sorted(chapter_numbers)
    # In-sequence gaps.
    gap = 0
    for prev, nxt in zip(ordered, ordered[1:]):
        if nxt > prev + 1:
            gap += nxt - prev - 1
    # Shortfall vs expected_count.
    if expected_count is not None and expected_count > ordered[-1]:
        gap += expected_count - ordered[-1]
    return gap


# ---------------------------------------------------------------------------
# Async computation — driver.
# ---------------------------------------------------------------------------


async def compute_scorecard(
    session: AsyncSession,
    project_id: UUID,
    *,
    expected_chapter_count: int | None = None,
    top_overused_n: int = 10,
) -> NovelScorecard:
    """Pull all the evidence and produce a ``NovelScorecard``.

    ``expected_chapter_count`` lets the caller override the gap-count
    logic when the project plan's expected total is known (e.g. from the
    outline). Without it we only count in-sequence gaps.
    """

    # 1. Chapter list + current draft lengths + hype assignment.
    chapters_stmt = (
        select(
            ChapterModel.chapter_number,
            ChapterDraftVersionModel.content_md,
            ChapterModel.hype_type,
            ChapterModel.hype_intensity,
        )
        .join(
            ChapterDraftVersionModel,
            ChapterDraftVersionModel.chapter_id == ChapterModel.id,
            isouter=True,
        )
        .where(
            ChapterModel.project_id == project_id,
            (
                ChapterDraftVersionModel.is_current.is_(True)
                | ChapterDraftVersionModel.is_current.is_(None)
            ),
        )
        .order_by(ChapterModel.chapter_number)
    )
    chapter_rows = (await session.execute(chapters_stmt)).all()

    chapter_numbers: list[int] = []
    lengths: list[int] = []
    hype_types_by_chapter: dict[int, str] = {}
    hype_intensities: list[float] = []
    for ch_no, content_md, hype_type, hype_intensity in chapter_rows:
        chapter_numbers.append(int(ch_no))
        if content_md:
            lengths.append(len(content_md))
        if hype_type:
            hype_types_by_chapter[int(ch_no)] = str(hype_type)
        if hype_intensity is not None:
            hype_intensities.append(float(hype_intensity))

    total_chapters = len(set(chapter_numbers))
    missing_chapters = _chapter_gap_count(chapter_numbers, expected_chapter_count)
    mean_len, stddev_len, cv_len = length_stats(lengths)

    # 2. Quality reports — blocked chapters + per-violation counts.
    quality_stmt = select(
        ChapterQualityReportModel.blocks_write,
        ChapterQualityReportModel.report_json,
        ChapterQualityReportModel.chapter_id,
    ).where(
        ChapterQualityReportModel.chapter_id.in_(
            select(ChapterModel.id).where(
                ChapterModel.project_id == project_id
            )
        )
    )
    quality_rows = (await session.execute(quality_stmt)).all()

    blocked_chapter_ids: set[UUID] = set()
    violation_counter: Counter[str] = Counter()
    dialog_count = 0
    pov_drift_chapter_ids: set[UUID] = set()
    cjk_leak_chapter_ids: set[UUID] = set()
    # Hype-engine penalties surfaced through quality reports. ``golden_three_weak``
    # flips true when any chapter 1-3 fires GOLDEN_THREE_WEAK or (pre-three)
    # ENDING_SENTENCE_WEAK with severity="block". Those violations are the
    # gate-escalated ones from ``_GOLDEN_THREE_BLOCK_CODES``.
    golden_three_weak = False

    for blocks_write, report_json, chapter_id in quality_rows:
        if blocks_write:
            blocked_chapter_ids.add(chapter_id)
        violations = (report_json or {}).get("violations") or []
        for v in violations:
            code = str(v.get("code", ""))
            if not code:
                continue
            violation_counter[code] += 1
            if code == "DIALOG_UNPAIRED":
                dialog_count += 1
            elif code == "POV_DRIFT":
                pov_drift_chapter_ids.add(chapter_id)
            elif code == "LANG_LEAK_CJK_IN_EN":
                cjk_leak_chapter_ids.add(chapter_id)
            elif code == "GOLDEN_THREE_WEAK":
                golden_three_weak = True
            elif code == "ENDING_SENTENCE_WEAK" and v.get("severity") == "block":
                # The chapter_validator emits severity="block" only for
                # chapters 1-3 (see EndingSentenceImpactCheck docstring).
                golden_three_weak = True

    # 3. Audit findings — cross-check CHAPTER_GAP count.
    audit_stmt = select(
        ChapterAuditFindingModel.code, func.count()
    ).where(
        ChapterAuditFindingModel.project_id == project_id
    ).group_by(ChapterAuditFindingModel.code)
    audit_counts = dict((await session.execute(audit_stmt)).all())
    if audit_counts.get("CHAPTER_GAP") and missing_chapters == 0:
        # Only trust audit findings when we had no expected-count hint.
        missing_chapters = int(audit_counts["CHAPTER_GAP"])

    # 3b. Latest-audit-window snapshot — the L7 CLI is append-only so we
    # filter to the most recent run (defined as MAX(created_at) with a
    # small look-back window) to get a point-in-time picture. Historical
    # quality_reports only capture generation-time state; when the audit
    # has been re-run after repairs, we want the fresher signal to
    # contribute to dialog / POV / CJK metrics.
    latest_at_stmt = select(
        func.max(ChapterAuditFindingModel.created_at)
    ).where(ChapterAuditFindingModel.project_id == project_id)
    latest_at = (await session.execute(latest_at_stmt)).scalar_one_or_none()

    audit_dialog_chapters: set[int] = set()
    audit_pov_chapters: set[int] = set()
    audit_cjk_block_chapters: set[int] = set()
    audit_cjk_residue_chapters: set[int] = set()

    if latest_at is not None:
        # A `bestseller audit` run takes well under a minute; 5 is a safe
        # window that groups all findings from the same invocation while
        # still excluding older historical runs.
        window_start = latest_at - timedelta(minutes=5)
        latest_audit_stmt = (
            select(
                ChapterAuditFindingModel.code,
                ChapterAuditFindingModel.chapter_no,
            )
            .where(
                ChapterAuditFindingModel.project_id == project_id,
                ChapterAuditFindingModel.created_at >= window_start,
                ChapterAuditFindingModel.chapter_no.is_not(None),
            )
            .distinct()
        )
        for code, chapter_no in (
            await session.execute(latest_audit_stmt)
        ).all():
            if chapter_no is None:
                continue
            if code == "DIALOG_UNPAIRED":
                audit_dialog_chapters.add(int(chapter_no))
            elif code == "POV_DRIFT":
                audit_pov_chapters.add(int(chapter_no))
            elif code == "LANG_LEAK_CJK_IN_EN":
                audit_cjk_block_chapters.add(int(chapter_no))
            elif code == "LANG_RESIDUE_CJK_IN_EN":
                audit_cjk_residue_chapters.add(int(chapter_no))

    # 4. Diversity budget — openings / cliffhangers / vocab.
    budget_stmt = select(DiversityBudgetModel).where(
        DiversityBudgetModel.project_id == project_id
    )
    budget_row = (await session.execute(budget_stmt)).scalar_one_or_none()
    if budget_row is None:
        budget = DiversityBudget(project_id=project_id)
    else:
        budget = DiversityBudget.from_dict(
            project_id,
            {
                "openings_used": budget_row.openings_used,
                "cliffhangers_used": budget_row.cliffhangers_used,
                "titles_used": budget_row.titles_used,
                "vocab_freq": budget_row.vocab_freq,
            },
        )

    opening_counter: Counter[str] = Counter(
        u.archetype.value for u in budget.openings_used
    )
    cliffhanger_counter: Counter[str] = Counter(
        u.kind.value for u in budget.cliffhangers_used
    )
    opening_entropy = normalized_entropy(opening_counter)
    cliffhanger_entropy = normalized_entropy(cliffhanger_counter)

    aggregate_vocab: Counter[str] = Counter()
    for chapter_counts in budget.vocab_freq.values():
        for word, count in chapter_counts.items():
            aggregate_vocab[word] += count
    vocab_hhi = herfindahl_index(aggregate_vocab)
    top_overused = tuple(aggregate_vocab.most_common(top_overused_n))

    # 4b. Hype engine aggregation. Loads the project's HypeScheme (for the
    # comedic-beat target) and crunches per-chapter assignments into
    # distribution entropy / intensity stats / coverage / comedic ratio.
    scheme_stmt = select(ProjectModel.hype_scheme_json).where(
        ProjectModel.id == project_id
    )
    scheme_raw = (await session.execute(scheme_stmt)).scalar_one_or_none()
    scheme = hype_scheme_from_dict(scheme_raw if isinstance(scheme_raw, dict) else None)

    (
        hype_entropy,
        hype_intensity_mean,
        hype_intensity_var,
        comedic_density,
        comedic_hit_ratio,
        hype_missing,
        hype_scored,  # chapters with at least one hype assignment
    ) = _aggregate_hype_metrics(
        total_chapters=total_chapters,
        hype_types_by_chapter=hype_types_by_chapter,
        hype_intensities=hype_intensities,
        scheme=scheme,
    )

    # 5. Blend.
    #
    # For dialog / POV / CJK we prefer the fresher of two signals:
    #  - ``quality_reports`` (written at generation time by L4/L5 gates)
    #  - latest-audit-window distinct chapter set (written by L7 CLI)
    # Taking MAX avoids double-counting (quality reports and audit can
    # flag the same chapter) while ensuring repairs that haven't been
    # regenerated through the pipeline still bubble up as "fixed" when
    # audits no longer see them.
    chapters_blocked = len(blocked_chapter_ids)
    cjk_leak_chapters = max(len(cjk_leak_chapter_ids), len(audit_cjk_block_chapters))
    pov_drift_chapters = max(len(pov_drift_chapter_ids), len(audit_pov_chapters))
    dialog_integrity_violations = max(dialog_count, len(audit_dialog_chapters))

    # Projects without any hype assignments (legacy / Phase 0) get median
    # stand-ins so they don't crater the score. When the project has
    # scored some chapters we honor the measured values even if a handful
    # of chapters are still unassigned (``hype_missing_ratio`` captures
    # that coverage gap).
    if hype_scored == 0 and scheme.is_empty:
        qs_hype_entropy = LEGACY_HYPE_DISTRIBUTION_ENTROPY
        qs_hype_intensity = LEGACY_HYPE_INTENSITY_MEAN
        qs_comedic_hit = LEGACY_COMEDIC_BEAT_HIT_RATIO
        qs_hype_missing_ratio = 0.0
    else:
        qs_hype_entropy = hype_entropy
        qs_hype_intensity = hype_intensity_mean
        qs_comedic_hit = comedic_hit_ratio
        qs_hype_missing_ratio = (
            hype_missing / max(total_chapters, 1) if total_chapters else 0.0
        )

    quality_score = compute_quality_score(
        total_chapters=total_chapters,
        missing_chapters=missing_chapters,
        chapters_blocked=chapters_blocked,
        length_cv=cv_len,
        cjk_leak_chapters=cjk_leak_chapters,
        dialog_integrity_violations=dialog_integrity_violations,
        pov_drift_chapters=pov_drift_chapters,
        opening_archetype_entropy=opening_entropy,
        cliffhanger_entropy=cliffhanger_entropy,
        vocab_hhi=vocab_hhi,
        hype_distribution_entropy=qs_hype_entropy,
        hype_intensity_mean=qs_hype_intensity,
        comedic_beat_hit_ratio=qs_comedic_hit,
        hype_missing_ratio=qs_hype_missing_ratio,
        golden_three_weak=golden_three_weak,
    )

    return NovelScorecard(
        project_id=project_id,
        total_chapters=total_chapters,
        missing_chapters=missing_chapters,
        chapters_blocked=chapters_blocked,
        length_mean=mean_len,
        length_stddev=stddev_len,
        length_cv=cv_len,
        cjk_leak_chapters=cjk_leak_chapters,
        cjk_residue_chapters=len(audit_cjk_residue_chapters),
        dialog_integrity_violations=dialog_integrity_violations,
        pov_drift_chapters=pov_drift_chapters,
        opening_archetype_entropy=opening_entropy,
        cliffhanger_entropy=cliffhanger_entropy,
        vocab_hhi=vocab_hhi,
        top_overused_words=top_overused,
        quality_score=quality_score,
        hype_distribution_entropy=hype_entropy,
        hype_intensity_mean=hype_intensity_mean,
        hype_intensity_variance=hype_intensity_var,
        comedic_beat_density=comedic_density,
        comedic_beat_hit_ratio=comedic_hit_ratio,
        hype_missing_chapters=hype_missing,
        golden_three_weak=golden_three_weak,
    )


async def save_scorecard(
    session: AsyncSession, scorecard: NovelScorecard
) -> None:
    """Upsert the scorecard snapshot (``project_id`` is the primary key)."""

    snapshot = scorecard.to_dict()
    stmt = pg_insert(NovelScorecardModel).values(
        project_id=scorecard.project_id,
        snapshot_json=snapshot,
        quality_score=scorecard.quality_score,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["project_id"],
        set_={
            "snapshot_json": stmt.excluded.snapshot_json,
            "quality_score": stmt.excluded.quality_score,
            "computed_at": func.now(),
        },
    )
    await session.execute(stmt)


__all__ = [
    "NovelScorecard",
    "compute_quality_score",
    "compute_scorecard",
    "herfindahl_index",
    "length_stats",
    "normalized_entropy",
    "save_scorecard",
]
