"""Pure historical literal-counter reconstruction, never dispatch or paid proof.

The caller must independently prove physical custody, returns and settlement.
Only the transport estimate and its derived request/contract hashes may differ;
the calibrated whole-request capacity is always preserved exactly.
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
import json
import math
import re

from bestseller.services.llm_capacity import measure_request_capacity
from bestseller.services.planning_review_literal_transport import (
    decode_literal_planning_review_transport,
)
from bestseller.services.prompt_compiler import estimate_legacy_prompt_tokens
from bestseller.services.prompt_source_policy import content_hash

SCHEMA = "planning-literal-capacity-audit-equivalence.v1"
_PREFIX = "planning_literal_capacity_binding:"
_CONTRACT_KEYS = frozenset({
    "schema", "wire_version", "grade_schema", "hard_admission_policy",
    "graded_input_hash", "candidate_hash", "model", "effective_input_sha256",
    "source_transport", "output_budget", "capacity", "request_sha256",
})
_WIRE_VERSIONS = {
    "planning-draft-admission.v4": "v4-literal-full-source.v1",
    "planning-draft-admission.v5": "v5-literal-full-source.v1",
}
_INPUT_RECEIPT = "planning_admission_input_receipt"
_WIRE_HASH = "planning_admission_wire_contract_sha256"


def _need(condition: object, reason: str) -> None:
    if not condition:
        raise ValueError(_PREFIX + reason)


def _copy_json(value: object, active: set[int] | None = None) -> object:
    """Copy JSON values without coercion, including Mapping API inputs."""
    active = set() if active is None else active
    if isinstance(value, Mapping) or type(value) is list:
        _need(id(value) not in active, "cyclic_json")
        active.add(id(value))
        try:
            if isinstance(value, Mapping):
                result = {}
                for key, child in value.items():
                    _need(type(key) is str and key not in result, "object_keys")
                    result[key] = _copy_json(child, active)
                return result
            return [_copy_json(child, active) for child in value]
        finally:
            active.remove(id(value))
    _need(value is None or type(value) in (str, int, float, bool), "non_json_value")
    _need(type(value) is not float or math.isfinite(value), "nonfinite_json")
    return value


def _json(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"), allow_nan=False)
    encoded.encode("utf-8")  # Reject invalid Unicode before calling content_hash.
    return encoded


def _stable(request: dict) -> dict:
    stable = deepcopy(request)
    stable["metadata"].pop(_INPUT_RECEIPT, None)
    stable["metadata"].pop(_WIRE_HASH, None)
    return stable


def _current_source(request: dict, contract: dict) -> None:
    metadata = request["metadata"]
    transport = contract["source_transport"]
    _need(type(transport) is dict
          and _json(metadata.get("source_transport")) == _json(transport),
          "current_source_transport")
    _need(metadata.get(_WIRE_HASH) == content_hash(contract), "current_contract_hash")
    _need(contract["request_sha256"] == content_hash(_stable(request)), "current_request_hash")
    data = decode_literal_planning_review_transport(
        system=request["system_prompt"], user=request["user_prompt"], receipt=transport,
        max_chars=transport["max_chars"],
        max_estimated_tokens=transport["max_estimated_tokens"],
    )
    source = data.get("input")
    _need(type(source) is dict and data.get("graded_input_hash") == contract["graded_input_hash"]
          and content_hash(source) == contract["graded_input_hash"]
          and content_hash(source.get("candidate_payload")) == contract["candidate_hash"]
          and source.get("schema") == contract["grade_schema"]
          and source.get("hard_admission_policy") == contract["hard_admission_policy"]
          and source.get("project_id") == request.get("project_id")
          and source.get("workflow_run_id") == request.get("workflow_run_id"),
          "current_source_binding")


def _current_capacity(request: dict, contract: dict) -> None:
    budget, capacity = contract["output_budget"], contract["capacity"]
    _need(type(budget) is dict and set(budget) == {
        "schema", "model", "answer_tokens", "role_output_tokens",
        "total_output_tokens", "reasoning_effort",
    } and budget["schema"] == "planning-reasoning-output-budget.v1"
          and budget["model"] == contract["model"]
          and type(budget["reasoning_effort"]) is str
          and all(type(budget[key]) is int and budget[key] > 0 for key in (
              "answer_tokens", "role_output_tokens", "total_output_tokens"))
          and budget["answer_tokens"] <= budget["total_output_tokens"] <= budget["role_output_tokens"]
          and type(request.get("max_tokens_override")) is int
          and request["max_tokens_override"] == budget["total_output_tokens"]
          and _json(request["metadata"].get("planning_output_budget")) == _json(budget),
          "current_output_budget")
    messages = [{"role": "system", "content": request["system_prompt"]},
                {"role": "user", "content": request["user_prompt"]}]
    for item in (capacity, request["metadata"].get(_INPUT_RECEIPT, capacity)):
        _need(type(item) is dict and type(item.get("total_budget")) is int
              and item["total_budget"] > 0 and item.get("budget_passed") is True,
              "current_capacity")
        measured = measure_request_capacity(
            messages, tools=request.get("tools"), tool_choice=request.get("tool_choice"),
            output_tokens=budget["total_output_tokens"], n_candidates=1,
            total_budget=item["total_budget"],
        )
        _need(_json(item) == _json(measured), "current_capacity_changed")
    _need(request["metadata"].get(_INPUT_RECEIPT, capacity)["total_budget"]
          <= capacity["total_budget"], "current_capacity_bound")


def _prove(request: dict, contract: dict, retained: dict) -> dict | None:
    _need(all(type(item) is dict for item in (request, contract, retained)), "mapping_required")
    _need(set(contract) == _CONTRACT_KEYS
          and contract["schema"] == "planning-admission-literal-request.v1"
          and type(contract["grade_schema"]) is str
          and contract["grade_schema"] in _WIRE_VERSIONS
          and contract["wire_version"] == _WIRE_VERSIONS.get(contract["grade_schema"])
          and type(contract["model"]) is str and bool(contract["model"]), "current_contract")
    for key in ("hard_admission_policy", "graded_input_hash", "candidate_hash",
                "effective_input_sha256", "request_sha256"):
        _need(type(contract[key]) is str
              and re.fullmatch(r"[0-9a-f]{64}", contract[key]) is not None, "current_hash")
    _need(type(request.get("metadata")) is dict
          and request.get("logical_role") == "critic"
          and request.get("prompt_template") == "planning_draft_admission"
          and request.get("prompt_version") == contract["wire_version"]
          and request.get("messages_override") is None
          and request.get("cache_system", False) is False
          and all(type(request.get(key)) is str and bool(request[key])
                  for key in ("system_prompt", "user_prompt")), "current_request")
    _current_source(request, contract)
    _current_capacity(request, contract)
    if _json(retained) == _json(contract):
        return None

    old_count = (estimate_legacy_prompt_tokens(request["system_prompt"])
                 + estimate_legacy_prompt_tokens(request["user_prompt"]))
    prompt_capacity = contract["source_transport"]["prompt_capacity"]
    _need(old_count != prompt_capacity["estimated_tokens"]
          and old_count <= prompt_capacity["max_estimated_tokens"], "historical_counter")
    historical_request = deepcopy(request)
    historical_request["metadata"]["source_transport"]["prompt_capacity"]["estimated_tokens"] = old_count
    historical_contract = deepcopy(contract)
    historical_contract["source_transport"]["prompt_capacity"]["estimated_tokens"] = old_count
    historical_contract["request_sha256"] = content_hash(_stable(historical_request))
    # No normalization or Python equality: True, 1 and 1.0 are distinct here.
    _need(_json(historical_contract) == _json(retained), "retained_contract_changed")
    historical_request["metadata"][_WIRE_HASH] = content_hash(historical_contract)
    return {
        "request": historical_request,
        "contract": historical_contract,
        "receipt": {
            "schema": SCHEMA,
            "receipt_kind": "derived_validation",
            "current_request_sha256": contract["request_sha256"],
            "old_request_sha256": historical_contract["request_sha256"],
            "current_contract_sha256": content_hash(contract),
            "old_contract_sha256": content_hash(historical_contract),
            "current_estimated_tokens": prompt_capacity["estimated_tokens"],
            "old_estimated_tokens": old_count,
            "grade_asserted": False,
            "credit_granted": False,
            "new_dispatch_capacity_granted": False,
        },
    }


def prove_historical_literal_capacity(
    *, current_request: Mapping, current_contract: Mapping, retained_contract: Mapping,
) -> dict | None:
    """Return independently rebuilt historical JSON, or None for exact current.

    All inputs must be finite JSON mappings. Every incompatibility raises
    ValueError with the static ``planning_literal_capacity_binding:`` prefix.
    The two unhashed metadata derivatives are excluded only from request hashing;
    the input receipt and all unrelated metadata survive reconstruction unchanged.
    """
    try:
        values = tuple(_copy_json(item) for item in (
            current_request, current_contract, retained_contract))
        for value in values:
            _json(value)
        return _prove(*values)
    except (ValueError, TypeError, KeyError, OverflowError, RecursionError, UnicodeError) as error:
        if isinstance(error, ValueError) and str(error).startswith(_PREFIX):
            raise
        raise ValueError(_PREFIX + "invalid_input") from None


__all__ = ["SCHEMA", "prove_historical_literal_capacity"]
