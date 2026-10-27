# ruff: noqa: F811
"""Real approved prefix/materialization guard; synthetic provider only in fixture."""
from copy import deepcopy
from uuid import UUID
import json
import pytest
from sqlalchemy import select
from test_chapter_outline_readiness import rig,sqlite_identity_store  # noqa:F401
from test_prefix_production_entry_fault_replay import entry_case  # noqa:F401
from test_prefix_window_activation_sqlite import setup
from bestseller.infra.db.models import ProjectModel,ChapterModel,SceneCardModel,PlanningArtifactVersionModel
from bestseller.services.chapter_writer_delivery import load_delivery_blocks
pytestmark=[pytest.mark.unit,pytest.mark.asyncio]

async def test_exact_approved_task_includes_full_source_and_rejects_changed_scene(entry_case,monkeypatch):
 c=entry_case;result=await setup(c,monkeypatch);paid=len(c.sends)
 async with c.sessions() as s:
  p=await s.get(ProjectModel,c.project_id)
  chapter=await s.scalar(select(ChapterModel).where(ChapterModel.project_id==p.id))
  scenes=list(await s.scalars(select(SceneCardModel).where(SceneCardModel.chapter_id==chapter.id).order_by(SceneCardModel.scene_number)))
  artifact=await s.get(PlanningArtifactVersionModel,UUID(result['artifact_id']))
  blocks=await load_delivery_blocks(s,c.settings,project=p,chapter=chapter,scenes=scenes)
  assert len(blocks)==1 and blocks[0].required and blocks[0].trim_policy=='preserve'
  logical=json.loads(blocks[0].text.split('\n',2)[2]);approved=artifact.content['chapters'][0]
  assert logical['chapter_id']==str(chapter.id) and logical['artifact_id']==str(artifact.id)
  for expected,actual in zip(approved['scenes'],logical['scenes'],strict=True):
   assert actual['exit_state']==expected['exit_state']
   if 'action_sequence' in expected:assert actual['action_sequence']==expected['action_sequence']
  old=deepcopy(scenes[0].exit_state);scenes[0].exit_state={'summary':'Invented launch result, not approved.'};await s.flush()
  with pytest.raises(ValueError):await load_delivery_blocks(s,c.settings,project=p,chapter=chapter,scenes=scenes)
  scenes[0].exit_state=old;await s.rollback()
 assert len(c.sends)==paid
