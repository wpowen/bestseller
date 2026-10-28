"""Recorded source codecs and raw types; no adoption/paid authority fixtures."""

from copy import deepcopy
import json
import socket
from types import SimpleNamespace
from uuid import uuid4

import pytest

from bestseller.services import llm
from bestseller.services import primary_review_transport as primary
from bestseller.services import recorded_review_sources as service
from bestseller.services.commercial_review_transport import prepare_commercial_review_transport
from bestseller.services.commercial_source_evidence import assemble_commercial_sources

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def no_network_or_provider(monkeypatch):
    def denied(*args, **kwargs):
        pytest.fail("recorded source codec tests must not use network/provider IO")

    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(llm, "complete_text", denied)


def test_primary_keeps_array_order_and_boolean_integer_types():
    source = {"schema": "typed-codec-fixture", "ordered": [2, 0, 1], "false": False, "zero": 0}
    system, user, receipt = primary.prepare_source_complete_prompt(
        system_prompt="Read supplied data.",
        user_prompt="Review.",
        source_context=source,
        tools=None,
        output_tokens=1024,
    )
    request = llm.LLMCompletionRequest(
        logical_role="critic",
        system_prompt=system,
        user_prompt=user,
        fallback_response="unused",
        max_tokens_override=1024,
    )
    result = service._primary_source(request, receipt)
    assert result == source
    assert result["ordered"] == [2, 0, 1]
    assert type(result["false"]) is bool and type(result["zero"]) is int


def commercial_fixture():
    # The supplied data are explicit synthetic observations, never a native grade/prefix.
    event = {"text": "An observation.", "temporal_status": "past", "position": 0}
    context = {
        "established_facts": {"z": [event], "a": [deepcopy(event)]},
        "story_bible": {"literal": {"$ref": "authored_literal"}},
    }
    original = assemble_commercial_sources(
        chapter_number=2, content_md="First.\n\nSecond.", source=context, previous_chapters=[]
    )
    wire, transport = prepare_commercial_review_transport(
        "Read sources.", original.text, sources=original, tools=None, output_tokens=8192
    )
    request = llm.LLMCompletionRequest(
        logical_role="critic",
        system_prompt="Read sources.",
        user_prompt=wire,
        fallback_response="unused",
        max_tokens_override=8192,
    )
    metadata = {
        "commercial_source_transport": transport,
        "commercial_source_input_receipt": original.receipt,
    }
    return original, request, metadata


def test_commercial_rebuilds_full_catalog_in_the_paid_wire_order():
    original, request, metadata = commercial_fixture()
    rebuilt = service._commercial_source(request, metadata)
    assert rebuilt.text == original.text
    assert list(rebuilt.catalog) == list(original.catalog)
    assert service._json(rebuilt.catalog) == service._json(original.catalog)
    assert rebuilt.receipt == original.receipt
    assert "authored_literal" in rebuilt.text


def test_commercial_packet_tampering_is_rejected():
    _, request, metadata = commercial_fixture()
    request = request.model_copy(update={"user_prompt": request.user_prompt + " "})
    with pytest.raises(ValueError):
        service._commercial_source(request, metadata)


@pytest.mark.parametrize(
    "field,replacement", [("input_tokens", False), ("output_tokens", 0.0), ("fallback_used", 0)]
)
def test_raw_paid_result_rejects_model_type_coercion(field, replacement):
    raw = llm.LLMCompletionResult(
        content="x", provider="minimax", model_name="fixture", input_tokens=0, output_tokens=0
    ).model_dump(mode="json")
    raw[field] = replacement
    with pytest.raises(service.RecordedReviewSourceError, match="paid_result_types"):
        service._strict_paid_result(raw)


def test_original_raw_paid_result_is_preserved():
    raw = llm.LLMCompletionResult(
        content="x", provider="minimax", model_name="fixture", input_tokens=0, output_tokens=0
    ).model_dump(mode="json")
    before = json.dumps(raw, sort_keys=True)
    service._strict_paid_result(raw)
    assert json.dumps(raw, sort_keys=True) == before


def test_two_paid_roots_cannot_disagree_by_nested_boolean_integer_equality():
    raw = llm.LLMCompletionResult(
        content="x", provider="minimax", model_name="fixture", raw_message={"index": 0}
    ).model_dump(mode="json")
    other = deepcopy(raw)
    other["raw_message"]["index"] = False
    assert raw == other  # Demonstrate the unsafe Python equality explicitly.
    service._strict_paid_result(raw)
    service._strict_paid_result(other)
    with pytest.raises(service.RecordedReviewSourceError, match="paid_return_types"):
        service._strict_paid_pair(raw, other, request=None, effective_hash=None, model=None)


@pytest.mark.parametrize(
    "key,field", [("paid_call_origin", "workflow_run_id"), ("effective_input_receipt", "hash")]
)
def test_two_paid_roots_cannot_disagree_on_original_identity_metadata(key, field):
    raw, other, request, effective = native_receipt_fixture()
    other["metadata"][key][field] = "different"
    with pytest.raises(service.RecordedReviewSourceError, match="paid_identity_metadata"):
        service._strict_paid_pair(
            raw, other, request=request, effective_hash=effective, model="fixture"
        )


def native_receipt_fixture():
    request = llm.LLMCompletionRequest(
        project_id=uuid4(),
        workflow_run_id=uuid4(),
        logical_role="critic",
        prompt_template="chapter_review",
        prompt_version="fixture",
        system_prompt="Read.",
        user_prompt="Original.",
        fallback_response="unused",
    )
    effective = "a" * 64
    core = {"schema": "effective-input.v1", "hash": effective, "model": "fixture"}
    transport = llm.LLMCompletionResult(
        content="x",
        provider="minimax",
        model_name="fixture",
        metadata={
            "paid_call_origin": llm._paid_call_origin(request, effective),
            "effective_input_receipt": core,
        },
    ).model_dump(mode="json")
    operation = deepcopy(transport)
    operation["metadata"]["effective_input_receipt"] = {
        **core,
        "messages_source": "system_user",
        "tools_included": False,
        "legacy_prompt_hash": llm._hash_prompt(request.system_prompt, request.user_prompt),
    }
    return operation, transport, request, effective


def test_native_finished_operation_can_expand_transport_identity_receipt():
    operation, transport, request, effective = native_receipt_fixture()
    before = service._json([operation, transport])
    service._strict_paid_pair(
        operation, transport, request=request, effective_hash=effective, model="fixture"
    )
    assert service._json([operation, transport]) == before


def test_native_settlement_before_finish_keeps_compact_operation_receipt():
    _, transport, request, effective = native_receipt_fixture()
    service._strict_paid_pair(
        transport, deepcopy(transport), request=request, effective_hash=effective, model="fixture"
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("messages_source", "override"),
        ("tools_included", 0),
        ("legacy_prompt_hash", "f" * 64),
        ("hash", "f" * 64),
        ("model", "other"),
        ("schema", "other"),
        ("invented", True),
    ],
)
def test_forged_expanded_receipt_fails_with_safe_exact_field_differences(field, value):
    operation, transport, request, effective = native_receipt_fixture()
    operation["metadata"]["effective_input_receipt"][field] = value
    with pytest.raises(service.RecordedReviewSourceError, match="paid_identity_metadata") as caught:
        service._strict_paid_pair(
            operation, transport, request=request, effective_hash=effective, model="fixture"
        )
    differences = caught.value.differences
    assert len(differences) == 1
    assert differences[0]["field"] == "operation.metadata.effective_input_receipt." + field
    assert set(differences[0]["expected"]) == {"type", "length", "sha256"}
    assert set(differences[0]["actual"]) == {"type", "length", "sha256"}
    assert request.user_prompt not in service._json(differences)


@pytest.mark.parametrize("field", ["messages_source", "tools_included", "legacy_prompt_hash"])
def test_partial_expanded_receipt_is_not_a_native_compact_receipt(field):
    operation, transport, request, effective = native_receipt_fixture()
    del operation["metadata"]["effective_input_receipt"][field]
    with pytest.raises(service.RecordedReviewSourceError, match="paid_identity_metadata"):
        service._strict_paid_pair(
            operation, transport, request=request, effective_hash=effective, model="fixture"
        )


def test_forging_both_origins_cannot_replace_the_native_request_identity():
    operation, transport, request, effective = native_receipt_fixture()
    for result in (operation, transport):
        result["metadata"]["paid_call_origin"]["workflow_run_id"] = str(uuid4())
    with pytest.raises(service.RecordedReviewSourceError, match="paid_identity_metadata"):
        service._strict_paid_pair(
            operation, transport, request=request, effective_hash=effective, model="fixture"
        )


def slot_fixture():
    binding = {
        "samples": 2,
        "source_binding": {"body_sha256": "body"},
        "evaluation_contract": {"version": 1},
    }
    slots = {}
    for i in range(2):
        name = f"chapter-quality.independent-v2:{i}"
        envelope = {
            "schema_version": "commercial-slot-evidence.v1",
            "sample_slot": name,
            "attempt": 1,
            "source_binding": binding["source_binding"],
            "evaluation_contract": binding["evaluation_contract"],
        }
        slots[name] = {
            "state": "completed",
            "used_attempts": 1,
            "report": {"llm_run_id": str(uuid4()), "raw_excerpt": json.dumps(envelope)},
        }
    return {
        "slots": slots,
        "aggregate": {
            "slots_sha256": service._hash({name: entry["report"] for name, entry in slots.items()})
        },
    }, binding


def test_native_slot_collection_retains_order():
    journal, binding = slot_fixture()
    assert [name for name, _ in service._commercial_slots(journal, binding)] == list(
        journal["slots"]
    )


def report_slot_fixture():
    journal, binding = slot_fixture()
    for entry in journal["slots"].values():
        envelope = json.loads(entry["report"]["raw_excerpt"])
        envelope.update(
            raw_report="retained provider text",
            raw_report_sha256=service._sha(b"retained provider text"),
            exact_source_audit={"source_sha256": "source"},
        )
        entry["report"]["raw_excerpt"] = json.dumps(envelope)
    samples = [
        {"sample_slot": name, "report": deepcopy(entry["report"])}
        for name, entry in journal["slots"].items()
    ]
    commercial = {
        "raw_excerpt": json.dumps(
            {
                "schema_version": "chapter-quality-aggregation.v1",
                "requested_samples": 2,
                "samples": samples,
            }
        )
    }
    return {"llm_commercial_judge": commercial}, list(journal["slots"].items())


def test_report_source_references_bind_to_the_same_native_paid_slots():
    evidence, slots = report_slot_fixture()
    service._commercial_report_binding(evidence, slots)


@pytest.mark.parametrize(
    "fault", ["other_cohort", "raw_changed", "missing_sample", "sample_count_boolean"]
)
def test_report_cannot_borrow_sources_from_a_different_self_consistent_cohort(fault):
    evidence, slots = report_slot_fixture()
    report = evidence["llm_commercial_judge"]
    payload = json.loads(report["raw_excerpt"])
    if fault == "other_cohort":
        payload["samples"][0]["report"]["llm_run_id"] = str(uuid4())
    elif fault == "raw_changed":
        item = payload["samples"][0]["report"]
        envelope = json.loads(item["raw_excerpt"])
        envelope["raw_report"] = "another provider return"
        item["raw_excerpt"] = json.dumps(envelope)
    elif fault == "missing_sample":
        payload["samples"].pop()
    else:
        payload["requested_samples"] = True
    report["raw_excerpt"] = json.dumps(payload)
    with pytest.raises(service.RecordedReviewSourceError):
        service._commercial_report_binding(evidence, slots)


@pytest.mark.parametrize("fault", ["swapped_run_ids", "wrong_sample", "boolean_attempt"])
def test_native_slot_collection_rejects_broken_continuous_binding(fault):
    journal, binding = slot_fixture()
    entries = list(journal["slots"].values())
    if fault == "swapped_run_ids":
        left, right = entries[0]["report"], entries[1]["report"]
        left["llm_run_id"], right["llm_run_id"] = right["llm_run_id"], left["llm_run_id"]
    else:
        envelope = json.loads(entries[0]["report"]["raw_excerpt"])
        envelope["sample_slot" if fault == "wrong_sample" else "attempt"] = (
            "other" if fault == "wrong_sample" else True
        )
        entries[0]["report"]["raw_excerpt"] = json.dumps(envelope)
        journal["aggregate"]["slots_sha256"] = service._hash(
            {name: entry["report"] for name, entry in journal["slots"].items()}
        )
    with pytest.raises(service.RecordedReviewSourceError):
        service._commercial_slots(journal, binding)


@pytest.mark.parametrize("value", [{"valid": True}, {"llm_run_id": ""}, {}])
def test_present_window_evidence_requires_a_native_run_id(value):
    with pytest.raises(service.RecordedReviewSourceError, match="window_run"):
        service._window_run_id({"llm_window_quality_judge": value})


def test_window_identity_cannot_be_a_different_critic_kind():
    project = SimpleNamespace(id=uuid4())
    run = SimpleNamespace(
        project_id=project.id, logical_role="critic", prompt_template="chapter_review"
    )
    with pytest.raises(service.RecordedReviewSourceError, match="run_identity"):
        service._run_identity(run, project, kind="window")


def test_request_decoder_uses_the_supplied_immutable_bytes_without_reopening_files(monkeypatch):
    from pathlib import Path

    from bestseller.services import retained_review_input_proof as retained

    run = SimpleNamespace(
        id=uuid4(),
        project_id=uuid4(),
        workflow_run_id=uuid4(),
        step_run_id=None,
        logical_role="critic",
        prompt_template="fixture",
        prompt_version="1",
        metadata_json={},
    )
    payload = {
        "schema_version": "llm-payload.v1",
        "kind": "request",
        "replayable": True,
        "replayability_reasons": [],
        "llm_run_id": str(run.id),
        "project_id": str(run.project_id),
        "workflow_run_id": str(run.workflow_run_id),
        "logical_role": run.logical_role,
        "prompt_template": run.prompt_template,
        "prompt_version": run.prompt_version,
        "messages": {
            "source": "system_user",
            "replayable": True,
            "system": {"text": "Read.", "truncated": False, "original_chars": 5},
            "user": {"text": "Original.", "truncated": False, "original_chars": 9},
        },
        "requested": {
            "model_tier": "standard",
            "model_catalog_key": None,
            "temperature_override": None,
            "max_tokens_override": 1024,
            "cache_system": False,
        },
    }
    raw = json.dumps(payload).encode()

    def cannot_reopen(*args, **kwargs):
        pytest.fail("decoder must use the exact already-hashed request bytes")

    monkeypatch.setattr(Path, "read_bytes", cannot_reopen)
    assert retained._request_from_payload(run, raw).user_prompt == "Original."


@pytest.mark.parametrize(
    "extra", [{"source": {}}, {"source_hash": "caller"}, {"issuer": "caller"}, {"approved": True}]
)
def test_public_api_does_not_accept_source_or_approval_arguments(extra):
    with pytest.raises(TypeError):
        service.read_recorded_review_sources(
            None, None, project_id=None, chapter_id=None, draft_id=None, **extra
        )
