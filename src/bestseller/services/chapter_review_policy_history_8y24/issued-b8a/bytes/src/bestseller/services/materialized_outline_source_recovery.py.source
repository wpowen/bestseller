"""Assess the current source eligibility of an unchanged materialized outline.

Legacy editorial observations remain historical. A genuine current hard-source
review is distinct from them and never promotes a draft or rewrites an artifact.
The artifact owns one stable workflow and the shared protocol owns its attempts.
"""

from copy import deepcopy
from uuid import UUID, NAMESPACE_URL, uuid5

from sqlalchemy import select, update

from bestseller.infra.db.models import (
    BookProductionControlModel,
    ExecutionBudgetModel,
    ChapterModel,
    PlanningArtifactVersionModel,
    ProjectModel,
    SceneCardModel,
    WorkflowRunModel,
)
from bestseller.services.prompt_source_policy import content_hash

SCHEMA = "materialized-outline-source-admission.v1"
WORKFLOW_TYPE = "materialized_outline_source_admission"


def admission_workflow_id(project_id, artifact_id):
    return uuid5(NAMESPACE_URL, f"bestseller:{SCHEMA}:{project_id}:{artifact_id}")


def _need(condition, code):
    if not condition:
        raise ValueError("materialized_outline_source:" + code)


async def _read_sources(
    session, settings, *, project_slug, artifact_id, materialization_workflow_id
):
    from bestseller.services.chapter_outline_readiness import (
        _verify_materialized_content,
        outline_prefix_projection_hash,
    )
    from bestseller.services.foundation_selection import load_selected_foundation_artifacts
    from bestseller.services.planner import _without_planning_meta
    from bestseller.services.retained_planning_generation import _Reader

    with session.no_autoflush:
        reader = _Reader(session)
        pid = await session.scalar(select(ProjectModel.id).where(ProjectModel.slug == project_slug))
        _need(pid is not None, "project_missing")
        project = await reader.get(ProjectModel, pid)
        artifact = await reader.get(PlanningArtifactVersionModel, UUID(str(artifact_id)))
        owner = await reader.get(WorkflowRunModel, UUID(str(materialization_workflow_id)))
        _need(
            artifact is not None
            and artifact.project_id == pid
            and artifact.artifact_type == "chapter_outline_batch"
            and artifact.status == "approved"
            and isinstance(artifact.content, dict),
            "approved_source_required",
        )
        _need(
            owner is not None
            and owner.project_id == pid
            and owner.workflow_type == "materialize_chapter_outline_batch"
            and owner.status == "completed"
            and owner.scope_type == "planning_artifact"
            and owner.scope_id == artifact.id
            and (owner.metadata_json or {}).get("source_artifact_id") == str(artifact.id),
            "materialization_owner",
        )
        candidate = _without_planning_meta(artifact.content)
        expected = candidate.get("chapters")
        _need(
            isinstance(expected, list)
            and bool(expected)
            and all(isinstance(c, dict) for c in expected),
            "chapter_shape",
        )
        numbers = [c.get("chapter_number") for c in expected]
        _need(
            all(type(n) is int and n > 0 for n in numbers)
            and numbers == list(range(numbers[0], numbers[-1] + 1)),
            "chapter_range",
        )
        chapters = await reader.rows(
            ChapterModel, ChapterModel.project_id == pid, ChapterModel.chapter_number.in_(numbers)
        )
        _need(sorted(c.chapter_number for c in chapters) == numbers, "chapter_coverage")
        scenes = await reader.rows(
            SceneCardModel,
            SceneCardModel.project_id == pid,
            SceneCardModel.chapter_id.in_([c.id for c in chapters]),
        )
        projections = {}
        by_number = {c["chapter_number"]: c for c in expected}
        for chapter in chapters:
            current_scenes = sorted(
                [s for s in scenes if s.chapter_id == chapter.id], key=lambda s: s.scene_number
            )
            _verify_materialized_content(chapter, current_scenes, by_number[chapter.chapter_number])
            projections[str(chapter.id)] = outline_prefix_projection_hash(chapter, current_scenes)
        selected = await load_selected_foundation_artifacts(
            session, project=project, settings=settings
        )
        _need(selected is not None, "selected_foundation_required")
        foundation = {}
        for kind, source in selected.items():
            fresh = await reader.get(PlanningArtifactVersionModel, source.id)
            _need(
                fresh is not None
                and fresh.project_id == pid
                and fresh.status == "approved"
                and fresh.artifact_type == kind
                and content_hash(fresh.content) == content_hash(source.content),
                "selected_foundation_changed",
            )
            foundation[kind] = fresh
        reports = {
            key: deepcopy((owner.metadata_json or {}).get(key))
            for key in ("outline_llm_commercial_judge", "outline_reader_experience_judge")
        }
        binding = {
            "source_artifact_id": str(artifact.id),
            "source_content_sha256": content_hash(artifact.content),
            "candidate_hash": content_hash(candidate),
            "materialization_workflow_run_id": str(owner.id),
            "chapter_projection_hashes": projections,
            "foundation": {
                kind: {"id": str(row.id), "content_hash": content_hash(row.content)}
                for kind, row in foundation.items()
            },
            "canonical_id": (project.metadata_json or {}).get("creation_canonical_snapshot_id"),
            "canonical_hash": (project.metadata_json or {}).get("creation_canonical_snapshot_hash"),
            "historical_editorial_observations": reports,
        }
        return project, artifact, candidate, foundation, binding


async def assess_materialized_outline_source(
    session,
    settings,
    *,
    project_slug,
    artifact_id,
    materialization_workflow_id,
    requested_by="system",
):
    """Explicit original-plan recovery. A valid hard rejection is retained, not redrawn."""
    from bestseller.services.budget import current_execution_scope, raise_if_execution_stopped
    from bestseller.services.retry_ledger import (
        project_execution_scope,
    )
    from bestseller.services.pipelines import _checkpoint_commit
    from bestseller.services.planning_draft_admission import assess_planning_draft_admission
    from bestseller.services.model_context_capacity import bind_selected_model_capacity

    _need(not session.in_nested_transaction(), "outer_checkpoint_required")
    _need(
        not any(
            isinstance(
                row,
                (
                    ProjectModel,
                    PlanningArtifactVersionModel,
                    ChapterModel,
                    SceneCardModel,
                    BookProductionControlModel,
                ),
            )
            and (row in session.new or session.is_modified(row))
            for row in tuple(session.new) + tuple(session.dirty)
        ),
        "pending_source_state",
    )
    project, artifact, candidate, foundation, binding = await _read_sources(
        session,
        settings,
        project_slug=project_slug,
        artifact_id=artifact_id,
        materialization_workflow_id=materialization_workflow_id,
    )
    pid, aid = project.id, artifact.id
    scope = project_execution_scope(project)
    raise_if_execution_stopped()
    _need(current_execution_scope() == scope, "bound_original_scope_required")
    _need(project.status not in {"paused", "archived", "cancelled", "completed"}, "project_halted")
    control = await session.get(BookProductionControlModel, pid, populate_existing=True)
    _need(control is None or control.desired_state == "run", "control_halted")
    from bestseller.services.retained_planning_generation import _Reader
    from bestseller.services.budget import ExecutionBudgetError, execution_scope_block_code

    budget = await _Reader(session).get(ExecutionBudgetModel, scope)
    _need(budget is not None, "original_ledger_missing")
    code = execution_scope_block_code(budget)
    if code:
        raise ExecutionBudgetError(code, scope)
    identity = admission_workflow_id(pid, aid)
    workflow = await session.get(WorkflowRunModel, identity, populate_existing=True)
    new_owner = workflow is None
    if workflow is None:
        workflow = WorkflowRunModel(
            id=identity,
            project_id=pid,
            workflow_type=WORKFLOW_TYPE,
            scope_type="planning_artifact",
            scope_id=aid,
            status="running",
            current_step="assess_current_source",
            requested_by=requested_by,
            idempotency_key=f"{WORKFLOW_TYPE}:{aid}",
            metadata_json={"schema": SCHEMA, **binding, "protocol": {}},
        )
        session.add(workflow)
    else:
        _need(
            workflow.project_id == pid
            and workflow.workflow_type == WORKFLOW_TYPE
            and workflow.scope_type == "planning_artifact"
            and workflow.scope_id == aid,
            "workflow_identity_changed",
        )
        _need(workflow.status not in {"cancelled", "stopping", "paused"}, "workflow_halted")
        _need(
            all(
                content_hash((workflow.metadata_json or {}).get(k)) == content_hash(v)
                for k, v in binding.items()
            ),
            "original_dependency_changed",
        )
    # Keep exact owner identity available if a failed checkpoint expires the ORM row.
    workflow._failure_identity = {
        "id": identity, "project_id": pid, "workflow_type": WORKFLOW_TYPE,
    }
    observed_owner = {
        "status": workflow.status, "current_step": workflow.current_step,
        "error_message": workflow.error_message,
        "metadata_json": deepcopy(workflow.metadata_json or {}),
    }
    working_status = "running" if observed_owner["status"] == "failed" else observed_owner["status"]
    working_step = ("assess_current_source" if observed_owner["status"] == "failed"
                    else observed_owner["current_step"])
    working_error = None if observed_owner["status"] == "failed" else observed_owner["error_message"]
    first_checkpoint = True
    state = deepcopy(workflow.metadata_json or {})
    protocol = state.setdefault("protocol", {})

    async def persist(*, protocol_error=None):
        nonlocal first_checkpoint
        from bestseller.services.retained_planning_generation import _Reader

        current = await _Reader(session).get(WorkflowRunModel, identity)
        if current is not None:
            _need(
                current.project_id == pid and current.workflow_type == WORKFLOW_TYPE
                and current.scope_type == "planning_artifact" and current.scope_id == aid,
                "workflow_identity_changed",
            )
            _need(current.status not in {"cancelled", "stopping", "paused"}, "workflow_halted")
            expected_status = observed_owner["status"] if first_checkpoint else working_status
            expected_step = observed_owner["current_step"] if first_checkpoint else working_step
            expected_error = observed_owner["error_message"] if first_checkpoint else working_error
            _need(
                current.status == expected_status and current.current_step == expected_step
                and current.error_message == expected_error
                and (not first_checkpoint or content_hash(current.metadata_json or {})
                     == content_hash(observed_owner["metadata_json"])),
                "workflow_changed_during_assessment",
            )
            transition = {}
            if first_checkpoint and observed_owner["status"] == "failed":
                previous_failure = {
                    "status": current.status, "current_step": current.current_step,
                    "error_message": current.error_message,
                }
                failures = state.setdefault("previous_source_assessment_failures", [])
                if previous_failure not in failures:
                    failures.append(previous_failure)
                transition = {
                    "status": "running", "current_step": "assess_current_source",
                    "error_message": None,
                }
            if protocol_error is not None:
                _need(not first_checkpoint, "protocol_error_before_checkpoint")
                _need(content_hash(protocol_error.receipt) == content_hash(protocol),
                      "protocol_error_state_changed")
                state["source_admission_status"] = "protocol_unknown"
                transition = {
                    "status": "machine_blocked", "current_step": protocol_error.code,
                    "error_message": protocol_error.code,
                }
            with session.no_autoflush:
                changed = await session.execute(
                    update(WorkflowRunModel)
                    .where(
                        WorkflowRunModel.id == identity,
                        WorkflowRunModel.project_id == pid,
                        WorkflowRunModel.workflow_type == WORKFLOW_TYPE,
                        WorkflowRunModel.scope_type == "planning_artifact",
                        WorkflowRunModel.scope_id == aid,
                        WorkflowRunModel.status == current.status,
                        WorkflowRunModel.current_step == current.current_step,
                        WorkflowRunModel.error_message == current.error_message,
                        WorkflowRunModel.metadata_json == current.metadata_json,
                    )
                    .values(
                        metadata_json={**deepcopy(current.metadata_json or {}), **deepcopy(state)},
                        **transition,
                    )
                    .execution_options(synchronize_session=False)
                )
            _need(changed.rowcount == 1, "workflow_changed_during_checkpoint")
            if transition:
                from sqlalchemy.orm.attributes import set_committed_value

                for key, value in transition.items():
                    set_committed_value(workflow, key, value)
        else:
            _need(first_checkpoint and new_owner, "workflow_owner_missing")
            workflow.metadata_json = deepcopy(state)
        await _checkpoint_commit(session)
        first_checkpoint = False

    try:
        await persist()  # Durable identity and running owner before dispatch.
        with bind_selected_model_capacity(project.metadata_json):
            receipt = await assess_planning_draft_admission(
                session,
                settings,
                project=project,
                workflow_run_id=identity,
                candidate_payload=candidate,
                book_spec=foundation["book_spec"].content,
                world_spec=foundation["world_spec"].content,
                cast_spec=foundation["cast_spec"].content,
                commercial_report={
                    "schema": "historical-plan-review-observations.v1",
                    "commercial": binding["historical_editorial_observations"][
                        "outline_llm_commercial_judge"
                    ],
                    "reader": binding["historical_editorial_observations"][
                        "outline_reader_experience_judge"
                    ],
                },
                protocol_state=protocol,
                persist_protocol=persist,
            )
        # No network under these locks. Freeze source/control while the fresh
        # read and derived receipt are committed; consumers verify again.
        await session.scalars(
            select(BookProductionControlModel)
            .where(BookProductionControlModel.project_id == pid)
            .with_for_update()
        )
        await session.scalars(
            select(ProjectModel.id).where(ProjectModel.id == pid).with_for_update()
        )
        source_ids = [aid, *(row.id for row in foundation.values())]
        if binding.get("canonical_id"):
            source_ids.append(UUID(binding["canonical_id"]))
        await session.scalars(
            select(PlanningArtifactVersionModel.id)
            .where(PlanningArtifactVersionModel.id.in_(source_ids))
            .order_by(PlanningArtifactVersionModel.id)
            .with_for_update()
        )
        await session.scalars(
            select(ChapterModel.id)
            .where(ChapterModel.project_id == pid)
            .order_by(ChapterModel.chapter_number)
            .with_for_update()
        )
        await session.scalars(
            select(SceneCardModel.id)
            .where(SceneCardModel.project_id == pid)
            .order_by(SceneCardModel.chapter_id, SceneCardModel.scene_number)
            .with_for_update()
        )
        fresh_project, _, _, _, after = await _read_sources(
            session,
            settings,
            project_slug=project_slug,
            artifact_id=aid,
            materialization_workflow_id=materialization_workflow_id,
        )
        _need(content_hash(after) == content_hash(binding), "source_changed_during_assessment")
        control = await session.get(BookProductionControlModel, pid, populate_existing=True)
        _need(
            fresh_project.status not in {"paused", "archived", "cancelled", "completed"}
            and project_execution_scope(fresh_project) == scope
            and (control is None or control.desired_state == "run"),
            "halted_after_assessment",
        )
        from bestseller.services.retained_planning_generation import _Reader
        from bestseller.services.budget import ExecutionBudgetError, execution_scope_block_code

        budget = await _Reader(session).get(ExecutionBudgetModel, scope)
        if budget is not None:
            code = execution_scope_block_code(budget)
            if code:
                raise ExecutionBudgetError(code, scope)
        final_workflow = await _Reader(session).get(WorkflowRunModel, identity)
        _need(
            final_workflow is not None
            and final_workflow.status not in {"cancelled", "stopping", "paused"},
            "workflow_halted",
        )
        _need(
            final_workflow.project_id == pid and final_workflow.workflow_type == WORKFLOW_TYPE
            and final_workflow.scope_type == "planning_artifact" and final_workflow.scope_id == aid
            and final_workflow.status == working_status and final_workflow.current_step == working_step
            and final_workflow.error_message == working_error,
            "workflow_changed_during_assessment",
        )
        state["source_admission"] = deepcopy(receipt)
        state["grade_granted_to_old_editorial_reports"] = False
        if final_workflow.error_message:
            previous_failure = {"status": final_workflow.status,
                "current_step": final_workflow.current_step,
                "error_message": final_workflow.error_message}
            failures = state.setdefault("previous_source_assessment_failures", [])
            if previous_failure not in failures:
                failures.append(previous_failure)
        with session.no_autoflush:
            changed = await session.execute(
                update(WorkflowRunModel)
                .where(
                    WorkflowRunModel.id == identity,
                    WorkflowRunModel.project_id == pid,
                    WorkflowRunModel.workflow_type == WORKFLOW_TYPE,
                    WorkflowRunModel.scope_type == "planning_artifact",
                    WorkflowRunModel.scope_id == aid,
                    WorkflowRunModel.status == working_status,
                    WorkflowRunModel.current_step == working_step,
                    WorkflowRunModel.error_message == working_error,
                    WorkflowRunModel.metadata_json == final_workflow.metadata_json,
                )
                .values(
                    status="completed"
                    if receipt.get("draft_eligible") is True
                    else "machine_blocked",
                    current_step="current_source_admitted"
                    if receipt.get("draft_eligible") is True
                    else "source_repair_required",
                    error_message=None,
                    metadata_json={
                        **deepcopy(final_workflow.metadata_json or {}),
                        **deepcopy(state),
                    },
                )
                .execution_options(synchronize_session=False)
            )
        _need(changed.rowcount == 1, "workflow_changed_during_commit")
        await _checkpoint_commit(session)
        return deepcopy(receipt)
    except BaseException as error:
        from bestseller.services.execution_stage import attach_workflow_failure
        from bestseller.services.planning_draft_admission import PlanningDraftAdmissionProtocolError

        if isinstance(error, PlanningDraftAdmissionProtocolError):
            try:
                await persist(protocol_error=error)
            except BaseException as checkpoint_error:
                attach_workflow_failure(checkpoint_error, workflow)
                raise
        attach_workflow_failure(error, workflow)
        raise
