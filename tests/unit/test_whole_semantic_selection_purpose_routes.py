"""Exact structured-selection producer routes classify purpose, never fees/grades."""
from copy import deepcopy

import pytest

from test_commercial_request_purpose import case as case
from test_commercial_request_purpose import paid, add_run, add_transport
from test_whole_semantic_purpose_route import route_sources
from bestseller.services.commercial_request_purpose import prove_known_noncommercial_request

pytestmark = pytest.mark.unit


@pytest.fixture(params=[
    'fanqie_short_semantic_evidence_selection',
    'fanqie_short_title_semantic_judge',
])
def selection_route(case, request):
    add_run(case, origin=True)
    add_transport(case)
    for source in route_sources(case).values():
        source.update(logical_role='critic', prompt_template=request.param,
                      prompt_version='selection.1')
    return case


def test_structured_routes_prove_purpose_without_mutation_or_fee_acceptance(selection_route):
    before = deepcopy(selection_route)
    assert prove_known_noncommercial_request(**selection_route) is True
    assert selection_route == before
    assert 'grade' not in paid(selection_route)


@pytest.mark.parametrize('field,value', [
    ('logical_role', 'planner'), ('logical_role', 'editor'),
    ('prompt_version', None), ('prompt_version', 'selection.10'),
    ('prompt_version', ' selection.1'),
    ('prompt_template', 'fanqie_short_semantic_evidence_selection_extra'),
    ('prompt_template', 'fanqie_short_title_semantic_judge_extra'),
])
def test_unregistered_tuple_cannot_borrow_a_known_route(selection_route, field, value):
    for source in route_sources(selection_route).values():
        source[field] = value
    assert prove_known_noncommercial_request(**selection_route) is False


@pytest.mark.parametrize('source', ['paid_origin', 'run', 'run_origin', 'transport_origin'])
def test_mixed_route_versions_remain_blocked(selection_route, source):
    route_sources(selection_route)[source]['prompt_version'] = 'selection.2'
    assert prove_known_noncommercial_request(**selection_route) is False


@pytest.mark.parametrize('fault', ['unknown', 'reserved', 'extra_attempt', 'commercial_marker'])
def test_new_routes_do_not_relax_original_financial_guards(selection_route, fault):
    if fault in {'unknown', 'reserved'}:
        selection_route['actual_attempts'][0]['status'] = fault
    elif fault == 'extra_attempt':
        selection_route['actual_attempts'].append({
            **selection_route['actual_attempts'][0], 'id': 'another-original-send'})
    else:
        paid(selection_route)['metadata']['commercial_grade'] = 'not-a-selection-verdict'
    before = deepcopy(selection_route)
    assert prove_known_noncommercial_request(**selection_route) is False
    assert selection_route == before
