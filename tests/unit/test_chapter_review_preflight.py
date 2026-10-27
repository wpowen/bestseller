"""Real SQLite checks stop stale retained review reuse before paid processing."""
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from test_adopted_knowledge_lineage import db as db
from test_chapter_critic_semantic_dependencies import semantic_db as semantic_db

from bestseller.infra.db.models import ChapterDraftVersionModel, ChapterModel, ProjectModel
from bestseller.services import pipelines
from bestseller.services.chapter_review_preflight import validate_retained_review_before_processing
from bestseller.settings import load_settings

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@pytest.fixture
async def retained(semantic_db):
    session, project, chapter, scene, design, prior = semantic_db
    settings = load_settings(env={})
    draft = ChapterDraftVersionModel(project_id=project.id, chapter_id=chapter.id,
        version_no=1, content_md='The radio operator leaves the seal intact.',
        is_current=True, promotion_state='candidate')
    session.add(draft)
    await session.flush()
    body_hash = pipelines.chapter_review_body_fingerprint(draft.content_md)['digest']
    key = pipelines._chapter_critic_cycle_id(project.id, chapter.id, body_hash)
    semantic = await pipelines._chapter_critic_semantic_dependencies(session, settings, chapter)
    cycle = {'cycle_id': key, 'state': 'completed', 'source_draft_id': str(draft.id),
        'source_body_hash': body_hash, 'policy_source_hash': pipelines.chapter_review_recovery_source_hash(),
        'semantic_dependencies_sha256': semantic,
        'attempts': [{'attempt': 1, 'state': 'completed', 'outcome': 'unknown'}]}
    chapter.metadata_json = {**chapter.metadata_json, 'chapter_review_attempts_active': 2,
        'chapter_review_recovery_cycles': [deepcopy(cycle)],
        'chapter_critic_cycles': {key: cycle}}
    pipelines._store_chapter_review_recovery_cycle(chapter, cycle)
    await session.flush()
    return session, settings, project, chapter, draft, scene, design, prior


def change_cycle(chapter, **updates):
    metadata = deepcopy(chapter.metadata_json)
    key = metadata['chapter_review_active_cycle']['cycle_id']
    metadata['chapter_critic_cycles'][key].update(updates)
    chapter.metadata_json = metadata


@pytest.mark.parametrize('state', ['prepared', 'running', 'completed'])
@pytest.mark.parametrize('outcome', ['unknown', 'available'])
async def test_legacy_state_labels_need_original_workflow_before_processing(retained, state, outcome):
    session, settings, _project, chapter, draft, *_ = retained
    change_cycle(chapter, state=state, attempts=[{'attempt': 1, 'state': state, 'outcome': outcome}])
    await session.flush()
    before = deepcopy(chapter.metadata_json)
    added, dirty, deleted = list(session.new), list(session.dirty), list(session.deleted)
    # This legacy fixture lacks an original review_workflow_run_id. State labels
    # cannot authorize paid preprocessing, resume, or a fabricated available grade.
    with pytest.raises(ValueError, match='chapter_critic_cycle_workflow_binding_mismatch'):
        await validate_retained_review_before_processing(session, settings, chapter)
    assert chapter.metadata_json == before and draft.is_current
    assert (list(session.new), list(session.dirty), list(session.deleted)) == (added, dirty, deleted)
    assert session.in_transaction()  # No commit, rollback or replacement cycle.


async def test_no_active_cycle_returns_before_any_database_query():
    session = SimpleNamespace(scalar=AsyncMock(side_effect=AssertionError('new generation must not query')))
    chapter = SimpleNamespace(metadata_json={})
    assert await validate_retained_review_before_processing(session, object(), chapter) is None
    session.scalar.assert_not_awaited()


async def test_legitimate_new_body_does_not_try_to_reuse_old_cycle(retained, monkeypatch):
    session, settings, _project, chapter, draft, *_ = retained
    draft.content_md = 'An unrelated observatory opens a second sealed instrument.'
    await session.flush()
    semantic = AsyncMock(side_effect=AssertionError('different body is not cached review reuse'))
    monkeypatch.setattr(pipelines, '_chapter_critic_semantic_dependencies', semantic)
    before = deepcopy(chapter.metadata_json)
    assert await validate_retained_review_before_processing(session, settings, chapter) is None
    semantic.assert_not_awaited()
    assert chapter.metadata_json == before


@pytest.mark.parametrize('binding', ['missing_active_body', 'malformed_active', 'missing_active_cycle',
    'missing_cycle', 'missing_cycles', 'wrong_active_draft', 'wrong_stored_draft', 'wrong_stored_body'])
async def test_anticipated_reuse_rejects_missing_or_invalid_binding(retained, binding):
    session, settings, _project, chapter, _draft, *_ = retained
    metadata = deepcopy(chapter.metadata_json)
    key = metadata['chapter_review_active_cycle']['cycle_id']
    if binding == 'missing_active_body':
        del metadata['chapter_review_active_cycle']['source_body_hash']
    elif binding == 'malformed_active':
        metadata['chapter_review_active_cycle'] = ['not a cycle binding']
    elif binding == 'missing_active_cycle':
        del metadata['chapter_review_active_cycle']['cycle_id']
    elif binding == 'missing_cycle':
        metadata['chapter_critic_cycles'].clear()
    elif binding == 'missing_cycles':
        del metadata['chapter_critic_cycles']
    elif binding == 'wrong_active_draft':
        metadata['chapter_review_active_cycle']['source_draft_id'] = str(uuid4())
    elif binding == 'wrong_stored_draft':
        metadata['chapter_critic_cycles'][key]['source_draft_id'] = str(uuid4())
    else:
        metadata['chapter_critic_cycles'][key]['source_body_hash'] = '0' * 64
    chapter.metadata_json = metadata
    await session.flush()
    before = deepcopy(metadata)
    with pytest.raises(ValueError, match='chapter_critic_cycle_source_mismatch'):
        await validate_retained_review_before_processing(session, settings, chapter)
    assert chapter.metadata_json == before


async def test_current_code_policy_mismatch_is_rejected_before_semantic_work(retained, monkeypatch):
    session, settings, _project, chapter, *_ = retained
    monkeypatch.setattr(pipelines, 'chapter_review_recovery_source_hash', lambda: 'changed code policy')
    semantic = AsyncMock(side_effect=AssertionError('policy must be checked first'))
    monkeypatch.setattr(pipelines, '_chapter_critic_semantic_dependencies', semantic)
    with pytest.raises(ValueError, match='chapter_critic_policy_changed_requires_explicit_recovery'):
        await validate_retained_review_before_processing(session, settings, chapter)
    semantic.assert_not_awaited()


async def test_real_pipeline_entry_rejects_policy_before_first_paid_backfill(retained, monkeypatch):
    session, settings, project, chapter, draft, *_ = retained
    change_cycle(chapter, policy_source_hash='known-obsolete-code-policy')
    await session.flush()
    before = deepcopy(chapter.metadata_json)
    backfill = AsyncMock(side_effect=AssertionError('stale review reached paid backfill'))
    monkeypatch.setattr(pipelines, '_ensure_emotion_kernel_backfill_for_pipeline', backfill)
    # This older fixture exercises current preflight refusal, not native resume
    # admission (real SQL/controls/paid proofs are covered by the native suite).
    from bestseller.services import chapter_review_reconciliation
    admission = AsyncMock(return_value={'status': 'unchanged'})
    monkeypatch.setattr(chapter_review_reconciliation, 'prepare_native_review_resume', admission)
    with pytest.raises(ValueError, match='chapter_critic_policy_changed_requires_explicit_recovery'):
        await pipelines.run_chapter_pipeline.__wrapped__(
            session, settings, project.slug, chapter.chapter_number,
            chapter_first=True, supersede_pending_rewrites=False,
        )
    backfill.assert_not_awaited()
    admission.assert_awaited_once()
    assert chapter.metadata_json == before and draft.is_current


@pytest.mark.parametrize('change', ['configuration', 'approved_plan', 'scene', 'prior_prose', 'missing_hash', 'change_latch'])
async def test_actual_source_or_config_error_stops_before_paid_processing(retained, change):
    session, settings, _project, chapter, _draft, scene, design, prior = retained
    if change == 'configuration':
        settings.quality.thresholds.scene_min_score += 0.01
    elif change == 'approved_plan':
        design.content = {'rules': ['A different current approved rule']}
    elif change == 'scene':
        scene.exit_state = {'door': 'Different story result'}
    elif change == 'prior_prose':
        prior.content_md += 'Unauthorized change to adopted history.'
    elif change == 'missing_hash':
        change_cycle(chapter, semantic_dependencies_sha256=None)
    else:
        change_cycle(chapter, semantic_dependency_change_detected=True)
    await session.flush()
    before = deepcopy(chapter.metadata_json)
    paid_processing = AsyncMock(side_effect=AssertionError('paid style must not run'))
    with pytest.raises(ValueError):
        await validate_retained_review_before_processing(session, settings, chapter)
        await paid_processing()
    paid_processing.assert_not_awaited()
    assert chapter.metadata_json == before


async def test_foreign_same_text_draft_cannot_supply_the_active_binding(retained):
    session, settings, _project, chapter, draft, *_ = retained
    foreign_project = ProjectModel(slug='unrelated-meteorology-' + uuid4().hex, title='Weather station',
        genre='science-fiction', target_word_count=10000, target_chapters=4)
    session.add(foreign_project)
    await session.flush()
    foreign_chapter = ChapterModel(project_id=foreign_project.id, chapter_number=2, chapter_goal='Collect rain')
    session.add(foreign_chapter)
    await session.flush()
    foreign_draft = ChapterDraftVersionModel(project_id=foreign_project.id, chapter_id=foreign_chapter.id,
        version_no=1, content_md=draft.content_md, is_current=True, promotion_state='candidate')
    session.add(foreign_draft)
    await session.flush()
    metadata = deepcopy(chapter.metadata_json)
    metadata['chapter_review_active_cycle']['source_draft_id'] = str(foreign_draft.id)
    chapter.metadata_json = metadata
    await session.flush()
    with pytest.raises(ValueError, match='chapter_critic_cycle_source_mismatch'):
        await validate_retained_review_before_processing(session, settings, chapter)
    assert draft.is_current and foreign_draft.is_current


@pytest.mark.parametrize('active_pointer', ['old_body', 'absent'])
@pytest.mark.parametrize('policy_changed', [False, True])
async def test_normal_current_body_cycle_is_fenced_even_when_recovery_pointer_is_old(retained, monkeypatch, active_pointer, policy_changed):
    session, settings, _project, chapter, draft, *_ = retained
    metadata = deepcopy(chapter.metadata_json)
    if active_pointer == 'old_body':
        metadata['chapter_review_active_cycle']['source_body_hash'] = 'f' * 64
    else:
        metadata.pop('chapter_review_active_cycle')
    chapter.metadata_json = metadata
    await session.flush()
    before = deepcopy(chapter.metadata_json)
    if policy_changed:
        monkeypatch.setattr(pipelines, 'chapter_review_recovery_source_hash', lambda: 'changed reviewer policy')
        with pytest.raises(ValueError, match='chapter_critic_policy_changed_requires_explicit_recovery'):
            await validate_retained_review_before_processing(session, settings, chapter)
    else:
        with pytest.raises(ValueError, match='chapter_critic_cycle_workflow_binding_mismatch'):
            await validate_retained_review_before_processing(session, settings, chapter)
    assert chapter.metadata_json == before and draft.is_current
