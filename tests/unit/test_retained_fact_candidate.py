"""Real SQLite custody/transaction regression; no provider or production IO."""
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from sqlalchemy import JSON, MetaData, event, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.schema import CreateTable
from test_rewrite_fact_scope import FACT, FIXES, PARENT, _report

from bestseller.infra.db import models as db
from bestseller.services import retained_fact_candidate as service
from bestseller.services.drafts import count_words
from bestseller.services.pipelines import _chapter_critic_semantic_dependencies
from bestseller.services.reviews import chapter_review_body_fingerprint
from bestseller.services.rewrite_patch import text_hash
from bestseller.settings import load_settings

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]
MODELS = (db.ProjectModel, db.ChapterModel, db.SceneCardModel, db.ChapterDraftVersionModel,
          db.RewriteTaskModel, db.ReviewReportModel, db.QualityScoreModel, db.WorkflowRunModel,
          db.LlmRunModel, db.ExecutionBudgetModel, db.ExecutionOperationModel, db.ExecutionAttemptModel,
          db.PlanningArtifactVersionModel, db.WorldRuleModel, db.CharacterModel, db.ChapterContractModel)
RETURNED = PARENT.replace(FACT, FIXES[0]).replace("square stamp", "round stamp")
CANDIDATE = PARENT.replace(FACT, FIXES[0])


async def inventory(session):
    return {model.__tablename__: sorted((service._columns(row) for row in
            await session.scalars(select(model).execution_options(populate_existing=True))),
            key=service._json) for model in MODELS}


@pytest.fixture
async def case(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'retained_fact_test.sqlite3'}")

    # Explicit BEGIN makes SAVEPOINT remain subordinate to the caller's outer
    # transaction on sqlite3 as well as on the production SQLAlchemy dialect.
    @event.listens_for(engine.sync_engine, "connect")
    def connect(connection, _record):
        connection.isolation_level = None

    @event.listens_for(engine.sync_engine, "begin")
    def begin(connection):
        connection.exec_driver_sql("BEGIN")

    def ddl(connection):
        for model in MODELS:
            table = model.__table__.to_metadata(MetaData())
            for column in table.c:
                if isinstance(column.type, JSONB):
                    column.type = JSON()
                if column.server_default and "::jsonb" in str(column.server_default.arg):
                    column.server_default = None
            connection.execute(CreateTable(table, include_foreign_key_constraints=[]))
        connection.exec_driver_sql("CREATE UNIQUE INDEX retained_test_current "
                                   "ON chapter_draft_versions(chapter_id) WHERE is_current=1")

    async with engine.begin() as connection:
        await connection.run_sync(ddl)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    settings = load_settings(env={})
    async with factory() as session:
        project = db.ProjectModel(slug="retained-fact-test", title="Register", genre="mystery",
                                  language="en", target_chapters=4, target_word_count=8000)
        session.add(project)
        await session.flush()
        chapter = db.ChapterModel(project_id=project.id, chapter_number=1, chapter_goal="Check the register",
                                  target_word_count=2000, current_word_count=99,
                                  production_state="reviewed", metadata_json={"preserve_history": ["old"]})
        session.add(chapter)
        await session.flush()
        scene = db.SceneCardModel(project_id=project.id, chapter_id=chapter.id, scene_number=1,
                                  scene_type="action", target_word_count=2000)
        workflow = db.WorkflowRunModel(project_id=project.id, workflow_type="chapter_pipeline",
            scope_type="chapter", scope_id=chapter.id, requested_by="fixture", status="completed")
        parent = db.ChapterDraftVersionModel(project_id=project.id, chapter_id=chapter.id,
            version_no=11, content_md=PARENT, word_count=count_words(PARENT), is_current=True,
            promotion_state="candidate", promotion_metadata={"old_evidence": ["keep"]})
        session.add_all([scene, workflow, parent])
        await session.flush()
        run = db.LlmRunModel(project_id=project.id, workflow_run_id=workflow.id, logical_role="editor",
            provider="recorded-provider", model_name="recorded-model", prompt_template="chapter_rewrite",
            prompt_version="source-v1", prompt_hash="a" * 64, input_tokens=320, output_tokens=80,
            finish_reason="stop", request_payload_ref=None)
        session.add(run)
        await session.flush()
        returned = db.ChapterDraftVersionModel(project_id=project.id, chapter_id=chapter.id,
            version_no=12, content_md=RETURNED, word_count=count_words(RETURNED), is_current=False,
            promotion_state="candidate", llm_run_id=run.id)
        payload = _report()
        fingerprint = chapter_review_body_fingerprint(PARENT)
        payload.update(draft_id=str(parent.id), reviewed_body_fingerprint=fingerprint)
        report = db.ReviewReportModel(project_id=project.id, target_type="chapter", target_id=chapter.id,
            reviewer_type="retained-review", verdict="rewrite", severity_max="high", structured_output=payload)
        session.add_all([returned, report])
        await session.flush()
        task = db.RewriteTaskModel(project_id=project.id, trigger_type="chapter_review", trigger_source_id=chapter.id,
            rewrite_strategy="chapter_coherence_bridge_rewrite", status="completed", attempts=3,
            instructions="Correct the cited arithmetic only.", metadata_json={
                "parent_draft_id": str(parent.id), "parent_draft_hash": text_hash(PARENT),
                "candidate_chapter_draft_id": str(returned.id), "candidate_body_sha256": text_hash(RETURNED),
                "candidate_llm_run_id": str(run.id), "review_report_id": str(report.id), "history": ["preserve"]})
        quality = db.QualityScoreModel(project_id=project.id, target_type="chapter", target_id=chapter.id,
            chapter_draft_version_id=parent.id, review_report_id=report.id, score_overall=0.91,
            evidence_summary=deepcopy(payload["evidence_summary"]))
        session.add_all([task, quality])
        await session.flush()
        semantic = await _chapter_critic_semantic_dependencies(session, settings, chapter)
        chapter.metadata_json = {**chapter.metadata_json, "chapter_critic_cycles": {"original-cycle": {
            "state": "completed", "source_draft_id": str(parent.id), "source_body_hash": fingerprint["digest"],
            "semantic_dependencies_sha256": semantic, "attempts": [{"state": "completed", "outcome": "available",
                "draft_id": str(parent.id), "body_fingerprint": fingerprint, "rewrite_task_id": str(task.id),
                "review_report_id": str(report.id), "quality_score_id": str(quality.id)}]}}}
        origin = {"schema": "paid-call-origin.v1", "status": "recorded", **{
            key: str(getattr(run, key)) if getattr(run, key) is not None else None for key in
            ("project_id", "workflow_run_id", "step_run_id", "logical_role", "prompt_template", "prompt_version", "prompt_hash")}}
        scope, key, attempt_id = "retained-test-scope", "b" * 64, "retained-test-attempt"
        receipt = {"policy": "execution-budget.v1", "scope_id": scope, "operation_key": key,
            "attempts": [{"attempt_id": attempt_id, "model": run.model_name, "status": "completed",
                          "reserved_tokens": 500, "effective_input_hash": run.prompt_hash}]}
        effective = {"schema": "effective-input.v1", "hash": run.prompt_hash, "model": run.model_name}
        run.metadata_json = {"paid_call_origin": origin, "execution_receipt": receipt,
                             "effective_input_receipt": {**effective, "tools_included": False}}
        physical = {"content": RETURNED, "provider": run.provider, "model_name": run.model_name,
            "tool_calls": None, "raw_message": {"content": RETURNED}, "input_tokens": 320, "output_tokens": 80,
            "fallback_used": False, "fallback_source": None, "finish_reason": "stop", "llm_run_id": None,
            "metadata": {"paid_call_origin": origin, "effective_input_receipt": effective}}
        now = datetime.now(UTC)
        budget = db.ExecutionBudgetModel(scope_id=scope, max_calls=10, max_tokens=10000, calls_used=1,
            tokens_charged=400, deadline_at=now + timedelta(hours=1), status="active", metadata_json={"keep": True})
        operation = db.ExecutionOperationModel(key=key, scope_id=scope, input_hash=run.prompt_hash,
            status="completed", owner="fixture", lease_until=now, result_json=deepcopy(physical))
        attempt = db.ExecutionAttemptModel(id=attempt_id, operation_key=key, scope_id=scope, model_name=run.model_name,
            input_hash=run.prompt_hash, reserved_tokens=500, actual_tokens=400, status="completed", created_at=now)
        transport = db.ExecutionOperationModel(key=text_hash("transport-result.v1\n" + scope + "\n" + attempt_id),
            scope_id=scope, input_hash=run.prompt_hash, status="transport_result", owner="fixture", lease_until=now,
            result_json={"schema": "transport-result.v1", "attempt_id": attempt_id, "operation_key": key,
                         "input_hash": run.prompt_hash, "result": deepcopy(physical)})
        session.add_all([budget, operation, attempt, transport])
        await session.commit()
        ids = {name: getattr(row, "id", None) for name, row in (
            ("project", project), ("chapter", chapter), ("task", task), ("parent", parent),
            ("returned", returned), ("report", report), ("run", run), ("quality", quality))}
    writes, commits = [], []

    @event.listens_for(engine.sync_engine, "before_cursor_execute")
    def observe(_connection, _cursor, statement, *_args):
        if statement.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")):
            writes.append(statement)

    @event.listens_for(engine.sync_engine, "commit")
    def observe_commit(_connection):
        commits.append(True)

    async def forbidden(*_args, **_kwargs):
        pytest.fail("retained body derivation must never invoke a provider")

    from bestseller.services import llm, reviews
    for module in (llm, reviews):
        monkeypatch.setattr(module, "complete_text", forbidden)
    result = SimpleNamespace(factory=factory, settings=settings, ids=ids, writes=writes, commits=commits,
        arguments={"project_id": ids["project"], "chapter_id": ids["chapter"], "task_id": ids["task"]})
    try:
        yield result
    finally:
        await engine.dispose()


async def prepare(session, case):
    return await service.prepare_retained_fact_candidate(session, case.settings, **case.arguments)


async def apply(session, case, witness):
    return await service.apply_retained_fact_candidate(session, case.settings, **case.arguments,
        expected_witness_sha256=witness, actor="test-operator", evidence_ref="local-test-proof")


async def test_prepare_read_only_detached_witness_uses_real_scope_and_semantics(case):
    async with case.factory() as session:
        before = await inventory(session)
        prepared = await prepare(session, case)
        assert prepared["candidate_text"] == CANDIDATE != RETURNED
        assert prepared["candidate_sha256"] == text_hash(CANDIDATE)
        assert prepared["unknowns"] == ["full_original_request_payload_not_retained"]
        assert prepared["witness_sha256"] == text_hash(service._json(prepared["witness"]))
        assert not prepared["already_applied"] and prepared["fresh_review_required"]
        assert prepared["adoption_authorized"] is prepared["grades_modified"] is prepared["grants_credit"] is False
        assert before == await inventory(session) and not case.writes and not case.commits
        prepared["witness"]["task"]["metadata_json"]["history"].append("detached")
        assert (await prepare(session, case))["witness"]["task"]["metadata_json"]["history"] == ["preserve"]


async def test_apply_idempotent_readback_no_commit_and_outer_rollback_preserves_every_row(case):
    async with case.factory() as session:
        before = await inventory(session)
        prepared = await prepare(session, case)
        await apply(session, case, prepared["witness_sha256"])
        candidate = await session.get(db.ChapterDraftVersionModel, UUID(prepared["candidate_id"]))
        assert candidate.content_md == CANDIDATE and candidate.llm_run_id is None
        assert candidate.is_current and candidate.promotion_state == "candidate" and candidate.promotion_score is None
        assert candidate.promotion_metadata["paid_llm_run_id"] == str(case.ids["run"])
        assert candidate.word_count == count_words(CANDIDATE)
        after = await inventory(session)
        for table in before:
            if table not in {"chapters", "chapter_draft_versions"}:
                assert after[table] == before[table], table
        first_write_count = len(case.writes)
        again = await apply(session, case, prepared["witness_sha256"])
        assert again["already_applied"] and again["candidate_id"] == prepared["candidate_id"]
        assert len(case.writes) == first_write_count and not case.commits
        await session.rollback()
        assert await inventory(session) == before


@pytest.mark.parametrize("damage", ["parent", "returned", "report", "task", "quality", "paid_origin",
                                     "physical", "attempt", "budget", "semantic", "pointer"])
async def test_changed_source_rejected_before_any_write(case, damage):
    async with case.factory() as session:
        prepared = await prepare(session, case)
        if damage in {"parent", "returned"}:
            row = await session.get(db.ChapterDraftVersionModel, case.ids[damage])
            row.content_md += " altered"
        elif damage == "report":
            row = await session.get(db.ReviewReportModel, case.ids["report"])
            row.structured_output = {**row.structured_output, "unexpected_changed_field": True}
        elif damage == "task":
            row = await session.get(db.RewriteTaskModel, case.ids["task"])
            row.attempts += 1
        elif damage == "quality":
            row = await session.get(db.QualityScoreModel, case.ids["quality"])
            row.score_overall = 0.12
        elif damage == "paid_origin":
            row = await session.get(db.LlmRunModel, case.ids["run"])
            changed = deepcopy(row.metadata_json)
            changed["paid_call_origin"]["workflow_run_id"] = str(uuid4())
            row.metadata_json = changed
        elif damage == "physical":
            row = await session.scalar(select(db.ExecutionOperationModel).where(
                db.ExecutionOperationModel.status == "transport_result"))
            changed = deepcopy(row.result_json)
            changed["result"]["raw_message"] = {"content": "changed"}
            row.result_json = changed
        elif damage == "attempt":
            row = await session.scalar(select(db.ExecutionAttemptModel))
            row.actual_tokens += 1
        elif damage == "budget":
            row = await session.scalar(select(db.ExecutionBudgetModel))
            row.tokens_charged += 1
        elif damage == "semantic":
            row = await session.get(db.ChapterModel, case.ids["chapter"])
            row.chapter_goal = "Changed authored contract"
        else:
            row = await session.get(db.ChapterDraftVersionModel, case.ids["parent"])
            row.is_current = False
        await session.flush()
        before = await inventory(session)
        case.writes.clear()
        with pytest.raises(ValueError):
            await apply(session, case, prepared["witness_sha256"])
        assert await inventory(session) == before and not case.writes
        assert not case.commits and session.is_active


async def test_insert_failure_savepoint_restores_pointer_and_keeps_caller_transaction(case, monkeypatch):
    async with case.factory() as session:
        prepared = await prepare(session, case)
        before = await inventory(session)
        original = session.flush
        calls = 0

        async def fail_after_pointer_release(*args, **kwargs):
            nonlocal calls
            calls += 1
            if any(isinstance(row, db.ChapterDraftVersionModel) for row in session.new):
                raise RuntimeError("injected candidate insert failure")
            return await original(*args, **kwargs)

        monkeypatch.setattr(session, "flush", fail_after_pointer_release)
        with pytest.raises(RuntimeError, match="injected"):
            await apply(session, case, prepared["witness_sha256"])
        monkeypatch.setattr(session, "flush", original)
        assert calls >= 2 and session.is_active and not case.commits
        assert await inventory(session) == before


async def test_clean_session_and_explicit_expected_witness_required(case):
    async with case.factory() as session:
        chapter = await session.get(db.ChapterModel, case.ids["chapter"])
        chapter.title = "Caller pending edit"
        with pytest.raises(ValueError, match="clean_session_required"):
            await prepare(session, case)
        assert not case.writes
        await session.rollback()
        with pytest.raises(ValueError, match="expected_witness_required"):
            await apply(session, case, None)
        with pytest.raises(ValueError, match="witness_changed"):
            await apply(session, case, "0" * 64)
        assert not case.writes and not case.commits
