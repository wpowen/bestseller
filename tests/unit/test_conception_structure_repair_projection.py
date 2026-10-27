"""Complete semantic editor inputs, bounded before the paid gateway."""

from __future__ import annotations

import copy
import hashlib
import json
from uuid import uuid4

import pytest

from bestseller.services import conception_structure_repair as repair
from bestseller.services.llm import LLMCompletionResult

pytestmark = pytest.mark.unit


def state():
    return {
        "premise": "A courier must reach a closed port.",
        "synopsis": "The courier repeatedly asks the same guard for entry.",
        "story_spine": {"who": "a courier", "obstacle": "closed port"},
        "writing_profile": {"world": {"boundary": "no instant travel"}, "custom": [1, 2]},
        "world": {"tides": "daily"},
        "world_model_payload": {"identity": "port"},
        "concept_contract": {
            "story_architecture": {"goal": "reach the port"},
            "quality_evidence": {"original": "retain canonical contract"},
        },
        "genre_intent_contract": {"source": "explicit", "genre_label": "adventure"},
        "explicit_concept_seed": {"text": "a courier", "source": "explicit"},
        "ctx": {
            "creation_intent_contract": {"field_sources": {"tone": "explicit"}},
            "_user_intent_snapshot": {"tags": ["adventure"]},
            "user_notes": "keep the courier alive",
            "existing_overrides": {"tone": "warm"},
            "conception_log": [{"llm_run_id": "CTX_RUNTIME_ONLY"}],
        },
        "conception_log": [{"text": "RUNTIME_ONLY" * 100_000}],
        "last_failure": {"conception_log": [{"text": "RUNTIME_ONLY" * 100_000}]},
        "llm_run_ids": ["PAID_ROW_ONLY"],
        "repair_quotas": {"attempts": 5},
        "story_appeal_report": {"raw": "UNRELATED_REPORT_ONLY"},
    }


def diagnosis():
    return {
        "source": "logline_review",
        "scope": "story",
        "issues": {
            "escalation": {
                "repair_scope": "story",
                "reason": "The action repeats.",
                "evidence": [{"field": "synopsis", "quote": "repeatedly asks"}],
                "old_report": "UNRELATED_REPORT_ONLY",
            }
        },
    }


def intent_diagnosis(source):
    vote = {
        "items": {"adventure": {"pass": False, "quote": ""}},
        "failed_tags": ["adventure"],
        "counter_elements": [{"against": "adventure", "quote": "repeatedly asks"}],
        "revise_direction": "UNTRUSTED_PLOT_PROPOSAL_ONLY",
    }
    return {
        "source": "corroborated_intent",
        "scope": "story",
        "report": {
            "candidate": {key: source[key] for key in ("premise", "synopsis", "story_spine")},
            "failed_intents": ["adventure"],
            "votes": [copy.deepcopy(vote), copy.deepcopy(vote)],
            "old_logs": "UNRELATED_REPORT_ONLY",
        },
    }


def candidate(source):
    return {
        "premise": source["premise"],
        "synopsis": "The guard demands a tide chart.",
        "story_spine": {**source["story_spine"], "escalation": "a concrete trade"},
    }


def test_full_canonical_fields_and_creator_controls_no_runtime_or_truncation():
    source = state()
    original = copy.deepcopy(source)
    issue = diagnosis()
    request = repair.build_structure_repair_request(state=source, diagnosis=issue)
    payload = json.loads(request.user_prompt)
    for key in (
        "premise",
        "synopsis",
        "story_spine",
        "writing_profile",
        "world",
        "world_model_payload",
        "concept_contract",
        "genre_intent_contract",
        "explicit_concept_seed",
    ):
        assert payload["state"][key] == source[key]
    for key in (
        "creation_intent_contract",
        "_user_intent_snapshot",
        "user_notes",
        "existing_overrides",
    ):
        assert payload["state"]["ctx"][key] == source["ctx"][key]
    for marker in ("RUNTIME_ONLY", "PAID_ROW_ONLY", "UNRELATED_REPORT_ONLY"):
        assert marker not in request.user_prompt
    assert len(request.user_prompt) < 3000
    assert request.prompt_version == repair.STRUCTURE_REPAIR_PROMPT_VERSION
    assert request.logical_role == "editor" and request.model_tier == "strong"
    assert request.max_tokens_override == 8192
    assert source == original
    assert issue == diagnosis()


def test_runtime_mutation_changes_full_guard_hash_but_not_semantic_prompt():
    source = state()
    a = repair.build_structure_repair_request(state=source, diagnosis=diagnosis())
    source["last_failure"]["attempts"] = 21
    b = repair.build_structure_repair_request(state=source, diagnosis=diagnosis())
    assert a.user_prompt == b.user_prompt
    ar, br = (r.metadata["structure_repair_projection"] for r in (a, b))
    assert ar["source_hash"] != br["source_hash"]
    assert ar["semantic_sha256"] == br["semantic_sha256"]


@pytest.mark.parametrize(
    "field",
    [
        "premise",
        "synopsis",
        "story_spine",
        "writing_profile",
        "world",
        "world_model_payload",
        "concept_contract",
    ],
)
def test_semantic_field_change_is_kept_in_full(field):
    source = state()
    if isinstance(source[field], str):
        source[field] += " New causal development."
    else:
        source[field]["new_complete_field"] = {"tail": "must survive"}
    request = repair.build_structure_repair_request(state=source, diagnosis=diagnosis())
    assert json.loads(request.user_prompt)["state"][field] == source[field]


async def test_service_returns_existing_three_field_receipt_and_full_state_hash(monkeypatch):
    source = state()
    expected_candidate = candidate(source)
    original = copy.deepcopy(source)
    calls = []
    run_id = uuid4()

    async def complete(session, settings, request):
        calls.append(request)
        return LLMCompletionResult(
            content=json.dumps(expected_candidate),
            provider="fixture",
            model_name="editor",
            finish_reason="stop",
            llm_run_id=run_id,
        )

    monkeypatch.setattr(repair, "complete_text", complete)
    result = await repair.propose_conception_structure_repair(
        None, None, state=source, diagnosis=diagnosis()
    )
    assert result == {
        "candidate": expected_candidate,
        "llm_run_id": str(run_id),
        "source_hash": hashlib.sha256(repair._json(source).encode()).hexdigest(),
    }
    assert len(calls) == 1 and source == original


def test_corroborated_intents_keep_each_relevant_row_not_whole_report():
    source = state()
    issue = intent_diagnosis(source)
    issue["report"]["votes"][1]["counter_elements"].append(
        {"against": "adventure", "quote": "closed port"}
    )
    request = repair.build_structure_repair_request(state=source, diagnosis=issue)
    projected = json.loads(request.user_prompt)["diagnosis"]
    assert len(projected["votes"]) == 2
    assert len(projected["votes"][1]["counter_elements"]) == 2
    assert "candidate" not in projected
    assert "UNTRUSTED_PLOT_PROPOSAL_ONLY" not in request.user_prompt


@pytest.mark.parametrize(
    "case",
    [
        "empty",
        "unknown",
        "wrong_scope",
        "late_quote",
        "late_shape",
        "no_reason",
        "missing_field",
        "runtime_quote",
        "intent_one_vote",
        "intent_late_quote",
        "intent_pass",
        "intent_candidate",
        "intent_no_evidence",
        "intent_item_quote",
    ],
)
async def test_invalid_diagnosis_fails_before_paid_dispatch(monkeypatch, case):
    source, issue = state(), diagnosis()
    if case.startswith("intent_"):
        issue = intent_diagnosis(source)
        report = issue["report"]
        if case == "intent_one_vote":
            report["votes"].pop()
        elif case == "intent_late_quote":
            report["votes"][1]["counter_elements"].append(
                {"against": "adventure", "quote": "repeatedly……entry"}
            )
        elif case == "intent_pass":
            report["votes"][1]["items"]["adventure"]["pass"] = True
        elif case == "intent_candidate":
            report["candidate"]["premise"] = "different candidate"
        elif case == "intent_no_evidence":
            report["votes"][1]["counter_elements"] = []
        else:
            report["votes"][1]["items"]["adventure"]["quote"] = "not in source"
    elif case == "empty":
        issue = {}
    elif case == "unknown":
        issue["source"] = "arbitrary_report"
    elif case == "wrong_scope":
        issue["scope"] = "title"
    else:
        detail = issue["issues"]["escalation"]
        if case == "late_quote":
            detail["evidence"].append({"field": "premise", "quote": "absent"})
        elif case == "late_shape":
            detail["evidence"].append(None)
        elif case == "no_reason":
            detail["reason"] = ""
        elif case == "missing_field":
            detail["evidence"][0]["field"] = "nonexistent"
        else:
            detail["evidence"] = [{"field": "last_failure", "quote": "RUNTIME_ONLY"}]

    async def forbidden(*args, **kwargs):
        pytest.fail("invalid diagnosis must never claim a paid request")

    monkeypatch.setattr(repair, "complete_text", forbidden)
    with pytest.raises(
        repair.StructureRepairDiagnosisError, match="structure_repair_diagnosis_invalid"
    ):
        await repair.propose_conception_structure_repair(None, None, state=source, diagnosis=issue)


@pytest.mark.parametrize("field", ["premise", "writing_profile", "concept_contract", "ctx"])
async def test_overlarge_semantics_fail_before_paid_without_silent_cut(monkeypatch, field):
    source = state()
    if field == "premise":
        source[field] += "完整" * 100_000
    elif field == "ctx":
        source[field]["user_notes"] += "完整" * 100_000
    else:
        source[field]["full_canonical_text"] = "完整" * 100_000
    original = copy.deepcopy(source)

    async def forbidden(*args, **kwargs):
        pytest.fail("overlarge semantic input must not claim a paid request")

    monkeypatch.setattr(repair, "complete_text", forbidden)
    with pytest.raises(
        repair.StructureRepairAdmissionError, match="structure_repair_input_budget_exceeded"
    ):
        await repair.propose_conception_structure_repair(
            None, None, state=source, diagnosis=diagnosis()
        )
    assert source == original


@pytest.mark.parametrize(
    "payload, expected", [("[]", "candidate_fields"), ('{"premise":"x"}', "candidate_fields")]
)
async def test_original_output_guards_still_apply(monkeypatch, payload, expected):
    async def complete(*args):
        return LLMCompletionResult(
            content=payload,
            provider="fixture",
            model_name="editor",
            finish_reason="stop",
            llm_run_id=uuid4(),
        )

    monkeypatch.setattr(repair, "complete_text", complete)
    with pytest.raises(ValueError, match=expected):
        await repair.propose_conception_structure_repair(
            None, None, state=state(), diagnosis=diagnosis()
        )


@pytest.mark.parametrize(
    "case,code",
    [
        ("unchanged", "candidate_unchanged"),
        ("missing_spine_key", "story_spine_keys_missing"),
        ("extra_field", "candidate_fields_invalid"),
        ("blank", "candidate_synopsis_invalid"),
        ("fallback", "model_fallback"),
        ("length", "finish_reason:length"),
        ("tools", "tool_calls_present"),
        ("missing_id", "llm_run_id_missing"),
        ("nonfinite", "output_not_json"),
    ],
)
async def test_future_protocol_preserves_output_and_identity_guards(monkeypatch, case, code):
    source = state()
    output = candidate(source)
    changes = {}
    if case == "unchanged":
        output = {key: source[key] for key in ("premise", "synopsis", "story_spine")}
    elif case == "missing_spine_key":
        output["story_spine"].pop("who")
    elif case == "extra_field":
        output["title"] = "not writable"
    elif case == "blank":
        output["synopsis"] = "  "
    elif case == "fallback":
        changes["fallback_used"] = True
    elif case == "length":
        changes["finish_reason"] = "length"
    elif case == "tools":
        changes["tool_calls"] = [{"function": {"name": "unexpected", "arguments": "{}"}}]
    elif case == "missing_id":
        changes["llm_run_id"] = None
    else:
        output["story_spine"]["bad"] = float("nan")

    async def complete(*args):
        values = {
            "content": json.dumps(output),
            "provider": "fixture",
            "model_name": "editor",
            "finish_reason": "stop",
            "llm_run_id": uuid4(),
            **changes,
        }
        return LLMCompletionResult(**values)

    original = copy.deepcopy(source)
    monkeypatch.setattr(repair, "complete_text", complete)
    with pytest.raises(ValueError, match=code):
        await repair.propose_conception_structure_repair(
            None, None, state=source, diagnosis=diagnosis()
        )
    assert source == original


def test_logline_nested_current_contract_quote_is_grounded():
    issue = diagnosis()
    issue["issues"]["escalation"]["evidence"] = [
        {"field": "story_context.story_architecture.goal", "quote": "reach the port"}
    ]
    request = repair.build_structure_repair_request(state=state(), diagnosis=issue)
    assert (
        json.loads(request.user_prompt)["diagnosis"]["issues"]["escalation"]["evidence"]
        == (issue["issues"]["escalation"]["evidence"])
    )


def test_token_admission_is_independent_of_character_admission(monkeypatch):
    monkeypatch.setattr(repair, "estimate_prompt_tokens", lambda _: 40_000)
    with pytest.raises(repair.StructureRepairAdmissionError):
        repair.build_structure_repair_request(state=state(), diagnosis=diagnosis())


def test_nonfinite_diagnosis_has_specific_diagnostic_error():
    issue = diagnosis()
    issue["old_report"] = float("nan")
    with pytest.raises(repair.StructureRepairDiagnosisError, match="diagnosis_invalid:not_json"):
        repair.build_structure_repair_request(state=state(), diagnosis=issue)
