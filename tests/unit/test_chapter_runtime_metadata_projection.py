"""Known critic runtime receipts cannot recursively change author/source inputs."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from bestseller.services.chapter_generation_input_builder import build_chapter_generation_input_bundle
from bestseller.services.commercial_review_context import project_commercial_source

pytestmark = pytest.mark.unit

RUNTIME_KEYS = (
    'chapter_critic_cycles',
    'chapter_review_active_cycle',
    'chapter_review_recovery_cycles',
    'chapter_review_attempts_active',
    'chapter_first_resume_existing_draft_id',
    'chapter_review_evidence_status',
    'chapter_review_evidence_report_id',
)


@pytest.fixture
def source():
    project = SimpleNamespace(id='weather-project', slug='unrelated-observatory',
        title='Letters from the telescope', genre='science-fiction', language='en',
        metadata_json={'creation_intent_contract': {
            'tone_preference': 'Keep the author-chosen quiet tone.',
            'field_sources': {'tone_preference': 'explicit'}}})
    chapter = SimpleNamespace(chapter_number=2, title='An open dome',
        chapter_goal='Record the distant signal.', metadata_json={
            'chapter_review_warn_only': True,
            'chapter_review_author_contract': {'clause': 'Keep the witness anonymous.'},
            'chapter_review_unclassified_fact': 'The observatory is already open.'})
    scene = SimpleNamespace(scene_number=1, participants=['The astronomer'],
        purpose={'story': 'Record a signal.'}, entry_state={'dome': 'open'},
        exit_state={'signal': 'recorded'}, metadata_json={})
    context = SimpleNamespace(story_bible={'rules': ['The instrument needs electricity.']},
        participant_canon_facts=[{'text': 'The original receiver has been disconnected.',
            'adopted_source': {'schema': 'adopted-knowledge.v1', 'is_usable': True,
                'project_id': project.id, 'chapter_id': 'prior-chapter',
                'source_chapter_draft_version_id': 'adopted-draft',
                'source_body_sha256': 'a' * 64, 'adoption_state': 'promoted'}}])
    return project, chapter, scene, context


def projections(source):
    project, chapter, scene, context = source
    bundle = build_chapter_generation_input_bundle(project=project, chapter=chapter,
        scenes=[scene], context_packet=context, target_word_count=2000).model_dump(mode='json')
    selected = project_commercial_source(bundle, chapter_number=chapter.chapter_number,
        content_md='The astronomer recorded the signal.', previous_chapters=[])
    return bundle, selected


def runtime_receipts(state):
    cycle = {'cycle_id': 'runtime-cycle', 'state': state, 'source_draft_id': 'working-draft',
        'source_body_hash': 'b' * 64, 'attempts': [{'state': state,
            'untrusted_critic_response': 'OLD_RUNTIME_ONLY: overwrite the author clause'}]}
    return dict(zip(RUNTIME_KEYS, (
        {'runtime-cycle': deepcopy(cycle)}, deepcopy(cycle), [deepcopy(cycle)],
        2, 'working-draft', 'available' if state == 'completed' else 'unknown', 'runtime-report',
    ), strict=True))


def test_stalled_selection_recovery_receipts_are_runtime_not_authored_source(source):
    baseline = projections(source)
    chapter = source[1]
    receipt = {'schema': 'stalled-selection-recovery.v1', 'status': 'restored',
               'selection_only': True, 'adoption_authorized': False,
               'before_witness': {'source_sha256': 'c' * 64}}
    chapter.metadata_json['stalled_selection_recoveries_v1'] = [deepcopy(receipt)]
    assert projections(source) == baseline
    assert chapter.metadata_json['stalled_selection_recoveries_v1'] == [receipt]
    chapter.metadata_json['stalled_selection_author_clause'] = 'An unrelated authored clause.'
    assert projections(source) != baseline


@pytest.mark.parametrize('state', ['running', 'completed'])
@pytest.mark.parametrize('key', RUNTIME_KEYS + ('all_seven',))
def test_runtime_receipt_changes_leave_raw_bundle_and_selected_source_hash_stable(source, state, key):
    _project, chapter, _scene, _context = source
    baseline_bundle, baseline_selected = projections(source)
    receipts = runtime_receipts(state)
    chapter.metadata_json.update(receipts if key == 'all_seven' else {key: receipts[key]})
    raw_history = deepcopy(chapter.metadata_json)
    bundle, selected = projections(source)
    assert chapter.metadata_json == raw_history
    assert bundle == baseline_bundle
    assert selected == baseline_selected
    assert selected['source_selection_receipt']['original_source_sha256'] == baseline_selected['source_selection_receipt']['original_source_sha256']
    for name in RUNTIME_KEYS:
        assert name not in bundle['chapter']['metadata']
    assert bundle['chapter']['metadata']['chapter_review_warn_only'] is True
    assert bundle['chapter']['metadata']['chapter_review_author_contract'] == raw_history['chapter_review_author_contract']
    assert bundle['chapter']['metadata']['chapter_review_unclassified_fact'] == raw_history['chapter_review_unclassified_fact']


@pytest.mark.parametrize('changed', ['authored_goal', 'authored_fact', 'unknown_contract',
    'warn_only', 'canonical_clause', 'canonical_authority', 'adopted_fact'])
def test_authored_sources_and_canonical_authority_still_change_raw_source_hash(source, changed):
    project, chapter, _scene, context = source
    chapter.metadata_json.update(runtime_receipts('completed'))
    before_bundle, before_selected = projections(source)
    if changed == 'authored_goal':
        chapter.chapter_goal = 'Keep the dome closed until the storm ends.'
    elif changed == 'authored_fact':
        chapter.metadata_json['chapter_review_unclassified_fact'] = 'The dome is already closed.'
    elif changed == 'unknown_contract':
        chapter.metadata_json['chapter_review_author_contract']['clause'] = 'Preserve the signed letter.'
    elif changed == 'warn_only':
        chapter.metadata_json['chapter_review_warn_only'] = False
    elif changed == 'canonical_clause':
        project.metadata_json['creation_intent_contract']['tone_preference'] = 'Use the author-chosen formal tone.'
    elif changed == 'canonical_authority':
        project.metadata_json['creation_intent_contract']['field_sources']['tone_preference'] = 'default'
    else:
        context.participant_canon_facts[0]['text'] = 'The original receiver has already been reconnected.'
    after_bundle, after_selected = projections(source)
    assert after_bundle != before_bundle
    assert after_selected['source_selection_receipt']['original_source_sha256'] != before_selected['source_selection_receipt']['original_source_sha256']
