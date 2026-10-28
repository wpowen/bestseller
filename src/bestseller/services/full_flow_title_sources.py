"""Read native sources before a fresh normal-planner title request.

No workflow, grant, budget, paid request, transaction or export is created here.
The caller owns transaction isolation and the normal planner's paid boundary.
The returned snapshot joins verified native sources in memory; the original
adopted assembly and every persisted source/receipt remain unchanged.
"""

from copy import deepcopy
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import inspect, select
from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.domain.book_design_snapshot import BookDesignSnapshot
from bestseller.infra.db import models as db
from bestseller.services.budget import (
    ExecutionBudgetError,
    current_execution_scope,
    execution_scope_block_code,
    raise_if_execution_stopped,
)
from bestseller.services.fanqie_short_plan_resume import read_existing_complete_fanqie_plan
from bestseller.services.fanqie_short_source_export import read_adopted_assembly
from bestseller.services.native_title_operator import title_operator as op
from bestseller.services.production_control import ProductionControlHaltedError, load_control_state
from bestseller.services.retry_ledger import assert_execution_scope_open

_OWNER_TYPES = frozenset({"project_pipeline", "autowrite"})
_OPEN_TASKS = frozenset({"pending", "queued", "processing", "running", "stopping", "in_progress"})
_SOURCES = (db.ProjectModel, db.WorkflowRunModel, db.PlanningArtifactVersionModel,
            db.ChapterModel, db.SceneCardModel, db.ChapterDraftVersionModel,
            db.BookProductionControlModel, db.RewriteTaskModel,
            db.BookPackagingContractModel, db.ChapterStateSnapshotModel)


@dataclass(frozen=True)
class NativeTitleSources:
    project: db.ProjectModel
    native_plan: dict
    assembly: object
    snapshot: dict
    owner_workflow_id: UUID | None


def _need(value, reason):
    if not value:
        raise ValueError("native_title_sources:" + reason)


def _reject_pending(session, project_id, scope=None):
    for row in (*session.new, *session.dirty, *session.deleted):
        owned = (isinstance(row, db.ProjectModel) and row.id == project_id) or (
            isinstance(row, _SOURCES) and getattr(row, "project_id", None) == project_id
        ) or (isinstance(row, db.ExecutionBudgetModel) and row.scope_id == scope)
        if owned and (row in session.new or row in session.deleted
                      or session.is_modified(row, include_collections=True)):
            raise ValueError("native_title_sources:pending_source_state")


async def _permission(session, project_id, scope):
    raise_if_execution_stopped()
    budget = await session.get(db.ExecutionBudgetModel, scope, populate_existing=True)
    if budget is None:
        raise ExecutionBudgetError("execution_scope_missing", scope)
    # The native policy handles completion-bound authorization even when old
    # raw status/max/deadline fields remain exhausted. Never reconstruct credit.
    await assert_execution_scope_open(session, scope)
    control = await load_control_state(session, project_id)
    if control.halted:
        raise ProductionControlHaltedError(control)
    return execution_scope_block_code(budget) is None and not control.halted


async def _owner_id(session, project_id, scope, owner):
    if owner is None:
        return None
    _need(isinstance(owner, db.WorkflowRunModel), "owner_workflow_object_required")
    state = inspect(owner)
    _need(state.persistent and state.session is session.sync_session, "owner_workflow_not_persistent")
    _need(owner not in session.dirty and owner not in session.deleted, "pending_owner_workflow")
    fresh = await session.scalar(select(db.WorkflowRunModel).where(
        db.WorkflowRunModel.id == owner.id).execution_options(populate_existing=True))
    _need(fresh is owner and fresh.project_id == project_id
          and fresh.scope_type == "project" and fresh.scope_id == project_id
          and fresh.workflow_type in _OWNER_TYPES and fresh.status == "running"
          and (fresh.metadata_json or {}).get("execution_scope_id") == scope,
          "owner_workflow_scope_or_status")
    return fresh.id


async def _census(session, project_id, owner_id):
    workflows = list(await session.scalars(select(db.WorkflowRunModel).where(
        db.WorkflowRunModel.project_id == project_id).order_by(db.WorkflowRunModel.id)
        .execution_options(populate_existing=True)))
    open_workflows = op.active_workflows([
        {"id": str(row.id), "status": row.status} for row in workflows if row.id != owner_id
    ])
    _need(not open_workflows, "open_workflow")
    tasks = list(await session.scalars(select(db.RewriteTaskModel).where(
        db.RewriteTaskModel.project_id == project_id).order_by(db.RewriteTaskModel.id)
        .execution_options(populate_existing=True)))
    open_tasks = [{"id": str(row.id), "status": row.status} for row in tasks if row.status in _OPEN_TASKS]
    _need(not open_tasks, "open_rewrite_task")
    return open_workflows, open_tasks


def _design(project):
    meta = project.metadata_json or {}
    raw = meta.get("book_design_snapshot")
    _need(isinstance(raw, dict), "design_snapshot_required")
    parsed = BookDesignSnapshot.model_validate(raw)
    verified = (parsed.source_hash == parsed.canonical_hash() == meta.get("book_design_snapshot_hash")
                and parsed.snapshot_id == parsed.source_hash[:16] == meta.get("book_design_snapshot_id"))
    _need(verified and meta.get("book_design_snapshot_status") == "locked", "design_snapshot_not_verified")
    _need(parsed.chapter_budget.total_chapters == project.target_chapters, "design_chapter_count")
    return verified


async def read_current_title_sources(session, settings, *, project_id, owner_workflow=None):
    """Acquire a v2 source fence; permit None only with no other active workflow.

    A supplied owner is the persistent normal project workflow in this session,
    not an ignore-UUID capability. Chapter workflows are never exempted. A
    missing complete native plan or adoption/knowledge proof remains an error.
    """
    _need(isinstance(session, AsyncSession), "actual_async_session_required")
    project_id = UUID(str(project_id))
    _reject_pending(session, project_id)
    with session.no_autoflush:
        project = await session.scalar(select(db.ProjectModel).where(
            db.ProjectModel.id == project_id).execution_options(populate_existing=True))
        _need(project is not None, "project_required")
        _need(type(project.target_chapters) is int and 4 <= project.target_chapters <= 8, "segment_count")
        meta = project.metadata_json or {}
        scope = meta.get("execution_scope_id")
        _need(isinstance(scope, str) and bool(scope.strip())
              and current_execution_scope() in (None, scope), "execution_scope_changed")
        _reject_pending(session, project_id, scope)
        admissible = await _permission(session, project_id, scope)
        owner_id = await _owner_id(session, project_id, scope, owner_workflow)
        open_workflows, open_tasks = await _census(session, project_id, owner_id)
        design_verified = _design(project)

        plan_receipt = await read_existing_complete_fanqie_plan(session, settings, project=project)
        _need(isinstance(plan_receipt, dict)
              and plan_receipt.get("schema") == "fanqie-materialized-plan-reuse.v1", "complete_native_plan_required")
        artifact = await session.scalar(select(db.PlanningArtifactVersionModel).where(
            db.PlanningArtifactVersionModel.id == UUID(plan_receipt["source_artifact_id"]),
            db.PlanningArtifactVersionModel.project_id == project_id,
        ).execution_options(populate_existing=True))
        _need(artifact is not None, "complete_native_plan_source_required")
        binding = plan_receipt["source_binding"]
        _need(binding["source_artifact_id"] == str(artifact.id)
              and binding["source_content_sha256"] == op.fingerprint(artifact.content), "native_plan_changed")
        # The normal producer legitimately stores source_run_id=None. Keep it
        # literal; independent materialization/admission custody is re-read,
        # never copied into the producer-run field or manufactured as a grade.
        materializer = await session.scalar(select(db.WorkflowRunModel).where(
            db.WorkflowRunModel.id == UUID(str(plan_receipt["outline_workflow_run_id"])),
            db.WorkflowRunModel.project_id == project_id,
        ).execution_options(populate_existing=True))
        admission = await session.scalar(select(db.WorkflowRunModel).where(
            db.WorkflowRunModel.id == UUID(str(plan_receipt["hard_admission_workflow_run_id"])),
            db.WorkflowRunModel.project_id == project_id,
        ).execution_options(populate_existing=True))
        _need(materializer is not None and admission is not None, "native_plan_workflow_sources_required")
        def workflow_export(row):
            return {"id": str(row.id), "project_id": str(row.project_id), "workflow_type": row.workflow_type,
                "status": row.status, "scope_type": row.scope_type,
                "scope_id": str(row.scope_id) if row.scope_id is not None else None,
                "metadata_json": deepcopy(row.metadata_json or {})}

        receipt_export = deepcopy(plan_receipt)
        for key in ("planning_workflow_run_id", "outline_workflow_run_id", "hard_admission_workflow_run_id",
                    "story_bible_workflow_run_id", "narrative_graph_workflow_run_id"):
            if receipt_export.get(key) is not None:
                receipt_export[key] = str(receipt_export[key])
        native_plan = {"id": str(artifact.id), "project_id": str(artifact.project_id),
            "artifact_type": artifact.artifact_type, "status": artifact.status,
            "source_run_id": str(artifact.source_run_id) if artifact.source_run_id is not None else None,
            "content": deepcopy(artifact.content), "source_binding": deepcopy(binding),
            "complete_plan_receipt": receipt_export, "materialization_workflow": workflow_export(materializer),
            "admission_workflow": workflow_export(admission)}
        assembly = await read_adopted_assembly(session, project_id, lock=False)
        canonical = await session.scalar(select(db.PlanningArtifactVersionModel).where(
            db.PlanningArtifactVersionModel.id == UUID(meta["creation_canonical_snapshot_id"]),
            db.PlanningArtifactVersionModel.project_id == project_id,
        ).execution_options(populate_existing=True))
        _need(canonical is not None, "canonical_snapshot_required")
        chapters = list(await session.scalars(select(db.ChapterModel).where(
            db.ChapterModel.project_id == project_id).order_by(db.ChapterModel.chapter_number)
            .execution_options(populate_existing=True)))
        drafts = list(await session.scalars(select(db.ChapterDraftVersionModel).where(
            db.ChapterDraftVersionModel.project_id == project_id).order_by(db.ChapterDraftVersionModel.id)
            .execution_options(populate_existing=True)))
        packaging = list(await session.scalars(select(db.BookPackagingContractModel).where(
            db.BookPackagingContractModel.project_id == project_id).execution_options(populate_existing=True)))

        # This detached join preserves the raw assembly. A marker must exist on
        # the actual selected chapter; no marker is invented from plan counts.
        manifest = deepcopy(assembly.manifest)
        by_id = {str(row.id): row for row in chapters}
        for source in manifest["sources"]:
            chapter = by_id.get(source["chapter_id"])
            _need(chapter is not None, "native_chapter_source_required")
            cm = chapter.metadata_json or {}
            markers = [cm[key] for key in ("fanqie_outline_source_selection", "outline_source_admission") if key in cm]
            _need(len(markers) == 1, "native_selected_plan_required")
            lineage = source["lineage"]
            _need("source_plan_binding" not in lineage or lineage["source_plan_binding"] == markers[0],
                  "native_adopted_plan_changed")
            lineage["source_plan_binding"] = deepcopy(markers[0])

        # Re-read operational guards after the native readers, before exposing
        # the source fence to the normal title caller. No transaction is ended.
        admissible = await _permission(session, project_id, scope)
        await _owner_id(session, project_id, scope, owner_workflow)
        open_workflows, open_tasks = await _census(session, project_id, owner_id)
        snapshot = {
            "schema": "title-operator-source.v2", "project_id": str(project_id), "project_slug": project.slug,
            "manifest": manifest, "full_text": assembly.full_text,
            "knowledge_closed": [source["chapter_number"] for source in manifest["sources"]],
            "canonical_snapshot": {"id": str(canonical.id), "project_id": str(canonical.project_id),
                "artifact_type": canonical.artifact_type, "status": canonical.status, "content": deepcopy(canonical.content)},
            "book_design_snapshot": deepcopy(meta["book_design_snapshot"]),
            "old_metadata": {key: deepcopy(meta[key]) for key in op.OLD_FIELDS if key in meta},
            "design_hash_verified": design_verified,
            "chapters": [{"id": str(c.id), "project_id": str(c.project_id), "chapter_number": c.chapter_number,
                "status": c.status, "production_state": c.production_state, "metadata": deepcopy(c.metadata_json or {})}
                for c in chapters],
            "drafts": [{"id": str(d.id), "project_id": str(d.project_id), "chapter_id": str(d.chapter_id),
                "version_no": d.version_no, "promotion_state": d.promotion_state, "is_current": d.is_current,
                "body_sha256": op.sha(d.content_md or "")} for d in drafts],
            "packaging_rows": [str(row.id) for row in packaging],
            "active_packaging_contract_id": meta.get("active_packaging_contract_id"),
            "open_workflows": open_workflows,
            "open_rewrite_tasks": open_tasks,
            "dispatch_admissible": admissible, "adoption_admissible": admissible,
        }
        op.check_current_snapshot(snapshot, deepcopy(manifest), actual_project=project, native_plan=native_plan)
        return NativeTitleSources(project, native_plan, assembly, snapshot, owner_id)
