"""WP4 consumers and transaction owners, with isolated SQLite readback.

SQLite executes real ORM SQL through a stdlib driver facade; it does not
establish PostgreSQL lock semantics or real model/prose acceptance.
"""

from contextlib import asynccontextmanager
from copy import deepcopy
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session
from sqlalchemy.schema import CreateTable
from typer.testing import CliRunner

from bestseller.infra.db.models import (
    ChapterDraftVersionModel, ChapterModel, ChapterQualityReportModel,
    LlmRunModel, ProjectModel, RewriteTaskModel, SceneCardModel,
    SceneDraftVersionModel, WorkflowRunModel,
)
from bestseller.services import drafts, repair, reviews
from bestseller.services.quality_report_claim import graded_text_fingerprint
from bestseller.settings import load_settings

pytestmark = pytest.mark.unit


@compiles(JSONB, "sqlite")
def _sqlite_jsonb(_type, _compiler, **_kwargs):
    return "JSON"


class SQLiteSession:
    def __init__(self, engine):
        self.orm = Session(engine, expire_on_commit=False)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        self.orm.close()

    def add(self, row):
        self.orm.add(row)

    async def get(self, model, key):
        return self.orm.get(model, key)

    async def scalar(self, query):
        return self.orm.scalar(query)

    async def scalars(self, query):
        return self.orm.scalars(query)

    async def execute(self, query):
        return self.orm.execute(query)

    async def flush(self):
        self.orm.flush()

    async def commit(self):
        self.orm.commit()

    async def rollback(self):
        self.orm.rollback()


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "wp4_closure_test.sqlite3"
    assert "test" in path.name
    engine = create_engine(f"sqlite:///{path}")
    with engine.begin() as connection:
        for model in (
            ProjectModel, ChapterModel, ChapterDraftVersionModel, ChapterQualityReportModel,
            RewriteTaskModel, SceneCardModel, SceneDraftVersionModel, WorkflowRunModel,
            LlmRunModel,
        ):
            # PostgreSQL JSON defaults have the same value without their cast
            # in this isolated SQLite schema.
            ddl = str(CreateTable(model.__table__).compile(dialect=engine.dialect))
            connection.exec_driver_sql(ddl.replace("::jsonb", ""))
    yield lambda: SQLiteSession(engine)
    engine.dispose()


async def seed(db, *, scene=False, repair_history=False):
    async with db() as session:
        project = ProjectModel(
            id=uuid4(), slug="another-book", title="Another book", genre="mystery",
            language="en", target_word_count=24000, target_chapters=10,
            status="draft", metadata_json={},
        )
        chapter = ChapterModel(
            id=uuid4(), project_id=project.id, chapter_number=3, chapter_goal="Original goal",
            target_word_count=2400, metadata_json={}, production_state="blocked",
        )
        parent = chapter
        if scene:
            parent = SceneCardModel(
                id=uuid4(), project_id=chapter.project_id, chapter_id=chapter.id,
                scene_number=1, scene_type="setup", target_word_count=800,
                metadata_json={},
            )
            session.add(parent)
        cls = SceneDraftVersionModel if scene else ChapterDraftVersionModel
        draft = cls(
            id=uuid4(), project_id=chapter.project_id, version_no=3 if repair_history else 1,
            content_md="The original text, 42.", word_count=2400, is_current=True,
            **({"scene_card_id": parent.id} if scene else {"chapter_id": parent.id}),
        )
        task = RewriteTaskModel(
            id=uuid4(), project_id=chapter.project_id, trigger_type="scene_review" if scene else "chapter_review",
            trigger_source_id=parent.id, rewrite_strategy="clarify", instructions="Clarify this text.",
            attempts=2, status="pending", metadata_json=reviews.rewrite_source_binding(draft),
        )
        for row in (project, chapter, draft, task):
            session.add(row)
        if repair_history:
            await seed_repair_history(session, chapter)
        await session.commit()
        return chapter, parent, draft, task


async def load_project(db, project_id):
    async with db() as session:
        return await session.get(ProjectModel, project_id)


async def seed_repair_history(session, chapter):
    """Two persisted historical actions, never a review of the current body.

    These are explicitly local ORM fixtures without provider dispatch, usage,
    credit or paid-report receipts. Report multiplicity alone cannot establish
    a plateau under the real source-bound history reader.
    """
    parent = ChapterDraftVersionModel(
        id=uuid4(), project_id=chapter.project_id, chapter_id=chapter.id,
        version_no=0, content_md="Initial unreleased text, 39.", word_count=2400,
        is_current=False, promotion_state="candidate",
    )
    session.add(parent)
    for version in (1, 2):
        body = f"Earlier unreleased text, {39 + version}."
        workflow = WorkflowRunModel(
            id=uuid4(), project_id=chapter.project_id, scope_type="chapter",
            scope_id=chapter.id, workflow_type="chapter_pipeline", status="completed",
            metadata_json={},
        )
        task = RewriteTaskModel(
            id=uuid4(), project_id=chapter.project_id, trigger_type="chapter_review",
            trigger_source_id=chapter.id, rewrite_strategy="clarify",
            instructions="Clarify the historical text.", status="completed", attempts=1,
            metadata_json=reviews.rewrite_source_binding(parent),
        )
        run = LlmRunModel(
            id=uuid4(), project_id=chapter.project_id, workflow_run_id=workflow.id,
            logical_role="editor", provider="fixture", model_name="fixture-no-provider",
            prompt_template="chapter_rewrite", finish_reason="stop",
            metadata_json={"chapter_number": chapter.chapter_number,
                           "rewrite_task_id": str(task.id)},
        )
        historical = ChapterDraftVersionModel(
            id=uuid4(), project_id=chapter.project_id, chapter_id=chapter.id,
            version_no=version, content_md=body, word_count=2400,
            is_current=False, promotion_state="candidate", llm_run_id=run.id,
        )
        report = ChapterQualityReportModel(
            id=uuid4(), chapter_id=chapter.id, blocks_write=True,
            report_json={"blocking_codes": ["TIMELINE_INCONSISTENT"],
                         "graded": graded_text_fingerprint(body)},
        )
        for row in (workflow, task, run, historical, report):
            session.add(row)
        parent = historical


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["mismatch", "legacy", "missing_draft", "current"])
async def test_both_report_consumers_require_shipped_body(db, kind):
    chapter, _, draft, _ = await seed(db)
    payload = {"blocking_codes": ["TIMELINE_INCONSISTENT"]}
    if kind != "legacy":
        payload["graded"] = graded_text_fingerprint(
            "A different candidate" if kind == "mismatch" else draft.content_md
        )
    async with db() as session:
        session.add(ChapterQualityReportModel(
            id=uuid4(), chapter_id=chapter.id, blocks_write=True, report_json=payload,
        ))
        if kind == "missing_draft":
            (await session.get(ChapterDraftVersionModel, draft.id)).is_current = False
        await session.commit()
    async with db() as session:
        row = await session.scalar(select(ChapterQualityReportModel))
        resolved = await drafts._resolve_shipped_report_payload(session, chapter, row)
        report, claimed = await repair._load_latest_chapter_quality_report(
            session, chapter, current_text=None if kind == "missing_draft" else draft.content_md,
        )
        if kind == "current":
            assert resolved == payload and claimed and report.id == row.id
        else:
            assert resolved == {} and report is None and not claimed


@pytest.mark.asyncio
async def test_report_lookup_failure_does_not_fallback_to_newest():
    class Broken:
        async def scalar(self, _query):
            raise RuntimeError("isolated lookup failed")

        async def scalars(self, _query):
            raise RuntimeError("isolated lookup failed")

    chapter = SimpleNamespace(id=uuid4(), chapter_number=4)
    old = SimpleNamespace(report_json={"blocking_codes": ["OLD_ADVICE"]})
    assert await drafts._resolve_shipped_report_payload(Broken(), chapter, old) == {}
    assert await repair._load_latest_chapter_quality_report(
        Broken(), chapter, current_text="new"
    ) == (None, False)


@pytest.mark.asyncio
async def test_auto_repair_does_not_convert_an_old_report_into_new_hints(db, monkeypatch):
    chapter, _, draft, _ = await seed(db)
    monkeypatch.setattr(drafts, "_resolve_chapter_scene_rounds_budget", lambda: 0)
    monkeypatch.setattr(drafts, "_pipeline_settings_for_plateau", lambda: SimpleNamespace(rework_plateau_patience=0))
    async with db() as session:
        session.add(ChapterQualityReportModel(
            id=uuid4(), chapter_id=chapter.id, blocks_write=True,
            report_json={"blocking_codes": ["TIMELINE_INCONSISTENT"],
                         "graded": graded_text_fingerprint("Unadopted candidate")},
        ))
        await session.commit()
    async with db() as session:
        current = await session.get(ChapterModel, chapter.id)
        result = await drafts.maybe_prepare_chapter_auto_repair(
            session, project=SimpleNamespace(id=chapter.project_id, metadata_json={}, language="en"),
            chapter=current, repairable_codes=("TIMELINE_INCONSISTENT",),
        )
        assert result == (False, ())
        assert current.production_state == "blocked"  # absence is not a passing review
        assert not current.metadata_json.get("auto_repair_in_progress")


@pytest.mark.asyncio
@pytest.mark.parametrize("late", [False, True])
@pytest.mark.parametrize("scene", [False, True])
async def test_rejection_survives_outer_rollback_without_committing_other_work(db, late, scene):
    chapter, parent, draft, task = await seed(db, scene=scene)
    cls = SceneDraftVersionModel if scene else ChapterDraftVersionModel
    original = deepcopy(task.metadata_json)
    async with db() as session:
        changed = await session.get(cls, draft.id)
        changed.content_md = "Current edition, 43."
        await session.commit()
    async with db() as session:
        transient = await session.get(ChapterModel, chapter.id)
        transient.chapter_goal = "Unrelated user edit, must roll back"
        active_task = await session.get(RewriteTaskModel, task.id)
        current = await session.get(cls, draft.id)
        with pytest.raises(reviews.RewriteSourceRejected) as caught:
            if late:
                await reviews._recheck_rewrite_parent(
                    session, active_task, draft, parent=parent, scene=scene,
                    candidate_text="Retained candidate from edition 42.",
                )
            else:
                await reviews._validate_rewrite_task_parent(session, active_task, current)
        rejection = caught.value
        await session.rollback()
    async with db() as session:
        assert (await session.get(RewriteTaskModel, task.id)).status == "pending"
        assert await reviews.persist_rewrite_rejection(session, rejection)
        await session.commit()
    # Idempotent recovery merges, never resets source or cumulative attempts.
    async with db() as session:
        assert await reviews.persist_rewrite_rejection(session, rejection)
        await session.commit()
    async with db() as session:
        stored = await session.get(RewriteTaskModel, task.id)
        assert stored.status == "stale" and stored.attempts == 2
        assert all(stored.metadata_json[key] == value for key, value in original.items())
        assert len(stored.metadata_json["source_binding_rejections"]) == 1
        assert (await session.get(ChapterModel, chapter.id)).chapter_goal == "Original goal"
        body = await session.get(cls, draft.id)
        assert body.content_md == "Current edition, 43." and body.is_current
        assert len(list(await session.scalars(select(cls)))) == 1
        if late:
            assert stored.metadata_json["unpromoted_stale_candidate"]["content_md"] == "Retained candidate from edition 42."


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["completed", "rebound"])
async def test_recovery_merges_audit_without_overwriting_later_task_changes(db, change):
    _, _, draft, task = await seed(db)
    async with db() as session:
        row = await session.get(RewriteTaskModel, task.id)
        error = reviews._reject_rewrite_source(row, "stale source binding", reason="fixture")
        await session.rollback()
    async with db() as session:
        row = await session.get(RewriteTaskModel, task.id)
        row.metadata_json = {**row.metadata_json, "later_note": "preserve"}
        if change == "completed":
            row.status = "completed"
        else:
            row.instructions = "A separately rebased review"
            row.metadata_json = {**row.metadata_json, "parent_draft_id": str(uuid4())}
        await session.commit()
    async with db() as session:
        assert await reviews.persist_rewrite_rejection(session, error)
        await session.commit()
    async with db() as session:
        row = await session.get(RewriteTaskModel, task.id)
        assert row.status == ("completed" if change == "completed" else "pending")
        assert row.metadata_json["later_note"] == "preserve"
        assert len(row.metadata_json["source_binding_rejections"]) == 1
        assert "source_binding_rejection" not in row.metadata_json


@pytest.mark.asyncio
async def test_uncommitted_task_is_not_fabricated_by_audit_recovery(db):
    task = RewriteTaskModel(id=uuid4(), project_id=uuid4(), status="pending", metadata_json={})
    error = reviews._reject_rewrite_source(task, "stale source binding", reason="fixture")
    async with db() as session:
        assert not await reviews.persist_rewrite_rejection(session, error)
        assert list(await session.scalars(select(RewriteTaskModel))) == []


@pytest.mark.asyncio
async def test_execution_added_instructions_do_not_break_recovery_identity(db):
    chapter, _, draft, task = await seed(db)
    async with db() as session:
        active = await session.get(RewriteTaskModel, task.id)
        current = await session.get(ChapterDraftVersionModel, draft.id)
        await reviews._validate_rewrite_task_parent(session, active, current)
        active.instructions += "\nConvergence instructions appended during execution."
        error = reviews._reject_rewrite_source(active, "stale source binding", reason="fixture")
        await session.rollback()
    async with db() as session:
        assert await reviews.persist_rewrite_rejection(session, error)
        await session.commit()
    async with db() as session:
        stored = await session.get(RewriteTaskModel, task.id)
        assert stored.status == "stale" and stored.instructions == task.instructions


@pytest.mark.asyncio
async def test_identity_check_does_not_turn_unclaimed_quality_report_into_release(db):
    from bestseller.services.identity_guard import CharacterIdentity

    chapter, _, draft, _ = await seed(db)
    async with db() as session:
        row = await session.get(ChapterModel, chapter.id)
        row.metadata_json = {"blocked_by_write_safety_gate": True, "write_safety_block_code": "pronoun_mismatch"}
        body = await session.get(ChapterDraftVersionModel, draft.id)
        body.content_md = "Kade Mercer opened the file. He read it."
        session.add(ChapterQualityReportModel(
            id=uuid4(), chapter_id=chapter.id, blocks_write=True,
            report_json={"blocking_codes": ["OLD_TIMELINE_ADVICE"]},
        ))
        await session.commit()
    async with db() as session:
        row = await session.get(ChapterModel, chapter.id)
        blocked = await repair._heal_chapter_gate_state_before_repair(
            session, row, await session.get(ChapterDraftVersionModel, draft.id),
            project=SimpleNamespace(id=chapter.project_id), settings=load_settings(),
            identity_registry=[CharacterIdentity(name="Kade Mercer", gender="male", pronoun_set_en="he/him")],
            language="en",
        )
        assert blocked and row.production_state == "blocked"
        assert row.metadata_json["resolved_write_safety_block"]["code"] == "pronoun_mismatch"
        assert row.metadata_json["quality_report_revalidation_required"]["reason"] == "unfingerprinted"
        assert "OLD_TIMELINE_ADVICE" not in str(row.metadata_json)
        await session.commit()
    # A new, exactly bound review supersedes the unknown historical state.
    report_id = uuid4()
    async with db() as session:
        body = await session.get(ChapterDraftVersionModel, draft.id)
        session.add(ChapterQualityReportModel(
            id=report_id, chapter_id=chapter.id, blocks_write=False,
            report_json={"graded": graded_text_fingerprint(body.content_md), "blocking_codes": []},
        ))
        await session.commit()
    async with db() as session:
        row = await session.get(ChapterModel, chapter.id)
        blocked = await repair._heal_chapter_gate_state_before_repair(
            session, row, await session.get(ChapterDraftVersionModel, draft.id),
            project=SimpleNamespace(id=chapter.project_id), settings=load_settings(),
            identity_registry=[], language="en",
        )
        assert not blocked and row.production_state == "ok"
        assert "quality_report_revalidation_required" not in row.metadata_json
        resolved = row.metadata_json["quality_report_revalidation_resolved"]
        assert resolved["previous"]["reason"] == "unfingerprinted"
        assert resolved["report_id"] == str(report_id)


@pytest.mark.asyncio
@pytest.mark.parametrize("stop", ["budget", "plateau"])
async def test_repair_stop_with_unknown_report_never_labels_body_clean(db, monkeypatch, stop):
    chapter, _, draft, _ = await seed(db, repair_history=stop == "plateau")
    monkeypatch.setattr(drafts, "_resolve_chapter_scene_rounds_budget", lambda: 1 if stop == "budget" else 0)
    monkeypatch.setattr(drafts, "_pipeline_settings_for_plateau", lambda: SimpleNamespace(rework_plateau_patience=1 if stop == "plateau" else 0))
    async with db() as session:
        session.add(SceneCardModel(
            id=uuid4(), project_id=chapter.project_id, chapter_id=chapter.id,
            scene_number=1, scene_type="setup", target_word_count=800,
            metadata_json={"scene_auto_repair_total_attempts": 1},
        ))
        for _ in range(2):
            session.add(ChapterQualityReportModel(
                id=uuid4(), chapter_id=chapter.id, blocks_write=True,
                report_json={"blocking_codes": [], "graded": graded_text_fingerprint("Not shipped")},
            ))
        await session.commit()
    async with db() as session:
        row = await session.get(ChapterModel, chapter.id)
        triggered, codes = await drafts.maybe_prepare_chapter_auto_repair(
            session, project=SimpleNamespace(id=chapter.project_id, metadata_json={}),
            chapter=row, repairable_codes=("TIMELINE_INCONSISTENT",),
        )
        assert not triggered and codes == ()
        assert row.production_state == "blocked"
        assert not row.metadata_json.get("rounds_budget_stopped_while_clean")
        assert row.metadata_json["quality_report_revalidation_required"]["reason"] == "no_current_report"


@pytest.mark.asyncio
@pytest.mark.parametrize("report_kind", ["legacy", "other_body", "none"])
async def test_length_revalidation_cannot_clear_independent_blocks(db, report_kind):
    chapter, _, draft, _ = await seed(db)
    async with db() as session:
        row = await session.get(ChapterModel, chapter.id)
        row.metadata_json = {
            "blocked_by_repair_audit": "current_chapter_length_out_of_range",
            "repair_audit_min_words": 1800, "repair_audit_max_words": 3500,
            "auto_repair_last_block_codes": ["LENGTH_OVER", "TIMELINE_INCONSISTENT"],
            "quality_gate_block_codes": ["CHAPTER_LENGTH_BLOCK_HIGH", "TIMELINE_INCONSISTENT"],
        }
        if report_kind != "none":
            payload = {"blocking_codes": ["OLD_REPORT_ADVICE"]}
            if report_kind == "other_body":
                payload["graded"] = graded_text_fingerprint("Another edition")
            session.add(ChapterQualityReportModel(
                id=uuid4(), chapter_id=chapter.id, blocks_write=True, report_json=payload,
            ))
        await session.commit()
    async with db() as session:
        row = await session.get(ChapterModel, chapter.id)
        blocked = await repair._heal_chapter_gate_state_before_repair(
            session, row, await session.get(ChapterDraftVersionModel, draft.id),
            project=SimpleNamespace(id=chapter.project_id), settings=load_settings(),
            identity_registry=[], language="en",
        )
        assert blocked and row.production_state == "blocked"
        assert row.metadata_json["quality_gate_block_codes"] == ["TIMELINE_INCONSISTENT"]
        assert row.metadata_json["auto_repair_last_block_codes"] == ["TIMELINE_INCONSISTENT"]
        assert "blocked_by_repair_audit" not in row.metadata_json
        assert "OLD_REPORT_ADVICE" not in str(row.metadata_json)
        assert row.metadata_json["resolved_repair_audit_length_block"]["previous"]["auto_repair_last_block_codes"] == ["LENGTH_OVER", "TIMELINE_INCONSISTENT"]
        await session.commit()
    async with db() as session:
        row = await session.get(ChapterModel, chapter.id)
        assert row.production_state == "blocked"
        assert row.metadata_json["quality_gate_block_codes"] == ["TIMELINE_INCONSISTENT"]


@pytest.mark.asyncio
async def test_project_repair_typed_rejection_rolls_back_before_recovery(db, monkeypatch):
    from bestseller.infra.db import session as sessions

    chapter, _, draft, task = await seed(db)
    project = await load_project(db, chapter.project_id)
    workflow_id = uuid4()
    recovery_engines = []

    async def get_project(*_args):
        return project

    async def workflow(session, **kwargs):
        row = WorkflowRunModel(
            id=workflow_id, project_id=project.id, workflow_type="project_repair",
            status=kwargs["status"].value, current_step=kwargs["current_step"],
            metadata_json=kwargs["metadata"],
        )
        session.add(row)
        await session.flush()
        return row

    async def no_restoration(*_args):
        return []

    @asynccontextmanager
    async def recovery_scope():
        async with db() as recovery:
            yield recovery
            await recovery.commit()

    monkeypatch.setattr(repair, "get_project_by_slug", get_project)
    monkeypatch.setattr(repair, "create_workflow_run", workflow)
    monkeypatch.setattr(repair, "_restore_best_chapter_drafts", no_restoration)
    def no_global_factory():
        raise AssertionError("One-shot CLI has no global server factory")

    # Adapt only the unavailable async SQLite driver/factory. The recovery
    # routing, task update, workflow update, commit and readback remain real.
    def sqlite_factory(*, bind, **_kwargs):
        recovery_engines.append(bind)
        return SimpleNamespace(begin=recovery_scope)

    monkeypatch.setattr(sessions, "get_server_session", no_global_factory)
    monkeypatch.setattr(repair, "async_sessionmaker", sqlite_factory)
    async with db() as session:
        session.bind = session.orm.bind
        active_task = await session.get(RewriteTaskModel, task.id)

        def reject_at_audit(*_args):
            session.orm.get(ChapterModel, chapter.id).chapter_goal = "Rolled-back work"
            raise reviews._reject_rewrite_source(active_task, "invalid source binding", reason="fixture")

        monkeypatch.setattr(repair, "_project_repair_source_artifact_audit", reject_at_audit)
        with pytest.raises(reviews.RewriteSourceRejected):
            await repair.run_project_repair(session, load_settings(), project.slug, include_pending_rewrite_tasks=False)
    async with db() as session:
        assert (await session.get(RewriteTaskModel, task.id)).status == "stale"
        assert (await session.get(ChapterModel, chapter.id)).chapter_goal == "Original goal"
        workflow_row = await session.get(WorkflowRunModel, workflow_id)
        assert workflow_row.status == "failed"
        assert workflow_row.current_step == "collect_pending_rewrite_tasks"
        assert workflow_row.error_message == "invalid source binding"
    assert len(recovery_engines) == 2 and recovery_engines[0] is recovery_engines[1]


@pytest.mark.parametrize("scene", [False, True])
def test_cli_rewrite_rolls_back_then_persists_only_rejection(db, monkeypatch, scene):
    import asyncio
    from bestseller.cli import main

    chapter, parent, draft, task = asyncio.run(seed(db, scene=scene))
    scopes = []

    @asynccontextmanager
    async def scope(_settings):
        async with db() as session:
            scopes.append(session)
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    async def reject(session, *_args, **_kwargs):
        row = await session.get(RewriteTaskModel, task.id)
        (await session.get(ChapterModel, chapter.id)).chapter_goal = "Do not commit"
        error = reviews._reject_rewrite_source(
            row, "Rewrite source binding changed", reason="parent_changed_before_commit",
            unpromoted_stale_candidate={"content_md": "Unpromoted candidate"},
        )
        await session.flush()
        raise error

    monkeypatch.setattr(main, "session_scope", scope)
    monkeypatch.setattr(main, "rewrite_scene_from_task" if scene else "rewrite_chapter_from_task", reject)
    args = ["scene" if scene else "chapter", "rewrite", "unrelated-story", "3"]
    if scene:
        args.append("1")
    result = CliRunner().invoke(main.app, args)
    assert result.exit_code != 0 and isinstance(result.exception, reviews.RewriteSourceRejected)
    assert len(scopes) == 2

    async def readback():
        async with db() as session:
            stored = await session.get(RewriteTaskModel, task.id)
            assert stored.status == "stale" and stored.attempts == 2
            assert stored.metadata_json["unpromoted_stale_candidate"]["content_md"] == "Unpromoted candidate"
            assert (await session.get(ChapterModel, chapter.id)).chapter_goal == "Original goal"

    asyncio.run(readback())
