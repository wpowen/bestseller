"""New protocol keeps audit-only directions out of body editing requests."""
from copy import deepcopy
import json
import pytest
from bestseller.services.reviews import merge_judge_rewrite_direction

pytestmark = pytest.mark.unit
MARKER = "REVIEW_REPAIR_HANDOFF_PROPOSAL_JSON:\n"

@pytest.mark.parametrize("evaluator", ["commercial", "window"])
def test_audit_only_payload_cannot_request_body_changes(evaluator):
    payload={"pass":False,"rewrite_plan":{"instructions":"Have the protagonist explain archive custody."},
        "blocking_issues":[],"audit_issues":[{"code":"SOURCE_AUDIT","required_fix":"Explain custody."}]}
    original=deepcopy(payload)
    assert merge_judge_rewrite_direction("Keep the original task.",judge_payload=payload,
        evaluator=evaluator,reader_first=True)=="Keep the original task."
    assert payload==original

@pytest.mark.parametrize("evaluator", ["commercial", "window"])
def test_mixed_payload_uses_only_explicit_blocking_items_without_shared_audit_plan(evaluator):
    payload={"pass":False,"rewrite_plan":{"instructions":"Narrate the system review then repair dialogue.",
        "preserve":["Narrate audit logs."]},"blocking_issues":[{"code":"DIALOGUE_SCENE_FLAT", "required_fix":"Keep the disagreement in the dialogue."}],
        "audit_issues":[{"code":"SOURCE_AUDIT","evidence":"private source custody diagnostic"}]}
    original=deepcopy(payload)
    merged=merge_judge_rewrite_direction("Task",judge_payload=payload,evaluator=evaluator,reader_first=True)
    proposal=json.loads(merged.split(MARKER,1)[1])
    assert "audit_issues" not in proposal
    assert proposal["blocking_issues"]==payload["blocking_issues"]
    assert proposal["rewrite_plan"]=={"scope":"chapter", "instructions":"Keep the disagreement in the dialogue."}
    assert payload==original
    assert "private source custody diagnostic" not in merged
    assert "Narrate the system review" not in merged


def test_primary_validated_direction_retains_direction_without_audit_citation_payload():
    payload={"pass":False,"rewrite_plan":{"scope":"chapter","instructions":"Let the two voices differ."},
        "blocking_issues":[],"audit_issues":[{"quote":"raw citation retained in report"}]}
    result=merge_judge_rewrite_direction("",judge_payload=payload,evaluator="primary",reader_first=True)
    proposal=json.loads(result.split(MARKER,1)[1])
    assert proposal["rewrite_plan"]==payload["rewrite_plan"]
    assert "audit_issues" not in proposal
