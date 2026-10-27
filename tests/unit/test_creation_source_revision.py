"""Real SQLite canonical versioning and exact retained clause provenance."""
from copy import deepcopy
from uuid import UUID
import pytest
from sqlalchemy import select
from test_creation_configuration_slice import store as _store
store=_store
from test_concept_contract import WINNER,SPINE
from bestseller.infra.db.models import ProjectModel,PlanningArtifactVersionModel,WorkflowRunModel
from bestseller.services.concept_contract import build_concept_contract
from bestseller.services.conception_snapshots import create_conception_snapshot_artifact,snapshot_hash
from bestseller.services.creation_source_revision import revise_empty_book_sources
pytestmark=[pytest.mark.unit,pytest.mark.asyncio]

async def setup(store):
 async with store.sessions() as s:
  p=await s.get(ProjectModel,store.pid)
  contract=build_concept_contract(winner=WINNER,story_spine=SPINE,target_chapters=30,genre='悬疑',sub_genre='悬疑推理')
  phases=['第1-10章调查信号','第11-20章破解规则','第21-30章完成救援']
  contract['seriality_proof']['phase_transitions']=phases
  contract['story_spine']['phase_desire_ladder']=phases
  c=await create_conception_snapshot_artifact(s,project_id=p.id,artifact_type='conception_snapshot',content={'premise':WINNER['concept'],'concept_contract':contract},status='canonical',idempotency_key='original')
  a=PlanningArtifactVersionModel(project_id=p.id,artifact_type='world_spec',version_no=1,schema_version='1.0',status='approved',content={'rules':[{'description':'耗电记账后才加领航额度。'}]})
  s.add(a);await s.flush()
  p.metadata_json={**p.metadata_json,'creation_canonical_snapshot_id':str(c.id),'creation_canonical_snapshot_hash':snapshot_hash(c.content),'concept_contract':contract}
  await s.commit()
  return dict(project_id=p.id,expected_snapshot_sha256=snapshot_hash(c.content),concept_contract=contract,canonical_updates={},clause_selections=[{'artifact_id':str(a.id),'kind':'world_spec','content_sha256':snapshot_hash(a.content),'source_pointer':'/rules/0/description','target_pointer':'/rules/0/description'}],superseded_workflow_ids=[],actor='operator',authorization_ref='human-repair',reason='Preserve inspected rules',explicit_creator_revision=True),deepcopy(c.content),a.id

async def test_source_versioned_no_raw_rewrite_or_grade_credit(store):
 args,old,aid=await setup(store)
 async with store.sessions() as s:
  receipt=await revise_empty_book_sources(s,**args);await s.commit()
 async with store.sessions() as s:
  p=await s.get(ProjectModel,store.pid);archive=await s.get(WorkflowRunModel,UUID(receipt['revision_id']))
  assert archive.metadata_json['before']['content']==old
  c=await s.get(PlanningArtifactVersionModel,UUID(receipt['snapshot_id']))
  assert c.status=='canonical' and c.content['retained_planning_clauses'][0]['value']=='耗电记账后才加领航额度。'
  assert c.content['concept_contract']['quality_evidence']['approved'] is False
  assert (await s.get(PlanningArtifactVersionModel,aid)).content=={'rules':[{'description':'耗电记账后才加领航额度。'}]}
  assert receipt==await revise_empty_book_sources(s,**args)
  assert p.metadata_json['protected_authored']=='保留'

@pytest.mark.parametrize('fault',['stale','unauthorized','foreign','hash','duplicate'])
async def test_invalid_source_revision_rejected_without_mutation(store,fault):
 args,old,aid=await setup(store)
 if fault=='stale':args['expected_snapshot_sha256']='bad'
 if fault=='unauthorized':args['explicit_creator_revision']=False
 if fault=='foreign':
  async with store.sessions() as s:
   a=await s.get(PlanningArtifactVersionModel,aid);a.status='draft';await s.commit()
 if fault=='hash':args['clause_selections'][0]['content_sha256']='bad'
 if fault=='duplicate':args['clause_selections']*=2
 async with store.sessions() as s:
  with pytest.raises(ValueError):await revise_empty_book_sources(s,**args)
  await s.rollback()
 async with store.sessions() as s:
  p=await s.get(ProjectModel,store.pid);c=await s.get(PlanningArtifactVersionModel,UUID(p.metadata_json['creation_canonical_snapshot_id']))
  assert c.content==old and c.status=='canonical'
  assert not list(await s.scalars(select(WorkflowRunModel).where(WorkflowRunModel.workflow_type=='creation_source_revision')))
