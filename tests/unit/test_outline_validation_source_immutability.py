"""Reading approved JSON cannot change its receipt hash, even on invalid input."""
from copy import deepcopy
import pytest
from pydantic import ValidationError
from bestseller.domain.workflow import SceneOutlineInput, ChapterOutlineInput, ChapterOutlineBatchInput
from bestseller.services.prompt_source_policy import content_hash

pytestmark = pytest.mark.unit


def raw_scene():
    return {'scene_number': 1.2, 'scene_title': 'A clock without a key',
        'story_task': 'Mara checks the old sealed clock.', 'emotion_task': 'Concern',
        'participants': 'Mara, Steward', 'entry_state': 'The clock is still stopped.',
        'exit_state': 'Both names are on the dated receipt.',
        'purpose': {'unknown_reference': {'keep': ['exact', 0, False]}}}


@pytest.mark.parametrize('kind', ['scene', 'chapter', 'batch'])
def test_successful_alias_and_nested_shape_normalization_preserves_source(kind):
    scene = raw_scene()
    if kind == 'scene':
        model, raw = SceneOutlineInput, scene
    elif kind == 'chapter':
        model, raw = ChapterOutlineInput, {'chapter_number': 1, 'chapter_title': 'Two receipts',
            'world_rule_refs': 'No remote voices.', 'scenes': [scene]}
    else:
        model, raw = ChapterOutlineBatchInput, {'chapters': [
            {'chapter_number': 1, 'goal': 'Compare the original dates.', 'scenes': [scene]}],
            '_meta': {'sourcebound_readiness_receipt': {'hash': 'immutable fixture'}}}
    before = deepcopy(raw); before_hash = content_hash(raw)
    normalized = model.model_validate(raw)
    assert raw == before and content_hash(raw) == before_hash
    normalized_scene = normalized if kind == 'scene' else normalized.scenes[0] if kind == 'chapter' else normalized.chapters[0].scenes[0]
    assert normalized_scene.scene_number == 2
    assert normalized_scene.participants == ['Mara', 'Steward']
    assert normalized_scene.purpose['story'] == 'Mara checks the old sealed clock.'
    assert normalized_scene.entry_state == {'summary': 'The clock is still stopped.'}


@pytest.mark.parametrize('model,raw', [
    (SceneOutlineInput, {'scene_number': -1, 'story_task': 'Check the seal.', 'participants': 'Mara, Steward'}),
    (ChapterOutlineInput, {'chapter_number': 0, 'scenes': [{'story_task': 'Check the seal.'}]}),
])
def test_failed_validation_also_leaves_original_source_untouched(model,raw):
    before=deepcopy(raw)
    with pytest.raises(ValidationError):model.model_validate(raw)
    assert raw==before
