# ruff: noqa: F811
"""SQLite public prefix flow; synthetic provider is not real-book acceptance."""
from copy import deepcopy
from uuid import UUID
import pytest
from test_prefix_production_entry_fault_replay import entry_case, _entry, _snapshot, _install  # noqa: F401
from test_chapter_outline_readiness import rig  # noqa: F401
from bestseller.infra.db.models import ProjectModel, WorkflowRunModel, PlanningArtifactVersionModel
from sqlalchemy import select
from bestseller.services import chapter_outline_readiness as ready, planning_source_resolution as resolution
from bestseller.services.planning_draft_admission import _input, projection_audit_targets, _authority_roles, verify_planning_draft_admission as _REAL_VERIFY
from bestseller.services import planning_draft_admission as admission
from bestseller.services.prefix_source_proposal import register_prefix_source_proposal
from bestseller.services.prompt_source_policy import content_hash
from bestseller.services.budget import bind_execution_scope

pytestmark=[pytest.mark.unit,pytest.mark.asyncio]


async def prepared(c):
    await _entry(c)
    async with c.sessions() as s:
        world=await s.scalar(select(PlanningArtifactVersionModel).where(
            PlanningArtifactVersionModel.project_id==c.project_id,
            PlanningArtifactVersionModel.artifact_type=='world_spec'))
        world.content={**world.content,'rules':[{'description':'A seal stroke requires a dated witness entry.'}]}
        await s.commit()
    async with c.sessions() as s:
        p=await s.get(ProjectModel,c.project_id);parent=await s.get(WorkflowRunModel,c.wf_id)
        state=deepcopy(parent.metadata_json)
        foundation,_=await ready._foundation(s,p)
        state['foundation']=foundation
        state['identity']['foundation']=deepcopy(foundation)
        # Synthetic exhausted native history, expressly not a paid-result claim.
        state['phase']='admission_protocol_blocked'
        state['candidates'][-1]['source_admission_protocol']={'status':'protocol_exhausted','attempts':[{'status':'protocol_invalid','raw':False}]}
        state['candidates'][-1]['candidate']={**deepcopy(state['candidates'][-1]['candidate']),
            'batch_name':'A distinct recovered planning envelope',
            'omission_receipt':{'historical_source_only':True}}
        state['candidates'][-1]['candidate_hash']=content_hash(state['candidates'][-1]['candidate'])
        parent.metadata_json=state;parent.status='machine_blocked';await s.commit()
        original=deepcopy(state['candidates'][-1]['candidate'])
        patches=[{'op':'replace','path':'/chapters/0/goal','value':'Inspect the observatory register without writing a new seal.'}]
        from bestseller.services.operator_outline_edit import _apply
        edited=_apply(original,patches)
        candidate={**ready._body(state['source_payload']),'chapters':edited['chapters']}
        _,contents=await ready._foundation(s,p)
        supplied=await _input(s,project=p,workflow_run_id=parent.id,candidate_payload=candidate,
            book_spec=contents['book_spec'],world_spec=contents['world_spec'],cast_spec=contents['cast_spec'],commercial_report=None)
        roles=_authority_roles(supplied['source_authority'])
        path=next(path for path,role in roles.items() if role=='world_rule' and isinstance(resolution.pointer(supplied,path),str))
        review={'reviewer_kind':'authorized_operator_agent','unresolved_hard_items':[],
            'historical_attempts':resolution.protocol_review_bindings(state['candidates'][-1]['source_admission_protocol']),
            'current_projections':[{'target':t,'decision':'source_consistent','reason':'All fields checked against full source; observe and record without triggering a new seal.'} for t in projection_audit_targets(candidate)],
            'historical_projections':[{'target':t,'disposition':'corrected_in_requested_scope','reason':'The original scene action remains, and the inconsistent goal is explicitly corrected.'} for t in projection_audit_targets(original)],
            'checked_candidate_leaf_paths':resolution.leaves(candidate),
            'source_evidence':[{'path':path,'quote':resolution.pointer(supplied,path),'use':'world_rule'}],
            'old_model_grade_changed':False,'provisional_is_canon':False,'commercial_grade_asserted':False}
        plan=await resolution.prepare_source_resolution(s,c.settings,project=p,parent_workflow_id=parent.id,
            chapter_numbers=state['chapter_numbers'],patches=patches,review=review)
        await s.rollback()
    return plan


async def register(c,plan):
    async with c.sessions() as s:
        rid=await resolution.record_source_resolution(s,plan=plan,actor='isolated-agent',
            authorization_ref='fixture authorization only',explicit_user_approval=True)
        p=await s.get(ProjectModel,c.project_id)
        await register_prefix_source_proposal(s,project=p,parent_workflow_id=c.wf_id,
            source_payload=plan['source_payload'],through_chapter=plan['chapter_numbers'][-1],
            from_chapter=plan['chapter_numbers'][0],actor='isolated-agent',authorization_ref='fixture only',explicit_user_approval=True)
        await s.commit()
    return rid


async def test_explicit_resolution_preserves_old_history_and_reopens_normal_artifact(entry_case,monkeypatch):
    c=entry_case;plan=await prepared(c);before=await _snapshot(c);rid=await register(c,plan)
    _install(monkeypatch,c.sends,passed=True);paid=len(c.sends)
    monkeypatch.setattr(admission,'verify_planning_draft_admission',_REAL_VERIFY)
    async with c.sessions() as s:
        p=await s.get(ProjectModel,c.project_id)
        with bind_execution_scope(c.scope):
            result=await ready.approve_chapter_outline_prefix(s,c.settings,project=p,
                source_payload=plan['source_payload'],from_chapter=plan['chapter_numbers'][0],
                through_chapter=plan['chapter_numbers'][-1],source_resolution_id=UUID(rid),
                max_repair_rounds=2,allow_editorial_debt=True)
        await s.commit()
    assert result['draft_eligible'] and result['artifact_id']
    assert len(c.sends)==paid+1  # commercial only; no third native/rebase/repair
    assert (await _snapshot(c))['state']==before['state']
    async with c.sessions() as s:
        p=await s.get(ProjectModel,c.project_id)
        verified=await ready.verify_outline_prefix_readiness(s,project=p,artifact_id=UUID(result['artifact_id']),settings=c.settings)
        row=await s.get(WorkflowRunModel,UUID(result['workflow_run_id']))
        assert verified['chapter_numbers']==plan['chapter_numbers']
        for key,attempts in plan['inherited_attempts'].items(): assert row.metadata_json.get(key,[])==attempts
        assert row.metadata_json['candidates'][-1]['source_admission']['model_grade_asserted'] is False
    from test_chapter_outline_readiness import _materialized
    from bestseller.infra.db.models import ChapterModel, SceneCardModel, BookProductionControlModel
    from bestseller.services.creation_configuration import configuration_metadata
    from bestseller.services.prefix_window_activation import activate_verified_prefix_window, verify_activated_prefix_window
    from sqlalchemy import inspect
    from sqlalchemy.schema import CreateTable
    from uuid import uuid4
    async with c.engine.begin() as connection:
        for model in (SceneCardModel,BookProductionControlModel):
            ddl=str(CreateTable(model.__table__).compile(dialect=connection.dialect))
            await connection.exec_driver_sql(ddl.replace('::jsonb',''))
    async with c.sessions() as s:
        p=await s.get(ProjectModel,c.project_id)
        artifact=await s.get(PlanningArtifactVersionModel,UUID(result['artifact_id']))
        raw,scenes=_materialized(artifact.content['chapters'][0])
        def mapped(model,obj):
            return {k:v for k,v in vars(obj).items() if k in inspect(model).column_attrs.keys()}
        chapter=ChapterModel(id=uuid4(),project_id=p.id,**mapped(ChapterModel,raw));s.add(chapter);await s.flush()
        actual=[]
        for scene in scenes:
            item=SceneCardModel(id=uuid4(),project_id=p.id,chapter_id=chapter.id,**mapped(SceneCardModel,scene));s.add(item);actual.append(item)
        await s.flush()
        chapter.metadata_json={**chapter.metadata_json,'outline_prefix_readiness':{'artifact_id':str(artifact.id),
            'workflow_run_id':result['workflow_run_id'],'projection_hash':ready.outline_prefix_projection_hash(chapter,actual)}}
        p.metadata_json=configuration_metadata(p.metadata_json,{'configuration':{'book':{'total_chapters':50,'chapter_target':2600}},
            'execution_window':{'purpose':'opening_window','start':1,'end':1}})
        p.metadata_json={**p.metadata_json,'planning_status':'needs_replan'};p.status='needs_replan'
        await s.commit()
        activated=await activate_verified_prefix_window(s,c.settings,project=p,actor='isolated-agent',reason='exact independent source proof')
        assert activated['binding']['proofs'][0]['independent_source_resolution']['resolution_id']==rid
        await s.commit()
    async with c.sessions() as s:
        p=await s.get(ProjectModel,c.project_id)
        await verify_activated_prefix_window(s,c.settings,p)
        resolved=await s.get(WorkflowRunModel,UUID(rid))
        approval=await s.get(WorkflowRunModel,UUID(resolved.metadata_json['approval_id']));approval.status='cancelled'
        await s.flush()
        with pytest.raises(ValueError):await verify_activated_prefix_window(s,c.settings,p)
    assert len(c.sends)==paid+1


@pytest.mark.parametrize('fault',['coverage','hard','role','history','patch','attempts','authority'])
async def test_forged_plan_rejected_before_any_dispatch(entry_case,fault):
    c=entry_case;plan=await prepared(c);paid=len(c.sends)
    if fault=='coverage':plan['review']['checked_candidate_leaf_paths'].pop()
    if fault=='hard':plan['review']['unresolved_hard_items']=['actual conflict']
    if fault=='role':plan['review']['source_evidence'][0]['use']='explicit_creator'
    if fault=='history':plan['review']['historical_projections'].pop()
    if fault=='patch':plan['candidate']['chapters'][0]['goal']='arbitrary body'
    if fault=='attempts':plan['inherited_attempts']['patch_attempts']=[]
    if fault=='authority':plan['automatic_credit_granted']=True
    plan['plan_sha256']=content_hash({k:v for k,v in plan.items() if k!='plan_sha256'})
    with pytest.raises(ValueError):await register(c,plan)
    assert len(c.sends)==paid


async def test_absent_explicit_authority_cannot_record(entry_case):
    c=entry_case;plan=await prepared(c)
    async with c.sessions() as s:
        with pytest.raises(ValueError,match='explicit_authority_required'):
            await resolution.record_source_resolution(s,plan=plan,actor='LLM',authorization_ref='self approval')


@pytest.mark.parametrize('fault',['parent','approval','foundation','scope'])
async def test_current_source_or_authority_change_blocks_before_commercial(entry_case,monkeypatch,fault):
    c=entry_case;plan=await prepared(c);rid=await register(c,plan)
    _install(monkeypatch,c.sends,passed=True)
    monkeypatch.setattr(admission,'verify_planning_draft_admission',_REAL_VERIFY)
    paid=len(c.sends)
    async with c.sessions() as s:
        p=await s.get(ProjectModel,c.project_id)
        if fault=='parent':
            row=await s.get(WorkflowRunModel,c.wf_id)
            row.metadata_json={**row.metadata_json,'unexpected_new_history':True}
        elif fault=='approval':
            row=await s.get(WorkflowRunModel,UUID(rid))
            approval=await s.get(WorkflowRunModel,UUID(row.metadata_json['approval_id']))
            approval.status='cancelled'
        elif fault=='foundation':
            world=await s.scalar(select(PlanningArtifactVersionModel).where(
                PlanningArtifactVersionModel.project_id==c.project_id,PlanningArtifactVersionModel.artifact_type=='world_spec'))
            world.content={**world.content,'unknown_authored_clause':'This new constraint must be reviewed.'}
        await s.commit()
        with pytest.raises(ValueError):
            with bind_execution_scope(c.scope):
                await ready.approve_chapter_outline_prefix(s,c.settings,project=p,
                    source_payload=plan['source_payload'],from_chapter=plan['chapter_numbers'][0],
                    through_chapter=2 if fault=='scope' else plan['chapter_numbers'][-1],
                    source_resolution_id=UUID(rid),max_repair_rounds=2,allow_editorial_debt=True)
    assert len(c.sends)==paid
