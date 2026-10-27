"""Operator-only, audited authorization of a bounded next execution phase.

Not registered as an LLM tool, retry hook, self-heal action or reconciliation.
Ordinary execution config can only tighten caps. A trusted transaction owner
must supply the operator, authorization reference and expected old limits here.
"""

import asyncio
from typing import Any
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from bestseller.infra.db.models import ExecutionBudgetModel, ExecutionOperationModel
from bestseller.services.budget import ExecutionBudgetError


async def authorize_execution_phase(
    engine: AsyncEngine,
    *,
    scope_id: str,
    idempotency_key: str,
    actor: str,
    reason: str,
    authorization_ref: str,
    expected_max_calls: int,
    expected_max_tokens: int,
    additional_calls: int,
    additional_tokens: int,
    additional_seconds: int = 0,
    renew_from_now: bool = False,
) -> dict:
    """Append audited permission; an expired window may restart for <=1 hour.

    The restart mode is time-only. It neither replenishes spend nor reopens an
    unresolved operation, and idempotent replays retain the first start time.
    """
    strings = (scope_id, idempotency_key, actor, reason, authorization_ref)
    if any(
        not isinstance(value, str) or not value.strip() or len(value) > 1024 for value in strings
    ):
        raise ValueError("explicit operator identity, reason and authorization reference required")
    for value, cap in (
        (additional_calls, 64),
        (additional_tokens, 1000000),
        (additional_seconds, 7200),
    ):
        if type(value) is not int or not 0 <= value <= cap:
            raise ValueError("phase allowance must be finite and within operator action bounds")
    if not any((additional_calls, additional_tokens, additional_seconds)):
        raise ValueError("empty phase authorization")
    if type(renew_from_now) is not bool:
        raise ValueError("renew_from_now must be boolean")
    if renew_from_now and (additional_calls or additional_tokens or not 1 <= additional_seconds <= 3600):
        raise ValueError("renewed window must be time-only and at most one hour")
    request = {
        "actor": actor,
        "reason": reason,
        "authorization_ref": authorization_ref,
        "expected_max_calls": expected_max_calls,
        "expected_max_tokens": expected_max_tokens,
        "additional_calls": additional_calls,
        "additional_tokens": additional_tokens,
        "additional_seconds": additional_seconds,
    }
    if renew_from_now:
        request["deadline_anchor"] = "authorization_time"
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with asyncio.timeout(5), factory() as session, session.begin():
        row = await session.scalar(
            select(ExecutionBudgetModel)
            .where(ExecutionBudgetModel.scope_id == scope_id)
            .with_for_update()
        )
        if row is None:
            raise ExecutionBudgetError("execution_scope_missing", scope_id)
        records = dict((row.metadata_json or {}).get("operator_authorizations") or {})
        prior = records.get(idempotency_key)
        if prior is not None:
            if prior.get("request") != request:
                raise ValueError("authorization key already bound to different action")
            return prior
        if row.status not in {"active", "exhausted"}:
            raise ExecutionBudgetError("execution_authorization_scope_frozen", scope_id)
        if (row.max_calls, row.max_tokens) != (expected_max_calls, expected_max_tokens):
            raise ExecutionBudgetError("execution_authorization_stale", scope_id)
        unresolved = await session.scalar(
            select(ExecutionOperationModel.key)
            .where(
                ExecutionOperationModel.scope_id == scope_id,
                ExecutionOperationModel.status.in_(["pending", "unknown"]),
            )
            .limit(1)
        )
        if unresolved:
            raise ExecutionBudgetError("execution_authorization_inflight", scope_id)
        deadline = row.deadline_at
        if deadline.tzinfo is None:
            deadline = deadline.replace(tzinfo=UTC)
        authorized_at = datetime.now(UTC)
        if renew_from_now and deadline > authorized_at:
            raise ValueError("renewed window requires an expired original window")
        if renew_from_now and (row.metadata_json or {}).get("provider_authorization"):
            raise ValueError("renewed window requires an original bounded scope")
        before = {
            "max_calls": row.max_calls,
            "max_tokens": row.max_tokens,
            "deadline_at": deadline.isoformat(),
            "status": row.status,
            "calls_used": row.calls_used,
            "tokens_charged": row.tokens_charged,
        }
        row.max_calls += additional_calls
        row.max_tokens += additional_tokens
        anchor = authorized_at if renew_from_now else deadline
        row.deadline_at = anchor + timedelta(seconds=additional_seconds)
        row.status = "active"
        after = {
            "max_calls": row.max_calls,
            "max_tokens": row.max_tokens,
            "deadline_at": row.deadline_at.isoformat(),
            "status": row.status,
            "calls_used": row.calls_used,
            "tokens_charged": row.tokens_charged,
        }
        receipt = {
            "request": request,
            "before": before,
            "after": after,
            "authorized_at": authorized_at.isoformat(),
            "policy": "operator-phase-authorization.v1",
        }
        records[idempotency_key] = receipt
        row.metadata_json = {**(row.metadata_json or {}), "operator_authorizations": records}
        return receipt


async def authorize_provider_completion(
    engine: AsyncEngine,
    *,
    scope_id: str,
    idempotency_key: str,
    actor: str,
    authorization_ref: str,
    objective: str,
    expected_max_calls: int,
    expected_max_tokens: int,
    model: str | None = None,
) -> dict:
    """Explicit MiniMax-only unlimited aggregate allowance for one objective.

    Historical ceilings, usage and deadline remain unchanged. This policy does
    not reconcile uncertain sends, undo cancellation, or authorize another
    provider. It is deliberately an operator API, never an agent retry hook.
    """
    values = (scope_id, idempotency_key, actor, authorization_ref, objective)
    if any(not isinstance(v, str) or not v.strip() or len(v) > 2048 for v in values):
        raise ValueError("explicit authorization and completion objective required")
    if model is not None and model not in {
        "openai/MiniMax-M3", "openai/MiniMax-M3.1-Flash",
    }:
        raise ValueError("completion authorization requires a supported MiniMax model")
    request = {
        "actor": actor,
        "authorization_ref": authorization_ref,
        "objective": objective,
        "expected_max_calls": expected_max_calls,
        "expected_max_tokens": expected_max_tokens,
    }
    if model is not None:
        request["model"] = model
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with asyncio.timeout(5), factory() as session, session.begin():
        row = await session.scalar(
            select(ExecutionBudgetModel)
            .where(ExecutionBudgetModel.scope_id == scope_id)
            .with_for_update()
        )
        if row is None:
            raise ExecutionBudgetError("execution_scope_missing", scope_id)
        metadata = dict(row.metadata_json or {})
        records = dict(metadata.get("provider_authorizations") or {})
        if idempotency_key in records:
            receipt = records[idempotency_key]
            if receipt["request"] != request:
                raise ValueError("authorization key already bound to different action")
            return receipt
        if row.status not in {"active", "exhausted"}:
            raise ExecutionBudgetError("execution_authorization_scope_frozen", scope_id)
        if (row.max_calls, row.max_tokens) != (expected_max_calls, expected_max_tokens):
            raise ExecutionBudgetError("execution_authorization_stale", scope_id)
        if await session.scalar(
            select(ExecutionOperationModel.key)
            .where(
                ExecutionOperationModel.scope_id == scope_id,
                ExecutionOperationModel.status.in_(["pending", "unknown"]),
            )
            .limit(1)
        ):
            raise ExecutionBudgetError("execution_authorization_inflight", scope_id)
        previous_policy = metadata.get("provider_authorization")
        if previous_policy:
            if previous_policy.get("status") != "completed":
                raise ExecutionBudgetError("execution_authorization_already_bound", scope_id)
            if previous_policy.get("authorization_ref") == authorization_ref:
                raise ValueError("a completed objective requires new operator authorization")
        policy = {
            "mode": "completion_bound",
            "provider": "minimax",
            "status": "active",
            "models": [model or "openai/MiniMax-M3"],
            "endpoint_hosts": ["api.minimaxi.com"],
            "objective": objective,
            "authorization_ref": authorization_ref,
            "aggregate_max_calls": None,
            "aggregate_max_tokens": None,
            "aggregate_deadline_at": None,
        }
        receipt = {
            "policy": "operator-provider-completion.v1",
            "request": request,
            "before": {
                "max_calls": row.max_calls,
                "max_tokens": row.max_tokens,
                "calls_used": row.calls_used,
                "tokens_charged": row.tokens_charged,
                "deadline_at": row.deadline_at.isoformat(),
                "status": row.status,
                "previous_provider_authorization": previous_policy,
            },
            "authorization": policy,
            "authorized_at": datetime.now(UTC).isoformat(),
        }
        records[idempotency_key] = receipt
        row.metadata_json = {
            **metadata,
            "provider_authorization": policy,
            "provider_authorizations": records,
        }
        row.status = "active"
        return receipt


async def close_provider_completion(
    engine: AsyncEngine,
    *,
    scope_id: str,
    actor: str,
    evidence_sha256: str,
) -> dict:
    """Close the operator objective after inspected delivery evidence exists."""
    import re

    if not actor or not re.fullmatch(r"[a-f0-9]{64}", evidence_sha256):
        raise ValueError("operator and delivery evidence digest required")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with asyncio.timeout(5), factory() as session, session.begin():
        row = await session.scalar(
            select(ExecutionBudgetModel)
            .where(ExecutionBudgetModel.scope_id == scope_id)
            .with_for_update()
        )
        from bestseller.services.budget import completion_authorization

        policy = completion_authorization(row) if row is not None else None
        if not policy:
            raise ExecutionBudgetError("execution_scope_missing", scope_id)
        history = dict((row.metadata_json or {}).get("completion_receipts") or {})
        prior = history.get(policy["authorization_ref"])
        if prior:
            if prior["evidence_sha256"] != evidence_sha256:
                raise ValueError("completion already bound to other evidence")
            return prior
        if row.status not in {"active", "exhausted"} or policy.get("status") != "active":
            raise ExecutionBudgetError("execution_authorization_scope_frozen", scope_id)
        if await session.scalar(
            select(ExecutionOperationModel.key)
            .where(
                ExecutionOperationModel.scope_id == scope_id,
                ExecutionOperationModel.status.in_(["pending", "unknown"]),
            )
            .limit(1)
        ):
            raise ExecutionBudgetError("execution_authorization_inflight", scope_id)
        receipt = {
            "actor": actor,
            "evidence_sha256": evidence_sha256,
            "completed_at": datetime.now(UTC).isoformat(),
            "calls_used": row.calls_used,
            "tokens_charged": row.tokens_charged,
            "objective": policy["objective"],
            "authorization_ref": policy["authorization_ref"],
        }
        history[policy["authorization_ref"]] = receipt
        row.metadata_json = {
            **row.metadata_json,
            "provider_authorization": {**policy, "status": "completed"},
            "completion_receipt": receipt,
            "completion_receipts": history,
        }
        return receipt


async def set_provider_completion_pause(
    engine: AsyncEngine,
    *,
    scope_id: str,
    paused: bool,
    actor: str,
    reason: str,
    require_idle: bool = False,
) -> dict:
    """Operator stage handoff: stop new sends, retaining in-flight receipts.

    Does not cancel transport, reconcile unknowns, or clear a user stop. Resume
    requires the previous dispatcher to have settled all potentially paid work.
    """
    if not actor.strip() or not reason.strip():
        raise ValueError("operator and reason required")
    if type(paused) is not bool or type(require_idle) is not bool:
        raise ValueError("pause controls must be boolean")
    from bestseller.services.budget import completion_authorization

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with asyncio.timeout(5), factory() as session, session.begin():
        row = await session.scalar(
            select(ExecutionBudgetModel)
            .where(ExecutionBudgetModel.scope_id == scope_id)
            .with_for_update()
        )
        policy = completion_authorization(row) if row is not None else None
        if not policy or row.status not in {"active", "exhausted"}:
            raise ExecutionBudgetError("execution_authorization_scope_frozen", scope_id)
        if policy.get("status") not in {"active", "paused"}:
            raise ExecutionBudgetError("execution_authorization_closed", scope_id)
        if (not paused or require_idle) and await session.scalar(
            select(ExecutionOperationModel.key)
            .where(
                ExecutionOperationModel.scope_id == scope_id,
                ExecutionOperationModel.status.in_(["pending", "unknown"]),
            )
            .limit(1)
        ):
            raise ExecutionBudgetError("execution_authorization_inflight", scope_id)
        desired = "paused" if paused else "active"
        history = list((row.metadata_json or {}).get("provider_pause_history") or [])
        if policy["status"] != desired:
            history.append(
                {
                    "actor": actor,
                    "reason": reason,
                    "status": desired,
                    "at": datetime.now(UTC).isoformat(),
                }
            )
            row.metadata_json = {
                **row.metadata_json,
                "provider_authorization": {**policy, "status": desired},
                "provider_pause_history": history,
            }
        return {"scope_id": scope_id, "status": desired, "history": history}


async def supersede_obsolete_limit_terminal(
    session, *, project_id, scope_id: str, authorization_ref: str,
    expected_code: str, evidence_sha256: str, actor: str,
) -> dict:
    """Operator-only correction after authoritative current limits are proven open.

    This never clears unknown sends, cancellation, operator intent or retry debt.
    It archives the exact old terminal, retaining cumulative use and deadlines.
    """
    import re
    from bestseller.infra.db.models import ProjectModel
    from bestseller.services.budget import completion_authorization, execution_scope_block_code
    from bestseller.services.retry_ledger import execution_terminal_code, project_execution_scope

    if (expected_code not in {'execution_deadline_exhausted', 'execution_budget_exhausted'}
        or not actor or not re.fullmatch(r'[a-f0-9]{64}', evidence_sha256)):
        raise ValueError('explicit_limit_correction_evidence_required')
    budget = await session.scalar(select(ExecutionBudgetModel).where(
        ExecutionBudgetModel.scope_id == scope_id).with_for_update())
    policy = completion_authorization(budget) if budget is not None else None
    if (policy is None or policy.get('authorization_ref') != authorization_ref
        or execution_scope_block_code(budget) is not None):
        raise ValueError('limit_correction_scope_not_authorized_open')
    if await session.scalar(select(ExecutionOperationModel.key).where(
        ExecutionOperationModel.scope_id == scope_id,
        ExecutionOperationModel.status.in_(['pending','unknown'])).limit(1)):
        raise ValueError('limit_correction_inflight')
    project = await session.scalar(select(ProjectModel).where(ProjectModel.id == project_id).with_for_update())
    if project is None or project_execution_scope(project) != scope_id:
        raise ValueError('limit_correction_wrong_project_scope')
    metadata = dict(project.metadata_json or {})
    history = dict(metadata.get('operator_limit_terminal_corrections') or {})
    key = authorization_ref + ':' + evidence_sha256
    if key in history:
        prior = history[key]
        # Earlier corrections retained the terminal's legacy pause boolean.
        # With its reason retired it becomes an unknown structural stop. Retire
        # only that exact derived projection; durable operator control and all
        # explicit structural markers remain independent and unchanged.
        projection_key = key + ':pause-projection.v2'
        if projection_key in history:
            return history[projection_key]
        if (prior.get('schema') == 'operator-limit-terminal-correction.v1'
            and prior.get('original_fields', {}).get('production_pause_reason') == expected_code
            and execution_terminal_code(metadata) is None
            and metadata.get('production_paused') is True
            and not metadata.get('production_pause_reason')
            and not metadata.get('last_generation_gate_reason')):
            metadata.pop('production_paused')
            receipt = {**prior, 'schema': 'operator-limit-terminal-correction.v2',
                'recorded_at': datetime.now(UTC).isoformat(),
                'supersedes_correction_key': key,
                'retired_projection': {'production_paused': True}}
            history[projection_key] = receipt
            project.metadata_json = {**metadata, 'operator_limit_terminal_corrections': history}
            await session.flush()
            return receipt
        return prior
    if execution_terminal_code(metadata) != expected_code:
        raise ValueError('limit_correction_terminal_changed')
    original = {k: metadata.get(k) for k in ('execution_terminal', 'production_paused',
        'production_pause_reason', 'last_generation_gate_reason', 'requires_human_review') if k in metadata}
    metadata.pop('execution_terminal', None)
    for field in ('production_pause_reason', 'last_generation_gate_reason'):
        if metadata.get(field) == expected_code:
            metadata.pop(field)
    metadata.pop('production_paused', None)
    if execution_terminal_code(metadata) is not None:
        raise ValueError('limit_correction_independent_terminal_remains')
    receipt = {'schema': 'operator-limit-terminal-correction.v2', 'recorded_at': datetime.now(UTC).isoformat(),
        'actor': actor, 'authorization_ref': authorization_ref, 'evidence_sha256': evidence_sha256,
        'original_fields': original, 'scope_id': scope_id, 'project_id': str(project_id),
        'calls_used': budget.calls_used, 'tokens_charged': budget.tokens_charged,
        'operator_control_unchanged': True, 'budget_unchanged': True}
    history[key] = receipt
    project.metadata_json = {**metadata, 'operator_limit_terminal_corrections': history}
    await session.flush()
    return receipt


async def revise_provider_completion_objective(
    session: AsyncSession, *, scope_id: str, expected_authorization_sha256: str,
    authorization_ref: str, objective: str, actor: str,
    explicit_user_approval: bool = False,
) -> dict[str, Any]:
    """Audited objective update, never budget growth or old-objective completion.

    Trusted operator only. Preserve paused controls, all limits/usage, previous
    authorizations and uncertain operations. No provider/model changes allowed.
    Caller commits this together with the corresponding content revision.
    """
    from copy import deepcopy
    from bestseller.services.prompt_source_policy import content_hash
    from bestseller.services.budget import completion_authorization
    if (explicit_user_approval is not True or not actor.strip()
            or not authorization_ref.strip() or not objective.strip()
            or len(objective) > 2048):
        raise ValueError("explicit_objective_revision_authorization_required")
    row = await session.scalar(select(ExecutionBudgetModel).where(
        ExecutionBudgetModel.scope_id == scope_id).with_for_update())
    policy = completion_authorization(row) if row is not None else None
    if row is None or policy is None or row.status not in {"active", "exhausted"}:
        raise ExecutionBudgetError("execution_authorization_scope_frozen", scope_id)
    records = dict((row.metadata_json or {}).get("provider_objective_revisions") or {})
    previous = records.get(authorization_ref)
    if previous is not None:
        if (previous["objective"] != objective or previous["actor"] != actor
                or previous["expected_authorization_sha256"] != expected_authorization_sha256
                or policy.get("authorization_ref") != authorization_ref):
            raise ValueError("objective_revision_idempotence_conflict")
        return dict(previous)
    if (content_hash(policy) != expected_authorization_sha256
            or policy.get("status") not in {"active", "paused"}
            or authorization_ref == policy.get("authorization_ref")):
        raise ValueError("objective_revision_stale_authorization")
    if await session.scalar(select(ExecutionOperationModel.key).where(
            ExecutionOperationModel.scope_id == scope_id,
            ExecutionOperationModel.status.in_(["pending", "unknown"])).limit(1)):
        raise ExecutionBudgetError("execution_authorization_inflight", scope_id)
    receipt = {"schema": "operator-objective-revision.v1", "actor": actor,
        "authorization_ref": authorization_ref, "objective": objective,
        "expected_authorization_sha256": expected_authorization_sha256,
        "previous_authorization": deepcopy(policy), "at": datetime.now(UTC).isoformat(),
        "calls_used": row.calls_used, "tokens_charged": row.tokens_charged,
        "old_objective_marked_complete": False, "limits_changed": False,
        "unknown_or_spent_exceptions_granted": False}
    records[authorization_ref] = receipt
    row.metadata_json = {**(row.metadata_json or {}),
        "provider_authorization": {**policy, "objective": objective,
            "authorization_ref": authorization_ref},
        "provider_objective_revisions": records}
    await session.flush()
    return receipt
