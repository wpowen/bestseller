from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest

from bestseller.domain.enums import ArtifactType
from bestseller.infra.db.models import ProjectModel, WorkflowRunModel, WorkflowStepRunModel
from bestseller.services import planner as planner_services
from bestseller.settings import load_settings


pytestmark = pytest.mark.unit


class FakeSession:
    def __init__(self) -> None:
        self.added: list[object] = []

    def add(self, obj: object) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        for obj in self.added:
            table = getattr(obj, "__table__", None)
            if table is not None and "id" in table.c and getattr(obj, "id", None) is None:
                setattr(obj, "id", uuid4())


def build_settings():
    return load_settings(
        config_path=Path("config/default.yaml"),
        local_config_path=Path("config/does-not-exist.yaml"),
        env={},
    )


def build_project() -> ProjectModel:
    project = ProjectModel(
        slug="my-story",
        title="长夜巡航",
        genre="science-fantasy",
        target_word_count=80000,
        target_chapters=12,
        audience="web-serial",
        metadata_json={},
    )
    project.id = uuid4()
    return project


def test_extract_json_payload_handles_wrapped_json() -> None:
    payload = planner_services._extract_json_payload("```json\n{\"title\":\"长夜巡航\"}\n```")
    assert payload["title"] == "长夜巡航"


def test_fallback_generators_create_complete_chain() -> None:
    project = build_project()
    premise = "一名被放逐的导航员发现帝国正在篡改边境航线记录。"

    book_spec = planner_services._fallback_book_spec(project, premise)
    world_spec = planner_services._fallback_world_spec(project, premise, book_spec)
    cast_spec = planner_services._fallback_cast_spec(project, premise, book_spec, world_spec)
    volume_plan = planner_services._fallback_volume_plan(project, book_spec, cast_spec, world_spec)
    outline_batch = planner_services._fallback_chapter_outline_batch(
        project,
        book_spec,
        cast_spec,
        volume_plan,
    )

    assert book_spec["title"] == "长夜巡航"
    assert world_spec["rules"][0]["rule_id"] == "R001"
    assert cast_spec["protagonist"]["relationships"][0]["character"]
    assert len(volume_plan) >= 1
    assert len(outline_batch["chapters"]) == project.target_chapters
    assert len(outline_batch["chapters"][0]["scenes"]) == 3


def test_merge_planning_payload_preserves_fallback_nested_fields() -> None:
    project = build_project()
    premise = "一名被放逐的导航员发现帝国正在篡改边境航线记录。"
    fallback_book_spec = planner_services._fallback_book_spec(project, premise)

    merged = planner_services._merge_planning_payload(
        fallback_book_spec,
        {
            "title": "长夜巡航",
            "protagonist": {
                "name": "沈砚",
            },
        },
    )

    assert merged["title"] == "长夜巡航"
    assert merged["protagonist"]["name"] == "沈砚"
    assert merged["protagonist"]["external_goal"] == fallback_book_spec["protagonist"]["external_goal"]
    assert merged["stakes"]["personal"] == fallback_book_spec["stakes"]["personal"]


def test_fallback_cast_spec_tolerates_partial_or_malformed_inputs() -> None:
    project = build_project()
    premise = "一名被放逐的导航员发现帝国正在篡改边境航线记录。"

    cast_spec = planner_services._fallback_cast_spec(
        project,
        premise,
        {
            "protagonist": {
                "name": "沈砚",
            }
        },
        {
            "locations": {"broken": True},
            "factions": [],
            "power_system": {},
        },
    )

    assert cast_spec["protagonist"]["name"] == "沈砚"
    assert cast_spec["protagonist"]["goal"]
    assert cast_spec["protagonist"]["background"]
    assert cast_spec["antagonist"]["background"]


def test_fallback_chapter_outline_batch_tolerates_non_mapping_volume_items() -> None:
    project = build_project()

    outline_batch = planner_services._fallback_chapter_outline_batch(
        project,
        {},
        {
            "protagonist": {"name": "沈砚"},
            "antagonist": {"name": "祁镇"},
        },
        ["broken-volume-item"],  # type: ignore[list-item]
    )

    assert outline_batch["chapters"]
    assert outline_batch["chapters"][0]["volume_number"] == 1
    assert outline_batch["chapters"][0]["scenes"]


def test_fallback_chapter_outline_titles_do_not_cycle() -> None:
    """No chapter title may repeat across a 24-chapter book.

    Before the fix, ``_fallback_chapter_outline_batch`` indexed an 8-element
    hard-coded list by ``chapter_number % 8``, so chapters 2/10/18, 3/11/19,
    4/12/20 etc. got literally identical subtitles (封锁, 碰撞, 反咬, …).
    The fix replaces the cycle with a deterministic subtitle derived from
    the volume goal plus the chapter number, guaranteeing uniqueness.
    """
    project = build_project()
    project.target_chapters = 24
    premise = "一名被放逐的导航员发现帝国正在篡改边境航线记录。"
    book_spec = planner_services._fallback_book_spec(project, premise)
    world_spec = planner_services._fallback_world_spec(project, premise, book_spec)
    cast_spec = planner_services._fallback_cast_spec(project, premise, book_spec, world_spec)
    volume_plan = planner_services._fallback_volume_plan(project, book_spec, cast_spec, world_spec)

    outline_batch = planner_services._fallback_chapter_outline_batch(
        project,
        book_spec,
        cast_spec,
        volume_plan,
    )

    titles = [ch["title"] for ch in outline_batch["chapters"]]
    # Chapter 1 might be a genre-specific opener; chapters 2+ must be unique.
    non_empty = [t for t in titles[1:] if t]
    assert len(non_empty) == len(set(non_empty)), (
        f"Chapter titles must not repeat in a 24-chapter book; got {titles}"
    )


def test_fallback_chapter_outline_titles_are_concise_and_not_volume_goal_clips() -> None:
    project = build_project()
    project.target_chapters = 12
    premise = "一名被放逐的导航员发现帝国正在篡改边境航线记录。"
    book_spec = planner_services._fallback_book_spec(project, premise)
    world_spec = planner_services._fallback_world_spec(project, premise, book_spec)
    cast_spec = planner_services._fallback_cast_spec(project, premise, book_spec, world_spec)
    volume_plan = [
        {
            "volume_number": 1,
            "chapter_count_target": 12,
            "volume_goal": "沈渡需要在本卷内拿到一组足以改变局势的关键证据或盟友。",
        }
    ]

    outline_batch = planner_services._fallback_chapter_outline_batch(
        project,
        book_spec,
        cast_spec,
        volume_plan,
    )

    titles = [ch["title"] for ch in outline_batch["chapters"] if ch.get("title")]

    assert titles
    assert all("需要在本卷内" not in title for title in titles)
    assert all("·" not in title for title in titles)
    assert all(len(title) <= 8 for title in titles)


def test_fallback_chapter_outline_scenes_have_no_chapter_number_prefix() -> None:
    """Scene titles / time_labels must not embed the chapter number.

    Historically these looked like ``f"第{chapter_number}章中段"`` and that
    prefix leaked into the rewrite-template fallback prose as
    ``"第13章中段，程彻重新被推回…"``. Keeping them generic guarantees no
    renderer can reconstruct a chapter-numbered meta sentence.
    """
    project = build_project()
    project.target_chapters = 6
    premise = "一名被放逐的导航员发现帝国正在篡改边境航线记录。"
    book_spec = planner_services._fallback_book_spec(project, premise)
    world_spec = planner_services._fallback_world_spec(project, premise, book_spec)
    cast_spec = planner_services._fallback_cast_spec(project, premise, book_spec, world_spec)
    volume_plan = planner_services._fallback_volume_plan(project, book_spec, cast_spec, world_spec)

    outline_batch = planner_services._fallback_chapter_outline_batch(
        project,
        book_spec,
        cast_spec,
        volume_plan,
    )

    import re

    prefix_re = re.compile(r"第\s*\d+\s*章")
    for chapter in outline_batch["chapters"]:
        for scene in chapter["scenes"]:
            assert not prefix_re.search(scene.get("title", "")), (
                f"scene title leaked chapter number: {scene['title']}"
            )
            assert not prefix_re.search(scene.get("time_label", "")), (
                f"scene time_label leaked chapter number: {scene['time_label']}"
            )


def test_fallback_volume_plan_does_not_create_zero_chapter_volumes_for_short_projects() -> None:
    project = build_project()
    project.target_chapters = 1
    premise = "一名被放逐的导航员发现帝国正在篡改边境航线记录。"

    book_spec = planner_services._fallback_book_spec(project, premise)
    world_spec = planner_services._fallback_world_spec(project, premise, book_spec)
    cast_spec = planner_services._fallback_cast_spec(project, premise, book_spec, world_spec)
    volume_plan = planner_services._fallback_volume_plan(project, book_spec, cast_spec, world_spec)

    assert len(volume_plan) == 1
    assert volume_plan[0]["chapter_count_target"] == 1


def test_planner_prompts_switch_to_english_for_english_projects() -> None:
    project = ProjectModel(
        slug="storm-ledger",
        title="Storm Ledger",
        genre="Fantasy",
        sub_genre="Epic Fantasy",
        language="en-US",
        target_word_count=90000,
        target_chapters=24,
        audience="KU readers",
        metadata_json={
            "writing_profile": {
                "market": {
                    "platform_target": "Kindle Unlimited",
                    "content_mode": "English-language commercial fantasy serial",
                    "reader_promise": "Fast-moving fantasy with escalating political danger.",
                    "selling_points": ["storm magic", "buried dynasty", "betrayal"],
                    "trope_keywords": ["chosen family", "forbidden archive"],
                    "hook_keywords": ["sealed letter", "execution order"],
                    "opening_strategy": "Open with the order and the stolen key in the same scene.",
                    "chapter_hook_strategy": "End every chapter with a fresh threat or reveal.",
                    "payoff_rhythm": "Short payoff every chapter, major payoff every 5-7 chapters",
                },
                "style": {
                    "tone_keywords": ["taut", "ominous", "fast"],
                },
                "serialization": {
                    "opening_mandate": "Hook the reader in the first scene with concrete danger.",
                    "first_three_chapter_goal": "Lock in the central conflict, edge, and reversal.",
                    "scene_drive_rule": "Every scene must create a gain, a loss, or a sharper choice.",
                    "chapter_ending_rule": "Every chapter must end on a question, a threat, or a costly next move.",
                },
            }
        },
    )
    project.id = uuid4()

    system_prompt, user_prompt = planner_services._book_spec_prompts(  # noqa: SLF001
        project,
        "A royal archivist discovers the crown has been deleting its own bloodline.",
        {},
    )

    assert "English-language commercial fiction planner" in system_prompt
    assert "Project title: Storm Ledger" in user_prompt
    assert "Target chapters: 24" in user_prompt
    assert "Write all planning artifacts in English." in user_prompt
    assert "长篇中文小说" not in system_prompt + user_prompt


@pytest.mark.asyncio
async def test_generate_structured_artifact_merges_partial_llm_payload_with_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = build_project()
    fallback_book_spec = planner_services._fallback_book_spec(
        project,
        "一名被放逐的导航员发现帝国正在篡改边境航线记录。",
    )

    async def fake_complete_text(session: object, settings: object, request: object):
        return type(
            "CompletionStub",
            (),
            {
                "content": json.dumps(
                    {
                        "title": "Gemini Book",
                        "protagonist": {
                            "name": "沈砚",
                        },
                    },
                    ensure_ascii=False,
                ),
                "llm_run_id": uuid4(),
            },
        )()

    monkeypatch.setattr(planner_services, "complete_text", fake_complete_text)

    payload, llm_run_id = await planner_services._generate_structured_artifact(
        FakeSession(),
        build_settings(),
        project=project,
        logical_name="book_spec",
        system_prompt="system",
        user_prompt="user",
        fallback_payload=fallback_book_spec,
        workflow_run_id=uuid4(),
    )

    assert llm_run_id is not None
    assert payload["title"] == "Gemini Book"
    assert payload["protagonist"]["name"] == "沈砚"
    assert payload["protagonist"]["external_goal"] == fallback_book_spec["protagonist"]["external_goal"]


@pytest.mark.asyncio
async def test_generate_structured_artifact_uses_fallback_when_validator_rejects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = build_project()
    fallback_book_spec = planner_services._fallback_book_spec(
        project,
        "一名被放逐的导航员发现帝国正在篡改边境航线记录。",
    )

    async def fake_complete_text(session: object, settings: object, request: object):
        return type(
            "CompletionStub",
            (),
            {
                "content": json.dumps({"protagonist": []}, ensure_ascii=False),
                "llm_run_id": uuid4(),
            },
        )()

    def reject_non_mapping_protagonist(value: dict[str, object]) -> None:
        if not isinstance(value.get("protagonist"), dict):
            raise ValueError("invalid")

    monkeypatch.setattr(planner_services, "complete_text", fake_complete_text)

    payload, _ = await planner_services._generate_structured_artifact(
        FakeSession(),
        build_settings(),
        project=project,
        logical_name="book_spec",
        system_prompt="system",
        user_prompt="user",
        fallback_payload=fallback_book_spec,
        workflow_run_id=uuid4(),
        validator=reject_non_mapping_protagonist,
    )

    assert payload == fallback_book_spec


@pytest.mark.asyncio
async def test_generate_novel_plan_creates_all_artifacts_and_workflow_records(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = build_project()

    async def fake_get_project_by_slug(session: object, slug: str) -> ProjectModel:
        assert slug == "my-story"
        return project

    artifact_counter = 0

    async def fake_import_planning_artifact(session: object, project_slug: str, payload: object):
        nonlocal artifact_counter
        artifact_counter += 1
        return type(
            "ArtifactStub",
            (),
            {
                "id": uuid4(),
                "version_no": artifact_counter,
                "artifact_type": payload.artifact_type.value,
            },
        )()

    async def fake_generate_structured_artifact(
        session: object,
        settings: object,
        *,
        project: object,
        logical_name: str,
        system_prompt: str,
        user_prompt: str,
        fallback_payload: object,
        workflow_run_id,
        step_run_id=None,
        validator=None,
    ):
        return fallback_payload, uuid4()

    monkeypatch.setattr(planner_services, "get_project_by_slug", fake_get_project_by_slug)
    monkeypatch.setattr(planner_services, "import_planning_artifact", fake_import_planning_artifact)
    monkeypatch.setattr(
        planner_services,
        "_generate_structured_artifact",
        fake_generate_structured_artifact,
    )

    session = FakeSession()
    result = await planner_services.generate_novel_plan(
        session,
        build_settings(),
        "my-story",
        "一名被放逐的导航员发现帝国正在篡改边境航线记录。",
        requested_by="tester",
    )

    workflow_runs = [item for item in session.added if isinstance(item, WorkflowRunModel)]
    workflow_steps = [item for item in session.added if isinstance(item, WorkflowStepRunModel)]

    assert result.chapter_count == project.target_chapters
    assert result.volume_count >= 1
    assert [item.artifact_type for item in result.artifacts] == [
        ArtifactType.PREMISE,
        ArtifactType.BOOK_SPEC,
        ArtifactType.WORLD_SPEC,
        ArtifactType.CAST_SPEC,
        ArtifactType.VOLUME_PLAN,
        ArtifactType.PLAN_VALIDATION,
        ArtifactType.CHAPTER_OUTLINE_BATCH,
    ]
    assert len(result.llm_run_ids) == 5
    assert len(workflow_runs) == 1
    assert workflow_runs[0].status == "completed"
    assert len(workflow_steps) == 6


def test_fallback_volume_plan_has_different_obstacles_per_volume() -> None:
    """Each volume must have a unique obstacle, not the same antagonist template."""
    project = build_project()
    project.target_chapters = 24
    premise = "一名被放逐的导航员发现帝国正在篡改边境航线记录。"
    book_spec = planner_services._fallback_book_spec(project, premise)
    world_spec = planner_services._fallback_world_spec(project, premise, book_spec)
    cast_spec = planner_services._fallback_cast_spec(project, premise, book_spec, world_spec)
    volume_plan = planner_services._fallback_volume_plan(project, book_spec, cast_spec, world_spec)

    obstacles = [v["volume_obstacle"] for v in volume_plan]
    # With multiple volumes, obstacles should be different
    assert len(volume_plan) >= 2
    assert len(set(obstacles)) == len(obstacles), (
        f"Volume obstacles must be unique; got {obstacles}"
    )


def test_fallback_volume_plan_carries_conflict_phase() -> None:
    """Each volume entry must include a conflict_phase and primary_force_name."""
    project = build_project()
    project.target_chapters = 24
    premise = "一名被放逐的导航员发现帝国正在篡改边境航线记录。"
    book_spec = planner_services._fallback_book_spec(project, premise)
    world_spec = planner_services._fallback_world_spec(project, premise, book_spec)
    cast_spec = planner_services._fallback_cast_spec(project, premise, book_spec, world_spec)
    volume_plan = planner_services._fallback_volume_plan(project, book_spec, cast_spec, world_spec)

    for vol in volume_plan:
        assert "conflict_phase" in vol
        assert "primary_force_name" in vol
        assert vol["conflict_phase"] in (
            "survival", "political_intrigue", "betrayal",
            "faction_war", "existential_threat", "internal_reckoning",
        )


def test_fallback_chapter_outline_main_conflict_varies_across_volumes() -> None:
    """main_conflict in chapters of different volumes should differ."""
    project = build_project()
    project.target_chapters = 24
    premise = "一名被放逐的导航员发现帝国正在篡改边境航线记录。"
    book_spec = planner_services._fallback_book_spec(project, premise)
    world_spec = planner_services._fallback_world_spec(project, premise, book_spec)
    cast_spec = planner_services._fallback_cast_spec(project, premise, book_spec, world_spec)
    volume_plan = planner_services._fallback_volume_plan(project, book_spec, cast_spec, world_spec)
    outline = planner_services._fallback_chapter_outline_batch(project, book_spec, cast_spec, volume_plan)

    chapters = outline["chapters"]
    # Group main_conflict by volume
    conflicts_by_volume: dict[int, set[str]] = {}
    for ch in chapters:
        vol = ch["volume_number"]
        conflicts_by_volume.setdefault(vol, set()).add(ch["main_conflict"])

    # Different volumes should produce different conflict texts
    all_vol_conflicts = [next(iter(s)) for s in conflicts_by_volume.values()]
    unique_count = len(set(all_vol_conflicts))
    assert unique_count >= min(2, len(conflicts_by_volume)), (
        f"Expected different conflict texts across volumes; got {all_vol_conflicts}"
    )


def test_fallback_cast_spec_includes_antagonist_forces() -> None:
    """The cast spec should include antagonist_forces for multi-force conflict."""
    project = build_project()
    project.target_chapters = 24
    premise = "一名被放逐的导航员发现帝国正在篡改边境航线记录。"
    book_spec = planner_services._fallback_book_spec(project, premise)
    world_spec = planner_services._fallback_world_spec(project, premise, book_spec)
    cast_spec = planner_services._fallback_cast_spec(project, premise, book_spec, world_spec)

    assert "antagonist_forces" in cast_spec
    forces = cast_spec["antagonist_forces"]
    assert len(forces) >= 2
    # Each force has required fields
    for force in forces:
        assert "name" in force
        assert "force_type" in force
        assert "active_volumes" in force
        assert len(force["active_volumes"]) >= 1


def test_fallback_cast_spec_backward_compat_single_chapter() -> None:
    """A single-chapter project should still work with antagonist_forces."""
    project = build_project()
    project.target_chapters = 1
    premise = "一名被放逐的导航员发现帝国正在篡改边境航线记录。"
    book_spec = planner_services._fallback_book_spec(project, premise)
    world_spec = planner_services._fallback_world_spec(project, premise, book_spec)
    cast_spec = planner_services._fallback_cast_spec(project, premise, book_spec, world_spec)
    volume_plan = planner_services._fallback_volume_plan(project, book_spec, cast_spec, world_spec)
    outline = planner_services._fallback_chapter_outline_batch(project, book_spec, cast_spec, volume_plan)

    assert cast_spec["antagonist"] is not None
    assert len(cast_spec["antagonist_forces"]) >= 1
    assert len(volume_plan) == 1
    assert len(outline["chapters"]) == 1


def test_assign_conflict_phases_distributes_correctly() -> None:
    assert planner_services._assign_conflict_phases(1) == ["survival"]
    assert planner_services._assign_conflict_phases(2) == ["survival", "existential_threat"]
    assert planner_services._assign_conflict_phases(3) == ["survival", "political_intrigue", "existential_threat"]
    phases_5 = planner_services._assign_conflict_phases(5)
    assert len(phases_5) == 5
    assert phases_5[0] == "survival"
    assert phases_5[-1] == "existential_threat"


def test_assign_conflict_phases_7_volumes_cycles_middle() -> None:
    """For 7+ volumes, middle phases should cycle instead of repeating last."""
    phases_7 = planner_services._assign_conflict_phases(7)
    assert len(phases_7) == 7
    assert phases_7[0] == "survival"
    assert phases_7[-1] == "internal_reckoning"
    # Middle should NOT just repeat internal_reckoning
    middle = phases_7[1:-1]
    assert "internal_reckoning" not in middle
    # Should cycle through the 4 middle phases
    assert len(set(middle)) >= 3  # at least 3 distinct phases in the middle

    phases_8 = planner_services._assign_conflict_phases(8)
    assert len(phases_8) == 8
    assert phases_8[0] == "survival"
    assert phases_8[-1] == "internal_reckoning"


def test_json_dump_helper_keeps_unicode() -> None:
    payload = {"title": "长夜巡航"}
    dumped = planner_services._json_dumps(payload)
    assert "长夜巡航" in dumped
    assert json.loads(dumped)["title"] == "长夜巡航"
