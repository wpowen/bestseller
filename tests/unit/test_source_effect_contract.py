import json
from pathlib import Path
from uuid import UUID

import pytest

from bestseller.services.source_effect_contract import (
    compare_source_effect_contracts,
    normalize_source_effect_contract,
    render_source_effect_extraction_prompt,
)

_ROOT = Path(__file__).parents[2]
_SOURCE_PROBE = _ROOT / "tests/fixtures/source-direction-real-probe.json"


def _contract(
    text: str,
    *,
    mode: str = "causes_target_action",
    origin: str = "source",
    receipt: str = "00000000-0000-4000-8000-000000000001",
    quote: str | None = None,
    applicability: str = "applicable",
    reason: str = "",
) -> dict:
    return normalize_source_effect_contract(
        {
            "applicability": applicability,
            "applicability_reason": reason,
            "mechanism_quote": quote if quote is not None else text,
            "causal_mode": mode,
        },
        source_text=text,
        origin=origin,
        llm_run_id=receipt,
    )


@pytest.mark.parametrize("case_index", range(5))
def test_prompt_matches_recorded_real_probe(case_index):
    fixture = json.loads(
        (_ROOT / "tests/fixtures/source-direction-definition-first-real-probe.json").read_text()
    )
    case = fixture["cases"][case_index]
    system, user = render_source_effect_extraction_prompt(
        text=case["text"], origin=case["origin"]
    )
    assert system == case["prompt"]["system"]
    assert user == case["prompt"]["user"]
    contract = normalize_source_effect_contract(
        case["raw"], source_text=case["text"], origin=case["origin"],
        llm_run_id=case["llm_run_ids"][-1],
    )
    if case["expected"] == "unknown":
        assert contract["known"] is False
        assert contract["applicability"] == "unknown"
    else:
        assert contract["known"] is True
        assert (contract["applicability"] if case["expected"] == "not_applicable"
                else contract["causal_mode"]) == case["expected"]


def test_real_probe_rejects_changed_direction_and_accepts_faithful_control():
    cases = json.loads(_SOURCE_PROBE.read_text())["cases"]
    contracts = {
        label: normalize_source_effect_contract(
            case["raw"],
            source_text=case["text"],
            origin=case["origin"],
            llm_run_id=case["receipt"],
        )
        for label, case in cases.items()
    }
    assert all(contract["known"] for contract in contracts.values())
    assert (
        compare_source_effect_contracts(contracts["source"], contracts["candidate"])["status"]
        == "conflict"
    )
    assert (
        compare_source_effect_contracts(contracts["source"], contracts["control"])["status"]
        == "pass"
    )


def test_direction_rewrite_is_hard_conflict():
    source = _contract("主角让石像抬手", quote="主角让石像抬手")
    candidate = _contract(
        "主角看见石像抬手",
        origin="candidate",
        mode="perceives_information",
        receipt="00000000-0000-4000-8000-000000000002",
    )
    report = compare_source_effect_contracts(source, candidate)
    assert report["status"] == "conflict"
    assert report["hard_conflicts"][0]["classification"] == "causal_mode_mismatch"


@pytest.mark.parametrize("mode", ["other", "unknown", "not-a-mode"])
def test_other_and_unknown_modes_never_become_known(mode):
    result = _contract("主角施展无法归类的能力", mode=mode)
    assert result["known"] is False


@pytest.mark.parametrize(
    "receipt", [None, "", "00000000-0000-0000-0000-000000000000", "not-a-uuid"]
)
def test_nil_or_invalid_receipt_fails_closed(receipt):
    assert _contract("主角让石像抬手", receipt=receipt)["known"] is False


def test_quote_must_be_one_literal_contiguous_source_substring():
    result = _contract("主角让石像抬手", quote="主角抬手")
    assert result["known"] is False
    assert result["mechanism_quote"] == ""


def test_not_applicable_requires_reason_and_no_quote():
    valid = _contract(
        "她在雨夜离开旧城",
        applicability="not_applicable",
        reason="普通现实叙事且没有明确核心能力",
        quote="",
        mode="unknown",
    )
    assert valid["known"] is True
    missing_reason = _contract(
        "她在雨夜离开旧城", applicability="not_applicable", quote="", mode="unknown"
    )
    assert missing_reason["known"] is False


@pytest.mark.parametrize(
    "extra",
    [{"causal_mode": "perceives_information"}, {"operations": [{"causal_mode": "unknown"}]}],
)
def test_not_applicable_with_direction_or_operations_is_not_known(extra):
    payload = {
        "applicability": "not_applicable",
        "applicability_reason": "普通现实叙事且没有明确核心能力",
        "mechanism_quote": "",
        "causal_mode": "unknown",
        **extra,
    }
    result = normalize_source_effect_contract(
        payload,
        source_text="她在雨夜离开旧城",
        origin="source",
        llm_run_id="00000000-0000-4000-8000-000000000001",
    )
    assert result["known"] is False


def test_operations_is_a_single_element_compatibility_view():
    result = _contract("主角让石像抬手")
    assert result["operations"] == [
        {
            "mechanism_quote": "主角让石像抬手",
            "causal_mode": "causes_target_action",
            "evidence_complete": True,
        }
    ]


def test_source_and_candidate_receipts_must_be_distinct():
    source = _contract("主角让石像抬手")
    candidate = _contract("主角让石像抬手", origin="candidate", receipt=source["llm_run_id"])
    assert compare_source_effect_contracts(source, candidate)["status"] == "unknown"


def test_contract_id_must_be_non_nil_and_distinct():
    source = _contract("主角让石像抬手")
    candidate = _contract(
        "主角让石像抬手", origin="candidate", receipt="00000000-0000-4000-8000-000000000002"
    )
    candidate["contract_id"] = source["contract_id"]
    assert compare_source_effect_contracts(source, candidate)["status"] == "unknown"
    candidate["contract_id"] = str(UUID("00000000-0000-0000-0000-000000000000"))
    assert compare_source_effect_contracts(source, candidate)["status"] == "unknown"


def test_origin_and_text_hash_are_bound_to_the_contract():
    source = _contract("主角让石像抬手")
    candidate = _contract(
        "主角让石像抬手", origin="candidate", receipt="00000000-0000-4000-8000-000000000002"
    )
    candidate["origin"] = "source"
    assert compare_source_effect_contracts(source, candidate)["status"] == "unknown"
    candidate["origin"] = "candidate"
    candidate["text_hash"] = "not-a-sha256"
    assert compare_source_effect_contracts(source, candidate)["status"] == "unknown"


def test_unknown_contract_is_not_promoted_by_compare():
    source = _contract("主角让石像抬手")
    candidate = _contract(
        "主角让石像抬手",
        origin="candidate",
        receipt="00000000-0000-4000-8000-000000000002",
        quote="",
    )
    assert compare_source_effect_contracts(source, candidate)["status"] == "unknown"
