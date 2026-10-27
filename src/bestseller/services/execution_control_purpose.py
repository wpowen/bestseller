"""Validate non-dispatch control rows by producer identity and original parents.

No status-only exemption: own sends are forbidden and every referenced result
must retain known noncommercial purpose. This never closes/replays a send or
changes financial, story-source, quality or author authority.
"""

from collections.abc import Mapping
import hashlib
import json
import re
from uuid import UUID
from sqlalchemy import select
from bestseller.infra.db.models import ExecutionOperationModel, ExecutionAttemptModel, LlmRunModel
from bestseller.services.commercial_protocol_transition_runtime import _row
from bestseller.services.commercial_request_purpose import (
    prove_known_noncommercial_request,
    _route,
    _no_commercial_markers,
)
from bestseller.services.independent_acceptance import digest

SCHEMA = "non-dispatch-control-purpose.v1"
STATUSES = frozenset(
    {
        "recovered_success_reference",
        "review_revision",
        "identity_recovery",
        "phase_handoff",
        "unusable_provider_return",
    }
)
INFO = "verified_non_dispatch_controls"


def _need(ok, code):
    if not ok:
        raise ValueError("commercial_control:" + code)


async def _source(session, source):
    """Original declared reference, not a guessed owner or newly granted grade."""
    _need(isinstance(source, Mapping), "source_shape")
    scope, key, h = (source.get(k) for k in ("source_scope", "operation_key", "input_hash"))
    op = await session.get(ExecutionOperationModel, key)
    _need(
        op is not None
        and op.status == "completed"
        and op.scope_id == scope
        and op.input_hash == h
        and digest(op.result_json) == source.get("result_sha256"),
        "source_result",
    )
    runs = []
    rid = source.get("source_llm_run_id")
    if rid:
        run = await session.get(LlmRunModel, UUID(str(rid)))
        _need(
            run is not None
            and run.prompt_hash == h
            and run.logical_role == source.get("role")
            and run.prompt_template == source.get("template"),
            "source_run",
        )
        runs = [run]
    _route(
        {
            "logical_role": source.get("role"),
            "prompt_template": source.get("template"),
            "prompt_version": None,
        }
    )
    attempts = list(
        await session.scalars(
            select(ExecutionAttemptModel).where(
                ExecutionAttemptModel.scope_id == scope, ExecutionAttemptModel.operation_key == key
            )
        )
    )
    operations = list(
        await session.scalars(
            select(ExecutionOperationModel).where(
                ExecutionOperationModel.scope_id == scope,
                ExecutionOperationModel.status == "transport_result",
            )
        )
    )
    transports = [o for o in operations if (o.result_json or {}).get("operation_key") == key]
    _need(
        prove_known_noncommercial_request(
            operation=_row(op),
            actual_attempts=[_row(a) for a in attempts],
            original_runs=[_row(r) for r in runs],
            transport_results=[_row(o) for o in transports],
        ),
        "source_purpose",
    )
    return {
        "operation_key": key,
        "scope_id": scope,
        "input_hash": h,
        "result_sha256": digest(op.result_json),
    }


async def verify_non_dispatch_control(session, *, operation, operations, attempts):
    if operation.status == "conception_logline_replacement":
        from bestseller.infra.db.models import ProjectModel
        from bestseller.services.commercial_judge_recovery import _prove_consumed_logline_control

        with session.no_autoflush:
            record = operation.result_json
            _need(isinstance(record, Mapping), "logline_control_record")
            projects = list(
                await session.scalars(
                    select(ProjectModel).where(
                        ProjectModel.slug == record.get("slug"),
                        ProjectModel.metadata_json["execution_scope_id"].as_string()
                        == operation.scope_id,
                    )
                )
            )
            _need(len(projects) == 1, "logline_control_project")
            native = await _prove_consumed_logline_control(
                session,
                project_id=projects[0].id,
                operation=operation,
                actual_attempts=[a for a in attempts if a.operation_key == operation.key],
                original_runs=[],
                transport_results=[],
            )
            _need(native is not None, "logline_control_native_proof")
            proof = {
                "schema": SCHEMA,
                "control_key": operation.key,
                "control_status": operation.status,
                "scope_id": operation.scope_id,
                "control_row_sha256": digest(_row(operation)),
                "parents": [
                    {
                        "operation_key": native["original_operation_key"],
                        "outcome_still_unknown": True,
                    },
                    {"operation_key": native["replacement_operation_key"]},
                ],
                "native_purpose_proof": native,
                "own_physical_sends": 0,
                "grade_granted": False,
                "credit_granted": False,
                "unknown_reconciled": False,
            }
            session.info.setdefault(INFO, {})[operation.key] = proof
            return proof
    if operation.status not in STATUSES:
        return None
    with session.no_autoflush:
        proof = await _verify(session, operation, operations, attempts)
        session.info.setdefault(INFO, {})[operation.key] = proof
        return proof


async def _verify(session, op, operations, attempts):
    record = op.result_json
    _need(isinstance(record, Mapping), "record")
    _need(not any(a.operation_key == op.key for a in attempts), "control_has_actual_send")
    _no_commercial_markers(record)
    parents = []
    if op.status == "recovered_success_reference":
        _need(
            op.owner == "audited-continuation"
            and op.key
            == hashlib.sha256(
                (op.scope_id + "\nadopt\n" + str(record.get("operation_key"))).encode()
            ).hexdigest()
            and op.input_hash == record.get("input_hash"),
            "reference_identity",
        )
        parents.append(await _source(session, record))
    elif op.status == "review_revision":
        _need(
            op.owner == "audited-completion-objective"
            and record.get("schema") == "conception-review-input-revision.v1"
            and record.get("grade_reused") is False
            and record.get("decision") == "fresh_current_input_review_required"
            and record.get("reuse_policy")
            == "only_identical_effective_input_can_reuse_a_completed_result"
            and isinstance(record.get("authorization_ref"), str)
            and bool(record["authorization_ref"])
            and record.get("effective_input_hash") == op.input_hash,
            "revision_record",
        )
        _route(
            {
                "logical_role": record.get("role"),
                "prompt_template": record.get("template"),
                "prompt_version": None,
            }
        )
        _need(
            op.key
            == digest(
                {
                    "schema": record["schema"],
                    "scope": op.scope_id,
                    "role": record["role"],
                    "template": record["template"],
                    "effective_input_hash": op.input_hash,
                }
            ),
            "revision_identity",
        )
        sources = record.get("previous_sources")
        _need(isinstance(sources, list) and bool(sources), "revision_sources")
        for source in sources:
            parents.append(await _source(session, source))
    elif op.status == "identity_recovery":
        _need(
            op.owner == "audited-completion-objective"
            and record.get("schema") == "completed-market-identity-recovery.v1"
            and op.key == digest(record)
            and record.get("scope_id") == op.scope_id
            and record.get("unknown_reconciled") is False
            and record.get("grade_changed") is False
            and type(record.get("provider_calls")) is int
            and record["provider_calls"] == 0
            and record.get("credit_released") is False,
            "identity_record",
        )
        _need(op.input_hash == (record.get("source") or {}).get("input_hash"), "identity_hash")
        parents.append(await _source(session, record["source"]))
    elif op.status == "phase_handoff":
        binding = record.get("binding")
        _need(
            op.owner == "explicit-same-project-phase-handoff"
            and isinstance(binding, Mapping)
            and binding.get("schema") == "execution-phase-handoff.v1"
            and binding.get("scope_id") == op.scope_id
            and binding.get("from_phase") == "approved_conception"
            and binding.get("to_phase") == "project_execution"
            and binding.get("unknown_reconciled") is False
            and binding.get("credit_released") is False
            and binding.get("grade_reused_for_changed_content") is False
            and type(record.get("provider_calls")) is int
            and record["provider_calls"] == 0
            and record.get("budget_regranted") is False
            and op.input_hash == binding.get("derived_binding_sha256")
            and op.key == digest({"schema": binding["schema"], "binding": binding}),
            "handoff_record",
        )
        parent = await session.get(ExecutionOperationModel, binding.get("quality_operation"))
        run = await session.get(LlmRunModel, UUID(str(binding.get("quality_run_id"))))
        _need(
            parent is not None
            and run is not None
            and parent.scope_id == op.scope_id
            and parent.status == "completed"
            and digest(parent.result_json) == binding.get("quality_result_sha256"),
            "handoff_parent",
        )
        parents.append(
            await _source(
                session,
                {
                    "source_scope": parent.scope_id,
                    "operation_key": parent.key,
                    "input_hash": parent.input_hash,
                    "role": run.logical_role,
                    "template": run.prompt_template,
                    "result_sha256": digest(parent.result_json),
                    "source_llm_run_id": str(run.id),
                },
            )
        )
    else:
        aid = record.get("attempt_id")
        actual = [a for a in attempts if a.id == aid]
        _need(len(actual) == 1, "partial_attempt")
        attempt = actual[0]
        parent = next((o for o in operations if o.key == attempt.operation_key), None)
        _need(
            parent is not None
            and parent.scope_id == op.scope_id
            and op.input_hash == attempt.input_hash == parent.input_hash
            and record.get("schema") == "provider-return.v1"
            and record.get("operation_key") == parent.key
            and record.get("input_hash") == attempt.input_hash
            and record.get("model_name") == attempt.model_name
            and record.get("usable") is False
            and record.get("adoption_authorized") is False
            and record.get("credit_released") is False
            and op.key
            == hashlib.sha256(f"provider-return.v1\n{op.scope_id}\n{aid}".encode()).hexdigest(),
            "partial_identity",
        )
        encoded = json.dumps(
            record.get("response"), ensure_ascii=False, sort_keys=True, allow_nan=False
        )
        _need(
            hashlib.sha256(encoded.encode()).hexdigest() == record.get("response_sha256"),
            "partial_payload",
        )
        _need(
            isinstance(record.get("wire_body_hash"), str)
            and re.fullmatch(r"[0-9a-f]{64}", record["wire_body_hash"]) is not None,
            "partial_wire_hash",
        )
        failures = [
            o
            for o in operations
            if o.status == "transport_failure_receipt"
            and (o.result_json or {}).get("operation_key") == parent.key
        ]
        parent_attempts = [a for a in attempts if a.operation_key == parent.key]
        _need(
            prove_known_noncommercial_request(
                operation=_row(parent),
                actual_attempts=[_row(a) for a in parent_attempts],
                failure_receipts=[_row(o) for o in failures],
            ),
            "partial_parent_purpose",
        )
        parents.append(
            {"operation_key": parent.key, "attempt_id": aid, "outcome_still_unknown": True}
        )
    return {
        "schema": SCHEMA,
        "control_key": op.key,
        "control_status": op.status,
        "scope_id": op.scope_id,
        "control_row_sha256": digest(_row(op)),
        "parents": parents,
        "own_physical_sends": 0,
        "grade_granted": False,
        "credit_granted": False,
        "unknown_reconciled": False,
    }
