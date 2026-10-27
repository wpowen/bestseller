"""Operator-only continuation after a versioned repair; no old unknown replay.

A later explicit completion objective can replace aggregate spending quotas.
This never mutates an ancestor, settles unknown usage, changes provider, or
creates an automatic retry/renewal hook. UI consumes the prepared grant once.
"""
from datetime import datetime, timedelta, timezone
from uuid import NAMESPACE_URL, UUID, uuid5
from urllib.parse import urlsplit
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker
from bestseller.infra.db.models import ExecutionBudgetModel, ExecutionOperationModel, ExecutionAttemptModel, LlmRunModel
from bestseller.services.independent_acceptance import KEY, SCHEMA, MODEL, digest, snapshot, policy, old_cost_proof, payload_identity, utc
from bestseller.services.minimax_book_cost_contract import CONTRACT_SHA256
from bestseller.services.conception_continuation import PREFIX


async def prepare(engine, *, task_id, payload, source_scope, authorization_id,
                  authorization_ref, actor, objective, repaired_template, repaired_version):
    if (not all(isinstance(x, str) and x.strip() for x in
            (task_id, source_scope, authorization_id, authorization_ref, actor, objective,
             repaired_template, repaired_version)) or repaired_version != "v2"):
        raise ValueError("explicit versioned completion repair authorization required")
    scope = PREFIX + task_id + ":" + str(uuid5(NAMESPACE_URL, authorization_id))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session, session.begin():
        source = await session.scalar(select(ExecutionBudgetModel).where(
            ExecutionBudgetModel.scope_id == source_scope).with_for_update())
        prior = policy(source) if source else None
        if (prior is None or source.status != "unknown"
            or prior["request"].get("continuation_task_id") != task_id
            or prior["request"]["payload_sha256"] != payload_identity(payload)):
            raise ValueError("same-story isolated source required")
        binding = {"authorization_ref": authorization_ref, "actor": actor, "objective": objective,
                   "source_scope": source_scope, "repaired_template": repaired_template,
                   "repaired_version": repaired_version, "payload_sha256": payload_identity(payload)}
        existing = await session.get(ExecutionBudgetModel, scope)
        if existing:
            if (existing.metadata_json or {}).get("completion_repair_binding") != binding:
                raise ValueError("completion repair authorization conflict")
            return policy(existing)
        sibling = await session.scalar(select(ExecutionBudgetModel.scope_id).where(
            ExecutionBudgetModel.metadata_json[KEY]["request"]["old_scope"].as_string() == source_scope).limit(1))
        if sibling:
            raise ValueError("source already has a successor; inspect it instead of duplicating")
        ancestor_ids = sorted({source_scope, *(x["scope"] for x in prior["request"].get("ancestor_snapshots", []))})
        ancestors, proofs, blocked = [], [], []
        for key in ancestor_ids:
            row = await session.scalar(select(ExecutionBudgetModel).where(
                ExecutionBudgetModel.scope_id == key).with_for_update())
            if row is None or row.status != "unknown":
                raise ValueError("all historical ancestors must remain isolated")
            if await session.scalar(select(ExecutionAttemptModel.id).where(
                ExecutionAttemptModel.scope_id == key, ExecutionAttemptModel.status == "reserved").limit(1)):
                raise ValueError("in-flight request prevents repair handoff")
            ancestors.append(snapshot(row)); proofs.append(await old_cost_proof(session, row))
            blocked.extend(await session.scalars(select(ExecutionAttemptModel.input_hash).where(
                ExecutionAttemptModel.scope_id == key,
                ExecutionAttemptModel.actual_tokens.is_(None),
                ExecutionAttemptModel.status.in_(["unknown", "reconciled_charged"]))))
        failures = list(await session.scalars(select(ExecutionOperationModel).where(
            ExecutionOperationModel.scope_id == source_scope,
            ExecutionOperationModel.status == "transport_failure_receipt")))
        if not any((x.result_json or {}).get("prompt_template") == repaired_template for x in failures):
            raise ValueError("repair must identify a retained failed operation")
        cached = [{**x, "source_scope": x.get("source_scope", prior["request"]["old_scope"])}
                  for x in prior["request"]["cached_results"]]
        ops = list(await session.scalars(select(ExecutionOperationModel).where(
            ExecutionOperationModel.scope_id == source_scope, ExecutionOperationModel.status == "completed")))
        for op in ops:
            result = op.result_json or {}; origin = (result.get("metadata") or {}).get("paid_call_origin") or {}
            if result.get("model_name") != MODEL or not origin.get("prompt_template"):
                raise ValueError("retained success lacks model attribution")
            run = await session.get(LlmRunModel, UUID(str(result["llm_run_id"]))) if result.get("llm_run_id") else None
            if run is None:
                runs = list(await session.scalars(select(LlmRunModel).where(
                    LlmRunModel.metadata_json["execution_receipt"]["operation_key"].as_string() == op.key)))
                if len(runs) != 1: raise ValueError("retained success lacks unique paid audit")
                run = runs[0]
            if (run.model_name != MODEL or (run.input_tokens, run.output_tokens) !=
                    (result.get("input_tokens"), result.get("output_tokens"))):
                raise ValueError("retained paid usage mismatch")
            cached.append({"operation_key": op.key, "input_hash": op.input_hash,
                "result_sha256": digest(result), "template": origin["prompt_template"],
                "role": origin["logical_role"], "source_llm_run_id": str(run.id), "source_scope": source_scope})
        cached = list({x["operation_key"]: x for x in cached}.values())
        endpoint = prior["request"]["endpoint"]
        if endpoint not in {"https://api.minimaxi.com/v1", "https://api.minimax.cn/v1"}:
            raise ValueError("configured MiniMax endpoint required")
        provider = {"mode": "completion_bound", "provider": "minimax", "status": "active",
            "models": [MODEL], "endpoint_hosts": [urlsplit(endpoint).hostname],
            "objective": objective, "authorization_ref": authorization_ref,
            "aggregate_max_calls": None, "aggregate_max_tokens": None, "aggregate_deadline_at": None}
        req = {**prior["request"], "scope": scope, "old_scope": source_scope,
            "old_snapshot": snapshot(source), "ancestor_snapshots": ancestors,
            "old_proof": {"calls": sum(x["calls"] for x in proofs),
                "tokens_upper": sum(x["tokens_upper"] for x in proofs),
                "standard_micro_cny_upper": sum(x["standard_micro_cny_upper"] for x in proofs),
                "evidence_sha256": digest(proofs)},
            "new_calls": source.max_calls, "new_tokens": source.max_tokens,
            "fixed_deadline_at": utc(source.deadline_at).isoformat(),
            "completion_authorization_ref": authorization_ref,
            "max_output": 524288, "cached_results": cached,
            "blocked_input_hashes": sorted(set(blocked)), "actor": actor,
            "authorization_ref": authorization_ref, "counter_sha256": CONTRACT_SHA256,
            "legacy_aggregate_fields_effective": False,
            "repair": {"template": repaired_template, "version": repaired_version}}
        saved = {"schema": SCHEMA, "request": req, "request_sha256": digest(req),
            "prepared_at": datetime.now(timezone.utc).isoformat(), "submitted_at": None}
        session.add(ExecutionBudgetModel(scope_id=scope, max_calls=source.max_calls,
            max_tokens=source.max_tokens, calls_used=0, tokens_charged=0,
            status="awaiting_submission", deadline_at=datetime.now(timezone.utc)+timedelta(minutes=30),
            metadata_json={KEY: saved, "provider_authorization": provider,
                "completion_repair_binding": binding,
                "operator_authorization_receipt": {"schema": "completion-repair.v1",
                    "at": datetime.now(timezone.utc).isoformat(), **binding,
                    "historical_scopes_unchanged": ancestors,
                    "unknown_reservations_released": False, "automatic_dispatch": False}}))
    return saved
