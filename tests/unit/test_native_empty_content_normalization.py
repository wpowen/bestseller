"""Exact wrapper normalization, physical JSON fidelity and conflict rejection."""

from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest
from test_retained_candidate_native_patch import BODY, completion, payload, prepared

from bestseller.services import llm
from bestseller.services import retained_candidate_native_patch as native

pytestmark = pytest.mark.unit
REPO = Path("/Volumes/MACSSD/owen-home/Documents/workspace/bestseller")


def sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def canonical(value: object) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


@pytest.mark.parametrize("serialized", [False, True])
def test_actual_wrapper_factory_null_empty_is_typed_retained_and_replayable(
    serialized: bool,
) -> None:
    p = prepared()
    c = completion(payload(p))
    c["raw_message"] = llm._build_raw_assistant_message("".strip(), c["tool_calls"])
    assert c["content"] == "" and c["raw_message"]["content"] is None
    if serialized:
        c = json.loads(json.dumps(c, ensure_ascii=False, allow_nan=False))
    before = deepcopy(c)
    result, receipt = native.apply_native_candidate_patch(BODY, p, c)
    submission = receipt["submission_receipt"]
    normalization = submission["content_normalization_receipt"]
    assert normalization == {
        "schema": "retained-native-empty-content-normalization.v1",
        "direction": "raw_null_to_completion_empty_string",
        "raw_field": "raw_message.content",
        "raw_json_type": "null",
        "raw_value": None,
        "completion_field": "content",
        "completion_json_type": "string",
        "completion_value": "",
        "wrapper_factory": "bestseller.services.llm._build_raw_assistant_message",
        "raw_message_sha256": sha(canonical(c["raw_message"])),
        "tool_calls_sha256": sha(canonical(c["tool_calls"])),
        "original_raw_preserved": True,
        "used_as_proposal": False,
    }
    assert submission["original_content"] == ""
    assert submission["raw_message_sha256"] == normalization["raw_message_sha256"]
    assert submission["raw_arguments"] == c["tool_calls"][0]["function"]["arguments"]
    assert submission["raw_arguments_sha256"] == sha(submission["raw_arguments"])
    assert submission["receipt_sha256"] == sha(
        canonical({key: value for key, value in submission.items() if key != "receipt_sha256"})
    )
    assert c == before and result != BODY
    assert receipt["legacy_application_receipt"]["untouched_bytes_verified"]
    assert not receipt["grants_credit"] and not receipt["adoption_authorized"]


@pytest.mark.parametrize(
    "content,raw",
    [
        ("commentary", None),
        (" ", None),
        ("\n", None),
        (None, ""),
        ("", " "),
        ("", "commentary"),
        ("note", "different note"),
        ("", False),
        ("", 0),
    ],
)
def test_only_exact_one_way_pair_is_accepted(content: object, raw: object) -> None:
    p = prepared()
    c = completion(payload(p))
    c["content"], c["raw_message"]["content"] = content, raw
    before = deepcopy(c)
    with pytest.raises(ValueError, match="native_candidate_patch:raw_content_conflict"):
        native.apply_native_candidate_patch(BODY, p, c)
    assert c == before


@pytest.mark.parametrize(
    "fault", ["missing_content", "wrong_role", "raw_tools", "missing_tools", "legacy_function"]
)
def test_normalized_pair_never_bypasses_raw_message_guards(fault: str) -> None:
    p = prepared()
    c = completion(payload(p))
    c["raw_message"] = llm._build_raw_assistant_message("", c["tool_calls"])
    if fault == "missing_content":
        del c["raw_message"]["content"]
    elif fault == "wrong_role":
        c["raw_message"]["role"] = "user"
    elif fault == "raw_tools":
        c["raw_message"]["tool_calls"] = deepcopy(c["tool_calls"])
        c["raw_message"]["tool_calls"][0]["function"]["arguments"] += " "
    elif fault == "missing_tools":
        del c["raw_message"]["tool_calls"]
    else:
        c["raw_message"]["function_call"] = {"name": "competing"}
    with pytest.raises(ValueError, match="native_candidate_patch:"):
        native.apply_native_candidate_patch(BODY, p, c)


@pytest.mark.parametrize("content", [None, "", "non-authoritative explanation"])
def test_already_equal_fields_do_not_claim_normalization(content: str | None) -> None:
    p = prepared()
    c = completion(payload(p))
    c["content"] = c["raw_message"]["content"] = content
    _, submission = native.parse_native_candidate_patch(c, p)
    assert submission["content_normalization_receipt"] is None


def test_actual_paid_response_applies_all_five_exact_targets_without_raw_changes() -> None:
    base = REPO / "reports/continuous-recovery-2026-10-16"
    artifacts = REPO / "artifacts/runtime/llm_payloads"
    paths = [
        base / "20261017T233959306472Z-retained-candidate-operator.json",
        base / "20261017T230307779572Z-copy-candidate-full-progress.json",
        artifacts / "eaddf4bf-79aa-4338-8fdd-6398ec7bbc61.request.json",
        artifacts / "eaddf4bf-79aa-4338-8fdd-6398ec7bbc61.response.json",
    ]
    original = [p.read_bytes() for p in paths]
    record, progress, request, response = [json.loads(x) for x in original]
    saved = record["result"]["receipt"]
    target = next(x for x in progress["drafts"] if x["id"] == saved["candidate_id"])
    p = native.prepare_native_candidate_patch(target["content_md"], target["prepared"]["findings"])
    assert p["catalog_sha256"] == saved["proof"]["remaining_catalog_sha256"]
    assert p["legacy_prepared"] == target["prepared"] and len(p["targets"]) == 5
    assert request["requested"]["tools"] == p["request"]["tools"]
    c = {key: saved["completion"][key] for key in saved["completion"]}
    assert c["content"] == response["content"]["text"] == ""
    assert c["raw_message"] == response["raw_message"]
    assert c["tool_calls"] == response["tool_calls"]
    assert c["raw_message"]["content"] is None
    result, receipt = native.apply_native_candidate_patch(target["content_md"], p, c)
    assert sha(result) == "5cd8de2c0cee26733ad89543d6658a8120ee96e2686386f70cc643e09343f151"
    assert len(receipt["legacy_application_receipt"]["targets"]) == 5
    assert receipt["legacy_application_receipt"]["untouched_bytes_verified"] is True
    assert receipt["submission_receipt"]["content_normalization_receipt"]["raw_value"] is None
    assert (
        saved["status"] == "failed"
        and saved["error_code"] == "native_candidate_patch:raw_content_conflict"
    )
    assert all(path.read_bytes() == value for path, value in zip(paths, original, strict=True))
