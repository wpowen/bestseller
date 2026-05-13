from __future__ import annotations

import pytest

from bestseller.settings import PipelineSettings

pytestmark = pytest.mark.unit


def test_story_design_capability_flags_default_to_warn_only_rollout() -> None:
    settings = PipelineSettings()

    assert settings.enable_story_design_kernel is True
    assert settings.story_design_kernel_candidate_count == 3
    assert settings.enable_story_state_driven_planning is True
    assert settings.enable_reverse_outline_gate is True
    assert settings.reverse_outline_gate_block_on_failure is False
    assert settings.story_design_require_kernel_for_new_projects is False
