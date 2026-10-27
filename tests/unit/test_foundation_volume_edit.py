"""Real SQLite review ledger -> derived cohort -> real production selectors.

Historical component-review construction is a fixture boundary. Admission
run/root/transport, original-vs-derived binding, transactions and readers are real.
No production DB or real provider.
"""
from copy import deepcopy
import json
from uuid import UUID,uuid4

import pytest
from sqlalchemy import JSON,ForeignKeyConstraint,MetaData,select,func
from sqlalchemy.dialects.postgresql import JSONB
from test_planning_draft_admission import harness as harness  # noqa:F401
from test_volume_source_admission import volume as volume  # noqa:F401
from test_foundation_selection import seed_objects
from bestseller.domain.enums import ArtifactType
from bestseller.infra.db.models import (
    ProjectModel,WorkflowRunModel,PlanningArtifactVersionModel as Artifact,
    ChapterModel,ChapterDraftVersionModel,CanonFactModel,TimelineEventModel,
    ExecutionOperationModel,LlmRunModel,ExecutionBudgetModel,
)
from bestseller.services import (
    foundation_volume_edit as edit,foundation_selection as selection,
    foundation_revision_selection as source,planning_draft_admission as admission,workflows,llm,
)
from bestseller.services.prompt_source_policy import content_hash
from bestseller.services.conception_snapshots import snapshot_hash
from bestseller.services.budget import bind_execution_scope
from bestseller.services.planning_quote_evidence import quote_contract
from bestseller.services.lossless_source_encoding import decode_lossless_source

pytestmark=[pytest.mark.unit,pytest.mark.asyncio]
SCOPE='audit:admission-fixture'


@pytest.fixture
async def rig(volume,monkeypatch):
    h=volume
    # Existing paid-gateway fixture creates LLM/operation/attempt/budget tables;
    # add actual source/consumer tables without changing production schemas.
    engine=h.session_factory.kw['bind']
    for model in (ProjectModel,WorkflowRunModel,Artifact,ChapterModel,ChapterDraftVersionModel,CanonFactModel,TimelineEventModel):
        table=model.__table__.to_metadata(MetaData())
        for col in table.c:
            if isinstance(col.type,JSONB):col.type=JSON()
            if col.server_default is not None and '::' in str(col.server_default.arg):col.server_default=None
        for constraint in list(table.constraints):
            if isinstance(constraint,ForeignKeyConstraint):table.constraints.remove(constraint)
        async with engine.begin() as connection:await connection.run_sync(table.create)
    project,canonical,_,_,seed=seed_objects()
    project.metadata_json={**project.metadata_json,'execution_scope_id':SCOPE}
    h.project=project
    revision=WorkflowRunModel(id=uuid4(),project_id=project.id,workflow_type='creation_source_dependency_revision',
        scope_type='project',scope_id=project.id,status='completed',metadata_json={})
    candidates={}
    for kind in selection.KINDS:
        content=deepcopy(seed[kind].content)
        if kind=='volume_plan':
            content=deepcopy(h.candidate['volumes'])
            for item in content:item['_meta']={'llm_run_id':'retained-original-only','source_projection_sha256':'old-paid-projection'}
        row=Artifact(id=uuid4(),project_id=project.id,artifact_type=kind,scope_ref_id=None,version_no=1,
            status='candidate',schema_version='fixture.v1',source_run_id=revision.id,content=content)
        candidates[kind]=row
    revision.metadata_json={'candidate_ids':{kind:str(row.id) for kind,row in candidates.items()}}
    review=WorkflowRunModel(id=h.workflow.id,project_id=project.id,workflow_type='review_volume_source_admission',
        scope_type='project',scope_id=project.id,status='completed',metadata_json={})
    h.workflow=review
    for kind in selection.KINDS[:3]:setattr(h,kind,deepcopy(candidates[kind].content))
    async def components(session,settings,*,project,revision,volume):
        rows={kind:await session.get(Artifact,UUID(identity)) for kind,identity in revision.metadata_json['candidate_ids'].items()}
        return {'schema':'fixture-source-components','revision_id':str(revision.id),
            'canonical':project.metadata_json['creation_canonical_snapshot_hash'],
            'sources':{kind:{'artifact_id':str(row.id),'content_hash':snapshot_hash(row.content),
                'version':row.version_no,'status':row.status} for kind,row in rows.items() if kind!='volume_plan'}}
    monkeypatch.setattr(source,'validate_volume_foundation_components',components)
    async with h.session_factory() as session:
        session.add_all([project,canonical,revision,review,*candidates.values()]);await session.flush()
        comp=await components(session,h.settings,project=project,revision=revision,volume=candidates['volume_plan'])
        review.metadata_json={'source_dependency_revision_id':str(revision.id),'volume_candidate_id':str(candidates['volume_plan'].id),
            'volume_content_hash':content_hash(candidates['volume_plan'].content),'source_component_receipt_hash':content_hash(comp)}
        await session.commit()
    h.revision=revision;h.review=review;h.candidates=candidates;h.canonical=canonical
    h.settings_resolved=h.settings
    monkeypatch.setattr(workflows,'load_settings',lambda:h.settings)
    monkeypatch.setattr(workflows,'apply_runtime_llm_profile',lambda settings:settings)
    # Native source input itself is the existing volume fixture boundary;
    # all physical admission proving and source-row binding remain actual.
    h.schema=admission.VOLUME_SCHEMA
    async def transport(request,role):
        h.requests.append(request)
        if request.prompt_version==admission.VOLUME_QUOTE_SCHEMA:
            if 'source_review_data=' in request.user_prompt:
                encoded,_=json.JSONDecoder().raw_decode(request.user_prompt.split('source_review_data=',1)[1]);data=decode_lossless_source(encoded)
            else:data=json.loads(request.user_prompt)
            contract=quote_contract(data['input'])
            value={key:contract[key] for key in ('schema','input_sha256','candidate_sha256','catalog_sha256')}
            value.update(json.loads(h.raw))
            raw=json.dumps(value)
        else:raw=h.raw
        calls=[{'id':'actual-fixture-tool','type':'function','function':{'name':'submit_planning_admission','arguments':raw}}]
        return '',10,5,'tool_calls',calls,{'role':'assistant','content':None,'tool_calls':deepcopy(calls)}
    monkeypatch.setattr(llm,'_call_litellm',transport)
    return h


async def reviewed(h,*,schema=None):
    async with h.session_factory() as session:
        with bind_execution_scope(SCOPE):
            receipt=await admission.assess_planning_draft_admission(session,h.settings,**h.volume_kwargs(),
                commercial_report=h.commercial,admission_schema=schema)
        review=await session.get(WorkflowRunModel,h.review.id)
        review.metadata_json={**review.metadata_json,'source_hard_admission_receipt':receipt}
        await session.commit()
    return receipt


def kwargs(h):
    return dict(project_id=h.project.id,revision_id=h.revision.id,admission_workflow_id=h.review.id,
        expected_candidate_sha256=content_hash(h.candidates['volume_plan'].content),actor='fixture-verified-review-consumer')


async def snapshot(session,h):
    project=await session.get(ProjectModel,h.project.id,populate_existing=True)
    rows=list(await session.scalars(select(Artifact).where(Artifact.project_id==h.project.id).order_by(Artifact.id)))
    owners=list(await session.scalars(select(WorkflowRunModel).where(WorkflowRunModel.project_id==h.project.id).order_by(WorkflowRunModel.id)))
    return {'metadata':deepcopy(project.metadata_json),
        'rows':[(str(x.id),x.status,x.version_no,str(x.source_run_id),content_hash(x.content)) for x in rows],
        'workflows':[(str(x.id),x.status,content_hash(x.metadata_json)) for x in owners]}


@pytest.mark.parametrize('schema',[admission.VOLUME_SCHEMA,admission.VOLUME_QUOTE_SCHEMA])
async def test_actual_review_consumption_select_read_downstream_and_idempotence(rig,schema):
    h=rig;receipt=await reviewed(h,schema=schema)
    originals={kind:edit._source_record(row) for kind,row in h.candidates.items()}
    async with h.session_factory() as session:
        selected=await edit.complete_reviewed_volume_foundation(session,h.settings,**kwargs(h));await session.commit()
    async with h.session_factory() as session:
        project=await session.get(ProjectModel,h.project.id)
        reread=await selection.select_completed_foundation(session,project=project,workflow_id=UUID(selected['workflow_id']),settings=h.settings)
        assert reread==selected and reread['reviewed_volume_edit']['new_grade_or_credit_granted'] is False
        rows=await selection.load_selected_foundation_artifacts(session,project=project,settings=h.settings)
        for kind,row in rows.items():
            assert row.id!=h.candidates[kind].id and row.source_run_id==UUID(selected['workflow_id'])
            original=await session.get(Artifact,h.candidates[kind].id,populate_existing=True)
            assert edit._source_record(original)==originals[kind]
            downstream=await workflows.get_latest_approved_planning_artifact(session,project_id=project.id,artifact_type=ArtifactType(kind))
            assert downstream.id==row.id
        before=await snapshot(session,h)
        repeated_params=kwargs(h)
        for name in ('project_id','revision_id','admission_workflow_id'):
            repeated_params[name]=str(repeated_params[name]).upper()
        repeated=await edit.complete_reviewed_volume_foundation(session,h.settings,**repeated_params)
        assert repeated==selected
        await session.commit();assert await snapshot(session,h)==before
        budget=await session.get(ExecutionBudgetModel,SCOPE);assert budget.calls_used==1
        review=await session.get(WorkflowRunModel,h.review.id)
        assert review.metadata_json['source_hard_admission_receipt']==receipt
    assert len(h.requests)==1


@pytest.mark.parametrize('fault',['missing','failed_review','rejected','invalid_raw','old_candidate','source_body','source_version',
    'source_status','foreign_review','canonical','expected_hash','transport_missing','root_return_changed'])
async def test_invalid_or_stale_review_never_creates_approved_or_pin(rig,fault):
    h=rig
    if fault!='missing':await reviewed(h)
    async with h.session_factory() as session:
        review=await session.get(WorkflowRunModel,h.review.id)
        if fault=='failed_review':review.status='failed'
        elif fault in {'rejected','invalid_raw'}:
            receipt=deepcopy(review.metadata_json['source_hard_admission_receipt'])
            if fault=='rejected':receipt['draft_eligible']=False
            else:receipt['raw_grade']='{}'
            review.metadata_json={**review.metadata_json,'source_hard_admission_receipt':receipt}
        elif fault=='old_candidate':review.metadata_json={**review.metadata_json,'volume_candidate_id':str(uuid4())}
        elif fault in {'source_body','source_version','source_status'}:
            row=await session.get(Artifact,h.candidates['world_spec'].id)
            if fault=='source_body':row.content={**row.content,'rule':'Changed authoritative rule'}
            elif fault=='source_version':row.version_no+=1
            else:row.status='superseded'
        elif fault=='foreign_review':review.project_id=uuid4()
        elif fault=='canonical':
            project=await session.get(ProjectModel,h.project.id);project.metadata_json={**project.metadata_json,'creation_canonical_snapshot_hash':'f'*64}
        elif fault in {'transport_missing','root_return_changed'}:
            receipt=review.metadata_json['source_hard_admission_receipt'];run=await session.get(LlmRunModel,UUID(receipt['llm_run_id']))
            operations=list(await session.scalars(select(ExecutionOperationModel)))
            if fault=='transport_missing':
                row=next(x for x in operations if x.status=='transport_result');await session.delete(row)
            else:
                row=await session.get(ExecutionOperationModel,run.metadata_json['execution_receipt']['operation_key'])
                row.result_json={**row.result_json,'tool_calls':[]}
        await session.commit();before=await snapshot(session,h)
        params=kwargs(h)
        if fault=='expected_hash':params['expected_candidate_sha256']='wrong'
        with pytest.raises(ValueError):await edit.complete_reviewed_volume_foundation(session,h.settings,**params)
        await session.commit()
        assert await snapshot(session,h)==before
        assert await session.scalar(select(func.count()).select_from(Artifact).where(Artifact.project_id==h.project.id,Artifact.status=='approved'))==0


@pytest.mark.parametrize('stage',['second_asset','pin'])
async def test_failure_after_writes_rolls_back_entire_savepoint_even_if_caller_commits(rig,monkeypatch,stage):
    h=rig;await reviewed(h)
    if stage=='second_asset':
        original=edit._derived_content
        def fail(source,*args):
            if source.artifact_type=='world_spec':raise RuntimeError('fixture-stage-failure')
            return original(source,*args)
        monkeypatch.setattr(edit,'_derived_content',fail)
    else:
        original=selection.pin_completed_foundation
        async def fail(*args,**kwargs):
            await original(*args,**kwargs)
            raise RuntimeError('fixture-stage-failure')
        monkeypatch.setattr(selection,'pin_completed_foundation',fail)
    async with h.session_factory() as session:
        before=await snapshot(session,h)
        with pytest.raises(RuntimeError,match='fixture-stage-failure'):
            await edit.complete_reviewed_volume_foundation(session,h.settings,**kwargs(h))
        await session.commit()
        assert await snapshot(session,h)==before


async def test_legitimate_physically_proved_rejection_is_not_adopted(rig):
    h=rig
    action='Mira fires without touching the tool.'
    async with h.session_factory() as session:
        volume=await session.get(Artifact,h.candidates['volume_plan'].id)
        content=deepcopy(volume.content);content[0]['information_flow_scene_cards'][0]['actual_action']=action
        volume.content=content;h.candidates['volume_plan'].content=deepcopy(content)
        world=await session.get(Artifact,h.candidates['world_spec'].id)
        world.content={**world.content,'requirement':'Touching the tool is required.'}
        h.world_spec=deepcopy(world.content);h.candidates['world_spec'].content=deepcopy(world.content)
        h.candidate={'volumes':[{key:deepcopy(value) for key,value in content[0].items() if key!='_meta'}]}
        await session.flush()
        revision=await session.get(WorkflowRunModel,h.revision.id)
        components=await source.validate_volume_foundation_components(session,h.settings,project=h.project,revision=revision,volume=volume)
        review=await session.get(WorkflowRunModel,h.review.id)
        review.metadata_json={**review.metadata_json,'volume_content_hash':content_hash(content),
            'source_component_receipt_hash':content_hash(components)}
        await session.commit()
    path='volumes[0].information_flow_scene_cards[0]'
    h.raw=json.dumps({'draft_eligible':False,'hard_violations':[{'code':'ABILITY_RULE_CONFLICT','path':path,
        'evidence':action,'source_requirement':'Touching the tool is required.',
        'source_refs':['/approved_world_spec/requirement']}],'editorial_notes':[],
        'projection_checks':[{**target,'status':'conflict','explanation':'Tool activated without required touch.',
                              'related_violation_paths':[path]} for target in admission.projection_audit_targets(h.candidate)]})
    receipt=await reviewed(h)
    assert receipt['draft_eligible'] is False
    async with h.session_factory() as session:
        before=await snapshot(session,h)
        with pytest.raises(admission.PlanningDraftAdmissionError,match='hard_rejected'):
            await edit.complete_reviewed_volume_foundation(session,h.settings,**kwargs(h))
        await session.commit();assert await snapshot(session,h)==before
        assert (await session.get(WorkflowRunModel,h.review.id)).metadata_json['source_hard_admission_receipt']==receipt
    assert len(h.requests)==1


@pytest.mark.parametrize('fault',['source_version','source_body','receipt','physical','derived','canonical','consumer_kind'])
async def test_selected_cohort_revalidates_and_downstream_never_falls_back(rig,fault):
    h=rig;await reviewed(h)
    async with h.session_factory() as session:
        selected=await edit.complete_reviewed_volume_foundation(session,h.settings,**kwargs(h));await session.commit()
    async with h.session_factory() as reader:
        project=await reader.get(ProjectModel,h.project.id)
        await selection.load_selected_foundation_artifacts(reader,project=project,settings=h.settings)
        # Separate writer invalidates rows already present in reader's identity map.
        async with h.session_factory() as writer:
            if fault in {'source_version','source_body'}:
                row=await writer.get(Artifact,h.candidates['world_spec'].id)
                if fault=='source_version':row.version_no+=1
                else:row.content={**row.content,'unreviewed_rule':'Changed.'}
            elif fault=='receipt':
                row=await writer.get(WorkflowRunModel,h.review.id);data=deepcopy(row.metadata_json)
                data['source_hard_admission_receipt']['raw_grade']='{}';row.metadata_json=data
            elif fault=='physical':
                review=await writer.get(WorkflowRunModel,h.review.id);receipt=review.metadata_json['source_hard_admission_receipt']
                run=await writer.get(LlmRunModel,UUID(receipt['llm_run_id']))
                root=await writer.get(ExecutionOperationModel,run.metadata_json['execution_receipt']['operation_key'])
                root.result_json={**root.result_json,'tool_calls':[]}
            elif fault=='derived':
                row=await writer.get(Artifact,UUID(selected['artifact_ids']['volume_plan']))
                data=deepcopy(row.content);data[0]['unreviewed_body']='Changed.';row.content=data
            elif fault=='canonical':
                row=await writer.get(ProjectModel,h.project.id)
                row.metadata_json={**row.metadata_json,'creation_canonical_snapshot_hash':'f'*64}
            else:
                row=await writer.get(WorkflowRunModel,UUID(selected['workflow_id']))
                row.metadata_json={**row.metadata_json,'foundation_consumer_kind':'unknown-subtype'}
            await writer.commit()
        with pytest.raises(ValueError):
            await selection.load_selected_foundation_artifacts(reader,project=project,settings=h.settings)
        with pytest.raises(ValueError):
            await workflows.get_latest_approved_planning_artifact(reader,project_id=project.id,artifact_type=ArtifactType.VOLUME_PLAN)
    assert len(h.requests)==1


async def test_lost_pin_does_not_allow_latest_approved_derived_assets(rig):
    h=rig;await reviewed(h)
    async with h.session_factory() as session:
        await edit.complete_reviewed_volume_foundation(session,h.settings,**kwargs(h));await session.commit()
        project=await session.get(ProjectModel,h.project.id)
        data=deepcopy(project.metadata_json);data.pop(selection.POINTER);project.metadata_json=data
        await session.commit()
        with pytest.raises(ValueError,match='reviewed_edit_pointer_required'):
            await workflows.get_latest_approved_planning_artifact(session,project_id=project.id,artifact_type=ArtifactType.VOLUME_PLAN)


async def test_dirty_source_read_rejects_without_discarding_callers_changes(rig):
    h=rig;await reviewed(h)
    async with h.session_factory() as session:
        await edit.complete_reviewed_volume_foundation(session,h.settings,**kwargs(h));await session.commit()
        project=await session.get(ProjectModel,h.project.id)
        row=await session.get(Artifact,h.candidates['world_spec'].id)
        row.content={**row.content,'pending_authored_clause':'Keep my unsaved edit.'}
        with pytest.raises(ValueError,match='unflushed_source_change'):
            await selection.load_selected_foundation_artifacts(session,project=project,settings=h.settings)
        assert row.content['pending_authored_clause']=='Keep my unsaved edit.'
        assert row in session.dirty


async def test_dirty_caller_is_rejected_before_savepoint_flush(rig):
    h=rig;await reviewed(h)
    async with h.session_factory() as session:
        project=await session.get(ProjectModel,h.project.id)
        project.title='Pending caller edit'
        with pytest.raises(ValueError,match='clean_session_required'):
            await edit.complete_reviewed_volume_foundation(session,h.settings,**kwargs(h))
        assert project.title=='Pending caller edit' and project in session.dirty


async def test_newer_unreviewed_candidate_cannot_reactivate_older_review(rig):
    h=rig;await reviewed(h)
    async with h.session_factory() as session:
        old=await session.get(Artifact,h.candidates['volume_plan'].id)
        session.add(Artifact(project_id=h.project.id,artifact_type='volume_plan',scope_ref_id=None,
            version_no=old.version_no+1,status='candidate',schema_version=old.schema_version,
            source_run_id=h.revision.id,content=deepcopy(old.content)))
        await session.commit();before=await snapshot(session,h)
        with pytest.raises(ValueError,match='superseded_candidate'):
            await edit.complete_reviewed_volume_foundation(session,h.settings,**kwargs(h))
        await session.commit();assert await snapshot(session,h)==before


async def test_repeat_does_not_repin_a_different_current_selection(rig):
    h=rig;await reviewed(h)
    async with h.session_factory() as session:
        await edit.complete_reviewed_volume_foundation(session,h.settings,**kwargs(h));await session.commit()
        project=await session.get(ProjectModel,h.project.id)
        project.metadata_json={**project.metadata_json,selection.POINTER:{'workflow_id':str(uuid4())}}
        await session.commit();before=await snapshot(session,h)
        with pytest.raises(ValueError,match='already_consumed_selection_changed'):
            await edit.complete_reviewed_volume_foundation(session,h.settings,**kwargs(h))
        await session.commit();assert await snapshot(session,h)==before


@pytest.mark.parametrize('fault',['root','run','consumer_mapping','consumer_scope','pin_removed','pin_switched'])
async def test_strongly_cached_proof_and_pin_cannot_hide_external_changes(rig,fault):
    h=rig;receipt=await reviewed(h)
    async with h.session_factory() as session:
        selected=await edit.complete_reviewed_volume_foundation(session,h.settings,**kwargs(h));await session.commit()
    async with h.session_factory() as reader:
        project=await reader.get(ProjectModel,h.project.id)
        held_run=await reader.get(LlmRunModel,UUID(receipt['llm_run_id']))
        held_root=await reader.get(ExecutionOperationModel,held_run.metadata_json['execution_receipt']['operation_key'])
        held_consumer=await reader.get(WorkflowRunModel,UUID(selected['workflow_id']))
        held_review=await reader.get(WorkflowRunModel,h.review.id)
        held_source=await reader.get(Artifact,h.candidates['world_spec'].id)
        assert all(x is not None for x in (held_run,held_root,held_consumer,held_review,held_source))
        root_before=deepcopy(held_root.result_json)
        await selection.load_selected_foundation_artifacts(reader,project=project,settings=h.settings)
        async with h.session_factory() as writer:
            if fault=='root':
                row=await writer.get(ExecutionOperationModel,held_root.key);row.result_json={**row.result_json,'content':'changed'}
            elif fault=='run':
                row=await writer.get(LlmRunModel,held_run.id);row.output_tokens+=1
            elif fault in {'consumer_mapping','consumer_scope'}:
                row=await writer.get(WorkflowRunModel,held_consumer.id)
                if fault=='consumer_scope':row.scope_id=uuid4()
                else:row.metadata_json={**row.metadata_json,'artifact_ids':{}}
            else:
                row=await writer.get(ProjectModel,project.id);data=deepcopy(row.metadata_json)
                if fault=='pin_removed':data.pop(selection.POINTER)
                else:data[selection.POINTER]={**data[selection.POINTER],'workflow_id':str(uuid4())}
                row.metadata_json=data
            await writer.commit()
        with pytest.raises(ValueError):
            await selection.load_selected_foundation_artifacts(reader,project=project,settings=h.settings)
        with pytest.raises(ValueError):
            await workflows.get_latest_approved_planning_artifact(reader,project_id=project.id,artifact_type=ArtifactType.VOLUME_PLAN)
        # Proof verification reads detached snapshots; it does not overwrite
        # the caller's explicitly held old proof object to make tests pass.
        if fault=='root':assert held_root.result_json==root_before


async def test_orphan_derived_asset_without_pin_never_falls_back(rig):
    h=rig;await reviewed(h)
    async with h.session_factory() as session:
        selected=await edit.complete_reviewed_volume_foundation(session,h.settings,**kwargs(h));await session.commit()
        project=await session.get(ProjectModel,h.project.id)
        meta=deepcopy(project.metadata_json);meta.pop(selection.POINTER);project.metadata_json=meta
        owner=await session.get(WorkflowRunModel,UUID(selected['workflow_id']));await session.delete(owner)
        await session.commit()
        assert await session.get(Artifact,UUID(selected['artifact_ids']['volume_plan'])) is not None
        with pytest.raises(ValueError,match='reviewed_edit_pointer_required'):
            await workflows.get_latest_approved_planning_artifact(session,project_id=project.id,artifact_type=ArtifactType.VOLUME_PLAN)


async def test_unflushed_physical_proof_change_is_rejected_without_overwrite(rig):
    h=rig;receipt=await reviewed(h)
    async with h.session_factory() as session:
        await edit.complete_reviewed_volume_foundation(session,h.settings,**kwargs(h));await session.commit()
        project=await session.get(ProjectModel,h.project.id)
        run=await session.get(LlmRunModel,UUID(receipt['llm_run_id']))
        root=await session.get(ExecutionOperationModel,run.metadata_json['execution_receipt']['operation_key'])
        root.result_json={**root.result_json,'content':'pending-authored-investigation'}
        with pytest.raises(ValueError,match='unflushed_source_change'):
            await selection.load_selected_foundation_artifacts(session,project=project,settings=h.settings)
        assert root.result_json['content']=='pending-authored-investigation' and root in session.dirty


async def test_runtime_project_edits_survive_read_only_selection_validation(rig):
    h=rig;await reviewed(h)
    async with h.session_factory() as session:
        await edit.complete_reviewed_volume_foundation(session,h.settings,**kwargs(h));await session.commit()
        project=await session.get(ProjectModel,h.project.id)
        project.metadata_json={**project.metadata_json,'unrelated_runtime_update':'keep pending'}
        rows=await selection.load_selected_foundation_artifacts(session,project=project,settings=h.settings)
        assert len(rows)==4
        assert project.metadata_json['unrelated_runtime_update']=='keep pending' and project in session.dirty
