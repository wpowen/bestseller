"""Bounded native whole-book repair; no synthetic grades or adoption.

The persisted round is an execution journal, not evidence that any body passed.
Only normal rewrite, chapter review/adoption and knowledge services can close it.
"""

from copy import deepcopy
import hashlib
import json
import re
from uuid import UUID

from sqlalchemy import select

from bestseller.infra.db.models import (
    ChapterDraftVersionModel,
    ChapterModel,
    ExecutionBudgetModel,
    PlanningArtifactVersionModel,
    RewriteTaskModel,
)

SCHEMA = "fanqie-whole-repair.v1"
STATE_KEY = "fanqie_whole_repair"
MAX_ROUNDS = 3
MAX_NEW_CALLS = 80
_KEYS = {
    "schema",
    "round",
    "status",
    "source_sha256",
    "task_ids",
    "scope_id",
    "calls_at_start",
    "absolute_call_ceiling",
    "history",
    "completed_task_ids",
}


def _need(value, code):
    if not value:
        raise ValueError(code)


def digest(value):
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


def begin_round(previous, *, source_sha256, task_ids, scope_id, calls_used, native_max_calls):
    """Pure decision using native inputs supplied by the transaction owner."""
    _need(
        isinstance(source_sha256, str)
        and re.fullmatch("[a-f0-9]{64}", source_sha256)
        and type(calls_used) is int
        and calls_used >= 0
        and type(native_max_calls) is int
        and native_max_calls > 0
        and isinstance(scope_id, str)
        and bool(scope_id)
        and isinstance(task_ids, list)
        and all(isinstance(identifier, str) and bool(identifier) for identifier in task_ids)
        and len(set(task_ids)) == len(task_ids),
        "whole_repair_inputs_invalid",
    )
    if previous is not None:
        _need(
            isinstance(previous, dict)
            and set(previous) == _KEYS
            and previous["schema"] == SCHEMA
            and type(previous["round"]) is int
            and 1 <= previous["round"] <= MAX_ROUNDS
            and previous["status"] in {"running", "completed"}
            and isinstance(previous["source_sha256"], str)
            and re.fullmatch("[a-f0-9]{64}", previous["source_sha256"])
            and type(previous["calls_at_start"]) is int
            and previous["calls_at_start"] >= 0
            and type(previous["absolute_call_ceiling"]) is int
            and previous["absolute_call_ceiling"] > previous["calls_at_start"]
            and previous["absolute_call_ceiling"] <= previous["calls_at_start"] + MAX_NEW_CALLS
            and isinstance(previous["history"], list)
            and len(previous["history"]) == previous["round"] - 1
            and isinstance(previous["task_ids"], list)
            and isinstance(previous["completed_task_ids"], list)
            and set(previous["completed_task_ids"]) <= set(previous["task_ids"]),
            "whole_repair_state_invalid",
        )
        _need(previous["scope_id"] == scope_id, "whole_repair_scope_changed")
        if previous["status"] == "running":
            _need(
                previous["source_sha256"] == source_sha256 and previous["task_ids"] == task_ids,
                "whole_repair_active_source_changed",
            )
            return deepcopy(previous)
        prior_sources = {previous["source_sha256"]}
        for historical in previous["history"]:
            _need(
                isinstance(historical, dict) and isinstance(historical.get("source_sha256"), str),
                "whole_repair_state_invalid",
            )
            prior_sources.add(historical["source_sha256"])
        _need(source_sha256 not in prior_sources, "whole_repair_stagnant")
        _need(previous["round"] < MAX_ROUNDS, "whole_repair_round_limit")
        ceiling = min(previous["absolute_call_ceiling"], native_max_calls)
        history = [
            *previous["history"],
            deepcopy({k: v for k, v in previous.items() if k != "history"}),
        ]
        ordinal = previous["round"] + 1
        initial = previous["calls_at_start"]
    else:
        ceiling = min(native_max_calls, calls_used + MAX_NEW_CALLS)
        ordinal, initial, history = 1, calls_used, []
    _need(calls_used < ceiling, "whole_repair_call_limit")
    return dict(
        schema=SCHEMA,
        round=ordinal,
        status="running",
        source_sha256=source_sha256,
        task_ids=list(task_ids),
        scope_id=scope_id,
        calls_at_start=initial,
        absolute_call_ceiling=ceiling,
        history=history,
        completed_task_ids=[],
    )


async def _save(session, project, state):
    from bestseller.services.pipelines import _checkpoint_commit

    project.metadata_json = {**(project.metadata_json or {}), STATE_KEY: deepcopy(state)}
    await _checkpoint_commit(session)


async def _title(session, settings, project, *, premise, requested_by):
    """Use normal source-grounded title generation; do not relabel old proof."""
    from bestseller.domain.enums import WorkflowStatus
    from bestseller.services.fanqie_short_plan_resume import read_existing_complete_fanqie_plan
    from bestseller.services.full_flow_title_sources import read_current_title_sources
    from bestseller.services.pipelines import _checkpoint_commit
    from bestseller.services.planner import _refresh_story_grounded_project_title
    from bestseller.services.workflows import create_workflow_run

    sources = await read_current_title_sources(
        session, settings, project_id=project.id, owner_workflow=None
    )
    project = sources.project
    plan = await read_existing_complete_fanqie_plan(session, settings, project=project)
    _need(plan is not None, "whole_title_complete_plan_required")
    payload = {}
    for kind, identifier in plan["foundation_artifact_ids"].items():
        artifact = await session.get(PlanningArtifactVersionModel, UUID(identifier))
        _need(
            artifact is not None and artifact.project_id == project.id, "whole_title_source_changed"
        )
        payload[kind] = artifact.content
    workflow = await create_workflow_run(
        session,
        project_id=project.id,
        workflow_type="fanqie_whole_title_repair",
        scope_type="project",
        scope_id=project.id,
        requested_by=requested_by,
        status=WorkflowStatus.RUNNING,
        current_step="source_grounded_title",
    )
    metadata = deepcopy(project.metadata_json or {})
    prior = deepcopy(metadata.get("title_revision_status"))
    if prior is not None:
        metadata["title_revision_history"] = [*metadata.get("title_revision_history", []), prior]
    metadata["title_revision_status"] = {**(prior or {}), "quality_status": "needs_revision"}
    project.metadata_json = metadata
    workflow_id = workflow.id
    await _checkpoint_commit(session)
    previous_title = project.title
    title, changed, run_id, evidence = await _refresh_story_grounded_project_title(
        session,
        settings,
        project=project,
        premise=premise,
        book_spec_payload=payload.get("book_spec"),
        cast_spec_payload=payload.get("cast_spec"),
        story_design_payload=payload.get("story_design_kernel"),
        volume_plan_payload=payload.get("volume_plan"),
        prewrite_payload=None,
        workflow_run_id=workflow_id,
    )
    _need(changed and title != previous_title, "whole_title_no_accepted_candidate")
    project.title = title
    project.metadata_json = {
        **(project.metadata_json or {}),
        "title_revision_status": evidence,
        "fanqie_whole_title_repair": {
            **(project.metadata_json or {})["fanqie_whole_title_repair"],
            "status": "completed",
            "workflow_run_id": str(workflow_id),
            "llm_run_id": str(run_id) if run_id else None,
            "adopted_title": title,
        },
    }
    workflow.status = WorkflowStatus.COMPLETED.value
    workflow.current_step = "completed"
    await _checkpoint_commit(session)


async def execute_round(
    session, settings, *, project_id, outcome=None, requested_by="system", premise="", progress=None
):
    """Resume an existing round, or begin work issued by the real publisher.

    Returns False for a blocker without an actionable normal task. Native
    Unknown and failed task states remain blocked; they never get new credit.
    """
    from bestseller.domain.enums import WorkflowStatus
    from bestseller.services.budget import (
        bind_additional_call_ceiling,
        completion_authorization,
        current_execution_scope,
    )
    from bestseller.services.fanqie_short_pipeline import _require_fanqie_production_running
    from bestseller.services.fanqie_short_source_export import _project_guard
    from bestseller.services.pipelines import _checkpoint_commit, run_chapter_pipeline
    from bestseller.services.reviews import rewrite_chapter_from_task
    from bestseller.services.rewrite_patch import text_hash
    from bestseller.services.workflows import create_workflow_run

    # Native control precedes project. Commit before any model dispatch.
    with session.no_autoflush:
        project, _serial = await _project_guard(session, project_id, lock=True)
    old = deepcopy((project.metadata_json or {}).get(STATE_KEY))
    if outcome is None and (not old or old.get("status") != "running"):
        return False
    scope = current_execution_scope()
    _need(bool(scope), "whole_repair_native_budget_scope_required")
    budget = await session.scalar(
        select(ExecutionBudgetModel)
        .where(ExecutionBudgetModel.scope_id == scope)
        .execution_options(populate_existing=True)
    )
    _need(budget is not None, "whole_repair_native_budget_missing")
    if old and old.get("status") == "running":
        tasks = list(old["task_ids"])
        source_hash = old["source_sha256"]
    else:
        tasks = list(outcome["normal_rewrite_task_ids"])
        title_request = (project.metadata_json or {}).get("fanqie_whole_title_repair")
        if title_request and title_request.get("status") == "pending":
            tasks.append("title:" + title_request["source_sha256"])
        if not tasks:
            await _checkpoint_commit(session)
            return False
        source_hash = digest({"body": outcome["full_text"], "title": project.title})
    state = begin_round(
        old,
        source_sha256=source_hash,
        task_ids=tasks,
        scope_id=scope,
        calls_used=budget.calls_used,
        native_max_calls=(budget.calls_used + MAX_NEW_CALLS
                          if completion_authorization(budget) else budget.max_calls),
    )
    await _save(session, project, state)
    with bind_additional_call_ceiling(scope, state["absolute_call_ceiling"]):
        for identifier in state["task_ids"]:
            if identifier in state["completed_task_ids"]:
                continue
            await _require_fanqie_production_running(
                session,
                project_id=project.id,
                slug=project.slug,
                segment_number=project.target_chapters + 1,
                progress=progress,
            )
            if identifier.startswith("title:"):
                _need(
                    identifier
                    == "title:"
                    + (project.metadata_json or {})["fanqie_whole_title_repair"]["source_sha256"],
                    "whole_title_request_changed",
                )
                await _title(session, settings, project, premise=premise, requested_by=requested_by)
            else:
                task = await session.get(RewriteTaskModel, UUID(identifier), populate_existing=True)
                _need(
                    task is not None and task.project_id == project.id, "whole_repair_task_missing"
                )
                chapter = await session.get(
                    ChapterModel, task.trigger_source_id, populate_existing=True
                )
                _need(
                    chapter is not None and chapter.project_id == project.id,
                    "whole_repair_chapter_missing",
                )
                if task.status in {"pending", "queued"}:
                    workflow = await create_workflow_run(
                        session,
                        project_id=project.id,
                        workflow_type="fanqie_whole_chapter_repair",
                        scope_type="chapter",
                        scope_id=chapter.id,
                        requested_by=requested_by,
                        status=WorkflowStatus.RUNNING,
                        current_step="rewrite",
                        metadata={"source_task_id": identifier, "whole_round": state["round"]},
                    )
                    workflow_id = workflow.id
                    await _checkpoint_commit(session)
                    draft, task = await rewrite_chapter_from_task(
                        session,
                        project.slug,
                        chapter.chapter_number,
                        rewrite_task_id=task.id,
                        settings=settings,
                        workflow_run_id=workflow_id,
                    )
                    workflow.status = WorkflowStatus.COMPLETED.value
                    workflow.current_step = "candidate_produced_ungraded"
                    await _checkpoint_commit(session)
                elif task.status == "completed":
                    draft = await session.scalar(
                        select(ChapterDraftVersionModel).where(
                            ChapterDraftVersionModel.project_id == project.id,
                            ChapterDraftVersionModel.chapter_id == chapter.id,
                            ChapterDraftVersionModel.is_current.is_(True),
                        )
                    )
                else:
                    raise ValueError("whole_repair_task_not_dispatchable")
                _need(draft is not None, "whole_repair_candidate_missing")
                await run_chapter_pipeline(
                    session,
                    settings,
                    project.slug,
                    chapter.chapter_number,
                    requested_by=requested_by,
                    chapter_first=True,
                    export_markdown=False,
                    resume_existing_draft_only=True,
                    supersede_pending_rewrites=False,
                    review_source_binding={
                        "draft_id": str(draft.id),
                        "body_hash": text_hash(draft.content_md),
                    },
                    progress=progress,
                )
                await _checkpoint_commit(session)
                from bestseller.services.adopted_chapter_source import select_adopted_chapter_source
                from bestseller.services.adopted_knowledge_resume import resume_adopted_knowledge

                adopted, _ = await select_adopted_chapter_source(
                    session, chapter=chapter, lock=False
                )
                _need(
                    adopted is not None and adopted.id == draft.id,
                    "whole_repair_candidate_not_adopted",
                )
                await resume_adopted_knowledge(
                    session,
                    settings,
                    project=project,
                    chapter=chapter,
                    draft=adopted,
                    workflow_type="fanqie_whole_knowledge_resume",
                    require_bible=True,
                )
            state["completed_task_ids"] = [*state["completed_task_ids"], identifier]
            await _save(session, project, state)
    state["status"] = "completed"
    await _save(session, project, state)
    return True
