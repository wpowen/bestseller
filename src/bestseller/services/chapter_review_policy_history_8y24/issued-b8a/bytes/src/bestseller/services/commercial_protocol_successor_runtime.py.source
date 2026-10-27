"""Explicit, once-only successor authorization; never dispatch or auto-grant.

Only the operator may persist authorization. Normal prepare consumes an EXISTING
row and returns the new owner/journals; the caller commits before dispatch and
uses a contract-bound namespace. Old workflows, reports and ledger stay intact.
"""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import inspect, select, update
from sqlalchemy.orm.attributes import set_committed_value

from bestseller.infra.db.models import (
    ChapterDraftVersionModel,
    ChapterModel,
    ExecutionAttemptModel,
    ExecutionBudgetModel,
    ExecutionOperationModel,
    LlmRunModel,
    ReviewReportModel,
    WorkflowRunModel,
)
from bestseller.services.budget import (
    completion_authorization,
    current_execution_scope,
    execution_scope_block_code,
)
from bestseller.services.commercial_protocol_successor import (
    SCHEMA,
    _hash,
    _need,
    _object,
    build_commercial_protocol_successor_receipt,
)
from bestseller.services.commercial_source_evidence import PROMPT_VERSION, CommercialSources

# ruff: noqa: ANN401
WORKFLOW_TYPE = "commercial_protocol_successor"
RUNTIME_KEY = "commercial_component_cohorts"
_SOURCE_FIELDS = (
    "body_sha256",
    "semantic_source_sha256",
    "previous_sha256",
    "source_sha256",
    "assembled_source_sha256",
)


def successor_source_manifest() -> dict[str, str]:
    from bestseller.services.commercial_protocol_transition_runtime import protocol_manifest

    manifest = protocol_manifest()
    for name in ("commercial_protocol_successor.py", "commercial_protocol_successor_runtime.py"):
        manifest[name] = hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
    return manifest


def _row(model: Any) -> dict:
    # The serialized rows are full preimages, including immutable physical refs.
    return json.loads(
        json.dumps(
            {field.key: getattr(model, field.key) for field in inspect(type(model)).column_attrs},
            default=str,
        )
    )


def _authorization_id(policy_target_key: str) -> UUID:
    return uuid5(NAMESPACE_URL, f"bestseller:{SCHEMA}:{policy_target_key}")


async def _locked_target(
    session: Any, *, chapter: Any, binding: dict, effective_critic: dict
) -> tuple[Any, dict]:
    locked = await session.scalar(
        select(ChapterModel)
        .where(
            ChapterModel.id == chapter.id,
            ChapterModel.project_id == chapter.project_id,
        )
        .execution_options(populate_existing=True)
        .with_for_update()
    )
    _need(
        locked is not None
        and str(locked.project_id) == binding.get("project_id")
        and str(locked.id) == binding.get("chapter_id"),
        "runtime_target",
    )
    scope = current_execution_scope()
    _need(scope and scope == binding.get("scope_id"), "runtime_scope")
    draft = await session.get(ChapterDraftVersionModel, UUID(binding["draft_id"]))
    _need(
        draft is not None
        and draft.is_current
        and draft.project_id == locked.project_id
        and draft.chapter_id == locked.id
        and draft.promotion_state != "promoted"
        and hashlib.sha256(draft.content_md.encode()).hexdigest()
        == binding["source_binding"]["body_sha256"],
        "runtime_current_draft",
    )
    from bestseller.services.chapter_scene_contract_materializer import (
        chapter_contract_has_adopted_source,
    )

    _need(
        not await chapter_contract_has_adopted_source(session, chapter=locked),
        "runtime_adopted_target",
    )
    budget = await session.get(ExecutionBudgetModel, scope)
    _need(budget is not None and not execution_scope_block_code(budget), "runtime_scope_blocked")
    auth = completion_authorization(budget)
    _need(auth and auth.get("status") == "active", "runtime_completion_authorization")
    _need(
        effective_critic.get("model") in auth["models"]
        and urlparse(effective_critic.get("api_base") or "").hostname in auth["endpoint_hosts"]
        and _hash(effective_critic) == binding.get("effective_critic_sha256"),
        "runtime_route",
    )
    unresolved = await session.scalar(
        select(ExecutionAttemptModel.id)
        .where(
            ExecutionAttemptModel.scope_id == scope,
            ExecutionAttemptModel.status.in_(["reserved", "pending", "unknown"]),
        )
        .limit(1)
    )
    _need(unresolved is None, "runtime_scope_unsettled_actual")
    unresolved_root = await session.scalar(
        select(ExecutionOperationModel.key)
        .where(
            ExecutionOperationModel.scope_id == scope,
            ExecutionOperationModel.status.in_(["pending", "unknown"]),
        )
        .limit(1)
    )
    _need(unresolved_root is None, "runtime_scope_unsettled_root")
    # Existing quarantine closes the GLOBAL fence while retaining remote
    # uncertainty/charges. It does not authorize replay or change old status.
    # Canonical budget.reconcile writes reconciled_charged + stopped/completed;
    # an attempt still marked unknown/reserved above is not silently relabelled.
    controls = list(
        await session.scalars(
            select(ExecutionOperationModel).where(
                ExecutionOperationModel.scope_id == scope,
                ExecutionOperationModel.status == "reconciliation_receipt",
            )
        )
    )
    quarantines = [
        row
        for row in controls
        if ((row.result_json or {}).get("receipt") or {}).get("decision") == "quarantine_charged"
    ]
    if quarantines:
        from bestseller.services.commercial_ambiguity_retirement import _quarantines

        aids = {(row.result_json or {})["receipt"]["attempt_id"] for row in quarantines}
        attempts = list(
            await session.scalars(
                select(ExecutionAttemptModel).where(
                    ExecutionAttemptModel.scope_id == scope,
                    ExecutionAttemptModel.id.in_(aids),
                )
            )
        )
        all_operations = list(
            await session.scalars(
                select(ExecutionOperationModel).where(
                    ExecutionOperationModel.scope_id == scope,
                )
            )
        )
        _quarantines(
            {row.key: _row(row) for row in quarantines},
            {row.key: _row(row) for row in all_operations},
            {row.id: _row(row) for row in attempts},
            scope,
        )
    return locked, auth


async def _locked_old(
    session: Any,
    *,
    chapter: Any,
    old_owner: Any,
    key: str,
    pointer: dict,
    old_journal: dict,
    successor_pointer: dict | None = None,
) -> Any:
    actual = ((chapter.metadata_json or {}).get(RUNTIME_KEY) or {}).get(key)
    _need(
        actual == (successor_pointer or pointer)
        and pointer.get("workflow_run_id") == str(old_owner.id),
        "runtime_pointer_CAS",
    )
    owner = await session.scalar(
        select(WorkflowRunModel)
        .where(
            WorkflowRunModel.id == old_owner.id,
        )
        .execution_options(populate_existing=True)
        .with_for_update()
    )
    _need(
        owner is not None
        and owner.project_id == chapter.project_id
        and owner.scope_type == "chapter"
        and owner.scope_id == chapter.id
        and ((owner.metadata_json or {}).get(RUNTIME_KEY) or {}).get(key) == old_journal,
        "runtime_old_journal_CAS",
    )
    return owner


def _report_candidates(value: Any) -> list[dict]:
    """Walk persisted report envelopes; no raw response repair or rescore."""
    out = []
    if isinstance(value, dict):
        if isinstance(value.get("raw_excerpt"), str):
            try:
                envelope = _object(value["raw_excerpt"])
            except ValueError:
                envelope = {}
            if envelope.get("schema_version") == "commercial-slot-evidence.v1":
                out.append(value)
            elif envelope.get("schema_version") == "chapter-quality-aggregation.v1":
                out.extend(_report_candidates(envelope))
        for key, child in value.items():
            if key != "raw_excerpt":
                out.extend(_report_candidates(child))
    elif isinstance(value, list):
        for child in value:
            out.extend(_report_candidates(child))
    return out


def _commercial_payload_from_request(prompt: str, expected_hash: str) -> tuple[str, dict]:
    """Recover only a unique whole serialized payload matching its paid receipt."""
    decoder = json.JSONDecoder()
    position, matches = 0, []
    while True:
        start = prompt.find("{", position)
        if start < 0:
            break
        position = start + 1
        try:
            value, end = decoder.raw_decode(prompt, start)
        except (ValueError, RecursionError):
            continue
        if (
            not isinstance(value, dict)
            or not {
                "schema_version",
                "source_context",
                "current_body",
                "previous_chapters",
                "citation_catalog",
            }
            <= value.keys()
        ):
            continue
        raw = prompt[start:end]
        if hashlib.sha256(raw.encode()).hexdigest() == expected_hash:
            # Reparse the whole candidate with strict duplicate/finite checks.
            matches.append((raw, _object(raw)))
    _need(len(matches) == 1, "diagnostic_unique_paid_source_payload_required")
    return matches[0]


def _source_payload_differences(old: Any, current: Any) -> list[dict]:
    differences = []
    missing = object()

    def visit(left: Any, right: Any, pointer: str) -> None:
        if left is not missing and right is not missing and _hash(left) == _hash(right):
            return
        if isinstance(left, dict) and isinstance(right, dict):
            for key in sorted(left.keys() | right.keys()):
                visit(
                    left.get(key, missing),
                    right.get(key, missing),
                    pointer + "/" + key.replace("~", "~0").replace("/", "~1"),
                )
        elif isinstance(left, list) and isinstance(right, list):
            for index in range(max(len(left), len(right))):
                visit(
                    left[index] if index < len(left) else missing,
                    right[index] if index < len(right) else missing,
                    pointer + f"/{index}",
                )
        else:
            differences.append(
                {
                    "pointer": pointer,
                    "old_present": left is not missing,
                    "current_present": right is not missing,
                    "old_value": None if left is missing else deepcopy(left),
                    "current_value": None if right is missing else deepcopy(right),
                }
            )

    visit(old, current, "")
    return differences


def _source_comparison_diagnostic(
    old_payload: dict, current_payload: dict, old_receipt: dict, current_receipt: dict
) -> dict:
    """Classify one known audit leaf without authorizing any reuse or admission."""
    pointer = "/source_context/source_selection_receipt/original_source_sha256"
    differences = _source_payload_differences(old_payload, current_payload)
    normalized_old, normalized_current = deepcopy(old_payload), deepcopy(current_payload)
    selected_old = normalized_old.get("source_context", {}).get("source_selection_receipt")
    selected_current = normalized_current.get("source_context", {}).get("source_selection_receipt")
    required = {
        "policy",
        "original_source_sha256",
        "original_bible_sha256",
        "retained_bible_sha256",
        "original_bible_characters",
        "retained_bible_characters",
        "retained_bible_fields",
        "omitted_bible_fields",
        "authority",
    }
    known = all(
        isinstance(row, dict)
        and set(row) == required
        and row.get("policy") == "typed-world-and-relevant-current-state.v1"
        and row.get("authority")
        == "unverified_context; adopted facts and explicit clauses remain separate"
        and isinstance(row.get("original_source_sha256"), str)
        and len(row["original_source_sha256"]) == 64
        and all(char in "0123456789abcdef" for char in row["original_source_sha256"])
        for row in (selected_old, selected_current)
    )
    known = known and (
        old_receipt.get("source_selection") == selected_old
        and current_receipt.get("source_selection") == selected_current
    )
    if known:
        selected_old.pop("original_source_sha256")
        selected_current.pop("original_source_sha256")
    normalized_equal = known and _hash(normalized_old) == _hash(normalized_current)
    stable_semantics = all(
        old_receipt.get(k) is not None and old_receipt.get(k) == current_receipt.get(k)
        for k in _SOURCE_FIELDS[:3]
    )
    return {
        "schema": "commercial-successor-source-diagnostic.v1",
        "differences": differences,
        "differences_truncated": False,
        "known_audit_leaf_only": bool(
            known
            and stable_semantics
            and normalized_equal
            and [row["pointer"] for row in differences] == [pointer]
        ),
        "normalized_full_payload_equal": bool(normalized_equal),
        "old_assembled_source_sha256": old_receipt.get("assembled_source_sha256"),
        "current_assembled_source_sha256": current_receipt.get("assembled_source_sha256"),
        "old_canonical_payload_sha256": _hash(old_payload),
        "current_canonical_payload_sha256": _hash(current_payload),
        "normalized_old_payload_sha256": _hash(normalized_old),
        "normalized_current_payload_sha256": _hash(normalized_current),
        "old_source_policy": {
            k: deepcopy(old_receipt.get(k))
            for k in (
                "schema_version",
                "prompt_version",
                "source_protocol_sha256",
                "source_selection",
            )
        },
        "current_source_policy": {
            k: deepcopy(current_receipt.get(k))
            for k in (
                "schema_version",
                "prompt_version",
                "source_protocol_sha256",
                "source_selection",
            )
        },
        "current_protocol_manifest": successor_source_manifest(),
        "compatibility_authorized": False,
        "grade_reused": False,
        "old_files_modified": False,
    }


async def diagnose_successor_source_compatibility(
    session: Any,
    *,
    old_journal: dict,
    sources: CommercialSources,
    effective_critic: dict,
) -> dict:
    """Read-only forensic output; never prepare a slot or relax the admission guard.

    Covers every report retained directly in the journal. If an imported journal
    lacks an earlier round, that missing coverage is explicit and not a proof.
    Caller may serialize full source payloads locally; no source values enter an
    exception, SQL statement, provider request, new grade or old receipt.
    """
    from bestseller.services.llm import _effective_input_hash
    from bestseller.services.retained_review_input_proof import _physical_return, _retained_request
    from bestseller.settings import LLMRoleSettings, get_settings

    binding = old_journal["binding"]
    current_payload = _object(sources.text)
    _need(
        hashlib.sha256(sources.text.encode()).hexdigest()
        == sources.receipt.get("assembled_source_sha256"),
        "diagnostic_current_payload_hash",
    )
    _need(
        _hash(effective_critic) == binding["effective_critic_sha256"], "diagnostic_critic_changed"
    )
    rows, missing = [], []
    for slot, entry in sorted(old_journal["slots"].items()):
        used = entry.get("used_attempts")
        _need(type(used) is int and used in (1, 2), "diagnostic_slot_rounds")
        reports = [(used, entry["report"])]
        if used == 2:
            if "previous_report" in entry:
                reports.insert(0, (1, entry["previous_report"]))
            else:
                missing.append({"sample_slot": slot, "attempt": 1})
        for number, report in reports:
            run = await session.get(LlmRunModel, UUID(report["llm_run_id"]))
            _need(
                run is not None and str(run.project_id) == binding["project_id"],
                "diagnostic_original_run_missing",
            )
            old_receipt = (run.metadata_json or {}).get("commercial_source_input_receipt") or {}
            envelope = _object(report.get("raw_excerpt"))
            _need(
                envelope.get("sample_slot") == slot
                and envelope.get("attempt") == number
                and envelope.get("source_binding") == binding["source_binding"]
                and envelope.get("evaluation_contract") == binding["evaluation_contract"]
                and all(old_receipt.get(k) == v for k, v in binding["source_binding"].items()),
                "diagnostic_original_report_binding",
            )
            request = _retained_request(get_settings(), run)
            computed = _effective_input_hash(
                request, LLMRoleSettings.model_validate(effective_critic)
            )
            _need(computed == run.prompt_hash, "diagnostic_original_request_hash")
            paid, operation_key = await _physical_return(
                session,
                run,
                scope=binding["scope_id"],
                expected_hash=computed,
                expected_model=effective_critic["model"],
            )
            from bestseller.services.commercial_review_tool import submitted_review_payload

            raw, _, errors = submitted_review_payload(paid)
            _need(
                not errors
                and raw == envelope.get("raw_report")
                and hashlib.sha256(raw.encode()).hexdigest() == envelope.get("raw_report_sha256"),
                "diagnostic_original_report_physical_mismatch",
            )
            old_text, old_payload = _commercial_payload_from_request(
                request.user_prompt, old_receipt.get("assembled_source_sha256")
            )
            comparison = _source_comparison_diagnostic(
                old_payload, current_payload, old_receipt, dict(sources.receipt)
            )
            path = Path(run.request_payload_ref)
            if not path.is_absolute():
                path = Path(get_settings().artifact_store.local_dir).expanduser() / path
            rows.append(
                {
                    "sample_slot": slot,
                    "attempt": number,
                    "llm_run_id": str(run.id),
                    "operation_key": operation_key,
                    "original_request_ref": run.request_payload_ref,
                    "original_request_file_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                    "original_effective_input_hash": computed,
                    "original_request_verified": True,
                    "original_payload_sha256": hashlib.sha256(old_text.encode()).hexdigest(),
                    "receipt_field_comparison": [
                        {
                            "field": key,
                            "old": old_receipt.get(key),
                            "current": sources.receipt.get(key),
                            "equal": old_receipt.get(key) == sources.receipt.get(key),
                        }
                        for key in _SOURCE_FIELDS
                    ],
                    "old_source_payload": old_payload,
                    "current_source_payload": deepcopy(current_payload),
                    "comparison_receipt": comparison,
                }
            )
    return {
        "schema": "commercial-successor-source-diagnostic.v1",
        "status": "diagnostic_only",
        "slot_rounds": rows,
        "missing_rounds": missing,
        "coverage_complete": not missing,
        "provider_calls_performed": 0,
        "database_writes": 0,
        "compatibility_authorized": False,
    }


async def _financial_snapshot(
    session: Any,
    *,
    chapter: Any,
    old_journal: dict,
    sources: CommercialSources,
    effective_critic: dict,
) -> tuple[dict, dict, list[dict]]:
    from bestseller.services.commercial_judge_recovery import _verified_paid_slot
    from bestseller.settings import get_settings

    binding = old_journal["binding"]
    reports: dict[tuple[str, int], dict] = {}
    needed = set()
    for slot, entry in old_journal["slots"].items():
        _need(
            entry.get("state") == "completed"
            and type(entry.get("used_attempts")) is int
            and entry["used_attempts"] in (1, 2),
            "runtime_old_slot_unsettled",
        )
        number = entry["used_attempts"]
        needed.update((slot, i) for i in range(1, number + 1))
        reports[(slot, number)] = entry["report"]
        if number == 2 and "previous_report" in entry:
            reports[(slot, 1)] = entry["previous_report"]
    if needed - reports.keys():
        candidates = list(
            await session.scalars(
                select(ReviewReportModel).where(
                    ReviewReportModel.project_id == chapter.project_id,
                    ReviewReportModel.target_type == "chapter",
                    ReviewReportModel.target_id == chapter.id,
                )
            )
        )
        matches: dict[tuple[str, int], dict[str, dict]] = {}
        for row in candidates:
            for report in _report_candidates(row.structured_output):
                envelope = _object(report["raw_excerpt"])
                pair = (envelope.get("sample_slot"), envelope.get("attempt"))
                if (
                    pair in needed - reports.keys()
                    and envelope.get("source_binding") == binding["source_binding"]
                    and envelope.get("evaluation_contract") == binding["evaluation_contract"]
                ):
                    matches.setdefault(pair, {})[_hash(report)] = report
        for pair in needed - reports.keys():
            _need(len(matches.get(pair, {})) == 1, "runtime_old_round_missing_or_ambiguous")
            reports[pair] = next(iter(matches[pair].values()))
    runs, roots, file_proofs = {}, set(), {}
    compatibility = []
    current_payload = _object(sources.text)
    _need(
        hashlib.sha256(sources.text.encode()).hexdigest()
        == sources.receipt.get("assembled_source_sha256"),
        "runtime_current_payload_hash",
    )
    for (slot, _number), report in sorted(reports.items()):
        run = await session.get(LlmRunModel, UUID(report["llm_run_id"]))
        _need(run is not None, "runtime_old_run_missing")
        receipt = (run.metadata_json or {}).get("commercial_source_input_receipt")
        _need(isinstance(receipt, dict), "runtime_original_source_receipt_missing")
        for field in _SOURCE_FIELDS[:3]:
            _need(
                receipt.get(field) == sources.receipt.get(field) and receipt.get(field) is not None,
                "runtime_original_actual_source_changed",
            )
        from bestseller.services.llm import _effective_input_hash
        from bestseller.services.retained_review_input_proof import _retained_request
        from bestseller.settings import LLMRoleSettings

        physical_request = _retained_request(get_settings(), run)
        _need(
            _effective_input_hash(
                physical_request, LLMRoleSettings.model_validate(effective_critic)
            )
            == run.prompt_hash,
            "runtime_physical_request_hash",
        )
        old_text, old_payload = _commercial_payload_from_request(
            physical_request.user_prompt, receipt.get("assembled_source_sha256")
        )
        comparison = None
        if any(receipt.get(k) != sources.receipt.get(k) for k in _SOURCE_FIELDS[3:]):
            comparison = _source_comparison_diagnostic(
                old_payload, current_payload, receipt, dict(sources.receipt)
            )
            _need(comparison["known_audit_leaf_only"], "runtime_original_actual_source_changed")
            # A wire pointer must not turn the ignored audit leaf into cited
            # evidence. Resolve every catalog entry against BOTH complete inputs.
            from bestseller.services.commercial_source_evidence import (
                resolve_commercial_source_pointer,
            )

            rendered = current_payload["citation_catalog"]
            _need(
                isinstance(rendered, list)
                and len(rendered) == len(sources.catalog)
                and {r["id"] for r in rendered} == set(sources.catalog),
                "runtime_source_catalog_changed",
            )
            for entry in rendered:
                old_value = resolve_commercial_source_pointer(old_payload, entry["source_ref"])
                current_value = resolve_commercial_source_pointer(
                    current_payload, entry["source_ref"]
                )
                _need(_hash(old_value) == _hash(current_value), "runtime_source_catalog_changed")
                actual = sources.catalog[entry["id"]]
                _need(actual["kind"] == entry["kind"], "runtime_source_catalog_changed")
                if entry["kind"] in {"current", "prior"}:
                    _need(
                        actual["start"] == entry["start"]
                        and actual["end"] == entry["end"]
                        and current_value[entry["start"] : entry["end"]] == actual["text"],
                        "runtime_source_catalog_changed",
                    )
        else:
            _need(old_text == sources.text, "runtime_original_actual_source_changed")
        original = CommercialSources(
            text=old_text, catalog=sources.catalog, receipt=deepcopy(receipt)
        )
        await _verified_paid_slot(
            session,
            report=report,
            sources=original,
            contract=binding["evaluation_contract"],
            project_id=chapter.project_id,
            scope=binding["scope_id"],
            slot=slot,
            expected_model=effective_critic["model"],
        )
        serialized = _row(run)
        _need(str(run.id) not in runs, "runtime_duplicate_paid_run")
        runs[str(run.id)] = serialized
        roots.add((run.metadata_json or {})["execution_receipt"]["operation_key"])
        payloads = {}
        for kind in ("request", "response"):
            ref = getattr(run, f"{kind}_payload_ref")
            _need(isinstance(ref, str) and ref, "runtime_physical_file_ref_missing")
            path = Path(ref)
            if not path.is_absolute():
                path = Path(get_settings().artifact_store.local_dir).expanduser() / path
            _need(path.is_file(), "runtime_physical_file_missing")
            payloads[kind] = _object(path.read_text())
            file_proofs[f"{run.id}:{kind}"] = {
                "ref": ref,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        response = payloads["response"]
        root = await session.get(
            ExecutionOperationModel, (run.metadata_json or {})["execution_receipt"]["operation_key"]
        )
        paid = root.result_json
        _need(
            response.get("schema_version") == "llm-payload.v1"
            and response.get("kind") == "response"
            and response.get("replayable") is True
            and response.get("llm_run_id") == str(run.id)
            and response.get("project_id") == str(run.project_id)
            and response.get("workflow_run_id") == str(run.workflow_run_id)
            and response.get("model") == run.model_name
            and response.get("provider") == run.provider
            and all(
                response.get(k) == paid.get(k)
                for k in (
                    "finish_reason",
                    "fallback_used",
                    "input_tokens",
                    "output_tokens",
                    "tool_calls",
                    "raw_message",
                )
            )
            and isinstance(response.get("content"), dict)
            and response["content"].get("truncated") is False
            and response["content"].get("text") == paid.get("content")
            and response["content"].get("original_chars") == len(paid["content"])
            and response.get("tool_call_count") == len(paid.get("tool_calls") or []),
            "runtime_physical_response_changed",
        )
        if comparison is not None:
            compatibility.append(
                {
                    **comparison,
                    "schema": "commercial-successor-audit-leaf-compatibility.v1",
                    "compatibility_authorized": True,
                    "purpose": "original_physical_source_verification_only",
                    "sample_slot": slot,
                    "attempt": _number,
                    "llm_run_id": str(run.id),
                    "original_effective_input_hash": run.prompt_hash,
                    "original_request_file": deepcopy(file_proofs[f"{run.id}:request"]),
                    "old_source_sha256": receipt["source_sha256"],
                    "current_source_sha256": sources.receipt["source_sha256"],
                    "catalog_sha256": _hash(sources.catalog),
                    # Only the historically recorded contract is asserted here;
                    # no historical source-file manifest is reconstructed.
                    "old_protocol_manifest": {
                        "recorded_source_policy": comparison["old_source_policy"],
                        "evaluation_contract": deepcopy(binding["evaluation_contract"]),
                        "historical_source_file_manifest": None,
                    },
                    "new_grade_created": False,
                    "slot_reset": False,
                    "credit_released": False,
                }
            )
    attempts = list(
        await session.scalars(
            select(ExecutionAttemptModel).where(
                ExecutionAttemptModel.scope_id == binding["scope_id"],
                ExecutionAttemptModel.operation_key.in_(roots),
            )
        )
    )
    all_ops = list(
        await session.scalars(
            select(ExecutionOperationModel).where(
                ExecutionOperationModel.scope_id == binding["scope_id"],
            )
        )
    )
    aids = {row.id for row in attempts}
    operations = []
    for row in all_ops:
        result = row.result_json or {}
        if row.key in roots or (
            isinstance(result, dict)
            and (
                result.get("operation_key") in roots
                or result.get("attempt_id") in aids
                or (
                    isinstance(result.get("receipt"), dict)
                    and result["receipt"].get("attempt_id") in aids
                )
            )
        ):
            operations.append(_row(row))
    return (
        {
            "runs": list(runs.values()),
            "operations": operations,
            "attempts": [_row(a) for a in attempts],
            "slot_reports": [
                {"sample_slot": s, "attempt": n, "report": deepcopy(r)}
                for (s, n), r in sorted(reports.items())
            ],
        },
        file_proofs,
        compatibility,
    )


async def _prepare(
    session: Any,
    *,
    chapter: Any,
    old_owner: Any,
    key: str,
    pointer: dict,
    old_journal: dict,
    binding: dict,
    sources: CommercialSources,
    current_input_hash: str,
    effective_critic: dict,
    actor: str,
    reason: str,
    source_manifest: dict,
    consumed_owner_id: str | None = None,
) -> tuple[dict, dict, dict, Any, list[dict]]:
    chapter, auth = await _locked_target(
        session, chapter=chapter, binding=binding, effective_critic=effective_critic
    )
    successor_pointer = (
        {"workflow_run_id": consumed_owner_id, "binding_sha256": _hash(binding)}
        if consumed_owner_id
        else None
    )
    owner = await _locked_old(
        session,
        chapter=chapter,
        old_owner=old_owner,
        key=key,
        pointer=pointer,
        old_journal=old_journal,
        successor_pointer=successor_pointer,
    )
    _need(source_manifest == successor_source_manifest(), "runtime_source_manifest_changed")
    inventory, files, compatibility = await _financial_snapshot(
        session,
        chapter=chapter,
        old_journal=old_journal,
        sources=sources,
        effective_critic=effective_critic,
    )
    args = {
        "old_binding": old_journal["binding"],
        "old_pointer": pointer,
        "old_journal": old_journal,
        "financial_snapshots": inventory,
        "new_binding": binding,
        "new_protocol_manifest": {
            "prompt_version": PROMPT_VERSION,
            "source_manifest": source_manifest,
            "effective_input_hash": current_input_hash,
        },
        "actor": actor,
        "reason": reason,
    }
    receipt = build_commercial_protocol_successor_receipt(**args)
    _need(receipt["stable_body_key"] == key, "runtime_body_key")
    return receipt, files, auth, owner, compatibility


async def prepare_successor_authorization(session: Any, **kwargs: Any) -> dict:
    """Operator-only preflight; no writes, preparation or commit."""
    receipt, _, _, _, _ = await _prepare(session, **kwargs)
    return receipt


async def persist_successor_authorization(session: Any, **kwargs: Any) -> Any:
    """Explicit operator writes ONE audit WF only; caller commits/readbacks."""
    receipt, files, auth, _, compatibility = await _prepare(session, **kwargs)
    uid = _authorization_id(receipt["policy_target_key"])
    existing = await session.scalar(
        select(WorkflowRunModel)
        .where(
            WorkflowRunModel.id == uid,
        )
        .execution_options(populate_existing=True)
        .with_for_update()
    )
    meta = {
        "schema": SCHEMA,
        "receipt": receipt,
        "physical_file_proofs": files,
        "source_compatibility_proofs": compatibility,
        "execution_scope_id": receipt["target"]["scope_id"],
        "authorization_ref": auth["authorization_ref"],
        "consumed_owner_id": None,
    }
    if existing is not None:
        _need(
            existing.workflow_type == WORKFLOW_TYPE
            and existing.project_id == UUID(receipt["target"]["project_id"])
            and existing.scope_type == "chapter"
            and existing.scope_id == UUID(receipt["target"]["chapter_id"])
            and existing.status == "completed"
            and existing.idempotency_key == receipt["policy_target_key"]
            and existing.metadata_json == meta,
            "runtime_authorization_collision",
        )
        return existing
    row = WorkflowRunModel(
        id=uid,
        project_id=UUID(receipt["target"]["project_id"]),
        workflow_type=WORKFLOW_TYPE,
        scope_type="chapter",
        scope_id=UUID(receipt["target"]["chapter_id"]),
        status="completed",
        current_step="authorized",
        requested_by="authorized-commercial-successor",
        idempotency_key=receipt["policy_target_key"],
        metadata_json=meta,
    )
    session.add(row)
    await session.flush()
    return row


async def consume_authorized_successor(
    session: Any,
    *,
    chapter: Any,
    old_owner: Any,
    key: str,
    pointer: dict,
    old_journal: dict,
    binding: dict,
    workflow_run_id: UUID,
    sources: CommercialSources,
    current_input_hash: str,
    effective_critic: dict,
) -> tuple | None:
    """Consume an existing one-target receipt, never manufacture authorization.

    Returns (newowner, newjournals, newjournal). No commit: normal prepare owns
    the before-provider checkpoint. Re-entry by a third owner is rejected.
    """
    # Same lock order as operator prepare: chapter before authorization row.
    locked = await session.scalar(
        select(ChapterModel)
        .where(
            ChapterModel.id == chapter.id,
            ChapterModel.project_id == chapter.project_id,
        )
        .execution_options(populate_existing=True)
        .with_for_update()
    )
    _need(locked is not None, "runtime_target_missing")
    chapter = locked
    target_rows = list(
        await session.scalars(
            select(WorkflowRunModel)
            .where(
                WorkflowRunModel.workflow_type == WORKFLOW_TYPE,
                WorkflowRunModel.project_id == chapter.project_id,
                WorkflowRunModel.scope_type == "chapter",
                WorkflowRunModel.scope_id == chapter.id,
            )
            .with_for_update()
        )
    )
    candidates = []
    for row in target_rows:
        meta = row.metadata_json or {}
        receipt = meta.get("receipt") or {}
        if (
            receipt.get("new_binding") == binding
            and (receipt.get("new_protocol_manifest") or {}).get("effective_input_hash")
            == current_input_hash
        ):
            candidates.append(row)
    _need(len(candidates) <= 1, "runtime_multiple_authorizations")
    if not candidates:
        return None
    authorization = candidates[0]
    saved = deepcopy(authorization.metadata_json)
    receipt = saved["receipt"]
    _need(
        saved.get("consumed_owner_id") in {None, str(workflow_run_id)}, "runtime_already_consumed"
    )
    rebuilt, files, auth, owner, compatibility = await _prepare(
        session,
        chapter=chapter,
        old_owner=old_owner,
        key=key,
        pointer=pointer,
        old_journal=old_journal,
        binding=binding,
        sources=sources,
        current_input_hash=current_input_hash,
        effective_critic=effective_critic,
        actor=receipt["actor"],
        reason=receipt["reason"],
        source_manifest=receipt["new_protocol_manifest"]["source_manifest"],
        consumed_owner_id=saved.get("consumed_owner_id"),
    )
    _need(
        authorization.id == _authorization_id(receipt["policy_target_key"])
        and authorization.status == "completed"
        and authorization.idempotency_key == receipt["policy_target_key"]
        and saved.get("schema") == SCHEMA
        and rebuilt == receipt
        and saved.get("physical_file_proofs") == files
        and saved.get("source_compatibility_proofs") == compatibility
        and saved.get("authorization_ref") == auth["authorization_ref"],
        "runtime_authorization_changed",
    )
    new_owner = await session.scalar(
        select(WorkflowRunModel)
        .where(
            WorkflowRunModel.id == workflow_run_id,
        )
        .execution_options(populate_existing=True)
        .with_for_update()
    )
    _need(
        new_owner is not None
        and new_owner.id != owner.id
        and new_owner.project_id == chapter.project_id
        and new_owner.scope_type == "chapter"
        and new_owner.scope_id == chapter.id
        and new_owner.status in {"running", "pending", "queued"}
        and (new_owner.metadata_json or {}).get("execution_scope_id") == binding["scope_id"],
        "runtime_new_owner_binding",
    )
    journals = deepcopy((new_owner.metadata_json or {}).get(RUNTIME_KEY) or {})
    expected = {
        "schema": "commercial-component-cohort.v1",
        "binding": deepcopy(binding),
        "historical": False,
        "slots": {},
        "protocol_successor_workflow_id": str(authorization.id),
        "protocol_successor_identity": receipt["identity"],
    }
    if saved.get("consumed_owner_id") is not None:
        existing = journals.get(key) or {}
        _need(
            existing.get("binding") == binding
            and existing.get("protocol_successor_workflow_id") == str(authorization.id)
            and existing.get("protocol_successor_identity") == receipt["identity"],
            "runtime_consumed_journal_changed",
        )
        return new_owner, journals, existing
    _need(key not in journals, "runtime_new_owner_journal_collision")
    journals[key] = expected
    pointers = deepcopy((chapter.metadata_json or {}).get(RUNTIME_KEY) or {})
    _need(pointers.get(key) == pointer, "runtime_pointer_CAS")
    pointers[key] = {"workflow_run_id": str(new_owner.id), "binding_sha256": _hash(binding)}
    # Atomic consumption also protects SQLite, where FOR UPDATE is a no-op.
    consumed = {**saved, "consumed_owner_id": str(new_owner.id)}
    claimed = await session.execute(
        update(WorkflowRunModel)
        .where(WorkflowRunModel.id == authorization.id, WorkflowRunModel.metadata_json == saved)
        .values(metadata_json=consumed, current_step="consumed")
        .execution_options(synchronize_session=False)
    )
    _need(claimed.rowcount == 1, "runtime_concurrent_consumption")
    set_committed_value(authorization, "metadata_json", consumed)
    set_committed_value(authorization, "current_step", "consumed")
    # All validation precedes mutation. The old owner's metadata is never assigned.
    chapter.metadata_json = {**(chapter.metadata_json or {}), RUNTIME_KEY: pointers}
    # The caller stages a slot in the returned journal before reassigning it.
    # Keep the flushed ORM preimage detached so that reassignment is dirty.
    new_owner.metadata_json = {**(new_owner.metadata_json or {}), RUNTIME_KEY: deepcopy(journals)}
    await session.flush()
    return new_owner, journals, expected
