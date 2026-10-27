"""Source anchors are frozen before editing, not invented by the editor."""
# ruff: noqa: RUF001

from copy import deepcopy

import pytest

from bestseller.services.world_preservation_contract import (
    PRESERVATION_AXES,
    build_preservation_contract,
    build_preservation_response,
    check_preservation_anchors,
    require_preservation_edit_space,
    verify_preservation_contract,
)

pytestmark = pytest.mark.unit
BEFORE = "越阶画符需以自身胆气为墨（怕的人画不怕的符），胆气耗尽则符废但不会退阶；"
FAILED = "越阶画符需以自身胆气为墨（怕的人画不怕的符），胆气不足则本次所画之符未必能成；"


def inputs():
    target = {
        "clause_id": "C012",
        "quote": BEFORE,
        "path": ["world", "power_system", "hard_limits"],
        "start": 171,
        "end": 171 + len(BEFORE),
    }
    anchors = {
        "actor_scope": "",
        "trigger": "越阶画符",
        "failure_outcome": "符废",
        "existing_protection": "不会退阶",
        "unaffected_mechanism": "",
    }
    payload = {
        "source_hash": "hash",
        "clause_id": "C012",
        "obligations": [
            {
                "axis": axis,
                "presence": "present" if anchors[axis] else "absent",
                "before_quotes": [anchors[axis]] if anchors[axis] else [],
                "reason": "Source-only extraction, not semantic approval",
            }
            for axis in PRESERVATION_AXES
        ],
    }
    return payload, {"source_hash": "hash", "target": target}


@pytest.mark.parametrize("amendments", [None, [], "胆气耗尽", ["forged"], ["不会退阶"]])
def test_disjoint_response_rejects_missing_invalid_or_protected_amendments(amendments):
    payload, kwargs = inputs()
    payload["amendment_quotes"] = amendments
    with pytest.raises(ValueError):
        build_preservation_response(payload, disjoint=True, **kwargs)


def test_response_protocol_shape_is_strict_and_version_bound():
    payload, kwargs = inputs()
    with pytest.raises(ValueError, match="INVALID_PAYLOAD"):
        build_preservation_response(payload, disjoint=True, **kwargs)
    payload["amendment_quotes"] = ["胆气耗尽"]
    contract = build_preservation_response(payload, disjoint=True, **kwargs)
    verify_preservation_contract(contract, **kwargs)
    with pytest.raises(ValueError, match="INVALID_PAYLOAD"):
        build_preservation_response(payload, disjoint=False, **kwargs)
    payload["approved"] = True
    with pytest.raises(ValueError, match="INVALID_PAYLOAD"):
        build_preservation_response(payload, disjoint=True, **kwargs)


def test_real_failed_restatement_is_rejected_without_another_model_call():
    payload, kwargs = inputs()
    contract = build_preservation_contract(payload, **kwargs)
    result = check_preservation_anchors(contract, FAILED, **kwargs)
    assert result["status"] == "rejected"
    assert "existing_protection" in result["missing_axes"]
    assert "failure_outcome" in result["missing_axes"]


def test_literal_presence_never_claims_semantic_approval():
    payload, kwargs = inputs()
    contract = build_preservation_contract(payload, **kwargs)
    original = deepcopy(contract)
    # Even this negation passes literal matching: full semantic review is mandatory.
    result = check_preservation_anchors(contract, "越阶画符后符废；不保证不会退阶；", **kwargs)
    assert result["status"] == "literal_anchors_present"
    assert result["semantic_review_required"] is True
    assert result["promotion_allowed"] is False
    assert contract == original


@pytest.mark.parametrize(
    "case",
    [
        "missing",
        "duplicate",
        "forged",
        "empty",
        "absent_quote",
        "unknown_presence",
        "stale",
        "extra",
        "no_anchors",
    ],
)
def test_invalid_or_unbound_contract_is_rejected(case):
    payload, kwargs = inputs()
    if case == "missing":
        payload["obligations"].pop()
    elif case == "duplicate":
        payload["obligations"][-1] = deepcopy(payload["obligations"][0])
    elif case == "forged":
        payload["obligations"][1]["before_quotes"] = ["Guaranteed victory"]
    elif case == "empty":
        payload["obligations"][1]["before_quotes"] = []
    elif case == "absent_quote":
        payload["obligations"][0]["before_quotes"] = ["越阶"]
    elif case == "unknown_presence":
        payload["obligations"][1]["presence"] = "maybe"
    elif case == "stale":
        payload["source_hash"] = "old"
    elif case == "extra":
        payload["approved"] = True
    else:
        for row in payload["obligations"]:
            row.update(presence="absent", before_quotes=[])
    with pytest.raises(ValueError):
        build_preservation_contract(payload, **kwargs)


def test_tampered_contract_fails_reconstruction():
    payload, kwargs = inputs()
    contract = build_preservation_contract(payload, **kwargs)
    contract["promotion_allowed"] = True
    with pytest.raises(ValueError):
        verify_preservation_contract(contract, **kwargs)


def test_diagnostic_required_protection_cannot_be_skipped_as_absent():
    payload, kwargs = inputs()
    payload["obligations"][3].update(presence="absent", before_quotes=[])
    with pytest.raises(ValueError, match="UNPROVEN_PRESENCE_OR_ABSENCE"):
        build_preservation_contract(payload, required_axes=("existing_protection",), **kwargs)


def test_other_genre_protection_is_not_chinese_book_specific():
    payload, kwargs = inputs()
    kwargs["target"]["quote"] = "Overclock may burn the chip but never erases memory;"
    payload["obligations"][1]["before_quotes"] = ["Overclock"]
    payload["obligations"][2]["before_quotes"] = ["burn the chip"]
    payload["obligations"][3]["before_quotes"] = ["never erases memory"]
    contract = build_preservation_contract(payload, **kwargs)
    result = check_preservation_anchors(contract, "Overclock may burn the chip;", **kwargs)
    assert result["missing_axes"] == ["existing_protection"]


def test_live_overwide_anchors_are_rejected_before_editing():
    payload, kwargs = inputs()
    payload["obligations"][0].update(
        presence="present",
        before_quotes=["越阶画符需以自身胆气为墨（怕的人画不怕的符）"],
    )
    payload["obligations"][2]["before_quotes"] = ["胆气耗尽则符废但不会退阶"]
    contract = build_preservation_contract(payload, **kwargs)
    with pytest.raises(ValueError, match="NO_SEMANTIC_EDIT_SPACE"):
        require_preservation_edit_space(contract, target=kwargs["target"])


def test_disjoint_amendments_keep_protection_but_release_conflicting_cause():
    payload, kwargs = inputs()
    amendments = ["需以自身胆气为墨（怕的人画不怕的符）", "胆气耗尽"]
    contract = build_preservation_contract(payload, amendment_quotes=amendments, **kwargs)
    verify_preservation_contract(contract, **kwargs)
    passed = check_preservation_anchors(contract, "越阶画符若失败则符废，但不会退阶；", **kwargs)
    assert passed["status"] == "literal_anchors_present"
    assert passed["unmodified_amendments"] == []
    assert passed["promotion_allowed"] is False
    failed = check_preservation_anchors(contract, BEFORE, **kwargs)
    assert failed["status"] == "rejected"
    assert failed["unmodified_amendments"] == amendments


@pytest.mark.parametrize(
    "quotes", [[], ["不会退阶"], ["made up"], ["；"], ["胆气耗尽", "胆气耗尽"]]
)
def test_invalid_or_overlapping_amendment_quotes_are_rejected(quotes):
    payload, kwargs = inputs()
    with pytest.raises(ValueError):
        build_preservation_contract(payload, amendment_quotes=quotes, **kwargs)


def test_live_overwide_failure_anchor_cannot_also_be_amended():
    payload, kwargs = inputs()
    payload["obligations"][2]["before_quotes"] = ["胆气耗尽则符废但不会退阶"]
    with pytest.raises(ValueError, match="KEEP_AMEND_OVERLAP"):
        build_preservation_contract(payload, amendment_quotes=["胆气耗尽"], **kwargs)
