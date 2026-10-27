"""Actual paid v5 data + synthetic boundary checks; zero native/DB/provider IO."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import socket
import sqlite3
from uuid import uuid4

import pytest
from sqlalchemy.engine import Engine

from bestseller.services import fanqie_short_semantic_evidence_selection as selection
from bestseller.services import fanqie_short_semantic_review as semantic

pytestmark = pytest.mark.unit
ROOT=Path(__file__).resolve().parents[2]
EXECUTION=ROOT/'reports/project-reliability-review-2026-10-24/implementation/original-four-whole-v5-protocol-execution.json'
PAID_PREIMAGE=ROOT/'reports/project-reliability-review-2026-10-24/implementation/whole-v5-paid-preimage/src/bestseller/services'


@pytest.fixture(scope='module')
def captured():
    return json.loads(EXECUTION.read_text())


@pytest.fixture(scope='module')
def base_plan(captured):
    return selection.prepare_selection(captured['result'])


@pytest.fixture(autouse=True)
def offline_guards(monkeypatch):
    def forbidden(*a,**k): raise AssertionError('forbidden IO or current-source rebuild')
    monkeypatch.setattr(Engine,'connect',forbidden)
    monkeypatch.setattr(sqlite3,'connect',forbidden)
    monkeypatch.setattr(socket.socket,'connect',forbidden)
    monkeypatch.setattr(socket.socket,'connect_ex',forbidden)
    monkeypatch.setattr(semantic,'_fresh',forbidden)
    monkeypatch.setattr(semantic,'validate_response',forbidden)
    monkeypatch.setattr(semantic,'_native_complete_text',forbidden)


def abstentions(plan):
    return dict(binding_sha256=plan['binding_sha256'], selections={r['id']:dict(
        status='uncertain', evidence_ids=[], events=[],
        repair_rationale='当前窗口的证据不足以严格证明独立完成因果事件，故保留无法判断。') for r in plan['repair_rows']})


def completion(args):
    return dict(content='',fallback_used=False,fallback_source=None,finish_reason='tool_calls',
                tool_calls=[dict(id='call_selection_offline',type='function',function=dict(
                    name=selection.TOOL_NAME,arguments=selection.encoded(args)))])


def test_actual_parent_two_body_rows_and_title_rejection_frozen_six_only_repair(captured,base_plan):
    assert captured['calls_before']==287 and captured['calls_after']==288
    assert captured['result']['error']=='nonactual_event'
    assert [r['id'] for r in base_plan['frozen_body_rows']]==['weak_hook@opening_30_percent','opening_pressure_missing@opening_300']
    assert [(r['id'],r['original_verdict']) for r in base_plan['frozen_title_rows']]==[('title_abstract_setting_only@title','confirmed')]
    assert len(base_plan['repair_rows'])==6
    assert all(r['original_verdict']=='not_supported' for r in base_plan['repair_rows'])
    assert base_plan['parent_raw_response_sha256']=='41fe5f374a10a9701e29af4fe2187bdc3754bf17a4e41fc5799613ae777b7432'
    raw=json.loads(captured['result']['raw_response'])
    by_id={r['id']:r for r in raw['reviews']}
    for r in base_plan['frozen_body_rows']+base_plan['frozen_title_rows']+base_plan['repair_rows']:
        assert r['raw_row']==by_id[r['id']]
        assert r['original_reason']==by_id[r['id']]['reason']


def test_seven_frozen_paid_preimage_files_are_trusted_not_current_mapper(captured):
    for name,sha in selection.V5_PAID_SOURCES.items():
        assert hashlib.sha256((PAID_PREIMAGE/name).read_bytes()).hexdigest()==sha
    assert selection.prepare_selection(captured['result'])['parent_report']==captured['result']


def test_historical_preimage_does_not_require_current_mapper_or_current_seven_hashes(captured,monkeypatch):
    actual=Path.read_bytes
    mapper=Path(semantic.__file__).parent/'native_publication_title.py'
    monkeypatch.setattr(Path,'read_bytes',lambda p: actual(p)+b'\n# current mapper revision\n' if p==mapper else actual(p))
    monkeypatch.setattr(semantic,'implementation_source_hashes',lambda: (_ for _ in ()).throw(AssertionError('no old-source freshness')))
    assert len(selection.prepare_selection(captured['result'])['repair_rows'])==6


def test_every_actual_bank_is_complete_literal_unique_and_not_only_positive(base_plan):
    text=base_plan['parent_report']['request_payload']['full_text']
    for bank in base_plan['scope_banks']:
        scope=bank['scope']
        assert bank['scope_text']==text[scope['start']:scope['end']]
        expected=selection.compile_scope_bank(text,scope,bank_id=bank['id'])
        assert [(c['span'],c['kinds'],c['asserted_eligible']) for c in bank['candidates']]==[(c['span'],c['kinds'],c['asserted_eligible']) for c in expected['candidates']]
        assert bank['excluded_units']==expected['excluded_units']
        for candidate in bank['candidates']:
            s=candidate['span'];assert text[s['start']:s['end']]==s['quote']
            assert scope['start']<=s['start']<s['end']<=scope['end']
            assert text.find(s['quote'],s['start']+1,scope['end'])<0
    assert any(not c['asserted_eligible'] for b in base_plan['scope_banks'] for c in b['candidates'])


def test_full_sentence_modality_and_same_clause_negation_are_retained_not_selectable():
    text='如果门开了，我修好了锁。我没有修好马达，供电与走带恢复正常，未再卡滞。'
    b=selection.compile_scope_bank(text,dict(source='full_text',start=0,end=len(text)))
    by_quote={c['span']['quote']:c for c in b['candidates']}
    for q in ('我修好了锁。','我没有修好马达，','未再卡滞。'):
        assert by_quote[q]['asserted_eligible'] is False
    assert by_quote['供电与走带恢复正常，']['asserted_eligible'] is True
    with pytest.raises(selection.SelectionInputError,match='nonactual_selected_span'):
        selection._member(by_quote['我修好了锁。']['id'],{c['id']:c for c in b['candidates']},asserted=True)


def test_window_clipped_clauses_and_ambiguous_repetitions_never_become_source_ids():
    text='门关着。完成。完成。请把电源接好，我接通了电源。'
    scope=dict(source='full_text',start=1,end=len(text)-3)
    b=selection.compile_scope_bank(text,scope)
    assert not any(c['span']['start']==1 or c['span']['end']==scope['end'] for c in b['candidates'])
    assert all(c['span']['quote']!='完成。' for c in b['candidates'])
    assert any(c['span']['quote']=='完成。' for c in [x for x in b['excluded_units']])


def test_request_is_forced_native_tool_without_unverified_strict_or_response_format(base_plan):
    req=selection.native_request_fields(base_plan,workflow_run_id=str(uuid4()))
    assert req['tool_choice']=='required'
    assert len(req['tools'])==1 and 'strict' not in req['tools'][0]['function']
    assert 'response_format' not in req
    assert req['max_tokens_override']==12000 and req['sample_slot']==0
    assert req['prompt_template']=='fanqie_short_semantic_evidence_selection'
    assert req['metadata']['tool_schema_sha256']==selection.digest(req['tools'][0])
    assert len(req['user_prompt'])+len(selection.encoded(req['tools']))+len(req['system_prompt'])<=120000
    assert len(selection.encoded(req['tools']))<5000
    parameters=req['tools'][0]['function']['parameters']
    assert parameters['additionalProperties'] is False
    assert set(parameters['properties']['selections']['properties'])=={r['id'] for r in base_plan['repair_rows']}
    assert 'verdict' not in selection.encoded(parameters) and 'original_reason' not in selection.encoded(parameters)


def test_compact_wire_reconstructs_every_scope_candidate_without_context_loss(base_plan):
    req=selection.native_request_fields(base_plan,workflow_run_id=str(uuid4()))
    wire=json.loads(req['user_prompt'])
    assert wire['full_text']==base_plan['parent_report']['request_payload']['full_text']
    for actual,bank in zip(wire['scope_banks'],base_plan['scope_banks'],strict=True):
        context=wire[actual['scope_text_ref']] if 'scope_text_ref' in actual else actual['scope_text']
        assert context==bank['scope_text']
        records={r[0]:r for r in wire['source_spans'] if bank['id'] in r[3]}
        assert set(records)=={c['id'] for c in bank['candidates']}
        for candidate in bank['candidates']:
            assert records[candidate['id']][1]==candidate['span']['quote']
            assert records[candidate['id']][2] is candidate['asserted_eligible']


def test_all_uncertain_proof_preserves_originals_and_does_not_promote_title(base_plan):
    proof=selection.validate_selection(base_plan,completion(abstentions(base_plan)))
    assert len(proof['uncertain_rows'])==6 and proof['selected_rows']==[]
    assert proof['frozen_body_rows']==base_plan['frozen_body_rows']
    assert proof['title_blockers']==base_plan['frozen_title_rows']
    assert all(r['verdict']=='uncertain' and r['original_verdict']=='not_supported' and r['resolved_row'] is None for r in proof['uncertain_rows'])
    assert 'passed' not in proof and 'validated' not in proof


def test_jsonb_object_order_does_not_change_plan_or_toolproof(base_plan):
    copy=json.loads(selection.encoded(base_plan))
    assert selection.validate_selection(copy,completion(abstentions(copy)))==selection.validate_selection(base_plan,completion(abstentions(base_plan)))


@pytest.mark.parametrize('mutation', ['free_json','missing_tool','multiple_tools','wrong_name','wrong_binding','missing_row','frozen_row','flip_verdict','revised_original_reason','uncertain_with_evidence','duplicate_key'])
def test_malformed_or_rejudging_protocol_fails_closed(base_plan,mutation):
    args=abstentions(base_plan); c=completion(args)
    first=next(iter(args['selections']))
    if mutation=='free_json': c['content']=selection.encoded(args);c['tool_calls']=None
    if mutation=='missing_tool': c['tool_calls']=[]
    if mutation=='multiple_tools': c['tool_calls']*=2
    if mutation=='wrong_name': c['tool_calls'][0]['function']['name']='another_tool'
    if mutation=='wrong_binding': args['binding_sha256']='0'*64
    if mutation=='missing_row': del args['selections'][first]
    if mutation=='frozen_row': args['selections'][base_plan['frozen_body_rows'][0]['id']]=deepcopy(args['selections'][first])
    if mutation=='flip_verdict': args['selections'][first]['verdict']='confirmed'
    if mutation=='revised_original_reason': args['selections'][first]['original_reason']='改写历史'
    if mutation=='uncertain_with_evidence': args['selections'][first]['evidence_ids']=['c0']
    if mutation not in {'free_json','missing_tool','multiple_tools','wrong_name'}:
        c['tool_calls'][0]['function']['arguments']=selection.encoded(args)
    if mutation=='duplicate_key': c['tool_calls'][0]['function']['arguments']='{"binding_sha256":"x","binding_sha256":"y","selections":{}}'
    with pytest.raises(ValueError): selection.validate_selection(base_plan,c)


@pytest.mark.parametrize('kind',['unknown','cross_scope','nonasserted'])
def test_source_ids_cannot_escape_banks_or_select_nonactual_action(base_plan,kind):
    args=abstentions(base_plan)
    target=next(r for r in base_plan['repair_rows'] if r['item']['target']=='opening_80')
    banks={b['id']:b for b in base_plan['scope_banks']};bank=banks[target['bank_id']]
    other=next(b for b in base_plan['scope_banks'] if b['scope']['end']==len(base_plan['parent_report']['request_payload']['full_text']))
    valid_id=bank['candidates'][0]['id']
    entry=args['selections'][target['id']];entry.update(status='selected',evidence_ids=[valid_id])
    if kind=='unknown':entry['evidence_ids']=['c999999']
    if kind=='cross_scope':entry['evidence_ids']=[next(c['id'] for c in other['candidates'] if c['span']['start']>bank['scope']['end'])]
    if kind=='nonasserted':
        target=next(r for r in base_plan['repair_rows'] if r['item']['code']=='first_10k_loop_missing');bank=banks[target['bank_id']]
        bad=next(c['id'] for c in bank['candidates'] if not c['asserted_eligible'])
        args=abstentions(base_plan);args['selections'][target['id']].update(status='selected',evidence_ids=[bank['candidates'][0]['id']],
            events=[dict(goal_id=bad,action_id=bad,result_id=bad,causal_link='此处只检验非发生态候选不能作为实际行动或完成结果。')])
    with pytest.raises(selection.SelectionInputError): selection.validate_selection(base_plan,completion(args))


def test_selected_row_preserves_original_reason_and_uses_separate_rationale(base_plan):
    # Structural wiring only: this does not establish that the particular
    # actual window semantically defeats its finding. Uses the real validator.
    args=abstentions(base_plan);r=next(x for x in base_plan['repair_rows'] if x['item']['target']=='opening_80')
    bank=next(b for b in base_plan['scope_banks'] if b['id']==r['bank_id'])
    args['selections'][r['id']].update(status='selected',evidence_ids=[bank['candidates'][0]['id']])
    proof=selection.validate_selection(base_plan,completion(args))
    selected=proof['selected_rows'][0]
    assert selected['resolved_row']['reason']==selected['original_reason']==r['original_reason']
    assert selected['repair_rationale']==args['selections'][r['id']]['repair_rationale']
    assert selected['resolved_row']['verdict']==selected['original_verdict']=='not_supported'
    assert proof['title_blockers'][0]['original_verdict']=='confirmed'


def test_plan_tamper_stops_and_original_paid_source_stays_byte_exact(base_plan):
    clone=deepcopy(base_plan);clone['scope_banks'][0]['candidates'][0]['span']['quote']='伪造引文'
    with pytest.raises(selection.SelectionInputError,match='selection_plan_changed'):
        selection.validate_selection(clone,completion(abstentions(clone)))
    assert hashlib.sha256(Path(semantic.__file__).read_bytes()).hexdigest()==selection.FROZEN_REVIEW_SHA256


def structural_density_selection(plan):
    # These are deliberately structural fixtures, not a new model verdict or
    # a claim of literary causality between arbitrarily sampled sentences.
    r=next(r for r in plan['repair_rows'] if r['item']['code']=='payoff_density_too_low')
    bank=next(b for b in plan['scope_banks'] if b['id']==r['bank_id'])
    units=[c for c in bank['candidates'] if 'sentence' in c['kinds'] and c['asserted_eligible']]
    n=r['item']['expected_payoff_count']; assert n==4
    ev=[]
    for i in range(n):
        g,a,z=units[3*i:3*i+3]
        ev.append(dict(goal_id=g['id'],action_id=a['id'],result_id=z['id'],
                       causal_link='仅供结构校验的合成因果说明，不代表实际文学因果关系已经成立。'))
    args=abstentions(plan)
    args['selections'][r['id']].update(status='selected', evidence_ids=[units[2]['id']], events=ev)
    return args,r['id']


def test_structural_four_event_ids_use_real_v5_validator_without_title_override(base_plan):
    args,key=structural_density_selection(base_plan)
    proof=selection.validate_selection(base_plan,completion(args))
    assert len(proof['selected_rows'])==1
    assert len(proof['selected_rows'][0]['resolved_row']['events'])==4
    assert len(proof['uncertain_rows'])==5 and len(proof['title_blockers'])==1
    assert proof['selected_rows'][0]['original_reason']==args['selections'][key].get('original_reason',proof['selected_rows'][0]['resolved_row']['reason'])


@pytest.mark.parametrize('mutation,expected', [
    ('order','event_order'),('overlap','overlapping_events'),
    ('missing_count','completed_event_count'),('short_causal','event_causality'),
])
def test_id_selection_still_enforces_original_order_overlap_n_and_causality(base_plan,mutation,expected):
    args,key=structural_density_selection(base_plan);events=args['selections'][key]['events']
    if mutation=='order': events[0]['action_id']=events[0]['result_id']
    if mutation=='overlap': events[1]=deepcopy(events[0])
    if mutation=='missing_count':events.pop()
    if mutation=='short_causal':events[0]['causal_link']='相邻'
    with pytest.raises(ValueError,match=expected): selection.validate_selection(base_plan,completion(args))


def test_each_original_parent_failure_and_frozen_receipt_is_bound(base_plan):
    proof=selection.validate_selection(base_plan,completion(abstentions(base_plan)))
    for key in ('parent_report_sha256','parent_raw_response_sha256','parent_payload_sha256','parent_binding_sha256','bank_sha256','frozen_rows_sha256'):
        assert proof[key]==base_plan[key]
    assert proof['plan_binding_sha256']==base_plan['binding_sha256']
    assert proof['tool_schema_sha256']==selection.digest(selection.tool_schema(base_plan))


def test_foreign_parent_source_hash_cannot_be_blessed_by_rehashing_report(captured):
    r=deepcopy(captured['result']);p=r['request_payload']
    p['implementation_source_hashes']['native_publication_title.py']='0'*64
    with pytest.raises(selection.SelectionInputError,match='parent_not_frozen_paid_v5_route'):
        selection.prepare_selection(r)


def diagnostic_ref(bank, span, text):
    # Complete-unit IDs only. Diagnostic quotes may omit their trailing comma;
    # including that original delimiter preserves the complete clause's text.
    start=[c for c in bank['candidates'] if c['span']['start']==span['start']]
    ends=[c for c in bank['candidates'] if c['span']['end']==span['end'] or
          (c['span']['end']==span['end']+1 and text[span['end']:span['end']+1] in '，,；;。！？.!?')]
    for a in sorted(start,key=lambda c:c['span']['end']):
        for b in sorted(ends,key=lambda c:c['span']['start'],reverse=True):
            if a['span']['start']<=b['span']['start'] and a['span']['end']<=b['span']['end']:
                ref=a['id'] if a['id']==b['id'] else a['id']+':'+b['id']
                resolved=selection._member(ref,{c['id']:c for c in bank['candidates']},full_text=text,scope=bank['scope'])
                assert resolved['quote'].startswith(span['quote'])
                return ref
    raise AssertionError(('unrepresentable_editor_diagnostic_span',span))


def editor_diagnostic_arguments(plan):
    path=ROOT/'reports/project-reliability-review-2026-10-24/implementation/whole-semantic-v5-event-diagnostic-stage/editor-diagnostic.json'
    diagnostic=json.loads(path.read_text());args=abstentions(plan)
    assert diagnostic['authority'].startswith('editor_diagnostic_only')
    text=plan['parent_report']['request_payload']['full_text']
    catalog=[r['event_for_strict_helper_only'] for r in diagnostic['minimal_four_event_catalog']]
    for r in plan['repair_rows']:
        bank=next(b for b in plan['scope_banks'] if b['id']==r['bank_id'])
        entry=args['selections'][r['id']];entry['status']='selected'
        if r['item']['target']=='opening_80':
            pressure=next(w['pressure_span'] for w in diagnostic['exact_windows'] if w['target']=='opening_80')
            entry['evidence_ids']=[diagnostic_ref(bank,pressure,text)]
            entry['repair_rationale']='此窗口首句绑定主角维修铺，随后给出今晚十点四十房东收铺的迫近损失，未借用窗口外句尾。'
            continue
        chosen=catalog if r['item']['code']=='payoff_density_too_low' else catalog[:1]
        selected=[]
        for event in chosen:
            selected.append({key+'_id':diagnostic_ref(bank,event[key],text) for key in ('goal','action','result')}
                            | {'causal_link':event['causal_link']})
        entry['events']=selected
        entry['evidence_ids']=[e['result_id'] for e in selected]
        entry['repair_rationale']=('本窗口卸下后盖后发现夹层维修单，是可见的实际物证收益；不声称窗外已经看见乔芸姓名。'
            if len(chosen)==1 else '全文依次给出发现维修单、原带实际出声、当面确认来源以及完成六十分钟试机四个独立变化，保留机器与关系各自限制。')
    return args,diagnostic


def test_every_actual_invalid_row_is_expressible_editor_diagnostic_to_ids_to_strict(base_plan):
    args,diagnostic=editor_diagnostic_arguments(base_plan)
    proof=selection.validate_selection(base_plan,completion(args))
    assert {r['id'] for r in proof['selected_rows']}=={r['id'] for r in base_plan['repair_rows']}
    assert len(proof['selected_rows'])==6 and proof['uncertain_rows']==[]
    assert proof['frozen_body_rows']==base_plan['frozen_body_rows']
    assert proof['frozen_title_rows']==proof['title_blockers']==base_plan['frozen_title_rows']
    density=next(r for r in proof['selected_rows'] if r['id'].startswith('payoff_density'))
    assert len(density['resolved_row']['events'])==4
    text=base_plan['parent_report']['request_payload']['full_text']
    for record in proof['selected_rows']:
        item=next(r['item'] for r in base_plan['repair_rows'] if r['id']==record['id'])
        selection._validator()._validate_row(record['resolved_row'],item,base_plan['parent_report']['request_payload'])
        assert record['original_reason']==record['resolved_row']['reason']
        for e in record['resolved_row']['events']:
            for k in ('goal','action','result'):
                span=e[k]; assert text[span['start']:span['end']]==span['quote']
    # No literary verdict/paid-result replacement is inferred from this test.
    assert diagnostic['authority'].startswith('editor_diagnostic_only') and 'passed' not in proof


def test_range_rules_are_general_and_cannot_skip_internal_negation_or_modality():
    text='门坏了。我拆下旧件，装好新的锁芯。门恢复正常。若是门再坏，我修好了锁。'
    bank=selection.compile_scope_bank(text,dict(source='full_text',start=0,end=len(text)))
    byquote={c['span']['quote']:c for c in bank['candidates']};members={c['id']:c for c in bank['candidates']}
    first=byquote['我拆下旧件，'];last=byquote['装好新的锁芯。']
    span=selection._member(first['id']+':'+last['id'],members,asserted=True,full_text=text,scope=bank['scope'])
    assert span['quote']=='我拆下旧件，装好新的锁芯。'
    with pytest.raises(selection.SelectionInputError,match='source_range_order'):
        selection._member(last['id']+':'+first['id'],members,full_text=text,scope=bank['scope'])
    end=byquote['我修好了锁。']
    with pytest.raises(selection.SelectionInputError,match='nonactual_selected_span'):
        selection._member(first['id']+':'+end['id'],members,asserted=True,full_text=text,scope=bank['scope'])


def test_packaged_runtime_prepares_actual_parent_with_reports_db_gateway_access_forbidden(captured,tmp_path):
    import os
    import subprocess
    import sys
    code='''
import importlib.abc,sys,json,sqlite3,socket
from unittest.mock import patch
class NoGateway(importlib.abc.MetaPathFinder):
 def find_spec(self, fullname, path=None, target=None):
  if fullname == 'bestseller.services.llm':
   raise AssertionError('provider gateway import forbidden')
sys.meta_path.insert(0,NoGateway())
def audit(event,args):
 if event == 'open' and isinstance(args[0],str) and '/reports/' in args[0]:
  raise AssertionError('runtime reports dependency')
sys.addaudithook(audit)
from sqlalchemy.engine import Engine
with patch.object(socket.socket,'connect',side_effect=AssertionError('network')), patch.object(sqlite3,'connect',side_effect=AssertionError('DB')), patch.object(Engine,'connect',side_effect=AssertionError('DB')):
 from bestseller.services import fanqie_short_semantic_evidence_selection as s
 p=s.prepare_selection(json.loads(sys.stdin.read()))
 assert len(p['repair_rows'])==6 and len(p['frozen_body_rows'])==2
 assert p['frozen_title_rows'][0]['original_verdict']=='confirmed'
 print('packaged history only, no reports/provider gateway/DB connections')
'''
    env={**os.environ,'PYTHONPATH':str(ROOT/'src'),'PYTHONDONTWRITEBYTECODE':'1'}
    run=subprocess.run([sys.executable,'-B','-c',code],cwd=tmp_path,env=env,
                       input=selection.encoded(captured['result']),text=True,capture_output=True)
    assert run.returncode==0,run.stderr
    assert 'packaged history only' in run.stdout
