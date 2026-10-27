"""Real SQLite semantic fences use effective runtime settings across entrypoints."""

from __future__ import annotations

from copy import deepcopy
import json

import pytest
from test_chapter_critic_semantic_dependencies import db as db
from test_chapter_critic_semantic_dependencies import semantic_db as semantic_db

from bestseller import settings as settings_module
from bestseller.services import llm, pipelines
from bestseller.settings import apply_runtime_llm_profile, load_settings

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@pytest.fixture
def runtime_configuration(tmp_path, monkeypatch):
    profile = tmp_path / "runtime-profile.json"
    profile.write_text(json.dumps({"active_profile": "minimax"}))
    monkeypatch.setenv(settings_module.LLM_RUNTIME_PROFILE_ENV, str(profile))

    async def forbidden(*args, **kwargs):
        pytest.fail("semantic dependency checks must not call models")

    monkeypatch.setattr(llm, "complete_text", forbidden)
    settings = load_settings(env={})
    settings.llm.critic.model = "fixture/raw-role"
    settings.llm.critic.api_base = "https://raw.fixture.invalid/v1"
    return settings, profile


async def test_raw_and_effective_entrypoints_have_identical_real_semantic_fence(
    semantic_db, runtime_configuration
):
    session, _project, chapter, *_ = semantic_db
    raw, _profile = runtime_configuration
    before = deepcopy(raw.model_dump(mode="json"))
    effective = apply_runtime_llm_profile(raw)
    effective_before = deepcopy(effective.model_dump(mode="json"))
    assert raw.llm.model_dump() != effective.llm.model_dump()
    assert raw.llm.critic.model == "fixture/raw-role"
    expected = await pipelines._chapter_critic_semantic_dependencies(session, effective, chapter)
    assert await pipelines._chapter_critic_semantic_dependencies(session, raw, chapter) == expected
    assert (
        await pipelines._chapter_critic_semantic_dependencies(session, effective, chapter)
        == expected
    )
    assert raw.model_dump(mode="json") == before
    assert effective.model_dump(mode="json") == effective_before


@pytest.mark.parametrize(
    "change",
    [
        "temperature",
        "runtime_profile",
        "profile_endpoint",
        "threshold",
        "generation",
        "pipeline",
    ],
)
async def test_meaningful_effective_configuration_change_still_invalidates_real_fence(
    semantic_db, runtime_configuration, monkeypatch, change
):
    session, _project, chapter, *_ = semantic_db
    settings, profile = runtime_configuration
    before = await pipelines._chapter_critic_semantic_dependencies(session, settings, chapter)
    if change == "temperature":
        settings.llm.critic.temperature += 0.1
    elif change == "runtime_profile":
        profile.write_text(json.dumps({"active_profile": "deepseek"}))
    elif change == "profile_endpoint":
        profiles = deepcopy(settings_module.LLM_RUNTIME_PROFILES)
        profiles["minimax"]["roles"]["critic"]["api_base"] = "https://changed.fixture.invalid/v1"
        monkeypatch.setattr(settings_module, "LLM_RUNTIME_PROFILES", profiles)
    elif change == "threshold":
        settings.quality.thresholds.scene_min_score += 0.01
    elif change == "generation":
        settings.generation.active_context_scenes += 1
    elif change == "pipeline":
        settings.pipeline.resume_enabled = not settings.pipeline.resume_enabled
    original = deepcopy(settings.model_dump(mode="json"))
    after = await pipelines._chapter_critic_semantic_dependencies(session, settings, chapter)
    assert after != before
    assert after == await pipelines._chapter_critic_semantic_dependencies(
        session, apply_runtime_llm_profile(settings), chapter
    )
    assert settings.model_dump(mode="json") == original
