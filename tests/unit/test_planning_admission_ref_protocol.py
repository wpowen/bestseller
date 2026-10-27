"""Exact v4 selectors and real SQLite gateway receipts; no PG or providers."""

from copy import deepcopy
import json
import socket
from uuid import uuid4

import pytest
from sqlalchemy import select
from test_planning_admission_native_entry import _logical_prompt, _physical_rows
from test_planning_draft_admission import grade_payload
from test_planning_draft_admission import harness as harness

from bestseller.infra.db.models import (
    ExecutionAttemptModel,
    ExecutionBudgetModel,
    ExecutionOperationModel,
    LlmRunModel,
)
from bestseller.services import llm
from bestseller.services import planning_admission_ref_protocol as refs
from bestseller.services import planning_draft_admission as admission
from bestseller.services.budget import bind_execution_scope
from bestseller.services.planning_admission_submission import (
    PlanningAdmissionSubmissionError,
    planning_admission_tool,
)
from bestseller.services.planning_output_budget import admission_output_budget
from bestseller.services.retained_planning_clauses import resolve_pointer

pytestmark = pytest.mark.unit
SCOPE = "audit:admission-fixture"


@pytest.fixture(autouse=True)
def no_io_dispatch(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("staged selector tests cannot use PG/network/providers")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)


def supplied():
    candidate = {
        "chapters": [
            {
                "chapter_number": 7,
                "goal": "Observe an unrelated glacier",
                "causal_contract": {
                    "fanqie_short_v2": {
                        "continuity_contract": {
                            "time_position": "After the first measurement",
                        }
                    }
                },
                "scenes": [
                    {
                        "entry_state": "First observation",
                        "unknown_authored": {
                            "中文.键/带~号": "§literal $ref — keep all text",
                            "unset": None,
                            "empty": "",
                            "flag": False,
                            "count": 0,
                        },
                    }
                ],
                "_meta": {"receipt": "opaque old receipt"},
            }
        ],
        "_meta": {"history": [None, {"raw": "old"}]},
    }
    result = {
        "schema": admission.SCHEMA,
        "hard_admission_policy": admission.HARD_ADMISSION_POLICY,
        "project_id": str(uuid4()),
        "workflow_run_id": str(uuid4()),
        "candidate_payload": candidate,
        "projection_audit_targets": admission.projection_audit_targets(candidate),
        "approved_creation_contracts": {"selected_clause": "Keep the selected boundary"},
        "approved_world_spec": {"rule/~中文": "Do not occupy two places at once", "unknown": None},
        "approved_cast_spec": {"people": [{"name": "Observer", "arc_trajectory": "Optional arc"}]},
        "approved_book_spec": {"logline": "Observe the glacier", "_meta": {"producer": "old"}},
        "untrusted_commercial_report": {"pass": False, "raw": "Preserve original negative"},
    }
    result["source_authority"] = admission._source_authority(result)
    result["source_authority_allowed_codes"] = refs._permissions(result["source_authority"])
    return result


def proposal(current):
    return {
        "draft_eligible": False,
        "hard_violations": [
            {
                "code": "SCENE_NOT_EXECUTABLE",
                "path": "chapters[0]",
                "evidence": "Reviewer claim is retained, not promoted to source content",
                "source_requirement": "The full real sources remain the review input",
                "source_refs": [
                    "/candidate_payload/chapters/0/causal_contract/fanqie_short_v2/continuity_contract/time_position"
                ],
            }
        ],
        "projection_checks": [
            {
                **target,
                "status": "conflict",
                "explanation": "Retained reviewer explanation",
                "related_violation_paths": ["chapters[0]"],
            }
            for target in current["projection_audit_targets"]
        ],
        "editorial_notes": ["Not author authority"],
    }


def completion(payload):
    raw = json.dumps(payload, ensure_ascii=False, indent=2)
    calls = [
        {
            "id": "new-prospective-submission",
            "type": "function",
            "function": {
                "name": "submit_planning_admission",
                "arguments": raw,
            },
        }
    ]
    return {
        "content": "",
        "finish_reason": "tool_calls",
        "function_call": None,
        "tool_calls": calls,
        "raw_message": {"role": "assistant", "content": "", "tool_calls": deepcopy(calls)},
    }


def parse(payload, current=None):
    current = current or refs.v4_input(supplied())
    return refs.parse_reference_submission(
        completion(payload), supplied=current, contract=refs.reference_contract(current)
    )


def test_complete_input_unknown_fields_and_raw_history_unchanged():
    original = supplied()
    before = deepcopy(original)
    current = refs.v4_input(original)
    assert original == before
    for key in original.keys() - {
        "schema",
        "hard_admission_policy",
        "source_authority",
        "source_authority_allowed_codes",
    }:
        assert current[key] == original[key]
    catalog = refs.reference_contract(current)["catalog"]
    key = "/candidate_payload/chapters/0/scenes/0/unknown_authored/中文.键~1带~0号"
    assert resolve_pointer(current, key) == "§literal $ref — keep all text"
    assert catalog[key]["role"] == "candidate_order"
    prefix = "/candidate_payload/chapters/0/scenes/0/unknown_authored/"
    assert catalog[prefix + "flag"]["value_sha256"] != catalog[prefix + "count"]["value_sha256"]
    assert catalog[prefix + "unset"]["allowed_codes"] == []
    assert catalog[prefix + "empty"]["allowed_codes"] == []
    assert current["untrusted_commercial_report"]["pass"] is False


def test_finite_enum_all_actual_rfc_pointers_and_default_v3_tool_unchanged():
    old = supplied()
    default = planning_admission_tool(
        admission._Grade.model_json_schema(), old["projection_audit_targets"]
    )
    current = refs.v4_input(old)
    tool, contract = refs.reference_tool(admission._Grade.model_json_schema(), current)
    field = tool["function"]["parameters"]["properties"]["hard_violations"]["items"]["properties"][
        "source_refs"
    ]
    assert field["uniqueItems"] is True
    enum = field["items"]["enum"]
    assert enum and all(pointer.startswith("/") for pointer in enum)
    for pointer in enum:
        assert not isinstance(resolve_pointer(current, pointer), (dict, list))
        assert contract["catalog"][pointer]["allowed_codes"]
    assert "/approved_world_spec/rule~1~0中文" in enum
    assert (
        "/candidate_payload/chapters/0/causal_contract/continuity_contract/time_position"
        not in enum
    )
    assert (
        planning_admission_tool(
            admission._Grade.model_json_schema(), old["projection_audit_targets"]
        )
        == default
    )
    assert (
        "enum"
        not in default["function"]["parameters"]["properties"]["hard_violations"]["items"][
            "properties"
        ]["source_refs"]["items"]
    )


def test_native_false_proposal_preserved_and_no_semantic_approval():
    current = refs.v4_input(supplied())
    payload = proposal(current)
    before = deepcopy(current)
    physical = completion(payload)
    raw, receipt = refs.parse_reference_submission(
        physical, supplied=current, contract=refs.reference_contract(current)
    )
    assert raw == physical["tool_calls"][0]["function"]["arguments"]
    assert receipt["parsed_payload"] == payload
    assert receipt["parsed_payload"]["draft_eligible"] is False
    assert receipt["refs_modified"] is False
    assert receipt["new_grade_or_adoption"] is False
    assert receipt["semantic_entailment"] == "not_evaluated"
    assert current == before


@pytest.mark.parametrize(
    "bad",
    [
        "candidate:chapters[0].causal_contract.fanqie_short_v2.continuity_contract.time_position",
        "/candidate_payload/chapters/0/causal_contract/continuity_contract/time_position",
        "/candidate_payload/chapters/0/causal_contract/fanqie_short_v2/continuity_contract",
        "/candidate_payload/chapters/00/goal",
        "/candidate_payload/chapters/2/goal",
        "/candidate_payload/chapters/0/scenes/0/unknown_authored/中文.键/带~号",
        "/input/candidate_payload/chapters/0/goal",
        "/untrusted_commercial_report/raw",
    ],
)
def test_no_alias_container_or_untrusted_ref_repair(bad):
    current = refs.v4_input(supplied())
    payload = proposal(current)
    payload["hard_violations"][0]["source_refs"] = [bad]
    with pytest.raises(PlanningAdmissionSubmissionError, match="reference_not_in_catalog"):
        parse(payload, current)


@pytest.mark.parametrize(
    "pointer",
    [
        "/candidate_payload/chapters/0/_meta/receipt",
        "/candidate_payload/chapters/0/scenes/0/unknown_authored/unset",
        "/candidate_payload/chapters/0/scenes/0/unknown_authored/empty",
        "/approved_book_spec/logline",
    ],
)
def test_provenance_unknown_and_advisory_not_hard_authority(pointer):
    current = refs.v4_input(supplied())
    payload = proposal(current)
    payload["hard_violations"][0]["source_refs"] = [pointer]
    with pytest.raises(PlanningAdmissionSubmissionError, match="reference_authority"):
        parse(payload, current)


@pytest.mark.parametrize(
    "change", ["duplicate", "code_permission", "projection_hash", "coverage", "eligible", "extra"]
)
def test_existing_strict_checks_retained(change):
    current = refs.v4_input(supplied())
    payload = proposal(current)
    if change == "duplicate":
        payload["hard_violations"][0]["source_refs"] *= 2
    elif change == "code_permission":
        payload["hard_violations"][0]["code"] = "EXPLICIT_CREATION_REQUIREMENT_CONFLICT"
    elif change == "projection_hash":
        payload["projection_checks"][0]["projection_hash"] = "0" * 64
    elif change == "coverage":
        payload["projection_checks"].pop()
    elif change == "eligible":
        payload["draft_eligible"] = True
    else:
        payload["unknown_grade"] = "Do not silently drop"
    with pytest.raises(PlanningAdmissionSubmissionError):
        parse(payload, current)


@pytest.mark.parametrize(
    "change", ["input", "catalog", "authority", "permissions", "bool_int", "targets"]
)
def test_stale_or_forged_catalog_rejected(change):
    current = refs.v4_input(supplied())
    contract = refs.reference_contract(current)
    physical = completion(proposal(current))
    if change == "input":
        current["approved_world_spec"]["rule/~中文"] += " Changed"
    elif change == "catalog":
        contract["catalog"]["/approved_book_spec/logline"]["allowed_codes"] = ["IDENTITY_CONFLICT"]
    elif change == "authority":
        current["source_authority"]["canonical_author"]["/approved_book_spec"] = ["/logline"]
    elif change == "permissions":
        current["source_authority_allowed_codes"]["candidate_order"] += ["IDENTITY_CONFLICT"]
    elif change == "bool_int":
        current["candidate_payload"]["chapters"][0]["scenes"][0]["unknown_authored"]["flag"] = 0
    else:
        current["projection_audit_targets"][0]["projection_hash"] = "0" * 64
    with pytest.raises(ValueError):
        refs.parse_reference_submission(physical, supplied=current, contract=contract)


def test_unicode_and_literal_marker_pointers_accepted_without_text_injection():
    current = refs.v4_input(supplied())
    payload = proposal(current)
    payload["hard_violations"][0]["source_refs"] = [
        "/candidate_payload/chapters/0/scenes/0/unknown_authored/中文.键~1带~0号",
        "/approved_world_spec/rule~1~0中文",
    ]
    _, receipt = parse(payload, current)
    assert receipt["parsed_payload"] == payload


def test_distinct_literal_and_nested_keys_never_alias_even_with_same_text():
    old = supplied()
    data = old["candidate_payload"]["chapters"][0]["scenes"][0]["unknown_authored"]
    data.update({"a.b": "same", "a": {"b": "same"}, "~1": "same", "/": "same"})
    old["projection_audit_targets"] = admission.projection_audit_targets(old["candidate_payload"])
    current = refs.v4_input(old)
    catalog = refs.reference_contract(current)["catalog"]
    prefix = "/candidate_payload/chapters/0/scenes/0/unknown_authored/"
    pointers = [prefix + suffix for suffix in ("a.b", "a/b", "~01", "~1")]
    assert len(set(pointers)) == 4
    assert all(
        pointer in catalog and resolve_pointer(current, pointer) == "same" for pointer in pointers
    )
    payload = proposal(current)
    payload["hard_violations"][0]["source_refs"] = pointers
    assert parse(payload, current)[1]["parsed_payload"] == payload


def test_protocol_consistent_result_is_data_not_a_grade_from_the_helper():
    current = refs.v4_input(supplied())
    payload = {
        "draft_eligible": True,
        "hard_violations": [],
        "editorial_notes": [],
        "projection_checks": [
            {**target, "status": "consistent", "explanation": "Complete review claim"}
            for target in current["projection_audit_targets"]
        ],
    }
    _, receipt = parse(payload, current)
    assert receipt["parsed_payload"] == payload
    assert receipt["new_grade_or_adoption"] is False


@pytest.mark.parametrize(
    "change", ["same", "new_id_only", "metadata_only", "same_artifact", "project"]
)
def test_cannot_unlock_same_candidate_using_policy_id_or_metadata(change):
    previous = supplied()
    current = deepcopy(previous)
    a, b = uuid4(), uuid4()
    if change == "metadata_only":
        current["candidate_payload"]["chapters"][0]["_meta"]["receipt"] += " new policy"
        current["projection_audit_targets"] = admission.projection_audit_targets(
            current["candidate_payload"]
        )
    elif change == "same_artifact":
        b = a
        current["candidate_payload"]["chapters"][0]["goal"] += " changed"
    elif change == "project":
        current["project_id"] = str(uuid4())
    if change == "same":
        current = refs.v4_input(current)
    with pytest.raises(ValueError):
        refs.require_changed_candidate(
            previous, current, previous_artifact_id=a, current_artifact_id=b
        )


def test_actual_candidate_leaf_change_report_does_not_grant_grade_or_reuse_slot():
    previous = supplied()
    current = deepcopy(previous)
    current["candidate_payload"]["chapters"][0]["goal"] += " with a new order"
    current["projection_audit_targets"] = admission.projection_audit_targets(
        current["candidate_payload"]
    )
    receipt = refs.require_changed_candidate(
        previous, current, previous_artifact_id=uuid4(), current_artifact_id=uuid4()
    )
    assert receipt["changed_candidate_refs"] == ["/candidate_payload/chapters/0/goal"]
    assert receipt["new_grade_or_adoption"] is False


def test_public_current_policy_status_accepts_v4_and_rejects_tampering():
    receipt = {"schema": refs.SCHEMA, "hard_admission_policy": refs.POLICY}
    assert admission.admission_receipt_status(receipt)["status"] == "current"
    assert (
        admission.admission_receipt_status(
            {**receipt, "hard_admission_policy": admission.HARD_ADMISSION_POLICY}
        )["status"]
        == "reassessment_required"
    )


@pytest.fixture
async def native_v4(harness, monkeypatch):
    h = harness
    engine = h.session_factory.kw["bind"]
    assert engine.dialect.name == "sqlite"
    h.settings.database.url = str(engine.url)
    assert str(h.settings.database.url).startswith("sqlite")
    h.settings.llm.critic = h.settings.llm.critic.model_copy(
        update={
            "model": "openai/MiniMax-M3.1-Flash",
            "max_tokens": 32768,
            "reasoning_effort": None,
        }
    )
    original = h.kwargs
    candidate = {
        "chapters": [
            {
                "chapter_number": number,
                "goal": f"Measure unrelated glacier sample {number}",
                "causal_contract": {
                    "fanqie_short_v2": {
                        "continuity_contract": {
                            "time_position": f"After sample {number - 1}",
                        }
                    }
                },
                "scenes": [
                    {
                        "scene_number": 1,
                        "action": f"Observer measures sample {number}",
                        "unknown_authored": {
                            "中文.键/~": "§literal $ref",
                            "unset": None,
                            "flag": False,
                        },
                    }
                ],
                "_meta": {"old_receipt": {"raw": None}},
            }
            for number in range(1, 5)
        ]
    }

    def kwargs():
        return {**original(), "candidate_payload": deepcopy(candidate)}

    h.kwargs = kwargs
    h.mode = "pass"
    h.modes = []
    h.wires = []
    h.roles = []
    h.native_returns = []

    async def transport(request, role):
        h.sent.append(request)
        h.roles.append(role)
        assert request.project_id == h.project.id and request.workflow_run_id == h.workflow.id
        assert request.prompt_template == admission.TEMPLATE
        assert request.prompt_version in {admission.SCHEMA, refs.SCHEMA}
        assert role.model == "openai/MiniMax-M3.1-Flash"
        assert role.reasoning_effort == "low"
        assert llm._effective_input_payload(request, role)["max_tokens"] == 32768
        logical = _logical_prompt(request)
        assert logical["input"]["candidate_payload"] == candidate
        wire = grade_payload(candidate)
        assert len(wire["projection_checks"]) == 8
        mode = h.modes.pop(0) if h.modes else h.mode
        if mode != "pass":
            pointer = (
                "/candidate_payload/chapters/2/causal_contract/fanqie_short_v2/"
                "continuity_contract/time_position"
            )
            pointer = {
                "missing_wrapper": (
                    "/candidate_payload/chapters/2/causal_contract/"
                    "continuity_contract/time_position"
                ),
                "dot_alias": (
                    "candidate:chapters[2].causal_contract.fanqie_short_v2."
                    "continuity_contract.time_position"
                ),
                "container": "/candidate_payload/chapters/2/causal_contract/fanqie_short_v2",
            }.get(mode, pointer)
            wire.update(
                draft_eligible=False,
                hard_violations=[
                    {
                        "code": "SCENE_NOT_EXECUTABLE",
                        "path": "chapters[3].scenes[0]",
                        "evidence": "Fixture semantic claim, not model acceptance evidence",
                        "source_requirement": "The exact earlier planned order",
                        "source_refs": [pointer, "/approved_world_spec/requirement"],
                    }
                ],
            )
            for check in wire["projection_checks"]:
                check.update(status="conflict", related_violation_paths=["chapters[3].scenes[0]"])
        raw = "\n" + json.dumps(wire, ensure_ascii=False, indent=2) + " \n"
        calls = [
            {
                "id": f"offline-selector-{len(h.sent)}",
                "type": "function",
                "function": {
                    "name": "submit_planning_admission",
                    "arguments": raw,
                },
            }
        ]
        message = {
            "role": "assistant",
            "content": None,
            "tool_calls": deepcopy(calls),
            "reasoning_content": "Offline transport reasoning is retained",
            "unknown_provider_tree": {"kept": None, "literal": "§raw $ref"},
        }
        h.wires.append(deepcopy(wire))
        h.native_returns.append((deepcopy(calls), deepcopy(message)))
        return "", 10, 5, "tool_calls", calls, message

    monkeypatch.setattr(llm, "_call_litellm", transport)
    return h


async def assess_native(h, state, *, schema=refs.SCHEMA, rollback=False, persist=None):
    async with h.session_factory() as session:

        async def save():
            await session.commit()
            if persist is not None:
                await persist()

        with bind_execution_scope(SCOPE):
            receipt = await admission.assess_planning_draft_admission(
                h.wrap(session),
                h.settings,
                **h.kwargs(),
                commercial_report=h.commercial,
                admission_schema=schema,
                protocol_state=state,
                persist_protocol=None if rollback else save,
            )
        await session.rollback() if rollback else await session.commit()
        return receipt


async def assert_paid_native(h, receipt, slot=-1):
    run, root, attempt, transport, budget = await _physical_rows(h, receipt)
    completion = receipt["protocol_attempts"][slot]["completion"]
    assert completion["tool_calls"] == h.native_returns[slot][0]
    assert completion["raw_message"] == h.native_returns[slot][1]
    assert run.project_id == h.project.id and run.workflow_run_id == h.workflow.id
    assert run.prompt_template == admission.TEMPLATE and run.prompt_version == refs.SCHEMA
    assert run.input_tokens == 10 and run.output_tokens == 5
    assert root.status == "completed" and transport.status == "transport_result"
    assert attempt.status == "completed" and attempt.actual_tokens == 15
    assert attempt.scope_id == root.scope_id == transport.scope_id == SCOPE
    assert transport.result_json["input_hash"] == run.prompt_hash == receipt["effective_input_hash"]
    assert transport.result_json["operation_key"] == root.key
    for saved in (root.result_json, transport.result_json["result"]):
        assert saved["content"] == completion["content"] == ""
        assert saved["finish_reason"] == completion["finish_reason"] == "tool_calls"
        assert saved["tool_calls"] == completion["tool_calls"]
        assert saved["raw_message"] == completion["raw_message"]
        assert saved["tool_calls"][0]["function"]["arguments"] == receipt["raw_grade"]
        assert (
            saved["raw_message"]["reasoning_content"] == "Offline transport reasoning is retained"
        )
    return run, root, attempt, transport, budget


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["pass", "reject"])
async def test_v4_actual_gateway_pass_fail_full_native_and_exact_read_proof(native_v4, mode):
    h = native_v4
    h.mode = mode
    before = deepcopy((h.kwargs()["candidate_payload"], h.project.metadata_json, h.commercial))
    state = {}
    receipt = await assess_native(h, state)
    assert receipt["schema"] == refs.SCHEMA and receipt["hard_admission_policy"] == refs.POLICY
    assert receipt["draft_eligible"] is (mode == "pass")
    assert receipt["submission_receipt"]["parsed_payload"] == h.wires[0]
    assert receipt["submission_receipt"]["raw_arguments"] == receipt["raw_grade"]
    assert receipt["submission_receipt"]["new_grade_or_adoption"] is False
    assert receipt["commercial_report"]["pass"] is False
    logical = _logical_prompt(h.sent[0])
    current = logical["input"]
    expected_tool, contract = refs.reference_tool(admission._Grade.model_json_schema(), current)
    assert h.sent[0].tools == [expected_tool]
    assert h.sent[0].tool_choice == {
        "type": "function",
        "function": {"name": "submit_planning_admission"},
    }
    assert h.sent[0].system_prompt == refs.SYSTEM
    assert receipt["submission_receipt"]["reference_binding"] == refs.contract_view(contract)
    enum = expected_tool["function"]["parameters"]["properties"]["hard_violations"]["items"][
        "properties"
    ]["source_refs"]["items"]["enum"]
    assert enum and all(pointer.startswith("/") for pointer in enum)
    assert (
        "/candidate_payload/chapters/2/causal_contract/fanqie_short_v2/continuity_contract/time_position"
        in enum
    )
    assert "/candidate_payload/chapters/0/scenes/0/unknown_authored/中文.键~1~0" in enum
    assert all(not isinstance(resolve_pointer(current, pointer), (dict, list)) for pointer in enum)
    assert (await assert_paid_native(h, receipt))[-1].tokens_charged == 15
    async with h.session_factory() as session:
        if mode == "pass":
            assert (
                await admission.verify_planning_draft_admission(
                    h.wrap(session), **h.kwargs(), receipt=receipt
                )
                == receipt
            )
        else:
            with pytest.raises(ValueError, match="admission_hard_rejected"):
                await admission.verify_planning_draft_admission(
                    h.wrap(session), **h.kwargs(), receipt=receipt
                )
    assert await assess_native(h, json.loads(json.dumps(state))) == receipt
    assert len(h.sent) == 1
    assert before == (h.kwargs()["candidate_payload"], h.project.metadata_json, h.commercial)


@pytest.mark.asyncio
async def test_v4_gateway_budget_model_and_effort_equal_original_v3_wire_allowance(native_v4):
    h = native_v4
    receipt = await assess_native(h, {})
    req, actual_role = h.sent[0], h.roles[0]
    plan3 = admission_output_budget(
        h.settings, h.project, schema=admission.SCHEMA, system=admission.SYSTEM
    )
    plan4 = admission_output_budget(h.settings, h.project, schema=refs.SCHEMA, system=refs.SYSTEM)
    assert plan3 == plan4 == req.metadata["planning_output_budget"]
    run = (await assert_paid_native(h, receipt))[0]
    assert run.metadata_json["planning_output_budget"] == plan4
    assert plan4["total_output_tokens"] == 32768 and plan4["reasoning_effort"] == "low"
    assert req.metadata["planning_admission_input_receipt"]["effective_max_output_tokens"] == 32768
    assert (
        req.metadata["source_transport"]["request_capacity"]["effective_max_output_tokens"] == 32768
    )
    assert llm._effective_input_hash(req, actual_role) == receipt["effective_input_hash"]
    legacy_req = req.model_copy(
        update={"prompt_version": admission.SCHEMA, "system_prompt": admission.SYSTEM}
    )
    legacy_role = llm._apply_conception_phase_settings(legacy_req, h.settings.llm.critic)
    v3wire, v4wire = (
        llm._effective_input_payload(legacy_req, legacy_role),
        llm._effective_input_payload(req, actual_role),
    )
    for field in ("model", "max_tokens", "reasoning_effort", "temperature", "endpoint_hash"):
        assert v3wire[field] == v4wire[field]
    assert (await assert_paid_native(h, receipt))[-1].calls_used == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ["missing_wrapper", "dot_alias", "container"])
async def test_v4_invalid_exact_refs_exhaust_two_paid_slots_without_drop_or_new_round(
    native_v4, bad
):
    h = native_v4
    h.mode = bad
    state = {}
    for _ in range(2):
        with pytest.raises(admission.PlanningDraftAdmissionProtocolError) as caught:
            await assess_native(h, state)
        assert caught.value.receipt["status"] == state["status"] == "protocol_exhausted"
        assert len(state["attempts"]) == len(h.sent) == 2
        for item in state["attempts"]:
            assert item["status"] == "protocol_invalid"
            assert "reference_not_in_catalog" in item["error"]["detail"]
            assert (
                json.loads(item["completion"]["tool_calls"][0]["function"]["arguments"])[
                    "draft_eligible"
                ]
                is False
            )
    async with h.session_factory() as session:
        budget = await session.get(ExecutionBudgetModel, SCOPE)
        attempts = list(await session.scalars(select(ExecutionAttemptModel)))
        runs = list(await session.scalars(select(LlmRunModel)))
        assert budget.calls_used == 2 and budget.tokens_charged == 30
        assert len(attempts) == len(runs) == 2
        assert all(item.status == "completed" and item.actual_tokens == 15 for item in attempts)
    before = deepcopy(state)
    h.mode = "pass"
    with pytest.raises(admission.PlanningDraftAdmissionProtocolError):
        await assess_native(h, state)
    assert state == before and len(h.sent) == 2


@pytest.mark.asyncio
async def test_v4_bounded_second_slot_preserves_invalid_native_and_replays(native_v4):
    h = native_v4
    h.modes = ["missing_wrapper", "pass"]
    state, snapshots = {}, []

    async def persist():
        snapshots.append(deepcopy(state))

    receipt = await assess_native(h, state, persist=persist)
    assert receipt["draft_eligible"] is True and len(h.sent) == 2
    assert snapshots[0]["attempts"] == []
    assert receipt["protocol_attempts"][0]["status"] == "protocol_invalid"
    assert receipt["protocol_attempts"][0]["completion"]["tool_calls"] == h.native_returns[0][0]
    assert h.sent[0].operation_id != h.sent[1].operation_id
    assert _logical_prompt(h.sent[0])["input"] == _logical_prompt(h.sent[1])["input"]
    assert h.sent[0].tools == h.sent[1].tools
    assert (await assert_paid_native(h, receipt))[-1].calls_used == 2
    assert await assess_native(h, json.loads(json.dumps(state))) == receipt
    assert len(h.sent) == 2


@pytest.mark.asyncio
async def test_v4_rollback_and_cold_replay_restore_native_without_extra_dispatch(native_v4):
    h = native_v4
    first = await assess_native(h, {}, rollback=True)
    state = {}
    second = await assess_native(h, state)
    assert first["graded_input_hash"] == second["graded_input_hash"]
    assert first["raw_grade"] == second["raw_grade"]
    assert len(h.sent) == 1
    assert (await assert_paid_native(h, second))[-1].calls_used == 1
    assert await assess_native(h, state) == second and len(h.sent) == 1


@pytest.mark.asyncio
async def test_old_actual_v3_exhausted_journal_cannot_upgrade_to_v4(native_v4):
    h = native_v4
    h.mode = "missing_wrapper"
    state = {}
    with pytest.raises(admission.PlanningDraftAdmissionProtocolError):
        await assess_native(h, state, schema=admission.SCHEMA)
    assert state["status"] == "protocol_exhausted" and len(h.sent) == 2
    assert all(
        item["completion"]["metadata"]["paid_call_origin"]["prompt_version"] == admission.SCHEMA
        for item in state["attempts"]
    )
    before = deepcopy(state)
    h.mode = "pass"
    with pytest.raises(ValueError, match="admission_protocol_input_changed"):
        await assess_native(h, state)
    assert state == before and len(h.sent) == 2
    altered = deepcopy(state)
    altered["hard_admission_policy"] = refs.POLICY
    with pytest.raises(ValueError, match="admission_protocol_input_changed"):
        await assess_native(h, altered)
    assert len(h.sent) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "location,field",
    [
        ("root", "tool_calls"),
        ("root", "raw_message"),
        ("transport", "tool_calls"),
        ("transport", "raw_message"),
    ],
)
async def test_v4_actual_paid_native_tamper_blocks_read_without_redispatch(
    native_v4, location, field
):
    h = native_v4
    receipt = await assess_native(h, {})
    _, root, _, transport, _ = await assert_paid_native(h, receipt)
    async with h.session_factory() as session:
        row = await session.get(
            ExecutionOperationModel, root.key if location == "root" else transport.key
        )
        saved = deepcopy(row.result_json)
        target = saved if location == "root" else saved["result"]
        calls = (
            target["tool_calls"] if field == "tool_calls" else target["raw_message"]["tool_calls"]
        )
        calls[0]["function"]["arguments"] += " "
        row.result_json = saved
        await session.commit()
    async with h.session_factory() as session:
        with pytest.raises(ValueError, match=r"paid_grade_mismatch|transport_return_missing"):
            await admission.verify_planning_draft_admission(
                h.wrap(session), **h.kwargs(), receipt=receipt
            )
    assert len(h.sent) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["candidate", "world"])
async def test_v4_fresh_read_rejects_changed_real_input_without_another_send(native_v4, change):
    from bestseller.domain.enums import ArtifactType

    h = native_v4
    receipt = await assess_native(h, {})
    kwargs = h.kwargs()
    if change == "candidate":
        kwargs["candidate_payload"]["chapters"][0]["scenes"][0]["unknown_authored"]["flag"] = 0
    else:
        h.sources[ArtifactType.WORLD_SPEC].content["requirement"] += " Source changed"
        kwargs["world_spec"] = deepcopy(h.sources[ArtifactType.WORLD_SPEC].content)
    before = deepcopy(receipt)
    async with h.session_factory() as session:
        with pytest.raises(ValueError):
            await admission.verify_planning_draft_admission(
                h.wrap(session), **kwargs, receipt=receipt
            )
    assert len(h.sent) == 1 and receipt == before
    assert (await assert_paid_native(h, receipt))[-1].calls_used == 1
