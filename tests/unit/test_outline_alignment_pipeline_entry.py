"""Actual pipeline entry dispatches bound plan repair before old-body/style work."""
from types import SimpleNamespace
from uuid import uuid4
import pytest
from test_fanqie_materialized_plan_resume import (  # noqa: F401
    integrated_modules, materialized_case, plan_case, store,
)
from test_full_outline_source_selection import install_markers
from bestseller.services import pipelines
from bestseller.services import rewrite_outline_dispatch as dispatch
from bestseller.services.rewrite_patch import text_hash

pytestmark=[pytest.mark.unit,pytest.mark.asyncio]


@pytest.mark.parametrize('state',['prepared','blocked','not_applicable'])
async def test_original_entry_orders_source_alignment_before_retained_review_or_style(plan_case,monkeypatch,state):  # noqa: F811
    c=plan_case
    await install_markers(c)
    events=[]
    owner=uuid4()
    def stop(*args,**kwargs):
        events.append('ordinary_entry');raise RuntimeError('ordinary_entry_reached')
    monkeypatch.setattr(pipelines,'_chapter_first_requested',stop)
    async def prepare(session,**kwargs):
        events.append('prepare')
        assert kwargs['expected_draft_id']==c.draft_id
        assert kwargs['expected_body_sha256']==text_hash('The original observer records a faint winter signal.')
        assert kwargs['expected_plan_binding']['workflow_run_id']==str(c.stable_id)
        return dispatch.OutlineAlignmentDispatch(state,
            'source_repair_blocked' if state=='blocked' else None,owner)
    async def execute(factory,settings,**kwargs):
        events.append('execute')
        assert not session.in_transaction(), 'parent retained preparation locks during provider handoff'
        return dispatch.OutlineAlignmentDispatch('review_ready',workflow_run_id=owner,
            review_source_binding={'draft_id':str(uuid4()),'body_hash':'1'*64})
    async def strict(*args,**kwargs):
        events.append('strict_review')
        assert kwargs['resume_existing_draft_only'] is True
        assert kwargs['supersede_pending_rewrites'] is False
        assert kwargs['export_markdown'] is False
        assert kwargs['review_source_binding']['body_hash']=='1'*64
        return SimpleNamespace(final_verdict='pass',workflow_run_id=owner)
    monkeypatch.setattr(dispatch,'prepare_outline_alignment_dispatch',prepare)
    monkeypatch.setattr(dispatch,'execute_outline_alignment_dispatch',execute)
    real=pipelines.run_chapter_pipeline.__wrapped__
    monkeypatch.setattr(pipelines,'run_chapter_pipeline',strict)
    async with c.factory() as session:
        if state=='prepared':
            result=await real(session,c.settings,c.original.project.slug,1)
            assert result.final_verdict=='pass'
            assert events==['prepare','execute','strict_review']
        else:
            with pytest.raises((ValueError,RuntimeError),match='source_repair_blocked|ordinary_entry_reached'):
                await real(session,c.settings,c.original.project.slug,1)
            assert events==(['prepare'] if state=='blocked' else ['prepare','ordinary_entry'])
    assert len(c.sent)==1


async def test_after_new_body_real_review_requests_rewrite_normal_loop_resumes_once(plan_case,monkeypatch):  # noqa: F811
    c=plan_case
    await install_markers(c)
    async def ready(*args,**kwargs):
        return dispatch.OutlineAlignmentDispatch('review_ready',workflow_run_id=uuid4(),
            review_source_binding={'draft_id':str(uuid4()),'body_hash':'2'*64})
    calls=[]
    async def continue_pipeline(*args,**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(final_verdict='rewrite' if len(calls)==1 else 'pass')
    monkeypatch.setattr(dispatch,'prepare_outline_alignment_dispatch',ready)
    real=pipelines.run_chapter_pipeline.__wrapped__
    monkeypatch.setattr(pipelines,'run_chapter_pipeline',continue_pipeline)
    async with c.factory() as session:
        result=await real(session,c.settings,c.original.project.slug,1)
    assert result.final_verdict=='pass' and len(calls)==2
    assert calls[0]['resume_existing_draft_only'] is True
    assert 'resume_existing_draft_only' not in calls[1]
    assert calls[1]['supersede_pending_rewrites'] is False
    assert len(c.sent)==1
