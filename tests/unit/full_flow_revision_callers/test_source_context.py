"""Native caller selectors reach transport; they grant no revision authority."""
import asyncio
from uuid import uuid4
import pytest
from bestseller.services import chapter_automatic_revision_budget as budget
from bestseller.services import deslop_revise as deslop, chapter_revision as revision, reviews
from bestseller.settings import load_settings
from test_deslop_revise import _DIRTY, _CLEAN, _fake_result
from test_reader_revision_acceptance import fixtures, Session
from test_rewrite_atomic_evidence_sqlite import db as db, runtime as runtime, _execute

pytestmark = pytest.mark.unit


def selector(project_id):
    function = getattr(budget, "current_revision_source", None)
    assert callable(function), "REVISION_CALLER_CONTEXT_API_MISSING"
    return function(project_id)


@pytest.mark.asyncio
async def test_real_rewrite_binds_parent_on_every_edit_and_clears_on_exit(db, monkeypatch, runtime):
    observed = []
    async def wrapped(session, slug, chapter_no, **kw):
        old = reviews.complete_text
        async def capture(s, settings, request):
            observed.append((request.project_id, selector(request.project_id)))
            return await old(s, settings, request)
        monkeypatch.setattr(reviews, "complete_text", capture)
        return await reviews.rewrite_chapter_from_task(session, slug, chapter_no, **kw)
    _, saved = await _execute(db, monkeypatch, runtime, executor=wrapped)
    assert observed
    for project_id, value in observed:
        assert value["source_draft_id"] == str(saved.original_id)
        assert value["chapter_id"] == str(saved.result.chapter_id)
        assert selector(project_id) is None


@pytest.mark.asyncio
async def test_deslop_binds_explicit_native_source_without_inheriting_another_task(monkeypatch):
    project_id, chapter_id, source_id = uuid4(), uuid4(), uuid4()
    observed = []
    async def complete(s, settings, request):
        observed.append(selector(project_id))
        assert await asyncio.create_task(asyncio.to_thread(lambda: None)) is None
        return _fake_result(_CLEAN)
    monkeypatch.setattr(deslop, "complete_text", complete)
    await deslop.revise_prose_deslop(None, object(), content=_DIRTY, project_id=project_id,
        chapter_id=chapter_id, source_draft_id=source_id, rounds=1, target_chars=40)
    assert observed == [{"chapter_id": str(chapter_id), "source_draft_id": str(source_id)}]
    assert selector(project_id) is None


@pytest.mark.asyncio
async def test_reader_edit_sets_source_only_after_parent_validation_and_clears_on_exception(monkeypatch):
    project, chapter, current, task = fixtures()
    seen = []
    class StopBeforeProvider(Exception):
        pass
    async def load(*args):
        return project, chapter, current
    async def archives(*args, **kwargs):
        return set()
    async def candidate(*args, **kwargs):
        seen.append(selector(project.id))
        raise StopBeforeProvider()
    from bestseller.services import review_source_ordering
    monkeypatch.setattr(review_source_ordering, "archived_rewrite_task_ids", archives)
    monkeypatch.setattr(revision, "_load_current_chapter_draft", load)
    monkeypatch.setattr(revision, "_build_revision_candidate", candidate)
    with pytest.raises(StopBeforeProvider):
        await revision.apply_chapter_revision_task(Session(task, current), load_settings(env={}),
            project.slug,20,rewrite_task_id=task.id,operation="polish")
    assert seen == [{"chapter_id": str(chapter.id), "source_draft_id": str(current.id)}]
    assert selector(project.id) is None
    assert task.status == "queued" and task.attempts == 0
