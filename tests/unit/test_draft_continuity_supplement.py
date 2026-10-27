"""A second witness must finish uncertainty; original malformed JSON stays invalid."""
from copy import deepcopy
import json
from types import SimpleNamespace
from uuid import uuid4
import pytest
from bestseller.services import draft_continuity as dc, draft_continuity_supplement as service
from bestseller.services.lossless_source_encoding import decode_lossless_source
from bestseller.services.prompt_source_policy import content_hash
from test_draft_continuity import BODY, response
pytestmark=pytest.mark.unit


def case():
 sources={'chapter_sources':{'chapter':{'chapter_number':3},'rule':'Do not open the sealed case.'}}
 original=response(body=BODY,sources=sources);original.pop('submission_complete');original['unknown_issues']=['The case origin is not established; the prose does not assert its history.']
 arguments=json.dumps(original,ensure_ascii=False)[:-1]+', "unknown_issues": '
 observations=service.partial_observations(arguments,body=BODY,sources=sources)
 ids=[a['id'] for a in dc.build_critic_body_anchors(BODY)];sid=dc.source_catalog(sources)[0]['id']
 report={'schema':service.POLICY,'body_sha256':dc.body_hash(BODY),'source_sha256':content_hash(sources),'original_arguments_sha256':dc.body_hash(arguments),'approved_plan_sha256':content_hash({}),'checked_body_ids':ids,
  'issue_decisions':[{'index':0,'status':'not_hard_safety_unknown','body_ids':[ids[0]],'source_ids':[sid],'explanation':'The exact unopened-case action preserves the applicable source boundary.'}],
  'additional_hard_issues':[],'submission_complete':'full_body_sources_and_remaining_hard_uncertainty_examined'}
 return sources,arguments,observations,report


def test_partial_token_observation_never_becomes_a_completed_old_report():
 sources,arguments,original,report=case();before=deepcopy(original)
 with pytest.raises(ValueError):dc.parse_safety_response(arguments,body=BODY,sources=sources)
 assert original['original_submission_complete'] is False and original['counts_as_complete_safety'] is False
 assert original['repaired_report_created'] is False and 'submission_complete' not in original['completed_fields']
 result=service.parse(json.dumps(report),body=BODY,sources=sources,original=original)
 assert service.passed(result) and original==before
 for span in original['completed_value_slices']:
  assert content_hash(json.loads(arguments[span['start']:span['end']]))==span['value_sha256']


@pytest.mark.parametrize('fault',['incomplete','duplicate_key','body','source','original','missing_coverage','bad_body','bad_source','duplicate_citation','missing_issue','extra_issue','explanation','missing_completion'])
def test_new_witness_is_strict_and_fails_closed(fault):
 sources,arguments,original,report=case()
 if fault=='incomplete':text=json.dumps(report)[:-1]
 elif fault=='duplicate_key':text=json.dumps(report)[:-1]+',"additional_hard_issues":[]}'
 else:
  if fault=='body':report['body_sha256']='foreign'
  if fault=='source':report['source_sha256']='foreign'
  if fault=='original':report['original_arguments_sha256']='foreign'
  if fault=='missing_coverage':report['checked_body_ids'].pop()
  if fault=='bad_body':report['issue_decisions'][0]['body_ids']=['D0001']
  if fault=='bad_source':report['issue_decisions'][0]['source_ids']=['Sforeign']
  if fault=='duplicate_citation':report['issue_decisions'][0]['body_ids']*=2
  if fault=='missing_issue':report['issue_decisions']=[]
  if fault=='extra_issue':report['issue_decisions']*=2
  if fault=='explanation':report['issue_decisions'][0]['explanation']='x'*161
  if fault=='missing_completion':report.pop('submission_complete')
  text=json.dumps(report)
 with pytest.raises(ValueError):service.parse(text,body=BODY,sources=sources,original=original)


@pytest.mark.parametrize('status,extra',[('unknown',[]),('hard_blocker',[]),('not_hard_safety_unknown',['Unresolved physical source contradiction.'])])
def test_true_hard_unknown_or_additional_blocker_never_passes(status,extra):
 sources,arguments,original,report=case();report['issue_decisions'][0]['status']=status;report['additional_hard_issues']=extra
 parsed=service.parse(json.dumps(report),body=BODY,sources=sources,original=original)
 assert not service.passed(parsed)


@pytest.mark.parametrize('fault',['failed_axis','unknown_axis','invalid_id','source','coverage','blocker','missing_unknown','wrong_partial_tail','already_complete'])
def test_original_negative_or_other_malformed_shape_has_no_supplement_route(fault):
 sources,arguments,original,report=case();value=deepcopy(original['completed_fields'])
 if fault=='failed_axis':value['checks'][0]['status']='fail'
 if fault=='unknown_axis':value['checks'][0]['status']='unknown'
 if fault=='invalid_id':value['checks'][0]['body_ids']=['D0001']
 if fault=='source':value['source_sha256']='foreign'
 if fault=='coverage':value['checked_body_ids'].pop()
 if fault=='blocker':value['blocking_issues']=['Actual hard failure.']
 if fault=='missing_unknown':value.pop('unknown_issues')
 text=json.dumps(value)[:-1]+', "unknown_issues": '
 if fault=='wrong_partial_tail':text=json.dumps(value)[:-1]+', "checks": '
 if fault=='already_complete':text=json.dumps(value)
 with pytest.raises(ValueError):service.partial_observations(text,body=BODY,sources=sources)


def test_request_preserves_whole_source_and_exact_enumerations_with_short_output():
 sources,arguments,original,report=case();before=deepcopy(sources)
 project=SimpleNamespace(id=uuid4());draft=SimpleNamespace(id=uuid4(),content_md=BODY)
 request=service.request_for(project=project,workflow_id=uuid4(),draft=draft,sources=sources,original=original,grant_id='fixture-one-send')
 wire=json.loads(request.user_prompt);decoded=decode_lossless_source(wire['value'])
 assert decoded['sources']==sources==before and decoded['body']==BODY and decoded['original_incomplete_observations']==original
 schema=request.tools[0]['function']['parameters'];decision=schema['properties']['issue_decisions']['items']['properties']
 assert set(decision['body_ids']['items']['enum'])=={a['id'] for a in dc.build_critic_body_anchors(BODY)}
 assert set(decision['source_ids']['items']['enum'])=={a['id'] for a in dc.source_catalog(sources)}
 assert decision['explanation']['maxLength']==160 and request.max_tokens_override==4000
 assert request.metadata['single_dispatch_authorization_id']=='fixture-one-send'
 assert 'checks' not in schema['properties'] and request.prompt_template=='draft_continuity_hard_safety' and request.prompt_version=='4.0-completion-supplement'


def test_current_approved_plan_is_separate_future_evidence_not_a_rewritten_old_source_hash():
 sources,arguments,original,report=case()
 plan={'schema':'approved-current-chapter-delivery.v1','artifact_id':str(uuid4()),'approved_chapter_sha256':'f'*64,'readiness_receipt_sha256':'a'*64,'authority':'Approved future task for this chapter, not already occurred history.','scenes':[{'action_sequence':['Use the new brass seal after signing; no previous custody is asserted.']}]}
 before=deepcopy(sources);project=SimpleNamespace(id=uuid4());draft=SimpleNamespace(id=uuid4(),content_md=BODY)
 request=service.request_for(project=project,workflow_id=uuid4(),draft=draft,sources=sources,original=original,approved_plan=plan)
 data=decode_lossless_source(json.loads(request.user_prompt)['value'])
 assert data['sources']==sources==before and data['source_sha256']==original['completed_fields']['source_sha256']
 assert data['approved_current_chapter_task']==plan and data['approved_plan_sha256']==content_hash(plan)
 entry=next(x for x in service.plan_catalog(plan) if x['path']=='/scenes/0/action_sequence/0')
 assert entry['id'].startswith('A')
 report['approved_plan_sha256']=content_hash(plan);report['issue_decisions'][0]['source_ids']=[entry['id']]
 assert service.passed(service.parse(json.dumps(report),body=BODY,sources=sources,original=original,approved_plan=plan))
 with pytest.raises(ValueError):service.parse(json.dumps(report),body=BODY,sources=sources,original=original,approved_plan={**plan,'scenes':[]})
 with pytest.raises(ValueError):service.parse(json.dumps(report),body=BODY,sources=sources,original=original)

@pytest.mark.asyncio
@pytest.mark.parametrize('fault',[None,'user','spent','two_attempts','operation'])
async def test_reconstructed_retained_request_checks_real_input_and_spent_grant_not_discarded_fallback(monkeypatch,fault):
 import hashlib
 from unittest.mock import AsyncMock
 from bestseller.services import retained_review_input_proof as retained,llm
 from bestseller.settings import load_settings
 sources,arguments,original,report=case();settings=load_settings(env={});project=SimpleNamespace(id=uuid4(),metadata_json={'execution_scope_id':'scope:fixture'})
 draft=SimpleNamespace(id=uuid4(),content_md=BODY);wid=uuid4();grant_id='one-fixture-send'
 expected=service.request_for(project=project,workflow_id=wid,draft=draft,sources=sources,original=original,grant_id=grant_id)
 reconstructed=expected.model_copy(update={'fallback_response':'proof only; never dispatched','operation_id':None,'metadata':{}})
 if fault=='user':reconstructed=reconstructed.model_copy(update={'user_prompt':'Foreign body input'})
 role=settings.llm.critic;digest=llm._effective_input_hash(expected,role);key=hashlib.sha256(('scope:fixture\n'+expected.operation_id).encode()).hexdigest()
 proof={'input_hash':digest,'model':role.model,'physical_receipt':key if fault!='operation' else 'foreign','actual_attempt_ids':['one','two'] if fault=='two_attempts' else ['one']}
 binding={'scope_id':'scope:fixture','operation_key':key,'input_hash':digest,'workflow_id':str(wid),'model':role.model,'template':expected.prompt_template,'version_prefix':expected.prompt_version,'dispatch_scope':'workflow'}
 ledger=SimpleNamespace(metadata_json={'single_dispatch_grants':{grant_id:{'status':'available' if fault=='spent' else 'spent','physical_attempt_cap':1,'used_attempt_id':'one','binding':binding}}})
 session=SimpleNamespace(get=AsyncMock(return_value=ledger))
 paid=SimpleNamespace(tool_calls=[{'id':'one','type':'function','function':{'name':service.SUBMIT,'arguments':json.dumps(report)}}],raw_message=None)
 monkeypatch.setattr(service,'physical_result',AsyncMock(return_value=(reconstructed,paid,proof)))
 monkeypatch.setattr(service,'applicable_plan',AsyncMock(return_value={}))
 monkeypatch.setattr(retained,'_fresh_catalog',lambda:({},'fixture'))
 monkeypatch.setattr(retained,'_fresh_role',lambda *a:role)
 evidence={'run_id':str(uuid4()),'workflow_id':str(wid),'grant_id':grant_id,'arguments_sha256':dc.body_hash(json.dumps(report))}
 if fault:
  with pytest.raises(ValueError):await service.verify_supplement(session,settings,project=project,chapter=SimpleNamespace(),draft=draft,sources=sources,original=original,evidence=evidence)
 else:
  result,_=await service.verify_supplement(session,settings,project=project,chapter=SimpleNamespace(),draft=draft,sources=sources,original=original,evidence=evidence)
  assert result==report
