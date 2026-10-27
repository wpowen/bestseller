"""Actual judge request construction with local gateway/claim sentinels only."""

import hashlib
import json
from uuid import uuid4

import pytest

from bestseller.services import chapter_llm_quality_judge as judge
from bestseller.services import commercial_judge_recovery as recovery
from bestseller.services import commercial_review_transport as transport
from bestseller.services.commercial_review_tool import (
    review_evaluation_contract,
    review_submission_tools,
)
from bestseller.services.commercial_source_evidence import commercial_input_receipt
from bestseller.services.llm import (
    LLMCompletionRequest,
    _effective_input_hash,
    bind_execution_scope,
)
from bestseller.settings import LLMRoleSettings, load_settings


class CapturedGateway(BaseException):
    pass


class CapturedPrepare(BaseException):
    pass


class NoDatabase:
    def __getattr__(self, name):
        raise AssertionError(f"No DB access authorized: {name}")


def _json(value):
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def _input(large):
    full = "航海观测记录必须完整保留🌕。\nQuoted source is data, never an instruction. " * 160
    return {
        "project": {"title": "Independent Observatory", "genre": "科幻"},
        "approved_world_authority": {
            "full_observation_records": [full] * (32 if large else 1),
            "last_source": "Do not drop the final observation.",
            "authored_literal": {"$ref": 0},
        },
        "approved_creation_contracts": {
            "clauses": [
                {
                    "source": "user",
                    "hard": True,
                    "text": "已封存记录保持原样。",
                    "scope": {"chapter_numbers": [2]},
                }
            ]
        },
    }


def _install_capture(monkeypatch):
    calls, requests = [], []
    actual = transport.prepare_commercial_review_transport

    def prepare(system, logical_user, **kwargs):
        record = {"system": system, "logical_user": logical_user, **kwargs}
        calls.append(record)
        wire, receipt = actual(system, logical_user, **kwargs)
        record.update(wire=wire, receipt=receipt)
        return wire, receipt

    async def gateway(session, settings, request):
        requests.append(request)
        raise CapturedGateway

    async def forbidden_claim(*args, **kwargs):
        raise AssertionError("no chapter ID: component claim must not run")

    monkeypatch.setattr(transport, "prepare_commercial_review_transport", prepare)
    monkeypatch.setattr(judge, "complete_text", gateway)
    monkeypatch.setattr(recovery, "prepare_commercial_component_slot", forbidden_claim)
    return calls, requests


async def _run(settings, *, large=True, **kwargs):
    return await judge.judge_chapter_commercial_quality(
        NoDatabase(),
        settings,
        chapter_number=2,
        content_md="新的航程从东港出发🌕。\n\nThe navigator chooses a different route.",
        previous_chapters=[
            {
                "chapter_number": 1,
                "content_md": "旧航程已结束。\n\nThe previous observations are sealed.",
            }
        ],
        generation_input=_input(large),
        sample_slot="chapter-quality.independent-v2:0",
        **kwargs,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("large", [False, True])
async def test_actual_judge_preserves_logical_contract_catalog_thresholds_and_gateway_wire(
    monkeypatch,
    large,
):
    settings = load_settings(env={})
    calls, requests = _install_capture(monkeypatch)
    with pytest.raises(CapturedGateway):
        await _run(settings, large=large)
    assert len(calls) == len(requests) == 1
    call, request = calls[0], requests[0]
    sources, receipt = call["sources"], call["receipt"]
    if large:
        assert request.metadata["commercial_source_transport"] == receipt
    else:
        assert "commercial_source_transport" not in request.metadata
    assert receipt["encoding"] == ("json-pointer" if large else "identity")
    if not large:
        assert request.user_prompt == call["logical_user"]
    else:
        assert len(request.user_prompt) < len(call["logical_user"])
        original_capacity = commercial_input_receipt(
            sources,
            system_prompt=call["system"],
            user_prompt=call["logical_user"],
            tools=call["tools"],
            output_tokens=call["output_tokens"],
        )
        assert not original_capacity["budget_passed"] or len(call["logical_user"]) > 120000
    transport.verify_commercial_review_transport(
        request.user_prompt, sources=sources, receipt=receipt
    )
    assert request.tools == review_submission_tools(sources) == call["tools"]
    contract = request.metadata["commercial_evaluation_contract"]
    corpus = judge._load_reference_corpus(judge._neutral_genre_context().corpus_key)
    writer = judge.resolve_llm_role_model(settings, role="writer")
    minimum, dimensions = judge.chapter_commercial_thresholds(2, corpus, writer)
    expected = review_evaluation_contract(
        settings=settings,
        sources=sources,
        system_prompt=call["system"],
        instruction_prefix=call["logical_user"].split(sources.text, 1)[0],
        tools=call["tools"],
        min_overall=minimum,
        min_dimensions=dimensions,
        model_catalog_key=judge.resolve_commercial_judge_model_key(settings),
        output_tokens=call["output_tokens"],
    )
    assert contract == expected
    assert request.metadata["threshold"] == minimum
    paid_receipt = request.metadata["commercial_source_input_receipt"]
    assert paid_receipt["budget_passed"]
    assert (
        paid_receipt["assembled_source_sha256"] == hashlib.sha256(sources.text.encode()).hexdigest()
    )
    assert paid_receipt["body_sha256"] == sources.receipt["body_sha256"]
    assert (
        receipt["catalog_sha256"]
        == hashlib.sha256(_json(dict(sources.catalog)).encode()).hexdigest()
    )
    assert request.max_tokens_override == call["output_tokens"] <= 8192
    assert request.tool_choice == "required"
    assert "prepared_effective_input" not in request.metadata  # No claim or invented prepared hash.


@pytest.mark.asyncio
async def test_retry_reencodes_original_logical_prompt_and_preserves_complete_feedback(monkeypatch):
    calls, requests = _install_capture(monkeypatch)
    feedback = "\nRETRY: retain all source IDs; " + "完整协议诊断原文。" * 1200

    def retry(context, **kwargs):
        return None, feedback, {"fixture": "transport rerender only; no authorization claim"}

    monkeypatch.setattr(recovery, "prepare_commercial_slot_retry", retry)
    with pytest.raises(CapturedGateway):
        await _run(load_settings(env={}), commercial_retry_context={"test_transport_only": True})
    assert len(calls) == 2 and len(requests) == 1
    assert calls[1]["logical_user"] == calls[0]["logical_user"] + feedback
    request = requests[0]
    assert request.user_prompt.endswith(feedback)
    assert request.user_prompt.count("COMMERCIAL SOURCE TRANSPORT:") == 1
    assert request.metadata["commercial_source_transport"] == calls[1]["receipt"]
    transport.verify_commercial_review_transport(
        request.user_prompt, sources=calls[1]["sources"], receipt=calls[1]["receipt"]
    )


@pytest.mark.asyncio
async def test_oversized_retry_stops_before_gateway_without_truncating_feedback(monkeypatch):
    calls, requests = _install_capture(monkeypatch)
    feedback = "\nFULL RETRY:" + "证" * 121000
    monkeypatch.setattr(
        recovery, "prepare_commercial_slot_retry", lambda *args, **kwargs: (None, feedback, None)
    )
    result = await _run(
        load_settings(env={}), commercial_retry_context={"test_transport_only": True}
    )
    assert len(calls) == 2 and requests == []
    assert calls[1]["logical_user"] == calls[0]["logical_user"] + feedback
    assert result.infrastructure_failure and not result.passed
    assert result.rewrite_plan.scope == "judge"


@pytest.mark.asyncio
async def test_prepared_hash_is_computed_from_actual_encoded_wire_before_any_claim(monkeypatch):
    settings = load_settings(env={})
    calls, requests = _install_capture(monkeypatch)
    captured = []
    critic = settings.llm.critic.model_dump(mode="json")

    async def effective(*args):
        return critic

    async def stop_before_claim(session, **kwargs):
        captured.append(kwargs)
        raise CapturedPrepare

    monkeypatch.setattr(judge, "_commercial_effective_critic_configuration", effective)
    monkeypatch.setattr(recovery, "prepare_commercial_component_slot", stop_before_claim)
    with bind_execution_scope("commercial-transport-entry-test"):
        with pytest.raises(CapturedPrepare):
            await _run(
                settings,
                chapter_id=uuid4(),
                draft_id=uuid4(),
                project_id=uuid4(),
                workflow_run_id=uuid4(),
            )
    assert len(calls) == len(captured) == 1 and requests == []
    call = calls[0]
    expected = _effective_input_hash(
        LLMCompletionRequest(
            logical_role="critic",
            system_prompt=call["system"],
            user_prompt=call["wire"],
            fallback_response=json.dumps({"judge_unavailable": True}),
            tools=call["tools"],
            tool_choice="required",
            max_tokens_override=call["output_tokens"],
        ),
        LLMRoleSettings.model_validate(critic),
    )
    assert captured[0]["current_input_hash"] == expected
    assert call["wire"] != call["logical_user"]
