"""Review a changed full plan before replacing any unadopted projection.

Draft registration grants review permission only. The actual paid hard review
and the normal materializer keep their own identities, gates and histories.
"""

from copy import deepcopy
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import select, update

from bestseller.domain.workflow import ChapterOutlineBatchInput
from bestseller.infra.db import models as db
from bestseller.services.materialized_outline_source_recovery import (
    SCHEMA as SOURCE_SCHEMA, WORKFLOW_TYPE, _read_sources, admission_workflow_id,
)
from bestseller.services.planner import _without_planning_meta
from bestseller.services.prompt_source_policy import content_hash
from bestseller.services.retained_planning_generation import _Reader

SCHEMA = "materialized-outline-revision.v1"
ADMISSION_SCHEMA = "planning-draft-admission.v4"
READY = "approved_for_materialization"


def _need(condition, code):
    if not condition:
        raise ValueError("outline_revision:" + code)


async def _runnable(session, project):
    from bestseller.services.budget import (
        current_execution_scope, execution_scope_block_code, raise_if_execution_stopped,
    )
    from bestseller.services.retry_ledger import execution_terminal_code, project_execution_scope

    raise_if_execution_stopped()
    fresh = await _Reader(session).get(db.ProjectModel, project.id)
    _need(fresh is not None and fresh.status not in {"paused", "archived", "cancelled", "completed"},
          "project_halted")
    scope = project_execution_scope(fresh)
    _need(current_execution_scope() == scope and execution_terminal_code(fresh.metadata_json) is None,
          "original_scope_required")
    control = await _Reader(session).get(db.BookProductionControlModel, project.id)
    _need(control is None or control.desired_state == "run", "control_halted")
    budget = await _Reader(session).get(db.ExecutionBudgetModel, scope)
    _need(budget is not None and execution_scope_block_code(budget) is None, "original_budget_closed")
    return fresh


async def _unadopted(session, project_id, numbers):
    from bestseller.services.chapter_scene_contract_materializer import chapter_contract_has_adopted_source

    chapters = await _Reader(session).rows(db.ChapterModel, db.ChapterModel.project_id == project_id)
    _need(sorted(c.chapter_number for c in chapters) == numbers, "exact_current_chapter_range")
    for chapter in chapters:
        adopted = await chapter_contract_has_adopted_source(session, chapter=chapter)
        _need(type(adopted) is bool and not adopted, "adopted_or_ambiguous")
    return chapters


async def stage_full_outline_revision(session, settings, *, project_slug, parent_artifact_id,
        parent_materialization_workflow_id, candidate_payload, proposal_receipt,
        actor, authorization_ref, explicit_user_approval=False, admission_schema=ADMISSION_SCHEMA):
    """Append a stable ungraded proposal. No current chapter or old artifact changes."""
    from bestseller.services.pipelines import _checkpoint_commit

    _need(explicit_user_approval is True and bool(actor.strip()) and bool(authorization_ref.strip()),
          "explicit_review_authorization_required")
    _need(admission_schema in {ADMISSION_SCHEMA, "planning-draft-admission.v5"},
          "revision_admission_schema_unsupported")
    _need(not session.in_nested_transaction(), "outer_checkpoint_required")
    project, parent, old_candidate, foundation, binding = await _read_sources(
        session, settings, project_slug=project_slug, artifact_id=parent_artifact_id,
        materialization_workflow_id=parent_materialization_workflow_id)
    project = await _runnable(session, project)
    candidate = deepcopy(_without_planning_meta(candidate_payload))
    typed = ChapterOutlineBatchInput.model_validate(candidate)
    numbers = [c.chapter_number for c in typed.chapters]
    _need(numbers == list(range(1, project.target_chapters + 1))
          and numbers == [c["chapter_number"] for c in old_candidate["chapters"]],
          "unchanged_full_chapter_inventory")
    _need(content_hash(candidate) != content_hash(old_candidate), "actual_candidate_change_required")
    # Structural metadata alone cannot buy another protocol under a fresh UUID.
    from bestseller.services.planning_admission_ref_protocol import require_changed_candidate
    from bestseller.services import planning_draft_admission as admission
    supplied = await admission._input(session, project=project,
        workflow_run_id=admission_workflow_id(project.id, parent.id),
        candidate_payload=old_candidate, book_spec=foundation["book_spec"].content,
        world_spec=foundation["world_spec"].content, cast_spec=foundation["cast_spec"].content,
        commercial_report=None, check_initial_context=True)
    new_supplied = {**deepcopy(supplied), "candidate_payload": candidate,
        "projection_audit_targets": admission.projection_audit_targets(candidate)}
    identity = uuid5(NAMESPACE_URL,
        f"bestseller:{SCHEMA}:{parent.id}:{content_hash(candidate)}")
    changed = require_changed_candidate(supplied, new_supplied,
        previous_artifact_id=parent.id, current_artifact_id=identity)
    workflow_id = admission_workflow_id(project.id, identity)
    revision = {"schema": SCHEMA, "phase": "review_pending",
        "base_artifact_id": str(parent.id), "base_source_content_sha256": content_hash(parent.content),
        "base_candidate_hash": content_hash(old_candidate),
        "base_materialization_workflow_run_id": str(parent_materialization_workflow_id),
        "base_binding": deepcopy(binding), "changed_candidate": changed,
        "proposal_receipt": deepcopy(proposal_receipt), "proposal_receipt_sha256": content_hash(proposal_receipt),
        "actor": actor, "authorization_ref": authorization_ref,
        "admission_schema": admission_schema, "grade_asserted": False, "write_authorized": False}
    content = {**candidate, "_meta": {"full_outline_revision": {
        "schema": SCHEMA, "base_artifact_id": str(parent.id),
        "proposal_receipt_sha256": content_hash(proposal_receipt),
        "grade_asserted": False, "write_authorized": False}}}
    existing = await _Reader(session).get(db.PlanningArtifactVersionModel, identity)
    if existing is not None:
        _need(existing.project_id == project.id and existing.source_run_id == workflow_id
              and content_hash(existing.content) == content_hash(content), "existing_proposal_changed")
        owner = await _Reader(session).get(db.WorkflowRunModel, workflow_id)
        _need(owner is not None and owner.project_id == project.id
              and owner.workflow_type == WORKFLOW_TYPE and owner.scope_type == "planning_artifact"
              and owner.scope_id == identity and owner.status not in {"cancelled", "paused", "stopping"},
              "existing_proposal_owner_changed")
        saved = owner.metadata_json or {}
        _need((saved.get("full_outline_revision") or {}).get("admission_schema") == admission_schema,
              "existing_proposal_schema_pinned")
        from bestseller.services.budget import current_execution_scope
        from bestseller.services.retry_ledger import project_execution_scope
        _need(isinstance(saved.get("execution_scope_id"), str)
              and saved["execution_scope_id"] == current_execution_scope()
              == project_execution_scope(project), "original_scope_changed")
        _need(saved.get("source_content_sha256") == content_hash(content)
              and saved.get("candidate_hash") == content_hash(candidate)
              and content_hash(saved.get("foundation")) == content_hash(binding["foundation"])
              and saved.get("canonical_id") == binding["canonical_id"]
              and saved.get("canonical_hash") == binding["canonical_hash"],
              "existing_proposal_dependencies_changed")
        return {"artifact_id": str(identity), "workflow_run_id": str(workflow_id), "reused": True}
    await session.scalar(select(db.BookProductionControlModel.project_id).where(
        db.BookProductionControlModel.project_id == project.id).with_for_update())
    await session.scalar(select(db.ProjectModel.id).where(db.ProjectModel.id == project.id).with_for_update())
    await _runnable(session, project)
    _, _, _, _, after = await _read_sources(session, settings, project_slug=project_slug,
        artifact_id=parent.id, materialization_workflow_id=parent_materialization_workflow_id)
    _need(content_hash(after) == content_hash(binding), "base_changed_during_registration")
    await _unadopted(session, project.id, numbers)
    versions = await session.scalars(select(db.PlanningArtifactVersionModel.version_no).where(
        db.PlanningArtifactVersionModel.project_id == project.id,
        db.PlanningArtifactVersionModel.artifact_type == "chapter_outline_batch",
        db.PlanningArtifactVersionModel.scope_ref_id.is_(None)))
    version = max(list(versions), default=0) + 1
    from bestseller.services.budget import current_execution_scope
    state = {"schema": SOURCE_SCHEMA, **deepcopy(binding),
        "execution_scope_id": current_execution_scope(),
        "source_artifact_id": str(identity), "source_content_sha256": content_hash(content),
        "candidate_hash": content_hash(candidate), "protocol": {}, "full_outline_revision": revision}
    state.pop("materialization_workflow_run_id", None)
    workflow = db.WorkflowRunModel(id=workflow_id, project_id=project.id, workflow_type=WORKFLOW_TYPE,
        scope_type="planning_artifact", scope_id=identity, status="running",
        current_step="review_revision_before_materialization", requested_by=actor,
        idempotency_key=f"{WORKFLOW_TYPE}:{identity}", metadata_json=state)
    artifact = db.PlanningArtifactVersionModel(id=identity, project_id=project.id,
        artifact_type="chapter_outline_batch", scope_ref_id=None, version_no=version,
        schema_version="1.0", status="draft", content=content, source_run_id=workflow_id,
        notes="Unadopted full-source revision proposal; no grade or projection authority")
    session.add_all([workflow, artifact])
    await _checkpoint_commit(session)
    return {"artifact_id": str(identity), "workflow_run_id": str(workflow_id), "reused": False}


async def review_and_apply_full_outline_revision(session, settings, *, artifact_id, actor):
    """Resume the same paid review and materializer. A false/unknown grade never writes plans."""
    from bestseller.services import planning_draft_admission as admission
    from bestseller.services.execution_error_diagnostics import safe_error_chain
    from bestseller.services.foundation_selection import load_selected_foundation_artifacts
    from bestseller.services.materialized_outline_revision import validate_full_outline_revision_for_materialization
    from bestseller.services.model_context_capacity import bind_selected_model_capacity
    from bestseller.services.pipelines import _checkpoint_commit
    from bestseller.services.workflows import materialize_chapter_outline_batch
    from bestseller.services.llm import bind_execution_operation

    _need(not session.in_nested_transaction(), "outer_checkpoint_required")
    artifact = await _Reader(session).get(db.PlanningArtifactVersionModel, UUID(str(artifact_id)))
    _need(artifact is not None and artifact.artifact_type == "chapter_outline_batch", "artifact_missing")
    project = await _Reader(session).get(db.ProjectModel, artifact.project_id)
    workflow_id = admission_workflow_id(project.id, artifact.id)
    workflow = await _Reader(session).get(db.WorkflowRunModel, workflow_id)
    _need(workflow is not None and workflow.project_id == project.id
          and artifact.source_run_id == workflow_id, "same_actual_review_owner")
    from bestseller.services.budget import current_execution_scope
    from bestseller.services.retry_ledger import project_execution_scope
    state = deepcopy(workflow.metadata_json or {})
    _need(isinstance(state.get("execution_scope_id"), str)
          and state["execution_scope_id"] == current_execution_scope()
          == project_execution_scope(project), "original_scope_changed")
    if workflow.status == "completed":
        from bestseller.services.materialized_outline_admission_read import require_materialized_outline_source_admission
        return await require_materialized_outline_source_admission(session, settings,
            project=project, artifact=artifact)
    _need(workflow.status in {"running", "failed", "machine_blocked"}, "workflow_halted")
    project = await _runnable(session, project)
    observed = {key: deepcopy(getattr(workflow, key))
        for key in ("status", "current_step", "error_message", "metadata_json")}
    revision = state.get("full_outline_revision")
    _need(isinstance(revision, dict) and revision.get("schema") == SCHEMA
          and revision.get("admission_schema") in {ADMISSION_SCHEMA, "planning-draft-admission.v5"},
          "revision_protocol_pinned")
    candidate = _without_planning_meta(artifact.content)
    _need(state.get("candidate_hash") == content_hash(candidate)
          and state.get("source_content_sha256") == content_hash(artifact.content), "candidate_changed")
    numbers = [c["chapter_number"] for c in candidate["chapters"]]
    await _unadopted(session, project.id, numbers)
    foundation = await load_selected_foundation_artifacts(session, project=project, settings=settings)
    _need(foundation is not None and {k: {"id": str(v.id), "content_hash": content_hash(v.content)}
        for k, v in foundation.items()} == state["foundation"], "current_foundation_changed")

    async def persist(*, status=None, step=None):
        nonlocal observed
        current = await _Reader(session).get(db.WorkflowRunModel, workflow_id)
        _need(current is not None and current.project_id == project.id
              and current.workflow_type == WORKFLOW_TYPE and current.scope_type == "planning_artifact"
              and current.scope_id == artifact.id, "workflow_identity_changed")
        _need(all(getattr(current, key) == value for key, value in observed.items()),
              "workflow_changed_during_review")
        values = {"metadata_json": deepcopy(state)}
        if status is not None:
            values.update(status=status, current_step=step, error_message=None)
        with session.no_autoflush:
            changed = await session.execute(update(db.WorkflowRunModel).where(
                db.WorkflowRunModel.id == workflow_id, db.WorkflowRunModel.status == current.status,
                db.WorkflowRunModel.current_step == current.current_step,
                db.WorkflowRunModel.error_message == current.error_message,
                db.WorkflowRunModel.metadata_json == current.metadata_json).values(**values)
                .execution_options(synchronize_session=False))
        _need(changed.rowcount == 1, "workflow_changed_during_checkpoint")
        await _checkpoint_commit(session)
        observed = {**observed, **deepcopy(values)}

    try:
        if workflow.status in {"failed", "machine_blocked"}:
            history = state.setdefault("previous_revision_failures", [])
            previous = {key: deepcopy(observed[key])
                for key in ("status", "current_step", "error_message")}
            if previous not in history:
                history.append(previous)
            await persist(status="running", step=(READY if revision["phase"] == READY
                else "review_revision_before_materialization"))
        if revision["phase"] != READY:
            await persist(status="running", step="review_revision_before_materialization")
            with bind_selected_model_capacity(project.metadata_json):
                receipt = await admission.assess_planning_draft_admission(session, settings,
                    project=project, workflow_run_id=workflow_id, candidate_payload=candidate,
                    book_spec=foundation["book_spec"].content, world_spec=foundation["world_spec"].content,
                    cast_spec=foundation["cast_spec"].content,
                    commercial_report={"schema": "historical-plan-review-observations.v1",
                        **deepcopy(revision["base_binding"]["historical_editorial_observations"])},
                    protocol_state=state["protocol"], persist_protocol=persist,
                    admission_schema=revision["admission_schema"])
            state["source_admission"] = deepcopy(receipt)
            if receipt.get("draft_eligible") is not True:
                revision["phase"] = "source_repair_required"
                await persist(status="machine_blocked", step="source_repair_required")
                return {"workflow_run_id": str(workflow_id), "source_admission": deepcopy(receipt),
                    "materialized": False}
            revision["phase"] = READY
            await persist(status="running", step=READY)
        project = await _runnable(session, project)
        await validate_full_outline_revision_for_materialization(session, settings,
            project=project, artifact=artifact)
        # Approval and projection are one transaction, after the paid grade is
        # durable. Provider gates keep their ordinary evidence and stop rules.
        with session.no_autoflush:
            approved = await session.execute(update(db.PlanningArtifactVersionModel).where(
                db.PlanningArtifactVersionModel.id == artifact.id,
                db.PlanningArtifactVersionModel.status.in_(["draft", "approved"]),
                db.PlanningArtifactVersionModel.content == artifact.content,
            ).values(status="approved").execution_options(synchronize_session=False))
        _need(approved.rowcount == 1, "proposal_changed_before_approval")
        with bind_execution_operation(f"full-outline-materialization:{artifact.id}"):
            materialized = await materialize_chapter_outline_batch(session, project.slug,
                ChapterOutlineBatchInput.model_validate(candidate), source_artifact_id=artifact.id,
                reconcile_unadopted_chapter_numbers=numbers, prune_missing_planned=False,
                requested_by=actor)
        await session.flush()
        project, current, _, _, binding = await _read_sources(session, settings,
            project_slug=project.slug, artifact_id=artifact.id,
            materialization_workflow_id=materialized.workflow_run_id)
        await _runnable(session, project)
        state.update(deepcopy(binding))
        # A changed plan does not rebind old instructions to the retained body.
        # Append ungraded alignment tasks, retaining old task attempts/history.
        from bestseller.services.rewrite_outline_binding import stage_outline_revision_rewrite_tasks
        from bestseller.services.rewrite_patch import text_hash
        await session.flush()
        alignments = []
        for chapter in await _Reader(session).rows(db.ChapterModel,
                db.ChapterModel.project_id == project.id):
            drafts = await _Reader(session).rows(db.ChapterDraftVersionModel,
                db.ChapterDraftVersionModel.project_id == project.id,
                db.ChapterDraftVersionModel.chapter_id == chapter.id,
                db.ChapterDraftVersionModel.is_current.is_(True))
            _need(len(drafts) <= 1, "ambiguous_current_body")
            if drafts:
                draft = drafts[0]
                alignments.append(await stage_outline_revision_rewrite_tasks(session,
                    project_id=project.id, chapter_id=chapter.id, draft_id=draft.id,
                    expected_body_sha256=text_hash(draft.content_md or ""),
                    expected_plan_binding=chapter.metadata_json["outline_source_admission"]))
        revision["source_alignment_tasks"] = alignments
        revision["phase"] = "materialized"
        state["grade_granted_to_old_editorial_reports"] = False
        await persist(status="completed", step="current_source_admitted")
        return {"workflow_run_id": str(workflow_id), "source_admission": deepcopy(state["source_admission"]),
            "source_artifact_id": str(current.id), "materialization_workflow_run_id": str(materialized.workflow_run_id),
            "materialized": True}
    except BaseException as error:
        if isinstance(error, admission.PlanningDraftAdmissionProtocolError):
            revision["phase"] = "protocol_unknown"
            state["failure_diagnostics"] = safe_error_chain(error)
            await persist(status="machine_blocked", step=error.code)
        from bestseller.services.execution_stage import attach_workflow_failure
        attach_workflow_failure(error, workflow)
        raise
