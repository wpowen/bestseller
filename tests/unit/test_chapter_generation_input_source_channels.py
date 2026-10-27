"""Stored runtime receipts never become new writing instructions or authority."""
import copy
import json
from types import SimpleNamespace

import pytest

from bestseller.services import chapter_generation_input_builder as builder

pytestmark = pytest.mark.unit


def inputs():
    project = SimpleNamespace(slug='fixture', title='Independent story', genre='custom',
        language='en', metadata_json={'creation_intent_contract': {
            'field_sources': {'hook_spec': 'explicit'},
            'hook_spec': {'rule': 'Keep the chosen ending'}}})
    chapter = SimpleNamespace(chapter_number=1, chapter_goal='A quiet departure',
        metadata_json={'write_safety_hint': 'OBSOLETE_25_CHAR', 'write_safety_block_code': 'OLD_BLOCK',
            'post_assembly_duplicate_gate': {'status': 'blocked', 'findings': ['OLD_25_CHAR']},
            'causal_contract': {'protagonist_choice': 'AI_CHOICE'},
            'methodology_contract': {'emotion_phase': 'AI_EMOTION'},
            'object_signal_contract': {'source': 'user', 'rule': 'EXPLICIT_SIGNAL'},
            'authored_fact': 'KEEP_FACT'})
    scene = SimpleNamespace(scene_number=1, scene_type='opening', participants=['Ari'],
        purpose={'story': 'Leave'}, entry_state={'room': 'inside'}, exit_state={'room': 'outside'},
        metadata_json={'methodology_contract': {'signature_image': 'A closed door',
            'write_safety_hint': 'NESTED_OLD_HINT'}})
    context = SimpleNamespace(story_bible={'rules': ['Approved boundary']},
        chapter_contract={'causal_contract': {'choice': 'PLANNED_CHOICE'}},
        reader_contract_block='DEFAULT_OPENING_RECIPE', canon_guardrails_block='CANON_GUARD',
        previous_scene_summaries=[{'summary': 'An accepted departure'}],
        recent_timeline_events=[{'event': 'An accepted meeting'}],
        planned_payoffs=[{'label': 'A future letter'}])
    return project, chapter, scene, context


def build(values):
    project, chapter, scene, context = values
    return builder.build_chapter_generation_input_bundle(project=project, chapter=chapter,
        scenes=[scene], context_packet=context, target_word_count=2200)


def test_runtime_removed_authority_separated_history_unchanged():
    values = inputs()
    before = copy.deepcopy(values[1].metadata_json)
    bundle = build(values)
    encoded = json.dumps(bundle.model_dump(mode='json'), ensure_ascii=False)
    for stale in ('OBSOLETE_25_CHAR', 'OLD_25_CHAR', 'OLD_BLOCK', 'NESTED_OLD_HINT'):
        assert stale not in encoded
    assert values[1].metadata_json == before
    assert bundle.chapter['metadata']['authored_fact'] == 'KEEP_FACT'
    assert 'causal_contract' not in bundle.chapter['metadata']
    assert 'methodology_contract' not in bundle.chapter['metadata']
    channels = bundle.acceptance_contract['source_authority_channels']
    assert channels['ai_proposals']['chapter_metadata']['causal_contract']['protagonist_choice'] == 'AI_CHOICE'
    assert channels['ai_proposals']['chapter_metadata']['methodology_contract']['emotion_phase'] == 'AI_EMOTION'
    explicit = channels['explicit_creator_requirements']['creation_intent_contract']
    assert explicit['hook_spec']['rule'] == 'Keep the chosen ending'
    assert channels['established_facts']['recent_timeline_events'] == []
    assert channels['prior_prose_reference']['recent_timeline_events'] == values[3].recent_timeline_events
    assert channels['prior_prose_reference']['previous_scene_summaries'] == values[3].previous_scene_summaries
    assert channels['long_term_planning']['planned_payoffs'] == values[3].planned_payoffs
    assert bundle.chapter_contract['authority'] == 'planning_intent_not_hard'
    assert bundle.acceptance_contract['must_deliver'] == []
    assert bundle.acceptance_contract['planning_intent'][0]['value'] == 'A quiet departure'
    assert bundle.acceptance_contract['object_signal_contract']['rule'] == 'EXPLICIT_SIGNAL'


@pytest.mark.parametrize('number,kind', [(1, 'opening'), (3, 'reveal'), (8, 'pressure'), (20, 'hook')])
def test_absent_gate_is_unknown_at_every_position(number, kind, monkeypatch):
    values = inputs()
    values[2].scene_number, values[2].scene_type = number, kind
    monkeypatch.setattr(builder, '_default_scene_gate_function', lambda **kw: pytest.fail('No inferred duty'))
    assert build(values).scenes[0]['gate_function'] is None
    values[2].metadata_json['methodology_contract']['gate_function'] = 'SOURCE_SCENE_DUTY'
    assert build(values).scenes[0]['gate_function'] is None
    values[0].metadata_json['creation_intent_contract']['hook_spec'].update(
        require_methodology_overlay=True, methodology_contract_mode='strict')
    assert build(values).scenes[0]['gate_function'] == 'SOURCE_SCENE_DUTY'


def test_default_craft_not_hard_but_real_operational_thresholds_remain():
    bundle = build(inputs())
    assert bundle.methodology_blocks == {'canon_guardrails_block': 'CANON_GUARD'}
    channels = bundle.acceptance_contract['source_authority_channels']
    assert channels['ai_proposals']['methodology_blocks']['reader_contract_block'] == 'DEFAULT_OPENING_RECIPE'
    for key in ('front_position_rules', 'ending_frame_contract', 'knowledge_boundary_contract', 'methodology_application_contract'):
        assert bundle.acceptance_contract[key] == {}
    assert bundle.acceptance_contract['rule_gate_thresholds']['overall'] == .75
    expected_overall, expected_dimensions = builder.chapter_commercial_thresholds(1)
    assert bundle.acceptance_contract['llm_gate_thresholds'] == {'overall': expected_overall, 'dimensions': expected_dimensions}
    assert bundle.ready
