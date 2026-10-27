"""One persistent manuscript objective; existing completed bodies are never rerun.

The executor owns no literary pass/fail thresholds. Normal stage adapters return
bound receipts or explicit hard blockers. Debt is retained and does not prevent
forward progress. A started unknown stage cannot be retried by restarting.
"""

from copy import deepcopy
from uuid import UUID, uuid5, NAMESPACE_URL
from sqlalchemy import select, update
from bestseller.infra.db import models as m
from bestseller.services.prompt_source_policy import content_hash
from bestseller.services import draft_continuity as dc

SCHEMA = "source-bound-book-run.v1"
STAGES = ("plan", "admit", "materialize", "activate", "write", "review", "knowledge")
TERMINAL = ("ending", "export", "reopen")


async def create(session, settings, *, project, requested_model, prepared_scopes=None):
    if not await dc.draft_mode_enabled(session, project_id=project.id, project=project):
        raise ValueError("book_pending_mode_not_authorized")
    scope = project.metadata_json.get("execution_scope_id")
    if not scope:
        raise ValueError("book_execution_scope_missing")
    binding = {
        "project_id": str(project.id),
        "scope_id": scope,
        "target_chapters": project.target_chapters,
        "requested_model": requested_model,
    }
    identity = uuid5(NAMESPACE_URL, SCHEMA + ":" + content_hash(binding))
    await session.execute(
        select(m.ProjectModel.id).where(m.ProjectModel.id == project.id).with_for_update()
    )
    row = await session.get(m.WorkflowRunModel, identity, populate_existing=True)
    if row is not None:
        if row.metadata_json.get("binding") != binding:
            raise ValueError("book_run_binding_changed")
        return row
    selected = {}
    existing = {}
    chapters = await session.scalars(
        select(m.ChapterModel)
        .where(m.ChapterModel.project_id == project.id)
        .order_by(m.ChapterModel.chapter_number)
    )
    for chapter in chapters:
        pair = await dc.select_reference(session, settings, project=project, chapter=chapter)
        if pair:
            draft, reference = pair
            selected[str(chapter.chapter_number)] = {
                "chapter_id": str(chapter.id),
                "draft_id": str(draft.id),
                "body_sha256": dc.body_hash(draft.content_md),
                "reference": deepcopy(reference),
            }
        else:
            draft = await session.scalar(
                select(m.ChapterDraftVersionModel).where(
                    m.ChapterDraftVersionModel.chapter_id == chapter.id,
                    m.ChapterDraftVersionModel.is_current.is_(True),
                )
            )
            if draft:
                existing[str(chapter.chapter_number)] = {
                    "chapter_id": str(chapter.id),
                    "draft_id": str(draft.id),
                    "body_sha256": dc.body_hash(draft.content_md),
                }
    if selected and sorted(map(int, selected)) != list(range(1, max(map(int, selected)) + 1)):
        raise ValueError("book_selected_reference_gap")
    scopes = deepcopy(prepared_scopes or {})
    if not scopes:
        prepared = await session.scalars(
            select(m.WorkflowRunModel).where(
                m.WorkflowRunModel.project_id == project.id,
                m.WorkflowRunModel.workflow_type == "applicable_source_hard_safety",
                m.WorkflowRunModel.status == "queued",
            )
        )
        for candidate in prepared:
            data = candidate.metadata_json or {}
            chapter = await session.get(m.ChapterModel, candidate.scope_id)
            original = existing.get(str(chapter.chapter_number)) if chapter is not None else None
            if (
                original
                and data.get("body_sha256") == original["body_sha256"]
                and data.get("manifest")
                and data.get("authorization_ref")
            ):
                scopes[str(chapter.chapter_number)] = {
                    "body_sha256": data["body_sha256"],
                    "parent_source_sha256": data["source_sha256"],
                    "applicability_manifest": deepcopy(data["manifest"]),
                    "original_scope_workflow_id": str(candidate.id),
                }
    row = m.WorkflowRunModel(
        id=identity,
        project_id=project.id,
        workflow_type="autonomous_continuation",
        scope_type="project",
        scope_id=project.id,
        status="pending",
        current_step="book_resume",
        requested_by="normal-book-objective",
        metadata_json={
            "schema": SCHEMA,
            "binding": binding,
            "selected": selected,
            "existing": existing,
            "outputs": {},
            "prepared_scopes": scopes,
            "debts": [],
            "stage_started": None,
            "attempt_history": [],
            "whole_book_complete": False,
        },
    )
    session.add(row)
    await session.flush()
    return row


async def verify_selected(session, settings, project, row):
    if (
        project.target_chapters != row.metadata_json["binding"]["target_chapters"]
        or project.metadata_json.get("execution_scope_id")
        != row.metadata_json["binding"]["scope_id"]
    ):
        raise ValueError("book_objective_or_scope_changed")
    if not await dc.draft_mode_enabled(session, project_id=project.id, project=project):
        raise ValueError("book_mode_revoked")
    memo = {}
    for number, source in row.metadata_json["selected"].items():
        chapter = await session.get(
            m.ChapterModel, UUID(source["chapter_id"]), populate_existing=True
        )
        pair = await dc.select_reference(
            session, settings, project=project, chapter=chapter, _memo=memo
        )
        if (
            pair is None
            or str(pair[0].id) != source["draft_id"]
            or dc.body_hash(pair[0].content_md) != source["body_sha256"]
            or pair[1] != source["reference"]
        ):
            raise ValueError("book_selected_source_changed:" + number)


async def execute(
    session,
    checkpoint_id,
    *,
    settings,
    handlers,
    permit,
    owner_id,
    resume_after_restart=False,
    recovery_owner=None,
    input_counter=None,
):
    row = await session.get(m.WorkflowRunModel, UUID(str(checkpoint_id)), populate_existing=True)
    if row is None or row.metadata_json.get("schema") != SCHEMA:
        raise ValueError("book_run_missing")
    if row.status == "completed":
        # A completed goal is re-read through its version-bound artifact verifier.
        verified = await handlers["reopen"](
            session,
            await session.get(m.ProjectModel, row.project_id),
            row,
            deepcopy(row.metadata_json["outputs"]),
        )
        return {
            **verified,
            "state": "completed",
            "whole_book_complete": True,
            "publication_ready": False,
        }
    if row.status in {"machine_blocked", "cancelled", "failed"}:
        return {"state": row.status, "reason": row.current_step}
    meta = deepcopy(row.metadata_json)
    if row.status == "running" and (
        not resume_after_restart or recovery_owner != meta.get("owner")
    ):
        return {"state": "already_running"}
    if meta.get("stage_started"):
        row.status = "machine_blocked"
        row.current_step = "book_started_stage_requires_reconciliation"
        await session.commit()
        return {"state": "machine_blocked", "reason": row.current_step}
    claimed = await session.execute(
        update(m.WorkflowRunModel)
        .where(
            m.WorkflowRunModel.id == row.id,
            m.WorkflowRunModel.status == row.status,
            m.WorkflowRunModel.metadata_json["owner"].as_string() == recovery_owner
            if row.status == "running"
            else m.WorkflowRunModel.status == row.status,
        )
        .values(status="running", metadata_json={**meta, "owner": owner_id})
        .execution_options(synchronize_session=False)
    )
    await session.commit()
    if claimed.rowcount != 1:
        return {"state": "already_running"}
    await session.refresh(row)
    project = await session.get(m.ProjectModel, row.project_id, populate_existing=True)
    total = meta["binding"]["target_chapters"]
    stages = []
    for n in range(1, total + 1):
        # Already qualified bodies only need their source-bound continuity index;
        # never re-plan/rewrite/review them for a new objective.
        if str(n) in meta["selected"]:
            stages.append((n, "knowledge"))
            continue
        if str(n) in meta["existing"]:
            stages.extend((n, s) for s in ("review", "knowledge"))
            continue
        stages.extend((n, s) for s in STAGES)
    stages.extend((total, s) for s in TERMINAL)
    for number, stage in stages:
        key = f"{number}:{stage}"
        await session.refresh(row)
        if row.status != "running" or row.metadata_json.get("owner") != owner_id:
            return {"state": "owner_lost", "stage": key}
        meta = deepcopy(row.metadata_json)
        if key in meta["outputs"]:
            continue
        try:
            await session.refresh(project)
            await verify_selected(session, settings, project, row)
            allowed, reason = await permit(session, project, row)
            if not allowed:
                raise ValueError(reason or "book_authority_required")
        except ValueError as exc:
            row.status = (
                "awaiting_authority" if str(exc) == "book_authority_required" else "machine_blocked"
            )
            row.current_step = str(exc)
            await session.commit()
            return {"state": row.status, "reason": row.current_step}
        row.current_step = key
        row.metadata_json = {**meta, "stage_started": key}
        await session.commit()
        rid = row.id
        try:
            from bestseller.services.llm import bind_execution_scope, bind_execution_operation
            from bestseller.services.finite_book_budget import bind_run

            budget = await session.get(
                m.ExecutionBudgetModel, meta["binding"]["scope_id"], populate_existing=True
            )
            policy = (
                ((budget.metadata_json or {}).get("finite_book_runs") or {}).get(str(rid))
                if budget
                else None
            )
            capacity = (policy or {}).get("binding", {}).get("capacity_limit", 65536)
            # The pre-existing exact frontier uses its original stricter ceiling.
            if str(number) in meta.get("prepared_scopes", {}):
                capacity = min(capacity, 65536)
            with (
                bind_execution_scope(meta["binding"]["scope_id"]),
                bind_execution_operation(f"book-run:{rid}:{key}"),
                bind_run(
                    rid,
                    project_id=project.id,
                    stage=key,
                    input_counter=input_counter,
                    capacity_limit=capacity,
                ),
            ):
                output = await handlers[stage](
                    session,
                    project,
                    row,
                    {"chapter_number": number, "stage_outputs": deepcopy(meta["outputs"])},
                )
            if not isinstance(output, dict) or output.get("hard_blocker"):
                raise ValueError((output or {}).get("hard_blocker") or "book_stage_receipt_missing")
            # A handler may commit independently. Recheck ownership and the live
            # lease before publishing its result into the objective checkpoint.
            # Keep stage_started on loss: an interrupted stage is not resend credit.
            await session.refresh(row)
            if row.status != "running" or row.metadata_json.get("owner") != owner_id:
                return {"state": "owner_lost", "stage": key}
            allowed, reason = await permit(session, project, row)
            if not allowed:
                return {"state": "owner_lost", "stage": key, "reason": reason}
            if stage == "review":
                source = output.get("qualified_source")
                if not isinstance(source, dict):
                    raise ValueError("book_review_source_unqualified")
                row.metadata_json = {
                    **row.metadata_json,
                    "selected": {**row.metadata_json["selected"], str(number): source},
                }
            row.metadata_json = {
                **row.metadata_json,
                "stage_started": None,
                "outputs": {**row.metadata_json["outputs"], key: output},
                "debts": [*meta["debts"], *output.get("debts", [])],
            }
            await session.commit()
        except BaseException as exc:
            await session.rollback()
            row = await session.get(m.WorkflowRunModel, rid, populate_existing=True)
            if row.status != "running" or row.metadata_json.get("owner") != owner_id:
                return {"state": "owner_lost", "stage": key}
            # Explicit pre-send fences can safely wait. All other interrupted
            # stages retain stage_started; they cannot manufacture resend credit.
            from bestseller.services.pending_manuscript_continuation import AwaitingAuthority
            from bestseller.services.finite_book_budget import FiniteBudgetError

            if isinstance(exc, (AwaitingAuthority, FiniteBudgetError)):
                row.status = "awaiting_authority"
                row.current_step = key
                row.metadata_json = {
                    **row.metadata_json,
                    "stage_started": None,
                    "pre_send_reason": str(exc),
                }
                await session.commit()
                return {"state": "awaiting_authority", "reason": str(exc), "stage": key}
            if isinstance(exc, ValueError):
                row.status = "machine_blocked"
                row.current_step = str(exc)
                await session.commit()
                return {"state": "machine_blocked", "reason": str(exc)}
            raise
    result = row.metadata_json["outputs"][f"{total}:reopen"]
    if result.get("verified") is not True:
        raise ValueError("book_reopen_not_verified")
    row.status = "completed"
    row.current_step = "complete_manuscript_with_debt"
    row.metadata_json = {
        **row.metadata_json,
        "whole_book_complete": True,
        "publication_ready": False,
    }
    budget = await session.scalar(
        select(m.ExecutionBudgetModel)
        .where(m.ExecutionBudgetModel.scope_id == row.metadata_json["binding"]["scope_id"])
        .with_for_update()
    )
    if budget is not None and str(row.id) in (budget.metadata_json.get("finite_book_runs") or {}):
        data = deepcopy(budget.metadata_json)
        data["finite_book_runs"][str(row.id)]["status"] = "completed"
        budget.metadata_json = data
    await session.commit()
    return {
        "state": "completed",
        "whole_book_complete": True,
        "publication_ready": False,
        "artifact": result,
        "debts": row.metadata_json["debts"],
    }


async def enqueue(session, redis, row, *, settings):
    """Same normal queue, one deterministic objective ID and restart owner fence."""
    project = await session.get(m.ProjectModel, row.project_id)
    meta = row.metadata_json
    budget = await session.get(
        m.ExecutionBudgetModel, meta["binding"]["scope_id"], populate_existing=True
    )
    policy = (
        ((budget.metadata_json or {}).get("finite_book_runs") or {}).get(str(row.id))
        if budget
        else None
    )
    if not policy or policy.get("status") != "active":
        if row.status not in {"completed", "machine_blocked", "cancelled", "failed"}:
            row.status = "awaiting_authority"
            row.current_step = "finite_book_authority_required"
            await session.commit()
        return {"dispatched": False, "state": row.status, "book_run_id": str(row.id)}
    if row.status in {"completed", "machine_blocked", "cancelled", "failed"}:
        return {"dispatched": False, "state": row.status}
    if await redis.exists("bestseller:pipeline-live:" + project.slug):
        return {"dispatched": False, "state": "project_owner_active"}
    if meta.get("stage_started"):
        row.status = "machine_blocked"
        row.current_step = "book_started_stage_requires_reconciliation"
        await session.commit()
        return {"dispatched": False, "state": row.status}
    if row.status == "awaiting_authority":
        row.status = "pending"
        meta = {**meta, "wake_generation": meta.get("wake_generation", 0) + 1}
        row.metadata_json = meta
        await session.commit()
    job = (
        str(row.id)
        + ":book:"
        + content_hash(meta["outputs"])[:16]
        + ":wake"
        + str(meta.get("wake_generation", 0))
    )
    if meta.get("queued_job_id") == job and await redis.exists(
        "arq:job:" + job, "arq:in-progress:" + job, "arq:result:" + job
    ):
        return {"dispatched": False, "reused": True, "job_id": job}
    owned = False
    queue = redis
    if not callable(getattr(queue, "enqueue_job", None)):
        from arq.connections import create_pool, RedisSettings

        queue = await create_pool(RedisSettings.from_dsn(settings.redis.url))
        owned = True
    try:
        await queue.enqueue_job(
            "run_autonomous_continuation_task",
            workflow_run_id=job,
            payload={
                "checkpoint_id": str(row.id),
                "project_slug": project.slug,
                "resume_after_restart": row.status == "running",
                "recovery_owner": meta.get("owner"),
            },
            _job_id=job,
        )
    finally:
        if owned:
            await queue.aclose()
    row.metadata_json = {**meta, "queued_job_id": job}
    await session.commit()
    return {"dispatched": True, "book_run_id": str(row.id), "job_id": job}


async def submit(session, settings, redis, *, project, requested_model=None, prepared_scopes=None):
    """Normal API/Web resume producer. Creating an objective grants no SEND."""
    from bestseller.services.model_catalog import (
        get_model_catalog_entry,
        resolve_project_model_entry,
    )

    entry = (
        get_model_catalog_entry(requested_model)
        if requested_model
        else resolve_project_model_entry(project.metadata_json)
    )
    model = entry.model if entry is not None else requested_model
    if not model:
        raise ValueError("book_requested_model_required")
    row = await create(
        session, settings, project=project, requested_model=model, prepared_scopes=prepared_scopes
    )
    await session.commit()
    if row.status == "completed":
        from bestseller.services.book_run_handlers import handlers

        result = await execute(
            session,
            row.id,
            settings=settings,
            handlers=handlers(settings),
            permit=None,
            owner_id="readback",
        )
        return {**result, "book_run_id": str(row.id), "dispatched": False}
    dispatch = await enqueue(session, redis, row, settings=settings)
    return {
        **dispatch,
        "book_run_id": str(row.id),
        "whole_book_complete": False,
        "publication_ready": False,
    }
