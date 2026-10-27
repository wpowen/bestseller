"""Pure proof for one explicitly authorized successor evaluation of one body.

No DB/provider calls, pointer changes, financial release, grades or dispatch.
The caller must lock/CAS the actual target and old pointer, load ALL rows for
the old roots, and revalidate every old slot via _verified_paid_slot with its
original CommercialSources receipt. Hashes prove supplied-row integrity, not
database completeness, operator authority or natural-language entailment.

financial_snapshots has runs, operations, attempts and slot_reports lists. Each
slot_reports row is {sample_slot, attempt, report}, including every used round.
new_protocol_manifest has prompt_version, source_manifest and effective_input_hash.
Bindings use the existing commercial-component-cohort.v1 runtime shape.
Persist the deterministic receipt BEFORE dispatch. Normal preparation may only
consume that existing receipt once and record its owner; it cannot auto-grant.
"""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math
import re
from typing import Any
from uuid import UUID

# ruff: noqa: ANN401
SCHEMA = "commercial-protocol-successor.v1"
_BINDING = {
    "project_id",
    "chapter_id",
    "draft_id",
    "source_binding",
    "evaluation_contract",
    "samples",
    "scope_id",
    "effective_critic_sha256",
}
_SOURCE = {"body_sha256", "semantic_source_sha256", "previous_sha256", "source_protocol_sha256"}
_SEMANTIC = {
    "system_prompt_sha256",
    "instruction_prefix_sha256",
    "tools_sha256",
    "wire_version",
    "adapter_version",
    "submission_policy",
}
_FLAGS = {
    "credit_released": False,
    "old_grades_reused": False,
    "old_history_mutated": False,
    "unknown_financial_attempts_waived": False,
    "old_report_unknowns_preserved": True,
    "baseline_inventory_grown": False,
    "quota_reset": False,
    "provider_calls_performed": 0,
    "new_assessment_requires_existing_scope_budget": True,
}


def _need(value: Any, code: str) -> None:
    if not value:
        raise ValueError(f"commercial_successor_{code}")


def _json(value: Any) -> str:
    def check(item: Any) -> None:
        if item is None or type(item) in (str, bool, int):
            return
        if type(item) is float and math.isfinite(item):
            return
        if type(item) is list:
            for child in item:
                check(child)
            return
        if type(item) is dict and all(type(k) is str for k in item):
            for child in item.values():
                check(child)
            return
        raise ValueError("commercial_successor_not_strict_json")

    check(value)
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def _hash(value: Any) -> str:
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _sha(value: Any) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _object(text: Any) -> dict:
    def unique(pairs: list[tuple[str, Any]]) -> dict:
        out = {}
        for key, value in pairs:
            _need(key not in out, "duplicate_json_key")
            out[key] = value
        return out

    try:
        parsed = json.loads(text, object_pairs_hook=unique)
        _json(parsed)
    except (TypeError, ValueError) as exc:
        raise ValueError("commercial_successor_invalid_json_receipt") from exc
    _need(isinstance(parsed, dict), "object_required")
    return parsed


def _index(rows: Any, key: str) -> dict:
    _need(isinstance(rows, list), "inventory_list")
    out = {}
    for row in rows:
        _need(isinstance(row, dict), "inventory_row")
        uid = row.get(key)
        _need(isinstance(uid, str) and uid and uid not in out, "inventory_identity")
        out[uid] = row
    return out


def _uuid(value: Any) -> bool:
    try:
        return isinstance(value, str) and str(UUID(value)) == value
    except ValueError:
        return False


def build_commercial_protocol_successor_receipt(
    *,
    old_binding: dict,
    old_pointer: dict,
    old_journal: dict,
    financial_snapshots: dict,
    new_binding: dict,
    new_protocol_manifest: dict,
    actor: str,
    reason: str,
) -> dict:
    """Build an audit proof, never a grant of free or additional business slots.

    Financial proof currently requires completed actual attempts with physical
    returns. Unknown, pending, rejected or reconciled-only actuals fail closed;
    this helper does not reinterpret their debt. Old report-level UNKNOWN may
    remain if the original paid return is complete and financially settled.
    actor/reason and old history cannot mint another policy_target_key.
    """
    _json(
        [
            old_binding,
            old_pointer,
            old_journal,
            financial_snapshots,
            new_binding,
            new_protocol_manifest,
            actor,
            reason,
        ]
    )
    _need(
        isinstance(actor, str) and actor.strip() and isinstance(reason, str) and reason.strip(),
        "authorization_text",
    )
    _need(
        isinstance(old_binding, dict)
        and isinstance(new_binding, dict)
        and set(old_binding) == _BINDING
        and set(new_binding) == _BINDING,
        "binding_shape",
    )
    _need(
        all(_uuid(old_binding[k]) for k in ("project_id", "chapter_id", "draft_id")), "target_uuid"
    )
    _need(isinstance(old_binding["scope_id"], str) and old_binding["scope_id"], "scope")
    _need(
        type(old_binding["samples"]) is int and old_binding["samples"] == 3, "three_slots_required"
    )
    _need(_sha(old_binding["effective_critic_sha256"]), "critic_hash")
    _need(
        all(
            new_binding[k] == old_binding[k]
            for k in _BINDING - {"evaluation_contract", "source_binding"}
        ),
        "target_or_scope_changed",
    )
    before, after = old_binding["source_binding"], new_binding["source_binding"]
    _need(
        isinstance(before, dict)
        and isinstance(after, dict)
        and set(before) == _SOURCE
        and set(after) == _SOURCE
        and all(_sha(x) for x in [*before.values(), *after.values()]),
        "source_binding",
    )
    _need(
        all(before[k] == after[k] for k in _SOURCE - {"source_protocol_sha256"}),
        "actual_source_changed",
    )
    old_contract, new_contract = (
        old_binding["evaluation_contract"],
        new_binding["evaluation_contract"],
    )
    _need(
        isinstance(old_contract, dict)
        and isinstance(new_contract, dict)
        and set(old_contract) == set(new_contract)
        and _SEMANTIC <= set(old_contract),
        "contract_shape",
    )
    _need(
        all(
            _sha(c.get(k))
            for c in (old_contract, new_contract)
            for k in ("system_prompt_sha256", "instruction_prefix_sha256", "tools_sha256")
        ),
        "contract_hashes",
    )
    allowed = _SEMANTIC | {"source_protocol_sha256"}
    _need(
        all(old_contract[k] == new_contract[k] for k in set(old_contract) - allowed),
        "nonprotocol_evaluation_changed",
    )
    changed = sorted(k for k in _SEMANTIC if old_contract[k] != new_contract[k])
    _need(changed, "genuine_protocol_change_required")
    _need(
        old_contract.get("source_protocol_sha256") == before["source_protocol_sha256"]
        and new_contract.get("source_protocol_sha256") == after["source_protocol_sha256"],
        "contract_source_protocol",
    )
    _need(
        isinstance(new_protocol_manifest, dict)
        and set(new_protocol_manifest)
        == {"prompt_version", "source_manifest", "effective_input_hash"},
        "new_protocol_shape",
    )
    manifest = new_protocol_manifest["source_manifest"]
    _need(
        isinstance(manifest, dict)
        and manifest
        and all(isinstance(k, str) and k and _sha(v) for k, v in manifest.items()),
        "manifest",
    )
    _need(_sha(new_protocol_manifest["effective_input_hash"]), "new_effective_input_hash")
    new_version = re.match(r"^v(\d+)(?:[-.]|$)", str(new_protocol_manifest["prompt_version"]))
    _need(new_version, "new_prompt_version")
    _need(
        isinstance(old_pointer, dict)
        and set(old_pointer) == {"workflow_run_id", "binding_sha256"}
        and _uuid(old_pointer["workflow_run_id"])
        and old_pointer["binding_sha256"] == _hash(old_binding),
        "old_pointer",
    )
    _need(
        isinstance(old_journal, dict)
        and old_journal.get("schema") == "commercial-component-cohort.v1"
        and old_journal.get("binding") == old_binding,
        "old_journal",
    )
    slots = old_journal.get("slots")
    expected_slots = {f"chapter-quality.independent-v2:{i}" for i in range(3)}
    _need(isinstance(slots, dict) and set(slots) == expected_slots, "old_slot_set")
    _need(
        isinstance(financial_snapshots, dict)
        and set(financial_snapshots) == {"runs", "operations", "attempts", "slot_reports"},
        "financial_snapshot_shape",
    )
    runs = _index(financial_snapshots["runs"], "id")
    operations = _index(financial_snapshots["operations"], "key")
    actuals = _index(financial_snapshots["attempts"], "id")
    report_rows = financial_snapshots["slot_reports"]
    _need(isinstance(report_rows, list), "slot_reports_list")
    reports = {}
    for row in report_rows:
        _need(
            isinstance(row, dict) and set(row) == {"sample_slot", "attempt", "report"},
            "slot_report_row",
        )
        _need(
            isinstance(row["sample_slot"], str)
            and row["sample_slot"] in expected_slots
            and type(row["attempt"]) is int
            and row["attempt"] in (1, 2),
            "slot_report_identity",
        )
        key = (row["sample_slot"], row["attempt"])
        _need(key not in reports and isinstance(row["report"], dict), "slot_report_duplicate")
        reports[key] = row["report"]
    expected_rounds = set()
    for slot, entry in slots.items():
        _need(
            isinstance(entry, dict)
            and entry.get("state") == "completed"
            and type(entry.get("used_attempts")) is int
            and entry["used_attempts"] in (1, 2),
            "old_slot_unsettled",
        )
        expected_rounds.update((slot, i) for i in range(1, entry["used_attempts"] + 1))
        _need(
            reports.get((slot, entry["used_attempts"])) == entry.get("report"),
            "latest_report_changed",
        )
        if "previous_report" in entry:
            _need(
                entry["used_attempts"] == 2 and reports.get((slot, 1)) == entry["previous_report"],
                "previous_report_changed",
            )
    _need(set(reports) == expected_rounds, "all_slot_rounds_required")
    seen_runs, seen_roots, seen_actuals, seen_transports, versions = (
        set(),
        set(),
        set(),
        set(),
        set(),
    )
    for (slot, number), report in sorted(reports.items()):
        envelope = _object(report.get("raw_excerpt"))
        _need(
            envelope.get("schema_version") == "commercial-slot-evidence.v1"
            and envelope.get("sample_slot") == slot
            and type(envelope.get("attempt")) is int
            and envelope["attempt"] == number
            and envelope.get("evaluation_contract") == old_contract
            and envelope.get("source_binding") == before,
            "old_report_binding",
        )
        _need(isinstance(envelope.get("validation_errors"), list), "old_validation_state_missing")
        uid = report.get("llm_run_id")
        _need(
            isinstance(uid, str) and uid in runs and uid not in seen_runs,
            "independent_runs_required",
        )
        run = runs[uid]
        _need(
            run.get("project_id") == old_binding["project_id"]
            and _uuid(run.get("workflow_run_id"))
            and run.get("prompt_template") == "chapter_commercial_quality_judge"
            and run.get("logical_role") == "critic"
            and run.get("provider") not in {None, "", "mock", "test", "fallback"}
            and isinstance(run.get("model_name"), str)
            and run["model_name"],
            "old_run_binding",
        )
        version = re.match(r"^v(\d+)(?:[-.]|$)", str(run.get("prompt_version")))
        _need(version and int(new_version[1]) > int(version[1]), "prompt_not_successor")
        versions.add(run["prompt_version"])
        meta = run.get("metadata_json")
        _need(
            isinstance(meta, dict)
            and meta.get("commercial_evaluation_contract") == old_contract
            and meta.get("commercial_sample_slot") == slot,
            "old_run_contract",
        )
        source = meta.get("commercial_source_input_receipt", {})
        _need(
            all(source.get(k) == before[k] for k in _SOURCE)
            and source.get("prompt_version") == run["prompt_version"],
            "old_run_source",
        )
        _need(
            all(
                (envelope.get("exact_source_audit") or {}).get(k) == source.get(k)
                for k in (
                    "body_sha256",
                    "source_sha256",
                    "previous_sha256",
                    "assembled_source_sha256",
                )
            ),
            "old_exact_source",
        )
        execution = meta.get("execution_receipt", {})
        root_key = execution.get("operation_key")
        _need(
            execution.get("scope_id") == old_binding["scope_id"]
            and root_key in operations
            and root_key not in seen_roots
            and _sha(run.get("prompt_hash")),
            "old_execution_binding",
        )
        root = operations[root_key]
        _need(
            root.get("scope_id") == old_binding["scope_id"]
            and root.get("status") == "completed"
            and root.get("input_hash") == run["prompt_hash"],
            "old_root_unsettled",
        )
        actual = [a for a in actuals.values() if a.get("operation_key") == root_key]
        declared = execution.get("attempts")
        _need(
            isinstance(declared, list) and len(actual) == len(declared) == 1,
            "all_actual_attempts_required",
        )
        attempt = actual[0]
        _need(
            isinstance(declared[0], dict)
            and declared[0].get("attempt_id") == attempt["id"]
            and declared[0].get("status") == "completed"
            and attempt.get("status") == "completed"
            and type(attempt.get("actual_tokens")) is int
            and attempt["actual_tokens"] >= 0
            and attempt.get("scope_id") == old_binding["scope_id"]
            and attempt.get("input_hash") == run["prompt_hash"]
            and attempt.get("model_name") == run["model_name"],
            "actual_unsettled_or_mismatch",
        )
        descriptor = declared[0]
        _need(
            ("model" not in descriptor or descriptor["model"] == attempt["model_name"])
            and (
                "effective_input_hash" not in descriptor
                or descriptor["effective_input_hash"] == attempt["input_hash"]
            )
            and (
                "reserved_tokens" not in descriptor
                or (
                    type(descriptor["reserved_tokens"]) is int
                    and descriptor["reserved_tokens"] >= 0
                    and type(attempt.get("reserved_tokens")) is int
                    and descriptor["reserved_tokens"] == attempt["reserved_tokens"]
                )
            )
            and ("wire_body_hash" not in descriptor or _sha(descriptor["wire_body_hash"])),
            "actual_descriptor_mismatch",
        )
        transport_key = hashlib.sha256(
            f"transport-result.v1\n{old_binding['scope_id']}\n{attempt['id']}".encode()
        ).hexdigest()
        transport = operations.get(transport_key, {})
        proof = transport.get("result_json") or {}
        _need(
            transport.get("status") == "transport_result"
            and transport.get("scope_id") == old_binding["scope_id"]
            and proof.get("operation_key") == root_key
            and proof.get("attempt_id") == attempt["id"]
            and proof.get("input_hash") == run["prompt_hash"],
            "transport_missing_or_mismatch",
        )
        paid, completed = proof.get("result"), root.get("result_json")
        _need(isinstance(paid, dict) and isinstance(completed, dict), "physical_return_missing")
        fields = (
            "content",
            "tool_calls",
            "raw_message",
            "provider",
            "model_name",
            "finish_reason",
            "input_tokens",
            "output_tokens",
            "fallback_used",
        )
        _need(
            all(paid.get(k) == completed.get(k) for k in fields)
            and paid.get("model_name") == run["model_name"]
            and paid.get("provider") == run["provider"]
            and paid.get("finish_reason") == run.get("finish_reason")
            and paid.get("fallback_used") is False,
            "physical_return_changed",
        )
        paid_meta = paid.get("metadata") or {}
        # Rich production descriptors are physical evidence, not an alternate
        # attempt identity. Preserve/hash every field and require the ORIGINAL
        # transport and completed returns to retain the same full descriptor.
        if set(descriptor) - {"attempt_id", "status"}:
            for result in (paid, completed):
                physical_execution = (result.get("metadata") or {}).get("execution_receipt") or {}
                _need(
                    physical_execution.get("scope_id") == old_binding["scope_id"]
                    and physical_execution.get("operation_key") == root_key
                    and physical_execution.get("attempts") == declared,
                    "actual_descriptor_physical_mismatch",
                )
        origin = paid_meta.get("paid_call_origin") or {}
        _need(
            all(
                origin.get(k) == run.get(k)
                for k in ("project_id", "workflow_run_id", "prompt_template", "prompt_version")
            )
            and origin.get("prompt_hash") == run["prompt_hash"],
            "paid_origin",
        )
        effective = meta.get("effective_input_receipt") or {}
        _need(
            effective.get("schema") == "effective-input.v1"
            and effective.get("hash") == run["prompt_hash"]
            and effective.get("model", run["model_name"]) == run["model_name"]
            and run["prompt_hash"] != new_protocol_manifest["effective_input_hash"],
            "effective_input",
        )
        calls = paid.get("tool_calls")
        _need(
            isinstance(calls, list) and len(calls) == 1 and isinstance(calls[0], dict),
            "unique_submission_required",
        )
        function = calls[0].get("function") or {}
        raw = function.get("arguments")
        _need(
            function.get("name") == "submit_chapter_review"
            and isinstance(raw, str)
            and raw == envelope.get("raw_report")
            and hashlib.sha256(raw.encode()).hexdigest() == envelope.get("raw_report_sha256"),
            "raw_submission",
        )
        # Preserve even malformed old arguments as old UNKNOWN. Physical
        # equality proves what was paid for, never that its grade was valid.
        raw_message = paid.get("raw_message")
        _need(
            raw_message is None
            or (isinstance(raw_message, dict) and raw_message.get("tool_calls") == calls),
            "raw_message_conflict",
        )
        _need(
            envelope.get("original_overall_score") == report.get("overall_score")
            and envelope.get("original_dimension_scores") == report.get("dimension_scores"),
            "old_scores_changed",
        )
        seen_runs.add(uid)
        seen_roots.add(root_key)
        seen_actuals.add(attempt["id"])
        seen_transports.add(transport_key)
    _need(
        len(versions) == 1
        and set(runs) == seen_runs
        and set(actuals) == seen_actuals
        and set(operations) == seen_roots | seen_transports,
        "closed_inventory_mismatch",
    )
    row_hashes = {
        "runs": {k: _hash(v) for k, v in sorted(runs.items())},
        "operations": {k: _hash(v) for k, v in sorted(operations.items())},
        "attempts": {k: _hash(v) for k, v in sorted(actuals.items())},
        "slot_reports": {f"{s}:round:{n}": _hash(v) for (s, n), v in sorted(reports.items())},
    }
    target = {k: new_binding[k] for k in ("project_id", "chapter_id", "draft_id", "scope_id")}
    target["body_sha256"] = after["body_sha256"]
    policy_key = _hash(
        {
            "schema": SCHEMA,
            "target": target,
            "new_binding": new_binding,
            "prompt_version": new_protocol_manifest["prompt_version"],
            "effective_input_hash": new_protocol_manifest["effective_input_hash"],
        }
    )
    return {
        "schema": SCHEMA,
        "policy_target_key": policy_key,
        "new_evaluation_contract_sha256": _hash(new_contract),
        "stable_body_key": _hash(
            {
                "project_id": old_binding["project_id"],
                "chapter_id": old_binding["chapter_id"],
                "body_sha256": before["body_sha256"],
            }
        ),
        "identity": _hash(
            {
                "policy_target_key": policy_key,
                "old_pointer": old_pointer,
                "old_journal_sha256": _hash(old_journal),
                "inventory_sha256": _hash(row_hashes),
            }
        ),
        "target": target,
        "old_binding": deepcopy(old_binding),
        "old_pointer": deepcopy(old_pointer),
        "old_journal_sha256": _hash(old_journal),
        "row_hashes": row_hashes,
        "inventory_sha256": _hash(row_hashes),
        "new_binding": deepcopy(new_binding),
        "new_protocol_manifest": deepcopy(new_protocol_manifest),
        "changed_protocol_fields": changed,
        "old_prompt_version": next(iter(versions)),
        "actor": actor,
        "reason": reason,
        "old_slot_attempts": {s: slots[s]["used_attempts"] for s in sorted(slots)},
        "runtime_fences_required": [
            "locked_current_target_and_pointer_CAS",
            "complete_root_query",
            "original_sources_verified_paid_slot_all_rounds",
            "persist_before_provider",
            "unique_policy_target_key",
            "single_consuming_owner",
            "contract_bound_namespace",
            "existing_scope_budget_and_unknown_guards",
            "fixed_legacy_baseline_unchanged",
        ],
        **_FLAGS,
    }


def validate_commercial_protocol_successor_receipt(receipt: dict, **kwargs: Any) -> dict:
    """Rebuild against current locked preimages; mutations/collisions fail closed."""
    _json(receipt)
    expected = build_commercial_protocol_successor_receipt(**kwargs)
    _need(receipt == expected, "receipt_or_snapshot_changed")
    return deepcopy(expected)
