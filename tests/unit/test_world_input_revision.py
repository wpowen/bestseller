"""Upstream rule proposals cannot mutate creation authority or parent receipts."""

from copy import deepcopy

import pytest
from test_world_recompile_candidate import build

from bestseller.services.world_recompile_candidate import (
    revise_world_recompile_candidate,
    verify_world_input_revision,
)

pytestmark = pytest.mark.unit
PATH = ["writing_profile", "world", "power_system_style"]


def edit(parent):
    before = parent["frozen_inputs"]["metadata"]["writing_profile"]["world"]["power_system_style"]
    return {
        "path": PATH,
        "before": before,
        "after": before.replace("手抖", ""),
        "rationale": "Remove the confirmed weakening; meaning still requires review.",
    }


def test_revision_recompiles_upstream_and_preserves_all_other_inputs():
    parent = build()
    original = deepcopy(parent)
    result = revise_world_recompile_candidate(parent, parent_report_id="parent", edit=edit(parent))
    assert parent == original
    # Detached diagnostic inputs lack a selected canonical row. Keep the
    # upstream edit frozen for review without declaring it an approved rule.
    assert result["candidate_world"]["power_system"] == {}
    assert result["candidate_world"]["_meta"]["source_gaps"] == ["full_canonical_source_unavailable"]
    assert result["frozen_inputs"]["metadata"]["writing_profile"]["world"]["power_system_style"] == edit(parent)["after"]
    expected = deepcopy(parent["frozen_inputs"])
    expected["metadata"]["writing_profile"]["world"]["power_system_style"] = edit(parent)["after"]
    assert result["frozen_inputs"] == expected
    assert result["input_hash"] != parent["input_hash"]
    assert result["candidate_book"] == parent["candidate_book"]
    assert result["source_artifact"] == parent["source_artifact"]
    assert result["revision"]["parent_report_id"] == "parent"
    assert result["revision"]["authority"] == "proposal_only_requires_semantic_review"
    assert result["promotion_allowed"] is False
    assert result["production_ready"] is False
    verify_world_input_revision(result, parent=parent, parent_report_id="parent")
    result["revision"]["edit"]["rationale"] = "tampered"
    with pytest.raises(ValueError, match="RECEIPT_MISMATCH"):
        verify_world_input_revision(result, parent=parent, parent_report_id="different")


@pytest.mark.parametrize(
    "case", ["stale", "protected", "empty", "no_change", "bad_parent", "extra", "addition"]
)
def test_revision_rejects_unsafe_or_ambiguous_edit(case):
    parent = build()
    proposal = edit(parent)
    if case == "stale":
        proposal["before"] = "old"
    elif case == "protected":
        proposal["path"] = ["book_design_snapshot", "reader_promise"]
    elif case == "empty":
        proposal["after"] = ""
    elif case == "no_change":
        proposal["after"] = proposal["before"]
    elif case == "extra":
        proposal["approved"] = True
    elif case == "addition":
        proposal["after"] = "A new power not in the source"
    else:
        parent["frozen_inputs"]["premise"] = "tampered"
    with pytest.raises(ValueError):
        revise_world_recompile_candidate(parent, parent_report_id="parent", edit=proposal)
