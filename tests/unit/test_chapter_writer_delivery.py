"""Current task remains complete while prior prose stays a reference."""
import json
from uuid import uuid4
import pytest
from bestseller.services.chapter_writer_delivery import delivery_block
from bestseller.services.prompt_compiler import compile_prompt,PromptBudgetError

pytestmark=pytest.mark.unit

@pytest.mark.parametrize('language',['zh','en'])
def test_full_actions_and_both_state_ends_survive_real_compilation(language):
    first='观测站旧仪表已经校准。'*30 if language=='zh' else 'The old gauge has already been calibrated. '*30
    final='释放新探空气球并封存潮位签条。' if language=='zh' else 'Launch the new weather balloon and seal the tide slip.'
    actions=['检查新气球阀门，不再校准旧仪表。','将青色封签贴到新探空囊上。'] if language=='zh' else ['Inspect the new balloon valve; do not recalibrate the old gauge.','Attach the blue seal to the new sounding bag.']
    approved={'chapter_number':7,'scenes':[{'scene_number':1,'participants':['Observer'],
        'entry_state':{'summary':first},'exit_state':{'summary':final},'action_sequence':actions,
        'forbidden_actions':['Do not relaunch yesterday’s probe.'],'hook_requirement':'Keep the seal intact.',
        'key_dialogue_beats':['A discarded stylistic example, not the approved task.'],'sensory_anchors':{'sound':'Old resonance'}}]}
    original=json.dumps(approved,ensure_ascii=False)
    block=delivery_block(project_id=uuid4(),chapter_id=uuid4(),chapter_number=7,artifact_id=uuid4(),approved=approved,receipt={'chapter_numbers':[7,8]})
    compiled=compile_prompt([block],total_budget_tokens=8000,require_provenance=True)
    payload=json.loads(block.text.split('\n',2)[2])
    assert payload['scenes'][0]['entry_state']['summary']==first
    assert payload['scenes'][0]['exit_state']['summary']==final
    assert payload['scenes'][0]['action_sequence']==actions
    assert first in compiled.user and final in compiled.user and all(x in compiled.user for x in actions)
    assert '不能复制为本章正文' in compiled.user and not compiled.report.truncated
    assert 'discarded stylistic example' not in compiled.user
    assert json.dumps(approved,ensure_ascii=False)==original
    with pytest.raises(PromptBudgetError):compile_prompt([block],total_budget_tokens=40,require_provenance=True)

@pytest.mark.parametrize('number,scope',[(6,[7]),(7,[8])])
def test_other_chapter_cannot_become_current_task(number,scope):
    with pytest.raises(ValueError,match='scope_changed'):
        delivery_block(project_id=uuid4(),chapter_id=uuid4(),chapter_number=7,artifact_id=uuid4(),approved={'chapter_number':number,'scenes':[]},receipt={'chapter_numbers':scope})

@pytest.mark.parametrize('targets',[(1100,900),(800,1200),(None,2000)])
def test_existing_approved_length_targets_are_lossless_references_not_new_scene_quotas(targets):
    approved={'chapter_number':8,'target_word_count':2000,'scenes':[{'scene_number':i+1,'target_word_count':value,'action_sequence':['Launch the tide marker.']} for i,value in enumerate(targets)]}
    original=json.dumps(approved,ensure_ascii=False)
    block=delivery_block(project_id=uuid4(),chapter_id=uuid4(),chapter_number=8,artifact_id=uuid4(),approved=approved,receipt={'chapter_numbers':[8]})
    compiled=compile_prompt([block],total_budget_tokens=8000,require_provenance=True)
    payload=json.loads(block.text.split('\n',2)[2])
    assert payload['chapter_target_word_count_reference']==2000
    for row,target in zip(payload['scenes'],targets,strict=True):
        if target is None:assert 'target_word_count' not in row
        else:assert row['target_word_count']==target
    assert '不是新增逐场硬配额' in compiled.user and '未达已明确下限前不得提前收束' in compiled.user
    assert '标点、空白与Markdown标记不计' in compiled.user and '必要现场行动、选择与后果' in compiled.user
    assert not compiled.report.truncated and json.dumps(approved,ensure_ascii=False)==original


@pytest.mark.parametrize('language',['zh','en'])
def test_chapter_level_time_goal_and_causal_contract_reach_actual_compiler(language):
    approved={'chapter_number':9,'target_word_count':2100,
        'opening_situation':'昨夜签署后已过一夜，此刻辰初观察气球。' if language=='zh' else 'One night after signing; observe the balloon at dawn.',
        'goal':'今日完成潮位比对，不重演昨日签署。' if language=='zh' else 'Compare tides today without replaying yesterday’s signature.',
        'causal_contract':{'protagonist_choice':'仅观察和纸上记录，不再次发射。' if language=='zh' else 'Observe and record; do not launch another probe.'},
        'unknown_authored_chapter_clause':'The brass tag remains sealed.',
        '_meta':{'producer_debug':'Not a story instruction'},
        'scenes':[{'scene_number':1,'action_sequence':['Read the tide marker.'],'target_word_count':2100}]}
    before=json.dumps(approved,ensure_ascii=False)
    block=delivery_block(project_id=uuid4(),chapter_id=uuid4(),chapter_number=9,artifact_id=uuid4(),approved=approved,receipt={'chapter_numbers':[9]})
    payload=json.loads(block.text.split('\n',2)[2]);compiled=compile_prompt([block],total_budget_tokens=8000,require_provenance=True)
    assert payload['schema']=='approved-current-chapter-delivery.v2'
    assert payload['chapter_contract']=={k:v for k,v in approved.items() if k not in {'scenes','_meta'}}
    assert approved['opening_situation'] in compiled.user and approved['goal'] in compiled.user
    assert approved['causal_contract']['protagonist_choice'] in compiled.user
    assert approved['unknown_authored_chapter_clause'] in compiled.user
    assert '属于前章时点' in compiled.user and 'producer_debug' not in compiled.user
    assert '/opening_situation' in block.provenance.field_paths and '/causal_contract' in block.provenance.field_paths
    assert not compiled.report.truncated and json.dumps(approved,ensure_ascii=False)==before
    with pytest.raises(PromptBudgetError):compile_prompt([block],total_budget_tokens=40,require_provenance=True)
