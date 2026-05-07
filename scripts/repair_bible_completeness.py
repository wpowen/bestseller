"""Repair L2 Bible completeness gaps on existing projects.

The live planner now emits and repairs the required BookSpec/CastSpec fields,
but historical projects may still have thin artifacts already persisted in
Postgres. This script upgrades the latest live artifacts and re-materializes the
affected rows instead of merely marking deficiencies as ignored.

Usage:
    uv run python scripts/repair_bible_completeness.py --all --apply
    uv run python scripts/repair_bible_completeness.py --project-slug xianxia-upgrade --apply
    uv run python scripts/repair_bible_completeness.py --all
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from pathlib import Path
import sys
from typing import Any

from sqlalchemy import select

_THIS = Path(__file__).resolve()
_SRC = _THIS.parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from bestseller.domain.enums import ArtifactType, WorkflowStatus  # noqa: E402
from bestseller.domain.planning import PlanningArtifactCreate  # noqa: E402
from bestseller.infra.db.models import ProjectModel  # noqa: E402
from bestseller.infra.db.session import create_session_factory  # noqa: E402
from bestseller.services.bible_gate import (  # noqa: E402
    build_draft_from_materialization_content,
    validate_bible_completeness,
)
from bestseller.services.invariants import invariants_from_dict, seed_invariants  # noqa: E402
from bestseller.services.planner import (  # noqa: E402
    _CAST_PERSONHOOD_REPAIR_CODES,
    _ensure_book_spec_bible_fields,
    _repair_cast_personhood_if_needed,
    _synthesize_missing_cast_bible_fields,
)
from bestseller.services.projects import import_planning_artifact  # noqa: E402
from bestseller.services.story_bible import apply_book_spec, upsert_cast_spec  # noqa: E402
from bestseller.services.workflows import (  # noqa: E402
    create_workflow_run,
    get_latest_planning_artifact,
)
from bestseller.settings import load_settings  # noqa: E402


@dataclass(frozen=True)
class RepairRow:
    slug: str
    before_codes: dict[str, int]
    book_repaired: bool
    cast_repaired: bool
    after_codes: dict[str, int]
    notes: str = ""


def _count_codes(report: Any) -> dict[str, int]:
    counts: dict[str, int] = {}
    for deficiency in report.deficiencies:
        counts[deficiency.code] = counts.get(deficiency.code, 0) + 1
    return counts


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


async def _latest_content(
    session: Any,
    project: ProjectModel,
    artifact_type: ArtifactType,
) -> dict[str, Any] | None:
    artifact = await get_latest_planning_artifact(
        session,
        project_id=project.id,
        artifact_type=artifact_type,
    )
    if artifact is None:
        return None
    return _mapping(artifact.content)


async def _latest_premise(session: Any, project: ProjectModel, book_spec: dict[str, Any]) -> str:
    premise_artifact = await get_latest_planning_artifact(
        session,
        project_id=project.id,
        artifact_type=ArtifactType.PREMISE,
    )
    premise_content = _mapping(premise_artifact.content if premise_artifact else None)
    raw_premise = premise_content.get("premise")
    if isinstance(raw_premise, str) and raw_premise.strip():
        return raw_premise.strip()
    raw_logline = book_spec.get("logline")
    if isinstance(raw_logline, str) and raw_logline.strip():
        return raw_logline.strip()
    return project.title


def _validate_project_bible(
    settings: Any,
    project: ProjectModel,
    *,
    book_spec: dict[str, Any],
    world_spec: dict[str, Any],
    cast_spec: dict[str, Any],
) -> Any:
    if project.invariants_json:
        invariants = invariants_from_dict(project.invariants_json)
    else:
        invariants = seed_invariants(
            project_id=project.id,
            language=project.language,
            words_per_chapter=settings.generation.words_per_chapter,
        )
    draft = build_draft_from_materialization_content(
        book_spec_content=book_spec,
        world_spec_content=world_spec,
        cast_spec_content=cast_spec,
    )
    return validate_bible_completeness(draft, invariants)


async def _repair_one_project(
    session: Any,
    settings: Any,
    project: ProjectModel,
    *,
    apply: bool,
    skip_llm: bool,
) -> RepairRow:
    book_spec = await _latest_content(session, project, ArtifactType.BOOK_SPEC)
    world_spec = await _latest_content(session, project, ArtifactType.WORLD_SPEC)
    cast_spec = await _latest_content(session, project, ArtifactType.CAST_SPEC)
    if not book_spec or not world_spec or not cast_spec:
        return RepairRow(
            slug=project.slug,
            before_codes={},
            book_repaired=False,
            cast_repaired=False,
            after_codes={},
            notes="missing required planning artifacts",
        )

    premise = await _latest_premise(session, project, book_spec)
    before_report = _validate_project_bible(
        settings,
        project,
        book_spec=book_spec,
        world_spec=world_spec,
        cast_spec=cast_spec,
    )
    before_codes = _count_codes(before_report)
    book_repaired = False
    cast_repaired = False

    normalized_book = _ensure_book_spec_bible_fields(project, premise, book_spec)
    if normalized_book != book_spec:
        book_repaired = True
        book_spec = normalized_book
        if apply:
            await import_planning_artifact(
                session,
                project.slug,
                PlanningArtifactCreate(
                    artifact_type=ArtifactType.BOOK_SPEC,
                    content=book_spec,
                    notes="L2 Bible completeness repair: project signature/naming pool",
                ),
            )
            await apply_book_spec(session, project, book_spec)

    actionable_cast_codes = set(before_codes).intersection(_CAST_PERSONHOOD_REPAIR_CODES)
    if apply and actionable_cast_codes and not skip_llm:
        workflow_run = await create_workflow_run(
            session,
            project_id=project.id,
            workflow_type="repair_bible_completeness",
            status=WorkflowStatus.RUNNING,
            scope_type="project",
            scope_id=project.id,
            requested_by="repair_bible_completeness",
            current_step="repair_cast_spec",
            metadata={
                "project_slug": project.slug,
                "before_codes": before_codes,
            },
        )
        try:
            repaired_cast, llm_run_id = await _repair_cast_personhood_if_needed(
                session=session,
                settings=settings,
                project=project,
                book_spec_payload=book_spec,
                world_spec_payload=world_spec,
                cast_spec_payload=cast_spec,
                workflow_run_id=workflow_run.id,
            )
            if llm_run_id is not None and repaired_cast != cast_spec:
                cast_spec = repaired_cast
                cast_repaired = True
                await import_planning_artifact(
                    session,
                    project.slug,
                    PlanningArtifactCreate(
                        artifact_type=ArtifactType.CAST_SPEC,
                        content=cast_spec,
                        notes="L2 Bible completeness repair: character personhood/IP/motives",
                    ),
                )
                await upsert_cast_spec(session, project, cast_spec)
            workflow_run.status = WorkflowStatus.COMPLETED.value
            workflow_run.current_step = "completed"
        except Exception as exc:  # noqa: BLE001
            workflow_run.status = WorkflowStatus.FAILED.value
            workflow_run.error_message = f"{type(exc).__name__}: {exc}"[:1000]
            raise

    current_report = _validate_project_bible(
        settings,
        project,
        book_spec=book_spec,
        world_spec=world_spec,
        cast_spec=cast_spec,
    )
    current_codes = _count_codes(current_report)
    if apply and set(current_codes).intersection(_CAST_PERSONHOOD_REPAIR_CODES):
        synthesized_cast = _synthesize_missing_cast_bible_fields(project, cast_spec)
        synthesized_report = _validate_project_bible(
            settings,
            project,
            book_spec=book_spec,
            world_spec=world_spec,
            cast_spec=synthesized_cast,
        )
        if len(synthesized_report.deficiencies) < len(current_report.deficiencies):
            cast_spec = synthesized_cast
            cast_repaired = True
            await import_planning_artifact(
                session,
                project.slug,
                PlanningArtifactCreate(
                    artifact_type=ArtifactType.CAST_SPEC,
                    content=cast_spec,
                    notes="L2 Bible completeness repair: deterministic character-bible synthesis",
                ),
            )
            await upsert_cast_spec(session, project, cast_spec)

    after_report = _validate_project_bible(
        settings,
        project,
        book_spec=book_spec,
        world_spec=world_spec,
        cast_spec=cast_spec,
    )
    return RepairRow(
        slug=project.slug,
        before_codes=before_codes,
        book_repaired=book_repaired,
        cast_repaired=cast_repaired,
        after_codes=_count_codes(after_report),
    )


async def _load_projects(
    session: Any,
    *,
    project_slugs: set[str],
    all_projects: bool,
) -> list[ProjectModel]:
    stmt = select(ProjectModel).order_by(ProjectModel.slug.asc())
    if project_slugs:
        stmt = stmt.where(ProjectModel.slug.in_(sorted(project_slugs)))
    elif not all_projects:
        raise ValueError("Pass --all or at least one --project-slug.")
    return list(await session.scalars(stmt))


async def _run(
    *,
    project_slugs: set[str],
    all_projects: bool,
    apply: bool,
    skip_llm: bool,
) -> None:
    settings = load_settings()
    session_factory = create_session_factory(settings)
    rows: list[RepairRow] = []

    async with session_factory() as session:
        projects = await _load_projects(
            session,
            project_slugs=project_slugs,
            all_projects=all_projects,
        )
        for project in projects:
            row = await _repair_one_project(
                session,
                settings,
                project,
                apply=apply,
                skip_llm=skip_llm,
            )
            rows.append(row)
            if apply:
                await session.commit()

    action = "applied" if apply else "dry-run"
    print(f"{action}: {len(rows)} project(s)")
    for row in rows:
        print(
            f"{row.slug}: book_repaired={row.book_repaired} "
            f"cast_repaired={row.cast_repaired} before={row.before_codes} "
            f"after={row.after_codes} {row.notes}".rstrip()
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-slug", action="append", default=[])
    parser.add_argument("--all", action="store_true", help="Process all projects.")
    parser.add_argument("--apply", action="store_true", help="Write repairs and call LLM CastSpec repair.")
    parser.add_argument(
        "--skip-llm",
        action="store_true",
        help="Do not call LLM CastSpec repair; only apply deterministic synthesis.",
    )
    args = parser.parse_args()
    asyncio.run(
        _run(
            project_slugs=set(args.project_slug),
            all_projects=args.all,
            apply=args.apply,
            skip_llm=args.skip_llm,
        )
    )


if __name__ == "__main__":
    main()
