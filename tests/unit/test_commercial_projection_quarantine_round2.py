"""Real round-2 helper/SQLite custody crosses into the pure action consumer."""

from copy import deepcopy
import json

import pytest
from test_commercial_component_cohort import _judge
from test_commercial_component_cohort import cohort as cohort
from test_commercial_quarantine_action_proofs import _all_rows, _load, _sources
from test_commercial_quarantine_round2 import _round2_case

from bestseller.services.commercial_action_projection import _sha, project_commercial_actions
from bestseller.services.write_gate import GateConfig

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


def project(cohort, result, mapping):
    return project_commercial_actions(
        result,
        sources=_sources(cohort),
        gate_config=GateConfig({}),
        chapter_number=2,
        verified_quarantine_slots=mapping,
    )


async def test_actual_round2_helper_loader_and_projector_preserve_sources_and_financial_rows(
    cohort, monkeypatch
):
    case = await _round2_case(cohort, monkeypatch)
    result = await _judge(cohort, bound=True)
    original = result.model_dump(mode="json")
    before = await _all_rows(cohort)
    mapping = await _load(case, result)
    saved_mapping = deepcopy(mapping)
    proof = mapping[case.slot]["proof"]
    retry = proof["round2_input_binding"]
    assert retry["base_input_hash"] == case.input_hash
    assert retry["final_input_hash"] == case.final_hash == proof["input_hash"]
    assert retry["base_input_hash"] != retry["final_input_hash"]
    assert retry["retry_context_sha256"] == _sha(case.retry)
    assert retry["sources"] == {
        "text": _sources(cohort).text,
        "catalog": dict(_sources(cohort).catalog),
        "receipt": dict(_sources(cohort).receipt),
    }
    view, receipt = project(cohort, result, mapping)
    assert view is not None and receipt["status"] == "projected", receipt
    assert view["overall_score"] == result.overall_score
    assert (
        view["dimension_scores"] == dict(result.dimension_scores) and view["pass"] is result.passed
    )
    slot = next(s for s in receipt["slots"] if s["sample_slot"] == case.slot)
    assert slot["round"] == 2 and slot["abstains"] is True
    assert (
        slot["domain_issues_contributed"]
        == slot["actions_contributed"]
        == slot["scores_contributed"]
        == 0
    )
    assert slot["round2_input_binding_sha256"] == _sha(retry)
    assert slot["quarantine_proof_sha256"] == _sha(proof)
    assert not {"sources", "round2_input_binding", "proof", "root", "attempts"} & set(slot)
    assert retry["sources"]["text"] not in json.dumps(receipt, ensure_ascii=False)
    assert mapping == saved_mapping and result.model_dump(mode="json") == original
    assert await _all_rows(cohort) == before
    assert not case.forbidden and len(cohort.calls) == 3


@pytest.mark.parametrize(
    "fault",
    [
        "missing_block",
        "extra_block_field",
        "null_block",
        "base_equals_final",
        "final_is_base",
        "physical_is_base",
        "base_invalid",
        "context_hash_invalid",
        "sources_missing",
        "sources_extra",
        "source_text",
        "source_catalog",
        "source_receipt",
        "round1_with_block",
        "proof_hash",
    ],
)
async def test_actual_round2_input_block_tamper_cannot_project_even_when_envelope_is_resealed(
    cohort, monkeypatch, fault
):
    case = await _round2_case(cohort, monkeypatch)
    result = await _judge(cohort, bound=True)
    mapping = deepcopy(await _load(case, result))
    proof = mapping[case.slot]["proof"]
    retry = proof["round2_input_binding"]
    if fault == "missing_block":
        proof.pop("round2_input_binding")
    if fault == "extra_block_field":
        retry["grant_replay"] = True
    if fault == "null_block":
        proof["round2_input_binding"] = None
    if fault == "base_equals_final":
        retry["base_input_hash"] = retry["final_input_hash"]
    if fault == "final_is_base":
        retry["final_input_hash"] = retry["base_input_hash"]
    if fault == "physical_is_base":
        proof["input_hash"] = retry["base_input_hash"]
    if fault == "base_invalid":
        retry["base_input_hash"] = True
    if fault == "context_hash_invalid":
        retry["retry_context_sha256"] = "not-a-hash"
    if fault == "sources_missing":
        retry["sources"].pop("catalog")
    if fault == "sources_extra":
        retry["sources"]["grant"] = True
    if fault == "source_text":
        retry["sources"]["text"] += " "
    if fault == "source_catalog":
        retry["sources"]["catalog"] = {}
    if fault == "source_receipt":
        retry["sources"]["receipt"]["structural_aliases"] = ["changed"]
    if fault == "round1_with_block":
        proof["round"] = 1
    if fault == "proof_hash":
        retry["retry_context_sha256"] = "f" * 64
    envelope = json.loads(result.raw_excerpt)
    report = next(s["report"] for s in envelope["samples"] if s["sample_slot"] == case.slot)
    raw = json.loads(report["raw_excerpt"])
    if fault != "proof_hash":
        raw["proof_sha256"] = _sha(proof)
    report["raw_excerpt"] = json.dumps(raw, sort_keys=True)
    mapping[case.slot]["original_report_sha256"] = _sha(report)
    altered = result.model_copy(update={"raw_excerpt": json.dumps(envelope)})
    previous = altered.model_dump(mode="json")
    before = await _all_rows(cohort)
    view, receipt = project(cohort, altered, mapping)
    assert view is None and receipt["status"] in {"blocked", "unsupported"}
    assert altered.model_dump(mode="json") == previous
    assert await _all_rows(cohort) == before and not case.forbidden
