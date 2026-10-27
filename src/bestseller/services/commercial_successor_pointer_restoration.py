"""Restore a reverted audit pointer to its existing, already-consumed cohort.

No provider, grade reconstruction, slot edits, new authorization or commit.
The operator supplies the normal current sources/configuration and commits only
an explicitly reviewed receipt. Complete original-scope inventories are read.
"""

from __future__ import annotations

from copy import deepcopy
import hashlib
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.orm.attributes import set_committed_value

from bestseller.infra.db.models import (
    ChapterModel,
    ExecutionAttemptModel,
    ExecutionOperationModel,
    LlmRunModel,
    WorkflowRunModel,
)
from bestseller.services import commercial_protocol_successor_runtime as runtime
from bestseller.services.commercial_protocol_successor import (
    _hash,
    _need,
    build_commercial_protocol_successor_receipt,
)
from bestseller.services.commercial_source_evidence import CommercialSources

# ruff: noqa: ANN401
SCHEMA = "commercial-successor-pointer-restoration.v1"
HISTORY_KEY = "pointer_restoration_history"


def _root_key(binding: dict, prompt_hash: str, version: str, slot: str, number: int) -> str:
    from bestseller.services.budget import execution_operation_id

    operation = execution_operation_id(
        f"commercial-component:{binding['key']}:policy:{_hash(binding['evaluation_contract'])}:round:{number}",
        subject=f"critic:chapter_commercial_quality_judge:{version}",
        revision=prompt_hash,
    )
    return hashlib.sha256(f"{binding['scope_id']}\n{operation}:sample:{slot}".encode()).hexdigest()


async def _proof(
    session: Any,
    *,
    chapter: Any,
    authorization_id: UUID,
    sources: CommercialSources,
    effective_critic: dict,
    actor: str,
    reason: str,
) -> tuple[dict, Any, Any]:
    _need(
        isinstance(actor, str) and actor.strip() and isinstance(reason, str) and reason.strip(),
        "pointer_actor_reason",
    )
    authorization = await session.scalar(
        select(WorkflowRunModel)
        .where(WorkflowRunModel.id == authorization_id)
        .execution_options(populate_existing=True)
    )
    _need(authorization is not None, "pointer_authorization_missing")
    saved = deepcopy(authorization.metadata_json or {})
    receipt = saved.get("receipt") or {}
    binding = receipt.get("new_binding") or {}
    chapter, auth = await runtime._locked_target(
        session, chapter=chapter, binding=binding, effective_critic=effective_critic
    )
    # Match normal prepare's chapter -> authorization lock order. A changed
    # authorization between discovery and locking is never silently adopted.
    authorization = await session.scalar(
        select(WorkflowRunModel)
        .where(WorkflowRunModel.id == authorization_id)
        .execution_options(populate_existing=True)
        .with_for_update()
    )
    _need(authorization.metadata_json == saved, "pointer_authorization_changed")
    _need(
        authorization.workflow_type == runtime.WORKFLOW_TYPE
        and authorization.status == "completed"
        and authorization.project_id == chapter.project_id
        and authorization.scope_type == "chapter"
        and authorization.scope_id == chapter.id
        and authorization.id == runtime._authorization_id(receipt["policy_target_key"])
        and authorization.idempotency_key == receipt["policy_target_key"]
        and saved.get("schema") == receipt.get("schema")
        and saved.get("authorization_ref") == auth["authorization_ref"]
        and saved.get("execution_scope_id") == binding["scope_id"]
        and saved.get("consumed_owner_id"),
        "pointer_authorization_binding",
    )
    _need(
        all(sources.receipt.get(k) == v for k, v in binding["source_binding"].items()),
        "pointer_current_source_changed",
    )
    key = receipt["stable_body_key"]
    old_pointer = receipt["old_pointer"]
    new_pointer = {"workflow_run_id": saved["consumed_owner_id"], "binding_sha256": _hash(binding)}
    actual = ((chapter.metadata_json or {}).get(runtime.RUNTIME_KEY) or {}).get(key)
    _need(actual == old_pointer, "pointer_old_pointer_CAS")
    owners = {}
    for name, identity in (
        ("old", old_pointer["workflow_run_id"]),
        ("new", saved["consumed_owner_id"]),
    ):
        row = await session.scalar(
            select(WorkflowRunModel)
            .where(WorkflowRunModel.id == UUID(identity))
            .execution_options(populate_existing=True)
            .with_for_update()
        )
        _need(
            row is not None
            and row.project_id == chapter.project_id
            and row.scope_type == "chapter"
            and row.scope_id == chapter.id,
            "pointer_owner_scope",
        )
        owners[name] = row
    old_journal = ((owners["old"].metadata_json or {}).get(runtime.RUNTIME_KEY) or {}).get(key)
    _need(_hash(old_journal) == receipt["old_journal_sha256"], "pointer_original_journal_changed")
    old_inventory, old_files, _ = await runtime._financial_snapshot(
        session,
        chapter=chapter,
        old_journal=old_journal,
        sources=sources,
        effective_critic=effective_critic,
    )
    rebuilt = build_commercial_protocol_successor_receipt(
        old_binding=receipt["old_binding"],
        old_pointer=old_pointer,
        old_journal=old_journal,
        financial_snapshots=old_inventory,
        new_binding=binding,
        new_protocol_manifest=receipt["new_protocol_manifest"],
        actor=receipt["actor"],
        reason=receipt["reason"],
    )
    _need(
        rebuilt == receipt and old_files == saved.get("physical_file_proofs"),
        "pointer_original_authorization_proof_changed",
    )
    owner = owners["new"]
    journal = deepcopy(((owner.metadata_json or {}).get(runtime.RUNTIME_KEY) or {}).get(key) or {})
    _need(
        owner.id != owners["old"].id
        and (owner.metadata_json or {}).get("execution_scope_id") == binding["scope_id"]
        and journal.get("schema") == "commercial-component-cohort.v1"
        and journal.get("binding") == binding
        and journal.get("protocol_successor_workflow_id") == str(authorization.id)
        and journal.get("protocol_successor_identity") == receipt["identity"]
        and journal.get("historical") is False,
        "pointer_consumed_journal_binding",
    )
    expected_slots = {f"chapter-quality.independent-v2:{i}" for i in range(binding["samples"])}
    slots = journal.get("slots")
    _need(
        isinstance(slots, dict) and slots and set(slots) <= expected_slots, "pointer_slot_inventory"
    )
    completed = {}
    unsent = []
    from bestseller.services.llm import LLMCompletionRequest, _effective_input_hash
    from bestseller.settings import LLMRoleSettings

    role = LLMRoleSettings.model_validate(effective_critic)
    initial_hash = receipt["new_protocol_manifest"]["effective_input_hash"]
    version = receipt["new_protocol_manifest"]["prompt_version"]
    for slot in sorted(expected_slots):
        entry = slots.get(slot)
        if entry is None:
            unsent.append((slot, 1, initial_hash))
            continue
        number = entry.get("used_attempts")
        _need(
            type(number) is int
            and number in (1, 2)
            and entry.get("state") in {"prepared", "completed"},
            "pointer_slot_state",
        )
        request = entry.get("request")
        if request is not None:
            request = LLMCompletionRequest.model_validate(request)
            source = entry.get("source_receipt") or {}
            _need(
                str(request.project_id) == binding["project_id"]
                and str(request.workflow_run_id) == str(owner.id)
                and request.logical_role == "critic"
                and request.prompt_template == "chapter_commercial_quality_judge"
                and request.prompt_version == version
                and request.sample_slot == slot
                and all(source.get(k) == v for k, v in binding["source_binding"].items()),
                "pointer_frozen_request_binding",
            )
        if entry["state"] == "completed":
            _need(request is not None, "pointer_completed_request_missing")
            completed[slot] = entry
        else:
            _need("report" not in entry, "pointer_prepared_report_conflict")
            if number == 2:
                _need(
                    isinstance(entry.get("previous_report"), dict), "pointer_previous_round_missing"
                )
                completed[slot] = {
                    "state": "completed",
                    "used_attempts": 1,
                    "report": entry["previous_report"],
                }
            _need(number == 1 or request is not None, "pointer_prepared_request_missing")
            unsent.append(
                (slot, number, _effective_input_hash(request, role) if request else initial_hash)
            )
    _need(completed, "pointer_no_retained_paid_evidence")
    current_inventory, files, compatibility = await runtime._financial_snapshot(
        session,
        chapter=chapter,
        old_journal={**journal, "slots": completed},
        sources=sources,
        effective_critic=effective_critic,
    )
    # Explicit ALL-actual check, including charged/unknown or extra attempts.
    known_roots, known_runs = set(), set()
    for run in current_inventory["runs"]:
        _need(run["workflow_run_id"] == str(owner.id), "pointer_paid_owner")
        execution = run["metadata_json"]["execution_receipt"]
        root = execution["operation_key"]
        actuals = [a for a in current_inventory["attempts"] if a["operation_key"] == root]
        _need(len(actuals) == 1 and len(execution["attempts"]) == 1, "pointer_all_actuals_required")
        actual = actuals[0]
        declared = execution["attempts"][0]
        _need(
            actual["status"] == "completed"
            and type(actual["actual_tokens"]) is int
            and actual["actual_tokens"] >= 0
            and actual["scope_id"] == binding["scope_id"]
            and actual["input_hash"] == run["prompt_hash"]
            and actual["model_name"] == run["model_name"]
            and declared.get("attempt_id") == actual["id"]
            and declared.get("status") == "completed",
            "pointer_actual_unsettled_or_mismatch",
        )
        known_roots.add(root)
        known_runs.add(run["id"])
    all_runs = list(
        await session.scalars(
            select(LlmRunModel).where(
                LlmRunModel.workflow_run_id == owner.id,
                LlmRunModel.prompt_template == "chapter_commercial_quality_judge",
            )
        )
    )
    _need({str(r.id) for r in all_runs} == known_runs, "pointer_unjournaled_paid_run")
    operations = list(
        await session.scalars(
            select(ExecutionOperationModel).where(
                ExecutionOperationModel.scope_id == binding["scope_id"]
            )
        )
    )
    attempts = list(
        await session.scalars(
            select(ExecutionAttemptModel).where(
                ExecutionAttemptModel.scope_id == binding["scope_id"]
            )
        )
    )
    unsent_proofs = []
    for slot, number, input_hash in unsent:
        root = _root_key({**binding, "key": key}, input_hash, version, slot, number)
        matched = [o for o in operations if o.key == root]
        actuals = [a for a in attempts if a.operation_key == root]
        _need(
            not actuals
            and (
                not matched
                or (
                    len(matched) == 1
                    and matched[0].status == "stopped"
                    and matched[0].result_json is None
                )
            ),
            "pointer_missing_slot_has_send",
        )
        unsent_proofs.append(
            {
                "sample_slot": slot,
                "attempt": number,
                "input_hash": input_hash,
                "expected_root": root,
                "actual_attempts": 0,
                "root": runtime._row(matched[0]) if matched else None,
            }
        )
    allowed_unsent = {p["expected_root"] for p in unsent_proofs}
    input_hashes = {r["prompt_hash"] for r in current_inventory["runs"]} | {
        p["input_hash"] for p in unsent_proofs
    }
    for operation in operations:
        paid = operation.result_json or {}
        if operation.status == "transport_result":
            paid = paid.get("result") or {}
        origin = (paid.get("metadata") or {}).get("paid_call_origin") or {}
        owned = (
            origin.get("workflow_run_id") == str(owner.id)
            and origin.get("prompt_template") == "chapter_commercial_quality_judge"
        )
        is_root = operation.key in {a.operation_key for a in attempts} or operation.status in {
            "completed",
            "stopped",
            "pending",
            "unknown",
        }
        if owned or (is_root and operation.input_hash in input_hashes):
            linked = (
                (operation.result_json or {}).get("operation_key")
                if operation.status == "transport_result"
                else operation.key
            )
            _need(linked in known_roots | allowed_unsent, "pointer_unjournaled_paid_root")
    proof = {
        "schema": SCHEMA,
        "authorization_id": str(authorization.id),
        "authorization_receipt_sha256": _hash(receipt),
        "target": deepcopy(receipt["target"]),
        "key": key,
        "old_pointer": deepcopy(old_pointer),
        "new_pointer": new_pointer,
        "old_journal_sha256": _hash(old_journal),
        "consumed_journal_sha256": _hash(journal),
        "retained_inventory_sha256": _hash(
            {name: sorted(rows, key=_hash) for name, rows in current_inventory.items()}
        ),
        "physical_file_proofs": files,
        "current_source_compatibility_proofs": compatibility,
        "unsent_slots": unsent_proofs,
        "scope_operations_sha256": _hash(
            sorted([runtime._row(o) for o in operations], key=lambda r: r["key"])
        ),
        "scope_attempts_sha256": _hash(
            sorted([runtime._row(a) for a in attempts], key=lambda r: r["id"])
        ),
        "source_policy_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "actor": actor,
        "reason": reason,
        "grade_created": False,
        "cohort_created": False,
        "authorization_created": False,
        "slot_reset": False,
        "credit_released": False,
    }
    proof["identity"] = _hash({k: v for k, v in proof.items() if k not in {"actor", "reason"}})
    return proof, chapter, authorization


async def prepare_successor_pointer_restoration(session: Any, **kwargs: Any) -> dict:
    """Read-only preflight; locks only, caller rolls back or reviews proof."""
    proof, _, _ = await _proof(session, **kwargs)
    return proof


async def restore_successor_pointer(session: Any, *, expected_receipt: dict, **kwargs: Any) -> dict:
    """Apply reviewed CAS + append-only audit. Never commit or dispatch."""
    # Repeated apply is a read of the original audit, never another restoration.
    authorization = await session.get(
        WorkflowRunModel, kwargs["authorization_id"], populate_existing=True
    )
    history = (authorization.metadata_json or {}).get(HISTORY_KEY, []) if authorization else []
    existing = [r for r in history if r.get("identity") == expected_receipt.get("identity")]
    if existing:
        _need(
            len(existing) == 1 and existing[0] == expected_receipt,
            "pointer_restoration_audit_collision",
        )
        chapter, _ = await runtime._locked_target(
            session,
            chapter=kwargs["chapter"],
            binding=authorization.metadata_json["receipt"]["new_binding"],
            effective_critic=kwargs["effective_critic"],
        )
        _need(
            ((chapter.metadata_json or {}).get(runtime.RUNTIME_KEY) or {}).get(
                expected_receipt["key"]
            )
            == expected_receipt["new_pointer"],
            "pointer_repeat_CAS",
        )
        return deepcopy(existing[0])
    proof, chapter, authorization = await _proof(session, **kwargs)
    _need(proof == expected_receipt, "pointer_restoration_preimage_changed")
    saved = deepcopy(authorization.metadata_json)
    history = deepcopy(saved.get(HISTORY_KEY) or [])
    history.append(proof)
    updated = {**saved, HISTORY_KEY: history}
    changed = await session.execute(
        update(WorkflowRunModel)
        .where(WorkflowRunModel.id == authorization.id, WorkflowRunModel.metadata_json == saved)
        .values(metadata_json=updated)
        .execution_options(synchronize_session=False)
    )
    _need(changed.rowcount == 1, "pointer_restoration_concurrent_change")
    set_committed_value(authorization, "metadata_json", updated)
    chapter_before = deepcopy(chapter.metadata_json)
    pointers = deepcopy(chapter_before[runtime.RUNTIME_KEY])
    pointers[proof["key"]] = deepcopy(proof["new_pointer"])
    chapter_after = {**chapter_before, runtime.RUNTIME_KEY: pointers}
    changed = await session.execute(
        update(ChapterModel)
        .where(ChapterModel.id == chapter.id, ChapterModel.metadata_json == chapter_before)
        .values(metadata_json=chapter_after)
        .execution_options(synchronize_session=False)
    )
    _need(changed.rowcount == 1, "pointer_restoration_chapter_CAS")
    set_committed_value(chapter, "metadata_json", chapter_after)
    await session.flush()
    return proof
