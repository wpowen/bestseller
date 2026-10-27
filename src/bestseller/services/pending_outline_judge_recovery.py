"""One durable judge-only continuation of an exact failed planning checkpoint.

No generation, materialization, adoption, source repair or financial reset.
Inspection never writes. Execution owns its claim/result commits, as does the
normal planner checkpoint before/after the judge. A failed identity stays shut.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime
import hashlib
import json
from pathlib import Path
import re
import traceback
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import inspect, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.domain.llm_quality_judge import LLMQualityJudgeResult
from bestseller.infra.db.models import (
    ExecutionAttemptModel,
    ExecutionBudgetModel,
    ExecutionOperationModel,
    LlmRunModel,
    PlanningArtifactVersionModel,
    ProjectModel,
    WorkflowRunModel,
)
from bestseller.services import foundation_selection, planner
from bestseller.services.budget import (
    bind_execution_scope,
    completion_authorization,
    current_execution_scope,
    execution_scope_block_code,
)
from bestseller.services.llm import bind_execution_operation
from bestseller.services.outline_llm_judge import require_available_outline_judgement
from bestseller.services.outline_recovery_checkpoint import outline_window, recovery_binding
from bestseller.services.prompt_source_policy import content_hash
from bestseller.services.rolling_outline import rolling_window_schedule_hash
from bestseller.settings import AppSettings

# ruff: noqa: ANN401 -- Full immutable SQL/JSON preimages contain heterogeneous values.

POLICY = "pending-outline-judge-recovery.v1"
WORKFLOW_TYPE = "pending_outline_judge_recovery"
_OUTPUT_KEYS = frozenset(
    {
        "outline_commercial_judge_checkpoints",
        "outline_commercial_repair_baseline",
        "outline_commercial_repair_baseline_kind",
        "outline_repair_dependency_hash",
        "outline_commercial_repair_baseline_revision_key",
        "outline_commercial_repair_directives",
        "outline_commercial_last_failure",
    }
)


def _need(ok: object, code: str) -> None:
    if not ok:
        raise ValueError("pending_outline_judge_" + code)


def _failure_diagnostic(exc: BaseException) -> dict:
    """Never stringify SQLAlchemy wrappers, SQL statements, params or locals."""
    diagnostic = {
        "type": type(exc).__name__,
        "frames": [
            {"filename": frame.filename, "lineno": frame.lineno, "function": frame.name}
            for frame in traceback.extract_tb(exc.__traceback__, limit=16)
        ],
    }
    if isinstance(exc, SQLAlchemyError):
        original = getattr(exc, "orig", None)
        diagnostic["driver_type"] = type(original).__name__ if original is not None else None
        diagnostic["sqlstate"] = next(
            (
                code
                for code in (getattr(original, "sqlstate", None), getattr(original, "pgcode", None))
                if isinstance(code, str)
            ),
            None,
        )
        diagnostic["driver_message"] = (
            str(original) if isinstance(original, BaseException) else None
        )
    else:
        diagnostic["message"] = str(exc)
    return diagnostic


def _wire(value: Any) -> Any:
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _wire(child) for key, child in value.items()}
    if isinstance(value, (list, tuple)):
        return [_wire(child) for child in value]
    return value


def _hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            _wire(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


def _row(row: Any) -> dict:
    return _wire({field.key: getattr(row, field.key) for field in inspect(type(row)).column_attrs})


def _source_metadata(metadata: dict) -> dict:
    return deepcopy({key: value for key, value in metadata.items() if key not in _OUTPUT_KEYS})


def _policy_sources() -> dict[str, str]:
    names = (
        "planner.py",
        "outline_llm_judge.py",
        "outline_recovery_checkpoint.py",
        "foundation_selection.py",
        "prompt_packs.py",
        "prompt_source_policy.py",
    )
    root = Path(planner.__file__).parent
    return {
        **{name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in names},
        Path(__file__).name: hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    }


def _identity(project_id: UUID, source_workflow_id: UUID, candidate_hash: str) -> UUID:
    return uuid5(
        NAMESPACE_URL,
        f"bestseller:pending-outline-judge:{project_id}:{source_workflow_id}:{candidate_hash}",
    )


async def _sources(
    session: AsyncSession,
    settings: AppSettings,
    project_id: UUID,
    source_id: UUID,
    digest: str,
    *,
    archived: dict | None = None,
    lock: bool = False,
) -> tuple[ProjectModel, dict, dict, dict]:
    project_query = select(ProjectModel).where(ProjectModel.id == project_id).execution_options(
        populate_existing=True
    )
    workflow_query = select(WorkflowRunModel).where(WorkflowRunModel.id == source_id).execution_options(
        populate_existing=True
    )
    project = await session.scalar(project_query.with_for_update() if lock else project_query)
    old = await session.scalar(workflow_query.with_for_update() if lock else workflow_query)
    _need(project is not None and old is not None, "source_missing")
    meta = project.metadata_json
    _need(isinstance(meta, dict) and isinstance(old.metadata_json, dict), "source_metadata")
    _need(
        old.project_id == project.id
        and old.workflow_type == "generate_volume_plan"
        and old.scope_type == "volume"
        and old.scope_id == project.id
        and old.status == "failed"
        and re.search(
            r"(?<![a-z_])llm_project_and_workflow_required(?![a-z_])", old.error_message or ""
        )
        is not None,
        "failed_origin",
    )
    scope = old.metadata_json.get("execution_scope_id")
    _need(
        isinstance(scope, str)
        and 0 < len(scope) <= 256
        and scope.startswith(("conception:", "project:", "workflow:")),
        "original_scope",
    )
    _need(
        meta.get("execution_scope_id", scope) == scope
        and current_execution_scope() in (None, scope),
        "scope_conflict",
    )
    checkpoints = meta.get("outline_commercial_judge_checkpoints")
    _need(isinstance(checkpoints, dict), "checkpoint_required")
    owner_id = _identity(project_id, source_id, digest)
    if archived is None:
        records = [
            (key, bucket, bucket.get("latest_candidate"))
            for key, bucket in checkpoints.items()
            if isinstance(bucket, dict)
            and isinstance(bucket.get("latest_candidate"), dict)
            and bucket["latest_candidate"].get("workflow_run_id") == str(source_id)
            and bucket["latest_candidate"].get("candidate_hash") == digest
        ]
        _need(len(records) == 1, "unique_latest_pending_required")
        key, bucket, record = records[0]
    else:
        key = archived["checkpoint_key"]
        bucket = checkpoints.get(key)
        _need(isinstance(bucket, dict), "checkpoint_changed")
        record = archived["original_pending_record"]
    candidate = record.get("candidate")
    window = outline_window(candidate) if isinstance(candidate, dict) else None
    _need(
        window is not None
        and content_hash(candidate) == digest
        and record.get("candidate_hash") == digest
        and record.get("report") is None
        and record.get("judgement_status") == "pending"
        and record.get("workflow_run_id") == str(source_id),
        "pending_candidate",
    )
    binding = record.get("binding")
    _need(
        isinstance(binding, dict)
        and type(binding.get("volume_number")) is int
        and binding["volume_number"] > 0,
        "volume_binding",
    )
    volume = binding["volume_number"]
    _need(
        binding == recovery_binding(meta, volume, window) and key == content_hash(binding),
        "current_source_binding",
    )
    _need(
        all(
            type(ch.get("volume_number")) is int and ch["volume_number"] == volume
            for ch in candidate["chapters"]
        ),
        "candidate_volume",
    )
    for field, expected in (
        ("outline_repair_dependency_hash", binding["dependency_hash"]),
        ("outline_repair_source_snapshot_hash", binding["source_snapshot_hash"]),
        ("outline_repair_window", window),
    ):
        _need(old.metadata_json.get(field) == expected, "origin_checkpoint_binding")
    active = meta.get("rolling_outline_plan")
    schedule = meta.get("rolling_outline_windows")
    _need(
        isinstance(active, dict)
        and [active.get("window_start"), active.get("window_end")] == window
        and active.get("source_snapshot_hash") == binding["source_snapshot_hash"],
        "active_window",
    )
    _need(
        isinstance(schedule, list)
        and bool(schedule)
        and all(isinstance(item, dict) for item in schedule)
        and meta.get("rolling_outline_windows_hash") == rolling_window_schedule_hash(schedule),
        "schedule_hash",
    )
    next_chapter, ordinals = 1, {}
    for item in schedule:
        _need(
            all(
                type(item.get(k)) is int and item[k] > 0
                for k in ("volume_number", "window_index", "window_start", "window_end")
            ),
            "schedule",
        )
        ordinal = ordinals.get(item["volume_number"], 0) + 1
        _need(
            item["window_start"] == next_chapter
            and item["window_end"] >= item["window_start"]
            and item["window_index"] == ordinal,
            "schedule",
        )
        next_chapter = item["window_end"] + 1
        ordinals[item["volume_number"]] = ordinal
    _need(
        next_chapter == project.target_chapters + 1
        and sum(
            item["volume_number"] == volume and [item["window_start"], item["window_end"]] == window
            for item in schedule
        )
        == 1,
        "scheduled_window",
    )
    latest = bucket.get("latest_candidate")
    _need(
        isinstance(latest, dict)
        and latest.get("candidate_hash") == digest
        and latest.get("candidate") == candidate
        and latest.get("binding") == binding
        and latest.get("workflow_run_id") in {str(source_id), str(owner_id)},
        "latest_candidate_changed",
    )
    if archived is None:
        _need(latest == record, "latest_candidate_changed")
    else:
        _need(
            {k: v for k, v in checkpoints.items() if k != key} == archived["other_checkpoints"],
            "other_checkpoint_changed",
        )
    pointer = meta.get(foundation_selection.POINTER)
    _need(isinstance(pointer, dict), "selected_foundation_required")
    selected = await foundation_selection.select_completed_foundation(
        session, project=project, workflow_id=UUID(pointer["workflow_id"]), settings=settings
    )
    _need(pointer == selected, "selected_foundation_changed")
    assets = {}
    for kind in foundation_selection.KINDS:
        row = await session.get(
            PlanningArtifactVersionModel,
            UUID(selected["artifact_ids"][kind]),
            populate_existing=True,
        )
        _need(
            row is not None
            and row.project_id == project.id
            and row.artifact_type == kind
            and row.status == "approved"
            and content_hash(row.content) == selected["artifact_content_hashes"][kind],
            "selected_asset_changed",
        )
        assets[kind] = row
    canonical = await session.get(
        PlanningArtifactVersionModel,
        UUID(selected["canonical_source"]["id"]),
        populate_existing=True,
    )
    _need(canonical is not None and canonical.project_id == project.id, "canonical_source")
    source = {
        "schema": POLICY,
        "project_id": str(project.id),
        "source_workflow_id": str(source_id),
        "candidate_hash": digest,
        "execution_scope_id": scope,
        "checkpoint_key": key,
        "original_pending_record": deepcopy(record),
        "original_workflow_snapshot": _row(old),
        "project_source_metadata": _source_metadata(meta),
        "project_fields": {
            name: _wire(getattr(project, name))
            for name in (
                "slug",
                "title",
                "genre",
                "sub_genre",
                "language",
                "target_chapters",
                "target_word_count",
                "reader_contract_json",
                "hype_scheme_json",
            )
        },
        "other_checkpoints": deepcopy({k: v for k, v in checkpoints.items() if k != key}),
        "selection": deepcopy(selected),
        "canonical_source_row": _row(canonical),
        "selected_asset_rows": {kind: _row(row) for kind, row in assets.items()},
        "source_code_sha256": _policy_sources(),
        "settings_sha256": _hash(settings.model_dump(mode="json")),
        "operation_namespace": f"pending-outline-judge:{source_id}:{digest}",
        "judge_only": True,
        "adoption_allowed": False,
        "materialization_allowed": False,
        "attempt_reset_allowed": False,
        "credit_granted": False,
    }
    return project, source, assets, latest


async def _admit_scope(session: AsyncSession, scope: str) -> dict:
    budget = await session.get(ExecutionBudgetModel, scope, populate_existing=True)
    auth = completion_authorization(budget) if budget is not None else None
    _need(
        budget is not None
        and execution_scope_block_code(budget) is None
        and auth is not None
        and auth.get("status") == "active",
        "completion_authorization",
    )
    _need(
        await session.scalar(
            select(ExecutionOperationModel.key)
            .where(
                ExecutionOperationModel.scope_id == scope,
                ExecutionOperationModel.status.in_(("pending", "unknown")),
            )
            .limit(1)
        )
        is None,
        "unsettled_scope",
    )
    _need(
        await session.scalar(
            select(ExecutionAttemptModel.id)
            .where(
                ExecutionAttemptModel.scope_id == scope,
                ExecutionAttemptModel.status.in_(("reserved", "pending", "unknown")),
            )
            .limit(1)
        )
        is None,
        "unsettled_attempt",
    )
    return {"authorization": deepcopy(auth), "budget_before": _row(budget)}


async def _available_report(
    session: AsyncSession,
    record: dict,
    plan: dict,
    owner_id: UUID,
) -> dict:
    _need(
        record.get("workflow_run_id") == str(owner_id)
        and record.get("judgement_status") == "judged"
        and record.get("candidate_hash") == plan["candidate_hash"],
        "judge_result_binding",
    )
    raw = record.get("report")
    _need(
        isinstance(raw, dict)
        and raw.get("graded_outline_hash") == plan["candidate_hash"]
        and raw.get("outline_repair_dependency_hash")
        == plan["original_pending_record"]["binding"]["dependency_hash"],
        "judge_result_hash",
    )
    result = LLMQualityJudgeResult.model_validate(raw)
    require_available_outline_judgement(result)
    _need(result.llm_run_id is not None, "real_judge_run_required")
    run = await session.get(LlmRunModel, UUID(result.llm_run_id), populate_existing=True)
    _need(
        run is not None
        and str(run.project_id) == plan["project_id"]
        and run.workflow_run_id == owner_id
        and run.logical_role == "critic"
        and run.prompt_template == "outline_commercial_judge"
        and run.provider not in {None, "", "mock", "fallback", "fixture", "test"}
        and run.finish_reason in {"stop", "end_turn", "completed"},
        "real_judge_custody",
    )
    return deepcopy(raw)


def _result(owner: WorkflowRunModel, plan: dict) -> dict:
    meta = owner.metadata_json
    return {
        "schema": POLICY,
        "status": owner.status,
        "workflow_run_id": str(owner.id),
        "source_workflow_id": plan["source_workflow_id"],
        "candidate_hash": plan["candidate_hash"],
        "plan_sha256": _hash(plan),
        "judge_only": True,
        "full_window_production_completed": False,
        "adoption_allowed": False,
        "credit_granted": False,
        "report": deepcopy(meta.get("report")),
        "error": deepcopy(meta.get("failure")),
    }


async def recover_pending_outline_judgement(
    session: AsyncSession,
    settings: AppSettings,
    *,
    project_id: UUID | str,
    source_workflow_id: UUID | str,
    candidate_hash: str,
    requested_by: str,
    execute: bool = False,
    expected_plan_sha256: str | None = None,
) -> dict:
    """Inspect or resume this single exact candidate; execute commits claim/results.

    First execution requires the reviewed inspect hash. Same identity/plan only
    may resume RUNNING; FAILED is terminal, COMPLETED returns its saved report.
    Inspector owns rollback; this service never changes the old failed WF.
    """
    project_id, source_id = UUID(str(project_id)), UUID(str(source_workflow_id))
    _need(
        isinstance(candidate_hash, str) and re.fullmatch(r"[0-9a-f]{64}", candidate_hash),
        "candidate_hash",
    )
    _need(isinstance(requested_by, str) and 0 < len(requested_by.strip()) <= 64, "actor")
    _need(not session.in_nested_transaction(), "nested_transaction")
    _need(not session.new and not session.dirty and not session.deleted, "clean_session_required")
    owner_id = _identity(project_id, source_id, candidate_hash)
    with session.no_autoflush:
        owner = await session.get(WorkflowRunModel, owner_id, populate_existing=True)
        archived = owner.metadata_json.get("plan") if owner is not None else None
        project, plan, assets, latest = await _sources(
            session, settings, project_id, source_id, candidate_hash, archived=archived, lock=execute
        )
        digest = _hash(plan)
        if owner is not None:
            _need(
                owner.project_id == project_id
                and owner.workflow_type == WORKFLOW_TYPE
                and owner.scope_type == "volume"
                and owner.scope_id == project_id
                and owner.metadata_json.get("schema") == POLICY
                and archived == plan
                and owner.metadata_json.get("plan_sha256") == digest
                and owner.metadata_json.get("execution_scope_id") == plan["execution_scope_id"]
                and owner.idempotency_key == str(owner_id),
                "existing_identity_or_plan_changed",
            )
        if expected_plan_sha256 is not None:
            _need(expected_plan_sha256 == digest, "plan_changed")
        if owner is not None and owner.status == "completed":
            report = await _available_report(session, latest, plan, owner_id)
            _need(report == owner.metadata_json.get("report"), "cached_result_changed")
            return {**_result(owner, plan), "reused": True}
        if owner is not None and owner.status == "failed":
            return {**_result(owner, plan), "terminal": True}
        authorization = await _admit_scope(session, plan["execution_scope_id"])
        if not execute:
            return {
                "schema": POLICY,
                "status": "prepared",
                "workflow_run_id": str(owner_id),
                "plan_sha256": digest,
                "plan": plan,
                "authorization": authorization,
                "provider_calls": 0,
                "database_writes": False,
            }
        _need(expected_plan_sha256 == digest, "reviewed_plan_required")
        if owner is None:
            owner = WorkflowRunModel(
                id=owner_id,
                project_id=project_id,
                workflow_type=WORKFLOW_TYPE,
                scope_type="volume",
                scope_id=project_id,
                idempotency_key=str(owner_id),
                status="running",
                requested_by=requested_by,
                current_step="pending_outline_judge",
                metadata_json={
                    "schema": POLICY,
                    "plan": deepcopy(plan),
                    "plan_sha256": digest,
                    "execution_scope_id": plan["execution_scope_id"],
                    "operation_namespace": plan["operation_namespace"],
                    "authorization_at_claim": authorization,
                    "requested_by": requested_by,
                },
            )
            session.add(owner)
        else:
            _need(owner.status == "running", "unsupported_owner_status")
        await session.flush()
        await session.commit()  # Full origin/archive/plan durable BEFORE any judge request.
    try:
        with (
            bind_execution_scope(plan["execution_scope_id"]),
            bind_execution_operation(plan["operation_namespace"], sample_slot=0),
        ):
            project, current, assets, latest = await _sources(
                session, settings, project_id, source_id, candidate_hash, archived=plan, lock=True
            )
            _need(current == plan, "pre_dispatch_source_changed")
            # A crash after the normal report checkpoint needs no second judge.
            if latest.get("report") is None:
                try:
                    await planner._run_planner_outline_commercial_judge(
                        session,
                        settings,
                        project=project,
                        workflow_run_id=owner_id,
                        step_name=f"volume_{plan['original_pending_record']['binding']['volume_number']}"
                        "_outline_commercial_judge",
                        step_order=1,
                        outline_payload=deepcopy(plan["original_pending_record"]["candidate"]),
                        world_spec=deepcopy(assets["world_spec"].content),
                    )
                except planner.PlannerFallbackError:
                    # Only an available, exact, already-checkpointed real verdict
                    # can convert the strict quality-block exception into DONE.
                    await session.flush()
                    _, current, _, latest = await _sources(
                        session, settings, project_id, source_id, candidate_hash, archived=plan, lock=True
                    )
                    _need(current == plan, "terminal_source_changed")
                    await _available_report(session, latest, plan, owner_id)
            await session.flush()
            _, current, _, latest = await _sources(
                session, settings, project_id, source_id, candidate_hash, archived=plan, lock=True
            )
            _need(current == plan, "terminal_source_changed")
            report = await _available_report(session, latest, plan, owner_id)
            owner = await session.get(WorkflowRunModel, owner_id)
            owner.metadata_json = {
                **owner.metadata_json,
                "report": report,
                "judgement_completed_not_production": True,
            }
            owner.status, owner.current_step = "completed", "judgement_completed"
            await session.commit()
            return _result(owner, plan)
    except BaseException as exc:
        diagnostic = _failure_diagnostic(exc)
        await session.rollback()
        owner = await session.get(WorkflowRunModel, owner_id, populate_existing=True)
        if owner is not None:
            owner.metadata_json = {
                **owner.metadata_json,
                "failure": {
                    **diagnostic,
                    "paid_state_retained": True,
                    "retry_credit_granted": False,
                },
            }
            owner.status, owner.current_step = "failed", "judge_failed"
            owner.error_message = json.dumps(diagnostic, ensure_ascii=False, allow_nan=False)
            await session.commit()
        raise
