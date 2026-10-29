"""Operator-only, audited authorization of finite new books and execution phases.

Not registered as an LLM tool, retry hook, self-heal action or reconciliation.
Ordinary execution config can only tighten caps. A trusted transaction owner
must supply the operator, authorization reference and expected old limits here.
Fresh-book grants instead bind one exact normal POST before any dispatch.
"""

import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import UTC, datetime, timedelta
import hashlib
import json
from typing import Any
from urllib.parse import urlsplit
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from bestseller.infra.db.models import ExecutionBudgetModel, ExecutionOperationModel
from bestseller.services.budget import ExecutionBudgetError

_NEW_BOOK_KEY = "new_book_budget_grant"
_NEW_BOOK_POLICY = "new-book-budget.v1"
_NEW_BOOK_BOUNDED_POLICY = "new-book-budget.v2"
_NEW_BOOK_SELECTOR = "execution_budget_grant_id"
_NEW_BOOK_CALLS = 400
_NEW_BOOK_TOKENS = 20_000_000
_NEW_BOOK_BINDING_FIELDS = frozenset({
    "grant_id", "task_id", "scope_id", "payload_sha256", "model", "endpoint",
    "max_calls", "max_tokens", "created_at", "deadline_at",
})
_NEW_BOOK_BOUNDED_BINDING_FIELDS = _NEW_BOOK_BINDING_FIELDS | {"schema", "authorization_id"}
_NEW_BOOK_BOUNDED_RECORD_FIELDS = frozenset({
    "schema", "binding", "authorization_id", "actor", "authorization_ref",
    "payload_summary", "consumed_at", "sha256",
})


def _new_book_summary(value: Any) -> dict[str, Any]:
    """Typed operands only; never include POST text or provider credentials."""
    try:
        encoded = json.dumps(value, sort_keys=True, default=str).encode()
    except (TypeError, ValueError, RecursionError):
        encoded = type(value).__name__.encode()
    return {
        "type": type(value).__name__,
        "length": len(value) if isinstance(value, (str, list, dict, bytes)) else None,
        "sha256": hashlib.sha256(encoded).hexdigest(),
    }


def _new_book_require(
    valid: bool, code: str, scope: str, field: str, expected: Any, actual: Any,
) -> None:
    if not valid:
        error = ExecutionBudgetError(code, scope)
        error.differences = [{
            "field": field, "expected": _new_book_summary(expected),
            "actual": _new_book_summary(actual),
        }]
        raise error


def _new_book_hash(value: Any) -> str:
    encoded = json.dumps(
        value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _new_book_json(value: Any) -> bool:
    if type(value) is dict:
        return all(type(key) is str and _new_book_json(item) for key, item in value.items())
    if type(value) is list:
        return all(_new_book_json(item) for item in value)
    return type(value) in (str, int, float, bool, type(None))


def _new_book_payload(payload: Any, *, preparing: bool, scope: str = "") -> dict:
    code = "execution_new_book_payload_invalid"
    try:
        valid = type(payload) is dict and _new_book_json(payload)
        if valid:
            _new_book_hash(payload)  # Also reject NaN/Infinity and invalid Unicode.
    except (ValueError, TypeError, RecursionError, UnicodeError):
        valid = False
    _new_book_require(valid, code, scope, "request.payload", "finite JSON object", payload)
    if preparing:
        _new_book_require(
            _NEW_BOOK_SELECTOR not in payload, code, scope,
            f"request.{_NEW_BOOK_SELECTOR}", None, payload.get(_NEW_BOOK_SELECTOR),
        )
        for name in (
            "project_slug", "project_id", "_active_continuation_scope",
            "execution_scope_id", "scope_id", "task_id",
        ):
            _new_book_require(
                payload.get(name) in (None, ""), code, scope,
                f"request.{name}", None, payload.get(name),
            )
    return {key: value for key, value in payload.items() if key != _NEW_BOOK_SELECTOR}


def _new_book_route(model: Any, endpoint: Any, *, scope: str = "") -> None:
    code = "execution_new_book_grant_invalid"
    _new_book_require(
        type(model) is str and bool(model.strip()) and len(model) <= 1024,
        code, scope, "request.model", "resolved provider model", model,
    )
    try:
        parsed = urlsplit(endpoint) if type(endpoint) is str else None
        valid = bool(
            parsed and parsed.scheme == "https" and parsed.hostname
            and not parsed.username and not parsed.password and not parsed.query
            and not parsed.fragment and len(endpoint) <= 2048
        )
    except ValueError:
        valid = False
    _new_book_require(
        valid, code, scope, "request.endpoint", "HTTPS base URL without credentials", endpoint,
    )


def _new_book_identity(payload_hash: str) -> str:
    # The same full creation cannot receive another ledger by changing its locator.
    return str(uuid5(NAMESPACE_URL, f"{_NEW_BOOK_POLICY}:{payload_hash}"))


def _new_book_bounded_limits(
    max_calls: Any, max_tokens: Any, *, code: str, scope: str = "", prefix: str = "",
) -> None:
    for name, value, ceiling in (
        ("max_calls", max_calls, _NEW_BOOK_CALLS),
        ("max_tokens", max_tokens, _NEW_BOOK_TOKENS),
    ):
        _new_book_require(
            type(value) is int and 1 <= value <= ceiling, code, scope, prefix + name,
            {"type": "int", "minimum": 1, "maximum": ceiling}, value,
        )


def _new_book_bounded_identity(
    payload_hash: str, authorization_id: str, max_calls: int, max_tokens: int,
) -> str:
    binding = {"authorization_id": authorization_id, "payload_sha256": payload_hash,
               "max_calls": max_calls, "max_tokens": max_tokens}
    return str(uuid5(NAMESPACE_URL, f"{_NEW_BOOK_BOUNDED_POLICY}:{_new_book_hash(binding)}"))


def _new_book_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def new_book_budget_policy(row: ExecutionBudgetModel) -> dict | None:
    """Validate the native finite grant; legacy scopes retain their old policy."""
    metadata = row.metadata_json or {}
    if not isinstance(metadata, dict):
        return None
    if (_NEW_BOOK_KEY not in metadata
            and metadata.get("policy") not in (_NEW_BOOK_POLICY, _NEW_BOOK_BOUNDED_POLICY)):
        return None
    scope, code = row.scope_id, "execution_new_book_binding_invalid"
    prefix = f"execution_budgets.metadata.{_NEW_BOOK_KEY}"
    bounded = metadata.get("policy") == _NEW_BOOK_BOUNDED_POLICY
    policy = _NEW_BOOK_BOUNDED_POLICY if bounded else _NEW_BOOK_POLICY
    binding_fields = _NEW_BOOK_BOUNDED_BINDING_FIELDS if bounded else _NEW_BOOK_BINDING_FIELDS
    _new_book_require(
        metadata.get("policy") == policy, code, scope,
        "execution_budgets.metadata.policy", policy, metadata.get("policy"),
    )
    record = metadata.get(_NEW_BOOK_KEY)
    _new_book_require(type(record) is dict, code, scope, prefix, "grant record", record)
    if bounded:
        _new_book_require(
            set(record) == _NEW_BOOK_BOUNDED_RECORD_FIELDS, code, scope,
            f"{prefix}.keys", sorted(_NEW_BOOK_BOUNDED_RECORD_FIELDS), sorted(record),
        )
    binding = record.get("binding")
    _new_book_require(
        type(binding) is dict and set(binding) == binding_fields,
        code, scope, f"{prefix}.binding", sorted(binding_fields),
        sorted(binding) if type(binding) is dict else binding,
    )
    _new_book_require(
        record.get("schema") == policy, code, scope,
        f"{prefix}.schema", policy, record.get("schema"),
    )
    for name in ("authorization_id", "actor", "authorization_ref"):
        value = record.get(name)
        _new_book_require(
            type(value) is str and bool(value.strip()) and len(value) <= 1024,
            code, scope, f"{prefix}.{name}", "nonempty trusted identity", value,
        )
    payload_hash = binding.get("payload_sha256")
    _new_book_require(
        type(payload_hash) is str and len(payload_hash) == 64
        and all(char in "0123456789abcdef" for char in payload_hash),
        code, scope, f"{prefix}.binding.payload_sha256", "sha256", payload_hash,
    )
    calls, tokens = _NEW_BOOK_CALLS, _NEW_BOOK_TOKENS
    if bounded:
        calls, tokens = binding["max_calls"], binding["max_tokens"]
        _new_book_bounded_limits(calls, tokens, code=code, scope=scope,
                                 prefix=f"{prefix}.binding.")
        for name, expected in (("schema", policy), ("authorization_id", record["authorization_id"])):
            _new_book_require(
                type(binding[name]) is str and binding[name] == expected, code, scope,
                f"{prefix}.binding.{name}", expected, binding[name],
            )
        task_id = _new_book_bounded_identity(payload_hash, record["authorization_id"], calls, tokens)
    else:
        task_id = _new_book_identity(payload_hash)
    for name, expected in (
        ("task_id", task_id), ("grant_id", task_id), ("scope_id", f"conception:{task_id}"),
        ("max_calls", calls), ("max_tokens", tokens),
    ):
        actual = binding.get(name)
        _new_book_require(
            type(actual) is type(expected) and actual == expected,
            code, scope, f"{prefix}.binding.{name}", expected, actual,
        )
    _new_book_require(
        row.scope_id == binding["scope_id"], code, scope,
        "execution_budgets.scope_id", binding["scope_id"], row.scope_id,
    )
    for name in ("max_calls", "max_tokens"):
        actual = getattr(row, name)
        _new_book_require(
            type(actual) is int and actual == binding[name], code, scope,
            f"execution_budgets.{name}", binding[name], actual,
        )
    for name in ("calls_used", "tokens_charged"):
        actual = getattr(row, name)
        _new_book_require(
            type(actual) is int and actual >= 0, code, scope,
            f"execution_budgets.{name}", "nonnegative integer", actual,
        )
    dates = {}
    for name in ("created_at", "deadline_at"):
        value = binding.get(name)
        try:
            parsed = datetime.fromisoformat(value) if type(value) is str else None
        except ValueError:
            parsed = None
        _new_book_require(
            parsed is not None and parsed.utcoffset() == timedelta(0),
            code, scope, f"{prefix}.binding.{name}", "UTC ISO timestamp", value,
        )
        dates[name] = parsed
    _new_book_require(
        dates["deadline_at"] == dates["created_at"] + timedelta(hours=24), code, scope,
        f"{prefix}.binding.deadline_at", (dates["created_at"] + timedelta(hours=24)).isoformat(),
        binding["deadline_at"],
    )
    actual_deadline = _new_book_utc(row.deadline_at).isoformat()
    _new_book_require(
        actual_deadline == binding["deadline_at"], code, scope,
        "execution_budgets.deadline_at", binding["deadline_at"], actual_deadline,
    )
    for name in ("provider_authorization", "independent_acceptance"):
        _new_book_require(
            not metadata.get(name), code, scope, f"execution_budgets.metadata.{name}",
            None, metadata.get(name),
        )
    summary = record.get("payload_summary")
    _new_book_require(
        type(summary) is dict and set(summary) == {"type", "length", "sha256"}
        and summary.get("type") == "dict" and type(summary.get("length")) is int
        and summary["length"] >= 0 and type(summary.get("sha256")) is str
        and len(summary["sha256"]) == 64,
        code, scope, f"{prefix}.payload_summary", "typed full POST hash", summary,
    )
    core = {key: value for key, value in record.items() if key != "sha256"}
    try:
        seal = _new_book_hash(core)
    except (TypeError, ValueError, RecursionError):
        seal = None
    _new_book_require(
        seal is not None and record.get("sha256") == seal,
        code, scope, f"{prefix}.sha256", seal, record.get("sha256"),
    )
    consumed = record.get("consumed_at")
    if consumed is None:
        _new_book_require(
            row.status != "active", code, scope, f"{prefix}.consumed_at",
            "committed consumption before active state", consumed,
        )
    else:
        try:
            consumed_at = datetime.fromisoformat(consumed) if type(consumed) is str else None
        except ValueError:
            consumed_at = None
        _new_book_require(
            consumed_at is not None and consumed_at.utcoffset() == timedelta(0)
            and dates["created_at"] <= consumed_at < dates["deadline_at"]
            and row.status != "awaiting_submission",
            code, scope, f"{prefix}.consumed_at", "once within original window", consumed,
        )
    return record


def assert_new_book_budget_route(
    row: ExecutionBudgetModel, *, model: str, endpoint: str | None,
) -> None:
    record = new_book_budget_policy(row)
    if record is not None:
        for field, actual in (("model", model), ("endpoint", endpoint)):
            expected = record["binding"][field]
            _new_book_require(
                type(actual) is str and actual == expected,
                "execution_new_book_route_mismatch", row.scope_id,
                f"request.{field}", expected, actual,
            )


@asynccontextmanager
async def _new_book_transaction(engine: AsyncEngine, *, identities: tuple[str, ...] = ()):
    """Serialize fresh identities too, before any lookup, with no sentinel rows."""
    if not isinstance(engine, AsyncEngine) or engine.dialect.name not in {"sqlite", "postgresql"}:
        raise ExecutionBudgetError("execution_ledger_unavailable")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with asyncio.timeout(5), factory() as session, session.begin():
            if engine.dialect.name == "sqlite":
                await session.execute(text("BEGIN IMMEDIATE"))
            else:
                keys = {
                    int.from_bytes(
                        hashlib.sha256(identity.encode()).digest()[:8], "big", signed=True,
                    )
                    for identity in identities
                }
                for key in sorted(keys):
                    await session.execute(select(func.pg_advisory_xact_lock(key)))
            yield session
    except ExecutionBudgetError:
        raise
    except Exception as error:
        raise ExecutionBudgetError("execution_ledger_unavailable") from error


async def prepare_new_book_budget(
    engine: AsyncEngine, *, authorization_id: str, actor: str, authorization_ref: str,
    payload: dict[str, Any], model: str, endpoint: str,
    max_calls: int | None = None, max_tokens: int | None = None,
) -> dict[str, Any]:
    """Trusted one-book grant; explicit bounded limits select v2, omission keeps v1.

    v2 binds the authorization, payload and limits to one native identity. It
    does not calculate cross-book allowances or modify any previous grant.
    Both protocols retain the original 24-hour window on retries.
    """
    identity = {
        "authorization_id": authorization_id, "actor": actor,
        "authorization_ref": authorization_ref,
    }
    for name, value in identity.items():
        _new_book_require(
            type(value) is str and bool(value.strip()) and len(value) <= 1024,
            "execution_new_book_grant_invalid", "", name, "trusted nonempty identity", value,
        )
    body = _new_book_payload(payload, preparing=True)
    _new_book_route(model, endpoint)
    payload_hash = _new_book_hash(body)
    bounded = max_calls is not None or max_tokens is not None
    policy = _NEW_BOOK_BOUNDED_POLICY if bounded else _NEW_BOOK_POLICY
    calls, tokens = _NEW_BOOK_CALLS, _NEW_BOOK_TOKENS
    if bounded:
        _new_book_bounded_limits(max_calls, max_tokens, code="execution_new_book_grant_invalid")
        calls, tokens = max_calls, max_tokens
        task_id = _new_book_bounded_identity(payload_hash, authorization_id, calls, tokens)
    else:
        task_id = _new_book_identity(payload_hash)
    scope = f"conception:{task_id}"
    # Keep the same authorization lock across v1/v2 and all limit variants:
    # changed limits must conflict with the existing scope, never create one.
    async with _new_book_transaction(engine, identities=(
        f"{_NEW_BOOK_POLICY}:authorization:{authorization_id}", scope,
    )) as session:
        prior_scope = await session.scalar(select(ExecutionBudgetModel.scope_id).where(
            ExecutionBudgetModel.metadata_json[_NEW_BOOK_KEY]["authorization_id"].as_string()
            == authorization_id,
        ).limit(1))
        _new_book_require(
            prior_scope in (None, scope), "execution_new_book_binding_mismatch", scope,
            "authorization_id.scope_id", prior_scope, scope,
        )
        row = await session.get(ExecutionBudgetModel, scope, with_for_update=True)
        if row is not None:
            record = new_book_budget_policy(row)
            _new_book_require(
                record is not None and record["schema"] == policy,
                "execution_new_book_binding_mismatch", scope,
                "execution_budgets.metadata.policy", policy,
                (row.metadata_json or {}).get("policy"),
            )
            for name, actual in {**identity, "model": model, "endpoint": endpoint}.items():
                expected = (
                    record["binding"][name] if name in {"model", "endpoint"} else record[name]
                )
                _new_book_require(
                    actual == expected, "execution_new_book_binding_mismatch", scope,
                    name, expected, actual,
                )
            return deepcopy(record["binding"])
        now = datetime.now(UTC)
        binding = {
            "grant_id": task_id, "task_id": task_id, "scope_id": scope,
            "payload_sha256": payload_hash, "model": model, "endpoint": endpoint,
            "max_calls": calls, "max_tokens": tokens,
            "created_at": now.isoformat(), "deadline_at": (now + timedelta(hours=24)).isoformat(),
        }
        if bounded:
            binding.update(schema=policy, authorization_id=authorization_id)
        record = {
            "schema": policy, "binding": binding, **identity,
            "payload_summary": _new_book_summary(body), "consumed_at": None,
        }
        record["sha256"] = _new_book_hash(record)
        session.add(ExecutionBudgetModel(
            scope_id=scope, max_calls=calls, max_tokens=tokens,
            calls_used=0, tokens_charged=0, status="awaiting_submission",
            deadline_at=now + timedelta(hours=24),
            metadata_json={"policy": policy, _NEW_BOOK_KEY: record},
        ))
        return deepcopy(binding)


async def consume_new_book_budget(
    engine: AsyncEngine, *, payload: dict[str, Any], model: str, endpoint: str,
) -> dict[str, Any] | None:
    """Normal POST consumes a prebound grant; replay never resets its ledger."""
    if type(payload) is dict and _NEW_BOOK_SELECTOR not in payload:
        return None
    locator = payload.get(_NEW_BOOK_SELECTOR) if type(payload) is dict else None
    try:
        valid = type(locator) is str and str(UUID(locator)) == locator
    except ValueError:
        valid = False
    code = "execution_new_book_grant_invalid"
    _new_book_require(valid, code, "", f"request.{_NEW_BOOK_SELECTOR}", "grant UUID", locator)
    scope = f"conception:{locator}"
    body = _new_book_payload(payload, preparing=False, scope=scope)
    async with _new_book_transaction(engine) as session:
        # PostgreSQL locks the native row; SQLite already holds BEGIN IMMEDIATE.
        row = await session.get(
            ExecutionBudgetModel, scope, with_for_update=True, populate_existing=True,
        )
        _new_book_require(
            row is not None, code, scope,
            f"request.{_NEW_BOOK_SELECTOR}", "persisted grant", locator,
        )
        record = new_book_budget_policy(row)
        _new_book_require(
            record is not None, code, scope,
            f"request.{_NEW_BOOK_SELECTOR}", "persisted finite grant", locator,
        )
        if _new_book_hash(body) != record["binding"]["payload_sha256"]:
            error = ExecutionBudgetError("execution_new_book_payload_mismatch", scope)
            error.differences = [{
                "field": "request.payload", "expected": deepcopy(record["payload_summary"]),
                "actual": _new_book_summary(body),
            }]
            raise error
        assert_new_book_budget_route(row, model=model, endpoint=endpoint)
        now = datetime.now(UTC)
        from bestseller.services.budget import execution_scope_block_code

        block = execution_scope_block_code(row, now=now)
        if block and block != "execution_submission_not_authorized":
            raise ExecutionBudgetError(block, scope)
        if now >= _new_book_utc(row.deadline_at):
            raise ExecutionBudgetError("execution_deadline_exhausted", scope)
        if record["consumed_at"] is None:
            _new_book_require(
                row.status == "awaiting_submission" and row.calls_used == row.tokens_charged == 0,
                "execution_new_book_binding_invalid", scope, "execution_budgets.consumption",
                ["awaiting_submission", 0, 0], [row.status, row.calls_used, row.tokens_charged],
            )
            record = {**record, "consumed_at": now.isoformat()}
            record["sha256"] = _new_book_hash({k: v for k, v in record.items() if k != "sha256"})
            row.metadata_json = {**row.metadata_json, _NEW_BOOK_KEY: record}
            row.status = "active"
        return deepcopy(record["binding"])


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
