"""Maintenance CLI — cleanup of stuck workflows / chapters / tasks.

These commands are designed to be **safe and idempotent**: each one
has a ``--dry-run`` mode (default true on destructive ops) and emits
exactly what it would change before doing anything.

Provided commands:

* ``bestseller maintenance unstuck-blocked``
  Find chapters whose quality-gate block has been resolved (per the
  ``resolved_quality_gate_block`` stamp in metadata_json) but whose
  ``production_state`` was never committed back to ``ok``. Flips them
  to ``ok``. Safe because we only touch chapters where the gate logic
  has already determined they should be unblocked.

* ``bestseller maintenance cancel-stale-workflow-runs``
  Cancel ``workflow_runs`` rows that are stuck in non-terminal states
  (``running`` / ``machine_blocked``) and older than
  the supplied retention threshold. Optionally scoped to specific
  project slugs.

* ``bestseller maintenance cancel-pending-rewrite-tasks``
  Cancel ``rewrite_tasks`` rows in ``pending`` / ``paused`` state, scoped
  to specific project slugs. Used when retiring legacy projects.

* ``bestseller maintenance stuck-summary``
  Read-only diagnostic: prints per-project counts of stuck artifacts
  (no DB writes).

* ``bestseller maintenance retention-repair``
  Reset selected chapters into the retention auto-repair path, run the
  chapter pipeline serially, and print retention gate outcomes.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import logging

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
import typer

from bestseller.domain.enums import ChapterStatus, SceneStatus
from bestseller.infra.db.models import (
    ChapterModel,
    ProjectModel,
    RewriteTaskModel,
    SceneCardModel,
    SceneDraftVersionModel,
    WorkflowRunModel,
)
from bestseller.infra.db.session import session_scope
from bestseller.services.pipelines import run_chapter_pipeline
from bestseller.settings import load_settings

logger = logging.getLogger(__name__)


maintenance_app = typer.Typer(
    help=(
        "Maintenance commands for cleaning up stuck workflows, chapters, "
        "and tasks. All destructive operations default to --dry-run."
    )
)


# ---------------------------------------------------------------------------
# stuck-summary (read-only)
# ---------------------------------------------------------------------------


@maintenance_app.command("stuck-summary")
def stuck_summary(
    slug: str | None = typer.Option(
        None, "--slug", help="Limit summary to a single project slug."
    ),
) -> None:
    """Print per-project counts of stuck artifacts (read-only)."""

    async def _run() -> None:
        settings = load_settings()
        async with session_scope(settings) as session:
            projects = await _select_projects(session, slug=slug)
            if not projects:
                typer.echo("(no projects match)")
                return

            for project in projects:
                stats = await _project_stuck_stats(session, project)
                typer.secho(
                    f"\n── {project.slug} (target={stats['target']}) ──",
                    fg=typer.colors.CYAN,
                )
                typer.echo(
                    f"  chapters in DB:           {stats['total_chapters']}"
                )
                typer.echo(
                    f"  ✓ complete + ok:          {stats['done']}"
                )
                typer.echo(
                    f"  ⚠ production blocked:     {stats['blocked']}"
                )
                typer.echo(
                    f"     ↳ resolved-but-stuck:  {stats['resolved_but_blocked']}"
                )
                typer.echo(
                    f"  ⏳ revision (gate ok):     {stats['revision_ok']}"
                )
                typer.echo(
                    f"  workflow_runs unfinished: {stats['workflows_unfinished']}"
                )
                typer.echo(
                    f"  rewrite_tasks pending:    {stats['rewrite_pending']}"
                )

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# unstuck-blocked (destructive but safe — only releases already-resolved blocks)
# ---------------------------------------------------------------------------


@maintenance_app.command("unstuck-blocked")
def unstuck_blocked(
    slug: str | None = typer.Option(
        None, "--slug", help="Scope to one project. Omit for all projects."
    ),
    dry_run: bool = typer.Option(
        True,
        "--dry-run/--apply",
        help="Default: show what would change. Use --apply to actually update.",
    ),
    limit: int = typer.Option(
        0,
        "--limit",
        help="Cap on chapters touched per project (0 = unlimited).",
    ),
) -> None:
    """Release chapters whose qg block was resolved but production_state was not updated."""

    async def _run() -> None:
        settings = load_settings()
        async with session_scope(settings) as session:
            projects = await _select_projects(session, slug=slug)
            if not projects:
                typer.echo("(no projects match)")
                return

            total_changed = 0
            for project in projects:
                chapters = await _select_resolved_but_blocked_chapters(
                    session, project_id=project.id, limit=limit
                )
                if not chapters:
                    continue

                typer.secho(
                    f"\n── {project.slug}: {len(chapters)} stuck chapters ──",
                    fg=typer.colors.YELLOW,
                )
                for chapter in chapters:
                    resolved_by = (
                        (chapter.metadata_json or {})
                        .get("resolved_quality_gate_block", {})
                        .get("resolved_by", "?")
                    )
                    typer.echo(
                        f"  ch-{chapter.chapter_number:<4} "
                        f"status={chapter.status:<10} "
                        f"production_state={chapter.production_state:<10} "
                        f"resolved_by={resolved_by}"
                    )

                if dry_run:
                    typer.secho(
                        f"  (dry-run) would flip {len(chapters)} chapters to production_state='ok'",
                        fg=typer.colors.BLUE,
                    )
                else:
                    chapter_ids = [c.id for c in chapters]
                    await session.execute(
                        update(ChapterModel)
                        .where(ChapterModel.id.in_(chapter_ids))
                        .values(production_state="ok")
                    )
                    await session.commit()
                    typer.secho(
                        f"  ✓ updated {len(chapters)} chapters",
                        fg=typer.colors.GREEN,
                    )
                total_changed += len(chapters)

            typer.echo(
                f"\nTotal: {total_changed} chapters "
                f"{'would be' if dry_run else 'were'} unblocked."
            )
            if dry_run and total_changed > 0:
                typer.secho(
                    "Re-run with --apply to commit changes.",
                    fg=typer.colors.MAGENTA,
                )

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# cancel-stale-workflow-runs
# ---------------------------------------------------------------------------


@maintenance_app.command("cancel-stale-workflow-runs")
def cancel_stale_workflow_runs(
    slug: list[str] | None = typer.Option(
        None,
        "--slug",
        help="Restrict to listed project slugs (repeatable). Omit for all.",
    ),
    older_than_hours: int = typer.Option(
        24,
        "--older-than-hours",
        help="Only cancel runs idle for more than this many hours.",
    ),
    dry_run: bool = typer.Option(
        True, "--dry-run/--apply", help="Default dry-run."
    ),
) -> None:
    """Mark stuck workflow_runs as cancelled."""

    async def _run() -> None:
        settings = load_settings()
        cutoff = datetime.now(timezone.utc) - timedelta(hours=older_than_hours)
        async with session_scope(settings) as session:
            project_ids = await _project_ids_for(session, slug)
            stuck_states = ("running", "machine_blocked")

            q = select(WorkflowRunModel).where(
                WorkflowRunModel.status.in_(stuck_states),
                WorkflowRunModel.updated_at < cutoff,
            )
            if project_ids:
                q = q.where(WorkflowRunModel.project_id.in_(project_ids))
            rows = (await session.execute(q)).scalars().all()
            if not rows:
                typer.echo("(no stale workflow_runs match)")
                return

            by_proj_status: dict[tuple[str, str], int] = {}
            for r in rows:
                proj_slug = next(
                    (
                        p.slug
                        for p in await _projects_by_ids(session, [r.project_id])
                    ),
                    str(r.project_id),
                )
                key = (proj_slug, r.status)
                by_proj_status[key] = by_proj_status.get(key, 0) + 1

            for (proj_slug, status), cnt in sorted(by_proj_status.items()):
                typer.echo(
                    f"  {proj_slug:<45} {status:<18} {cnt:>6}"
                )

            if dry_run:
                typer.secho(
                    f"\n(dry-run) would cancel {len(rows)} workflow_runs",
                    fg=typer.colors.BLUE,
                )
            else:
                ids = [r.id for r in rows]
                await session.execute(
                    update(WorkflowRunModel)
                    .where(WorkflowRunModel.id.in_(ids))
                    .values(status="cancelled")
                )
                await session.commit()
                typer.secho(
                    f"\n✓ cancelled {len(rows)} workflow_runs",
                    fg=typer.colors.GREEN,
                )

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# cancel-pending-rewrite-tasks
# ---------------------------------------------------------------------------


@maintenance_app.command("cancel-pending-rewrite-tasks")
def cancel_pending_rewrite_tasks(
    slug: list[str] | None = typer.Option(
        None,
        "--slug",
        help="Restrict to listed project slugs (repeatable, required).",
    ),
    dry_run: bool = typer.Option(
        True, "--dry-run/--apply", help="Default dry-run."
    ),
) -> None:
    """Cancel pending/paused rewrite_tasks for the given project slugs."""

    if not slug:
        typer.echo(
            "error: at least one --slug is required (refuses to bulk-cancel ALL projects).",
            err=True,
        )
        raise typer.Exit(code=2)

    async def _run() -> None:
        settings = load_settings()
        async with session_scope(settings) as session:
            project_ids = await _project_ids_for(session, slug)
            if not project_ids:
                typer.echo("(no projects match)")
                return

            q = select(RewriteTaskModel).where(
                RewriteTaskModel.status.in_(("pending", "paused")),
                RewriteTaskModel.project_id.in_(project_ids),
            )
            rows = (await session.execute(q)).scalars().all()
            if not rows:
                typer.echo("(no pending/paused rewrite_tasks)")
                return

            typer.echo(f"  {len(rows)} rewrite_tasks would be cancelled")

            if dry_run:
                typer.secho(
                    f"\n(dry-run) would cancel {len(rows)} rewrite_tasks",
                    fg=typer.colors.BLUE,
                )
            else:
                ids = [r.id for r in rows]
                await session.execute(
                    update(RewriteTaskModel)
                    .where(RewriteTaskModel.id.in_(ids))
                    .values(status="cancelled")
                )
                await session.commit()
                typer.secho(
                    f"\n✓ cancelled {len(rows)} rewrite_tasks",
                    fg=typer.colors.GREEN,
                )

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# retention-repair
# ---------------------------------------------------------------------------


@maintenance_app.command("retention-repair")
def retention_repair(
    slug: str = typer.Option(..., "--slug", help="Project slug to repair."),
    chapter_numbers: list[int] = typer.Option(
        ...,
        "--chapter",
        help="Chapter number to repair. Repeat or pass multiple values.",
    ),
    max_retries: int = typer.Option(
        3,
        "--max-retries",
        min=1,
        help="Retention repair retry budget stamped on each selected chapter.",
    ),
    dry_run: bool = typer.Option(
        True,
        "--dry-run/--apply",
        help="Default: show what would change. Use --apply to run pipeline.",
    ),
) -> None:
    """Reset selected chapters into the retention repair path and rerun them."""

    async def _run() -> None:
        settings = load_settings()
        async with session_scope(settings) as session:
            project = await session.scalar(
                select(ProjectModel).where(ProjectModel.slug == slug)
            )
            if project is None:
                typer.echo(f"error: project '{slug}' was not found", err=True)
                raise typer.Exit(code=2)

            unique_numbers = sorted({int(n) for n in chapter_numbers if int(n) > 0})
            if not unique_numbers:
                typer.echo("error: at least one positive --chapter is required", err=True)
                raise typer.Exit(code=2)

            chapters = await _select_chapters_by_number(
                session,
                project=project,
                chapter_numbers=unique_numbers,
            )
            found_numbers = {c.chapter_number for c in chapters}
            missing = [n for n in unique_numbers if n not in found_numbers]
            if missing:
                typer.echo(f"error: chapter(s) not found: {missing}", err=True)
                raise typer.Exit(code=2)

            typer.secho(
                f"── retention repair: {slug} chapters={unique_numbers} ──",
                fg=typer.colors.CYAN,
            )
            for chapter in chapters:
                typer.echo(
                    f"  ch-{chapter.chapter_number:<4} reset scenes to needs_rewrite "
                    "and stamp retention block codes"
                )

            if dry_run:
                typer.secho(
                    "\n(dry-run) no DB writes or pipeline runs performed",
                    fg=typer.colors.BLUE,
                )
                return

            for chapter in chapters:
                await _stamp_retention_repair_request(
                    session,
                    chapter=chapter,
                    max_retries=max_retries,
                )
            await session.flush()

            passed = 0
            failed = 0
            for chapter in chapters:
                result = await run_chapter_pipeline(
                    session,
                    settings,
                    slug,
                    chapter.chapter_number,
                    requested_by="maintenance:retention-repair",
                )
                await session.refresh(chapter)
                metadata = dict(chapter.metadata_json or {})
                gate_passed = bool(metadata.get("retention_gate_passed"))
                if gate_passed and not result.requires_human_review:
                    passed += 1
                    verdict = "passed"
                else:
                    failed += 1
                    verdict = "failed"
                typer.echo(
                    f"  ch-{chapter.chapter_number:<4} {verdict:<7} "
                    f"human_review={result.requires_human_review} "
                    f"findings={len(metadata.get('retention_gate_last_findings') or [])}"
                )

            await session.commit()
            typer.secho(
                f"\nretention repair complete: passed={passed} failed={failed}",
                fg=typer.colors.GREEN if failed == 0 else typer.colors.YELLOW,
            )

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# internals
# ---------------------------------------------------------------------------


async def _select_projects(
    session: AsyncSession, *, slug: str | None
) -> list[ProjectModel]:
    q = select(ProjectModel)
    if slug:
        q = q.where(ProjectModel.slug == slug)
    return list((await session.execute(q)).scalars().all())


async def _projects_by_ids(
    session: AsyncSession, ids: list[object]
) -> list[ProjectModel]:
    if not ids:
        return []
    q = select(ProjectModel).where(ProjectModel.id.in_(ids))
    return list((await session.execute(q)).scalars().all())


async def _project_ids_for(
    session: AsyncSession, slugs: list[str] | None
) -> list[object]:
    if not slugs:
        return []
    q = select(ProjectModel.id).where(ProjectModel.slug.in_(slugs))
    return list((await session.execute(q)).scalars().all())


async def _select_chapters_by_number(
    session: AsyncSession,
    *,
    project: ProjectModel,
    chapter_numbers: list[int],
) -> list[ChapterModel]:
    if not chapter_numbers:
        return []
    q = (
        select(ChapterModel)
        .where(
            ChapterModel.project_id == project.id,
            ChapterModel.chapter_number.in_(chapter_numbers),
        )
        .order_by(ChapterModel.chapter_number)
    )
    return list((await session.execute(q)).scalars().all())


async def _stamp_retention_repair_request(
    session: AsyncSession,
    *,
    chapter: ChapterModel,
    max_retries: int,
) -> None:
    retention_codes = [
        "HOOK_ECHO_MISSING",
        "SIGNATURE_SCENE_MISSING",
        "EXPOSITION_DUMP",
        "CAST_VIOLATION",
    ]
    metadata = dict(chapter.metadata_json or {})
    metadata["auto_repair_last_block_codes"] = retention_codes
    metadata["retention_repair_requested"] = True
    metadata["retention_repair_max_retries"] = max(1, int(max_retries))
    metadata["retention_retry_count"] = 0
    metadata["retention_gate_passed"] = False
    metadata["production_block_code"] = retention_codes[0]
    chapter.metadata_json = metadata
    chapter.status = ChapterStatus.REVISION.value
    chapter.production_state = "blocked"

    scene_rows = list(
        await session.scalars(
            select(SceneCardModel)
            .where(SceneCardModel.chapter_id == chapter.id)
            .order_by(SceneCardModel.scene_number.asc())
        )
    )
    for scene in scene_rows:
        scene.status = SceneStatus.NEEDS_REWRITE.value
        scene_meta = dict(scene.metadata_json or {})
        scene_meta["auto_repair_block_codes"] = retention_codes
        scene_meta["auto_repair_hint"] = (
            "本章被 maintenance retention-repair 标记为留存自修复。"
            "重写时必须优先兑现上一章钩子、招牌场景、铺垫节制和 cast/正典约束。"
        )
        scene.metadata_json = scene_meta

    if scene_rows:
        await session.execute(
            update(SceneDraftVersionModel)
            .where(
                SceneDraftVersionModel.scene_card_id.in_(
                    [scene.id for scene in scene_rows]
                ),
                SceneDraftVersionModel.is_current.is_(True),
            )
            .values(is_current=False)
        )


async def _select_resolved_but_blocked_chapters(
    session: AsyncSession,
    *,
    project_id: object,
    limit: int = 0,
) -> list[ChapterModel]:
    q = (
        select(ChapterModel)
        .where(
            ChapterModel.project_id == project_id,
            ChapterModel.production_state == "blocked",
            ChapterModel.metadata_json["resolved_quality_gate_block"].is_not(None),
        )
        .order_by(ChapterModel.chapter_number)
    )
    if limit > 0:
        q = q.limit(limit)
    return list((await session.execute(q)).scalars().all())


async def _project_stuck_stats(
    session: AsyncSession, project: ProjectModel
) -> dict[str, int]:
    from sqlalchemy import func

    chapters_q = select(
        func.count(ChapterModel.id).label("total"),
        func.count(ChapterModel.id).filter(
            ChapterModel.status == "complete",
            ChapterModel.production_state == "ok",
        ).label("done"),
        func.count(ChapterModel.id).filter(
            ChapterModel.production_state == "blocked"
        ).label("blocked"),
        func.count(ChapterModel.id).filter(
            ChapterModel.production_state == "blocked",
            ChapterModel.metadata_json["resolved_quality_gate_block"].is_not(None),
        ).label("resolved_but_blocked"),
        func.count(ChapterModel.id).filter(
            ChapterModel.status == "revision",
            ChapterModel.production_state == "ok",
        ).label("revision_ok"),
    ).where(ChapterModel.project_id == project.id)

    row = (await session.execute(chapters_q)).one()

    wf_q = select(func.count(WorkflowRunModel.id)).where(
        WorkflowRunModel.project_id == project.id,
        WorkflowRunModel.status.in_(("running", "machine_blocked", "failed")),
    )
    rt_q = select(func.count(RewriteTaskModel.id)).where(
        RewriteTaskModel.project_id == project.id,
        RewriteTaskModel.status.in_(("pending", "paused")),
    )
    return {
        "target": int(getattr(project, "target_chapters", 0) or 0),
        "total_chapters": int(row.total or 0),
        "done": int(row.done or 0),
        "blocked": int(row.blocked or 0),
        "resolved_but_blocked": int(row.resolved_but_blocked or 0),
        "revision_ok": int(row.revision_ok or 0),
        "workflows_unfinished": int(
            (await session.execute(wf_q)).scalar_one() or 0
        ),
        "rewrite_pending": int(
            (await session.execute(rt_q)).scalar_one() or 0
        ),
    }


__all__ = ["maintenance_app"]
