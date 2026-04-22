"""Phase 3 historical-project scorecard replay.

Why this exists
---------------
Phase 2 of the Reader Hype Engine rewrote ``compute_quality_score`` from a
70/30 (penalty/diversity) blend to a 55/25/20 (penalty/diversity/hype)
blend. Historical projects have no hype assignments in the database, so
re-running the scorecard on them would drop 20 points overnight. The
plan's migration strategy substitutes median values (entropy=0.5 /
intensity=5.0 / comedic_hit=0.5) for legacy projects so the delta stays
small; this script verifies the prediction empirically by replaying every
project through the new weights and emitting a per-project CSV.

The output is the evidence pack used to decide whether a legacy project
"really has no 爽感" (→ ship the new score as-is) or "scored low because
the medians were unflattering" (→ tune the defaults).

Usage
-----
    # dry-run — no DB writes; CSV only (default)
    python scripts/replay_scorecard_phase3.py --out /tmp/scorecard_delta.csv

    # filter to one project
    python scripts/replay_scorecard_phase3.py \\
        --project-slug romantasy-1776330993 --out /tmp/delta.csv

    # persist the new scorecard snapshot after reviewing the CSV
    python scripts/replay_scorecard_phase3.py --apply

CSV columns
-----------
    project_slug, old_score, new_score, delta,
    dominant_delta_dimension, notes

``dominant_delta_dimension`` is a rough "where did the points move"
heuristic — the sub-score that swung the most between the old and new
weighting formulas. Values are one of {penalty, diversity, hype, legacy}
so you can eyeball whether a drop was driven by the new 20pt hype axis
or by the re-weighted penalty/diversity block.

Safety
------
* Dry-run by default. ``--apply`` is the only path that writes back to
  ``novel_scorecards``.
* Idempotent: running twice produces the same CSV (up to floating-point
  rounding).
* Prints a summary at the end: total projects, count with |Δ| > 5,
  mean/max delta. Per plan acceptance criterion: "∥Δ∥ > 5 的项目数 < 总项
  目数 10%".
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

# Path shim — ``scripts/`` isn't a package; mirror backfill_invariants.py.
_THIS = Path(__file__).resolve()
_SRC = _THIS.parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402

from bestseller.infra.db.models import (  # noqa: E402
    NovelScorecardModel,
    ProjectModel,
)
from bestseller.infra.db.session import session_scope  # noqa: E402
from bestseller.services.scorecard import (  # noqa: E402
    LEGACY_COMEDIC_BEAT_HIT_RATIO,
    LEGACY_HYPE_DISTRIBUTION_ENTROPY,
    LEGACY_HYPE_INTENSITY_MEAN,
    compute_quality_score,
    compute_scorecard,
    save_scorecard,
)
from bestseller.settings import load_settings  # noqa: E402


# Plan Phase 3 acceptance threshold — a project with |Δ| > 5 points is
# flagged for human review. < 10% of the fleet should exceed this.
_FLAG_DELTA: float = 5.0


@dataclass(frozen=True)
class ReplayRow:
    """One project's before/after scorecard projection."""

    project_slug: str
    old_score: float
    new_score: float
    delta: float
    dominant_delta_dimension: str
    notes: str

    def to_csv_row(self) -> list[str]:
        return [
            self.project_slug,
            f"{self.old_score:.2f}",
            f"{self.new_score:.2f}",
            f"{self.delta:+.2f}",
            self.dominant_delta_dimension,
            self.notes,
        ]


def _dominant_delta_dimension(
    *,
    total_chapters: int,
    miss_ratio: float,
    block_ratio: float,
    cjk_ratio: float,
    dialog_ratio: float,
    pov_ratio: float,
    length_penalty: float,
    opening_reward: float,
    cliff_reward: float,
    vocab_reward: float,
    hype_entropy_reward: float,
    hype_intensity_reward: float,
    comedic_reward: float,
    hype_coverage_reward: float,
    golden_three_weak: bool,
    had_hype_data: bool,
) -> str:
    """Return which axis drove the biggest re-weighting delta.

    The plan's 70/30 → 55/25/20 rewrite moves 15 points out of penalties
    (70 → 55) and 5 points out of diversity (30 → 25), donating 20 to a
    brand-new hype axis. We compute, per project, what fraction of each
    axis the project *kept* (so broken projects keep less penalty pool,
    high-diversity projects keep more diversity pool) and report the axis
    where the delta between the old pool contribution and the new pool
    contribution is largest.

    When the project has no hype data, the new 20-point hype axis is
    populated entirely by median substitutes — so the label is always
    "legacy" regardless of which axis moved most. That separates "this
    project's delta is a legacy-default artefact" from "this project
    really did lose points in the penalty/diversity rewrite" and is the
    triage signal the plan calls for.
    """

    if total_chapters <= 0:
        return "unknown"

    if not had_hype_data:
        # Legacy fleet — the hype score is a median fabrication, so the
        # delta breakdown is inherently suspect. Stamp it "legacy" and
        # let reviewers decide whether to tune defaults or accept.
        return "legacy"

    # OLD formula (70/30): reconstructed weights.
    old_penalty = (
        20.0 * (1 - miss_ratio)
        + 15.0 * (1 - block_ratio)
        + 15.0 * (1 - cjk_ratio)
        + 10.0 * (1 - dialog_ratio)
        + 5.0 * (1 - pov_ratio)
        + 5.0 * (1 - length_penalty)
    )  # 70 total
    old_diversity = (
        10.0 * opening_reward + 10.0 * cliff_reward + 10.0 * vocab_reward
    )  # 30 total

    # NEW formula (55/25/20).
    new_penalty = (
        16.0 * (1 - miss_ratio)
        + 12.0 * (1 - block_ratio)
        + 12.0 * (1 - cjk_ratio)
        + 7.0 * (1 - dialog_ratio)
        + 4.0 * (1 - pov_ratio)
        + 4.0 * (1 - length_penalty)
    )  # 55 total
    new_diversity = (
        9.0 * opening_reward + 9.0 * cliff_reward + 7.0 * vocab_reward
    )  # 25 total
    new_hype = (
        6.0 * hype_entropy_reward
        + 6.0 * hype_intensity_reward
        + 4.0 * comedic_reward
        + 4.0 * hype_coverage_reward
    )  # 20 total

    penalty_delta = new_penalty - old_penalty
    diversity_delta = new_diversity - old_diversity
    # "hype_delta" is simply the new axis contribution since the old
    # formula didn't have one.
    hype_delta = new_hype - (3.0 if golden_three_weak else 0.0)

    # Rank by absolute magnitude; break ties by the sign-agnostic size.
    magnitudes: list[tuple[str, float]] = [
        ("penalty", abs(penalty_delta)),
        ("diversity", abs(diversity_delta)),
        ("hype", abs(hype_delta)),
    ]
    magnitudes.sort(key=lambda kv: kv[1], reverse=True)
    return magnitudes[0][0]


async def _old_quality_score(
    snapshot: dict[str, Any], total_chapters: int
) -> float:
    """Reconstruct the 70/30-era quality score from a stored snapshot.

    Pre-Phase 2 snapshots don't carry hype metrics, so we derive the old
    score purely from the penalty + diversity fields. This matches what
    ``compute_quality_score`` used to produce before the rewrite.
    """

    if total_chapters <= 0:
        return 0.0

    miss_ratio = min(1.0, (snapshot.get("missing_chapters") or 0) / max(total_chapters, 1))
    block_ratio = min(
        1.0, (snapshot.get("chapters_blocked") or 0) / max(total_chapters, 1)
    )
    cjk_ratio = min(
        1.0, (snapshot.get("cjk_leak_chapters") or 0) / max(total_chapters, 1)
    )
    dialog_ratio = min(
        1.0,
        (snapshot.get("dialog_integrity_violations") or 0) / max(total_chapters, 1),
    )
    pov_ratio = min(
        1.0, (snapshot.get("pov_drift_chapters") or 0) / max(total_chapters, 1)
    )
    length_cv = float(snapshot.get("length_cv") or 0.0)
    length_penalty = min(1.0, length_cv / 0.30)

    opening_reward = max(0.0, min(1.0, float(snapshot.get("opening_archetype_entropy") or 0.0)))
    cliff_reward = max(0.0, min(1.0, float(snapshot.get("cliffhanger_entropy") or 0.0)))
    vocab_hhi = float(snapshot.get("vocab_hhi") or 0.0)
    vocab_reward = max(0.0, 1.0 - (vocab_hhi / 0.15))

    old_penalty = (
        20.0 * (1 - miss_ratio)
        + 15.0 * (1 - block_ratio)
        + 15.0 * (1 - cjk_ratio)
        + 10.0 * (1 - dialog_ratio)
        + 5.0 * (1 - pov_ratio)
        + 5.0 * (1 - length_penalty)
    )
    old_diversity = (
        10.0 * opening_reward
        + 10.0 * cliff_reward
        + 10.0 * min(1.0, vocab_reward)
    )

    return round(max(0.0, min(100.0, old_penalty + old_diversity)), 2)


async def _process_project(
    session: AsyncSession,
    project: ProjectModel,
    *,
    apply: bool,
) -> ReplayRow | None:
    """Compute old vs new score for one project and optionally persist."""

    # Read the stored snapshot (if any) — this is the "before" point.
    prior_stmt = select(NovelScorecardModel).where(
        NovelScorecardModel.project_id == project.id
    )
    prior = (await session.execute(prior_stmt)).scalar_one_or_none()
    prior_snapshot: dict[str, Any] = {}
    if prior is not None and isinstance(prior.snapshot_json, dict):
        prior_snapshot = prior.snapshot_json

    # Recompute with the current (55/25/20) formula.
    new_scorecard = await compute_scorecard(session, project.id)
    new_score = new_scorecard.quality_score
    total_chapters = new_scorecard.total_chapters

    if total_chapters <= 0:
        return ReplayRow(
            project_slug=project.slug,
            old_score=float(prior.quality_score) if prior is not None else 0.0,
            new_score=0.0,
            delta=0.0,
            dominant_delta_dimension="empty_project",
            notes="no chapters",
        )

    if prior_snapshot:
        old_score = await _old_quality_score(prior_snapshot, total_chapters)
    else:
        # No prior snapshot → approximate "what the old formula would
        # have said" by running the new scorecard numbers through the
        # legacy 70/30 weights. This is a best-effort fallback for
        # projects that never had a scorecard computed at all.
        old_score = await _old_quality_score(
            new_scorecard.to_dict(), total_chapters
        )

    delta = round(new_score - old_score, 2)

    # Ratios/rewards — recomputed from the new scorecard so the
    # dominant-delta heuristic sees consistent inputs.
    miss_ratio = min(
        1.0, new_scorecard.missing_chapters / max(total_chapters, 1)
    )
    block_ratio = min(
        1.0, new_scorecard.chapters_blocked / max(total_chapters, 1)
    )
    cjk_ratio = min(
        1.0, new_scorecard.cjk_leak_chapters / max(total_chapters, 1)
    )
    dialog_ratio = min(
        1.0,
        new_scorecard.dialog_integrity_violations / max(total_chapters, 1),
    )
    pov_ratio = min(
        1.0, new_scorecard.pov_drift_chapters / max(total_chapters, 1)
    )
    length_penalty = min(1.0, new_scorecard.length_cv / 0.30)
    opening_reward = max(
        0.0, min(1.0, new_scorecard.opening_archetype_entropy)
    )
    cliff_reward = max(0.0, min(1.0, new_scorecard.cliffhanger_entropy))
    vocab_reward = max(0.0, 1.0 - (new_scorecard.vocab_hhi / 0.15))

    # Detect "no hype data" — if the project scored 0 chapters with hype
    # assignments, ``compute_scorecard`` substituted the LEGACY_* medians.
    had_hype_data = new_scorecard.hype_missing_chapters < total_chapters

    if had_hype_data:
        hype_entropy_reward = max(
            0.0, min(1.0, new_scorecard.hype_distribution_entropy)
        )
        hype_intensity_reward = max(
            0.0, min(1.0, new_scorecard.hype_intensity_mean / 10.0)
        )
        comedic_reward = max(
            0.0, min(1.0, new_scorecard.comedic_beat_hit_ratio)
        )
        hype_missing_ratio = (
            new_scorecard.hype_missing_chapters / max(total_chapters, 1)
        )
        hype_coverage_reward = max(0.0, 1.0 - min(1.0, hype_missing_ratio))
    else:
        hype_entropy_reward = LEGACY_HYPE_DISTRIBUTION_ENTROPY
        hype_intensity_reward = LEGACY_HYPE_INTENSITY_MEAN / 10.0
        comedic_reward = LEGACY_COMEDIC_BEAT_HIT_RATIO
        hype_coverage_reward = 1.0  # nothing to miss when no chapters scored

    dominant = _dominant_delta_dimension(
        total_chapters=total_chapters,
        miss_ratio=miss_ratio,
        block_ratio=block_ratio,
        cjk_ratio=cjk_ratio,
        dialog_ratio=dialog_ratio,
        pov_ratio=pov_ratio,
        length_penalty=length_penalty,
        opening_reward=opening_reward,
        cliff_reward=cliff_reward,
        vocab_reward=min(1.0, vocab_reward),
        hype_entropy_reward=hype_entropy_reward,
        hype_intensity_reward=hype_intensity_reward,
        comedic_reward=comedic_reward,
        hype_coverage_reward=hype_coverage_reward,
        golden_three_weak=new_scorecard.golden_three_weak,
        had_hype_data=had_hype_data,
    )

    notes_parts: list[str] = [f"chapters={total_chapters}"]
    if not had_hype_data:
        notes_parts.append("no_hype_data")
    if new_scorecard.golden_three_weak:
        notes_parts.append("golden_three_weak")
    notes = ";".join(notes_parts)

    if apply:
        await save_scorecard(session, new_scorecard)

    return ReplayRow(
        project_slug=project.slug,
        old_score=old_score,
        new_score=new_score,
        delta=delta,
        dominant_delta_dimension=dominant,
        notes=notes,
    )


async def run(
    *,
    slug_filter: str | None,
    apply: bool,
    out_path: Path | None,
) -> int:
    settings = load_settings()
    rows: list[ReplayRow] = []

    async with session_scope(settings) as session:
        if slug_filter:
            stmt = select(ProjectModel).where(ProjectModel.slug == slug_filter)
        else:
            stmt = select(ProjectModel).order_by(ProjectModel.slug)
        projects: Sequence[ProjectModel] = (
            (await session.execute(stmt)).scalars().all()
        )

        if not projects:
            print(
                "[replay-scorecard] no projects found"
                + (f" for slug={slug_filter!r}" if slug_filter else ""),
                file=sys.stderr,
            )
            return 2

        print(
            f"[replay-scorecard] scanning {len(projects)} project(s); "
            f"mode={'APPLY' if apply else 'DRY-RUN'}"
        )

        for project in projects:
            try:
                row = await _process_project(
                    session, project, apply=apply
                )
            except Exception as exc:  # pragma: no cover — loud failure
                print(
                    f"[replay-scorecard] {project.slug}: ERROR "
                    f"({type(exc).__name__}: {exc})",
                    file=sys.stderr,
                )
                continue
            if row is None:
                continue
            rows.append(row)
            print(
                f"[replay-scorecard] {row.project_slug}: "
                f"{row.old_score:.2f} → {row.new_score:.2f} "
                f"(Δ {row.delta:+.2f}, dominant={row.dominant_delta_dimension})"
            )

        if not apply:
            # Swallow any incidental mutations (compute_scorecard itself
            # is read-only but SQLAlchemy session_scope commits on exit).
            await session.rollback()

    # Summary.
    flagged = [r for r in rows if abs(r.delta) > _FLAG_DELTA]
    deltas = [r.delta for r in rows]
    mean_delta = (sum(deltas) / len(deltas)) if deltas else 0.0
    max_abs_delta = max((abs(d) for d in deltas), default=0.0)
    flagged_pct = (
        (len(flagged) / len(rows) * 100.0) if rows else 0.0
    )

    print()
    print("[replay-scorecard] summary:")
    print(f"  projects: {len(rows)}")
    print(
        f"  mean Δ: {mean_delta:+.2f}  |  max |Δ|: {max_abs_delta:.2f}"
    )
    print(
        f"  flagged (|Δ| > {_FLAG_DELTA}): {len(flagged)} "
        f"({flagged_pct:.1f}%)"
    )
    if flagged_pct > 10.0:
        print(
            "  ⚠  plan target was < 10%; inspect the flagged rows for "
            "legacy-median artefacts vs. real content gaps",
            file=sys.stderr,
        )

    # CSV output.
    if out_path is not None:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with out_path.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            writer.writerow(
                [
                    "project_slug",
                    "old_score",
                    "new_score",
                    "delta",
                    "dominant_delta_dimension",
                    "notes",
                ]
            )
            for row in rows:
                writer.writerow(row.to_csv_row())
        print(f"[replay-scorecard] wrote CSV → {out_path}")

    return 0


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--project-slug",
        dest="slug",
        default=None,
        help="Restrict to one project slug. Default: all projects.",
    )
    mutex = parser.add_mutually_exclusive_group()
    mutex.add_argument(
        "--dry-run",
        action="store_true",
        help="Recompute scorecards but do not write. Default.",
    )
    mutex.add_argument(
        "--apply",
        action="store_true",
        help="Persist the new scorecard snapshot for each project.",
    )
    parser.add_argument(
        "--out",
        dest="out",
        type=Path,
        default=None,
        help="Write the per-project delta CSV to this path.",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    return asyncio.run(
        run(slug_filter=args.slug, apply=bool(args.apply), out_path=args.out)
    )


if __name__ == "__main__":
    raise SystemExit(main())
