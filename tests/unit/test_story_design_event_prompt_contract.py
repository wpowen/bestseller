import pytest

from bestseller.services import planner
from bestseller.services.story_design_kernel import EventPatternContract
from test_planner_services import build_project


@pytest.mark.parametrize('language', ['zh-CN', 'en'])
def test_event_pattern_required_fields_explicit_without_fallback(language):
    project = build_project()
    project.language = language
    _, prompt = planner._story_design_kernel_prompts(
        project, 'A navigator must recover a falsified route.', {}, {}, {}, {}
    )
    for name, field in EventPatternContract.model_fields.items():
        if field.is_required():
            assert name in prompt
    assert ('non-empty' in prompt) if language == 'en' else ('非空' in prompt)
