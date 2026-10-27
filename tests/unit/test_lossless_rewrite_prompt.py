"""Editor-only source encoding: exact legacy path, bounded lossless overflow.

All inputs are local JSON/fixtures. No DB or completion gateway is invoked.
Compatibility checks isolate an editor-only delta from frozen source images,
execute both policy hash functions, and reject broader current source changes.
"""

from __future__ import annotations

import ast
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from bestseller.services import chapter_review_reconciliation as reconciliation
from bestseller.services import lossless_adopted_span_encoding as spans
from bestseller.services import lossless_rewrite_prompt as editor
from bestseller.services import lossless_source_encoding as codec
from bestseller.services import pipelines
from bestseller.services import review_source_context as legacy
from bestseller.services.prompt_compiler import PromptBudgetError

pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[2]
SERVICES = ROOT / "src/bestseller/services"
REPORTS = ROOT / "reports/continuous-recovery-2026-10-16"
FROZEN = REPORTS / "freezes/before-lossless-editor-source"
CAPTURE = REPORTS / "20261017T161015909513Z-rewrite-source-capacity-diagnostic.json"


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def overflow_source():
    text = "星图已经封存,船员继续记录后果。" * 1100 + "完整尾证据_END_不可省略"
    subtree = {"body": text, "entry": "船员已到站", "exit": "发出新信号"}
    return {
        "adopted_continuity": {"body": text},
        "editable_plan": {"authority": "revision_target_not_adopted_history",
                          "scenes": [deepcopy(subtree) for _ in range(8)]},
        "literal_authored_ref": {"$ref": "/this-is-authored-data-not-a-declaration"},
    }


def kwargs(source):
    return {"system_prompt": "Editor protocol: preserve exact sources.",
            "user_prompt": "原始完整任务_JSON_不可改写\n" + "独立任务结尾_TASK_END",
            "source_context": source, "output_tokens": 8832}


def assert_encoded_result(arguments, result):
    system, user, receipt = result
    encoded, _ = json.JSONDecoder().raw_decode(user)
    decoded = (spans.decode_adopted_spans(encoded) if encoded.get("schema") == spans.SCHEMA
               else codec.decode_lossless_source(encoded))
    assert decoded == arguments["source_context"]
    assert canonical(decoded) == canonical(arguments["source_context"])
    assert arguments["user_prompt"] in user
    assert user.endswith(arguments["user_prompt"])
    assert receipt["source_sha256"] == digest(decoded)
    assert receipt["source_transport_sha256"] == digest(encoded)
    assert receipt["source_transport_sha256"] != receipt["source_sha256"]
    assert receipt["source_roundtrip_verified"] is True
    assert receipt["editor_source_encoding_policy"] == editor.POLICY
    assert receipt["budget_increased"] is False and receipt["reviewer_inputs_changed"] is False
    assert receipt["total_budget"] == legacy.TOTAL_BUDGET == 65536
    assert receipt["output_reserve_tokens"] == arguments["output_tokens"] == 8832
    compiler = receipt["compiler"]
    assert not compiler["dropped"] and not compiler["truncated"]
    assert compiler["source_integrity_complete"] and receipt["source_complete"]
    assert compiler["total_tokens"] <= compiler["budget_tokens"]
    assert compiler["total_tokens"] * receipt["safety_factor"] + 8832 <= 65536
    assert editor.PROTOCOL in system
    return encoded, decoded


def test_fitting_request_is_byte_exact_legacy_including_receipt_without_codec(monkeypatch):
    arguments = kwargs({"body": "门已锁死。", "duplicate": ["short", "short"],
                        "literal": {"$ref": "/not-a-reference"}})
    expected = legacy.prepare_source_complete_prompt(**arguments)

    def forbidden(*_args, **_kwargs):
        pytest.fail("fitting legacy input must never enter codec")

    monkeypatch.setattr(codec, "encode_lossless_source", forbidden)
    monkeypatch.setattr(spans, "encode_adopted_spans", forbidden)
    assert editor.prepare_source_complete_prompt(**arguments) == expected
    assert "editor_source_encoding_policy" not in expected[2]


def test_overflow_deduplication_preserves_all_values_task_tail_and_authored_refs():
    arguments = kwargs(overflow_source())
    before = deepcopy(arguments)
    with pytest.raises(PromptBudgetError):
        legacy.prepare_source_complete_prompt(**arguments)
    result = editor.prepare_source_complete_prompt(**arguments)
    encoded, decoded = assert_encoded_result(arguments, result)
    assert encoded["references"]
    assert len(canonical(encoded)) < len(canonical(decoded))
    assert decoded["literal_authored_ref"] == before["source_context"]["literal_authored_ref"]
    assert all(s["body"].endswith("完整尾证据_END_不可省略")
               for s in decoded["editable_plan"]["scenes"])
    assert arguments == before


def test_historical_estimator_capture_fits_losslessly_with_exact_task_and_source_hash(monkeypatch):
    from bestseller.services import prompt_compiler
    # Preserve the original overflow counterexample under its original
    # estimator. The monotonic estimator can fit these same raw bytes today.
    monkeypatch.setattr(prompt_compiler, "estimate_prompt_tokens", prompt_compiler.estimate_legacy_prompt_tokens)
    capture = json.loads(CAPTURE.read_text())
    assert capture["provider_calls"] == capture["database_writes"] == 0
    assert capture["output_reserve"] == 8832
    arguments = {
        "system_prompt": capture["system"], "user_prompt": capture["user"],
        "source_context": capture["source_context"], "output_tokens": capture["output_reserve"],
    }
    before = deepcopy(arguments)
    with pytest.raises(PromptBudgetError):
        legacy.prepare_source_complete_prompt(**arguments)
    pointer_only = codec.encode_lossless_source(arguments["source_context"])
    assert codec.decode_lossless_source(pointer_only) == arguments["source_context"]
    # Retain the counterexample: byte savings from long-pointer deduplication
    # alone do not establish that this real CJK request fits.
    assert pointer_only["receipt"]["encoded_bytes"] < pointer_only["receipt"]["original_bytes"]
    assert len(canonical(pointer_only)) >= len(canonical(arguments["source_context"]))
    result = editor.prepare_source_complete_prompt(**arguments)
    encoded, decoded = assert_encoded_result(arguments, result)
    assert encoded["schema"] == spans.SCHEMA
    assert encoded["integrity"]["original_source_sha256"] == digest(decoded)
    assert encoded["integrity"]["span_count"] > 0
    assert decoded["adopted_continuity"] == capture["source_context"]["adopted_continuity"]
    assert decoded["editable_plan"] == capture["source_context"]["editable_plan"]
    for row in decoded["adopted_continuity"]["recent_adopted_prose"]:
        body_literal = json.dumps(row["body"], ensure_ascii=False)[1:-1]
        assert canonical(encoded).count(body_literal) == 1
    assert result[1].count(arguments["user_prompt"]) == 1
    assert result[2]["original_source_chars"] == len(canonical(arguments["source_context"]))
    assert result[2]["encoded_source_chars"] == len(canonical(encoded))
    assert arguments == before


@pytest.mark.parametrize("fault", ["span_hash", "false_roundtrip"])
def test_real_span_transport_tampering_never_emits_a_complete_receipt(monkeypatch, fault):
    from bestseller.services import prompt_compiler
    # Exercise the overflow codec, rather than bypassing it with a now-fitting
    # raw request under the current monotonic estimator.
    monkeypatch.setattr(prompt_compiler, "estimate_prompt_tokens", prompt_compiler.estimate_legacy_prompt_tokens)
    capture = json.loads(CAPTURE.read_text())
    arguments = {"system_prompt": capture["system"], "user_prompt": capture["user"],
                 "source_context": capture["source_context"], "output_tokens": 8832}
    before = deepcopy(arguments)
    if fault == "span_hash":
        original = spans.encode_adopted_spans

        def tampered(source):
            encoded = original(source)
            encoded["integrity"]["original_source_sha256"] = "0" * 64
            return encoded

        monkeypatch.setattr(spans, "encode_adopted_spans", tampered)
    else:
        original = spans.decode_adopted_spans

        def tampered(encoded):
            decoded = original(encoded)
            decoded["authority"] = "forged source authority"
            return decoded

        monkeypatch.setattr(spans, "decode_adopted_spans", tampered)
    expected_error = ("adopted_span_original_source_changed" if fault == "span_hash"
                      else "adopted_span_roundtrip_changed")
    with pytest.raises(ValueError, match=expected_error):
        editor.prepare_source_complete_prompt(**arguments)
    assert arguments == before


def test_current_estimator_preserves_exact_fitting_capture_without_encoding():
    capture = json.loads(CAPTURE.read_text())
    arguments = {"system_prompt": capture["system"], "user_prompt": capture["user"],
                 "source_context": capture["source_context"], "output_tokens": capture["output_reserve"]}
    before = deepcopy(arguments)
    expected = legacy.prepare_source_complete_prompt(**arguments)
    assert editor.prepare_source_complete_prompt(**arguments) == expected
    assert canonical(arguments["source_context"]) in expected[1]
    assert arguments["user_prompt"] in expected[1]
    assert expected[2]["source_complete"] is True
    assert "editor_source_encoding_policy" not in expected[2]
    assert arguments == before


@pytest.mark.parametrize("fault", ["tampered_reference", "tampered_hash", "false_roundtrip"])
def test_tampered_codec_cannot_produce_a_complete_source_receipt(monkeypatch, fault):
    arguments = kwargs(overflow_source())
    before = deepcopy(arguments)
    original_encode = codec.encode_lossless_source
    original_decode = codec.decode_lossless_source

    def tampered(value):
        encoded = original_encode(value)
        if fault == "tampered_reference":
            encoded["references"][0]["target"] = "/missing"
        elif fault == "tampered_hash":
            encoded["receipt"]["original_sha256"] = "0" * 64
        return encoded

    monkeypatch.setattr(codec, "encode_lossless_source", tampered)
    if fault == "false_roundtrip":
        def false_decode(encoded):
            decoded = original_decode(encoded)
            decoded["adopted_continuity"]["body"] = "tail omitted"
            return decoded
        monkeypatch.setattr(codec, "decode_lossless_source", false_decode)
    with pytest.raises((ValueError, PromptBudgetError)):
        editor.prepare_source_complete_prompt(**arguments)
    assert arguments == before


def test_truly_oversized_unique_source_fails_without_reserve_or_budget_growth(monkeypatch):
    arguments = kwargs({"single_unique_source_blob": "正文不可截断" * 40000})
    original_prepare = editor._prepare
    attempts = []

    def prepare(**values):
        attempts.append(values)
        assert values["output_tokens"] == 8832
        assert legacy.TOTAL_BUDGET == 65536
        return original_prepare(**values)

    monkeypatch.setattr(editor, "_prepare", prepare)
    with pytest.raises(PromptBudgetError):
        editor.prepare_source_complete_prompt(**arguments)
    assert attempts and all(a["user_prompt"] == arguments["user_prompt"] for a in attempts)


def policy_hash_for_source(source):
    tree = ast.parse(source.decode())
    function = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
                    and n.name == "chapter_review_recovery_source_hash")
    namespace = {"Path": Path, "hashlib": hashlib,
                 "__file__": str(SERVICES / "pipelines.py")}
    exec(compile(ast.Module(body=[function], type_ignores=[]), "fixture-policy-hash", "exec"),
         namespace)
    return namespace["chapter_review_recovery_source_hash"]()


def replace_definitions(source, replacements):
    text = source.decode()
    lines = text.splitlines(keepends=True)
    nodes = [node for node in ast.parse(text).body
             if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
             and node.name in replacements]
    assert {node.name for node in nodes} == set(replacements)
    assert len(nodes) == len(replacements)
    for node in reversed(nodes):
        assert not node.decorator_list
        lines[node.lineno - 1:node.end_lineno] = [replacements[node.name] + "\n"]
    return "".join(lines).encode()


@pytest.fixture
def isolated_editor_policy(monkeypatch):
    """Synthetic editor-only after-image; never a compatibility claim for today.

    Only four historical files are retained. Other inventory dependencies use
    identical current bytes on both sides, not invented historical hashes.
    """
    old_pipeline = (FROZEN / "pipelines.py").read_bytes()
    old_reviews = (FROZEN / "reviews.py").read_bytes()
    function = next(node for node in ast.parse(old_pipeline.decode()).body
                    if isinstance(node, ast.FunctionDef)
                    and node.name == "chapter_review_recovery_source_hash")
    inventory = next(node for node in ast.walk(function)
                     if isinstance(node, ast.Tuple)
                     and any(isinstance(value, ast.Constant) and value.value == "pipelines.py"
                             for value in node.elts))
    added_files = {"lossless_rewrite_prompt.py", "lossless_source_encoding.py",
                   "lossless_adopted_span_encoding.py"}
    assert not added_files & {value.value for value in inventory.elts}
    inventory.elts.extend(ast.Constant(value) for value in sorted(added_files))
    new_pipeline = replace_definitions(
        old_pipeline, {function.name: ast.unparse(ast.fix_missing_locations(function))},
    )
    current_reviews = (SERVICES / "reviews.py").read_text()
    editor_functions = {
        node.name: ast.get_source_segment(current_reviews, node)
        for node in ast.parse(current_reviews).body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name in {"rewrite_chapter_from_task", "build_chapter_rewrite_prompts"}
    }
    new_reviews = replace_definitions(old_reviews, editor_functions)
    assert new_reviews != old_reviews
    images = {
        SERVICES / "pipelines.py": new_pipeline,
        SERVICES / "reviews.py": new_reviews,
        SERVICES / "review_source_context.py": (FROZEN / "review_source_context.py").read_bytes(),
    }
    original_read = Path.read_bytes

    def current_read(path):
        return images[path] if path in images else original_read(path)

    monkeypatch.setattr(Path, "read_bytes", current_read)
    current_hash = policy_hash_for_source(new_pipeline)
    old_images = {SERVICES / "pipelines.py": old_pipeline, SERVICES / "reviews.py": old_reviews}

    def old_read(path):
        return old_images[path] if path in old_images else current_read(path)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "read_bytes", old_read)
        old_hash = policy_hash_for_source(old_pipeline)
    return {
        "root": ROOT, "previous_pipeline_source": old_pipeline,
        "previous_review_source": old_reviews,
        "expected_previous_policy_hash": old_hash,
        "expected_current_policy_hash": current_hash,
    }


def test_isolated_frozen_editor_only_change_reproduces_both_policy_hashes(isolated_editor_policy):
    expected_files = json.loads((FROZEN / "hashes.json").read_text())
    for name, expected in expected_files.items():
        assert hashlib.sha256((FROZEN / name).read_bytes()).hexdigest() == expected
    # The isolated editor-only image holds the shared reviewer context fixed.
    assert (SERVICES / "review_source_context.py").read_bytes() == (
        FROZEN / "review_source_context.py"
    ).read_bytes()
    old_hash = isolated_editor_policy["expected_previous_policy_hash"]
    current_hash = isolated_editor_policy["expected_current_policy_hash"]
    proof = reconciliation.candidate_selection_policy_compatibility(**isolated_editor_policy)
    assert old_hash != current_hash
    assert proof["previous_policy_hash"] == old_hash
    assert proof["current_policy_hash"] == current_hash
    assert proof["unchanged_review_evaluator_ast"] and proof["unchanged_evaluation_ast"]
    assert {"lossless_rewrite_prompt.py", "lossless_source_encoding.py",
            "lossless_adopted_span_encoding.py"} <= set(
        proof["allowed_delta"]
    )


def test_compatibility_rejects_changed_review_evaluator_even_for_editor_migration(
    isolated_editor_policy,
):
    arguments = {**isolated_editor_policy, "previous_review_source": (
        isolated_editor_policy["previous_review_source"] + b"\nOTHER_POLICY=True\n"
    )}
    with pytest.raises(ValueError, match="review_compatibility_review_evaluator_changed"):
        reconciliation.candidate_selection_policy_compatibility(**arguments)


def test_compatibility_rejects_wrong_historical_policy_hash(isolated_editor_policy):
    arguments = {**isolated_editor_policy, "expected_previous_policy_hash": "0" * 64}
    with pytest.raises(ValueError, match="review_compatibility_original_policy_bytes_unverified"):
        reconciliation.candidate_selection_policy_compatibility(**arguments)


def test_actual_current_broader_source_changes_reject_editor_only_compatibility():
    assert (SERVICES / "review_source_context.py").read_bytes() != (
        FROZEN / "review_source_context.py"
    ).read_bytes()
    with pytest.raises(ValueError, match="review_compatibility_evaluation_code_changed"):
        reconciliation.candidate_selection_policy_compatibility(
            root=ROOT, previous_pipeline_source=(FROZEN / "pipelines.py").read_bytes(),
            previous_review_source=(FROZEN / "reviews.py").read_bytes(),
            expected_previous_policy_hash="0" * 64,
            expected_current_policy_hash=pipelines.chapter_review_recovery_source_hash(),
        )
