from types import SimpleNamespace
from uuid import uuid4

import pytest
from bestseller.services import planner
from bestseller.services.source_bound_planning_content import (
    SourceBoundPlanningCapacityError,
    assert_source_bound_planning_prompt_capacity,
    assert_source_bound_planning_request_capacity,
)


def test_full_tool_schema_can_fail_while_original_prompt_fits():
    assert_source_bound_planning_prompt_capacity('source protocol', 'entire source')
    tools = [{'type': 'function', 'function': {'parameters': {'description': '境' * 90000}}}]
    with pytest.raises(SourceBoundPlanningCapacityError) as caught:
        assert_source_bound_planning_request_capacity('source protocol', 'entire source', tools=tools, output_tokens=8192)
    assert caught.value.receipt['tool_tokens'] > 40000
    assert caught.value.receipt['budget_passed'] is False


@pytest.mark.parametrize('output_tokens', [0, -1, True, '8192'])
def test_output_reserve_must_be_exact_positive_integer(output_tokens):
    with pytest.raises(ValueError, match='output_reserve_invalid'):
        assert_source_bound_planning_request_capacity('s', 'u', tools=[], output_tokens=output_tokens)


def test_whole_output_reserve_stays_in_admission():
    with pytest.raises(SourceBoundPlanningCapacityError):
        assert_source_bound_planning_request_capacity('s', 'u', tools=None, output_tokens=65536)


@pytest.mark.asyncio
async def test_real_structured_entry_checks_canonical_then_rejects_before_gateway(monkeypatch):
    async def noop(*args, **kwargs):
        return None
    async def forbidden(*args, **kwargs):
        raise AssertionError('Request must stop before provider dispatch')
    monkeypatch.setattr(planner, '_heartbeat_planner_workflow', noop)
    monkeypatch.setattr(planner, 'complete_text', forbidden)
    settings = SimpleNamespace(pipeline=SimpleNamespace(planning_artifact_reuse_enabled=False))
    from test_source_bound_planning_content import _project, _compile
    from bestseller.services.source_bound_planning_content import build_source_bound_planning_prompts
    project = _project()
    project.slug = 'unrelated-sea-chronicle'
    project.id = uuid4()
    book, world, cast, volumes = _compile(project)
    system, user = build_source_bound_planning_prompts(
        kind='volume_plan', project=project, premise='观测站完整来源', projection=volumes,
        book_spec=book, world_spec=world, cast_spec=cast,
    )
    with pytest.raises(SourceBoundPlanningCapacityError):
        await planner._generate_structured_artifact(
            None, settings, project=project, logical_name='volume_plan',
            system_prompt=system, user_prompt=user,
            fallback_payload=[], workflow_run_id=uuid4(), prompt_version='v4-volume-native',
            attach_default_methodology=False, output_tool={'type': 'function', 'function': {'name': 'submit_volume_plan', 'parameters': {'description': '境' * 90000}}},
            before_dispatch=noop,
        )


def test_volume_current_contract_exposes_exact_projected_delivery_shape():
    import copy
    import json
    from test_source_bound_planning_content import _project, _compile
    from bestseller.services import source_bound_planning_content as content
    project = _project()
    book, world, cast, projected = _compile(project)
    before = copy.deepcopy(projected)
    _, user = content.build_source_bound_planning_prompts(
        kind='volume_plan', project=project, premise='观测站的本轮交付',
        projection=projected, book_spec=book, world_spec=world, cast_spec=cast,
    )
    shape = json.loads(user)['current_output_contract']['delivery_shape']
    assert shape['volume_count'] == len(projected)
    assert shape['volumes'] == [{k: row[k] for k in ('volume_number', 'chapter_count_target')} for row in projected]
    assert projected == before
    assert 'story obligation' in shape['scope']


@pytest.mark.parametrize('kind', ['cast_spec', 'volume_plan', 'book_spec'])
def test_truncation_retry_never_introduces_fixed_roster_or_reduces_delivery(kind):
    original = '完整源要求：本轮交付保持原人物和卷数。'
    retry = planner._planner_length_retry_prompt(original, kind)
    assert retry.startswith(original)
    assert '3-4 supporting_cast' not in retry
    assert '2 antagonist_forces' not in retry
    assert 'fewer' not in retry
    assert 'root type' in retry and 'Keep every required' in retry
