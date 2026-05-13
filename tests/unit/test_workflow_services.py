from __future__ import annotations

from uuid import uuid4

import pytest

from bestseller.domain.workflow import ChapterOutlineBatchInput
from bestseller.infra.db.models import (
    CharacterModel,
    ChapterModel,
    PlanningArtifactVersionModel,
    ProjectModel,
    SceneCardModel,
    WorkflowRunModel,
    WorkflowStepRunModel,
)
from bestseller.domain.enums import ArtifactType, ChapterStatus, SceneStatus
from bestseller.services.invariants import invariants_to_dict, seed_invariants
from bestseller.services import workflows as workflow_services


pytestmark = pytest.mark.unit


class FakeSession:
    def __init__(
        self,
        scalar_results: list[object | None] | None = None,
        scalars_results: list[list[object]] | None = None,
    ) -> None:
        self.scalar_results = list(scalar_results or [])
        self.scalars_results = list(scalars_results or [])
        self.added: list[object] = []
        self.deleted: list[object] = []

    def add(self, obj: object) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        for obj in self.added:
            table = getattr(obj, "__table__", None)
            if table is None or "id" not in table.c:
                continue
            if getattr(obj, "id", None) is None:
                setattr(obj, "id", uuid4())

    async def scalar(self, stmt: object) -> object | None:
        if not self.scalar_results:
            return None
        return self.scalar_results.pop(0)

    async def scalars(self, stmt: object) -> list[object]:
        if not self.scalars_results:
            return []
        return self.scalars_results.pop(0)

    async def delete(self, obj: object) -> None:
        self.deleted.append(obj)


def build_project() -> ProjectModel:
    project = ProjectModel(
        slug="my-story",
        title="My Story",
        genre="fantasy",
        target_word_count=120000,
        target_chapters=60,
        metadata_json={
            "identity_manifest_status": "locked",
            "identity_manifest": [
                {
                    "name": "沈砚",
                    "role": "protagonist",
                    "gender": "male",
                    "pronoun_set_zh": "他",
                    "pronoun_set_en": "he/him",
                    "aliases": ["沈导航"],
                },
                {
                    "name": "港务官",
                    "role": "supporting",
                    "gender": "female",
                    "pronoun_set_zh": "她",
                    "pronoun_set_en": "she/her",
                    "aliases": [],
                },
            ],
        },
    )
    project.id = uuid4()
    return project


def test_bible_completeness_gate_blocks_incomplete_materialization() -> None:
    project = build_project()
    project.invariants_json = invariants_to_dict(
        seed_invariants(
            project_id=project.id,
            language="zh-CN",
            words_per_chapter=type(
                "Words",
                (),
                {"min": 5000, "target": 6400, "max": 7500},
            )(),
        )
    )

    with pytest.raises(ValueError, match="L2 bible gate failed"):
        workflow_services._audit_bible_completeness(
            project=project,
            project_slug=project.slug,
            book_spec_content={
                "title": "长夜巡航",
                "themes": ["真相"],
                "dramatic_question": "沈砚能否找回真相？",
            },
            world_spec_content={
                "power_system": {"name": "导航印记", "tiers": ["学徒"]}
            },
            cast_spec_content={
                "protagonist": {"name": "沈砚"},
                "antagonist": {"name": "祁镇"},
            },
        )


def build_batch() -> ChapterOutlineBatchInput:
    return ChapterOutlineBatchInput.model_validate(
        {
            "batch_name": "opening",
            "chapters": [
                {
                    "chapter_number": 1,
                    "title": "The Signal",
                    "goal": "Introduce the investigation.",
                    "main_conflict": "沈砚必须在封港命令生效前确认信号来源。",
                    "hook_description": "封港倒计时只剩一小时。",
                    "causal_contract": {
                        "chapter_function": "action",
                        "pressure": "封港命令一小时后生效，沈砚必须立刻确认信号来源。",
                        "protagonist_desire": "沈砚要在封港前拿到异常信号的来源。",
                        "protagonist_choice": "沈砚选择接下调查任务并进入码头。",
                        "visible_action_or_reaction": "沈砚接下港务官的任务，开始追查信号。",
                        "resistance": "封港命令和倒计时压缩了他的调查窗口。",
                        "cost_or_tradeoff": "如果判断失误，沈砚会失去封港前最后一次追查机会。",
                        "gain_or_reveal": "沈砚获得异常信号来自码头深处的线索。",
                        "state_change": "沈砚从旁观封港变成承担调查责任的人。",
                        "next_reader_desire": "读者想知道一小时倒计时内他能否找到信号来源。",
                    },
                    "scenes": [
                        {
                            "scene_number": 1,
                            "scene_type": "setup",
                            "title": "Silent Dock",
                            "time_label": "第一日夜，封港前一小时",
                            "participants": ["沈砚", "港务官"],
                            "purpose": {
                                "story": "抛出封港命令并逼迫沈砚接下调查任务。",
                                "emotion": "压迫感和抗拒同时上升。",
                            },
                        }
                    ],
                }
            ],
        }
    )


def test_chapter_outline_aliases_and_contract_input_repair() -> None:
    batch = ChapterOutlineBatchInput.model_validate(
        {
            "batch_name": "volume-1-outline",
            "chapters": [
                {
                    "chapter_number": 1,
                    "title": "镜中泣",
                    "goal": "苏砚确认铜镜异变与母亲旧案有关。",
                    "main_conflict": "苏砚必须在宿老封宅前读取铜镜残痕。",
                    "hook_description": "铜镜渗出血珠，映出大火夜的人影。",
                    "scenes": [
                        {
                            "scene_number": 1.1,
                            "scene_setting": "青萝镇旧宅暮色",
                            "story_emotion_task": "苏砚进入旧宅，发现铜镜渗出血珠。",
                            "aesthetic_goal": "冷艳诡异的东方器物志怪氛围。",
                            "philosophical_anchor": "器物承载执念，执念深重则生灵。",
                        }
                    ],
                }
            ],
        }
    )

    repaired = workflow_services._repair_chapter_outline_contract_inputs(
        batch,
        identity_manifest=[
            {
                "name": "苏砚",
                "role": "protagonist",
                "gender": "male",
                "pronoun_set_zh": "他",
                "pronoun_set_en": "he/him",
            }
        ],
    )
    report = workflow_services.validate_chapter_plan_contract(
        batch,
        identity_manifest=[
            {
                "name": "苏砚",
                "role": "protagonist",
                "gender": "male",
                "pronoun_set_zh": "他",
                "pronoun_set_en": "he/him",
            }
        ],
        require_identity_registry=True,
    )

    scene = batch.chapters[0].scenes[0]
    assert repaired == 1
    assert scene.scene_number == 1
    assert scene.time_label == "青萝镇旧宅暮色"
    assert scene.participants == ["苏砚"]
    assert "铜镜渗出血珠" in scene.purpose["story"]
    assert report.passed is True


def test_outline_chapter_number_normalization_closes_materialization_gaps() -> None:
    batch = ChapterOutlineBatchInput.model_validate(
        {
            "batch_name": "volume-10-outline",
            "chapters": [
                {
                    "chapter_number": number,
                    "title": f"第{number}章",
                    "goal": f"推进第{number}章",
                    "main_conflict": f"第{number}章冲突",
                    "hook_description": f"第{number}章钩子",
                    "scenes": [
                        {
                            "scene_number": 1,
                            "scene_type": "setup",
                            "title": "场景",
                            "time_label": "夜间",
                            "participants": ["林鸢"],
                            "purpose": {
                                "story": "林鸢必须推进计划。",
                                "emotion": "压力上升。",
                            },
                        }
                    ],
                }
                for number in [482, 483, 485, 486]
            ],
        }
    )

    normalization = workflow_services._normalize_outline_chapter_numbers(batch)

    assert [chapter.chapter_number for chapter in batch.chapters] == [482, 483, 484, 485]
    assert normalization == {
        "start": 482,
        "end": 485,
        "renumbered": [{"from": 485, "to": 484}, {"from": 486, "to": 485}],
    }


def test_chapter_outline_accepts_chapter_level_llm_aliases() -> None:
    batch = ChapterOutlineBatchInput.model_validate(
        {
            "batch_name": "volume-1-outline",
            "chapters": [
                {
                    "chapter_number": 1,
                    "chapter_title": "镜泣",
                    "chapter_goal": "苏砚读取铜镜残相并确认大火线索。",
                    "chapter_main_conflict": "铜镜器灵抗拒共感，苏砚必须在反噬前截取残相。",
                    "chapter_hook_type": "信息揭示",
                    "hook_description": "镜框灼痕指向母亲临终时的伤口。",
                    "scenes": [
                        {
                            "scene_number": 1,
                            "location": "青萝镇古宅",
                            "participants": ["苏砚"],
                            "story_task": "苏砚触碰铜镜，看到火海中的铭纹鼎。",
                            "emotion_task": "震惊与追索欲同时上升。",
                        }
                    ],
                }
            ],
        }
    )

    chapter = batch.chapters[0]
    assert chapter.title == "镜泣"
    assert chapter.main_conflict == "铜镜器灵抗拒共感，苏砚必须在反噬前截取残相。"
    assert chapter.hook_type == "信息揭示"


def test_chapter_outline_repair_adds_identity_names_from_scene_purpose() -> None:
    batch = ChapterOutlineBatchInput.model_validate(
        {
            "batch_name": "volume-1-outline",
            "chapters": [
                {
                    "chapter_number": 8,
                    "title": "旧识",
                    "goal": "引入沈夜寒并揭示志怪监线索。",
                    "main_conflict": "师父的警告与苏砚追查真相的决心正面冲突。",
                    "hook_description": "沈夜寒说出苏砚母亲曾是志怪监执律使。",
                    "scenes": [
                        {
                            "scene_number": 8.1,
                            "setting": "青萝镇口·黄昏",
                            "participants": ["苏砚"],
                            "story_task": "沈夜寒出现在苏砚面前，以师徒身份介入调查。",
                            "emotion_task": "重逢的复杂情感。",
                        }
                    ],
                }
            ],
        }
    )
    identity_manifest = [
        {
            "name": "苏砚",
            "role": "protagonist",
            "gender": "male",
            "pronoun_set_zh": "他",
            "pronoun_set_en": "he/him",
        },
        {
            "name": "沈夜寒",
            "role": "mentor",
            "gender": "male",
            "pronoun_set_zh": "他",
            "pronoun_set_en": "he/him",
        },
    ]

    repaired = workflow_services._repair_chapter_outline_contract_inputs(
        batch,
        identity_manifest=identity_manifest,
    )
    report = workflow_services.validate_chapter_plan_contract(
        batch,
        identity_manifest=identity_manifest,
        require_identity_registry=True,
    )

    assert repaired == 1
    assert batch.chapters[0].scenes[0].participants == ["苏砚", "沈夜寒"]
    assert report.passed is True


def test_chapter_outline_repair_does_not_synthesize_generic_story_fields() -> None:
    batch = ChapterOutlineBatchInput.model_validate(
        {
            "batch_name": "volume-1-outline",
            "chapters": [
                {
                    "chapter_number": 54,
                    "title": "暗潮失衡",
                    "goal": "一种环境或体系层面的威胁出现，无法用力量硬碰硬。",
                    "opening_situation": "承接上一章尾钩，主角没有空档去长篇解释设定。",
                    "main_conflict": "铜镜器灵·残相执念收紧包围圈，苏砚必须在有限时间内做出取舍。",
                    "hook_description": "具体事件是「尾钩」。",
                    "scenes": [
                        {
                            "scene_number": 54.1,
                            "time_label": "章节开场",
                            "participants": ["苏砚"],
                            "purpose": {
                                "story": "承接上章后果并明确本章行动目标（本章目标：一种环境或体系层面的威胁出现。）",
                                "emotion": "压力上升。",
                            },
                        }
                    ],
                }
            ],
        }
    )
    identity_manifest = [
        {
            "name": "苏砚",
            "role": "protagonist",
            "gender": "male",
            "pronoun_set_zh": "他",
            "pronoun_set_en": "he/him",
        }
    ]

    repaired = workflow_services._repair_chapter_outline_contract_inputs(
        batch,
        identity_manifest=identity_manifest,
    )
    report = workflow_services.validate_chapter_plan_contract(
        batch,
        identity_manifest=identity_manifest,
        require_identity_registry=True,
    )

    assert repaired == 1
    assert report.passed is False
    codes = {violation.code for violation in report.violations}
    assert "PLAN_CHAPTER_GOAL_GENERIC" in codes
    assert "PLAN_CHAPTER_OPENING_GENERIC" in codes
    assert "PLAN_CHAPTER_HOOK_GENERIC" in codes
    assert "PLAN_SCENE_STORY_PURPOSE_GENERIC" in codes


def test_outline_word_targets_are_normalized_to_shared_budget() -> None:
    project = build_project()
    batch = ChapterOutlineBatchInput.model_validate(
        {
            "batch_name": "stale-budget",
            "chapters": [
                {
                    "chapter_number": 1,
                    "title": "Stale Target",
                    "goal": "推进调查",
                    "main_conflict": "沈砚必须确认信号来源。",
                    "hook_description": "新倒计时出现。",
                    "target_word_count": 6400,
                    "scenes": [
                        {
                            "scene_number": 1,
                            "scene_type": "setup",
                            "time_label": "第一日夜",
                            "participants": ["沈砚"],
                            "purpose": {"story": "建立压力", "emotion": "紧张"},
                            "target_word_count": 1600,
                        },
                        {
                            "scene_number": 2,
                            "scene_type": "turn",
                            "time_label": "第一日夜",
                            "participants": ["沈砚"],
                            "purpose": {"story": "揭示线索", "emotion": "疑虑"},
                            "target_word_count": 1600,
                        },
                        {
                            "scene_number": 3,
                            "scene_type": "hook",
                            "time_label": "第一日夜",
                            "participants": ["沈砚"],
                            "purpose": {"story": "留下尾钩", "emotion": "压迫"},
                            "target_word_count": 1600,
                        },
                    ],
                }
            ],
        }
    )

    repaired = workflow_services._normalize_outline_word_targets(
        batch,
        project=project,
        settings=workflow_services.load_settings(env={}),
    )

    assert repaired == 4
    assert batch.chapters[0].target_word_count == 2000
    assert [scene.target_word_count for scene in batch.chapters[0].scenes] == [667, 667, 667]


def build_planned_chapter(project: ProjectModel, number: int, *, status: str = ChapterStatus.PLANNED.value) -> ChapterModel:
    chapter = ChapterModel(
        project_id=project.id,
        chapter_number=number,
        title=f"Old {number}",
        chapter_goal="old-goal",
        opening_situation="old-open",
        main_conflict="old-conflict",
        hook_type="old-hook",
        hook_description="old-desc",
        information_revealed=[],
        information_withheld=[],
        foreshadowing_actions={},
        metadata_json={},
        target_word_count=1200,
        status=status,
    )
    chapter.id = uuid4()
    chapter.volume_id = uuid4()
    return chapter


def build_planned_scene(project: ProjectModel, chapter: ChapterModel, number: int, *, status: str = SceneStatus.PLANNED.value) -> SceneCardModel:
    scene = SceneCardModel(
        project_id=project.id,
        chapter_id=chapter.id,
        scene_number=number,
        scene_type="setup",
        title=f"Old Scene {number}",
        participants=[],
        purpose={},
        entry_state={},
        exit_state={},
        key_dialogue_beats=[],
        sensory_anchors={},
        forbidden_actions=[],
        status=status,
    )
    scene.id = uuid4()
    return scene


@pytest.mark.asyncio
async def test_materialize_chapter_outline_batch_creates_workflow_records(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = build_project()

    async def fake_get_project_by_slug(session: object, slug: str) -> ProjectModel:
        return project

    async def fake_create_chapter(session: object, project_slug: str, payload: object) -> object:
        return type(
            "ChapterStub",
            (),
            {
                "id": uuid4(),
                "chapter_number": payload.chapter_number,
                "target_word_count": payload.target_word_count,
            },
        )()

    async def fake_create_scene_card(
        session: object,
        project_slug: str,
        chapter_number: int,
        payload: object,
    ) -> object:
        return type("SceneStub", (), {"id": uuid4(), "scene_number": payload.scene_number})()

    monkeypatch.setattr(workflow_services, "get_project_by_slug", fake_get_project_by_slug)
    monkeypatch.setattr(workflow_services, "create_chapter", fake_create_chapter)
    monkeypatch.setattr(workflow_services, "create_scene_card", fake_create_scene_card)

    session = FakeSession()
    result = await workflow_services.materialize_chapter_outline_batch(
        session,
        "my-story",
        build_batch(),
        requested_by="tester",
    )

    workflow_runs = [obj for obj in session.added if isinstance(obj, WorkflowRunModel)]
    workflow_steps = [obj for obj in session.added if isinstance(obj, WorkflowStepRunModel)]

    assert result.chapters_created == 1
    assert result.scenes_created == 1
    assert len(workflow_runs) == 1
    assert workflow_runs[0].status == "completed"
    assert workflow_runs[0].metadata_json["chapters_created"] == 1
    assert workflow_runs[0].metadata_json["scenes_created"] == 1
    assert len(workflow_steps) == 3


@pytest.mark.asyncio
async def test_materialize_latest_chapter_outline_batch_uses_stored_artifact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = build_project()
    artifact = PlanningArtifactVersionModel(
        project_id=project.id,
        artifact_type="chapter_outline_batch",
        scope_ref_id=None,
        version_no=2,
        status="approved",
        schema_version="1.0",
        content=build_batch().model_dump(mode="json", by_alias=True),
        created_by="tester",
    )
    artifact.id = uuid4()

    async def fake_get_project_by_slug(session: object, slug: str) -> ProjectModel:
        return project

    async def fake_create_chapter(session: object, project_slug: str, payload: object) -> object:
        return type(
            "ChapterStub",
            (),
            {
                "id": uuid4(),
                "chapter_number": payload.chapter_number,
                "target_word_count": payload.target_word_count,
            },
        )()

    async def fake_create_scene_card(
        session: object,
        project_slug: str,
        chapter_number: int,
        payload: object,
    ) -> object:
        return type("SceneStub", (), {"id": uuid4(), "scene_number": payload.scene_number})()

    monkeypatch.setattr(workflow_services, "get_project_by_slug", fake_get_project_by_slug)
    monkeypatch.setattr(workflow_services, "create_chapter", fake_create_chapter)
    monkeypatch.setattr(workflow_services, "create_scene_card", fake_create_scene_card)

    session = FakeSession(scalar_results=[artifact])
    result = await workflow_services.materialize_latest_chapter_outline_batch(
        session,
        "my-story",
        requested_by="tester",
    )

    assert result.source_artifact_id == artifact.id
    assert result.batch_name == "opening"


@pytest.mark.asyncio
async def test_materialize_latest_chapter_outline_batch_updates_existing_planned_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = build_project()
    artifact = PlanningArtifactVersionModel(
        project_id=project.id,
        artifact_type="chapter_outline_batch",
        scope_ref_id=None,
        version_no=2,
        status="approved",
        schema_version="1.0",
        content=build_batch().model_dump(mode="json", by_alias=True),
        created_by="tester",
    )
    artifact.id = uuid4()
    existing_chapter = build_planned_chapter(project, 1)
    existing_scene = build_planned_scene(project, existing_chapter, 1)
    stale_chapter = build_planned_chapter(project, 99)

    async def fake_get_project_by_slug(session: object, slug: str) -> ProjectModel:
        return project

    async def fake_create_or_get_volume(session: object, project_id, payload: object) -> object:
        return type("VolumeStub", (), {"id": uuid4()})()

    monkeypatch.setattr(workflow_services, "get_project_by_slug", fake_get_project_by_slug)
    monkeypatch.setattr(workflow_services, "create_or_get_volume", fake_create_or_get_volume)

    session = FakeSession(
        scalar_results=[artifact, existing_chapter],
        scalars_results=[
            # New plan_fingerprint.scan_batch_for_duplicates pulls existing
            # chapters (outside the new batch) to compare against.  Empty is
            # fine here — the scan only affects logging / metadata.
            [],
            [existing_scene],
            [existing_chapter, stale_chapter],
        ],
    )
    result = await workflow_services.materialize_latest_chapter_outline_batch(
        session,
        "my-story",
        requested_by="tester",
    )

    workflow_runs = [obj for obj in session.added if isinstance(obj, WorkflowRunModel)]

    assert result.source_artifact_id == artifact.id
    assert existing_chapter.title == "The Signal"
    assert existing_chapter.chapter_goal == "Introduce the investigation."
    assert existing_scene.title == "Silent Dock"
    assert stale_chapter in session.deleted
    assert workflow_runs[0].metadata_json["chapters_updated"] == 1
    assert workflow_runs[0].metadata_json["scenes_updated"] == 1
    assert workflow_runs[0].metadata_json["chapters_pruned"] == 1


@pytest.mark.asyncio
async def test_materialize_chapter_outline_batch_blocks_critical_plan_fingerprint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = build_project()
    artifact = PlanningArtifactVersionModel(
        project_id=project.id,
        artifact_type="chapter_outline_batch",
        scope_ref_id=None,
        version_no=1,
        status="approved",
        schema_version="1.0",
        content=build_batch().model_dump(mode="json", by_alias=True),
        created_by="tester",
    )
    artifact.id = uuid4()

    async def fake_get_project_by_slug(session: object, slug: str) -> ProjectModel:
        return project

    async def fake_create_or_get_volume(session: object, project_id, payload: object) -> object:
        return type("VolumeStub", (), {"id": uuid4()})()

    class Finding:
        chapter_a = 1
        chapter_b = 2
        similarity = 0.91
        severity = "critical"
        reason = "same conflict and hook"

    class Report:
        findings = (Finding(),)
        has_critical = True

    def fake_scan_batch_for_duplicates(batch_outlines: object, existing_db_chapters: object) -> Report:
        return Report()

    from bestseller.services import plan_fingerprint as plan_fingerprint_services

    monkeypatch.setattr(workflow_services, "get_project_by_slug", fake_get_project_by_slug)
    monkeypatch.setattr(workflow_services, "create_or_get_volume", fake_create_or_get_volume)
    monkeypatch.setattr(
        plan_fingerprint_services,
        "scan_batch_for_duplicates",
        fake_scan_batch_for_duplicates,
    )

    session = FakeSession(scalar_results=[artifact], scalars_results=[[]])

    with pytest.raises(ValueError, match="plan fingerprint gate"):
        await workflow_services.materialize_latest_chapter_outline_batch(
            session,
            "my-story",
            requested_by="tester",
        )

    workflow_runs = [obj for obj in session.added if isinstance(obj, WorkflowRunModel)]
    assert workflow_runs[0].status == "failed"
    assert workflow_runs[0].metadata_json["plan_fingerprint_has_critical"] is True


@pytest.mark.asyncio
async def test_materialize_chapter_outline_batch_warns_on_critical_plan_fingerprint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = build_project()
    project.metadata_json = {
        **(project.metadata_json or {}),
        "plan_fingerprint_gate_warn_only": True,
    }

    async def fake_get_project_by_slug(session: object, slug: str) -> ProjectModel:
        return project

    async def fake_create_chapter(session: object, project_slug: str, payload: object) -> object:
        return type(
            "ChapterStub",
            (),
            {
                "id": uuid4(),
                "chapter_number": payload.chapter_number,
                "target_word_count": payload.target_word_count,
            },
        )()

    async def fake_create_scene_card(
        session: object,
        project_slug: str,
        chapter_number: int,
        payload: object,
    ) -> object:
        return type("SceneStub", (), {"id": uuid4(), "scene_number": payload.scene_number})()

    class Finding:
        chapter_a = 1
        chapter_b = 2
        similarity = 0.91
        severity = "critical"
        reason = "same conflict and hook"

    class Report:
        findings = (Finding(),)
        has_critical = True

    def fake_scan_batch_for_duplicates(batch_outlines: object, existing_db_chapters: object) -> Report:
        return Report()

    from bestseller.services import plan_fingerprint as plan_fingerprint_services

    monkeypatch.setattr(workflow_services, "get_project_by_slug", fake_get_project_by_slug)
    monkeypatch.setattr(workflow_services, "create_chapter", fake_create_chapter)
    monkeypatch.setattr(workflow_services, "create_scene_card", fake_create_scene_card)
    monkeypatch.setattr(
        plan_fingerprint_services,
        "scan_batch_for_duplicates",
        fake_scan_batch_for_duplicates,
    )

    session = FakeSession(scalars_results=[[], []])
    result = await workflow_services.materialize_chapter_outline_batch(
        session,
        "my-story",
        build_batch(),
        requested_by="tester",
    )

    workflow_runs = [obj for obj in session.added if isinstance(obj, WorkflowRunModel)]
    assert result.chapters_created == 1
    assert workflow_runs[0].status == "completed"
    assert workflow_runs[0].metadata_json["plan_fingerprint_has_critical"] is True
    assert workflow_runs[0].metadata_json["plan_fingerprint_gate_warn_only"] is True


@pytest.mark.asyncio
async def test_materialize_chapter_outline_batch_blocks_contract_violation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = build_project()
    bad_batch = ChapterOutlineBatchInput.model_validate(
        {
            "batch_name": "bad-plan",
            "chapters": [
                {
                    "chapter_number": 1,
                    "title": "Bad Plan",
                    "main_conflict": "沈砚必须处理封港命令。",
                    "scenes": [
                        {
                            "scene_number": 1,
                            "scene_type": "setup",
                            "participants": ["陌生人"],
                            "purpose": {"story": "引出封港命令。"},
                        }
                    ],
                }
            ],
        }
    )

    async def fake_get_project_by_slug(session: object, slug: str) -> ProjectModel:
        return project

    monkeypatch.setattr(workflow_services, "get_project_by_slug", fake_get_project_by_slug)
    session = FakeSession(scalars_results=[[]])

    with pytest.raises(ValueError, match="chapter_plan_contract"):
        await workflow_services.materialize_chapter_outline_batch(
            session,
            "my-story",
            bad_batch,
            requested_by="tester",
        )

    workflow_runs = [obj for obj in session.added if isinstance(obj, WorkflowRunModel)]
    assert workflow_runs[0].status == "failed"
    assert workflow_runs[0].metadata_json["chapter_plan_contract"]["passed"] is False


@pytest.mark.asyncio
async def test_materialize_chapter_outline_batch_blocks_weak_causality(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = build_project()
    bad_batch = ChapterOutlineBatchInput.model_validate(
        {
            "batch_name": "weak-causality",
            "chapters": [
                {
                    "chapter_number": 12,
                    "title": "空潮",
                    "goal": "沈砚继续处理港口局势。",
                    "main_conflict": "港口压力继续存在。",
                    "hook_description": "港口局势出现新的情况。",
                    "scenes": [
                        {
                            "scene_number": 1,
                            "scene_type": "transition",
                            "time_label": "港口次日",
                            "participants": ["沈砚"],
                            "purpose": {
                                "story": "沈砚思考港口局势。",
                                "emotion": "情绪复杂。",
                            },
                        }
                    ],
                }
            ],
        }
    )

    async def fake_get_project_by_slug(session: object, slug: str) -> ProjectModel:
        return project

    monkeypatch.setattr(workflow_services, "get_project_by_slug", fake_get_project_by_slug)
    session = FakeSession(scalars_results=[[]])

    with pytest.raises(ValueError, match="chapter_causality_contract"):
        await workflow_services.materialize_chapter_outline_batch(
            session,
            "my-story",
            bad_batch,
            requested_by="tester",
        )

    workflow_runs = [obj for obj in session.added if isinstance(obj, WorkflowRunModel)]
    assert workflow_runs[0].status == "failed"
    assert workflow_runs[0].metadata_json["chapter_causality_contract"]["passed"] is False


@pytest.mark.asyncio
async def test_materialize_chapter_outline_batch_marks_workflow_failed_on_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = build_project()

    async def fake_get_project_by_slug(session: object, slug: str) -> ProjectModel:
        return project

    async def fake_create_chapter(session: object, project_slug: str, payload: object) -> object:
        raise ValueError("chapter creation failed")

    monkeypatch.setattr(workflow_services, "get_project_by_slug", fake_get_project_by_slug)
    monkeypatch.setattr(workflow_services, "create_chapter", fake_create_chapter)

    session = FakeSession()

    with pytest.raises(ValueError, match="chapter creation failed"):
        await workflow_services.materialize_chapter_outline_batch(
            session,
            "my-story",
            build_batch(),
        )

    workflow_runs = [obj for obj in session.added if isinstance(obj, WorkflowRunModel)]
    workflow_steps = [obj for obj in session.added if isinstance(obj, WorkflowStepRunModel)]

    assert len(workflow_runs) == 1
    assert workflow_runs[0].status == "failed"
    assert workflow_steps[-1].status == "failed"


@pytest.mark.asyncio
async def test_materialize_story_bible_creates_workflow_records(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = build_project()

    async def fake_get_project_by_slug(session: object, slug: str) -> ProjectModel:
        return project

    async def fake_apply_book_spec(session: object, project_obj: object, content: object) -> bool:
        return True

    async def fake_upsert_world_spec(session: object, project_obj: object, content: object) -> dict[str, int]:
        return {
            "world_rules_upserted": 2,
            "locations_upserted": 1,
            "factions_upserted": 1,
        }

    async def fake_upsert_cast_spec(session: object, project_obj: object, content: object) -> dict[str, int]:
        return {
            "characters_upserted": 3,
            "relationships_upserted": 2,
            "state_snapshots_created": 3,
        }

    async def fake_upsert_volume_plan(session: object, project_obj: object, content: object) -> dict[str, int]:
        return {"volumes_upserted": 2}

    async def fake_refresh_story_bible_retrieval_index(session: object, settings: object, project_id) -> int:
        return 9

    async def fake_refresh_world_expansion_boundaries(session: object, *, project: object) -> dict[str, int]:
        return {
            "world_backbones_upserted": 1,
            "volume_frontiers_upserted": 2,
            "deferred_reveals_upserted": 3,
            "expansion_gates_upserted": 2,
        }

    monkeypatch.setattr(workflow_services, "get_project_by_slug", fake_get_project_by_slug)
    monkeypatch.setattr(workflow_services, "apply_book_spec", fake_apply_book_spec)
    monkeypatch.setattr(workflow_services, "upsert_world_spec", fake_upsert_world_spec)
    monkeypatch.setattr(workflow_services, "upsert_cast_spec", fake_upsert_cast_spec)
    monkeypatch.setattr(workflow_services, "upsert_volume_plan", fake_upsert_volume_plan)
    monkeypatch.setattr(
        workflow_services,
        "refresh_world_expansion_boundaries",
        fake_refresh_world_expansion_boundaries,
    )
    monkeypatch.setattr(
        workflow_services,
        "refresh_story_bible_retrieval_index",
        fake_refresh_story_bible_retrieval_index,
    )

    session = FakeSession()
    result = await workflow_services.materialize_story_bible(
        session,
        "my-story",
        requested_by="tester",
        book_spec_content={"title": "长夜巡航"},
        world_spec_content={"rules": []},
        cast_spec_content={"supporting_cast": []},
        volume_plan_content=[],
    )

    workflow_runs = [obj for obj in session.added if isinstance(obj, WorkflowRunModel)]
    workflow_steps = [obj for obj in session.added if isinstance(obj, WorkflowStepRunModel)]

    assert result.applied_artifacts == ["book_spec", "world_spec", "cast_spec", "volume_plan"]
    assert result.world_rules_upserted == 2
    assert result.characters_upserted == 3
    assert result.volumes_upserted == 2
    assert result.world_backbones_upserted == 1
    assert len(workflow_runs) == 1
    assert workflow_runs[0].status == "completed"
    assert len(workflow_steps) == 7


@pytest.mark.asyncio
async def test_materialize_story_bible_blocks_missing_identity_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = build_project()

    async def fake_get_project_by_slug(session: object, slug: str) -> ProjectModel:
        return project

    monkeypatch.setattr(workflow_services, "get_project_by_slug", fake_get_project_by_slug)

    session = FakeSession()
    with pytest.raises(ValueError, match="foundation_identity_contract"):
        await workflow_services.materialize_story_bible(
            session,
            "my-story",
            requested_by="tester",
            cast_spec_content={
                "protagonist": {
                    "name": "沈砚",
                    "role": "protagonist",
                }
            },
        )

    assert not [obj for obj in session.added if isinstance(obj, WorkflowRunModel)]


@pytest.mark.asyncio
async def test_ensure_project_identity_manifest_blocks_invalid_resume_cast() -> None:
    project = build_project()
    project.metadata_json = {}
    artifact = PlanningArtifactVersionModel(
        project_id=project.id,
        artifact_type=ArtifactType.CAST_SPEC.value,
        scope_ref_id=None,
        version_no=1,
        status="approved",
        schema_version="1.0",
        content={"protagonist": {"name": "沈砚", "role": "protagonist"}},
        created_by="tester",
    )
    artifact.id = uuid4()
    session = FakeSession(scalar_results=[artifact])

    with pytest.raises(ValueError, match="foundation_identity_contract"):
        await workflow_services.ensure_project_identity_manifest(
            session,
            project,
            project_slug=project.slug,
        )

    assert project.metadata_json == {}


@pytest.mark.asyncio
async def test_ensure_project_identity_manifest_backfills_project_and_characters() -> None:
    project = build_project()
    project.metadata_json = {}
    artifact = PlanningArtifactVersionModel(
        project_id=project.id,
        artifact_type=ArtifactType.CAST_SPEC.value,
        scope_ref_id=None,
        version_no=1,
        status="approved",
        schema_version="1.0",
        content={
            "protagonist": {
                "name": "沈砚",
                "role": "protagonist",
                "gender": "male",
                "aliases": ["沈导航"],
            }
        },
        created_by="tester",
    )
    artifact.id = uuid4()
    character = CharacterModel(
        project_id=project.id,
        name="沈砚",
        role="protagonist",
        metadata_json={},
    )
    character.id = uuid4()
    session = FakeSession(scalar_results=[artifact], scalars_results=[[character]])

    manifest = await workflow_services.ensure_project_identity_manifest(
        session,
        project,
        project_slug=project.slug,
    )

    assert manifest[0]["name"] == "沈砚"
    assert project.metadata_json["identity_manifest_status"] == "locked"
    assert character.metadata_json["gender"] == "male"
    assert character.metadata_json["pronoun_set_zh"] == "他"
    assert character.metadata_json["cast_entry"]["pronoun_set_en"] == "he/him"


@pytest.mark.asyncio
async def test_materialize_latest_story_bible_uses_stored_artifacts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = build_project()
    book_artifact = PlanningArtifactVersionModel(
        project_id=project.id,
        artifact_type=ArtifactType.BOOK_SPEC.value,
        scope_ref_id=None,
        version_no=1,
        status="approved",
        schema_version="1.0",
        content={"title": "长夜巡航"},
        created_by="tester",
    )
    book_artifact.id = uuid4()
    cast_artifact = PlanningArtifactVersionModel(
        project_id=project.id,
        artifact_type=ArtifactType.CAST_SPEC.value,
        scope_ref_id=None,
        version_no=1,
        status="approved",
        schema_version="1.0",
        content={"supporting_cast": []},
        created_by="tester",
    )
    cast_artifact.id = uuid4()

    async def fake_get_project_by_slug(session: object, slug: str) -> ProjectModel:
        return project

    async def fake_get_latest_planning_artifact(session: object, *, project_id, artifact_type):
        if artifact_type is ArtifactType.BOOK_SPEC:
            return book_artifact
        if artifact_type is ArtifactType.CAST_SPEC:
            return cast_artifact
        return None

    async def fake_materialize_story_bible(session: object, project_slug: str, **kwargs):
        return workflow_services.StoryBibleMaterializationResult(
            workflow_run_id=uuid4(),
            project_id=project.id,
            applied_artifacts=["book_spec", "cast_spec"],
            characters_upserted=1,
            source_artifact_ids={
                "book_spec": book_artifact.id,
                "cast_spec": cast_artifact.id,
            },
        )

    monkeypatch.setattr(workflow_services, "get_project_by_slug", fake_get_project_by_slug)
    monkeypatch.setattr(
        workflow_services,
        "get_latest_planning_artifact",
        fake_get_latest_planning_artifact,
    )
    monkeypatch.setattr(workflow_services, "materialize_story_bible", fake_materialize_story_bible)

    result = await workflow_services.materialize_latest_story_bible(
        FakeSession(),
        "my-story",
        requested_by="tester",
    )

    assert result.applied_artifacts == ["book_spec", "cast_spec"]
    assert result.source_artifact_ids["book_spec"] == book_artifact.id


@pytest.mark.asyncio
async def test_materialize_narrative_graph_creates_workflow_records(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = build_project()

    async def fake_get_project_by_slug(session: object, slug: str) -> ProjectModel:
        return project

    async def fake_rebuild_narrative_graph(session: object, *, project, volume_plan_content=None):
        assert project.id == project.id
        assert volume_plan_content == [{"volume_number": 1}]
        return {
            "plot_arc_count": 3,
            "arc_beat_count": 8,
            "clue_count": 2,
            "payoff_count": 1,
            "chapter_contract_count": 4,
            "scene_contract_count": 12,
        }

    monkeypatch.setattr(workflow_services, "get_project_by_slug", fake_get_project_by_slug)
    monkeypatch.setattr(workflow_services, "rebuild_narrative_graph", fake_rebuild_narrative_graph)

    session = FakeSession()
    result = await workflow_services.materialize_narrative_graph(
        session,
        "my-story",
        requested_by="tester",
        volume_plan_content=[{"volume_number": 1}],
    )

    workflow_runs = [obj for obj in session.added if isinstance(obj, WorkflowRunModel)]
    workflow_steps = [obj for obj in session.added if isinstance(obj, WorkflowStepRunModel)]

    assert result.plot_arc_count == 3
    assert result.scene_contract_count == 12
    assert len(workflow_runs) == 1
    assert workflow_runs[0].status == "completed"
    assert workflow_runs[0].metadata_json["plot_arc_count"] == 3
    assert len(workflow_steps) == 2


@pytest.mark.asyncio
async def test_materialize_latest_narrative_graph_uses_volume_plan_artifact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = build_project()
    volume_plan_artifact = PlanningArtifactVersionModel(
        project_id=project.id,
        artifact_type=ArtifactType.VOLUME_PLAN.value,
        scope_ref_id=None,
        version_no=1,
        status="approved",
        schema_version="1.0",
        content=[{"volume_number": 1}],
        created_by="tester",
    )
    volume_plan_artifact.id = uuid4()

    async def fake_get_project_by_slug(session: object, slug: str) -> ProjectModel:
        return project

    async def fake_get_latest_planning_artifact(session: object, *, project_id, artifact_type):
        if artifact_type is ArtifactType.VOLUME_PLAN:
            return volume_plan_artifact
        return None

    async def fake_materialize_narrative_graph(session: object, project_slug: str, **kwargs):
        assert kwargs["volume_plan_content"] == [{"volume_number": 1}]
        assert kwargs["source_artifact_ids"][ArtifactType.VOLUME_PLAN.value] == volume_plan_artifact.id
        return workflow_services.NarrativeGraphMaterializationResult(
            workflow_run_id=uuid4(),
            project_id=project.id,
            plot_arc_count=3,
            arc_beat_count=8,
            clue_count=2,
            payoff_count=1,
            chapter_contract_count=4,
            scene_contract_count=12,
            source_artifact_ids={ArtifactType.VOLUME_PLAN.value: volume_plan_artifact.id},
        )

    monkeypatch.setattr(workflow_services, "get_project_by_slug", fake_get_project_by_slug)
    monkeypatch.setattr(
        workflow_services,
        "get_latest_planning_artifact",
        fake_get_latest_planning_artifact,
    )
    monkeypatch.setattr(
        workflow_services,
        "materialize_narrative_graph",
        fake_materialize_narrative_graph,
    )

    result = await workflow_services.materialize_latest_narrative_graph(
        FakeSession(),
        "my-story",
        requested_by="tester",
    )

    assert result.plot_arc_count == 3
    assert result.source_artifact_ids[ArtifactType.VOLUME_PLAN.value] == volume_plan_artifact.id


@pytest.mark.asyncio
async def test_materialize_narrative_tree_creates_workflow_records(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = build_project()

    async def fake_get_project_by_slug(session: object, slug: str) -> ProjectModel:
        return project

    async def fake_rebuild_narrative_tree(session: object, *, project) -> dict[str, object]:
        return {
            "node_count": 12,
            "node_type_counts": {"chapter": 2, "scene": 4},
        }

    monkeypatch.setattr(workflow_services, "get_project_by_slug", fake_get_project_by_slug)
    monkeypatch.setattr(workflow_services, "rebuild_narrative_tree", fake_rebuild_narrative_tree)

    session = FakeSession()
    result = await workflow_services.materialize_narrative_tree(
        session,
        "my-story",
        requested_by="tester",
    )

    workflow_runs = [obj for obj in session.added if isinstance(obj, WorkflowRunModel)]
    workflow_steps = [obj for obj in session.added if isinstance(obj, WorkflowStepRunModel)]

    assert result.node_count == 12
    assert result.node_type_counts["chapter"] == 2
    assert len(workflow_runs) == 1
    assert workflow_runs[0].status == "completed"
    assert len(workflow_steps) == 1


@pytest.mark.asyncio
async def test_materialize_latest_narrative_tree_delegates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = build_project()

    async def fake_get_project_by_slug(session: object, slug: str) -> ProjectModel:
        return project

    async def fake_materialize_narrative_tree(session: object, project_slug: str, **kwargs):
        assert project_slug == "my-story"
        return type(
            "NarrativeTreeResultStub",
            (),
            {
                "workflow_run_id": uuid4(),
                "project_id": project.id,
                "node_count": 7,
                "node_type_counts": {"premise": 1},
            },
        )()

    monkeypatch.setattr(workflow_services, "get_project_by_slug", fake_get_project_by_slug)
    monkeypatch.setattr(
        workflow_services,
        "materialize_narrative_tree",
        fake_materialize_narrative_tree,
    )

    result = await workflow_services.materialize_latest_narrative_tree(
        FakeSession(),
        "my-story",
        requested_by="tester",
    )

    assert result.node_count == 7
