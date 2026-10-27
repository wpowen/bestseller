from copy import deepcopy
import pytest
from bestseller.services.story_architecture_layout import restore_architecture_field_placement, FIELDS
from bestseller.services.concept_tournament import _story_architecture_audit
from test_story_architecture_contract import _architecture
pytestmark=pytest.mark.unit


def misplaced():
    value=_architecture('异世界经营')
    for key in FIELDS:
        if key in value:value['grammar'][key]=value.pop(key)
    return value


def test_lossless_placement_restores_existing_complete_story_then_runs_original_gate():
    source=misplaced();before=deepcopy(source)
    normalized,receipt=restore_architecture_field_placement(source)
    assert source==before and receipt['facts_generated'] is False
    assert len(receipt['moved'])==7
    for key in FIELDS:assert normalized[key]==before['grammar'][key]
    report=_story_architecture_audit({'story_architecture':source},genre='玄幻',sub_genre='异世大陆',chapter_count=60)
    assert report['passed'] and report['placement_normalization']==receipt
    again,receipt2=restore_architecture_field_placement(normalized)
    assert again==normalized and receipt2['moved']==[]


def test_conflicting_duplicate_fields_never_choose_a_story_version():
    source=misplaced();source['story_engine']={'endgame_direction':'different facts'}
    before=deepcopy(source)
    with pytest.raises(ValueError,match='AMBIGUOUS_ARCHITECTURE_FIELD_PLACEMENT'):
        restore_architecture_field_placement(source)
    report=_story_architecture_audit({'story_architecture':source},genre='玄幻',sub_genre='异世大陆',chapter_count=60)
    assert not report['passed'] and source==before


def test_missing_cost_mode_and_invalid_values_still_fail_original_quality_gate():
    source=misplaced()
    for unit in source['grammar']['causal_units']:unit.pop('cost_mode')
    report=_story_architecture_audit({'story_architecture':source},genre='玄幻',sub_genre='异世大陆',chapter_count=60)
    assert not report['passed'] and 'INCOMPLETE_STORY_ARCHITECTURE' in report['blocking_codes']
    source=misplaced();source['grammar']['causal_units']='not units'
    assert not _story_architecture_audit({'story_architecture':source},genre='玄幻',sub_genre='异世大陆',chapter_count=60)['passed']


def test_duplicate_equal_is_allowed_but_number_type_changes_are_not():
    source=misplaced();source['progression']=deepcopy(source['grammar']['progression'])
    normalized,receipt=restore_architecture_field_placement(source)
    assert receipt['duplicate_equal']==['progression'] and normalized['progression']==source['progression']
    with pytest.raises(ValueError,match='AMBIGUOUS'):
        restore_architecture_field_placement({'grammar':{'progression':[1.0]},'progression':[1]})
