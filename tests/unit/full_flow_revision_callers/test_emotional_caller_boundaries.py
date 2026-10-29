"""Source anchoring must precede each native editing entry, not follow dispatch.

Boundary spies stop before any model request. Semantic custody itself is tested
separately with real gateway/ledger fixtures; these tests claim only ordering.
"""
from uuid import uuid4
import pytest
from test_rewrite_atomic_evidence_sqlite import db as db, runtime as runtime, _execute
from test_reader_revision_acceptance import fixtures, Session
from test_deslop_revise import _DIRTY
from bestseller.services import reviews, chapter_revision, deslop_revise, reader_judge
from bestseller.services.source_bound_planning_content import new_reader_first_binding
from bestseller.settings import load_settings

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]

class StopBeforeEdit(Exception):
    pass

async def test_rewrite_freezes_native_parent_before_first_editor(db, monkeypatch, runtime):
    seen = []
    async def freeze(session, settings, **ids):
        seen.append(ids)
        raise StopBeforeEdit()
    monkeypatch.setattr(reader_judge, "freeze_reader_revision_anchors", freeze, raising=False)
    async def entry(session, slug, number, **kwargs):
        project, chapter, style, scenes, parent = await reviews._load_chapter_context(session, slug, number)
        project.metadata_json = {"reader_first_planning": new_reader_first_binding("Ordinary family choice.")}
        kwargs["workflow_run_id"] = uuid4()
        return await reviews.rewrite_chapter_from_task(session, slug, number, **kwargs)
    with pytest.raises(StopBeforeEdit):
        await _execute(db, monkeypatch, runtime, executor=entry)
    assert len(seen) == 1
    assert set(seen[0]) == {"project_id", "chapter_id", "source_draft_id", "workflow_run_id"}

async def test_reader_edit_freezes_after_parent_guard_before_candidate_builder(monkeypatch):
    project, chapter, current, task = fixtures()
    project.metadata_json = {"reader_first_planning": new_reader_first_binding("Ordinary family choice.")}
    async def load(*args):
        return project, chapter, current
    async def archives(*args, **kwargs):
        return set()
    seen = []
    async def freeze(session, settings, **ids):
        seen.append(ids)
        raise StopBeforeEdit()
    async def forbidden(*args, **kwargs):
        pytest.fail("UNANCHORED_READER_EDIT_DISPATCH")
    async def workflow_boundary(*args, **kwargs):
        # This probe covers caller order only. The independent native workflow
        # suite covers identity, control, durable custody and paid-row binding.
        from types import SimpleNamespace
        return SimpleNamespace(id=uuid4())
    from bestseller.services import review_source_ordering
    monkeypatch.setattr(review_source_ordering, "archived_rewrite_task_ids", archives)
    monkeypatch.setattr(chapter_revision, "_load_current_chapter_draft", load)
    monkeypatch.setattr(chapter_revision, "_build_revision_candidate", forbidden)
    monkeypatch.setattr(chapter_revision, "_reader_revision_workflow", workflow_boundary)
    monkeypatch.setattr(reader_judge, "freeze_reader_revision_anchors", freeze, raising=False)
    with pytest.raises(StopBeforeEdit):
        await chapter_revision.apply_chapter_revision_task(Session(task, current),load_settings(env={}),
            project.slug,20,rewrite_task_id=task.id,operation="polish")
    assert seen[0]["source_draft_id"] == current.id
    assert task.attempts == 0

async def test_deslop_freezes_bound_source_before_provider(monkeypatch):
    project, chapter, source, workflow = uuid4(), uuid4(), uuid4(), uuid4()
    seen = []
    async def freeze(session, settings, **ids):
        seen.append(ids)
        raise StopBeforeEdit()
    async def forbidden(*args, **kwargs):
        pytest.fail("UNANCHORED_DESLOP_DISPATCH")
    monkeypatch.setattr(reader_judge,"freeze_reader_revision_anchors",freeze,raising=False)
    monkeypatch.setattr(deslop_revise,"complete_text",forbidden)
    with pytest.raises(StopBeforeEdit):
        await deslop_revise.revise_prose_deslop(None,object(),content=_DIRTY,project_id=project,
            chapter_id=chapter,source_draft_id=source,workflow_run_id=workflow,rounds=1,target_chars=40)
    assert seen == [{"project_id":project,"chapter_id":chapter,"source_draft_id":source,
                    "workflow_run_id":workflow}]
