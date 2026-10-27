"""Isolated actual gateway/publication/materializer; HTTP intercepted, no PG."""
from copy import deepcopy
import json
import pytest
from sqlalchemy import MetaData, JSON, ForeignKeyConstraint, select, func
from sqlalchemy.dialects.postgresql import JSONB
from test_bounded_outline_review import bounded as bounded  # noqa:F401
from test_bounded_outline_review import volume as volume, harness as harness  # noqa:F401
from test_chapter_outline_readiness import rig as prefix_rig  # noqa:F401,F811
import test_chapter_outline_readiness as pf
from test_workflow_approved_outline_source_guard import _batch
from bestseller.infra.db import models as m
from bestseller.services import bounded_outline_review as bounded_api, chapter_outline_readiness as ready, workflows, projects
from bestseller.services import operator_outline_edit as edit
from bestseller.services.operator_action_approval import record_operator_action_approval
from bestseller.services.prompt_source_policy import content_hash
from bestseller.domain.workflow import ChapterOutlineBatchInput

pytestmark=[pytest.mark.unit,pytest.mark.asyncio]


@pytest.fixture
async def consumer(bounded, prefix_rig, monkeypatch):  # noqa:F811
    h=bounded;r=prefix_rig
    # Initial failed checkpoint is a synthetic producer fixture. All subsequent
    # rows, approvals, gateway records, publication and materialization are real SQLite.
    from bestseller.services.book_design import ensure_project_book_design_snapshot
    from bestseller.services.outline_recovery_checkpoint import recovery_binding
    snapshot=ensure_project_book_design_snapshot(r.project,force_rebuild=True,protagonist_name="Mara")
    r.project.metadata_json=json.loads(json.dumps(r.project.metadata_json).replace('a'*64,snapshot.source_hash))
    from bestseller.services.rolling_outline import _canonical_hash
    rolling=r.project.metadata_json['rolling_outline_plan']
    rolling['plan_hash']=_canonical_hash({k:v for k,v in rolling.items() if k!='plan_hash'})
    for row in r.foundation.values():
        row.content['_meta']['source_snapshot_hash']=snapshot.source_hash
    r.source['chapters'][0]=_batch().model_dump(mode='json')['chapters'][0]
    record=r.project.metadata_json['outline_commercial_judge_checkpoints']['window']['best']
    record['binding']=recovery_binding(r.project.metadata_json,1,[1,9])
    record['report']['outline_repair_dependency_hash']=record['binding']['dependency_hash']
    record['candidate']=deepcopy(r.source)
    record['candidate_hash']=content_hash(r.source)
    record['report']['graded_outline_hash']=content_hash(r.source)
    r.judges[:]=[False]
    await pf._approve(r,through_chapter=1,max_repair_rounds=0,allow_editorial_debt=False)
    old=pf._workflow(r)
    engine=h.session_factory.kw['bind']
    for model in (m.ProjectModel,m.VolumeModel,m.WorkflowRunModel,m.WorkflowStepRunModel,m.PlanningArtifactVersionModel,
                  m.ChapterModel,m.SceneCardModel,m.ChapterDraftVersionModel,m.SceneDraftVersionModel,
                  m.CanonFactModel,m.TimelineEventModel,m.RewriteTaskModel):
        table=model.__table__.to_metadata(MetaData())
        for col in table.c:
            if isinstance(col.type,JSONB):col.type=JSON()
            if col.server_default is not None and '::' in str(col.server_default.arg):col.server_default=None
        for constraint in list(table.constraints):
            if isinstance(constraint,ForeignKeyConstraint):table.constraints.remove(constraint)
        async with engine.begin() as connection:await connection.run_sync(table.create)
    p=m.ProjectModel(id=r.project.id,slug=r.project.slug,title=r.project.title,genre='general-fiction',language='en',
        target_chapters=50,target_word_count=130000,metadata_json={**r.project.metadata_json,
            'execution_scope_id':'conception:consumer-fixture','identity_manifest_status':'locked',
            'identity_manifest':[{'name':'Mara','role':'protagonist','gender':'female'},
                                 {'name':'Steward','role':'supporting','gender':'unknown'}]})
    state=deepcopy(old.metadata_json)
    state['patch_attempts']=[{'status':'consumed','response':'historical invalid one'},
                             {'status':'consumed','response':'historical invalid two'}]
    state['identity']['max_repair_rounds']=2
    # Use a complete unrelated authored fixture as the candidate; source window
    # and immutable automatic attempts remain the original producer checkpoint.
    parent=state['candidates'][-1]['candidate']
    patches=[{'op':'replace','path':'/chapters/0/goal',
              'value':'Mara pays the fee and compares both dated receipts before closing.'}]
    wf=m.WorkflowRunModel(id=old.id,project_id=p.id,workflow_type=old.workflow_type,scope_type=old.scope_type,
        scope_id=old.scope_id,status=old.status,current_step=old.current_step,requested_by='fixture',metadata_json=state)
    # Foundation lookup uses the existing isolated source fixture, never production.
    h.project=p;h.workflow=wf;h.r=r
    h.settings.pipeline.enable_outline_llm_commercial_judge=True
    h.settings.pipeline.enable_outline_reader_experience_judge=True
    monkeypatch.setattr(workflows,'load_settings',lambda:h.settings)
    monkeypatch.setattr(ready,'create_workflow_run',workflows.create_workflow_run)
    monkeypatch.setattr(ready,'import_planning_artifact',projects.import_planning_artifact)
    async with h.session_factory() as s:
        s.add_all([p,wf]);await s.commit()
        plan=await edit.prepare_operator_outline_edit(s,project_id=p.id,workflow_id=wf.id,
            expected_candidate_sha256=content_hash(parent),patches=patches)
        approval=await record_operator_action_approval(s,purpose='outline_candidate_edit',binding=edit.approval_binding(plan),
            capabilities=['apply_candidate','review_candidate'],actor='ISOLATED FIXTURE',authorization_ref='fixture-human',explicit_user_approval=True)
        await s.commit()
        await edit.apply_operator_outline_edit(s,project_id=p.id,workflow_id=wf.id,
            expected_candidate_sha256=plan['parent_sha256'],patches=patches,expected_plan_sha256=plan['plan_sha256'],approval_id=approval)
        await s.commit()
    h.plan=plan;h.approval=approval;h.candidate=deepcopy(plan['candidate'])
    async def review():
        async with h.session_factory() as s:
            project=await s.get(m.ProjectModel,p.id)
            result=await bounded_api.review_candidate_with_send_cap(s,h.settings,project=project,workflow_id=wf.id,
                scope='conception:consumer-fixture',plan=plan,approval_id=approval,user_message_id='fixture-human')
            await s.commit()
            return result
    h.review=review
    async def consume(bundle):
        async with h.session_factory() as s:
            project=await s.get(m.ProjectModel,p.id)
            return await bounded_api.consume_bounded_prefix_review(s,h.settings,project=project,workflow_id=wf.id,bundle=bundle)
    h.consume=consume
    async def materialize(artifact_id):
        async with h.session_factory() as s:
            row=await s.get(m.PlanningArtifactVersionModel,artifact_id)
            result=await workflows.materialize_chapter_outline_batch(s,p.slug,
                ChapterOutlineBatchInput.model_validate(row.content),source_artifact_id=row.id)
            await s.commit()
            return result
    h.materialize=materialize
    return h


async def test_actual_gateway_review_publish_and_materialization_no_third_send(consumer,monkeypatch):
    h=consumer
    bundle=await h.review()
    assert bundle['grade']['passed'] and len(h.wires)==1
    forbid_dispatch(monkeypatch)
    outcome=await h.consume(bundle)
    assert outcome['passed']
    from uuid import UUID
    result=await h.materialize(UUID(outcome['artifact_id']))
    assert result.chapters_created==1 and result.scenes_created==1
    assert len(h.wires)==1
    async with h.session_factory() as s:
        assert await s.scalar(select(func.count()).select_from(m.ChapterModel))==1
        assert await s.scalar(select(func.count()).select_from(m.SceneCardModel))==1
        assert await s.scalar(select(func.count()).select_from(m.ChapterDraftVersionModel))==0
        from bestseller.services.prewrite_review import assert_prewrite_review_approved
        with pytest.raises(PermissionError):await assert_prewrite_review_approved(s,h.project.slug)
        assert (await s.get(m.WorkflowRunModel,h.workflow.id)).metadata_json['patch_attempts']==[{'status':'consumed','response':'historical invalid one'},
            {'status':'consumed','response':'historical invalid two'}]


def forbid_dispatch(monkeypatch):
    from bestseller.services import llm, outline_llm_judge, outline_reader_experience_judge
    async def forbidden(*args,**kwargs):
        raise AssertionError('No unbounded completion or physical send after review')
    monkeypatch.setattr(llm,'_call_litellm',forbidden)
    # Bounded verification uses its explicit retained callback; ordinary aliases
    # cannot silently claim cache or invoke a third opinion.
    monkeypatch.setattr(llm,'complete_text',forbidden)
    monkeypatch.setattr(outline_llm_judge,'complete_text',forbidden)
    monkeypatch.setattr(outline_reader_experience_judge,'complete_text',forbidden)
    monkeypatch.setattr(ready,'complete_text',forbidden)


async def test_two_stage_pass_actual_materialization_and_repeat_zero_send(consumer,monkeypatch):
    h=consumer
    from bestseller.services import llm
    original=llm._call_litellm
    h.blocked=True
    async def fixture_transport(request,role):
        raw,ins,outs,finish,tools,msg=await original(request,role)
        value=json.loads(raw)
        if request.prompt_template=='outline_commercial_judge':value['pass']=True
        else:value['decisions'][0]['decision']='DISMISS'
        return json.dumps(value),ins,outs,finish,tools,msg
    monkeypatch.setattr(llm,'_call_litellm',fixture_transport)
    bundle=await h.review()
    assert bundle['grade']['passed'] and len(h.wires)==2
    # Gateway cached replay is allowed but must never send physically again.
    async def no_physical(*args,**kwargs):raise AssertionError('Third send')
    monkeypatch.setattr(llm,'_call_litellm',no_physical)
    replay=await h.review()
    assert replay['grade']==bundle['grade'] and len(h.wires)==2
    before=await paid_snapshot(h)
    forbid_dispatch(monkeypatch)
    from uuid import UUID
    first=await h.consume(bundle)
    assert first['passed']
    artifact_id=UUID(first['artifact_id'])
    result=await h.materialize(artifact_id)
    assert result.chapters_created==1 and result.scenes_created==1
    again=await h.consume(bundle)
    assert again['artifact_id']==first['artifact_id']
    repeat=await h.materialize(artifact_id)
    assert repeat.chapters_created==0 and repeat.scenes_created==0
    assert len(h.wires)==2
    assert await paid_snapshot(h)==before
    async with h.session_factory() as s:
        state=(await s.get(m.WorkflowRunModel,h.workflow.id)).metadata_json
        assert len(state['patch_attempts'])==2
        assert len(state['operator_edit_prior_outcomes'])==1
        assert await s.scalar(select(func.count()).select_from(m.ChapterDraftVersionModel))==0


@pytest.mark.parametrize('fault',['old_schema','failed','unknown','candidate','source','project','workflow','grade','scope'])
async def test_invalid_bundle_never_publishes_or_dispatches(consumer,monkeypatch,fault):
    h=consumer;bundle=await h.review();before=len(h.wires)
    if fault=='old_schema':bundle['schema']='obsolete'
    elif fault=='failed':bundle['grade']['passed']=False
    elif fault=='unknown':bundle['grade']['infrastructure_failure']=True
    elif fault=='candidate':bundle['candidate_sha256']='0'*64
    elif fault=='source':bundle['project_brief_sha256']='0'*64
    elif fault=='project':bundle['project_id']=str(__import__('uuid').uuid4())
    elif fault=='workflow':bundle['workflow_id']=str(__import__('uuid').uuid4())
    elif fault=='grade':bundle['grade']['overall_score']=0.99
    elif fault=='scope':bundle['scope']='conception:another-fixture'
    forbid_dispatch(monkeypatch)
    with pytest.raises(ValueError):await h.consume(bundle)
    assert len(h.wires)==before
    async with h.session_factory() as s:
        assert await s.scalar(select(func.count()).select_from(m.PlanningArtifactVersionModel))==0
        assert await s.scalar(select(func.count()).select_from(m.ChapterModel))==0
        assert len((await s.get(m.WorkflowRunModel,h.workflow.id)).metadata_json['patch_attempts'])==2


@pytest.mark.parametrize('fault',['receipt_deleted','receipt_none','receipt_empty','meta_deleted',
    'outer_grade','body','request_missing','response_missing','request_tampered','response_tampered','request_truncated'])
async def test_actual_materializer_rejects_changed_proof_before_writes(consumer,monkeypatch,fault):
    from pathlib import Path
    from uuid import UUID
    h=consumer;bundle=await h.review();published=await h.consume(bundle)
    artifact_id=UUID(published['artifact_id']);before=len(h.wires)
    forbid_dispatch(monkeypatch)
    async with h.session_factory() as s:
        artifact=await s.get(m.PlanningArtifactVersionModel,artifact_id)
        wf=await s.get(m.WorkflowRunModel,h.workflow.id)
        content=deepcopy(artifact.content);state=deepcopy(wf.metadata_json)
        if fault=='receipt_deleted':del content['_meta']['sourcebound_readiness_receipt']
        elif fault=='receipt_none':content['_meta']['sourcebound_readiness_receipt']=None
        elif fault=='receipt_empty':content['_meta']['sourcebound_readiness_receipt']={}
        elif fault=='meta_deleted':del content['_meta']
        elif fault=='body':content['chapters'][0]['goal']='Tampered unrelated plot'
        elif fault=='outer_grade':
            # Recompute all ordinary report/artifact hashes: physical root and
            # transport remain untouched, so bundle-only self-consistency fails.
            report=deepcopy(state['final_report']);report['overall_score']=0.99
            state['final_report']=report;state['candidates'][-1]['report']=report
            receipt=deepcopy(state['receipt']);receipt['judge_report_hash']=content_hash(report)
            state['receipt']=receipt;content['_meta']['sourcebound_readiness_receipt']=receipt
            state['artifact_hash']=content_hash(content)
        else:
            phase=bundle['review_send_receipts']['initial']
            kind='response' if fault.startswith('response') else 'request'
            path=Path(h.settings.artifact_store.local_dir)/phase['retained_files'][kind]['ref']
            if fault.endswith('missing'):path.unlink()
            else:
                payload=json.loads(path.read_bytes())
                if kind=='response':payload['content']['text']='tampered'
                elif fault.endswith('truncated'):payload['messages']['user']['truncated']=True
                else:payload['messages']['user']['text']+=' tampered'
                path.write_text(json.dumps(payload))
        artifact.content=content;wf.metadata_json=state;await s.commit()
    with pytest.raises((ValueError,FileNotFoundError)):await h.materialize(artifact_id)
    assert len(h.wires)==before
    async with h.session_factory() as s:
        assert await s.scalar(select(func.count()).select_from(m.ChapterModel))==0
        assert await s.scalar(select(func.count()).select_from(m.SceneCardModel))==0
        # Only prefix + approval + candidate edit exist, no materializer write.
        assert await s.scalar(select(func.count()).select_from(m.WorkflowRunModel).where(
            m.WorkflowRunModel.workflow_type==workflows.WORKFLOW_TYPE_MATERIALIZE_CHAPTER_OUTLINE))==0


async def paid_snapshot(h):
    from sqlalchemy import inspect
    values = {}
    async with h.session_factory() as s:
        for model in (m.ExecutionBudgetModel,m.ExecutionOperationModel,m.ExecutionAttemptModel,m.LlmRunModel):
            values[model.__tablename__]=[{col.key:deepcopy(getattr(row,col.key)) for col in inspect(model).column_attrs}
                for row in await s.scalars(select(model))]
    return values


@pytest.mark.parametrize('fault',['paid_transport','paid_root','run_project','run_workflow','spent_grant'])
async def test_actual_materializer_rejects_broken_original_physical_proof(consumer,monkeypatch,fault):
    from uuid import UUID,uuid4
    h=consumer;bundle=await h.review();published=await h.consume(bundle)
    phase=bundle['review_send_receipts']['initial']
    forbid_dispatch(monkeypatch)
    async with h.session_factory() as s:
        run=await s.get(m.LlmRunModel,UUID(phase['llm_run_id']))
        key=run.metadata_json['execution_receipt']['operation_key']
        if fault in {'paid_transport','paid_root'}:
            if fault=='paid_transport':
                row=await s.scalar(select(m.ExecutionOperationModel).where(m.ExecutionOperationModel.status=='transport_result'))
                data=deepcopy(row.result_json);data['result']['content']='different original result'
            else:
                row=await s.get(m.ExecutionOperationModel,key)
                data=deepcopy(row.result_json);data['content']='different original result'
            row.result_json=data
        elif fault=='run_project':run.project_id=uuid4()
        elif fault=='run_workflow':run.workflow_run_id=uuid4()
        else:
            row=await s.get(m.ExecutionBudgetModel,bundle['scope'])
            data=deepcopy(row.metadata_json);data['single_dispatch_grants'][phase['grant_id']]['status']='available';row.metadata_json=data
        await s.commit()
    before=await paid_snapshot(h)
    with pytest.raises(ValueError):await h.materialize(UUID(published['artifact_id']))
    assert await paid_snapshot(h)==before and len(h.wires)==1
    async with h.session_factory() as s:
        assert await s.scalar(select(func.count()).select_from(m.ChapterModel))==0


async def test_failed_physical_review_preserves_spend_and_old_rejection(consumer,monkeypatch):
    h=consumer;h.blocked=True
    bundle=await h.review()
    assert not bundle['grade']['passed'] and len(h.wires)==2
    before=await paid_snapshot(h)
    forbid_dispatch(monkeypatch)
    with pytest.raises(ValueError,match='consumer_not_passed'):await h.consume(bundle)
    assert await paid_snapshot(h)==before
    async with h.session_factory() as s:
        state=(await s.get(m.WorkflowRunModel,h.workflow.id)).metadata_json
        assert len(state['patch_attempts'])==2 and state['operator_edit_prior_outcomes'][0]['final_report']['pass'] is False
        assert await s.scalar(select(func.count()).select_from(m.PlanningArtifactVersionModel))==0


async def test_review_permission_does_not_apply_candidate_or_approve_writing(consumer,monkeypatch):
    h=consumer
    async with h.session_factory() as s:
        wf=await s.get(m.WorkflowRunModel,h.workflow.id)
        edit_row=await s.scalar(select(m.WorkflowRunModel).where(m.WorkflowRunModel.workflow_type=='operator_outline_edit'))
        wf.metadata_json=deepcopy(edit_row.metadata_json['previous_state'])
        wf.current_step='rejected';wf.status='machine_blocked'
        authority=await s.get(m.WorkflowRunModel,h.approval)
        data=deepcopy(authority.metadata_json);data['capabilities']=['review_candidate'];authority.metadata_json=data
        await s.commit()
    bundle=await h.review()
    forbid_dispatch(monkeypatch)
    with pytest.raises(ValueError):await h.consume(bundle)
    async with h.session_factory() as s:
        with pytest.raises(ValueError,match='approval_binding_or_capability'):
            await edit.apply_operator_outline_edit(s,project_id=h.project.id,workflow_id=h.workflow.id,
                expected_candidate_sha256=h.plan['parent_sha256'],patches=h.plan['patches'],
                expected_plan_sha256=h.plan['plan_sha256'],approval_id=h.approval)
        assert await s.scalar(select(func.count()).select_from(m.ChapterModel))==0
        assert await s.scalar(select(func.count()).select_from(m.PlanningArtifactVersionModel))==0
        from bestseller.services.prewrite_review import assert_prewrite_review_approved
        with pytest.raises(PermissionError):await assert_prewrite_review_approved(s,h.project.slug)
