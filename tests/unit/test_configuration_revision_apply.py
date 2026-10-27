"""Real file SQLite, explicit operator apply, sources and ledger retained."""
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from uuid import UUID
import pytest
from sqlalchemy import select
from sqlalchemy.schema import CreateTable
from test_creation_configuration_slice import store as _base_store, submission
store = _base_store
from bestseller.infra.db.models import (ProjectModel,WorkflowRunModel,PlanningArtifactVersionModel,
    ExecutionBudgetModel,ExecutionOperationModel)
from bestseller.services import creation_configuration as cfg
from bestseller.services.execution_authorization import revise_provider_completion_objective
from bestseller.services.prompt_source_policy import content_hash
from bestseller.services.gate_registry import project_blocks_all_prose_generation
pytestmark=[pytest.mark.unit,pytest.mark.asyncio]

async def proposal(store):
    async with store.sessions() as s:
        p=await s.get(ProjectModel,store.pid)
        p.metadata_json={**p.metadata_json,'book_design_snapshot_status':'approved','old_spent':[{'result':'rejected','attempts':2}], 'old_unknown':'retained'}
        s.add(PlanningArtifactVersionModel(project_id=p.id,artifact_type='world_spec',version_no=1,schema_version='1.0',status='approved',content={'rules':['hard rule']}))
        await s.commit()
    async with store.sessions() as s:
        p=await s.get(ProjectModel,store.pid)
        value=submission(total=20,target=2000,end=20,purpose='complete_story')
        r=await cfg.save_project_configuration(s,p,value,expected_hash=None,expected_window_hash=None)
        await s.commit()
        return UUID(r['receipt_id']),cfg.configuration_revision_fingerprint(p),content_hash(r['diff']['requested'])

async def test_apply_versions_contract_preserves_sources_and_idempotence(store):
    proposal_id,before,ph=await proposal(store)
    async with store.sessions() as s:
        p=await s.get(ProjectModel,store.pid);old=deepcopy(p.metadata_json)
        r=await cfg.apply_project_configuration_proposal(s,project_id=p.id,proposal_id=proposal_id,
            expected_project_sha256=before,expected_proposal_sha256=ph,actor='operator',authorization_ref='human20',explicit_user_approval=True)
        await s.commit()
    async with store.sessions() as s:
        p=await s.get(ProjectModel,store.pid);assert (p.target_chapters,p.target_word_count)==(20,40000)
        assert p.metadata_json['old_spent']==old['old_spent'] and p.metadata_json['old_unknown']=='retained'
        assert p.metadata_json['book_design_snapshot_status']=='approved'
        assert project_blocks_all_prose_generation(p)
        a=await s.scalar(select(PlanningArtifactVersionModel));assert a.content=={'rules':['hard rule']} and a.status=='approved'
        archive=await s.get(WorkflowRunModel,UUID(r['revision_id']));assert archive.metadata_json['before']['metadata']==old
        assert await cfg.apply_project_configuration_proposal(s,project_id=p.id,proposal_id=proposal_id,
            expected_project_sha256=before,expected_proposal_sha256=ph,actor='operator',authorization_ref='human20',explicit_user_approval=True)==r

@pytest.mark.parametrize('fault',['unauthorized','stale','changed_proposal'])
async def test_apply_rejects_without_mutation(store,fault):
    proposal_id,before,ph=await proposal(store)
    async with store.sessions() as s:
        with pytest.raises(ValueError):
            await cfg.apply_project_configuration_proposal(s,project_id=store.pid,proposal_id=proposal_id,
                expected_project_sha256='bad' if fault=='stale' else before,
                expected_proposal_sha256='bad' if fault=='changed_proposal' else ph,actor='operator',authorization_ref='human20',explicit_user_approval=fault!='unauthorized')
        await s.rollback()
    async with store.sessions() as s:assert (await s.get(ProjectModel,store.pid)).target_chapters==30

async def test_objective_revision_never_grows_budget_or_completes_old_goal(store):
    async with store.engine.begin() as c:
        for model in (ExecutionBudgetModel,ExecutionOperationModel):
            await c.exec_driver_sql(str(CreateTable(model.__table__).compile(dialect=c.dialect)).replace('::jsonb',''))
    old={'mode':'completion_bound','status':'active','provider':'minimax','models':['only-minimax'],
        'endpoint_hosts':['allowed'],'objective':'first3','authorization_ref':'old-human',
        'aggregate_max_calls':None,'aggregate_max_tokens':None,'aggregate_deadline_at':None}
    async with store.sessions() as s:
        s.add(ExecutionBudgetModel(scope_id='isolated',max_calls=64,max_tokens=1000000,calls_used=222,
            tokens_charged=3455668,deadline_at=datetime.now(UTC)+timedelta(hours=1),status='active',
            metadata_json={'provider_authorization':old,'provider_authorizations':{'old':{'preserve':'exact'}}}))
        await s.commit()
        receipt=await revise_provider_completion_objective(s,scope_id='isolated',expected_authorization_sha256=content_hash(old),
            authorization_ref='human20',objective='samebook20',actor='operator',explicit_user_approval=True)
        await s.commit()
        b=await s.get(ExecutionBudgetModel,'isolated')
        assert (b.max_calls,b.max_tokens,b.calls_used,b.tokens_charged)==(64,1000000,222,3455668)
        assert b.metadata_json['provider_authorizations']=={'old':{'preserve':'exact'}}
        assert receipt['previous_authorization']==old and not receipt['old_objective_marked_complete']
        assert b.metadata_json['provider_authorization']['models']==old['models']

async def test_same_story_lineage_resealed_without_model_or_old_grade_credit(store):
    from test_concept_contract import WINNER,SPINE
    from bestseller.services.concept_contract import build_concept_contract,validate_concept_contract
    from bestseller.services.creation_intent_contract import build_creation_intent_contract
    from bestseller.services.genre_intent_contract import contract_from_selection
    from bestseller.services.conception_snapshots import create_conception_snapshot_artifact,snapshot_hash
    from bestseller.services.conception_approval_bridge import prepare_adopted_design_snapshot
    contract=build_concept_contract(winner=WINNER,story_spine=SPINE,target_chapters=30,genre='悬疑',sub_genre='悬疑推理')
    intent=build_creation_intent_contract(contract_from_selection({'channel':'male','genre':'xuanhuan'}),chapter_count=30).model_dump(mode='json')
    async with store.sessions() as s:
        p=await s.get(ProjectModel,store.pid)
        canonical=await create_conception_snapshot_artifact(s,project_id=p.id,artifact_type='conception_snapshot',
            content={'premise':WINNER['concept'],'concept_contract':contract,'story_spine':contract['story_spine']},
            status='canonical',idempotency_key='old-source',created_by='fixture')
        oldid=canonical.id;oldcontent=deepcopy(canonical.content)
        p.metadata_json={**p.metadata_json,'book_design_snapshot_status':'approved','creation_canonical_snapshot_id':str(oldid),
            'creation_canonical_snapshot_hash':snapshot_hash(canonical.content),'concept_contract':contract,
            'creation_intent_contract':intent,'creation_intent_contract_hash':content_hash(intent),
            'creation_protagonist_name':'林默','conception_approved':True,'premise':WINNER['concept']}
        await s.commit()
        r=await cfg.save_project_configuration(s,p,submission(total=20,end=20,purpose='complete_story'),expected_hash=None,expected_window_hash=None)
        await s.commit()
        await cfg.apply_project_configuration_proposal(s,project_id=p.id,proposal_id=UUID(r['receipt_id']),
            expected_project_sha256=cfg.configuration_revision_fingerprint(p),expected_proposal_sha256=content_hash(r['diff']['requested']),
            actor='fixture-operator',authorization_ref='human20',explicit_user_approval=True)
        receipt=await cfg.complete_empty_configuration_revision_lineage(s,p)
        await s.commit()
        assert not validate_concept_contract(p.metadata_json['concept_contract'],target_chapters=20)
        assert p.metadata_json['concept_contract']['hook_card']['story_motion']==contract['hook_card']['story_motion']
        assert receipt['new_model_grade_claimed'] is False
        archive=await s.get(WorkflowRunModel,UUID(p.metadata_json['configuration_revision']['id']))
        assert archive.metadata_json['lineage_before']['content']==oldcontent
        assert (await s.get(PlanningArtifactVersionModel,oldid)).status=='superseded'
        assert await prepare_adopted_design_snapshot(s,p)
        assert await cfg.complete_empty_configuration_revision_lineage(s,p)==receipt
