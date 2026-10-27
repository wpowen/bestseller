"""SQLite + physical retained generation: capacity admits dispatch, not cache reads."""
from copy import deepcopy
import json
from unittest.mock import AsyncMock

import pytest
import test_retained_planning_generation as fixtures
from test_retained_planning_generation import case as case  # noqa: F401
from test_retained_planning_generation import snapshot

from bestseller.services import planner
from bestseller.services import retained_planning_generation as reader
from bestseller.services import source_bound_planning_content as content
from bestseller.services import source_bound_planning_submission as submission

pytestmark = [pytest.mark.unit, pytest.mark.asyncio,
              pytest.mark.parametrize("case", ["cast_spec"], indirect=True)]


@pytest.fixture(autouse=True)
def large_native_tool(monkeypatch):
    actual = submission.planning_submission_tool

    def full_schema(*args, **kwargs):
        tool = deepcopy(actual(*args, **kwargs))
        # A real native-schema field, retained completely by the existing
        # physical fixture (<120k). Story sources and response schema stay intact.
        tool["function"]["description"] += "完整字段说明。" * 13000
        return tool

    monkeypatch.setattr(submission, "planning_submission_tool", full_schema)
    monkeypatch.setattr(fixtures, "planning_submission_tool", full_schema)
    monkeypatch.setattr(reader, "planning_submission_tool", full_schema)


@pytest.fixture
async def entry(case, monkeypatch):  # noqa: F811
    monkeypatch.setattr(planner, "_heartbeat_planner_workflow", AsyncMock())
    monkeypatch.setattr(planner, "_planner_stage_max_tokens", lambda *args, **kwargs: 4096)
    providers = []

    async def forbidden(*args, **kwargs):
        providers.append(True)
        pytest.fail("No provider dispatch permitted in retained/capacity tests")
    monkeypatch.setattr(planner, "complete_text", forbidden)
    request = case.args["current_request"]
    original_prompt = content.assert_source_bound_planning_prompt_capacity(
        request.system_prompt, request.user_prompt,
    )
    assert original_prompt["combined_chars"] <= 120000
    with pytest.raises(content.SourceBoundPlanningCapacityError) as error:
        content.assert_source_bound_planning_request_capacity(
            request.system_prompt, request.user_prompt, tools=request.tools,
            output_tokens=request.max_tokens_override,
        )
    assert error.value.receipt["budget_passed"] is False
    assert error.value.receipt["tool_tokens"] > 40000
    events = []
    actual_reader = reader.find_retained_planning_generation
    actual_capacity = content.assert_source_bound_planning_request_capacity

    async def read(*args, **kwargs):
        events.append("reader")
        value = await actual_reader(*args, **kwargs)
        events.append("hit" if value is not None else "miss")
        return value

    def capacity(*args, **kwargs):
        events.append("capacity")
        return actual_capacity(*args, **kwargs)

    monkeypatch.setattr(reader, "find_retained_planning_generation", read)
    monkeypatch.setattr(content, "assert_source_bound_planning_request_capacity", capacity)
    case.events, case.providers = events, providers
    return case


async def invoke(case, *, explicit=True, changed=False):
    if explicit:
        case.project._retained_planning_generation_source_runs = {"cast_spec": case.run.id}
    else:
        case.project._retained_planning_generation_source_runs = {}
    return await planner._complete_source_bound_planning_content(
        case.session, case.settings, project=case.project,
        premise=("Scientists inspect another current observation" if changed
                 else "Scientists inspect measurement errors"),
        kind="cast_spec", projection={}, workflow_run_id=case.consumer.id,
        book_spec={"theme_statement": "Publish the error", "protagonist": {"name": "Mara"}},
        world_spec={"world_name": "Observatory", "world_premise": "Scientists inspect errors"},
        cast_spec={"protagonist": {"name": "Mara", "goal": "Find the error"}},
    )


async def test_explicit_valid_paid_candidate_reuses_before_new_request_capacity_without_dispatch(entry):
    case = entry
    before = await snapshot(case)
    files = {key: path.read_bytes() for key, path in case.paths.items()}
    original = deepcopy(case.original.metadata_json)
    payload, run_id = await invoke(case)
    assert run_id == case.run.id
    assert {k: v for k, v in payload.items() if k != "_meta"} == case.payload
    assert case.events == ["reader", "hit"]
    assert payload["_meta"]["approved_complete"] is False
    assert payload["_meta"]["retained_generation_reuse"]["proof"]["provider_dispatches"] == 0
    assert case.providers == case.denied == []
    await case.session.commit()
    after = await snapshot(case)
    for table, rows in before.items():
        if table != "workflow_runs":
            assert after[table] == rows
        else:
            for old in rows:
                actual = next(row for row in after[table] if row["id"] == old["id"])
                if old["id"] != case.consumer.id:
                    assert actual == old
                else:
                    assert {k: v for k, v in actual.items() if k not in {"metadata_json", "updated_at"}} == {
                        k: v for k, v in old.items() if k not in {"metadata_json", "updated_at"}}
    assert case.original.metadata_json == original
    assert files == {key: path.read_bytes() for key, path in case.paths.items()}


async def test_ordinary_no_selector_dispatch_transport_runs_full_real_capacity_guard_before_provider(entry):
    case = entry
    before = await snapshot(case)
    files = {key: path.read_bytes() for key, path in case.paths.items()}
    # A genuinely different current request has no exact retained candidate;
    # all historical paid rows remain present and charged.
    with pytest.raises(content.SourceBoundPlanningCapacityError) as error:
        await invoke(case, explicit=False, changed=True)
    assert error.value.receipt["budget_passed"] is False
    assert case.events and set(case.events) == {"capacity"}
    assert case.providers == case.denied == []
    assert await snapshot(case) == before
    assert files == {key: path.read_bytes() for key, path in case.paths.items()}


@pytest.mark.parametrize("fault", ["incompatible_request", "missing_response", "unreplayable_request", "unknown_paid_root"])
async def test_explicit_incompatible_or_unproven_candidate_fails_closed_without_buying_replacement(entry, fault):
    case = entry
    if fault == "missing_response":
        case.paths["response"].unlink()
    elif fault == "unreplayable_request":
        payload = json.loads(case.paths["request"].read_text())
        payload["replayable"] = False
        case.paths["request"].write_text(json.dumps(payload))
    elif fault == "unknown_paid_root":
        case.operation.status = "unknown"
        await case.session.commit()
    before = await snapshot(case)
    with pytest.raises(reader.RetainedPlanningGenerationError):
        await invoke(case, changed=fault == "incompatible_request")
    assert case.events == ["reader"]
    assert case.providers == case.denied == []
    assert await snapshot(case) == before


async def test_generator_without_explicit_retained_selector_checks_wire_capacity_before_provider(entry):
    case = entry
    system, user = content.build_source_bound_planning_prompts(
        kind="cast_spec", project=case.project,
        premise="Scientists inspect another current observation", projection={},
        book_spec={"theme_statement": "Publish the error", "protagonist": {"name": "Mara"}},
        world_spec={"world_name": "Observatory", "world_premise": "Scientists inspect errors"},
        cast_spec={"protagonist": {"name": "Mara", "goal": "Find the error"}},
    )
    assert content.assert_source_bound_planning_prompt_capacity(system, user)["combined_chars"] <= 120000
    before = await snapshot(case)
    with pytest.raises(content.SourceBoundPlanningCapacityError) as error:
        await planner._generate_structured_artifact(
            case.session, case.settings, project=case.project, logical_name="cast_spec",
            system_prompt=system, user_prompt=user, fallback_payload={},
            workflow_run_id=case.consumer.id, validator=case.args["validator"],
            abort_on_fallback=True, merge_fallback=False,
            prompt_version=case.args["current_request"].prompt_version,
            attach_default_methodology=False, allow_legacy_reuse=False,
            allow_retained_generation=True,
            output_tool=case.args["current_request"].tools[0],
            before_dispatch=lambda: planner._assert_bound_planning_canonical(case.session, case.project),
        )
    assert error.value.receipt["budget_passed"] is False
    assert case.events and set(case.events) == {"capacity"}
    assert case.providers == case.denied == []
    assert await snapshot(case) == before
