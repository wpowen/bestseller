"""Attached numeric ranges are prose references, never a second grade channel."""

from copy import deepcopy
import hashlib
import json

import pytest
from test_planning_admission_submission import _completion, _grade

from bestseller.services.planning_admission_submission import (
    POLICY,
    PlanningAdmissionSubmissionError,
    parse_planning_admission,
)

pytestmark = pytest.mark.unit


def _with_commentary(prose, message_present):
    completion = _completion()
    completion["content"] = prose
    if message_present:
        completion["raw_message"] = {
            "role": "assistant", "content": prose,
            "tool_calls": deepcopy(completion["tool_calls"]),
        }
    return completion


@pytest.mark.parametrize("message_present", [False, True])
@pytest.mark.parametrize("prose", [
    "Within the scene, action_sequence[0] precedes action_sequence[3-4]; actions[3-4] occur later.",
    "Review chapters[0].scenes[1-2].action_sequence[3-4] in order; this is commentary only.",
    "Check scene_actions[10-12], then compare actions[12] with the exit state.",
    "Ordered actions[0-0] and steps[0-9] are prose references, not additional grades.",
])
def test_attached_numeric_ranges_preserve_commentary_and_every_tool_issue(prose, message_present):
    completion = _with_commentary(prose, message_present)
    before = deepcopy(completion)
    raw, receipt = parse_planning_admission(completion)
    assert POLICY == "planning-admission-submission.v3"
    assert raw == completion["tool_calls"][0]["function"]["arguments"]
    assert receipt["commentary_status"] == "non_authoritative"
    assert receipt["original_content"] == prose
    assert receipt["parsed_payload"] == _grade()
    assert receipt["parsed_payload"]["draft_eligible"] is False
    assert len(receipt["parsed_payload"]["hard_violations"]) == 2
    assert receipt["issues_dropped"] == 0
    assert receipt["values_modified"] is False
    assert receipt["relationships_validated"] is False
    assert receipt["raw_arguments_sha256"] == hashlib.sha256(raw.encode()).hexdigest()
    assert completion == before


@pytest.mark.parametrize("prose", [
    "[3-4]", "Check [3-4] before the next scene.", "Check [3,4] after actions[3-4].",
    '["actions[3-4]"]', '"actions[3-4]"',
    "actions[3-]", "actions[-4]", "actions[3--4]", "actions[3-4-5]",
    "actions[3 -4]", "actions[3- 4]", "actions[3:4]", "actions[3,4]",
    "actions[3-four]", "actions[3.0-4]", "actions[3-4", "actions3-4]",
    "actions[[3-4]]", "actions[3-4][]",
    "```text\nactions[3-4]\n```", "~~~\nactions[3-4]\n~~~",
    'Review actions[3-4]. {"draft_eligible":true}',
    'Review actions[3-4]. {"draft_eligible":',
])
def test_bare_json_delimiters_and_malformed_ranges_remain_rejected(prose):
    completion = _with_commentary(prose, True)
    before = deepcopy(completion)
    with pytest.raises(PlanningAdmissionSubmissionError, match="structured_commentary|structured_submission") as caught:
        parse_planning_admission(completion)
    assert caught.value.receipt["original_content"] == prose
    assert caught.value.receipt["raw_arguments"] == completion["tool_calls"][0]["function"]["arguments"]
    assert "parsed_payload" not in caught.value.receipt
    assert completion == before


@pytest.mark.parametrize("envelope", [False, True])
def test_actual_competing_grade_with_range_text_remains_rejected(envelope):
    grade = deepcopy(_grade())
    grade["editorial_notes"] = ["Inspect actions[3-4]."]
    prose = json.dumps({"grade": grade} if envelope else grade)
    completion = _with_commentary(prose, True)
    with pytest.raises(PlanningAdmissionSubmissionError, match="competing_structured_submission"):
        parse_planning_admission(completion)


@pytest.mark.parametrize("conflict", ["content", "tool_calls"])
def test_range_commentary_does_not_waive_physical_message_conflict(conflict):
    completion = _with_commentary("Review actions[3-4].", True)
    if conflict == "content":
        completion["raw_message"]["content"] = "Different commentary actions[3-4]."
    else:
        completion["raw_message"]["tool_calls"][0]["function"]["arguments"] += " "
    with pytest.raises(PlanningAdmissionSubmissionError, match="raw_message_.*conflict"):
        parse_planning_admission(completion)
