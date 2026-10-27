"""Exact eighth policy node; historical grades are not new dispatch authority."""

import ast
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil

import pytest
from test_commercial_component_cohort import cohort  # noqa: F401
from test_native_chapter_review_resume import completed, native_scope, snapshot  # noqa: F401
from test_retained_review_input_proof import retained  # noqa: F401

from bestseller.infra.db import models as db
from bestseller.services import chapter_review_policy_history_8y24 as history
from bestseller.services import chapter_review_preflight as preflight
from bestseller.services import chapter_review_reconciliation as reconciliation
from bestseller.services import pipelines
from bestseller.services import retained_review_input_proof as retained_proof
from bestseller.services import waiting_chapter_review_resume as waiting
from bestseller.services.reviews import rewrite_source_binding

pytestmark = pytest.mark.unit
ELIGIBILITY = "src/bestseller/services/chapter_draft_candidate_eligibility.py"
RECONCILIATION = "src/bestseller/services/chapter_review_reconciliation.py"


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def test_issued_b8a_is_complete_and_immutable():
    document = history.verified_issued_b8a_archive()
    assert document["policy_hash"] == history.EPOCH5_POLICY
    assert len(document["inventory"]) == 265
    assert len(document["additional_sources"]) == 2
    for path, expected in {
        **document["inventory"], **document["additional_sources"],
    }.items():
        assert digest(history.issued_b8a_bytes(path)) == expected
    assert len(history.known_b8a_transitions()) == 21


def test_eighth_endpoint_preserves_all_previous_nodes_and_zero_credit():
    documents = history._verified_eight_epochs()
    assert len(documents) == 8
    assert documents[-2]["policy_hash"] == history.EPOCH5_POLICY
    assert documents[-1]["policy_hash"] == history.EPOCH6_POLICY
    previous = {**documents[-2]["inventory"], **documents[-2]["additional_sources"]}
    current = {**documents[-1]["inventory"], **documents[-1]["additional_sources"]}
    assert {name for name in current if current[name] != previous[name]} == {
        ELIGIBILITY, RECONCILIATION,
    }
    proof = history.verified_transition_from(history.EPOCH5_POLICY)
    assert proof == history.verified_alias_transition(deepcopy(proof))
    assert proof["provider_calls"] == 0
    assert all(proof[key] is False for key in (
        "new_credit", "dispatch_authorized", "grade_recomputed",
        "acceptance_semantics_identical",
    ))
    assert proof["raw_grade_reports_attempts_immutable"] is True


def test_b8a_forward_receipts_are_replayed_without_rewriting_them():
    edges = history.known_b8a_transitions()
    saved = deepcopy(edges)
    # known_b8a_transitions verifies the entire immutable 21-edge document.
    # One real public replay checks selection without repeating full inventory
    # IO for every member of that already verified document.
    assert history.verified_alias_transition(edges[-1]) == edges[-1]
    assert edges == saved


def test_only_explicit_runtime_datafiles_are_trusted():
    manifest = history.verified_issued_b8a_archive()
    index = json.loads((history.HERE / "issued-b8a/manifest.json").read_bytes())
    expected = {"bytes/" + p + ".source" for p in {
        **manifest["inventory"], **manifest["additional_sources"],
    }}
    assert set(index["source_files"]) == expected
    assert len(expected) == 267
    assert len(expected | {"manifest.json", "final.json", "__init__.py",
                           "known-forward-edges.json"}) == 271
    assert all(not Path(p).is_absolute() and ".." not in Path(p).parts for p in expected)
    with pytest.raises(ValueError, match="unknown_issued_b8a_source"):
        history.issued_b8a_bytes("../../reports/untrusted.py")


@pytest.mark.parametrize("part", ["manifest.json", "__init__.py", "known-forward-edges.json",
                                  "bytes/" + ELIGIBILITY + ".source"])
def test_any_changed_issued_b8a_file_is_rejected(tmp_path, monkeypatch, part):
    target = tmp_path / "history"
    shutil.copytree(history.HERE / "issued-b8a", target / "issued-b8a")
    path = target / "issued-b8a" / part
    path.write_bytes(path.read_bytes() + b"\n")
    monkeypatch.setattr(history, "HERE", target)
    with pytest.raises(ValueError, match="manifest_not_frozen|issued_b8a_source_changed"):
        history.verified_issued_b8a_archive()


@pytest.mark.parametrize("change", ["zero_type", "abstention", "quorum_return", "canonical_guard",
                                  "native_slot", "typed_receipt"])
def test_exact_consumer_ast_rejects_an_extra_permission_even_with_a_new_hash(change):
    previous = history.issued_b8a_bytes(ELIGIBILITY)
    current = (history.ROOT / ELIGIBILITY).read_bytes()
    mutations = {
        "zero_type": (b"type(neutral[key]) is int", b"isinstance(neutral[key], int)"),
        "abstention": (b'neutral.get("abstains") is True', b'bool(neutral.get("abstains"))'),
        "quorum_return": (b'actions = _quorum_actions(summary, body["digest"])', b"actions = []"),
        "canonical_guard": (b'proof.get("unknown_sample_slots") == []', b"True"),
        "native_slot": (b'_slot(sample["report"], sample["sample_slot"], sources, {}, rebuilt_slots)',
                        b"pass"),
        "typed_receipt": (b"_json(rebuilt_slots) == _json(neutral_slots)",
                          b"rebuilt_slots == neutral_slots"),
    }
    before, after = mutations[change]
    assert current.count(before) == 1
    changed = current.replace(before, after, 1)
    assert digest(changed) != digest(current)
    with pytest.raises(ValueError, match="neutral_quorum_.*(changed|invalid)"):
        history._neutral_quorum_consumer_boundary(previous, changed)


def test_consumer_insertion_preserves_every_original_canonical_statement():
    previous = history.issued_b8a_bytes(ELIGIBILITY)
    current = (history.ROOT / ELIGIBILITY).read_bytes()
    history._neutral_quorum_consumer_boundary(previous, current)
    before, after = ast.parse(previous), ast.parse(current)
    functions = lambda module: {
        n.name: ast.dump(n, include_attributes=False)
        for n in module.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    old, new = functions(before), functions(after)
    assert set(old) == set(new)
    assert {name for name in old if old[name] != new[name]} == {"_verified_commercial_actions"}


@pytest.mark.parametrize("change", ["depth", "attempt_identity", "raw_grade"])
def test_reconciliation_only_extends_the_exact_new_root_depth(change):
    previous = history.issued_b8a_bytes(RECONCILIATION)
    current = (history.ROOT / RECONCILIATION).read_bytes()
    history._neutral_quorum_reconciliation_boundary(previous, current)
    mutations = {
        "depth": (b"depth < 7", b"depth < 8"),
        "attempt_identity": (b"len(current) <= 2", b"len(current) <= 3"),
        "raw_grade": (b"_native_need(old == new, 'alias_valid_result_changed')",
                      b"_native_need(True, 'alias_valid_result_changed')"),
    }
    old, new = mutations[change]
    assert current.count(old) == 1
    with pytest.raises(ValueError, match="neutral_quorum_.*changed"):
        history._neutral_quorum_reconciliation_boundary(previous, current.replace(old, new, 1))


@pytest.mark.parametrize("value", [0, True])
def test_issued_transition_authority_fields_are_type_sensitive(value):
    documents = history._verified_eight_epochs()
    proof = history._epoch6_proof(documents[-2], documents[-1],
                                 history.EPOCH5_FINAL_SHA256, history.EPOCH6_FINAL_SHA256)
    proof["new_credit"] = value
    with pytest.raises(ValueError, match="unknown_alias_transition"):
        history.verified_alias_transition(proof)


def test_real_current_inventory_is_required_for_alias_runtime(tmp_path, monkeypatch):
    current = json.loads((history.HERE / "final.json").read_bytes())
    for name in {**current["inventory"], **current["additional_sources"]}:
        out = tmp_path / name
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes((history.ROOT / name).read_bytes())
    changed = tmp_path / "src/bestseller/services/feedback.py"
    changed.write_bytes(changed.read_bytes() + b"\n")
    monkeypatch.setattr(history, "ROOT", tmp_path)
    with pytest.raises(ValueError, match="unlisted_runtime_delta"):
        history.verified_source_alias_runtime()


def test_only_the_exact_eighth_root_gets_seven_alias_levels():
    documents = history._verified_eight_epochs()
    new = history._epoch6_proof(documents[-2], documents[-1],
                               history.EPOCH5_FINAL_SHA256, history.EPOCH6_FINAL_SHA256)
    old = history.known_b8a_transitions()[-1]
    assert history.verified_alias_depth_limit(new) == 7
    assert history.verified_alias_depth_limit(old) == 6
    assert new["current_policy_hash"] != old["current_policy_hash"]


@pytest.mark.asyncio
async def test_completed_b8a_paid_rewrite_with_prior_alias_and_once_upgrades_without_rearm(
    completed, monkeypatch,  # noqa: F811
):
    """Actual SQLite paid primary/window/cohort fixture; no dispatch after setup.

    The issued alias/once history below is historical fixture data, not a grant
    produced by this test. All current source, policy, paid physical and ledger
    checks run normally. The actual live length packet has separate consumer
    tests; this fixture isolates its completed/available lifecycle boundary.
    """
    r = completed
    cycle = deepcopy(r.cycle)
    cycle["policy_source_hash"] = history.EPOCH4_POLICY
    entry = cycle["attempts"][0]
    result = deepcopy(entry["review_result"])
    result["verdict"] = "rewrite"
    result["rewrite_instructions"] = "Repair the source-bound reviewed passage, then review again."
    r.report.verdict = "rewrite"
    task = db.RewriteTaskModel(
        project_id=r.project.id,
        trigger_type="chapter_review",
        trigger_source_id=r.chapter.id,
        rewrite_strategy="chapter_coherence_bridge_rewrite",
        priority=1,
        status="pending",
        instructions=result["rewrite_instructions"],
        metadata_json={**rewrite_source_binding(r.draft, chapter=r.chapter),
                       "chapter_id": str(r.chapter.id),
                       "review_report_id": str(r.report.id)},
    )
    r.session.add(task)
    await r.session.flush()
    entry.update(review_result=result, rewrite_task_id=str(task.id))
    # Preserve a spent historical once record, without claiming a new native
    # prerequisite or manufacturing history_replays/model capacity.
    cycle["infrastructure_resumptions"] = [{
        "schema": waiting.POLICY, "status": "rearmed", "state_after": "prepared",
        "attempt": entry["attempt"], "operation_namespace": entry["operation_namespace"],
        "review_workflow_run_id": cycle["review_workflow_run_id"],
        "new_attempts": 0, "provider_calls": 0, "credit_released": False,
        "dispatch_authorized": False, "adoption_authorized": False,
    }]
    await r.session.commit()
    paid = await retained_proof.verify_retained_review_inputs(
        r.session, r.settings, project=r.project, chapter=r.chapter,
        draft=r.draft, report=r.report, cycle=cycle,
    )
    edges = history.known_b8a_transitions()
    edge = next(p for p in edges if p["previous_policy_hash"] == history.EPOCH4_POLICY
                and p["current_policy_hash"] == history.EPOCH5_POLICY)
    alias = {
        "schema": reconciliation._NATIVE_ALIAS,
        "project_id": str(r.project.id), "chapter_id": str(r.chapter.id),
        "draft_id": str(r.draft.id), "body_sha256": cycle["source_body_hash"],
        "cycle_id": cycle["cycle_id"],
        "review_workflow_run_id": cycle["review_workflow_run_id"],
        "transition": edge, "original_cycle": deepcopy(cycle), "input_proof": paid,
        "ledger_sha256": reconciliation._native_digest(
            await waiting._ledger(r.session, native_scope_value(r), lock=False)),
        "control_command_serial": 3, "provider_calls": 0, "new_credit": False,
        "dispatch_authorized": False, "grade_recomputed": False, "adoption_authorized": False,
    }
    alias["receipt_sha256"] = reconciliation._native_digest(alias)
    cycle.update(policy_source_hash=history.EPOCH5_POLICY, native_runtime_policy_alias=alias)
    r.chapter.metadata_json = {
        **r.chapter.metadata_json, "chapter_critic_cycles": {r.cid: cycle},
    }
    r.owner.status = "machine_blocked"
    r.owner.current_step = "waiting_infrastructure"
    await r.session.commit()

    async def no_rearm_or_dispatch(*args, **kwargs):
        raise AssertionError("An available paid result must never rearm or review again")

    monkeypatch.setattr(waiting, "rearm_waiting_review_native", no_rearm_or_dispatch)
    monkeypatch.setattr(waiting, "_normal_commercial_prerequisite", no_rearm_or_dispatch)
    monkeypatch.setattr(pipelines, "review_chapter_draft", no_rearm_or_dispatch)
    before = await snapshot(r)
    answer = await reconciliation.prepare_native_review_resume(
        r.session, r.settings, project_id=r.project.id, chapter_id=r.chapter.id,
    )
    assert answer["status"] == "prepared"
    leaf = answer["chapter_receipts"][0]
    assert leaf["waiting_rearm"] is None
    assert leaf["policy_alias"]["input_proof"]["paid_rows"] == paid["paid_rows"]
    reuse = await preflight.validate_retained_review_before_processing(
        r.session, r.settings, r.chapter,
    )
    assert reuse["reuse_kind"] == "available_review" and reuse["verdict"] == "rewrite"
    consumed = await pipelines._review_chapter_with_evidence_retry(
        r.session, r.settings, r.project.slug, r.chapter.chapter_number,
        workflow=r.owner, draft=r.draft,
    )
    assert consumed[1].id == r.report.id and consumed[2].id == r.quality.id
    assert consumed[3].id == task.id and consumed[3].status == "pending"
    current = r.chapter.metadata_json["chapter_critic_cycles"][r.cid]
    assert current["attempts"] == cycle["attempts"]
    assert current["infrastructure_resumptions"] == cycle["infrastructure_resumptions"]
    assert current["native_runtime_policy_alias"]["original_cycle"] == cycle
    after = await snapshot(r)
    for table, rows in before.items():
        if table != "chapters":
            assert after[table] == rows, table
    repeat = await reconciliation.prepare_native_review_resume(
        r.session, r.settings, project_id=r.project.id, chapter_id=r.chapter.id,
    )
    assert repeat["status"] == "unchanged" and await snapshot(r) == after


def native_scope_value(r):
    return r.project.metadata_json["execution_scope_id"]
