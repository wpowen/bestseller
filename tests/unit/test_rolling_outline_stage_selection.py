"""No story-dependent filters: preserve global facts and mixed future prose."""
from copy import deepcopy
import json
from uuid import uuid4
import hashlib
import pytest

from bestseller.services.rolling_outline_stage_selection import (
    select_rolling_stage_source, verify_stage_selection,
)
from bestseller.services import planner
from bestseller.services.prompt_compiler import PromptBudgetError
from bestseller.infra.db.models import ProjectModel
from bestseller.settings import load_settings

pytestmark = pytest.mark.unit


def trusted_fixture_options(value):
    # Explicit source-role fixture supplied by a trusted caller, not inferred
    # from the values' field names. Production uses verified_selection_inputs.
    from bestseller.services.retained_planning_clauses import resolve_pointer
    paths = {'/story_assets/world_spec/_meta/workflow_run_id': 'producer_provenance',
        '/source_authority/genre_taxonomy_reference': 'diagnostic_reference',
        **{'/story_assets/cast_spec/protagonist/psych_profile/'+key:'characterization_index'
           for key in ('mbti','enneagram','big_five')}}
    return {'author_binding': {'canonical_id': 'isolated-fixture', 'canonical_sha256': 'a'*64},
        'non_author_proofs': {path:{'source_role':role,'value_sha256':hashlib.sha256(
            json.dumps(resolve_pointer(value,path),ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()}
            for path,role in paths.items()}}


def source():
    return {'story_assets': {'book_spec': {'unknown_author_clause': 'Keep the observatory clock stopped.'},
        'world_spec': {'rules': ['No remote voices.'], '_meta': {'workflow_run_id': str(uuid4()),
            'planned_frontiers': {'future': {'historical_fact': False, 'unknown_stays_unknown': True}}}},
        'cast_spec': {'protagonist': {'name': 'Mara', 'secret': 'Has never entered the tower.',
            'psych_profile': {'mbti': 'INTJ', 'enneagram': '5w4',
                'big_five': {'openness': 81},
                'attachment_style': 'Avoidant; trapped below the tower for twenty years.'},
            'life_history': {'prior': 'Signed only one log.'},
            'voice_profile': {'taboo': 'Never claims to have visited the roof.'}}},
        'volume_entry': {'volume_number': 1, 'chapter_range': [1, 3], 'chapter_count_target': 3,
            'information_flow_scene_cards': [{'chapter': 15,
                'actual_action': 'First speaks after chapter fourteen. No pipe, spell or new authority.',
                'actual_information': 'A short reply.', 'trigger_account': 'No new witness.',
                'present': 'The door remains closed; no unknown listener can hear.', 'location': 'Under the tower'}]},
        'volume_plan': []},
        'source_authority': {'approved_creation_contracts': {'keep': 'Never repair the clock.'},
            'genre_taxonomy_reference': {'default_tags': ['mystery']}},
        'extra_constraints': ['An untrusted critic quotation is still retained verbatim.']}


def test_mixed_future_actions_and_current_knowledge_limits_are_never_dropped():
    original = source();before = deepcopy(original)
    options=trusted_fixture_options(original)
    selected, receipt = select_rolling_stage_source(original,**options)
    assert original == before
    assert selected['story_assets']['volume_entry'] == original['story_assets']['volume_entry']
    assert selected['story_assets']['world_spec']['rules'] == original['story_assets']['world_spec']['rules']
    assert selected['story_assets']['world_spec']['_meta']['planned_frontiers'] == original['story_assets']['world_spec']['_meta']['planned_frontiers']
    assert selected['story_assets']['book_spec'] == original['story_assets']['book_spec']
    assert selected['source_authority']['approved_creation_contracts'] == original['source_authority']['approved_creation_contracts']
    assert selected['extra_constraints'] == original['extra_constraints']
    person = selected['story_assets']['cast_spec']['protagonist']
    assert person['psych_profile'] == {'attachment_style': 'Avoidant; trapped below the tower for twenty years.'}
    for field in ('secret', 'life_history', 'voice_profile'):
        assert person[field] == original['story_assets']['cast_spec']['protagonist'][field]
    assert any(row['path'].endswith('/actual_action') and row['action']=='retained_required'
        for row in receipt['classification_blockers'])
    assert verify_stage_selection(original, selected, receipt,**options)


def test_unknown_provenance_with_pointer_escaping_cannot_authorize_selection():
    original = source()
    original['story_assets']['world_spec']['_meta']['author/rule~extra'] = 'No bells.'
    selected, receipt = select_rolling_stage_source(original,**trusted_fixture_options(original))
    assert selected['story_assets']['world_spec']['_meta']['author/rule~extra'] == 'No bells.'
    assert receipt['selection_dispatchable'] is False
    assert '/story_assets/world_spec/_meta/author~1rule~0extra' in receipt['unresolved_provenance_paths']


def test_field_names_and_valid_indexes_alone_are_not_non_author_proof():
    original=source();selected,receipt=select_rolling_stage_source(original)
    assert selected==original and not receipt['selection_dispatchable']


def test_author_clause_ancestor_cannot_be_removed_even_with_producer_witness():
    original=source();options=trusted_fixture_options(original)
    options['protected_paths']=['/story_assets/cast_spec/protagonist/psych_profile']
    selected,receipt=select_rolling_stage_source(original,**options)
    assert selected['story_assets']['cast_spec']['protagonist']['psych_profile']==original['story_assets']['cast_spec']['protagonist']['psych_profile']
    assert not receipt['selection_dispatchable']


@pytest.mark.parametrize('markers',[None,{'creative_direction':None},{'creative_direction':'unknown'}])
def test_missing_or_invalid_field_source_markers_block_selection(markers):
    original=source();original['source_authority']['book_design_snapshot']={
        'creation_intent':{'creative_direction':'Never ring the bell.','field_sources':markers}}
    selected,receipt=select_rolling_stage_source(original,**trusted_fixture_options(original))
    assert selected['source_authority']['book_design_snapshot']==original['source_authority']['book_design_snapshot']
    assert not receipt['selection_dispatchable']


@pytest.mark.parametrize('change', ['selected', 'receipt', 'source'])
def test_exact_path_receipt_rejects_tampering(change):
    original=source();options=trusted_fixture_options(original)
    selected,receipt=select_rolling_stage_source(original,**options)
    if change=='selected':selected['story_assets']['world_spec']['rules']=[]
    elif change=='receipt':receipt['decisions'][0]['reason']='changed'
    else:original['extra_constraints'].append('New author clause')
    with pytest.raises(ValueError,match='rolling_stage_selection_changed'):
        verify_stage_selection(original,selected,receipt,**options)


def test_real_compiler_can_defer_large_known_producer_journal_with_same_budget():
    p=ProjectModel(id=uuid4(),slug='stage-observatory',title='Observatory',genre='fantasy',
        language='en',target_chapters=20,target_word_count=40000,metadata_json={
            'creation_protagonist_name':'Mara','creation_protagonist_source':'user'})
    value=source()
    value['story_assets']['world_spec']['_meta']['repair_attempts']=['unique-record-'+str(i)+'x'*120 for i in range(250)]
    value['story_assets']['cast_spec']['protagonist'].pop('psych_profile')
    value['source_authority'].pop('genre_taxonomy_reference')
    from bestseller.services.conception_snapshots import snapshot_hash
    canonical={'retained_planning_clauses':[]}
    p._source_bound_planning_canonical={'id':'fixture','content_hash':snapshot_hash(canonical),'content':canonical}
    p.metadata_json={**p.metadata_json,'creation_canonical_snapshot_id':'fixture',
        'creation_canonical_snapshot_hash':snapshot_hash(canonical)}
    world=value['story_assets']['world_spec'];body={k:v for k,v in world.items() if k!='_meta'}
    digest=lambda x:hashlib.sha256(json.dumps(x,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    value['source_authority']['approved_world_authority']={'source':'current_planning_artifact',
        'authority_status':'current','full_story_source':body,'source_proof':{
            'source_payload_sha256':digest(world),'story_payload_sha256':digest(body),
            'producer_meta_role':'provenance_not_story_or_author_instructions'}}
    user='\n[SOURCE AUTHORITY]\n'+json.dumps(value,ensure_ascii=False)+'\n[/SOURCE AUTHORITY]\n\n[ROLLING OUTLINE TASK]\nGenerate a ChapterOutlineBatch for chapters 1-3 only.'
    settings=load_settings(env={});settings.llm.planner_prompt_budget_tokens=2200
    settings.llm.planner_source_complete_input_budget_tokens=32768
    system,result=planner._compile_volume_outline_prompt(p,settings,system_prompt='Return JSON.',user_prompt=user)
    report=p.metadata_json['planner_prompt_compiler_latest']
    assert report['total_tokens']<=report['usable_budget_tokens']==1980
    assert 'stage_source_selection' in report and 'source_complete_capacity' not in report
    receipt=report['stage_source_selection']['selection']
    assert receipt['original_document']==value and receipt['global_creation_contracts_exact']
    assert settings.llm.planner_prompt_budget_tokens==2200


def test_unknown_provenance_blocks_instead_of_expanding_budget_or_overwriting_old_report():
    p=ProjectModel(id=uuid4(),slug='stage-tide',title='Tide',genre='fantasy',language='en',
        target_chapters=20,target_word_count=40000,metadata_json={
            'creation_protagonist_name':'Mara','creation_protagonist_source':'user',
            'planner_prompt_compiler_latest':{'old':'preserved'}})
    value=source();value['story_assets']['world_spec']['_meta']['unknown_author_scope']='No pipe. '*2000
    user='\n[SOURCE AUTHORITY]\n'+json.dumps(value)+'\n[/SOURCE AUTHORITY]\n\n[ROLLING OUTLINE TASK]\nGenerate a ChapterOutlineBatch.'
    settings=load_settings(env={});settings.llm.planner_prompt_budget_tokens=1000
    with pytest.raises(PromptBudgetError) as caught:
        planner._compile_volume_outline_prompt(p,settings,system_prompt='Return JSON.',user_prompt=user)
    assert caught.value.stage_source_selection['reason']=='unclassified_source_provenance'
    assert p.metadata_json['planner_prompt_compiler_latest']=={'old':'preserved'}
    assert settings.llm.planner_prompt_budget_tokens==1000
