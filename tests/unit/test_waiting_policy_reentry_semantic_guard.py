"""Persisted waiting-policy proof must fence public prepared/running reentry."""

from copy import deepcopy
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from test_chapter_review_first_reuse_proof import snapshot
from test_chapter_waiting_recovery_policy_guard import ledger
from test_waiting_review_policy_source import db as db
from test_waiting_review_policy_source import semantic_db as semantic_db
from test_waiting_review_policy_source import waiting as waiting

from bestseller.services import pipelines
from bestseller.services.budget import bind_execution_scope
from bestseller.services.chapter_review_preflight import _waiting_digest

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


class ReachedDispatch(BaseException):
    """Stop at the existing pipeline boundary, before any provider."""


async def resume(rig):
    with bind_execution_scope(rig.scope):
        return await pipelines.resume_chapter_review_after_policy_change(
            rig.session,
            rig.settings,
            rig.project.slug,
            rig.chapter.chapter_number,
            previous_workflow_run_id=rig.owner.id,
            expected_draft_id=rig.draft.id,
            expected_body_hash=rig.fingerprint["digest"],
            policy_revision="Same explicit policy repair",
            policy_source_hash=rig.policy,
            reason="Continue the retained waiting origin without new credit",
        )


@pytest.fixture(params=["prepared", "running"])
async def persisted(waiting, monkeypatch, request):
    rig = waiting
    rig.policy = pipelines.chapter_review_recovery_source_hash()
    original_cycles = deepcopy(rig.chapter.metadata_json["chapter_critic_cycles"])
    original_paid = await ledger(rig.session)
    # Both checkpoints are written by the actual public entry. For prepared,
    # interrupt after its commit; for running, let the real wrapper commit too.
    with monkeypatch.context() as checkpoint:
        boundary = (
            "_run_prepared_chapter_review_cycle"
            if request.param == "prepared"
            else "run_chapter_pipeline"
        )
        checkpoint.setattr(pipelines, boundary, AsyncMock(side_effect=ReachedDispatch))
        with pytest.raises(ReachedDispatch):
            await resume(rig)
    await rig.session.refresh(rig.chapter)
    await rig.session.refresh(rig.owner)
    rig.cycle = deepcopy(rig.chapter.metadata_json["chapter_review_recovery_cycles"][-1])
    rig.origin = deepcopy(rig.cycle["waiting_origin_proof"])
    assert rig.cycle["state"] == request.param
    assert rig.owner.current_step == "chapter_adoption_required"
    assert rig.origin["schema"] == "waiting-review-policy-source.v1"
    assert rig.origin["proof_sha256"] == _waiting_digest(
        {k: v for k, v in rig.origin.items() if k != "proof_sha256"}
    )
    assert rig.chapter.metadata_json["chapter_critic_cycles"] == original_cycles
    assert await ledger(rig.session) == original_paid
    assert rig.cycle["attempts_base"] == 5
    return rig


async def assert_refused(rig, monkeypatch, error):
    prepared = AsyncMock(side_effect=ReachedDispatch)
    pipeline = AsyncMock(side_effect=ReachedDispatch)
    monkeypatch.setattr(pipelines, "_run_prepared_chapter_review_cycle", prepared)
    monkeypatch.setattr(pipelines, "run_chapter_pipeline", pipeline)
    before, paid = await snapshot(rig.session), await ledger(rig.session)
    for _ in range(2):
        with pytest.raises(ValueError, match=error):
            await resume(rig)
        assert await snapshot(rig.session) == before
        assert await ledger(rig.session) == paid
        assert not rig.session.new and not rig.session.dirty and not rig.session.deleted
    prepared.assert_not_awaited()
    pipeline.assert_not_awaited()
    assert rig.report.verdict == "unknown"
    assert rig.old_owner.status == "failed"
    assert rig.draft.content_md == rig.body


@pytest.mark.parametrize(
    "change",
    ["plan", "prior", "config", "scene", "latch", "recovery_latch", "bound_latch"],
)
async def test_changed_actual_dependencies_refuse_before_prepared_or_paid(
    persisted,
    monkeypatch,
    change,
):
    rig = persisted
    if change == "plan":
        rig.design.content = {"rules": ["A different approved plan"]}
    elif change == "prior":
        rig.prior.content_md += " The adopted predecessor was changed."
    elif change == "config":
        rig.settings.quality.thresholds.scene_min_score += 0.01
    elif change == "scene":
        rig.scene.exit_state = {"door": "closed again"}
    else:
        cm = deepcopy(rig.chapter.metadata_json)
        if change == "latch":
            cm["chapter_critic_cycles"][rig.key]["semantic_dependency_change_detected"] = True
        elif change == "recovery_latch":
            cm["chapter_review_recovery_cycles"][-1]["semantic_dependency_change_detected"] = True
        elif change == "bound_latch":
            cm["chapter_critic_cycles"][rig.cycle["cycle_id"]] = {
                "semantic_dependency_change_detected": True,
            }
        rig.chapter.metadata_json = cm
    await rig.session.commit()
    await assert_refused(rig, monkeypatch, "semantic_dependencies_changed|adopted")


@pytest.mark.parametrize(
    "field",
    [
        "schema",
        "status",
        "project_id",
        "chapter_id",
        "draft_id",
        "previous_workflow_run_id",
        "execution_scope_id",
        "source_body_fingerprint",
        "current_policy_source_hash",
        "previous_policy_source_hash",
        "source_cycle_id",
        "source_cycle_sha256",
        "latest_report_id",
        "latest_quality_score_id",
        "previous_status",
        "previous_step",
        "origin_only",
        "dispatch_allowed",
        "attempt_reset_allowed",
        "new_credit_allowed",
        "grade_reuse_allowed",
        "adoption_allowed",
        "proof_sha256",
        "missing",
        "null",
    ],
)
async def test_persisted_proof_integrity_and_binding_are_required(persisted, monkeypatch, field):
    rig = persisted
    cm = deepcopy(rig.chapter.metadata_json)
    recovery = cm["chapter_review_recovery_cycles"][-1]
    proof = recovery["waiting_origin_proof"]
    if field == "missing":
        recovery.pop("waiting_origin_proof")
    elif field == "null":
        recovery["waiting_origin_proof"] = None
    elif field == "proof_sha256":
        proof[field] = "0" * 64
    else:
        value = proof[field]
        proof[field] = (
            not value
            if type(value) is bool
            else {**value, "digest": "0" * 64}
            if isinstance(value, dict)
            else str(uuid4())
        )
        # Re-sign to distinguish binding validation from digest validation.
        proof["proof_sha256"] = _waiting_digest(
            {k: v for k, v in proof.items() if k != "proof_sha256"}
        )
    cm["chapter_review_active_cycle"] = deepcopy(recovery)
    rig.chapter.metadata_json = cm
    # Keep the producer's second stored copy in sync. Re-signing both copies
    # must not turn invalid source/binding fields into dispatch permission.
    wm = deepcopy(rig.owner.metadata_json)
    wm["review_recovery_policy"] = deepcopy(recovery)
    rig.owner.metadata_json = wm
    await rig.session.commit()
    await assert_refused(rig, monkeypatch, "chapter_review_recovery_waiting_origin")


@pytest.mark.parametrize(
    "change", ["semantic", "source_cycle", "project_scope", "owner_scope", "body_binding"]
)
async def test_reentry_cannot_rebind_persisted_origin(persisted, monkeypatch, change):
    rig = persisted
    cm, wm = deepcopy(rig.chapter.metadata_json), deepcopy(rig.owner.metadata_json)
    recovery = cm["chapter_review_recovery_cycles"][-1]
    if change == "semantic":
        proof = recovery["waiting_origin_proof"]
        proof["semantic_dependencies_sha256"] = "0" * 64
        proof["proof_sha256"] = _waiting_digest(
            {k: v for k, v in proof.items() if k != "proof_sha256"}
        )
        wm["review_recovery_policy"] = deepcopy(recovery)
    elif change == "source_cycle":
        cm["chapter_critic_cycles"][rig.key]["attempts"][0]["operation_namespace"] += ":changed"
    elif change == "project_scope":
        rig.project.metadata_json = {
            **rig.project.metadata_json,
            "execution_scope_id": "other-scope",
        }
    elif change == "owner_scope":
        wm["execution_scope_id"] = "other-scope"
    elif change == "body_binding":
        # The old matching lookup explicitly permits None for legacy entries.
        recovery["source_body_hash"] = None
    rig.chapter.metadata_json, rig.owner.metadata_json = cm, wm
    await rig.session.commit()
    await assert_refused(rig, monkeypatch, "waiting_origin|semantic_dependencies_changed")


async def test_unchanged_persisted_origin_reenters_existing_route_without_reset(
    persisted, monkeypatch
):
    rig = persisted
    before, paid = await snapshot(rig.session), await ledger(rig.session)
    pipeline = AsyncMock(side_effect=ReachedDispatch)
    monkeypatch.setattr(pipelines, "run_chapter_pipeline", pipeline)
    with pytest.raises(ReachedDispatch):
        await resume(rig)
    pipeline.assert_awaited_once()
    kwargs = pipeline.call_args.kwargs
    assert kwargs["critic_cycle_id"] == rig.cycle["cycle_id"]
    assert kwargs["review_source_binding"] == {
        "draft_id": str(rig.draft.id),
        "body_hash": rig.fingerprint["digest"],
    }
    assert kwargs["resume_existing_draft_only"] is True
    await rig.session.refresh(rig.chapter)
    after = await snapshot(rig.session)
    # Only the existing recovery checkpoint may advance prepared -> running.
    cm = deepcopy(after["chapters"][str(rig.chapter.id)]["metadata_json"])
    recovery = cm["chapter_review_recovery_cycles"][-1]
    assert recovery["state"] == "running"
    assert recovery["waiting_origin_proof"] == rig.origin
    recovery["state"] = rig.cycle["state"]
    cm["chapter_review_active_cycle"]["state"] = rig.cycle["state"]
    after["chapters"][str(rig.chapter.id)]["metadata_json"] = cm
    # ORM timestamp tracks the permitted checkpoint transition.
    after["chapters"][str(rig.chapter.id)]["updated_at"] = before["chapters"][str(rig.chapter.id)][
        "updated_at"
    ]
    assert after == before
    assert await ledger(rig.session) == paid
