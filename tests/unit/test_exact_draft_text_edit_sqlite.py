from copy import deepcopy
import hashlib
from uuid import uuid4
from sqlalchemy import select
import pytest
from test_chapter_outline_readiness import rig,sqlite_identity_store  # noqa:F401
from test_prefix_production_entry_fault_replay import entry_case  # noqa:F401
from test_prefix_window_activation_sqlite import setup
from bestseller.infra.db.models import ProjectModel,ChapterModel,ChapterDraftVersionModel
from bestseller.services.exact_draft_text_edit import prepare_exact_draft_text_edit,apply_exact_draft_text_edit
from bestseller.services.operator_action_approval import record_operator_action_approval
pytestmark=[pytest.mark.unit,pytest.mark.asyncio]

@pytest.mark.parametrize('fault',[None,'hash','duplicate','overlap','missing_approval','stale','promoted'])
async def test_exact_authorized_edit_is_append_only_bound_and_zero_model(entry_case,monkeypatch,fault):
 c=entry_case;await setup(c,monkeypatch)
 async with c.sessions() as s:
  p=await s.get(ProjectModel,c.project_id);ch=await s.scalar(select(ChapterModel).where(ChapterModel.project_id==p.id))
  body='Mara cannot verify the old witness list. The bell remains on the table.'
  if fault=='duplicate':body+=' The bell remains on the table.'
  d=ChapterDraftVersionModel(id=uuid4(),project_id=p.id,chapter_id=ch.id,version_no=1,content_md=body,is_current=True,promotion_state='promoted' if fault=='promoted' else 'candidate',assembled_from_scene_draft_ids=['chapter_first_scene:'+str(uuid4())],llm_run_id=None)
  s.add(d);ch.metadata_json={**ch.metadata_json,'chapter_first_local_repair_count':2,'chapter_first_full_regeneration_count':1,'auto_repair_total_attempts':3}
  await s.commit();before=deepcopy(ch.metadata_json);sends=len(c.sends)
  edits=[{'before':'The bell remains on the table.','after':'The bell stays on the table.'}]
  if fault=='overlap':edits.append({'before':'bell remains','after':'bell stays'})
  kwargs=dict(project_id=p.id,chapter_id=ch.id,draft_id=d.id,body_sha256='bad' if fault=='hash' else hashlib.sha256(body.encode()).hexdigest(),edits=edits)
  if fault in ['hash','duplicate','overlap','promoted']:
   with pytest.raises(ValueError):await prepare_exact_draft_text_edit(s,**kwargs)
  else:
   plan=await prepare_exact_draft_text_edit(s,**kwargs)
   aid=None
   if fault!='missing_approval':
    aid=await record_operator_action_approval(s,purpose='explicit_draft_text_edit',binding=plan,capabilities=['apply_exact_text_edits'],actor='isolated-test',authorization_ref='isolated-only',explicit_user_approval=True)
   if fault=='stale':d.content_md+=' New source.';await s.flush()
   if fault:
    with pytest.raises(ValueError):await apply_exact_draft_text_edit(s,plan=plan,approval_id=aid)
   else:
    result=await apply_exact_draft_text_edit(s,plan=plan,approval_id=aid);await s.commit()
    new=await s.scalar(select(ChapterDraftVersionModel).where(ChapterDraftVersionModel.chapter_id==ch.id,ChapterDraftVersionModel.is_current.is_(True)))
    assert new.version_no==2 and new.llm_run_id is None and new.promotion_state=='candidate'
    assert new.content_md==body.replace(edits[0]['before'],edits[0]['after'])
    assert new.promotion_metadata['model_generated_this_version'] is False
    assert await apply_exact_draft_text_edit(s,plan=plan,approval_id=aid)==result
    assert d.content_md==body and not d.is_current
    for key in ['chapter_first_local_repair_count','chapter_first_full_regeneration_count','auto_repair_total_attempts']:assert ch.metadata_json[key]==before[key]
  assert len(c.sends)==sends
