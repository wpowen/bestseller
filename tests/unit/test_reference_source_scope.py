 # ruff: noqa: F811
"""Real SQL source trees, synthetic physical verifier; no provider or PG."""
import asyncio
from types import SimpleNamespace
from uuid import uuid4
import pytest
from sqlalchemy.schema import CreateTable
from test_draft_continuity import sql  # noqa:F401
from bestseller.infra.db import models as m
from bestseller.services import draft_continuity as dc,draft_continuity_adjudication as actions
from bestseller.services import foundation_selection,chapter_generation_input_builder as builder
from bestseller.services.operator_action_approval import record_operator_action_approval
from bestseller.services.prompt_source_policy import content_hash
from bestseller.settings import load_settings

pytestmark=[pytest.mark.unit,pytest.mark.asyncio]


async def test_source_tree_shared_only_inside_one_selection_and_failures_reset(sql,monkeypatch):
    settings=load_settings(env={});assets={'world_spec':SimpleNamespace(id=uuid4(),status='approved',content={'rule':'The observatory box remains sealed.'})}
    async def foundations(*args,**kwargs):return assets
    monkeypatch.setattr(foundation_selection,'load_selected_foundation_artifacts',foundations)
    calls=[];real_builder=builder.build_chapter_generation_input_bundle
    def counted(**kwargs):
        calls.append(kwargs['chapter'].chapter_number);return real_builder(**kwargs)
    monkeypatch.setattr(builder,'build_chapter_generation_input_bundle',counted)
    async def physical_boundary(session,settings,*,project,chapter,draft,proof,_memo=None):
        # Mirrors the actual corrected-reference witness's independent source
        # lookup, intentionally omitting _memo to exercise the repaired scope.
        actual=await dc.current_sources(session,settings,project=project,chapter=chapter)
        assert content_hash(actual)==proof['source_sha256']
    monkeypatch.setattr(actions,'validate_reference',physical_boundary)
    # Both financial/physical boundaries are synthetic in this source-cache
    # isolation suite. The real settled-operation route has its own SQL tests.
    from contextlib import asynccontextmanager
    from bestseller.services import retained_review_input_proof
    @asynccontextmanager
    async def synthetic_financial_boundary(*args,**kwargs):
        yield
    monkeypatch.setattr(retained_review_input_proof,'historical_reference_route_scope',synthetic_financial_boundary)

    async with sql() as s:
        connection=await s.connection()
        await connection.exec_driver_sql(str(CreateTable(m.SceneCardModel.__table__).compile(dialect=connection.dialect)).replace('::jsonb',''))
        p=m.ProjectModel(slug='scope-observatory',title='Observatory register',genre='mystery',language='en',target_chapters=3,target_word_count=6000,metadata_json={})
        s.add(p);await s.flush();chapters=[]
        for number in (1,2,3):
            chapter=m.ChapterModel(project_id=p.id,chapter_number=number,title=f'Observation {number}',chapter_goal='Record the observed tide.',target_word_count=2000,metadata_json={})
            s.add(chapter);await s.flush()
            s.add(m.SceneCardModel(project_id=p.id,chapter_id=chapter.id,scene_number=1,scene_type='investigation',title='Observe the instrument',metadata_json={'action_sequence':['Record the tide.']}))
            draft=m.ChapterDraftVersionModel(project_id=p.id,chapter_id=chapter.id,version_no=1,content_md=f'Observer records tide {number}.',is_current=False,promotion_state='candidate')
            s.add(draft);await s.flush()
            source=await dc.current_sources(s,settings,project=p,chapter=chapter)
            proof={'schema':actions.POLICY,'draft_id':str(draft.id),'body_sha256':dc.body_hash(draft.content_md),'source_sha256':content_hash(source)}
            rid=uuid4();binding={'project_id':str(p.id),'target_id':str(rid),'proof_sha256':content_hash(proof),'draft_id':str(draft.id),'body_sha256':proof['body_sha256'],'source_sha256':proof['source_sha256'],'formal_adoption':False}
            approval=await record_operator_action_approval(s,purpose='draft_continuity_reference',binding=binding,capabilities=['reference_pending_manuscript'],actor='fixture',authorization_ref='isolated fixture only',explicit_user_approval=True)
            s.add(m.WorkflowRunModel(id=rid,project_id=p.id,workflow_type='draft_continuity_reference',scope_type='chapter',scope_id=chapter.id,status='completed',current_step='qualified',metadata_json={'proof':proof,'binding':binding,'approval_id':str(approval)}))
            chapter.metadata_json={dc.POINTER:str(rid)};chapters.append(chapter);await s.commit()
        calls.clear();first=await dc.select_reference(s,settings,project=p,chapter=chapters[-1])
        assert sorted(calls)==[1,2,3] and dc._REFERENCE_SOURCE_MEMO.get() is None
        calls.clear();again=await dc.select_reference(s,settings,project=p,chapter=chapters[-1])
        assert first[1]==again[1] and sorted(calls)==[1,2,3]  # no cross-entry cache
        assets['world_spec'].content={'rule':'A newly authored rule must invalidate the prior source.'}
        with pytest.raises(ValueError,match='referenced_sources_changed'):
            await dc.select_reference(s,settings,project=p,chapter=chapters[-1])
        assert dc._REFERENCE_SOURCE_MEMO.get() is None
        assets['world_spec'].content={'rule':'The observatory box remains sealed.'}
        async def cancelled(*args,**kwargs):raise asyncio.CancelledError()
        monkeypatch.setattr(actions,'validate_reference',cancelled)
        with pytest.raises(asyncio.CancelledError):
            await dc.select_reference(s,settings,project=p,chapter=chapters[-1])
        assert dc._REFERENCE_SOURCE_MEMO.get() is None


async def test_context_source_cache_never_leaks_into_other_async_tasks(monkeypatch):
    session=object();project=SimpleNamespace(id=uuid4());chapter=SimpleNamespace(id=uuid4())
    sentinel={'snapshot':'same task only'}
    token=dc._REFERENCE_SOURCE_MEMO.set((session,project.id,{chapter.id:sentinel},asyncio.current_task()))
    class FreshRead(Exception):pass
    async def must_read(*args,**kwargs):raise FreshRead()
    monkeypatch.setattr(foundation_selection,'load_selected_foundation_artifacts',must_read)
    try:
        assert await dc.current_sources(session,None,project=project,chapter=chapter)==sentinel
        with pytest.raises(FreshRead):
            await asyncio.create_task(dc.current_sources(session,None,project=project,chapter=chapter))
    finally:dc._REFERENCE_SOURCE_MEMO.reset(token)
    assert dc._REFERENCE_SOURCE_MEMO.get() is None
