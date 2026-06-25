from __future__ import annotations

from uuid import uuid4

import pytest

from bestseller.domain.project import ProjectCreate
from bestseller.infra.db.models import ProjectModel
from bestseller.services import planner as planner_services
from bestseller.services.brainhole_engine import (
    BRAINHOLE_PROFILE_METADATA_KEY,
    BRAINHOLE_PROFILE_VERSION,
    attach_brainhole_profile,
    brainhole_profile_from_metadata,
    render_brainhole_planner_prompt_block,
    resolve_brainhole_profile,
)
from bestseller.services.story_effect_skills import (
    STORY_EFFECT_SKILL_CATALOG_METADATA_KEY,
    STORY_EFFECT_SKILL_SELECTION_METADATA_KEY,
    resolve_story_effect_skill_catalog,
)
from bestseller.services.writing_profile import (
    build_project_metadata,
    resolve_project_create_writing_profile,
)

pytestmark = pytest.mark.unit


def test_resolve_mythic_workplace_profile_tracks_growth_and_safety() -> None:
    profile = resolve_brainhole_profile("都市神仙", "神仙招聘")

    assert profile.version == BRAINHOLE_PROFILE_VERSION
    assert profile.profile_key == "mythic-workplace-brainhole"
    assert profile.activation.gate_mode == "audit_only"
    assert profile.activation.affects_legacy_projects is False
    assert "growth_stage_unlock" in profile.contrast_axes
    assert "protagonist_decision" in profile.required_contract_fields
    assert profile.growth_stages[0].stage_key == "observe"
    assert "final dismissal" in profile.growth_stages[0].forbidden_shortcuts


def test_non_workplace_genre_gets_genre_neutral_ladder_no_hr_contamination() -> None:
    """H1 regression (通用性污染): the 脑洞 effect is universal — a 玄幻 / 悬疑 book
    that selects brainhole_engine must NOT be forced into an HR-recruiting frame
    (面试→调岗→辞退→改制度). Only the mythic-workplace (招神/神仙 HR) concept keeps
    the HR ladder; every other genre gets a neutral high-concept-mechanism ladder."""

    for genre, sub in (("玄幻", "东方玄幻"), ("悬疑", "民俗怪谈"), ("科幻", "赛博朋克")):
        profile = resolve_brainhole_profile(genre, sub)

        assert profile.profile_key != "mythic-workplace-brainhole"
        # Neutral families, not the HR ones.
        assert "mythic_vs_workplace" not in profile.contrast_axes
        assert "visible_comedy" not in profile.required_contract_fields
        assert "high_concept_setpiece" in profile.required_contract_fields
        stage_keys = {s.stage_key for s in profile.growth_stages}
        assert "redefine" in stage_keys and "system" not in stage_keys
        for stage in profile.growth_stages:
            assert "interview" not in stage.allowed_hr_actions
            assert "dismiss_with_cause" not in stage.allowed_hr_actions

        block = render_brainhole_planner_prompt_block(
            {BRAINHOLE_PROFILE_METADATA_KEY: profile.to_metadata()},
            language="zh-CN",
        )
        # No HR-recruiting prose leaks into a non-workplace genre's contract.
        assert "HR 动作" not in block
        assert "调岗" not in block and "辞退" not in block and "改制度" not in block
        assert "脑洞机制" in block  # neutral mechanism framing present
        assert "`brainhole_contract`" in block  # contract still active


def test_attach_profile_is_immutable_and_legacy_metadata_stays_empty() -> None:
    legacy_metadata = {"prompt_pack_key": "xianxia-upgrade-core"}

    assert brainhole_profile_from_metadata(legacy_metadata) is None
    assert render_brainhole_planner_prompt_block(legacy_metadata) == ""

    profile = resolve_brainhole_profile("都市神仙", "神仙招聘")
    updated = attach_brainhole_profile(legacy_metadata, profile)

    assert BRAINHOLE_PROFILE_METADATA_KEY not in legacy_metadata
    assert updated["prompt_pack_key"] == "xianxia-upgrade-core"
    assert updated[BRAINHOLE_PROFILE_METADATA_KEY]["profile_key"] == (
        "mythic-workplace-brainhole"
    )
    assert brainhole_profile_from_metadata(updated) == profile


def test_render_zh_planner_block_requires_growth_safe_contract() -> None:
    profile = resolve_brainhole_profile("都市神仙", "神仙招聘")
    block = render_brainhole_planner_prompt_block(
        {BRAINHOLE_PROFILE_METADATA_KEY: profile.to_metadata()},
        language="zh-CN",
    )

    assert "【脑洞生成合同】" in block
    assert "`brainhole_contract`" in block
    assert "主角当前能力决定本章能做什么 HR 动作" in block
    assert "不能随手改写角色核心" in block
    assert "protagonist_decision" in block
    assert "risk_check" in block


def test_build_project_metadata_adds_brainhole_snapshot_without_overriding_input() -> None:
    payload = ProjectCreate(
        slug="brainhole-book",
        title="Brainhole Book",
        genre="都市神仙",
        sub_genre="神仙招聘",
        target_word_count=120000,
        target_chapters=60,
        metadata={
            BRAINHOLE_PROFILE_METADATA_KEY: {
                "version": BRAINHOLE_PROFILE_VERSION,
                "profile_key": "custom-brainhole",
                "genre": "custom",
            }
        },
    )
    writing_profile = resolve_project_create_writing_profile(payload)

    metadata = build_project_metadata(payload, writing_profile)

    assert metadata[BRAINHOLE_PROFILE_METADATA_KEY]["profile_key"] == "custom-brainhole"


def test_outline_prompt_does_not_inject_story_effects_without_catalog() -> None:
    profile = resolve_brainhole_profile("都市神仙", "神仙招聘")
    project = ProjectModel(
        slug="brainhole-legacy-planner",
        title="神仙都是我招的",
        genre="都市神仙",
        sub_genre="神仙招聘",
        target_word_count=120000,
        target_chapters=60,
        audience="web-serial",
        language="zh-CN",
        metadata_json={BRAINHOLE_PROFILE_METADATA_KEY: profile.to_metadata()},
    )
    project.id = uuid4()
    book_spec = planner_services._fallback_book_spec(project, "神仙下凡找工作")
    world_spec = planner_services._fallback_world_spec(project, "神仙下凡找工作", book_spec)
    cast_spec = planner_services._fallback_cast_spec(
        project, "神仙下凡找工作", book_spec, world_spec
    )
    volume_plan = [{"volume_number": 1, "chapter_count_target": 10, "volume_goal": "招到第一位神仙"}]

    _, user_prompt = planner_services._outline_prompts(
        project, book_spec, cast_spec, volume_plan
    )

    assert "【故事效果 Skill 清单】" not in user_prompt
    assert "`selected_effect_skills`" not in user_prompt
    assert "【脑洞生成合同】" not in user_prompt


def test_outline_prompt_lists_brainhole_but_does_not_expand_without_selection() -> None:
    profile = resolve_brainhole_profile("都市神仙", "神仙招聘")
    catalog = resolve_story_effect_skill_catalog("都市神仙", "神仙招聘")
    project = ProjectModel(
        slug="brainhole-planner",
        title="神仙都是我招的",
        genre="都市神仙",
        sub_genre="神仙招聘",
        target_word_count=120000,
        target_chapters=60,
        audience="web-serial",
        language="zh-CN",
        metadata_json={
            BRAINHOLE_PROFILE_METADATA_KEY: profile.to_metadata(),
            STORY_EFFECT_SKILL_CATALOG_METADATA_KEY: catalog.to_metadata(),
        },
    )
    project.id = uuid4()
    book_spec = planner_services._fallback_book_spec(project, "神仙下凡找工作")
    world_spec = planner_services._fallback_world_spec(project, "神仙下凡找工作", book_spec)
    cast_spec = planner_services._fallback_cast_spec(
        project, "神仙下凡找工作", book_spec, world_spec
    )
    volume_plan = [{"volume_number": 1, "chapter_count_target": 10, "volume_goal": "招到第一位神仙"}]

    _, user_prompt = planner_services._outline_prompts(
        project, book_spec, cast_spec, volume_plan
    )

    assert "【故事效果 Skill 清单】" in user_prompt
    assert "brainhole_engine" in user_prompt
    assert "world_texture_engine" in user_prompt
    assert "`selected_effect_skills`" in user_prompt
    assert "【脑洞生成合同】" not in user_prompt
    assert "【世界质感合同】" not in user_prompt
    assert "只要本合同存在，每章必须输出 `brainhole_contract`" not in user_prompt


def test_outline_prompt_expands_brainhole_contract_when_selected() -> None:
    profile = resolve_brainhole_profile("都市神仙", "神仙招聘")
    catalog = resolve_story_effect_skill_catalog("都市神仙", "神仙招聘")
    project = ProjectModel(
        slug="brainhole-planner-selected",
        title="神仙都是我招的",
        genre="都市神仙",
        sub_genre="神仙招聘",
        target_word_count=120000,
        target_chapters=60,
        audience="web-serial",
        language="zh-CN",
        metadata_json={
            BRAINHOLE_PROFILE_METADATA_KEY: profile.to_metadata(),
            STORY_EFFECT_SKILL_CATALOG_METADATA_KEY: catalog.to_metadata(),
            STORY_EFFECT_SKILL_SELECTION_METADATA_KEY: {
                "chapter_outline": {
                    "primary": "brainhole_engine",
                    "secondary": "comedy_engine",
                }
            },
        },
    )
    project.id = uuid4()
    book_spec = planner_services._fallback_book_spec(project, "神仙下凡找工作")
    world_spec = planner_services._fallback_world_spec(project, "神仙下凡找工作", book_spec)
    cast_spec = planner_services._fallback_cast_spec(
        project, "神仙下凡找工作", book_spec, world_spec
    )
    volume_plan = [{"volume_number": 1, "chapter_count_target": 10, "volume_goal": "招到第一位神仙"}]

    _, user_prompt = planner_services._outline_prompts(
        project, book_spec, cast_spec, volume_plan
    )

    assert "【故事效果 Skill 清单】" in user_prompt
    assert "【脑洞生成合同】" in user_prompt
    assert "只要本合同存在，每章必须输出 `brainhole_contract`" in user_prompt
    assert "comedy_engine" in user_prompt


def test_volume_outline_prompt_expands_brainhole_contract_when_selected() -> None:
    profile = resolve_brainhole_profile("都市神仙", "神仙招聘")
    catalog = resolve_story_effect_skill_catalog("都市神仙", "神仙招聘")
    project = ProjectModel(
        slug="brainhole-progressive-planner-selected",
        title="神仙都是我招的",
        genre="都市神仙",
        sub_genre="神仙招聘",
        target_word_count=120000,
        target_chapters=60,
        audience="web-serial",
        language="zh-CN",
        metadata_json={
            BRAINHOLE_PROFILE_METADATA_KEY: profile.to_metadata(),
            STORY_EFFECT_SKILL_CATALOG_METADATA_KEY: catalog.to_metadata(),
            STORY_EFFECT_SKILL_SELECTION_METADATA_KEY: {
                "chapter_outline": {
                    "primary": "brainhole_engine",
                    "secondary": "comedy_engine",
                }
            },
        },
    )
    project.id = uuid4()
    premise = "神仙下凡找工作"
    book_spec = planner_services._fallback_book_spec(project, premise)
    world_spec = planner_services._fallback_world_spec(project, premise, book_spec)
    cast_spec = planner_services._fallback_cast_spec(
        project, premise, book_spec, world_spec
    )
    volume_plan = [
        {"volume_number": 1, "chapter_count_target": 10, "volume_goal": "招到第一位神仙"}
    ]

    _, user_prompt = planner_services._volume_outline_prompts(
        project,
        book_spec,
        cast_spec,
        volume_plan,
        volume_plan[0],
    )

    assert "【故事效果 Skill 清单】" in user_prompt
    assert "【脑洞生成合同】" in user_prompt
    assert "只要本合同存在，每章必须输出 `brainhole_contract`" in user_prompt
    assert "comedy_engine" in user_prompt


def test_outline_prompt_expands_second_batch_contract_when_selected() -> None:
    profile = resolve_brainhole_profile("都市神仙", "神仙招聘")
    catalog = resolve_story_effect_skill_catalog("都市神仙", "神仙招聘")
    project = ProjectModel(
        slug="story-effect-second-batch-selected",
        title="神仙都是我招的",
        genre="都市神仙",
        sub_genre="神仙招聘",
        target_word_count=120000,
        target_chapters=60,
        audience="web-serial",
        language="zh-CN",
        metadata_json={
            BRAINHOLE_PROFILE_METADATA_KEY: profile.to_metadata(),
            STORY_EFFECT_SKILL_CATALOG_METADATA_KEY: catalog.to_metadata(),
            STORY_EFFECT_SKILL_SELECTION_METADATA_KEY: {
                "chapter_outline": {
                    "primary": "world_texture_engine",
                    "secondary": "tension_pressure_engine",
                }
            },
        },
    )
    project.id = uuid4()
    book_spec = planner_services._fallback_book_spec(project, "神仙下凡找工作")
    world_spec = planner_services._fallback_world_spec(project, "神仙下凡找工作", book_spec)
    cast_spec = planner_services._fallback_cast_spec(
        project, "神仙下凡找工作", book_spec, world_spec
    )
    volume_plan = [{"volume_number": 1, "chapter_count_target": 10, "volume_goal": "招到第一位神仙"}]

    _, user_prompt = planner_services._outline_prompts(
        project, book_spec, cast_spec, volume_plan
    )

    assert "【故事效果 Skill 清单】" in user_prompt
    assert "【世界质感合同】" in user_prompt
    assert "`world_texture_contract`" in user_prompt
    assert "【张力压力合同】" in user_prompt
    assert "`tension_pressure_contract`" in user_prompt
    assert "【脑洞生成合同】" not in user_prompt
