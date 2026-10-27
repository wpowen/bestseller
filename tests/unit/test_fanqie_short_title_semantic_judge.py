"""Pure title protocol with SYNTHETIC adoption rows over retained paid input.

No production adoption/semantic verdict or provider/DB call is performed.
"""
from copy import deepcopy
import json
from pathlib import Path
import socket
import sqlite3
from uuid import UUID,uuid4,uuid5

import pytest
from sqlalchemy.engine import Engine
from bestseller.services import fanqie_short_title_semantic_judge as judge
from bestseller.services import native_paid_title_selection as adoption
from bestseller.services import native_publication_title as mapper
from bestseller.services.fanqie_short_quality import review_whole_fanqie_short_story

ROOT=Path(__file__).resolve().parents[2]
BASE=ROOT/'reports/project-reliability-review-2026-10-24/implementation'
pytestmark=pytest.mark.unit


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def no(*a,**k):raise AssertionError('DB/network/provider forbidden')
    monkeypatch.setattr(Engine,'connect',no);monkeypatch.setattr(sqlite3,'connect',no)
    monkeypatch.setattr(socket.socket,'connect',no);monkeypatch.setattr(socket.socket,'connect_ex',no)
    monkeypatch.setattr(judge.current,'_native_complete_text',no)


def fixture_inputs():
    paid=json.loads((BASE/'original-four-whole-v4-execution.json').read_text())['payload']
    origin=deepcopy(paid['approved_contract']['approval_evidence']['native_publishing_title_evidence'])
    context={k:deepcopy(paid[k]) for k in ('project_id','title','full_text','manifest','approved_contract')}
    selected='我修好旧收音机时，已经交还了铺面钥匙'  # actual retained candidate, not a new suggestion
    before=deepcopy(origin['project']['metadata_json'])
    plan=adoption.prepare_paid_title_selection(origin,context=context,selected_title=selected,
        delegation=dict(original_task_id=adoption.op.TASK_ID,actor='assistant',authorization_ref='SYNTHETIC-test-only-no-production-authority',
                        reason='SYNTHETIC unit row assembly preserves original paid candidate and open debt without a new model call.'),
        project_lock_version=0,review_fence_sha256='f'*64,current_metadata_sha256=adoption.op.fingerprint(before))
    metadata,lineage,record=adoption.selection_metadata(before,plan=plan,origin_project=origin['project'])
    wf=plan['workflow_run_id'];manifest=deepcopy(context['manifest']);manifest['project']['title']=selected
    wrapped=dict(schema=mapper.SELECTION_SCHEMA,project=dict(id=context['project_id'],title=selected,metadata_json=metadata),origin=origin,
        selection_workflow=dict(id=wf,project_id=context['project_id'],workflow_type=adoption.WORKFLOW_TYPE,status='completed',
            current_step='paid_title_candidate_adopted',scope_type='project',scope_id=context['project_id'],requested_by='assistant',error_message=None,
            metadata_json=dict(schema=adoption.SCHEMA,execution_scope_id=adoption.op.SCOPE,original_task_id=adoption.op.TASK_ID,
                selection_plan=deepcopy(plan),origin_project_projection=deepcopy(origin['project']),selection_metadata_before=before,
                editorial_quality_debt=deepcopy(plan['editorial_quality_debt']),**{adoption.LINEAGE_KEY:deepcopy(lineage)})),
        selection_step=dict(id=str(uuid5(UUID(wf),'paid_title_candidate_selection')),workflow_run_id=wf,
            step_name='adopt_existing_paid_title_candidate',step_order=1,status='completed',error_message=None,
            input_ref=dict(plan_sha256=plan['plan_sha256'],source_manifest=context['manifest']),
            output_ref={adoption.LINEAGE_KEY:deepcopy(lineage),'title_record':deepcopy(record)}))
    values={k:deepcopy(paid[k]) for k in ('project_id','workflow_run_id','current_scope','full_text','title','protagonist','manifest',
        'approved_contract','original_report','operation_namespace','execution_scope_id','unlock_line_ratio','methodology_contract_mode')}
    values.update(title=selected,manifest=manifest,recovery_parent=None)
    values['approved_contract']['approval_evidence']['native_publishing_title_evidence']=wrapped
    values['original_report']=review_whole_fanqie_short_story(values['full_text'],title=selected,unlock_line_ratio=.30,protagonist_name=values['protagonist']).to_dict()
    parent=json.loads((BASE/'original-four-whole-v5-protocol-execution.json').read_text())
    descriptor=dict(artifact_id=parent['artifact_id'],workflow_id=parent['result']['request_payload']['workflow_run_id'],
        content_sha256=judge.digest(parent['result']),error=parent['result']['error'],raw_response_sha256=judge.sha(parent['result']['raw_response']))
    return values,descriptor


@pytest.fixture(scope='module')
def title_plan():
    values,parent=fixture_inputs()
    return judge.prepare_title_review(values,parent_descriptor=parent)


def supported_entry(plan,verdict):
    paths=plan['contract_requirements']
    ids=set(paths['required_contract_ids']);ids.add(paths['canonical_story_any_of_ids'][0]);ids.add(paths['explicit_creator_any_of_ids'][0])
    # A complete current body clause containing the actual positive test result.
    candidate=next(c for c in plan['body_bank']['candidates'] if '连续六十分钟试机中恢复正常' in c['span']['quote'] and c['asserted_eligible'])
    return dict(verdict=verdict,reason='当前标题明确区分交还铺面钥匙与后来修好机器的时序，并由全文实际修复结果支撑。',
        evidence_ids=['t0'],contract_ids=sorted(ids),body_evidence_ids=[candidate['id']],
        readable_promise='交还店铺以后继续修复旧收音机，让读者期待物件与家庭来源核验的具体结果。',
        contract_explanation='明确创作种子和批准承诺支持现实维修与有限联系的故事；默认反击背景不覆盖这些明确边界，正文提供之后才恢复功能的完成结果。',
        default_contract=plan['current_payload']['default_title_contract'],readability='supported',creator_alignment='supported',
        default_basis='explicit_creator_and_canonical_content',readability_reason='标题以交钥匙与修好旧物的先后关系形成清楚的现实压力和行动结果期待，未承诺恢复共同生活。',
        body_reason='正文在交还钥匙后的新场所完成固定修复和六十分钟试机，供电走带恢复正常，且仍保留机器磨损和关系边界。')


def answer(plan):
    return dict(binding_sha256=plan['binding_sha256'],title_assessment=supported_entry(plan,'supported'),
                reviews={i['id']:supported_entry(plan,'not_supported') for i in plan['actual_title_rows']})


def completion(args):
    return dict(content='',fallback_used=False,fallback_source=None,finish_reason='tool_calls',tool_calls=[dict(
        id='call_offline_title',type='function',function=dict(name=judge.TOOL_NAME,arguments=judge.encoded(args)))])


def test_current_native_wrapper_and_debt_preserved_no_adoption_equals_semantic_acceptance(title_plan):
    assert title_plan['current_payload']['title']=='我修好旧收音机时，已经交还了铺面钥匙'
    assert title_plan['source_receipt']['lineage_kind']=='delegated_paid_candidate_selection'
    assert title_plan['editorial_quality_debt'] and title_plan['editorial_quality_debt'][0]['status']=='open'
    assert title_plan['editorial_quality_debt'][0]['overall_score']==52
    assert title_plan['human_attraction_acceptance']=='not_performed'
    assert all(i['target']=='title' for i in title_plan['actual_title_rows'])


def test_native_forced_title_tool_has_no_body_outputs_or_unverified_provider_keys(title_plan):
    req=judge.native_request_fields(title_plan,workflow_run_id=str(uuid4()))
    assert req['tool_choice']=='required'
    assert req['prompt_template']==judge.TEMPLATE and req['prompt_version']=='selection.1'
    assert req['max_tokens_override']==12000 and req['sample_slot']==0
    assert 'strict' not in req['tools'][0]['function'] and 'response_format' not in req
    assert len(req['user_prompt'])+len(judge.encoded(req['tools']))+len(req['system_prompt'])<=120000
    properties=req['tools'][0]['function']['parameters']['properties']
    assert set(properties)=={'binding_sha256','title_assessment','reviews'}
    assert set(properties['reviews']['properties'])=={i['id'] for i in title_plan['actual_title_rows']}


def test_synthetic_tool_ids_expand_to_same_v5_title_rules_and_keep_debt(title_plan):
    original=deepcopy(title_plan)
    proof=judge.validate_title_response(title_plan,completion(answer(title_plan)))
    assert proof['title_assessment']['verdict']=='supported' and proof['blockers']==[]
    assert all(r['verdict']=='not_supported' for r in proof['reviews'])
    assert proof['source_receipt']==title_plan['source_receipt']
    assert proof['editorial_quality_debt']==title_plan['editorial_quality_debt']
    assert proof['human_attraction_acceptance']=='not_performed' and 'passed' not in proof
    assert title_plan==original
    for row in proof['reviews']:
        item=next(i for i in title_plan['actual_title_rows'] if i['id']==row['id'])
        judge.sources._validator()._validate_row(row,item,title_plan['current_payload'])


@pytest.mark.parametrize('mutation,expected',[
    ('missing_contract','title_contract_unresolved'),('missing_body','title_body_evidence_missing'),
    ('wrong_default','title_default_template_mismatch'),('bad_grade','title_assessment_grade'),
    ('body_id_wrong','unknown_or_cross_scope_source_id'),('title_id_wrong','title_source_id'),
    ('extra_body_row','title_actual_row_coverage'),('missing_assessment','title_answer_binding'),
])
def test_strict_title_failures_cannot_be_repaired_or_auto_approved(title_plan,mutation,expected):
    args=answer(title_plan);entry=args['title_assessment']
    if mutation=='missing_contract':entry['contract_ids'].remove(title_plan['contract_requirements']['required_contract_ids'][0])
    if mutation=='missing_body':entry['body_evidence_ids']=[]
    if mutation=='wrong_default':entry['default_contract']='another template'
    if mutation=='bad_grade':entry['readability']='approved'
    if mutation=='body_id_wrong':entry['body_evidence_ids']=['bc999999']
    if mutation=='title_id_wrong':entry['evidence_ids']=['bc0']
    if mutation=='extra_body_row':args['reviews']['weak_hook@opening_30_percent']=deepcopy(entry)
    if mutation=='missing_assessment':del args['title_assessment']
    with pytest.raises(ValueError,match=expected):judge.validate_title_response(title_plan,completion(args))


def test_assessment_unaccepted_blocks_even_when_actual_findings_all_not_supported(title_plan):
    args=answer(title_plan);args['title_assessment']['verdict']='uncertain'
    proof=judge.validate_title_response(title_plan,completion(args))
    assert proof['blockers']==['current_title_assessment_unaccepted']
    assert all(r['verdict']=='not_supported' for r in proof['reviews'])
    assert proof['editorial_quality_debt'][0]['status']=='open'


def test_valid_current_title_rejection_cannot_carry_forbidden_title_assessment(title_plan):
    args=answer(title_plan);key=next(iter(args['reviews']));args['reviews'][key]['verdict']='confirmed'
    with pytest.raises(ValueError,match='unexpected_title_assessment'):
        judge.validate_title_response(title_plan,completion(args))
    empty=('readable_promise','contract_explanation','default_contract','readability','creator_alignment','default_basis','readability_reason','body_reason')
    for k in empty:args['reviews'][key][k]=''
    args['reviews'][key]['body_evidence_ids']=[]
    proof=judge.validate_title_response(title_plan,completion(args))
    assert key in proof['blockers'] and proof['reviews'][0]['verdict']=='confirmed'


def test_no_actual_title_findings_still_requires_independent_assessment_without_fake_finding(title_plan):
    # Direct schema/strict-evidence seam only. The real builder uses the actual
    # native report; it never inserts a finding to force a model request.
    p=deepcopy(title_plan);p['actual_title_rows']=[]
    schema=judge.tool_schema(p)['function']['parameters']
    assert schema['properties']['reviews']['properties']=={} and schema['properties']['reviews']['required']==[]
    assert 'title_assessment' in schema['required']
    carrier=judge._expanded_entry(p,supported_entry(p,'supported'),p['title_assessment_item'],independent=True)
    assert carrier['target']=='title' and p['current_payload']['original_report']==title_plan['current_payload']['original_report']


def test_workflow_identity_stable_and_parent_shape_exact(title_plan):
    values=deepcopy(title_plan['current_inputs']);values['workflow_run_id']=str(uuid4())
    assert judge.prepare_title_review(values,parent_descriptor=title_plan['semantic_selection_parent'])==title_plan
    fields=judge.native_request_fields(title_plan,workflow_run_id=str(uuid4()))
    assert fields['metadata']['semantic_selection_parent']==title_plan['semantic_selection_parent']
    values=deepcopy(title_plan['semantic_selection_parent']);values['approved']=True
    with pytest.raises(ValueError,match='title_parent_descriptor_schema'):
        judge.prepare_title_review(title_plan['current_inputs'],parent_descriptor=values)


def test_free_json_and_wrong_function_cannot_substitute_native_tool_return(title_plan):
    args=answer(title_plan);c=completion(args);c['content']=judge.encoded(args)
    with pytest.raises(ValueError,match='title_free_text_forbidden'):judge.validate_title_response(title_plan,c)
    c=completion(args);c['tool_calls'][0]['function']['name']='select_whole_semantic_evidence'
    with pytest.raises(ValueError,match='title_tool_mismatch'):judge.validate_title_response(title_plan,c)
