"""Four-result historical API under the exact issued fifth initializer.

Full archived runtime verification is real; current-sixth behavior is tested in
epoch4 separately. Alias cycle bodies below are explicit
historical fixtures and do not assert issued model results or production rows.
"""

from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest
from test_native_policy_epoch3_transition import exact_issued_fifth_context  # noqa: F401

from bestseller.services import chapter_review_policy_history_8y24 as archive
from bestseller.services import chapter_review_reconciliation as service

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def no_external_io(monkeypatch):
    import socket

    from sqlalchemy.engine import Engine
    from sqlalchemy.ext.asyncio import AsyncEngine

    from bestseller.services import llm

    def denied(*args, **kwargs):
        raise AssertionError(
            "Epoch2 tests forbid DB acquisition, provider calls and socket connections"
        )

    monkeypatch.setattr(Engine, "connect", denied)
    monkeypatch.setattr(AsyncEngine, "connect", denied)
    monkeypatch.setattr(AsyncEngine, "begin", denied)
    monkeypatch.setattr(llm, "complete_text", denied)
    monkeypatch.setattr(socket, "create_connection", denied)
    monkeypatch.setattr(socket.socket, "connect", denied)


def _epochs():
    documents = archive._verified_four_epochs()
    shas = (archive.ORIGINAL_SHA256, archive.CURRENT_SHA256,
            archive.A296_SHA256, archive.FINAL_SHA256)
    return documents, shas


def _edge(source, target):
    documents, shas = _epochs()
    return archive._proof(documents[source], documents[target], shas[source], shas[target])


def _cycle(*, record=None):
    entry = dict(attempt=1, operation_namespace="chapter-critic:fixture:1", state="prepared")
    if record is not None:
        entry["infrastructure_history"] = [deepcopy(record)]
    return dict(cycle_id="fixture", source_draft_id="draft", source_body_hash="body",
                policy_source_hash=archive.OLD_POLICY, review_workflow_run_id="owner",
                semantic_dependencies_sha256="semantic", attempts=[entry], state="prepared")


def _wrap(cycle, source, target):
    transition = _edge(source, target)
    assert cycle["policy_source_hash"] == transition["previous_policy_hash"]
    alias = dict(schema=service._NATIVE_ALIAS, project_id="project", chapter_id="chapter",
                 draft_id="draft", body_sha256="body", cycle_id="fixture",
                 review_workflow_run_id="owner", transition=transition,
                 original_cycle=deepcopy(cycle), input_proof={"synthetic_history": True},
                 ledger_sha256="fixture-ledger", control_command_serial=3,
                 provider_calls=0, new_credit=False, dispatch_authorized=False,
                 grade_recomputed=False, adoption_authorized=False)
    alias["receipt_sha256"] = service._native_digest(alias)
    result = deepcopy(cycle)
    result["policy_source_hash"] = transition["current_policy_hash"]
    result["native_runtime_policy_alias"] = alias
    return result


def _validate(cycle):
    return service.validate_native_policy_alias(cycle, project_id="project",
        chapter_id="chapter", draft_id="draft", body_sha256="body")


def _reseal(cycle):
    alias = cycle["native_runtime_policy_alias"]
    plain = deepcopy(alias)
    plain.pop("receipt_sha256")
    alias["receipt_sha256"] = service._native_digest(plain)


def test_historical_archives_are_exact_before_final_epoch_is_enabled():
    for manifest_name, digest, reader in (
        ("original.json", archive.ORIGINAL_SHA256, archive.original_bytes),
        ("current.json", archive.CURRENT_SHA256, archive.prior_bytes),
        ("a296-final.json", archive.A296_SHA256, archive.a296_bytes),
    ):
        document = archive._document(manifest_name, digest)
        assert len(document["inventory"]) == 263
        assert archive._aggregate(document["inventory"]) == document["policy_hash"]
        for name, expected in {**document["inventory"], **document["additional_sources"]}.items():
            assert hashlib.sha256(reader(name)).hexdigest() == expected


@pytest.mark.parametrize("relative", [archive._HELPER, archive._SHORT_PIPELINE])
def test_frozen_main_source_structural_fences_reject_unrelated_changes(relative):
    previous = archive.a296_bytes(relative)
    current = (archive.ROOT / relative).read_bytes()
    assert hashlib.sha256(current).hexdigest() == archive.REVIEWED_BEAT_RESUME_SHA256[relative]
    archive._beat_resume_boundary(previous, current, relative)
    changed = current + b"\nUNRELATED_POLICY_CHANGE = True\n"
    with pytest.raises(ValueError):
        archive._beat_resume_boundary(previous, changed, relative)


def test_four_frozen_epochs_preserve_unissued_archive_and_reviewed_current_inventory():
    original, prior, a296, current = archive._verified_four_epochs()
    assert archive._verified_epochs() == (original, prior, current)
    assert [len(d["inventory"]) for d in (original, prior, a296, current)] == [263] * 4
    unissued_raw = (archive.HERE / "unissued-3e51/final.json").read_bytes()
    assert hashlib.sha256(unissued_raw).hexdigest() == (
        "78c202ca2204c2c12477558b2f590c294e2ee8f79d2e3de62dc19d1107e7e7c6")
    unissued = json.loads(unissued_raw)
    assert sorted(n for n in a296["inventory"]
                  if a296["inventory"][n] != unissued["inventory"][n]) == sorted((
        archive._HELPER, archive._SHORT_PIPELINE, archive._BEAT))
    expected = {
        "chapter_scene_contract_materializer", "chapter_writer_delivery", "drafts",
        "editable_plan_source", "fanqie_beat_recovery", "fanqie_short_pipeline",
        "fanqie_short_plan_resume", "fanqie_short_planner",
        "materialized_outline_admission_read", "outline_source_entry", "planning_surgical_budget",
    }
    issued_raw = (archive.HERE / "issued-c90a/final.json").read_bytes()
    assert hashlib.sha256(issued_raw).hexdigest() == (
        "5a2697cc87cb330f9057160de77696d67eaab50d18aa67f91b2b0db5f2184d7d")
    issued = json.loads(issued_raw)
    assert issued["policy_hash"] == (
        "c90a6d32172332d754ca4d59d39cd109e42b0b8444fcfa456ea73522a8aa9796")
    assert {n for n in a296["inventory"]
            if a296["inventory"][n] != issued["inventory"][n]} == {
                "src/bestseller/services/" + name + ".py" for name in expected}
    expected.add("commercial_judge_recovery")
    expected.add("execution_control_purpose")
    assert {n for n in a296["inventory"]
            if a296["inventory"][n] != current["inventory"][n]} == {
                "src/bestseller/services/" + name + ".py" for name in expected}
    assert current["policy_hash"] == archive.EPOCH3_POLICY == (
        "1a5a25074912ecd42191f4fdd5c0be4e80d00414cc6dc4e84526174e9ebf1530")
    assert current["inventory"]["src/bestseller/services/commercial_judge_recovery.py"] == (
        "0c929fff84e3075996c25cc11b589df2c42bc619e6bd53acda1ef4e7d5a65fd7")
    assert current["inventory"]["src/bestseller/services/execution_control_purpose.py"] == (
        "70e09a76e170c9ba139c3135d597dfaabc05a018cc1013de0cb2c543bbc0a3c8")
    assert current["inventory"]["src/bestseller/services/pipelines.py"] == (
        a296["inventory"]["src/bestseller/services/pipelines.py"])
    assert a296["inventory"][archive._SHORT_PIPELINE] == (
        "aed2f6820c7476e97186c92932b38dcd605f3f440971adaccac0e99b63f14a99")
    for name, digest in {**a296["inventory"], **a296["additional_sources"]}.items():
        assert hashlib.sha256(archive.a296_bytes(name)).hexdigest() == digest


@pytest.mark.parametrize("source", [0, 1, 2])
def test_native_selector_uses_only_direct_edge_to_final(source):
    documents, _ = _epochs()
    expected = _edge(source, 3)
    assert archive.verified_transition_from(documents[source]["policy_hash"]) == expected
    assert expected["provider_calls"] == 0
    assert expected["new_credit"] is expected["dispatch_authorized"] is False


@pytest.mark.parametrize("source,target", [(0, 1), (0, 2), (1, 2), (0, 3), (1, 3), (2, 3)])
def test_all_six_known_edges_replay_without_receipt_rewriting(source, target):
    claimed = _edge(source, target)
    before = deepcopy(claimed)
    assert archive.verified_alias_transition(claimed) is claimed
    assert claimed == before
    if target == 2:
        assert claimed["current_manifest_sha256"] == (
            "ad84ee5bf4a0fcbee4a1d55ea39ef7d5039dc32718c2cd1ccf5c794bc55f3371")
    if (source, target) == (1, 2):
        assert claimed["reviewed_delta_files"] == sorted((archive._HELPER, archive._RECONCILIATION))


@pytest.mark.parametrize("path", [(0, 1), (0, 2), (0, 1, 2), (1, 2), (0, 3),
                                 (0, 2, 3), (0, 1, 3), (1, 2, 3), (0, 1, 2, 3)])
def test_existing_a296_and_three_receipt_histories_remain_exact(path):
    cycle = _cycle()
    if path[0] == 1:
        cycle["policy_source_hash"] = archive.PRIOR_POLICY
    for source, target in zip(path, path[1:]):
        original = deepcopy(cycle)
        cycle = _wrap(cycle, source, target)
        assert cycle["native_runtime_policy_alias"]["original_cycle"] == original
    before = deepcopy(cycle)
    _validate(cycle)
    assert cycle == before


def test_fourth_alias_receipt_is_rejected_before_recursing():
    with pytest.raises(ValueError, match="alias_chain_depth"):
        service._validate_native_alias_chain(_cycle(), project_id="project", chapter_id="chapter",
            draft_id="draft", body_sha256="body", depth=3)


@pytest.mark.parametrize("source,target", [(0, 0), (1, 0), (2, 1), (3, 2)])
def test_known_epoch_hashes_do_not_authorize_reverse_or_self_edges(source, target):
    with pytest.raises(ValueError, match="unknown_alias_transition"):
        archive.verified_alias_transition(_edge(source, target))


@pytest.mark.parametrize("fault", ["unknown", "digest", "body", "namespace", "history", "flags"])
def test_mutated_nested_history_fails_closed(fault):
    record = {"review_report_id": "report", "waiting_receipt": {"immutable": True}}
    cycle = _wrap(_wrap(_wrap(_cycle(record=record), 0, 1), 1, 2), 2, 3)
    alias = cycle["native_runtime_policy_alias"]
    if fault == "unknown":
        alias["transition"]["previous_policy_hash"] = "f" * 64
    elif fault == "digest":
        alias["receipt_sha256"] = "f" * 64
    elif fault == "body":
        alias["original_cycle"]["source_body_hash"] = "changed"
    elif fault == "namespace":
        cycle["attempts"][0]["operation_namespace"] = "different"
    elif fault == "history":
        cycle["attempts"][0]["infrastructure_history"][0]["waiting_receipt"]["immutable"] = False
    else:
        alias["new_credit"] = True
    if fault != "digest":
        _reseal(cycle)
    before = deepcopy(cycle)
    with pytest.raises(ValueError):
        _validate(cycle)
    assert cycle == before


@pytest.mark.parametrize("introduced", [0, 1, 2])
def test_wait_record_binds_deepest_contiguous_original_domain(introduced):
    record = {"review_report_id": "report", "waiting_receipt": {"immutable": True}}
    cycle = _cycle(record=record if introduced == 0 else None)
    for source, target in ((0, 1), (1, 2), (2, 3)):
        cycle = _wrap(cycle, source, target)
        if target == introduced:
            cycle["attempts"][0]["infrastructure_history"] = [deepcopy(record)]
    binding = dict(project_id="project", chapter_id="chapter", source_draft_id="draft",
                   source_body_hash="body", policy_source_hash=cycle["policy_source_hash"])
    before = deepcopy(cycle)
    actual = service.native_waiting_record_binding(cycle, cycle["attempts"][0], record, binding)
    documents, _ = _epochs()
    assert actual == {**binding, "policy_source_hash": documents[introduced]["policy_hash"]}
    assert cycle == before


def test_prepared_once_old_binding_is_not_rebound_for_another_upgrade():
    # The strict existing resumption validator must reject, not normalize, an
    # E1-bound once receipt when the locked E2 proof names a different policy.
    from bestseller.services.waiting_chapter_review_resume import POLICY

    cycle = _cycle()
    cycle["policy_source_hash"] = archive.A296_POLICY
    proof = dict(project_id="project", chapter_id="chapter", draft_id="draft",
                 body_sha256="body", cycle_id="fixture", policy_source_hash=archive.A296_POLICY,
                 semantic_dependencies_sha256="semantic")
    plan = dict(schema=POLICY, status="prepared_plan", database_writes=0,
                binding={**proof, "policy_source_hash": archive.PRIOR_POLICY},
                operation_namespace=cycle["attempts"][0]["operation_namespace"],
                review_workflow_run_id="owner", attempt=1, attempt_count=1,
                new_attempts=0, provider_calls=0, credit_released=False,
                dispatch_authorized=False, adoption_authorized=False)
    plan["plan_sha256"] = service._native_digest(plan)
    cycle["infrastructure_resumptions"] = [
        {**plan, "status": "rearmed", "state_after": "prepared"}]
    before = deepcopy(cycle)
    with pytest.raises(ValueError, match="prior_resumption_binding"):
        service._validate_prior_prepared_resumption(cycle, proof)
    assert cycle == before


@pytest.mark.parametrize("target", ["a296_manifest", "a296_helper", "a296_short_pipeline",
                                   "a296_beat", "a296_reconciliation", "runtime", "final_manifest"])
def test_frozen_archive_and_complete_runtime_drift_are_rejected(monkeypatch, target):
    paths = {"a296_manifest": archive.HERE / "a296-final.json",
             "runtime": archive.ROOT / "src/bestseller/services/pipelines.py",
             "final_manifest": archive.HERE / "final.json"}
    for key, name in (("helper", archive._HELPER), ("short_pipeline", archive._SHORT_PIPELINE),
                      ("beat", archive._BEAT), ("reconciliation", archive._RECONCILIATION)):
        paths["a296_" + key] = archive.HERE / "a296-bytes" / (name + ".source")
    read = Path.read_bytes
    monkeypatch.setattr(
        Path, "read_bytes", lambda p: read(p) + b"\n# drift" if p == paths[target] else read(p)
    )
    with pytest.raises(ValueError):
        archive.verified_transition()


def test_unreviewed_final_pins_never_autocapture_runtime(monkeypatch):
    monkeypatch.setattr(archive, "FINAL_SHA256", None)
    with pytest.raises(ValueError, match="final_epoch_not_reviewed"):
        archive.verified_transition()


def test_unknown_archive_source_and_unknown_selector_rejected():
    with pytest.raises(ValueError, match="unknown_a296_source"):
        archive.a296_bytes("../untrusted.py")
    with pytest.raises(ValueError, match="unsupported_previous_policy"):
        archive.verified_transition_from("f" * 64)
