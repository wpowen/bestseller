"""Effective fact actions filter real SQLite candidates before stall ranking.

Paid returns below are retained offline fixtures, never provider requests. The
existing fixture supplies the actual completed cycle/report/quality bindings.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from uuid import uuid4

import pytest
from test_adopted_knowledge_lineage import db as db
from test_chapter_critic_semantic_dependencies import semantic_db as semantic_db
from test_chapter_candidate_source_eligibility import candidates as candidates, choose, edit_cycle
from test_commercial_action_projection import slot as paid_slot

from bestseller.services import pipelines
from bestseller.services import llm
from bestseller.services.chapter_draft_candidate_eligibility import (
    AUDIT_KEY,
    _commercial_effective_fact_blockers,
    verified_commercial_actions,
)
from bestseller.services.commercial_action_projection import project_commercial_actions
from bestseller.services.commercial_source_evidence import assemble_commercial_sources
from bestseller.services.write_gate import GateConfig

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


def sha(value):
    return hashlib.sha256(json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode()).hexdigest()


def save_summary(data, index, summary):
    report, quality = data[6][index], data[5][index]
    report.structured_output = {**report.structured_output, "evidence_summary": deepcopy(summary)}
    quality.evidence_summary = {**quality.evidence_summary, **deepcopy(summary)}
    edit_cycle(data, index, lambda c: c["attempts"][0]["review_result"].update(
        evidence_summary=deepcopy(summary)))


def install_projection(data, index=0, *, kind="current_fact_conflict", severity="high",
                       code="UNRELATED_FACT_DISAGREEMENT", score=.91, effective=None):
    draft = data[4][index]
    src = assemble_commercial_sources(
        chapter_number=data[2].chapter_number, content_md=draft.content_md,
        previous_chapters=[{"chapter_number": 1, "body": data[-1].content_md}],
        source={"approved_chapter_contract": {
            "text": "The observer's registered identity is Tavi.", "source": "user", "hard": True,
        }} if kind == "constraint_violation" else {},
    )
    original = paid_slot(src, kind="narrative_quality" if kind == "retained_threshold_policy" else kind,
                         severity=severity, code=code,
                         score=score, issues=kind != "retained_threshold_policy")
    view, proof = project_commercial_actions(
        original, sources=src, gate_config=GateConfig({"ENDING_SENTENCE_WEAK": "block"}),
        chapter_number=data[2].chapter_number,
    )
    assert view is not None and proof["status"] == "projected"
    if effective is not None:
        # Exercise an explicitly retained action independently of severity.
        # This changes only this synthetic fixture, never a historical report.
        for action in proof["issue_actions"]:
            if action["assessment_kind"] != kind:
                continue
            action["effective_action"] = effective
            index_in_receipt = proof["issue_actions"].index(action)
            for group in ("blocking_issues", "audit_issues"):
                match = next((i for i in view[group] if i["action_receipt_index"] == index_in_receipt), None)
                if match is not None:
                    view[group].remove(match)
                    break
            view["blocking_issues" if effective == "block" else "audit_issues"].append(match)
        proof["effective_blocking_count"] = len(view["blocking_issues"])
        proof["projection_sha256"] = sha(view)
    summary = {
        "commercial_action_projection": proof, "llm_commercial_judge": view,
        "llm_commercial_judge_original": original.model_dump(mode="json", by_alias=True),
    }
    save_summary(data, index, summary)
    # The primary critic's paid identity is distinct from the commercial slot.
    data[6][index].llm_run_id = uuid4()
    return summary


def decision(data, index):
    return next(row for row in data[2].metadata_json[AUDIT_KEY][-1]["candidates"]
                if row["draft_id"] == str(data[4][index].id))


@pytest.fixture(autouse=True)
def no_provider(monkeypatch):
    async def forbidden(*args, **kwargs):
        pytest.fail("Candidate selection must not call a provider")
    monkeypatch.setattr(llm, "complete_text", forbidden)


@pytest.mark.parametrize("severity", ["medium", "high"])
@pytest.mark.parametrize("kind", ["current_fact_conflict", "prior_fact_conflict", "constraint_violation"])
async def test_higher_score_effective_fact_block_cannot_displace_lower_soft_debt(candidates, kind, severity):
    summary = install_projection(candidates, kind=kind, severity=severity, effective="block")
    blockers = _commercial_effective_fact_blockers(
        (summary,), pipelines.chapter_review_body_fingerprint(candidates[4][0].content_md))
    assert any(b["assessment_kind"] == kind and b["severity"] == severity for b in blockers)
    install_projection(candidates, 1, kind="retained_threshold_policy", score=.4)
    before = deepcopy(([r.structured_output for r in candidates[6]],
                       [q.evidence_summary for q in candidates[5]],
                       candidates[2].metadata_json["chapter_critic_cycles"]))
    selected = await choose(candidates)
    assert selected.id == candidates[4][1].id
    assert candidates[4][1].is_current and not candidates[4][0].is_current
    rejected = decision(candidates, 0)
    if kind == "constraint_violation":
        # The wire additionally derives a critical explicit-rule failure;
        # the pre-existing critical fence retains precedence and its reason.
        assert rejected["reasons"] == ["known_hard_or_critical_blocker"]
    else:
        assert rejected["reasons"] == ["known_effective_fact_blocker"]
        assert any(b["assessment_kind"] == kind and b["severity"] == severity
                   for b in rejected["effective_fact_blockers"])
    assert decision(candidates, 1)["eligible"]
    assert before == ([r.structured_output for r in candidates[6]],
                      [q.evidence_summary for q in candidates[5]],
                      candidates[2].metadata_json["chapter_critic_cycles"])
    assert summary["commercial_action_projection"]["scores_modified"] is False
    assert all(d.promotion_state == "candidate" for d in candidates[4])
    assert not candidates[2].metadata_json[AUDIT_KEY][-1]["adoption_authorized"]


@pytest.mark.parametrize("kind,severity,code,effective", [
    ("narrative_quality", "high", "ENDING_SENTENCE_WEAK", None),
    ("narrative_quality", "high", "UNREGISTERED_CRAFT_ADVICE", None),
    ("current_fact_conflict", "medium", "AUDIT_FACT_OBSERVATION", None),
    ("retained_threshold_policy", "high", "UNUSED", None),
])
async def test_genuine_quality_or_audit_debt_remains_rankable(candidates, kind, severity, code, effective):
    install_projection(candidates, kind=kind, severity=severity, code=code,
                       score=.4 if kind == "retained_threshold_policy" else .91, effective=effective)
    before = deepcopy(candidates[6][0].structured_output)
    assert (await choose(candidates)).id == candidates[4][0].id
    assert decision(candidates, 0)["eligible"]
    assert candidates[6][0].structured_output == before


@pytest.mark.parametrize("mutation", [
    "missing_projection", "null_projection", "missing_original", "unsupported", "schema",
    "body", "source_binding", "view_hash", "original_hash", "unknown_slot", "unknown_slots",
    "empty_slots", "unpaid_slot", "slot_envelope", "slot_raw", "empty_lineage", "lineage_raw",
    "lineage_source", "authority", "action", "action_count", "missing_action", "quality_disagrees",
    "cycle_disagrees", "malformed_summary", "missing_paid_original", "malformed_original",
])
async def test_missing_malformed_or_unknown_commercial_proof_fails_closed(candidates, mutation):
    summary = install_projection(candidates, kind="narrative_quality")
    proof, view = summary["commercial_action_projection"], summary["llm_commercial_judge"]
    if mutation == "missing_projection":
        del summary["commercial_action_projection"]
    elif mutation == "null_projection":
        summary["commercial_action_projection"] = None
    elif mutation == "missing_original":
        del summary["llm_commercial_judge_original"]
    elif mutation in {"unsupported", "schema"}:
        proof["status" if mutation == "unsupported" else "schema"] = "unknown"
    elif mutation == "body":
        proof["source_binding"]["body_sha256"] = "0" * 64
    elif mutation == "source_binding":
        del proof["source_binding"]["semantic_source_sha256"]
    elif mutation == "view_hash":
        proof["projection_sha256"] = "0" * 64
    elif mutation == "original_hash":
        proof["original_aggregate_sha256"] = "0" * 64
    elif mutation == "unknown_slot":
        proof["slots"][0]["status"] = "unknown"
    elif mutation == "unknown_slots":
        proof["unknown_sample_slots"] = [proof["slots"][0]["sample_slot"]]
    elif mutation == "empty_slots":
        proof["slots"] = []
    elif mutation == "unpaid_slot":
        proof["slots"][0]["llm_run_id"] = None
    elif mutation == "slot_envelope":
        proof["slots"][0]["envelope_sha256"] = "0" * 64
    elif mutation == "slot_raw":
        proof["slots"][0]["raw_report_sha256"] = "0" * 64
    elif mutation == "empty_lineage":
        proof["issue_actions"][0]["lineage"] = []
    elif mutation == "lineage_raw":
        proof["issue_actions"][0]["lineage"][0]["raw_report_sha256"] = "0" * 64
    elif mutation == "lineage_source":
        proof["issue_actions"][0]["lineage"][0]["sample_slot"] = "foreign-paid-slot"
    elif mutation == "authority":
        proof["issue_actions"][0]["authority"] = "verified_source"
    elif mutation == "action":
        proof["issue_actions"][0]["effective_action"] = "block"
    elif mutation == "action_count":
        proof["effective_blocking_count"] = 100
    elif mutation == "missing_action":
        proof["issue_actions"] = []
    elif mutation == "missing_paid_original":
        summary["llm_commercial_judge_original"]["llm_run_id"] = None
    elif mutation == "malformed_original":
        summary["llm_commercial_judge_original"]["raw_excerpt"] = "not-json"
    save_summary(candidates, 0, summary)
    if mutation == "quality_disagrees":
        candidates[5][0].evidence_summary = {**summary, "commercial_action_projection": {"status": "blocked"}}
    elif mutation == "cycle_disagrees":
        edit_cycle(candidates, 0, lambda c: c["attempts"][0]["review_result"].update(
            evidence_summary={"commercial_action_projection": {"status": "blocked"}}))
    elif mutation == "malformed_summary":
        candidates[6][0].structured_output = {**candidates[6][0].structured_output, "evidence_summary": []}
    assert (await choose(candidates)).id == candidates[4][1].id
    assert not decision(candidates, 0)["eligible"]
    assert set(decision(candidates, 0)["reasons"]) <= {
        "commercial_action_proof_unverified", "review_receipt_malformed",
    }


@pytest.mark.parametrize("missing", ["source_citations", "body_citations"])
async def test_fact_provenance_cannot_be_replaced_by_a_severity_label(candidates, missing):
    summary = install_projection(candidates)
    proof, view = summary["commercial_action_projection"], summary["llm_commercial_judge"]
    del proof["issue_actions"][0]["lineage"][0]["issue"][missing]
    del view["blocking_issues"][0][missing]
    proof["projection_sha256"] = sha(view)
    save_summary(candidates, 0, summary)
    assert (await choose(candidates)).id == candidates[4][1].id
    assert decision(candidates, 0)["reasons"] == ["commercial_action_proof_unverified"]


@pytest.mark.parametrize("genre,name", [("space-opera", "Tavi"), ("sports", "Jun")])
async def test_unrelated_genre_and_identity_rename_use_semantics_not_issue_code(candidates, genre, name):
    session, project, chapter, settings, drafts, qualities, reports, *_ = candidates
    project.genre, project.title = genre, f"{name}'s expedition"
    cycles = deepcopy(chapter.metadata_json["chapter_critic_cycles"])
    for index, draft in enumerate(drafts):
        text = (f"{name} counted twelve boxes; seven plus four made twelve. " if index == 0
                else f"{name} counted eleven boxes and checked the register. ")
        draft.content_md, draft.word_count = text * 40, len(text) * 40
        body = pipelines.chapter_review_body_fingerprint(draft.content_md)
        key, cycle = next((k, c) for k, c in list(cycles.items()) if c["source_draft_id"] == str(draft.id))
        del cycles[key]
        key = pipelines._chapter_critic_cycle_id(project.id, chapter.id, body["digest"])
        cycle.update(cycle_id=key, source_body_hash=body["digest"])
        cycle["attempts"][0]["body_fingerprint"] = body
        cycles[key] = cycle
        reports[index].structured_output = {**reports[index].structured_output, "reviewed_body_fingerprint": body}
    chapter.metadata_json = {**chapter.metadata_json, "chapter_critic_cycles": cycles}
    semantic = await pipelines._chapter_critic_semantic_dependencies(session, settings, chapter)
    for index in range(2):
        edit_cycle(candidates, index, lambda c: c.update(semantic_dependencies_sha256=semantic))
    install_projection(candidates, code=f"{name.upper()}_IDENTITY_OR_COUNT_EVIDENCE")
    install_projection(candidates, 1, kind="narrative_quality", severity="high")
    assert (await choose(candidates)).id == drafts[1].id
    assert decision(candidates, 0)["reasons"] == ["known_effective_fact_blocker"]
    assert decision(candidates, 1)["eligible"]


async def test_actual_retained_payload_offline_exposes_effective_current_fact_block():
    root = Path(__file__).resolve().parents[2]
    path = root / "reports/framework-recovery-2026-10-21/phase8t-chapter2-review-third-readback.json"
    before = path.read_bytes()
    reports = json.loads(before)["reports"]
    payload = reports[0]["output"]
    summary = payload["evidence_summary"]
    expected = [a for a in summary["commercial_action_projection"]["issue_actions"]
                if a["assessment_kind"] == "current_fact_conflict" and a["effective_action"] == "block"]
    assert expected and all(a["severity"] == "high" for a in expected)
    blockers = _commercial_effective_fact_blockers((summary, {}, summary), payload["reviewed_body_fingerprint"])
    assert {b["code"] for b in blockers} == {a["code"] for a in expected}
    assert path.read_bytes() == before


@pytest.mark.parametrize("tamper", ["reclassify", "demote", "omit_action", "lineage_kind", "original_group"])
async def test_full_actual_counterexample_cannot_launder_retained_raw_fact_into_soft_debt(tamper):
    root = Path(__file__).resolve().parents[2]
    readback = root / "reports/framework-recovery-2026-10-21/phase8t-chapter2-review-third-readback.json"
    counterexample = root / "reports/framework-remediation-2026-10-22/phase8u-candidate-action-lineage-counterexample.json"
    recorded = json.loads(counterexample.read_bytes())
    assert recorded["retained_original_unchanged"] and recorded["raw_slots_unchanged"]
    payload = json.loads(readback.read_bytes())["reports"][0]["output"]
    summary = deepcopy(payload["evidence_summary"])
    original = deepcopy(summary["llm_commercial_judge_original"])
    proof, view = summary["commercial_action_projection"], summary["llm_commercial_judge"]
    paid_slots = deepcopy(proof["slots"])
    index, action = next((i, a) for i, a in enumerate(proof["issue_actions"])
                         if a["assessment_kind"] == "current_fact_conflict" and a["effective_action"] == "block")
    issue = next(i for i in view["blocking_issues"] if i["action_receipt_index"] == index)
    if tamper in {"reclassify", "demote"}:
        view["blocking_issues"].remove(issue)
        view["audit_issues"].append(issue)
        action["effective_action"] = "audit_only"
        if tamper == "reclassify":
            issue["assessment_kind"] = action["assessment_kind"] = "narrative_quality"
            action["authority"] = "craft_only"
            for origin in action["lineage"]:
                origin["issue"]["assessment_kind"] = "narrative_quality"
    elif tamper == "omit_action":
        view["blocking_issues"].remove(issue)
        del proof["issue_actions"][index]
        for group in ("blocking_issues", "audit_issues"):
            for item in view[group]:
                if item["action_receipt_index"] > index:
                    item["action_receipt_index"] -= 1
    elif tamper == "lineage_kind":
        for origin in action["lineage"]:
            origin["issue"]["assessment_kind"] = "retained_threshold_policy"
    else:
        action["aggregate_group"], action["aggregate_index"] = "audit_issues", 0
    proof["effective_blocking_count"] = len(view["blocking_issues"])
    proof["projection_sha256"] = sha(view)
    assert summary["llm_commercial_judge_original"] == original
    assert proof["slots"] == paid_slots
    assert view["raw_excerpt"] == original["raw_excerpt"]
    with pytest.raises(ValueError, match="commercial_action_proof_unverified"):
        _commercial_effective_fact_blockers((summary,), payload["reviewed_body_fingerprint"])


async def test_actual_current_review_threshold_miss_remains_soft_offline():
    root = Path(__file__).resolve().parents[2]
    current = root / "reports/framework-recovery-2026-10-21/phase8u-current-review-and-pointer.json"
    independent = root / "reports/framework-remediation-2026-10-22/chapter2-v6-review-independent.json"
    before = current.read_bytes()
    draft_id = json.loads(independent.read_bytes())["draft_id"]
    payload = next(r["output"] for r in json.loads(before)["reports"] if r["output"]["draft_id"] == draft_id)
    summary = payload["evidence_summary"]
    blocks = [a for a in summary["commercial_action_projection"]["issue_actions"] if a["effective_action"] == "block"]
    assert blocks and {a["assessment_kind"] for a in blocks} == {"retained_threshold_policy"}
    assert _commercial_effective_fact_blockers((summary,), payload["reviewed_body_fingerprint"]) == []
    assert current.read_bytes() == before


async def test_public_fact_action_api_returns_detached_full_raw_proven_lineage(candidates):
    summary = install_projection(candidates)
    before = deepcopy(summary)
    actions = verified_commercial_actions(summary, candidates[4][0].content_md)
    assert isinstance(actions, tuple) and actions
    expected = summary["commercial_action_projection"]["issue_actions"]
    assert list(actions) == expected
    assert all(a["lineage"][0]["issue"]["body_citations"][0]["id"] for a in actions)
    actions[0]["lineage"][0]["issue"]["body_citations"][0]["quote"] = "caller-local change"
    assert summary == before


@pytest.mark.parametrize("kind", ["retained_threshold_policy", "narrative_quality"])
async def test_public_api_preserves_all_nonfact_actions_without_vetoing_soft_candidate(candidates, kind):
    summary = install_projection(candidates, kind=kind, code="ENDING_SENTENCE_WEAK",
                                 score=.4 if kind == "retained_threshold_policy" else .91)
    actions = verified_commercial_actions(summary, candidates[4][0].content_md)
    assert list(actions) == summary["commercial_action_projection"]["issue_actions"]
    assert any(a["effective_action"] == "block" and a["authority"] != "verified_source" for a in actions)
    assert (await choose(candidates)).id == candidates[4][0].id


@pytest.mark.parametrize("change", ["missing", "body", "unknown"])
async def test_public_fact_scope_fails_closed_without_authorizing_any_edit(candidates, change):
    summary = install_projection(candidates)
    body = candidates[4][0].content_md
    if change == "missing":
        summary = {}
    elif change == "body":
        body += "unreviewed prose"
    else:
        summary["commercial_action_projection"]["status"] = "unknown"
    with pytest.raises(ValueError, match="commercial_action_proof_unverified"):
        verified_commercial_actions(summary, body)


async def test_mixed_fact_and_threshold_raw_actions_cannot_clear_threshold_for_fact_only_scope(candidates):
    summary = install_projection(candidates, kind="current_fact_conflict", severity="medium",
                                 score=.4, effective="block")
    body = candidates[4][0].content_md
    actions = verified_commercial_actions(summary, body)
    assert {a["assessment_kind"] for a in actions if a["effective_action"] == "block"} == {
        "current_fact_conflict", "retained_threshold_policy",
    }
    original = deepcopy(summary["llm_commercial_judge_original"])
    proof, view = summary["commercial_action_projection"], summary["llm_commercial_judge"]
    for index, action in enumerate(proof["issue_actions"]):
        if action["assessment_kind"] != "retained_threshold_policy":
            continue
        issue = next(i for i in view["blocking_issues"] if i["action_receipt_index"] == index)
        view["blocking_issues"].remove(issue)
        view["audit_issues"].append(issue)
        action["effective_action"] = "audit_only"
    proof["effective_blocking_count"] = len(view["blocking_issues"])
    proof["projection_sha256"] = sha(view)
    assert summary["llm_commercial_judge_original"] == original
    with pytest.raises(ValueError, match="commercial_action_proof_unverified"):
        verified_commercial_actions(summary, body)


async def test_registered_craft_audit_policy_cannot_clear_scope_without_full_gate_config(candidates):
    summary = install_projection(candidates, kind="narrative_quality", code="ENDING_SENTENCE_WEAK")
    body = candidates[4][0].content_md
    assert any(a["effective_action"] == "block" for a in verified_commercial_actions(summary, body))
    original = deepcopy(summary["llm_commercial_judge_original"])
    proof, view = summary["commercial_action_projection"], summary["llm_commercial_judge"]
    issue = view["blocking_issues"].pop()
    view["audit_issues"].append(issue)
    proof["issue_actions"][issue["action_receipt_index"]]["effective_action"] = "audit_only"
    proof["effective_blocking_count"], proof["projection_sha256"] = 0, sha(view)
    assert summary["llm_commercial_judge_original"] == original
    with pytest.raises(ValueError, match="commercial_action_policy_unverified"):
        verified_commercial_actions(summary, body)
    save_summary(candidates, 0, summary)
    # This stricter repair-scope API does not turn craft advice into a new
    # veto on the existing stall-selection soft-debt policy.
    assert (await choose(candidates)).id == candidates[4][0].id
