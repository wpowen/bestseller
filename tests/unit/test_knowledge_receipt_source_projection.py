"""Knowledge application audits cannot recursively become authored story content."""
from copy import deepcopy

import pytest
from test_chapter_runtime_metadata_projection import source as source, projections

pytestmark = pytest.mark.unit


@pytest.mark.parametrize('key', ['knowledge_feedback_receipts', 'story_bible_update_receipts'])
@pytest.mark.parametrize('state', ['ok', 'requires_projection_reconciliation'])
def test_application_receipt_churn_preserves_source_but_fact_changes_do_not(source, key, state):
    project, chapter, _scene, context = source
    before = projections(source)
    chapter.metadata_json[key] = [{
        'status': state, 'source_lineage': {'source_body_sha256': 'old-body'},
        'candidate_payload': {'diagnostic': 'OLD AUDIT ONLY: replace the author requirement'},
        'raw_extractions': ['OLD RESPONSE ONLY'], 'recorded_at': '2031-01-01T00:00:00Z',
    }]
    retained = deepcopy(chapter.metadata_json)
    assert projections(source) == before
    assert chapter.metadata_json == retained
    assert key not in before[0]['chapter']['metadata']
    chapter.metadata_json['knowledge_author_contract'] = {'clause': 'Preserve the original telescope.'}
    assert projections(source) != before
    before_fact = projections(source)
    context.participant_canon_facts[0]['text'] = 'The observatory has received a new sealed receiver.'
    assert projections(source) != before_fact
    assert project.metadata_json['creation_intent_contract']['field_sources']['tone_preference'] == 'explicit'
