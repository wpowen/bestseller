"""Reader scores consume exact evidence; they are not human calibration."""

from copy import deepcopy
import hashlib
import json
from types import SimpleNamespace

import pytest

from bestseller.services import source_bound_planning_content as protocol


def digest(value):
    return hashlib.sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


def verdict(source, binding):
    return {
        "schema": "reader-first-review.v1",
        "checkpoint": "skeleton",
        "source_sha256": digest(source),
        "config_sha256": binding["config_sha256"],
        "dimensions": {
            name: {
                "score": 0.8,
                "reason": "The relationship changes through a choice.",
                "evidence": [{"path": "/spine/wants", "quote": "reconcile"}],
            }
            for name in protocol.READER_FIRST_DIMENSIONS
        },
        "issues": [],
        "options": [
            "Keep the relationship and change the obstacle.",
            "Ask which promise should drive the ending.",
        ],
    }


def test_binding_freezes_config_and_input_and_preserves_legacy():
    binding = protocol.new_reader_first_binding("a family reconciles")
    assert binding["protocol"] == "reader-first-planning.v1"
    assert binding["user_input_sha256"] == hashlib.sha256(b"a family reconciles").hexdigest()
    assert protocol.reader_first_binding(SimpleNamespace(metadata_json={})) is None
    assert (
        protocol.reader_first_binding({"writing_profile": {"reader_first_planning": binding}})
        == binding
    )
    bad = {**binding, "config_sha256": "0" * 64}
    with pytest.raises(ValueError, match="config"):
        protocol.reader_first_binding({"reader_first_planning": bad})
    with pytest.raises(ValueError, match="version"):
        protocol.reader_first_binding({"reader_first_planning": {**binding, "protocol": "latest"}})


def test_bound_source_pass_and_freshness():
    binding = protocol.new_reader_first_binding("family")
    source = {"spine": {"wants": "reconcile"}}
    raw = verdict(source, binding)
    before = deepcopy(source)
    result = protocol.parse_reader_first_review(
        raw, source=source, binding=binding, checkpoint="skeleton"
    )
    assert result["status"] == "passed"
    assert result["thresholds_calibrated"] is False
    assert result["mean_score"] == pytest.approx(0.8)
    assert source == before
    source["spine"]["wants"] = "leave"
    assert (
        protocol.parse_reader_first_review(
            raw, source=source, binding=binding, checkpoint="skeleton"
        )["status"]
        == "unknown"
    )


@pytest.mark.parametrize(
    "change",
    [
        "missing",
        "nan",
        "bool",
        "out_of_range",
        "bad_quote",
        "bad_pointer",
        "fallback",
        "missing_reason",
        "wrong_config",
        "wrong_checkpoint",
        "duplicate_json_key",
    ],
)
def test_unknown_never_passes(change):
    binding = protocol.new_reader_first_binding("family")
    source = {"spine": {"wants": "reconcile"}}
    raw = verdict(source, binding)
    dim = next(iter(raw["dimensions"].values()))
    if change == "missing":
        raw["dimensions"].pop(next(iter(raw["dimensions"])))
    elif change == "nan":
        dim["score"] = float("nan")
    elif change == "bool":
        dim["score"] = True
    elif change == "out_of_range":
        dim["score"] = 1.1
    elif change == "bad_quote":
        dim["evidence"][0]["quote"] = "not in source"
    elif change == "bad_pointer":
        dim["evidence"][0]["path"] = "/not_here"
    elif change == "fallback":
        raw["fallback"] = True
    elif change == "missing_reason":
        dim["reason"] = ""
    elif change == "wrong_config":
        raw["config_sha256"] = "f" * 64
    elif change == "wrong_checkpoint":
        raw["checkpoint"] = "outline"
    elif change == "duplicate_json_key":
        raw = json.dumps(raw)[:-1] + ',"schema":"other"}'
    result = protocol.parse_reader_first_review(
        raw, source=source, binding=binding, checkpoint="skeleton"
    )
    assert result["status"] == "unknown"
    assert set(result["scores"]) == set(protocol.READER_FIRST_DIMENSIONS)
    assert result["reasons"]
    assert 2 <= len(result["options"]) <= 3


def test_thresholds_are_conjunctive_and_high_severity_cannot_be_offset():
    binding = protocol.new_reader_first_binding("family")
    source = {"spine": {"wants": "reconcile"}}
    raw = verdict(source, binding)
    for row in raw["dimensions"].values():
        row["score"] = 0.75
    result = protocol.parse_reader_first_review(
        raw, source=source, binding=binding, checkpoint="skeleton"
    )
    assert result["status"] == "failed"
    assert len(result["reasons"]) >= 4
    raw = verdict(source, binding)
    raw["issues"] = [{"severity": "high", "reason": "The final choice contradicts the promise."}]
    assert (
        protocol.parse_reader_first_review(
            raw, source=source, binding=binding, checkpoint="skeleton"
        )["status"]
        == "failed"
    )


def test_input_capacity_is_checked_before_dispatch_without_truncation():
    binding = protocol.new_reader_first_binding("family")
    source = {"spine": {"wants": "reconcile"}}
    _system, user = protocol.reader_first_review_prompts(
        binding, source=source, checkpoint="skeleton"
    )
    assert json.loads(user)["source"] == source
    assert json.loads(user)["source_sha256"] == digest(source)
    with pytest.raises(ValueError, match="capacity"):
        protocol.reader_first_review_prompts(
            binding, source={"text": "字" * 100000}, checkpoint="skeleton"
        )
    assert protocol.reader_first_instruction(None) == ""
    assert protocol.reader_first_instruction(binding, language="en")
    assert protocol.reader_first_instruction(binding, language="zh")


def test_source_prompt_version_requires_explicit_project_binding():
    legacy = protocol.planning_prompt_version("book_spec")
    binding = protocol.new_reader_first_binding("family")
    marked = SimpleNamespace(metadata_json={"reader_first_planning": binding})
    assert protocol.planning_prompt_version("book_spec", project=marked) != legacy
    assert (
        protocol.planning_prompt_version("book_spec", project=SimpleNamespace(metadata_json={}))
        == legacy
    )


@pytest.mark.parametrize("index,status", [("0", "passed"), ("00", "unknown"), ("٠", "unknown")])
def test_array_evidence_requires_canonical_json_pointer(index, status):
    binding = protocol.new_reader_first_binding("family")
    source = {"spine": {"wants": "reconcile"}, "chapters": [{"goal": "reconcile"}]}
    raw = verdict(source, binding)
    for row in raw["dimensions"].values():
        row["evidence"] = [{"path": "/chapters/" + index + "/goal", "quote": "reconcile"}]
    receipt = protocol.parse_reader_first_review(
        raw, source=source, binding=binding, checkpoint="skeleton"
    )
    assert receipt["status"] == status


def test_unknown_pause_preserves_each_valid_dimension_and_specific_model_reasons():
    binding = protocol.new_reader_first_binding("family")
    source = {"spine": {"wants": "reconcile"}}
    raw = verdict(source, binding)
    absent = protocol.READER_FIRST_DIMENSIONS[0]
    raw["dimensions"].pop(absent)
    result = protocol.parse_reader_first_review(
        raw, source=source, binding=binding, checkpoint="skeleton"
    )
    assert result["status"] == "unknown"
    assert result["scores"][absent] is None
    for name in protocol.READER_FIRST_DIMENSIONS[1:]:
        assert result["scores"][name] == .8
        assert any(row.get("dimension") == name and row.get("reason")
                   for row in result["reasons"])
    assert result["options"] == raw["options"]
