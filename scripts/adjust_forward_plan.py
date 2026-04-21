"""Produce a canon-respecting forward-plan adjustment report per project.

This script answers the user's directive:

    "已经写完的卷和章节先不变。后面的卷和章节做调整。你思考下是否
     可以针对书籍做一下针对性的调整。"

It scans each project's current state, pins the frontier volume (the
earliest volume with unwritten chapters), and runs the
:mod:`bestseller.services.plan_forward_adjustment` rules to emit
forward-only recommendations that never ask the writer to regenerate
already-finished content.

It can also set the ``metadata_json["b10d_frontier_volume"]`` watermark
on the project so downstream review gates (``review_chapter_draft``)
skip canon chapters — see the ``--write-watermark`` flag.

Usage::

    # Report for a single project
    .venv/bin/python -m scripts.adjust_forward_plan \\
        --project-slug daozhongpoxu-xianxia

    # Report for every project
    .venv/bin/python -m scripts.adjust_forward_plan

    # Machine-readable report
    .venv/bin/python -m scripts.adjust_forward_plan \\
        --project-slug daozhongpoxu-xianxia --format json > fwd.json

    # Also persist the frontier watermark so review gates honor it
    .venv/bin/python -m scripts.adjust_forward_plan \\
        --project-slug daozhongpoxu-xianxia --write-watermark

Exit code:
  * 0 — no critical recommendations (or nothing to do)
  * 1 — at least one critical recommendation
  * 2 — usage / environment error
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import asdict
from typing import Any

from sqlalchemy import select

from bestseller.infra.db.models import (
    AntagonistPlanModel,
    ChapterModel,
    ProjectModel,
    VolumeModel,
)
from bestseller.infra.db.session import session_scope
from bestseller.services.plan_forward_adjustment import (
    ForwardPlanReport,
    build_forward_plan_report,
)
from bestseller.settings import load_settings


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------


async def _load_project_slugs() -> list[str]:
    async with session_scope() as session:
        rows = await session.scalars(
            select(ProjectModel.slug).order_by(ProjectModel.slug)
        )
        return list(rows)


async def _load_project(slug: str) -> ProjectModel | None:
    async with session_scope() as session:
        return await session.scalar(
            select(ProjectModel).where(ProjectModel.slug == slug)
        )


async def _load_volumes(project_id: Any) -> list[VolumeModel]:
    async with session_scope() as session:
        rows = await session.scalars(
            select(VolumeModel).where(VolumeModel.project_id == project_id)
        )
        return list(rows)


async def _load_chapters_view(
    project_id: Any,
    volume_number_by_id: dict[Any, int],
) -> list[dict[str, Any]]:
    """Return a lightweight chapter view: volume_number + status."""
    async with session_scope() as session:
        rows = list(
            await session.scalars(
                select(ChapterModel).where(ChapterModel.project_id == project_id)
            )
        )
    out: list[dict[str, Any]] = []
    for c in rows:
        vn = volume_number_by_id.get(c.volume_id)
        if vn is None:
            # fallback: chapter metadata might carry volume_number
            vn = int((c.metadata_json or {}).get("volume_number") or 0)
        out.append(
            {
                "chapter_number": c.chapter_number,
                "volume_number": int(vn or 0),
                "status": getattr(c, "status", "") or "",
            }
        )
    return out


async def _load_antagonist_plans(project_id: Any) -> list[dict[str, Any]]:
    async with session_scope() as session:
        rows = list(
            await session.scalars(
                select(AntagonistPlanModel).where(
                    AntagonistPlanModel.project_id == project_id
                )
            )
        )
    plans: list[dict[str, Any]] = []
    for r in rows:
        meta = r.metadata_json or {}
        plans.append(
            {
                "name": r.antagonist_label,
                "line_role": meta.get("line_role", ""),
                "scope_volume_number": r.scope_volume_number,
                "stages_of_relevance": meta.get("stages_of_relevance") or [],
                "resolution_type": meta.get("resolution_type", ""),
                "plan_code": r.plan_code,
                "status": r.status,
            }
        )
    return plans


async def _write_watermark(project_id: Any, frontier_volume: int) -> None:
    """Persist ``b10d_frontier_volume`` into project.metadata_json."""
    async with session_scope() as session:
        project = await session.scalar(
            select(ProjectModel).where(ProjectModel.id == project_id)
        )
        if project is None:
            return
        metadata = dict(project.metadata_json or {})
        metadata["b10d_frontier_volume"] = int(frontier_volume)
        project.metadata_json = metadata
        await session.commit()


# ---------------------------------------------------------------------------
# Per-project runner
# ---------------------------------------------------------------------------


async def _run_for_project(
    *,
    project_slug: str,
    language: str,
    write_watermark: bool,
) -> ForwardPlanReport | None:
    project = await _load_project(project_slug)
    if project is None:
        print(f"[skip] project not found: {project_slug}", file=sys.stderr)
        return None

    volumes = await _load_volumes(project.id)
    if not volumes:
        print(f"[skip] {project_slug}: no volumes defined.", file=sys.stderr)
        return None

    volume_number_by_id = {v.id: v.volume_number for v in volumes}
    volume_count = max((v.volume_number for v in volumes), default=0)

    chapters_view = await _load_chapters_view(project.id, volume_number_by_id)
    antagonist_plans = await _load_antagonist_plans(project.id)

    report = build_forward_plan_report(
        project_slug=project_slug,
        volume_count=volume_count or 1,
        chapters=chapters_view,
        antagonist_plans=antagonist_plans,
        language=language,
    )

    if write_watermark and report.has_forward_work:
        await _write_watermark(project.id, report.frontier_volume)
        print(
            f"[watermark] {project_slug}: set b10d_frontier_volume="
            f"{report.frontier_volume}",
            file=sys.stderr,
        )

    return report


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def _render_human(project_slug: str, report: ForwardPlanReport) -> str:
    lines = [
        f"── Forward-plan adjustment: {project_slug} ──",
        (
            f"  volumes={report.volume_count}  "
            f"frontier={report.frontier_volume}  "
            f"forward={list(report.forward_volumes)}"
        ),
        (
            f"  fully_written={list(report.fully_written_volumes)}  "
            f"in_progress={list(report.in_progress_volumes)}  "
            f"unwritten={list(report.unwritten_volumes)}"
        ),
        (
            f"  recommendations: critical={report.critical_count} "
            f"warning={report.warning_count} info={report.info_count}"
        ),
    ]
    # Antagonist classification summary
    by_status: dict[str, list[str]] = {}
    for a in report.antagonist_summaries:
        by_status.setdefault(a.status_vs_frontier, []).append(a.name)
    if by_status:
        lines.append("")
        lines.append("  Antagonist status vs. frontier:")
        for status in ("retired", "carries_forward", "fully_forward", "book_wide"):
            names = by_status.get(status, [])
            if names:
                lines.append(
                    f"    {status:>18s}: {', '.join(sorted(names))}"
                )

    # Per-volume coverage
    if report.coverage_by_volume:
        lines.append("")
        lines.append("  Forward-volume coverage (overt / undercurrent / hidden):")
        for cov in report.coverage_by_volume:
            overt = ", ".join(cov.overt_antagonists) or "—"
            uc = ", ".join(cov.undercurrent_antagonists) or "—"
            hd = ", ".join(cov.hidden_antagonists) or "—"
            mark = "✗" if not cov.has_overt else "✓"
            lines.append(
                f"    {mark} vol{cov.volume_number:>2d}: "
                f"overt=[{overt}]  undercurrent=[{uc}]  hidden=[{hd}]"
            )

    # Recommendations
    if report.recommendations:
        lines.append("")
        lines.append("  Recommendations:")
        for rec in report.recommendations:
            tag = rec.severity.upper()
            vol = (
                f"vol{rec.volume_number:>2d} " if rec.volume_number is not None else "       "
            )
            lines.append(f"    [{tag}] {vol}{rec.code}: {rec.message}")
    return "\n".join(lines)


def _report_to_jsonable(report: ForwardPlanReport) -> dict[str, Any]:
    return {
        "project_slug": report.project_slug,
        "volume_count": report.volume_count,
        "frontier_volume": report.frontier_volume,
        "forward_volumes": list(report.forward_volumes),
        "fully_written_volumes": list(report.fully_written_volumes),
        "in_progress_volumes": list(report.in_progress_volumes),
        "unwritten_volumes": list(report.unwritten_volumes),
        "antagonist_summaries": [asdict(a) for a in report.antagonist_summaries],
        "coverage_by_volume": [asdict(c) for c in report.coverage_by_volume],
        "resolution_distribution_forward": dict(report.resolution_distribution_forward),
        "uncovered_forward_volumes": list(report.uncovered_forward_volumes),
        "recommendations": [asdict(r) for r in report.recommendations],
        "critical_count": report.critical_count,
        "warning_count": report.warning_count,
        "info_count": report.info_count,
    }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


async def run_async(
    *,
    project_slug: str | None,
    language: str,
    output_format: str,
    write_watermark: bool,
) -> int:
    _ = load_settings()  # fail fast on bad env

    if project_slug:
        slugs = [project_slug]
    else:
        slugs = await _load_project_slugs()

    aggregate: dict[str, Any] = {}
    any_critical = False
    for slug in slugs:
        report = await _run_for_project(
            project_slug=slug,
            language=language,
            write_watermark=write_watermark,
        )
        if report is None:
            continue
        if report.critical_count > 0:
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
        help="Project slug (default: scan every project in the DB).",
        default=None,
    )
    parser.add_argument(
        "--language",
        default="zh-CN",
        help="Recommendation language (zh-CN | en-US).",
    )
    parser.add_argument(
        "--format",
        choices=("human", "json"),
        default="human",
        help="Output format.",
    )
    parser.add_argument(
        "--write-watermark",
        action="store_true",
        help=(
            "Persist the computed frontier volume as "
            "project.metadata_json['b10d_frontier_volume'] so the "
            "review gate skips canon chapters."
        ),
    )
    args = parser.parse_args()

    try:
        exit_code = asyncio.run(
            run_async(
                project_slug=args.project_slug,
                language=args.language,
                output_format=args.format,
                write_watermark=bool(args.write_watermark),
            )
        )
    except Exception as exc:  # pragma: no cover - top-level safety net
        print(f"[error] {type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(2)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
