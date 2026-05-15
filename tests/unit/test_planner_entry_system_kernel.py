from uuid import uuid4

from bestseller.domain.enums import ArtifactType
from bestseller.infra.db.models import ProjectModel
from bestseller.services import planner as planner_services


def _project() -> ProjectModel:
    project = ProjectModel(
        slug="entry-story",
        title="玄门账本",
        genre="玄幻",
        sub_genre="修仙",
        target_word_count=300000,
        target_chapters=80,
        audience="web-serial",
        metadata_json={},
    )
    project.id = uuid4()
    return project


def test_entry_system_kernel_artifact_type() -> None:
    assert ArtifactType.ENTRY_SYSTEM_KERNEL.value == "entry_system_kernel"


def test_fallback_entry_system_kernel_persists_to_metadata() -> None:
    project = _project()
    story_design = {"reader_promise": "每章都要有资源账和代价。"}

    payload = planner_services._persist_entry_system_kernel_metadata(
        project,
        story_design_kernel=story_design,
    )

    assert project.metadata_json["entry_system_kernel"] == payload
    assert payload["taxonomy"]
    assert "artifact" in {item["type"] for item in payload["taxonomy"]}


def test_entry_system_prompt_block_renders_metadata_kernel() -> None:
    project = _project()
    planner_services._persist_entry_system_kernel_metadata(project)

    block = planner_services._entry_system_kernel_prompt_block(project)

    assert "【词条体系约束】" in block
    assert "硬规则" in block


def test_entry_system_prompt_block_handles_invalid_metadata() -> None:
    project = _project()
    project.metadata_json = {"entry_system_kernel": {"taxonomy": []}}

    assert planner_services._entry_system_kernel_prompt_block(project) == ""


def test_entry_registry_persists_after_kernel() -> None:
    project = _project()
    kernel = planner_services._persist_entry_system_kernel_metadata(project)

    registry = planner_services._persist_entry_registry_metadata(
        project,
        entry_system_kernel=kernel,
    )

    assert project.metadata_json["entry_registry"] == registry
    assert registry["entries"]
    taxonomy = {item["type"] for item in kernel["taxonomy"]}
    assert all(entry["taxonomy_ref"] in taxonomy for entry in registry["entries"])
    assert "【词条注册表】" in planner_services._entry_registry_prompt_block(project)


def test_entry_system_blocks_enter_volume_prompt() -> None:
    project = _project()
    planner_services._persist_entry_system_kernel_metadata(project)
    planner_services._persist_entry_registry_metadata(project)
    premise = "凡人少年靠账本修复宗门断裂灵脉。"
    book_spec = planner_services._fallback_book_spec(project, premise)
    world_spec = planner_services._fallback_world_spec(project, premise, book_spec)
    cast_spec = planner_services._fallback_cast_spec(project, premise, book_spec, world_spec)

    _, prompt = planner_services._volume_plan_prompts(
        project,
        book_spec,
        world_spec,
        cast_spec,
    )

    assert "【词条体系约束】" in prompt
    assert "【词条注册表】" in prompt
