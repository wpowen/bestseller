"""Audited independent UI acceptance; never clears or replays its ancestor.

All token reservations use the published model input ceiling, not a local
estimate. The submitted window starts once and cannot slide on resume.
"""
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import json
import weakref
from uuid import uuid5, NAMESPACE_URL

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from bestseller.infra.db.models import ExecutionBudgetModel, ExecutionOperationModel, ExecutionAttemptModel
from bestseller.services.budget import ExecutionBudgetError
from bestseller.services.minimax_book_cost_contract import input_bound, CONTRACT_SHA256

KEY = "independent_acceptance"
SCHEMA = "independent-ui-acceptance.v1"
MODEL = "openai/MiniMax-M3.1-Flash"
_LOCKS = weakref.WeakKeyDictionary()


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(",", ":"), default=str).encode()).hexdigest()


def utc(value):
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def snapshot(row):
    return {"scope": row.scope_id, "status": row.status, "max_calls": row.max_calls,
            "max_tokens": row.max_tokens, "calls_used": row.calls_used,
            "tokens_charged": row.tokens_charged, "deadline_at": utc(row.deadline_at).isoformat(),
            "metadata_sha256": digest(row.metadata_json)}


def payload_identity(payload):
    hints = payload.get("user_hints") or {}
    return digest({"seed": payload.get("concept_seed") or hints.get("concept_seed"),
        "profile": payload.get("production_profile") or hints.get("production_profile"),
        "project_type": payload.get("project_type"), "chapters": payload.get("target_chapters"),
        "words": payload.get("target_words"), "model_id": payload.get("llm_model_id"),
        "conception": payload.get("_run_conception")})


async def old_cost_proof(session, row):
    attempts = list(await session.scalars(select(ExecutionAttemptModel).where(ExecutionAttemptModel.scope_id == row.scope_id)))
    records = list(await session.scalars(select(ExecutionOperationModel).where(
        ExecutionOperationModel.scope_id == row.scope_id,
        ExecutionOperationModel.status.in_(["transport_result", "unusable_provider_return"]))))
    usage = {}
    for record in records:
        v = record.result_json or {}
        body = v.get("result") or v.get("response") or {}
        units = body.get("usage") or {}
        i, o = body.get("input_tokens", units.get("prompt_tokens")), body.get("output_tokens", units.get("completion_tokens"))
        if type(i) is int and type(o) is int and min(i, o) >= 0:
            if v["attempt_id"] in usage and usage[v["attempt_id"]] != (i, o):
                raise ValueError("conflicting original provider usage")
            usage[v["attempt_id"]] = (i, o)
    inputs = outputs = 0
    evidence = []
    for attempt in attempts:
        if attempt.model_name != MODEL:
            raise ValueError("original model not covered by price proof")
        if (attempt.id in usage
            and attempt.status in {"completed", "reconciled_charged"}
            and attempt.actual_tokens is not None
            and attempt.actual_tokens == sum(usage[attempt.id])):
            # Partial usage never releases an unknown or quarantined hold.
            # A charged abandonment with no settled actual_tokens also keeps
            # its full reservation, even when an unusable body contains usage.
            i, o = usage[attempt.id]
            method = "retained_provider_usage"
        elif attempt.status == "not_sent" and attempt.actual_tokens == 0:
            i = o = 0
            method = "confirmed_not_sent"
        else:
            # Historical reservation is NOT an input bound. Its construction
            # does cover the requested output cap; reserve a full context for input.
            bound = policy(row)
            output_bound = attempt.reserved_tokens
            if bound and 0 < attempt.reserved_tokens - 1_000_000 <= bound["request"]["max_output"]:
                output_bound = attempt.reserved_tokens - 1_000_000
            i, o = 1_000_000, output_bound
            method = "unknown_full_input_context_and_output_cap_upper"
        inputs += i
        outputs += o
        evidence.append({"attempt": attempt.id, "input": i, "output": o, "method": method})
    if len(attempts) != row.calls_used:
        raise ValueError("original attempt count mismatch")
    return {"calls": len(attempts), "tokens_upper": inputs + outputs,
            "standard_micro_cny_upper": inputs + 4 * outputs,
            "evidence_sha256": digest(sorted(evidence, key=lambda x: x["attempt"]))}


def policy(row):
    value = (row.metadata_json or {}).get(KEY)
    if value is None:
        return None
    request = value.get("request", {})
    if (value.get("schema") != SCHEMA or value.get("request_sha256") != digest(request)
        or request.get("scope") != row.scope_id or request.get("counter_sha256") != CONTRACT_SHA256
        or (row.max_calls, row.max_tokens) != (request.get("new_calls"), request.get("new_tokens"))
        or not request.get("authorization_ref") or request.get("model") != MODEL
        or request.get("new_calls", 0) > 128 or request.get("window_seconds") != 7200):
        raise ExecutionBudgetError("execution_acceptance_binding_invalid", row.scope_id)
    return value


async def prepare(engine, *, old_scope, authorization_id, actor, authorization_ref,
                  payload, endpoint, combined_token_cap=3_000_000, total_money_micro_cny=75_000_000):
    if not actor or not authorization_ref or not authorization_id:
        raise ValueError("explicit acceptance authorization required")
    if total_money_micro_cny != 75_000_000 or type(combined_token_cap) is not int:
        raise ValueError("acceptance total financial boundary changed")
    if (payload.get("production_profile") != "complete" or payload.get("target_chapters") != 4
        or payload.get("target_words") != 8000 or payload.get("project_type") != "fanqie_short"
        or payload.get("llm_model_id") != "minimax-m3.1-flash" or not payload.get("_run_conception")):
        raise ValueError("acceptance objective mismatch")
    if endpoint not in {"https://api.minimaxi.com/v1", "https://api.minimax.cn/v1"}:
        raise ValueError("acceptance endpoint not authorized")
    scope = "conception:" + str(uuid5(NAMESPACE_URL, SCHEMA + authorization_id))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session, session.begin():
        old = await session.scalar(select(ExecutionBudgetModel).where(ExecutionBudgetModel.scope_id == old_scope).with_for_update())
        if old is None or old.status != "unknown" or utc(old.deadline_at) > datetime.now(timezone.utc):
            raise ValueError("original acceptance must remain expired and unknown")
        prior = await session.get(ExecutionBudgetModel, scope)
        if prior is not None:
            p = policy(prior)
            if p["request"]["authorization_ref"] != authorization_ref or p["request"]["payload_sha256"] != payload_identity(payload):
                raise ValueError("acceptance authorization already bound")
            return p
        sibling = await session.scalar(select(ExecutionBudgetModel.scope_id).where(
            ExecutionBudgetModel.metadata_json[KEY]["request"]["old_scope"].as_string() == old_scope).limit(1))
        if sibling:
            raise ValueError("original acceptance already has an independent successor")
        proof = await old_cost_proof(session, old)
        combined_tokens = proof["tokens_upper"] + 2_000_000
        standard_cost = proof["standard_micro_cny_upper"] + 8_000_000
        margin_cost = (standard_cost * 3 + 1) // 2
        if combined_tokens > combined_token_cap:
            raise ValueError(f"strict_combined_token_cap_not_proven:{combined_tokens}>{combined_token_cap}")
        if proof["calls"] != 56 or margin_cost > min(total_money_micro_cny, 18_000_000):
            raise ValueError("strict_combined_call_or_money_cap_not_proven")
        request = {"scope": scope, "old_scope": old_scope, "old_snapshot": snapshot(old),
            "old_proof": proof, "new_calls": 128, "new_tokens": 2_000_000,
            "combined_calls": 184, "combined_tokens_cap": combined_token_cap,
            "combined_tokens_upper": combined_tokens, "combined_cost_micro_cny_upper": margin_cost,
            "total_money_micro_cny": total_money_micro_cny, "window_seconds": 7200,
            "model": MODEL, "endpoint": endpoint, "max_output": 32768,
            "payload_sha256": payload_identity(payload), "actor": actor,
            "authorization_ref": authorization_ref, "counter_sha256": CONTRACT_SHA256}
        saved = {"schema": SCHEMA, "request": request, "request_sha256": digest(request),
                 "prepared_at": datetime.now(timezone.utc).isoformat(), "submitted_at": None}
        session.add(ExecutionBudgetModel(scope_id=scope, max_calls=128, max_tokens=2_000_000,
            calls_used=0, tokens_charged=0, status="awaiting_submission",
            deadline_at=datetime.now(timezone.utc) + timedelta(minutes=30), metadata_json={KEY: saved}))
    return saved


async def consume_ui(engine, payload):
    identity = payload_identity(payload)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session, session.begin():
        rows = list(await session.scalars(select(ExecutionBudgetModel).where(
            ExecutionBudgetModel.status == "awaiting_submission").with_for_update()))
        for row in rows:
            p = policy(row)
            if p is None or p["request"].get("continuation_task_id") or p["request"]["payload_sha256"] != identity:
                continue
            await validate_old(session, p)
            now = datetime.now(timezone.utc)
            if utc(row.deadline_at) <= now:
                raise ValueError("acceptance submission authorization expired")
            p = {**p, "submitted_at": now.isoformat()}
            row.metadata_json = {**row.metadata_json, KEY: p}
            row.deadline_at = now + timedelta(seconds=7200)
            row.status = "active"
            return row.scope_id.split(":", 1)[1]
    return None


async def validate_old(session, saved, *, lock=True):
    req = saved["request"]
    ancestors = req.get("ancestor_snapshots") or [req["old_snapshot"]]
    for expected in sorted(ancestors, key=lambda x: x["scope"]):
        statement = select(ExecutionBudgetModel).where(ExecutionBudgetModel.scope_id == expected["scope"])
        if lock:
            statement = statement.with_for_update()
        old = await session.scalar(statement)
        if old is None or snapshot(old) != expected:
            raise ExecutionBudgetError("execution_original_acceptance_changed", req["scope"])


async def quote(ledger, payload, endpoint):
    async with ledger.sessions() as session:
        row = await session.get(ExecutionBudgetModel, ledger.scope_id)
        saved = policy(row) if row is not None else None
    if saved is None:
        return None
    req = saved["request"]
    if (endpoint != req["endpoint"] or payload.get("model") != MODEL
        or payload.get("max_tokens", 0) > req["max_output"]):
        raise ExecutionBudgetError("execution_acceptance_route_changed", ledger.scope_id)
    bound = input_bound(payload)
    return {**bound, "authorization_sha256": saved["request_sha256"]}


async def validate_reservation(session, row, proof, *, model, input_hash, tokens, endpoint):
    saved = policy(row)
    if saved is None:
        if proof is not None:
            raise ExecutionBudgetError("execution_acceptance_binding_missing", row.scope_id)
        # An ancestor cannot be reopened while its independently authorized
        # successor relies on it staying frozen. No ancestor fields are changed.
        linked = await session.scalar(select(ExecutionBudgetModel.scope_id).where(
            ExecutionBudgetModel.metadata_json[KEY]["request"]["old_scope"].as_string() == row.scope_id).limit(1))
        if linked:
            raise ExecutionBudgetError("execution_original_acceptance_frozen", row.scope_id)
        return
    req = saved["request"]
    if (not isinstance(proof, dict) or proof.get("authorization_sha256") != saved["request_sha256"]
        or proof.get("counter_sha256") != CONTRACT_SHA256 or proof.get("input_hash") != input_hash
        or proof.get("input_tokens_upper") != 1_000_000
        or not 0 < proof.get("output_tokens_upper", 0) <= req["max_output"]
        or tokens != proof["input_tokens_upper"] + proof["output_tokens_upper"]
        or model != MODEL or endpoint != req["endpoint"] or not saved.get("submitted_at")):
        raise ExecutionBudgetError("execution_acceptance_reservation_invalid", row.scope_id)
    await validate_old(session, saved)
    if input_hash in {req.get("blocked_input_hash"), *req.get("blocked_input_hashes", [])}:
        raise ExecutionBudgetError("execution_unknown_replay_forbidden", row.scope_id)
    from bestseller.services.budget import completion_authorization
    objective = completion_authorization(row)
    if (objective and req.get("completion_authorization_ref")
        == objective.get("authorization_ref")):
        # Explicit later user instruction replaces aggregate quotas for this
        # objective; exact route, capacity, frozen history and unknown fences
        # above remain mandatory. Legacy grant fields remain as audit history.
        return
    if (row.calls_used + 1 + req["old_proof"]["calls"] > req["combined_calls"]
        or row.tokens_charged + tokens + req["old_proof"]["tokens_upper"] > req["combined_tokens_cap"]):
        raise ExecutionBudgetError("execution_combined_acceptance_exhausted", row.scope_id)


@asynccontextmanager
async def serialize(scope):
    loop = asyncio.get_running_loop()
    lock = _LOCKS.setdefault(loop, {}).setdefault(scope, asyncio.Lock())
    async with lock:
        yield
