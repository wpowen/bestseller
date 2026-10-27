"""Run a bounded, evidence-only audit of a project's current world source.

The default mode is read-only.  ``--execute`` performs at most one audit and
records its result in ``review_reports``; it never edits planning artifacts or
queues production work.
"""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Mapping
import json
from pathlib import Path
import sys
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

_THIS = Path(__file__).resolve()
_SRC = _THIS.parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from bestseller.domain.enums import ArtifactType, WorkflowStatus  # noqa: E402
from bestseller.infra.db.models import (  # noqa: E402
    PlanningArtifactVersionModel,
    ProjectModel,
    ReviewReportModel,
    WorkflowRunModel,
)
from bestseller.infra.db.session import session_scope  # noqa: E402
from bestseller.services.ideology_kernel import cost_style_directive  # noqa: E402
from bestseller.services.production_control import load_control_state  # noqa: E402
from bestseller.services.story_enhancers import resolve_cost_style  # noqa: E402
from bestseller.services.workflows import get_latest_planning_artifact  # noqa: E402
from bestseller.services.world_authority import (  # noqa: E402
    AUDIT_VERSION,
    WorldAuthorityAuditError,
    audit_world_authority,
    world_authority_source_hash,
    world_authority_source_index,
)
from bestseller.settings import load_settings  # noqa: E402


def build_sources(project: ProjectModel, *, world_content: Mapping[str, Any]) -> dict[str, Any]:
    """Build the narrow source envelope consumed by the audit.

    Only the formal cost choice/definition and canonical identity are creation
    authority.  The world side is deliberately restricted to the two fields
    whose semantics this audit can assess.
    """
    metadata = dict(project.metadata_json or {})
    raw_enhancers = metadata.get("story_enhancers")
    has_explicit_cost = (
        isinstance(raw_enhancers, Mapping)
        and isinstance(raw_enhancers.get("cost_style"), str)
        and bool(raw_enhancers.get("cost_style"))
    )
    cost_style = resolve_cost_style(metadata)
    creation = {
        "cost_style": cost_style,
        "cost_style_origin": "explicit" if has_explicit_cost else "default",
        "cost_rule": cost_style_directive(cost_style),
        "creation_protagonist_name": metadata.get("creation_protagonist_name", ""),
    }
    # This function intentionally does not derive or repair world fields.
    return {
        "creation": creation,
        "world": {
            key: world_content[key] for key in ("power_system", "rules") if key in world_content
        },
    }


def safe_summary(
    *, slug: str, artifact: PlanningArtifactVersionModel, sources: Mapping[str, Any]
) -> dict[str, Any]:
    """Return only identifiers and sizes suitable for CLI stdout."""
    index = world_authority_source_index(sources)
    return {
        "slug": slug,
        "artifact_id": str(artifact.id),
        "artifact_version": artifact.version_no,
        "source_hash": world_authority_source_hash(sources),
        "source_entries": len(index),
        "source_chars": sum(len(str(item.get("quote", ""))) for item in index),
    }


async def _active_workflow_count(session: AsyncSession, project_id: UUID) -> int:
    return int(
        await session.scalar(
            select(func.count(WorkflowRunModel.id)).where(
                WorkflowRunModel.project_id == project_id,
                WorkflowRunModel.status.in_(
                    {
                        WorkflowStatus.PENDING.value,
                        WorkflowStatus.QUEUED.value,
                        WorkflowStatus.RUNNING.value,
                    }
                ),
            )
        )
        or 0
    )


def _report_payload(result: Mapping[str, Any]) -> tuple[str, str, dict[str, Any]]:
    status = str(result.get("status", "review_required"))
    # A source audit is never an approval: the service explicitly requires a
    # semantic review even when provenance checks are consistent.
    return "needs_review", "low" if status == "consistent" else "high", dict(result)


def production_guard_status(*, halted: bool, active_workflows: int) -> tuple[bool, str]:
    """Return whether an execute call may start, without changing state."""
    if not halted:
        return False, "production_must_be_halted"
    if active_workflows:
        return False, "active_workflows_present"
    return True, "ok"


async def _load_bound_diagnostic(
    session: AsyncSession,
    *,
    diagnostic_id: str | UUID | None,
    project_id: UUID,
    artifact_id: UUID,
    reviewer_type: str = "world_cost_claims_diagnostic",
    target_type: str = "planning_artifact",
) -> dict[str, Any]:
    if diagnostic_id is None:
        raise ValueError("SOURCE_PATCH_DIAGNOSTIC_ID_REQUIRED")
    try:
        report_id = diagnostic_id if isinstance(diagnostic_id, UUID) else UUID(str(diagnostic_id))
    except (TypeError, ValueError) as exc:
        raise ValueError("SOURCE_PATCH_INVALID_DIAGNOSTIC_ID") from exc
    report = await session.scalar(
        select(ReviewReportModel).where(
            ReviewReportModel.id == report_id,
            ReviewReportModel.project_id == project_id,
            ReviewReportModel.target_type == target_type,
            ReviewReportModel.target_id == artifact_id,
            ReviewReportModel.reviewer_type == reviewer_type,
            ReviewReportModel.verdict == "needs_review",
        )
    )
    if report is None or not isinstance(report.structured_output, Mapping):
        raise ValueError("SOURCE_PATCH_DIAGNOSTIC_NOT_BOUND")
    return dict(report.structured_output)


_PATCH_MODES = {"source-patch", "patch-review", "partition-patch"}


async def _revision_cost_sources(
    session: AsyncSession, *, project: ProjectModel,
    artifact: PlanningArtifactVersionModel, candidate_id: str | UUID,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Resolve and verify the exact revised input before spending a model call."""
    from bestseller.services.world_recompile_candidate import (
        build_world_recompile_candidate,
        verify_world_input_revision,
    )

    candidate = await _load_bound_diagnostic(
        session, diagnostic_id=candidate_id, project_id=project.id, artifact_id=artifact.id,
        reviewer_type="world_input_revision_candidate",
    )
    revision = candidate.get("revision")
    if not isinstance(revision, Mapping):
        raise ValueError("WORLD_COST_REVISION_RECEIPT_MISSING")
    parent_id = revision.get("parent_report_id")
    parent = await _load_bound_diagnostic(
        session, diagnostic_id=parent_id, project_id=project.id, artifact_id=artifact.id,
        reviewer_type="world_recompile_candidate",
    )
    verify_world_input_revision(candidate, parent=parent, parent_report_id=str(parent_id))
    premise = await get_latest_planning_artifact(
        session, project_id=project.id, artifact_type=ArtifactType.PREMISE,
    )
    if premise is None or premise.status != "approved" or not isinstance(premise.content, Mapping):
        raise ValueError("WORLD_COST_APPROVED_PREMISE_REQUIRED")
    premise_text = premise.content.get("premise")
    if not isinstance(premise_text, str):
        raise ValueError("WORLD_COST_PREMISE_MISSING")
    current = build_world_recompile_candidate(
        project, premise=premise_text, source_artifact=artifact,
        premise_artifact_id=str(premise.id),
    )
    if parent.get("project_id") != str(project.id) or any(
        current[key] != parent[key]
        for key in ("input_hash", "source_hash", "candidate_hash", "book_hash")
    ):
        raise ValueError("WORLD_COST_REVISION_PARENT_STALE")
    return build_sources(project, world_content=candidate["candidate_world"]), {
        "source_candidate_id": str(UUID(str(candidate_id))),
        "source_candidate_hash": candidate["candidate_hash"],
        "source_input_hash": candidate["input_hash"],
        "evaluated_scope": "revised_candidate_cost_facts_not_full_world_approval",
        "production_ready": False,
        "promotion_allowed": False,
    }


async def run(
    *,
    slug: str,
    execute: bool = False,
    mode: str = "source",
    diagnostic_id: str | UUID | None = None,
    candidate_id: str | UUID | None = None,
    shadow_judge: bool = False,
    revision_edit: Mapping[str, Any] | None = None,
) -> int:
    requested_mode = mode
    disjoint_restatement = mode == "world-restate-disjoint"
    atomic_restatement = mode == "world-restate-atomic"
    anchored_restatement = mode in {
        "world-restate-anchored",
        "world-restate-disjoint",
        "world-restate-atomic",
    }
    if anchored_restatement:
        mode = "world-restate"
    if mode == "cost-claims" and candidate_id is not None and diagnostic_id is not None:
        raise ValueError("WORLD_COST_CANDIDATE_NOT_OLD_DIAGNOSTIC")
    if mode == "world-restate" and (candidate_id is None or diagnostic_id is None):
        raise ValueError("RESTATEMENT_REQUIRES_CANDIDATE_AND_DIAGNOSTIC")
    if shadow_judge and (mode != "partition-eval" or diagnostic_id is not None):
        raise ValueError("SHADOW_JUDGE_ONLY_FOR_FRESH_PARTITION_EVAL")
    if mode == "partition-staged-eval" and (diagnostic_id is not None or candidate_id is not None):
        raise ValueError("STAGED_EVAL_FRESH_ONLY")
    if mode == "world-recompile" and (diagnostic_id is not None or candidate_id is not None):
        raise ValueError("RECOMPILE_FRESH_INPUTS_ONLY")
    if mode == "world-revise":
        if candidate_id is None or revision_edit is None or diagnostic_id is not None:
            raise ValueError("WORLD_REVISE_REQUIRES_PARENT_AND_EDIT")
    elif revision_edit is not None:
        raise ValueError("REVISION_EDIT_ONLY_FOR_WORLD_REVISE")
    if mode not in {
        "source",
        "cost-claims",
        "partition-eval",
        "partition-staged-eval",
        "world-recompile",
        "world-revise",
        "world-restate",
        *_PATCH_MODES,
    }:
        raise ValueError(f"unsupported audit mode: {mode}")
    settings = load_settings()
    diagnostic: dict[str, Any] | None = None
    exit_code = 0
    output: dict[str, Any] | None = None
    async with session_scope(settings) as session:
        project = await session.scalar(select(ProjectModel).where(ProjectModel.slug == slug))
        if project is None:
            print(json.dumps({"status": "not_found", "slug": slug}, ensure_ascii=False))
            return 2
        artifact = await get_latest_planning_artifact(
            session,
            project_id=project.id,
            artifact_type=ArtifactType.WORLD_SPEC,
        )
        if artifact is None or not isinstance(artifact.content, Mapping):
            print(json.dumps({"status": "missing_world_spec", "slug": slug}, ensure_ascii=False))
            return 2
        # Keep the source builder independent of the persisted model schema.
        sources = build_sources(project, world_content=artifact.content)
        candidate_binding: dict[str, Any] = {}
        if mode in {"cost-claims", "world-restate"} and candidate_id is not None:
            sources, candidate_binding = await _revision_cost_sources(
                session, project=project, artifact=artifact, candidate_id=candidate_id,
            )
        summary = safe_summary(slug=slug, artifact=artifact, sources=sources)
        summary.update(candidate_binding)
        bound_diagnostic = None
        bound_diagnostic_id = None
        bound_candidate = None
        bound_candidate_id = None
        if mode == "world-restate":
            from bestseller.services.world_clause_restatement import _target

            bound_candidate_id = str(UUID(str(candidate_id)))
            bound_diagnostic_id = str(UUID(str(diagnostic_id)))
            bound_candidate = await _load_bound_diagnostic(
                session, diagnostic_id=candidate_id, project_id=project.id,
                artifact_id=artifact.id, reviewer_type="world_input_revision_candidate",
            )
            bound_diagnostic = await _load_bound_diagnostic(
                session, diagnostic_id=diagnostic_id, project_id=project.id,
                artifact_id=UUID(bound_candidate_id), reviewer_type="world_candidate_cost_claims",
                target_type="review_report",
            )
            _target(parent=bound_candidate, parent_report_id=bound_candidate_id,
                    sources=sources, diagnostic=bound_diagnostic,
                    diagnostic_report_id=bound_diagnostic_id)
            candidate_binding["evaluated_scope"] = "clause_restatement_proposal_not_world_approval"
            candidate_binding["source_diagnostic_id"] = bound_diagnostic_id
            summary.update(candidate_binding)
        if mode == "world-revise":
            bound_candidate = await _load_bound_diagnostic(
                session, diagnostic_id=candidate_id, project_id=project.id,
                artifact_id=artifact.id, reviewer_type="world_recompile_candidate",
            )
            bound_candidate_id = str(UUID(str(candidate_id)))
        if mode == "partition-eval" and diagnostic_id is not None:
            bound_diagnostic = await _load_bound_diagnostic(
                session,
                diagnostic_id=diagnostic_id,
                project_id=project.id,
                artifact_id=artifact.id,
                reviewer_type="world_partition_capability_diagnostic",
            )
            bound_diagnostic_id = str(UUID(str(diagnostic_id)))
        if mode == "patch-review":
            bound_candidate = await _load_bound_diagnostic(
                session,
                diagnostic_id=candidate_id,
                project_id=project.id,
                artifact_id=artifact.id,
                reviewer_type="world_source_patch_candidate",
            )
            bound_candidate_id = str(UUID(str(candidate_id)))
            if bound_candidate.get("source_artifact_id") != str(artifact.id):
                raise ValueError("SOURCE_PATCH_STALE_ARTIFACT")
            diagnostic_id = bound_candidate.get("source_diagnostic_id")
        if mode in _PATCH_MODES:
            bound_diagnostic = await _load_bound_diagnostic(
                session,
                diagnostic_id=diagnostic_id,
                project_id=project.id,
                artifact_id=artifact.id,
            )
            bound_diagnostic_id = str(UUID(str(diagnostic_id)))
        if not execute:
            output = {"status": "dry_run", "mode": requested_mode, **summary}
            exit_code = 0
        else:
            control = await load_control_state(session, project.id)
            active_count = await _active_workflow_count(session, project.id)
            allowed, reason = production_guard_status(
                halted=control.halted, active_workflows=active_count
            )
            if not allowed:
                output = {
                    "status": "blocked",
                    "slug": slug,
                    "reason": reason,
                    "halted": control.halted,
                    "active_workflows": active_count,
                }
                exit_code = 3
            else:
                if mode == "world-restate":
                    from bestseller.services.world_clause_restatement import (
                        ANCHORED_RESTATEMENT_VERSION,
                        ATOMIC_RESTATEMENT_VERSION,
                        DISJOINT_RESTATEMENT_VERSION,
                        RESTATEMENT_VERSION,
                        propose_anchored_world_restatement,
                        propose_world_clause_restatement,
                    )

                    audit_version = (ATOMIC_RESTATEMENT_VERSION if atomic_restatement
                                     else DISJOINT_RESTATEMENT_VERSION if disjoint_restatement
                                     else ANCHORED_RESTATEMENT_VERSION if anchored_restatement
                                     else RESTATEMENT_VERSION)
                    reviewer_type = (
                        "world_atomic_clause_restatement_candidate" if atomic_restatement
                        else "world_disjoint_clause_restatement_candidate" if disjoint_restatement
                        else "world_anchored_clause_restatement_candidate" if anchored_restatement
                        else "world_clause_restatement_candidate"
                    )
                    locked = await session.scalar(select(func.pg_try_advisory_xact_lock(
                        func.hashtext(f"world-restate:{project.id}:{candidate_id}:{diagnostic_id}")
                    )))
                    if not locked:
                        print(json.dumps({"status": "blocked", "mode": requested_mode,
                                          "reason": "restatement_attempt_in_progress"}))
                        return 3
                    prior = await session.scalar(select(ReviewReportModel).where(
                        ReviewReportModel.project_id == project.id,
                        ReviewReportModel.target_type == "review_report",
                        ReviewReportModel.target_id == UUID(str(candidate_id)),
                        ReviewReportModel.reviewer_type == reviewer_type,
                        ReviewReportModel.structured_output["source_diagnostic_id"].astext
                        == bound_diagnostic_id,
                    ).limit(1))
                    if prior is not None:
                        print(json.dumps({"status": "already_attempted", "mode": requested_mode,
                                          "report_id": str(prior.id)}, ensure_ascii=False))
                        return 4
                elif mode in {"world-recompile", "world-revise"}:
                    from bestseller.services.world_recompile_candidate import (
                        RECOMPILE_VERSION,
                        build_world_recompile_candidate,
                        revise_world_recompile_candidate,
                        verify_world_input_revision,
                        verify_world_recompile_candidate,
                    )

                    audit_version = RECOMPILE_VERSION
                    reviewer_type = (
                        "world_input_revision_candidate" if mode == "world-revise"
                        else "world_recompile_candidate"
                    )
                elif mode == "partition-staged-eval":
                    from bestseller.services.world_partition_staged_eval import (
                        STAGED_VERSION,
                        run_staged_partition_eval,
                    )

                    audit_version = STAGED_VERSION
                    reviewer_type = "world_partition_staged_capability_diagnostic"
                elif mode == "partition-eval":
                    from bestseller.services.world_partition_eval import (
                        EVAL_VERSION,
                        replay_partition_eval,
                        run_partition_eval,
                    )

                    audit_version = EVAL_VERSION
                    reviewer_type = "world_partition_capability_diagnostic"
                elif mode == "cost-claims":
                    from bestseller.services.world_cost_claims import (
                        CLAIMS_VERSION,
                        audit_world_cost_claims,
                    )

                    audit_fn = audit_world_cost_claims
                    audit_version = CLAIMS_VERSION
                    reviewer_type = (
                        "world_candidate_cost_claims" if candidate_binding
                        else "world_cost_claims_diagnostic"
                    )
                elif mode == "partition-patch":
                    from bestseller.services.world_source_partition import (
                        PARTITION_VERSION,
                        propose_partition_patch,
                    )

                    audit_version = PARTITION_VERSION
                    reviewer_type = "world_source_partition_diagnostic"
                elif mode == "patch-review":
                    from bestseller.services.world_source_patch_review import (
                        REVIEW_VERSION,
                        review_world_source_patch,
                    )

                    audit_version = REVIEW_VERSION
                    reviewer_type = "world_source_patch_semantic_review"
                elif mode == "source-patch":
                    from bestseller.services.world_source_patch import (
                        PATCH_VERSION,
                        propose_world_source_patch,
                    )

                    audit_version = PATCH_VERSION
                    reviewer_type = "world_source_patch_candidate"
                else:
                    audit_fn = audit_world_authority
                    audit_version = AUDIT_VERSION
                    reviewer_type = "world_authority_diagnostic"
                try:
                    if mode == "world-restate":
                        if (bound_candidate is None or bound_diagnostic is None
                                or bound_candidate_id is None or bound_diagnostic_id is None):
                            raise ValueError("RESTATEMENT_BOUND_INPUTS_REQUIRED")
                        if anchored_restatement:
                            restatement_kwargs: dict[str, Any] = {
                                "project_id": project.id,
                                "parent": bound_candidate,
                                "parent_report_id": bound_candidate_id,
                                "sources": sources,
                                "diagnostic": bound_diagnostic,
                                "diagnostic_report_id": bound_diagnostic_id,
                                "disjoint_amendments": disjoint_restatement,
                            }
                            if atomic_restatement:
                                restatement_kwargs["atomic_facts"] = True
                                restatement_kwargs["disjoint_amendments"] = True
                            result = await propose_anchored_world_restatement(
                                session, settings, **restatement_kwargs,
                            )
                        else:
                            result = await propose_world_clause_restatement(
                                session, settings, project_id=project.id,
                                parent=bound_candidate, parent_report_id=bound_candidate_id,
                                sources=sources, diagnostic=bound_diagnostic,
                                diagnostic_report_id=bound_diagnostic_id,
                            )
                    elif mode in {"world-recompile", "world-revise"}:
                        premise_artifact = await get_latest_planning_artifact(
                            session, project_id=project.id, artifact_type=ArtifactType.PREMISE,
                        )
                        if (premise_artifact is None or premise_artifact.status != "approved"
                                or not isinstance(premise_artifact.content, Mapping)):
                            raise ValueError("RECOMPILE_APPROVED_PREMISE_REQUIRED")
                        premise_text = premise_artifact.content.get("premise")
                        if not isinstance(premise_text, str) or not premise_text.strip():
                            raise ValueError("RECOMPILE_PREMISE_MISSING")
                        result = build_world_recompile_candidate(
                            project, premise=premise_text,
                            source_artifact=artifact,
                            premise_artifact_id=str(premise_artifact.id),
                        )
                        verify_world_recompile_candidate(result)
                        if mode == "world-revise":
                            if bound_candidate is None or bound_candidate_id is None:
                                raise ValueError("WORLD_REVISE_PARENT_MISSING")
                            verify_world_recompile_candidate(bound_candidate)
                            if (bound_candidate.get("project_id") != str(project.id)
                                    or any(result[key] != bound_candidate[key] for key in (
                                        "input_hash", "source_hash", "candidate_hash", "book_hash"
                                    ))):
                                raise ValueError("WORLD_REVISE_PARENT_STALE")
                            if not isinstance(revision_edit, Mapping):
                                raise ValueError("WORLD_REVISE_EDIT_INVALID")
                            result = revise_world_recompile_candidate(
                                bound_candidate, parent_report_id=bound_candidate_id,
                                edit=revision_edit,
                            )
                            verify_world_input_revision(
                                result, parent=bound_candidate, parent_report_id=bound_candidate_id,
                            )
                    elif mode in {"partition-eval", "partition-staged-eval"}:
                        fixture_path = (
                            _THIS.parent.parent / "config/evaluation/world_partition_semantics.json"
                        )
                        fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
                        if mode == "partition-staged-eval":
                            result = await run_staged_partition_eval(
                                session, settings, fixture=fixture, project_id=project.id
                            )
                        elif bound_diagnostic is not None:
                            result = replay_partition_eval(bound_diagnostic, fixture=fixture)
                            result["replayed_from_report_id"] = bound_diagnostic_id
                        else:
                            result = await run_partition_eval(
                                session,
                                settings,
                                fixture=fixture,
                                project_id=project.id,
                                shadow_judge=shadow_judge,
                            )
                        result["evaluated_scope"] = "synthetic_contrasts_not_book_source"
                        result["book_source_hash"] = world_authority_source_hash(sources)
                    elif mode == "partition-patch":
                        if bound_diagnostic is None:
                            raise ValueError("SOURCE_PATCH_DIAGNOSTIC_ID_REQUIRED")
                        result = await propose_partition_patch(
                            session,
                            settings,
                            sources=sources,
                            diagnostic=bound_diagnostic,
                            project_id=project.id,
                        )
                        if result.get("status") == "candidate_pending_review":
                            reviewer_type = "world_source_patch_candidate"
                    elif mode == "patch-review":
                        if bound_diagnostic is None or bound_candidate is None:
                            raise ValueError("SOURCE_PATCH_DIAGNOSTIC_ID_REQUIRED")
                        result = await review_world_source_patch(
                            session,
                            settings,
                            sources=sources,
                            diagnostic=bound_diagnostic,
                            candidate=bound_candidate,
                            project_id=project.id,
                        )
                    elif mode == "source-patch":
                        if bound_diagnostic is None:
                            raise ValueError("SOURCE_PATCH_DIAGNOSTIC_ID_REQUIRED")
                        result = await propose_world_source_patch(
                            session,
                            settings,
                            sources=sources,
                            diagnostic=bound_diagnostic,
                            project_id=project.id,
                        )
                    else:
                        result = await audit_fn(
                            session,
                            settings,
                            sources=sources,
                            project_id=project.id,
                        )
                    verdict, severity, payload = _report_payload(result)
                    audit_status = str(result.get("status", "review_required"))
                    if mode in {
                        *_PATCH_MODES,
                        "partition-eval",
                        "partition-staged-eval",
                        "world-recompile",
                        "world-revise",
                    } or candidate_binding or audit_status in {
                        "conflict",
                        "review_required",
                    }:
                        exit_code = 4
                except WorldAuthorityAuditError as exc:
                    # Preserve the complete diagnostic, including raw excerpts.
                    payload = dict(exc.diagnostic)
                    verdict, severity = "needs_review", "high"
                    audit_status = str(payload.get("status", "review_required"))
                    diagnostic = payload
                    exit_code = 4
                except Exception as exc:
                    payload = {
                        "version": audit_version,
                        "status": "review_required",
                        "error": type(exc).__name__,
                        "source_hash": world_authority_source_hash(sources),
                    }
                    verdict, severity = "needs_review", "high"
                    audit_status = "review_required"
                    diagnostic = payload
                    exit_code = 4

                if mode in _PATCH_MODES:
                    payload = dict(payload)
                    payload["source_diagnostic_id"] = bound_diagnostic_id
                    payload["source_artifact_id"] = str(artifact.id)
                    if mode == "patch-review":
                        payload["source_candidate_id"] = bound_candidate_id

                if candidate_binding:
                    payload = {**payload, **candidate_binding}
                report = ReviewReportModel(
                    project_id=project.id,
                    target_type="review_report" if candidate_binding else "planning_artifact",
                    target_id=UUID(str(candidate_id)) if candidate_binding else artifact.id,
                    reviewer_type=reviewer_type,
                    verdict=verdict,
                    severity_max=severity,
                    structured_output=payload,
                    llm_run_id=None,
                )
                session.add(report)
                await session.flush()
                output = {
                    "status": "recorded_failure" if diagnostic else "recorded",
                    "mode": requested_mode,
                    "audit_status": audit_status,
                    **summary,
                    "report_id": str(report.id),
                    "verdict": verdict,
                }
                if (mode in {*_PATCH_MODES, "world-recompile", "world-revise", "world-restate"}
                        and isinstance(payload, Mapping)):
                    candidate_hash = payload.get("candidate_hash")
                    if isinstance(candidate_hash, str):
                        output["candidate_hash"] = candidate_hash
    if output is not None:
        print(json.dumps(output, ensure_ascii=False))
    return exit_code


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--slug", required=True)
    parser.add_argument(
        "--execute", action="store_true", help="Run one audit and persist its report"
    )
    parser.add_argument(
        "--mode",
        choices=(
            "source",
            "cost-claims",
            "source-patch",
            "patch-review",
            "partition-patch",
            "partition-eval",
            "partition-staged-eval",
            "world-recompile",
            "world-revise",
            "world-restate",
            "world-restate-anchored",
            "world-restate-disjoint",
            "world-restate-atomic",
        ),
        default="source",
        help="Audit contract to run (default: source)",
    )
    parser.add_argument(
        "--diagnostic-id",
        default=None,
        help=(
            "Bound cost report for source-patch; "
            "optional capability report for offline partition-eval replay"
        ),
    )
    parser.add_argument(
        "--candidate-id", default=None, help="Required by patch-review; bound candidate report UUID"
    )
    parser.add_argument(
        "--revision-file", type=Path, default=None,
        help="JSON path/before/after/rationale proposal; only for world-revise",
    )
    parser.add_argument(
        "--shadow-judge",
        action="store_true",
        help="Use the existing primary shadow judge for a diagnostic partition-eval",
    )
    args = parser.parse_args(argv)
    return asyncio.run(
        run(
            slug=args.slug,
            execute=args.execute,
            mode=args.mode,
            diagnostic_id=args.diagnostic_id,
            candidate_id=args.candidate_id,
            shadow_judge=args.shadow_judge,
            revision_edit=(
                json.loads(args.revision_file.read_text(encoding="utf-8"))
                if args.revision_file is not None else None
            ),
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
