"""R08 real isolated PG transactions/files, with synthetic retained input rows.

No model grade/literary acceptance is asserted. Native source/KG/grade joins,
pure gates, publisher, closure, savepoints and files are NOT mocked. All retained
review/empty knowledge rows are explicitly synthetic and created without any
gateway invocation. The test never touches a production project or public table.
"""
from copy import deepcopy
import asyncio
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import sys
from uuid import uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bestseller.infra.db import models as m
from bestseller.services import continuity, export_only_recovery as recovery
from bestseller.services.book_delivery_readiness import read_book_delivery_closure
from bestseller.services.fanqie_short_quality import review_whole_fanqie_short_story
from bestseller.services.reviews import chapter_review_body_fingerprint

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]
DB = "bestseller_repair_test_20261015"


@pytest.fixture(autouse=True)
def no_model_or_SDK(monkeypatch):
    def deny(*_args, **_kwargs):
        raise AssertionError("R08 forbids complete_text/provider/SDK/HTTP")
    from bestseller.services import llm
    import httpx
    import litellm
    for module in list(sys.modules.values()):
        if getattr(module, "__name__", "").startswith("bestseller.services.") and hasattr(module, "complete_text"):
            monkeypatch.setattr(module, "complete_text", deny)
    monkeypatch.setattr(llm, "complete_text", deny)
    for name in ("completion", "acompletion"):
        monkeypatch.setattr(litellm, name, deny)
    monkeypatch.setattr(httpx.Client, "send", deny)
    monkeypatch.setattr(httpx.AsyncClient, "send", deny)


def fixture_body():
    # Retained synthetic pure-gate fixture, not original-four production prose.
    path = Path(__file__).parents[1] / "unit" / "test_fanqie_source_export_integrity.py"
    import ast
    tree = ast.parse(path.read_text())
    node = next(n for n in tree.body if isinstance(n, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == "FIXTURE_TEXTS" for t in n.targets))
    value = json.loads(ast.literal_eval(node.value.args[0]))
    return value["strong_title"], value["strong_text"]


@pytest.fixture
async def pg_case(postgresql_test_settings, tmp_path):
    from sqlalchemy.engine import make_url
    url = make_url(postgresql_test_settings.database.url)
    assert url.database == DB
    schema = "r08_test_" + uuid4().hex
    engine = create_async_engine(url, connect_args={"server_settings": {
        "search_path": schema + ",public", "application_name": schema,
        "lock_timeout": "4000", "statement_timeout": "15000"}})
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    settings = postgresql_test_settings.model_copy(deep=True)
    settings.output.base_dir = tmp_path / "output"
    title, body = fixture_body()
    assert review_whole_fanqie_short_story(body, title=title, protagonist_name="我").passed
    try:
        async with engine.begin() as connection:
            assert await connection.scalar(text("select current_database()")) == DB
            await connection.execute(text("CREATE SCHEMA " + schema))
            assert await connection.scalar(text("select current_schema()")) == schema
            # checkfirst=False prevents public-table discovery from skipping our
            # private tables. Every table is created in the verified own schema.
            await connection.run_sync(lambda conn: m.Base.metadata.create_all(conn, checkfirst=False))
        async with factory() as session:
            project = m.ProjectModel(id=uuid4(), slug="r08-synthetic-" + uuid4().hex,
                title=title, genre="东方幻想", project_type="fanqie_short", status="writing",
                target_chapters=1, target_word_count=700,
                metadata_json={"execution_scope_id": "r08-test-" + uuid4().hex, "pov": "first_person",
                               "retained_counters": {"calls": 290}, "old_failed_title": {"status": "failed"},
                               "debt": [{"status": "open", "score": 52}]})
            session.add(project); await session.flush()
            chapter = m.ChapterModel(id=uuid4(), project_id=project.id, chapter_number=1,
                status="complete", production_state="ok", chapter_goal="SYNTHETIC retained source", metadata_json={})
            session.add(chapter); await session.flush()
            draft = m.ChapterDraftVersionModel(id=uuid4(), project_id=project.id, chapter_id=chapter.id,
                version_no=1, content_md=body, word_count=len(body), is_current=True,
                promotion_state="promoted", promotion_metadata={}, assembled_from_scene_draft_ids=[str(uuid4())])
            session.add(draft); await session.flush()
            source = {"schema": "adopted-knowledge.v1", "project_id": str(project.id), "chapter_id": str(chapter.id),
                "source_chapter_draft_version_id": str(draft.id), "source_version_no": 1,
                "source_body_sha256": hashlib.sha256(body.encode()).hexdigest()}
            chapter.metadata_json = {"knowledge_feedback_receipts": [{"status": "empty", "source_lineage": source}],
                "story_bible_update_receipts": [{"status": "empty", "source_lineage": source}]}
            snapshot = m.ChapterStateSnapshotModel(id=uuid4(), project_id=project.id, chapter_id=chapter.id,
                chapter_number=1, extraction_status="ok_promoted", raw_extraction='{"facts":[]}',
                extraction_model="SYNTHETIC retained fixture", facts={"facts": [], "snapshot_contract": {**source, "is_usable": True}})
            view = continuity.build_snapshot_epistemic_view(snapshot, source_body=body)
            continuity._publish_epistemic_view(snapshot, view, actor="R08-isolated-fixture",
                reason="synthetic empty snapshot view; no grade", before_sha256=continuity.snapshot_epistemic_witness(snapshot))
            wf = m.WorkflowRunModel(id=uuid4(), project_id=project.id, workflow_type="chapter_pipeline",
                status="completed", scope_type="chapter", scope_id=chapter.id, metadata_json={"fixture_only": True})
            session.add_all([snapshot, wf]); await session.flush()
            run = m.LlmRunModel(id=uuid4(), project_id=project.id, workflow_run_id=wf.id,
                logical_role="critic", provider="SYNTHETIC-NOT-DISPATCHED", model_name="SYNTHETIC-NOT-DISPATCHED",
                metadata_json={"fixture_only": True, "not_paid_proof": True})
            session.add(run); await session.flush()
            report = m.ReviewReportModel(id=uuid4(), project_id=project.id, target_type="chapter",
                target_id=chapter.id, reviewer_type="SYNTHETIC retained-input fixture", verdict="pass", llm_run_id=run.id,
                structured_output={"draft_id": str(draft.id), "reviewed_body_fingerprint": chapter_review_body_fingerprint(body),
                    "critic_evidence": {"status": "verified", "passed": True}, "fixture_only": True})
            session.add(report); await session.flush()
            score = m.QualityScoreModel(id=uuid4(), project_id=project.id, target_type="chapter", target_id=chapter.id,
                chapter_draft_version_id=draft.id, review_report_id=report.id, is_current=True, score_overall=.9, evidence_summary={})
            session.add_all([score, m.BookProductionControlModel(project_id=project.id, desired_state="run", command_serial=1)])
            await session.commit()
        yield type("Case", (), {"factory": factory, "settings": settings, "project": project,
            "chapter": chapter, "draft": draft, "snapshot": snapshot, "score": score, "report": report,
            "run": run, "workflow": wf, "engine": engine, "schema": schema})()
    finally:
        async with engine.begin() as connection:
            await connection.execute(text("DROP SCHEMA IF EXISTS " + schema + " CASCADE"))
        await engine.dispose()


async def immutable(case):
    async with case.factory() as session:
        rows = []
        for model in (m.ChapterModel, m.ChapterDraftVersionModel, m.ChapterStateSnapshotModel,
                      m.QualityScoreModel, m.ReviewReportModel, m.LlmRunModel, m.WorkflowRunModel):
            rows.extend(recovery._row(row) for row in await session.scalars(select(model)))
        return recovery._sha(rows)


async def test_pg_native_fanqie_export_reentry_missing_file_rebuild_and_unchanged_sources(pg_case):
    case = pg_case; before = await immutable(case)
    async with case.factory() as session:
        checked = await recovery.preflight_export_only_recovery(session, case.settings,
            project_id=case.project.id, requested_by="r08-test")
        assert checked.status is recovery.ExportOnlyStatus.ELIGIBLE, checked.to_dict()
        assert not session.new and not session.dirty
    first = await recovery.run_export_only_recovery(case.factory, case.settings, project_id=case.project.id, requested_by="r08-test")
    assert first.status is recovery.ExportOnlyStatus.COMPLETED, first.to_dict()
    assert await immutable(case) == before
    async with case.factory() as session:
        project = await session.get(m.ProjectModel, case.project.id)
        assert (await read_book_delivery_closure(session, project)).verdict.is_complete
        metadata = deepcopy(project.metadata_json)
    skipped = await recovery.run_export_only_recovery(case.factory, case.settings, project_id=case.project.id, requested_by="r08-test")
    assert skipped.status is recovery.ExportOnlyStatus.ALREADY_COMPLETE
    path = Path(metadata["fanqie_whole_export_receipt"]["export_paths"]["markdown_path"])
    original = path.read_bytes(); path.unlink()
    recovered = await recovery.run_export_only_recovery(case.factory, case.settings, project_id=case.project.id, requested_by="r08-test")
    assert recovered.status is recovery.ExportOnlyStatus.COMPLETED, recovered.to_dict()
    assert path.read_bytes() == original and await immutable(case) == before
    async with case.factory() as session:
        project = await session.get(m.ProjectModel, case.project.id)
        for key in ("retained_counters", "debt", "old_failed_title"):
            assert project.metadata_json[key] == case.project.metadata_json[key]


@pytest.mark.parametrize("fault", ["stop", "active", "grade", "knowledge", "unknown_attempt"])
async def test_pg_blockers_never_publish_or_create_new_model_receipts(pg_case, fault):
    case = pg_case
    async with case.factory() as session:
        if fault == "stop":
            row = await session.get(m.BookProductionControlModel, case.project.id); row.desired_state = "stop"
        elif fault == "active":
            session.add(m.WorkflowRunModel(project_id=case.project.id, workflow_type="chapter_pipeline", status="running", metadata_json={}))
        elif fault == "grade":
            row = await session.get(m.ReviewReportModel, case.report.id); row.verdict = "rewrite"
        elif fault == "knowledge":
            row = await session.get(m.ChapterStateSnapshotModel, case.snapshot.id); row.extraction_status = "failed"
        else:
            scope = case.project.metadata_json["execution_scope_id"]
            session.add(m.ExecutionBudgetModel(scope_id=scope, max_calls=1, calls_used=1, max_tokens=10,
                tokens_charged=10, deadline_at=datetime.now(UTC), status="exhausted", metadata_json={}))
            await session.flush()
            session.add(m.ExecutionOperationModel(key="a"*64, scope_id=scope, input_hash="b"*64, status="completed",
                owner="SYNTHETIC", lease_until=datetime.now(UTC), result_json={}))
            await session.flush()
            session.add(m.ExecutionAttemptModel(id="c"*64, operation_key="a"*64, scope_id=scope, model_name="SYNTHETIC",
                input_hash="b"*64, reserved_tokens=10, status="unknown", created_at=datetime.now(UTC)))
        await session.commit()
    before = await immutable(case)
    result = await recovery.run_export_only_recovery(case.factory, case.settings, project_id=case.project.id, requested_by="r08-test")
    assert result.status is recovery.ExportOnlyStatus.BLOCKED, result.to_dict()
    assert result.audit_path is None
    assert not (Path(case.settings.output.base_dir) / case.project.slug).exists()
    assert await immutable(case) == before


async def test_pg_file_failure_rolls_back_native_checkpoint_and_is_reenterable(pg_case, monkeypatch):
    case = pg_case; before = await immutable(case)
    from bestseller.services import fanqie_short_export as exporter
    native = exporter.export_fanqie_short_markdown
    def disk_full(*_args, **_kwargs): raise OSError("SYNTHETIC disk full")
    with monkeypatch.context() as patch:
        patch.setattr(exporter, "export_fanqie_short_markdown", disk_full)
        failed = await recovery.run_export_only_recovery(case.factory, case.settings, project_id=case.project.id, requested_by="r08-test")
    assert failed.status is recovery.ExportOnlyStatus.BLOCKED
    assert Path(failed.audit_path).is_file()
    async with case.factory() as session:
        project = await session.get(m.ProjectModel, case.project.id)
        assert project.status == "writing" and "fanqie_whole_export_receipt" not in project.metadata_json
    assert await immutable(case) == before
    assert exporter.export_fanqie_short_markdown is native
    retried = await recovery.run_export_only_recovery(case.factory, case.settings, project_id=case.project.id, requested_by="r08-test")
    assert retried.status is recovery.ExportOnlyStatus.COMPLETED, retried.to_dict()


async def test_pg_exception_after_actual_native_checkpoint_does_not_commit_outer_delivery(pg_case, monkeypatch):
    case = pg_case; before = await immutable(case)
    from bestseller.services import fanqie_short_source_export as publisher
    native = publisher.publish_adopted_assembly
    async def after_checkpoint(*args, **kwargs):
        result = await native(*args, **kwargs)
        assert result["whole_book_complete"]
        raise OSError("SYNTHETIC failure after native checkpoint")
    with monkeypatch.context() as patch:
        patch.setattr(publisher, "publish_adopted_assembly", after_checkpoint)
        failed = await recovery.run_export_only_recovery(case.factory, case.settings,
            project_id=case.project.id, requested_by="r08-test")
    assert failed.status is recovery.ExportOnlyStatus.BLOCKED
    assert Path(failed.audit_path).is_file()
    async with case.factory() as session:
        project = await session.get(m.ProjectModel, case.project.id)
        assert project.status == "writing"
        assert "fanqie_whole_export_receipt" not in project.metadata_json
    assert await immutable(case) == before
    retried = await recovery.run_export_only_recovery(case.factory, case.settings,
        project_id=case.project.id, requested_by="r08-test")
    assert retried.status is recovery.ExportOnlyStatus.COMPLETED, retried.to_dict()


async def test_pg_generic_native_settlement_conserves_adopted_rows_and_rebuilds_deleted_file(pg_case):
    case = pg_case
    async with case.factory() as session:
        project = await session.get(m.ProjectModel, case.project.id)
        project.project_type = "linear"
        await session.commit()
    before = await immutable(case)
    result = await recovery.run_export_only_recovery(case.factory, case.settings,
        project_id=case.project.id, requested_by="r08-test")
    assert result.status is recovery.ExportOnlyStatus.COMPLETED, result.to_dict()
    assert await immutable(case) == before
    async with case.factory() as session:
        project = await session.get(m.ProjectModel, case.project.id)
        assert (await read_book_delivery_closure(session, project)).verdict.is_complete
        path = Path(project.metadata_json["completion_export_path"])
    original = path.read_bytes(); path.unlink()
    rebuilt = await recovery.run_export_only_recovery(case.factory, case.settings,
        project_id=case.project.id, requested_by="r08-test")
    assert rebuilt.status is recovery.ExportOnlyStatus.COMPLETED, rebuilt.to_dict()
    assert path.read_bytes() == original and await immutable(case) == before


async def test_pg_concurrent_STOP_is_observed_after_waiting_for_actual_control_lock(pg_case):
    case = pg_case
    async with case.factory() as writer:
        control = await writer.scalar(select(m.BookProductionControlModel).where(
            m.BookProductionControlModel.project_id == case.project.id).with_for_update())
        control.desired_state = "stop"
        await writer.flush()
        task = asyncio.create_task(recovery.run_export_only_recovery(case.factory, case.settings,
            project_id=case.project.id, requested_by="r08-test"))
        waiting = False
        try:
            for _ in range(60):
                async with case.factory() as observer:
                    waiting = bool(await observer.scalar(text("SELECT count(*) FROM pg_stat_activity WHERE "
                        "application_name=:app AND wait_event_type='Lock' AND query LIKE '%book_production_control%' "
                        "AND query LIKE '%FOR UPDATE%'"), {"app": case.schema}))
                if waiting: break
                await asyncio.sleep(.02)
            assert waiting, "Actual second PG transaction must wait on control before source work"
            await writer.commit()
            result = await task
            assert result.status is recovery.ExportOnlyStatus.BLOCKED
            assert result.reason == "operator_halted"
            assert result.audit_path is None
            assert not (Path(case.settings.output.base_dir) / case.project.slug).exists()
        finally:
            if not task.done():
                task.cancel()
                try: await task
                except asyncio.CancelledError: pass


async def test_pg_readonly_preflight_does_not_flush_or_discard_caller_changes(pg_case):
    case = pg_case
    async with case.factory() as session:
        project = await session.get(m.ProjectModel, case.project.id)
        project.metadata_json = {**project.metadata_json, "author_unflushed": "keep in caller"}
        result = await recovery.preflight_export_only_recovery(session, case.settings,
            project_id=case.project.id, requested_by="r08-test")
        assert result.status is recovery.ExportOnlyStatus.BLOCKED
        assert result.reason.endswith("pending_caller_state")
        assert project.metadata_json["author_unflushed"] == "keep in caller"
        assert session.is_modified(project)
        async with case.factory() as independent:
            persisted = await independent.get(m.ProjectModel, case.project.id)
            assert "author_unflushed" not in persisted.metadata_json
        await session.rollback()


async def test_pg_recovery_audit_symlink_cannot_write_into_another_book(pg_case, tmp_path):
    case = pg_case; before = await immutable(case)
    output = Path(case.settings.output.base_dir) / case.project.slug
    output.mkdir(parents=True)
    other = tmp_path / "other-book"
    other.mkdir()
    (output / ".recovery").symlink_to(other, target_is_directory=True)
    result = await recovery.run_export_only_recovery(case.factory, case.settings,
        project_id=case.project.id, requested_by="r08-test")
    assert result.status is recovery.ExportOnlyStatus.BLOCKED
    assert result.reason == "unsafe_audit_path"
    assert result.audit["audit_write_failed"] is True
    assert not list(other.iterdir()) and await immutable(case) == before
