from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, contextmanager
import contextvars
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import hashlib
import json
import logging
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from bestseller.infra.db.models import (
    ExecutionAttemptModel,
    ExecutionBudgetModel,
    ExecutionOperationModel,
    LlmRunModel,
)
from bestseller.settings import AppSettings, BudgetSettings

logger = logging.getLogger(__name__)


def completion_authorization(row: ExecutionBudgetModel) -> dict | None:
    policy = (row.metadata_json or {}).get("provider_authorization")
    if (isinstance(policy, dict) and policy.get("mode") == "completion_bound"
        and policy.get("authorization_ref") and policy.get("models")
        and policy.get("endpoint_hosts") and policy.get("objective")):
        return policy
    return None


def execution_scope_block_code(row: ExecutionBudgetModel, *, now: datetime | None = None) -> str | None:
    """One read-only policy for worker admission and the paid gateway."""
    if row.status == "unknown":
        return "execution_result_unknown"
    if row.status == "awaiting_submission":
        return "execution_submission_not_authorized"
    if row.status == "cancelled":
        return "execution_cancelled"
    policy = completion_authorization(row)
    if policy:
        if policy.get("status") == "paused":
            return "execution_authorization_paused"
        if policy.get("status") != "active":
            return "execution_authorization_closed"
        return None
    if (now or datetime.now(timezone.utc)) >= _utc(row.deadline_at):
        return "execution_deadline_exhausted"
    if row.status == "exhausted":
        return "execution_budget_exhausted"
    return None


def execution_snapshot(row: ExecutionBudgetModel) -> dict[str, Any]:
    """Project current permission without changing persisted limits or history.

    Legacy max/deadline fields retain their stored values. Consumers should use
    effective_limits for aggregate ceilings and block_code/execution_open for
    permission; a completion-bound objective has no aggregate cap even while
    paused or closed. Provider/model restrictions still apply at reservation.
    """
    policy = completion_authorization(row)
    block_code = execution_scope_block_code(row)
    deadline = _utc(row.deadline_at).isoformat()
    return {
        "scope_id": row.scope_id, "status": row.status,
        "calls_used": row.calls_used, "tokens_charged": row.tokens_charged,
        "max_calls": row.max_calls, "max_tokens": row.max_tokens,
        "deadline_at": deadline,
        "provider_authorization": policy,
        "mode": "completion_bound" if policy else "bounded",
        "effective_limits": {
            "max_calls": None if policy else row.max_calls,
            "max_tokens": None if policy else row.max_tokens,
            "deadline_at": None if policy else deadline,
        },
        "block_code": block_code,
        "execution_open": block_code is None,
    }


class ExecutionBudgetError(RuntimeError):
    """Terminal paid-execution boundary; never convert to prose/fallback/retry."""

    terminal = True

    def __init__(self, code: str, scope_id: str = "") -> None:
        self.code = code
        self.scope_id = scope_id
        super().__init__(code)  # no DB/provider messages, keys or prompt text


class ExecutionOperationBusyError(ExecutionBudgetError):
    """Another owner holds this exact operation; no new send or global latch."""

    terminal = False


class ExecutionProviderNotSentError(ExecutionBudgetError):
    """An admitted request failed locally before HTTP; repair the attempt."""

    terminal = False


class ExecutionRepairCallLimitError(ExecutionBudgetError):
    """A persisted repair sub-limit; the global budget remains unchanged."""

    terminal = False


_EXECUTION_SCOPE = contextvars.ContextVar("execution_budget_scope", default=None)
_ADDITIONAL_CALL_CEILINGS = contextvars.ContextVar("additional_call_ceilings", default=())
_PROCESS_EPOCH = uuid4().hex[:16]


@contextmanager
def bind_additional_call_ceiling(scope_id: str, absolute_max_calls: int):
    """Narrow native admission for a bounded repair, without granting credit.

    The transaction owner persists the absolute ceiling across process restarts.
    Native budget, Unknown, provider and owner checks remain authoritative.
    Nested callers can only tighten the smallest ceiling for the same scope.
    """
    if not scope_id or type(absolute_max_calls) is not int or absolute_max_calls < 1:
        raise ValueError("whole_repair_call_ceiling_invalid")
    token = _ADDITIONAL_CALL_CEILINGS.set(
        (*_ADDITIONAL_CALL_CEILINGS.get(), (scope_id, absolute_max_calls))
    )
    try:
        yield
    finally:
        _ADDITIONAL_CALL_CEILINGS.reset(token)


@dataclass
class _ScopeState:
    scope_id: str
    stopped: ExecutionBudgetError | None = None
    invocation_slots: dict[str, int] = field(default_factory=dict)
    owner_epoch: str = field(default_factory=lambda: uuid4().hex[:16])


@contextmanager
def bind_execution_scope(scope_id: str):
    """Code-side task identity; callers must preserve it across workflow resumes."""
    if not scope_id or len(scope_id) > 256:
        raise ValueError("invalid execution scope")
    previous = _EXECUTION_SCOPE.get()
    state = (
        previous
        if previous is not None and previous.scope_id == scope_id
        else _ScopeState(scope_id)
    )
    token = _EXECUTION_SCOPE.set(state)
    try:
        yield
    finally:
        _EXECUTION_SCOPE.reset(token)


def current_execution_scope() -> str | None:
    state = _EXECUTION_SCOPE.get()
    return state.scope_id if state is not None else None


def record_execution_stop(error: ExecutionBudgetError) -> None:
    state = _EXECUTION_SCOPE.get()
    if state is not None and error.terminal:
        state.stopped = error


def raise_if_execution_stopped() -> None:
    state = _EXECUTION_SCOPE.get()
    if state is not None and state.stopped is not None:
        raise state.stopped


def next_execution_operation_slot(key: str) -> int | None:
    state = _EXECUTION_SCOPE.get()
    if state is None:
        return None  # no stable dispatcher: require explicit ID for replay
    slot = state.invocation_slots.get(key, 0)
    state.invocation_slots[key] = slot + 1
    return slot


def execution_operation_id(stage: str, *, subject: str, revision: str) -> str:
    """Stable business identity, independent of retry/workflow/process IDs.

    revision must identify the adopted input/parent version. Independent draws
    belong in LLMCompletionRequest.sample_slot, never in a random operation id.
    """
    if not all(isinstance(v, str) and v.strip() for v in (stage, subject, revision)):
        raise ValueError("stage, subject and revision must be non-empty")
    payload = json.dumps([stage, subject, revision], ensure_ascii=False, separators=(",", ":"))
    return "semantic.v1:" + hashlib.sha256(payload.encode()).hexdigest()


class ExecutionReconciliation(BaseModel):
    """Explicit operator evidence; cannot grant tokens, calls or more time.

    A dead dispatcher may quarantine its uncertain result at full charge.
    This is not proof of remote cancellation or permission to replay that
    operation. It releases only the global fence for separately inspected work.
    """
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    reconciliation_id: str = Field(min_length=1, max_length=64)
    attempt_id: str = Field(min_length=1, max_length=64)
    expected_input_hash: str = Field(min_length=1, max_length=64)
    decision: Literal["abandon_charged", "recover_completed", "quarantine_charged"]
    evidence_kind: Literal["provider_receipt", "billing_record", "transport_log", "dispatcher_exit"]
    evidence_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    actor: str = Field(min_length=1, max_length=160)
    remote_operation_closed: bool
    local_dispatcher_stopped: bool = False
    result: dict[str, Any] | None = None

    @model_validator(mode="after")
    def validate_result(self):
        if self.decision == "quarantine_charged":
            if (self.remote_operation_closed or not self.local_dispatcher_stopped
                or self.evidence_kind != "dispatcher_exit"):
                raise ValueError("quarantine requires a stopped dispatcher and retains unknown remote outcome")
        elif not self.remote_operation_closed:
            raise ValueError("this reconciliation decision requires a closed remote operation")
        elif self.evidence_kind == "dispatcher_exit":
            raise ValueError("dispatcher exit cannot prove remote closure")
        if self.decision == "recover_completed":
            from bestseller.services.llm import LLMCompletionResult
            parsed = LLMCompletionResult.model_validate(self.result)
            if parsed.provider in {"mock", "fallback"}:
                raise ValueError("reconciliation requires an actual provider result")
            self.result = parsed.model_dump(mode="json")
        elif self.result is not None:
            raise ValueError("abandoned operation cannot supply a result")
        if len(json.dumps(self.result, ensure_ascii=False, allow_nan=False).encode()) > 2_000_000:
            raise ValueError("recovered result exceeds reconciliation bound")
        return self


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


@dataclass(frozen=True)
class OperationClaim:
    key: str
    owner: str
    cached_result: dict[str, Any] | None = None


class ExecutionLedger:
    """Atomic reservations committed BEFORE each transport attempt.

    Uses independent transactions on the caller's bound engine, not an ambient
    default database. A result with unknown remote outcome retains its whole
    reservation and blocks the scope pending explicit reconciliation. No TTL
    releases money. Resumption may reuse completed results, never an unknown.
    """

    def __init__(self, engine: AsyncEngine, scope_id: str, limits: BudgetSettings):
        if not isinstance(engine, AsyncEngine):
            raise ExecutionBudgetError("execution_ledger_unavailable", scope_id)
        self.engine, self.scope_id, self.limits = engine, scope_id, limits
        self.sessions = async_sessionmaker(engine, expire_on_commit=False)
        self.deadline_at: datetime | None = None
        state = _EXECUTION_SCOPE.get()
        self.owner_prefix = _PROCESS_EPOCH + (state.owner_epoch if state is not None else uuid4().hex[:16])

    @asynccontextmanager
    async def transaction(self):
        try:
            # Bound connection acquisition, row-lock waits and commit too.
            async with asyncio.timeout(5):
                async with self.sessions() as session, session.begin():
                    yield session
        except ExecutionBudgetError:
            raise
        except Exception as exc:
            raise ExecutionBudgetError("execution_ledger_unavailable", self.scope_id) from exc

    async def _historical(self, session: AsyncSession) -> dict[str, int]:
        """Adopt existing known spend once; missing usage is never assumed free."""
        where = None
        if self.scope_id.startswith("project:"):
            try:
                where = LlmRunModel.project_id == UUID(self.scope_id.split(":", 1)[1])
            except ValueError:
                return {}
        elif self.scope_id.startswith("conception:"):
            where = (
                LlmRunModel.metadata_json["conception_task_id"].as_string()
                == self.scope_id.split(":", 1)[1]
            )
        elif self.scope_id.startswith("workflow:"):
            where = LlmRunModel.workflow_run_id == UUID(self.scope_id.split(":", 1)[1])
        if where is None:
            return {}
        values = (
            await session.execute(
                select(
                    func.count(LlmRunModel.id),
                    func.coalesce(func.sum(LlmRunModel.input_tokens), 0),
                    func.coalesce(func.sum(LlmRunModel.output_tokens), 0),
                    func.count(LlmRunModel.input_tokens),
                    func.count(LlmRunModel.output_tokens),
                ).where(where, LlmRunModel.provider.notin_(["mock", "fallback"]))
            )
        ).one()
        calls, input_t, output_t, n_input, n_output = map(int, values)
        missing = calls - min(n_input, n_output)
        return {
            "calls": calls,
            "tokens": input_t + output_t + missing * self.limits.execution_max_tokens,
            "missing_usage_calls": missing,
        }

    async def _lock(
        self, session: AsyncSession, *, reconciling_operation_key: str | None = None,
    ) -> ExecutionBudgetModel:
        now = datetime.now(timezone.utc)
        # Dialect-specific upsert avoids a race rolling back the whole txn.
        from sqlalchemy.dialects.postgresql import insert as pg_insert
        from sqlalchemy.dialects.sqlite import insert as sqlite_insert

        dialect = self.engine.dialect.name
        if dialect not in {"postgresql", "sqlite"}:
            raise ExecutionBudgetError("execution_ledger_unavailable", self.scope_id)
        existing = await session.get(ExecutionBudgetModel, self.scope_id)
        if existing is None:
            history = await self._historical(session)
            insert = pg_insert if dialect == "postgresql" else sqlite_insert
            cap = self.limits.execution_max_tokens
            if self.limits.max_tokens_per_project > 0:
                cap = min(cap, self.limits.max_tokens_per_project)
            await session.execute(
                insert(ExecutionBudgetModel)
                .values(
                    scope_id=self.scope_id,
                    max_calls=self.limits.execution_max_calls,
                    max_tokens=cap,
                    calls_used=history.get("calls", 0),
                    tokens_charged=history.get("tokens", 0),
                    status="active",
                    deadline_at=now + timedelta(seconds=self.limits.execution_deadline_seconds),
                    metadata_json={
                        "policy": "execution-budget.v1",
                        "historical_usage": history,
                        "semantics": "technical ceiling, not spending authorization; unknown retains reservation",
                    },
                )
                .on_conflict_do_nothing(index_elements=["scope_id"])
            )
        # An actual UPDATE serializes SQLite too (FOR UPDATE alone does not).
        await session.execute(
            update(ExecutionBudgetModel)
            .where(ExecutionBudgetModel.scope_id == self.scope_id)
            .values(calls_used=ExecutionBudgetModel.calls_used)
        )
        row = (
            await session.execute(
                select(ExecutionBudgetModel)
                .where(ExecutionBudgetModel.scope_id == self.scope_id)
                .execution_options(populate_existing=True)
                .with_for_update()
            )
        ).scalar_one()
        # One scope has one dispatcher epoch. A second entrance must not
        # declare a still-leased provider request abandoned and poison its
        # live owner. Dead owners can be explicitly quarantined at full charge;
        # expired/uncertain operations retain the existing unknown fence.
        foreign = (await session.execute(select(
            ExecutionOperationModel.key, ExecutionOperationModel.status,
            ExecutionOperationModel.lease_until, ExecutionAttemptModel.status,
        ).join(
            ExecutionAttemptModel, ExecutionAttemptModel.operation_key == ExecutionOperationModel.key,
        ).where(
            ExecutionAttemptModel.scope_id == self.scope_id,
            ExecutionOperationModel.status.in_(["pending", "unknown"]),
            or_(ExecutionAttemptModel.status == "reserved",
                (ExecutionAttemptModel.status == "completed")
                & (ExecutionOperationModel.status == "pending")),
            ExecutionOperationModel.owner.not_like(self.owner_prefix + "%"),
        ).distinct())).all()
        abandoned = {key for key, state, lease, paid_state in foreign
                     if key == reconciling_operation_key or state == "unknown"
                     or paid_state == "completed" or _utc(lease) <= now}
        if not abandoned and foreign and execution_scope_block_code(row, now=now) is None:
            raise ExecutionOperationBusyError("execution_dispatcher_inflight", self.scope_id)
        if abandoned:
            row.status = "unknown"
            await session.execute(update(ExecutionOperationModel).where(
                ExecutionOperationModel.key.in_(abandoned),
                ExecutionOperationModel.status.in_(["pending", "unknown"]),
            ).values(status="unknown"))
        if row.max_calls < 1 or row.max_tokens < 1 or row.calls_used < 0 or row.tokens_charged < 0:
            raise ExecutionBudgetError("execution_ledger_invalid", self.scope_id)
        # Live config can tighten a persisted ceiling, never replenish it.
        authorized = completion_authorization(row)
        from bestseller.services.independent_acceptance import policy as independent_policy
        independent = independent_policy(row)
        if not authorized and independent is None:
            row.max_calls = min(row.max_calls, self.limits.execution_max_calls)
        cap = self.limits.execution_max_tokens
        if self.limits.max_tokens_per_project > 0:
            cap = min(cap, self.limits.max_tokens_per_project)
        if not authorized and independent is None:
            row.max_tokens = min(row.max_tokens, cap)
        # Retain the old aggregate deadline as history. Each invocation still
        # has a finite transport horizon and the provider's own request timeout.
        self.deadline_at = (now + timedelta(seconds=self.limits.execution_deadline_seconds)
                            if authorized else _utc(row.deadline_at))
        return row

    def _check(self, row: ExecutionBudgetModel, *, persist_expiry: bool = True) -> str | None:
        code = execution_scope_block_code(row)
        if persist_expiry:
            if code == "execution_deadline_exhausted":
                row.status = "exhausted"
            elif code is None and completion_authorization(row) and row.status == "exhausted":
                row.status = "active"
        return code

    async def check(self) -> None:
        async with self.transaction() as session:
            error = self._check(await self._lock(session))
        if error:
            raise ExecutionBudgetError(error, self.scope_id)

    async def assert_result_adoptable(
        self, claim: OperationClaim, *, allow_pending: bool = False, cached: bool = False,
    ) -> None:
        """One read snapshot checks permission, owner and charged quarantine.

        Keep the five-second fail-closed transaction bound. Live stream polls
        must not fetch historical result bodies or need three round trips.
        Cached adoption still compares the entire immutable paid result.
        """
        from types import SimpleNamespace
        from sqlalchemy import literal

        quarantined = select(ExecutionAttemptModel.id).where(
            ExecutionAttemptModel.operation_key == claim.key,
            ExecutionAttemptModel.status == "reconciled_charged",
        ).exists()
        async with self.transaction() as session:
            state = (await session.execute(select(
                ExecutionBudgetModel.status.label("scope_status"),
                ExecutionBudgetModel.deadline_at.label("scope_deadline"),
                ExecutionBudgetModel.metadata_json["provider_authorization"].label("authorization"),
                ExecutionOperationModel.scope_id.label("operation_scope"),
                ExecutionOperationModel.status.label("operation_status"),
                ExecutionOperationModel.owner.label("operation_owner"),
                (ExecutionOperationModel.result_json if cached else literal(None)).label("paid_result"),
                quarantined.label("quarantined_attempt"),
            ).select_from(ExecutionBudgetModel).outerjoin(
                ExecutionOperationModel, ExecutionOperationModel.key == claim.key,
            ).where(ExecutionBudgetModel.scope_id == self.scope_id))).mappings().first()
            row = (SimpleNamespace(status=state["scope_status"], deadline_at=state["scope_deadline"],
                metadata_json={"provider_authorization":state["authorization"]}) if state else None)
            error = self._check(row, persist_expiry=False) if row is not None else "execution_scope_missing"
            if cached and error in {"execution_deadline_exhausted", "execution_budget_exhausted"}:
                error = None
            if error is None:
                allowed = {"pending", "completed"} if allow_pending else {"completed"}
                if (state["operation_scope"] != self.scope_id or state["operation_status"] not in allowed
                    or (not cached and state["operation_owner"] != claim.owner)
                    or (cached and (claim.cached_result is None or state["paid_result"] != claim.cached_result))
                    or (not cached and state["quarantined_attempt"])):
                    error = "execution_operation_stopped"
        if error:
            raise ExecutionBudgetError(error, self.scope_id)


    async def claim(self, operation_id: str, input_hash: str) -> OperationClaim:
        key = hashlib.sha256(f"{self.scope_id}\n{operation_id}".encode()).hexdigest()
        owner = self.owner_prefix + uuid4().hex[:16]
        cached = None
        async with self.transaction() as session:
            row = await self._lock(session)
            op = await session.get(ExecutionOperationModel, key)
            # Read an already paid result independently of permission to SEND.
            # A deadline or exhausted ceiling must not destroy durable work.
            if op is not None and op.input_hash == input_hash and op.status == "completed":
                cached = op.result_json
                error = None
            else:
                error = self._check(row)
            if not error and cached is None:
                if op is not None:
                    if op.input_hash != input_hash:
                        error = "execution_operation_input_changed"
                    elif op.status == "completed":
                        cached = op.result_json
                    elif op.status == "pending":
                        if _utc(op.lease_until) > datetime.now(timezone.utc):
                            error = "execution_operation_inflight"
                        else:
                            uncertain = await session.scalar(select(ExecutionAttemptModel.id).where(
                                ExecutionAttemptModel.operation_key == op.key,
                                ExecutionAttemptModel.status.in_(["reserved", "unknown", "completed"]),
                            ).limit(1))
                            # Reservation is committed before send. A claim with
                            # no potentially paid attempt cannot freeze money.
                            if uncertain is None:
                                op.status = "stopped"
                                error = "execution_operation_stopped"
                            else:
                                op.status = row.status = "unknown"
                                error = "execution_result_unknown"
                    elif op.status == "stopped":
                        uncertain = await session.scalar(select(ExecutionAttemptModel.id).where(
                            ExecutionAttemptModel.operation_key == op.key,
                            ~(ExecutionAttemptModel.status.in_(["not_sent", "rejected"])
                              & ExecutionAttemptModel.actual_tokens.is_not(None)
                              & (ExecutionAttemptModel.actual_tokens == 0)),
                        ).limit(1))
                        if uncertain is None:
                            # An interrupted admission or definite free
                            # rejection may resume the SAME identity. Preserve
                            # its owners and every consumed attempt/counter.
                            history_key = hashlib.sha256(
                                f"unsent-reclaim.v1\n{op.key}\n{op.owner}".encode()).hexdigest()
                            if await session.get(ExecutionOperationModel, history_key) is None:
                                session.add(ExecutionOperationModel(key=history_key,
                                    scope_id=self.scope_id, input_hash=op.input_hash,
                                    status="unsent_reclaim_receipt", owner=op.owner,
                                    lease_until=op.lease_until, result_json={
                                        "operation_key": op.key, "old_owner": op.owner,
                                        "new_owner": owner, "credit_released": False,
                                    }))
                            op.status, op.owner, op.lease_until = "pending", owner, self.deadline_at
                        else:
                            error = "execution_operation_stopped"
                    else:
                        error = (
                            "execution_result_unknown"
                            if op.status == "unknown"
                            else "execution_operation_stopped"
                        )
                else:
                    session.add(
                        ExecutionOperationModel(
                            key=key,
                            scope_id=self.scope_id,
                            input_hash=input_hash,
                            status="pending",
                            owner=owner,
                            lease_until=self.deadline_at,
                        )
                    )
        if error:
            if error == "execution_operation_inflight":
                raise ExecutionOperationBusyError(error, self.scope_id)
            raise ExecutionBudgetError(error, self.scope_id)
        return OperationClaim(key, owner, cached)

    async def reserve(self, claim: OperationClaim, model: str, input_hash: str, tokens: int,
                      *, endpoint: str | None = None, exact_unknown_guard: dict | None = None,
                      single_dispatch_id: str | None = None, workflow_id: str | None = None,
                      template: str | None = None, version: str | None = None,
                      finite_book_binding: dict | None = None,
                      independent_acceptance_proof: dict | None = None) -> str:
        if tokens < 1:
            raise ValueError("reservation must be positive")
        attempt_id = uuid4().hex
        async with self.transaction() as session:
            row = await self._lock(session)
            ceilings = [cap for scope, cap in _ADDITIONAL_CALL_CEILINGS.get()
                        if scope == self.scope_id]
            if ceilings and row.calls_used >= min(ceilings):
                # No attempt, charge or global budget-status mutation. This
                # local boundary cannot turn an Unknown into a new paid send.
                raise ExecutionRepairCallLimitError("whole_repair_call_limit", self.scope_id)
            error = self._check(row)
            policy = completion_authorization(row)
            if not error and policy:
                from urllib.parse import urlsplit
                parsed = urlsplit(endpoint or "")
                from bestseller.services.finite_book_budget import route_authorized as finite_route
                if ((model not in policy["models"] or parsed.scheme != "https"
                    or parsed.hostname not in policy["endpoint_hosts"])
                    and not finite_route(row.metadata_json,finite_book_binding,model,endpoint)):
                    error = "execution_provider_not_authorized"
            op = await session.get(ExecutionOperationModel, claim.key)
            if not error and (op is None or op.owner != claim.owner or op.status != "pending"):
                error = "execution_operation_stopped"
            if not error:
                from bestseller.services.independent_acceptance import validate_reservation
                await validate_reservation(session, row, independent_acceptance_proof,
                    model=model, input_hash=input_hash, tokens=tokens, endpoint=endpoint)
            if not error and not policy and (
                row.calls_used >= row.max_calls or row.tokens_charged + tokens > row.max_tokens
            ):
                row.status = "exhausted"
                error = "execution_budget_exhausted"
            if not error and not policy:
                # Atomic with original ledger admission: exploration cannot
                # spend the credit reserved for drafting and review.
                from bestseller.services.production_strategy import assert_conception_headroom
                assert_conception_headroom(calls_used=row.calls_used, max_calls=row.max_calls,
                    tokens_charged=row.tokens_charged, max_tokens=row.max_tokens,
                    requested_tokens=tokens)
            if not error and exact_unknown_guard is not None:
                from bestseller.services.exact_unknown_business import validate_exact_unknown_reservation
                try:
                    await validate_exact_unknown_reservation(session, guard=exact_unknown_guard,
                        scope=self.scope_id, operation_key=claim.key, input_hash=input_hash, model=model)
                except (ValueError, TypeError, KeyError) as exc:
                    raise ExecutionBudgetError("execution_exact_unknown_admission_rejected", self.scope_id) from exc
            if not error:
                from bestseller.services.finite_book_budget import reserve as reserve_finite, compile_attempt_grant, FiniteBudgetError
                try:
                    if finite_book_binding is not None:
                        from bestseller.services.prompt_source_policy import content_hash
                        finite_book_binding={**finite_book_binding,'journal_sha256':content_hash((row.metadata_json.get('finite_book_runs') or {}).get(finite_book_binding['run_id']))}
                    row.metadata_json = reserve_finite(row.metadata_json,attempt_id=attempt_id,
                        operation_key=claim.key,model=model,input_hash=input_hash,endpoint=endpoint,
                        binding=finite_book_binding)
                    if finite_book_binding is not None:
                        if single_dispatch_id is not None:
                            raise FiniteBudgetError('finite_cannot_reuse_historical_grant')
                        row.metadata_json,single_dispatch_id=compile_attempt_grant(row.metadata_json,
                            binding=finite_book_binding,attempt_id=attempt_id,operation_key=claim.key,
                            input_hash=input_hash,model=model,endpoint=endpoint,workflow_id=workflow_id,
                            template=template,version=version)
                except FiniteBudgetError as exc:
                    raise ExecutionBudgetError(str(exc),self.scope_id) from exc
                from bestseller.services.single_dispatch_authorization import consume_grant
                try:
                    row.metadata_json = consume_grant(row.metadata_json, scope_id=self.scope_id,
                        operation_key=claim.key,input_hash=input_hash,model=model,endpoint=endpoint,
                        workflow_id=workflow_id,template=template,version=version,
                        grant_id=single_dispatch_id,attempt_id=attempt_id)
                except ValueError as exc:
                    raise ExecutionBudgetError(str(exc),self.scope_id) from exc
                row.calls_used += 1
                row.tokens_charged += tokens
                session.add(
                    ExecutionAttemptModel(
                        id=attempt_id,
                        operation_key=claim.key,
                        scope_id=self.scope_id,
                        model_name=model,
                        input_hash=input_hash,
                        reserved_tokens=tokens,
                        status="reserved",
                        created_at=datetime.now(timezone.utc),
                    )
                )
        if error:
            raise ExecutionBudgetError(error, self.scope_id)
        return attempt_id

    def _transport_result_key(self, attempt_id: str) -> str:
        return hashlib.sha256(f"transport-result.v1\n{self.scope_id}\n{attempt_id}".encode()).hexdigest()

    async def transport_result(self, attempt_id: str) -> dict[str, Any] | None:
        """Read immutable paid-return evidence without granting SEND permission."""
        async with self.transaction() as session:
            attempt = await session.get(ExecutionAttemptModel, attempt_id)
            if attempt is None or attempt.scope_id != self.scope_id:
                raise ExecutionBudgetError("execution_reconciliation_conflict", self.scope_id)
            saved = await session.get(ExecutionOperationModel, self._transport_result_key(attempt_id))
            return saved.result_json if saved is not None else None

    async def record_transport_failure(self, attempt_id: str, diagnostic: dict[str, Any]) -> None:
        """Persist bounded identifiers before a provider exception loses its stack.

        No exception text, request/response body, headers or credentials enter
        this receipt. Caller data cannot invent physical-send evidence.
        """
        allowed = {key: diagnostic.get(key) for key in (
            "schema", "physical_sends", "exception_type", "http_status",
            "prompt_template", "prompt_version", "logical_role", "project_id", "workflow_run_id",
            "operator_interruption", "elapsed_ms", "timeout_seconds", "stream", "partial_stream_observed",
            "error_chain",
        )}
        if diagnostic.get("native_deadline_policy") is not None:
            from bestseller.services.planning_native_deadline import validate_native_deadline_policy
            allowed["native_deadline_policy"] = validate_native_deadline_policy(diagnostic["native_deadline_policy"])
        async with self.transaction() as session:
            attempt = await session.get(ExecutionAttemptModel, attempt_id)
            if attempt is None or attempt.scope_id != self.scope_id:
                raise ExecutionBudgetError("execution_reconciliation_conflict", self.scope_id)
            op = await session.get(ExecutionOperationModel, attempt.operation_key)
            if op is None or op.scope_id != self.scope_id:
                raise ExecutionBudgetError("execution_ledger_unavailable", self.scope_id)
            key = hashlib.sha256(f"transport-failure.v1\n{self.scope_id}\n{attempt_id}".encode()).hexdigest()
            existing = await session.get(ExecutionOperationModel, key)
            if existing is None:
                session.add(ExecutionOperationModel(key=key, scope_id=self.scope_id,
                    input_hash=attempt.input_hash, status="transport_failure_receipt",
                    owner=op.owner, lease_until=op.lease_until,
                    result_json={"attempt_id": attempt_id, "operation_key": op.key, **allowed}))

    async def record_provider_return(
        self, claim: OperationClaim, attempt_id: str, response: dict[str, Any], *,
        wire_body_hash: str | None,
    ) -> None:
        """Append an unusable SDK return without changing money or permission.

        Kept separate from reusable transport_result receipts: this body is
        evidence for inspection, never a successful completion or grade.
        """
        if (not isinstance(response, dict) or not isinstance(wire_body_hash, str)
            or len(wire_body_hash) != 64 or any(c not in "0123456789abcdef" for c in wire_body_hash)):
            raise ExecutionBudgetError("execution_transport_unverified", self.scope_id)
        encoded = json.dumps(response, ensure_ascii=False, sort_keys=True, allow_nan=False)
        if len(encoded.encode("utf-8")) > 2_000_000:
            raise ExecutionBudgetError("execution_provider_return_exceeds_bound", self.scope_id)
        async with self.transaction() as session:
            attempt = await session.get(ExecutionAttemptModel, attempt_id)
            op = await session.get(ExecutionOperationModel, claim.key)
            if (attempt is None or attempt.scope_id != self.scope_id or attempt.operation_key != claim.key
                or op is None or op.scope_id != self.scope_id or op.owner != claim.owner):
                raise ExecutionBudgetError("execution_reconciliation_conflict", self.scope_id)
            record = {"schema": "provider-return.v1", "attempt_id": attempt_id,
                "operation_key": claim.key, "input_hash": attempt.input_hash,
                "model_name": attempt.model_name, "wire_body_hash": wire_body_hash,
                "response": json.loads(encoded), "response_sha256": hashlib.sha256(encoded.encode()).hexdigest(),
                "usable": False, "adoption_authorized": False, "credit_released": False}
            key = hashlib.sha256(f"provider-return.v1\n{self.scope_id}\n{attempt_id}".encode()).hexdigest()
            existing = await session.get(ExecutionOperationModel, key)
            if existing is not None:
                if existing.status != "unusable_provider_return" or existing.result_json != record:
                    raise ExecutionBudgetError("execution_reconciliation_conflict", self.scope_id)
                return
            session.add(ExecutionOperationModel(key=key, scope_id=self.scope_id,
                input_hash=attempt.input_hash, status="unusable_provider_return",
                owner=claim.owner, lease_until=op.lease_until, result_json=record))

    async def settle(
        self, attempt_id: str, status: str, *, actual_tokens: int | None = None,
        claim: OperationClaim | None = None, result: dict[str, Any] | None = None,
    ) -> str | None:
        """Commit successful content and usage together, before upper formatting.

        Immutable attempt evidence survives a competing dispatcher or a later
        human decision. Retaining a late return never reverses that decision or
        grants permission to continue a frozen operation.
        """
        if status not in {"completed", "rejected", "unknown", "not_sent"}:
            raise ValueError("invalid execution settlement")
        if status == "not_sent" and (actual_tokens != 0 or result is not None):
            raise ValueError("an unsent attempt must have zero usage and no provider result")
        if result is not None and (status != "completed" or claim is None):
            raise ValueError("a paid result requires a successful owned attempt")
        blocked = None
        async with self.transaction() as session:
            row = await self._lock(session)
            attempt = await session.get(ExecutionAttemptModel, attempt_id)
            if attempt is None or attempt.scope_id != self.scope_id:
                raise ExecutionBudgetError("execution_ledger_unavailable", self.scope_id)
            op = await session.get(ExecutionOperationModel, attempt.operation_key)
            if result is not None:
                if (op is None or op.scope_id != self.scope_id
                    or op.key != claim.key or op.owner != claim.owner):
                    raise ExecutionBudgetError("execution_operation_stopped", self.scope_id)
                if result.get("model_name") != attempt.model_name:
                    raise ExecutionBudgetError("execution_settlement_conflict", self.scope_id)
                key = self._transport_result_key(attempt_id)
                saved = await session.get(ExecutionOperationModel, key)
                record = {"schema": "transport-result.v1", "attempt_id": attempt_id,
                          "operation_key": op.key, "input_hash": attempt.input_hash,
                          "result": result}
                if saved is not None:
                    if saved.status != "transport_result" or saved.result_json != record:
                        raise ExecutionBudgetError("execution_settlement_conflict", self.scope_id)
                else:
                    session.add(ExecutionOperationModel(key=key, scope_id=self.scope_id,
                        input_hash=attempt.input_hash, status="transport_result",
                        owner=claim.owner, lease_until=row.deadline_at, result_json=record))
                if attempt.status == "reconciled_charged" or op.status == "stopped":
                    return "execution_operation_stopped"
                if attempt.status == "unknown":
                    # Previously uncertain reservations stay charged pending
                    # human reconciliation, even if a late response arrives.
                    return "execution_result_unknown"
            if attempt.status != "reserved":
                if attempt.status != status:
                    raise ExecutionBudgetError("execution_settlement_conflict", self.scope_id)
                if result is not None and (op.status == "unknown" or row.status == "unknown"):
                    return "execution_result_unknown"
                return
            from bestseller.services.finite_book_budget import settle as settle_finite
            row.metadata_json, financial_block = settle_finite(row.metadata_json,
                attempt_id=attempt_id,status=status,result=result,actual_tokens=actual_tokens)
            if financial_block:
                row.status = "unknown"
                blocked = financial_block
            attempt.status, attempt.actual_tokens = status, actual_tokens
            if actual_tokens is not None and status != "unknown":
                row.tokens_charged += max(0, actual_tokens) - attempt.reserved_tokens
            if (not completion_authorization(row) and row.tokens_charged > row.max_tokens
                and row.status not in {"unknown", "cancelled"}):
                row.status = "exhausted"
            if status == "unknown":
                row.status = "unknown"
                op.status = "unknown"
            if result is not None:
                if op.status == "unknown" or row.status == "unknown":
                    op.status = "unknown"
                    blocked = "execution_result_unknown"
                else:
                    op.status, op.result_json = "completed", result
                    if row.status == "cancelled":
                        blocked = "execution_cancelled"
        return blocked

    async def finish(self, claim: OperationClaim, result: dict[str, Any] | None) -> None:
        if result is not None:
            await self.assert_result_adoptable(claim, allow_pending=True)
        await self._finish(claim, result)
        if result is not None:
            await self.assert_result_adoptable(claim)

    async def _finish(self, claim: OperationClaim, result: dict[str, Any] | None) -> None:
        async with self.transaction() as session:
            row = await self._lock(session)
            op = await session.get(ExecutionOperationModel, claim.key)
            if op is None or op.owner != claim.owner:
                raise ExecutionBudgetError("execution_operation_stopped", self.scope_id)
            if op.status == "completed":
                if result is None:
                    return  # upper formatting/telemetry failure cannot erase paid work
                reconciled = await session.scalar(select(ExecutionAttemptModel.id).where(
                    ExecutionAttemptModel.operation_key == claim.key,
                    ExecutionAttemptModel.status == "reconciled_charged",
                ).limit(1))
                if reconciled is not None:
                    raise ExecutionBudgetError("execution_operation_stopped", self.scope_id)
                # Only enrich the receipt. Content, tools, model and usage
                # belong to the immutable physical return, never a new sample.
                fields = ("content", "provider", "model_name", "input_tokens", "output_tokens",
                          "finish_reason", "tool_calls", "raw_message")
                if any(result.get(k) != (op.result_json or {}).get(k) for k in fields):
                    raise ExecutionBudgetError("execution_settlement_conflict", self.scope_id)
                op.result_json = result
                return
            if op.status == "unknown" and result is None:
                return  # evidence was retained; only explicit reconciliation may adopt it
            if op.status != "pending":
                raise ExecutionBudgetError("execution_operation_stopped", self.scope_id)
            unsettled = await session.scalar(
                select(ExecutionAttemptModel.id)
                .where(
                    ExecutionAttemptModel.operation_key == claim.key,
                    ExecutionAttemptModel.status == "reserved",
                )
                .limit(1)
            )
            completed_without_result = (
                result is None
                and await session.scalar(
                    select(ExecutionAttemptModel.id)
                    .where(
                        ExecutionAttemptModel.operation_key == claim.key,
                        ExecutionAttemptModel.status == "completed",
                    )
                    .limit(1)
                )
                is not None
            )
            if unsettled is not None or completed_without_result:
                # A failed settlement/commit cannot be converted into a
                # stopped operation followed by a fresh paid operation ID.
                op.status = row.status = "unknown"
                await session.execute(
                    update(ExecutionAttemptModel)
                    .where(
                        ExecutionAttemptModel.operation_key == claim.key,
                        ExecutionAttemptModel.status == "reserved",
                    )
                    .values(status="unknown")
                )
            else:
                op.status, op.result_json = (
                    ("completed", result) if result is not None else ("stopped", None)
                )

    async def cancel(self) -> None:
        async with self.transaction() as session:
            row = await self._lock(session)
            row.metadata_json = {**(row.metadata_json or {}), "cancel_requested": True}
            # An unknown remote result cannot be reconciled by cancellation.
            if row.status != "unknown":
                row.status = "cancelled"

    async def reconciliation_status(self, *, limit: int = 50) -> dict[str, Any]:
        """Bounded evidence inventory, without request bodies or secrets."""
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        async with self.transaction() as session:
            row = await session.get(ExecutionBudgetModel, self.scope_id)
            if row is None:
                raise ExecutionBudgetError("execution_scope_missing", self.scope_id)
            attempts = (await session.scalars(select(ExecutionAttemptModel).join(
                ExecutionOperationModel, ExecutionOperationModel.key == ExecutionAttemptModel.operation_key).where(
                ExecutionAttemptModel.scope_id == self.scope_id,
                or_(ExecutionAttemptModel.status.in_(["reserved", "unknown"]),
                    (ExecutionOperationModel.status == "unknown") & (ExecutionAttemptModel.status == "completed")),
            ).order_by(ExecutionAttemptModel.created_at, ExecutionAttemptModel.id).limit(limit))).all()
            return {
                **execution_snapshot(row),
                "quarantined_remote_outcomes": int(await session.scalar(
                    select(func.count()).select_from(ExecutionOperationModel).where(
                        ExecutionOperationModel.scope_id == self.scope_id,
                        ExecutionOperationModel.status == "reconciliation_receipt",
                        ExecutionOperationModel.result_json["receipt"]["decision"].as_string()
                        == "quarantine_charged",
                    )) or 0),
                "attempts": [{"attempt_id": a.id, "operation_key": a.operation_key,
                              "input_hash": a.input_hash, "status": a.status,
                              "model": a.model_name, "reserved_tokens": a.reserved_tokens}
                             for a in attempts],
                "limit": limit,
                "reconciliations": (row.metadata_json or {}).get("reconciliations", []),
            }

    async def reconcile(self, evidence: ExecutionReconciliation) -> dict[str, Any]:
        """Close one unknown outcome with evidence. Never replenish credit.

        This is an operator action, never called by retries or recovery. A
        closed abandoned identity stays stopped; a different authorized
        operation may only consume the ORIGINAL remaining budget and deadline.
        """
        fingerprint = hashlib.sha256(json.dumps(evidence.model_dump(mode="json"),
            ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        def receipt_key(identifier: str) -> str:
            # Domain-separated from paid operation keys; no schema change.
            return hashlib.sha256(f"reconciliation.v1\n{self.scope_id}\n{identifier}".encode()).hexdigest()
        async with self.transaction() as session:
            if await session.get(ExecutionBudgetModel, self.scope_id) is None:
                raise ExecutionBudgetError("execution_scope_missing", self.scope_id)
            candidate = await session.get(ExecutionAttemptModel, evidence.attempt_id)
            if (candidate is None or candidate.scope_id != self.scope_id
                or candidate.input_hash != evidence.expected_input_hash):
                raise ExecutionBudgetError("execution_reconciliation_conflict", self.scope_id)
            row = await self._lock(session, reconciling_operation_key=candidate.operation_key)
            records = list((row.metadata_json or {}).get("reconciliations", []))
            durable_receipt = await session.get(ExecutionOperationModel, receipt_key(evidence.reconciliation_id))
            if durable_receipt is not None:
                if durable_receipt.status != "reconciliation_receipt" or durable_receipt.input_hash != fingerprint:
                    raise ExecutionBudgetError("execution_reconciliation_conflict", self.scope_id)
                return durable_receipt.result_json["receipt"]
            for record in records:
                if record["reconciliation_id"] == evidence.reconciliation_id:
                    if record["request_hash"] != fingerprint:
                        raise ExecutionBudgetError("execution_reconciliation_conflict", self.scope_id)
                    return record["receipt"]
            attempt = await session.get(ExecutionAttemptModel, evidence.attempt_id)
            if (attempt is None or attempt.scope_id != self.scope_id
                or attempt.input_hash != evidence.expected_input_hash):
                raise ExecutionBudgetError("execution_reconciliation_conflict", self.scope_id)
            op = await session.get(ExecutionOperationModel, attempt.operation_key)
            if (op is None or op.scope_id != self.scope_id or op.status not in {"unknown", "pending"}
                or row.status != "unknown"):
                raise ExecutionBudgetError("execution_reconciliation_conflict", self.scope_id)
            if attempt.status not in {"unknown", "reserved", "completed"}:
                raise ExecutionBudgetError("execution_reconciliation_conflict", self.scope_id)
            if evidence.result is not None and evidence.result["model_name"] != attempt.model_name:
                raise ExecutionBudgetError("execution_reconciliation_conflict", self.scope_id)
            # Never release reservations, even when a receipt reports fewer
            # tokens. That requires separate financial reconciliation policy.
            attempt.status = "reconciled_charged"
            unresolved_op = await session.scalar(select(ExecutionAttemptModel.id).where(
                ExecutionAttemptModel.operation_key == op.key,
                ExecutionAttemptModel.id != attempt.id,
                ExecutionAttemptModel.status.in_(["reserved", "unknown"]),
            ).limit(1))
            if unresolved_op is None:
                op.status = "completed" if evidence.result is not None else "stopped"
                op.result_json = evidence.result
            elif evidence.result is not None:
                raise ExecutionBudgetError("execution_reconciliation_conflict", self.scope_id)
            else:
                op.status = "unknown"
            await session.flush()
            remaining = int(await session.scalar(select(func.count(func.distinct(ExecutionOperationModel.key)))
                .outerjoin(ExecutionAttemptModel, ExecutionAttemptModel.operation_key == ExecutionOperationModel.key)
                .where(ExecutionOperationModel.scope_id == self.scope_id,
                       or_(ExecutionOperationModel.status == "unknown",
                           ExecutionAttemptModel.status.in_(["reserved", "unknown"])))) or 0)
            if row.status == "unknown" and remaining == 0:
                row.status = "active"
                if (row.metadata_json or {}).get("cancel_requested"):
                    row.status = "cancelled"
                elif not completion_authorization(row) and (datetime.now(timezone.utc) >= _utc(row.deadline_at)
                    or row.calls_used >= row.max_calls or row.tokens_charged >= row.max_tokens):
                    row.status = "exhausted"
            receipt = {"scope_id": self.scope_id, "reconciliation_id": evidence.reconciliation_id,
                       "attempt_id": attempt.id, "decision": evidence.decision,
                       "status": row.status, "remaining_unknown": remaining,
                       "calls_used": row.calls_used, "tokens_charged": row.tokens_charged,
                       "automatic_resume": False, "credit_released": False}
            if evidence.decision == "quarantine_charged":
                receipt.update(remote_outcome="unknown_quarantined", operation_replay_allowed=False)
            record = {"reconciliation_id": evidence.reconciliation_id,
                "request_hash": fingerprint, "actor": evidence.actor,
                "evidence_kind": evidence.evidence_kind, "evidence_sha256": evidence.evidence_sha256,
                "remote_operation_closed": evidence.remote_operation_closed,
                "local_dispatcher_stopped": evidence.local_dispatcher_stopped,
                "at": datetime.now(timezone.utc).isoformat(), "receipt": receipt}
            records.append(record)
            session.add(ExecutionOperationModel(key=receipt_key(evidence.reconciliation_id),
                scope_id=self.scope_id, input_hash=fingerprint, status="reconciliation_receipt",
                owner="manual-reconciliation", lease_until=row.deadline_at, result_json=record))
            # Keep API/index bounded without destroying old evidence or creating
            # a lifetime audit cap that could permanently freeze unknown money.
            for old in records[:-128]:
                key = receipt_key(old["reconciliation_id"])
                if await session.get(ExecutionOperationModel, key) is None:
                    session.add(ExecutionOperationModel(key=key, scope_id=self.scope_id,
                        input_hash=old["request_hash"], status="reconciliation_receipt",
                        owner="manual-reconciliation", lease_until=row.deadline_at, result_json=old))
            row.metadata_json = {**(row.metadata_json or {}), "reconciliations": records[-128:]}
        return receipt


class TokenUsageSummary(BaseModel):
    project_id: UUID
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_tokens: int = 0
    estimated_cost_usd: float = 0.0
    llm_call_count: int = 0


class BudgetCheckResult(BaseModel):
    within_budget: bool = True
    usage: TokenUsageSummary
    budget_limit: int = 0
    utilization_pct: float = 0.0
    warning_level: str | None = None  # "50%", "80%", "exceeded"


async def get_project_token_usage(
    session: AsyncSession,
    project_id: UUID,
    settings: AppSettings,
) -> TokenUsageSummary:
    """Aggregate token usage across all LLM runs for a project."""
    result = await session.execute(
        select(
            func.coalesce(func.sum(LlmRunModel.input_tokens), 0).label("total_input"),
            func.coalesce(func.sum(LlmRunModel.output_tokens), 0).label("total_output"),
            func.count(LlmRunModel.id).label("call_count"),
        ).where(LlmRunModel.project_id == project_id)
    )
    row = result.one()
    total_input = int(row.total_input)
    total_output = int(row.total_output)
    cost = (
        total_input / 1000 * settings.budget.cost_per_1k_input_tokens
        + total_output / 1000 * settings.budget.cost_per_1k_output_tokens
    )
    return TokenUsageSummary(
        project_id=project_id,
        total_input_tokens=total_input,
        total_output_tokens=total_output,
        total_tokens=total_input + total_output,
        estimated_cost_usd=round(cost, 4),
        llm_call_count=int(row.call_count),
    )


def estimate_project_cost(
    chapter_count: int,
    settings: AppSettings,
) -> dict[str, Any]:
    """Pre-generation cost estimation based on typical per-chapter token usage."""
    # Empirical averages: ~15k input + ~4k output per scene, ~3 scenes per chapter
    scenes_per_chapter = settings.generation.scenes_per_chapter.target
    avg_input_per_scene = 15_000
    avg_output_per_scene = 4_000
    # Add review + knowledge refresh overhead (~1.5x)
    overhead_factor = 1.5

    total_input = int(chapter_count * scenes_per_chapter * avg_input_per_scene * overhead_factor)
    total_output = int(chapter_count * scenes_per_chapter * avg_output_per_scene * overhead_factor)
    cost = (
        total_input / 1000 * settings.budget.cost_per_1k_input_tokens
        + total_output / 1000 * settings.budget.cost_per_1k_output_tokens
    )
    return {
        "chapter_count": chapter_count,
        "estimated_input_tokens": total_input,
        "estimated_output_tokens": total_output,
        "estimated_total_tokens": total_input + total_output,
        "estimated_cost_usd": round(cost, 2),
        "note": "Estimates based on typical usage; actual may vary by genre and complexity.",
    }


async def check_budget(
    session: AsyncSession,
    project_id: UUID,
    settings: AppSettings,
) -> BudgetCheckResult:
    """Check if project is within budget. Returns warning levels at configured thresholds."""
    usage = await get_project_token_usage(session, project_id, settings)
    limit = settings.budget.max_tokens_per_project

    if limit <= 0:
        return BudgetCheckResult(
            within_budget=True,
            usage=usage,
            budget_limit=0,
            utilization_pct=0.0,
            warning_level=None,
        )

    utilization = usage.total_tokens / limit if limit > 0 else 0.0
    warning_level: str | None = None
    within_budget = True

    for threshold in sorted(settings.budget.warning_thresholds):
        if utilization >= threshold:
            warning_level = f"{int(threshold * 100)}%"
            if threshold >= 1.0:
                within_budget = False

    if warning_level is not None:
        logger.warning(
            "Budget check for project %s: utilization=%.1f%%, warning=%s, within_budget=%s",
            project_id,
            utilization * 100,
            warning_level,
            within_budget,
        )

    return BudgetCheckResult(
        within_budget=within_budget,
        usage=usage,
        budget_limit=limit,
        utilization_pct=round(utilization * 100, 1),
        warning_level=warning_level,
    )
