"""Public policy-recovery boundary with actual SQLite automatic waiting history."""
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import inspect, select
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy.schema import CreateTable
from test_chapter_review_first_reuse_proof import proof_case, snapshot  # noqa: F401
from test_chapter_review_prerequisite_waiting import produce, waiting_case  # noqa: F401
from test_chapter_review_reconciliation import migration  # noqa: F401
from test_waiting_review_policy_source import db as db
from test_waiting_review_policy_source import semantic_db as semantic_db
from test_waiting_review_policy_source import waiting as waiting

from bestseller.infra.db.models import (
    ExecutionAttemptModel, ExecutionBudgetModel, ExecutionOperationModel, LlmRunModel,
)
from bestseller.services import pipelines
from bestseller.services.budget import bind_execution_scope
from bestseller.services.chapter_review_prerequisite import WAITING

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]
SCOPE = "test:waiting-recovery-policy-guard"


@pytest.fixture
async def automatic_wait(waiting_case):  # noqa: F811
    rig = waiting_case
    conn = await rig.session.connection()
    for model in (ExecutionBudgetModel, ExecutionOperationModel, ExecutionAttemptModel, LlmRunModel):
        ddl = str(CreateTable(model.__table__).compile(dialect=conn.dialect))
        await conn.exec_driver_sql(ddl.replace("::jsonb", ""))
    # Set up the original automatic entry before invoking the real producer.
    # The inherited fixture also contains an unrelated older-body cycle.
    rig.cid = pipelines._chapter_critic_cycle_id(
        rig.project.id, rig.chapter.id,
        pipelines.chapter_review_body_fingerprint(rig.source.content_md)["digest"],
    )
    metadata = deepcopy(rig.chapter.metadata_json)
    metadata["chapter_critic_cycles"].pop(rig.cid)
    metadata["chapter_review_recovery_cycles"] = []
    metadata["chapter_review_active_cycle"] = {
        "cycle_id": rig.cid, "state": "prepared",
        "source_draft_id": str(rig.source.id),
        "source_body_hash": pipelines.chapter_review_body_fingerprint(rig.source.content_md)["digest"],
        "policy_source_hash": "current-policy",
    }
    rig.chapter.metadata_json = metadata
    await rig.session.commit()
    await produce(rig)
    await pipelines._finish_unknown_chapter_review(
        rig.session, settings=rig.settings, workflow=rig.owner, chapter=rig.chapter,
        draft=rig.source, report=rig.wait_report, quality=rig.wait_quality,
    )
    # Historical charged accounting is deliberately opaque and retained, never
    # presented as new financial authorization or classified as a reusable call.
    budget = ExecutionBudgetModel(
        scope_id=SCOPE, max_calls=10, max_tokens=10000, calls_used=2,
        tokens_charged=700, status="active", deadline_at=datetime.now(UTC) + timedelta(days=1),
        metadata_json={"historical": "untouched"},
    )
    rig.session.add(budget)
    await rig.session.commit()
    assert rig.owner.status == "machine_blocked"
    assert rig.chapter.metadata_json["chapter_review_recovery_cycles"] == []
    assert rig.chapter.metadata_json["chapter_critic_cycles"][rig.cid]["state"] == WAITING
    return rig


async def ledger(session):
    result = {}
    for model in (ExecutionBudgetModel, ExecutionOperationModel, ExecutionAttemptModel, LlmRunModel):
        result[model.__tablename__] = [
            {column.key: deepcopy(getattr(row, column.key)) for column in inspect(model).column_attrs}
            for row in await session.scalars(select(model).execution_options(populate_existing=True))
        ]
    return result


async def recover(rig, policy):
    with bind_execution_scope(SCOPE):
        return await pipelines.resume_chapter_review_after_policy_change(
            rig.session, rig.settings, rig.project.slug, rig.chapter.chapter_number,
            previous_workflow_run_id=rig.owner.id, expected_draft_id=rig.source.id,
            expected_body_hash=pipelines.chapter_review_body_fingerprint(rig.source.content_md)["digest"],
            policy_revision="An operator label must not grant same-policy credit",
            policy_source_hash=policy, reason="Explicit infrastructure/policy repair fixture",
        )


async def test_same_body_same_policy_automatic_wait_rejects_before_dispatch_and_preserves_all_rows(
    automatic_wait, monkeypatch,
):
    rig = automatic_wait

    async def forbidden(*args, **kwargs):
        pytest.fail("waiting guard must reject before starting generation or any new review cycle")
    monkeypatch.setattr(pipelines, "run_chapter_pipeline", forbidden)
    monkeypatch.setattr(pipelines, "_run_prepared_chapter_review_cycle", forbidden)
    monkeypatch.setattr(pipelines, "review_chapter_draft", forbidden)
    before, paid = await snapshot(rig.session), await ledger(rig.session)
    for _ in range(2):
        with pytest.raises(ValueError, match="^chapter_review_recovery_waiting_requires_original_cycle$"):
            await recover(rig, "current-policy")
    assert await snapshot(rig.session) == before
    assert await ledger(rig.session) == paid
    assert rig.chapter.metadata_json["chapter_review_recovery_cycles"] == []
    assert not rig.session.new and not rig.session.dirty and not rig.session.deleted
    # A second real connection confirms this was not merely an in-memory refusal.
    factory = async_sessionmaker(rig.session.bind, expire_on_commit=False)
    async with factory() as observer:
        assert await snapshot(observer) == before
        assert await ledger(observer) == paid


async def test_genuinely_changed_policy_uses_normal_bound_recovery_without_rewriting_wait_history(
    waiting,
    monkeypatch,
):
    # This positive path needs the current explicit migration contract: real
    # semantic/config input, scope, RUN intent and completion authorization.
    # The legacy automatic_wait fixture remains a same-policy refusal fixture.
    rig = waiting
    old_cycles = deepcopy(rig.chapter.metadata_json["chapter_critic_cycles"])
    old_report = deepcopy(rig.report.structured_output)
    paid = await ledger(rig.session)
    policy = pipelines.chapter_review_recovery_source_hash()
    assert len(policy) == 64 and policy != old_cycles[rig.key]["policy_source_hash"]

    class ReachedNormalRoute(BaseException):
        pass

    # Stop at the existing generation boundary; the public service, source
    # validator and recovery wrapper all perform their actual native work.
    pipeline = AsyncMock(side_effect=ReachedNormalRoute)
    monkeypatch.setattr(pipelines, "run_chapter_pipeline", pipeline)
    with bind_execution_scope(rig.scope), pytest.raises(ReachedNormalRoute):
        await pipelines.resume_chapter_review_after_policy_change(
            rig.session,
            rig.settings,
            rig.project.slug,
            rig.chapter.chapter_number,
            previous_workflow_run_id=rig.owner.id,
            expected_draft_id=rig.draft.id,
            expected_body_hash=rig.fingerprint["digest"],
            policy_revision="Explicit bound source policy migration",
            policy_source_hash=policy,
            reason="Preserve original waiting source and settled ledger",
        )
    assert pipeline.await_count == 1
    call = pipeline.call_args.kwargs
    expected_id = pipelines._chapter_critic_cycle_id(
        rig.project.id,
        rig.chapter.id,
        rig.fingerprint["digest"],
        policy,
    )
    assert call["critic_cycle_id"] == expected_id != rig.key
    assert call["resume_existing_draft_only"] is True and call["chapter_first"] is True
    assert call["supersede_pending_rewrites"] is False
    assert call["review_source_binding"] == {
        "draft_id": str(rig.draft.id),
        "body_hash": old_cycles[rig.key]["source_body_hash"],
    }
    await rig.session.refresh(rig.chapter)
    await rig.session.refresh(rig.report)
    assert rig.chapter.metadata_json["chapter_critic_cycles"] == old_cycles
    assert rig.report.structured_output == old_report
    assert await ledger(rig.session) == paid
    history = rig.chapter.metadata_json["chapter_review_recovery_cycles"]
    assert len(history) == 1 and history[0]["cycle_id"] == expected_id
    assert history[0]["attempts_base"] == 5
    origin = history[0]["waiting_origin_proof"]
    assert origin["status"] == "verified_waiting_review_origin"
    assert origin["previous_workflow_run_id"] == str(rig.owner.id)
    assert origin["source_cycle_id"] == rig.key
    assert origin["latest_report_id"] == str(rig.report.id)
    assert origin["latest_quality_score_id"] == str(rig.quality.id)
    assert origin["previous_policy_source_hash"] == old_cycles[rig.key]["policy_source_hash"]
    assert origin["current_policy_source_hash"] == policy
    assert all(
        origin[key] is False
        for key in (
            "grade_reuse_allowed",
            "adoption_allowed",
            "attempt_reset_allowed",
            "new_credit_allowed",
            "dispatch_allowed",
        )
    )
