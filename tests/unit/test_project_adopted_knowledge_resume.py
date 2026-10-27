"""Normal project continuation uses real adopted sources and durable receipts."""

from copy import deepcopy
import socket

import pytest
from sqlalchemy import select
from test_adopted_knowledge_resume_control_integrity import configured as resume_configured
from story_bible_read_views_test_support import create_story_bible_read_view_tables
from test_fanqie_segment_resume import forbid, seed as seed_feedback_snapshot
from support.adopted_bible_fixture import seed_empty_bible
from test_adopted_knowledge_lineage import make_accepted_debt, stub_llm

from bestseller.infra.db.models import ChapterModel, ChapterDraftVersionModel, WorkflowRunModel
from bestseller.services import continuity, feedback
from bestseller.services import pipelines
from bestseller.services.adopted_knowledge_resume import resume_project_adopted_knowledge

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@pytest.fixture
async def configured(resume_configured):
    await create_story_bible_read_view_tables(resume_configured[0])
    return resume_configured


async def seed(configured, monkeypatch, **kwargs):
    await seed_feedback_snapshot(configured, monkeypatch, **kwargs)
    await seed_empty_bible(configured, monkeypatch)


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setattr(socket.socket, "connect", forbid)


async def test_resume_completes_only_missing_snapshot_and_second_resume_is_free(
    configured, monkeypatch,
):
    session, settings, project, chapter, draft = configured
    await seed(configured, monkeypatch, snapshot_done=False)
    before_body = draft.content_md
    before_scope = deepcopy(project.metadata_json)
    monkeypatch.setattr(feedback, "complete_text", forbid)
    calls = stub_llm(monkeypatch, continuity, {"facts": []})
    first = await resume_project_adopted_knowledge(session, settings, project=project)
    assert first[1]["knowledge"]["closed"]
    assert len(calls) == 1
    runs = list(await session.scalars(select(WorkflowRunModel)))
    assert len(runs) == 1 and runs[0].workflow_type == "project_adopted_knowledge_resume"
    assert runs[0].status == "completed"
    monkeypatch.setattr(continuity, "complete_text", forbid)
    second = await resume_project_adopted_knowledge(session, settings, project=project)
    assert second == first
    assert len(list(await session.scalars(select(WorkflowRunModel)))) == 1
    assert draft.content_md == before_body and project.metadata_json == before_scope


async def test_candidate_remains_pending_and_cannot_be_skipped_as_prior_prefix(configured):
    session, settings, project, chapter, draft = configured
    chapter.status = "complete"
    chapter.production_state = "ok"
    draft.promotion_state = "candidate"
    await session.commit()
    assert await resume_project_adopted_knowledge(session, settings, project=project) == {}
    with pytest.raises(ValueError, match="prefix_not_adopted"):
        await resume_project_adopted_knowledge(
            session, settings, project=project, before_chapter_number=2,
            require_adopted_prefix=True,
        )
    assert not list(await session.scalars(select(WorkflowRunModel)))
    assert draft.promotion_state == "candidate"
    pending, _ = await pipelines._select_pending_chapters_for_resume(
        session, [chapter], resume_enabled=True, accept_on_stall=True,
    )
    assert pending == [chapter]


async def test_resume_filter_requires_same_body_knowledge_not_complete_label(configured, monkeypatch):
    session, settings, project, chapter, draft = configured
    chapter.status = "complete"
    chapter.production_state = "ok"
    await session.commit()
    pending, _ = await pipelines._select_pending_chapters_for_resume(
        session, [chapter], resume_enabled=True, accept_on_stall=False,
    )
    assert pending == [chapter]
    await seed(configured, monkeypatch)
    pending, _ = await pipelines._select_pending_chapters_for_resume(
        session, [chapter], resume_enabled=True, accept_on_stall=False,
    )
    assert pending == []
    draft.content_md += "来源发生了变化。"
    await session.commit()
    pending, _ = await pipelines._select_pending_chapters_for_resume(
        session, [chapter], resume_enabled=True, accept_on_stall=False,
    )
    assert pending == [chapter]


async def test_missing_chapter_prefix_never_calls_knowledge(configured):
    session, settings, project, _, _ = configured
    with pytest.raises(ValueError, match="prefix_missing_chapter"):
        await resume_project_adopted_knowledge(
            session, settings, project=project, before_chapter_number=3,
            require_adopted_prefix=True,
        )
    assert not list(await session.scalars(select(WorkflowRunModel)))


async def test_numeric_debt_preserves_adoption_without_synthetic_promotion(configured, monkeypatch):
    session, settings, project, chapter, draft = configured
    await make_accepted_debt((session, project, chapter, draft))
    await seed(configured, monkeypatch)
    result = await resume_project_adopted_knowledge(
        session, settings, project=project, before_chapter_number=2,
        require_adopted_prefix=True,
    )
    assert result[1]["source"]["adoption_state"] == "accepted_with_debt"
    assert draft.promotion_state == "under_review"
    assert not list(await session.scalars(select(WorkflowRunModel)))


async def test_interruption_does_not_repeat_completed_feedback(configured, monkeypatch):
    session, settings, project, chapter, draft = configured
    await seed_empty_bible(configured, monkeypatch)
    feedback_calls = stub_llm(monkeypatch, feedback, {})

    async def fail(*args, **kwargs):
        raise RuntimeError("snapshot transport unavailable")

    with monkeypatch.context() as patch:
        patch.setattr(continuity, "extract_chapter_state_snapshot", fail)
        with pytest.raises(RuntimeError, match="snapshot transport unavailable"):
            await resume_project_adopted_knowledge(session, settings, project=project)
    await session.refresh(chapter)
    receipts = deepcopy(chapter.metadata_json["knowledge_feedback_receipts"])
    assert len(feedback_calls) == 1 and receipts
    monkeypatch.setattr(feedback, "complete_text", forbid)
    snapshot_calls = stub_llm(monkeypatch, continuity, {"facts": []})
    assert (await resume_project_adopted_knowledge(session, settings, project=project))[1]["knowledge"]["closed"]
    assert len(snapshot_calls) == 1 and chapter.metadata_json["knowledge_feedback_receipts"] == receipts
    assert [r.status for r in await session.scalars(
        select(WorkflowRunModel).order_by(WorkflowRunModel.created_at)
    )] == ["machine_blocked", "completed"]


async def test_selected_scope_never_extracts_other_project_or_later_candidate(configured, monkeypatch):
    session, settings, project, chapter, draft = configured
    later = ChapterModel(project_id=project.id, chapter_number=2, chapter_goal="以后", metadata_json={})
    session.add(later)
    await session.flush()
    session.add(ChapterDraftVersionModel(
        project_id=project.id, chapter_id=later.id, version_no=1,
        content_md="候选草稿没有采用。", promotion_state="candidate", is_current=True,
    ))
    await session.commit()
    await seed(configured, monkeypatch)
    result = await resume_project_adopted_knowledge(
        session, settings, project=project, chapter_numbers={1},
    )
    assert list(result) == [1]
    assert later.metadata_json == {}



async def test_missing_bible_is_recovered_after_feedback_and_snapshot(configured, monkeypatch):
    from bestseller.services import llm
    from bestseller.services.adopted_knowledge_resume import read_adopted_knowledge
    session, settings, project, chapter, draft = configured
    # Create the extra Bible tables through real empty extraction, then remove
    # only this fixture receipt to reproduce the pre-Bible interruption.
    await seed_empty_bible(configured, monkeypatch)
    chapter.metadata_json = {k: v for k, v in chapter.metadata_json.items() if k != "story_bible_update_receipts"}
    await session.commit()
    await seed_feedback_snapshot(configured, monkeypatch)
    _, legacy = await read_adopted_knowledge(session, project_id=project.id, chapter_id=chapter.id,
        source_chapter_draft_version_id=draft.id, chapter_md=draft.content_md)
    _, strict = await read_adopted_knowledge(session, project_id=project.id, chapter_id=chapter.id,
        source_chapter_draft_version_id=draft.id, chapter_md=draft.content_md, require_bible=True)
    assert legacy["closed"] and not strict["closed"] and not strict["bible_complete"]
    monkeypatch.setattr(feedback, "complete_text", forbid)
    monkeypatch.setattr(continuity, "complete_text", forbid)
    calls = stub_llm(monkeypatch, llm, {})
    first = await resume_project_adopted_knowledge(session, settings, project=project)
    assert first[1]["knowledge"]["closed"] and len(calls) == 1
    assert (await resume_project_adopted_knowledge(session, settings, project=project)) == first
    assert len(calls) == 1
