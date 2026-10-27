"""Actual platform/window resume fills only a missing exact-body Bible item."""
from copy import deepcopy

import pytest
from support.adopted_bible_fixture import create_bible_fixture_tables
from test_adopted_knowledge_lineage import db as knowledge_db, stub_llm  # noqa: F401
from story_bible_read_views_test_support import create_story_bible_read_view_tables
from test_fanqie_segment_resume import configured as fanqie_case, seed  # noqa: F401
from test_execution_window_knowledge_resume_integrity import configured as window_case  # noqa: F401

from bestseller.services import continuity, execution_windows, fanqie_short_pipeline, feedback, llm
from bestseller.services.adopted_knowledge_resume import read_adopted_knowledge

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@pytest.fixture
async def db(knowledge_db):
    await create_story_bible_read_view_tables(knowledge_db[0])
    return knowledge_db


async def missing_bible(case, monkeypatch):
    await seed(case, monkeypatch)
    session, settings, project, chapter, draft = case
    await create_bible_fixture_tables(session)
    await session.commit()
    kwargs = dict(project_id=project.id,chapter_id=chapter.id,
        source_chapter_draft_version_id=draft.id,chapter_md=draft.content_md)
    _, legacy = await read_adopted_knowledge(session, **kwargs)
    _, strict = await read_adopted_knowledge(session, **kwargs, require_bible=True)
    assert legacy["closed"] and strict["feedback_complete"] and strict["snapshot_complete"]
    assert strict["bible_complete"] is strict["closed"] is False
    before = deepcopy(chapter.metadata_json["knowledge_feedback_receipts"])
    body = draft.content_md
    async def forbidden(*a, **kw):
        raise AssertionError("Paid prose and completed feedback/snapshot must be reused")
    monkeypatch.setattr(feedback, "complete_text", forbidden)
    monkeypatch.setattr(continuity, "complete_text", forbidden)
    monkeypatch.setattr(fanqie_short_pipeline, "run_chapter_pipeline", forbidden)
    calls = stub_llm(monkeypatch, llm, {})
    return kwargs, before, body, calls


async def test_fanqie_assembly_and_resume_require_explicit_bible(fanqie_case, monkeypatch):  # noqa: F811
    kwargs, before, body, calls = await missing_bible(fanqie_case, monkeypatch)
    session, settings, project, chapter, draft = fanqie_case
    with pytest.raises(ValueError,match="knowledge_not_closed"):
        await fanqie_short_pipeline._load_chapter_drafts(session, project.id)
    for _ in range(2):
        await fanqie_short_pipeline._run_fanqie_segments(session,settings,project,requested_by="test")
    assert len(calls) == 1
    assert (await read_adopted_knowledge(session, **kwargs, require_bible=True))[1]["closed"]
    assert (await fanqie_short_pipeline._load_chapter_drafts(session,project.id))[0][1].id == draft.id
    assert draft.content_md == body and chapter.metadata_json["knowledge_feedback_receipts"] == before


async def test_window_progress_and_resume_require_explicit_bible(window_case, monkeypatch):  # noqa: F811
    kwargs, before, body, calls = await missing_bible(window_case, monkeypatch)
    session, settings, project, chapter, draft = window_case
    state = await execution_windows.window_progress(session, project)
    assert state["adopted"] == [1] and state["knowledge_closed"] == []
    assert state["sources"][0]["bible_complete"] is False
    for _ in range(2):
        await execution_windows.resume_window_knowledge(session,settings,project,
            await execution_windows.window_progress(session, project))
    state = await execution_windows.window_progress(session, project)
    assert state["knowledge_closed"] == [1] and state["window_complete"]
    assert len(calls) == 1
    assert (await read_adopted_knowledge(session, **kwargs, require_bible=True))[1]["closed"]
    assert draft.content_md == body and chapter.metadata_json["knowledge_feedback_receipts"] == before
