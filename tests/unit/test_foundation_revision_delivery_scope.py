from copy import deepcopy
from types import SimpleNamespace

import pytest

from bestseller.services.creation_configuration import configuration_metadata
from bestseller.services.foundation_revision_delta import _revision_delivery
from bestseller.services.foundation_dependency_scope import bind_prior_dependency_review, UNCHANGED_AXES, REFINEMENT_AXES
from bestseller.services.conception_snapshots import snapshot_hash

pytestmark = pytest.mark.unit


def fixture():
    submission = {'configuration': {'book': {'total_chapters': 17, 'chapter_target': 2100}},
        'execution_window': {'purpose': 'complete_story', 'start': 1, 'end': 17}}
    saved = configuration_metadata({}, submission)
    current = deepcopy(submission)
    current['execution_window'] = {'purpose': 'opening_window', 'start': 1, 'end': 1}
    project = SimpleNamespace(target_chapters=17, target_word_count=35700,
        metadata_json=configuration_metadata(saved, current))
    return project, {'metadata': saved}, current


def test_window_change_preserves_source_review_scope_and_historical_bytes():
    p, before, _ = fixture()
    frozen = deepcopy(before)
    delivery = _revision_delivery(p, before)
    assert delivery == {'purpose': 'complete_story', 'target_chapters': 17, 'target_word_count': 35700}
    assert before == frozen and p.metadata_json['execution_window']['end'] == 1
    current = {'candidate': {'canonical': {'unrelated_rule': '先登记航标耗电'},
        'assets': {k: {} for k in ('book_spec', 'world_spec', 'cast_spec')}},
        **{k: {} for k in ('protected_clauses', 'progression_events', 'new_character_cards', 'authorization', 'current_authority_snapshot')}, 'delivery': delivery}
    previous = deepcopy(current)
    verdict = {'input_sha256': snapshot_hash(previous), 'decision': 'revise', 'raw_verdict_unchanged': True,
        'checks': [{'axis': a, 'verdict': 'pass' if a in UNCHANGED_AXES else 'revise'} for a in UNCHANGED_AXES + REFINEMENT_AXES], 'feedback': '原失败仍保留'}
    result = bind_prior_dependency_review(current, previous, verdict, report_id='original', run_id='paid')
    assert result['prior_dependency_review']['original_decision'] == 'revise'
    current['candidate']['canonical']['unrelated_rule'] = '不用登记耗电'
    with pytest.raises(ValueError, match='source_changed'):
        bind_prior_dependency_review(current, previous, verdict, report_id='original', run_id='paid')


@pytest.mark.parametrize('fault', ['target', 'current_hash', 'old_hash', 'creative', 'window'])
def test_real_source_or_invalid_window_changes_still_rejected(fault):
    p, before, current = fixture()
    if fault == 'target': p.target_chapters = 18
    elif fault == 'current_hash': p.metadata_json['creation_configuration_hash'] = 'wrong'
    elif fault == 'old_hash': before['metadata']['creation_configuration_hash'] = 'wrong'
    elif fault == 'creative':
        current['configuration']['readers'] = {'primary': '另一读者群'}
        p.metadata_json = configuration_metadata(p.metadata_json, current)
    elif fault == 'window': p.metadata_json['execution_window']['end'] = 18
    with pytest.raises(ValueError): _revision_delivery(p, before)
