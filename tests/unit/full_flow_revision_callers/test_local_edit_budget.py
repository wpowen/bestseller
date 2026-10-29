"""Local body replacements cannot bypass the native two-round operation budget."""
from unittest.mock import AsyncMock
import hashlib
import pytest
from bestseller.services import reviews, chapter_revision as revision, review_source_ordering, reader_judge
from bestseller.services.source_bound_planning_content import new_reader_first_binding
from bestseller.settings import load_settings
from test_rewrite_atomic_evidence_sqlite import db as db, runtime as runtime, _execute, TRIMMED
from test_reader_revision_acceptance import fixtures, Session

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]

@pytest.mark.parametrize('mode', ['candidate_trim', 'incumbent_trim'])
async def test_marked_local_trim_does_not_produce_unbudgeted_candidate(db, monkeypatch, runtime, mode):
    # This existing caller fixture spies on editor and quality boundaries. Keep
    # native emotional custody in its actual-ledger suite; here isolate the
    # local-trim prohibition after a separately accepted candidate receipt.
    async def freeze(*args, **kwargs):
        return {'anchors': []}
    async def verify(*args, **kwargs):
        return {'schema': 'reader-revision-preservation.v1', 'preserved': True,
            'adoption_authority': False,
            'candidate_body_sha256': hashlib.sha256(kwargs['candidate_text'].encode()).hexdigest(),
            'candidate_llm_run_id': str(kwargs['candidate_llm_run_id'])}
    monkeypatch.setattr(reader_judge, 'freeze_reader_revision_anchors', freeze)
    monkeypatch.setattr(reader_judge, 'verify_reader_revision_preservation', verify)
    invoked=[]
    async def executor(session, slug, number, **kwargs):
        project,*_ = await reviews._load_chapter_context(session,slug,number)
        project.metadata_json={**(project.metadata_json or {}),
            'reader_first_planning':new_reader_first_binding('A family disagreement.')}
        def trim(*args, **kwargs):
            invoked.append(True)
            return TRIMMED, {'applied':True}
        monkeypatch.setattr(reviews,'_micro_trim_overlength_chapter_text',trim)
        return await reviews.rewrite_chapter_from_task(session,slug,number,**kwargs)
    _,saved=await _execute(db,monkeypatch,runtime,mode=mode,executor=executor)
    assert invoked==[]
    assert all(row.content_md!=TRIMMED for row in saved.rows)
    assert saved.task.metadata_json['quality_gate_rejected_current_promotion'] is True
    assert saved.task.metadata_json['local_edit_budget_denial']['code']=='automatic_local_edit_requires_native_budget'

async def test_marked_humanize_does_not_synthesize_paid_operation(monkeypatch):
    project,chapter,current,task=fixtures()
    project.metadata_json={'reader_first_planning':new_reader_first_binding('A family disagreement.')}
    monkeypatch.setattr(revision,'_load_current_chapter_draft',AsyncMock(return_value=(project,chapter,current)))
    monkeypatch.setattr(review_source_ordering,'archived_rewrite_task_ids',AsyncMock(return_value=set()))
    builder=AsyncMock(return_value=('An unbudgeted new body.',{},None))
    monkeypatch.setattr(revision,'_build_revision_candidate',builder)
    session=Session(task,current)
    result=await revision.apply_chapter_revision_task(session,load_settings(env={}),project.slug,20,
        rewrite_task_id=task.id,operation='humanize')
    assert result['ok'] is False
    assert result['rejected_reason']=='automatic_local_edit_requires_native_budget'
    assert builder.await_count==0
    assert not session.added and not session.executed
    assert chapter.production_state=='quality_debt'
