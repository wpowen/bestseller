"""The operator cannot turn a partial cohort into another sampling allowance."""
from copy import deepcopy
import hashlib
import importlib.util
from pathlib import Path
import sys

import pytest

BASE=Path(__file__).resolve().parents[2]/'reports/continuous-recovery-2026-10-16'
sys.path.insert(0,str(BASE))
spec=importlib.util.spec_from_file_location('missing_component_operator',BASE/'resume_commercial_component.py')
op=importlib.util.module_from_spec(spec);spec.loader.exec_module(op)
pytestmark=pytest.mark.unit


def financial_case():
    old={'execution_operations':[{'key':'old','status':'stopped','result_json':None}],
         'execution_attempts':[{'id':'old-paid','status':'completed','operation_key':'old-paid-root'}],
         'execution_budgets':[{'scope_id':'scope','calls_used':8,'tokens_charged':100,
            'metadata_json':{'authorization':'retained'},'status':'active'}]}
    new=deepcopy(old)
    new['execution_attempts'].append({'id':'new','scope_id':'scope','operation_key':'root',
        'status':'completed','actual_tokens':12})
    key=hashlib.sha256(b'transport-result.v1\nscope\nnew').hexdigest()
    new['execution_operations'] += [{'key':'root','status':'completed'},{'key':key,'status':'transport_result'}]
    new['execution_budgets'][0].update(calls_used=9,tokens_charged=112)
    return old,new


def test_one_completed_missing_draw_keeps_every_old_row_and_charge():
    a,b=financial_case(); old=deepcopy(a)
    assert op.verify_financial_growth(a,b,'root')['calls_delta']==1
    assert a==old


@pytest.mark.parametrize('fault',['old_operation','old_attempt','old_authorization','extra_draw',
                                  'missing_transport','counter_reset','pending_return'])
def test_financial_reset_or_expansion_cannot_claim_component_completion(fault):
    a,b=financial_case()
    if fault=='old_operation': b['execution_operations'][0]['status']='completed'
    elif fault=='old_attempt': b['execution_attempts'][0]['status']='unknown'
    elif fault=='old_authorization':b['execution_budgets'][0]['metadata_json']={}
    elif fault=='extra_draw':b['execution_attempts'].append({'id':'other'})
    elif fault=='missing_transport':b['execution_operations'].pop()
    elif fault=='counter_reset':b['execution_budgets'][0]['calls_used']=1
    elif fault=='pending_return':b['execution_attempts'][-1]['status']='unknown'
    with pytest.raises(ValueError):op.verify_financial_growth(a,b,'root')


def test_gateway_rejects_other_slot_before_provider():
    from uuid import uuid4
    from bestseller.services.llm import LLMCompletionRequest,_effective_input_hash
    from bestseller.settings import load_settings
    role=load_settings(env={}).llm.critic
    wid,pid=uuid4(),uuid4()
    request=LLMCompletionRequest(logical_role='critic',system_prompt='source',user_prompt='body',fallback_response='{}',
        prompt_template='chapter_commercial_quality_judge',workflow_run_id=wid,project_id=pid,
        sample_slot='chapter-quality.independent-v2:1')
    proof={'owner_id':str(wid),'project_id':str(pid),'missing_slot':request.sample_slot,
        'effective_critic':role.model_dump(mode='json'),'input_hash':_effective_input_hash(request,role),
        'scope':'scope','key':'body-key','binding':{'evaluation_contract':{'min':.8}},'prompt_version':request.prompt_version}
    proof['expected_root']=op._root_key({'scope_id':'scope','key':'body-key',
        'evaluation_contract':proof['binding']['evaluation_contract']},proof['input_hash'],
        request.prompt_version,request.sample_slot,1)
    namespace='commercial-component:body-key:policy:'+op.shared.digest(proof['binding']['evaluation_contract'])+':round:1'
    from bestseller.services.llm import bind_execution_scope,bind_execution_operation
    with bind_execution_scope('scope'),bind_execution_operation(namespace):
        op.guard_request(request,proof)
        with pytest.raises(ValueError):op.guard_request(request.model_copy(update={'sample_slot':0}),proof)
        with pytest.raises(ValueError):op.guard_request(request.model_copy(update={'user_prompt':'other body'}),proof)
        with pytest.raises(ValueError):op.guard_request(request.model_copy(update={'prompt_version':'different'}),proof)
        with pytest.raises(ValueError):op.guard_request(request.model_copy(update={'operation_id':'new-credit'}),proof)
    with pytest.raises(ValueError):op.guard_request(request,proof)
