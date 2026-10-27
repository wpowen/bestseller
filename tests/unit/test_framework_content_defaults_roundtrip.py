"""Offline integration of creation, stored profile reads and actual prompt blocks."""

from copy import deepcopy
from uuid import uuid4

import pytest

from bestseller.domain.project import ProjectCreate
from bestseller.infra.db.models import ProjectModel
from bestseller.services.diversity_budget import DiversityBudget
from bestseller.services.invariants import invariants_from_dict, invariants_to_dict, seed_invariants
from bestseller.services.prompt_constructor import build_chapter_hype_blocks
from bestseller.services.writing_presets import get_genre_preset, synthesize_genre_preset
from bestseller.services.writing_profile import (
    build_project_metadata,
    get_project_writing_profile,
    render_serial_fiction_guardrails,
    render_writing_profile_prompt_block,
    resolve_project_create_writing_profile,
)


@pytest.mark.parametrize("genre", ["自定义实验叙事", "现实百态", "种田仙侠"])
@pytest.mark.parametrize("language", ["zh-CN", "en-US"])
def test_creation_storage_read_and_prompt_blocks_leave_unselected_content_empty(genre, language):
    payload = ProjectCreate(
        slug="roundtrip-test",
        title="独立创作",
        genre=genre,
        language=language,
        target_word_count=78000,
        target_chapters=30,
    )
    initial = resolve_project_create_writing_profile(payload)
    metadata = build_project_metadata(payload, initial)
    before = deepcopy(metadata)
    project = ProjectModel(
        id=uuid4(),
        slug=payload.slug,
        title=payload.title,
        genre=genre,
        sub_genre=None,
        audience=None,
        language=language,
        metadata_json=metadata,
    )
    reread = get_project_writing_profile(project)
    for mode in ("chapter", "scene", "full"):
        text = render_writing_profile_prompt_block(reread, mode=mode, language=language)
        assert "即时危险" not in text
        assert "immediate danger" not in text
        assert "弱感情线" not in text
        assert "bookshop" not in text
    assert reread.serialization.opening_mandate in ("未指定", "unspecified")
    assert metadata == before  # Read does not pretend migration was persisted.
    neutral = synthesize_genre_preset("custom-unmapped-test", genre="自定义实验叙事")
    inv = seed_invariants(
        project_id=project.id,
        language=language,
        words_per_chapter=None,
        overrides={"preset_overrides": neutral.writing_profile_overrides},
    )
    blocks = build_chapter_hype_blocks(
        invariants_from_dict(invariants_to_dict(inv)),
        DiversityBudget(project_id=project.id),
        chapter_no=1,
        total_chapters=30,
    )
    assert blocks.hype_constraints_block == ""
    assert blocks.assigned_hype_recipe is None


def test_explicit_story_and_effect_adoption_still_reaches_output():
    payload = ProjectCreate(
        slug="chosen-test",
        title="作者选择",
        genre="仙侠",
        target_chapters=30,
        target_word_count=78000,
        writing_profile={
            "character": {"romance_mode": "弱感情线"},
            "serialization": {"opening_mandate": "主角主动承担危险。"},
        },
    )
    profile = resolve_project_create_writing_profile(payload)
    assert "主角主动承担危险。" in render_serial_fiction_guardrails(profile)
    preset = get_genre_preset("xianxia-upgrade")
    project_id = uuid4()
    inv = seed_invariants(
        project_id=project_id,
        language="zh-CN",
        words_per_chapter=None,
        overrides={"preset_overrides": preset.writing_profile_overrides},
    )
    blocks = build_chapter_hype_blocks(
        inv, DiversityBudget(project_id=project_id), chapter_no=1, total_chapters=30
    )
    assert blocks.hype_constraints_block
    assert blocks.assigned_hype_recipe is not None
