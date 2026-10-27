"""Prospective patch capacity honors configured role and exact historical wire."""
from copy import deepcopy
from uuid import uuid4
import pytest
from bestseller.services import llm
from bestseller.services import planning_surgical_budget as budget
from bestseller.settings import LLMRoleSettings

pytestmark=pytest.mark.unit


def role(**kw):
    return LLMRoleSettings(model='openai/MiniMax-M3.1-Flash',max_tokens=32768,
        temperature=.82,timeout_seconds=900,stream=True,**kw)


def request(receipt=None,**kw):
    args={'logical_role':'planner','project_id':uuid4(),'workflow_run_id':uuid4(),
        'system_prompt':'Synthetic full-source repair','user_prompt':'{}','fallback_response':'{}',
        'prompt_template':'planner_full_outline_revision_commercial_surgical_repair',
        'prompt_version':budget.PROMPT_VERSION,'max_tokens_override':32768,
        'metadata':{'planning_surgical_output_budget':deepcopy(receipt)} if receipt else {}}
    return llm.LLMCompletionRequest(**{**args,**kw})


@pytest.mark.parametrize('effort',[None,'low','medium','high','xhigh','max'])
def test_selected_role_budget_preserves_explicit_effort_and_readonly_reconstruction(effort):
    r=role(reasoning_effort=effort);before=r.model_dump()
    plan=budget.output_budget(r,answer_tokens=12000)
    actual=llm._apply_conception_phase_settings(request(plan),r)
    wire=llm._effective_input_payload(request(plan),actual)
    assert wire['max_tokens']==32768 and wire['reasoning_effort']==(effort or 'medium')
    reconstructed=llm._apply_conception_phase_settings(request(),r)
    assert llm._effective_input_payload(request(),reconstructed)==wire
    assert r.model_dump()==before and wire['temperature']==.82


@pytest.mark.parametrize('effort',[None,'medium','high'])
def test_legacy_3_4_historical_effective_input_is_byte_exact(effort):
    r=role(reasoning_effort=effort)
    req=request(prompt_version='3.4-window-complete-repair',max_tokens_override=12000)
    before=llm._effective_input_hash(req,r)
    after=llm._apply_conception_phase_settings(req,r)
    assert after==r and llm._effective_input_hash(req,after)==before
    assert llm._effective_input_payload(req,after)['max_tokens']==12000


@pytest.mark.parametrize('key,value',[
    ('model','another-model'),('total_output_tokens',12000),('role_output_tokens',64000),
    ('reasoning_effort','max'),('schema','wrong')])
def test_changed_allowance_rejected_before_dispatch(key,value):
    r=role(reasoning_effort='medium');plan=budget.output_budget(r,answer_tokens=12000)
    plan[key]=value
    with pytest.raises(ValueError):llm._apply_conception_phase_settings(request(plan),r)


@pytest.mark.parametrize('kw',[{'max_tokens_override':12000},{'logical_role':'critic'},
    {'prompt_version':'3.4-window-complete-repair'},{'prompt_template':'chapter_rewrite'}])
def test_receipt_cannot_widen_other_routes_or_keep_old_too_small_wire(kw):
    r=role(reasoning_effort='medium');plan=budget.output_budget(r,answer_tokens=12000)
    with pytest.raises(ValueError):llm._apply_conception_phase_settings(request(plan,**kw),r)


def test_insufficient_role_capacity_fails_without_increasing_role():
    r=role().model_copy(update={'max_tokens':6000})
    with pytest.raises(ValueError,match='role_below_answer_allowance'):
        budget.output_budget(r,answer_tokens=12000)
    assert r.max_tokens==6000


@pytest.mark.parametrize('template,version',[
    (budget.DOMAIN_TEMPLATE,budget.DOMAIN_VERSION),
    (budget.FLAT_DOMAIN_TEMPLATE,budget.FLAT_DOMAIN_VERSION),
])
def test_typed_domain_request_has_exact_new_route_and_physical_reconstruction(template,version):
    r=role(reasoning_effort=None)
    receipt=budget.output_budget(r,answer_tokens=12000)
    req=request(receipt,prompt_version=version,prompt_template=template)
    actual=llm._apply_conception_phase_settings(req,r)
    physical=request(prompt_version=version,prompt_template=template)
    rebuilt=llm._apply_conception_phase_settings(physical,r)
    assert llm._effective_input_payload(req,actual)==llm._effective_input_payload(physical,rebuilt)
    assert actual.reasoning_effort=='medium' and r.reasoning_effort is None


@pytest.mark.parametrize('changes',[
    {'prompt_template':'planner_outline_domain_field_corrections_other'},
    {'logical_role':'critic'},
    {'prompt_version':'3.6-other'},
])
def test_typed_domain_allowance_cannot_leak_to_other_requests(changes):
    r=role(reasoning_effort='medium')
    receipt=budget.output_budget(r,answer_tokens=12000)
    args={'prompt_version':budget.DOMAIN_VERSION,'prompt_template':budget.DOMAIN_TEMPLATE,**changes}
    with pytest.raises(ValueError,match='wrong_request_contract'):
        llm._apply_conception_phase_settings(request(receipt,**args),r)


@pytest.mark.parametrize('template,version',[
    (budget.DOMAIN_TEMPLATE,budget.FLAT_DOMAIN_VERSION),
    (budget.FLAT_DOMAIN_TEMPLATE,budget.DOMAIN_VERSION),
])
def test_historical_and_flat_allowances_cannot_cross_pair(template,version):
    r=role(reasoning_effort='medium');receipt=budget.output_budget(r,answer_tokens=12000)
    with pytest.raises(ValueError,match='wrong_request_contract'):
        llm._apply_conception_phase_settings(request(receipt,prompt_template=template,
            prompt_version=version),r)
