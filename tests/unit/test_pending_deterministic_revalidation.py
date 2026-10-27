# ruff: noqa: F811
"""Unrelated-source negative guards and actual SQLite receipt approval."""
from copy import deepcopy
from uuid import uuid4
import pytest
from test_draft_continuity import sql,BODY  # noqa:F401
from bestseller.infra.db import models as m
from bestseller.services import draft_continuity as dc,draft_continuity_revalidation as service
from bestseller.services.prompt_source_policy import content_hash
from bestseller.services.operator_action_approval import record_operator_action_approval
pytestmark=pytest.mark.unit

@pytest.mark.parametrize('fault',[None,'missing','body','proof','approval','policy','blocked_report'])
@pytest.mark.asyncio
async def test_current_receipt_is_explicit_and_original_proof_never_changes(sql,monkeypatch,fault):
 from bestseller.services.quality_report_claim import graded_text_fingerprint
 monkeypatch.setattr(dc,'hard_policy_hash',lambda:'b'*64)
 async with sql() as s:
  project=m.ProjectModel(slug='tide-observatory',title='Tide',genre='science-fiction',target_chapters=3,target_word_count=6000,metadata_json={});s.add(project);await s.flush()
  chapter=m.ChapterModel(project_id=project.id,chapter_number=1,chapter_goal='Launch the instrument.',metadata_json={});s.add(chapter);await s.flush()
  draft=m.ChapterDraftVersionModel(project_id=project.id,chapter_id=chapter.id,version_no=3,content_md=BODY,is_current=False,promotion_state='candidate');s.add(draft);await s.flush()
  original={'draft_id':str(draft.id),'body_sha256':dc.body_hash(BODY),'source_sha256':'s'*64,'hard_policy_sha256':'a'*64}
  saved=deepcopy(original);tag={'evaluation_id':str(uuid4())}
  report=m.ChapterQualityReportModel(chapter_id=chapter.id,report_json={'graded':graded_text_fingerprint(BODY),'blocking_codes':[],'draft_continuity_hard_safety':tag},blocks_write=fault=='blocked_report',regen_attempts=0);s.add(report);await s.flush()
  fresh={'schema':service.SCHEMA,'original_proof_sha256':content_hash(original),'draft_id':str(draft.id),'body_sha256':original['body_sha256'],'source_sha256':original['source_sha256'],
   'hard_policy_sha256':'b'*64,'quality_report_id':str(report.id),'deterministic_binding':tag,'deterministic_outcome':'ok',
   'source_transition':{'old_policy_sha256':'a'*64,'current_policy_sha256':'b'*64,'substantive_old_review_rules_unchanged':True}}
  if fault=='proof':fresh['original_proof_sha256']='wrong'
  if fault=='policy':fresh['hard_policy_sha256']='wrong'
  ident=service.identity(original,'b'*64)
  binding={'project_id':str(project.id),'target_id':str(ident),'proof_sha256':content_hash(fresh),'draft_id':str(draft.id),'body_sha256':fresh['body_sha256'],'source_sha256':fresh['source_sha256'],'formal_adoption':False}
  approval=await record_operator_action_approval(s,purpose='draft_continuity_reference',binding=binding,capabilities=['reference_pending_manuscript'],actor='fixture',authorization_ref='isolated fixture',explicit_user_approval=True)
  if fault!='missing':s.add(m.WorkflowRunModel(id=ident,project_id=project.id,workflow_type='draft_continuity_deterministic_revalidation',scope_type='chapter',scope_id=chapter.id,status='completed',current_step='verified',requested_by='fixture',metadata_json={'proof':fresh,'binding':binding,'approval_id':str(uuid4()) if fault=='approval' else str(approval)}))
  if fault=='body':draft.content_md+='changed'
  await s.commit()
  if fault:
   with pytest.raises(ValueError):await dc._verify_deterministic_receipt(s,draft=draft,proof=original)
  else:
   await dc._verify_deterministic_receipt(s,draft=draft,proof=original)
   result=await service.resolve_revalidation(s,draft=draft,original_proof=original)
   assert result['quality_report_id']==str(report.id) and result['hard_policy_sha256']=='b'*64
  assert original==saved and draft.promotion_state=='candidate' and draft.is_current is False

@pytest.mark.parametrize('name',list(service.OWNED))
def test_review_or_generation_edits_beyond_exact_dispatch_are_rejected(name):
 # Fails closed even if the caller claims that a generic changed function is harmless.
 with pytest.raises((ValueError,StopIteration)):
  service.permitted_source_edit(name,'def unrelated(): return False','def unrelated(): return True')

@pytest.mark.parametrize('tamper',[False,True])
def test_independent_axis_dispatch_transition_preserves_entire_legacy_branch(tamper):
 old="""async def _verify_deterministic_receipt(session, *, draft, proof):
    need(proof['hard_policy_sha256']==hard_policy_hash(),'hard_policy_changed')
async def select_reference(session,settings,*,project,chapter,_memo=None):
    if proof.get('schema')=='source-adjudicated-draft-continuity.v1':
        from bestseller.services.draft_continuity_adjudication import validate_reference
        await validate_reference(session,settings,project=project,chapter=chapter,draft=draft,proof=proof,_memo=_memo)
    else:
        need(proof['hard_safety_status']=='verified','unknown')
"""
 new=old.replace("    need(proof['hard_policy_sha256']", "    if proof.get('hard_policy_sha256')!=hard_policy_hash():\n        from bestseller.services.draft_continuity_revalidation import resolve_revalidation\n        proof=await resolve_revalidation(session,draft=draft,original_proof=proof)\n    need(proof['hard_policy_sha256']")
 new=new.replace("    if proof.get('schema')=='source-adjudicated-draft-continuity.v1':", "    if proof.get('schema')=='source-adjudicated-axis-continuity.v1':\n        from bestseller.services.draft_continuity_axis_adjudication import validate_reference\n        await validate_reference(session,settings,project=project,chapter=chapter,draft=draft,proof=proof,_memo=_memo)\n    elif proof.get('schema')=='source-adjudicated-draft-continuity.v1':")
 if tamper:
  new=new.replace("need(proof['hard_safety_status']=='verified','unknown')",'need(True,\'unknown\')')
  with pytest.raises(ValueError,match='non_writer_or_review_rules_changed'):service.permitted_source_edit('src/bestseller/services/draft_continuity.py',old,new)
 else:service.permitted_source_edit('src/bestseller/services/draft_continuity.py',old,new)

@pytest.mark.parametrize('tamper',[None,'delivery_action','legacy_approval','axis_unresolved'])
def test_length_presentation_transition_rejects_changes_beyond_exact_projection_and_dispatch(tmp_path,tamper):
 # Reconstruct a version-neutral pre-presentation source from the exact
 # permitted delta; no real book, source rows, provider or runtime ledger.
 from pathlib import Path
 root=Path(__file__).resolve().parents[2]
 delivery='src/bestseller/services/chapter_writer_delivery.py';validation='src/bestseller/services/draft_continuity_revalidation.py'
 # This adapter is specifically the retained v0→v1 length presentation
 # transition, not the subsequent v2 full-task/protocol dispatcher.
 now={delivery:(root/'tests/fixtures/source_memo_task_before_v2/chapter_writer_delivery.py.txt').read_text(),
      validation:(root/validation).read_text()}
 old_delivery=now[delivery].replace("'information_held_back','target_word_count')","'information_held_back')")
 old_delivery=old_delivery.replace("    if approved.get('target_word_count') is not None:\n        payload['chapter_target_word_count_reference']=deepcopy(approved['target_word_count'])\n",'')
 start=old_delivery.index("'这些计划不授权撤销历史")
 end=old_delivery.index("\\n')",start)+len("\\n')")
 old_delivery=old_delivery[:start]+"'这些计划不授权撤销历史，也不构成角色已经知道全部信息的证据。\\n')"+old_delivery[end:]
 # The prior compatibility implementation is reconstructed by removing
 # precisely the new helper/branch and reversing schema dispatch, then its
 # AST is compared by the production verifier.
 import ast
 tree=ast.parse(now[validation]);tree.body=[n for n in tree.body if not isinstance(n,ast.FunctionDef) or n.name!='_verify_presentation_delta']
 transition=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='verify_source_transition')
 transition.body=[n for n in transition.body if not (isinstance(n,ast.If) and 'changed ==' in ast.unparse(n.test))]
 fn=next(n for n in tree.body if isinstance(n,ast.AsyncFunctionDef) and n.name=='revalidate_reference')
 i=next(i for i,n in enumerate(fn.body) if isinstance(n,ast.ImportFrom) and n.module=='bestseller.services' and any(a.name=='draft_continuity_axis_adjudication' for a in n.names))
 fn.body[i:i+3]=ast.parse("need(proof['schema']==adjudicate.POLICY and proof['adjudicator_source_sha256']==adjudicate.policy_hash() and original['binding']['proof_sha256']==content_hash(proof),'original_adjudication')").body
 i=next(i for i,n in enumerate(fn.body) if isinstance(n,ast.Assign) and isinstance(n.targets[0],ast.Name) and n.targets[0].id=='actions')
 fn.body[i:i+2]=ast.parse("need(physical==proof['physical_evidence'] and adjudicate.derive_axis_evidence(raw,body=draft.content_md,sources=sources,decisions=proof['decisions'])==proof['source_adjudication'],'old_physical_or_positive_evidence_changed')").body
 old={delivery:old_delivery,validation:ast.unparse(tree)}
 if tamper=='delivery_action':now[delivery]=now[delivery].replace("not already occurred history.","automatically occurred history.")
 if tamper=='legacy_approval':now[validation]=now[validation].replace("need(explicit_user_approval is True and actor and authorization_ref,'explicit_authorization')","need(True,'explicit_authorization')")
 if tamper=='axis_unresolved':now[validation]=now[validation].replace("axis_action=proof['axis_action']","axis_action={}")
 for name,value in now.items():
  path=tmp_path/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(value)
 if tamper:
  with pytest.raises(ValueError):service._verify_presentation_delta(old_sources=old,current_root=tmp_path)
 else:service._verify_presentation_delta(old_sources=old,current_root=tmp_path)
