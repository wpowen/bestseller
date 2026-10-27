"""Real SQLite receipts; synthetic physical returns only, never a provider call."""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime, timedelta
import hashlib
import json
from types import SimpleNamespace
from uuid import uuid4
import os
import subprocess
import sys

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.schema import CreateTable
from test_adopted_knowledge_lineage import _jsonb  # noqa: F401
from test_commercial_judge_recovery import BODY, _review_completion, context, report

from bestseller.infra.db.models import (
    ChapterDraftVersionModel,
    ChapterModel,
    ExecutionAttemptModel,
    ExecutionBudgetModel,
    ExecutionOperationModel,
    LlmRunModel,
    ProjectModel,
    ReviewReportModel,
    WorkflowRunModel,
)
from bestseller.services import chapter_llm_quality_judge as judge
from bestseller.services import commercial_judge_recovery as recovery
from bestseller.services.budget import bind_execution_scope
from bestseller.services.llm import _effective_input_hash, _paid_call_origin
from bestseller.settings import load_settings

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]
SCOPE = "test:commercial-retained-cohort"


@pytest.fixture
async def cohort(tmp_path, monkeypatch):
    path = tmp_path / "commercial_component_test.sqlite3"
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    async with engine.begin() as connection:
        for model in (
            ProjectModel,
            ChapterModel,
            ChapterDraftVersionModel,
            WorkflowRunModel,
            ReviewReportModel,
            LlmRunModel,
            ExecutionBudgetModel,
            ExecutionOperationModel,
            ExecutionAttemptModel,
        ):
            ddl = str(CreateTable(model.__table__).compile(dialect=connection.dialect))
            await connection.exec_driver_sql(ddl.replace("::jsonb", ""))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        project = ProjectModel(
            slug="cohort-test",
            title="test",
            genre="general-fiction",
            target_chapters=3,
            target_word_count=8000,
            metadata_json={},
        )
        session.add(project)
        await session.flush()
        chapter = ChapterModel(
            project_id=project.id, chapter_number=2, chapter_goal="test", metadata_json={}
        )
        session.add(chapter)
        await session.flush()
        draft = ChapterDraftVersionModel(
            project_id=project.id,
            chapter_id=chapter.id,
            version_no=9,
            content_md=BODY,
            is_current=True,
            promotion_state="under_review",
        )
        owner = WorkflowRunModel(
            project_id=project.id,
            workflow_type="chapter_pipeline",
            scope_type="chapter",
            scope_id=chapter.id,
            status="running",
            metadata_json={},
        )
        budget = ExecutionBudgetModel(
            scope_id=SCOPE,
            max_calls=100,
            max_tokens=100000,
            calls_used=0,
            tokens_charged=0,
            deadline_at=datetime.now(UTC) + timedelta(days=1),
            status="active",
            metadata_json={},
        )
        session.add_all([draft, owner, budget])
        await session.commit()
        settings = load_settings(env={})
        settings.llm.critic.model = "minimax/offline-fixture"
        settings.llm.critic.model_override = None
        calls = []
        outcomes = {0: [True], 1: [False, True], 2: [False, False]}
        slot_calls = {0: 0, 1: 0, 2: 0}

        async def complete(sess, cfg, request):
            slot = int(str(request.sample_slot).rsplit(":", 1)[-1])
            index = slot_calls[slot]
            slot_calls[slot] += 1
            valid = outcomes[slot][min(index, len(outcomes[slot]) - 1)]
            payload = report(0.71 if slot == 0 else 0.75)
            if slot == 0:
                payload["issues"][0]["severity"] = "critical"
            if not valid:
                payload["issues"][0]["body_id"] = "Dforeign:P0001"
            raw = json.dumps(payload, ensure_ascii=False)
            run_id = uuid4()
            # Tool arguments, NOT nonempty auxiliary paid.content, are the report.
            value = _review_completion(
                content=raw,
                provider="minimax",
                model_name=cfg.llm.critic.model,
                finish_reason="tool_calls",
                llm_run_id=run_id,
                input_tokens=3,
                output_tokens=4,
            )
            value = value.model_copy(update={"content": "辅助文本，实际报告仅在工具 arguments。"})
            prompt_hash = _effective_input_hash(request, cfg.llm.critic)
            operation_key, attempt_id = uuid4().hex, uuid4().hex
            execution = {
                "scope_id": SCOPE,
                "operation_key": operation_key,
                "attempts": [{"attempt_id": attempt_id, "status": "completed"}],
            }
            metadata = {**request.metadata, "execution_receipt": execution}
            paid = value.model_copy(
                update={
                    "llm_run_id": None,
                    "metadata": {
                        "paid_call_origin": _paid_call_origin(request, prompt_hash),
                        "effective_input_receipt": {
                            "hash": prompt_hash,
                            "model": cfg.llm.critic.model,
                        },
                        "execution_receipt": execution,
                    },
                }
            ).model_dump(mode="json")
            sess.add(
                LlmRunModel(
                    id=run_id,
                    project_id=project.id,
                    workflow_run_id=request.workflow_run_id,
                    logical_role="critic",
                    provider="minimax",
                    model_name=cfg.llm.critic.model,
                    prompt_template=request.prompt_template,
                    prompt_version=request.prompt_version,
                    prompt_hash=prompt_hash,
                    finish_reason="tool_calls",
                    metadata_json=metadata,
                )
            )
            sess.add(
                ExecutionOperationModel(
                    key=operation_key,
                    scope_id=SCOPE,
                    input_hash=prompt_hash,
                    status="completed",
                    owner="fixture",
                    lease_until=datetime.now(UTC) + timedelta(hours=1),
                    result_json=paid,
                )
            )
            sess.add(
                ExecutionAttemptModel(
                    id=attempt_id,
                    operation_key=operation_key,
                    scope_id=SCOPE,
                    model_name=cfg.llm.critic.model,
                    input_hash=prompt_hash,
                    status="completed",
                    reserved_tokens=7,
                    actual_tokens=7,
                    created_at=datetime.now(UTC),
                )
            )
            transport_key = hashlib.sha256(
                f"transport-result.v1\n{SCOPE}\n{attempt_id}".encode()
            ).hexdigest()
            sess.add(
                ExecutionOperationModel(
                    key=transport_key,
                    scope_id=SCOPE,
                    input_hash=prompt_hash,
                    status="transport_result",
                    owner="fixture",
                    lease_until=datetime.now(UTC) + timedelta(hours=1),
                    result_json={
                        "schema": "transport-result.v1",
                        "attempt_id": attempt_id,
                        "operation_key": operation_key,
                        "input_hash": prompt_hash,
                        "result": paid,
                    },
                )
            )
            paid_budget = await sess.get(ExecutionBudgetModel, SCOPE)
            paid_budget.calls_used += 1
            paid_budget.tokens_charged += 7
            await sess.commit()
            calls.append(request)
            return value

        monkeypatch.setattr(judge, "complete_text", complete)
        # Keep the fixture on one real local session; no server pool or PG.
        from bestseller.infra.db import session as db_session

        def no_pool():
            raise RuntimeError("isolated SQLite fixture")

        monkeypatch.setattr(db_session, "get_server_session", no_pool)
        yield SimpleNamespace(
            session=session,
            engine=engine,
            settings=settings,
            project=project,
            chapter=chapter,
            draft=draft,
            owner=owner,
            budget=budget,
            calls=calls,
            outcomes=outcomes,
        )
    await engine.dispose()


async def _judge(rig, *, workflow=None, bound=False, retry=None, samples=3, **changes):
    args = dict(
        chapter_number=2,
        content_md=rig.draft.content_md,
        samples=samples,
        workflow_run_id=(workflow or rig.owner).id,
        project_id=rig.project.id,
        generation_input=getattr(rig, "generation_input", None),
        commercial_retry_context=retry,
    )
    if bound:
        args.update(chapter_id=rig.chapter.id, draft_id=rig.draft.id)
    args.update(changes)
    with bind_execution_scope(SCOPE):
        return await judge.judge_chapter_commercial_quality_stable(
            rig.session, rig.settings, **args
        )


async def _save_report(rig, result):
    row = ReviewReportModel(
        project_id=rig.project.id,
        target_type="chapter",
        target_id=rig.chapter.id,
        reviewer_type="critic",
        verdict="unknown",
        severity_max="critical",
        structured_output={
            "draft_id": str(rig.draft.id),
            "reviewed_body_fingerprint": {"digest": hashlib.sha256(BODY.encode()).hexdigest()},
            "critic_evidence": {"status": "evidence_invalid"},
            "evidence_summary": {"llm_commercial_judge": result.model_dump(mode="json")},
        },
    )
    rig.session.add(row)
    await rig.session.commit()
    return row


async def _history(rig, *, bound=False):
    first = await _judge(rig, bound=bound)
    prior = await _save_report(rig, first)
    retry = await recovery.load_commercial_retry_context(
        rig.session,
        project_id=rig.project.id,
        chapter_id=rig.chapter.id,
        draft_id=rig.draft.id,
        content_md=BODY,
        retry_context={"attempt": 2, "prior_report_id": str(prior.id)},
    )
    second = await _judge(rig, bound=bound, retry=retry)
    latest = await _save_report(rig, second)
    return first, second, prior, latest


async def _new_workflow(rig):
    row = WorkflowRunModel(
        project_id=rig.project.id,
        workflow_type="chapter_pipeline",
        scope_type="chapter",
        scope_id=rig.chapter.id,
        status="running",
        metadata_json={},
    )
    rig.session.add(row)
    await rig.session.commit()
    return row


@pytest.mark.parametrize("bound_initial", [False, True])
@pytest.mark.parametrize("majority", [False, True])
async def test_new_primary_workflow_replays_entire_cohort_without_new_paid_slots(
    cohort, bound_initial, majority
):
    rig = cohort
    if not majority:
        rig.outcomes[1] = [False, False]
    _first, second, prior, latest = await _history(rig, bound=bound_initial)
    assert len(rig.calls) == 5 and rig.budget.calls_used == 5
    if majority:
        assert second.overall_score == 0.73 and second.quality_rejected and second.has_critical
    else:
        assert second.infrastructure_failure
    frozen = deepcopy([prior.structured_output, latest.structured_output])
    for _ in range(2):
        other = await _new_workflow(rig)
        replay = await _judge(rig, workflow=other, bound=True)
        assert replay.model_dump(exclude={"raw_excerpt"}) == second.model_dump(
            exclude={"raw_excerpt"}
        )
        samples = json.loads(replay.raw_excerpt)["samples"]
        assert len(samples) == 3 and not samples[2]["valid"]
        assert json.loads(samples[2]["report"]["raw_excerpt"])["attempt"] == 2
        assert len(rig.calls) == 5 and rig.budget.calls_used == 5
    assert [prior.structured_output, latest.structured_output] == frozen
    await rig.session.refresh(rig.chapter)
    assert "raw_report" not in json.dumps(rig.chapter.metadata_json)


@pytest.mark.parametrize(
    "changed",
    [
        "body",
        "draft",
        "source",
        "model",
        "threshold",
        "slots",
        "paid_unknown",
        "raw_tool",
        "domain",
        "duplicate",
    ],
)
async def test_changed_binding_or_unproven_paid_result_never_grants_fresh_calls(
    cohort, monkeypatch, changed
):
    rig = cohort
    _, _, _, latest = await _history(rig)
    args = {}
    if changed == "body":
        rig.draft.content_md += "新正文。"
    elif changed == "draft":
        original = rig.draft
        original.is_current = False
        rig.draft = ChapterDraftVersionModel(
            project_id=rig.project.id,
            chapter_id=rig.chapter.id,
            version_no=10,
            content_md=BODY,
            is_current=True,
            promotion_state="candidate",
        )
        rig.session.add(rig.draft)
    elif changed == "source":
        args["generation_input"] = {"established_facts": {"facts": [{"text": "港口已经关闭。"}]}}
    elif changed == "model":
        rig.settings.llm.critic.model = "minimax/different-model"
    elif changed == "threshold":
        original = judge.chapter_commercial_thresholds
        monkeypatch.setattr(
            judge, "chapter_commercial_thresholds", lambda *a: (0.999, original(*a)[1])
        )
    elif changed == "slots":
        args["samples"] = 2
    elif changed in {"paid_unknown", "raw_tool"}:
        run = await rig.session.scalar(select(LlmRunModel))
        key = run.metadata_json["execution_receipt"]["operation_key"]
        operation = await rig.session.get(ExecutionOperationModel, key)
        if changed == "paid_unknown":
            operation.status = "unknown"
        else:
            value = deepcopy(operation.result_json)
            value["tool_calls"][0]["function"]["arguments"] = "{}"
            operation.result_json = value
    else:
        value = deepcopy(latest.structured_output)
        commercial = value["evidence_summary"]["llm_commercial_judge"]
        if changed == "domain":
            commercial["overall_score"] = 0.99
        else:
            receipt = json.loads(commercial["raw_excerpt"])
            receipt["samples"][1]["sample_slot"] = receipt["samples"][0]["sample_slot"]
            commercial["raw_excerpt"] = json.dumps(receipt)
        latest.structured_output = value
    await rig.session.commit()
    replay = await _judge(rig, workflow=await _new_workflow(rig), bound=True, **args)
    assert replay.infrastructure_failure and not replay.passed
    assert len(rig.calls) == 5 and rig.budget.calls_used == 5


@pytest.mark.parametrize("existing", [False, True])
async def test_retained_only_public_probe_never_dispatches(cohort, monkeypatch, existing):
    rig = cohort
    second = None
    if existing:
        _, second, _, _ = await _history(rig)
    before = rig.budget.calls_used

    async def forbidden(*args, **kwargs):
        pytest.fail("retained-only probe must never dispatch")

    monkeypatch.setattr(judge, "complete_text", forbidden)
    result = await _judge(
        rig, bound=True, workflow=await _new_workflow(rig), commercial_retained_only=True
    )
    if existing:
        assert result.model_dump(exclude={"raw_excerpt"}) == second.model_dump(
            exclude={"raw_excerpt"}
        )
    else:
        assert result.infrastructure_failure
    assert rig.budget.calls_used == before


async def test_scope_unknown_is_not_bypassed_by_retained_components(cohort):
    rig = cohort
    await _history(rig)
    rig.budget.status = "unknown"
    await rig.session.commit()
    from bestseller.services.budget import ExecutionBudgetError

    with pytest.raises(ExecutionBudgetError, match="execution_result_unknown"):
        await _judge(
            rig, bound=True, workflow=await _new_workflow(rig), commercial_retained_only=True
        )
    assert len(rig.calls) == 5


async def test_prepared_request_survives_real_exit_new_workflow_and_runtime_receipt_change(
    cohort, monkeypatch
):
    rig = cohort
    original = judge.complete_text
    captured = []

    class ExitBeforeSend(BaseException):
        pass

    async def interrupt(session, settings, request):
        captured.append(request)
        raise ExitBeforeSend()

    monkeypatch.setattr(judge, "complete_text", interrupt)
    with pytest.raises(ExitBeforeSend):
        await _judge(rig, bound=True, generation_input={"runtime": {"attempt": 1}})
    assert not rig.calls
    changed_workflow = await _new_workflow(rig)
    expected_hash = hashlib.sha256(captured[0].user_prompt.encode()).hexdigest()
    database = str(rig.engine.url)
    ids = [str(rig.project.id), str(rig.chapter.id), str(rig.draft.id), str(changed_workflow.id)]
    await rig.session.close()
    script = """
import asyncio, hashlib, json, os, sys
from uuid import UUID
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from bestseller.services import chapter_llm_quality_judge as judge
from bestseller.services.budget import bind_execution_scope
from bestseller.infra.db import session as db
from bestseller.infra.db.models import ChapterDraftVersionModel
from bestseller.settings import load_settings
async def main():
    engine=create_async_engine(sys.argv[1]); factory=async_sessionmaker(engine,expire_on_commit=False)
    settings=load_settings(env={}); settings.llm.critic.model="minimax/offline-fixture"; settings.llm.critic.model_override=None
    def no_pool(): raise RuntimeError("isolated")
    db.get_server_session=no_pool
    async def stop(session, settings, request):
        print(json.dumps({"owner":str(request.workflow_run_id),"user_hash":hashlib.sha256(request.user_prompt.encode()).hexdigest()}),flush=True)
        os._exit(86)
    judge.complete_text=stop
    async with factory() as session:
        draft=await session.get(ChapterDraftVersionModel,UUID(sys.argv[4]))
        with bind_execution_scope("test:commercial-retained-cohort"):
            await judge.judge_chapter_commercial_quality_stable(session,settings,chapter_number=2,content_md=draft.content_md,
                project_id=UUID(sys.argv[2]),chapter_id=UUID(sys.argv[3]),draft_id=draft.id,
                workflow_run_id=UUID(sys.argv[5]),samples=3,generation_input={"runtime":{"attempt":2}})
asyncio.run(main())
"""
    child = subprocess.run(
        [sys.executable, "-c", script, database, *ids],
        env=os.environ.copy(),
        text=True,
        capture_output=True,
        timeout=30,
    )
    assert child.returncode == 86, child.stderr
    receipt = json.loads(child.stdout.strip().splitlines()[-1])
    assert receipt == {"owner": str(rig.owner.id), "user_hash": expected_hash}
    monkeypatch.setattr(judge, "complete_text", original)
    rig.session = async_sessionmaker(rig.engine, expire_on_commit=False)()
    try:
        rig.budget = await rig.session.get(ExecutionBudgetModel, SCOPE)
        result = await _judge(
            rig, bound=True, workflow=changed_workflow, generation_input={"runtime": {"attempt": 3}}
        )
        assert result.infrastructure_failure and len(rig.calls) == 3
        assert rig.calls[0].user_prompt == captured[0].user_prompt
        assert rig.calls[0].workflow_run_id == rig.owner.id
        assert rig.budget.calls_used == 3
        again = await _judge(
            rig,
            bound=True,
            workflow=await _new_workflow(rig),
            generation_input={"runtime": {"attempt": 4}},
        )
        assert again.infrastructure_failure and len(rig.calls) == 3
    finally:
        await rig.session.close()


@pytest.mark.parametrize("old_first", [True, False])
@pytest.mark.parametrize("legacy", [True, False])
async def test_distinct_approved_source_history_does_not_poison_current_cohort(
    cohort, monkeypatch, old_first, legacy
):
    from dataclasses import replace
    from bestseller.services import commercial_source_evidence as evidence

    rig = cohort
    old_source = {"established_facts": {"facts": [{"text": "港口已经关闭。"}]}}
    assemble = evidence.assemble_commercial_sources

    def historical_protocol(**kwargs):
        source = assemble(**kwargs)
        if legacy and rig.generation_input is not None:
            source = replace(source, receipt={
                **source.receipt, "source_protocol_sha256": hashlib.sha256(b"legacy-protocol").hexdigest()
            })
        return source

    monkeypatch.setattr(evidence, "assemble_commercial_sources", historical_protocol)
    histories = {}
    for old in ([True, False] if old_first else [False, True]):
        rig.generation_input = old_source if old else None
        histories[old] = await _history(rig)
    if legacy:
        for row in histories[True][2:]:
            payload = deepcopy(row.structured_output)
            commercial = payload["evidence_summary"]["llm_commercial_judge"]
            receipt = json.loads(commercial["raw_excerpt"])
            for sample in receipt["samples"]:
                envelope = json.loads(sample["report"]["raw_excerpt"])
                envelope.pop("evaluation_contract")
                sample["report"]["raw_excerpt"] = json.dumps(envelope)
                from uuid import UUID
                run = await rig.session.get(LlmRunModel, UUID(sample["report"]["llm_run_id"]))
                metadata = dict(run.metadata_json)
                metadata.pop("commercial_evaluation_contract", None)
                run.metadata_json = metadata
            commercial["raw_excerpt"] = json.dumps(receipt)
            row.structured_output = payload
        await rig.session.commit()
    rows = list(await rig.session.scalars(select(ReviewReportModel)))
    frozen = {row.id: deepcopy(row.structured_output) for row in rows}
    before = len(rig.calls)
    rig.generation_input = None
    replay = await _judge(rig, bound=True, commercial_retained_only=True)
    assert replay.model_dump(exclude={"raw_excerpt"}) == histories[False][1].model_dump(
        exclude={"raw_excerpt"}
    )
    assert len(rig.calls) == before
    assert {row.id: row.structured_output for row in rows} == frozen


async def test_only_incompatible_history_never_grants_fresh_credit(cohort):
    rig = cohort
    await _history(rig)
    rig.generation_input = {"established_facts": {"facts": [{"text": "港口已经关闭。"}]}}
    before = len(rig.calls)
    result = await _judge(rig, bound=True)
    assert result.infrastructure_failure and len(rig.calls) == before
    await rig.session.refresh(rig.chapter)
    assert "commercial_component_cohorts" not in (rig.chapter.metadata_json or {})


async def test_forged_source_identity_cannot_hide_matching_paid_history(cohort):
    rig = cohort
    _, _, _, latest = await _history(rig)
    payload = deepcopy(latest.structured_output)
    commercial = payload["evidence_summary"]["llm_commercial_judge"]
    receipt = json.loads(commercial["raw_excerpt"])
    for sample in receipt["samples"]:
        envelope = json.loads(sample["report"]["raw_excerpt"])
        envelope["source_binding"]["semantic_source_sha256"] = "forged-old-source"
        sample["report"]["raw_excerpt"] = json.dumps(envelope)
    commercial["raw_excerpt"] = json.dumps(receipt)
    latest.structured_output = payload
    await rig.session.commit()
    before = len(rig.calls)
    result = await _judge(rig, bound=True, commercial_retained_only=True)
    assert result.infrastructure_failure and len(rig.calls) == before


@pytest.mark.parametrize("samples", [1, 3])
async def test_rejected_component_savepoint_preserves_borrowed_caller_work(
    cohort, monkeypatch, samples
):
    from sqlalchemy import update

    rig = cohort
    chapter_id, project_id = rig.chapter.id, rig.project.id
    original_title = rig.chapter.title
    rig.project.title = "caller uncommitted work"

    async def reject(session, **kwargs):
        await session.execute(
            update(ChapterModel).where(ChapterModel.id == chapter_id).values(title="rejected")
        )
        raise ValueError("fixture paid proof rejected")

    async def forbidden(*args, **kwargs):
        pytest.fail("no gateway or caller-wide rollback allowed")

    monkeypatch.setattr(recovery, "_historical_commercial_cohort", reject)
    monkeypatch.setattr(judge, "complete_text", forbidden)
    monkeypatch.setattr(rig.session, "rollback", forbidden)
    result = await _judge(rig, bound=True, samples=samples, commercial_retained_only=True)
    assert result.infrastructure_failure and rig.session.in_transaction()
    assert await rig.session.scalar(
        select(ProjectModel.title).where(ProjectModel.id == project_id)
    ) == "caller uncommitted work"
    assert await rig.session.scalar(
        select(ChapterModel.title).where(ChapterModel.id == chapter_id)
    ) == original_title
    await rig.session.commit()
    factory = async_sessionmaker(rig.engine, expire_on_commit=False)
    async with factory() as other:
        assert await other.scalar(
            select(ProjectModel.title).where(ProjectModel.id == project_id)
        ) == "caller uncommitted work"


@pytest.mark.parametrize("failure", ["unavailable", "budget", "base", "commit"])
async def test_owned_sample_releases_real_sqlite_writer_before_siblings_finish(
    cohort, monkeypatch, failure
):
    import asyncio
    from contextlib import asynccontextmanager
    from sqlalchemy import update
    from bestseller.infra.db import session as db_session
    from bestseller.services.budget import ExecutionBudgetError
    from bestseller.services.commercial_source_evidence import commercial_judge_unavailable

    class SampleExit(BaseException):
        pass

    rig = cohort
    chapter_id = rig.chapter.id
    locked, waiting = asyncio.Event(), asyncio.Event()
    count = 0
    yielded = []
    factory = async_sessionmaker(rig.engine, expire_on_commit=False)

    @asynccontextmanager
    async def pool():
        async with factory() as independent:
            if failure == "commit" and not yielded:
                commit = independent.commit
                failed = False

                async def fail_first_commit():
                    nonlocal failed
                    if not failed:
                        failed = True
                        raise RuntimeError("fixture commit failure")
                    await commit()

                monkeypatch.setattr(independent, "commit", fail_first_commit)
            yielded.append(independent)
            yield independent
            await independent.commit()

    async def sample(session, settings, *, sample_slot, **kwargs):
        nonlocal count
        slot = int(sample_slot.rsplit(":", 1)[-1])
        if slot:
            await locked.wait()
            count += 1
            if count == 2:
                waiting.set()
        await session.execute(
            update(ChapterModel).where(ChapterModel.id == chapter_id).values(title=f"temporary{slot}")
        )
        if slot == 0:
            locked.set()
            await waiting.wait()
            if failure == "budget":
                raise ExecutionBudgetError("execution_result_unknown", SCOPE)
            if failure == "base":
                raise SampleExit("fixture exit")
        return commercial_judge_unavailable(diagnostic="fixture proof rejection")

    monkeypatch.setattr(db_session, "get_server_session", pool)
    monkeypatch.setattr(judge, "judge_chapter_commercial_quality", sample)
    operation = judge.judge_chapter_commercial_quality_stable(
        rig.session, rig.settings, chapter_number=2, content_md=BODY, samples=3
    )
    if failure == "unavailable":
        result = await asyncio.wait_for(operation, timeout=3)
        assert result.infrastructure_failure
    else:
        error = {"budget": ExecutionBudgetError, "base": SampleExit, "commit": RuntimeError}[failure]
        with pytest.raises(error):
            await asyncio.wait_for(operation, timeout=3)
    assert count == 2 and len({id(s) for s in yielded}) == 3
    assert all(not s.in_transaction() for s in yielded)
    async with factory() as other:
        assert await other.scalar(
            select(ChapterModel.title).where(ChapterModel.id == chapter_id)
        ) in {"temporary1", "temporary2"}


async def test_three_retained_wrong_draft_samples_preserve_valid_journal(cohort, monkeypatch):
    import asyncio
    from contextlib import asynccontextmanager
    from bestseller.infra.db import session as db_session

    rig = cohort
    _, expected, _, _ = await _history(rig)
    await _judge(rig, bound=True, commercial_retained_only=True)
    await rig.session.refresh(rig.chapter)
    await rig.session.refresh(rig.owner)
    chapter_id, owner_id, project_id = rig.chapter.id, rig.owner.id, rig.project.id
    before = (deepcopy(rig.chapter.metadata_json), deepcopy(rig.owner.metadata_json))
    calls = len(rig.calls)
    factory = async_sessionmaker(rig.engine, expire_on_commit=False)
    await rig.session.rollback()

    @asynccontextmanager
    async def pool():
        async with factory() as independent:
            yield independent
            await independent.commit()

    monkeypatch.setattr(db_session, "get_server_session", pool)
    with bind_execution_scope(SCOPE):
        result = await asyncio.wait_for(
            judge.judge_chapter_commercial_quality_stable(
                rig.session, rig.settings, chapter_number=2, content_md=BODY,
                project_id=project_id, chapter_id=chapter_id, draft_id=uuid4(),
                workflow_run_id=owner_id, samples=3, commercial_retained_only=True,
            ), timeout=3,
        )
    assert expected.overall_score == 0.73 and result.infrastructure_failure
    assert len(rig.calls) == calls
    async with factory() as other:
        chapter = await other.get(ChapterModel, chapter_id)
        owner = await other.get(WorkflowRunModel, owner_id)
        assert (chapter.metadata_json, owner.metadata_json) == before


async def test_current_source_protocol_missing_contract_is_not_legacy_skipped(cohort):
    rig = cohort
    _, _, _, latest = await _history(rig)
    payload = deepcopy(latest.structured_output)
    commercial = payload["evidence_summary"]["llm_commercial_judge"]
    receipt = json.loads(commercial["raw_excerpt"])
    for sample in receipt["samples"]:
        envelope = json.loads(sample["report"]["raw_excerpt"])
        envelope.pop("evaluation_contract")
        sample["report"]["raw_excerpt"] = json.dumps(envelope)
    commercial["raw_excerpt"] = json.dumps(receipt)
    latest.structured_output = payload
    await rig.session.commit()
    before = len(rig.calls)
    result = await _judge(rig, bound=True, commercial_retained_only=True)
    assert result.infrastructure_failure and len(rig.calls) == before


@pytest.mark.parametrize("invalid_slot", [False, True])
async def test_unbound_owned_samples_commit_real_flushed_llm_runs(cohort, monkeypatch, invalid_slot):
    from contextlib import asynccontextmanager
    from bestseller.infra.db import session as db_session

    rig = cohort
    project_id, owner_id = rig.project.id, rig.owner.id
    factory = async_sessionmaker(rig.engine, expire_on_commit=False)
    run_ids = []

    @asynccontextmanager
    async def pool():
        async with factory() as independent:
            yield independent
            await independent.commit()

    async def flush_only(session, settings, request):
        run_id = uuid4()
        run_ids.append(run_id)
        session.add(LlmRunModel(
            id=run_id, project_id=project_id, workflow_run_id=owner_id,
            logical_role="critic", provider="minimax", model_name="offline-fixture",
            prompt_template=request.prompt_template, prompt_version=request.prompt_version,
            prompt_hash=hashlib.sha256(request.user_prompt.encode()).hexdigest(),
            finish_reason="tool_calls", metadata_json=request.metadata,
        ))
        await session.flush()  # Normal gateway persistence relies on the session owner committing.
        payload = report(0.75)
        if invalid_slot and request.sample_slot.endswith(":2"):
            payload["issues"][0]["body_id"] = "Dforeign:P0001"
        return _review_completion(
            content=json.dumps(payload, ensure_ascii=False), provider="minimax",
            model_name="offline-fixture", finish_reason="tool_calls", llm_run_id=run_id,
        )

    monkeypatch.setattr(db_session, "get_server_session", pool)
    monkeypatch.setattr(judge, "complete_text", flush_only)
    result = await judge.judge_chapter_commercial_quality_stable(
        rig.session, rig.settings, chapter_number=2, content_md=BODY, samples=3,
        project_id=project_id, workflow_run_id=owner_id,
    )
    assert not result.infrastructure_failure and len(run_ids) == 3
    async with factory() as other:
        persisted = set(await other.scalars(select(LlmRunModel.id)))
        assert persisted == set(run_ids)


@pytest.mark.parametrize("bound_initial", [False, True])
@pytest.mark.parametrize("majority", [False, True])
async def test_aggregate_exact_receipt_survives_json_key_order_roundtrip(
    cohort, monkeypatch, bound_initial, majority
):
    from sqlalchemy.orm.attributes import flag_modified

    rig = cohort
    original_report = report

    def multiple_dimensions(score):
        payload = original_report(score)
        # JSONB's ordering differs from this original paid argument ordering.
        payload["dimension_scores"] = {"readability": score, "opening_pull": score}
        return payload

    monkeypatch.setattr(sys.modules[__name__], "report", multiple_dimensions)
    if not majority:
        rig.outcomes[1] = [False, False]
    _, second, prior, latest = await _history(rig, bound=bound_initial)
    protected = deepcopy([prior.structured_output, latest.structured_output])
    calls = len(rig.calls)
    first = await _judge(rig, bound=True, commercial_retained_only=True)
    assert first.raw_excerpt == second.raw_excerpt
    await rig.session.refresh(rig.chapter)
    await rig.session.refresh(rig.owner)
    frozen = deepcopy((rig.chapter.metadata_json, rig.owner.metadata_json))
    journal = next(iter(rig.owner.metadata_json["commercial_component_cohorts"].values()))
    ordered_report = next(iter(journal["slots"].values()))["report"]
    assert list(ordered_report["dimension_scores"]) == ["readability", "opening_pull"]
    for _ in range(2):
        # Model the JSONB roundtrip: sort object keys recursively, leave every
        # embedded raw slot/report string completely untouched, then reload.
        rig.owner.metadata_json = json.loads(json.dumps(rig.owner.metadata_json, sort_keys=True))
        flag_modified(rig.owner, "metadata_json")
        await rig.session.commit()
        await rig.session.refresh(rig.owner)
        journal = next(iter(rig.owner.metadata_json["commercial_component_cohorts"].values()))
        reordered_report = next(iter(journal["slots"].values()))["report"]
        assert list(reordered_report["dimension_scores"]) == ["opening_pull", "readability"]
        replay = await _judge(rig, bound=True, commercial_retained_only=True)
        assert replay.model_dump() == first.model_dump()
        await rig.session.refresh(rig.chapter)
        await rig.session.refresh(rig.owner)
        assert (rig.chapter.metadata_json, rig.owner.metadata_json) == frozen
    assert [prior.structured_output, latest.structured_output] == protected
    assert len(rig.calls) == calls
