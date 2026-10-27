"""Pending-reference lane on the normal continuation queue; never formal adoption.

Every model request needs its existing exact single-dispatch grant. A missing
one is a durable pre-send awaiting-authority checkpoint, not a paid Unknown.
"""

import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from hashlib import sha256
from uuid import UUID, uuid5, NAMESPACE_URL
from sqlalchemy import select, update
from bestseller.infra.db import models as m
from bestseller.services.prompt_source_policy import content_hash

SCHEMA = "pending-manuscript-continuation.v1"
STAGES = ("plan", "admit", "materialize", "activate", "write")
_GATE = ContextVar("pending_exact_stage_request", default=None)


class AwaitingAuthority(BaseException):
    def __init__(self, receipt):
        self.receipt = receipt
        super().__init__("pending_exact_request_authority_required")


@contextmanager
def exact_stage_requests(binding, stage):
    token = _GATE.set((asyncio.current_task(), binding, stage))
    try:
        yield
    finally:
        _GATE.reset(token)


async def enforce_stage_request(session, request, role, digest):
    state = _GATE.get()
    if state is None:
        return request
    task, binding, stage = state
    if task is not asyncio.current_task():
        raise ValueError("pending_stage_child_task_denied")
    if str(request.project_id) != binding["project_id"] or not request.operation_id:
        raise ValueError("pending_stage_request_scope")
    from bestseller.services.llm import current_execution_scope, _effective_input_payload

    if current_execution_scope() != binding["execution_scope_id"]:
        raise ValueError("pending_stage_execution_scope_changed")
    key = sha256((binding["execution_scope_id"] + "\n" + request.operation_id).encode()).hexdigest()
    operation = await session.get(m.ExecutionOperationModel, key, populate_existing=True)
    if operation is not None:
        if operation.scope_id != binding["execution_scope_id"] or operation.input_hash != digest:
            raise ValueError("pending_stage_operation_binding_changed")
        if operation.status == "completed" and operation.result_json is not None:
            # complete_text still validates/reuses the original operation result.
            # This grants no physical send and does not mint another grant.
            return request
        raise ValueError("pending_stage_operation_unknown")
    budget = await session.get(
        m.ExecutionBudgetModel, binding["execution_scope_id"], populate_existing=True
    )
    matches = []
    for gid, grant in (
        (budget.metadata_json if budget else {}).get("single_dispatch_grants") or {}
    ).items():
        b = grant.get("binding") or {}
        if (
            grant.get("status") == "available"
            and grant.get("used_attempt_id") is None
            and grant.get("physical_attempt_cap") == 1
            and (
                b.get("scope_id"),
                b.get("operation_key"),
                b.get("input_hash"),
                b.get("model"),
                b.get("endpoint"),
                b.get("workflow_id"),
                b.get("template"),
                b.get("version_prefix"),
            )
            == (
                binding["execution_scope_id"],
                key,
                digest,
                role.model,
                role.api_base,
                str(request.workflow_run_id),
                request.prompt_template,
                request.prompt_version,
            )
        ):
            matches.append(gid)
    if len(matches) != 1:
        raise AwaitingAuthority(
            {
                "stage": stage,
                "request": request.model_dump(mode="json"),
                "effective_input": _effective_input_payload(request, role),
                "input_hash": digest,
                "operation_key": key,
                "model": role.model,
                "endpoint": role.api_base,
                "matching_grants": len(matches),
                "pre_send": True,
                "calls_sent": 0,
            }
        )
    return request.model_copy(
        update={"metadata": {**request.metadata, "single_dispatch_authorization_id": matches[0]}}
    )


async def source_snapshot(session, settings, project, after_chapter, *, require_last):
    from bestseller.services import draft_continuity as dc

    if not await dc.draft_mode_enabled(session, project_id=project.id, project=project):
        raise ValueError("pending_mode_not_authorized")
    chapters = list(
        await session.scalars(
            select(m.ChapterModel)
            .where(
                m.ChapterModel.project_id == project.id,
                m.ChapterModel.chapter_number <= after_chapter,
            )
            .order_by(m.ChapterModel.chapter_number)
        )
    )
    if [c.chapter_number for c in chapters] != list(range(1, after_chapter + 1)):
        raise ValueError("pending_reference_gap")
    references = []
    for c in chapters:
        selected = await dc.select_reference(session, settings, project=project, chapter=c)
        if selected is None:
            if c.chapter_number != after_chapter or require_last:
                raise ValueError("pending_reference_unqualified")
            continue
        draft, proof = selected
        references.append(
            {
                "chapter_number": c.chapter_number,
                "draft_id": str(draft.id),
                "body_sha256": dc.body_hash(draft.content_md),
                "reference": deepcopy(proof),
            }
        )
    current = await session.scalar(
        select(m.ChapterDraftVersionModel).where(
            m.ChapterDraftVersionModel.chapter_id == chapters[-1].id,
            m.ChapterDraftVersionModel.is_current.is_(True),
        )
    )
    if current is None:
        raise ValueError("pending_current_source_missing")
    if require_last and references[-1]["draft_id"] != str(current.id):
        raise ValueError("pending_frontier_reference_not_current")
    sources = await dc.current_sources(session, settings, project=project, chapter=chapters[-1])
    # Full creative source proof; no whole-metadata hash or current pointers for prior chapters.
    return {
        "references": references,
        "frontier_draft_id": str(current.id),
        "frontier_body_sha256": dc.body_hash(current.content_md),
        "source_sha256": content_hash(sources),
        "execution_scope_id": (project.metadata_json or {}).get("execution_scope_id"),
        "selected_model": (project.metadata_json or {}).get("llm_model_id"),
        "target_chapters": project.target_chapters,
    }


async def record_intent(session, settings, *, project, after_chapter, through_chapter=None):
    if type(after_chapter) is not int or not 1 <= after_chapter < project.target_chapters:
        return None
    through_chapter = through_chapter or after_chapter + 1
    if not after_chapter < through_chapter <= project.target_chapters:
        raise ValueError("pending_target_extent")
    snapshot = await source_snapshot(session, settings, project, after_chapter, require_last=False)
    if not snapshot["execution_scope_id"]:
        raise ValueError("pending_execution_scope_missing")
    # The last reference may be appended only after exact frontier qualification;
    # it cannot change the identities of the earlier selected references.
    binding = {
        "project_id": str(project.id),
        "after_chapter": after_chapter,
        "through_chapter": through_chapter,
        "next_window_proposal": {"start": after_chapter + 1, "end": through_chapter},
        **{k: v for k, v in snapshot.items() if k != "references"},
        "prior_references": [
            v for v in snapshot["references"] if v["chapter_number"] < after_chapter
        ],
    }
    from pathlib import Path

    binding["coordinator_policy_sha256"] = content_hash(
        {
            name: sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
            for name in ("pending_manuscript_continuation.py", "pending_manuscript_handlers.py")
        }
    )
    identity = uuid5(NAMESPACE_URL, SCHEMA + ":" + content_hash(binding))
    await session.execute(
        select(m.ProjectModel.id).where(m.ProjectModel.id == project.id).with_for_update()
    )
    row = await session.get(m.WorkflowRunModel, identity, populate_existing=True)
    if row is None:
        owners = await session.scalars(
            select(m.WorkflowRunModel).where(
                m.WorkflowRunModel.project_id == project.id,
                m.WorkflowRunModel.workflow_type == "autonomous_continuation",
            )
        )
        if any(
            v.metadata_json.get("schema") == SCHEMA
            and v.metadata_json.get("binding", {}).get("after_chapter") == after_chapter
            for v in owners
        ):
            raise ValueError("pending_existing_frontier_owner_binding_changed")
        row = m.WorkflowRunModel(
            id=identity,
            project_id=project.id,
            workflow_type="autonomous_continuation",
            scope_type="project",
            scope_id=project.id,
            status="pending",
            current_step="reference_revalidation",
            requested_by="normal-pending-manuscript",
            metadata_json={
                "schema": SCHEMA,
                "binding": binding,
                "stage_outputs": {},
                "stage_started": None,
                "provider_dispatch_authorized": False,
                "formal_adoption": False,
                "canon_authority": False,
            },
        )
        session.add(row)
        await session.flush()
    elif row.metadata_json.get("binding") != binding:
        raise ValueError("pending_checkpoint_binding_changed")
    return row


async def verify_sources(session, settings, project, row):
    binding = row.metadata_json["binding"]
    await session.refresh(project)
    from pathlib import Path

    policy = content_hash(
        {
            name: sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
            for name in ("pending_manuscript_continuation.py", "pending_manuscript_handlers.py")
        }
    )
    if binding.get("coordinator_policy_sha256") != policy:
        raise ValueError("pending_coordinator_policy_changed")
    snapshot = await source_snapshot(
        session, settings, project, binding["after_chapter"], require_last=True
    )
    for key in (
        "frontier_draft_id",
        "frontier_body_sha256",
        "source_sha256",
        "execution_scope_id",
        "selected_model",
        "target_chapters",
    ):
        if snapshot[key] != binding[key]:
            raise ValueError("pending_source_changed:" + key)
    if [
        v for v in snapshot["references"] if v["chapter_number"] < binding["after_chapter"]
    ] != binding["prior_references"]:
        raise ValueError("pending_selected_prior_reference_changed")
    saved = row.metadata_json.get("qualified_references")
    if saved is not None and saved != snapshot["references"]:
        raise ValueError("pending_qualified_reference_changed")
    return snapshot["references"]


async def execute(
    session, checkpoint_id, *, settings, handlers, permit, owner_id, resume_after_restart=False
):
    row = await session.get(m.WorkflowRunModel, UUID(str(checkpoint_id)), populate_existing=True)
    if row is None or row.metadata_json.get("schema") != SCHEMA:
        raise ValueError("pending_checkpoint_missing")
    meta = deepcopy(row.metadata_json)
    if row.status == "completed":
        return meta["execution_result"]
    if row.status in {"machine_blocked", "failed", "cancelled"}:
        return {"state": row.status, "reason": row.current_step}
    if meta.get("stage_started") is not None:
        if row.status == "running" and not resume_after_restart:
            return {"state": "already_running"}
        row.status = "machine_blocked"
        row.current_step = "pending_interrupted_stage_unknown"
        row.metadata_json = {**meta, "automatic_retry": False}
        await session.commit()
        return {"state": "machine_blocked", "reason": row.current_step}
    if (
        row.status == "running"
        and meta.get("execution_owner") != owner_id
        and not resume_after_restart
    ):
        return {"state": "already_running"}
    claimed = await session.execute(
        update(m.WorkflowRunModel)
        .where(
            m.WorkflowRunModel.id == row.id,
            m.WorkflowRunModel.status == row.status,
            m.WorkflowRunModel.status.in_(
                ["pending", "awaiting_authority", "awaiting_reference", "running"]
            ),
        )
        .values(status="running", metadata_json={**meta, "execution_owner": owner_id})
        .execution_options(synchronize_session=False)
    )
    await session.commit()
    if claimed.rowcount != 1:
        return {"state": "already_running"}
    await session.refresh(row)
    project = await session.get(m.ProjectModel, row.project_id, populate_existing=True)
    for stage in STAGES:
        meta = deepcopy(row.metadata_json)
        if stage in meta["stage_outputs"]:
            continue
        try:
            refs = await verify_sources(session, settings, project, row)
            allowed, reason = await permit(session, project, row)
            if not allowed:
                raise ValueError(reason or "pending_not_runnable")
        except ValueError as exc:
            row.status = (
                "awaiting_reference"
                if str(exc) == "pending_reference_unqualified"
                else "machine_blocked"
            )
            row.current_step = str(exc)
            row.metadata_json = {**meta, "automatic_retry": False}
            await session.commit()
            return {"state": row.status, "reason": row.current_step}
        row.current_step = stage
        row.metadata_json = {**meta, "qualified_references": refs, "stage_started": stage}
        await session.commit()
        checkpoint_uuid = row.id
        try:
            from bestseller.services import llm

            with (
                llm.bind_execution_scope(meta["binding"]["execution_scope_id"]),
                llm.bind_execution_operation("pending-continuation:" + str(row.id) + ":" + stage),
                exact_stage_requests(meta["binding"], stage),
            ):
                output = await handlers[stage](
                    session, project, row, deepcopy(meta["stage_outputs"])
                )
            if not isinstance(output, dict) or output.get("blocked_reason"):
                raise ValueError("pending_stage_incomplete:" + stage)
            row.metadata_json = {
                **row.metadata_json,
                "stage_started": None,
                "stage_outputs": {**meta["stage_outputs"], stage: output},
                "prepared_request": None,
            }
            await session.commit()
        except AwaitingAuthority as exc:
            # This exception is raised before ledger claim/reserve. Keep any
            # normal producer checkpoint committed by its own API; no send Unknown.
            await session.rollback()
            row = await session.get(m.WorkflowRunModel, checkpoint_uuid, populate_existing=True)
            row.status = "awaiting_authority"
            row.current_step = stage
            row.metadata_json = {
                **row.metadata_json,
                "stage_started": None,
                "prepared_request": exc.receipt,
                "automatic_retry": False,
            }
            await session.commit()
            return {"state": "awaiting_authority", "stage": stage, "calls_sent": 0}
        except BaseException:
            # Cancellation/timeout/crash may follow a real paid reservation.
            # Persisted stage_started remains: restart must reconcile, never resend.
            await session.rollback()
            raise
    result = {
        "state": "completed",
        "whole_book_complete": False,
        "formal_adoption": False,
        "draft": row.metadata_json["stage_outputs"]["write"],
    }
    row.status = "completed"
    row.current_step = "pending_draft_created"
    row.metadata_json = {**row.metadata_json, "execution_result": result}
    await session.commit()
    return result


async def wake(session, settings, row):
    """Revalidate one existing checkpoint; never grants authority or resets a stage."""
    await session.refresh(row, with_for_update=True)
    if row.status not in {"awaiting_authority", "awaiting_reference"}:
        return row.status == "pending"
    project = await session.get(m.ProjectModel, row.project_id, populate_existing=True)
    if row.status == "awaiting_reference":
        try:
            await verify_sources(session, settings, project, row)
        except ValueError:
            return False
    else:
        p = row.metadata_json.get("prepared_request") or {}
        request = p.get("request") or {}
        budget = await session.get(
            m.ExecutionBudgetModel,
            row.metadata_json["binding"]["execution_scope_id"],
            populate_existing=True,
        )
        matches = []
        for gid, g in (
            (budget.metadata_json if budget else {}).get("single_dispatch_grants") or {}
        ).items():
            b = g.get("binding") or {}
            if (
                g.get("status") == "available"
                and g.get("used_attempt_id") is None
                and g.get("physical_attempt_cap") == 1
                and (
                    b.get("scope_id"),
                    b.get("operation_key"),
                    b.get("input_hash"),
                    b.get("model"),
                    b.get("endpoint"),
                    b.get("workflow_id"),
                    b.get("template"),
                    b.get("version_prefix"),
                )
                == (
                    row.metadata_json["binding"]["execution_scope_id"],
                    p.get("operation_key"),
                    p.get("input_hash"),
                    p.get("model"),
                    p.get("endpoint"),
                    request.get("workflow_run_id"),
                    request.get("prompt_template"),
                    request.get("prompt_version"),
                )
            ):
                matches.append(gid)
        if len(matches) != 1:
            return False
        # Full creative/reference/control revalidation still runs before handler.
    previous = row.status
    row.status = "pending"
    row.metadata_json = {
        **row.metadata_json,
        "wake_generation": row.metadata_json.get("wake_generation", 0) + 1,
        "prior_job_ids": [
            *row.metadata_json.get("prior_job_ids", []),
            *(
                [row.metadata_json["queued_job_id"]]
                if row.metadata_json.get("queued_job_id")
                else []
            ),
        ],
        "queued_job_id": None,
        "wake_history": [
            *row.metadata_json.get("wake_history", []),
            {
                "from": previous,
                "stage": row.current_step,
                "prepared_input_hash": (row.metadata_json.get("prepared_request") or {}).get(
                    "input_hash"
                ),
                "new_grants_created": False,
            },
        ],
    }
    await session.flush()
    return True


async def enqueue(session, redis, row):
    if row.status in {"awaiting_authority", "awaiting_reference"}:
        from bestseller.settings import apply_runtime_llm_profile, get_settings
        from bestseller.services.creation_configuration import project_settings

        project = await session.get(m.ProjectModel, row.project_id)
        if not await wake(
            session, project_settings(apply_runtime_llm_profile(get_settings()), project), row
        ):
            return {"dispatched": False, "state": row.status}

    if row.status not in {"pending", "running"}:
        return {"dispatched": False, "state": row.status}
    meta = row.metadata_json
    if meta.get("stage_started") is not None:
        row.status = "machine_blocked"
        row.current_step = "pending_interrupted_stage_unknown"
        row.metadata_json = {**meta, "automatic_retry": False}
        await session.commit()
        return {"dispatched": False, "reason": row.current_step}
    cursor = content_hash(meta["stage_outputs"])[:16]
    job = str(row.id) + ":" + cursor + ":wake" + str(meta.get("wake_generation", 0))
    if meta.get("queued_job_id") == job and await redis.exists(
        "arq:job:" + job, "arq:in-progress:" + job, "arq:result:" + job
    ):
        return {"dispatched": False, "reused": True}
    project = await session.get(m.ProjectModel, row.project_id)
    await redis.enqueue_job(
        "run_autonomous_continuation_task",
        workflow_run_id=job,
        payload={
            "checkpoint_id": str(row.id),
            "project_slug": project.slug,
            "resume_after_restart": True,
        },
        _job_id=job,
    )
    row.metadata_json = {**meta, "queued_job_id": job}
    await session.commit()
    return {"dispatched": True, "job_id": job}


async def dispatch_committed_intent(session, redis, checkpoint_id):
    """Caller invokes after qualification/grant commit; the DB outbox survives failure."""
    row = await session.get(m.WorkflowRunModel, UUID(str(checkpoint_id)), populate_existing=True)
    if row is None or row.metadata_json.get("schema") != SCHEMA:
        raise ValueError("pending_checkpoint_missing")
    return await enqueue(session, redis, row)
