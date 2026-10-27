"""SDK-only index metadata never changes arguments, grades or source authority."""
from copy import deepcopy
from types import SimpleNamespace
from uuid import uuid4
import json
import pytest
from bestseller.services.tool_call_evidence import same_tool_call_evidence
from bestseller.services.source_bound_planning_submission import parse_planning_submission
from bestseller.services.planning_admission_submission import parse_planning_admission
from bestseller.services.primary_review_submission import parse_primary_review_submission
from bestseller.services import intent_review_submission as intent
from bestseller.services.planning_transport_proposal import proposal_reference
from test_source_bound_planning_submission import completion as planning_completion
from test_planning_admission_submission import _completion as admission_completion
from test_primary_review_submission import completion as primary_completion,payload as primary_payload
from test_intent_review_submission import completion as intent_completion,inputs as intent_inputs,payload as intent_payload
pytestmark=pytest.mark.unit

@pytest.mark.parametrize('consumer',['planning','admission','primary','intent'])
def test_live_consumers_preserve_exact_indexed_raw_evidence_and_rejections(consumer):
    if consumer=='planning':
        c=planning_completion('book_spec',raw=True);parse=lambda:parse_planning_submission(c,kind='book_spec');calls=c.tool_calls;raw=c.raw_message
    elif consumer=='admission':
        c=admission_completion();c['raw_message']={'role':'assistant','content':'','tool_calls':deepcopy(c['tool_calls'])};calls=c['tool_calls'];raw=c['raw_message'];parse=lambda:parse_planning_admission(c)
    elif consumer=='primary':
        c=primary_completion(primary_payload((1,)),raw_message=True);calls=c.tool_calls;raw=c.raw_message;parse=lambda:parse_primary_review_submission(c,'一段真实正文。')
    else:
        request=intent.build_intent_review_request(**intent_inputs());c=intent_completion(request,intent_payload(request,verdict='fail'));calls=c.tool_calls;raw=c.raw_message;parse=lambda:intent.parse_intent_review_submission(c,request)
    for i,call in enumerate(raw['tool_calls']):call['index']=i
    before=deepcopy(c if isinstance(c,dict) else c.__dict__)
    result=parse()
    assert (c if isinstance(c,dict) else c.__dict__)==before
    if consumer=='planning':assert result[2]['status']=='verified'
    elif consumer=='admission':assert result[1]['parsed_payload']['draft_eligible'] is False
    elif consumer=='primary':assert result[2]==()
    else:assert result['status']=='verified' and result['failed_tags']
    assert same_tool_call_evidence(raw['tool_calls'],calls)
    raw['tool_calls'][0]['function']['arguments']+='\n'
    assert not same_tool_call_evidence(raw['tool_calls'],calls)
    if consumer in ['planning','admission']:
        with pytest.raises(ValueError,match='raw_message_tool_conflict'):parse()
    elif consumer=='primary':assert parse()[2]
    else:assert parse()['status']=='unknown'

@pytest.mark.parametrize('fault',['wrong_index','bool_index','negative_index','unknown_field','wrong_id','wrong_name','wrong_args','hidden_call'])
def test_only_position_index_is_ignorable(fault):
    calls=[{'id':'real','type':'function','function':{'name':'submit_book_spec','arguments':'{"asset_json":"{}"}'}}];raw=deepcopy(calls);raw[0]['index']=0
    if fault=='wrong_index':raw[0]['index']=1
    if fault=='bool_index':raw[0]['index']=False
    if fault=='negative_index':raw[0]['index']=-1
    if fault=='unknown_field':raw[0]['provider_override']='other'
    if fault=='wrong_id':raw[0]['id']='different'
    if fault=='wrong_name':raw[0]['function']['name']='execute'
    if fault=='wrong_args':raw[0]['function']['arguments']+=' '
    if fault=='hidden_call':raw.append(deepcopy(raw[0]))
    assert not same_tool_call_evidence(raw,calls)


def proposal(fault=None):
    pid=uuid4();wid=uuid4();rid=uuid4();scope='exact-current-scope';c=planning_completion('book_spec',raw=True)
    c.content='';c.raw_message['content']=None;c.raw_message['tool_calls'][0]['index']=0
    r={'content':c.content,'tool_calls':c.tool_calls,'raw_message':c.raw_message,'fallback_used':False,'finish_reason':'tool_calls','model_name':'fixed model',
       'input_tokens':10,'output_tokens':20,'metadata':{'paid_call_origin':{'project_id':str(pid),'workflow_run_id':str(wid),'prompt_hash':'hash',
           'prompt_template':'planner_book_spec','prompt_version':'v3-source-bound-kind-submit','logical_role':'planner'}}}
    run=SimpleNamespace(id=rid,project_id=pid,workflow_run_id=wid,logical_role='planner',prompt_template='planner_book_spec',prompt_version='v3-source-bound-kind-submit',request_payload_ref=None,prompt_hash='hash',model_name='fixed model',finish_reason='tool_calls',input_tokens=10,output_tokens=20)
    op=SimpleNamespace(key='known-completed',scope_id=scope,status='completed',input_hash='hash',result_json=r)
    a=SimpleNamespace(operation_key=op.key,status='completed',scope_id=scope,model_name='fixed model',input_hash='hash',actual_tokens=30)
    if fault=='unknown':op.status='unknown'
    if fault=='unsettled':a.actual_tokens=None
    if fault=='wrong_scope':a.scope_id='different'
    if fault=='changed_input':run.prompt_hash='different'
    if fault=='wrong_project':r['metadata']['paid_call_origin']['project_id']=str(uuid4())
    if fault=='captured_new_request':run.request_payload_ref='new-audited-request.json'
    if fault=='changed_raw':r['raw_message']['tool_calls'][0]['function']['arguments']+='\n'
    return run,op,[a],pid,scope


def test_old_known_paid_proposal_never_inherits_grade_or_claims_exact_input():
    run,op,attempts,pid,scope=proposal();before=deepcopy(op.result_json)
    ref=proposal_reference(run,op,attempts,kind='book_spec',project_id=pid,scope=scope)
    assert ref['proposal']['protagonist']['name']=='于岚'
    assert ref['grade_reused'] is ref['approval_asserted'] is ref['exact_historical_request_proven'] is False
    assert ref['authority']=='unreviewed_paid_candidate' and op.result_json==before

@pytest.mark.parametrize('fault',['unknown','unsettled','wrong_scope','changed_input','wrong_project','captured_new_request','changed_raw'])
def test_unproven_response_cannot_become_a_proposal(fault):
    run,op,attempts,pid,scope=proposal(fault)
    assert proposal_reference(run,op,attempts,kind='book_spec',project_id=pid,scope=scope) is None


@pytest.mark.parametrize('kind',['book_spec','world_spec','cast_spec','volume_plan'])
def test_prior_unreviewed_proposal_and_real_dispatch_preserve_sources_and_evidence(kind):
    from bestseller.services.planning_transport_proposal import prompt_with_prior_proposal
    from bestseller.services.planning_dispatch_transport import prepare_planning_dispatch,_document
    from bestseller.services.source_bound_planning_content import decode_source_bound_planning_document
    from test_planning_dispatch_transport import _plain
    run,op,attempts,pid,scope=proposal()
    ref=proposal_reference(run,op,attempts,kind='book_spec',project_id=pid,scope=scope)
    system,base,tools=_plain(kind)
    user=prompt_with_prior_proposal(base,ref)
    wire,receipt=prepare_planning_dispatch(system,user,tools=tools,output_tokens=12288)
    document,suffix=_document(wire);logical=decode_source_bound_planning_document(document)
    evidence=logical.pop('unreviewed_prior_paid_proposal')
    assert suffix=='' and evidence['evidence']==ref
    assert evidence['evidence']['grade_reused'] is False and evidence['evidence']['approval_asserted'] is False
    assert 'not author intent, canonical authority or an approved grade' in evidence['instructions']
    assert receipt['roundtrip_verified'] and receipt['capacity']['budget_passed']
    assert logical==decode_source_bound_planning_document(json.loads(base))
    for second in ['{}','[]']:
        with pytest.raises(ValueError,match='multiple_json_documents'):
            prepare_planning_dispatch(system,base+second,tools=tools,output_tokens=12288)
    with pytest.raises(ValueError,match='already_present'):prompt_with_prior_proposal(user,ref)
