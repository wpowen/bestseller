from copy import deepcopy
from types import SimpleNamespace

import pytest
from bestseller.services import planner
from bestseller.services.source_bound_planning_content import _dependency_projection


def sample():
    candidate = {'protagonist': {'name': 'Mara', 'unknown_authored': {'_meta': {'truth': 'literal'}}},
        '_meta': {'source_snapshot_hash': 'snapshot', 'unknown_author_clause': {'keep': True},
                  'source_bound_expansion': False, 'source_bound_expansion_draft_eligible': False}}
    approved = deepcopy(candidate)
    approved['_meta'].update(source_bound_expansion=True, source_bound_expansion_draft_eligible=True,
        source_bound_expansion_review={'status': 'passed', 'decision': 'pass'}, workflow_run_id='approved-origin')
    return candidate, SimpleNamespace(content=approved)


def test_downstream_uses_exact_approved_metadata_and_preserves_all_story_fields():
    candidate, artifact = sample()
    before = deepcopy((candidate, artifact.content))
    chosen = planner._approved_cast_payload_for_downstream(candidate, artifact)
    assert chosen == artifact.content and chosen is not artifact.content
    assert (candidate, artifact.content) == before
    assert chosen['protagonist']['unknown_authored'] == candidate['protagonist']['unknown_authored']
    assert planner._approved_payload_hash(_dependency_projection(chosen)) == planner._approved_payload_hash(_dependency_projection(artifact.content))
    assert planner._approved_payload_hash(_dependency_projection(chosen)) != planner._approved_payload_hash(_dependency_projection(candidate))


@pytest.mark.parametrize('mutation', ['story', 'nested_metadata', 'unknown_root_metadata', 'snapshot'])
def test_approval_bookkeeping_cannot_hide_story_or_unknown_metadata_changes(mutation):
    candidate, artifact = sample()
    if mutation == 'story':
        artifact.content['protagonist']['name'] = 'Neri'
    elif mutation == 'nested_metadata':
        artifact.content['protagonist']['unknown_authored']['_meta']['truth'] = 'changed'
    elif mutation == 'unknown_root_metadata':
        artifact.content['_meta']['unknown_author_clause']['keep'] = False
    else:
        artifact.content['_meta']['source_snapshot_hash'] = 'another'
    with pytest.raises(ValueError, match='approved_cast_downstream_source_changed'):
        planner._approved_cast_payload_for_downstream(candidate, artifact)
