"""Atomic source proposals are exact partitions, never semantic approvals."""
# ruff: noqa: RUF001 -- Exact source punctuation is part of lossless reconstruction.

from copy import deepcopy
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from test_world_clause_restatement import inputs

from bestseller.services import world_clause_restatement as restatement
from bestseller.services import world_preservation_contract as preservation
from bestseller.services.world_authority import WorldAuthorityAuditError

pytestmark = pytest.mark.unit


def atomic_payload():
    context, editor = inputs()
    roles = [
        ("Overreach", "trigger"),
        (" ", "connector"),
        ("drains resolve", "amend_cause"),
        (" and ", "connector"),
        ("ruins the seal", "failure_outcome"),
        (", ", "connector"),
        ("without rank loss", "existing_protection"),
        (";", "connector"),
    ]
    raw = {
        "source_hash": editor["source_hash"],
        "clause_id": editor["clause_id"],
        "atoms": [
            {"id": f"A{i}", "quote": quote, "role": role, "reason": "Source-only proposal"}
            for i, (quote, role) in enumerate(roles, 1)
        ],
        "relations": [{"from": "A1", "to": "A3", "relation": "condition_for"}],
        "absence_reasons": {"actor_scope": "Implicit subject", "unaffected_mechanism": "Not found"},
    }
    return context, raw


def build(raw, context):
    return preservation.build_atomic_preservation_contract(
        raw,
        source_hash=context["diagnostic"]["source_hash"],
        target=restatement._target(**context),
        required_axes=("trigger",),
    )


def test_atomic_partition_projects_disjoint_contract_and_replays_without_mutation():
    context, raw = atomic_payload()
    before = deepcopy(raw)
    result = build(raw, context)
    assert raw == before
    assert result["amendment_quotes"] == ["drains resolve"]
    assert result["promotion_allowed"] is False
    preservation.verify_preservation_contract(
        result,
        source_hash=raw["source_hash"],
        target=restatement._target(**context),
        required_axes=("trigger",),
    )
    assert result["atomic_source"] == raw
    assert result["semantic_review_required"] is True


def test_real_chinese_conflict_can_be_partitioned_without_locking_its_cause():
    from test_world_preservation_contract import BEFORE

    roles = [
        ("越阶画符", "trigger"),
        ("需以", "connector"),
        ("自身胆气为墨", "amend_cause"),
        ("（怕的人画不怕的符）", "unaffected_mechanism"),
        ("，", "connector"),
        ("胆气耗尽", "amend_cause"),
        ("则", "connector"),
        ("符废", "failure_outcome"),
        ("但", "connector"),
        ("不会退阶", "existing_protection"),
        ("；", "connector"),
    ]
    _, raw = atomic_payload()
    raw["atoms"] = [
        {"id": f"A{i}", "quote": q, "role": role, "reason": "Proposed classification"}
        for i, (q, role) in enumerate(roles, 1)
    ]
    raw["relations"] = [
        {"from": "A1", "to": "A3", "relation": "condition_for"},
        {"from": "A6", "to": "A8", "relation": "causes"},
    ]
    raw["absence_reasons"] = {"actor_scope": "Subject omitted in source"}
    target = {"quote": BEFORE, "clause_id": raw["clause_id"]}
    kwargs = {
        "source_hash": raw["source_hash"],
        "target": target,
        "required_axes": ("trigger", "existing_protection"),
    }
    contract = preservation.build_atomic_preservation_contract(raw, **kwargs)
    assert contract["amendment_quotes"] == ["自身胆气为墨", "胆气耗尽"]
    protection = next(
        row for row in contract["obligations"] if row["axis"] == "existing_protection"
    )
    assert protection["before_quotes"] == ["不会退阶"]
    assert contract["unreviewed_connector_ids"]
    assert contract["relation_semantics"] == "unreviewed_source_proposal"
    preservation.verify_preservation_contract(contract, **kwargs)


def test_repeated_quote_occurrences_are_not_silently_collapsed():
    context, raw = atomic_payload()
    raw["atoms"][-1]["quote"] += "Overreach"
    target = {"quote": "".join(a["quote"] for a in raw["atoms"]), "clause_id": raw["clause_id"]}
    with pytest.raises(ValueError, match="AMBIGUOUS_FACT_SPAN"):
        preservation.build_atomic_preservation_contract(
            raw, source_hash=context["diagnostic"]["source_hash"], target=target
        )


@pytest.mark.parametrize(
    "case",
    [
        "gap",
        "order",
        "duplicate_id",
        "role",
        "extra",
        "fake",
        "empty",
        "relation",
        "self_edge",
        "duplicate_edge",
        "missing_absence",
        "false_absence",
        "no_amend",
    ],
)
def test_invalid_atomic_source_never_becomes_a_contract(case):
    context, raw = atomic_payload()
    if case == "gap":
        raw["atoms"].pop(1)
    elif case == "order":
        raw["atoms"].reverse()
    elif case == "duplicate_id":
        raw["atoms"][2]["id"] = "A1"
    elif case == "role":
        raw["atoms"][2]["role"] = "approved_deletion"
    elif case == "extra":
        raw["approved"] = True
    elif case == "fake":
        raw["atoms"][2]["quote"] = "unlimited retries"
    elif case == "empty":
        raw["atoms"][2]["quote"] = ""
    elif case == "relation":
        raw["relations"][0]["to"] = "missing"
    elif case == "self_edge":
        raw["relations"][0]["to"] = "A1"
    elif case == "duplicate_edge":
        raw["relations"].append(deepcopy(raw["relations"][0]))
    elif case == "missing_absence":
        raw["absence_reasons"].pop("actor_scope")
    elif case == "false_absence":
        raw["absence_reasons"]["trigger"] = "Not found"
    elif case == "no_amend":
        raw["atoms"][2]["role"] = "unaffected_mechanism"
        raw["absence_reasons"].pop("unaffected_mechanism")
    with pytest.raises(ValueError):
        build(raw, context)


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["valid", "bad_source", "bad_editor"])
async def test_atomic_producer_uses_partition_then_editor_once_and_replays(monkeypatch, case):
    context, raw = atomic_payload()
    editor = {
        "source_hash": raw["source_hash"],
        "clause_id": raw["clause_id"],
        "replacement": "Overreach sometimes ruins the seal, without rank loss;",
    }
    if case == "bad_source":
        raw["atoms"][2]["id"] = "A1"
    if case == "bad_editor":
        editor["replacement"] = "Overreach sometimes ruins the seal;"
    calls = []

    async def complete(*args):
        calls.append(args[-1])
        return SimpleNamespace(
            content=json.dumps(raw if len(calls) == 1 else editor),
            provider="openai",
            fallback_used=False,
            finish_reason="stop",
            llm_run_id=uuid4(),
        )

    monkeypatch.setattr(restatement, "complete_text", complete)
    if case == "valid":
        result = await restatement.propose_anchored_world_restatement(
            None, None, project_id=uuid4(), atomic_facts=True, disjoint_amendments=True, **context
        )
        assert result["version"] == restatement.ATOMIC_RESTATEMENT_VERSION
        restatement.verify_world_clause_restatement(result, **context)
        assert result["promotion_allowed"] is False
        changed = deepcopy(result)
        changed["preservation_contract"]["atomic_source"]["atoms"][0]["quote"] = "Other"
        with pytest.raises(ValueError):
            restatement.verify_world_clause_restatement(changed, **context)
        changed = deepcopy(result)
        changed["preservation_generation_receipt"]["raw_response"] = "{}"
        with pytest.raises(ValueError):
            restatement.verify_world_clause_restatement(changed, **context)
    else:
        with pytest.raises(WorldAuthorityAuditError):
            await restatement.propose_anchored_world_restatement(
                None,
                None,
                project_id=uuid4(),
                atomic_facts=True,
                disjoint_amendments=True,
                **context,
            )
    assert len(calls) == (1 if case == "bad_source" else 2)
    assert calls[0].prompt_version == restatement.ATOMIC_RESTATEMENT_VERSION
    assert len(calls[0].prompt_version) <= 32
    assert "atoms" in calls[0].system_prompt
    if len(calls) == 2:
        assert "atomic_source" in calls[1].user_prompt
