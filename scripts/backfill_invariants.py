"""Backfill ``projects.invariants_json`` for existing novels.

Why this exists
---------------
The L1 invariants contract is only seeded by ``run_project_pipeline`` —
meaning any project created *before* that hook was added still has
``invariants_json = NULL``. That makes ``_finalize_chapter_quality_gate``
return early (drafts.py:120) so **no** L4/L5/L6 gate runs on new chapters
for those projects. Existing novels therefore keep generating on the old
path even though the framework is built.

This script closes that gap: for each project, it derives a sensible
``ProjectInvariants`` from (language, current drafts' POV, style_guide
tense) and persists it. Subsequent chapter generations will then take
the full L4 → L4.5 regen → L5 → L6 gate path.

Usage
-----
    # dry-run across all projects (no DB writes)
    python scripts/backfill_invariants.py --dry-run

    # dry-run for one project
    python scripts/backfill_invariants.py --dry-run --project-slug romantasy-1776330993

    # apply (writes invariants_json and commits)
    python scripts/backfill_invariants.py --apply
    python scripts/backfill_invariants.py --apply --project-slug romantasy-1776330993

Safety
------
* Idempotent: if ``invariants_json`` is already present and deserializes
  cleanly, we skip. Pass ``--force`` to reseed anyway.
* Read-only in dry-run mode (default).
* Prints a per-project summary showing inferred language / POV / tense
  / length envelope so you can sanity-check before applying.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from typing import Sequence

# Path shim — ``scripts/`` isn't a package, so importing from
# ``bestseller.*`` requires that ``src/`` be on the path when the script
# is invoked without the package being installed editable. In this repo
# it IS installed editable, but we keep the shim for parity with the
# other scripts/ helpers.
_THIS = Path(__file__).resolve()
_SRC = _THIS.parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402
from sqlalchemy.orm import selectinload  # noqa: E402

from bestseller.infra.db.models import (  # noqa: E402
    ChapterDraftVersionModel,
    ChapterModel,
    ProjectModel,
)
from bestseller.infra.db.session import session_scope  # noqa: E402
from bestseller.services.invariants import (  # noqa: E402
    InvariantSeedError,
    LengthEnvelope,
    ProjectInvariants,
    infer_pov_from_sample,
    invariants_from_dict,
    invariants_to_dict,
    seed_invariants,
)
from bestseller.settings import load_settings  # noqa: E402


# Number of recent-ordered chapters to sample when inferring POV.
# 3 is enough to swamp outlier chapters (e.g. ch-1 might be stylized
# in a way the main book isn't).
_POV_SAMPLE_CHAPTERS = 3

# Minimum number of completed chapters required before we trust the
# empirical length distribution. Below this we fall back to the
# settings-derived envelope.
_LENGTH_MIN_SAMPLE = 10

# Envelope widening factor — a ±X% tolerance around the p10/p90 band so
# legitimate variation doesn't trip LENGTH_UNDER/LENGTH_OVER on every
# new chapter. 15% is tight enough to still catch a truly broken
# 201-char chapter while tolerating normal prose rhythm.
_LENGTH_TOLERANCE = 0.15


async def _load_pov_sample(
    session: AsyncSession, project_id, *, limit: int = _POV_SAMPLE_CHAPTERS
) -> str:
    """Concatenate the first ``limit`` current chapter drafts' content."""

    stmt = (
        select(ChapterDraftVersionModel.content_md)
        .join(ChapterModel, ChapterModel.id == ChapterDraftVersionModel.chapter_id)
        .where(
            ChapterModel.project_id == project_id,
            ChapterDraftVersionModel.is_current.is_(True),
        )
        .order_by(ChapterModel.chapter_number.asc())
        .limit(limit)
    )
    rows = (await session.execute(stmt)).scalars().all()
    # Keep generous whitespace so the regexes in infer_pov_from_sample
    # don't accidentally merge two chapters into one token.
    return "\n\n".join(r for r in rows if r)


async def _load_chapter_lengths(
    session: AsyncSession, project_id
) -> list[int]:
    """Return a sorted list of char counts for every current chapter draft."""

    stmt = (
        select(ChapterDraftVersionModel.content_md)
        .join(ChapterModel, ChapterModel.id == ChapterDraftVersionModel.chapter_id)
        .where(
            ChapterModel.project_id == project_id,
            ChapterDraftVersionModel.is_current.is_(True),
        )
    )
    rows = (await session.execute(stmt)).scalars().all()
    return sorted(len(r) for r in rows if r)


def _empirical_length_envelope(
    lengths: list[int],
) -> LengthEnvelope | None:
    """Build a LengthEnvelope from the p10 / p50 / p90 of observed lengths.

    Returns ``None`` when the sample is too small to be representative —
    the caller then falls back to the settings-derived envelope (which
    uses the global defaults). We widen p10/p90 by ``_LENGTH_TOLERANCE``
    so the gate doesn't reject every borderline chapter.
    """

    if len(lengths) < _LENGTH_MIN_SAMPLE:
        return None

    def _percentile(xs: list[int], pct: float) -> int:
        # xs is already sorted. Simple linear interpolation.
        if not xs:
            return 0
        k = (len(xs) - 1) * pct
        f = int(k)
        c = min(f + 1, len(xs) - 1)
        if f == c:
            return xs[f]
        return int(xs[f] + (xs[c] - xs[f]) * (k - f))

    p10 = _percentile(lengths, 0.10)
    p50 = _percentile(lengths, 0.50)
    p90 = _percentile(lengths, 0.90)

    min_chars = max(1, int(p10 * (1 - _LENGTH_TOLERANCE)))
    max_chars = int(p90 * (1 + _LENGTH_TOLERANCE))
    target_chars = max(min_chars + 1, min(p50, max_chars - 1))

    # LengthEnvelope.__post_init__ enforces strict <; nudge if the
    # percentile band is degenerate (e.g. every chapter is identical).
    if not min_chars < target_chars < max_chars:
        target_chars = min_chars + max(1, (max_chars - min_chars) // 2)
        if target_chars <= min_chars:
            target_chars = min_chars + 1
        if max_chars <= target_chars:
            max_chars = target_chars + 1

    return LengthEnvelope(
        min_chars=min_chars,
        target_chars=target_chars,
        max_chars=max_chars,
    )


def _normalize_pov(raw: str | None) -> str | None:
    """Return one of {'first','close_third','omniscient'} or None.

    Style guides in the wild contain strings like "first-person limited",
    "close third (limited)", "omniscient narrator". We do a conservative
    lower-case substring match; anything ambiguous returns None so the
    caller falls back to evidence-based inference.
    """

    if not raw:
        return None
    s = raw.strip().lower()
    if "first" in s:
        return "first"
    if "omnisc" in s:
        return "omniscient"
    if "third" in s:
        return "close_third"
    return None


def _normalize_tense(raw: str | None) -> str:
    if raw and "present" in raw.lower():
        return "present"
    return "past"


async def _derive_invariants(
    session: AsyncSession,
    project: ProjectModel,
    settings,
) -> tuple[ProjectInvariants, dict[str, str]]:
    """Produce a ``ProjectInvariants`` for ``project`` + a diagnostics dict."""

    style_guide = getattr(project, "style_guide", None)

    # 1) Tense — style_guide is authoritative; fall back to "past".
    tense = _normalize_tense(getattr(style_guide, "tense", None))

    # 2) POV — prefer style_guide ONLY when it parses cleanly into one of
    #    the three supported literals. Otherwise infer from the first 3
    #    current chapters' text. Dialogue is stripped inside
    #    ``infer_pov_from_sample``; don't reimplement here.
    pov_from_guide = _normalize_pov(getattr(style_guide, "pov_type", None))
    if pov_from_guide:
        pov = pov_from_guide
        pov_source = f"style_guide:{getattr(style_guide, 'pov_type', '')!r}"
    else:
        sample = await _load_pov_sample(session, project.id)
        if not sample.strip():
            pov = "close_third"
            pov_source = "default (no sample available)"
        else:
            pov = infer_pov_from_sample(sample, project.language)
            pov_source = f"infer_pov_from_sample({len(sample)} chars)"

    # 3) Length envelope — prefer the empirical p10/p50/p90 band of
    #    existing chapters over the settings-derived default. Books in
    #    the wild consistently write to a per-project target (e.g.
    #    ~17K-char English chapters) that rarely matches the global
    #    fallback. When the sample is too small we fall back to
    #    seed_invariants' default derivation.
    lengths = await _load_chapter_lengths(session, project.id)
    empirical = _empirical_length_envelope(lengths)
    overrides: dict = {}
    if empirical is not None:
        overrides["length_envelope"] = empirical
        length_source = (
            f"empirical p10/p50/p90 over {len(lengths)} chapters "
            f"(raw band: "
            f"{lengths[int((len(lengths)-1)*0.10)]}"
            f"/{lengths[int((len(lengths)-1)*0.50)]}"
            f"/{lengths[int((len(lengths)-1)*0.90)]})"
        )
    else:
        length_source = (
            f"settings default (only {len(lengths)} chapters, need >= "
            f"{_LENGTH_MIN_SAMPLE})"
        )

    invariants = seed_invariants(
        project_id=project.id,
        language=project.language,
        words_per_chapter=settings.generation.words_per_chapter,
        pov=pov,
        tense=tense,
        overrides=overrides,
    )

    diagnostics = {
        "slug": project.slug,
        "language": invariants.language,
        "pov": invariants.pov,
        "pov_source": pov_source,
        "tense": invariants.tense,
        "length_envelope": (
            f"[{invariants.length_envelope.min_chars}, "
            f"{invariants.length_envelope.target_chars}, "
            f"{invariants.length_envelope.max_chars}]"
        ),
        "length_source": length_source,
    }
    return invariants, diagnostics


async def _process_project(
    session: AsyncSession,
    project: ProjectModel,
    *,
    settings,
    apply: bool,
    force: bool,
) -> str:
    """Return a one-line status string for the summary log."""

    if project.invariants_json and not force:
        try:
            invariants_from_dict(project.invariants_json)
        except InvariantSeedError:
            print(
                f"[backfill] {project.slug}: existing payload is INVALID — "
                f"will reseed"
            )
        else:
            return f"{project.slug}: SKIP (already seeded)"

    invariants, diag = await _derive_invariants(session, project, settings)

    print(
        f"[backfill] {project.slug}: language={diag['language']} "
        f"pov={diag['pov']} (source: {diag['pov_source']}) "
        f"tense={diag['tense']} "
        f"length_envelope={diag['length_envelope']} "
        f"(source: {diag['length_source']})"
    )

    if not apply:
        return f"{project.slug}: DRY-RUN (would seed)"

    project.invariants_json = invariants_to_dict(invariants)
    # Commit early per-project so a later failure doesn't lose prior
    # project's seed. session_scope will commit again at context exit;
    # an explicit flush here keeps the transaction small.
    await session.flush()
    return f"{project.slug}: APPLIED"


async def run(
    *,
    slug_filter: str | None,
    apply: bool,
    force: bool,
) -> int:
    settings = load_settings()

    async with session_scope(settings) as session:
        # Eagerly load ``style_guide`` — async SQLAlchemy forbids
        # implicit lazy-load, and ``_derive_invariants`` needs the
        # relationship to pick up pov_type / tense hints.
        if slug_filter:
            stmt = (
                select(ProjectModel)
                .where(ProjectModel.slug == slug_filter)
                .options(selectinload(ProjectModel.style_guide))
            )
            project = (await session.execute(stmt)).scalar_one_or_none()
            if project is None:
                print(
                    f"[backfill] project '{slug_filter}' not found",
                    file=sys.stderr,
                )
                return 2
            projects: Sequence[ProjectModel] = [project]
        else:
            stmt = (
                select(ProjectModel)
                .order_by(ProjectModel.slug)
                .options(selectinload(ProjectModel.style_guide))
            )
            projects = (await session.execute(stmt)).scalars().all()

        print(
            f"[backfill] scanning {len(projects)} project(s); "
            f"mode={'APPLY' if apply else 'DRY-RUN'} "
            f"force={force}"
        )
        summary: list[str] = []
        for project in projects:
            try:
                status = await _process_project(
                    session,
                    project,
                    settings=settings,
                    apply=apply,
                    force=force,
                )
            except Exception as exc:  # pragma: no cover — loud failure
                status = f"{project.slug}: ERROR ({type(exc).__name__}: {exc})"
                print(f"[backfill] {status}", file=sys.stderr)
            summary.append(status)

        # session_scope commits on context exit when apply=True. For
        # dry-runs we rollback by not flushing any mutations (we never
        # touched project.invariants_json in that branch).

    print("\n[backfill] summary:")
    for line in summary:
        print(f"  - {line}")
    return 0


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
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
        help="Print what would be seeded but do not write. Default.",
    )
    mutex.add_argument(
        "--apply",
        action="store_true",
        help="Persist invariants_json to the database.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Reseed even if invariants_json is already present and valid.",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    apply = bool(args.apply)
    return asyncio.run(
        run(slug_filter=args.slug, apply=apply, force=bool(args.force))
    )


if __name__ == "__main__":
    raise SystemExit(main())
