"""Real SQLite adoption/knowledge, with local gateway and writer stubs only."""

from asyncio import CancelledError
from copy import deepcopy
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.schema import CreateTable
from test_adopted_knowledge_lineage import db as db
from test_adopted_knowledge_lineage import make_accepted_debt, stub_llm

from bestseller.infra.db.models import (
    BookProductionControlModel,
    ChapterDraftVersionModel,
    ChapterStateSnapshotModel,
    WorkflowRunModel,
)
from bestseller.services import continuity, feedback
from bestseller.services import fanqie_short_pipeline as fanqie
from bestseller.services.adopted_knowledge_resume import (
    read_adopted_knowledge,
    resume_adopted_knowledge,
)
from bestseller.services.feedback import KNOWLEDGE_SOURCE_KEYS
from bestseller.settings import load_settings

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@pytest.fixture(autouse=True)
def sqlite_only(monkeypatch):
    monkeypatch.delenv("BESTSELLER_LINEAGE_PG", raising=False)


@pytest.fixture
async def configured(db):
    session, project, chapter, draft = db
    connection = await session.connection()
    assert connection.dialect.name == "sqlite"
    for model in (WorkflowRunModel, BookProductionControlModel):
        ddl = str(CreateTable(model.__table__).compile(dialect=connection.dialect))
        await connection.exec_driver_sql(ddl.replace("::jsonb", ""))
    project.status = "writing"
    project.target_chapters = 1
    settings = load_settings().model_copy(deep=True)
    settings.pipeline.enable_chapter_feedback = True
    await session.commit()
    return session, settings, project, chapter, draft


async def seed(configured, monkeypatch, *, feedback_done=True, snapshot_done=True):
    session, settings, project, chapter, draft = configured
    if feedback_done:
        stub_llm(monkeypatch, feedback, {})
        await feedback.extract_chapter_feedback(
            session,
            settings,
            project_id=project.id,
            chapter=chapter,
            chapter_md=draft.content_md,
            source_chapter_draft_version_id=draft.id,
        )
    if snapshot_done:
        stub_llm(monkeypatch, continuity, {"facts": []})
        await continuity.extract_chapter_state_snapshot(
            session,
            settings,
            project_id=project.id,
            chapter=chapter,
            chapter_md=draft.content_md,
            source_chapter_draft_version_id=draft.id,
        )
    await session.commit()


async def read(configured):
    session, _, project, chapter, draft = configured
    return await read_adopted_knowledge(
        session,
        project_id=project.id,
        chapter_id=chapter.id,
        source_chapter_draft_version_id=draft.id,
        chapter_md=draft.content_md,
    )


async def resume(configured, **kwargs):
    session, settings, project, chapter, draft = configured
    return await resume_adopted_knowledge(
        session, settings, project=project, chapter=chapter, draft=draft, **kwargs
    )


async def forbid(*args, **kwargs):
    raise AssertionError("writer/reviewer/completed knowledge must not run")


async def test_closed_adoption_skips_writer_review_and_current_candidate(configured, monkeypatch):
    await seed(configured, monkeypatch)
    from support.adopted_bible_fixture import seed_empty_bible
    await seed_empty_bible(configured, monkeypatch)
    session, settings, project, chapter, draft = configured
    draft.is_current = False
    await session.flush()
    candidate = ChapterDraftVersionModel(
        project_id=project.id,
        chapter_id=chapter.id,
        version_no=2,
        content_md="另一个未采用的候选正文。",
        promotion_state="candidate",
        is_current=True,
    )
    session.add(candidate)
    await session.commit()
    prior = deepcopy(chapter.metadata_json)
    monkeypatch.setattr(fanqie, "run_chapter_pipeline", forbid)
    monkeypatch.setattr(feedback, "complete_text", forbid)
    monkeypatch.setattr(continuity, "complete_text", forbid)
    events = []
    for _ in range(2):
        await fanqie._run_fanqie_segments(
            session,
            settings,
            project,
            requested_by="test",
            progress=lambda event, payload: events.append((event, payload)),
        )
    assert len([e for e, _ in events if e == "fanqie_segment_writing_completed"]) == 2
    assert (await fanqie._load_chapter_drafts(session, project.id))[0][1].id == draft.id
    assert candidate.promotion_state == "candidate" and not draft.is_current
    assert chapter.metadata_json == prior
    assert not list(await session.scalars(select(WorkflowRunModel)))


@pytest.mark.parametrize("missing", ["snapshot", "feedback"])
async def test_segment_resumes_only_missing_item_with_owned_workflow(
    configured, monkeypatch, missing
):
    await seed(
        configured,
        monkeypatch,
        feedback_done=missing != "feedback",
        snapshot_done=missing != "snapshot",
    )
    from support.adopted_bible_fixture import seed_empty_bible
    await seed_empty_bible(configured, monkeypatch)
    session, settings, project, chapter, draft = configured
    body = draft.content_md
    monkeypatch.setattr(fanqie, "run_chapter_pipeline", forbid)
    calls = stub_llm(
        monkeypatch,
        feedback if missing == "feedback" else continuity,
        {} if missing == "feedback" else {"facts": []},
    )
    monkeypatch.setattr(continuity if missing == "feedback" else feedback, "complete_text", forbid)
    await fanqie._run_fanqie_segments(session, settings, project, requested_by="test")
    assert (await read(configured))[1]["closed"]
    await fanqie._run_fanqie_segments(session, settings, project, requested_by="test")
    assert len(calls) == 1 and draft.content_md == body
    runs = list(await session.scalars(select(WorkflowRunModel)))
    assert len(runs) == 1 and runs[0].status == "completed"
    assert runs[0].project_id == project.id and runs[0].scope_type == "chapter"
    assert runs[0].scope_id == chapter.id and calls[0].workflow_run_id == runs[0].id
    assert calls[0].project_id == project.id


@pytest.mark.parametrize(
    "outcome", ["candidate", "adopted_missing_knowledge", "unresolved_feedback"]
)
async def test_fresh_blocker_emits_no_completion_and_does_not_start_next(
    configured, monkeypatch, outcome
):
    session, settings, project, chapter, draft = configured
    draft.promotion_state = "candidate"
    project.target_chapters = 2
    await session.commit()
    writer_calls, events = [], []

    async def writer(*args, **kwargs):
        writer_calls.append(args[3])
        # A misleading transport result must never replace DB truth.
        if outcome != "candidate":
            draft.promotion_state = "promoted"
            await session.flush()
        if outcome == "unresolved_feedback":
            stub_llm(
                monkeypatch,
                feedback,
                {
                    "canon_facts": [
                        {
                            "subject": "钥匙",
                            "predicate": "location",
                            "value": "柜内",
                            "evidence": "正文没有这句话",
                            "confidence": 0.9,
                        }
                    ]
                },
            )
            await feedback.extract_chapter_feedback(
                session,
                settings,
                project_id=project.id,
                chapter=chapter,
                chapter_md=draft.content_md,
                source_chapter_draft_version_id=draft.id,
            )
            stub_llm(monkeypatch, continuity, {"facts": []})
            await continuity.extract_chapter_state_snapshot(
                session,
                settings,
                project_id=project.id,
                chapter=chapter,
                chapter_md=draft.content_md,
                source_chapter_draft_version_id=draft.id,
            )
        return {"final_verdict": "completed"}

    monkeypatch.setattr(fanqie, "run_chapter_pipeline", writer)
    with pytest.raises(ValueError, match=r"fanqie_segment_(not_adopted|knowledge_not_closed):1"):
        await fanqie._run_fanqie_segments(
            session,
            settings,
            project,
            requested_by="test",
            progress=lambda event, payload: events.append((event, payload)),
        )
    assert writer_calls == [1]
    assert [p["segment_number"] for e, p in events if e.endswith("_started")] == [1]
    assert not any(e.endswith("_completed") for e, _ in events)
    assert await session.get(ChapterDraftVersionModel, draft.id) is draft
    assert draft.content_md and not list(await session.scalars(select(WorkflowRunModel)))


@pytest.mark.parametrize("key", KNOWLEDGE_SOURCE_KEYS)
async def test_resume_rejects_any_changed_source_key_before_payment(configured, monkeypatch, key):
    lineage, _ = await read(configured)
    expected = {**lineage, key: "foreign"}
    monkeypatch.setattr(feedback, "complete_text", forbid)
    monkeypatch.setattr(continuity, "complete_text", forbid)
    with pytest.raises(ValueError, match="knowledge_source_changed"):
        await resume(configured, expected_source=expected)
    assert not list(await configured[0].scalars(select(WorkflowRunModel)))


@pytest.mark.parametrize("fault", ["owner", "body", "candidate"])
async def test_read_rejects_foreign_owner_body_or_unadopted_source(configured, fault):
    session, _, project, chapter, draft = configured
    project_id, chapter_id, body = project.id, chapter.id, draft.content_md
    if fault == "owner":
        project_id = uuid4()
    elif fault == "body":
        body += "未采用的尾句。"
    else:
        draft.promotion_state = "candidate"
        await session.commit()
    with pytest.raises(ValueError, match="knowledge_source_changed"):
        await read_adopted_knowledge(
            session,
            project_id=project_id,
            chapter_id=chapter_id,
            source_chapter_draft_version_id=draft.id,
            chapter_md=body,
        )


@pytest.mark.parametrize("failure", ["invalid", "error", "cancel"])
async def test_partial_success_survives_block_or_cancel_and_retry_is_missing_only(
    configured, monkeypatch, failure
):
    session, _, _, chapter, _ = configured
    feedback_calls = stub_llm(monkeypatch, feedback, {})
    if failure == "invalid":
        stub_llm(monkeypatch, continuity, {"not_facts": []})
        error = ValueError
    else:

        async def fail(*args, **kwargs):
            raise CancelledError if failure == "cancel" else RuntimeError("snapshot failed")

        monkeypatch.setattr(continuity, "complete_text", fail)
        error = CancelledError if failure == "cancel" else RuntimeError
    # Snapshot intentionally rethrows runtime errors here; its normal gateway
    # fallback receipt is also exercised by the invalid-extraction case.
    if failure == "error":

        async def fail_service(*args, **kwargs):
            raise RuntimeError("snapshot failed")

        original = continuity.extract_chapter_state_snapshot
        monkeypatch.setattr(continuity, "extract_chapter_state_snapshot", fail_service)
    with pytest.raises(error):
        await resume(configured)
    await session.refresh(chapter)
    receipts = deepcopy(chapter.metadata_json["knowledge_feedback_receipts"])
    runs = list(await session.scalars(select(WorkflowRunModel)))
    assert len(runs) == 1 and runs[0].status == (
        "cancelled" if failure == "cancel" else "machine_blocked"
    )
    assert runs[0].metadata_json["knowledge_resume_progress"]["feedback_complete"]
    assert len(feedback_calls) == 1 and receipts
    if failure == "error":
        monkeypatch.setattr(continuity, "extract_chapter_state_snapshot", original)
    calls = stub_llm(monkeypatch, continuity, {"facts": []})
    monkeypatch.setattr(feedback, "complete_text", forbid)
    assert (await resume(configured))["closed"]
    assert (await resume(configured))["closed"]
    assert len(calls) == 1 and chapter.metadata_json["knowledge_feedback_receipts"] == receipts
    assert [
        r.status
        for r in await session.scalars(
            select(WorkflowRunModel).order_by(WorkflowRunModel.created_at)
        )
    ] == ["cancelled" if failure == "cancel" else "machine_blocked", "completed"]


@pytest.mark.parametrize(
    "status", ["llm_error", "requires_projection_reconciliation", "ok", "absent"]
)
async def test_explicit_bible_receipt_matches_server_contract(configured, monkeypatch, status):
    from bestseller.services.chapter_knowledge_state import adopted_bible_status
    from bestseller.web.server import _adopted_knowledge_evidence

    await seed(configured, monkeypatch)
    session, _, _, chapter, draft = configured
    lineage, _ = await read(configured)
    if status != "absent":
        chapter.metadata_json = {
            **chapter.metadata_json,
            "story_bible_update_receipts": [{"status": status, "source_lineage": lineage}],
        }
        await session.commit()
    snapshot = await session.scalar(select(ChapterStateSnapshotModel))
    bible_state = _adopted_knowledge_evidence(chapter, draft, snapshot, "promoted")[2]
    assert adopted_bible_status(chapter, lineage) == bible_state
    assert (await read(configured))[1]["closed"] == (bible_state in {"closed", "unknown"})


async def test_bible_only_resume_calls_only_existing_bible_service(configured, monkeypatch):
    from bestseller.services import story_bible

    await seed(configured, monkeypatch)
    session, _, _, chapter, _ = configured
    lineage, _ = await read(configured)
    chapter.metadata_json = {
        **chapter.metadata_json,
        "story_bible_update_receipts": [{"status": "llm_error", "source_lineage": lineage}],
    }
    await session.commit()
    monkeypatch.setattr(feedback, "complete_text", forbid)
    monkeypatch.setattr(continuity, "complete_text", forbid)
    calls = []

    async def bible(session, settings, **kwargs):
        assert not session.in_nested_transaction()
        run = await session.get(WorkflowRunModel, kwargs["workflow_run_id"])
        assert run.status == "running" and run.scope_id == chapter.id
        calls.append(kwargs)
        chapter.metadata_json = {
            **chapter.metadata_json,
            "story_bible_update_receipts": [
                *chapter.metadata_json["story_bible_update_receipts"],
                {"status": "empty", "source_lineage": lineage},
            ],
        }

    monkeypatch.setattr(story_bible, "update_story_bible_from_chapter", bible)
    assert (await resume(configured))["closed"]
    assert (await resume(configured))["closed"] and len(calls) == 1


async def test_adopted_debt_is_reused_without_manufacturing_promotion(configured, monkeypatch):
    session, settings, project, chapter, draft = configured
    await make_accepted_debt((session, project, chapter, draft))
    await seed(configured, monkeypatch)
    from support.adopted_bible_fixture import seed_empty_bible
    await seed_empty_bible(configured, monkeypatch)
    monkeypatch.setattr(fanqie, "run_chapter_pipeline", forbid)
    await fanqie._run_fanqie_segments(session, settings, project, requested_by="test")
    assert (await fanqie._load_chapter_drafts(session, project.id))[0][1].id == draft.id
    assert draft.promotion_state == "under_review"
    assert chapter.production_state == "quality_debt"


async def test_assembly_fails_closed_on_candidate_or_missing_knowledge(configured):
    session, _, project, _, draft = configured
    with pytest.raises(ValueError, match="knowledge_not_closed"):
        await fanqie._load_chapter_drafts(session, project.id)
    draft.promotion_state = "candidate"
    await session.commit()
    with pytest.raises(ValueError, match="not_adopted"):
        await fanqie._load_chapter_drafts(session, project.id)


async def test_gateway_source_change_is_blocked_after_payment_without_applying_knowledge(
    configured, monkeypatch
):
    session, _, _, _, draft = configured

    async def change_source(session):
        await session.commit()  # Same transaction release as the real gateway.
        draft.content_md += "网关返回时正文发生变化。"
        await session.flush()

    calls = stub_llm(monkeypatch, feedback, {}, change_source)
    monkeypatch.setattr(continuity, "complete_text", forbid)
    with pytest.raises(ValueError, match="knowledge_source_changed"):
        await resume(configured)
    runs = list(await session.scalars(select(WorkflowRunModel)))
    assert len(calls) == 1 and len(runs) == 1 and runs[0].status == "machine_blocked"
    assert not list(await session.scalars(select(ChapterStateSnapshotModel)))


async def test_stale_snapshot_dependencies_require_snapshot_only_resume(configured, monkeypatch):
    await seed(configured, monkeypatch)
    session, _, _, _, _ = configured
    snapshot = await session.scalar(select(ChapterStateSnapshotModel))
    facts = deepcopy(snapshot.facts)
    facts["snapshot_contract"]["snapshot_dependencies"]["previous_context_sha256"] = "stale"
    snapshot.facts = facts
    await session.commit()
    assert not (await read(configured))[1]["snapshot_complete"]
    monkeypatch.setattr(feedback, "complete_text", forbid)
    calls = stub_llm(monkeypatch, continuity, {"facts": []})
    assert (await resume(configured))["closed"]
    assert len(calls) == 1


async def test_unknown_execution_guard_is_propagated_without_scope_or_counter_reset(
    configured, monkeypatch
):
    from bestseller.services.budget import ExecutionBudgetError

    session, _, project, _, _ = configured
    prior = deepcopy(project.metadata_json)
    error = ExecutionBudgetError("execution_result_unknown", str(project.id))

    async def stop(*args, **kwargs):
        raise error

    monkeypatch.setattr(feedback, "complete_text", stop)
    monkeypatch.setattr(continuity, "complete_text", forbid)
    with pytest.raises(ExecutionBudgetError) as caught:
        await resume(configured)
    assert caught.value is error
    await session.refresh(project)
    assert project.metadata_json == prior
    runs = list(await session.scalars(select(WorkflowRunModel)))
    assert len(runs) == 1 and runs[0].status == "machine_blocked"
    assert runs[0].metadata_json["error_type"] == "ExecutionBudgetError"


async def test_assembly_rejects_missing_segment_even_when_existing_segment_is_closed(
    configured, monkeypatch
):
    await seed(configured, monkeypatch)
    session, _, project, _, _ = configured
    project.target_chapters = 2
    await session.commit()
    with pytest.raises(ValueError, match="fanqie_segments_incomplete"):
        await fanqie._load_chapter_drafts(session, project.id)


@pytest.mark.parametrize("repaired", [False, True])
async def test_public_window_resume_keeps_explicit_bible_gap_pending_until_repaired(
    configured, monkeypatch, tmp_path, repaired
):
    from types import SimpleNamespace

    from bestseller.services import adopted_knowledge_resume as generic
    from bestseller.services import execution_windows as windows
    from bestseller.services import exports, story_bible
    from bestseller.services.creation_configuration import ExecutionWindow

    await seed(configured, monkeypatch)
    session, settings, project, chapter, _ = configured
    lineage, _ = await read(configured)
    chapter.metadata_json = {
        **chapter.metadata_json,
        "story_bible_update_receipts": [{"status": "llm_error", "source_lineage": lineage}],
    }
    await session.commit()
    monkeypatch.setattr(windows, "read_window", lambda _: ExecutionWindow(start=1, end=1))

    async def runnable(*args):
        return None

    helper_calls, exports_called = [], []
    actual_resume = generic.resume_adopted_knowledge

    async def observed_resume(*args, **kwargs):
        helper_calls.append(kwargs)
        return await actual_resume(*args, **kwargs)

    async def bible(*args, **kwargs):
        if repaired:
            chapter.metadata_json = {
                **chapter.metadata_json,
                "story_bible_update_receipts": [
                    *chapter.metadata_json["story_bible_update_receipts"],
                    {"status": "empty", "source_lineage": lineage},
                ],
            }
        return {"extraction_status": "llm_error"}

    async def export(*args, **kwargs):
        state = await windows.window_progress(session, project)
        assert state["window_complete"] and state["sources"][0]["bible_complete"]
        exports_called.append(kwargs)
        return SimpleNamespace(id=uuid4()), tmp_path / "window.md"

    monkeypatch.setattr(windows, "assert_window_runnable", runnable)
    monkeypatch.setattr(generic, "resume_adopted_knowledge", observed_resume)
    monkeypatch.setattr(story_bible, "update_story_bible_from_chapter", bible)
    monkeypatch.setattr(exports, "export_execution_window_markdown", export)
    monkeypatch.setattr(feedback, "complete_text", forbid)
    monkeypatch.setattr(continuity, "complete_text", forbid)
    state = await windows.window_progress(session, project)
    assert state["adopted"] == [1] and state["knowledge_closed"] == []
    assert state["pending"] == [1] and not state["window_complete"]
    assert state["sources"][0]["feedback_complete"]
    assert state["sources"][0]["snapshot_complete"]
    assert not state["sources"][0]["bible_complete"] and not state["sources"][0]["closed"]
    if repaired:
        result = await windows.completed_window_resume(
            session,
            settings,
            project,
            export_markdown=True,
        )
        assert result is not None and result.final_verdict == "execution_window_completed"
        assert result.export_artifact_id is not None and len(exports_called) == 1
    else:
        with pytest.raises(ValueError, match="knowledge_resume_incomplete"):
            await windows.completed_window_resume(
                session,
                settings,
                project,
                export_markdown=True,
            )
        assert not exports_called
    assert len(helper_calls) == 1
    assert helper_calls[0]["workflow_type"] == "execution_window_knowledge_resume"
    await session.refresh(project)
    state = await windows.window_progress(session, project)
    assert state["window_complete"] is repaired
    assert state["pending"] == ([] if repaired else [1])
    runs = list(await session.scalars(select(WorkflowRunModel)))
    recoveries = [run for run in runs if run.workflow_type == "execution_window_knowledge_resume"]
    assert len(recoveries) == 1
    assert recoveries[0].status == ("completed" if repaired else "machine_blocked")
