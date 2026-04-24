"""Scan historical chapters for character lifecycle violations.

Why this exists
---------------
Phase A of the lifecycle fix added three new contradiction checks:
``_check_resurrection`` (dead character reappears), ``_check_stance_flip``
(ally↔enemy flip with no milestone event or within a lock), and
``_check_power_tier_regression`` (power tier drops below historical peak).

Live writing calls those checks before every scene.  Historical chapters
were written before the checks existed, so existing books probably contain
violations that no-one has catalogued.  This script walks every chapter /
scene of every target project, runs the three checks read-only, and emits
a Markdown report under ``artifacts/lifecycle_scan/<slug>.md`` plus a
per-project CSV summary.

**No prose is rewritten.  No DB rows are modified.** The report is the
input the user uses to decide which chapters (if any) warrant targeted
rewrites.

Usage
-----
    # Scan one project
    python scripts/scan_lifecycle_violations.py --project-slug xianxia-upgrade

    # Scan every writing/completed project and dump reports
    python scripts/scan_lifecycle_violations.py --all

    # Emit CSV index in addition to Markdown
    python scripts/scan_lifecycle_violations.py --all --csv /tmp/lifecycle_index.csv

Output
------
* ``artifacts/lifecycle_scan/<slug>.md`` — one human-readable report per
  project.  Groups violations by check type, cites chapter/scene numbers,
  and lists every affected character.
* Optional ``--csv PATH`` — project-level summary row for quick sorting
  (columns: project_slug, chapters_scanned, scenes_scanned, resurrection_violations,
  stance_flip_violations, power_tier_warnings, total_findings).

Safety
------
All checks are read-only.  The script opens a single session per project
and never calls ``session.commit()``.  It is safe to run at any time,
including while workers are writing.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

# Path shim — ``scripts/`` isn't a package.
_THIS = Path(__file__).resolve()
_SRC = _THIS.parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402

from bestseller.infra.db.models import (  # noqa: E402
    ChapterModel,
    ProjectModel,
    SceneCardModel,
)
from bestseller.infra.db.session import session_scope  # noqa: E402
from bestseller.services.contradiction import (  # noqa: E402
    _check_power_tier_regression,
    _check_resurrection,
    _check_stance_flip,
)
from bestseller.settings import get_settings  # noqa: E402

logger = logging.getLogger("scan_lifecycle_violations")

# ChapterModel.status values seen in production: "planned" | "revision" |
# "complete". The historical extras ("written", "reviewed", "finalized",
# "passed", "completed") are kept for forward/backward compatibility in
# case the writer is upgraded to use richer status names later.
SCANNABLE_STATUSES = {
    "written",
    "reviewed",
    "finalized",
    "passed",
    "complete",
    "completed",
    "revision",
}


@dataclass(frozen=True)
class Finding:
    chapter_number: int
    scene_number: int | None
    check_type: str
    severity: str  # "error" | "warning"
    message: str
    recommendation: str = ""


@dataclass
class ProjectScanResult:
    project_slug: str
    chapters_scanned: int = 0
    scenes_scanned: int = 0
    findings: list[Finding] = field(default_factory=list)

    @property
    def resurrection_violations(self) -> int:
        return sum(
            1 for f in self.findings
            if f.check_type == "character_resurrection" and f.severity == "error"
        )

    @property
    def stance_flip_violations(self) -> int:
        stance_types = {"character_stance_flip_locked", "character_stance_flip_unjustified"}
        return sum(
            1 for f in self.findings
            if f.check_type in stance_types and f.severity == "error"
        )

    @property
    def stance_flip_warnings(self) -> int:
        stance_types = {"character_stance_flip_locked", "character_stance_flip_unjustified"}
        return sum(
            1 for f in self.findings
            if f.check_type in stance_types and f.severity == "warning"
        )

    @property
    def power_tier_warnings(self) -> int:
        return sum(
            1 for f in self.findings
            if f.check_type == "character_power_tier_regression"
        )


async def _load_scannable_chapters(
    session: AsyncSession, project_id: object
) -> list[ChapterModel]:
    stmt = (
        select(ChapterModel)
        .where(
            ChapterModel.project_id == project_id,
            ChapterModel.status.in_(SCANNABLE_STATUSES),
        )
        .order_by(ChapterModel.chapter_number.asc())
    )
    return list((await session.execute(stmt)).scalars())


async def _load_scenes_for_chapter(
    session: AsyncSession, chapter_id: object
) -> list[SceneCardModel]:
    stmt = (
        select(SceneCardModel)
        .where(SceneCardModel.chapter_id == chapter_id)
        .order_by(SceneCardModel.scene_number.asc())
    )
    return list((await session.execute(stmt)).scalars())


def _extract_participants(scene: SceneCardModel) -> list[str]:
    """Robustly extract character names from ``SceneCardModel.participants``.

    The column is a JSONB list that historically held either scalar strings
    or ``{"name": ...}`` objects depending on the planner version.  We
    flatten both forms and drop empties."""
    raw = scene.participants or []
    out: list[str] = []
    for item in raw:
        if isinstance(item, str):
            name = item.strip()
            if name:
                out.append(name)
        elif isinstance(item, dict):
            for key in ("name", "character", "role"):
                val = item.get(key)
                if isinstance(val, str) and val.strip():
                    out.append(val.strip())
                    break
    return out


async def _scan_project(
    session: AsyncSession, project: ProjectModel
) -> ProjectScanResult:
    result = ProjectScanResult(project_slug=project.slug)

    chapters = await _load_scannable_chapters(session, project.id)
    for chapter in chapters:
        result.chapters_scanned += 1
        scenes = await _load_scenes_for_chapter(session, chapter.id)
        for scene in scenes:
            result.scenes_scanned += 1
            participants = _extract_participants(scene)

            # 1. Resurrection (hard error)
            try:
                v, w = await _check_resurrection(
                    session,
                    project.id,
                    chapter.chapter_number,
                    participants,
                    language=project.language,
                )
                for item in v:
                    result.findings.append(
                        Finding(
                            chapter_number=chapter.chapter_number,
                            scene_number=scene.scene_number,
                            check_type=item.check_type,
                            severity=item.severity,
                            message=item.message,
                        )
                    )
                for item in w:
                    result.findings.append(
                        Finding(
                            chapter_number=chapter.chapter_number,
                            scene_number=scene.scene_number,
                            check_type=item.check_type,
                            severity="warning",
                            message=item.message,
                            recommendation=item.recommendation,
                        )
                    )
            except Exception:
                logger.exception(
                    "resurrection check crashed for %s ch=%d sc=%d",
                    project.slug, chapter.chapter_number, scene.scene_number,
                )

            # 2. Stance flip (hard error when locked / unsupported)
            try:
                v, w = await _check_stance_flip(
                    session,
                    project.id,
                    chapter.chapter_number,
                    participants,
                    language=project.language,
                )
                for item in v:
                    result.findings.append(
                        Finding(
                            chapter_number=chapter.chapter_number,
                            scene_number=scene.scene_number,
                            check_type=item.check_type,
                            severity=item.severity,
                            message=item.message,
                        )
                    )
                for item in w:
                    result.findings.append(
                        Finding(
                            chapter_number=chapter.chapter_number,
                            scene_number=scene.scene_number,
                            check_type=item.check_type,
                            severity="warning",
                            message=item.message,
                            recommendation=item.recommendation,
                        )
                    )
            except Exception:
                logger.exception(
                    "stance_flip check crashed for %s ch=%d sc=%d",
                    project.slug, chapter.chapter_number, scene.scene_number,
                )

            # 3. Power tier regression (soft warning)
            try:
                v, w = await _check_power_tier_regression(
                    session,
                    project.id,
                    chapter.chapter_number,
                    participants,
                    language=project.language,
                )
                for item in v:
                    result.findings.append(
                        Finding(
                            chapter_number=chapter.chapter_number,
                            scene_number=scene.scene_number,
                            check_type=item.check_type,
                            severity=item.severity,
                            message=item.message,
                        )
                    )
                for item in w:
                    result.findings.append(
                        Finding(
                            chapter_number=chapter.chapter_number,
                            scene_number=scene.scene_number,
                            check_type=item.check_type,
                            severity="warning",
                            message=item.message,
                            recommendation=item.recommendation,
                        )
                    )
            except Exception:
                logger.exception(
                    "power_tier check crashed for %s ch=%d sc=%d",
                    project.slug, chapter.chapter_number, scene.scene_number,
                )

    return result


def _render_markdown(result: ProjectScanResult) -> str:
    lines: list[str] = []
    lines.append(f"# 角色生命周期一致性扫描 — {result.project_slug}")
    lines.append("")
    lines.append(f"- 扫描章节数: **{result.chapters_scanned}**")
    lines.append(f"- 扫描场景数: **{result.scenes_scanned}**")
    lines.append(f"- 死而复活硬性违规: **{result.resurrection_violations}**")
    lines.append(f"- 立场翻转硬性违规: **{result.stance_flip_violations}**")
    lines.append(f"- 立场翻转软告警: **{result.stance_flip_warnings}**")
    lines.append(f"- 实力倒退告警: **{result.power_tier_warnings}**")
    lines.append(f"- 总条目数: **{len(result.findings)}**")
    lines.append("")

    if not result.findings:
        lines.append("✅ 未发现违规。历史章节的角色生命周期与当前数据库状态一致。")
        lines.append("")
        return "\n".join(lines)

    grouped: dict[str, list[Finding]] = {}
    for f in result.findings:
        grouped.setdefault(f.check_type, []).append(f)

    order = [
        "character_resurrection",
        "character_stance_flip_locked",
        "character_stance_flip_unjustified",
        "character_power_tier_regression",
    ]
    remaining = [k for k in grouped if k not in order]
    for key in [*order, *remaining]:
        items = grouped.get(key)
        if not items:
            continue
        lines.append(f"## {key} ({len(items)} 条)")
        lines.append("")
        # Sort by chapter then scene
        items.sort(key=lambda f: (f.chapter_number, f.scene_number or 0))
        for f in items:
            sc = f"第{f.chapter_number}章" + (f" · 场景{f.scene_number}" if f.scene_number else "")
            sev = "❌" if f.severity == "error" else "⚠️"
            lines.append(f"- {sev} **{sc}** — {f.message}")
            if f.recommendation:
                lines.append(f"  - 建议: {f.recommendation}")
        lines.append("")
    return "\n".join(lines)


async def _select_projects(
    session: AsyncSession,
    *,
    slug: str | None,
    include_all: bool,
) -> list[ProjectModel]:
    if slug:
        stmt = select(ProjectModel).where(ProjectModel.slug == slug)
    elif include_all:
        stmt = select(ProjectModel).order_by(ProjectModel.created_at.asc())
    else:
        stmt = (
            select(ProjectModel)
            .where(ProjectModel.status.in_(("writing", "completed")))
            .order_by(ProjectModel.created_at.asc())
        )
    return list((await session.execute(stmt)).scalars())


async def _run(
    slug_filter: str | None,
    include_all: bool,
    out_dir: Path,
    csv_path: Path | None,
) -> int:
    settings = get_settings()
    out_dir.mkdir(parents=True, exist_ok=True)

    async with session_scope(settings) as session:
        projects = await _select_projects(session, slug=slug_filter, include_all=include_all)

        if not projects:
            logger.error("no matching projects; nothing to do")
            return 1

        summary_rows: list[tuple] = []
        for project in projects:
            logger.info("scanning project=%s", project.slug)
            try:
                result = await _scan_project(session, project)
            except Exception:
                logger.exception("scan failed for %s — continuing", project.slug)
                continue

            md_path = out_dir / f"{project.slug}.md"
            md_path.write_text(_render_markdown(result), encoding="utf-8")
            logger.info(
                "  → chapters=%d scenes=%d findings=%d (report=%s)",
                result.chapters_scanned, result.scenes_scanned, len(result.findings), md_path,
            )
            summary_rows.append(
                (
                    project.slug,
                    result.chapters_scanned,
                    result.scenes_scanned,
                    result.resurrection_violations,
                    result.stance_flip_violations,
                    result.power_tier_warnings,
                    len(result.findings),
                )
            )

    _emit_csv(summary_rows, csv_path)
    _emit_stdout_summary(summary_rows, out_dir)
    return 0


def _emit_csv(rows: Iterable[tuple], csv_path: Path | None) -> None:
    if csv_path is None:
        return
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(
            [
                "project_slug",
                "chapters_scanned",
                "scenes_scanned",
                "resurrection_violations",
                "stance_flip_violations",
                "power_tier_warnings",
                "total_findings",
            ]
        )
        for row in rows:
            writer.writerow(row)
    logger.info("csv index written to %s", csv_path)


def _emit_stdout_summary(rows: list[tuple], out_dir: Path) -> None:
    print("\n[lifecycle-scan] summary:")
    header = f"  {'slug':35s} {'chs':>5s} {'scns':>5s} {'res_err':>8s} {'stance_err':>11s} {'ptier_warn':>11s} {'total':>6s}"
    print(header)
    print("  " + "-" * (len(header) - 2))
    for r in rows:
        print(f"  {r[0]:35s} {r[1]:5d} {r[2]:5d} {r[3]:8d} {r[4]:11d} {r[5]:11d} {r[6]:6d}")
    print(f"\n  reports → {out_dir}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--project-slug", help="scan a single project")
    parser.add_argument("--all", action="store_true", help="include every project regardless of status")
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=Path("artifacts/lifecycle_scan"),
        help="directory for per-project Markdown reports",
    )
    parser.add_argument("--csv", type=Path, default=None, help="optional CSV index path")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    return asyncio.run(
        _run(
            slug_filter=args.project_slug,
            include_all=args.all,
            out_dir=args.out_dir,
            csv_path=args.csv,
        )
    )


if __name__ == "__main__":
    sys.exit(main())
