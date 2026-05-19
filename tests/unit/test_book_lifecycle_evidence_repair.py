from __future__ import annotations

import pytest

from bestseller.infra.db.models import CharacterModel, ChapterModel, ProjectModel
from bestseller.services.book_lifecycle_evidence import (
    build_book_lifecycle_evidence_from_project_state,
)
from bestseller.services.book_lifecycle_evidence_repair import (
    build_lifecycle_book_spec,
    build_lifecycle_cast_spec,
    build_lifecycle_story_design_kernel,
    build_lifecycle_volume_plan,
    build_lifecycle_world_spec,
    build_reverse_outline_payload,
    repair_character_identity_and_personhood,
)
from bestseller.services.planning_kernel import (
    build_project_planning_kernel,
    evaluate_prewrite_readiness,
)
from bestseller.services.reverse_outline_gate import evaluate_reverse_outline_gate
from bestseller.services.story_design_kernel import story_design_kernel_from_dict


pytestmark = pytest.mark.unit


def _project() -> ProjectModel:
    return ProjectModel(
        slug="legacy-test",
        title="青囊不语问阴阳",
        language="zh-CN",
        genre="惊悚灵异",
        sub_genre="驱魔探案综合",
        target_word_count=1_500_000,
        target_chapters=500,
        audience="男频",
        metadata_json={"canonical_category": "suspense-mystery"},
    )


def test_lifecycle_evidence_repair_builds_target_length_planning_contract() -> None:
    project = _project()
    metadata = dict(project.metadata_json)
    volume_plan = build_lifecycle_volume_plan(
        project,
        category_key="suspense-mystery",
        metadata=metadata,
    )
    book_spec = build_lifecycle_book_spec(
        project,
        category_key="suspense-mystery",
        metadata=metadata,
        protagonist_name="林渊",
    )
    world_spec = build_lifecycle_world_spec(
        project,
        category_key="suspense-mystery",
        metadata=metadata,
    )
    cast_spec = build_lifecycle_cast_spec(
        project,
        characters=[CharacterModel(name="林渊", role="protagonist")],
    )
    story_design = build_lifecycle_story_design_kernel(
        project,
        category_key="suspense-mystery",
        metadata=metadata,
        book_spec=book_spec,
        world_spec=world_spec,
        cast_spec=cast_spec,
        volume_plan=volume_plan,
    )

    assert len(volume_plan) >= 10
    assert volume_plan[-1]["chapter_range"] == "451-500"
    assert story_design_kernel_from_dict(dict(story_design)).reader_promise

    kernel = build_project_planning_kernel(
        project,
        project_metadata={
            **metadata,
            "benchmark_works": ["anonymous-category-benchmark:suspense-mystery"],
            "unique_hook": book_spec["unique_hook"],
        },
        book_spec=book_spec,
        world_spec=world_spec,
        cast_spec=cast_spec,
        volume_plan=volume_plan,
        story_design_kernel=story_design,
    )
    report = evaluate_prewrite_readiness(
        kernel,
        genre=project.genre,
        sub_genre=project.sub_genre,
        target_chapters=project.target_chapters,
    )

    assert report.passed is True


def test_lifecycle_evidence_repair_builds_reverse_outline_that_passes_gate() -> None:
    project = _project()
    metadata = dict(project.metadata_json)
    volume_plan = build_lifecycle_volume_plan(
        project,
        category_key="suspense-mystery",
        metadata=metadata,
    )
    book_spec = build_lifecycle_book_spec(
        project,
        category_key="suspense-mystery",
        metadata=metadata,
        protagonist_name="林渊",
    )
    world_spec = build_lifecycle_world_spec(
        project,
        category_key="suspense-mystery",
        metadata=metadata,
    )
    cast_spec = build_lifecycle_cast_spec(
        project,
        characters=[CharacterModel(name="林渊", role="protagonist")],
    )
    story_design = build_lifecycle_story_design_kernel(
        project,
        category_key="suspense-mystery",
        metadata=metadata,
        book_spec=book_spec,
        world_spec=world_spec,
        cast_spec=cast_spec,
        volume_plan=volume_plan,
    )
    chapters = [
        ChapterModel(
            chapter_number=1,
            title="纸符开眼",
            chapter_goal="林渊获得第一条可验证线索。",
            main_conflict="证据链被封锁，林渊必须付出代价才能获得推进资格。",
            hook_description="线索留下新的身份风险。",
            target_word_count=3000,
        )
    ]

    outline = build_reverse_outline_payload(
        chapters=chapters,
        story_design_kernel=story_design,
    )
    report = evaluate_reverse_outline_gate(story_design, outline)

    assert report.passed is True


def test_build_reverse_outline_payload_neutralizes_forbidden_default_motifs() -> None:
    story_design = {
        "change_vectors": ["关系变化", "代价变化", "证据推进"],
    }
    chapter = ChapterModel(
        chapter_number=1,
        title="遗嘱之夜",
        chapter_goal="父母失踪后主角为了查清身世，必须追击失踪旧案。",
        main_conflict="亲属创伤默认驱动触发角色崩溃，他需要证明身世旧案。",
        target_word_count=2500,
    )

    outline = build_reverse_outline_payload(chapters=[chapter], story_design_kernel=story_design)
    rebuilt = outline["chapters"][0]

    assert "家庭创伤或身世旧案默认驱动" not in rebuilt["goal"]
    assert "亲属创伤默认驱动" not in rebuilt["goal"]
    assert "身世旧案" not in rebuilt["main_conflict"]
    assert "失踪" not in rebuilt["goal"]
    assert "牵引" in rebuilt["goal"]


def test_lifecycle_evidence_repair_backfills_identity_and_personhood() -> None:
    project = _project()
    character = CharacterModel(name="何仙姑", role="supporting")
    mirror = CharacterModel(
        name="镜影林渊",
        role="supporting",
        metadata_json={"aliases": ["何仙姑", "镜影"]},
    )

    manifest, identity_updates, personhood_updates = repair_character_identity_and_personhood(
        project,
        [character, mirror],
        dry_run=False,
    )

    assert manifest[0]["name"] == "何仙姑"
    assert manifest[1]["aliases"] == ["镜影"]
    assert identity_updates == 2
    assert personhood_updates == 2
    assert character.metadata_json["pronoun_set_zh"] == "TA"
    assert character.goal
    assert character.fear

    project.metadata_json = {
        **dict(project.metadata_json),
        "identity_manifest_status": "locked",
        "identity_manifest": manifest,
        "character_drama_map": {"source": "test"},
        "cast_spec": {"protagonist": {"name": "何仙姑"}},
    }
    evidence = build_book_lifecycle_evidence_from_project_state(
        project,
        [character, mirror],
    )
    character_report = evidence["character_report"]
    assert character_report["character_gate_report"]["metrics"][
        "identity_manifest_duplicate_count"
    ] == 0
