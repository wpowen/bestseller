"""Real SQLite lifecycle; normal rendering/history tested separately; zero provider."""

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy.schema import CreateTable
from test_commercial_component_cohort import cohort  # noqa:F401,F811
from test_chapter_review_reconciliation import migration  # noqa:F401
from test_chapter_review_first_reuse_proof import proof_case, snapshot  # noqa:F401
from test_chapter_review_prerequisite_waiting import waiting_case, produce  # noqa:F401

from bestseller.domain.review import ChapterReviewResult
from bestseller.infra.db.models import (
    ChapterModel,
    ExecutionAttemptModel,
    ExecutionBudgetModel,
    ExecutionOperationModel,
    QualityScoreModel,
    ReviewReportModel,
)
from bestseller.services import pipelines
from bestseller.services.chapter_review_prerequisite import WAITING
from bestseller.services.retry_ledger import project_execution_scope
from bestseller.services import waiting_chapter_review_resume as recovery

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@pytest.fixture
async def ready(waiting_case, monkeypatch):  # noqa:F811
    r = waiting_case
    await produce(r)
    r.owner.status = "machine_blocked"
    r.owner.current_step = WAITING
    await r.session.commit()
    engine = r.session.bind
    async with engine.begin() as connection:
        for model in (ExecutionBudgetModel, ExecutionOperationModel, ExecutionAttemptModel):
            ddl = str(CreateTable(model.__table__).compile(dialect=connection.dialect))
            await connection.exec_driver_sql(ddl.replace("::jsonb", ""))
    r.scope = project_execution_scope(r.project)
    budget = ExecutionBudgetModel(
        scope_id=r.scope,
        max_calls=100,
        max_tokens=100000,
        calls_used=7,
        tokens_charged=12345,
        deadline_at=datetime.now(UTC) + timedelta(days=1),
        status="active",
        metadata_json={"authorized": "existing test scope"},
    )
    op = ExecutionOperationModel(
        key="old-root",
        scope_id=r.scope,
        input_hash="a" * 64,
        status="completed",
        owner="original",
        lease_until=datetime.now(UTC),
        result_json={"unmodified_paid_receipt": True},
    )
    attempt = ExecutionAttemptModel(
        id="old-attempt",
        operation_key="old-root",
        scope_id=r.scope,
        input_hash="a" * 64,
        status="completed",
        model_name="fixture-no-model",
        reserved_tokens=10,
        actual_tokens=7,
        created_at=datetime.now(UTC),
    )
    r.session.add_all([budget, op, attempt])
    await r.session.commit()
    r.budget, r.op, r.financial_attempt = budget, op, attempt
    actual = dict(
        schema="waiting-commercial-prerequisite.v1",
        draft_id=str(r.source.id),
        body_sha256=recovery.hashlib.sha256(r.source.content_md.encode()).hexdigest(),
        source_receipt={"bound": "synthetic lifecycle fixture"},
        evaluation_contract={"version": "fixture"},
        prepared_effective_input_hash="b" * 64,
    )
    r.actual = actual
    r.probe = {
        **deepcopy(actual),
        "status": "completed",
        "provider_calls": 0,
        "blocked_gateway_attempts": 0,
        "data_updates": 0,
        "slots_prepared": 0,
        "attempts_granted": 0,
        "history_replays": 2,
        "current_protocol_eligible": True,
        "protected_rows_and_ledger_unchanged": True,
    }
    r.normal = AsyncMock(return_value=deepcopy(actual))
    monkeypatch.setattr(recovery, "_normal_commercial_prerequisite", r.normal)

    async def forbidden(*args, **kwargs):
        raise AssertionError("prepare-only must never invoke gateway or judge")

    import bestseller.services.llm as llm
    import bestseller.services.chapter_llm_quality_judge as judge

    monkeypatch.setattr(llm, "complete_text", forbidden)
    monkeypatch.setattr(judge, "complete_text", forbidden)
    r.kw = dict(
        project_id=r.project.id,
        chapter_id=r.chapter.id,
        requested_by="authorized-sqlite-test",
        prerequisite_proof=r.probe,
    )
    return r


async def call(r, **kwargs):
    return await recovery.resume_waiting_chapter_review(r.session, r.settings, **{**r.kw, **kwargs})


async def financial(r):
    return await recovery._ledger(r.session, r.scope)


async def test_renderer_exact_ast_prefix_fence():
    assert len(recovery._renderer_fence()) == 64


async def test_inspect_zero_writes_then_same_slot_rearm_and_idempotence(ready):
    r = ready
    before = await snapshot(r.session)
    ledger = await financial(r)
    plan = await call(r)
    assert plan["status"] == "prepared_plan" and plan["provider_calls"] == plan["new_attempts"] == 0
    assert await snapshot(r.session) == before and await financial(r) == ledger
    assert not r.session.new and not r.session.dirty
    old_cycle = deepcopy(r.chapter.metadata_json["chapter_critic_cycles"][r.cid])
    old_meta = deepcopy(r.chapter.metadata_json)
    receipt = await call(r, execute=True, expected_plan_sha256=plan["plan_sha256"])
    assert receipt["status"] == "rearmed" and receipt["database_writes"] == 1
    # Releasing the helper's savepoint must NOT commit the caller transaction.
    async with async_sessionmaker(r.session.bind, expire_on_commit=False)() as observer:
        row = await observer.get(ChapterModel, r.chapter.id)
        assert row.metadata_json["chapter_critic_cycles"][r.cid]["state"] == WAITING
    await r.session.commit()
    cycle = r.chapter.metadata_json["chapter_critic_cycles"][r.cid]
    assert cycle["state"] == cycle["attempts"][0]["state"] == "prepared"
    assert cycle["review_workflow_run_id"] == str(r.owner.id)
    assert len(cycle["attempts"]) == 1
    assert cycle["attempts"][0]["operation_namespace"] == f"chapter-critic:{r.cid}:1"
    expected = deepcopy(old_cycle)
    expected["state"] = "prepared"
    expected["attempts"][0]["state"] = "prepared"
    expected["infrastructure_resumptions"] = [{**receipt, "database_writes": 0}]
    assert cycle == expected
    assert {k: v for k, v in r.chapter.metadata_json.items() if k != "chapter_critic_cycles"} == {
        k: v for k, v in old_meta.items() if k != "chapter_critic_cycles"
    }
    after = await snapshot(r.session)
    for table, rows in before.items():
        if table != "chapters":
            assert after[table] == rows
    assert await financial(r) == ledger
    replay = await call(r, execute=True, expected_plan_sha256=plan["plan_sha256"])
    assert replay["status"] == "already_rearmed" and replay["database_writes"] == 0
    assert await snapshot(r.session) == after and await financial(r) == ledger


async def test_rearmed_real_retry_wrapper_uses_original_owner_namespace_and_attempt(
    ready, monkeypatch
):
    r = ready
    plan = await call(r)
    await call(r, execute=True, expected_plan_sha256=plan["plan_sha256"])
    await r.session.commit()
    old_report = recovery._row(r.wait_report)
    old_quality = recovery._row(r.wait_quality)
    original_history = deepcopy(
        r.chapter.metadata_json["chapter_critic_cycles"][r.cid]["attempts"][0][
            "infrastructure_history"
        ]
    )
    seen = []

    async def reviewer(session, settings, slug, chapter_number, **kwargs):
        from bestseller.services.llm import _semantic_operation_namespace

        seen.append((kwargs["workflow_run_id"], _semantic_operation_namespace.get()))
        payload = deepcopy(r.wait_report.structured_output)
        payload.update(
            critic_evidence={"status": "verified", "passed": True},
            evidence_summary={},
            rewrite_instructions=None,
        )
        report = ReviewReportModel(
            project_id=r.project.id,
            target_type="chapter",
            target_id=r.chapter.id,
            reviewer_type="fixture-no-provider",
            verdict="pass",
            structured_output=payload,
        )
        session.add(report)
        await session.flush()
        columns = recovery._row(r.wait_quality)
        from sqlalchemy import inspect

        columns = {
            a.key: getattr(r.wait_quality, a.key)
            for a in inspect(QualityScoreModel).column_attrs
            if a.key not in {"id", "created_at", "review_report_id"}
        }
        quality = QualityScoreModel(**columns, review_report_id=report.id)
        session.add(quality)
        await session.flush()
        return (
            ChapterReviewResult(verdict="pass", scores=r.result.scores, severity_max="low"),
            report,
            quality,
            None,
        )

    monkeypatch.setattr(pipelines, "review_chapter_draft", reviewer)
    outcome = await produce(r)
    assert outcome[0].verdict == "pass"
    assert seen == [(r.owner.id, (f"chapter-critic:{r.cid}:1", 0))]
    cycle = r.chapter.metadata_json["chapter_critic_cycles"][r.cid]
    assert len(cycle["attempts"]) == 1 and cycle["state"] == "completed"
    assert cycle["attempts"][0]["infrastructure_history"] == original_history
    assert (
        recovery._row(r.wait_report) == old_report and recovery._row(r.wait_quality) == old_quality
    )
    again = await call(r, execute=True)
    assert again["status"] == "already_rearmed" and len(seen) == 1
    assert r.budget.calls_used == 7 and r.budget.tokens_charged == 12345


@pytest.mark.parametrize(
    "fault", ["namespace", "policy", "semantic", "history", "body", "owner", "active_writer"]
)
async def test_invalid_binding_blocks_before_prerequisite_or_model(ready, fault):
    r = ready
    meta = deepcopy(r.chapter.metadata_json)
    cycle = meta["chapter_critic_cycles"][r.cid]
    if fault == "namespace":
        cycle["attempts"][0]["operation_namespace"] = "fresh-credit"
    elif fault == "policy":
        cycle["policy_source_hash"] = "different"
    elif fault == "semantic":
        cycle["semantic_dependencies_sha256"] = "different"
    elif fault == "history":
        cycle["attempts"][0]["infrastructure_history"] = []
    elif fault == "body":
        r.source.content_md += " changed"
    elif fault == "owner":
        cycle["review_workflow_run_id"] = str(r.review_owner.id)  # completed owner, not waiting
    else:
        r.owner.status = "running"
    r.chapter.metadata_json = meta
    await r.session.commit()
    before = await snapshot(r.session)
    with pytest.raises(ValueError):
        await call(r, execute=True)
    assert r.normal.await_count == 0 and await snapshot(r.session) == before


@pytest.mark.parametrize(
    "kind,status",
    [("op", "running"), ("op", "unknown"), ("attempt", "reserved"), ("attempt", "unknown")],
)
async def test_unsettled_financial_rows_never_rearm(ready, kind, status):
    r = ready
    (r.op if kind == "op" else r.financial_attempt).status = status
    await r.session.commit()
    before = await snapshot(r.session)
    with pytest.raises(ValueError, match="unsettled"):
        await call(r)
    assert await snapshot(r.session) == before and r.normal.await_count == 0


@pytest.mark.parametrize(
    "fault",
    [
        "boolean",
        "cross_body",
        "reported_provider",
        "self_asserted_pass",
        "mismatch_plan",
        "producer_rejects",
    ],
)
async def test_external_receipt_not_authority_and_expected_plan_strict(ready, fault):
    r = ready
    kw = {}
    if fault == "boolean":
        kw["prerequisite_proof"] = True
    elif fault == "cross_body":
        r.probe["body_sha256"] = "f" * 64
    elif fault == "reported_provider":
        r.probe["provider_calls"] = 1
    elif fault == "self_asserted_pass":
        r.probe["protected_rows_and_ledger_unchanged"] = False
    elif fault == "mismatch_plan":
        kw.update(execute=True, expected_plan_sha256="f" * 64)
    else:
        r.normal.side_effect = ValueError("actual normal history still blocked")
    before = await snapshot(r.session)
    with pytest.raises(ValueError):
        await call(r, **kw)
    assert await snapshot(r.session) == before


async def test_caller_rollback_removes_rearm_but_preserves_waiting_and_finance(ready):
    r = ready
    plan = await call(r)
    before = await snapshot(r.session)
    ledger = await financial(r)
    await call(r, execute=True, expected_plan_sha256=plan["plan_sha256"])
    await r.session.rollback()
    assert await snapshot(r.session) == before
    assert await financial(r) == ledger


async def test_stored_resume_receipt_tampering_blocks_replay(ready):
    r = ready
    plan = await call(r)
    await call(r, execute=True, expected_plan_sha256=plan["plan_sha256"])
    await r.session.commit()
    meta = deepcopy(r.chapter.metadata_json)
    meta["chapter_critic_cycles"][r.cid]["infrastructure_resumptions"][0]["new_attempts"] = 1
    r.chapter.metadata_json = meta
    await r.session.commit()
    with pytest.raises(ValueError, match="integrity"):
        await call(r)


async def test_actual_normal_prefix_and_history_are_readonly_three_slot_proof(cohort, monkeypatch):  # noqa: F811
    """Only upstream context storage is substituted; renderer+history+route are real."""
    from types import SimpleNamespace
    from bestseller.infra.db.models import SceneCardModel
    from bestseller.services import context as context_service, commercial_review_context

    r = cohort
    async with r.session.bind.begin() as connection:
        ddl = str(CreateTable(SceneCardModel.__table__).compile(dialect=connection.dialect))
        await connection.exec_driver_sql(ddl.replace("::jsonb", ""))

    async def context(*args, **kwargs):
        return SimpleNamespace(story_bible={})

    async def history(*args, **kwargs):
        return {"recent_adopted_prose": []}

    monkeypatch.setattr(context_service, "build_chapter_writer_context", context)
    monkeypatch.setattr(commercial_review_context, "load_commercial_review_history", history)
    monkeypatch.setenv("JUDGE_SAMPLES", "3")

    async def forbidden(*args, **kwargs):
        raise AssertionError("history proof cannot prepare, claim or dispatch")

    monkeypatch.setattr(recovery.commercial, "prepare_commercial_component_slot", forbidden)
    monkeypatch.setattr(recovery.judge, "complete_text", forbidden)
    # SQLite reloads timezone-naive SQL values; compare persisted preimages.
    for row in (r.chapter, r.owner, r.budget):
        await r.session.refresh(row)
    before = recovery._row(r.chapter), recovery._row(r.owner), recovery._row(r.budget)
    result = await recovery._normal_commercial_prerequisite(
        r.session,
        r.settings,
        project=r.project,
        chapter=r.chapter,
        draft=r.draft,
        owner=r.owner,
        scope=r.budget.scope_id,
    )
    assert result["history_replays"] == 2 and result["current_protocol_eligible"] is True
    assert result["slots_prepared"] == result["attempts_granted"] == result["provider_calls"] == 0
    assert result["body_sha256"] == result["source_receipt"]["body_sha256"]
    assert len(result["prepared_effective_input_hash"]) == 64
    assert before == (recovery._row(r.chapter), recovery._row(r.owner), recovery._row(r.budget))
    assert not r.session.dirty and not r.session.new


async def test_actual_ast_capture_producer_drift_rejected(monkeypatch):
    original = recovery.inspect.getsource

    def altered(obj):
        source = original(obj)
        if obj is recovery.judge.judge_chapter_commercial_quality:
            return source.replace(
                "rule_ids = [", "unexpected_new_statement = True\n    rule_ids = [", 1
            )
        return source

    monkeypatch.setattr(recovery.inspect, "getsource", altered)
    with pytest.raises(ValueError, match="normal_commercial_renderer_changed"):
        recovery._renderer_fence()


async def test_normal_authorization_uuid_audit_is_json_safe():
    from uuid import uuid4

    identity = uuid4()
    assert recovery._sha({"row_id": identity}) == recovery._sha({"row_id": str(identity)})
    with pytest.raises(TypeError):
        recovery._sha({"unsupported": object()})


async def test_prerequisite_drift_between_locked_checks_rolls_back(ready):
    r = ready
    plan = await call(r)
    before = await snapshot(r.session)
    changed = {**r.actual, "prepared_effective_input_hash": "d" * 64}
    r.normal.side_effect = [deepcopy(r.actual), changed]
    with pytest.raises(ValueError, match="prerequisite_probe_binding_changed"):
        await call(r, execute=True, expected_plan_sha256=plan["plan_sha256"])
    assert await snapshot(r.session) == before


async def test_prepared_empty_cycle_cannot_get_rearm_receipt(ready):
    r = ready
    meta = deepcopy(r.chapter.metadata_json)
    c = meta["chapter_critic_cycles"][r.cid]
    c["state"] = "prepared"
    c["attempts"] = []
    r.chapter.metadata_json = meta
    await r.session.commit()
    with pytest.raises(ValueError, match="existing_attempt_required"):
        await call(r)
    assert not r.normal.await_count


async def test_inspect_real_feedback_no_flush_or_pg_row_lock(ready, monkeypatch):
    from sqlalchemy.dialects import postgresql
    from bestseller.services import feedback

    r = ready
    validate = feedback.validate_adopted_knowledge_source
    scalar = r.session.scalar
    scalars = r.session.scalars
    statements = []
    modes = []

    async def checked_scalar(statement, *args, **kwargs):
        sql = str(statement.compile(dialect=postgresql.dialect()))
        assert "FOR UPDATE" not in sql
        statements.append(sql)
        return await scalar(statement, *args, **kwargs)

    async def checked_scalars(statement, *args, **kwargs):
        sql = str(statement.compile(dialect=postgresql.dialect()))
        assert "FOR UPDATE" not in sql
        statements.append(sql)
        return await scalars(statement, *args, **kwargs)

    async def forbidden_flush(*args, **kwargs):
        raise AssertionError("readonly inspect cannot flush")

    async def checked_feedback(session, **kwargs):
        modes.append(kwargs["lock"])
        return await validate(session, **kwargs)

    monkeypatch.setattr(r.session, "scalar", checked_scalar)
    monkeypatch.setattr(r.session, "scalars", checked_scalars)
    monkeypatch.setattr(r.session, "flush", forbidden_flush)
    monkeypatch.setattr(feedback, "validate_adopted_knowledge_source", checked_feedback)
    plan = await call(r)
    assert plan["status"] == "prepared_plan" and modes == [False]
    assert any("chapter_draft_versions" in sql for sql in statements)
    assert not r.session.new and not r.session.dirty


async def test_execute_real_feedback_retains_lock_and_adoption_guard(ready, monkeypatch):
    from sqlalchemy.dialects import postgresql
    from bestseller.services import feedback

    r = ready
    validate = feedback.validate_adopted_knowledge_source
    scalar = r.session.scalar
    modes, locked = [], []

    async def checked_scalar(statement, *args, **kwargs):
        sql = str(statement.compile(dialect=postgresql.dialect()))
        if "FOR UPDATE" in sql:
            locked.append(sql)
        return await scalar(statement, *args, **kwargs)

    async def checked_feedback(session, **kwargs):
        modes.append(kwargs["lock"])
        return await validate(session, **kwargs)

    monkeypatch.setattr(r.session, "scalar", checked_scalar)
    monkeypatch.setattr(feedback, "validate_adopted_knowledge_source", checked_feedback)
    plan = await call(r)
    await call(r, execute=True, expected_plan_sha256=plan["plan_sha256"])
    assert modes == [False, True, True]
    assert any("chapter_draft_versions" in sql for sql in locked)
    await r.session.rollback()


async def test_inspect_real_feedback_rejects_actual_adopted_source(ready):
    r = ready
    r.source.promotion_state = "promoted"
    await r.session.commit()
    before = await snapshot(r.session)
    with pytest.raises(ValueError, match="source_already_adopted"):
        await call(r)
    assert r.normal.await_count == 0
    assert await snapshot(r.session) == before
