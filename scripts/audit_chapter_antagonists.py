"""Run the chapter-level antagonist audit against live project data.

This script answers the question: "Which already-written chapters
reference an antagonist that does NOT belong to the chapter's volume?"

Background: the original 道种破虚 production run shipped 25 volumes
where volume-2 boss 元婴老者 kept showing up as the present-tense enemy
in volume-7+ chapters because the writer context-packet was leaking
stale fragments. The planner-side fix lives in
``src/bestseller/services/antagonist_lifecycle.py`` (B9b). This script
is the *post-hoc* audit that flags the existing chapters needing
regeneration, and also serves as a pipeline gate (see ``B10d``).

Usage::

    # Audit a single project
    .venv/bin/python -m scripts.audit_chapter_antagonists \
        --project-slug daozhongpoxu-xianxia

    # Audit all projects
    .venv/bin/python -m scripts.audit_chapter_antagonists

    # Machine-readable report for pipelining
    .venv/bin/python -m scripts.audit_chapter_antagonists \
        --project-slug daozhongpoxu-xianxia --format json > audit.json

    # Stricter gate (flag foreign mentions at count >= 2)
    .venv/bin/python -m scripts.audit_chapter_antagonists \
        --project-slug daozhongpoxu-xianxia --salience-threshold 2

Exit code:
  * 0 — no critical findings
  * 1 — one or more critical findings
  * 2 — usage / environment error
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import asdict
from typing import Any, Iterable

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from bestseller.infra.db.models import (
    AntagonistPlanModel,
    ChapterDraftVersionModel,
    ChapterModel,
    ProjectModel,
    VolumeModel,
)
from bestseller.infra.db.session import session_scope
from bestseller.services.chapter_antagonist_audit import (
    DEFAULT_SALIENCE_THRESHOLD,
    ChapterAntagonistReport,
    audit_novel_chapters,
)
from bestseller.settings import load_settings


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------


async def _load_project_slugs() -> list[str]:
    async with session_scope() as session:
        rows = await session.scalars(select(ProjectModel.slug).order_by(ProjectModel.slug))
        return list(rows)


async def _load_antagonist_plans(project_id: Any) -> list[dict[str, Any]]:
    async with session_scope() as session:
        rows = await session.scalars(
            select(AntagonistPlanModel).where(
                AntagonistPlanModel.project_id == project_id
            )
        )
        plans: list[dict[str, Any]] = []
        for r in rows:
            stages = []
            meta = r.metadata_json or {}
            raw_stages = meta.get("stages_of_relevance") or []
            if isinstance(raw_stages, list):
                stages = raw_stages
            plans.append(
                {
                    "name": r.antagonist_label,
                    "scope_volume_number": r.scope_volume_number,
                    "stages_of_relevance": stages,
                    "plan_code": r.plan_code,
                    "status": r.status,
                }
            )
        return plans


async def _load_chapter_texts(
    project_id: Any,
    *,
    frontier_volume: int = 0,
    include_statuses: frozenset[str] | None = None,
) -> tuple[list[dict[str, Any]], int]:
    """Return chapters with (chapter_number, volume_number, text) and
    the total volume count for the project.

    ``frontier_volume`` (optional): if > 0, only chapters whose
    ``volume_number >= frontier_volume`` are returned. Use this to
    restrict the audit to "forward-going" content when the earlier
    volumes are locked as canon.

    ``include_statuses`` (optional): if set, only chapters whose status
    matches one of the given values are returned.
    """
    async with session_scope() as session:
        # volume_number lookup by volume_id
        volumes = list(
            await session.scalars(
                select(VolumeModel).where(VolumeModel.project_id == project_id)
            )
        )
        volume_number_by_id = {v.id: v.volume_number for v in volumes}
        volume_count = max((v.volume_number for v in volumes), default=0)

        chapters = list(
            await session.scalars(
                select(ChapterModel)
                .where(ChapterModel.project_id == project_id)
                .order_by(ChapterModel.chapter_number)
            )
        )
        if not chapters:
            return [], volume_count

        chapter_ids = [c.id for c in chapters]
        drafts = list(
            await session.scalars(
                select(ChapterDraftVersionModel).where(
                    ChapterDraftVersionModel.chapter_id.in_(chapter_ids),
                    ChapterDraftVersionModel.is_current.is_(True),
                )
            )
        )
        draft_by_chapter: dict[Any, str] = {d.chapter_id: d.content_md for d in drafts}

        out: list[dict[str, Any]] = []
        for c in chapters:
            volume_number = volume_number_by_id.get(c.volume_id)
            if volume_number is None and c.volume_id is None:
                # Chapter with no volume link — try metadata fallback
                volume_number = int((c.metadata_json or {}).get("volume_number") or 0)
            volume_number = int(volume_number or 0)
            text = draft_by_chapter.get(c.id, "")
            if not text:
                continue
            if frontier_volume and volume_number < frontier_volume:
                continue
            if include_statuses is not None:
                chapter_status = (getattr(c, "status", "") or "").lower()
                if chapter_status not in include_statuses:
                    continue
            out.append(
                {
                    "chapter_number": c.chapter_number,
                    "volume_number": volume_number,
                    "text": text,
                    "status": getattr(c, "status", ""),
                }
            )
        return out, volume_count


async def _run_for_project(
    *,
    project_slug: str,
    salience_threshold: int,
    language: str,
    frontier_volume: int = 0,
    include_statuses: frozenset[str] | None = None,
) -> ChapterAntagonistReport | None:
    async with session_scope() as session:
        project = await session.scalar(
            select(ProjectModel).where(ProjectModel.slug == project_slug)
        )
        if project is None:
            print(f"[skip] project not found: {project_slug}", file=sys.stderr)
            return None
        project_id = project.id

    antagonist_plans = await _load_antagonist_plans(project_id)
    chapters, volume_count = await _load_chapter_texts(
        project_id,
        frontier_volume=frontier_volume,
        include_statuses=include_statuses,
    )

    if not antagonist_plans:
        print(
            f"[skip] {project_slug}: no antagonist_plans rows — "
            "nothing to audit against.",
            file=sys.stderr,
        )
        return None

    if not chapters:
        print(
            f"[skip] {project_slug}: no written chapters found.",
            file=sys.stderr,
        )
        return None

    report = audit_novel_chapters(
        chapters,
        antagonist_plans,
        volume_count=max(volume_count, 1),
        salience_threshold=salience_threshold,
        language=language,
    )
    return report


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def _render_human(project_slug: str, report: ChapterAntagonistReport) -> str:
    lines = [
        f"── Chapter antagonist audit: {project_slug} ──",
        (
            f"  chapters={report.total_chapters} "
            f"volumes={report.total_volumes} "
            f"antagonists={report.total_antagonists} "
            f"critical={report.critical_count} warning={report.warning_count}"
        ),
    ]
    if not report.findings:
        lines.append("  ✓ no findings")
        return "\n".join(lines)

    for f in report.findings:
        tag = "CRITICAL" if f.severity == "critical" else "WARNING"
        lines.append(
            f"  [{tag}] ch{f.chapter_number:>4d} / vol{f.volume_number:>2d} "
            f"— {f.payload.get('name', '?')} x{f.payload.get('count', 0)}: "
            f"{f.message}"
        )
    if report.critical_chapter_numbers:
        lines.append("")
        lines.append(
            f"  Chapters needing regeneration: "
            f"{list(report.critical_chapter_numbers)}"
        )
    return "\n".join(lines)


def _report_to_jsonable(report: ChapterAntagonistReport) -> dict[str, Any]:
    return {
        "total_chapters": report.total_chapters,
        "total_volumes": report.total_volumes,
        "total_antagonists": report.total_antagonists,
        "critical_count": report.critical_count,
        "warning_count": report.warning_count,
        "critical_chapter_numbers": list(report.critical_chapter_numbers),
        "findings": [asdict(f) for f in report.findings],
        "chapter_audits": [
            {
                "chapter_number": a.chapter_number,
                "volume_number": a.volume_number,
                "expected_antagonists": list(a.expected_antagonists),
                "mentioned_expected": list(a.mentioned_expected),
                "mentioned_out_of_scope": [
                    {"name": n, "count": c} for (n, c) in a.mentioned_out_of_scope
                ],
                "finding_codes": [f.code for f in a.findings],
            }
            for a in report.chapter_audits
            if a.findings
        ],
    }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


async def run_async(
    *,
    project_slug: str | None,
    salience_threshold: int,
    language: str,
    output_format: str,
    frontier_volume: int = 0,
    status_filter: tuple[str, ...] | None = None,
) -> int:
    _ = load_settings()  # fail fast on bad env

    if project_slug:
        slugs = [project_slug]
    else:
        slugs = await _load_project_slugs()

    statuses = frozenset(status_filter) if status_filter else None

    aggregate: dict[str, Any] = {}
    any_critical = False
    for slug in slugs:
        report = await _run_for_project(
            project_slug=slug,
            salience_threshold=salience_threshold,
            language=language,
            frontier_volume=frontier_volume,
            include_statuses=statuses,
        )
        if report is None:
            continue
        if report.is_critical:
            any_critical = True
        if output_format == "json":
            aggregate[slug] = _report_to_jsonable(report)
        else:
            print(_render_human(slug, report))
            print()

    if output_format == "json":
        json.dump(aggregate, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")

    return 1 if any_critical else 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--project-slug",
        help="Project slug (default: audit every project in the DB).",
        default=None,
    )
    parser.add_argument(
        "--salience-threshold",
        type=int,
        default=DEFAULT_SALIENCE_THRESHOLD,
        help=(
            "Foreign-antagonist mention count at which a finding is "
            f"CRITICAL (default: {DEFAULT_SALIENCE_THRESHOLD})."
        ),
    )
    parser.add_argument(
        "--language",
        default="zh-CN",
        help="Language for prompt-block messages (zh-CN | en-US).",
    )
    parser.add_argument(
        "--format",
        choices=("human", "json"),
        default="human",
        help="Output format.",
    )
    parser.add_argument(
        "--frontier-volume",
        type=int,
        default=0,
        help=(
            "If > 0, only audit chapters whose volume_number >= this "
            "value. Use when earlier volumes are locked as canon and "
            "you only want to check forward-going content."
        ),
    )
    parser.add_argument(
        "--status",
        action="append",
        default=[],
        help=(
            "Only include chapters with this status (repeatable). "
            "Typical values: planned, drafting, review, revision, complete. "
            "Default: all statuses."
        ),
    )
    args = parser.parse_args()

    try:
        exit_code = asyncio.run(
            run_async(
                project_slug=args.project_slug,
                salience_threshold=args.salience_threshold,
                language=args.language,
                output_format=args.format,
                frontier_volume=args.frontier_volume,
                status_filter=tuple(args.status) if args.status else None,
            )
        )
    except Exception as exc:  # pragma: no cover - top-level safety net
        print(f"[error] {type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(2)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
