"""Rejected rewrites preserve ungraded incumbent state across real SQLite flushes."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
from test_revision_source_binding_sqlite import db as db
from test_revision_source_binding_sqlite import seed

from bestseller.infra.db.models import ChapterDraftVersionModel, ChapterModel, ProjectModel, RewriteTaskModel
from bestseller.services import drafts, reviews
from bestseller.services.write_safety_gate import WriteSafetyFinding

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@pytest.mark.parametrize('prior_state', ['pending', 'blocked', 'ok', 'review_unknown'])
async def test_none_gate_preserves_prior_state_after_rejected_rewrite(db, monkeypatch, prior_state):
    await _run_rejected_rewrite(db, monkeypatch, prior_state=prior_state, gate_outcome=None)


@pytest.mark.parametrize('gate_outcome', ['ok', 'blocked'])
async def test_explicit_gate_outcome_still_updates_preserved_state(db, monkeypatch, gate_outcome):
    await _run_rejected_rewrite(db, monkeypatch, prior_state='pending', gate_outcome=gate_outcome)


async def _run_rejected_rewrite(db, monkeypatch, *, prior_state, gate_outcome):
    async with db() as session:
        chapter, current, task = await seed(session)
        chapter.production_state = prior_state
        chapter.target_word_count = 2000
        await session.flush()
        original_text = current.content_md
        original_binding = dict(task.metadata_json)
        original_attempts = task.attempts
        project = ProjectModel(id=chapter.project_id, slug='unrelated-greenhouse',
            title='The weather station', genre='science-fiction', language='en',
            target_word_count=8000, target_chapters=4, invariants_json=None, metadata_json={})
        candidate = 'The botanist checked the glass.\n\n' * 300
        monkeypatch.setattr(reviews, '_load_chapter_context', AsyncMock(
            return_value=(project, chapter, None, [], current)))
        monkeypatch.setattr(drafts, '_prepare_quality_uplift_prompt_blocks', AsyncMock())
        monkeypatch.setattr(reviews, 'render_rewritten_chapter_markdown', lambda *a: candidate)
        monkeypatch.setattr(reviews, '_clean_generated_chapter_text', lambda text, **kw: (text, {}))
        monkeypatch.setattr(reviews, '_audit_chapter_rewrite_candidate', lambda **kw: (
            SimpleNamespace(passed=True, findings=[], to_dict=lambda: {'passed': True}), None))

        async def duplicates(*args, **kwargs):
            if kwargs['content_md'] == original_text:
                return ()
            return (WriteSafetyFinding(source='fixture_duplicate_gate', code='CROSS_CHAPTER_REPETITION',
                severity='critical', message='Repeated candidate paragraphs.', evidence='checked the glass', payload={}),)

        monkeypatch.setattr(reviews, '_collect_post_assembly_duplicate_findings', duplicates)
        provider = AsyncMock(side_effect=AssertionError('No model calls in this regression'))
        monkeypatch.setattr(reviews, 'complete_text', provider)
        # Exercise the real documented None contract (no invariants); typed
        # outcomes additionally verify that the guard preserves existing updates.
        gate = AsyncMock(wraps=drafts._evaluate_chapter_quality_gate) if gate_outcome is None else AsyncMock(return_value=gate_outcome)
        monkeypatch.setattr(reviews, '_evaluate_chapter_quality_gate', gate)
        returned, returned_task = await reviews.rewrite_chapter_from_task(
            session, project.slug, chapter.chapter_number, rewrite_task_id=task.id)
        assert returned is current and returned_task is task
        assert gate.await_count > 0
        assert task.metadata_json['preserved_current_quality_gate_outcome'] == gate_outcome
        assert task.metadata_json['quality_gate_rejected_current_promotion'] is True
        assert task.metadata_json['candidate_quality_gate_outcome'] == 'blocked'
        assert task.metadata_json['took_current_decision'] is False
        provider.assert_not_awaited()
        # The production failure occurred at a later checkpoint flush, rather
        # than at attribute assignment. Execute that boundary and real readback.
        await session.flush()
        await session.commit()
        chapter_id, draft_id, task_id = chapter.id, current.id, task.id
    async with db() as session:
        stored = await session.get(ChapterModel, chapter_id)
        assert stored.production_state == (prior_state if gate_outcome is None else gate_outcome)
        persisted_task = await session.get(RewriteTaskModel, task_id)
        assert persisted_task.status == 'failed'
        assert persisted_task.attempts == original_attempts + 1
        for key, value in original_binding.items():
            assert persisted_task.metadata_json[key] == value
        assert persisted_task.metadata_json['preserved_current_quality_gate_outcome'] == gate_outcome
        drafts_kept = list(await session.scalars(select(ChapterDraftVersionModel).where(
            ChapterDraftVersionModel.chapter_id == chapter_id).order_by(ChapterDraftVersionModel.version_no)))
        assert len(drafts_kept) == 2
        assert drafts_kept[0].id == draft_id and drafts_kept[0].is_current
        assert drafts_kept[0].content_md == original_text
        assert not drafts_kept[1].is_current
        assert str(drafts_kept[1].id) == persisted_task.metadata_json['candidate_chapter_draft_id']
        assert drafts_kept[1].content_md and drafts_kept[1].content_md != original_text
