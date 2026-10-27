"""Complete primary source transport: pure local source/physical-wire evidence only."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from bestseller.services import primary_review_transport as transport
from bestseller.services import review_source_context as legacy
from bestseller.services import lossless_source_encoding as pointers
from bestseller.services import lossless_adopted_span_encoding as spans
from bestseller.services.prompt_compiler import PromptBudgetError, estimate_prompt_tokens

pytestmark = pytest.mark.unit
REPORTS = Path(__file__).resolve().parents[2] / "reports/continuous-recovery-2026-10-16"
CAPTURE = REPORTS / "20261017T212213835889Z-primary-review-transport-preflight.json"


@pytest.fixture
def historical_estimator(monkeypatch):
    from bestseller.services import prompt_compiler
    import sys
    old = prompt_compiler.estimate_legacy_prompt_tokens
    monkeypatch.setattr(prompt_compiler, "estimate_prompt_tokens", old)
    monkeypatch.setattr(transport, "estimate_prompt_tokens", old)
    monkeypatch.setattr(sys.modules[__name__], "estimate_prompt_tokens", old)


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def arguments(source=None):
    text = "船员逐项记录已发生的事实🌕。不得省略末尾证据。" * 1400
    return {
        "system_prompt": "Primary protocol, original full system instructions.",
        "user_prompt": 'CURRENT_DRAFT:\n完整当前正文。\nEND_CURRENT_DRAFT\nBODY_ANCHOR_CATALOG:\n'
                       '[{"paragraph_number":1,"text":"完整当前正文。"}]\nRETRY: preserve every prior error exactly.',
        "source_context": source if source is not None else {
            "observed": text, "copies": [{"full": text, "index": i} for i in range(8)],
            "authored": {"$ref": "/not-declared"},
        },
        "output_tokens": 8192,
        "tools": [{"type": "function", "function": {"name": "submit_chapter_critique",
                  "parameters": {"type": "object", "properties": {"paragraph_number": {"enum": [1]}}}}}],
    }


def verify(args, result):
    return transport.verify_primary_review_transport(
        result[0], result[1], source_context=args["source_context"], receipt=result[2],
        tools=args["tools"], output_tokens=args["output_tokens"],
    )


def assert_lossless(args, result):
    system, user, receipt = result
    proof = receipt["primary_source_transport"]
    packet = user[proof["packet_start"]:proof["packet_end"]]
    encoded = json.loads(packet)
    restored = (spans.decode_adopted_spans(encoded) if proof["encoding"] == "adopted-spans"
                else pointers.decode_lossless_source(encoded))
    assert canonical(restored) == canonical(args["source_context"])
    assert args["system_prompt"] in system
    assert user[proof["packet_end"]:] == "\n\n" + args["user_prompt"]
    assert receipt["schema"] == legacy.SOURCE_POLICY
    assert receipt["source_sha256"] == sha(canonical(args["source_context"]))
    assert receipt["source_complete"] is True
    cap = proof["capacity"]
    tools_text = canonical(args["tools"]) if args["tools"] is not None else ""
    expected = estimate_prompt_tokens(system) + estimate_prompt_tokens(user) + estimate_prompt_tokens(tools_text)
    assert cap["estimated_input_tokens"] == expected
    assert cap["reserved_input_tokens"] == int(expected * 1.5) + 512
    assert cap["reserved_input_tokens"] + args["output_tokens"] <= 65536
    assert cap["combined_characters"] == len(system) + len(user) + len(tools_text) <= 120000
    assert cap["budget_passed"] and cap["truncated"] is False
    assert not receipt["compiler"]["dropped"] and not receipt["compiler"]["truncated"]
    assert verify(args, result) is None
    # JSONB/file-compatible receipt serialization must retain the exact proof.
    assert verify(args, (system, user, json.loads(json.dumps(receipt)))) is None
    return proof


def test_identity_matches_existing_bytes_and_entire_receipt_without_entering_codecs(monkeypatch):
    args = arguments({"fact": "门已关闭。", "literal": {"$ref": 0}})
    plain = {k: v for k, v in args.items() if k != "tools"}
    old = legacy.prepare_source_complete_prompt(**plain)

    def forbidden(*args, **kwargs):
        pytest.fail("A fitting literal request must not enter any codec")
    monkeypatch.setattr(pointers, "encode_lossless_source", forbidden)
    monkeypatch.setattr(spans, "encode_adopted_spans", forbidden)
    result = transport.prepare_source_complete_prompt(**args)
    assert result == old and "primary_source_transport" not in result[2]
    assert verify(args, result) is None


def test_overflow_preserves_full_task_sources_controls_authored_refs_and_retry():
    args = arguments()
    args["system_prompt"] = "  " + args["system_prompt"] + "  "
    args["user_prompt"] = "\n  " + args["user_prompt"] + "\n  "
    before = deepcopy(args)
    with pytest.raises(PromptBudgetError):
        legacy.prepare_source_complete_prompt(**{k: v for k, v in args.items() if k != "tools"})
    result = transport.prepare_source_complete_prompt(**args)
    proof = assert_lossless(args, result)
    assert proof["encoding"] == "json-pointer"
    assert args == before
    assert transport.prepare_source_complete_prompt(**args) == result


@pytest.mark.usefixtures("historical_estimator")
def test_historical_r3_reconstruction_fits_full_tools_body_catalog_and_source_v3():
    capture = json.loads(CAPTURE.read_text())
    assert capture["provider_calls"] == capture["database_writes"] == 0
    args = {k: capture[k] for k in ("source_context", "tools", "output_tokens")}
    args.update(system_prompt=capture["system"], user_prompt=capture["user"])
    before = deepcopy(args)
    result = transport.prepare_source_complete_prompt(**args)
    proof = assert_lossless(args, result)
    assert proof["encoding"] == "key-dictionary"
    assert proof["capacity"]["tool_schema_estimated_tokens"] > 0
    assert args["output_tokens"] == 8192 and args == before
    assert proof["original_source_characters"] == 50620
    # This is a persisted-current-report reconstruction, NOT the exact historical
    # failed in-memory request; no old report or effective-input identity changes.
    assert capture["transport_not_yet_integrated"]


def test_v2_retries_after_real_v1_capacity_failure_without_growing_budget():
    args = arguments()
    first = transport.prepare_source_complete_prompt(**args)
    proof = assert_lossless(args, first)
    assert proof["encoding"] == "json-pointer"
    cap = proof["capacity"]
    args["user_prompt"] += "\n" + "证" * (2 * (cap["input_allowance_tokens"] - cap["estimated_input_tokens"]) + 2)
    result = transport.prepare_source_complete_prompt(**args)
    proof2 = assert_lossless(args, result)
    assert proof2["encoding"] == "indexed-json-pointer"
    assert proof2["capacity"]["total_token_budget"] == 65536


@pytest.mark.usefixtures("historical_estimator")
def test_historical_adopted_span_fallback_uses_full_corpus_not_quotes_or_summary():
    capture = json.loads((REPORTS / "20261017T161015909513Z-rewrite-source-capacity-diagnostic.json").read_text())
    args = arguments(capture["source_context"])
    args.update(system_prompt=capture["system"], user_prompt=capture["user"], output_tokens=capture["output_reserve"])
    result = transport.prepare_source_complete_prompt(**args)
    proof = assert_lossless(args, result)
    assert proof["encoding"] == "adopted-spans"
    encoded = json.loads(result[1][:proof["packet_end"]])
    assert encoded["integrity"]["span_count"] > 0
    for row in args["source_context"]["adopted_continuity"]["recent_adopted_prose"]:
        body_literal = json.dumps(row["body"], ensure_ascii=False)[1:-1]
        assert canonical(encoded).count(body_literal) == 1


def test_current_estimator_preserves_raw_r3_capture_and_physical_tools():
    capture = json.loads(CAPTURE.read_text())
    args = {k: capture[k] for k in ("source_context", "tools", "output_tokens")}
    args.update(system_prompt=capture["system"], user_prompt=capture["user"])
    before = deepcopy(args)
    result = transport.prepare_source_complete_prompt(**args)
    assert result[2]["source_complete"] is True
    assert "primary_source_transport" not in result[2]
    assert canonical(args["source_context"]) in result[1]
    assert args["user_prompt"] in result[1]
    assert args == before and verify(args, result) is None


@pytest.mark.parametrize("fault", ["system", "task", "source", "tools", "output", "codec_target",
    "offset_bool", "offset_oob", "source_hash", "task_hash", "capacity", "unknown_policy", "extra_proof", "duplicate_key"])
def test_retained_wire_verifier_rejects_every_changed_bound_component(fault):
    args = arguments()
    system, user, receipt = transport.prepare_source_complete_prompt(**args)
    receipt = deepcopy(receipt)
    proof = receipt["primary_source_transport"]
    if fault == "system": system += "changed"
    elif fault == "task": user += "changed"
    elif fault == "source": args["source_context"]["last_fact"] = "A new fact"
    elif fault == "tools": args["tools"][0]["function"]["name"] = "other"
    elif fault == "output": args["output_tokens"] -= 1
    elif fault == "offset_bool": proof["packet_start"] = False
    elif fault == "offset_oob": proof["packet_end"] = len(user) + 1
    elif fault == "source_hash": receipt["source_sha256"] = "0" * 64
    elif fault == "task_hash": proof["original_task_sha256"] = "0" * 64
    elif fault == "capacity": proof["capacity"]["reserved_input_tokens"] -= 1
    elif fault == "unknown_policy": proof["schema"] = "unknown.v1"
    elif fault == "extra_proof": proof["paid_replay_allowed"] = True
    else:
        packet, suffix = user[:proof["packet_end"]], user[proof["packet_end"]:]
        if fault == "duplicate_key": packet = packet[:-1] + ',"encoding":"json-pointer"}'
        else:
            encoded = json.loads(packet)
            encoded["references"][0]["target"] = "/missing"
            packet = canonical(encoded)
        user = packet + suffix
        proof.update(packet_end=len(packet), wire_source_sha256=sha(packet), wire_user_sha256=sha(user))
    with pytest.raises((ValueError, PromptBudgetError)):
        transport.verify_primary_review_transport(system, user, source_context=args["source_context"],
            receipt=receipt, tools=args["tools"], output_tokens=args["output_tokens"])


def test_valid_other_codec_packet_cannot_substitute_different_source_even_with_rebound_hashes():
    args = arguments()
    system, user, receipt = transport.prepare_source_complete_prompt(**args)
    proof = receipt["primary_source_transport"]
    changed = deepcopy(args["source_context"])
    changed["current_author_rule"] = "A forged rule."
    encoded = pointers.encode_lossless_source(changed)
    packet = canonical(encoded)
    user = packet + user[proof["packet_end"]:]
    proof.update(packet_end=len(packet), wire_source_sha256=sha(packet), wire_user_sha256=sha(user))
    with pytest.raises(ValueError, match="decoded_source_binding"):
        transport.verify_primary_review_transport(system, user, source_context=args["source_context"],
            receipt=receipt, tools=args["tools"], output_tokens=args["output_tokens"])


@pytest.mark.parametrize("part", ["task", "system", "tools", "source"])
def test_true_overflow_cannot_drop_any_field_or_raise_budget(part):
    args = arguments()
    unique = "".join(hashlib.sha256(str(i).encode()).hexdigest() for i in range(2500))
    if part == "task": args["user_prompt"] += unique
    elif part == "system": args["system_prompt"] += unique
    elif part == "tools": args["tools"][0]["function"]["description"] = unique
    else: args["source_context"] = {"unique_complete_source": unique, "last_fact": "Cannot omit."}
    before = deepcopy(args)
    with pytest.raises(PromptBudgetError):
        transport.prepare_source_complete_prompt(**args)
    assert args == before and legacy.TOTAL_BUDGET == 65536 and legacy.SAFETY_FACTOR == 1.5


@pytest.mark.parametrize("value", [float("nan"), float("inf"), ("tuple",), {1: "nonstring key"}])
def test_no_silent_source_json_coercion(value):
    with pytest.raises(ValueError):
        transport.prepare_source_complete_prompt(**arguments({"invalid": value}))
