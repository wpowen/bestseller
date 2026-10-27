"""Monotone source budgeting, with immutable local capture and capacity receipts."""

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import socket
from uuid import uuid4

import pytest
from sqlalchemy.engine import Engine

from bestseller.services import drafts, llm, llm_capacity
from bestseller.services import prompt_compiler as compiler
from bestseller.services.chapter_writer_delivery import delivery_block

pytestmark = pytest.mark.unit

_CAPTURE = (
    Path(__file__).resolve().parents[2]
    / "reports/framework-recovery-2026-10-21/phase8s-writer-compiler-real-input.json"
)
_CAPTURE_SHA256 = "1293ac30df4d139dcc1189c11444832f4a039b8c8361fd4b7012bad315bf657d"


@pytest.fixture(autouse=True)
def no_external_calls(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Estimator tests forbid DB/provider/network calls")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(Engine, "connect", forbidden)
    monkeypatch.setattr(llm, "complete_text", forbidden)
    monkeypatch.setattr(drafts, "complete_text", forbidden)


@pytest.fixture
def actual_capture():
    if not _CAPTURE.exists():
        pytest.skip("Requires the archived, read-only production compiler capture")
    raw = _CAPTURE.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == _CAPTURE_SHA256
    saved = json.loads(raw)
    assert saved["provider_calls"] == 0 and saved["budget_unchanged"] is True
    kwargs = deepcopy(saved["compiler_kwargs"])
    assert kwargs["total_input_budget_tokens"] == 14800
    assert kwargs["prompt_safety_margin"] == .1
    assert kwargs["additional_blocks"] == []
    return kwargs


def test_real_capture_preserves_every_critical_source_at_the_unchanged_budget(
    actual_capture, monkeypatch,
):
    original = drafts.compile_prompt
    seen = []

    def record(blocks, **kwargs):
        seen.extend(blocks)
        return original(blocks, **kwargs)

    monkeypatch.setattr(drafts, "compile_prompt", record)
    compiled = drafts._compile_rendered_writer_prompt(**actual_capture)
    report = compiled.report
    assert report.budget_tokens == 14800 and report.usable_budget_tokens == 13320
    assert report.total_tokens <= report.usable_budget_tokens
    assert report.source_integrity_complete is True
    assert "chapter_first.section.story_state" in report.kept
    changed = {*report.dropped, *report.truncated, *report.duplicates}
    assert not changed.intersection(report.source_integrity_blocks)
    protected = [b for b in seen if b.key in report.source_integrity_blocks or b.required]
    for block in protected:
        channel = compiled.system if block.channel == "system" else compiled.user
        assert block.text.strip() in channel  # Full original bytes, including source endings.
    system, user = compiler._render_channels(sorted(protected, key=compiler._block_sort_key))
    assert compiler.estimate_prompt_tokens(system) + compiler.estimate_prompt_tokens(user) == 12558


def test_real_capture_with_historical_estimator_reproduces_the_original_stop(
    actual_capture, monkeypatch,
):
    monkeypatch.setattr(compiler, "estimate_prompt_tokens", compiler.estimate_legacy_prompt_tokens)
    with pytest.raises(compiler.PromptBudgetError, match="lost critical source fields") as caught:
        drafts._compile_rendered_writer_prompt(**actual_capture)
    assert caught.value.required_keys == ("chapter_first.section.story_state",)
    assert caught.value.total_budget_tokens == 14800 and caught.value.safety_margin == .1


def test_actual_full_delivery_preserves_the_real_core_and_still_stops(monkeypatch):
    path = _CAPTURE.with_name("phase8s-writer-full-delivery-input.json")
    if not path.exists():
        pytest.skip("Requires the archived, read-only full-delivery capture")
    raw = path.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == (
        "acf3f262a56fb5333d0b59635237bd36ab4550cc93d7ab96f1fd99017cf5b5fb"
    )
    kwargs = deepcopy(json.loads(raw)["compiler_kwargs"])
    kwargs["additional_blocks"] = [
        compiler.PromptBlock.model_validate(value) for value in kwargs["additional_blocks"]
    ]
    assert len(kwargs["additional_blocks"]) == 1
    typed = kwargs["additional_blocks"][0]
    assert typed.required and typed.trim_policy == "preserve"
    assert len(typed.text) == 10419 and compiler.estimate_prompt_tokens(typed.text) == 4259
    seen = []
    original = drafts.compile_prompt

    def record(blocks, **options):
        seen.extend(blocks)
        return original(blocks, **options)

    monkeypatch.setattr(drafts, "compile_prompt", record)
    before = deepcopy(kwargs)
    with pytest.raises(compiler.PromptBudgetError) as caught:
        drafts._compile_rendered_writer_prompt(**kwargs)
    assert caught.value.required_keys == (
        "chapter_first.section.story_state", "chapter_first.section.timeline_context",
    )
    assert caught.value.total_budget_tokens == 14800 and caught.value.safety_margin == .1
    core = [block for block in seen if block.required or block.layer == "scene_spec"]
    system, user = compiler._render_channels(sorted(core, key=compiler._block_sort_key))
    assert compiler.estimate_prompt_tokens(system) + compiler.estimate_prompt_tokens(user) == 16817
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    # Explicit capacity scenario, not an assertion about the capture's absent
    # effective model/output settings and not an elastic-budget authorization.
    receipt = llm_capacity.measure_request_capacity(
        messages, tools=None, output_tokens=8192, n_candidates=1, total_budget=65536,
    )
    assert receipt["estimated_input_tokens"] == 16826
    assert receipt["mixed_script_input_floor"] == 38926
    assert receipt["reserved_input_tokens"] == 43395
    assert receipt["reserved_total_tokens"] == 51587 and receipt["budget_passed"] is True
    tiny = llm_capacity.measure_request_capacity(
        messages, tools=None, output_tokens=8192, n_candidates=1, total_budget=4096,
    )
    assert tiny["budget_passed"] is False and tiny["reserved_total_tokens"] == 51587
    # Do not claim that approved delivery subsumes the legacy contract: only
    # chapter_number matches here; source coverage requires a separate proof.
    legacy = json.loads(kwargs["section_texts"]["contract_section"].split("\n", 1)[1])
    payload = json.loads(typed.text[typed.text.index("{"):])
    assert [key for key, value in legacy.items()
            if payload["chapter_contract"].get(key) == value] == ["chapter_number"]
    assert kwargs == before


@pytest.mark.parametrize("cjk", ["汉", "㐀", "豈"])
def test_adding_ascii_or_cjk_never_reduces_the_input_estimate(cjk):
    for count in range(170, 231):
        text = cjk * 99 + "x" * count
        before = compiler.estimate_prompt_tokens(text)
        assert before <= compiler.estimate_prompt_tokens(text + "x") <= before + 1
        assert before <= compiler.estimate_prompt_tokens(text + cjk) <= before + 1
    # A global script-ratio switch previously made removing bytes increase cost.
    assert compiler.estimate_prompt_tokens(cjk * 99 + "x" * 198) <= (
        compiler.estimate_prompt_tokens(cjk * 99 + "x" * 201)
    )


@pytest.mark.parametrize("text,expected", [
    ("", 0), ("x" * 5, 2), ("汉" * 5, 3),
    ("㐀" * 5, 3), ("豈" * 5, 3), ("汉" * 99 + "x" * 201, 100),
])
def test_shared_estimator_has_deterministic_componentwise_units(text, expected):
    assert compiler.estimate_prompt_tokens(text) == expected
    assert llm_capacity.estimate_prompt_tokens is compiler.estimate_prompt_tokens


def test_legacy_estimator_keeps_the_literal_historical_ratio_algorithm():
    assert compiler.estimate_legacy_prompt_tokens("") == 0
    assert compiler.estimate_legacy_prompt_tokens("汉" * 99 + "x" * 201) == 75
    assert compiler.estimate_legacy_prompt_tokens("汉" * 99 + "x" * 198) == 149
    # Historical counting did not include CJK Extension A or compatibility ideographs.
    assert compiler.estimate_legacy_prompt_tokens("㐀" * 99 + "x" * 198) == 75
    assert compiler.estimate_legacy_prompt_tokens("豈" * 99 + "x" * 198) == 75


@pytest.mark.parametrize("case", ["low", "high", "structured"])
def test_gateway_receipt_is_exactly_the_recorded_pre_repair_receipt(case):
    if case == "structured":
        kwargs = {
            "messages": [
                {"role": "user", "content": [{"type": "text", "text": "㐀汉豈abc" * 30}]},
                {"role": "tool", "content": "checked source", "tool_call_id": "original"},
            ],
            "tools": [{"type": "function", "function": {"name": "submit", "parameters": {
                "enum": ["全量", "retained"],
            }}}],
            "tool_choice": "required", "output_tokens": 4096, "n_candidates": 2,
        }
    else:
        kwargs = {
            "messages": [
                {"role": "system", "content": "Actual source scope"},
                {"role": "user", "content": "汉" * 99 + "x" * (201 if case == "low" else 198)},
            ],
            "tools": [{"description": "schema"}], "output_tokens": 16384,
        }
    # Captured before the source edit: compare the ENTIRE receipt, including the
    # legacy number, tool framing, mixed floor, 2.2 safety and candidate reserve.
    expected = {
        "schema": "calibrated-whole-model-request-capacity.v2",
        "estimator": (
            "componentwise-cjk-half-other-quarter; max with independent mixed-script floor"
        ),
        "safety_factor": 2.2, "observed_input_estimator_ratio_floor": 1.951,
        "total_budget": 65536, "message_count": 2, "budget_passed": True,
        "provider_usage_claimed": False, "financial_reservation_is_not_capacity": True,
        "truncated": False,
    }
    expected.update(dict(zip(
        ("estimated_input_tokens", "text_tokens", "tool_tokens", "legacy_estimated_input_tokens",
         "mixed_script_input_floor", "reserved_input_tokens", "output_reserve_tokens",
         "effective_max_output_tokens", "n_candidates", "message_chars", "tool_schema_chars",
         "reserved_total_tokens"),
        {
            "low": (128, 114, 14, 103, 253, 858, 16384, 16384, 1, 351, 55, 17242),
            "high": (127, 113, 14, 177, 252, 856, 16384, 16384, 1, 348, 55, 17240),
            "structured": (126, 94, 32, 103, 242, 854, 8192, 4096, 2, 277, 125, 9046),
        }[case], strict=True,
    )))
    before = deepcopy(kwargs)
    assert llm_capacity.measure_request_capacity(**kwargs) == expected
    assert kwargs == before


def test_added_full_typed_delivery_still_rejects_a_truly_oversized_necessary_core(
    actual_capture,
):
    # Generic approved task: fits alone, but cannot fit alongside this retained
    # complete source core. This is not a fabricated capture of the pending real task.
    approved = {
        "chapter_number": 2, "target_word_count": 2600,
        "chapter_goal": "保留完整时序与否定条件。" * 900,
        "scenes": [{"scene_number": 1, "participants": ["观察员"],
                    "entry_state": {"门锁": "已更换"}, "exit_state": {"凭证": "已核实"},
                    "action_sequence": ["核对留存凭证。不得撤销此前交接。"]}],
    }
    typed = delivery_block(
        project_id=uuid4(), chapter_id=uuid4(), chapter_number=2, artifact_id=uuid4(),
        approved=approved, receipt={"chapter_numbers": [2]},
    )
    standalone = compiler.compile_prompt(
        [typed], total_budget_tokens=14800, safety_margin=.1, require_provenance=True,
    )
    assert standalone.report.required_complete
    assert typed.required and typed.trim_policy == "preserve"
    assert typed.text in standalone.user
    before = deepcopy(actual_capture)
    with pytest.raises(compiler.PromptBudgetError) as caught:
        drafts._compile_rendered_writer_prompt(**{**actual_capture, "additional_blocks": [typed]})
    assert caught.value.required_keys
    assert caught.value.total_budget_tokens == 14800
    assert caught.value.safety_margin == .1
    assert actual_capture == before


def test_gateway_model_ceiling_and_effective_output_reserve_still_fail_closed():
    payload = {
        "messages": [{"role": "user", "content": "完整来源不可截断。"}],
        "tools": None, "max_tokens": 65536, "n_candidates": 2,
        "model": "unknown-fixture-model", "endpoint_hash": "0" * 64,
    }
    with pytest.raises(llm_capacity.ModelRequestCapacityError) as caught:
        llm_capacity.assert_effective_request_capacity(payload)
    receipt = caught.value.receipt
    assert receipt["total_budget"] == 65536
    assert receipt["effective_max_output_tokens"] == 65536
    assert receipt["output_reserve_tokens"] == 131072
    assert receipt["safety_factor"] == 2.2 and receipt["mixed_script_input_floor"] > 0
    assert receipt["reserved_total_tokens"] > receipt["total_budget"]
    assert receipt["budget_passed"] is False
