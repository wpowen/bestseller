"""Real full-source request builder and strict patch boundary, no provider/PG.

The only completion seam is llm.complete_text. Actual codecs, selected-role
resolution, retention metadata, patch applier and (normally) validator run.
"""

from copy import deepcopy
import json
import socket
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from test_source_bound_outline_integrity import project as source_project
from test_source_bound_outline_integrity import source as source_candidate

from bestseller.services import llm, model_catalog, planner
from bestseller.services import planning_surgical_budget as budget
from bestseller.services import planning_surgical_repair as service
from bestseller.services import planning_surgical_transport as transport
from bestseller.services import source_complete_retention as retention
from bestseller.services.source_bound_planning_content import SourceBoundPlanningCapacityError
from bestseller.settings import LLMRoleSettings, load_settings

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


def canonical(value):
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


@pytest.fixture
def case(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Surgical repair tests forbid network/PG/legacy transport")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(planner, "complete_text", forbidden)
    project = source_project()
    project.metadata_json = {**project.metadata_json, "llm_model_id": "offline-selected-m31"}
    settings = load_settings(env={})
    settings.llm.mock = False
    settings.llm.planner = LLMRoleSettings(
        model="openai/unused-default-model",
        max_tokens=32768,
        reasoning_effort="medium",
        temperature=0.82,
        timeout_seconds=900,
        stream=True,
    )
    settings.artifact_store.local_dir = str(tmp_path / "physical")
    settings.artifact_store.mode = "local"
    settings.artifact_store.retain_prompt_payloads = True
    catalog = tmp_path / "catalog.json"
    catalog.write_text(
        json.dumps(
            {
                "models": [
                    {
                        "id": "offline-selected-m31",
                        "display_name": "Offline selected M31",
                        "vendor": "minimax",
                        "model": "openai/MiniMax-M3.1-Flash",
                        "api_base": "https://api.minimaxi.com/v1",
                    }
                ]
            }
        )
    )
    monkeypatch.setattr(model_catalog, "_CATALOG_PATH", catalog)
    payload = source_candidate()
    payload["_meta"] = {"historical_grade": {"pass": False, "raw": "Unknown old source"}}
    brief = planner._outline_judge_project_brief(
        project,
        metadata=project.metadata_json,
        semantic_candidates=[],
        world_spec={"world_name": "Independent observatory", "rules": []},
    )
    context = {
        "project_source_brief": brief,
        "approved_foundation": {
            kind: {"raw": {"unknown": None, "version": 3}}
            for kind in (
                "book_spec",
                "world_spec",
                "cast_spec",
                "volume_plan",
            )
        },
        "current_canonical": {"raw": {"explicit": False, "unknown": [1, 1.0, True, None]}},
        "compiler_candidate": deepcopy(planner._without_planning_meta(payload)),
        "legacy_materialized_base": {"raw": deepcopy(payload), "paid_full_source_asserted": False},
        "untrusted_retained_beat": {
            "source_run_id": None,
            "paid_source_proof_asserted": False,
            "raw": {"$ref": "/not-a-reference", "§": "§0"},
        },
        "late_unknown": "EXACT_TAIL " + "保留原始细节 " * 3000,
    }
    validation = {
        "volume_number": 1,
        "expected_count": 4,
        "chapter_number_offset": 1,
        "cast_spec": {
            "protagonist": {
                "name": "Mira",
                "role": "protagonist",
                "aliases": ["M"],
                "gender": "unknown",
            }
        },
        "volume_entry": {"chapter_range": [1, 4]},
    }
    patch = {
        "patches": [
            {
                "chapter_number": 1,
                "scope": "chapter",
                "field": "opening_situation",
                "value": "Amber light crosses a dented mirror beside the open hatch.",
            }
        ]
    }
    c = SimpleNamespace(
        settings=settings,
        project=project,
        payload=payload,
        context=context,
        validation=validation,
        workflow_id=uuid4(),
        directives=[f"Preserve exact source condition {n}." for n in range(4)],
        patch=patch,
        responses=[],
        requests=[],
        run_ids=[],
    )

    async def dispatch(session, settings, request):
        c.requests.append(request.model_copy(deep=True))
        response = c.responses.pop(0) if c.responses else canonical(c.patch)
        if isinstance(response, BaseException):
            raise response
        result = (
            response
            if isinstance(response, llm.LLMCompletionResult)
            else llm.LLMCompletionResult(
                content=response,
                provider="openai",
                model_name="openai/MiniMax-M3.1-Flash",
                llm_run_id=uuid4(),
                finish_reason="stop",
                input_tokens=4000,
                output_tokens=500,
            )
        )
        c.run_ids.append(result.llm_run_id)
        return result

    c.gateway = AsyncMock(side_effect=dispatch)
    monkeypatch.setattr(llm, "complete_text", c.gateway)
    c.titles = AsyncMock(return_value=[])
    monkeypatch.setattr(planner, "_fetch_existing_chapter_titles", c.titles)
    return c


def request(case, **overrides):
    return service.build_request(
        case.settings,
        **{
            "project": case.project,
            "workflow_run_id": case.workflow_id,
            "logical_name": "volume_1_chapter_outline",
            "outline_payload": case.payload,
            "directives": case.directives,
            "source_context": case.context,
            **overrides,
        },
    )


async def repair(case, **overrides):
    return await planner._repair_outline_commercial_findings_surgically(
        None,
        case.settings,
        **{
            "project": case.project,
            "workflow_run_id": case.workflow_id,
            "logical_name": "volume_1_chapter_outline",
            "outline_payload": case.payload,
            "directives": case.directives,
            "source_context": case.context,
            "validation_context": case.validation,
            "source_report": {"pass": False, "raw": "old negative"},
            **overrides,
        },
    )


def decoded(req):
    bound=req.metadata['planning_surgical_source_transport']['prompt_capacity']['max_estimated_tokens']
    assert bound in (60_000,439_650)
    return transport.decode_source_complete_surgical_prompts(
        system_prompt=req.system_prompt,
        user_prompt=req.user_prompt,
        transport_receipt=req.metadata["planning_surgical_source_transport"],
        max_chars=1_000_000,
        max_estimated_tokens=bound,
    )


async def test_new_source_context_routes_3_5_selected_role_and_exact_five_node_transport(case):
    before = deepcopy((case.payload, case.context, case.directives))
    candidate, run_id = await repair(case)
    assert case.gateway.await_count == 1 and run_id == case.run_ids[0]
    req = case.requests[0]
    assert req.prompt_version == budget.PROMPT_VERSION == "3.5-source-complete-repair"
    assert req.project_id == case.project.id and req.workflow_run_id == case.workflow_id
    role = budget.resolve_request_role(case.settings, case.project, req)
    physical = llm._effective_input_payload(req, role)
    assert physical["model"] == "openai/MiniMax-M3.1-Flash"
    assert physical["max_tokens"] == 32768 and physical["reasoning_effort"] == "medium"
    assert physical["temperature"] == 0.82
    exact = {
        "project_brief": before[1]["project_source_brief"],
        "directives": {"repair_directives": before[2], "protocol_feedback": []},
        "candidate_payload": planner._without_planning_meta(before[0]),
        "patch_protocol": planner._outline_patch_protocol(before[0]),
        "source_context": before[1],
    }
    assert canonical(decoded(req)) == canonical(exact)
    receipt = req.metadata[retention.METADATA_KEY]
    start, end = receipt["packet_start"], receipt["packet_end"]
    wire = json.loads(req.user_prompt[len(transport.MARKER) :])
    assert req.user_prompt[start:end] == canonical(wire["payload"])
    assert receipt["offset_unit"] == "unicode_code_points"
    with llm.bind_project_prompt_audit(case.project.id, reason="offline source-complete preflight"):
        assert (
            retention.retention_limit(
                case.settings, req, audit=llm._project_prompt_audit.get(), role=role
            )
            == 1_000_000
        )
    expected = planner._apply_outline_commercial_patches(
        before[0],
        case.patch["patches"],
        strict=True,
    )
    assert canonical(planner._without_planning_meta(candidate)) == canonical(
        planner._without_planning_meta(expected)
    )
    assert (case.payload, case.context, case.directives) == before
    assert candidate["_meta"]["historical_grade"] == before[0]["_meta"]["historical_grade"]
    proof = candidate["_meta"]["surgical_repair_receipt"]
    assert proof["before_candidate"] == exact["candidate_payload"]
    assert proof["source_report"]["pass"] is False and proof["protocol_attempts"] == []
    assert proof["source_transport"] == req.metadata["planning_surgical_source_transport"]


@pytest.mark.parametrize(
    "first",
    ["not JSON", "[]", "null", "1", '{"patches":"wrong"}', '{"patches":[null]}', '{"patches":[]}'],
)
async def test_invalid_json_or_patch_shape_retries_once_with_full_untrusted_feedback(case, first):
    case.responses = [first, canonical(case.patch)]
    candidate, run_id = await repair(case)
    assert case.gateway.await_count == 2 and run_id == case.run_ids[-1]
    initial, retried = map(decoded, case.requests)
    for key in ("project_brief", "candidate_payload", "patch_protocol", "source_context"):
        assert canonical(initial[key]) == canonical(retried[key])
    assert retried["directives"]["repair_directives"] == initial["directives"]["repair_directives"]
    feedback = retried["directives"]["protocol_feedback"]
    assert len(feedback) == 1 and feedback[0]["rejected_response"] == first
    assert feedback[0]["authority"] == "untrusted_protocol_failure_not_current_candidate"
    assert feedback[0]["llm_run_id"] == str(case.run_ids[0])
    assert len(candidate["_meta"]["surgical_repair_receipt"]["protocol_attempts"]) == 1
    assert all(req.max_tokens_override == 32768 for req in case.requests)


async def test_two_invalid_responses_exhaust_protocol_without_third_dispatch(case):
    case.responses = ['{"patches":[]}', '{"patches":[]}']
    with pytest.raises(planner.PlannerFallbackError):
        await repair(case)
    assert case.gateway.await_count == 2


@pytest.mark.parametrize("content", ["", '{"patches":[]}', '{"patches":['])
async def test_terminal_length_is_spent_once_without_same_cap_retry(case, content):
    case.responses = [
        llm.LLMCompletionResult(
            content=content,
            provider="openai",
            model_name="openai/MiniMax-M3.1-Flash",
            llm_run_id=uuid4(),
            finish_reason="length",
            output_tokens=32768,
        )
    ]
    with pytest.raises(planner.PlannerFallbackError, match="capacity exhausted"):
        await repair(case)
    assert case.gateway.await_count == 1 and case.titles.await_count == 0


@pytest.mark.parametrize("finish", ["stop", "end_turn", "completed"])
async def test_complete_native_terminal_finishes_allow_exact_patch_with_no_tool_calls(case, finish):
    case.responses = [
        llm.LLMCompletionResult(
            content=canonical(case.patch),
            provider="openai",
            model_name="openai/MiniMax-M3.1-Flash",
            llm_run_id=uuid4(),
            finish_reason=finish,
            tool_calls=[],
        )
    ]
    candidate, _ = await repair(case)
    exact = planner._apply_outline_commercial_patches(
        case.payload, case.patch["patches"], strict=True
    )
    assert canonical(planner._without_planning_meta(candidate)) == canonical(
        planner._without_planning_meta(exact)
    )
    assert case.gateway.await_count == 1


@pytest.mark.parametrize("finish", [None, "", "tool_calls", "content_filter", "unknown"])
async def test_nonnative_finish_rejects_without_a_second_dispatch_even_with_valid_json(
    case, finish
):
    case.responses = [
        llm.LLMCompletionResult(
            content=canonical(case.patch),
            provider="openai",
            model_name="openai/MiniMax-M3.1-Flash",
            llm_run_id=uuid4(),
            finish_reason=finish,
        )
    ]
    with pytest.raises(planner.PlannerFallbackError, match="no complete native JSON return"):
        await repair(case)
    assert case.gateway.await_count == 1 and case.titles.await_count == 0


@pytest.mark.parametrize("finish", ["stop", "end_turn", "completed", "length"])
async def test_unexpected_tools_never_become_patch_or_retry_and_length_guard_stays_first(
    case, finish
):
    case.responses = [
        llm.LLMCompletionResult(
            content=canonical(case.patch),
            provider="openai",
            model_name="openai/MiniMax-M3.1-Flash",
            llm_run_id=uuid4(),
            finish_reason=finish,
            tool_calls=[
                {
                    "id": "unexpected",
                    "type": "function",
                    "function": {"name": "do_not_execute", "arguments": "{}"},
                }
            ],
        )
    ]
    message = "capacity exhausted" if finish == "length" else "no complete native JSON return"
    with pytest.raises(planner.PlannerFallbackError, match=message):
        await repair(case)
    assert case.gateway.await_count == 1 and case.titles.await_count == 0


@pytest.mark.parametrize("fault", ["extra", "wrapped", "missing"])
async def test_only_exact_patches_root_is_accepted_and_full_root_failure_goes_to_feedback(
    case, fault
):
    first = {**case.patch, "approved": True}
    if fault == "wrapped":
        first = {"proposal": deepcopy(case.patch)}
    elif fault == "missing":
        first = {}
    raw = canonical(first)
    case.responses = [raw, canonical(case.patch)]
    await repair(case)
    assert case.gateway.await_count == 2 and case.titles.await_count == 1
    initial, retried = map(decoded, case.requests)
    assert retried["candidate_payload"] == initial["candidate_payload"]
    feedback = retried["directives"]["protocol_feedback"]
    assert len(feedback) == 1 and feedback[0]["rejected_response"] == raw
    assert "exactly the patches field" in feedback[0]["error"]


@pytest.mark.parametrize(
    "overrides",
    [
        {"provider": "fixture"},
        {"provider": "mock"},
        {"provider": "fallback"},
        {"provider": "test"},
        {"provider": ""},
        {"provider": "placeholder"},
        {"provider": "openai", "llm_run_id": None},
        {"provider": "openai", "fallback_used": True},
    ],
)
async def test_fake_fallback_or_missing_paid_run_is_never_a_valid_patch(case, overrides):
    case.responses = [
        llm.LLMCompletionResult(
            **{
                "content": canonical(case.patch),
                "provider": "openai",
                "model_name": "openai/MiniMax-M3.1-Flash",
                "llm_run_id": uuid4(),
                "finish_reason": "stop",
                **overrides,
            }
        )
    ]
    with pytest.raises(planner.PlannerFallbackError, match="real paid response"):
        await repair(case)
    assert case.gateway.await_count == 1 and case.titles.await_count == 0


@pytest.mark.parametrize("validation", [None, {}])
async def test_validation_context_required_before_dispatch(case, validation):
    with pytest.raises(ValueError, match="validation_required"):
        await repair(case, validation_context=validation)
    case.gateway.assert_not_awaited()


async def test_strict_patch_and_validator_flags_are_always_enabled(case, monkeypatch):
    seen = []
    actual_apply = planner._apply_outline_commercial_patches

    def strict_apply(*args, **kwargs):
        assert kwargs["strict"] is True
        return actual_apply(*args, **kwargs)

    def validation_boundary(value, **kwargs):
        seen.append(kwargs)
        assert kwargs["strict_story_effects"] is kwargs["strict_field_degeneracy"] is True
        assert kwargs["cast_spec"] == case.validation["cast_spec"]
        assert kwargs["volume_entry"] == case.validation["volume_entry"]
        assert kwargs["expected_count"] == 4 and kwargs["chapter_number_offset"] == 1
        return deepcopy(value)

    monkeypatch.setattr(planner, "_apply_outline_commercial_patches", strict_apply)
    monkeypatch.setattr(planner, "_validate_generated_volume_outline_or_raise", validation_boundary)
    await repair(case)
    assert len(seen) == 1


async def test_all_supplied_directives_survive_in_complete_transport(case):
    directives = [f"Distinct exact diagnostic {n}." for n in range(12)]
    req = request(case, directives=directives)
    assert decoded(req)["directives"]["repair_directives"] == directives
    case.gateway.assert_not_awaited()


@pytest.mark.parametrize(
    "bad_rows",
    [
        [{"chapter_number": True, "scope": "chapter", "field": "goal", "value": "new"}],
        [{"chapter_number": 99, "scope": "chapter", "field": "goal", "value": "new"}],
        [{"chapter_number": 1, "scope": "project", "field": "goal", "value": "new"}],
        [{"chapter_number": 1, "scope": "chapter", "field": "unknown_authored", "value": "new"}],
        [
            {
                "chapter_number": 1,
                "scope": "scene",
                "scene_number": "1",
                "field": "purpose",
                "value": {},
            }
        ],
        [
            {
                "chapter_number": 1,
                "scope": "scene",
                "scene_number": 1,
                "field": "participants",
                "value": "Mira",
            }
        ],
        [{"chapter_number": 1, "scope": "chapter", "field": "goal", "value": None}],
        [{"chapter_number": 1, "scope": "chapter", "field": "goal", "value": "new"}] * 2,
    ],
)
async def test_real_strict_applier_rejects_invalid_targets_types_and_duplicates(case, bad_rows):
    native = canonical({"patches": bad_rows})
    case.responses = [native, native]
    before = deepcopy(case.payload)
    with pytest.raises(planner.PlannerFallbackError):
        await repair(case)
    assert case.gateway.await_count == 2 and case.titles.await_count == 0
    assert case.payload == before
    assert (
        decoded(case.requests[1])["directives"]["protocol_feedback"][0]["rejected_response"]
        == native
    )


def alias_patch():
    # The real source validator canonicalizes this exact declared identity alias.
    # The returned proposal still needs to bind to the original native patch,
    # rather than quietly returning a different body after validation.
    return {
        "patches": [
            {
                "chapter_number": 2,
                "scope": "scene",
                "scene_number": 1,
                "field": "participants",
                "value": ["M"],
            }
        ]
    }


async def test_real_validator_rejects_both_normalizing_attempts_without_changing_candidate(case):
    native = canonical(alias_patch())
    case.responses = [native, native]
    before = deepcopy(case.payload)
    with pytest.raises(planner.PlannerFallbackError, match="validator normalization"):
        await repair(case)
    assert case.gateway.await_count == 2
    assert case.payload == before
    initial, retried = map(decoded, case.requests)
    assert initial["candidate_payload"] == retried["candidate_payload"]
    feedback = retried["directives"]["protocol_feedback"]
    assert len(feedback) == 1 and feedback[0]["rejected_response"] == native
    assert "validator normalization" in feedback[0]["error"]
    assert feedback[0]["llm_run_id"] == str(case.run_ids[0])


async def test_explicit_canonical_second_native_patch_returns_exact_unnormalized_result(case):
    second = deepcopy(case.patch)
    canonical_identity = deepcopy(alias_patch()["patches"][0])
    canonical_identity["value"] = ["Mira"]
    second["patches"].append(canonical_identity)
    case.responses = [canonical(alias_patch()), canonical(second)]
    before = deepcopy(case.payload)
    exact = planner._apply_outline_commercial_patches(before, second["patches"], strict=True)
    result, run_id = await repair(case)
    assert case.gateway.await_count == 2 and run_id == case.run_ids[-1]
    assert canonical(planner._without_planning_meta(result)) == canonical(
        planner._without_planning_meta(exact)
    )
    assert case.payload == before
    proof = result["_meta"]["surgical_repair_receipt"]
    assert len(proof["protocol_attempts"]) == 1
    assert "validator normalization" in proof["protocol_attempts"][0]["error"]
    assert proof["llm_run_id"] == str(case.run_ids[-1])
    assert (
        proof["source_transport"]
        == case.requests[-1].metadata["planning_surgical_source_transport"]
    )


async def test_feedback_rebuild_rechecks_capacity_before_any_second_dispatch(case):
    # Intentionally invalid JSON; its complete native text must not be clipped.
    first = "REJECTED_NATIVE_EXACT " + "z" * 1_100_000
    case.responses = [first]
    with pytest.raises(SourceBoundPlanningCapacityError):
        await repair(case)
    assert case.gateway.await_count == 1


async def test_cancelled_gateway_propagates_without_retry(case):
    from asyncio import CancelledError

    case.responses = [CancelledError()]
    with pytest.raises(CancelledError):
        await repair(case)
    assert case.gateway.await_count == 1


async def test_execution_budget_stop_propagates_without_retry(case):
    from bestseller.services.budget import ExecutionBudgetError

    stop = ExecutionBudgetError("offline_budget_stop")
    case.responses = [stop]
    with pytest.raises(ExecutionBudgetError) as caught:
        await repair(case)
    assert caught.value is stop and case.gateway.await_count == 1
