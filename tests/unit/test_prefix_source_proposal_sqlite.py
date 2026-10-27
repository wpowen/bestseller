# ruff: noqa: F811
"""Real SQLite public admission/reopen, synthetic provider and typed narrative adapter."""
from copy import deepcopy
from uuid import UUID
import pytest
from test_chapter_outline_readiness import rig, sqlite_identity_store  # noqa:F401
from test_prefix_production_entry_fault_replay import entry_case, _entry, _snapshot, _install  # noqa:F401
from bestseller.infra.db.models import ProjectModel, WorkflowRunModel, PlanningArtifactVersionModel
from bestseller.services import chapter_outline_readiness as ready
from bestseller.services.prefix_source_proposal import register_prefix_source_proposal, verify_prefix_source_proposal, KEY,SCOPED_KEY
from bestseller.services.budget import bind_execution_scope

pytestmark=[pytest.mark.unit,pytest.mark.asyncio]


async def proposal(c):
    await _entry(c)
    before=await _snapshot(c)
    source=deepcopy(c.source);source['chapters'][0]['goal']='Inspect the observatory seal before signing the register.'
    async with c.sessions() as s:
        p=await s.get(ProjectModel,c.project_id)
        parent=await s.get(WorkflowRunModel,c.wf_id)
        _,contents=await ready._foundation(s,p)
        prefix={**source,'chapters':source['chapters'][:1]}
        typed=ready._validate(prefix,project=p,state=deepcopy(parent.metadata_json),cast=contents['cast_spec'])
        source['chapters'][0]=typed['chapters'][0]
        record=await register_prefix_source_proposal(s,project=p,parent_workflow_id=c.wf_id,
            source_payload=source,through_chapter=1,actor='isolated-human',
            authorization_ref='fixture only, no production permission',explicit_user_approval=True)
        await s.commit()
    return source,record,before


async def test_only_exact_prefix_gets_real_review_and_reopen_verifies_ungraded_parent(entry_case,monkeypatch):
    c=entry_case;source,record,before=await proposal(c)
    assert 'report' not in record and record['grade_asserted'] is False
    async with c.sessions() as s:
        p=await s.get(ProjectModel,c.project_id)
        with pytest.raises(ValueError,match='checkpoint_missing'):
            ready._source_record(p,source)
        with pytest.raises(ValueError,match='review_scope_changed'):
            await ready.approve_chapter_outline_prefix(s,c.settings,project=p,
                source_payload=source,through_chapter=2,max_repair_rounds=0)
    assert (await _snapshot(c))['state']==before['state']
    _install(monkeypatch,c.sends,passed=True)
    paid_before=len(c.sends)
    async with c.sessions() as s:
        p=await s.get(ProjectModel,c.project_id)
        with bind_execution_scope(c.scope):
            result=await ready.approve_chapter_outline_prefix(s,c.settings,project=p,
                source_payload=source,through_chapter=1,max_repair_rounds=0)
        assert result['passed'] and result['chapter_numbers']==[1]
        await s.commit()
    assert len(c.sends)==paid_before+1
    async with c.sessions() as s:
        p=await s.get(ProjectModel,c.project_id)
        receipt=await ready.verify_outline_prefix_readiness(s,project=p,
            artifact_id=UUID(result['artifact_id']),settings=c.settings)
        assert receipt['chapter_numbers']==[1] and receipt['full_window_approved'] is False
        with bind_execution_scope(c.scope):
            again=await ready.approve_chapter_outline_prefix(s,c.settings,project=p,
                source_payload=source,through_chapter=1,max_repair_rounds=0)
        assert again==result
    assert len(c.sends)==paid_before+1
    assert (await _snapshot(c))['state']==before['state']


@pytest.mark.parametrize('fault',['parent','source','scope','pointer','foundation','approval'])
async def test_recovery_rejects_changed_custody_before_paid_review(entry_case,fault):
    c=entry_case;source,record,_=await proposal(c);paid_before=len(c.sends)
    async with c.sessions() as s:
        p=await s.get(ProjectModel,c.project_id)
        if fault=='parent':
            row=await s.get(WorkflowRunModel,c.wf_id);row.metadata_json={**row.metadata_json,'changed':'not authorized'}
        elif fault=='source':source['chapters'][0]['goal']='A different source.'
        elif fault=='pointer':p.metadata_json={**p.metadata_json,KEY:{}}
        elif fault=='foundation':
            aid=UUID(record['foundation']['cast_spec']['artifact_id']);row=await s.get(PlanningArtifactVersionModel,aid)
            row.content={**row.content,'unknown_authored_clause':'do not omit'}
        elif fault=='approval':
            row=await s.get(WorkflowRunModel,UUID(record['proposal_id']))
            row.metadata_json={**row.metadata_json,'approval_id':str(c.wf_id)}
        await s.commit()
        with pytest.raises(ValueError):
            await verify_prefix_source_proposal(s,project=p,record=record,source_payload=source,
                chapter_numbers=[1,2] if fault=='scope' else [1])
    assert len(c.sends)==paid_before


async def test_human_authority_and_tail_are_not_inferred(entry_case):
    c=entry_case
    async with c.sessions() as s:
        p=await s.get(ProjectModel,c.project_id)
        with pytest.raises(ValueError,match='human_approval_required'):
            await register_prefix_source_proposal(s,project=p,parent_workflow_id=c.wf_id,
                source_payload=c.source,through_chapter=1,actor='untrusted',authorization_ref='prose says approved')
        changed=deepcopy(c.source);changed['chapters'][2]['goal']='Unreviewed new tail.'
        with pytest.raises(ValueError,match='tail_changed'):
            await register_prefix_source_proposal(s,project=p,parent_workflow_id=c.wf_id,
                source_payload=changed,through_chapter=1,actor='isolated-human',authorization_ref='fixture',explicit_user_approval=True)


async def test_disjoint_continuation_scope_preserves_original_proposal_and_reopens(entry_case,monkeypatch):
    c=entry_case;source,first,_=await proposal(c);first_source=deepcopy(source)
    _install(monkeypatch,c.sends,passed=True)
    async with c.sessions() as s:
        p=await s.get(ProjectModel,c.project_id)
        with bind_execution_scope(c.scope):
            admitted=await ready.approve_chapter_outline_prefix(s,c.settings,project=p,
                source_payload=source,through_chapter=1,max_repair_rounds=0)
        await s.commit()
    parent_id=UUID(admitted['workflow_run_id']);paid_before=len(c.sends)
    async with c.sessions() as s:
        p=await s.get(ProjectModel,c.project_id);parent=await s.get(WorkflowRunModel,parent_id)
        source=deepcopy(parent.metadata_json['source_payload'])
        _,contents=await ready._foundation(s,p)
        prefix={**ready._body(source),'chapters':[c for c in source['chapters'] if c['chapter_number'] in [2,3]]}
        validated=ready._validate(prefix,project=p,state={**parent.metadata_json,'chapter_numbers':[2,3]},cast=contents['cast_spec'])
        normalized={c['chapter_number']:c for c in validated['chapters']}
        source['chapters']=[normalized.get(c['chapter_number'],c) for c in source['chapters']]
    async with c.sessions() as s:
        p=await s.get(ProjectModel,c.project_id)
        continuation=await register_prefix_source_proposal(s,project=p,parent_workflow_id=parent_id,
            source_payload=source,from_chapter=2,through_chapter=3,actor='isolated-human',authorization_ref='explicit-2-3',explicit_user_approval=True)
        assert continuation['chapter_numbers']==[2,3]
        assert p.metadata_json[KEY][first['candidate_hash']]==first
        assert p.metadata_json[SCOPED_KEY][continuation['candidate_hash']]['2:3']==continuation
        assert ready._source_record(p,source,allow_ungraded_proposal=True,proposal_scope=[2,3])[0]==continuation
        again=await register_prefix_source_proposal(s,project=p,parent_workflow_id=parent_id,
            source_payload=source,from_chapter=2,through_chapter=3,actor='isolated-human',authorization_ref='explicit-2-3',explicit_user_approval=True)
        assert again==continuation
        await s.commit()
    async with c.sessions() as s:
        p=await s.get(ProjectModel,c.project_id)
        await verify_prefix_source_proposal(s,project=p,record=first,source_payload=first_source,chapter_numbers=[1])
        await verify_prefix_source_proposal(s,project=p,record=continuation,source_payload=source,chapter_numbers=[2,3])
        changed=deepcopy(source);changed['chapters'][0]['goal']='Unrequested rewrite of already inspected first chapter.'
        with pytest.raises(ValueError,match='tail_changed'):
            await register_prefix_source_proposal(s,project=p,parent_workflow_id=parent_id,
                source_payload=changed,from_chapter=2,through_chapter=3,actor='isolated-human',authorization_ref='explicit-2-3',explicit_user_approval=True)
        metadata=deepcopy(p.metadata_json);metadata[SCOPED_KEY]={};p.metadata_json=metadata;await s.commit()
        with pytest.raises(ValueError,match='pointer_changed'):
            await verify_prefix_source_proposal(s,project=p,record=continuation,source_payload=source,chapter_numbers=[2,3])
        await verify_prefix_source_proposal(s,project=p,record=first,source_payload=first_source,chapter_numbers=[1])
    assert len(c.sends)==paid_before
