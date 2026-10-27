"""Current-version operator source review is not an inherited model grade."""
from copy import deepcopy
import json
from uuid import uuid4
import pytest
from bestseller.services import draft_continuity as dc,draft_continuity_source_actions as service,draft_continuity_supplement as sp
from bestseller.services.prompt_source_policy import content_hash
from test_draft_continuity import response
pytestmark=pytest.mark.unit


def case():
 oldbody='档案员把玻璃匣留在观测台。\n\n旧日谁曾读过，档案员全都知道。'
 body='档案员把玻璃匣留在观测台。\n\n旧日谁曾读过，仍待核实。'
 source={'chapter_sources':{'chapter':{'chapter_number':3},'acceptance_contract':{'must_deliver':[],'source_authority_channels':{'policy':'Only explicit current authority binds; plans do not prove occurred history.'}}},
  'foundations':{'book_spec':{'content':{'series_engine':{'knowledge_boundary':'Historical readers remain Unknown; do not resolve that into known or not knowing.'},'protagonist':{'name':'Archivist'}}}}}
 plan={'artifact_id':str(uuid4()),'scenes':[{'action_sequence':['Keep the sealed instrument in view.']} ]}
 raw=response(body=oldbody,sources=source);raw.pop('submission_complete');raw['unknown_issues']=['No independent archive-history witness.']
 arguments=json.dumps(raw)[:-1]+',"unknown_issues": '
 original=sp.partial_observations(arguments,body=oldbody,sources=source)
 bids=[a['id'] for a in dc.build_critic_body_anchors(oldbody)]
 supplement={'schema':sp.POLICY,'body_sha256':dc.body_hash(oldbody),'source_sha256':content_hash(source),'original_arguments_sha256':dc.body_hash(arguments),'approved_plan_sha256':content_hash(plan),
  'checked_body_ids':bids,'issue_decisions':[{'index':0,'status':'not_hard_safety_unknown','body_ids':[bids[1]],'source_ids':[],'explanation':'Native row needs independent semantic review; no invented replacement citation.'}],
  'additional_hard_issues':['Unestablished historical reader scope remains Unknown.'],'submission_complete':'full_body_sources_and_remaining_hard_uncertainty_examined'}
 correction={'source_body_sha256':dc.body_hash(oldbody),'result_body_sha256':dc.body_hash(body),'edits':[{'before':'旧日谁曾读过，档案员全都知道。','after':'旧日谁曾读过，仍待核实。'}],'plan_sha256':'p'*64}
 anchors=dc.build_critic_body_anchors(body);known=next(a for a in dc.source_catalog(source) if a['path'].endswith('/knowledge_boundary'))
 item={'unresolved_hard_items':[],'disposition':'not_a_current_manuscript_hard_violation','category':'source_boundary_corrected_in_exact_child','reason':'The current child preserves the selected Unknown; the old positive model observations missed a real violation and are not passed off as a new-body grade.',
  'body_evidence':[{'id':anchors[1]['id'],'quote':anchors[1]['text']}],'source_evidence':[{'id':known['id'],'quote':known['text']}],'absence_checks':[{'scope':'body','text':'全都知道','expected_count':0}]}
 groups={}
 for key,values in [('original_diagnostics',original['completed_fields']['unknown_issues']),('supplement_decisions',supplement['issue_decisions']),('supplement_additional',supplement['additional_hard_issues'])]:
  groups[key]=[{**deepcopy(item),'position':i,'raw_item_sha256':content_hash(v)} for i,v in enumerate(values)]
 groups['current_axes']=[{**deepcopy(item),'category':'current_axis_source_review','axis':a,'source_verdict':'source_verified','position':i,'raw_item_sha256':content_hash(a)} for i,a in enumerate(dc.AXES)]
 actions={'body_sha256':dc.body_hash(body),'historical_body_sha256':dc.body_hash(oldbody),'source_sha256':content_hash(source),'approved_plan_sha256':content_hash(plan),'original_arguments_sha256':dc.body_hash(arguments),'supplement_report_sha256':content_hash(supplement),
  'source_correction_kind':'preserve_explicit_epistemic_unknown','acknowledged_historical_hard_violation':True,'checked_current_body_ids':[a['id'] for a in anchors],'unresolved_hard_items':[],'groups':groups}
 return dict(body=body,historical_body=oldbody,correction=correction,sources=source,plan=plan,original=original,supplement=supplement,actions=actions)


def test_old_false_pass_and_wrong_native_rows_remain_immutable_current_source_review_is_separate():
 args=case();before=deepcopy(args);derived=service.derive_actions(**args)
 assert args==before and derived['models_reviewed_only_historical_body'] is True
 assert derived['current_complete_source_review_is_operator_action'] is True and derived['raw_native_grade_changed'] is False
 assert derived['current_body_sha256']!=derived['historical_body_sha256']
 assert derived['supplement_native_indices_and_comments_unmodified']==args['supplement']
 assert len(derived['groups']['current_axes'])==6
 quote=derived['groups']['original_diagnostics'][0]['body_evidence'][0]
 assert args['body'][quote['start']:quote['end']]==quote['quote']


@pytest.mark.parametrize('fault',['body','source','plan','old_body','no_edit','many_edits','different_delta','no_acknowledgement','missing_group','missing_old','missing_extra','missing_axis',
 'old_item_hash','quote','source_quote','offset','absent_false','current_hard_unknown','item_hard_unknown','native_unknown','native_hard','original_negative','empty_evidence','irrelevant_name','unknown_quote_missing','no_boundary_action','foreign_body_id','no_full_current_coverage'])
def test_unreviewed_foreign_or_true_hard_uncertainty_cannot_be_source_adjudicated(fault):
 args=case();a=args['actions'];row=a['groups']['original_diagnostics'][0]
 if fault=='body':a['body_sha256']='foreign'
 if fault=='source':a['source_sha256']='foreign'
 if fault=='plan':a['approved_plan_sha256']='foreign'
 if fault=='old_body':args['historical_body']+=' changed'
 if fault=='no_edit':args['correction']['edits']=[]
 if fault=='many_edits':args['correction']['edits']*=2
 if fault=='different_delta':args['body']+=' new history'
 if fault=='no_acknowledgement':a['acknowledged_historical_hard_violation']=False
 if fault=='missing_group':a['groups'].pop('supplement_decisions')
 if fault=='missing_old':a['groups']['original_diagnostics']=[]
 if fault=='missing_extra':a['groups']['supplement_additional']=[]
 if fault=='missing_axis':a['groups']['current_axes'].pop()
 if fault=='old_item_hash':row['raw_item_sha256']='foreign'
 if fault=='quote':row['body_evidence'][0]['quote']='Invented body evidence'
 if fault=='source_quote':row['source_evidence'][0]['quote']='Invented source evidence'
 if fault=='offset':row['body_evidence'][0]['start']=10000
 if fault=='absent_false':row['absence_checks'][0]['text']='档案员'
 if fault=='current_hard_unknown':a['unresolved_hard_items']=['Unresolved selected hard boundary.']
 if fault=='item_hard_unknown':row['unresolved_hard_items']=['Unresolved physical contradiction.']
 if fault=='native_unknown':args['supplement']['issue_decisions'][0]['status']='unknown';a['supplement_report_sha256']=content_hash(args['supplement'])
 if fault=='native_hard':args['supplement']['issue_decisions'][0]['status']='hard_blocker';a['supplement_report_sha256']=content_hash(args['supplement'])
 if fault=='original_negative':args['original']['completed_fields']['checks'][0]['status']='fail'
 if fault=='empty_evidence':row['body_evidence']=[]
 if fault=='irrelevant_name':
  name=next(x for x in dc.source_catalog(args['sources']) if x['path'].endswith('/name'));row['source_evidence']=[{'id':name['id'],'quote':name['text']}]
 if fault=='unknown_quote_missing':row['source_evidence'][0]['quote']='Historical readers'
 if fault=='no_boundary_action':
  for rows in a['groups'].values():
   for r in rows:r['category']='misclassified_meta_comment'
 if fault=='foreign_body_id':row['body_evidence'][0]['id']='D0001'
 if fault=='no_full_current_coverage':a['checked_current_body_ids'].pop()
 with pytest.raises(ValueError):service.derive_actions(**args)


@pytest.mark.asyncio
async def test_qualification_cannot_mutate_anything_without_explicit_source_action_approval():
 with pytest.raises(ValueError,match='explicit_approval'):
  await service.qualify_reference(None,None,project=None,chapter=None,draft=None,receipt={},actions={},actor='fixture',authorization_ref='fixture',explicit_user_approval=False)

@pytest.mark.parametrize('fault',[None,'index1','index_bool','extra_field','id','type','function','arguments','usage','finish','second_tool'])
def test_only_known_single_tool_wire_index_projection_is_accepted_without_changing_raw(fault):
 from types import SimpleNamespace
 sdk={'id':'physical-one','type':'function','function':{'name':'submit_source','arguments':'{"retained":"exact bytes"}'}}
 wire={'choices':[{'message':{'tool_calls':[{**deepcopy(sdk),'index':0}]},'finish_reason':'tool_calls'}],'usage':{'prompt_tokens':123,'completion_tokens':45}}
 actual=SimpleNamespace(tool_calls=[deepcopy(sdk)],finish_reason='tool_calls',input_tokens=123,output_tokens=45)
 w=wire['choices'][0]['message']['tool_calls'][0]
 if fault=='index1':w['index']=1
 if fault=='index_bool':w['index']=True
 if fault=='extra_field':w['extra_semantic_field']='not accepted'
 if fault=='id':w['id']='foreign'
 if fault=='type':w['type']='other'
 if fault=='function':w['function']['name']='foreign'
 if fault=='arguments':w['function']['arguments']+=' '
 if fault=='usage':wire['usage']['prompt_tokens']+=1
 if fault=='finish':wire['choices'][0]['finish_reason']='length'
 if fault=='second_tool':wire['choices'][0]['message']['tool_calls']*=2
 before=deepcopy(wire)
 if fault:
  with pytest.raises(ValueError):service.verify_http_return(wire,actual,sdk['function']['arguments'])
 else:
  result=service.verify_http_return(wire,actual,sdk['function']['arguments'])
  assert result['wire_index']==0 and result['raw_http_rewritten'] is False
 assert wire==before
