"""Prepare/review or apply one bounded same-project derived-input revision.

No implicit model calls, retries, project resume, or chapter dispatch. Each
command has one phase; a stored attempt prevents silent duplicate generation.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys
from uuid import UUID

from sqlalchemy import func, select

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bestseller.domain.enums import ArtifactType
from bestseller.infra.db.models import (
    ProjectModel,
    ReviewReportModel,
    WorkflowRunModel,
)
from bestseller.infra.db.session import session_scope
from bestseller.services.derived_revision import (
    DerivedRevisionError,
    propose_and_review_derived_revision,
    validate_revision_issues,
)
from bestseller.services.derived_revision_apply import apply_reviewed_derived_revision
from bestseller.services.production_control import load_control_state
from bestseller.services.story_rebuild_workspace import source_content_hash
from bestseller.services.workflows import get_latest_planning_artifact
from bestseller.services.world_recompile_candidate import (
    build_world_recompile_candidate,
)
from bestseller.settings import load_settings


async def run(args: argparse.Namespace) -> int:
    settings = load_settings()
    async with session_scope(settings) as session:
        project = await session.scalar(select(ProjectModel).where(ProjectModel.slug == args.slug))
        if project is None:
            raise ValueError("project_not_found")
        if args.phase == "prepare-cast-source-review":
            from bestseller.services.derived_cast_source_review import (
                VERSION as CAST_SOURCE_REVIEW_VERSION,
                build_cast_recovery_candidate,
                build_historical_cast_evidence,
                collect_approved_historical_casts,
                normalize_cast_recovery_sources,
                prepare_cast_source_review,
            )
            from bestseller.services.planner import (
                _compile_source_bound_cast_spec,
                _enforce_premise_locked_cast,
            )

            if not args.report_id:
                raise ValueError("phase_requires_report_id")
            control = await load_control_state(session, project.id)
            if not control.halted or project.status != "paused":
                raise ValueError("cast_source_review_pause_required")
            marker = (project.metadata_json or {}).get("derived_input_revision") or {}
            if marker.get("status") != "applied" or marker.get("report_id") != args.report_id:
                raise ValueError("cast_source_review_source_revision_mismatch")
            applied_report = await session.scalar(
                select(ReviewReportModel).where(
                    ReviewReportModel.id == args.report_id,
                    ReviewReportModel.project_id == project.id,
                    ReviewReportModel.reviewer_type == "derived_input_revision",
                    ReviewReportModel.verdict == "pass",
                )
            )
            if applied_report is None:
                raise ValueError("cast_source_review_applied_report_missing")
            from bestseller.services.derived_revision import verify_derived_revision_report
            try:
                verify_derived_revision_report(applied_report.structured_output)
            except (TypeError, ValueError) as exc:
                raise ValueError("cast_source_review_applied_report_invalid") from exc
            active_workflow = await session.scalar(
                select(WorkflowRunModel.id)
                .where(
                    WorkflowRunModel.project_id == project.id,
                    WorkflowRunModel.status.in_(
                        ["pending", "queued", "running", "machine_blocked"]
                    ),
                )
                .limit(1)
            )
            if active_workflow is not None:
                raise ValueError("cast_source_review_active_workflow")
            cast = await get_latest_planning_artifact(
                session, project_id=project.id, artifact_type=ArtifactType.CAST_SPEC
            )
            book = await get_latest_planning_artifact(
                session, project_id=project.id, artifact_type=ArtifactType.BOOK_SPEC
            )
            world = await get_latest_planning_artifact(
                session, project_id=project.id, artifact_type=ArtifactType.WORLD_SPEC
            )
            premise = await get_latest_planning_artifact(
                session, project_id=project.id, artifact_type=ArtifactType.PREMISE
            )
            if any(item is None for item in (cast, book, world, premise)):
                raise ValueError("cast_source_review_current_sources_missing")
            if any(item.status != "approved" for item in (cast, book, world, premise)):
                raise ValueError("cast_source_review_approved_sources_required")
            if (
                str(book.id) != marker.get("book_artifact_id")
                or str(world.id) != marker.get("world_artifact_id")
            ):
                raise ValueError("cast_source_review_current_sources_stale")
            premise_text = premise.content.get("premise") or premise.content.get("text")
            if not premise_text:
                raise ValueError("cast_source_review_premise_empty")
            book_payload, world_payload = normalize_cast_recovery_sources(
                book.content, world.content
            )
            compiled = _compile_source_bound_cast_spec(
                project, premise_text, book_payload, world_payload
            )
            compiled = _enforce_premise_locked_cast(
                compiled, premise_text, language=project.language
            )
            historical_casts = await collect_approved_historical_casts(
                session, project_id=project.id, current_version=cast.version_no
            )
            historical_evidence = build_historical_cast_evidence(cast, historical_casts)
            candidate, audit = build_cast_recovery_candidate(
                compiled,
                cast.content,
                approved_sources={
                    "premise": premise_text,
                    "book_spec": book_payload,
                    "world_spec": world_payload,
                },
                parent_ids={
                    "premise": str(premise.id),
                    "book": str(book.id),
                    "world": str(world.id),
                },
                revision_report_id=args.report_id,
                legacy_artifact_id=str(cast.id),
                legacy_artifact_status=cast.status,
                legacy_artifact_version=cast.version_no,
                historical_cast_evidence=historical_evidence,
            )
            attempt_hash = source_content_hash(
                {"review_input_hash": audit["review_input_hash"], "phase": args.phase}
            )
            locked = await session.scalar(
                select(func.pg_try_advisory_xact_lock(
                    func.hashtext(f"cast-source-review:{project.id}:{attempt_hash}")
                ))
            )
            if not locked:
                raise ValueError("cast_source_review_attempt_in_progress")
            prior = await session.scalar(
                select(ReviewReportModel)
                .where(
                    ReviewReportModel.project_id == project.id,
                    ReviewReportModel.reviewer_type == "derived_cast_source",
                    ReviewReportModel.structured_output["attempt_hash"].astext == attempt_hash,
                )
                .limit(1)
            )
            if prior is not None:
                print(json.dumps({"status": "already_attempted", "report_id": str(prior.id)}))
                return 4
            review = ReviewReportModel(
                project_id=project.id,
                target_type="planning_artifact",
                target_id=cast.id,
                reviewer_type="derived_cast_source",
                verdict="needs_review",
                structured_output={
                    "status": "running",
                    "phase": args.phase,
                    "attempt_hash": attempt_hash,
                    "review_input_hash": audit["review_input_hash"],
                    "budget": {"max_calls": 3, "calls": 0},
                },
            )
            session.add(review)
            await session.commit()
            try:
                result = await prepare_cast_source_review(
                    session, settings, project_id=project.id, candidate=candidate, audit=audit
                )
            except Exception as exc:
                result = {
                    "version": CAST_SOURCE_REVIEW_VERSION,
                    "status": "needs_review",
                    "phase": args.phase,
                    "budget": {"max_calls": 3, "calls": None},
                    "error": type(exc).__name__,
                    "error_message": str(exc),
                    "candidate_cast": candidate,
                    "audit": audit,
                    "reviews": [],
                }
            review.structured_output = {
                **result,
                "attempt_hash": attempt_hash,
                "derived_revision_report_id": args.report_id,
                "source_premise_id": str(premise.id),
                "source_book_id": str(book.id),
                "source_world_id": str(world.id),
            }
            review.verdict = "pass" if result.get("status") == "review_passed" else "needs_review"
            await session.commit()
            print(json.dumps({"report_id": str(review.id), "status": result.get("status"), "phase": args.phase}, ensure_ascii=False))
            return 0 if review.verdict == "pass" else 2
        if args.phase == "repair-cast":
            from bestseller.services.derived_cast_repair import (
                CastDependencyRepairError,
                repair_cast_dependency,
            )
            from bestseller.services.ideology_kernel import cost_style_directive
            from bestseller.services.story_enhancers import resolve_cost_style

            if not args.issues or not args.report_id:
                raise ValueError("cast_repair_requires_issues_and_applied_report")
            control = await load_control_state(session, project.id)
            if not control.halted or project.status != "paused":
                raise ValueError("cast_repair_pause_required")
            marker = (project.metadata_json or {}).get("derived_input_revision") or {}
            if marker.get("report_id") != args.report_id:
                raise ValueError("cast_repair_source_revision_mismatch")
            cast = await get_latest_planning_artifact(
                session, project_id=project.id, artifact_type=ArtifactType.CAST_SPEC
            )
            world = await get_latest_planning_artifact(
                session, project_id=project.id, artifact_type=ArtifactType.WORLD_SPEC
            )
            premise = await get_latest_planning_artifact(
                session, project_id=project.id, artifact_type=ArtifactType.PREMISE
            )
            if (
                cast is None
                or world is None
                or premise is None
                or str(world.id) != marker.get("world_artifact_id")
            ):
                raise ValueError("cast_repair_current_sources_missing")
            issues = json.loads(Path(args.issues).read_text(encoding="utf-8"))
            attempt_hash = source_content_hash(
                {
                    "cast": cast.content,
                    "world": world.content,
                    "issues": issues,
                    "revision_report_id": args.report_id,
                }
            )
            locked = await session.scalar(
                select(
                    func.pg_try_advisory_xact_lock(
                        func.hashtext(f"cast-dependency:{project.id}:{attempt_hash}")
                    )
                )
            )
            if not locked:
                raise ValueError("cast_repair_attempt_in_progress")
            prior = await session.scalar(
                select(ReviewReportModel)
                .where(
                    ReviewReportModel.project_id == project.id,
                    ReviewReportModel.reviewer_type == "derived_cast_dependency",
                    ReviewReportModel.structured_output["attempt_hash"].astext == attempt_hash,
                )
                .limit(1)
            )
            if prior is not None:
                print(json.dumps({"status": "already_attempted", "report_id": str(prior.id)}))
                return 4
            review = ReviewReportModel(
                project_id=project.id,
                target_type="planning_artifact",
                target_id=cast.id,
                reviewer_type="derived_cast_dependency",
                verdict="needs_review",
                structured_output={"status": "running", "attempt_hash": attempt_hash},
            )
            session.add(review)
            await session.commit()
            try:
                result = await repair_cast_dependency(
                    session,
                    settings,
                    project_id=project.id,
                    cast_content=cast.content,
                    world_content=world.content,
                    premise=premise.content["premise"],
                    cost_policy=cost_style_directive(
                        resolve_cost_style(project.metadata_json or {})
                    ),
                    issues=issues,
                )
            except CastDependencyRepairError as exc:
                result = exc.report
            review.structured_output = {
                **result,
                "attempt_hash": attempt_hash,
                "derived_revision_report_id": args.report_id,
                "source_world_id": str(world.id),
            }
            review.verdict = "pass" if result["status"] == "review_passed" else "needs_review"
            await session.commit()
            print(
                json.dumps(
                    {"report_id": str(review.id), "status": result["status"], "applied": False}
                )
            )
            return 0 if review.verdict == "pass" else 2
        if args.phase in {"project", "project-source-cast", "outline"}:
            if not args.report_id:
                raise ValueError("phase_requires_report_id")
            if args.phase in {"project", "project-source-cast"}:
                if args.phase == "project-source-cast" and not args.cast_source_review_id:
                    raise ValueError("project_source_cast_requires_review_id")
                if args.phase == "project-source-cast" and args.cast_report_id:
                    raise ValueError("project_source_cast_review_ids_mutually_exclusive")
                from bestseller.services.derived_revision_projection import (
                    refresh_derived_revision_projection,
                )

                result = await refresh_derived_revision_projection(
                    session,
                    settings,
                    args.slug,
                    UUID(args.report_id),
                    cast_report_id=UUID(args.cast_report_id) if args.cast_report_id else None,
                    cast_source_review_id=(
                        UUID(args.cast_source_review_id)
                        if args.cast_source_review_id
                        else None
                    ),
                )
            else:
                from bestseller.services.first_chapter_recovery import recover_first_outline_window

                result = await recover_first_outline_window(
                    session, settings, project_slug=args.slug, report_id=UUID(args.report_id)
                )
            await session.commit()
            print(json.dumps(result, ensure_ascii=False))
            return 0
        if args.phase == "apply":
            if not args.report_id or args.lock_version is None:
                raise ValueError("apply_requires_report_id_and_lock_version")
            result = await apply_reviewed_derived_revision(
                session,
                project_slug=args.slug,
                report_id=UUID(args.report_id),
                expected_lock_version=args.lock_version,
            )
            await session.commit()
            print(json.dumps(result, ensure_ascii=False))
            return 0
        if not args.issues:
            raise ValueError("inspect_and_propose_require_issues_file")
        issues = json.loads(Path(args.issues).read_text(encoding="utf-8"))
        world = await get_latest_planning_artifact(
            session, project_id=project.id, artifact_type=ArtifactType.WORLD_SPEC
        )
        premise = await get_latest_planning_artifact(
            session, project_id=project.id, artifact_type=ArtifactType.PREMISE
        )
        if world is None or premise is None or premise.status != "approved":
            raise ValueError("approved_foundation_required")
        parent = build_world_recompile_candidate(
            project,
            premise=premise.content["premise"],
            premise_artifact_id=str(premise.id),
            source_artifact=world,
        )
        issues = validate_revision_issues(parent, issues)
        attempt_hash = source_content_hash({"parent": parent, "issues": issues})
        summary = {
            "project_id": str(project.id),
            "slug": project.slug,
            "phase": args.phase,
            "lock_version": project.lock_version,
            "input_hash": parent["input_hash"],
            "attempt_hash": attempt_hash,
            "planned_model_calls": 3,
            "changed_paths": [i["path"] for i in issues],
        }
        if args.phase == "inspect":
            print(json.dumps(summary, ensure_ascii=False))
            return 0
        control = await load_control_state(session, project.id)
        if not control.halted or project.status != "paused":
            raise ValueError("paused_project_required")
        active = await session.scalar(
            select(WorkflowRunModel.id)
            .where(
                WorkflowRunModel.project_id == project.id,
                WorkflowRunModel.status.in_(["pending", "queued", "running", "machine_blocked"]),
            )
            .limit(1)
        )
        if active:
            raise ValueError("active_workflow_present")
        locked = await session.scalar(
            select(
                func.pg_try_advisory_xact_lock(
                    func.hashtext(f"derived-revision-attempt:{project.id}:{attempt_hash}")
                )
            )
        )
        if not locked:
            raise ValueError("attempt_in_progress")
        prior = await session.scalar(
            select(ReviewReportModel)
            .where(
                ReviewReportModel.project_id == project.id,
                ReviewReportModel.reviewer_type == "derived_input_revision",
                ReviewReportModel.structured_output["attempt_hash"].astext == attempt_hash,
            )
            .limit(1)
        )
        if prior:
            print(
                json.dumps(
                    {
                        "status": "already_attempted",
                        "report_id": str(prior.id),
                        "verdict": prior.verdict,
                    }
                )
            )
            return 4
        report = ReviewReportModel(
            project_id=project.id,
            target_type="planning_artifact",
            target_id=world.id,
            reviewer_type="derived_input_revision",
            verdict="needs_review",
            severity_max="high",
            structured_output={"status": "running", "attempt_hash": attempt_hash, "parent": parent},
        )
        session.add(report)
        await session.flush()
        await session.commit()  # durable attempt claim even if the process dies
        try:
            result = await propose_and_review_derived_revision(
                session,
                settings,
                parent=parent,
                issues=issues,
                advisory_candidate=args.advisory_candidate,
            )
        except DerivedRevisionError as exc:
            result = exc.report
        report.structured_output = {**result, "attempt_hash": attempt_hash}
        report.verdict = "pass" if result["status"] == "review_passed" else "needs_review"
        report.severity_max = "low" if report.verdict == "pass" else "high"
        await session.commit()
        print(
            json.dumps(
                {
                    **summary,
                    "report_id": str(report.id),
                    "status": result["status"],
                    "error": result.get("error"),
                    "applied": False,
                },
                ensure_ascii=False,
            )
        )
        return 0 if report.verdict == "pass" else 2


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--slug", required=True)
    parser.add_argument(
        "--phase",
        choices=("inspect", "propose", "apply", "project", "prepare-cast-source-review", "project-source-cast", "outline", "repair-cast"),
        default="inspect",
    )
    parser.add_argument("--issues")
    parser.add_argument("--advisory-candidate")
    parser.add_argument("--report-id")
    parser.add_argument("--cast-report-id")
    parser.add_argument("--cast-source-review-id")
    parser.add_argument("--lock-version", type=int)
    return asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
