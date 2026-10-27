"""Real SQLite rejects stale/foreign/unpassed revision reviews before IO."""
from copy import deepcopy
from uuid import uuid4
import pytest
from test_creation_configuration_slice import store as _store
store=_store
from bestseller.infra.db.models import ProjectModel,WorkflowRunModel,PlanningArtifactVersionModel
from bestseller.services.conception_snapshots import snapshot_hash
from bestseller.services.foundation_revision_selection import validate_revision_review
pytestmark=[pytest.mark.unit,pytest.mark.asyncio]

@pytest.mark.parametrize('fault',['source','candidate_body','candidate_id','scope','unpassed'])
async def test_closed_revision_review_binding(store,fault):
 async with store.sessions() as s:
  p=await s.get(ProjectModel,store.pid);p.metadata_json={**p.metadata_json,'creation_canonical_snapshot_hash':'c'*64}
  revision=WorkflowRunModel(project_id=p.id,workflow_type='creation_source_revision',status='completed',scope_type='project',scope_id=p.id,metadata_json={})
  s.add(revision);await s.flush()
  candidates={}
  for kind in ['book_spec','world_spec','cast_spec']:
   a=PlanningArtifactVersionModel(project_id=p.id,artifact_type=kind,version_no=1,schema_version='1',status='candidate',content={'story':kind},source_run_id=revision.id)
   s.add(a);await s.flush();candidates[kind]=a
  metadata={'creator_revision_id':str(revision.id),'candidate_scope':'world','canonical_snapshot_sha256':'c'*64,'candidate_ids':{k:str(a.id) for k,a in candidates.items()},'candidate_content_sha256':{k:snapshot_hash(a.content) for k,a in candidates.items()},'source_bound_review':{'decision':'uncertain'}}
  w=WorkflowRunModel(project_id=p.id,workflow_type='review_revised_foundation_sources',status='completed',scope_type='project',scope_id=p.id,metadata_json=deepcopy(metadata))
  if fault=='source':metadata['canonical_snapshot_sha256']='d'*64
  if fault=='candidate_body':candidates['cast_spec'].content={'story':'changed after review'}
  if fault=='candidate_id':metadata['candidate_ids']['world_spec']=str(uuid4())
  if fault=='scope':metadata['candidate_scope']='cast'
  w.metadata_json=metadata;s.add(w);await s.commit()
  with pytest.raises(ValueError,match='foundation_revision_'):
   await validate_revision_review(s,store.settings,project=p,revision=revision,review_id=w.id,part='world',candidates=candidates)
  await s.rollback()
