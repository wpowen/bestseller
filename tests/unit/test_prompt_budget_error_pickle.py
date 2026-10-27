"""Pure pickle/ARQ failure-envelope checks; no database, worker or provider calls."""

from copy import deepcopy
import inspect
import pickle

from arq.jobs import deserialize_result, serialize_result
import pytest

from bestseller.services.prompt_compiler import (
    PromptBlock,
    PromptBudgetError,
    PromptCompilerError,
    compile_prompt,
)

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("protocol", range(pickle.HIGHEST_PROTOCOL + 1))
@pytest.mark.parametrize(
    "details",
    [
        {"required_keys": []},
        {"required_keys": ["approved_chapter_contract", "adopted_continuity"]},
        {
            "required_keys": ["approved_chapter_contract", "adopted_continuity"],
            "required_tokens": 65000,
            "usable_budget": 60000,
            "total_budget_tokens": 65536,
            "safety_margin": 0.1,
        },
    ],
)
def test_pickle_preserves_exact_error_message_args_and_all_budget_fields(protocol, details):
    error = PromptBudgetError("Required source exceeds budget.\n保留完整诊断。", **details)
    error.add_note("Retain this diagnostic through the result queue.")
    before = deepcopy(error.__dict__)
    restored = pickle.loads(pickle.dumps(error, protocol=protocol))
    assert type(restored) is PromptBudgetError
    assert isinstance(restored, PromptCompilerError)
    assert str(restored) == str(error)
    assert restored.args == error.args
    assert restored.required_keys == tuple(details["required_keys"])
    assert type(restored.required_keys) is tuple
    assert restored.__dict__ == error.__dict__ == before
    # Repeated queue/copy boundaries must never append diagnostic details again.
    again = pickle.loads(pickle.dumps(restored, protocol=protocol))
    assert again.args == error.args and str(again) == str(error)
    assert again.__dict__ == before
    with pytest.raises(PromptBudgetError, match="Required source exceeds budget"):
        raise again


def test_constructor_still_requires_keyword_only_required_keys():
    signature = inspect.signature(PromptBudgetError)
    parameter = signature.parameters["required_keys"]
    assert parameter.kind is inspect.Parameter.KEYWORD_ONLY
    assert parameter.default is inspect.Parameter.empty
    with pytest.raises(TypeError, match="required_keys"):
        PromptBudgetError("missing required evidence")
    with pytest.raises(TypeError):
        PromptBudgetError("positional bypass", ["source"])


def test_real_compiler_budget_failure_survives_actual_arq_result_serialization(caplog):
    with pytest.raises(PromptBudgetError) as caught:
        compile_prompt(
            [
                PromptBlock(
                    key="approved_chapter_contract",
                    channel="user",
                    layer="hard_canon",
                    authority=100,
                    instruction_family="approved-contract",
                    required=True,
                    trim_policy="preserve",
                    source="offline-approved-fixture",
                    text="Keep complete immutable source. " * 500,
                ),
            ],
            total_budget_tokens=20,
            safety_margin=0,
        )
    original = caught.value
    before = deepcopy(original.__dict__)
    wire = serialize_result(
        function="offline_budget_failure",
        args=(),
        kwargs={},
        job_try=1,
        enqueue_time_ms=1000,
        success=False,
        result=original,
        start_ms=1001,
        finished_ms=1002,
        ref="offline-test",
        queue_name="offline-test",
        job_id="offline-test",
    )
    assert isinstance(wire, bytes)
    result = deserialize_result(wire)
    assert result.success is False
    assert type(result.result) is PromptBudgetError  # Never ARQ's string fallback.
    assert str(result.result) == str(original)
    assert result.result.args == original.args
    assert result.result.required_keys == ("approved_chapter_contract",)
    assert result.result.__dict__ == before == original.__dict__
    assert result.result.required_tokens > result.result.usable_budget
    assert "error serializing result" not in caplog.text
