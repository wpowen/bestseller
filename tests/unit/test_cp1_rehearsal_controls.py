"""Rehearsal controls: failures must survive cleanup and never authorize PASS."""
from copy import deepcopy
from datetime import datetime, timezone, timedelta
import json
import subprocess

import pytest

from support import cp1_rehearsal as h

pytestmark = pytest.mark.unit


def budgets():
    return {
        "target": {"scope_id":"target", "max_calls":40, "max_tokens":10000,
            "deadline_at":"2030-01-01T00:00:00Z", "status":"active",
            "calls_used":10, "tokens_charged":100, "metadata":{"grant":"kept"}},
        "unrelated": {"scope_id":"unrelated", "status":"unknown", "calls_used":2,
            "tokens_charged":80, "metadata":{"unknown":"kept"}},
    }


@pytest.mark.parametrize("change", ["other_delete", "other_status", "grant", "limit", "deadline", "new_budget", "tokens"])
def test_complete_budget_protection_rejects_each_non_call_delta(change):
    before=budgets();after=deepcopy(before)
    after["target"]["calls_used"]+=5;after["target"]["tokens_charged"]+=1000
    if change=="other_delete":del after["unrelated"]
    elif change=="other_status":after["unrelated"]["status"]="active"
    elif change=="grant":after["target"]["metadata"]={"grant":"forged"}
    elif change=="limit":after["target"]["max_calls"]+=1
    elif change=="deadline":after["target"]["deadline_at"]="2031-01-01T00:00:00Z"
    elif change=="new_budget":after["unexpected"]={"status":"active"}
    elif change=="tokens":after["target"]["tokens_charged"]+=1
    with pytest.raises(h.HarnessInvariantError) as caught:
        h.verify_budgets(before,after,target="target",calls_delta=5,tokens_delta=1000)
    assert caught.value.differences
    assert {"field","expected","actual"} <= set(caught.value.differences[0])


def test_expected_budget_delta_preserves_every_other_field():
    before=budgets();after=deepcopy(before)
    after["target"]["calls_used"]+=5;after["target"]["tokens_charged"]+=1000
    h.verify_budgets(before,after,target="target",calls_delta=5,tokens_delta=1000)


def test_id_queries_remain_bounded_for_a_full_native_inventory():
    identifiers=list(range(150523))
    batches=list(h.id_batches(identifiers))
    assert max(map(len,batches))<=1000
    assert [item for batch in batches for item in batch]==identifiers
    assert list(h.id_batches([]))==[]


def test_datetime_canonicalization_keeps_all_fields_and_exact_instants():
    utc=datetime(2026,10,28,1,2,3,456789,tzinfo=timezone.utc)
    other=utc.astimezone(timezone(timedelta(hours=8)))
    left={"created_at":utc,"raw":"unchanged +08:00", "nested":{"value":False}}
    right={**left,"created_at":other}
    assert h.canonical_row(left)==h.canonical_row(right)
    assert set(h.canonical_row(left))==set(left)
    assert isinstance(h.canonical_row(left)['created_at'],str)
    assert h.canonical_row(left)['created_at'].endswith('+00:00')
    assert h.canonical_row(left)["raw"]==left["raw"]
    assert h.canonical_row(left)["nested"]["value"] is False
    assert h.canonical_row(left)!=h.canonical_row({**left,"created_at":utc+timedelta(microseconds=1)})


def test_final_check_failure_cannot_leave_an_earlier_pass(tmp_path):
    path=tmp_path/'RESULT.json'
    state=h.RunState(path)
    state.checkpoint('native_complete',candidate_success=True)
    state.failure(ValueError('FINAL_SOURCE_CHANGED'),step='final_source_check')
    state.finish()
    stored=json.loads(path.read_text())
    assert stored['status']=='失败' and stored['root_error_code']=='FINAL_SOURCE_CHANGED'
    assert stored['failed_workflow_step']=='final_source_check'
    assert stored['failure_differences'] and stored['traceback_path']


def test_cleanup_exception_keeps_the_original_failure(tmp_path):
    state=h.RunState(tmp_path/'RESULT.json')
    state.failure(KeyError('missing-slot'),step='freeze')
    state.failure(RuntimeError('cleanup failure'),step='cleanup',secondary=True)
    state.finish()
    stored=json.loads(state.path.read_text())
    assert stored['root_exception_type']=='KeyError'
    assert stored['failed_workflow_step']=='freeze'
    assert stored['secondary_failures'][0]['root_error_code']=='cleanup failure'


def test_pass_requires_all_final_checks(tmp_path):
    state=h.RunState(tmp_path/'RESULT.json')
    state.checkpoint('native_complete',candidate_success=True)
    state.finish()
    assert json.loads(state.path.read_text())['status']!='通过'


def test_docker_timeout_stops_and_confirms_owned_container_exit(tmp_path):
    alive=True;operations=[]
    def io(command,**kwargs):
        nonlocal alive
        operations.append(command[1])
        if command[1]=='run':raise subprocess.TimeoutExpired(command,1)
        if command[1]=='stop':alive=False;return subprocess.CompletedProcess(command,0,b'',b'')
        if command[1]=='inspect':return subprocess.CompletedProcess(command,0,'true' if alive else 'false','')
        raise AssertionError(command)
    outcome=h.run_owned_container(['docker','run','--name','owned-fixture','fixture'],
        name='owned-fixture',out=tmp_path,timeout=1,run=io)
    assert not alive and outcome['container_exit_verified']
    assert 'stop' in operations and 'inspect' in operations
    assert outcome['exit_code']==124 and outcome['root_error_code']=='CONTAINER_DEADLINE_EXCEEDED'


def test_complete_old_rows_rejects_mutated_or_missing_history():
    before={'one':'original','two':'kept'}
    h.verify_old_rows(before,{**before,'three':'new'},field='paid')
    for after in ({'one':'forged','two':'kept'},{'two':'kept'}):
        with pytest.raises(h.HarnessInvariantError) as caught:
            h.verify_old_rows(before,after,field='paid')
        assert caught.value.differences[0]['field']=='paid.one'


def test_success_only_after_explicit_final_checks(tmp_path):
    state=h.RunState(tmp_path/'RESULT.json')
    state.checkpoint('all_checks_passed',candidate_success=True)
    assert json.loads(state.path.read_text())['status']=='进行中'
    state.finish(final_checks_complete=True)
    assert json.loads(state.path.read_text())['status']=='通过'
    assert len(list((state.evidence_root/'stages').glob('*.json')))>=3


def test_timeout_with_failed_stop_kills_only_owned_container_and_confirms_exit(tmp_path):
    alive=True;names=[]
    def io(command,**kwargs):
        nonlocal alive
        if command[1]=='run':raise subprocess.TimeoutExpired(command,1)
        if command[1]=='stop':raise subprocess.TimeoutExpired(command,15)
        if command[1]=='kill':
            names.append(command[-1]);alive=False
            return subprocess.CompletedProcess(command,0,b'',b'')
        if command[1]=='inspect':return subprocess.CompletedProcess(command,0,'true' if alive else 'false','')
        raise AssertionError(command)
    result=h.run_owned_container(['docker','run','--name','owned-only','fixture'],
        name='owned-only',out=tmp_path,timeout=1,run=io)
    assert result['container_exit_verified'] and names==['owned-only']


def test_later_cleanup_checkpoint_never_relabels_the_failed_step(tmp_path):
    state=h.RunState(tmp_path/'RESULT.json')
    state.failure(KeyError('slot0'),step='freeze_slot0')
    state.checkpoint('final_source_unchanged',source_unchanged=True)
    state.finish()
    assert json.loads(state.path.read_text())['failed_workflow_step']=='freeze_slot0'


def test_missing_native_key_is_in_failure_fields(tmp_path):
    state=h.RunState(tmp_path/'RESULT.json')
    state.failure(KeyError('slot0'),step='native_freeze')
    state.finish()
    result=json.loads(state.path.read_text())
    assert 'slot0' in result['failure_differences'][0]['field']
    assert result['failure_differences'][0]['actual']['type']=='missing'


def test_separate_run_reports_never_overwrite_each_others_evidence(tmp_path):
    first=h.RunState(tmp_path/'first.json')
    first.failure(KeyError('first-only'),step='first')
    first.finish()
    before={p:p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    second=h.RunState(tmp_path/'second.json')
    second.failure(KeyError('second-only'),step='second')
    second.finish()
    assert all(p.read_bytes()==raw for p,raw in before.items())


def test_unproven_timeout_or_missing_result_never_gets_environment_exemption():
    native={'status':'失败','root_exception_type':'KeyError','failed_workflow_step':'native_caught:freeze',
        'root_location':'/app/src/bestseller/services/commercial_judge_recovery.py:3017','script_consumed_index':2}
    assert h.environment_exemption(native,[],timed_out=True)==(False,None)
    assert h.environment_exemption(None,[],timed_out=False)==(False,None)


def test_confirmed_before_send_harness_error_exemption_is_limited_to_two():
    harness={'status':'失败','root_exception_type':'NameError','failed_workflow_step':'uncaught_entrypoint',
        'root_location':'/audit.py:30','script_consumed_index':0,'root_error_code':'missing_name'}
    allowed,key=h.environment_exemption(harness,[],timed_out=False)
    assert allowed and key
    previous=[{'environment_exempted':True,'environment_same_cause_key':key}]*2
    assert h.environment_exemption(harness,previous,timed_out=False)==(False,key)


def test_budget_metadata_false_cannot_be_replaced_by_integer_zero():
    before=budgets();before['target']['metadata']['approved']=False
    after=deepcopy(before);after['target']['calls_used']+=5;after['target']['tokens_charged']+=1000
    after['target']['metadata']['approved']=0
    with pytest.raises(h.HarnessInvariantError) as caught:
        h.verify_budgets(before,after,target='target',calls_delta=5,tokens_delta=1000)
    assert caught.value.differences
