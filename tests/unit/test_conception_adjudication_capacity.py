import json
import pytest
from bestseller.services import conception
from bestseller.services.llm_capacity import measure_request_capacity, ModelRequestCapacityError, assert_effective_request_capacity
pytestmark=pytest.mark.unit


def test_receipt_only_prompt_keeps_full_frozen_plan_every_issue_and_user_intent():
    final={'title':'合成故事','premise':'事实'*11000,'ending':'不可丢失的结尾',
        'review_adjudications':[{'issue_id':'contradictions:0','decision':'retained','reason':'具体理由'}]}
    review={'contradictions':['矛盾一','矛盾二'],'gaps':[{'kind':'hard_gap','issue':'必要缺口'}],
        'market_suggestions':['不要重复传入的审美建议']}
    system,user=conception._adjudication_repair_prompts({'description':'用户原故事','chapter_count':4},review,final)
    data=json.loads(user)
    assert data['frozen_final']==final
    assert data['user_story']=='用户原故事' and data['chapter_count']==4
    assert [x['issue_id'] for x in data['actionable_fact_issues']]==['contradictions:0','contradictions:1','gaps:0']
    assert '审美建议' not in user
    assert 'EVERY' in system and 'not downstream fact verification' in system
    report=conception._review_adjudication_audit(review,{'review_adjudications':final['review_adjudications']})
    assert not report['dispositions_complete'] and not report['facts_verified']


def test_capacity_boundary_retained_and_large_evidence_fails_closed():
    messages=[{'role':'system','content':'receipt'},{'role':'user','content':'事实'*1000}]
    measured=measure_request_capacity(messages,tools=None,output_tokens=32768)
    boundary=measured['reserved_total_tokens']
    assert measure_request_capacity(messages,tools=None,output_tokens=32768,total_budget=boundary)['budget_passed']
    assert not measure_request_capacity(messages,tools=None,output_tokens=32768,total_budget=boundary-1)['budget_passed']
    final={'premise':'事实'*30000,'ending':'完整保留'}
    system,user=conception._adjudication_repair_prompts({}, {'contradictions':['冲突']},final)
    assert json.loads(user)['frozen_final']==final
    with pytest.raises(ModelRequestCapacityError):
        assert_effective_request_capacity({'messages':[{'role':'system','content':system},{'role':'user','content':user}],'max_tokens':32768})


def test_capacity_stopped_task_resumes_once_preserving_task_window_and_receipts(tmp_path,monkeypatch):
    from contextlib import asynccontextmanager
    from types import SimpleNamespace
    from bestseller.web import server
    from bestseller.services import conception_continuation as recovery
    dispatched=[];checks=[]
    class Thread:
        def __init__(self,**kwargs):dispatched.append(kwargs['args'])
        def start(self):pass
    @asynccontextmanager
    async def session_scope(settings):yield SimpleNamespace(bind='offline-engine')
    async def allowed(engine,task_id,payload):
        checks.append((engine,task_id,payload['_active_continuation_scope']))
        return payload['_active_continuation_scope']
    monkeypatch.setattr(server.threading,'Thread',Thread)
    monkeypatch.setattr(server,'session_scope',session_scope)
    monkeypatch.setattr(recovery,'resume_allowed',allowed)
    manager=server.WebTaskManager(persist_path=tmp_path/'tasks.json')
    prior={'result':{'execution_terminal':{'terminal':True,'code':'execution_result_unknown'}},'error':'execution_result_unknown'}
    payload={'_active_continuation_scope':'conception-continuation:same:scope','_prior_terminal_receipt':prior,
        'concept_seed':'same story','target_chapters':4}
    task=server.WebTaskState(task_id='same',task_type='autowrite',status='failed',created_at='',updated_at='',
        payload=payload,result=None,error='model_request_capacity_exceeded:{}')
    manager._tasks['same']=task
    result=manager.resume_reviewed_conception('same')
    assert result['task_id']=='same' and result['status']=='queued'
    assert result['payload']['_prior_terminal_receipt']==prior
    assert result['payload']['_active_continuation_scope']==payload['_active_continuation_scope']
    assert result['payload']['_continuation_control_failures'][0]['error']=='model_request_capacity_exceeded:{}'
    assert len(checks)==len(dispatched)==1
    with pytest.raises(ValueError,match='stopped original'):manager.resume_reviewed_conception('same')
    assert len(dispatched)==1


@pytest.mark.asyncio
async def test_candidate_and_final_fidelity_are_distinct_real_review_subjects(monkeypatch):
    captured=[]
    async def call(*args,**kwargs):
        captured.append(kwargs)
        return {'passed':True,'hard_conflicts':[]},[]
    monkeypatch.setattr(conception,'_llm_call_json',call)
    for stage in ('tournament_winner','final_material','reconciled_winner','preproject_materialized'):
        result,_=await conception._judge_explicit_concept_seed_fidelity(None,None,
            concept_seed='同一用户故事，四段完结',final_result={'title':'合成标题','premise':'同一用户故事'},
            ctx={'chapter_count':4,'language':'zh-CN'},fidelity_stage=stage)
        assert result['passed']
    assert captured[0]['template']=='conception_explicit_seed_fidelity_judge'
    assert captured[1]['template']=='conception_final_material_seed_fidelity_judge'
    assert captured[2]['template']=='conception_reconciled_seed_fidelity_judge'
    assert captured[3]['template']=='conception_preproject_materialized_seed_fidelity_judge'
    assert captured[3]['user_prompt']==captured[1]['user_prompt']
    assert captured[3]['system_prompt']==captured[1]['system_prompt']
    assert '候选协议' in captured[0]['system_prompt']
    assert '成书构思终稿' in captured[1]['system_prompt']
    for call in captured:
        assert '同一用户故事，四段完结' in call['user_prompt']
        assert '不得替换主角身份/姓名' in call['system_prompt']



def test_reconciled_evidence_keeps_all_facts_with_exact_duplicate_path_reference():
    from copy import deepcopy
    architecture={'causal_units':[{'ending':'完整终点'+('事实'*11000)}],'story_engine':{'endgame_direction':'真实收束'}}
    assets={'candidate_contract':{'story_architecture':architecture,'opening':'原始开局'},
        'premise':'用户原故事','writing_profile':{'style':{'pov':'third'}},
        'world_model':{'story_architecture':deepcopy(architecture),'other_fact':'额外事实仍保留'}}
    before=deepcopy(assets);evidence=conception._reconciled_fidelity_evidence(assets)
    assert assets==before
    assert evidence['candidate_contract']==assets['candidate_contract']
    ref=evidence['world_model']['story_architecture']
    assert ref['$evidence_ref']=='/candidate_contract/story_architecture' and len(ref['sha256'])==64
    assert evidence['world_model']['other_fact']=='额外事实仍保留'
    reconstructed=deepcopy(evidence)
    reconstructed['world_model']['story_architecture']=reconstructed['candidate_contract']['story_architecture']
    assert reconstructed==assets
    prompt=conception._render_reconciled_fidelity_story_assets(assets)
    assert '已截断' not in prompt and architecture['causal_units'][0]['ending'] in prompt


def test_reconciled_changed_projection_is_never_deduplicated():
    assets={'candidate_contract':{'story_architecture':{'causal_units':[1]}},
        'world_model':{'story_architecture':{'causal_units':[1.0]}}}
    assert conception._reconciled_fidelity_evidence(assets)==assets


def test_compact_reconciled_evidence_reduces_duplicates_under_original_capacity_limit():
    architecture={'facts':'共同修复寻找和解与告别'*1000,'ending':'完整结局'}
    assets={'candidate_contract':{'story_architecture':architecture},'world_model':{'story_architecture':architecture},'premise':'用户故事'}
    original=conception._render_seed_fidelity_story_assets(assets,budgets=conception._TOURNAMENT_FIDELITY_ASSET_BUDGETS)
    compact=conception._render_reconciled_fidelity_story_assets(assets)
    measure=lambda s:measure_request_capacity([{'role':'system','content':'独立保真检查'},{'role':'user','content':s}],tools=None,output_tokens=32768)
    assert measure(original)['reserved_total_tokens']>65536
    assert measure(compact)['budget_passed'] and measure(compact)['output_reserve_tokens']==32768
