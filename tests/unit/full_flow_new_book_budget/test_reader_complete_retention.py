"""Main-run RED for prospective reader v2 full-JSON physical retention.

No new service/API is proposed. Extend the two existing retention functions:
source_complete_retention_metadata(request, *, role, logical_source,
                                   packet_start, packet_end) -> dict
retention_limit(settings, request, *, audit, role) -> int

For ONLY the exact CONTRACTS below, logical_source is the ENTIRE strict JSON
user document, offsets are 0:len(user_prompt), and codec_schema records its
native document schema without pretending it is a lossless codec. Bind native
reader-first-planning.v2/config, inner source hash, complete wire and actual
effective role/output; enforce audit, physical/model/finite capacities again.
The new repair document embeds reader_first_planning like the roles document.

Producer seams: short._short_roles_request; planner's outline checkpoint;
short's repair_request. Reconstruct the same new branches in source selection
roles/failed-reader/repair and chapter_writer_delivery. V1 stays byte-identical.
The prospective fixtures below do NOT relabel or authorize retained paid rows.
Main owns execution; this module uses no DB or provider, only temp retention.
"""

from contextlib import nullcontext
from copy import deepcopy
import hashlib
import json
import socket
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from bestseller.services import fanqie_outline_source_selection as selection
from bestseller.services import fanqie_short_planner as short
from bestseller.services import llm
from bestseller.services import source_bound_planning_content as protocol
from bestseller.services import source_complete_retention as retention
from bestseller.services.finite_book_budget import bind_run
from bestseller.services.llm_capacity import ModelRequestCapacityError
from bestseller.settings import LLMRoleSettings, load_settings

from .conftest import diagnostic_summary

pytestmark = pytest.mark.unit

CONTRACTS = {
    "roles": ("planner", "fanqie_reader_first_field_roles", "fanqie-rf-field-roles-v2"),
    "review": ("critic", "planner_reader_first_outline_review", "rf-outline-review-v2"),
    "repair": (
        "planner", "planner_reader_first_full_outline_commercial_surgical_repair",
        "rf-outline-repair-v2",
    ),
}
OLD_VERSIONS = {
    "roles": "fanqie-rf-field-roles-v1", "review": "rf-outline-review-v1",
    "repair": "rf-outline-repair-v1",
}
TAIL = "FINAL_SOURCE_LEAF: preserve null, false, literal $ref and whitespace 🪼  "


def strict_json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"),
                      allow_nan=False)


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("reader retention RED forbids DB, HTTP and provider calls")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(llm, "_call_litellm", forbidden)


def make_state(tmp_path, kind="review", *, prospective=True, size=122937):
    binding = protocol.new_reader_first_binding(
        "An explicit synthetic choice and its emotional consequence.",
        protocol="reader-first-planning.v2" if prospective else "reader-first-planning.v1",
    )
    project = SimpleNamespace(id=uuid4(), slug="offline-retention", target_chapters=4,
                              metadata_json={"reader_first_planning": binding})
    workflow_id = uuid4()
    outline = {
        "_meta": {"archive": "", "tail": TAIL, "opaque": [None, False, 0, {"$ref": "literal"}]},
        "chapters": [{"chapter_number": n, "chapter_goal": f"A complete choice {n}.",
                      "_meta": {"original": True},
                      "scenes": [{"scene_number": 1, "purpose": {"story": "A visible consequence."},
                                  "_meta": {"unchanged": [None, False]}}]}
                     for n in range(1, 5)],
    }
    source = {
        "selected_source": {"complete_foundation_content": {"cast_spec": {"unknown": None}},
                            "project_inputs": {"target_chapters": 4}},
        "original_beat": {"raw": {"beats": [{"segment_number": n} for n in range(1, 5)]}},
        "base_outline": {"chapters": deepcopy(outline["chapters"])}, "outline": outline,
    }

    def build():
        if kind == "roles":
            request = short._short_roles_request(
                project, binding, outline, workflow_run_id=workflow_id,
                slot=0, rejected=None, requested_by="offline-retention-test",
            )
        elif kind == "repair":
            request = selection._short_repair_request(
                project, binding, source,
                {"status": "unknown", "protocol": binding["protocol"],
                 "config_sha256": binding["config_sha256"], "reason": "offline input, no grade"},
                workflow_id,
            )
        else:
            system, user = protocol.reader_first_review_prompts(
                binding, source=source, checkpoint="outline",
            )
            request = llm.LLMCompletionRequest(
                logical_role="critic", project_id=project.id, workflow_run_id=workflow_id,
                system_prompt=system, user_prompt=user, fallback_response="{}",
                prompt_template=CONTRACTS[kind][1], prompt_version=OLD_VERSIONS[kind],
                max_tokens_override=4096, temperature_override=.2,
                metadata={"reader_first_planning": deepcopy(binding),
                          "source_sha256": protocol._reader_hash(source), "checkpoint": "outline"},
            )
        if prospective:
            # A new request contract under test, never a migrated paid request.
            document = json.loads(request.user_prompt)
            if kind == "repair":
                document["reader_first_planning"] = deepcopy(binding)
            request = request.model_copy(update={
                "prompt_version": CONTRACTS[kind][2], "user_prompt": strict_json(document),
            })
        return request

    request = build()
    # Exactly reproduce the observed character boundary without copying story text.
    outline["_meta"]["archive"] = "x" * max(0, size - len(request.user_prompt))
    request = build()
    settings = load_settings(env={})
    settings.artifact_store.retain_prompt_payloads = True
    settings.artifact_store.mode = "local"
    settings.artifact_store.local_dir = str(tmp_path)
    role = LLMRoleSettings(model="openai/MiniMax-M3.1-Flash",
                           api_base="https://api.minimaxi.com/v1", temperature=.2,
                           max_tokens=8192, timeout_seconds=120, stream=True,
                           reasoning_effort="medium")
    return SimpleNamespace(request=request, role=role, settings=settings, binding=binding,
                           project=project, source=source, root=json.loads(request.user_prompt),
                           tmp_path=tmp_path, kind=kind)


def attach(state, *, logical_source=None, packet_start=0, packet_end=None):
    request = state.request
    receipt = retention.source_complete_retention_metadata(
        request, role=state.role,
        logical_source=state.root if logical_source is None else logical_source,
        packet_start=packet_start,
        packet_end=len(request.user_prompt) if packet_end is None else packet_end,
    )
    request.metadata[retention.METADATA_KEY] = receipt
    return receipt


def limit(state, *, audit=None, role=None):
    return retention.retention_limit(
        state.settings, state.request,
        audit=llm._project_prompt_audit.get() if audit is None else audit,
        role=state.role if role is None else role,
    )


def persist(state, *, run_id=None, role=None):
    role = role or state.role
    payload = llm._effective_input_payload(state.request, role)
    return llm._persist_prompt_payloads(
        settings=state.settings, request=state.request, llm_run_id=run_id or uuid4(),
        configured_model=role.model, effective_model=role.model, provider="openai",
        effective_temperature=payload["temperature"], effective_max_tokens=payload["max_tokens"],
        effective_timeout_seconds=role.timeout_seconds, effective_stream=payload["stream"],
        effective_thinking_type=payload["thinking_type"],
        effective_reasoning_effort=payload["reasoning_effort"], effective_role_settings=role,
        content="An offline retained response; no grade or adoption asserted.", tool_call_count=0,
        finish_reason="stop", fallback_used=False, fallback_source=None,
        input_tokens=None, output_tokens=None,
    )


def record_difference(request, field, expected, actual):
    request.node.user_properties.append(("reader_retention_operands", strict_json({
        "field": field, "expected": diagnostic_summary(expected),
        "actual": diagnostic_summary(actual),
    })))


def assert_source_difference(error, request, field, expected, actual):
    differences = getattr(error, "differences", ())
    request.node.user_properties.append(("reader_retention_error", strict_json({
        "code": getattr(error, "code", type(error).__name__), "differences": differences,
    })))
    assert {
        "field": field, "expected": diagnostic_summary(expected),
        "actual": diagnostic_summary(actual),
    } in differences


@pytest.mark.parametrize("kind", CONTRACTS)
def test_exact_v2_contract_retains_the_entire_native_json_without_codec_rewrite(tmp_path, kind):
    state = make_state(tmp_path, kind)
    before = state.request.model_dump(mode="json")
    root_before = deepcopy(state.root)
    assert len(state.request.user_prompt) == 122937
    receipt = attach(state)
    assert receipt["codec_schema"] == state.root["schema"]
    expected_hash = hashlib.sha256(strict_json(state.root).encode()).hexdigest()
    assert receipt["logical_source_sha256"] == expected_hash
    assert receipt["packet_start"] == 0 and receipt["packet_end"] == 122937
    assert receipt["author_authority_granted"] is receipt["grade_asserted"] is False
    with llm.bind_project_prompt_audit(state.request.project_id, reason="offline full native JSON"):
        assert limit(state) == limit(state) == 1_000_000
    after = state.request.model_dump(mode="json")
    after["metadata"].pop(retention.METADATA_KEY)
    assert after == before and state.root == root_before
    assert not list(tmp_path.iterdir())


def test_observed_122937_critic_request_is_physically_replayable_and_preserves_tail(
    tmp_path, request,
):
    state = make_state(tmp_path)
    attach(state)
    with llm.bind_project_prompt_audit(state.request.project_id, reason="physical reader custody"):
        refs = persist(state)
    assert refs[2:] == (None, True)
    retained, response = [json.loads((tmp_path / ref).read_bytes()) for ref in refs[:2]]
    actual = retained["messages"]["user"]
    record_difference(request, "retained_request.user.characters", 122937, len(actual["text"]))
    assert retained["replayable"] is response["replayable"] is True
    assert actual == {
        "text": state.request.user_prompt, "truncated": False, "original_chars": 122937,
    }
    assert json.loads(actual["text"]) == state.root
    assert TAIL in actual["text"]


@pytest.mark.parametrize("kind", CONTRACTS)
def test_new_identity_without_receipt_refuses_instead_of_downgrading_to_120k(tmp_path, kind):
    state = make_state(tmp_path, kind)
    with llm.bind_project_prompt_audit(state.request.project_id, reason="mandatory receipt"):
        with pytest.raises(ValueError, match="source_complete_optin_missing"):
            limit(state)


@pytest.mark.parametrize("kind", CONTRACTS)
def test_physical_retainer_cannot_ignore_a_missing_v2_receipt(tmp_path, kind):
    state = make_state(tmp_path, kind)
    with llm.bind_project_prompt_audit(state.request.project_id, reason="physical repeat"):
        with pytest.raises(ValueError, match="source_complete_optin_missing"):
            persist(state)
    assert not list(tmp_path.rglob("*.json"))


@pytest.mark.parametrize("kind", CONTRACTS)
def test_v1_story_flags_and_v2_metadata_alone_cannot_raise_retention(tmp_path, kind):
    # Real v1 wire remains untouched; contradictory story flags confer no authority.
    state = make_state(tmp_path, kind, prospective=False, size=4000)
    state.request.metadata.update(source_complete=True, project_audited=True,
                                  reader_first_planning=protocol.new_reader_first_binding(
                                      "An explicit synthetic choice and its emotional consequence.",
                                      protocol="reader-first-planning.v2"))
    before = state.request.model_dump(mode="json")
    with llm.bind_project_prompt_audit(state.request.project_id, reason="old native identity"):
        assert limit(state) == 120000
    assert state.request.model_dump(mode="json") == before


@pytest.mark.parametrize("kind", CONTRACTS)
def test_v1_retained_paid_bytes_remain_identical_after_retention_preflight(tmp_path, kind):
    state = make_state(tmp_path, kind, prospective=False, size=4000)
    run_id = uuid4()
    before = state.request.model_dump(mode="json")
    with llm.bind_project_prompt_audit(state.request.project_id, reason="v1 frozen physical bytes"):
        first = persist(state, run_id=run_id)
        old_bytes = [(tmp_path / ref).read_bytes() for ref in first[:2]]
        assert limit(state) == 120000
        second = persist(state, run_id=run_id)
    assert first == second
    assert old_bytes == [(tmp_path / ref).read_bytes() for ref in second[:2]]
    assert state.request.model_dump(mode="json") == before


@pytest.mark.parametrize("mutation", ["role", "template", "v1", "lookalike", "future"])
def test_only_exact_native_role_template_version_tuple_can_attach(tmp_path, mutation):
    state = make_state(tmp_path)
    changes = {
        "role": {"logical_role": "planner"}, "template": {"prompt_template": "scene_writer"},
        "v1": {"prompt_version": "rf-outline-review-v1"},
        "lookalike": {"prompt_version": "rf-outline-review-v2-extra"},
        "future": {"prompt_version": "rf-outline-review-v3"},
    }
    state.request = state.request.model_copy(update=changes[mutation])
    with pytest.raises(ValueError):
        attach(state)
    assert retention.METADATA_KEY not in state.request.metadata


@pytest.mark.parametrize("kind", CONTRACTS)
@pytest.mark.parametrize("mutation", ["wire_v1", "metadata_conflict", "source_hash"])
def test_native_json_binding_and_source_hash_must_match_not_just_metadata(
    tmp_path, kind, mutation, request,
):
    state = make_state(tmp_path, kind)
    root = deepcopy(state.root)
    marker_key = "binding" if kind == "review" else "reader_first_planning"
    if mutation == "wire_v1":
        root[marker_key] = protocol.new_reader_first_binding("An explicit synthetic choice.")
        field = "request.user_prompt." + marker_key + ".protocol"
        expected, actual = state.binding["protocol"], root[marker_key]["protocol"]
    elif mutation == "metadata_conflict":
        state.request.metadata["reader_first_planning"] = protocol.new_reader_first_binding(
            "Another original input.", protocol="reader-first-planning.v2",
        )
        field = "request.metadata.reader_first_planning"
        expected, actual = state.binding, state.request.metadata["reader_first_planning"]
    else:
        root["source_sha256"] = "0" * 64
        field = "request.user_prompt.source_sha256"
        expected, actual = state.root["source_sha256"], root["source_sha256"]
    state.root = root
    state.request.user_prompt = strict_json(root)
    record_difference(request, field, expected, actual)
    with pytest.raises(ValueError) as caught:
        attach(state)
    assert_source_difference(caught.value, request, field, expected, actual)
    assert retention.METADATA_KEY not in state.request.metadata


@pytest.mark.parametrize("mutation", ["subset", "omit_meta", "bool_to_int", "tail"])
def test_adapter_binds_complete_root_and_all_json_value_types(tmp_path, mutation, request):
    state = make_state(tmp_path)
    claimed = deepcopy(state.root)
    if mutation == "subset":
        claimed = claimed["source"]
    elif mutation == "omit_meta":
        del claimed["source"]["outline"]["_meta"]
    elif mutation == "bool_to_int":
        claimed["source"]["outline"]["_meta"]["opaque"][1] = 0
    else:
        claimed["source"]["outline"]["_meta"]["tail"] = ""
    record_difference(request, "logical_source", state.root, claimed)
    with pytest.raises(ValueError) as caught:
        attach(state, logical_source=claimed)
    assert_source_difference(caught.value, request, "logical_source", state.root, claimed)


@pytest.mark.parametrize("mutation", ["prefix", "bool_offset", "duplicate_key", "nonfinite"])
def test_full_document_offsets_and_strict_json_cannot_be_forged(tmp_path, mutation):
    state = make_state(tmp_path)
    if mutation == "prefix":
        state.request.user_prompt = "story flag\n" + state.request.user_prompt
        with pytest.raises(ValueError):
            attach(state, packet_start=len("story flag\n"))
    elif mutation == "bool_offset":
        with pytest.raises(ValueError):
            attach(state, packet_start=False)
    else:
        injection = '"source_sha256":"forged",' if mutation == "duplicate_key" else '"extra":NaN,'
        state.request.user_prompt = "{" + injection + state.request.user_prompt[1:]
        with pytest.raises(ValueError):
            attach(state)


@pytest.mark.parametrize("field", [
    "project_id", "workflow_run_id", "system_prompt", "user_prompt", "max_tokens_override",
    "temperature_override", "tools", "tool_choice",
])
def test_receipt_cannot_survive_scope_wire_output_or_tool_changes(tmp_path, field):
    state = make_state(tmp_path)
    if field == "tool_choice":
        state.request.tools = [{"type": "function", "function": {"name": "fixed_tool"}}]
        state.request.tool_choice = "none"
    attach(state)
    original_project = state.request.project_id
    values = {
        "project_id": uuid4(), "workflow_run_id": uuid4(),
        "system_prompt": state.request.system_prompt + " changed",
        "user_prompt": state.request.user_prompt + " ", "max_tokens_override": 4095,
        "temperature_override": .3, "tools": [{"type": "function", "function": {"name": "other"}}],
        "tool_choice": "auto",
    }
    state.request = state.request.model_copy(update={field: values[field]})
    with llm.bind_project_prompt_audit(original_project, reason="same captured receipt"):
        with pytest.raises(ValueError):
            limit(state)


@pytest.mark.parametrize("field,value", [
    ("wire_user_sha256", "0" * 64), ("effective_input_sha256", "0" * 64),
    ("logical_source_sha256", "0" * 64), ("source_complete", False),
    ("roundtrip_verified", False), ("grade_asserted", True), ("packet_start", True),
])
def test_receipt_fields_are_recomputed_not_trusted(tmp_path, field, value):
    state = make_state(tmp_path)
    attach(state)[field] = value
    with llm.bind_project_prompt_audit(state.request.project_id, reason="receipt mismatch"):
        with pytest.raises(ValueError):
            limit(state)


@pytest.mark.parametrize("mode", ["missing", "foreign", "forged_argument", "expired"])
def test_native_project_audit_must_be_current_for_complete_reader_source(tmp_path, mode):
    state = make_state(tmp_path)
    attach(state)
    state.request.metadata.update(project_audited=True, source_complete=True)
    if mode == "expired":
        with llm.bind_project_prompt_audit(state.request.project_id, reason="expired"):
            pass
    audit_context = (
        llm.bind_project_prompt_audit(uuid4(), reason="foreign") if mode == "foreign"
        else nullcontext()
    )
    with audit_context, pytest.raises(ValueError, match="project_audit_required"):
        audit = (state.request.project_id, "caller flag") if mode == "forged_argument" else None
        limit(state, audit=audit)


@pytest.mark.parametrize("field,value", [
    ("model", "openai/other"), ("api_base", "https://unverified.invalid/v1"),
    ("temperature", .5), ("max_tokens", 2048), ("reasoning_effort", "low"), ("stream", False),
])
def test_each_physical_retention_rechecks_actual_role_without_reusing_admission(
    tmp_path, field, value,
):
    state = make_state(tmp_path, "roles")
    attach(state)
    with llm.bind_project_prompt_audit(state.request.project_id, reason="actual retry role"):
        assert limit(state) == 1_000_000
        changed = state.role.model_copy(update={field: value})
        with pytest.raises(ValueError):
            persist(state, role=changed)
    assert not list(tmp_path.rglob("*.json"))


@pytest.mark.parametrize("boundary", ["route_context", "finite_context", "output_reserve"])
def test_one_million_retention_never_raises_actual_model_or_finite_capacity(tmp_path, boundary):
    state = make_state(tmp_path, "roles" if boundary == "output_reserve" else "review")
    if boundary == "route_context":
        state.role = state.role.model_copy(update={"api_base": "https://unverified.invalid/v1"})
    if boundary == "output_reserve":
        state.role = state.role.model_copy(update={"max_tokens": 32768})
    attach(state)
    cap = 65536 if boundary == "finite_context" else 90000 if boundary == "output_reserve" else None
    finite = (
        bind_run("offline-capacity-only", project_id=state.request.project_id, stage="reader",
                 capacity_limit=cap) if cap is not None else nullcontext()
    )
    with finite, llm.bind_project_prompt_audit(state.request.project_id, reason="actual capacity"):
        with pytest.raises(ModelRequestCapacityError) as caught:
            limit(state)
    assert caught.value.receipt["budget_passed"] is False
    assert caught.value.receipt["provider_usage_claimed"] is False
    assert caught.value.receipt["truncated"] is False


@pytest.mark.parametrize("mode", ["disabled", "nonlocal", "oversized_tools"])
def test_storage_and_complete_physical_wire_bounds_remain_mandatory(tmp_path, mode):
    state = make_state(tmp_path)
    if mode == "oversized_tools":
        state.request.tools = [{"type": "function", "function": {
            "name": "oversized", "description": "q" * 1_000_001,
        }}]
    attach(state)
    if mode == "disabled":
        state.settings.artifact_store.retain_prompt_payloads = False
    if mode == "nonlocal":
        state.settings.artifact_store.mode = "s3"
    with llm.bind_project_prompt_audit(state.request.project_id, reason="physical bound"):
        with pytest.raises(ValueError):
            limit(state)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["missing_receipt", "changed_role"])
async def test_physical_attempt_rechecks_before_native_reserve_or_wire(tmp_path, monkeypatch, mode):
    state = make_state(tmp_path)
    if mode == "changed_role":
        attach(state)
        state.role = state.role.model_copy(update={"api_base": "https://changed.invalid/v1"})
    # Isolate this repeat check after the separate native ownership boundary;
    # neither a successful operation nor any permission/approval row is forged.
    monkeypatch.setattr(llm, "_execution_boundary", AsyncMock())
    reserve = AsyncMock(side_effect=AssertionError("retention must refuse before reserve"))
    provider = AsyncMock(side_effect=AssertionError("retention must refuse before wire"))
    monkeypatch.setattr(llm, "_call_litellm", provider)
    call = (
        SimpleNamespace(settings=state.settings, ledger=SimpleNamespace(reserve=reserve))
        if mode == "changed_role" else None
    )
    token = llm._execution_call_var.set(call)
    try:
        with llm.bind_project_prompt_audit(state.request.project_id, reason="physical attempt"):
            with pytest.raises(ValueError, match="source_complete_retention:"):
                await llm._paid_transport_attempt_impl(state.request, state.role)
    finally:
        llm._execution_call_var.reset(token)
    reserve.assert_not_awaited()
    provider.assert_not_awaited()
