"""Observer eligibility only: real native proofs, fresh isolated SQLite readback.

Existing native fixtures stub their selected-foundation/paid boundary only; no
provider, worker, production database, or write producer is called by collector.
Writer helpers below are exclusively fixture setup, never the observed reader.
"""

from copy import deepcopy
import hashlib
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import event, inspect, select, update
from sqlalchemy.ext.asyncio import AsyncConnection, async_sessionmaker
from test_chapter_pending_review_policy_recovery import (
    apply as native_policy_apply,
)
from test_chapter_pending_review_policy_recovery import (  # noqa:F401
    db,
    pending,
    semantic_db,
    waiting,
)
from test_chapter_pending_review_policy_recovery import (
    validate as native_policy_validate,
)
from test_chapter_plan_observation_recovery import case as hype_case  # noqa:F401
from test_chapter_plan_observation_recovery import restore, rig

from bestseller.infra.db.models import ChapterDraftVersionModel, ChapterModel, ProjectModel
from bestseller.services import self_heal_repair_progress as service

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


def factory(r):
    bind = r.session.bind
    return async_sessionmaker(
        bind.engine if isinstance(bind, AsyncConnection) else bind, expire_on_commit=False
    )


async def collect(r):
    return await service.collect_repair_progress(factory(r), r.settings, r.project.id)


def resign(value, key):
    value.pop(key, None)
    value[key] = service._digest(value)
    return value


def compared(prior, snapshot):
    result = service.compare_repair_progress(prior, snapshot)
    assert all(result[k] is False for k in service.DENIALS)
    assert set(result["next_state"]) == {
        "schema",
        "project_id",
        "source_epoch",
        "seen_effect_keys",
        "seen_source_keys",
        "state_sha256",
    }
    return result


async def committed_hype(r):
    receipt = await restore(r)
    await r.session.commit()
    return receipt


async def test_committed_native_hype_is_one_observer_effect_and_no_credit(hype_case, monkeypatch):
    r = hype_case
    before = await collect(r)
    base = compared(None, before)
    assert base["baseline_only"] and not base["observer_progress_eligible"]
    receipt = await committed_hype(r)
    metadata = deepcopy(r.chapter.metadata_json)
    source = deepcopy(r.admission.metadata_json)
    draft = (r.draft.content_md, r.draft.promotion_state)
    from bestseller.services import chapter_plan_observation_recovery as writer

    async def forbidden(*a, **kw):
        raise AssertionError("writer/model/dispatch forbidden")

    monkeypatch.setattr(writer, "restore_source_owned_hype_fields", forbidden)
    monkeypatch.setattr(writer, "_restore_source_owned_hype_fields", forbidden)
    after = await collect(r)
    assert len(after["effects"]) == 1 and after["rejected"] == []
    effect = after["effects"][0]
    assert effect["receipt_sha256"] == receipt["receipt_sha256"]
    assert effect["native_after_proof"]["origin"] == metadata["outline_source_admission"]
    assert after["source_epoch"] == before["source_epoch"]
    result = compared(base["next_state"], after)
    assert result["observer_progress_eligible"] and not result["baseline_only"]
    assert result["new_effect_keys"] == [effect["effect_key"]]
    replay = compared(result["next_state"], await collect(r))
    assert not replay["observer_progress_eligible"]
    assert replay["next_state"] == result["next_state"]
    await r.session.refresh(r.chapter)
    await r.session.refresh(r.admission)
    await r.session.refresh(r.draft)
    assert r.chapter.metadata_json == metadata and r.admission.metadata_json == source
    assert (r.draft.content_md, r.draft.promotion_state) == draft


async def test_uncommitted_flush_and_rollback_are_not_committed_progress(hype_case):
    r = hype_case
    pid, cid = r.project.id, r.chapter.id
    baseline = await collect(r)
    await restore(r)  # staged/flush only
    uncommitted = await service.collect_repair_progress(factory(r), r.settings, pid)
    assert uncommitted["effects"] == []
    assert uncommitted["snapshot_sha256"] == baseline["snapshot_sha256"]
    await r.session.rollback()
    rolled_back = await service.collect_repair_progress(factory(r), r.settings, pid)
    assert rolled_back["effects"] == []
    async with factory(r)() as fresh:
        row = await fresh.get(ChapterModel, cid)
        assert row.hype_type == "golden_finger_reveal"
        assert "deterministic_audit_source_plan_field_recovery" not in row.metadata_json


async def test_aba_receipt_wrapper_and_epoch_changes_never_recount(hype_case):
    r = hype_case
    base = compared(None, await collect(r))["next_state"]
    receipt = await committed_hype(r)
    successful = await collect(r)
    counted = compared(base, successful)["next_state"]
    # Receipt actor/reason/digest and diagnostic metadata are not an effect ID.
    other = deepcopy(receipt)
    other.update(actor=str(uuid4()), reason="Another wrapper for exactly the same native effect")
    resign(other, "receipt_sha256")
    r.chapter.metadata_json = {
        **r.chapter.metadata_json,
        "heartbeat": "changed",
        "error": "another error",
        "deterministic_audit_source_plan_field_recovery": [other],
    }
    await r.session.commit()
    changed = await collect(r)
    assert changed["effects"][0]["effect_key"] == successful["effects"][0]["effect_key"]
    assert not compared(counted, changed)["observer_progress_eligible"]
    # Back to polluted A, then B, with the persisted native receipt still present.
    r.chapter.hype_type, r.chapter.hype_intensity = "golden_finger_reveal", 1.0
    await r.session.commit()
    a = await collect(r)
    assert not a["effects"] and a["known_effect_keys"] == successful["known_effect_keys"]
    seen_a = compared(counted, a)["next_state"]
    r.chapter.hype_type = r.chapter.hype_intensity = None
    await r.session.commit()
    assert not compared(seen_a, await collect(r))["observer_progress_eligible"]
    # Losing/corrupting the baseline at A seeds historical B as already seen.
    r.chapter.hype_type, r.chapter.hype_intensity = "golden_finger_reveal", 1.0
    await r.session.commit()
    a = await collect(r)
    for old in (None, {"schema": service.STATE_SCHEMA, "state_sha256": "0" * 64}):
        seeded = compared(old, a)
        assert seeded["baseline_only"] and not seeded["observer_progress_eligible"]
        assert successful["known_effect_keys"][0] in seeded["next_state"]["seen_effect_keys"]
        assert not compared(seeded["next_state"], successful)["observer_progress_eligible"]
    # Native execution-root switch establishes baseline; previous key history survives.
    r.project.metadata_json = {
        **r.project.metadata_json,
        "execution_scope_id": "new-explicit-execution-root",
    }
    await r.session.commit()
    new_epoch = await collect(r)
    result = compared(counted, new_epoch)
    assert result["baseline_only"] and not result["observer_progress_eligible"]
    assert set(counted["seen_effect_keys"]) <= set(result["next_state"]["seen_effect_keys"])


@pytest.mark.parametrize(
    "fault",
    [
        "body",
        "draft",
        "chapter",
        "project",
        "receipt_hash",
        "before_projection",
        "authority",
        "current_fields",
        "scene",
        "canonical",
        "foundation",
        "marker",
        "adopted",
        "no_native_receipt",
    ],
)
async def test_stale_or_incomplete_receipts_do_not_grant_progress(hype_case, fault):
    r = hype_case
    await committed_hype(r)
    receipt = deepcopy(r.chapter.metadata_json["deterministic_audit_source_plan_field_recovery"][0])
    if fault == "body":
        r.draft.content_md += " New actual prose."
    elif fault == "draft":
        receipt["draft_id"] = str(uuid4())
    elif fault == "chapter":
        receipt["chapter_id"] = str(uuid4())
    elif fault == "project":
        receipt["project_id"] = str(uuid4())
    elif fault == "before_projection":
        receipt["previous_projection_sha256"] = "0" * 64
    elif fault == "authority":
        receipt["adoption_authorized"] = True
    elif fault == "current_fields":
        r.chapter.hype_type = "different"
    elif fault == "scene":
        r.scene.exit_state = {"unapproved": "change"}
    elif fault == "canonical":
        r.canonical.status = "superseded"
    elif fault == "foundation":
        r.sources["world_spec"].content = {"different": "unapproved"}
    elif fault == "marker":
        r.chapter.metadata_json = {
            **r.chapter.metadata_json,
            "outline_source_admission": {
                **r.chapter.metadata_json["outline_source_admission"],
                "source_content_sha256": "0" * 64,
            },
        }
    elif fault == "adopted":
        r.draft.promotion_state = "promoted"
    if fault != "receipt_hash":
        resign(receipt, "receipt_sha256")
    else:
        receipt["receipt_sha256"] = "0" * 64
    r.chapter.metadata_json = {
        **r.chapter.metadata_json,
        "deterministic_audit_source_plan_field_recovery": []
        if fault == "no_native_receipt"
        else [receipt],
    }
    await r.session.commit()
    snapshot = await collect(r)
    assert not snapshot["effects"]
    assert not compared(None, snapshot)["observer_progress_eligible"]
    if fault != "no_native_receipt":
        assert snapshot["rejected"]


async def test_collector_emits_only_selects_no_locks_and_does_not_flush(hype_case):
    r = hype_case
    await committed_hype(r)
    seen = []

    def watch(conn, cursor, statement, params, context, many):
        if statement == "BEGIN":
            return
        st = getattr(getattr(context, "compiled", None), "statement", None)
        assert st is not None and st.is_select and getattr(st, "_for_update_arg", None) is None
        seen.append(statement)

    event.listen(r.session.bind.sync_engine, "before_cursor_execute", watch)
    try:
        assert (await collect(r))["effects"]
    finally:
        event.remove(r.session.bind.sync_engine, "before_cursor_execute", watch)
    assert seen


async def test_collector_blocks_accidental_writer_inside_native_reader(hype_case, monkeypatch):
    r = hype_case
    await committed_hype(r)

    async def accidental_writer(session, *args, **kwargs):
        await session.execute(
            update(ProjectModel).where(ProjectModel.id == r.project.id).values(title="FORBIDDEN")
        )

    monkeypatch.setattr(service, "require_current_outline_source_admission", accidental_writer)
    snapshot = await collect(r)
    assert not snapshot["effects"]
    assert any(x["reason"] == "select_only_violation" for x in snapshot["rejected"])
    async with factory(r)() as fresh:
        assert (
            await fresh.scalar(select(ProjectModel.title).where(ProjectModel.id == r.project.id))
            == r.project.title
        )


async def test_reused_dirty_or_active_session_is_rejected(hype_case):
    from contextlib import asynccontextmanager

    r = hype_case

    @asynccontextmanager
    async def reused():
        yield r.session

    with pytest.raises(ValueError, match="fresh_session_required"):
        await service.collect_repair_progress(reused, r.settings, r.project.id)


async def test_native_policy_rebind_commit_replay_same_slot_no_ledger_change(pending, monkeypatch):
    r = pending
    before = await collect(r)
    base = compared(None, before)["next_state"]
    proof = await native_policy_validate(r)
    receipt = await native_policy_apply(r, proof)
    await r.session.commit()
    for row in list(r.session.identity_map.values()):
        await r.session.refresh(row)
    frozen = {
        id(row): deepcopy({c.key: getattr(row, c.key) for c in inspect(type(row)).column_attrs})
        for row in r.session.identity_map.values()
    }
    from bestseller.services import chapter_pending_review_policy_recovery as policy

    async def forbidden(*a, **kw):
        raise AssertionError("write producer forbidden")

    monkeypatch.setattr(policy, "apply_pending_review_policy_recovery", forbidden)
    after = await collect(r)
    assert len(after["effects"]) == 1, after["rejected"]
    effect = after["effects"][0]
    assert (
        effect["family"] == service.POLICY and effect["receipt_sha256"] == receipt["receipt_sha256"]
    )
    assert effect["native_after_proof"]["ledger_sha256"] == proof["ledger_sha256"]
    assert effect["native_after_proof"]["budget_usage"] == proof["budget_usage"]
    result = compared(base, after)
    assert result["observer_progress_eligible"]
    assert not compared(result["next_state"], await collect(r))["observer_progress_eligible"]
    for row in r.session.identity_map.values():
        await r.session.refresh(row)
        assert {c.key: getattr(row, c.key) for c in inspect(type(row)).column_attrs} == frozen[
            id(row)
        ]


@pytest.mark.parametrize(
    "fault",
    ["after_records", "body", "owner", "ledger", "authority", "proof_digest", "current_policy"],
)
async def test_policy_native_afterproof_is_mandatory(pending, fault, monkeypatch):
    r = pending
    receipt = await native_policy_apply(r, await native_policy_validate(r))
    await r.session.commit()
    meta = deepcopy(r.chapter.metadata_json)
    cycle = meta["chapter_critic_cycles"][r.cycle_id]
    if fault == "after_records":
        meta["chapter_review_active_cycle"]["policy_source_hash"] = "1" * 64
    elif fault == "body":
        r.draft.content_md += " Different body."
    elif fault == "owner":
        r.owner.status = "completed"
    elif fault == "ledger":
        r.budget.calls_used += 1
    elif fault in {"authority", "proof_digest"}:
        receipt = deepcopy(receipt)
        if fault == "authority":
            receipt["new_attempt_credit"] = True
        else:
            receipt["proof"]["proof_sha256"] = "0" * 64
        resign(receipt, "receipt_sha256")
        cycle["policy_rebind_history"] = [receipt]
    elif fault == "current_policy":
        from bestseller.services import pipelines

        monkeypatch.setattr(pipelines, "chapter_review_recovery_source_hash", lambda: "2" * 64)
    r.chapter.metadata_json = meta
    await r.session.commit()
    snapshot = await collect(r)
    assert not snapshot["effects"]
    assert snapshot["rejected"]


async def test_stable_identity_excludes_actor_cycle_wrapper_and_draft_ids():
    pid, cid = str(uuid4()), str(uuid4())
    marker = dict(
        artifact_id=str(uuid4()),
        workflow_run_id=str(uuid4()),
        source_content_sha256="a" * 64,
        projection_hash="b" * 64,
    )
    clone = {**marker, "artifact_id": str(uuid4()), "workflow_run_id": str(uuid4())}
    assert service._hype_key(pid, cid, marker) == service._hype_key(pid, cid, clone)
    assert service._hype_source(cid, "c" * 64, "b" * 64, marker) == service._hype_source(
        cid, "c" * 64, "b" * 64, clone
    )
    proof = dict(cycle_id="a" * 64, source_body_fingerprint={"digest": "b" * 64})
    assert service._policy_key(pid, cid, proof) == service._policy_key(
        pid, cid, {**proof, "cycle_id": "c" * 64}
    )
    assert service._policy_source(
        cid, "a" * 64, "cycleA", "b" * 64, "c" * 64
    ) == service._policy_source(cid, "a" * 64, "cycleB", "b" * 64, "c" * 64)


async def test_epoch_is_execution_creation_root_not_body_policy_or_fingerprint():
    pid = uuid4()
    meta = {
        "execution_scope_id": "original-root",
        "creation_canonical_snapshot_id": str(uuid4()),
        "creation_canonical_snapshot_hash": "a" * 64,
        "creation_intent_snapshot_id": str(uuid4()),
        "creation_intent_contract_hash": "b" * 64,
    }
    original = service._epoch(SimpleNamespace(id=pid, metadata_json=meta))
    noisy = {
        **meta,
        "draft_id": str(uuid4()),
        "body_hash": "c" * 64,
        "policy_source_hash": "d" * 64,
        "book_design_snapshot_hash": "e" * 64,
        "heartbeat": {"error": "another"},
        "self_heal_attempts": 99,
    }
    assert service._epoch(SimpleNamespace(id=pid, metadata_json=noisy)) == original
    assert (
        service._epoch(
            SimpleNamespace(id=pid, metadata_json={**meta, "execution_scope_id": "another-root"})
        )
        != original
    )


async def test_new_session_bound_to_writers_uncommitted_connection_is_rejected(hype_case):
    r = hype_case
    connection = await r.session.connection()
    shared = async_sessionmaker(connection, expire_on_commit=False)
    with pytest.raises(ValueError, match="fresh_session_required"):
        await service.collect_repair_progress(shared, r.settings, r.project.id)


async def test_explicit_flush_or_commit_in_reader_is_blocked(hype_case, monkeypatch):
    r = hype_case
    await committed_hype(r)

    async def bad_reader(session, *a, **kw):
        row = await session.get(ProjectModel, r.project.id)
        row.title = "FORBIDDEN"
        await session.flush()

    monkeypatch.setattr(service, "require_current_outline_source_admission", bad_reader)
    with pytest.raises(ValueError, match="reader_mutated_session"):
        await collect(r)
    async with factory(r)() as fresh:
        assert (await fresh.get(ProjectModel, r.project.id)).title == r.project.title

    async def bad_commit(session, *a, **kw):
        await session.commit()

    monkeypatch.setattr(service, "require_current_outline_source_admission", bad_commit)
    snapshot = await collect(r)
    assert not snapshot["effects"]
    assert snapshot["rejected"][0]["reason"] == "observer_flush_forbidden"


async def test_policy_staged_and_rolled_back_never_count(pending):
    r = pending
    pid = r.project.id
    baseline = await collect(r)
    await native_policy_apply(r, await native_policy_validate(r))
    staged = await service.collect_repair_progress(factory(r), r.settings, pid)
    assert not staged["effects"]
    assert staged["snapshot_sha256"] == baseline["snapshot_sha256"]
    await r.session.rollback()
    rolled_back = await service.collect_repair_progress(factory(r), r.settings, pid)
    assert not rolled_back["effects"]
    assert rolled_back["snapshot_sha256"] == baseline["snapshot_sha256"]


async def test_policy_reads_only_selects_and_exact_origin(pending):
    r = pending
    proof = await native_policy_validate(r)
    await native_policy_apply(r, proof)
    await r.session.commit()
    seen = []

    def watch(conn, cursor, statement, params, context, many):
        st = getattr(getattr(context, "compiled", None), "statement", None)
        assert st is not None and st.is_select and getattr(st, "_for_update_arg", None) is None
        seen.append(statement)

    event.listen(r.session.bind.engine.sync_engine, "before_cursor_execute", watch)
    try:
        snapshot = await collect(r)
    finally:
        event.remove(r.session.bind.engine.sync_engine, "before_cursor_execute", watch)
    origin = snapshot["effects"][0]["native_after_proof"]["origin"]
    assert origin == {
        k: proof[k]
        for k in (
            "project_id",
            "chapter_id",
            "draft_id",
            "previous_workflow_run_id",
            "execution_scope_id",
            "source_body_fingerprint",
            "cycle_id",
            "operation_namespace",
            "attempt",
        )
    }
    assert seen


async def test_source_epoch_and_effect_history_return_to_original_root_is_baseline(hype_case):
    r = hype_case
    oldmeta = deepcopy(r.project.metadata_json)
    base = compared(None, await collect(r))["next_state"]
    await committed_hype(r)
    counted = compared(base, await collect(r))["next_state"]
    r.project.metadata_json = {**oldmeta, "execution_scope_id": "explicit-new-root"}
    await r.session.commit()
    switched = compared(counted, await collect(r))
    assert switched["baseline_only"] and not switched["observer_progress_eligible"]
    r.project.metadata_json = oldmeta
    await r.session.commit()
    returned = compared(switched["next_state"], await collect(r))
    assert returned["baseline_only"] and not returned["observer_progress_eligible"]
    assert set(counted["seen_effect_keys"]) <= set(returned["next_state"]["seen_effect_keys"])


async def test_seen_history_has_no_lru_and_no_watchdog_or_budget_fields(hype_case):
    snapshot = await collect(hype_case)
    # Pure comparator coverage of a long valid history; SQL provenance of the
    # starting snapshot is tested separately, these added keys only deny replay.
    keys = [hashlib.sha256(str(i).encode()).hexdigest() for i in range(1500)]
    snapshot["known_effect_keys"] = keys
    snapshot["historical_source_keys"] = list(reversed(keys))
    resign(snapshot, "snapshot_sha256")
    baseline = compared(None, snapshot)
    assert len(baseline["next_state"]["seen_effect_keys"]) == 1500
    actual = await collect(hype_case)
    next_result = compared(baseline["next_state"], actual)
    assert set(next_result["next_state"]["seen_effect_keys"]) == set(keys)
    assert set(keys) <= set(next_result["next_state"]["seen_source_keys"])
    assert not any(
        "counter" in k or "attempt" in k or "budget" in k or "grade" in k
        for k in next_result["next_state"]
    )


@pytest.mark.parametrize("fault", ["digest", "authority", "denial", "effect", "epoch"])
async def test_snapshot_tamper_fails_closed(hype_case, fault):
    r = hype_case
    await committed_hype(r)
    snapshot = await collect(r)
    if fault == "digest":
        snapshot["snapshot_sha256"] = "0" * 64
    elif fault == "authority":
        snapshot["authority"] = "approved"
    elif fault == "denial":
        snapshot["new_credit_allowed"] = True
    elif fault == "epoch":
        snapshot["source_epoch"] = "not-a-root"
    else:
        snapshot["effects"][0]["source_key"] = "0" * 64
    if fault != "digest":
        resign(snapshot, "snapshot_sha256")
    with pytest.raises(ValueError):
        service.compare_repair_progress(None, snapshot)


async def test_two_current_drafts_cannot_supply_successful_receipt(hype_case):
    r = hype_case
    await committed_hype(r)
    r.session.add(
        ChapterDraftVersionModel(
            project_id=r.project.id,
            chapter_id=r.chapter.id,
            version_no=2,
            is_current=True,
            content_md=r.draft.content_md,
            promotion_state="candidate",
        )
    )
    await r.session.commit()
    snapshot = await collect(r)
    assert not snapshot["effects"] and snapshot["rejected"][0]["reason"] == "hype_draft_body"


async def test_corrupt_baseline_at_other_hype_origin_still_seeds_old_receipt(hype_case):
    r = hype_case
    await committed_hype(r)
    original = deepcopy(r.chapter.metadata_json)
    legitimate = await collect(r)
    # Current source is no longer that receipt's source. Retained OLD records
    # are denial-only history and must survive baseline loss at this point.
    r.chapter.metadata_json = {
        **original,
        "outline_source_admission": {
            **original["outline_source_admission"],
            "artifact_id": str(uuid4()),
            "workflow_run_id": str(uuid4()),
            "source_content_sha256": "a" * 64,
            "projection_hash": "b" * 64,
        },
    }
    await r.session.commit()
    changed = await collect(r)
    assert not changed["effects"]
    assert changed["known_effect_keys"] == legitimate["known_effect_keys"]
    seeded = compared(None, changed)["next_state"]
    r.chapter.metadata_json = original
    await r.session.commit()
    restored = await collect(r)
    assert restored["effects"]
    assert not compared(seeded, restored)["observer_progress_eligible"]


async def test_inactive_policy_receipts_survive_baseline_loss(pending):
    r = pending
    await native_policy_apply(r, await native_policy_validate(r))
    await r.session.commit()
    original = deepcopy(r.chapter.metadata_json)
    legitimate = await collect(r)
    other = deepcopy(original)
    other_id = hashlib.sha256(b"another inactive-source test cycle").hexdigest()
    other["chapter_review_active_cycle"]["cycle_id"] = other_id
    other["chapter_critic_cycles"][other_id] = {
        "cycle_id": other_id,
        "policy_source_hash": "a" * 64,
        "semantic_dependencies_sha256": "b" * 64,
    }
    r.chapter.metadata_json = other
    await r.session.commit()
    changed = await collect(r)
    assert not changed["effects"]
    assert changed["known_effect_keys"] == legitimate["known_effect_keys"]
    seeded = compared(None, changed)["next_state"]
    r.chapter.metadata_json = original
    await r.session.commit()
    restored = await collect(r)
    assert restored["effects"]
    assert not compared(seeded, restored)["observer_progress_eligible"]


@pytest.mark.parametrize(
    "corrupt",
    [
        {"state_sha256": "a" * 64, "noise": float("nan")},
        {"state_sha256": "a" * 64, "schema": service.STATE_SCHEMA, "seen_effect_keys": None},
        {"state_sha256": "a" * 64, "schema": "another-schema"},
        None,
    ],
)
async def test_malformed_old_state_is_only_baseline_not_progress(hype_case, corrupt):
    r = hype_case
    await committed_hype(r)
    snapshot = await collect(r)
    original = deepcopy(snapshot)
    result = compared(corrupt, snapshot)
    assert result["baseline_only"] and not result["observer_progress_eligible"]
    assert snapshot == original
    assert snapshot["known_effect_keys"] == result["next_state"]["seen_effect_keys"]


@pytest.mark.parametrize(
    "fault", ["empty_proof", "adoption_flag", "receipt_hash", "fake_effect_identity"]
)
async def test_hype_pure_comparison_requires_bound_native_afterproof(hype_case, fault):
    r = hype_case
    baseline = compared(None, await collect(r))["next_state"]
    await committed_hype(r)
    snapshot = await collect(r)
    effect = snapshot["effects"][0]
    if fault == "empty_proof":
        effect["native_after_proof"] = {}
    elif fault == "adoption_flag":
        effect["native_after_proof"]["adoption_authorized"] = True
    elif fault == "receipt_hash":
        effect["receipt_sha256"] = "not-a-receipt"
    else:
        effect["effect_key"] = "a" * 64
        snapshot["known_effect_keys"] = ["a" * 64]
    resign(snapshot, "snapshot_sha256")
    with pytest.raises(ValueError):
        service.compare_repair_progress(baseline, snapshot)


@pytest.mark.parametrize("fault", ["empty_proof", "body_identity", "foreign_origin", "grade_flag"])
async def test_policy_pure_comparison_requires_bound_native_afterproof(pending, fault):
    r = pending
    baseline = compared(None, await collect(r))["next_state"]
    await native_policy_apply(r, await native_policy_validate(r))
    await r.session.commit()
    snapshot = await collect(r)
    proof = snapshot["effects"][0]["native_after_proof"]
    if fault == "empty_proof":
        snapshot["effects"][0]["native_after_proof"] = {}
    elif fault == "body_identity":
        proof["origin"]["source_body_fingerprint"]["digest"] = "a" * 64
    elif fault == "foreign_origin":
        proof["origin"]["project_id"] = str(uuid4())
    else:
        proof["grade_granted"] = True
    resign(snapshot, "snapshot_sha256")
    with pytest.raises(ValueError):
        service.compare_repair_progress(baseline, snapshot)
