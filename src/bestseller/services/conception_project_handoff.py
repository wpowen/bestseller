"""Native two-phase creation identity. Lifecycle state never grants approval.

Legacy project/checkpoint protocols remain unchanged. Only a newly created,
source-bound staging project uses these adapters and the same native validators.
The caller owns transactions, durable commits, provider admission and leases.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Mapping
from uuid import UUID

from sqlalchemy import select

from bestseller.domain.enums import ProjectType, WorkflowStatus
from bestseller.domain.project import ProjectCreate
from bestseller.infra.db.models import ChapterModel, ProjectModel, WorkflowRunModel
from bestseller.infra.db.models import PlanningArtifactVersionModel
from bestseller.services import conception_checkpoints as checkpoints
from bestseller.services.budget import current_execution_scope
from bestseller.services.creation_intent_contract import ConceptionAttemptInput, contract_from_payload
from bestseller.services.source_bound_planning_content import new_reader_first_binding, reader_first_binding

STAGING_KEY = "conception_project_handoff"
PROTOCOL = "conception-project-handoff.v1"
ACTIVE = ("pending", "queued", "running", "stopping")


@dataclass(frozen=True)
class ConceptionProjectBinding:
    project: ProjectModel
    workflow: WorkflowRunModel


def read_conception_project_handoff(project) -> dict[str, Any] | None:
    metadata = project.metadata_json or {}
    if STAGING_KEY not in metadata:
        return None
    stage = deepcopy(metadata[STAGING_KEY])
    if (not isinstance(stage, dict) or stage.get("schema_version") != PROTOCOL
        or stage.get("state") not in {"staging", "complete"}
        or stage.get("project_id") != str(project.id)
        or not isinstance(stage.get("request_payload"), dict)
        or stage["request_payload"].get("slug") != project.slug
        or not isinstance(stage.get("task_id"), str) or not stage["task_id"]
        or metadata.get("execution_scope_id") != stage.get("execution_scope_id")):
        raise ValueError("conception_staging_identity_invalid")
    UUID(stage["workflow_run_id"])
    raw = metadata.get("reader_first_original_input")
    if not isinstance(raw, str) or reader_first_binding(metadata) != new_reader_first_binding(raw):
        raise ValueError("conception_staging_reader_input_changed")
    intent = contract_from_payload(stage["request_payload"])
    if intent.contract_hash() != stage.get("creation_intent_hash"):
        raise ValueError("conception_staging_intent_changed")
    request = stage["request_payload"]
    if (project.target_chapters != int(request["target_chapters"])
        or project.target_word_count != int(request["target_words"])
        or project.genre != request["genre"]
        or metadata.get("creation_intent_contract_hash") != stage["creation_intent_hash"]):
        raise ValueError("conception_staging_target_changed")
    return stage


def pending_conception_project(project) -> bool:
    stage = read_conception_project_handoff(project)
    return stage is not None and stage["state"] == "staging"


async def staging_resume_payload(session, project):
    stage = read_conception_project_handoff(project)
    if stage is None:
        return None
    if stage["state"] == "staging":
        await verify_staging_workflow(session, project_id=project.id,
            workflow_run_id=UUID(stage["workflow_run_id"]), current=True)
        payload = deepcopy(stage["request_payload"])
        payload.update(_run_conception=True, _conception_workflow_run_id=stage["workflow_run_id"])
        return payload
    from bestseller.services.conception_snapshots import snapshot_hash
    intent = await session.get(PlanningArtifactVersionModel, UUID(stage["creation_intent_snapshot_id"]))
    snapshot = await session.get(PlanningArtifactVersionModel, UUID(stage["conception_snapshot_id"]))
    stop = contract_from_payload(stage["request_payload"]).stop_after_conception
    if (intent is None or snapshot is None or intent.project_id != project.id or snapshot.project_id != project.id
        or intent.artifact_type != "creation_intent" or intent.status != "validated"
        or snapshot.artifact_type != "conception_snapshot" or str(snapshot.source_run_id) != stage["workflow_run_id"]
        or snapshot_hash(intent.content) != stage["creation_intent_hash"]
        or (project.metadata_json or {}).get("conception_snapshot_id") != str(snapshot.id)):
        raise ValueError("conception_staging_completion_changed")
    if stop and snapshot.status == "canonical":
        # Native explicit approval appends an audit entry to the SAME snapshot.
        # Preserve the staging hash as its original pending-source anchor; only
        # accept the native receipt binding that source to the current body.
        metadata = project.metadata_json or {}
        receipt = metadata.get("conception_approval_idempotency")
        content = snapshot.content
        canonical_hash = snapshot_hash(content)
        if (not isinstance(receipt, Mapping) or not receipt.get("key")
            or metadata.get("conception_approved") is not True
            or metadata.get("creation_canonical_snapshot_id") != str(snapshot.id)
            or metadata.get("creation_canonical_snapshot_hash") != canonical_hash
            or receipt.get("snapshot_id") != str(snapshot.id)
            or receipt.get("content_hash") != canonical_hash
            or receipt.get("request") != {
                "snapshot_id": str(snapshot.id), "source_hash": stage["snapshot_hash"],
                "option_id": f"canonical:{snapshot.id}:{stage['snapshot_hash'][:16]}",
            } or not isinstance(content, Mapping)):
            raise ValueError("conception_staging_completion_changed")
        audit = content.get("_snapshot_audit")
        if (not isinstance(audit, list) or not audit or not isinstance(audit[-1], Mapping)
            or audit[-1].get("from_state") != "pending_user_approval"
            or audit[-1].get("to_state") != "canonical"
            or audit[-1].get("result_hash") != stage["snapshot_hash"]):
            raise ValueError("conception_staging_completion_changed")
        pending_content = dict(content)
        if audit[:-1]:
            pending_content["_snapshot_audit"] = audit[:-1]
        else:
            pending_content.pop("_snapshot_audit")
        if snapshot_hash(pending_content) != stage["snapshot_hash"]:
            raise ValueError("conception_staging_completion_changed")
        from bestseller.services.conception_approval_bridge import canonical_snapshot_premise
        canonical_snapshot_premise({"status": snapshot.status, "content": content})
    elif (snapshot.status != ("pending_user_approval" if stop else "canonical")
          or snapshot_hash(snapshot.content) != stage["snapshot_hash"]):
        raise ValueError("conception_staging_completion_changed")
    # None means use the ordinary accepted-project resume path. Stop-only
    # completion stays pending until native approval binds the original source.
    return None


async def verify_staging_workflow(session, *, project_id, workflow_run_id, current=False):
    project = await session.get(ProjectModel, project_id)
    run = await session.get(WorkflowRunModel, workflow_run_id)
    stage = read_conception_project_handoff(project) if project is not None else None
    if (stage is None or stage["state"] != "staging" or run is None
        or run.project_id != project.id or run.workflow_type != "conception_initial"
        or (run.scope_type, run.scope_id) != ("creation", None)):
        raise ValueError("conception_staging_workflow_invalid")
    if (project.metadata_json or {}).get("conception_approved") or (project.metadata_json or {}).get("creation_canonical_snapshot_id"):
        raise ValueError("conception_staging_unapproved_book_required")
    metadata = run.metadata_json or {}
    if (metadata.get("creation_intent_hash") != stage["creation_intent_hash"]
        or metadata.get("conception_checkpoint_input_hash") != stage["input_hash"]
        or metadata.get("conception_staging_task_id") != stage["task_id"]
        or metadata.get("execution_scope_id") != stage["execution_scope_id"]
        or (current and str(run.id) != stage["workflow_run_id"])):
        raise ValueError("conception_staging_workflow_binding_changed")
    if await session.scalar(select(ChapterModel.id).where(ChapterModel.project_id == project.id).limit(1)):
        raise ValueError("conception_staging_empty_book_required")
    return ConceptionProjectBinding(project, run)


async def prepare_conception_project_handoff(
    session, settings, *, task_id: str, request_payload: Mapping[str, object],
    attempt: ConceptionAttemptInput, source_workflow_run_id: UUID | None = None,
) -> ConceptionProjectBinding:
    from bestseller.services import projects
    from bestseller.services.conception import _bind_reader_first_conception, _build_genre_context
    from bestseller.services.workflows import create_workflow_run

    request = deepcopy(dict(request_payload))
    if STAGING_KEY in (request.get("metadata") or {}):
        raise ValueError("conception_staging_caller_marker_rejected")
    scope = current_execution_scope()
    if not task_id or not scope or contract_from_payload(request).contract_hash() != attempt.contract.contract_hash():
        raise ValueError("conception_staging_request_identity_required")
    if (int(request["target_chapters"]) != attempt.contract.chapter_count
        or bool(request.get("stop_after_conception", False)) != attempt.contract.stop_after_conception):
        raise ValueError("conception_staging_request_contract_changed")
    expected_inputs = {key: request.get(key) for key in ("premise", "genre", "sub_genre", "target_chapters")}
    expected_inputs["user_hints"] = request.get("user_hints") or {}
    if attempt.input_payload != expected_inputs:
        raise ValueError("conception_staging_request_input_changed")
    input_hash = checkpoints.stable_conception_input_hash(attempt)
    project = await session.scalar(select(ProjectModel).where(ProjectModel.slug == str(request["slug"])).with_for_update())
    stage = read_conception_project_handoff(project) if project is not None else None
    if project is not None:
        if (stage is None or stage["state"] != "staging" or stage["task_id"] != task_id
            or stage["creation_intent_hash"] != attempt.contract.contract_hash()
            or stage["input_hash"] != input_hash or stage["execution_scope_id"] != scope):
            raise ValueError("conception_staging_existing_identity_conflict")
    elif source_workflow_run_id is not None:
        raise ValueError("conception_staging_source_project_missing")
    else:
        # Freeze the SAME raw-input algorithm as Q4, then pass that exact raw
        # value explicitly even after optional architect facets are generated.
        ctx = _build_genre_context(
            str(request.get("_genre_key") or ""), int(request["target_chapters"]),
            genre=str(request["genre"]), sub_genre=str(request.get("sub_genre") or ""),
            genre_intent_contract=attempt.contract.genre_intent,
            creation_intent_contract=attempt.contract.model_dump(mode="json"),
        )
        marker = _bind_reader_first_conception(ctx, user_hints=request.get("user_hints"))
        if marker is None:
            raise ValueError("conception_staging_reader_first_required")
        metadata = deepcopy(request.get("metadata") or {})
        # Generated approval/contract fields are never valid staging inputs.
        if any(key in metadata for key in ("concept_contract", "concept_contract_version", "conception_approved",
            "creation_canonical_snapshot_id", "active_packaging_contract_id", "conception_artifacts")):
            raise ValueError("conception_staging_generated_authority_rejected")
        metadata.update(
            creation_intent_contract=attempt.contract.model_dump(mode="json"),
            creation_intent_contract_hash=attempt.contract.contract_hash(),
            genre_intent_contract=deepcopy(request.get("genre_intent_contract") or {}),
            premise=str(request["premise"]), user_hints=deepcopy(request.get("user_hints") or {}),
            reader_first_planning=marker, reader_first_original_input=ctx["reader_first_original_input"],
            execution_scope_id=scope,
        )
        payload = ProjectCreate(
            slug=str(request["slug"]), title=str(request["title"]), genre=str(request["genre"]),
            sub_genre=request.get("sub_genre"), audience=request.get("audience"),
            language=str(request.get("language") or "zh-CN"),
            target_word_count=int(request["target_words"]), target_chapters=int(request["target_chapters"]),
            project_type=ProjectType(str(request.get("project_type") or "linear")),
            metadata=metadata, writing_profile=request.get("writing_profile"),
        )
        project = await projects.create_project(session, payload, settings)
    if source_workflow_run_id is not None:
        source = await verify_staging_workflow(session, project_id=project.id, workflow_run_id=source_workflow_run_id)
        if str(source.workflow.id) != stage["workflow_run_id"] or source.workflow.status in ACTIVE:
            raise ValueError("conception_staging_source_not_terminal")
    run = await create_workflow_run(
        session, project_id=project.id, workflow_type="conception_initial", status=WorkflowStatus.RUNNING,
        scope_type="creation", scope_id=None, requested_by="web-ui", current_step="conception",
        idempotency_key=attempt.idempotency_key, metadata={
            "execution_scope_id": scope, "conception_staging_task_id": task_id,
            "attempt_id": attempt.attempt_id, "creation_intent_hash": attempt.contract.contract_hash(),
            "creation_intent_schema": attempt.contract.schema_version, "input_hash": attempt.input_hash(),
            "conception_checkpoint_input_hash": input_hash,
            "source_conception_workflow_run_id": str(source_workflow_run_id) if source_workflow_run_id else None,
        },
    )
    if (run.project_id != project.id or run.workflow_type != "conception_initial"
        or (run.scope_type, run.scope_id) != ("creation", None)
        or (run.metadata_json or {}).get("execution_scope_id") != scope
        or (run.metadata_json or {}).get("conception_staging_task_id") != task_id
        or (run.metadata_json or {}).get("input_hash") != attempt.input_hash()):
        raise ValueError("conception_staging_idempotency_conflict")
    active = await session.scalar(select(WorkflowRunModel.id).where(
        WorkflowRunModel.project_id == project.id, WorkflowRunModel.status.in_(ACTIVE), WorkflowRunModel.id != run.id,
    ).limit(1))
    if active is not None:
        raise ValueError("conception_staging_other_workflow_active")
    stage = {
        **(stage or {}), "schema_version": PROTOCOL, "state": "staging", "project_id": str(project.id),
        "task_id": task_id, "creation_intent_hash": attempt.contract.contract_hash(), "input_hash": input_hash,
        "execution_scope_id": scope, "workflow_run_id": str(run.id),
        "request_payload": (stage["request_payload"] if stage else request),
    }
    project.metadata_json = {**project.metadata_json, STAGING_KEY: stage}
    await session.flush()
    return ConceptionProjectBinding(project, run)


async def load_staging_conception_state(session, *, project_id, workflow_run_id, input_hash):
    binding = await verify_staging_workflow(session, project_id=project_id, workflow_run_id=workflow_run_id, current=True)
    run = binding.workflow
    stage = read_conception_project_handoff(binding.project)
    if input_hash != stage["input_hash"] or run.status in ACTIVE:
        raise ValueError("conception_staging_resume_identity_conflict")
    metadata = run.metadata_json or {}
    checkpoint = metadata.get(checkpoints.CHECKPOINT_KEY)
    if checkpoint is not None:
        checkpoint = checkpoints.select_conception_checkpoint_for_resume(
            checkpoint, input_hash=input_hash, signed_zero_recovery=metadata.get(checkpoints.SIGNED_ZERO_RECOVERY_KEY),
        )
        if "conception_structure_protocol_recovery" in metadata:
            from bestseller.services.conception_structure_protocol_recovery import select_recovery
            checkpoint = await select_recovery(session, run=run, checkpoint=checkpoint)
        _validate_staging_reader_state(checkpoint["state"], binding.project)
    record = metadata.get(checkpoints.REPLAY_CONTEXT_KEY)
    if record is not None:
        checkpoints.validate_replay_context(record, input_hash=input_hash)
        _validate_staging_reader_state(record["context"], binding.project)
    return deepcopy(checkpoint), deepcopy(record)


def _validate_staging_reader_state(container, project):
    expected = reader_first_binding(project)
    contexts = (container, container.get("ctx", {}), container.get("writing_profile", {}))
    markers = [reader_first_binding(value) for value in contexts if "reader_first_planning" in value]
    raw = container.get("reader_first_original_input") or container.get("ctx", {}).get("reader_first_original_input")
    if not markers or any(marker != expected for marker in markers) or raw != project.metadata_json["reader_first_original_input"]:
        raise ValueError("conception_staging_checkpoint_reader_input_changed")


async def save_staging_conception_state(session, *, project_id, workflow_run_id, checkpoint=None, record=None):
    await session.scalar(select(WorkflowRunModel).where(WorkflowRunModel.id == workflow_run_id)
        .with_for_update().execution_options(populate_existing=True))
    binding = await verify_staging_workflow(session, project_id=project_id, workflow_run_id=workflow_run_id, current=True)
    run = binding.workflow
    stage = read_conception_project_handoff(binding.project)
    if current_execution_scope() != stage["execution_scope_id"] or run.status != "running":
        raise ValueError("conception_staging_checkpoint_owner_changed")
    metadata = dict(run.metadata_json or {})
    if checkpoint is not None:
        checkpoints.validate_conception_checkpoint(checkpoint, input_hash=stage["input_hash"])
        _validate_staging_reader_state(checkpoint["state"], binding.project)
        metadata[checkpoints.CHECKPOINT_KEY] = deepcopy(checkpoint)
    if record is not None:
        checkpoints.validate_replay_context(record, input_hash=stage["input_hash"])
        _validate_staging_reader_state(record["context"], binding.project)
        existing = metadata.get(checkpoints.REPLAY_CONTEXT_KEY)
        if existing is not None and existing != record:
            raise ValueError("conception replay context already frozen")
        metadata[checkpoints.REPLAY_CONTEXT_KEY] = deepcopy(record)
    run.metadata_json = metadata
    await session.flush()


async def finalize_conception_project_handoff(
    session, settings, *, project_id, workflow_run_id, project_payload: ProjectCreate,
    attempt: ConceptionAttemptInput, conception_result,
) -> ProjectModel:
    from bestseller.services.conception import ConceptionResult
    from bestseller.services.concept_contract import require_conception_contract_for_target
    from bestseller.services.conception_snapshots import create_conception_snapshot_artifact, snapshot_hash
    from bestseller.services.projects import materialize_staged_project

    if not isinstance(conception_result, ConceptionResult):
        raise ValueError("conception_staging_native_result_required")
    await session.scalar(select(ProjectModel).where(ProjectModel.id == project_id)
        .with_for_update().execution_options(populate_existing=True))
    binding = await verify_staging_workflow(session, project_id=project_id, workflow_run_id=workflow_run_id, current=True)
    project, run = binding.project, binding.workflow
    stage = read_conception_project_handoff(project)
    if (current_execution_scope() != stage["execution_scope_id"]
        or stage["input_hash"] != checkpoints.stable_conception_input_hash(attempt)
        or stage["creation_intent_hash"] != attempt.contract.contract_hash()
        or run.status != "completed" or (run.metadata_json or {}).get("attempt_id") != attempt.attempt_id):
        raise ValueError("conception_staging_final_binding_changed")
    require_conception_contract_for_target(conception_result.concept_contract, target_chapters=project.target_chapters)
    if reader_first_binding(conception_result.writing_profile) != reader_first_binding(project):
        raise ValueError("conception_staging_reader_binding_changed")
    contract = require_conception_contract_for_target(project_payload.metadata.get("concept_contract"), target_chapters=project.target_chapters)
    if (project_payload.title != conception_result.title
        or project_payload.metadata.get("premise") != conception_result.premise
        or project_payload.metadata.get("creation_intent_contract") != attempt.contract.model_dump(mode="json")):
        raise ValueError("conception_staging_final_source_changed")
    stop = attempt.contract.stop_after_conception
    async with session.begin_nested():
        project = await materialize_staged_project(session, project_payload, settings, project=project)
        intent = await create_conception_snapshot_artifact(
            session, project_id=project.id, artifact_type="creation_intent", content=attempt.contract.model_dump(mode="json"),
            status="validated", idempotency_key=f"{attempt.attempt_id}:intent", schema_version=attempt.contract.schema_version,
            source_run_id=run.id, created_by="web-ui",
        )
        metadata = dict(project.metadata_json or {})
        from bestseller.services.hook_propagation import coerce_hook_spec
        payload_hook = coerce_hook_spec(stage["request_payload"].get("hook_spec"))
        content = {
            "schema_version": "conception-snapshot.v1", "attempt_id": attempt.attempt_id,
            "input_hash": attempt.input_hash(), "conception_mode": "initial",
            "creation_intent_hash": attempt.contract.contract_hash(),
            "title": conception_result.title, "premise": conception_result.premise,
            "synopsis": conception_result.synopsis, "tags": conception_result.tags,
            "writing_profile": conception_result.writing_profile, "world_model": conception_result.world_model or {},
            "hook_spec": conception_result.hook_spec or (payload_hook.model_dump(mode="json") if payload_hook else {}),
            "concept_contract": contract, "story_spine": conception_result.story_spine or {},
            "commercial_brief": conception_result.commercial_brief or {},
            "conception_artifacts": metadata.get("conception_artifacts") or {},
        }
        snapshot = await create_conception_snapshot_artifact(
            session, project_id=project.id, artifact_type="conception_snapshot", content=content,
            status="pending_user_approval" if stop else "validated", idempotency_key=f"{attempt.attempt_id}:snapshot",
            schema_version="conception-snapshot.v1", source_run_id=run.id, created_by="web-ui",
        )
        metadata.update(creation_intent_snapshot_id=str(intent.id), creation_intent_contract_hash=attempt.contract.contract_hash(),
            conception_snapshot_id=str(snapshot.id), conception_snapshot_status=snapshot.status,
            conception_workflow_run_id=str(run.id))
        project.metadata_json = metadata
        if not stop:
            from bestseller.services.reviewed_conception_selection import select_reviewed_conception_snapshot
            await select_reviewed_conception_snapshot(
                session, settings, project_id=project.id, snapshot_id=snapshot.id,
                expected_hash=snapshot_hash(snapshot.content), actor="web-ui complete-book selection",
                reason="Select the same successfully reviewed source version for the requested complete book",
            )
        else:
            project.metadata_json = {**project.metadata_json, "planning_status": "awaiting_concept_approval", "conception_only": True}
        project.metadata_json = {**project.metadata_json, STAGING_KEY: {
            **stage, "state": "complete", "creation_intent_snapshot_id": str(intent.id),
            "conception_snapshot_id": str(snapshot.id), "snapshot_hash": snapshot_hash(snapshot.content),
        }}
        run.metadata_json = {**run.metadata_json, "project_id": str(project.id),
            "creation_intent_snapshot_id": str(intent.id), "conception_snapshot_id": str(snapshot.id),
            "snapshot_hash": snapshot_hash(snapshot.content)}
        await session.flush()
    return project
