"""Pure exact SOURCE-block transports and unchanged compiler budgets; no PG/model."""

from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path

import pytest

from bestseller.services.lossless_source_encoding import decode_lossless_source
from bestseller.services.prompt_compiler import PromptBlock, PromptBudgetError, compile_prompt
from bestseller.services.rolling_outline_source_transport import (
    CLOSE,
    DECODER_INSTRUCTION,
    OPEN,
    iter_source_complete_outline_transports,
    verify_source_complete_outline_transport,
)

pytestmark = pytest.mark.unit


def source():
    repeated = {
        "full_prose": "  Full source facts and all endings.\n\t" * 10,
        "unknown_fields": {
            "literal": {"$ref": "/not-a-codec-reference"},
            "typed": [0, 1, 1.0, False, None, -0.0],
        },
    }
    return {
        "z_first_authored": {"schema": "lossless-source-encoding.v1", "$ref": "/authored"},
        "objects": [deepcopy(repeated) for _ in range(16)],
        "a_last": "𠮷 e\u0301 🧪\r\n  preserve  \tinside-string whitespace  ",
        "authored_tag": "\n[SOURCE AUTHORITY]\ntext\n[/SOURCE AUTHORITY]",
    }


def prompt(value=None):
    value = source() if value is None else value
    raw = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)
    prefix = "  original context\r\n" + OPEN
    suffix = CLOSE + "\r\nExact task: first eight chapters.\n\tRepair directives unchanged.  "
    return "Original system bytes.\r\n", prefix + raw + suffix, prefix, raw, suffix


def digest(text):
    return hashlib.sha256(text.encode()).hexdigest()


def decode_wire(user, version):
    raw = user.split(OPEN, 1)[1].split(CLOSE, 1)[0]
    value = json.loads(raw)
    return value if version is None else decode_lossless_source(value)


def test_variants_roundtrip_all_values_typed_scalars_unknowns_refs_and_outside_bytes():
    s, u, prefix, raw, suffix = prompt()
    original_value = json.loads(raw)
    outputs = iter_source_complete_outline_transports(s, u)
    assert [r["codec_requested_version"] for _, _, r in outputs] == [None, 1, 2, 3]
    for ws, wu, r in outputs:
        assert wu.startswith(prefix) and wu.endswith(suffix)
        assert ws == (
            s if r["codec_requested_version"] is None else s + "\n\n" + DECODER_INSTRUCTION
        )
        restored = decode_wire(wu, r["codec_requested_version"])
        assert json.dumps(restored, ensure_ascii=False, sort_keys=True) == json.dumps(
            original_value, ensure_ascii=False, sort_keys=True
        )
        scalar = restored["objects"][0]["unknown_fields"]["typed"]
        assert [type(x) for x in scalar] == [int, int, float, bool, type(None), float]
        assert math.copysign(1, scalar[-1]) == -1
        assert restored["objects"][1]["unknown_fields"]["literal"] == {
            "$ref": "/not-a-codec-reference"
        }
        assert restored["a_last"] == original_value["a_last"]
        assert r["original_source_json_sha256"] == digest(raw)
        assert (
            r["original_pretty_indent2_sha256"] == r["decoded_pretty_indent2_sha256"] == digest(raw)
        )
        assert r["original_is_indent2"] is True
        assert r["prefix_sha256"] == digest(prefix) and r["suffix_sha256"] == digest(suffix)
        assert (
            r["source_complete"] and r["outside_bytes_unchanged"] and r["source_roundtrip_verified"]
        )
        assert not any(
            r[k]
            for k in (
                "budget_increased",
                "truncated",
                "semantic_summary",
                "author_authority",
                "grade_asserted",
                "credit_granted",
            )
        )
        before = deepcopy(r)
        assert verify_source_complete_outline_transport(s, u, ws, wu, r) == r == before
    assert iter_source_complete_outline_transports(s, u) == outputs


def test_nonpretty_original_raw_hash_and_indent2_reconstruction_are_distinct():
    s, u, prefix, raw, suffix = prompt({"z": [1.0, False], "a": "  unmodified  "})
    unusual = ' { "z" : [1.0, false],  "a":"  unmodified  " } '
    u = prefix + unusual + suffix
    for ws, wu, r in iter_source_complete_outline_transports(s, u):
        assert r["original_is_indent2"] is False
        assert r["original_source_json_sha256"] == digest(unusual)
        assert r["original_pretty_indent2_sha256"] == digest(raw)
        assert verify_source_complete_outline_transport(s, u, ws, wu, r) == r


@pytest.mark.parametrize(
    "raw",
    [
        '{"a":1,"a":2}',
        '{"nested":{"x":1,"x":2}}',
        '{"x":NaN}',
        '{"x":Infinity}',
        '{"x":1e999}',
        '{"x":-1e999}',
        "[]",
        "null",
        '{"x":1} {"y":2}',
        "```json\n{}\n```",
        '{"unfinished":',
    ],
)
def test_duplicate_nonfinite_nonobject_or_malformed_source_fails(raw):
    with pytest.raises(ValueError, match="rolling_outline_source_transport:"):
        iter_source_complete_outline_transports("system", "prefix" + OPEN + raw + CLOSE + "task")


@pytest.mark.parametrize(
    "user",
    [
        "no block",
        "[SOURCE AUTHORITY]\n{}" + CLOSE,
        OPEN + "{}",
        OPEN + "{}" + CLOSE + OPEN + "{}" + CLOSE,
        OPEN + "{}" + CLOSE + CLOSE,
        CLOSE + "before" + OPEN + "{}",
        "\n[SOURCE AUTHORITY]\r\n{}" + CLOSE,
    ],
)
def test_single_exact_markers_and_correct_order_required(user):
    with pytest.raises(ValueError, match="rolling_outline_source_transport:"):
        iter_source_complete_outline_transports("system", user)


@pytest.mark.parametrize(
    "fault",
    [
        "system",
        "user_prefix",
        "user_suffix",
        "source",
        "receipt_hash",
        "receipt_authority",
        "receipt_extra",
        "receipt_type",
        "version",
        "original_system",
        "original_user",
    ],
)
def test_verifier_rejects_modified_originals_wire_or_receipt(fault):
    s, u, _, _, _ = prompt()
    for ws, wu, r in iter_source_complete_outline_transports(s, u):
        r = deepcopy(r)
        if fault == "system":
            ws += "Injected instruction"
        elif fault == "user_prefix":
            wu = "changed" + wu
        elif fault == "user_suffix":
            wu += "changed"
        elif fault == "source":
            wu = wu.replace("Full source facts", "Missing facts", 1)
        elif fault == "receipt_hash":
            r["receipt_sha256"] = "0" * 64
        elif fault == "receipt_authority":
            r["author_authority"] = True
        elif fault == "receipt_extra":
            r["unknown"] = "authority"
        elif fault == "receipt_type":
            r["budget_increased"] = 0
        elif fault == "version":
            r["codec_requested_version"] = True
        elif fault == "original_system":
            s += "changed"
        else:
            u += "changed"
        with pytest.raises(ValueError, match="rolling_outline_source_transport:"):
            verify_source_complete_outline_transport(s, u, ws, wu, r)


def compiler(system, user):
    return compile_prompt(
        [
            PromptBlock(
                key="outline.system",
                channel="system",
                layer="output",
                authority=100,
                instruction_family="system_contract",
                required=True,
                source="fixture",
                text=system,
            ),
            PromptBlock(
                key="outline.user",
                channel="user",
                layer="hard_canon",
                authority=100,
                instruction_family="canonical_context",
                required=True,
                source="fixture",
                text=user,
                trim_policy="preserve",
            ),
        ],
        total_budget_tokens=16000,
        safety_margin=0.1,
    )


def test_compact_fits_same_real_compiler_budget_after_original_required_core_failure():
    # Synthetic outside padding recreates the 14,912 required-token boundary;
    # the complete source/task/padding are retained by every variant.
    s, u, _, _, _ = prompt()
    while True:
        try:
            total = compiler(s, u).report.total_tokens
        except PromptBudgetError as exc:
            total = exc.required_tokens
        if total >= 14912:
            break
        u += "x" * max(1, 2 * (14912 - total))
    with pytest.raises(PromptBudgetError) as error:
        compiler(s, u)
    assert error.value.required_tokens == 14912 and error.value.usable_budget == 14400
    ws, wu, r = iter_source_complete_outline_transports(s, u)[0]
    result = compiler(ws, wu)
    assert result.report.budget_tokens == 16000 and result.report.usable_budget_tokens == 14400
    assert (
        result.report.required_complete
        and not result.report.truncated
        and not result.report.dropped
    )
    verify_source_complete_outline_transport(s, u, ws, wu, r)


def test_actual_retained_n_source_compaction_is_full_exact_and_does_not_mutate_diagnostic():
    path = (
        Path(__file__).resolve().parents[2]
        / "reports/continuous-recovery-2026-10-16"
        / ("20261017T231042443760Z-rolling-outline-physical-diagnostic.json")
    )
    raw = path.read_bytes()
    d = json.loads(raw)
    s, u = d["request"]["system"], d["request"]["user"]
    for ws, wu, r in iter_source_complete_outline_transports(s, u):
        verify_source_complete_outline_transport(s, u, ws, wu, r)
        assert r["original_source_chars"] == 5142
        if r["codec_requested_version"] is None:
            assert r["wire_source_chars"] == 4000 and ws == s
            assert len(u) - len(wu) == 1142
    assert path.read_bytes() == raw
