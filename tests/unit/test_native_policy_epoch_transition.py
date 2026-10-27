"""Historical three-result API plus actual-current native SQLite paid proofs.

Only the first historical inventory test uses the exact issued-fifth snapshot.
All remaining native lifecycle tests use the actual current policy and readers.
No history renderer or paid validator is mocked.
The prior-issued receipt fixture is explicit synthetic historical data, checked
against every current native reader; it is not a claim about production rows.
"""

# ruff: noqa: F811

import ast
from copy import deepcopy
import hashlib
from pathlib import Path

import pytest
from test_native_chapter_review_resume import (  # noqa: F401
    cohort,
    completed,
    native_scope,
    native_wait,
    retained,
    snapshot,
)
import test_native_policy_epoch3_transition as historical_policy_contract

from bestseller.services import chapter_review_policy_history_8y24 as archive
from bestseller.services import chapter_review_preflight as preflight
from bestseller.services import chapter_review_reconciliation as service
from bestseller.services import pipelines
from bestseller.services import waiting_chapter_review_resume as waiting
from bestseller.services.chapter_review_prerequisite import WAITING, wait_binding
from bestseller.services.retained_review_input_proof import verify_retained_review_inputs

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@pytest.fixture
def frozen_three_result_api_context(monkeypatch, tmp_path, request):
    """Retain the original 263-file assertion against a real frozen runtime.

    Execute the exact old native hashing function over all copied preimages;
    this is not a constant replacement or a claim about current-sixth inputs.
    """
    historical_policy_contract.exact_issued_fifth_context.__wrapped__(
        monkeypatch, tmp_path, request,
    )
    fifth = archive._document("final.json", archive.FINAL_SHA256)
    sources = {**fifth["inventory"], **fifth["additional_sources"]}
    for relative in sources:
        source = archive.HERE / "issued-1a5a/bytes" / (relative + ".source")
        target = archive.ROOT / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        raw = source.read_bytes()
        assert hashlib.sha256(raw).hexdigest() == sources[relative]
        target.write_bytes(raw)
    frozen_pipeline = archive.ROOT / "src/bestseller/services/pipelines.py"
    function = next(n for n in ast.parse(frozen_pipeline.read_bytes()).body
                    if isinstance(n, ast.FunctionDef)
                    and n.name == "chapter_review_recovery_source_hash")
    namespace = {"__file__": str(frozen_pipeline), "Path": Path, "hashlib": hashlib}
    module = ast.Module(body=[function], type_ignores=[])
    exec(compile(module, str(frozen_pipeline), "exec"), namespace)
    exact_native_hash = namespace["chapter_review_recovery_source_hash"]
    assert exact_native_hash() == (
        "1a5a25074912ecd42191f4fdd5c0be4e80d00414cc6dc4e84526174e9ebf1530"
    )
    monkeypatch.setattr(pipelines, "chapter_review_recovery_source_hash", exact_native_hash)


def _cycle(r):
    return deepcopy(r.chapter.metadata_json["chapter_critic_cycles"][r.cid])


async def _store(r, cycle):
    meta = deepcopy(r.chapter.metadata_json)
    meta["chapter_critic_cycles"][r.cid] = deepcopy(cycle)
    r.chapter.metadata_json = meta
    await r.session.commit()


async def _prior_alias(r, *, prepared=False):
    """Build old issued data using REAL old-domain proof + native paid sources."""
    original = _cycle(r)
    proof = await preflight._validate_retained_review_before_processing(
        r.session, r.settings, r.chapter, expected_policy_hash=archive.OLD_POLICY
    )
    old, prior, _ = archive._verified_epochs()
    transition = archive.verified_alias_transition(
        archive._proof(old, prior, archive.ORIGINAL_SHA256, archive.CURRENT_SHA256)
    )
    if proof["reuse_kind"] == "available_review":
        paid = await verify_retained_review_inputs(
            r.session,
            r.settings,
            project=r.project,
            chapter=r.chapter,
            draft=r.draft,
            report=r.report,
            cycle=original,
        )
    else:
        paid = await waiting._normal_commercial_prerequisite(
            r.session,
            r.settings,
            project=r.project,
            chapter=r.chapter,
            draft=r.draft,
            owner=r.owner,
            scope=r.project.metadata_json["execution_scope_id"],
        )
    ledger = await waiting._ledger(
        r.session, r.project.metadata_json["execution_scope_id"], lock=False
    )
    alias = dict(
        schema=service._NATIVE_ALIAS,
        project_id=str(r.project.id),
        chapter_id=str(r.chapter.id),
        draft_id=str(r.draft.id),
        body_sha256=original["source_body_hash"],
        cycle_id=r.cid,
        review_workflow_run_id=str(r.owner.id),
        transition=transition,
        original_cycle=original,
        input_proof=paid,
        ledger_sha256=service._native_digest(ledger),
        control_command_serial=3,
        provider_calls=0,
        new_credit=False,
        dispatch_authorized=False,
        grade_recomputed=False,
        adoption_authorized=False,
    )
    alias["receipt_sha256"] = service._native_digest(alias)
    cycle = deepcopy(original)
    cycle["policy_source_hash"] = archive.PRIOR_POLICY
    cycle["native_runtime_policy_alias"] = alias
    if prepared:
        entry = cycle["attempts"][-1]
        # Exact old native resume plan shape. All source/paid/cohort/renderer
        # evidence above is actual; only this historical receipt is synthetic.
        binding = {**proof, "policy_source_hash": archive.PRIOR_POLICY}
        plan = dict(
            schema=waiting.POLICY,
            status="prepared_plan",
            binding={
                key: binding[key]
                for key in (
                    "project_id",
                    "chapter_id",
                    "draft_id",
                    "body_sha256",
                    "cycle_id",
                    "policy_source_hash",
                    "semantic_dependencies_sha256",
                )
            },
            review_workflow_run_id=str(r.owner.id),
            attempt=entry["attempt"],
            operation_namespace=entry["operation_namespace"],
            attempt_count=len(cycle["attempts"]),
            new_attempts=0,
            credit_released=False,
            provider_calls=0,
            dispatch_authorized=False,
            adoption_authorized=False,
            cycle_before=deepcopy(cycle),
            owner_before=waiting._row(r.owner),
            waiting_results=[
                {"report": waiting._row(r.report), "quality": waiting._row(r.quality)}
            ],
            ledger_before=ledger,
            prerequisite=paid,
            external_prerequisite_proof_sha256=None,
            requested_by="system",
            database_writes=0,
        )
        plan["plan_sha256"] = waiting._sha(plan)
        cycle["infrastructure_resumptions"] = [
            {**plan, "status": "rearmed", "state_after": "prepared"}
        ]
        cycle["state"] = entry["state"] = "prepared"
    await _store(r, cycle)
    service.validate_native_policy_alias(
        cycle,
        project_id=r.project.id,
        chapter_id=r.chapter.id,
        draft_id=r.draft.id,
        body_sha256=cycle["source_body_hash"],
    )
    return cycle


async def test_all_three_epochs_full_bytes_and_materialized_tail(frozen_three_result_api_context):
    old, prior, current = archive._verified_epochs()
    assert len(old["inventory"]) == len(prior["inventory"]) == len(current["inventory"]) == 263
    assert archive._aggregate(old["inventory"]) == archive.OLD_POLICY
    assert archive._aggregate(prior["inventory"]) == archive.PRIOR_POLICY
    assert current["policy_hash"] == pipelines.chapter_review_recovery_source_hash()
    assert (
        hashlib.sha256((archive.HERE / "original.json").read_bytes()).hexdigest()
        == archive.ORIGINAL_SHA256
    )
    assert (
        hashlib.sha256((archive.HERE / "current.json").read_bytes()).hexdigest()
        == archive.CURRENT_SHA256
    )
    for name, sha in {**prior["inventory"], **prior["additional_sources"]}.items():
        assert hashlib.sha256(archive.prior_bytes(name)).hexdigest() == sha
    for epoch in (archive.OLD_POLICY, archive.PRIOR_POLICY):
        proof = archive.verified_transition_from(epoch)
        assert (
            proof["previous_policy_hash"] == epoch
            and proof["current_policy_hash"] == current["policy_hash"]
        )
        assert (
            proof["provider_calls"] == 0
            and proof["new_credit"] is proof["dispatch_authorized"] is False
        )


@pytest.mark.parametrize("epoch", [archive.OLD_POLICY, archive.PRIOR_POLICY])
async def test_native_available_paid_result_upgrades_both_known_epochs(completed, epoch):
    r = completed
    cycle = _cycle(r)
    cycle["policy_source_hash"] = epoch
    await _store(r, cycle)
    before = await snapshot(r)
    result = await service.prepare_native_review_resume(
        r.session, r.settings, project_id=r.project.id
    )
    alias = result["chapter_receipts"][0]["policy_alias"]
    assert alias["transition"]["previous_policy_hash"] == epoch
    assert len(alias["input_proof"]["paid_rows"]) == 2
    assert _cycle(r)["attempts"] == cycle["attempts"]
    assert (
        await preflight.validate_retained_review_before_processing(r.session, r.settings, r.chapter)
    )["reuse_kind"] == "available_review"
    after = await snapshot(r)
    for table, rows in before.items():
        if table != "chapters":
            assert after[table] == rows


async def test_previously_issued_available_alias_is_preserved_inside_upgrade(completed):
    r = completed
    prior = await _prior_alias(r)
    before = await snapshot(r)
    result = await service.prepare_native_review_resume(
        r.session, r.settings, project_id=r.project.id
    )
    alias = result["chapter_receipts"][0]["policy_alias"]
    assert alias["original_cycle"] == prior
    assert (
        alias["original_cycle"]["native_runtime_policy_alias"]
        == prior["native_runtime_policy_alias"]
    )
    assert _cycle(r)["attempts"] == prior["attempts"]
    assert (
        await preflight.validate_retained_review_before_processing(r.session, r.settings, r.chapter)
    )["reuse_kind"] == "available_review"
    consumed = await pipelines._review_chapter_with_evidence_retry(
        r.session,
        r.settings,
        r.project.slug,
        r.chapter.chapter_number,
        workflow=r.owner,
        draft=r.draft,
    )
    assert consumed[1].id == r.report.id and consumed[2].id == r.quality.id
    after = await snapshot(r)
    for table, rows in before.items():
        if table != "chapters":
            assert after[table] == rows


async def test_adopted_prior_alias_history_unchanged(completed):
    r = completed
    prior = await _prior_alias(r)
    r.draft.promotion_state = "promoted"
    await r.session.commit()
    before = await snapshot(r)
    result = await service.prepare_native_review_resume(
        r.session, r.settings, project_id=r.project.id
    )
    assert result["status"] == "unchanged" and await snapshot(r) == before
    assert _cycle(r) == prior
    service.validate_native_policy_alias(
        prior,
        project_id=r.project.id,
        chapter_id=r.chapter.id,
        draft_id=r.draft.id,
        body_sha256=prior["source_body_hash"],
    )


@pytest.mark.parametrize("epoch", [archive.OLD_POLICY, archive.PRIOR_POLICY])
async def test_native_waiting_same_slot_same_attempt_no_budget_reset(native_wait, epoch):
    r = native_wait
    cycle = _cycle(r)
    if epoch == archive.PRIOR_POLICY:
        # Generate the native old-domain immutable waiting receipt in this
        # epoch, rather than relabeling the 1fd receipt's binding.
        from bestseller.services.chapter_review_prerequisite import prerequisite_wait_receipt

        cycle["policy_source_hash"] = epoch
        entry = cycle["attempts"][0]
        binding = wait_binding(
            cycle,
            entry,
            project_id=r.project.id,
            chapter_id=r.chapter.id,
            body_fingerprint=pipelines.chapter_review_body_fingerprint(r.draft.content_md),
        )
        entry["infrastructure_history"][0]["waiting_receipt"] = prerequisite_wait_receipt(
            r.report, binding=binding
        )
        await _store(r, cycle)
    before = await snapshot(r)
    result = await service.prepare_native_review_resume(
        r.session, r.settings, project_id=r.project.id
    )
    leaf = result["chapter_receipts"][0]
    assert leaf["waiting_rearm"]["status"] == "rearmed"
    assert leaf["waiting_rearm"]["prerequisite"]["history_replays"] == 2
    current = _cycle(r)
    assert (
        current["attempts"][0]["operation_namespace"] == cycle["attempts"][0]["operation_namespace"]
    )
    assert (
        current["attempts"][0]["infrastructure_history"]
        == cycle["attempts"][0]["infrastructure_history"]
    )
    assert current["state"] == "prepared" and len(current["attempts"]) == 1
    after = await snapshot(r)
    for table, rows in before.items():
        if table != "chapters":
            assert after[table] == rows


async def test_old_alias_waiting_binds_deepest_original_and_native_rearms(native_wait):
    r = native_wait
    prior = await _prior_alias(r)
    await service.prepare_native_review_resume(r.session, r.settings, project_id=r.project.id)
    current = _cycle(r)
    entry = current["attempts"][0]
    record = entry["infrastructure_history"][0]
    binding = wait_binding(
        current,
        entry,
        project_id=r.project.id,
        chapter_id=r.chapter.id,
        body_fingerprint=pipelines.chapter_review_body_fingerprint(r.draft.content_md),
    )
    assert (
        service.native_waiting_record_binding(current, entry, record, binding)["policy_source_hash"]
        == archive.OLD_POLICY
    )
    assert current["native_runtime_policy_alias"]["original_cycle"] == prior
    assert (
        current["state"] == "prepared"
        and current["attempts"][0]["infrastructure_history"]
        == prior["attempts"][0]["infrastructure_history"]
    )


async def test_previously_rearmed_prepared_alias_upgrades_append_only(native_wait):
    r = native_wait
    prior = await _prior_alias(r, prepared=True)
    before = await snapshot(r)
    result = await service.prepare_native_review_resume(
        r.session, r.settings, project_id=r.project.id
    )
    leaf = result["chapter_receipts"][0]
    assert leaf["waiting_rearm"] is None
    current = _cycle(r)
    assert current["native_runtime_policy_alias"]["original_cycle"] == prior
    assert current["attempts"] == prior["attempts"]
    assert current["infrastructure_resumptions"] == prior["infrastructure_resumptions"]
    assert leaf["policy_alias"]["input_proof"]["history_replays"] == 2
    assert (
        await preflight.validate_retained_review_before_processing(r.session, r.settings, r.chapter)
    )["reuse_kind"] == "resume_pending_review"
    after = await snapshot(r)
    for table, rows in before.items():
        if table != "chapters":
            assert after[table] == rows


async def test_later_waiting_old_resumption_explicitly_denies_without_mutation(native_wait):
    r = native_wait
    cycle = await _prior_alias(r, prepared=True)
    cycle["state"] = cycle["attempts"][-1]["state"] = WAITING
    await _store(r, cycle)
    before = await snapshot(r)
    with pytest.raises(ValueError, match="prior_resumption_waiting_requires_separate_recovery"):
        await service.prepare_native_review_resume(r.session, r.settings, project_id=r.project.id)
    assert await snapshot(r) == before


@pytest.mark.parametrize(
    "fault", ["unknown", "body", "history", "alias_transition", "alias_flag", "resume_digest"]
)
async def test_unknown_or_mutated_history_never_upgrades(native_wait, fault):
    r = native_wait
    cycle = await _prior_alias(r, prepared=fault == "resume_digest")
    if fault == "unknown":
        cycle["policy_source_hash"] = "a" * 64
    elif fault == "body":
        cycle["source_body_hash"] = "b" * 64
    elif fault == "history":
        cycle["attempts"][0]["infrastructure_history"][0]["waiting_receipt"]["credit_released"] = (
            True
        )
    elif fault == "alias_transition":
        alias = cycle["native_runtime_policy_alias"]
        alias["transition"]["current_policy_hash"] = "b" * 64
        plain = deepcopy(alias)
        plain.pop("receipt_sha256")
        alias["receipt_sha256"] = service._native_digest(plain)
    elif fault == "alias_flag":
        alias = cycle["native_runtime_policy_alias"]
        alias["new_credit"] = True
        plain = deepcopy(alias)
        plain.pop("receipt_sha256")
        alias["receipt_sha256"] = service._native_digest(plain)
    else:
        cycle["infrastructure_resumptions"][0]["plan_sha256"] = "c" * 64
    await _store(r, cycle)
    before = await snapshot(r)
    with pytest.raises(ValueError):
        await service.prepare_native_review_resume(r.session, r.settings, project_id=r.project.id)
    assert await snapshot(r) == before


@pytest.mark.parametrize(
    "target",
    [
        "runtime",
        "prior_bytes",
        "prior_manifest",
        "original_bytes",
        "original_manifest",
        "final_manifest",
    ],
)
async def test_every_epoch_full_byte_drift_fails_closed(monkeypatch, target):
    mapping = {
        "runtime": archive.ROOT / "src/bestseller/services/feedback.py",
        "prior_bytes": archive.HERE
        / "prior-bytes/src/bestseller/services/chapter_review_reconciliation.py.source",
        "prior_manifest": archive.HERE / "current.json",
        "original_bytes": archive.HERE / "bytes/src/bestseller/services/pipelines.py.source",
        "original_manifest": archive.HERE / "original.json",
        "final_manifest": archive.HERE / "final.json",
    }
    path = mapping[target]
    read = Path.read_bytes
    monkeypatch.setattr(
        Path, "read_bytes", lambda p: read(p) + b"\n# drift" if p == path else read(p)
    )
    with pytest.raises(ValueError):
        archive.verified_transition()


async def test_unknown_epoch_selector_and_unknown_source_are_denied():
    with pytest.raises(ValueError, match="unsupported_previous_policy"):
        archive.verified_transition_from("f" * 64)
    with pytest.raises(ValueError, match="unknown_prior_source"):
        archive.prior_bytes("../untrusted.py")


async def test_prior_alias_upgrade_rollback_restores_every_row(completed):
    r = completed
    await _prior_alias(r)
    before = await snapshot(r)
    await service.prepare_native_review_resume(r.session, r.settings, project_id=r.project.id)
    await r.session.rollback()
    assert await snapshot(r) == before


async def test_changed_physical_body_never_reuses_old_alias(completed):
    r = completed
    cycle = await _prior_alias(r)
    # Keep the native active cycle pointer anchored to the new physical body,
    # while its actual old cycle/report still belongs to the old physical body.
    r.draft.content_md += " A different physical ending."
    meta = deepcopy(r.chapter.metadata_json)
    meta["chapter_review_active_cycle"] = {
        "cycle_id": r.cid,
        "source_draft_id": str(r.draft.id),
        "source_body_hash": pipelines.chapter_review_body_fingerprint(r.draft.content_md)["digest"],
    }
    r.chapter.metadata_json = meta
    await r.session.commit()
    before = await snapshot(r)
    with pytest.raises(ValueError, match="current_cycle_source"):
        await service.prepare_native_review_resume(r.session, r.settings, project_id=r.project.id)
    assert await snapshot(r) == before
    with pytest.raises(ValueError, match="alias_binding"):
        service.validate_native_policy_alias(
            cycle,
            project_id=r.project.id,
            chapter_id=r.chapter.id,
            draft_id=r.draft.id,
            body_sha256=pipelines.chapter_review_body_fingerprint(r.draft.content_md)["digest"],
        )


async def test_prepared_without_a_real_prior_once_alias_is_unsupported(native_wait):
    r = native_wait
    cycle = _cycle(r)
    cycle["policy_source_hash"] = archive.PRIOR_POLICY
    cycle["state"] = cycle["attempts"][0]["state"] = "prepared"
    await _store(r, cycle)
    before = await snapshot(r)
    with pytest.raises(ValueError, match="unsupported_pending_alias"):
        await service.prepare_native_review_resume(r.session, r.settings, project_id=r.project.id)
    assert await snapshot(r) == before
