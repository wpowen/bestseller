"""Derived audit compatibility cannot alter actions, scores or physical evidence."""
from copy import deepcopy
import hashlib
import json
from types import SimpleNamespace

import pytest
from test_retained_review_input_proof import _audit_pair

from bestseller.services import retained_review_input_proof as proof

pytestmark = pytest.mark.unit


def case():
    old_source, current_source = _audit_pair()
    source_edge = proof._audit_source_compatibility(
        json.dumps(old_source, sort_keys=True, ensure_ascii=False, separators=(",", ":")),
        {"source_complete": True, "source_sha256": proof._hash(old_source)}, current_source)
    source = SimpleNamespace(text="Complete current source packet")
    old = {"schema": "commercial-action-projection.v1", "status": "projected",
        "scores_modified": False, "raw_cohort_modified": False, "grants_slots": False,
        "authorizes_replay": False, "source_text_sha256": "a"*64,
        "source_binding": {"body_sha256": "b"*64, "semantic_source_sha256": "c"*64},
        "issue_actions": [{"code": "SOFT_CRAFT", "effective_action": "audit_only"}],
        "projection_sha256": "d"*64}
    current = deepcopy(old)
    current["source_text_sha256"] = hashlib.sha256(source.text.encode()).hexdigest()
    witnesses=[{"schema":"retained-commercial-packet-audit-proof.v1",
        "packet_sha256":old["source_text_sha256"], "current_packet_sha256":current["source_text_sha256"],
        "changed_pointer":"/source_context/source_selection_receipt/original_source_sha256",
        "literal_occurrences":1,"effective_input_hash":"1"*64,"operation_key":"2"*64,
        "old_opaque_audit_sha256":"3"*64,"current_opaque_audit_sha256":"4"*64}]
    return old, current, source, source_edge, witnesses


def test_single_derived_audit_hash_needs_typed_source_edge_and_preserves_both_receipts():
    old, current, source, edge, witnesses = case()
    before = deepcopy((old, current, edge))
    receipt = proof._projection_audit_compatibility(old, current, sources=source, source_compatibility=edge,packet_witnesses=witnesses)
    assert receipt["changed_pointer"] == "/source_text_sha256"
    assert receipt["historical_source_packet_reconstructed"] is True
    assert receipt["paid_request_bytes_equivalence_asserted"] is False
    assert receipt["scores_modified"] is receipt["grants_slots"] is receipt["authorizes_replay"] is False
    assert (old, current, edge) == before


@pytest.mark.parametrize("mutation", ["action", "binding", "scalar", "extra", "missing", "grant", "raw", "source_hash", "unknown_schema", "no_edge", "wrong_edge", "no_witness", "wrong_packet", "duplicate_packet", "wrong_operation"])
def test_any_other_projection_change_cannot_use_the_audit_edge(mutation):
    old, current, source, edge, witnesses = case()
    if mutation == "action":current["issue_actions"][0]["effective_action"] = "block"
    elif mutation == "binding":current["source_binding"]["body_sha256"] = "e"*64
    elif mutation == "scalar":current["projection_sha256"] = "f"*64
    elif mutation == "extra":current["extra"] = "new authority"
    elif mutation == "missing":current.pop("issue_actions")
    elif mutation == "grant":current["grants_slots"] = True
    elif mutation == "raw":current["raw_cohort_modified"] = True
    elif mutation == "source_hash":current["source_text_sha256"] = "f"*64
    elif mutation == "unknown_schema":old["schema"] = current["schema"] = "future-projection"
    elif mutation == "no_edge":edge = None
    elif mutation == "wrong_edge":edge["changed_pointer"] = "/editable_plan"
    elif mutation == "no_witness":witnesses = []
    elif mutation == "wrong_packet":witnesses[0]["packet_sha256"] = "f"*64
    elif mutation == "duplicate_packet":witnesses[0]["literal_occurrences"] = 2
    else:witnesses[0]["operation_key"] = "malformed"
    assert proof._projection_audit_compatibility(old, current, sources=source, source_compatibility=edge,packet_witnesses=witnesses) is None
