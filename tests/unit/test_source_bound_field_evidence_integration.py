"""Actual review entry with inert completions: structural proof never grades fiction."""

from __future__ import annotations

import copy
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from test_source_bound_semantic_review_authority import invoke, prompt_value, setup_review

from bestseller.services import planner
from bestseller.services import source_bound_field_evidence as presence

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


def _rig(*, structural=True):
    rig = setup_review(language="en")
    if structural:
        rig.world = copy.deepcopy(rig.world_base)
        rig.cast_base["antagonists"] = []
        rig.cast = copy.deepcopy(rig.cast_base)
        del rig.cast["antagonists"]
    return rig


def _evidence(request):
    fields = prompt_value(request, "changed_fields")
    source_options = prompt_value(request, "source_quote_options")
    candidate_options = prompt_value(request, "candidate_quote_options")
    result = {}
    for name, options in (
        ("source_evidence", source_options),
        ("candidate_evidence", candidate_options),
    ):
        result[name] = []
        for field in fields:
            option = next((item for item in options if item.get("field") == field), options[0])
            result[name].append(
                {
                    "field": field,
                    "quote_id": option["quote_id"],
                    "source_relation": "Observed state only; independent semantic decision below.",
                }
            )
    return result


def _payload(request, decision="pass", preserved=True):
    return {
        "decision": decision,
        "core_preserved": preserved,
        "core_conflicts": ["Fixture semantic contradiction"] if preserved is False else [],
        "quality_findings": [],
        "feedback": "Local fixture, not a real literary judgment",
        "authority_snapshot_hash": request.metadata["authority_snapshot_hash"],
        "candidate_hash": request.metadata["candidate_hash"],
        **_evidence(request),
    }


def _completion(payload, *, unavailable=False):
    return SimpleNamespace(
        content=json.dumps(payload, ensure_ascii=False),
        provider="fallback" if unavailable else "openai",
        finish_reason="stop",
        llm_run_id=uuid4(),
        fallback_used=unavailable,
    )


@pytest.mark.parametrize(
    "decision,preserved", [("pass", True), ("revise", False), ("uncertain", None)]
)
async def test_actual_review_typed_absence_requires_independent_semantic_verdict(
    monkeypatch,
    decision,
    preserved,
):
    rig = _rig()
    before = copy.deepcopy(
        (
            rig.project.metadata_json,
            rig.project._source_bound_planning_canonical,
            rig.world_base,
            rig.world,
            rig.cast_base,
            rig.cast,
        )
    )
    requests = []

    async def complete(session, settings, request):
        assert session is None
        requests.append(request)
        assert request.prompt_version == "4.3-field-presence"
        assert "not an authored sentence, permission to delete" in request.system_prompt
        return _completion(_payload(request, decision, preserved))

    monkeypatch.setattr(planner, "complete_text", complete)
    report = await invoke(rig)
    assert len(requests) == 1
    assert report["decision"] == decision
    assert report["core_preserved"] is preserved
    assert report["draft_eligible"] is (decision == "pass")
    assert report["evidence_verified"] is True
    assert report["review_version"].endswith("v4.3-field-presence")
    assert report["changed_fields"] == ["cast.antagonists"]
    for side, expected_present in (("source", True), ("candidate", False)):
        evidence = report[f"{side}_evidence"][0]
        assert evidence["schema"] == presence.SCHEMA
        assert evidence["present"] is expected_present
        assert "quote" not in evidence
        assert "value" not in evidence
    assert report["candidate_evidence"][0]["value_hash"] is None
    assert (
        rig.project.metadata_json,
        rig.project._source_bound_planning_canonical,
        rig.world_base,
        rig.world,
        rig.cast_base,
        rig.cast,
    ) == before


async def test_typed_absence_cannot_self_approve_without_reviewer_receipt(monkeypatch):
    rig = _rig()
    requests = []

    async def unavailable(session, settings, request):
        requests.append(request)
        raise RuntimeError("Local transport denied; no provider called")

    monkeypatch.setattr(planner, "complete_text", unavailable)
    report = await invoke(rig)
    assert len(requests) == 1
    assert report["reason"] == "review_call_unavailable"
    assert report["decision"] == "uncertain"
    assert report["draft_eligible"] is False
    assert report["llm_run_id"] is None


async def test_actual_evidence_only_repair_reproves_both_whole_payloads(monkeypatch):
    rig = _rig()
    requests = []
    semantic = {}

    async def complete(session, settings, request):
        requests.append(request)
        if len(requests) == 1:
            semantic.update(_payload(request))
            return _completion({**semantic, "source_evidence": [], "candidate_evidence": []})
        assert request.prompt_template == "planner_source_bound_expansion_evidence_repair"
        assert "not quotations or deletion permission" in request.user_prompt
        return _completion(
            {key: semantic[key] for key in ("source_evidence", "candidate_evidence")}
        )

    monkeypatch.setattr(planner, "complete_text", complete)
    report = await invoke(rig)
    assert len(requests) == 2
    assert report["evidence_repair_status"] == "passed", report["evidence_repair_attempts"]
    assert report["evidence_verified"] is True
    assert report["decision"] == "pass"
    assert report["core_preserved"] is True
    assert report["llm_run_id"] == report["review_attempts"][0]["llm_run_id"]
    assert report["evidence_repair_attempts"][0]["llm_run_id"] != report["llm_run_id"]
    for side, section in (
        ("source", "authority_and_baseline"),
        ("candidate", "derived_candidates"),
    ):
        assert presence.field_presence_evidence_matches(
            report[f"{side}_evidence"][0],
            payload=prompt_value(requests[0], section),
            field="cast.antagonists",
            side=side,
        )


@pytest.mark.parametrize("forgery", ["field", "id", "state", "side", "payload", "quote"])
async def test_forged_structural_reference_cannot_pass_actual_review(monkeypatch, forgery):
    rig = _rig()
    requests = []

    async def complete(session, settings, request):
        requests.append(request)
        if len(requests) > 1:
            return _completion({}, unavailable=True)
        payload = _payload(request)
        row = payload["candidate_evidence"][0]
        if forgery == "field":
            row["field"] = "cast.protagonist"
        elif forgery == "id":
            row["quote_id"] = "cp_forged"
        elif forgery == "state":
            row["present"] = True
        elif forgery == "side":
            row["quote_id"] = payload["source_evidence"][0]["quote_id"]
        elif forgery == "payload":
            row["payload_hash"] = "0" * 64
        else:
            row["quote"] = "Invented permission to remove an author constraint"
        return _completion(payload)

    monkeypatch.setattr(planner, "complete_text", complete)
    report = await invoke(rig)
    assert len(requests) == 2
    assert report["decision"] == "uncertain"
    assert report["reason"] == "evidence_not_verifiable"
    assert report["draft_eligible"] is False
    assert report["evidence_verified"] is False


async def test_text_only_review_keeps_v42_request_without_invoking_presence_compiler(monkeypatch):
    rig = _rig(structural=False)
    requests = []

    def forbidden(*args, **kwargs):
        raise AssertionError("Textual fields must not invoke typed presence compilation")

    async def complete(session, settings, request):
        requests.append(request)
        assert request.prompt_version == "4.2-stable-catalog"
        assert presence.SCHEMA not in request.system_prompt + request.user_prompt
        return _completion(_payload(request))

    monkeypatch.setattr(planner, "complete_text", complete)
    monkeypatch.setattr(presence, "compile_field_presence_options", forbidden)
    first = await invoke(rig)
    second = await invoke(rig)
    assert first["decision"] == second["decision"] == "pass"
    assert requests[0].system_prompt == requests[1].system_prompt
    assert requests[0].user_prompt == requests[1].user_prompt
    assert first["review_version"].endswith("v4.2-stable-catalog")
