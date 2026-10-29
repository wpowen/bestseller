"""Main-run RED for an opt-in capacity protocol; no provider or DB required.

Proposed API: new_reader_first_binding(text, *, protocol=...) and
_reader_config(protocol=...). Default/v1 remains byte-stable. Only explicit v2
has a 48,000-token outline ceiling; skeleton/output/repair limits stay fixed.
That ceiling never grants a send: native calibrated effective-request, actual
route, finite-run and output-reservation admission still apply independently.
"""

from contextlib import nullcontext
from copy import deepcopy
import hashlib
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from bestseller.services import llm
from bestseller.services import source_bound_planning_content as protocol
from bestseller.services.finite_book_budget import bind_run
from bestseller.services.llm_capacity import ModelRequestCapacityError, assert_effective_request_capacity
from bestseller.services.prompt_compiler import estimate_prompt_tokens
from bestseller.settings import LLMRoleSettings
from full_flow_q4_short.typed_leaf_differences import complete_leaf_differences, typed_summary

pytestmark = pytest.mark.unit
NEW_PROTOCOL = "reader-first-planning.v2"
V1_CONFIG_SHA = "70a0b7d70608ebef46de5d95e65d32e5cc3334c61f5a5f0fbb4104acc09d5396"


def source_document():
    """Four entire synthetic scopes; duplicated custody is intentional input."""
    chapters = [{"chapter_number": number, "chapter_goal": "A visible choice has a cost.",
                 "native_fact": str(number) + "x" * 10000,
                 "_meta": {"opaque": [None, False, 0, {"$ref": "literal source data"}]},
                 "scenes": [{"scene_number": 1, "purpose": {"story": "A complete synthetic action."},
                             "_meta": {"literal": "preserve scene metadata"}}]}
                for number in range(1, 5)]
    return {"selected_source": {"complete_foundation_content": {"cast_spec": {"unknown": None}}},
            "original_beat": {"raw": {"beats": deepcopy(chapters)}},
            "base_outline": {"chapters": deepcopy(chapters)}, "outline": {"chapters": chapters}}


def new_prompts(source):
    # Deliberately inside the test call, not a fixture: missing v2 is business
    # RED, never a setup failure or a monkeypatched permissive protocol.
    binding = protocol.new_reader_first_binding("fixed synthetic reader intent", protocol=NEW_PROTOCOL)
    system, user = protocol.reader_first_review_prompts(binding, source=source, checkpoint="outline")
    return binding, system, user


def role(*, endpoint="https://api.minimaxi.com/v1"):
    return LLMRoleSettings(model="openai/MiniMax-M3.1-Flash", api_base=endpoint,
                          temperature=.2, max_tokens=4096, timeout_seconds=60,
                          model_override=None, thinking_type="enabled", reasoning_effort="medium")


def request_for(binding, system, user):
    return llm.LLMCompletionRequest(
        project_id=uuid4(), workflow_run_id=uuid4(), logical_role="critic",
        prompt_template="planner_reader_first_outline_review", prompt_version="rf-outline-review-v1",
        system_prompt=system, user_prompt=user, fallback_response="{}",
        max_tokens_override=4096, temperature_override=.2,
        metadata={"reader_first_planning": deepcopy(binding), "checkpoint": "outline"},
    )


def test_current_capacity_refusal_exposes_exact_safe_operands_without_source():
    source = source_document()
    original = deepcopy(source)
    observed = []
    binding = protocol.new_reader_first_binding("fixed synthetic reader intent")
    # Observe the actual operand; never replace its count or the guard result.
    with pytest.MonkeyPatch.context() as patch:
        def observe(text):
            observed.append(text)
            return estimate_prompt_tokens(text)
        patch.setattr(protocol, "estimate_prompt_tokens", observe)
        with pytest.raises(ValueError, match="^reader_first_input_capacity_exceeded$") as stopped:
            protocol.reader_first_review_prompts(binding, source=source, checkpoint="outline")
    assert len(observed) == 1
    error = stopped.value
    assert error.code == str(error) == "reader_first_input_capacity_exceeded"
    assert error.differences == [{"field": "reader_first.outline_input_tokens",
                                  "expected": typed_summary(24000),
                                  "actual": typed_summary(estimate_prompt_tokens(observed[0]))}]
    assert error.receipt["full_wire"] == typed_summary(observed[0])
    assert error.receipt["operand_encoding"] == "json-utf8"
    assert error.receipt["full_wire_characters"] == len(observed[0])
    assert error.receipt["full_wire_utf8_bytes"] == len(observed[0].encode())
    assert "x" * 100 not in json.dumps(vars(error))
    assert source == original


def test_v1_binding_config_and_existing_prompt_bytes_remain_frozen():
    binding = protocol.new_reader_first_binding("fixed synthetic reader intent")
    assert binding["protocol"] == "reader-first-planning.v1"
    assert binding["config_sha256"] == V1_CONFIG_SHA
    source = {"outline": {"chapters": [{"chapter_number": 1, "chapter_goal": "A fixed synthetic choice."}]}}
    system, user = protocol.reader_first_review_prompts(binding, source=source, checkpoint="outline")
    assert hashlib.sha256(system.encode()).hexdigest() == "79e2344a4e35dbb477a4c3b6c7eb7537998287c77df064207bea10537f87a6ba"
    assert hashlib.sha256(user.encode()).hexdigest() == "4e7055d1a7c48462b577d3786cc4caffada466c4a07823cc8f680905ff2b134c"


def test_new_protocol_is_explicit_hash_bound_and_has_only_the_bounded_outline_increase():
    binding = protocol.new_reader_first_binding("fixed synthetic reader intent", protocol=NEW_PROTOCOL)
    config, digest = protocol._reader_config(protocol=NEW_PROTOCOL)
    assert binding["protocol"] == config["protocol"] == NEW_PROTOCOL
    assert binding["config_sha256"] == digest != V1_CONFIG_SHA
    assert config["outline_input_tokens"] == 48000
    assert config["skeleton_input_tokens"] == 12000
    assert config["review_output_tokens"] == 4096
    assert config["max_repairs_per_checkpoint"] == 1
    assert config["minimum_dimension_score"] == .75 and config["minimum_mean_score"] == .78
    assert config["thresholds_calibrated"] is False
    assert protocol.reader_first_binding({"reader_first_planning": binding}) == binding
    assert protocol.reader_first_instruction(binding).startswith("\n[reader-first-planning.v2:planning]\n")
    unknown = protocol.parse_reader_first_review({}, source={"outline": {}}, binding=binding, checkpoint="outline")
    assert unknown["protocol"] == NEW_PROTOCOL and unknown["status"] == "unknown"
    with pytest.raises(ValueError, match="config"):
        protocol.reader_first_binding({"reader_first_planning": {**binding, "config_sha256": V1_CONFIG_SHA}})
    with pytest.raises(ValueError, match="version"):
        protocol.new_reader_first_binding("fixed synthetic reader intent", protocol="reader-first-planning.latest")


def test_new_protocol_keeps_all_four_full_scopes_and_requires_fitting_actual_native_capacity():
    source = source_document()
    original = deepcopy(source)
    binding, system, user = new_prompts(source)
    assert 24000 < estimate_prompt_tokens(system + user) < 48000
    document = json.loads(user)
    assert document["source"] == original
    assert document["source_sha256"] == protocol._reader_hash(original)
    assert len(document["source"]["outline"]["chapters"]) == 4
    request = request_for(binding, system, user)
    payload = llm._effective_input_payload(request, role())
    before = deepcopy(payload)
    with bind_run("offline-capacity", project_id=request.project_id, stage="reader", capacity_limit=131072):
        receipt = assert_effective_request_capacity(payload)
    assert receipt["budget_passed"] is True and receipt["total_budget"] == 131072
    assert receipt["output_reserve_tokens"] == 4096 and receipt["safety_factor"] == 2.2
    assert receipt["provider_usage_claimed"] is False and receipt["truncated"] is False
    assert payload == before and source == original


def test_new_protocol_still_refuses_over_48000_without_source_truncation():
    source = source_document()
    source["outline"]["additional_original_fact"] = "z" * 100000
    original = deepcopy(source)
    binding = protocol.new_reader_first_binding("fixed synthetic reader intent", protocol=NEW_PROTOCOL)
    with pytest.raises(ValueError, match="^reader_first_input_capacity_exceeded$") as stopped:
        protocol.reader_first_review_prompts(binding, source=source, checkpoint="outline")
    assert stopped.value.receipt["maximum_input_tokens"] == 48000
    assert stopped.value.receipt["estimated_input_tokens"] > 48000
    assert source == original


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", ["route_context", "finite_context", "output_reserve"])
async def test_new_protocol_native_gateway_rejects_before_claim_reserve_or_send(boundary, monkeypatch):
    from support.database_safety import load_test_database_settings

    binding, system, user = new_prompts(source_document())
    request = request_for(binding, system, user)
    effective_role = role(endpoint="https://unverified.invalid/v1") if boundary == "route_context" else role()
    settings = load_test_database_settings().model_copy(deep=True)
    settings.llm.mock = False
    settings.llm.critic = effective_role
    monkeypatch.setattr(llm, "apply_runtime_llm_profile", lambda value: value)
    monkeypatch.setattr(llm, "_resolve_project_model_override", AsyncMock(return_value=None))
    claim = AsyncMock(side_effect=AssertionError("capacity must refuse before claim"))
    reserve = AsyncMock(side_effect=AssertionError("capacity must refuse before reserve"))
    provider = AsyncMock(side_effect=AssertionError("capacity must refuse before provider"))
    monkeypatch.setattr(llm.ExecutionLedger, "claim", claim)
    monkeypatch.setattr(llm.ExecutionLedger, "reserve", reserve)
    monkeypatch.setattr(llm, "_call_litellm", provider)
    payload = llm._effective_input_payload(request, effective_role)
    if boundary == "route_context":
        capacity_scope = nullcontext()
        limit = 65536
    else:
        baseline = assert_effective_request_capacity(payload)
        limit = 65536 if boundary == "finite_context" else baseline["reserved_input_tokens"] + 4095
        capacity_scope = bind_run("offline-capacity", project_id=request.project_id, stage="reader", capacity_limit=limit)
    original = request.model_dump(mode="json")
    # Pure gateway boundary: the read explicitly returns no project metadata,
    # so the configured role remains the capacity operand. No DB row, project
    # admission or model selection is claimed by this serialization fixture.
    metadata_read = AsyncMock(return_value=None)
    session = SimpleNamespace(bind=None, scalar=metadata_read)
    with capacity_scope, pytest.raises(ModelRequestCapacityError) as stopped:
        await llm.complete_text(session, settings, request)
    assert stopped.value.receipt["total_budget"] == limit
    assert stopped.value.receipt["reserved_total_tokens"] > limit
    assert stopped.value.receipt["output_reserve_tokens"] == 4096
    if boundary == "output_reserve":
        assert stopped.value.receipt["reserved_input_tokens"] <= limit
        assert stopped.value.receipt["reserved_total_tokens"] == limit + 1
    claim.assert_not_awaited()
    reserve.assert_not_awaited()
    provider.assert_not_awaited()
    metadata_read.assert_awaited_once()
    assert request.model_dump(mode="json") == original


def test_typed_diagnostics_include_every_nested_leaf_and_distinguish_unknown_null_and_bool():
    expected = {"chapters": [{"_meta": {"x/y": {"~leaf": None}}, "number": 1, "shape": {"0": False}}]}
    actual = {"chapters": [{"_meta": {"x/y": {"~leaf": False, "new": "opaque"}}, "number": True,
                             "shape": [False]}]}
    before = deepcopy((expected, actual))
    differences = complete_leaf_differences(expected, actual)
    by_path = {entry["field"]: entry for entry in differences}
    assert set(by_path) == {"/chapters/0/_meta/x~1y/~0leaf", "/chapters/0/_meta/x~1y/new",
                            "/chapters/0/number", "/chapters/0/shape"}
    assert by_path["/chapters/0/_meta/x~1y/~0leaf"]["expected"] == typed_summary(None)
    assert by_path["/chapters/0/_meta/x~1y/new"]["expected"] == {"type": "missing", "length": 0, "sha256": None}
    assert by_path["/chapters/0/number"]["actual"] == typed_summary(True)
    assert (expected, actual) == before
    assert "opaque" not in json.dumps(differences)
