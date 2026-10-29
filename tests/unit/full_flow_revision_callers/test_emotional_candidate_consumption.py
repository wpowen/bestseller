"""Native callers consume full candidate preservation before changing selection.

Boundary spies isolate orchestration; the separate reader preservation suite
covers native paid/physical custody. These tests do not claim semantic quality.
"""
from uuid import uuid4
import hashlib

import pytest
from test_deslop_revise import _DIRTY, _CLEAN, _fake_result
from test_rewrite_atomic_evidence_sqlite import db as db, runtime as runtime, _execute, PRIMARY

from bestseller.services import deslop_revise, reader_judge, reviews
from bestseller.services.source_bound_planning_content import new_reader_first_binding

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@pytest.mark.parametrize('preserved', [True, False])
async def test_deslop_requires_full_candidate_semantics_before_selection(monkeypatch, preserved):
    project, chapter, parent, workflow, paid = [uuid4() for _ in range(5)]
    checked = []
    observed = []
    async def freeze(*a, **k):
        return {'anchors': []}
    async def verify(*a, **k):
        checked.append(k)
        return {'schema':'reader-revision-preservation.v1', 'preserved': preserved, 'adoption_authority': False, 'candidate_body_sha256': hashlib.sha256(k['candidate_text'].encode()).hexdigest(), 'candidate_llm_run_id': str(k['candidate_llm_run_id'])}
    async def complete(*a):
        result = _fake_result(_CLEAN)
        return result.model_copy(update={'llm_run_id': paid})
    monkeypatch.setattr(reader_judge, 'freeze_reader_revision_anchors', freeze)
    monkeypatch.setattr(reader_judge, 'verify_reader_revision_preservation', verify)
    monkeypatch.setattr(deslop_revise, 'complete_text', complete)
    result = await deslop_revise.revise_prose_deslop(None, object(), content=_DIRTY,
        project_id=project, chapter_id=chapter, source_draft_id=parent,
        workflow_run_id=workflow, rounds=2, target_chars=40,
        observe_candidate=observed.append)
    assert checked == [{'project_id': project, 'chapter_id': chapter,
        'source_draft_id': parent, 'workflow_run_id': workflow,
        'candidate_text': _CLEAN, 'candidate_llm_run_id': paid}]
    assert str(result) == (_CLEAN if preserved else _DIRTY)
    assert observed[0]['reader_revision_preservation']['preserved'] is preserved
    if not preserved:
        assert result.phase_status.stop_reason == 'reader_revision_preservation_rejected'


@pytest.mark.parametrize('stage', ['initial', 'semantic_repair', 'retrofit_repair'])
async def test_rewrite_checks_exact_paid_candidate_before_activation(db, monkeypatch, runtime, stage):
    paid = []
    checked = []
    async def entry(session, slug, number, **kwargs):
        project, chapter, _, _, parent = await reviews._load_chapter_context(session, slug, number)
        project.metadata_json = {'reader_first_planning':new_reader_first_binding('Ordinary family choice.')}
        kwargs['workflow_run_id'] = uuid4()
        previous_complete = reviews.complete_text
        async def complete(*args):
            result = await previous_complete(*args)
            identity = uuid4()
            paid.append(identity)
            return result.model_copy(update={'llm_run_id': identity})
        async def freeze(*a, **k):
            return {'anchors': []}
        async def verify(*a, **k):
            checked.append(k)
            expected_index = len(checked)-1
            assert k['candidate_llm_run_id'] == paid[expected_index]
            return {'schema':'reader-revision-preservation.v1', 'preserved': stage != 'initial' and expected_index == 0, 'adoption_authority': False, 'candidate_body_sha256':hashlib.sha256(k['candidate_text'].encode()).hexdigest(), 'candidate_llm_run_id':str(k['candidate_llm_run_id'])}
        monkeypatch.setattr(reviews, 'complete_text', complete)
        monkeypatch.setattr(reader_judge, 'freeze_reader_revision_anchors', freeze)
        monkeypatch.setattr(reader_judge, 'verify_reader_revision_preservation', verify)
        return await reviews.rewrite_chapter_from_task(session,slug,number,**kwargs)
    if stage == 'initial':
        with pytest.raises(ValueError, match='reader_revision_preservation_rejected'):
            await _execute(db,monkeypatch,runtime,executor=entry)
        assert len(checked) == 1
    else:
        _, saved = await _execute(db,monkeypatch,runtime,
            mode='retrofit' if stage == 'retrofit_repair' else 'solved',executor=entry)
        assert len(checked) == 2
        assert all(row.get('stage') != stage or not row.get('activated')
            for row in saved.task.metadata_json['candidate_evidence_history'])
        assert saved.rows[-1].content_md == PRIMARY

async def test_new_protocol_keeps_candidate_derivable_from_its_paid_run(db,monkeypatch,runtime):
    calls=[]
    async def entry(session,slug,number,**kwargs):
        project,*_=await reviews._load_chapter_context(session,slug,number)
        project.metadata_json={'reader_first_planning':new_reader_first_binding('Ordinary family choice.')}
        kwargs['workflow_run_id']=uuid4()
        async def freeze(*a,**k):
            return {'anchors':[]}
        async def verify(*a,**k):
            assert k['candidate_text']==PRIMARY
            raise ValueError('fixture_stop_after_native_candidate')
        async def localized(*a,**k):
            calls.append('localized')
            return None
        def postprocess(text,**k):
            calls.append('postprocess')
            return text+' invented transformation', {}
        monkeypatch.setattr(reader_judge,'freeze_reader_revision_anchors',freeze)
        monkeypatch.setattr(reader_judge,'verify_reader_revision_preservation',verify)
        monkeypatch.setattr(reviews,'_try_localized_chapter_first_opening_repair',localized)
        monkeypatch.setattr(reviews,'_try_localized_chapter_first_ending_repair',localized)
        monkeypatch.setattr(reviews,'_clean_generated_chapter_text',postprocess)
        return await reviews.rewrite_chapter_from_task(session,slug,number,**kwargs)
    with pytest.raises(ValueError,match='fixture_stop_after_native_candidate'):
        await _execute(db,monkeypatch,runtime,executor=entry)
    assert calls==[]

async def test_bound_new_protocol_cannot_preselect_a_free_local_collapse(monkeypatch):
    project,chapter,parent,workflow=[uuid4() for _ in range(4)]
    collapsed=[]
    sent=[]
    async def freeze(*a,**k):
        return {'anchors':[]}
    async def verify(*a,**k):
        return {'schema':'reader-revision-preservation.v1','preserved':True,
            'adoption_authority':False,'candidate_body_sha256':hashlib.sha256(k['candidate_text'].encode()).hexdigest(),
            'candidate_llm_run_id':str(k['candidate_llm_run_id'])}
    async def complete(*a):
        sent.append(a[-1])
        return _fake_result(_CLEAN)
    def collapse(text):
        collapsed.append(text)
        return _CLEAN
    monkeypatch.setattr(reader_judge,'freeze_reader_revision_anchors',freeze)
    monkeypatch.setattr(reader_judge,'verify_reader_revision_preservation',verify)
    monkeypatch.setattr(deslop_revise,'complete_text',complete)
    monkeypatch.setattr(deslop_revise,'_moment_slice_rate',lambda text:4 if text==_DIRTY else 0)
    monkeypatch.setattr(deslop_revise,'collapse_moment_slice_chains',collapse)
    result=await deslop_revise.revise_prose_deslop(None,object(),content=_DIRTY,
        project_id=project,chapter_id=chapter,source_draft_id=parent,
        workflow_run_id=workflow,rounds=1,target_chars=40)
    assert collapsed==[]
    assert len(sent)==1 and str(result)==_CLEAN
