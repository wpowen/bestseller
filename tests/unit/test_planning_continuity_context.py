"""Real isolated SQL verifies continuation context and source/time isolation."""
import copy
import hashlib
from uuid import uuid4
import pytest
from bestseller.infra.db.models import CanonFactModel, ChapterDraftVersionModel, TimelineEventModel
from bestseller.services.planning_continuity_context import build_planning_continuity_context
from test_adopted_knowledge_lineage import db  # noqa: F401

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


def lineage(project, chapter, draft):
    return {'project_id': str(project.id), 'chapter_id': str(chapter.id),
        'source_chapter_draft_version_id': str(draft.id),
        'source_body_sha256': hashlib.sha256(draft.content_md.encode()).hexdigest()}


async def test_only_adopted_prior_sources_and_live_projections_are_supplied(db):
    session, project, chapter, draft = db
    proof = lineage(project, chapter, draft)
    for tag, source, current, valid_from in [
        ('valid', proof, True, 1), ('wrong-draft', {**proof, 'source_chapter_draft_version_id': str(uuid4())}, True, 1),
        ('wrong-project', {**proof, 'project_id': str(uuid4())}, True, 1),
        ('retired', proof, False, 1), ('future', proof, True, 2),
    ]:
        session.add(CanonFactModel(project_id=project.id, subject_type='object', subject_label=tag,
            predicate=tag, fact_type='source_observation', value_json={'value':tag,'source_lineage':source},
            source_chapter_id=chapter.id, valid_from_chapter_no=valid_from, is_current=current))
    for tag, status in [('observed', 'current'), ('inferred-old', 'superseded')]:
        session.add(TimelineEventModel(project_id=project.id,chapter_id=chapter.id,event_name=tag,
            event_type='chapter_observation',story_time_label='',story_order=1,
            metadata_json={'source_lineage':proof,'knowledge_lineage_status':status}))
    candidate = ChapterDraftVersionModel(project_id=project.id,chapter_id=chapter.id,version_no=2,
        content_md='这是未采用的改稿，不得进入后续章规划。',promotion_state='candidate',is_current=True)
    draft.is_current = False
    session.add(candidate)
    await session.commit()
    before = copy.deepcopy(chapter.metadata_json)
    result = await build_planning_continuity_context(session, project_id=project.id, before_chapter=2)
    assert result['adopted_sources'][0]['draft_id'] == str(draft.id)
    assert result['recent_adopted_prose'][0]['body'] == draft.content_md
    assert [f['subject'] for f in result['source_bound_canon']] == ['valid']
    assert [e['name'] for e in result['source_bound_timeline']] == ['observed']
    assert chapter.metadata_json == before and not session.dirty


async def test_missing_prior_chapter_cannot_be_planned_as_empty_history(db):
    session, project, _, _ = db
    with pytest.raises(ValueError, match='continuation_prior_chapter_gap'):
        await build_planning_continuity_context(session, project_id=project.id, before_chapter=3)


async def test_changed_adopted_body_changes_continuation_dependency(db):
    session, project, chapter, draft = db
    first = await build_planning_continuity_context(session, project_id=project.id, before_chapter=2)
    session.add(ChapterDraftVersionModel(project_id=project.id,chapter_id=chapter.id,version_no=2,
        content_md='阿青把钥匙交给了船夫。',promotion_state='promoted',is_current=True))
    draft.is_current = False
    draft.promotion_state = 'superseded'
    await session.commit()
    second = await build_planning_continuity_context(session, project_id=project.id, before_chapter=2)
    assert second['context_hash'] != first['context_hash']
