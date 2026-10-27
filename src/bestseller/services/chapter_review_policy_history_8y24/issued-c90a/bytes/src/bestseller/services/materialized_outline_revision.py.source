"""Staged read-only full-outline preproof; parent owns lifecycle orchestration.

This is not a prefix readiness receipt, a grade producer or SEND authorization.
The real shared verifier proves the grade under the same stable running owner.
"""

from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.infra.db import models as db
from bestseller.services import materialized_outline_admission_read as source_read
from bestseller.services import planning_draft_admission as admission
from bestseller.services.budget import (
    ExecutionBudgetError,
    current_execution_scope,
    execution_scope_block_code,
    raise_if_execution_stopped,
)
from bestseller.services.chapter_scene_contract_materializer import (
    chapter_contract_has_adopted_source,
)
from bestseller.services.conception_snapshots import snapshot_hash
from bestseller.services.planner import _without_planning_meta
from bestseller.services.prompt_source_policy import content_hash
from bestseller.services.retained_planning_generation import _Reader
from bestseller.services.retry_ledger import execution_terminal_code, project_execution_scope
from bestseller.settings import AppSettings

REVISION_SCHEMA = "materialized-outline-revision.v1"
READY = "approved_for_materialization"


def _need(condition: object, code: str) -> None:
    if not condition:
        raise ValueError("full_outline_revision:" + code)


def _numbers(candidate: dict) -> list[int]:
    chapters = candidate.get("chapters")
    _need(
        isinstance(chapters, list)
        and bool(chapters)
        and all(isinstance(c, dict) for c in chapters),
        "full_chapter_shape",
    )
    numbers = [c.get("chapter_number") for c in chapters]
    _need(
        all(type(n) is int and n > 0 for n in numbers)
        and numbers == list(range(1, numbers[-1] + 1)),
        "full_chapter_range",
    )
    return numbers


async def validate_full_outline_revision_for_materialization(
    session: AsyncSession,
    settings: AppSettings,
    project: db.ProjectModel | SimpleNamespace,
    artifact: db.PlanningArtifactVersionModel | SimpleNamespace,
) -> dict:
    """Return a real eligible hard receipt before projection, without any writes.

    Accept draft at the approval boundary and approved at the normal materializer
    boundary. Parent rechecks control and this proof after provider gates, before
    taking the existing unadopted reconciliation locks and writing projections.
    """
    with session.no_autoflush:
        reader = _Reader(session)
        current_project = await reader.get(db.ProjectModel, project.id)
        raise_if_execution_stopped()
        _need(
            current_project is not None
            and current_project.status not in {"paused", "archived", "cancelled", "completed"},
            "project_halted",
        )
        scope = project_execution_scope(current_project)
        _need(
            current_execution_scope() == scope
            and execution_terminal_code(current_project.metadata_json) is None,
            "original_scope_required",
        )
        control = await reader.get(db.BookProductionControlModel, project.id)
        _need(control is None or control.desired_state == "run", "control_halted")
        budget = await reader.get(db.ExecutionBudgetModel, scope)
        _need(budget is not None, "original_budget_missing")
        code = execution_scope_block_code(budget)
        if code:
            raise ExecutionBudgetError(code, scope)
        current = await reader.get(db.PlanningArtifactVersionModel, artifact.id)
        _need(
            current_project is not None
            and current is not None
            and current.project_id == artifact.project_id == project.id
            and current.artifact_type == "chapter_outline_batch"
            and current.scope_ref_id is None
            and current.status in {"draft", "approved"}
            and isinstance(current.content, dict)
            and content_hash(artifact.content) == content_hash(current.content),
            "artifact_binding",
        )
        meta = current.content.get("_meta") or {}
        _need(
            isinstance(meta, dict) and "sourcebound_readiness_receipt" not in meta,
            "full_source_not_prefix",
        )
        identity = source_read.admission_workflow_id(project.id, current.id)
        _need(current.source_run_id == identity, "artifact_actual_source_run")
        workflow = await reader.get(db.WorkflowRunModel, identity)
        _need(
            workflow is not None
            and workflow.project_id == project.id
            and workflow.workflow_type == source_read.WORKFLOW_TYPE
            and workflow.scope_type == "planning_artifact"
            and workflow.scope_id == current.id
            and workflow.status == "running"
            and workflow.current_step == READY,
            "ready_owner",
        )
        state = workflow.metadata_json
        _need(
            isinstance(state, dict) and state.get("schema") == source_read.SCHEMA
            and state.get("execution_scope_id") == scope,
            "standard_owner_schema",
        )
        revision = state.get("full_outline_revision")
        _need(
            isinstance(revision, dict)
            and revision.get("schema") == REVISION_SCHEMA
            and revision.get("phase") == READY,
            "revision_phase",
        )
        candidate = _without_planning_meta(current.content)
        _need(
            state.get("source_artifact_id") == str(current.id)
            and state.get("source_content_sha256") == content_hash(current.content)
            and state.get("candidate_hash") == content_hash(candidate),
            "full_candidate_binding",
        )
        try:
            base_id = UUID(revision["base_artifact_id"])
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError("full_outline_revision:base_identity") from error
        base = await reader.get(db.PlanningArtifactVersionModel, base_id)
        _need(
            base is not None
            and base.id != current.id
            and base.project_id == project.id
            and base.artifact_type == current.artifact_type
            and base.scope_ref_id is None
            and base.status == "approved"
            and isinstance(base.content, dict)
            and revision.get("base_source_content_sha256") == content_hash(base.content)
            and revision.get("base_candidate_hash")
            == content_hash(_without_planning_meta(base.content)),
            "base_binding",
        )
        _need(content_hash(candidate) != revision["base_candidate_hash"], "candidate_not_changed")
        numbers = _numbers(candidate)
        _need(
            type(current_project.target_chapters) is int
            and numbers == list(range(1, current_project.target_chapters + 1)),
            "complete_project_target",
        )
        _need(numbers == _numbers(_without_planning_meta(base.content)), "full_source_coverage")
        chapters = await reader.rows(db.ChapterModel, db.ChapterModel.project_id == project.id)
        _need(sorted(c.chapter_number for c in chapters) == numbers, "current_chapter_coverage")
        for chapter in chapters:
            adopted = await chapter_contract_has_adopted_source(session, chapter=chapter)
            _need(type(adopted) is bool and not adopted, "adopted_or_ambiguous")
        receipt = state.get("source_admission")
        _need(
            isinstance(receipt, dict) and receipt.get("draft_eligible") is True, "eligible_receipt"
        )
        canonical_id = (current_project.metadata_json or {}).get("creation_canonical_snapshot_id")
        canonical_hash = (current_project.metadata_json or {}).get(
            "creation_canonical_snapshot_hash"
        )
        _need(
            isinstance(canonical_id, str)
            and state.get("canonical_id") == canonical_id
            and isinstance(canonical_hash, str)
            and state.get("canonical_hash") == canonical_hash,
            "current_canonical_binding",
        )
        canonical = await reader.get(db.PlanningArtifactVersionModel, UUID(canonical_id))
        _need(
            canonical is not None
            and canonical.project_id == project.id
            and canonical.artifact_type == "conception_snapshot"
            and canonical.status == "canonical"
            and canonical.scope_ref_id is None
            and snapshot_hash(canonical.content) == canonical_hash,
            "canonical_source_changed",
        )
        # Reuse current full 4-source binding plus the shared grade's 3-source
        # contract. Neither an approved label nor a completed grade alone suffices.
        sources = await source_read._foundation(session, settings, current_project, receipt, state)
        protocol = state.get("protocol")
        _need(
            isinstance(protocol, dict)
            and protocol.get("status") == "validated"
            and protocol.get("graded_input_hash") == receipt.get("graded_input_hash")
            and protocol.get("candidate_hash") == receipt.get("candidate_hash")
            and content_hash(protocol.get("attempts"))
            == content_hash(receipt.get("protocol_attempts")),
            "same_protocol_journal",
        )
        # One shared input/proof implementation owns schema adapters (including
        # V4 once integrated). Do not independently hash a guessed V3 input.
        # Keep the producer's first-chapter continuity guard before writing.
        verified = await admission._prove(
            session,
            project=current_project,
            workflow_run_id=identity,
            candidate_payload=candidate,
            book_spec=sources["book_spec"],
            world_spec=sources["world_spec"],
            cast_spec=sources["cast_spec"],
            receipt=receipt,
            check_initial_context=True,
            settings=settings,
        )
        _need(
            verified.get("draft_eligible") is True
            and content_hash(verified) == content_hash(receipt),
            "shared_paid_proof_changed",
        )
        return {
            "schema": REVISION_SCHEMA,
            "workflow_run_id": str(identity),
            "source_artifact_id": str(current.id),
            "source_content_sha256": content_hash(current.content),
            "candidate_hash": content_hash(candidate),
            "chapter_numbers": numbers,
            "source_admission": deepcopy(verified),
        }
