"""Exercise the real scene prompt builder; no database or provider required."""
from copy import deepcopy
import pytest
from planning_capability_test_helpers import explicit_planning_metadata
from test_pipeline_services import build_project, build_chapter, build_scene
from test_emotion_driven_kernel import _valid_kernel_payload
from test_public_emotion_kernel import _valid_public_emotion_kernel
from bestseller.services import drafts
from bestseller.services.planning_capability_selection import PlanningCapabilitySelectionError


@pytest.mark.parametrize('capability', ['emotion_driven_kernel', 'public_emotion_kernel'])
@pytest.mark.parametrize('payload', [None, {}, 'invalid-method', {'schema_version': 'invalid'}])
def test_real_scene_prompt_refuses_selected_missing_or_invalid_method(capability, payload):
    project = build_project()
    project.metadata_json = {**explicit_planning_metadata(capability), capability: payload}
    chapter = build_chapter(project.id)
    scene = build_scene(project.id, chapter.id)
    before = deepcopy(project.metadata_json)
    with pytest.raises(PlanningCapabilitySelectionError):
        drafts.build_scene_draft_prompts(project, chapter, scene, None, context_budget_tokens=100000, total_input_budget_tokens=100000)
    assert project.metadata_json == before


@pytest.mark.parametrize('capability,make_payload', [
    ('emotion_driven_kernel', _valid_kernel_payload),
    ('public_emotion_kernel', _valid_public_emotion_kernel),
])
def test_real_scene_prompt_uses_selected_method_and_hides_unselected_cache(capability, make_payload):
    project = build_project()
    chapter = build_chapter(project.id)
    scene = build_scene(project.id, chapter.id)
    payload = make_payload()
    project.metadata_json = {**explicit_planning_metadata(capability), capability: payload}
    _, selected = drafts.build_scene_draft_prompts(project, chapter, scene, None, context_budget_tokens=100000, total_input_budget_tokens=100000)
    marker = 'emotion_driven_core' if capability == 'emotion_driven_kernel' else 'public_emotion_core'
    assert marker in selected
    project.metadata_json = {capability: payload}
    before = deepcopy(project.metadata_json)
    _, unselected = drafts.build_scene_draft_prompts(project, chapter, scene, None, context_budget_tokens=100000, total_input_budget_tokens=100000)
    assert marker not in unselected
    assert project.metadata_json == before


def test_real_scene_prompt_does_not_swallow_invalid_explicit_selection():
    project = build_project()
    project.metadata_json = explicit_planning_metadata('emotion_driven_kernel')
    project.metadata_json['creation_intent_contract']['hook_spec']['planning_capabilities']['selected']['emotion_driven_kernel'] = 'true'
    chapter = build_chapter(project.id)
    scene = build_scene(project.id, chapter.id)
    with pytest.raises(PlanningCapabilitySelectionError):
        drafts.build_scene_draft_prompts(project, chapter, scene, None, context_budget_tokens=100000, total_input_budget_tokens=100000)


@pytest.mark.asyncio
@pytest.mark.parametrize('name,producer,capability', [
    ('_ensure_emotion_kernel_backfill_for_pipeline', 'ensure_project_emotion_driven_kernel', 'emotion_driven_kernel'),
    ('_ensure_public_emotion_kernel_backfill_for_pipeline', 'ensure_project_public_emotion_kernels', 'public_emotion_kernel'),
    ('_ensure_entry_system_backfill_for_pipeline', 'ensure_project_entry_system_compat', 'entry_system_kernel'),
])
async def test_actual_pipeline_backfill_preserves_typed_failure(monkeypatch, name, producer, capability):
    from bestseller.services import pipelines
    from bestseller.settings import load_settings
    project = build_project()
    project.metadata_json = explicit_planning_metadata(capability)
    before = deepcopy(project.metadata_json)
    async def unavailable(*args, **kwargs):
        raise PlanningCapabilitySelectionError('selected_asset_invalid')
    monkeypatch.setattr(pipelines, producer, unavailable)
    with pytest.raises(PlanningCapabilitySelectionError, match='selected_asset_invalid'):
        await getattr(pipelines, name)(None, load_settings(), project, requested_by='test')
    assert project.metadata_json == before
