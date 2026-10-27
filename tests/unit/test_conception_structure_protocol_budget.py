from copy import deepcopy
import pytest
from bestseller.services.conception_structure_budget import reserve_structure_repair, structure_repair_budget

PROTOCOL = 'v2-semantic-context'


def test_fresh_draw_is_reserved_once_and_keeps_monotonic_total():
    state = {'llm_run_ids':['old-paid'], 'conception_log':[{'failure':'retained'}]}
    reserve_structure_repair(state, protocol=PROTOCOL)
    assert state['structure_repair_attempts'] == 1
    assert state['structure_repair_protocol_attempts'] == {PROTOCOL:1}
    before = deepcopy(state)
    with pytest.raises(ValueError, match='exhausted'):
        reserve_structure_repair(state, protocol=PROTOCOL)
    assert state == before


def test_legacy_spent_counter_cannot_gain_current_credit_by_new_workflow_or_label():
    state = {'structure_repair_attempts':1}
    assert structure_repair_budget(state, protocol=PROTOCOL) == (1,1)
    with pytest.raises(ValueError, match='exhausted'):
        reserve_structure_repair(state, protocol=PROTOCOL)
    assert state == {'structure_repair_attempts':1}


def test_explicit_migration_map_preserves_legacy_spend_and_all_history():
    state = {'structure_repair_attempts':1, 'structure_repair_protocol_attempts':{'v1':1,PROTOCOL:0},
             'repair_quotas':{'final_packaging':3}, 'llm_run_ids':['paid-v1'], 'conception_log':['old']}
    reserve_structure_repair(state, protocol=PROTOCOL)
    assert state['structure_repair_attempts'] == 2
    assert state['structure_repair_protocol_attempts'] == {'v1':1,PROTOCOL:1}
    assert state['repair_quotas'] == {'final_packaging':3}
    assert state['llm_run_ids'] == ['paid-v1'] and state['conception_log'] == ['old']
    before = deepcopy(state)
    with pytest.raises(ValueError, match='exhausted'):
        reserve_structure_repair(state, protocol=PROTOCOL)
    assert state == before


@pytest.mark.parametrize('state', [
    {'structure_repair_attempts':True}, {'structure_repair_attempts':-1},
    {'structure_repair_attempts':2}, {'structure_repair_attempts':'1'},
    {'structure_repair_attempts':1,'structure_repair_protocol_attempts':{}},
    {'structure_repair_attempts':1,'structure_repair_protocol_attempts':{'v1':0}},
    {'structure_repair_attempts':1,'structure_repair_protocol_attempts':{'v1':True}},
    {'structure_repair_attempts':1,'structure_repair_protocol_attempts':{'unknown':1}},
    {'structure_repair_attempts':0,'structure_repair_protocol_attempts':{'v1':1}},
])
def test_malformed_or_unattributed_budget_fails_without_mutation(state):
    before=deepcopy(state)
    with pytest.raises(ValueError, match='repair budget'):
        reserve_structure_repair(state, protocol=PROTOCOL)
    assert state==before
