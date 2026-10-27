"""Real planner wrapper/capacity guard; only the final gateway is inert."""

from copy import deepcopy
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from test_planner_services import FakeSession, build_settings

from bestseller.infra.db.models import ProjectModel
from bestseller.services import planner
from bestseller.services.source_bound_planning_content import (
    MAX_PROMPT_CHARS,
    SourceBoundPlanningCapacityError,
)
from bestseller.services.source_complete_outline_json import (
    FORMAT_INSTRUCTION,
    POLICY,
    PROMPT_VERSION,
    StrictOutlineJSONError,
)

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]
LOGICAL = "volume_1_chapter_outline_batch_1_3"
VALID = {
    "batch_name": "harbour-records",
    "chapters": [
        {
            "chapter_number": 1,
            "goal": "Compare the signed manifests.",
            "unknown_authored": {"$ref": "literal"},
        }
    ],
}


@pytest.fixture
def entry(monkeypatch):
    settings = build_settings()
    settings.pipeline.planning_artifact_reuse_enabled = False
    project = ProjectModel(
        id=uuid4(),
        slug=f"outline-{uuid4().hex}",
        title="潮汐观测站",
        genre="general-fiction",
        language="zh-CN",
        target_chapters=3,
        target_word_count=7800,
        metadata_json={},
    )
    h = SimpleNamespace(
        settings=settings,
        project=project,
        workflow=uuid4(),
        calls=[],
        failures=[],
        responses=[],
        user="FULL_SOURCE_START\n"
        + json.dumps(
            {
                "canonical": {"unknown": ["未裁剪作者原文🪼", "independent chronology"]},
                "source_tail": "FULL_SOURCE_END",
            },
            ensure_ascii=False,
        )
        + "\nTASK_COMPLETE",
    )

    async def gateway(session, settings, request):
        h.calls.append(request)
        response = h.responses.pop(0)
        return SimpleNamespace(
            content=response[0],
            finish_reason=response[1],
            tool_calls=None,
            llm_run_id=uuid4(),
            provider="openai",
        )

    monkeypatch.setattr(planner, "complete_text", gateway)
    monkeypatch.setattr(
        planner,
        "_persist_failing_planner_output",
        lambda **kwargs: h.failures.append(deepcopy(kwargs)),
    )
    return h


async def generate(h, **overrides):
    return await planner._generate_structured_artifact(
        FakeSession(),
        h.settings,
        project=h.project,
        logical_name=LOGICAL,
        system_prompt="system",
        user_prompt=h.user,
        fallback_payload={"chapters": [{"forbidden_default": True}]},
        workflow_run_id=h.workflow,
        prompt_version=PROMPT_VERSION,
        attach_default_methodology=False,
        max_attempts=2,
        **overrides,
    )


def assert_current_requests(h):
    for request in h.calls:
        assert request.project_id == h.project.id and request.workflow_run_id == h.workflow
        assert request.prompt_version == PROMPT_VERSION
        assert request.user_prompt.startswith(h.user + FORMAT_INSTRUCTION)
        assert request.user_prompt.count(h.user) == 1
        assert request.max_tokens_override == 16384
        assert request.metadata["strict_outline_json_policy"] == POLICY
        cap = request.metadata["rolling_outline_request_capacity"]
        assert cap["budget_passed"] and cap["output_reserve_tokens"] == 16384
        assert cap["safety_factor"] >= 2.2 and cap["total_budget"] == 65536
        assert cap["combined_chars"] == len(request.system_prompt) + len(request.user_prompt)


@pytest.mark.parametrize(
    "bad", ['{"chapters":[],"chapters":[{}]}', '{"chapters":[{"chapter_number":1}']
)
async def test_syntax_failure_retries_complete_original_source_and_retains_raw(
    entry, monkeypatch, bad
):
    h = entry
    h.responses = [(bad, "stop"), (json.dumps(VALID), "stop")]

    def forbidden(*args, **kwargs):
        pytest.fail("new strict protocol entered a legacy extractor/envelope unwrap")

    monkeypatch.setattr(planner, "_extract_json_payload", forbidden)
    monkeypatch.setattr(planner, "_unwrap_planner_envelope", forbidden)
    payload, run_id = await generate(h)
    assert payload["chapters"] == VALID["chapters"] and run_id
    assert len(h.calls) == 2 and h.failures[0]["content"] == bad
    assert h.calls[0].user_prompt == h.user + FORMAT_INSTRUCTION
    assert "[SOURCE-BOUND SUBMISSION CORRECTION]" in h.calls[1].user_prompt
    assert h.calls[1].metadata["semantic_repair_history"][0]["attempt"] == 1
    assert "forbidden_default" not in json.dumps(payload)
    assert_current_requests(h)


async def test_invalid_returns_exhaust_original_finite_attempts_without_fallback(entry):
    h = entry
    raw = '{"chapters":[]} trailing untrusted text'
    h.responses = [(raw, "stop"), (raw, "stop")]
    with pytest.raises(planner.PlannerFallbackError, match="after 2 attempts"):
        await generate(h)
    assert len(h.calls) == len(h.failures) == 2
    assert [failure["content"] for failure in h.failures] == [raw, raw]
    assert [request.metadata["attempt"] for request in h.calls] == [1, 2]
    assert_current_requests(h)


async def test_length_return_also_retries_full_original_sources(entry):
    h = entry
    raw = '{"chapters":['
    h.responses = [(raw, "length"), (json.dumps(VALID), "stop")]
    payload, _ = await generate(h)
    assert payload["chapters"] == VALID["chapters"]
    assert len(h.calls) == 2 and h.failures[0]["content"] == raw
    assert h.calls[1].metadata["semantic_repair_history"][0]["error_type"] == "finish_reason_length"
    assert_current_requests(h)


async def test_initial_capacity_overflow_stops_before_gateway(entry):
    h = entry
    h.user = "x" * MAX_PROMPT_CHARS
    with pytest.raises(SourceBoundPlanningCapacityError):
        await generate(h)
    assert h.calls == [] and h.failures == []


async def test_retry_is_remeasured_no_budget_bump_or_source_cropping(entry):
    h = entry
    # Start inside the current whole-request boundary, then let the real
    # correction suffix overflow it. Char-only admission is no longer enough.
    from bestseller.services.source_bound_planning_content import assert_source_bound_planning_request_capacity
    low, high = 0, MAX_PROMPT_CHARS
    while low + 1 < high:
        mid = (low + high) // 2
        try:
            assert_source_bound_planning_request_capacity('system', 'x' * mid + FORMAT_INSTRUCTION, tools=None, output_tokens=16384)
        except SourceBoundPlanningCapacityError:
            high = mid
        else:
            low = mid
    h.user = 'x' * low
    raw = '{"chapters":[]} trailing'
    h.responses = [(raw, "stop")]
    with pytest.raises(SourceBoundPlanningCapacityError):
        await generate(h)
    assert len(h.calls) == len(h.failures) == 1
    assert h.failures[0]["content"] == raw
    assert_current_requests(h)


@pytest.mark.parametrize("version", ["2.0-source-authority", "3.0-source-complete-outline"])
async def test_legacy_fitting_request_bytes_and_parser_branch_unchanged(entry, version):
    h = entry
    h.responses = [(json.dumps(VALID), "stop")]
    payload, _ = await planner._generate_structured_artifact(
        FakeSession(),
        h.settings,
        project=h.project,
        logical_name=LOGICAL,
        system_prompt="system",
        user_prompt=h.user,
        fallback_payload={},
        workflow_run_id=h.workflow,
        prompt_version=version,
        attach_default_methodology=False,
        merge_fallback=False,
    )
    assert payload["chapters"] == VALID["chapters"]
    assert len(h.calls) == 1 and h.calls[0].user_prompt == h.user
    assert "strict_outline_json_policy" not in h.calls[0].metadata


async def test_new_protocol_input_hash_cannot_reuse_legacy_parsed_artifact(entry, monkeypatch):
    h = entry
    h.settings.pipeline.planning_artifact_reuse_enabled = True
    old_hash = planner._planner_artifact_input_hash(
        logical_name=LOGICAL,
        system_prompt="system",
        user_prompt=h.user,
        fallback_payload={"chapters": [{"forbidden_default": True}]},
    )
    looked_up = []

    async def lookup(*args, **kwargs):
        looked_up.append(kwargs["input_hash"])
        if kwargs["input_hash"] == old_hash:
            pytest.fail("old parsed artifact input identity reused for strict protocol")
        return None

    monkeypatch.setattr(planner, "_latest_reusable_planning_artifact", lookup)
    h.responses = [(json.dumps(VALID), "stop")]
    payload, _ = await generate(h)
    assert len(looked_up) == len(h.calls) == 1
    assert payload["_meta"]["input_hash"] == looked_up[0] != old_hash


@pytest.mark.parametrize(
    "options", [{"allow_retained_generation": True}, {"retained_generation_source_run_id": uuid4()}]
)
async def test_old_paid_generation_cannot_be_imported_as_new_strict_protocol(entry, options):
    with pytest.raises(StrictOutlineJSONError, match="future_text_protocol_only"):
        await generate(entry, **options)
    assert entry.calls == [] and entry.failures == []


async def test_exact_precompiled_protocol_suffix_is_not_appended_again(entry):
    h = entry
    h.user += FORMAT_INSTRUCTION
    h.responses = [(json.dumps(VALID), "stop")]
    payload, _ = await generate(h)
    assert payload["chapters"] == VALID["chapters"]
    assert len(h.calls) == 1 and h.calls[0].user_prompt == h.user
    assert h.calls[0].user_prompt.count(FORMAT_INSTRUCTION) == 1
    capacity = h.calls[0].metadata["rolling_outline_request_capacity"]
    assert capacity["combined_chars"] == len("system") + len(h.user)
    assert capacity["budget_passed"]
