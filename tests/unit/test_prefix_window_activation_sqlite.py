# ruff: noqa: F811
"""SQLite prefix proof/materialized projection and scoped stale-state transition."""
from copy import deepcopy
from uuid import UUID,uuid4
from sqlalchemy import inspect
from sqlalchemy.schema import CreateTable
import pytest
from test_chapter_outline_readiness import rig,sqlite_identity_store,_materialized  # noqa:F401
from test_prefix_production_entry_fault_replay import entry_case,_install  # noqa:F401
from test_prefix_source_proposal_sqlite import proposal
from bestseller.infra.db.models import ProjectModel,PlanningArtifactVersionModel,ChapterModel,SceneCardModel,BookProductionControlModel
from bestseller.services import chapter_outline_readiness as ready
from bestseller.services.creation_configuration import configuration_metadata
from bestseller.services.budget import bind_execution_scope
from bestseller.services.prefix_window_activation import activate_verified_prefix_window,verify_activated_prefix_window,KEY
from bestseller.services.gate_registry import project_blocks_all_prose_generation

pytestmark=[pytest.mark.unit,pytest.mark.asyncio]


async def setup(c,monkeypatch):
    source,record,_=await proposal(c)
    _install(monkeypatch,c.sends,passed=True)
    async with c.engine.begin() as connection:
        for model in (SceneCardModel,BookProductionControlModel):
            ddl=str(CreateTable(model.__table__).compile(dialect=connection.dialect))
            await connection.exec_driver_sql(ddl.replace('::jsonb',''))
    async with c.sessions() as s:
        p=await s.get(ProjectModel,c.project_id)
        p.metadata_json=configuration_metadata(p.metadata_json,{'configuration':{'book':{'total_chapters':50,'chapter_target':2600}},'execution_window':{'purpose':'opening_window','start':1,'end':1}})
        await s.commit()
        with bind_execution_scope(c.scope):
            result=await ready.approve_chapter_outline_prefix(s,c.settings,project=p,source_payload=source,through_chapter=1,max_repair_rounds=0)
        a=await s.get(PlanningArtifactVersionModel,UUID(result['artifact_id']))
        ch,scenes=_materialized(a.content['chapters'][0])
        def mapped(model,obj):
            return {k:v for k,v in vars(obj).items() if k in inspect(model).column_attrs.keys()}
        chapter=ChapterModel(id=uuid4(),project_id=p.id,**mapped(ChapterModel,ch))
        s.add(chapter);await s.flush()
        actual=[]
        for scene in scenes:
            row=SceneCardModel(id=uuid4(),project_id=p.id,chapter_id=chapter.id,**mapped(SceneCardModel,scene));s.add(row);actual.append(row)
        await s.flush()
        chapter.metadata_json={**chapter.metadata_json,'outline_prefix_readiness':{'artifact_id':str(a.id),'workflow_run_id':result['workflow_run_id'],'projection_hash':ready.outline_prefix_projection_hash(chapter,actual)}}
        p.metadata_json={**p.metadata_json,'planning_status':'needs_replan','outline_commercial_last_failure':{'old':True}}
        p.status='needs_replan';await s.commit()
    return result


async def test_scoped_transition_reopens_and_rejects_window_or_source_change(entry_case,monkeypatch):
    c=entry_case;result=await setup(c,monkeypatch);sends=len(c.sends)
    async with c.sessions() as s:
        p=await s.get(ProjectModel,c.project_id)
        assert project_blocks_all_prose_generation(p)
        rec=await activate_verified_prefix_window(s,c.settings,project=p,actor='fixture-human',reason='exact verified window')
        assert rec['previous_status']=='needs_replan' and rec['previous_planning_status']=='needs_replan'
        assert rec['binding']['window']['end']==1 and not rec['whole_book_approved']
        assert not project_blocks_all_prose_generation(p)
        assert p.metadata_json['outline_commercial_last_failure']=={'old':True}
        assert await activate_verified_prefix_window(s,c.settings,project=p,actor='fixture-human',reason='same proof')==rec
        await s.commit()
    async with c.sessions() as s:
        p=await s.get(ProjectModel,c.project_id)
        await verify_activated_prefix_window(s,c.settings,p)
        p.metadata_json=configuration_metadata(p.metadata_json,{'configuration':p.metadata_json['creation_configuration'],'execution_window':{'purpose':'opening_window','start':1,'end':2}})
        assert project_blocks_all_prose_generation(p)
        with pytest.raises(ValueError):await verify_activated_prefix_window(s,c.settings,p)
        await s.rollback()
    async with c.sessions() as s:
        p=await s.get(ProjectModel,c.project_id);a=await s.get(PlanningArtifactVersionModel,UUID(result['artifact_id']))
        a.content={**a.content,'batch_name':'changed source'};await s.commit()
        with pytest.raises(ValueError):await verify_activated_prefix_window(s,c.settings,p)
    assert len(c.sends)==sends


@pytest.mark.parametrize('key',['outline_replan_in_progress','structural_repair_required','generation_resume_blocked_until_repair_audit'])
async def test_other_structural_blocks_cannot_be_cleared(entry_case,monkeypatch,key):
    c=entry_case;await setup(c,monkeypatch)
    async with c.sessions() as s:
        p=await s.get(ProjectModel,c.project_id);p.metadata_json={**p.metadata_json,key:True};before=deepcopy(p.metadata_json)
        with pytest.raises(ValueError,match='independent_structural_block'):
            await activate_verified_prefix_window(s,c.settings,project=p,actor='fixture',reason='cannot waive')
        assert p.metadata_json==before and p.status=='needs_replan' and KEY not in p.metadata_json


async def test_completed_parent_requires_its_actual_readiness_receipt(entry_case,monkeypatch):
    from bestseller.infra.db.models import WorkflowRunModel
    from bestseller.services.prefix_source_proposal import register_prefix_source_proposal
    c=entry_case;first=await setup(c,monkeypatch)
    from bestseller.services import planning_continuity_context,llm
    import json
    async def fixture_context(*args,**kwargs):
        return {'context_hash':'fixture-adopted-prior','before_chapter':kwargs['before_chapter'],
            'recent_adopted_prose':[{'body':'The observatory seal was inspected.'}]}
    monkeypatch.setattr(planning_continuity_context,'build_planning_continuity_context',fixture_context)
    original_boundary=llm._call_litellm
    async def synthetic_continuation(request,role):
        if request.prompt_template!='continuation_outline_rebase':
            return await original_boundary(request,role)
        c.sends.append({'template':request.prompt_template,'wf':str(request.workflow_run_id)})
        text=json.dumps(typed)
        return text,11,7,'stop',None,llm._build_raw_assistant_message(text,None)
    monkeypatch.setattr(llm,'_call_litellm',synthetic_continuation)
    async with c.sessions() as s:
        p=await s.get(ProjectModel,c.project_id)
        parent=await s.get(WorkflowRunModel,UUID(first['workflow_run_id']))
        assert parent.status=='completed' and parent.metadata_json['phase']=='completed'
        payload=deepcopy(parent.metadata_json['source_payload'])
        _,contents=await ready._foundation(s,p)
        prefix={**ready._body(payload),'chapters':[v for v in payload['chapters'] if v['chapter_number'] in [2,3]]}
        typed=ready._validate(prefix,project=p,state={**parent.metadata_json,'chapter_numbers':[2,3]},cast=contents['cast_spec'])
        replacements={v['chapter_number']:v for v in typed['chapters']}
        payload['chapters']=[replacements.get(v['chapter_number'],v) for v in payload['chapters']]
        await register_prefix_source_proposal(s,project=p,parent_workflow_id=parent.id,
            source_payload=payload,from_chapter=2,through_chapter=3,actor='fixture',
            authorization_ref='fixture scoped continuation',explicit_user_approval=True)
        with bind_execution_scope(c.scope):
            result=await ready.approve_chapter_outline_prefix(s,c.settings,project=p,
                source_payload=payload,from_chapter=2,through_chapter=3,max_repair_rounds=0)
        artifact=await s.get(PlanningArtifactVersionModel,UUID(result['artifact_id']))
        def mapped(model,obj):
            return {k:v for k,v in vars(obj).items() if k in inspect(model).column_attrs.keys()}
        for raw in artifact.content['chapters']:
            ch,scenes=_materialized(raw)
            chapter=ChapterModel(id=uuid4(),project_id=p.id,**mapped(ChapterModel,ch));s.add(chapter);await s.flush()
            actual=[]
            for scene in scenes:
                row=SceneCardModel(id=uuid4(),project_id=p.id,chapter_id=chapter.id,**mapped(SceneCardModel,scene));s.add(row);actual.append(row)
            await s.flush()
            chapter.metadata_json={**chapter.metadata_json,'outline_prefix_readiness':{'artifact_id':str(artifact.id),
                'workflow_run_id':result['workflow_run_id'],'projection_hash':ready.outline_prefix_projection_hash(chapter,actual)}}
        p.metadata_json=configuration_metadata(p.metadata_json,{'configuration':p.metadata_json['creation_configuration'],
            'execution_window':{'purpose':'opening_window','start':1,'end':3}})
        paid=len(c.sends)
        receipt=await activate_verified_prefix_window(s,c.settings,project=p,actor='fixture',reason='completed source verified')
        assert len(receipt['binding']['proofs'])==3
        await s.commit()
    async with c.sessions() as s:
        p=await s.get(ProjectModel,c.project_id)
        await verify_activated_prefix_window(s,c.settings,p)
        original=await s.get(PlanningArtifactVersionModel,UUID(first['artifact_id']))
        original.content={**original.content,'batch_name':'tampered parent evidence'}
        await s.flush()
        with pytest.raises(ValueError):
            await verify_activated_prefix_window(s,c.settings,p)
        await s.rollback()
    assert len(c.sends)==paid
