from __future__ import annotations

import pytest

from bestseller.settings import PipelineSettings

pytestmark = pytest.mark.unit


def test_story_design_capability_flags_default_to_warn_only_rollout() -> None:
    settings = PipelineSettings()

    assert settings.enable_story_design_kernel is True
    assert settings.enable_story_engine_shadow is True
    assert settings.story_engine_mode == "shadow"
    assert settings.story_engine_canary_project_ids == []
    assert settings.story_engine_canary_genres == []
    assert settings.story_engine_require_reader_validation_for_cutover is True
    assert settings.story_engine_rolling_window_size == 10
    assert settings.story_design_kernel_candidate_count == 3
    assert settings.enable_story_state_driven_planning is True
    assert settings.enable_reverse_outline_gate is True
    assert settings.reverse_outline_gate_block_on_failure is False
    assert settings.enable_worldview_compliance_gate is True
    assert settings.worldview_compliance_gate_block_on_failure is False
    assert settings.enable_worldview_progression_gate is True
    assert settings.worldview_progression_gate_block_on_failure is False
    assert settings.story_design_require_kernel_for_new_projects is False
    assert settings.enable_distilled_design_reference is True
    assert settings.enable_prewrite_readiness_gate is True
    assert settings.prewrite_readiness_gate_mode == "warn"
    assert settings.prewrite_readiness_block_on_failure is False
