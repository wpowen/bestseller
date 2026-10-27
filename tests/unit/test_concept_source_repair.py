from __future__ import annotations

from copy import deepcopy

import pytest

from bestseller.services.concept_source_repair import repair_concept_source

pytestmark = pytest.mark.unit


def _inputs():
    return {
        "card": {
            "core_abnormality": "旧异常",
            "opening_crisis": "旧危机",
            "reader_promise": "旧承诺",
        },
        "authority": "港口停电后,所有影子都开始倒着走。",
        "initial_failed_axes": ["seed_fidelity"],
        "initial_seed_fidelity_evidence": {"status": "verified"},
        "initial_scores": {"seed_fidelity": 0, "hook": 8, "commercial": 7},
    }


async def test_unknown_identity_is_rejected_without_callbacks():
    calls = []
    args = _inputs() | {"initial_seed_fidelity_evidence": {"status": "unknown"}}
    result = await repair_concept_source(
        args.pop("card"),
        args.pop("authority"),
        lambda *a: calls.append(a),
        lambda *a: calls.append(a),
        lambda *a: calls.append(a),
        **args,
    )
    assert result.status == "ineligible" and not calls


@pytest.mark.parametrize(
    "diagnosis",
    [
        {
            "decision": "missing_only",
            "missing_facts": [{"candidate_field": "opening_crisis", "source_quote": "伪造事实"}],
        },
        {
            "decision": "replacement",
            "missing_facts": [{"candidate_field": "opening_crisis", "source_quote": "港口停电"}],
        },
    ],
)
async def test_fake_quote_or_replacement_is_rejected(diagnosis):
    calls = 0

    async def judge(*_):
        nonlocal calls
        calls += 1
        return diagnosis

    args = _inputs()
    result = await repair_concept_source(
        args.pop("card"), args.pop("authority"), judge, lambda *a: {}, lambda *a: True, **args
    )
    assert result.status == "rejected" and calls == 1


async def test_extra_fact_semantics_are_rejected_before_normalization():
    args = _inputs()
    diagnosis = {
        "decision": "missing_only",
        "missing_facts": [
            {
                "candidate_field": "opening_crisis",
                "source_quote": "港口停电",
                "relation": "replacement",
            }
        ],
    }
    calls = []

    async def judge(*_):
        calls.append("judge")
        return diagnosis

    async def gen(*_):
        calls.append("gen")
        return {"opening_crisis": "新危机"}

    result = await repair_concept_source(
        args.pop("card"), args.pop("authority"), judge, gen, lambda *_: True, **args
    )
    assert result.status == "rejected"
    assert result.reason == "missing_fact_keys_must_be_exact"
    assert calls == ["judge"]


async def test_multiple_axes_are_ineligible():
    args = _inputs() | {"initial_failed_axes": ["seed_fidelity", "hook"]}
    result = await repair_concept_source(
        args.pop("card"),
        args.pop("authority"),
        lambda *a: {},
        lambda *a: {},
        lambda *a: True,
        **args,
    )
    assert result.status == "ineligible"


async def test_non_related_patch_is_rejected():
    args = _inputs()
    diagnosis = {
        "decision": "missing_only",
        "missing_facts": [{"candidate_field": "opening_crisis", "source_quote": "港口停电"}],
    }
    calls = {"gen": 0, "rejudge": 0}

    async def judge(*_):
        return diagnosis

    async def gen(*_):
        calls.__setitem__("gen", calls["gen"] + 1)
        return {"opening_crisis": "新危机", "title": "越权"}

    async def rejudge(*_):
        calls.__setitem__("rejudge", calls["rejudge"] + 1)
        return True

    result = await repair_concept_source(
        args.pop("card"), args.pop("authority"), judge, gen, rejudge, **args
    )
    assert result.status == "rejected" and calls == {"gen": 1, "rejudge": 0}


async def test_failed_full_rejudge_does_not_promote():
    args = _inputs()
    diagnosis = {
        "decision": "missing_only",
        "missing_facts": [{"candidate_field": "opening_crisis", "source_quote": "港口停电"}],
    }

    async def judge(*_):
        return diagnosis

    async def gen(*_):
        return {"opening_crisis": "新危机"}

    async def rejudge(*_):
        return False

    result = await repair_concept_source(
        args.pop("card"), args.pop("authority"), judge, gen, rejudge, **args
    )
    assert result.edited_card is None and result.reason == "full_rejudge_failed"


async def test_cards_are_isolated_and_success_is_bounded_to_three_calls():
    args = _inputs()
    original = deepcopy(args["card"])
    calls = []
    diagnosis = {
        "decision": "missing_only",
        "missing_facts": [{"candidate_field": "opening_crisis", "source_quote": "港口停电"}],
    }

    async def judge(card, authority):
        calls.append("judge")
        card["opening_crisis"] = "mutated"
        return diagnosis

    async def gen(card, authority, diag):
        calls.append("gen")
        return {"opening_crisis": "新危机"}

    async def rejudge(card, authority):
        calls.append("rejudge")
        return True

    result = await repair_concept_source(
        args.pop("card"), args.pop("authority"), judge, gen, rejudge, **args
    )
    assert calls == ["judge", "gen", "rejudge"]
    assert (
        result.edited_card["opening_crisis"] == "新危机" and original["opening_crisis"] == "旧危机"
    )


async def test_success_repairs_only_named_field():
    args = _inputs()
    diagnosis = {
        "decision": "missing_only",
        "missing_facts": [{"candidate_field": "opening_crisis", "source_quote": "港口停电"}],
    }

    async def judge(*_):
        return diagnosis

    async def gen(*_):
        return {"opening_crisis": "港口陷入黑暗"}

    async def rejudge(*_):
        return True

    result = await repair_concept_source(
        args.pop("card"), args.pop("authority"), judge, gen, rejudge, **args
    )
    assert result.status == "repaired"
    assert result.edited_card["opening_crisis"] == "港口陷入黑暗"
    assert result.edited_card["core_abnormality"] == "旧异常"


async def test_duplicate_missing_facts_for_one_field_are_allowed_and_normalized():
    args = _inputs()
    args["authority"] = "港口停电后,所有影子都开始倒着走。"
    diagnosis = {
        "decision": "missing_only",
        "missing_facts": [
            {"candidate_field": "opening_crisis", "source_quote": "港口停电"},
            {"candidate_field": "opening_crisis", "source_quote": "影子都开始倒着走"},
        ],
    }
    seen = {}

    async def judge(*_):
        return diagnosis

    async def gen(card, authority, normalized):
        seen.update(normalized)
        return {"opening_crisis": "新危机"}

    async def rejudge(*_):
        return True

    result = await repair_concept_source(
        args.pop("card"), args.pop("authority"), judge, gen, rejudge, **args
    )
    assert result.status == "repaired"
    assert result.changed_fields == ("opening_crisis",)
    assert seen == {
        "decision": "missing_only",
        "missing_facts": [
            {"source_quote": "港口停电", "candidate_field": "opening_crisis"},
            {"source_quote": "影子都开始倒着走", "candidate_field": "opening_crisis"},
        ],
    }


@pytest.mark.parametrize(
    "scores",
    [
        {},
        {"seed_fidelity": 0},
        {"seed_fidelity": 0, "hook": float("nan")},
        {"seed_fidelity": 0, "hook": float("inf")},
    ],
)
async def test_invalid_score_gate_does_not_call_callbacks(scores):
    calls = []
    args = _inputs() | {"initial_scores": scores}
    result = await repair_concept_source(
        args.pop("card"),
        args.pop("authority"),
        lambda *_: calls.append("judge"),
        lambda *_: calls.append("gen"),
        lambda *_: calls.append("rejudge"),
        **args,
    )
    assert result.status == "ineligible"
    assert calls == []
