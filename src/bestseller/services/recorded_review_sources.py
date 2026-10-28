"""Read original paid review inputs without rebuilding them from current DB order.

This proves custody and lossless decoding only. It does not establish issuer
code equivalence, current applicability, a grade, or permission to resume/send.
"""

from __future__ import annotations

# Native column snapshots and JSON have heterogeneous shapes checked below.
# ruff: noqa: ANN001,ANN002,ANN202
from copy import deepcopy
from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import UUID

from sqlalchemy import inspect

from bestseller.infra.db.models import (
    ChapterDraftVersionModel,
    ChapterModel,
    ExecutionAttemptModel,
    ExecutionOperationModel,
    LlmRunModel,
    ProjectModel,
    WorkflowRunModel,
)
from bestseller.services import llm
from bestseller.services import retained_planning_generation as physical
from bestseller.services import retained_review_input_proof as retained
from bestseller.services.commercial_source_evidence import (
    CommercialSources,
    assemble_commercial_sources,
)

POLICY = "recorded-review-sources.v1"


class RecordedReviewSourceError(ValueError):
    def __init__(self, code: str, *, differences=None) -> None:
        self.code = code
        self.differences = differences or []
        super().__init__("recorded_review_sources:" + code)


def _need(condition, code: str) -> None:
    if not condition:
        raise RecordedReviewSourceError(code)


def _json(value) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def _hash(value) -> str:
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _strict_paid_result(raw):
    _need(type(raw) is dict, "paid_result_types")
    try:
        parsed = llm.LLMCompletionResult.model_validate(raw)
        normalized = parsed.model_dump(mode="json")
        _need(
            set(raw) <= set(normalized)
            and _json(raw) == _json({key: normalized[key] for key in raw}),
            "paid_result_types",
        )
    except (TypeError, ValueError) as exc:
        raise RecordedReviewSourceError("paid_result_types") from exc
    return parsed


_MISSING = object()


def _identity_summary(value):
    if value is _MISSING:
        return {"type": "missing", "length": None, "sha256": None}
    return {
        "type": type(value).__name__,
        "length": len(value) if isinstance(value, (dict, list, str)) else None,
        "sha256": _hash(value),
    }


def _identity_differences(field, expected, actual):
    if _identity_summary(expected) == _identity_summary(actual):
        return []
    if type(expected) is dict and type(actual) is dict:
        return [
            difference
            for key in sorted(set(expected) | set(actual))
            for difference in _identity_differences(
                f"{field}.{key}", expected.get(key, _MISSING), actual.get(key, _MISSING)
            )
        ]
    return [
        {
            "field": field,
            "expected": _identity_summary(expected),
            "actual": _identity_summary(actual),
        }
    ]


def _strict_paid_pair(completed, transport, *, request, effective_hash, model) -> None:
    left, right = _strict_paid_result(completed), _strict_paid_result(transport)
    fields = (
        "content",
        "provider",
        "model_name",
        "tool_calls",
        "raw_message",
        "finish_reason",
        "input_tokens",
        "output_tokens",
        "fallback_used",
        "fallback_source",
    )
    _need(
        all(_json(getattr(left, key)) == _json(getattr(right, key)) for key in fields),
        "paid_return_types",
    )
    # Native settlement records the compact receipt atomically with transport;
    # complete_text.finish adds three fields derived from the original request.
    # Both domains must match their producer, not each other's dictionary shape.
    origin = llm._paid_call_origin(request, effective_hash)
    core = {"schema": "effective-input.v1", "hash": effective_hash, "model": model}
    expanded = {
        **core,
        "messages_source": "override" if request.messages_override is not None else "system_user",
        "tools_included": bool(request.tools),
        "legacy_prompt_hash": llm._hash_prompt(request.system_prompt, request.user_prompt),
    }
    operation_receipt = left.metadata.get("effective_input_receipt")
    # A crash after settle and before finish legitimately leaves the compact
    # operation too. Partial, invented or extra fields never select that form.
    expected_operation = (
        core
        if type(operation_receipt) is dict and set(operation_receipt) == set(core)
        else expanded
    )
    differences = []
    for key, expected, actual in (
        ("transport.metadata.paid_call_origin", origin, right.metadata.get("paid_call_origin")),
        ("operation.metadata.paid_call_origin", origin, left.metadata.get("paid_call_origin")),
        (
            "transport.metadata.effective_input_receipt",
            core,
            right.metadata.get("effective_input_receipt"),
        ),
        ("operation.metadata.effective_input_receipt", expected_operation, operation_receipt),
    ):
        differences.extend(_identity_differences(key, expected, actual))
    if differences:
        raise RecordedReviewSourceError("paid_identity_metadata", differences=differences)


def _run_identity(run, project, *, kind) -> None:
    templates = {
        "primary": "chapter_review",
        "window": "chapter_window_quality_judge",
        "commercial": "chapter_commercial_quality_judge",
    }
    _need(
        run is not None
        and run.project_id == project.id
        and run.logical_role == "critic"
        and run.prompt_template == templates.get(kind),
        "run_identity",
    )


def _window_run_id(evidence):
    window = evidence.get("llm_window_quality_judge")
    if window is None:
        return None
    _need(
        type(window) is dict
        and isinstance(window.get("llm_run_id"), str)
        and bool(window["llm_run_id"]),
        "window_run_missing",
    )
    try:
        return UUID(window["llm_run_id"])
    except ValueError as exc:
        raise RecordedReviewSourceError("window_run_invalid") from exc


def _commercial_slots(journal, binding):
    n, slots = binding.get("samples"), journal.get("slots") or {}
    _need(
        type(n) is int
        and n > 0
        and set(slots) == {f"chapter-quality.independent-v2:{i}" for i in range(n)},
        "commercial_slots",
    )
    _need(
        (journal.get("aggregate") or {}).get("slots_sha256")
        == _hash({name: entry["report"] for name, entry in slots.items()}),
        "commercial_slots_hash",
    )
    result = []
    for index in range(n):
        name = f"chapter-quality.independent-v2:{index}"
        entry = slots[name]
        _need(entry.get("state") == "completed", "commercial_slot_unavailable")
        envelope = json.loads(entry["report"]["raw_excerpt"])
        _need(
            envelope.get("schema_version") == "commercial-slot-evidence.v1"
            and envelope.get("sample_slot") == name
            and type(entry.get("used_attempts")) is int
            and type(envelope.get("attempt")) is int
            and envelope["attempt"] == entry["used_attempts"] in (1, 2)
            and _json(envelope.get("source_binding")) == _json(binding.get("source_binding"))
            and _json(envelope.get("evaluation_contract"))
            == _json(binding.get("evaluation_contract")),
            "commercial_slot_binding",
        )
        result.append((name, entry))
    return result


def _commercial_call_binding(run, paid, slot_evidence) -> None:
    from bestseller.services.commercial_judge_recovery import _BINDING_KEYS, _EXACT_AUDIT_KEYS
    from bestseller.services.commercial_review_tool import submitted_review_payload

    name, entry, binding = slot_evidence
    metadata = run.metadata_json or {}
    _need(
        metadata.get("commercial_sample_slot") == name
        and (metadata.get("execution_receipt") or {}).get("scope_id") == binding.get("scope_id"),
        "commercial_paid_slot",
    )
    source = metadata.get("commercial_source_input_receipt") or {}
    envelope = json.loads(entry["report"]["raw_excerpt"])
    _need(
        all(
            _json(source.get(k)) == _json((envelope.get("source_binding") or {}).get(k))
            for k in _BINDING_KEYS
        )
        and all(
            _json(source.get(k)) == _json((envelope.get("exact_source_audit") or {}).get(k))
            for k in _EXACT_AUDIT_KEYS
        ),
        "commercial_paid_source",
    )
    raw, submission, _ = submitted_review_payload(paid)
    _need(
        isinstance(raw, str)
        and raw == envelope.get("raw_report")
        and _sha(raw.encode()) == envelope.get("raw_report_sha256")
        and _json(submission) == _json((envelope.get("adapter_receipt") or {}).get("submission")),
        "commercial_paid_raw",
    )


def _commercial_report_binding(evidence, slots) -> None:
    """Bind the report's paid references, not its derived judgment domain.

    Full issued report/raw equality requires the separate issuer verifier. This
    check grants no grade and never substitutes a current aggregate for it.
    """
    commercial = evidence.get("llm_commercial_judge")
    if evidence.get("commercial_action_projection") is not None:
        commercial = evidence.get("llm_commercial_judge_original")
    _need(type(commercial) is dict and bool(commercial), "commercial_original_missing")
    cohort = json.loads(
        commercial.get("raw_excerpt") or "",
        object_pairs_hook=physical._unique,
        parse_constant=physical._constant,
    )
    _need(type(cohort) is dict, "commercial_report_cohort")
    if cohort.get("schema_version") == "commercial-slot-evidence.v1" and len(slots) == 1:
        samples = [{"sample_slot": cohort.get("sample_slot"), "report": commercial}]
    else:
        _need(
            cohort.get("schema_version") == "chapter-quality-aggregation.v1"
            and type(cohort.get("requested_samples")) is int
            and cohort["requested_samples"] == len(slots),
            "commercial_report_samples",
        )
        samples = cohort.get("samples")
    _need(type(samples) is list and len(samples) == len(slots), "commercial_report_samples")
    for sample, (name, entry) in zip(samples, slots, strict=True):
        _need(
            type(sample) is dict
            and sample.get("sample_slot") == name
            and type(sample.get("report")) is dict
            and isinstance(sample["report"].get("llm_run_id"), str)
            and sample["report"]["llm_run_id"] == entry["report"].get("llm_run_id"),
            "commercial_report_paid_reference",
        )
        recorded = json.loads(
            sample["report"].get("raw_excerpt") or "",
            object_pairs_hook=physical._unique,
            parse_constant=physical._constant,
        )
        native = json.loads(entry["report"]["raw_excerpt"])
        _need(
            type(recorded) is dict
            and isinstance(recorded.get("raw_report"), str)
            and isinstance(recorded.get("raw_report_sha256"), str)
            and _sha(recorded["raw_report"].encode()) == recorded["raw_report_sha256"]
            and all(
                _json(recorded.get(key)) == _json(native.get(key))
                for key in (
                    "schema_version",
                    "sample_slot",
                    "attempt",
                    "source_binding",
                    "evaluation_contract",
                    "exact_source_audit",
                    "raw_report",
                    "raw_report_sha256",
                )
            ),
            "commercial_report_source_reference",
        )


@dataclass(frozen=True)
class RecordedReviewCall:
    llm_run_id: UUID
    workflow_run_id: UUID
    operation_key: str
    execution_scope_id: str
    original_attempt_id: str
    transport_operation_key: str
    request_sha256: str
    response_sha256: str
    effective_input_sha256: str
    request: llm.LLMCompletionRequest = field(repr=False)
    completion: llm.LLMCompletionResult = field(repr=False)
    source: dict[str, Any] | CommercialSources | None = field(repr=False)


@dataclass(frozen=True)
class RecordedReviewSources:
    project_id: UUID
    chapter_id: UUID
    draft_id: UUID
    report_id: UUID
    cycle_id: str
    calls: tuple[RecordedReviewCall, ...] = field(repr=False)
    schema: str = field(default=POLICY, init=False)
    grade_granted: bool = field(default=False, init=False)
    new_credit_allowed: bool = field(default=False, init=False)
    dispatch_authorized: bool = field(default=False, init=False)
    adoption_authorized: bool = field(default=False, init=False)
    current_applicability_checked: bool = field(default=False, init=False)
    issued_report_verified: bool = field(default=False, init=False)


class _Reader(physical._Reader):
    async def scalars(self, statement):
        # Preserve the native SELECT predicates/order/limit, but avoid ORM identity
        # map refresh and autoflush of unrelated pending caller changes.
        descriptions = statement.column_descriptions
        _need(len(descriptions) == 1, "single_model_select_required")
        model = descriptions[0].get("entity")
        _need(
            model is not None
            and descriptions[0].get("expr") is model
            and statement._for_update_arg is None,
            "read_only_model_select_required",
        )
        columns = [getattr(model, c.key) for c in inspect(model).column_attrs]
        with self.session.no_autoflush:
            result = await self.session.execute(statement.with_only_columns(*columns))
        return [SimpleNamespace(**dict(row)) for row in result.mappings()]


def _primary_source(request, receipt):
    from bestseller.services import primary_review_transport as transport
    from bestseller.services.lossless_source_encoding import decode_lossless_source

    _need(type(receipt) is dict, "primary_receipt_missing")
    encoded_receipt = receipt.get("primary_source_transport")
    if encoded_receipt is None:
        source, end = json.JSONDecoder(
            object_pairs_hook=physical._unique, parse_constant=physical._constant
        ).raw_decode(request.user_prompt)
        _need(
            type(source) is dict and request.user_prompt[:end] == _json(source),
            "primary_literal_source",
        )
    else:
        end = encoded_receipt.get("packet_end")
        _need(type(end) is int and 0 < end <= len(request.user_prompt), "primary_offsets")
        encoded = transport._load(request.user_prompt[:end])
        mode = encoded_receipt.get("encoding")
        if mode == transport._FRAGMENT_ENCODING:
            source = transport._fragment_decode(
                decode_lossless_source(encoded), request.user_prompt[end + 2 :]
            )
        elif mode in ("adopted-spans-v4", transport._COMPACT_ENCODING):
            source = transport.spans.decode_adopted_spans(decode_lossless_source(encoded))
        elif mode == "adopted-spans":
            source = transport.spans.decode_adopted_spans(encoded)
        else:
            source = decode_lossless_source(encoded)
    _need(type(source) is dict, "primary_source_object")
    transport.verify_primary_review_transport(
        request.system_prompt,
        request.user_prompt,
        source_context=source,
        receipt=receipt,
        tools=request.tools,
        output_tokens=request.max_tokens_override,
    )
    return source


def _pointer(root, pointer):
    _need(isinstance(pointer, str) and pointer.startswith("#/source_context/"), "alias_pointer")
    node = root
    for part in pointer[2:].split("/"):
        part = part.replace("~1", "/").replace("~0", "~")
        node = node[int(part)] if isinstance(node, list) else node[part]
    return node


def _commercial_source(request, metadata):
    from bestseller.services import commercial_review_transport as transport
    from bestseller.services.commercial_source_span_transport import decode_commercial_source_spans
    from bestseller.services.lossless_source_encoding import decode_lossless_source

    receipt = metadata.get("commercial_source_transport")
    original = metadata.get("commercial_source_input_receipt")
    _need(type(receipt) is dict and type(original) is dict, "commercial_receipts_missing")
    wire = request.user_prompt
    _need(_sha(wire.encode()) == receipt.get("wire_prompt_sha256"), "commercial_wire_hash")
    start, end = receipt.get("packet_start"), receipt.get("packet_end")
    _need(
        type(start) is int and type(end) is int and 0 <= start < end <= len(wire),
        "commercial_offsets",
    )
    encoded = transport._load(wire[start:end])
    mode = receipt.get("encoding")
    packet = (
        encoded
        if mode == "identity"
        else decode_commercial_source_spans(encoded)
        if mode == "commercial-spans-v4"
        else decode_lossless_source(encoded)
    )
    text = _json(packet)
    _need(_sha(text.encode()) == original.get("assembled_source_sha256"), "commercial_packet_hash")
    restored = deepcopy(packet)
    aliases = original.get("structural_aliases")
    _need(type(aliases) is list, "commercial_aliases_missing")
    seen = set()
    for alias in aliases:
        _need(type(alias) is dict and set(alias) == {"path", "target"}, "commercial_alias_record")
        path, target = alias["path"], alias["target"]
        _need(path not in seen and path != target, "commercial_alias_cycle")
        seen.add(path)
        _need(_json(_pointer(restored, path)) == _json({"$ref": target}), "commercial_alias_value")
        parent, leaf = path.rsplit("/", 1)
        parent_node = (
            restored["source_context"]
            if parent == "#/source_context"
            else _pointer(restored, parent)
        )
        leaf = leaf.replace("~1", "/").replace("~0", "~")
        replacement = deepcopy(_pointer(restored, target))
        if isinstance(parent_node, list):
            parent_node[int(leaf)] = replacement
        else:
            parent_node[leaf] = replacement
    rebuilt = assemble_commercial_sources(
        chapter_number=packet["chapter_number"],
        content_md=packet["current_body"],
        source=restored["source_context"],
        previous_chapters=packet["previous_chapters"],
    )
    for key in (
        "schema_version",
        "prompt_version",
        "body_sha256",
        "source_sha256",
        "semantic_source_sha256",
        "source_protocol_sha256",
        "previous_sha256",
        "previous",
    ):
        _need(
            _json(rebuilt.receipt.get(key)) == _json(original.get(key)),
            "commercial_source_reconstruction",
        )
    entries = packet["citation_catalog"]
    _need(
        type(entries) is list
        and all(type(e) is dict and isinstance(e.get("id"), str) for e in entries),
        "commercial_catalog_shape",
    )
    ids = [entry["id"] for entry in entries]
    _need(
        len(ids) == len(set(ids)) == len(rebuilt.catalog)
        and all(key in rebuilt.catalog for key in ids),
        "commercial_catalog_members",
    )
    rendered = {entry["id"]: entry for entry in json.loads(rebuilt.text)["citation_catalog"]}
    _need(
        all(_json(entry) == _json(rendered[entry["id"]]) for entry in entries),
        "commercial_catalog_entries",
    )
    # The paid array is the order authority. Never sort historical observations
    # or compare their content as a set to manufacture an old source digest.
    sources = CommercialSources(
        text, {key: rebuilt.catalog[key] for key in ids}, deepcopy(original)
    )
    transport.verify_commercial_review_transport(wire, sources=sources, receipt=receipt)
    return sources


def _request_payload(settings, run):
    root = Path(settings.artifact_store.local_dir).expanduser().resolve()
    _need(
        settings.artifact_store.mode == "local" and isinstance(run.request_payload_ref, str),
        "request_store",
    )
    path = (root / run.request_payload_ref).resolve()
    _need(path.is_relative_to(root), "request_path")
    raw = path.read_bytes()
    payload = json.loads(raw, object_pairs_hook=physical._unique, parse_constant=physical._constant)
    _json(payload)
    for item in (payload.get("messages") or {}).values():
        if isinstance(item, dict) and "text" in item:
            _need(type(item.get("original_chars")) is int, "request_message_types")
    for item in (payload.get("structured_payload_receipts") or {}).values():
        _need(
            type(item.get("original_chars")) is int and type(item.get("limit_chars")) is int,
            "request_structured_types",
        )
    requested = payload.get("requested") or {}
    for key in ("tools", "tool_choice"):
        if key in payload and key in requested:
            _need(_json(payload[key]) == _json(requested[key]), "request_tool_alias_types")
    return payload, _sha(raw), raw


async def _call(reader, settings, project, chapter, run_id, catalog, *, kind, slot_evidence=None):
    run = await reader.get(LlmRunModel, UUID(str(run_id)))
    _run_identity(run, project, kind=kind)
    owner = await reader.get(WorkflowRunModel, run.workflow_run_id)
    _need(
        owner is not None
        and owner.project_id == project.id
        and owner.scope_type == "chapter"
        and owner.scope_id == chapter.id,
        "run_workflow",
    )
    payload, request_sha, request_bytes = _request_payload(settings, run)
    body = (
        await retained._primary6_body(reader, run, project_id=project.id, chapter_id=chapter.id)
        if retained._primary6(run)
        else None
    )
    request = retained._request_from_payload(
        run, request_bytes, chapter_body=body.content_md if body else None
    )
    for key in (
        "model_tier",
        "model_catalog_key",
        "temperature_override",
        "max_tokens_override",
        "cache_system",
    ):
        _need(
            _json(payload["requested"][key]) == _json(getattr(request, key)),
            "request_parameter_types",
        )
    role = retained._fresh_role(settings, project, request, catalog)
    effective = llm._effective_input_hash(request, role)
    execution = (run.metadata_json or {}).get("execution_receipt") or {}
    scope = execution.get("scope_id")
    operation = await reader.get(ExecutionOperationModel, execution.get("operation_key"))
    completed = [a for a in execution.get("attempts", []) if a.get("status") == "completed"]
    _need(operation is not None and len(completed) == 1, "paid_root_missing")
    attempt = await reader.get(ExecutionAttemptModel, completed[0]["attempt_id"])
    _need(attempt is not None, "paid_attempt_missing")
    transport_key = _sha(f"transport-result.v1\n{scope}\n{attempt.id}".encode())
    transport = await reader.get(ExecutionOperationModel, transport_key)
    _need(transport is not None, "paid_transport_missing")
    _strict_paid_pair(
        operation.result_json,
        (transport.result_json or {}).get("result"),
        request=request,
        effective_hash=effective,
        model=role.model,
    )
    paid, operation_key = await retained._physical_return(
        reader, run, scope=scope, expected_hash=effective, expected_model=role.model
    )
    response, response_sha = physical._file(settings, run, "response")
    physical._response(response, paid)
    if kind == "commercial":
        _need(slot_evidence is not None, "commercial_slot_evidence_required")
        _commercial_call_binding(run, paid, slot_evidence)
    _need(_request_payload(settings, run)[1] == request_sha, "request_changed_during_read")
    source = (
        _primary_source(request, (run.metadata_json or {}).get("review_source_receipt"))
        if kind == "primary"
        else _commercial_source(request, run.metadata_json or {})
        if kind == "commercial"
        else None
    )
    return RecordedReviewCall(
        run.id,
        run.workflow_run_id,
        operation_key,
        scope,
        attempt.id,
        transport_key,
        request_sha,
        response_sha,
        effective,
        request,
        paid,
        source,
    )


async def read_recorded_review_sources(
    session, settings, *, project_id, chapter_id, draft_id
) -> RecordedReviewSources:
    """Select original calls from native available-cycle/report/cohort pointers.

    No caller-supplied source, hash, issuer or approval is an input. Historical
    source comparison is distinct from current DB applicability and permissions.
    """
    return await _read_recorded_cycle_sources(
        session, settings, project_id=project_id, chapter_id=chapter_id, draft_id=draft_id,
    )


async def read_archived_review_sources(
    session, settings, *, project_id, chapter_id, draft_id, cycle_id
) -> RecordedReviewSources:
    """Read a native historical cycle for this current body, without authority.

    A cycle ID is only a lookup: the original cycle/report/body/paid bindings
    are revalidated exactly. This never moves the active pointer or a grade.
    """
    _need(isinstance(cycle_id, str) and len(cycle_id) == 64, "archive_cycle_id")
    return await _read_recorded_cycle_sources(
        session, settings, project_id=project_id, chapter_id=chapter_id, draft_id=draft_id,
        selected_cycle_id=cycle_id,
    )


async def _read_recorded_cycle_sources(
    session, settings, *, project_id, chapter_id, draft_id, selected_cycle_id=None
):
    from bestseller.services import pipelines

    _need(
        all(isinstance(key, UUID) for key in (project_id, chapter_id, draft_id)),
        "native_ids_required",
    )
    reader = _Reader(session)
    project = await reader.get(ProjectModel, project_id)
    chapter = await reader.get(ChapterModel, chapter_id)
    draft = await reader.get(ChapterDraftVersionModel, draft_id)
    _need(
        project is not None
        and chapter is not None
        and draft is not None
        and chapter.project_id == draft.project_id == project_id
        and draft.chapter_id == chapter_id,
        "native_ownership",
    )
    current = await reader.rows(
        ChapterDraftVersionModel,
        ChapterDraftVersionModel.chapter_id == chapter_id,
        ChapterDraftVersionModel.is_current.is_(True),
    )
    if selected_cycle_id is None:
        _need(len(current) == 1 and current[0].id == draft_id, "current_draft")
    fingerprint = pipelines.chapter_review_body_fingerprint(draft.content_md or "")
    metadata = chapter.metadata_json or {}
    active = metadata.get("chapter_review_active_cycle") or {}
    cycle_id = selected_cycle_id or (
        active.get("cycle_id")
        if active.get("source_body_hash") == fingerprint["digest"]
        else pipelines._chapter_critic_cycle_id(project_id, chapter_id, fingerprint["digest"])
    )
    cycle = (metadata.get("chapter_critic_cycles") or {}).get(cycle_id) or {}
    attempts = cycle.get("attempts") or []
    _need(
        cycle.get("cycle_id") == cycle_id
        and cycle.get("source_draft_id") == str(draft_id)
        and cycle.get("source_body_hash") == fingerprint["digest"]
        and cycle.get("state") == "completed"
        and bool(attempts),
        "available_cycle_required",
    )
    attempt = attempts[-1]
    _need(
        attempt.get("state") == "completed"
        and attempt.get("outcome") == "available"
        and type(attempt.get("attempt")) is int,
        "available_attempt_required",
    )
    _, report, _, _ = await pipelines._retained_chapter_critic_result(reader, draft, attempt)
    _need(
        _json(report.structured_output.get("reviewed_body_fingerprint")) == _json(fingerprint),
        "report_body_types",
    )
    catalog, _ = retained._fresh_catalog()
    primary = await _call(
        reader, settings, project, chapter, report.llm_run_id, catalog, kind="primary"
    )
    _need(
        str(primary.workflow_run_id) == cycle.get("review_workflow_run_id"),
        "primary_cycle_workflow",
    )
    calls = [primary]
    evidence = report.structured_output.get("evidence_summary") or {}
    window_id = _window_run_id(evidence)
    if window_id:
        window = await _call(reader, settings, project, chapter, window_id, catalog, kind="window")
        _need(
            window.workflow_run_id == primary.workflow_run_id
            and window.execution_scope_id == primary.execution_scope_id,
            "window_cycle_scope",
        )
        calls.append(window)
    commercial = evidence.get("llm_commercial_judge")
    if commercial is not None:
        _need(type(commercial) is dict and bool(commercial), "commercial_result_missing")
        key = _hash(
            {
                "project_id": str(project_id),
                "chapter_id": str(chapter_id),
                "body_sha256": _sha(draft.content_md.encode()),
            }
        )
        pointer = (metadata.get("commercial_component_cohorts") or {}).get(key) or {}
        owner = await reader.get(WorkflowRunModel, UUID(pointer["workflow_run_id"]))
        _need(
            owner is not None
            and owner.project_id == project_id
            and owner.scope_type == "chapter"
            and owner.scope_id == chapter_id,
            "commercial_owner",
        )
        journal = ((owner.metadata_json or {}).get("commercial_component_cohorts") or {}).get(
            key
        ) or {}
        binding = journal.get("binding") or {}
        _need(
            journal.get("schema") == "commercial-component-cohort.v1"
            and pointer.get("binding_sha256") == _hash(binding)
            and all(
                binding.get(k) == str(v)
                for k, v in (
                    ("project_id", project_id),
                    ("chapter_id", chapter_id),
                    ("draft_id", draft_id),
                )
            ),
            "commercial_binding",
        )
        slots = _commercial_slots(journal, binding)
        _commercial_report_binding(evidence, slots)
        for name, slot in slots:
            calls.append(
                await _call(
                    reader,
                    settings,
                    project,
                    chapter,
                    slot["report"]["llm_run_id"],
                    catalog,
                    kind="commercial",
                    slot_evidence=(name, slot, binding),
                )
            )
    _need(
        len({call.llm_run_id for call in calls}) == len(calls)
        and len({call.operation_key for call in calls}) == len(calls),
        "distinct_paid_calls",
    )
    return RecordedReviewSources(
        project_id, chapter_id, draft_id, report.id, cycle_id, tuple(calls)
    )
