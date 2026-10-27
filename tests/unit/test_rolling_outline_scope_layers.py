"""Scope clarification retains complete unrelated source data, never grades it."""
from copy import deepcopy
import json

import pytest

from bestseller.services.source_bound_rolling_outline_prompt import (
    build_source_bound_rolling_outline_prompt,
)

pytestmark = pytest.mark.unit


def render(entry):
    cast = {'protagonist': {'name': 'Mara', 'gender': 'female'}}
    book = {'unknown_authored': {'no_clock_repair': True}}
    world = {'rules': [{'description': 'The lighthouse cannot move.'}]}
    authority = {'approved_creation_contracts': {'keep': 'Full literal author clause'}}
    plan = [deepcopy(entry)]
    system, user = build_source_bound_rolling_outline_prompt(
        project={'id': 'scope-fixture', 'language': 'en'}, book_spec=book,
        world_spec=world, cast_spec=cast, volume_plan=plan,
        volume_entry=entry, authority=authority,
    )
    source = json.loads(user.split('\n[SOURCE AUTHORITY]\n')[1].split('\n[/SOURCE AUTHORITY]')[0])
    assert source['story_assets'] == dict(book_spec=book, world_spec=world,
        cast_spec=cast, volume_plan=plan, volume_entry=entry)
    assert source['source_authority'] == authority
    return system, user, source


def test_batch_window_volume_are_distinct_and_all_source_values_remain():
    entry = dict(volume_number=2, chapter_range=[11, 13], outline_batch_range=[11, 13],
        chapter_count_target=3, start_chapter_number=11, end_chapter_number=20,
        narrative_volume_chapter_range=[11, 40],
        information_flow_scene_cards=[{'chapter_number': 39, 'unknown': 'future plan'}])
    original = deepcopy(entry)
    system, user, source = render(entry)
    assert entry == original
    scope = source['planning_scope']
    assert scope['output_batch']['chapter_range'] == [11, 13]
    assert scope['rolling_window']['chapter_range'] == [11, 20]
    assert scope['narrative_volume']['chapter_range'] == [11, 40]
    assert scope['output_batch']['source_paths'] == ['/story_assets/volume_entry/outline_batch_range']
    assert 'chapters 11-13 only' in user
    assert 'do not expand the output range or prove past occurrence' in system
    assert 'Applicable author requirements and prerequisites still bind' in system


def test_minimal_entry_does_not_invent_enclosing_scopes():
    _, _, source = render(dict(volume_number=1, chapter_range=[1, 2], chapter_count_target=2))
    assert set(source['planning_scope']) == {'output_batch'}


def test_legacy_single_endpoint_is_preserved_without_inventing_range():
    _, _, source = render(dict(volume_number=1, chapter_range=[3, 5],
        chapter_count_target=3, start_chapter_number=1))
    assert 'rolling_window' not in source['planning_scope']
    assert source['planning_scope']['unresolved_rolling_window_fields']['values'] == {'start_chapter_number': 1}


def test_real_window_and_batch_producers_explain_different_endpoints():
    from bestseller.services.pipelines import _expand_volume_plan_into_rolling_windows
    from bestseller.services.planner import _volume_entry_for_outline_batch
    volume = dict(volume_number=1, chapter_count_target=20, chapter_range=[1, 20],
        information_flow_scene_cards=[{'chapter_number': 18, 'plan': 'future'}])
    windows = _expand_volume_plan_into_rolling_windows([volume], window_size=10)
    batch = _volume_entry_for_outline_batch(windows[0], chapter_start=1, chapter_end=3, count=3)
    _, _, source = render(batch)
    assert batch['chapter_range'] == [1, 3]
    assert batch['end_chapter_number'] == 10
    assert source['planning_scope']['narrative_volume']['chapter_range'] == [1, 20]
    assert source['story_assets']['volume_entry']['information_flow_scene_cards'] == volume['information_flow_scene_cards']


@pytest.mark.parametrize('fields,reason', [
    ({'start_chapter_number': 4, 'end_chapter_number': 8}, 'batch_outside_rolling_window'),
    ({'start_chapter_number': 1, 'end_chapter_number': 8,
      'narrative_volume_chapter_range': [1, 6]}, 'window_outside_narrative_volume'),
])
def test_inconsistent_enclosing_ranges_fail_before_request(fields, reason):
    with pytest.raises(ValueError, match=reason):
        render(dict(volume_number=1, chapter_range=[1, 3], chapter_count_target=3, **fields))
