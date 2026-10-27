"""Offline literal-counter reconstruction and strict, complete tamper rejection."""

from collections.abc import Mapping
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import socket
from types import MappingProxyType

import pytest

from bestseller.services.llm_capacity import measure_request_capacity
from bestseller.services.planning_literal_capacity_binding import (
    SCHEMA,
    prove_historical_literal_capacity,
)
from bestseller.services.planning_review_literal_transport import (
    prepare_literal_planning_review_transport,
)
from bestseller.services.prompt_compiler import estimate_legacy_prompt_tokens

pytestmark = pytest.mark.unit
REPORT = (Path(__file__).resolve().parents[2]
          / "reports/framework-recovery-2026-10-21/phase8t-literal-admission-input-comparison.json")
PREFIX = "^planning_literal_capacity_binding:"
WIRE_HASH = "planning_admission_wire_contract_sha256"
INPUT_RECEIPT = "planning_admission_input_receipt"


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False)


def sha(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def stable(request):
    result = deepcopy(request)
    result["metadata"].pop(INPUT_RECEIPT, None)
    result["metadata"].pop(WIRE_HASH, None)
    return result


def resign(request, contract):
    contract["request_sha256"] = sha(stable(request))
    request["metadata"][WIRE_HASH] = sha(contract)


def pair():
    """An unrelated source/model/native schema, independent of the retained book."""
    model = "fixture/independent-model"
    source = {
        "schema": "planning-draft-admission.v4", "project_id": "different-project",
        "workflow_run_id": "different-workflow", "hard_admission_policy": sha({"policy": 4}),
        "candidate_payload": {"chapters": [{"title": "灯塔与潮汐", "enabled": False,
                                             "source": "海" * 40 + " lighthouse " * 60}]},
        "unrecognized_late_field": {"$ref": "#/literal/authored", "allowed": False},
    }
    system = "Inspect the entire literal source. Preserve all authored values."
    data = {"input": source, "graded_input_hash": sha(source)}
    user, transport = prepare_literal_planning_review_transport(
        system, canonical(data), data=data, data_block=canonical(data),
        max_chars=1_000_000, max_estimated_tokens=400_000,
    )
    budget = {
        "schema": "planning-reasoning-output-budget.v1", "model": model,
        "answer_tokens": 6000, "role_output_tokens": 32768,
        "total_output_tokens": 32768, "reasoning_effort": "low",
    }
    tools = [{"type": "function", "function": {"name": "submit_planning_admission",
        "parameters": {"type": "object", "properties": {"accepted": {"type": "boolean"}}}}}]
    choice = {"type": "function", "function": {"name": "submit_planning_admission"}}
    capacity = measure_request_capacity(
        [{"role": "system", "content": system}, {"role": "user", "content": user}],
        tools=tools, tool_choice=choice, output_tokens=32768, total_budget=1_000_000,
    )
    request = {
        "logical_role": "critic", "prompt_template": "planning_draft_admission",
        "prompt_version": "v4-literal-full-source.v1", "system_prompt": system,
        "user_prompt": user, "project_id": source["project_id"],
        "workflow_run_id": source["workflow_run_id"], "max_tokens_override": 32768,
        "messages_override": None, "cache_system": False, "tools": tools,
        "tool_choice": choice, "fallback_response": "{}",
        "metadata": {"source_transport": deepcopy(transport), INPUT_RECEIPT: deepcopy(capacity),
                     "planning_output_budget": deepcopy(budget),
                     "unrecognized_late_field": {"narrative": [False, 1, "unchanged"]}},
    }
    contract = {
        "schema": "planning-admission-literal-request.v1",
        "wire_version": request["prompt_version"], "grade_schema": source["schema"],
        "hard_admission_policy": source["hard_admission_policy"],
        "graded_input_hash": sha(source), "candidate_hash": sha(source["candidate_payload"]),
        "model": model, "effective_input_sha256": sha({"physical": "caller-owned"}),
        "source_transport": transport, "output_budget": budget, "capacity": capacity,
    }
    resign(request, contract)
    historical_request, retained = deepcopy(request), deepcopy(contract)
    old_count = estimate_legacy_prompt_tokens(system) + estimate_legacy_prompt_tokens(user)
    historical_request["metadata"]["source_transport"]["prompt_capacity"]["estimated_tokens"] = old_count
    retained["source_transport"]["prompt_capacity"]["estimated_tokens"] = old_count
    resign(historical_request, retained)
    return request, contract, retained


def prove(request, contract, retained):
    return prove_historical_literal_capacity(
        current_request=request, current_contract=contract, retained_contract=retained)


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("Pure literal binding must not access PG, providers or the network")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)


def assert_proof(request, contract, retained, result):
    assert set(result) == {"request", "contract", "receipt"}
    historical = result["request"]
    assert canonical(result["contract"]) == canonical(retained)
    assert sha(stable(historical)) == retained["request_sha256"]
    assert historical["metadata"][WIRE_HASH] == sha(retained)
    restored = deepcopy(historical)
    restored["metadata"]["source_transport"]["prompt_capacity"]["estimated_tokens"] = (
        request["metadata"]["source_transport"]["prompt_capacity"]["estimated_tokens"])
    restored["metadata"][WIRE_HASH] = request["metadata"][WIRE_HASH]
    assert canonical(restored) == canonical(request)
    assert canonical(result["contract"]["capacity"]) == canonical(contract["capacity"])
    receipt = result["receipt"]
    assert receipt == {
        "schema": SCHEMA, "receipt_kind": "derived_validation",
        "current_request_sha256": contract["request_sha256"],
        "old_request_sha256": retained["request_sha256"],
        "current_contract_sha256": sha(contract), "old_contract_sha256": sha(retained),
        "current_estimated_tokens": contract["source_transport"]["prompt_capacity"]["estimated_tokens"],
        "old_estimated_tokens": retained["source_transport"]["prompt_capacity"]["estimated_tokens"],
        "grade_asserted": False, "credit_granted": False, "new_dispatch_capacity_granted": False,
    }
    assert all(type(v) in (str, int, bool) for v in receipt.values())


def test_full_actual_retained_pair_and_exact_historical_hash():
    before = REPORT.read_bytes()
    evidence = json.loads(before)["literal_admission_comparison"]
    request, contract, retained = (evidence[key] for key in (
        "current_request", "current_contract", "old_contract"))
    originals = canonical([request, contract, retained])
    assert evidence["physical_effective_payload_equal"] is True
    assert canonical(contract["capacity"]) == canonical(retained["capacity"])
    result = prove(request, contract, retained)
    assert_proof(request, contract, retained, result)
    assert result["receipt"]["current_estimated_tokens"] == 195606
    assert result["receipt"]["old_estimated_tokens"] == 177721
    assert result["receipt"]["old_contract_sha256"] == (
        "30e16353b27b15d811bc106985ab5037c5f4db7923c3224946674742ff006034")
    assert result["receipt"]["current_contract_sha256"] == (
        "77ec97c01b833d28e0cfd650575285f07f2ac203bf4a159e39985c21d38a7bc7")
    assert canonical([request, contract, retained]) == originals
    assert REPORT.read_bytes() == before


def test_unrelated_fixture_and_deep_json_copy():
    request, contract, retained = pair()
    before = canonical([request, contract, retained])
    result = prove(MappingProxyType(request), MappingProxyType(contract), MappingProxyType(retained))
    assert_proof(request, contract, retained, result)
    assert canonical([request, contract, retained]) == before
    result["request"]["metadata"]["unrecognized_late_field"]["narrative"].append("changed")
    result["contract"]["capacity"]["budget_passed"] = False
    assert canonical([request, contract, retained]) == before


def test_exact_current_returns_none_after_validation():
    request, contract, _ = pair()
    assert prove(request, contract, dict(reversed(list(contract.items())))) is None
    request["metadata"]["source_transport"]["literal_values"] = 1
    with pytest.raises(ValueError, match=PREFIX):
        prove(request, contract, contract)


def test_unknown_grade_schema_cannot_match_null_wire_version():
    request, contract, _ = pair()
    contract["grade_schema"] = "unknown.v1"
    contract["wire_version"] = None
    request["prompt_version"] = None
    resign(request, contract)
    with pytest.raises(ValueError, match=PREFIX):
        prove(request, contract, contract)


@pytest.mark.parametrize(("target", "path", "value"), [
    (0, ("system_prompt",), "edited source instructions"),
    (0, ("user_prompt",), "edited complete source and narrative"),
    (0, ("max_tokens_override",), 32769),
    (0, ("max_tokens_override",), True),
    (0, ("tools", 0, "function", "parameters", "properties", "extra"), {"type": "string"}),
    (0, ("metadata", WIRE_HASH), "0" * 64),
    (0, ("metadata", "source_transport", "source_complete"), False),
    (0, ("metadata", "unrecognized_late_field", "narrative"), [True, 1, "unchanged"]),
    (1, ("request_sha256",), "0" * 64),
    (1, ("model",), "other/model"),
    (1, ("source_transport", "prompt_capacity", "estimated_tokens"), 1),
    (1, ("capacity", "total_budget"), 999999),
    (2, ("request_sha256",), "0" * 64),
    (2, ("model",), "other/model"),
    (2, ("schema",), "other-schema"),
    (2, ("candidate_hash",), "0" * 64),
    (2, ("effective_input_sha256",), "0" * 64),
    (2, ("output_budget", "total_output_tokens"), 32769),
    (2, ("source_transport", "prompt_capacity", "estimated_tokens"), 123),
    (2, ("source_transport", "prompt_capacity", "max_estimated_tokens"), 400001),
    (2, ("source_transport", "max_chars"), 999999),
    (2, ("source_transport", "references_count"), False),
    (2, ("source_transport", "literal_values"), 1),
    (2, ("source_transport", "prompt_capacity", "truncated"), 0),
    (2, ("capacity", "n_candidates"), True),
    (2, ("capacity", "n_candidates"), 1.0),
    (2, ("capacity", "legacy_estimated_input_tokens"), 1),
    (2, ("capacity", "schema"), "legacy-whole-capacity.v1"),
    (2, ("extra_late_field",), {"narrative": "must not be ignored"}),
    (2, ("source_transport", "extra_late_field"), False),
])
def test_tampering_is_rejected_without_mutating_inputs(target, path, value):
    values = list(pair())
    parent = values[target]
    for key in path[:-1]:
        parent = parent[key]
    parent[path[-1]] = value
    before = canonical(values)
    with pytest.raises(ValueError, match=PREFIX):
        prove(*values)
    assert canonical(values) == before


@pytest.mark.parametrize("kind", ["source", "output", "capacity_bool", "schema", "extra", "late_metadata"])
def test_resigning_current_input_cannot_hide_changes(kind):
    request, contract, retained = pair()
    if kind == "source":
        request["user_prompt"] = request["user_prompt"].replace("灯塔与潮汐", "被修改的文字")
    elif kind == "output":
        request["max_tokens_override"] = 32769
    elif kind == "capacity_bool":
        contract["capacity"]["n_candidates"] = True
    elif kind == "schema":
        contract["grade_schema"] = "unrecognized.v1"
    elif kind == "extra":
        contract["extra_late_field"] = "new narrative"
    else:
        request["metadata"]["unrecognized_late_field"]["narrative"].append("new narrative")
    resign(request, contract)
    with pytest.raises(ValueError, match=PREFIX):
        prove(request, contract, retained)


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf"), {"set"}, (1, 2), b"bytes", object()])
@pytest.mark.parametrize("target", [0, 1, 2])
def test_non_json_and_nonfinite_rejected(target, bad):
    values = list(pair())
    values[target]["invalid_late_value"] = bad
    with pytest.raises(ValueError, match=PREFIX):
        prove(*values)


def test_cycles_and_duplicate_mapping_keys_rejected():
    request, contract, retained = pair()
    request["metadata"]["cycle"] = request
    with pytest.raises(ValueError, match=PREFIX):
        prove(request, contract, retained)

    class DuplicateMapping(Mapping):
        def __iter__(self):
            return iter(("repeated", "repeated"))

        def __len__(self):
            return 2

        def __getitem__(self, key):
            return False

    request, contract, retained = pair()
    request["metadata"]["duplicates"] = DuplicateMapping()
    with pytest.raises(ValueError, match=PREFIX):
        prove(request, contract, retained)


@pytest.mark.parametrize("bad_fragment", ['"schema":"duplicate",', '"nonfinite":NaN,', '"overflow":1e999,'])
def test_strict_literal_packet_parsing(bad_fragment):
    request, contract, retained = pair()
    start = contract["source_transport"]["packet_start"]
    user = request["user_prompt"]
    request["user_prompt"] = user[:start + 1] + bad_fragment + user[start + 1:]
    for transport in (contract["source_transport"], request["metadata"]["source_transport"]):
        transport["packet_end"] += len(bad_fragment)
    resign(request, contract)
    with pytest.raises(ValueError, match=PREFIX):
        prove(request, contract, retained)
