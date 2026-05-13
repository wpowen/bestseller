from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from bestseller.infra.db.models import (
    ChapterDraftVersionModel,
    ChapterModel,
    ExportArtifactModel,
    LlmRunModel,
    ProjectModel,
    RewriteTaskModel,
    SceneCardModel,
    SceneDraftVersionModel,
    StyleGuideModel,
    WorkflowRunModel,
    WorkflowStepRunModel,
)
from bestseller.domain.context import SceneWriterContextPacket
from bestseller.domain.contradiction import ContradictionCheckResult, ContradictionViolation
from bestseller.domain.knowledge import SceneKnowledgeRefreshResult
from bestseller.domain.pipeline import ProjectPipelineResult, ProjectRepairResult
from bestseller.services import contradiction as contradiction_services
from bestseller.services import drafts as draft_services
from bestseller.services import exports as export_services
from bestseller.services import identity_guard as identity_guard_services
from bestseller.services import pipelines as pipeline_services
from bestseller.services import reviews as review_services
from bestseller.services.truth_version import TruthVersionStaleError
from bestseller.services.write_safety_gate import WriteSafetyBlockError, WriteSafetyFinding
from bestseller.settings import load_settings


pytestmark = pytest.mark.unit


class FakeSession:
    def __init__(
        self,
        *,
        scalar_results: list[object | None] | None = None,
        scalars_results: list[list[object]] | None = None,
        execute_results: list[object | None] | None = None,
        get_map: dict[object, object] | None = None,
    ) -> None:
        self.scalar_results = list(scalar_results or [])
        self.scalars_results = list(scalars_results or [])
        self.execute_results = list(execute_results or [])
        self.get_map = dict(get_map or {})
        self.added: list[object] = []
        self.executed: list[object] = []
        self.is_active = True
        self.rollback_calls = 0

    def begin_nested(self):
        class _NoopNestedTransaction:
            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

        return _NoopNestedTransaction()

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

    async def get(self, model: object, key: object) -> object | None:
        return self.get_map.get((model, key))

    async def execute(self, *args: object, **kwargs: object) -> None:
        if args:
            self.executed.append(args[0])
        if self.execute_results:
            return self.execute_results.pop(0)
        return None

    async def rollback(self) -> None:
        self.rollback_calls += 1
        self.is_active = True


class FakeExecuteRows:
    def __init__(self, rows: list[tuple[object, ...]]) -> None:
        self._rows = list(rows)

    def all(self) -> list[tuple[object, ...]]:
        return list(self._rows)


def build_settings():
    return load_settings(env={})


@pytest.mark.asyncio
async def test_recover_session_after_nonfatal_error_rolls_back_dirty_session() -> None:
    session = FakeSession()
    session.is_active = False

    await pipeline_services._recover_session_after_nonfatal_error(
        session,
        RuntimeError("context helper failed"),
    )

    assert session.rollback_calls == 1
    assert session.is_active is True


def build_project() -> ProjectModel:
    # target_chapters kept <= PROGRESSIVE_CHAPTER_THRESHOLD so autowrite tests
    # exercising the non-progressive path aren't silently rerouted by the
    # target-based trigger in run_autowrite_pipeline.
    project = ProjectModel(
        slug="my-story",
        title="My Story",
        genre="fantasy",
        target_word_count=60000,
        target_chapters=30,
        metadata_json={
            "identity_manifest_status": "locked",
            "identity_manifest": [
                {
                    "name": "沈砚",
                    "role": "protagonist",
                    "gender": "male",
                    "pronoun_set_zh": "他",
                    "pronoun_set_en": "he/him",
                    "aliases": [],
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


def mark_project_blocked_for_structural_repair(project: ProjectModel) -> None:
    project.status = "paused"
    project.metadata_json = {
        **(project.metadata_json or {}),
        "production_paused": True,
        "production_pause_reason": "structural_repair_before_continuation",
        "generation_resume_blocked_until_repair_audit": True,
    }


def build_chapter(project_id) -> ChapterModel:
    chapter = ChapterModel(
        project_id=project_id,
        chapter_number=1,
        title="失准星图",
        chapter_goal="展示主线冲突",
        information_revealed=[],
        information_withheld=[],
        foreshadowing_actions={},
        metadata_json={},
        target_word_count=3000,
    )
    chapter.id = uuid4()
    return chapter


def build_scene(project_id, chapter_id) -> SceneCardModel:
    scene = SceneCardModel(
        project_id=project_id,
        chapter_id=chapter_id,
        scene_number=1,
        scene_type="setup",
        title="封港命令",
        time_label="第一日夜，封港前一小时",
        participants=["沈砚", "港务官"],
        purpose={"story": "抛出禁令任务", "emotion": "压迫感和抗拒"},
        entry_state={},
        exit_state={},
        key_dialogue_beats=[],
        sensory_anchors={},
        forbidden_actions=[],
        metadata_json={},
        target_word_count=1000,
    )
    scene.id = uuid4()
    return scene


def build_style(project_id) -> StyleGuideModel:
    return StyleGuideModel(
        project_id=project_id,
        pov_type="third-limited",
        tense="present",
        tone_keywords=["冷峻", "紧张"],
        prose_style="baseline",
        sentence_style="mixed",
        info_density="medium",
        dialogue_ratio=0.35,
        taboo_words=[],
        taboo_topics=[],
        reference_works=[],
        custom_rules=[],
    )


@pytest.mark.asyncio
async def test_run_scene_pipeline_blocks_when_truth_materializations_are_stale(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = build_project()
    project.metadata_json = {
        "truth_version": 2,
        "truth_updated_at": "2026-04-23T00:00:00+00:00",
        "truth_last_changed_artifact_type": "book_spec",
        "_truth_artifact_fingerprints": {},
        "_truth_change_log": [],
    }
    chapter = build_chapter(project.id)
    scene = build_scene(project.id, chapter.id)
    session = FakeSession(scalar_results=[None, None, None])

    async def fake_load_scene_identifiers(_session, _project_slug, _chapter_number, _scene_number):
        return project, chapter, scene

    monkeypatch.setattr(
        pipeline_services,
        "_load_scene_identifiers",
        fake_load_scene_identifiers,
    )

    with pytest.raises(TruthVersionStaleError):
        await pipeline_services.run_scene_pipeline(
            session,
            build_settings(),
            "my-story",
            1,
            1,
        )


def test_structural_repair_pause_guard_allows_explicit_repair() -> None:
    project = build_project()
    mark_project_blocked_for_structural_repair(project)

    with pytest.raises(pipeline_services.ProjectRepairPauseError):
        pipeline_services._assert_project_not_blocked_for_structural_repair(
            project,
            project_slug="my-story",
            operation="chapter pipeline 1",
        )

    pipeline_services._assert_project_not_blocked_for_structural_repair(
        project,
        project_slug="my-story",
        operation="chapter pipeline 1",
        allow_structural_repair=True,
    )


@pytest.mark.asyncio
async def test_run_scene_pipeline_blocks_structural_repair_pause_before_writing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = build_project()
    mark_project_blocked_for_structural_repair(project)
    chapter = build_chapter(project.id)
    scene = build_scene(project.id, chapter.id)
    session = FakeSession()

    async def fake_load_scene_identifiers(_session, _project_slug, _chapter_number, _scene_number):
        return project, chapter, scene

    async def fail_truth_guard(*args, **kwargs):
        raise AssertionError("truth guard should not run after structural pause block")

    monkeypatch.setattr(
        pipeline_services,
        "_load_scene_identifiers",
        fake_load_scene_identifiers,
    )
    monkeypatch.setattr(pipeline_services, "_enforce_truth_version_guard", fail_truth_guard)

    with pytest.raises(pipeline_services.ProjectRepairPauseError):
        await pipeline_services.run_scene_pipeline(
            session,
            build_settings(),
            "my-story",
            1,
            1,
        )


@pytest.mark.asyncio
async def test_run_scene_pipeline_blocks_on_contradiction_violation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = build_project()
    chapter = build_chapter(project.id)
    scene = build_scene(project.id, chapter.id)
    session = FakeSession(scalar_results=[None])
    settings = build_settings()
    settings.pipeline.enable_truth_version_guard = False
    settings.pipeline.enable_contradiction_checks = True
    settings.pipeline.contradiction_block_on_violation = True

    async def fake_load_scene_identifiers(_session, _project_slug, _chapter_number, _scene_number):
        return project, chapter, scene

    async def fake_build_context(*args, **kwargs):
        return SceneWriterContextPacket(
            project_id=project.id,
            project_slug=project.slug,
            chapter_id=chapter.id,
            scene_id=scene.id,
            chapter_number=1,
            scene_number=1,
            query_text="封港命令",
        )

    async def fake_run_pre_scene_contradiction_checks(*args, **kwargs):
        return ContradictionCheckResult(
            passed=False,
            violations=[
                ContradictionViolation(
                    check_type="knowledge_leak",
                    severity="error",
                    message="沈砚不能提前知道血莲印真相",
                    evidence="reader_knowledge chapter=7",
                )
            ],
            warnings=[],
            checks_run=1,
        )

    async def fake_load_identity_registry(*args, **kwargs):
        return [
            identity_guard_services.CharacterIdentity(
                name="沈砚",
                gender="male",
                pronoun_set_zh="他",
                pronoun_set_en="he/him",
            ),
            identity_guard_services.CharacterIdentity(
                name="港务官",
                gender="female",
                pronoun_set_zh="她",
                pronoun_set_en="she/her",
            ),
        ]

    monkeypatch.setattr(
        pipeline_services,
        "_load_scene_identifiers",
        fake_load_scene_identifiers,
    )
    monkeypatch.setattr(
        pipeline_services,
        "build_scene_writer_context_from_models",
        fake_build_context,
    )
    monkeypatch.setattr(
        contradiction_services,
        "run_pre_scene_contradiction_checks",
        fake_run_pre_scene_contradiction_checks,
    )
    monkeypatch.setattr(identity_guard_services, "load_identity_registry", fake_load_identity_registry)

    with pytest.raises(WriteSafetyBlockError):
        await pipeline_services.run_scene_pipeline(
            session,
            settings,
            "my-story",
            1,
            1,
        )

    workflow_runs = [obj for obj in session.added if isinstance(obj, WorkflowRunModel)]
    assert workflow_runs[0].status == "failed"
    assert workflow_runs[0].metadata_json["blocked_by_write_safety_gate"] is True
    assert workflow_runs[0].metadata_json["write_safety_gate_source"] == "contradiction"


@pytest.mark.asyncio
async def test_run_scene_pipeline_blocks_pre_draft_scene_contract_before_writer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = build_project()
    project.metadata_json = {"identity_manifest_status": "locked"}
    chapter = build_chapter(project.id)
    scene = build_scene(project.id, chapter.id)
    scene.participants = ["陌生人"]
    scene.time_label = None
    session = FakeSession()
    settings = build_settings()
    settings.pipeline.enable_truth_version_guard = False

    async def fake_load_scene_identifiers(_session, _project_slug, _chapter_number, _scene_number):
        return project, chapter, scene

    async def fake_load_current_scene_draft(_session, _scene_id):
        return None

    async def fake_build_context(*args, **kwargs):
        return SceneWriterContextPacket(
            project_id=project.id,
            project_slug=project.slug,
            chapter_id=chapter.id,
            scene_id=scene.id,
            chapter_number=1,
            scene_number=1,
            query_text="封港命令",
        )

    async def fake_load_identity_registry(*args, **kwargs):
        return [
            identity_guard_services.CharacterIdentity(
                name="沈砚",
                gender="male",
                pronoun_set_zh="他",
                pronoun_set_en="he/him",
            )
        ]

    async def fake_generate_scene_draft(*args, **kwargs):
        raise AssertionError("writer should not be called when the pre-draft contract blocks")

    monkeypatch.setattr(pipeline_services, "_load_scene_identifiers", fake_load_scene_identifiers)
    monkeypatch.setattr(pipeline_services, "_load_current_scene_draft", fake_load_current_scene_draft)
    monkeypatch.setattr(pipeline_services, "build_scene_writer_context_from_models", fake_build_context)
    monkeypatch.setattr(identity_guard_services, "load_identity_registry", fake_load_identity_registry)
    monkeypatch.setattr(pipeline_services, "generate_scene_draft", fake_generate_scene_draft)

    with pytest.raises(ValueError, match="pre_draft_scene_contract"):
        await pipeline_services.run_scene_pipeline(
            session,
            settings,
            "my-story",
            1,
            1,
        )

    workflow_runs = [obj for obj in session.added if isinstance(obj, WorkflowRunModel)]
    assert workflow_runs[0].status == "failed"
    assert workflow_runs[0].metadata_json["pre_draft_scene_contract"]["passed"] is False


@pytest.mark.asyncio
async def test_run_scene_pipeline_injects_premium_engine_blocks_into_writer_context(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    project = build_project()
    project.genre = "xianxia"
    project.metadata_json = {
        **(project.metadata_json or {}),
        "sub_genre": "凡人流修仙",
        "world_spec": {
            "world_name": "青岚界",
            "power_system": {
                "name": "灵根修行",
                "tiers": ["炼气", "筑基", "金丹"],
                "protagonist_starting_tier": "炼气十层",
            },
        },
        "cast_spec": {
            "protagonist": {
                "name": "沈砚",
                "power_tier": "炼气十层",
                "resources": [{"resource_key": "筑基丹", "amount": 1}],
                "relationships": [
                    {
                        "character": "港务官",
                        "type": "temporary ally",
                        "tension": (
                            "她要查清筑基丹流向, "
                            "沈砚必须决定是否借她的船离场。"
                        ),
                    }
                ],
            },
            "supporting_cast": [
                {
                    "name": "港务官",
                    "role": "broker",
                    "relationship_to_protagonist": "互相利用的临时盟友",
                    "evolution_arc": "从利益交换到一次有限信任",
                }
            ],
        },
        "volume_plan": [
            {
                "volume_number": 1,
                "volume_title": "入宗夺丹",
                "opening_state": {"protagonist_power_tier": "炼气十层"},
            }
        ],
        "prewrite_repair_directives": [
            "后续卷规划必须更换相邻卷主压力源；当前卷章节也要引入新的外部压力或内部代价，避免同一反派/势力连续驱动。"
        ],
        "factions": [
            {
                "name": "执法堂",
                "goal": "追回秘境中流失的筑基资源。",
                "method": "盘查、封港、追踪丹药气息。",
                "relationship_to_protagonist": "制度性压力",
                "internal_conflict": "长老要立威, 外务执事想私下分润。",
                "next_reaction": "若筑基丹消失, 会先封锁码头再查散修。",
            }
        ],
    }
    chapter = build_chapter(project.id)
    scene = build_scene(project.id, chapter.id)
    captured: dict[str, SceneWriterContextPacket] = {}

    async def fake_load_scene_identifiers(_session, _project_slug, _chapter_number, _scene_number):
        return project, chapter, scene

    async def fake_load_current_scene_draft(_session, _scene_id):
        return None

    async def fake_build_context(*args, **kwargs):
        return SceneWriterContextPacket(
            project_id=project.id,
            project_slug=project.slug,
            chapter_id=chapter.id,
            scene_id=scene.id,
            chapter_number=1,
            scene_number=1,
            query_text="封港命令",
            story_bible={
                "volume": {"volume_number": 1},
                "world_rules": [
                    {
                        "rule_code": "R-001",
                        "name": "试炼禁令",
                        "description": "秘境试炼中偷取筑基丹会引发执法堂追索。",
                        "exploitation_potential": "先藏丹后换身份离场。",
                        "future_backlash": "宗门会追查资源流向。",
                    }
                ],
            },
        )

    async def fake_generate_scene_draft(*args, **kwargs):
        captured["context"] = kwargs["context_packet"]
        draft = SceneDraftVersionModel(
            project_id=project.id,
            scene_card_id=scene.id,
            version_no=1,
            content_md="沈砚握紧筑基丹, 先退入阴影观察局势。",
            word_count=200,
            is_current=True,
            generation_params={},
        )
        draft.id = uuid4()
        draft.llm_run_id = uuid4()
        return draft

    async def fake_review_scene_draft(*args, **kwargs):
        return (
            type("ReviewResultStub", (), {"verdict": "pass", "severity_max": "low"})(),
            type("ReportStub", (), {"id": uuid4(), "llm_run_id": uuid4()})(),
            type("QualityStub", (), {"id": uuid4()})(),
            None,
        )

    async def fake_refresh_scene_knowledge(*args, **kwargs):
        return SceneKnowledgeRefreshResult(
            project_id=project.id,
            chapter_id=chapter.id,
            scene_id=scene.id,
            chapter_number=1,
            scene_number=1,
            canon_fact_ids=[],
            timeline_event_ids=[],
            canon_facts_created=0,
            canon_facts_reused=0,
            timeline_events_created=0,
            timeline_events_reused=0,
            summary_text="无新增知识",
            llm_run_id=None,
        )

    monkeypatch.setattr(pipeline_services, "_load_scene_identifiers", fake_load_scene_identifiers)
    monkeypatch.setattr(pipeline_services, "_load_current_scene_draft", fake_load_current_scene_draft)
    monkeypatch.setattr(pipeline_services, "build_scene_writer_context_from_models", fake_build_context)
    monkeypatch.setattr(pipeline_services, "generate_scene_draft", fake_generate_scene_draft)
    monkeypatch.setattr(pipeline_services, "review_scene_draft", fake_review_scene_draft)
    monkeypatch.setattr(pipeline_services, "refresh_scene_knowledge", fake_refresh_scene_knowledge)

    settings = build_settings()
    settings.output.base_dir = str(tmp_path)
    profile_path = tmp_path / project.slug / "story-bible" / "ranking-capability-profile.md"
    profile_path.parent.mkdir(parents=True)
    profile_path.write_text(
        "# 《测试书》榜单级能力 Profile\n\n"
        "- 固定入口：港口秘境。\n"
        "- 可解规则：禁令必须有破局路径和代价。\n"
        "- 单元案推动主线：每个试炼案都回收筑基资源线。\n",
        encoding="utf-8",
    )
    settings.pipeline.enable_truth_version_guard = False
    settings.pipeline.enable_contradiction_checks = False
    settings.pipeline.require_pre_draft_scene_contract = False
    settings.pipeline.enable_scene_plan_richness_gate = False

    result = await pipeline_services.run_scene_pipeline(
        FakeSession(),
        settings,
        "my-story",
        1,
        1,
        requested_by="tester",
    )

    context = captured["context"]
    assert result.final_verdict == "pass"
    assert context.ranking_capability_profile_block is not None
    assert any("[写前规划门禁]" in item for item in context.contradiction_warnings)
    assert any("更换相邻卷主压力源" in item for item in context.contradiction_warnings)
    assert "榜单级能力 Profile" in context.ranking_capability_profile_block
    assert "港口秘境" in context.ranking_capability_profile_block
    assert context.progression_context_block is not None
    assert "【进阶体系约束】" in context.progression_context_block
    assert "炼气 → 筑基 → 金丹" in context.progression_context_block
    assert "筑基丹=1" in context.progression_context_block
    assert context.decision_policy_block is not None
    assert "【主角决策策略】" in context.decision_policy_block
    assert "public_vanity_duel" in context.decision_policy_block
    assert context.rule_system_context_block is not None
    assert "【规则系统约束】" in context.rule_system_context_block
    assert "试炼禁令" in context.rule_system_context_block
    assert context.faction_ecology_context_block is not None
    assert "【阵营生态与反应压力约束】" in context.faction_ecology_context_block
    assert "执法堂" in context.faction_ecology_context_block
    assert context.relationship_agency_context_block is not None
    assert "【关系张力与主角能动性约束】" in context.relationship_agency_context_block
    assert "沈砚 -> 港务官" in context.relationship_agency_context_block
    assert "主角必须有主动选择和代价" in context.relationship_agency_context_block


@pytest.mark.asyncio
async def test_generate_scene_draft_creates_new_current_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = build_project()
    chapter = build_chapter(project.id)
    scene = build_scene(project.id, chapter.id)
    style = build_style(project.id)

    async def fake_get_project_by_slug(session, slug: str) -> ProjectModel:
        return project

    async def fake_load_identity_registry(*args, **kwargs):
        return [
            identity_guard_services.CharacterIdentity(
                name="沈砚",
                gender="male",
                pronoun_set_zh="他",
                pronoun_set_en="he/him",
            ),
            identity_guard_services.CharacterIdentity(
                name="港务官",
                gender="female",
                pronoun_set_zh="她",
                pronoun_set_en="she/her",
            ),
        ]

    monkeypatch.setattr(draft_services, "get_project_by_slug", fake_get_project_by_slug)
    monkeypatch.setattr(identity_guard_services, "load_identity_registry", fake_load_identity_registry)
    session = FakeSession(
        scalar_results=[chapter, scene, 0],
        get_map={(StyleGuideModel, project.id): style},
    )

    draft = await draft_services.generate_scene_draft(session, "my-story", 1, 1)

    assert draft.version_no == 1
    assert draft.is_current is True
    assert scene.status == "drafted"
    assert chapter.status == "drafting"
    assert any(isinstance(obj, SceneDraftVersionModel) for obj in session.added)


@pytest.mark.asyncio
async def test_assemble_chapter_draft_creates_assembled_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = build_project()
    chapter = build_chapter(project.id)
    scene = build_scene(project.id, chapter.id)
    scene_draft = SceneDraftVersionModel(
        project_id=project.id,
        scene_card_id=scene.id,
        version_no=1,
        content_md="程彻抓起挂在门后的黑色双肩包，猛地拉开拉链检查里面的物资。",
        word_count=128,
        is_current=True,
        generation_params={},
    )
    scene_draft.id = uuid4()

    async def fake_get_project_by_slug(session, slug: str) -> ProjectModel:
        return project

    monkeypatch.setattr(draft_services, "get_project_by_slug", fake_get_project_by_slug)
    session = FakeSession(
        scalar_results=[chapter, scene_draft, 0],
        scalars_results=[[scene]],
    )

    chapter_draft = await draft_services.assemble_chapter_draft(session, "my-story", 1)

    assert chapter_draft.version_no == 1
    assert chapter_draft.is_current is True
    assert chapter.current_word_count > 0
    assert any(isinstance(obj, ChapterDraftVersionModel) for obj in session.added)


@pytest.mark.asyncio
async def test_assemble_chapter_draft_blocks_cross_chapter_repetition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = build_project()
    chapter = build_chapter(project.id)
    chapter.chapter_number = 2
    scene = build_scene(project.id, chapter.id)
    repeated = "三年前试炼场崩塌，不是意外。叶长青提前改了阵法参数，宁尘的父亲冲进了崩塌区。"
    scene_draft = SceneDraftVersionModel(
        project_id=project.id,
        scene_card_id=scene.id,
        version_no=1,
        content_md=repeated,
        word_count=128,
        is_current=True,
        generation_params={},
    )
    scene_draft.id = uuid4()

    async def fake_get_project_by_slug(session, slug: str) -> ProjectModel:
        return project

    monkeypatch.setattr(draft_services, "get_project_by_slug", fake_get_project_by_slug)
    session = FakeSession(
        scalar_results=[chapter, scene_draft, 0],
        scalars_results=[[scene]],
        execute_results=[
            FakeExecuteRows([(1, f"# 第1章 暗潮试探\n\n{repeated}")]),
        ],
    )

    chapter_draft = await draft_services.assemble_chapter_draft(session, "my-story", 2)

    assert chapter_draft.is_current is True
    assert chapter.production_state == "blocked"
    assert chapter.metadata_json["write_safety_block_code"] == "CROSS_CHAPTER_REPETITION"
    assert chapter.metadata_json["post_assembly_duplicate_gate"]["finding_count"] >= 1


@pytest.mark.asyncio
async def test_review_scene_draft_creates_rewrite_task_for_low_score(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = build_project()
    chapter = build_chapter(project.id)
    scene = build_scene(project.id, chapter.id)
    scene_draft = SceneDraftVersionModel(
        project_id=project.id,
        scene_card_id=scene.id,
        version_no=1,
        content_md="短场景草稿。",
        word_count=10,
        is_current=True,
        generation_params={},
    )
    scene_draft.id = uuid4()
    style = build_style(project.id)

    async def fake_get_project_by_slug(session, slug: str) -> ProjectModel:
        return project

    monkeypatch.setattr(review_services, "get_project_by_slug", fake_get_project_by_slug)
    session = FakeSession(
        scalar_results=[chapter, scene, scene_draft],
        get_map={(StyleGuideModel, project.id): style},
    )

    result, report, quality, rewrite_task = await review_services.review_scene_draft(
        session,
        build_settings(),
        "my-story",
        1,
        1,
    )

    assert result.verdict == "rewrite"
    assert report.id is not None
    assert quality.id is not None
    assert rewrite_task is not None
    assert scene.status == "needs_rewrite"
    assert chapter.status == "revision"


@pytest.mark.asyncio
async def test_rewrite_scene_from_task_creates_new_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = build_project()
    chapter = build_chapter(project.id)
    scene = build_scene(project.id, chapter.id)
    scene_draft = SceneDraftVersionModel(
        project_id=project.id,
        scene_card_id=scene.id,
        version_no=1,
        content_md="旧版本草稿",
        word_count=120,
        is_current=True,
        generation_params={},
    )
    scene_draft.id = uuid4()
    style = build_style(project.id)
    rewrite_task = RewriteTaskModel(
        project_id=project.id,
        trigger_type="scene_review",
        trigger_source_id=scene.id,
        rewrite_strategy="scene_dialogue_conflict_expansion",
        priority=3,
        status="pending",
        instructions="补强冲突和对话",
        context_required=[],
        metadata_json={},
    )
    rewrite_task.id = uuid4()
    rewrite_task.attempts = 0

    async def fake_get_project_by_slug(session, slug: str) -> ProjectModel:
        return project

    monkeypatch.setattr(review_services, "get_project_by_slug", fake_get_project_by_slug)
    session = FakeSession(
        scalar_results=[chapter, scene, scene_draft, rewrite_task, 1],
        get_map={(StyleGuideModel, project.id): style},
    )

    new_draft, completed_task = await review_services.rewrite_scene_from_task(
        session,
        "my-story",
        1,
        1,
    )

    assert new_draft.version_no == 2
    # Rewrite fallback (when the LLM is unavailable) now preserves the
    # existing draft verbatim instead of inventing template prose. The
    # HTML comment marker attached by render_rewritten_scene_markdown is
    # stripped by sanitize_novel_markdown_content before the draft is
    # persisted — so the final stored content equals the original draft.
    # Previously this test asserted ``new_draft.word_count > scene_draft
    # .word_count``, which was implicitly relying on the template prose
    # inflation that we just removed.
    assert new_draft.content_md.strip() == "旧版本草稿"
    assert "重新被推回《" not in new_draft.content_md
    assert "third-limited" not in new_draft.content_md
    assert "rewrite-scene-fallback" not in new_draft.content_md
    assert completed_task.status == "completed"
    assert scene.status == "drafted"


@pytest.mark.asyncio
async def test_export_project_markdown_writes_artifact(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    project = build_project()
    chapter = build_chapter(project.id)
    chapter.target_word_count = 120
    chapter.status = "complete"
    chapter.production_state = "ok"
    chapter_draft = ChapterDraftVersionModel(
        project_id=project.id,
        chapter_id=chapter.id,
        version_no=1,
        content_md="# 第1章 失准星图",
        word_count=120,
        assembled_from_scene_draft_ids=[str(uuid4())],
        is_current=True,
    )
    chapter_draft.id = uuid4()

    async def fake_get_project_by_slug(session, slug: str) -> ProjectModel:
        return project

    monkeypatch.setattr(export_services, "get_project_by_slug", fake_get_project_by_slug)
    settings = build_settings()
    settings.output.base_dir = str(tmp_path / "output")
    session = FakeSession(
        scalar_results=[chapter_draft],
        scalars_results=[[chapter]],
    )

    artifact, output_path = await export_services.export_project_markdown(
        session,
        settings,
        "my-story",
    )

    assert artifact.id is not None
    assert output_path.exists() is True
    assert output_path.read_text(encoding="utf-8").startswith("# My Story")
    package_root = tmp_path / "output" / project.slug
    assert (package_root / "chapter-001.md").exists() is True
    assert (package_root / "story-bible" / "series-brief.md").exists() is True
    assert (package_root / "story-bible" / "reader-desire-map.md").exists() is True
    assert (package_root / "story-bible" / "series-bible.md").exists() is True
    assert (package_root / "story-bible" / "continuity-ledger.md").exists() is True
    assert (package_root / "story-bible" / "batch-queue.csv").exists() is True
    assert (package_root / "story-bible" / "volume-plan.csv").exists() is True


@pytest.mark.asyncio
async def test_export_project_markdown_removes_stale_chapter_files(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    project = build_project()
    chapter = build_chapter(project.id)
    chapter.target_word_count = 120
    chapter.status = "complete"
    chapter.production_state = "ok"
    chapter_draft = ChapterDraftVersionModel(
        project_id=project.id,
        chapter_id=chapter.id,
        version_no=2,
        content_md="# 第1章 新稿\n\n这一次是数据库当前稿。",
        word_count=120,
        assembled_from_scene_draft_ids=[str(uuid4())],
        is_current=True,
    )
    chapter_draft.id = uuid4()

    async def fake_get_project_by_slug(session, slug: str) -> ProjectModel:
        return project

    monkeypatch.setattr(export_services, "get_project_by_slug", fake_get_project_by_slug)
    settings = build_settings()
    settings.output.base_dir = str(tmp_path / "output")
    package_root = tmp_path / "output" / project.slug
    package_root.mkdir(parents=True)
    stale = package_root / "chapter-002.md"
    stale.write_text("# 第2章 旧稿\n\n这不再是当前稿。", encoding="utf-8")
    session = FakeSession(
        scalar_results=[chapter_draft],
        scalars_results=[[chapter]],
    )

    await export_services.export_project_markdown(session, settings, "my-story")

    assert stale.exists() is False
    assert "数据库当前稿" in (package_root / "chapter-001.md").read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_export_project_docx_writes_artifact(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    project = build_project()
    chapter = build_chapter(project.id)
    chapter.target_word_count = 120
    chapter.status = "complete"
    chapter.production_state = "ok"
    chapter_draft = ChapterDraftVersionModel(
        project_id=project.id,
        chapter_id=chapter.id,
        version_no=1,
        content_md="# 第1章 失准星图",
        word_count=120,
        assembled_from_scene_draft_ids=[str(uuid4())],
        is_current=True,
    )
    chapter_draft.id = uuid4()

    async def fake_get_project_by_slug(session, slug: str) -> ProjectModel:
        return project

    monkeypatch.setattr(export_services, "get_project_by_slug", fake_get_project_by_slug)
    settings = build_settings()
    settings.output.base_dir = str(tmp_path / "output")
    session = FakeSession(
        scalar_results=[chapter_draft],
        scalars_results=[[chapter]],
    )

    artifact, output_path = await export_services.export_project_docx(
        session,
        settings,
        "my-story",
    )

    assert artifact.id is not None
    assert output_path.exists() is True
    assert output_path.suffix == ".docx"


@pytest.mark.asyncio
async def test_export_project_epub_writes_artifact(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    project = build_project()
    chapter = build_chapter(project.id)
    chapter.target_word_count = 120
    chapter.status = "complete"
    chapter.production_state = "ok"
    chapter_draft = ChapterDraftVersionModel(
        project_id=project.id,
        chapter_id=chapter.id,
        version_no=1,
        content_md="# 第1章 失准星图",
        word_count=120,
        assembled_from_scene_draft_ids=[str(uuid4())],
        is_current=True,
    )
    chapter_draft.id = uuid4()

    async def fake_get_project_by_slug(session, slug: str) -> ProjectModel:
        return project

    monkeypatch.setattr(export_services, "get_project_by_slug", fake_get_project_by_slug)
    settings = build_settings()
    settings.output.base_dir = str(tmp_path / "output")
    session = FakeSession(
        scalar_results=[chapter_draft],
        scalars_results=[[chapter]],
    )

    artifact, output_path = await export_services.export_project_epub(
        session,
        settings,
        "my-story",
    )

    assert artifact.id is not None
    assert output_path.exists() is True
    assert output_path.suffix == ".epub"


@pytest.mark.asyncio
async def test_export_project_markdown_blocks_unfinished_placeholder_content(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    project = build_project()
    chapter = build_chapter(project.id)
    chapter.target_word_count = 120
    chapter.status = "complete"
    chapter.production_state = "ok"
    chapter_draft = ChapterDraftVersionModel(
        project_id=project.id,
        chapter_id=chapter.id,
        version_no=1,
        content_md="# 第1章 失准星图\n\n盟友甲在仓库门口等他。",
        word_count=120,
        assembled_from_scene_draft_ids=[],
        is_current=True,
    )
    chapter_draft.id = uuid4()

    async def fake_get_project_by_slug(session, slug: str) -> ProjectModel:
        return project

    monkeypatch.setattr(export_services, "get_project_by_slug", fake_get_project_by_slug)
    settings = build_settings()
    settings.output.base_dir = str(tmp_path / "output")
    session = FakeSession(
        scalar_results=[chapter_draft],
        scalars_results=[[chapter]],
    )

    with pytest.raises(ValueError, match="盟友甲"):
        await export_services.export_project_markdown(
            session,
            settings,
            "my-story",
        )


def test_publication_gate_blocks_unapproved_chapter_state() -> None:
    project = build_project()
    chapter = build_chapter(project.id)
    chapter.chapter_number = 30
    chapter.status = "drafting"
    chapter.production_state = "pending"
    chapter_draft = ChapterDraftVersionModel(
        project_id=project.id,
        chapter_id=chapter.id,
        version_no=1,
        content_md="# 第30章 沉渊绞杀\n\n宁尘向前走了一步。",
        word_count=20,
        assembled_from_scene_draft_ids=[],
        is_current=True,
    )

    blockers = export_services.collect_publication_blockers(project, [(chapter, chapter_draft)])

    assert any("不是可发布状态" in blocker for blocker in blockers)
    assert any("不是 ok" in blocker for blocker in blockers)


def test_publication_gate_allows_repaired_revision_ok_chapter() -> None:
    project = build_project()
    project.language = "en"
    chapter = build_chapter(project.id)
    chapter.chapter_number = 30
    chapter.status = "revision"
    chapter.production_state = "ok"
    chapter_draft = ChapterDraftVersionModel(
        project_id=project.id,
        chapter_id=chapter.id,
        version_no=1,
        content_md="# Chapter 30\n\n" + ("clean prose " * 1700),
        word_count=3400,
        assembled_from_scene_draft_ids=[str(uuid4())],
        is_current=True,
    )

    blockers = export_services.collect_publication_blockers(project, [(chapter, chapter_draft)])

    assert blockers == []


def test_publication_gate_blocks_cross_chapter_repeated_paragraph() -> None:
    project = build_project()
    chapter_29 = build_chapter(project.id)
    chapter_29.chapter_number = 29
    chapter_29.title = "冷锋死线"
    chapter_29.status = "complete"
    chapter_29.production_state = "ok"
    chapter_30 = build_chapter(project.id)
    chapter_30.chapter_number = 30
    chapter_30.title = "沉渊绞杀"
    chapter_30.status = "complete"
    chapter_30.production_state = "ok"
    repeated = "三年前试炼场崩塌，不是意外。叶长青提前改了阵法参数，你爹为了救人，冲进了崩塌区。"
    draft_29 = ChapterDraftVersionModel(
        project_id=project.id,
        chapter_id=chapter_29.id,
        version_no=1,
        content_md=f"# 第29章 冷锋死线\n\n{repeated}\n\n宁尘没有立刻回答。",
        word_count=60,
        assembled_from_scene_draft_ids=[],
        is_current=True,
    )
    draft_30 = ChapterDraftVersionModel(
        project_id=project.id,
        chapter_id=chapter_30.id,
        version_no=1,
        content_md=f"# 第30章 沉渊绞杀\n\n{repeated}\n\n陆沉的脸色变得难看。",
        word_count=60,
        assembled_from_scene_draft_ids=[],
        is_current=True,
    )

    blockers = export_services.collect_publication_blockers(
        project,
        [(chapter_30, draft_30)],
        comparison_payloads=[(chapter_29, draft_29), (chapter_30, draft_30)],
    )

    assert any("跨章段落重复" in blocker for blocker in blockers)


@pytest.mark.asyncio
async def test_export_project_markdown_blocks_cross_chapter_repetition(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    project = build_project()
    chapter_29 = build_chapter(project.id)
    chapter_29.chapter_number = 29
    chapter_29.title = "冷锋死线"
    chapter_29.status = "complete"
    chapter_29.production_state = "ok"
    chapter_30 = build_chapter(project.id)
    chapter_30.chapter_number = 30
    chapter_30.title = "沉渊绞杀"
    chapter_30.status = "complete"
    chapter_30.production_state = "ok"
    repeated = "周长老的手心滚烫，灵力顺着经脉一路向下，直直撞向丹田深处那枚沉睡的道种。"
    draft_29 = ChapterDraftVersionModel(
        project_id=project.id,
        chapter_id=chapter_29.id,
        version_no=1,
        content_md=f"# 第29章 冷锋死线\n\n{repeated}\n\n宁尘听见风声贴着耳侧刮过。",
        word_count=80,
        assembled_from_scene_draft_ids=[],
        is_current=True,
    )
    draft_30 = ChapterDraftVersionModel(
        project_id=project.id,
        chapter_id=chapter_30.id,
        version_no=1,
        content_md=f"# 第30章 沉渊绞杀\n\n{repeated}\n\n陆沉把纸条攥进掌心。",
        word_count=80,
        assembled_from_scene_draft_ids=[],
        is_current=True,
    )

    async def fake_get_project_by_slug(session, slug: str) -> ProjectModel:
        return project

    monkeypatch.setattr(export_services, "get_project_by_slug", fake_get_project_by_slug)
    settings = build_settings()
    settings.output.base_dir = str(tmp_path / "output")
    session = FakeSession(
        scalar_results=[draft_29, draft_30],
        scalars_results=[[chapter_29, chapter_30]],
    )

    with pytest.raises(ValueError, match="跨章段落重复"):
        await export_services.export_project_markdown(
            session,
            settings,
            "my-story",
        )


@pytest.mark.asyncio
async def test_generate_scene_draft_with_settings_records_llm_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = build_project()
    chapter = build_chapter(project.id)
    scene = build_scene(project.id, chapter.id)
    style = build_style(project.id)

    async def fake_get_project_by_slug(session, slug: str) -> ProjectModel:
        return project

    monkeypatch.setattr(draft_services, "get_project_by_slug", fake_get_project_by_slug)
    session = FakeSession(
        scalar_results=[chapter, scene, 0],
        get_map={(StyleGuideModel, project.id): style},
    )

    settings = build_settings()
    settings.llm.mock = True

    async def fake_load_identity_registry(*args, **kwargs):
        return [
            identity_guard_services.CharacterIdentity(
                name="沈砚",
                gender="male",
                pronoun_set_zh="他",
                pronoun_set_en="he/him",
            ),
            identity_guard_services.CharacterIdentity(
                name="港务官",
                gender="female",
                pronoun_set_zh="她",
                pronoun_set_en="she/her",
            ),
        ]

    monkeypatch.setattr(identity_guard_services, "load_identity_registry", fake_load_identity_registry)
    draft = await draft_services.generate_scene_draft(
        session,
        "my-story",
        1,
        1,
        settings=settings,
    )

    assert draft.llm_run_id is not None
    assert any(isinstance(obj, LlmRunModel) for obj in session.added)


@pytest.mark.asyncio
async def test_generate_scene_draft_direct_settings_injects_premium_blocks(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    project = build_project()
    project.genre = "xianxia"
    project.metadata_json = {
        **(project.metadata_json or {}),
        "sub_genre": "凡人流修仙",
        "world_spec": {
            "world_name": "青岚界",
            "power_system": {
                "name": "灵根修行",
                "tiers": ["炼气", "筑基"],
                "protagonist_starting_tier": "炼气十层",
            },
        },
        "cast_spec": {
            "protagonist": {
                "name": "沈砚",
                "power_tier": "炼气十层",
                "resources": [{"resource_key": "筑基丹", "amount": 1}],
                "relationships": [
                    {
                        "character": "港务官",
                        "type": "temporary ally",
                        "tension": (
                            "她要查清筑基丹流向, "
                            "沈砚必须决定是否借她的船离场。"
                        ),
                    }
                ],
            },
            "supporting_cast": [
                {
                    "name": "港务官",
                    "role": "broker",
                    "relationship_to_protagonist": "互相利用的临时盟友",
                    "evolution_arc": "从利益交换到一次有限信任",
                }
            ],
        },
        "factions": [
            {
                "name": "执法堂",
                "goal": "追回秘境中流失的筑基资源。",
                "method": "盘查、封港、追踪丹药气息。",
                "relationship_to_protagonist": "制度性压力",
                "internal_conflict": "长老要立威, 外务执事想私下分润。",
                "next_reaction": "若筑基丹消失, 会先封锁码头再查散修。",
            }
        ],
    }
    chapter = build_chapter(project.id)
    scene = build_scene(project.id, chapter.id)
    style = build_style(project.id)
    captured: dict[str, SceneWriterContextPacket] = {}

    async def fake_get_project_by_slug(session, slug: str) -> ProjectModel:
        return project

    async def fake_load_identity_registry(*args, **kwargs):
        return [
            identity_guard_services.CharacterIdentity(
                name="沈砚",
                gender="male",
                pronoun_set_zh="他",
                pronoun_set_en="he/him",
            ),
            identity_guard_services.CharacterIdentity(
                name="港务官",
                gender="female",
                pronoun_set_zh="她",
                pronoun_set_en="she/her",
            ),
        ]

    async def fake_build_context(*args, **kwargs):
        packet = SceneWriterContextPacket(
            project_id=project.id,
            project_slug=project.slug,
            chapter_id=chapter.id,
            scene_id=scene.id,
            chapter_number=1,
            scene_number=1,
            query_text="封港命令",
            story_bible={
                "volume": {"volume_number": 1},
                "world_rules": [
                    {
                        "rule_code": "R-001",
                        "name": "试炼禁令",
                        "description": "秘境偷取筑基丹会触发执法堂追索。",
                        "story_consequence": "主角不能正面带丹离开秘境。",
                        "exploitation_potential": "先藏丹后换身份离场。",
                        "future_backlash": "宗门会追查资源流向。",
                    }
                ],
            },
        )
        captured["context"] = packet
        return packet

    monkeypatch.setattr(draft_services, "get_project_by_slug", fake_get_project_by_slug)
    monkeypatch.setattr(identity_guard_services, "load_identity_registry", fake_load_identity_registry)
    monkeypatch.setattr(draft_services, "build_scene_writer_context_from_models", fake_build_context)

    session = FakeSession(
        scalar_results=[chapter, scene, 0],
        get_map={(StyleGuideModel, project.id): style},
    )
    settings = build_settings()
    settings.llm.mock = True
    settings.output.base_dir = str(tmp_path)
    profile_path = tmp_path / project.slug / "story-bible" / "ranking-capability-profile.md"
    profile_path.parent.mkdir(parents=True)
    profile_path.write_text(
        "# 《测试书》榜单级能力 Profile\n\n"
        "- 固定入口：港口秘境。\n"
        "- 可解规则：禁令必须有破局路径和代价。\n",
        encoding="utf-8",
    )

    await draft_services.generate_scene_draft(
        session,
        "my-story",
        1,
        1,
        settings=settings,
    )

    context = captured["context"]
    assert context.ranking_capability_profile_block is not None
    assert "港口秘境" in context.ranking_capability_profile_block
    assert context.progression_context_block is not None
    assert "炼气 → 筑基" in context.progression_context_block
    assert context.decision_policy_block is not None
    assert "public_vanity_duel" in context.decision_policy_block
    assert context.rule_system_context_block is not None
    assert "试炼禁令" in context.rule_system_context_block
    assert context.faction_ecology_context_block is not None
    assert "执法堂" in context.faction_ecology_context_block
    assert context.relationship_agency_context_block is not None
    assert "沈砚 -> 港务官" in context.relationship_agency_context_block


@pytest.mark.asyncio
async def test_generate_scene_draft_direct_call_blocks_pre_draft_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = build_project()
    project.metadata_json = {"identity_manifest_status": "locked"}
    chapter = build_chapter(project.id)
    scene = build_scene(project.id, chapter.id)
    scene.participants = ["陌生人"]
    scene.time_label = None

    async def fake_get_project_by_slug(session: object, slug: str) -> ProjectModel:
        return project

    async def fake_load_identity_registry(*args, **kwargs):
        return [
            identity_guard_services.CharacterIdentity(
                name="沈砚",
                gender="male",
                pronoun_set_zh="他",
                pronoun_set_en="he/him",
            )
        ]

    monkeypatch.setattr(draft_services, "get_project_by_slug", fake_get_project_by_slug)
    monkeypatch.setattr(identity_guard_services, "load_identity_registry", fake_load_identity_registry)

    session = FakeSession(scalar_results=[chapter, scene])
    settings = build_settings()
    settings.llm.mock = True

    with pytest.raises(ValueError, match="pre_draft_scene_contract"):
        await draft_services.generate_scene_draft(
            session,
            "my-story",
            1,
            1,
            settings=settings,
        )

    assert scene.metadata_json["pre_draft_scene_contract"]["passed"] is False


@pytest.mark.asyncio
async def test_run_scene_pipeline_rewrites_until_review_passes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = build_project()
    chapter = build_chapter(project.id)
    scene = build_scene(project.id, chapter.id)
    initial_draft = SceneDraftVersionModel(
        project_id=project.id,
        scene_card_id=scene.id,
        version_no=1,
        content_md="初始草稿",
        word_count=120,
        is_current=True,
        generation_params={},
    )
    initial_draft.id = uuid4()
    initial_draft.llm_run_id = uuid4()

    rewritten_draft = SceneDraftVersionModel(
        project_id=project.id,
        scene_card_id=scene.id,
        version_no=2,
        content_md="重写草稿",
        word_count=820,
        is_current=True,
        generation_params={},
    )
    rewritten_draft.id = uuid4()
    rewritten_draft.llm_run_id = uuid4()

    first_report = type("ReportStub", (), {"id": uuid4(), "llm_run_id": uuid4()})()
    second_report = type("ReportStub", (), {"id": uuid4(), "llm_run_id": uuid4()})()
    quality_a = type("QualityStub", (), {"id": uuid4()})()
    quality_b = type("QualityStub", (), {"id": uuid4()})()
    rewrite_task = type("RewriteTaskStub", (), {"id": uuid4(), "status": "pending"})()

    async def fake_load_scene_identifiers(session, project_slug, chapter_number, scene_number):
        return project, chapter, scene

    async def fake_load_current_scene_draft(session, scene_id):
        return initial_draft

    async def fake_review_scene_draft(
        session,
        settings,
        project_slug,
        chapter_number,
        scene_number,
        **kwargs,
    ):
        calls = getattr(fake_review_scene_draft, "calls", 0) + 1
        fake_review_scene_draft.calls = calls
        if calls == 1:
            return (
                type(
                    "ReviewResultStub",
                    (),
                    {"verdict": "rewrite", "severity_max": "medium"},
                )(),
                first_report,
                quality_a,
                rewrite_task,
            )
        return (
            type(
                "ReviewResultStub",
                (),
                {"verdict": "pass", "severity_max": "low"},
            )(),
            second_report,
            quality_b,
            None,
        )

    async def fake_rewrite_scene_from_task(
        session,
        project_slug,
        chapter_number,
        scene_number,
        **kwargs,
    ):
        return rewritten_draft, rewrite_task

    async def fake_refresh_scene_knowledge(
        session,
        settings,
        project_slug,
        chapter_number,
        scene_number,
        **kwargs,
    ):
        return SceneKnowledgeRefreshResult(
            project_id=project.id,
            chapter_id=chapter.id,
            scene_id=scene.id,
            chapter_number=chapter.chapter_number,
            scene_number=scene.scene_number,
            canon_fact_ids=[uuid4(), uuid4()],
            timeline_event_ids=[uuid4()],
            canon_facts_created=2,
            canon_facts_reused=0,
            timeline_events_created=1,
            timeline_events_reused=0,
            summary_text="知识层摘要",
            llm_run_id=uuid4(),
        )

    monkeypatch.setattr(pipeline_services, "_load_scene_identifiers", fake_load_scene_identifiers)
    monkeypatch.setattr(pipeline_services, "_load_current_scene_draft", fake_load_current_scene_draft)
    monkeypatch.setattr(pipeline_services, "review_scene_draft", fake_review_scene_draft)
    monkeypatch.setattr(pipeline_services, "rewrite_scene_from_task", fake_rewrite_scene_from_task)
    monkeypatch.setattr(pipeline_services, "refresh_scene_knowledge", fake_refresh_scene_knowledge)

    session = FakeSession()
    result = await pipeline_services.run_scene_pipeline(
        session,
        build_settings(),
        "my-story",
        1,
        1,
        requested_by="tester",
    )

    workflow_runs = [obj for obj in session.added if isinstance(obj, WorkflowRunModel)]
    workflow_steps = [obj for obj in session.added if isinstance(obj, WorkflowStepRunModel)]

    assert result.final_verdict == "pass"
    assert result.rewrite_iterations == 1
    assert result.review_iterations == 2
    assert result.canon_fact_count == 2
    assert result.timeline_event_count == 1
    assert result.current_draft_id == rewritten_draft.id
    assert result.requires_human_review is False
    assert len(workflow_runs) == 1
    assert workflow_runs[0].status == "completed"
    assert len(workflow_steps) == 5


@pytest.mark.asyncio
async def test_run_scene_pipeline_stops_after_stalled_rewrite(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = build_project()
    chapter = build_chapter(project.id)
    scene = build_scene(project.id, chapter.id)
    initial_draft = SceneDraftVersionModel(
        project_id=project.id,
        scene_card_id=scene.id,
        version_no=1,
        content_md="初始草稿",
        word_count=120,
        is_current=True,
        generation_params={},
    )
    initial_draft.id = uuid4()
    initial_draft.llm_run_id = uuid4()

    rewritten_draft = SceneDraftVersionModel(
        project_id=project.id,
        scene_card_id=scene.id,
        version_no=2,
        content_md="重写一次后的草稿",
        word_count=160,
        is_current=True,
        generation_params={},
    )
    rewritten_draft.id = uuid4()
    rewritten_draft.llm_run_id = uuid4()

    rewrite_task = type("RewriteTaskStub", (), {"id": uuid4(), "status": "pending"})()
    report_a = type("ReportStub", (), {"id": uuid4(), "llm_run_id": None})()
    report_b = type("ReportStub", (), {"id": uuid4(), "llm_run_id": None})()
    quality_a = type("QualityStub", (), {"id": uuid4()})()
    quality_b = type("QualityStub", (), {"id": uuid4()})()

    async def fake_load_scene_identifiers(session, project_slug, chapter_number, scene_number):
        return project, chapter, scene

    async def fake_load_current_scene_draft(session, scene_id):
        return initial_draft

    async def fake_review_scene_draft(
        session,
        settings,
        project_slug,
        chapter_number,
        scene_number,
        **kwargs,
    ):
        calls = getattr(fake_review_scene_draft, "calls", 0) + 1
        fake_review_scene_draft.calls = calls
        if calls == 1:
            return (
                type(
                    "ReviewResultStub",
                    (),
                    {
                        "verdict": "rewrite",
                        "severity_max": "medium",
                        "scores": type("ScoreStub", (), {"overall": 0.50})(),
                        "rewrite_instructions": "补强冲突和尾钩",
                    },
                )(),
                report_a,
                quality_a,
                rewrite_task,
            )
        return (
            type(
                "ReviewResultStub",
                (),
                {
                    "verdict": "rewrite",
                    "severity_max": "medium",
                    "scores": type("ScoreStub", (), {"overall": 0.51})(),
                    "rewrite_instructions": "补强冲突和尾钩",
                },
            )(),
            report_b,
            quality_b,
            rewrite_task,
        )

    async def fake_rewrite_scene_from_task(
        session,
        project_slug,
        chapter_number,
        scene_number,
        **kwargs,
    ):
        calls = getattr(fake_rewrite_scene_from_task, "calls", 0) + 1
        fake_rewrite_scene_from_task.calls = calls
        return rewritten_draft, rewrite_task

    monkeypatch.setattr(pipeline_services, "_load_scene_identifiers", fake_load_scene_identifiers)
    monkeypatch.setattr(pipeline_services, "_load_current_scene_draft", fake_load_current_scene_draft)
    monkeypatch.setattr(pipeline_services, "review_scene_draft", fake_review_scene_draft)
    monkeypatch.setattr(pipeline_services, "rewrite_scene_from_task", fake_rewrite_scene_from_task)

    session = FakeSession()
    settings = build_settings()
    settings.quality.min_scene_rewrite_improvement = 0.03
    settings.pipeline.accept_on_stall = False

    result = await pipeline_services.run_scene_pipeline(
        session,
        settings,
        "my-story",
        1,
        1,
        requested_by="tester",
    )

    workflow_runs = [obj for obj in session.added if isinstance(obj, WorkflowRunModel)]

    assert result.final_verdict == "rewrite"
    assert result.review_iterations == 2
    assert result.rewrite_iterations == 1
    assert result.requires_human_review is True
    assert getattr(fake_rewrite_scene_from_task, "calls", 0) == 1
    assert workflow_runs[0].status == "waiting_human"
    assert workflow_runs[0].metadata_json["stalled_rewrite"] is True


@pytest.mark.asyncio
async def test_run_chapter_pipeline_assembles_and_exports(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    project = build_project()
    chapter = build_chapter(project.id)
    scene = build_scene(project.id, chapter.id)
    chapter_draft = ChapterDraftVersionModel(
        project_id=project.id,
        chapter_id=chapter.id,
        version_no=1,
        content_md="# 第1章 失准星图",
        word_count=1200,
        assembled_from_scene_draft_ids=[],
        is_current=True,
    )
    chapter_draft.id = uuid4()
    export_artifact = ExportArtifactModel(
        project_id=project.id,
        export_type="markdown",
        source_scope="chapter",
        source_id=chapter.id,
        storage_uri=str(tmp_path / "output" / "chapter-001.md"),
        checksum="a" * 64,
        version_label="chapter-001-v1",
    )
    export_artifact.id = uuid4()
    report = type("ChapterReportStub", (), {"id": uuid4(), "llm_run_id": uuid4()})()
    quality = type("ChapterQualityStub", (), {"id": uuid4()})()

    async def fake_get_project_by_slug(session, slug: str) -> ProjectModel:
        return project

    async def fake_run_scene_pipeline(
        session,
        settings,
        project_slug,
        chapter_number,
        scene_number,
        **kwargs,
    ):
        return pipeline_services.ScenePipelineResult(
            workflow_run_id=uuid4(),
            project_id=project.id,
            chapter_id=chapter.id,
            scene_id=scene.id,
            chapter_number=chapter.chapter_number,
            scene_number=scene.scene_number,
            current_draft_id=uuid4(),
            current_draft_version_no=2,
            final_verdict="pass",
            review_report_id=uuid4(),
            quality_score_id=uuid4(),
            review_iterations=2,
            rewrite_iterations=1,
            requires_human_review=False,
            llm_run_ids=[],
        )

    async def fake_assemble_chapter_draft(session, project_slug: str, chapter_number: int, *, settings=None):
        return chapter_draft

    async def fake_export_chapter_markdown(
        session,
        settings,
        project_slug: str,
        chapter_number: int,
        **kwargs,
    ):
        output_path = tmp_path / "output" / "chapter-001.md"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(chapter_draft.content_md, encoding="utf-8")
        return export_artifact, output_path

    async def fake_review_chapter_draft(
        session,
        settings,
        project_slug,
        chapter_number,
        **kwargs,
    ):
        return (
            type(
                "ChapterReviewResultStub",
                (),
                {"verdict": "pass", "severity_max": "low"},
            )(),
            report,
            quality,
            None,
        )

    monkeypatch.setattr(pipeline_services, "get_project_by_slug", fake_get_project_by_slug)
    monkeypatch.setattr(pipeline_services, "run_scene_pipeline", fake_run_scene_pipeline)
    monkeypatch.setattr(pipeline_services, "assemble_chapter_draft", fake_assemble_chapter_draft)
    monkeypatch.setattr(pipeline_services, "export_chapter_markdown", fake_export_chapter_markdown)
    monkeypatch.setattr(pipeline_services, "review_chapter_draft", fake_review_chapter_draft)

    session = FakeSession(
        scalar_results=[chapter],
        scalars_results=[[scene]],
    )
    result = await pipeline_services.run_chapter_pipeline(
        session,
        build_settings(),
        "my-story",
        1,
        requested_by="tester",
        export_markdown=True,
    )

    workflow_runs = [obj for obj in session.added if isinstance(obj, WorkflowRunModel)]

    assert result.chapter_draft_id == chapter_draft.id
    assert result.export_artifact_id == export_artifact.id
    assert result.output_path is not None
    assert result.requires_human_review is False
    assert len(result.scene_results) == 1
    assert len(workflow_runs) == 1
    assert workflow_runs[0].status == "completed"


@pytest.mark.asyncio
async def test_run_chapter_pipeline_exports_checkpoint_when_scene_needs_human_review(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    project = build_project()
    chapter = build_chapter(project.id)
    scene = build_scene(project.id, chapter.id)
    chapter_draft = ChapterDraftVersionModel(
        project_id=project.id,
        chapter_id=chapter.id,
        version_no=1,
        content_md="# 第1章 失准星图\n\n场景草稿待人工复核。",
        word_count=900,
        assembled_from_scene_draft_ids=[],
        is_current=True,
    )
    chapter_draft.id = uuid4()
    export_artifact = ExportArtifactModel(
        project_id=project.id,
        export_type="markdown",
        source_scope="chapter",
        source_id=chapter.id,
        storage_uri=str(tmp_path / "output" / "chapter-001.md"),
        checksum="c" * 64,
        version_label="chapter-001-v1",
    )
    export_artifact.id = uuid4()

    async def fake_get_project_by_slug(session, slug: str) -> ProjectModel:
        return project

    async def fake_run_scene_pipeline(
        session,
        settings,
        project_slug,
        chapter_number,
        scene_number,
        **kwargs,
    ):
        return pipeline_services.ScenePipelineResult(
            workflow_run_id=uuid4(),
            project_id=project.id,
            chapter_id=chapter.id,
            scene_id=scene.id,
            chapter_number=chapter.chapter_number,
            scene_number=scene.scene_number,
            current_draft_id=uuid4(),
            current_draft_version_no=1,
            final_verdict="rewrite",
            review_report_id=uuid4(),
            quality_score_id=uuid4(),
            rewrite_task_id=uuid4(),
            review_iterations=2,
            rewrite_iterations=1,
            requires_human_review=True,
            llm_run_ids=[],
        )

    async def fake_assemble_chapter_draft(session, project_slug: str, chapter_number: int, *, settings=None):
        return chapter_draft

    async def fake_export_chapter_markdown(
        session,
        settings,
        project_slug: str,
        chapter_number: int,
        **kwargs,
    ):
        output_path = tmp_path / "output" / "chapter-001.md"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(chapter_draft.content_md, encoding="utf-8")
        return export_artifact, output_path

    monkeypatch.setattr(pipeline_services, "get_project_by_slug", fake_get_project_by_slug)
    monkeypatch.setattr(pipeline_services, "run_scene_pipeline", fake_run_scene_pipeline)
    monkeypatch.setattr(pipeline_services, "assemble_chapter_draft", fake_assemble_chapter_draft)
    monkeypatch.setattr(pipeline_services, "export_chapter_markdown", fake_export_chapter_markdown)

    session = FakeSession(
        scalar_results=[chapter],
        scalars_results=[[scene]],
    )
    result = await pipeline_services.run_chapter_pipeline(
        session,
        build_settings(),
        "my-story",
        1,
        requested_by="tester",
        export_markdown=True,
    )

    assert result.requires_human_review is True
    assert result.chapter_draft_id == chapter_draft.id
    assert result.export_artifact_id == export_artifact.id
    assert result.output_path is not None


@pytest.mark.asyncio
async def test_run_chapter_pipeline_repairs_scene_block_before_assembly(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    project = build_project()
    chapter = build_chapter(project.id)
    scene = build_scene(project.id, chapter.id)
    chapter_draft = ChapterDraftVersionModel(
        project_id=project.id,
        chapter_id=chapter.id,
        version_no=1,
        content_md="# 第1章 失准星图\n\n修复后章节。",
        word_count=1200,
        assembled_from_scene_draft_ids=[],
        is_current=True,
    )
    chapter_draft.id = uuid4()
    export_artifact = ExportArtifactModel(
        project_id=project.id,
        export_type="markdown",
        source_scope="chapter",
        source_id=chapter.id,
        storage_uri=str(tmp_path / "output" / "chapter-001.md"),
        checksum="c" * 64,
        version_label="chapter-001-v1",
    )
    export_artifact.id = uuid4()
    report = type("ChapterReportStub", (), {"id": uuid4(), "llm_run_id": uuid4()})()
    quality = type("ChapterQualityStub", (), {"id": uuid4()})()
    scene_calls = {"count": 0}

    async def fake_get_project_by_slug(session, slug: str) -> ProjectModel:
        return project

    async def fake_run_scene_pipeline(
        session,
        settings,
        project_slug,
        chapter_number,
        scene_number,
        **kwargs,
    ):
        scene_calls["count"] += 1
        if scene_calls["count"] == 1:
            raise WriteSafetyBlockError(
                "blocked",
                findings=[
                    WriteSafetyFinding(
                        source="contradiction",
                        code="character_resurrection",
                        severity="critical",
                        message="dead character appeared",
                    )
                ],
            )
        return pipeline_services.ScenePipelineResult(
            workflow_run_id=uuid4(),
            project_id=project.id,
            chapter_id=chapter.id,
            scene_id=scene.id,
            chapter_number=chapter.chapter_number,
            scene_number=scene.scene_number,
            current_draft_id=uuid4(),
            current_draft_version_no=1,
            final_verdict="pass",
            review_report_id=uuid4(),
            quality_score_id=uuid4(),
            review_iterations=1,
            rewrite_iterations=0,
            requires_human_review=False,
            llm_run_ids=[],
        )

    async def fake_prepare_auto_repair(session, *, project, chapter, repairable_codes, attempt_number=1):
        chapter.production_state = "pending"
        scene.status = "needs_rewrite"
        return True, ("character_resurrection",)

    async def fake_assemble_chapter_draft(session, project_slug: str, chapter_number: int, *, settings=None):
        assert scene_calls["count"] == 2
        return chapter_draft

    async def fake_export_chapter_markdown(
        session,
        settings,
        project_slug: str,
        chapter_number: int,
        **kwargs,
    ):
        output_path = tmp_path / "output" / "chapter-001.md"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(chapter_draft.content_md, encoding="utf-8")
        return export_artifact, output_path

    async def fake_review_chapter_draft(
        session,
        settings,
        project_slug,
        chapter_number,
        **kwargs,
    ):
        return (
            type("ChapterReviewResultStub", (), {"verdict": "pass", "severity_max": "low"})(),
            report,
            quality,
            None,
        )

    monkeypatch.setattr(pipeline_services, "get_project_by_slug", fake_get_project_by_slug)
    monkeypatch.setattr(pipeline_services, "run_scene_pipeline", fake_run_scene_pipeline)
    monkeypatch.setattr(pipeline_services, "assemble_chapter_draft", fake_assemble_chapter_draft)
    monkeypatch.setattr(pipeline_services, "export_chapter_markdown", fake_export_chapter_markdown)
    monkeypatch.setattr(pipeline_services, "review_chapter_draft", fake_review_chapter_draft)
    monkeypatch.setattr(
        "bestseller.services.drafts.maybe_prepare_chapter_auto_repair",
        fake_prepare_auto_repair,
    )

    session = FakeSession(
        scalar_results=[chapter],
        scalars_results=[[scene], [scene]],
    )
    result = await pipeline_services.run_chapter_pipeline(
        session,
        build_settings(),
        "my-story",
        1,
        requested_by="tester",
        export_markdown=True,
    )

    assert result.chapter_draft_id == chapter_draft.id
    assert scene_calls["count"] == 2


@pytest.mark.asyncio
async def test_run_chapter_pipeline_rewrites_until_review_passes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    project = build_project()
    chapter = build_chapter(project.id)
    scene = build_scene(project.id, chapter.id)
    initial_draft = ChapterDraftVersionModel(
        project_id=project.id,
        chapter_id=chapter.id,
        version_no=1,
        content_md="# 第1章 失准星图\n\n## 场景 1：封港命令",
        word_count=900,
        assembled_from_scene_draft_ids=[],
        is_current=True,
    )
    initial_draft.id = uuid4()
    rewritten_draft = ChapterDraftVersionModel(
        project_id=project.id,
        chapter_id=chapter.id,
        version_no=2,
        content_md="# 第1章 失准星图\n\n## 场景 1：封港命令\n\n章节重写完成。",
        word_count=1800,
        assembled_from_scene_draft_ids=[],
        is_current=True,
    )
    rewritten_draft.id = uuid4()
    first_report = type("ChapterReportStub", (), {"id": uuid4(), "llm_run_id": uuid4()})()
    second_report = type("ChapterReportStub", (), {"id": uuid4(), "llm_run_id": uuid4()})()
    quality_a = type("ChapterQualityStub", (), {"id": uuid4()})()
    quality_b = type("ChapterQualityStub", (), {"id": uuid4()})()
    rewrite_task = type("ChapterRewriteTaskStub", (), {"id": uuid4(), "status": "pending"})()
    export_artifact = ExportArtifactModel(
        project_id=project.id,
        export_type="markdown",
        source_scope="chapter",
        source_id=chapter.id,
        storage_uri=str(tmp_path / "output" / "chapter-001.md"),
        checksum="b" * 64,
        version_label="chapter-001-v2",
    )
    export_artifact.id = uuid4()

    async def fake_get_project_by_slug(session, slug: str) -> ProjectModel:
        return project

    async def fake_run_scene_pipeline(
        session,
        settings,
        project_slug,
        chapter_number,
        scene_number,
        **kwargs,
    ):
        return pipeline_services.ScenePipelineResult(
            workflow_run_id=uuid4(),
            project_id=project.id,
            chapter_id=chapter.id,
            scene_id=scene.id,
            chapter_number=chapter.chapter_number,
            scene_number=scene.scene_number,
            current_draft_id=uuid4(),
            current_draft_version_no=2,
            final_verdict="pass",
            review_report_id=uuid4(),
            quality_score_id=uuid4(),
            review_iterations=2,
            rewrite_iterations=1,
            requires_human_review=False,
            llm_run_ids=[],
        )

    async def fake_assemble_chapter_draft(session, project_slug: str, chapter_number: int, *, settings=None):
        calls = getattr(fake_assemble_chapter_draft, "calls", 0) + 1
        fake_assemble_chapter_draft.calls = calls
        return initial_draft if calls == 1 else rewritten_draft

    async def fake_review_chapter_draft(
        session,
        settings,
        project_slug,
        chapter_number,
        **kwargs,
    ):
        calls = getattr(fake_review_chapter_draft, "calls", 0) + 1
        fake_review_chapter_draft.calls = calls
        if calls == 1:
            return (
                type(
                    "ChapterReviewResultStub",
                    (),
                    {"verdict": "rewrite", "severity_max": "medium"},
                )(),
                first_report,
                quality_a,
                rewrite_task,
            )
        return (
            type(
                "ChapterReviewResultStub",
                (),
                {"verdict": "pass", "severity_max": "low"},
            )(),
            second_report,
            quality_b,
            None,
        )

    async def fake_rewrite_chapter_from_task(
        session,
        project_slug,
        chapter_number,
        **kwargs,
    ):
        rewrite_task.status = "completed"
        return rewritten_draft, rewrite_task

    async def fake_export_chapter_markdown(
        session,
        settings,
        project_slug: str,
        chapter_number: int,
        **kwargs,
    ):
        output_path = tmp_path / "output" / "chapter-001.md"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(rewritten_draft.content_md, encoding="utf-8")
        return export_artifact, output_path

    monkeypatch.setattr(pipeline_services, "get_project_by_slug", fake_get_project_by_slug)
    monkeypatch.setattr(pipeline_services, "run_scene_pipeline", fake_run_scene_pipeline)
    monkeypatch.setattr(pipeline_services, "assemble_chapter_draft", fake_assemble_chapter_draft)
    monkeypatch.setattr(pipeline_services, "review_chapter_draft", fake_review_chapter_draft)
    monkeypatch.setattr(
        pipeline_services,
        "rewrite_chapter_from_task",
        fake_rewrite_chapter_from_task,
    )
    monkeypatch.setattr(pipeline_services, "export_chapter_markdown", fake_export_chapter_markdown)

    session = FakeSession(
        scalar_results=[chapter],
        scalars_results=[[scene]],
    )
    result = await pipeline_services.run_chapter_pipeline(
        session,
        build_settings(),
        "my-story",
        1,
        requested_by="tester",
        export_markdown=True,
    )

    workflow_runs = [obj for obj in session.added if isinstance(obj, WorkflowRunModel)]
    workflow_steps = [obj for obj in session.added if isinstance(obj, WorkflowStepRunModel)]

    assert result.final_verdict == "pass"
    assert result.chapter_draft_id == rewritten_draft.id
    assert result.chapter_rewrite_iterations == 1
    assert result.chapter_review_iterations == 2
    assert result.review_report_id == second_report.id
    assert result.quality_score_id == quality_b.id
    assert result.export_artifact_id == export_artifact.id
    assert result.requires_human_review is False
    assert len(workflow_runs) == 1
    assert workflow_runs[0].status == "completed"
    assert len(workflow_steps) == 7


@pytest.mark.asyncio
async def test_run_chapter_pipeline_blocks_failed_review_even_when_accept_on_stall(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = build_project()
    chapter = build_chapter(project.id)
    scene = build_scene(project.id, chapter.id)
    chapter_draft = ChapterDraftVersionModel(
        project_id=project.id,
        chapter_id=chapter.id,
        version_no=1,
        content_md="# 第1章 失准星图\n\n章节仍不合格。",
        word_count=900,
        assembled_from_scene_draft_ids=[],
        is_current=True,
    )
    chapter_draft.id = uuid4()
    report = type("ChapterReportStub", (), {"id": uuid4(), "llm_run_id": uuid4()})()
    quality = type("ChapterQualityStub", (), {"id": uuid4()})()
    rewrite_task = type("ChapterRewriteTaskStub", (), {"id": uuid4(), "status": "pending"})()

    async def fake_get_project_by_slug(session, slug: str) -> ProjectModel:
        return project

    async def fake_run_scene_pipeline(
        session,
        settings,
        project_slug,
        chapter_number,
        scene_number,
        **kwargs,
    ):
        return pipeline_services.ScenePipelineResult(
            workflow_run_id=uuid4(),
            project_id=project.id,
            chapter_id=chapter.id,
            scene_id=scene.id,
            chapter_number=chapter.chapter_number,
            scene_number=scene.scene_number,
            current_draft_id=uuid4(),
            current_draft_version_no=1,
            final_verdict="pass",
            review_report_id=uuid4(),
            quality_score_id=uuid4(),
            review_iterations=1,
            rewrite_iterations=0,
            requires_human_review=False,
            llm_run_ids=[],
        )

    async def fake_assemble_chapter_draft(session, project_slug: str, chapter_number: int, *, settings=None):
        return chapter_draft

    async def fake_review_chapter_draft(
        session,
        settings,
        project_slug,
        chapter_number,
        **kwargs,
    ):
        return (
            type("ChapterReviewResultStub", (), {"verdict": "rewrite", "severity_max": "high"})(),
            report,
            quality,
            rewrite_task,
        )

    monkeypatch.setattr(pipeline_services, "get_project_by_slug", fake_get_project_by_slug)
    monkeypatch.setattr(pipeline_services, "run_scene_pipeline", fake_run_scene_pipeline)
    monkeypatch.setattr(pipeline_services, "assemble_chapter_draft", fake_assemble_chapter_draft)
    monkeypatch.setattr(pipeline_services, "review_chapter_draft", fake_review_chapter_draft)

    settings = build_settings()
    settings.pipeline.accept_on_stall = True
    settings.pipeline.chapter_review_block_on_failure = True
    settings.quality.max_chapter_revisions = 0
    session = FakeSession(
        scalar_results=[chapter],
        scalars_results=[[scene]],
    )
    result = await pipeline_services.run_chapter_pipeline(
        session,
        settings,
        "my-story",
        1,
        requested_by="tester",
        export_markdown=False,
    )

    workflow_runs = [obj for obj in session.added if isinstance(obj, WorkflowRunModel)]

    assert result.final_verdict == "rewrite"
    assert result.requires_human_review is True
    assert result.chapter_draft_id == chapter_draft.id
    assert workflow_runs[0].status == "waiting_human"


@pytest.mark.asyncio
async def test_run_project_pipeline_exports_project_checkpoint_when_human_review_required(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    project = build_project()
    chapter = build_chapter(project.id)
    chapter_result = pipeline_services.ChapterPipelineResult(
        workflow_run_id=uuid4(),
        project_id=project.id,
        chapter_id=chapter.id,
        chapter_number=1,
        scene_results=[],
        chapter_draft_id=uuid4(),
        chapter_draft_version_no=1,
        export_artifact_id=uuid4(),
        output_path=str(tmp_path / "output" / "chapter-001.md"),
        requires_human_review=True,
    )
    export_artifact = ExportArtifactModel(
        project_id=project.id,
        export_type="markdown",
        source_scope="project",
        source_id=project.id,
        storage_uri=str(tmp_path / "output" / "project.md"),
        checksum="d" * 64,
        version_label="project-current",
    )
    export_artifact.id = uuid4()

    async def fake_get_project_by_slug(session, slug: str) -> ProjectModel:
        return project

    async def fake_load_project_chapters(session, project_id):
        return [chapter]

    async def fake_run_chapter_pipeline(
        session,
        settings,
        project_slug,
        chapter_number,
        **kwargs,
    ):
        return chapter_result

    async def fake_export_project_markdown(session, settings, project_slug: str, **kwargs):
        output_path = tmp_path / "output" / "project.md"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text("# My Story", encoding="utf-8")
        return export_artifact, output_path

    async def fake_review_project_consistency(
        session,
        settings,
        project_slug: str,
        **kwargs,
    ):
        return (
            type("ProjectReviewResultStub", (), {"verdict": "attention"})(),
            type("ProjectReviewReportStub", (), {"id": uuid4()})(),
            type("ProjectReviewQualityStub", (), {"id": uuid4()})(),
        )

    monkeypatch.setattr(pipeline_services, "get_project_by_slug", fake_get_project_by_slug)
    monkeypatch.setattr(pipeline_services, "_load_project_chapters", fake_load_project_chapters)
    monkeypatch.setattr(pipeline_services, "run_chapter_pipeline", fake_run_chapter_pipeline)
    monkeypatch.setattr(pipeline_services, "export_project_markdown", fake_export_project_markdown)
    monkeypatch.setattr(
        pipeline_services,
        "review_project_consistency",
        fake_review_project_consistency,
    )
    monkeypatch.setattr(
        pipeline_services,
        "materialize_latest_narrative_graph",
        AsyncMock(
            return_value=type(
                "NarrativeGraphResultStub",
                (),
                {"workflow_run_id": uuid4(), "plot_arc_count": 3, "clue_count": 1},
            )()
        ),
    )
    monkeypatch.setattr(
        pipeline_services,
        "materialize_latest_narrative_tree",
        AsyncMock(
            return_value=type(
                "NarrativeTreeResultStub",
                (),
                {"workflow_run_id": uuid4(), "node_count": 16},
            )()
        ),
    )

    session = FakeSession()
    result = await pipeline_services.run_project_pipeline(
        session,
        build_settings(),
        "my-story",
        requested_by="tester",
        export_markdown=True,
    )

    assert result.requires_human_review is True
    assert result.export_artifact_id == export_artifact.id
    assert result.output_path is not None


@pytest.mark.asyncio
async def test_run_project_pipeline_stops_after_human_review_chapter(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    project = build_project()
    chapter1 = build_chapter(project.id)
    chapter2 = build_chapter(project.id)
    chapter2.chapter_number = 2
    calls: list[int] = []

    export_artifact = ExportArtifactModel(
        project_id=project.id,
        export_type="markdown",
        source_scope="project",
        source_id=project.id,
        storage_uri=str(tmp_path / "output" / "project.md"),
        checksum="e" * 64,
        version_label="project-current",
    )
    export_artifact.id = uuid4()

    async def fake_get_project_by_slug(session, slug: str) -> ProjectModel:
        return project

    async def fake_load_project_chapters(session, project_id):
        return [chapter1, chapter2]

    async def fake_run_chapter_pipeline(
        session,
        settings,
        project_slug,
        chapter_number,
        **kwargs,
    ):
        calls.append(chapter_number)
        return pipeline_services.ChapterPipelineResult(
            workflow_run_id=uuid4(),
            project_id=project.id,
            chapter_id=chapter1.id,
            chapter_number=chapter_number,
            scene_results=[],
            chapter_draft_id=uuid4(),
            chapter_draft_version_no=1,
            export_artifact_id=uuid4(),
            output_path=str(tmp_path / "output" / f"chapter-{chapter_number:03d}.md"),
            requires_human_review=True,
        )

    async def fake_export_project_markdown(session, settings, project_slug: str, **kwargs):
        output_path = tmp_path / "output" / "project.md"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text("# My Story", encoding="utf-8")
        return export_artifact, output_path

    async def fake_review_project_consistency(
        session,
        settings,
        project_slug: str,
        **kwargs,
    ):
        return (
            type("ProjectReviewResultStub", (), {"verdict": "attention"})(),
            type("ProjectReviewReportStub", (), {"id": uuid4()})(),
            type("ProjectReviewQualityStub", (), {"id": uuid4()})(),
        )

    monkeypatch.setattr(pipeline_services, "get_project_by_slug", fake_get_project_by_slug)
    monkeypatch.setattr(pipeline_services, "_load_project_chapters", fake_load_project_chapters)
    monkeypatch.setattr(pipeline_services, "run_chapter_pipeline", fake_run_chapter_pipeline)
    monkeypatch.setattr(pipeline_services, "export_project_markdown", fake_export_project_markdown)
    monkeypatch.setattr(
        pipeline_services,
        "review_project_consistency",
        fake_review_project_consistency,
    )
    monkeypatch.setattr(
        pipeline_services,
        "materialize_latest_narrative_graph",
        AsyncMock(
            return_value=type(
                "NarrativeGraphResultStub",
                (),
                {"workflow_run_id": uuid4(), "plot_arc_count": 3, "clue_count": 1},
            )()
        ),
    )
    monkeypatch.setattr(
        pipeline_services,
        "materialize_latest_narrative_tree",
        AsyncMock(
            return_value=type(
                "NarrativeTreeResultStub",
                (),
                {"workflow_run_id": uuid4(), "node_count": 16},
            )()
        ),
    )

    result = await pipeline_services.run_project_pipeline(
        FakeSession(),
        build_settings(),
        "my-story",
        requested_by="tester",
        export_markdown=True,
    )

    assert calls == [1]
    assert [item.chapter_number for item in result.chapter_results] == [1]
    assert result.requires_human_review is True


@pytest.mark.asyncio
async def test_run_project_pipeline_materializes_and_exports(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    project = build_project()
    chapter = build_chapter(project.id)
    materialization_result = type(
        "MaterializationResultStub",
        (),
        {"workflow_run_id": uuid4()},
    )()
    chapter_result = pipeline_services.ChapterPipelineResult(
        workflow_run_id=uuid4(),
        project_id=project.id,
        chapter_id=chapter.id,
        chapter_number=1,
        scene_results=[],
        chapter_draft_id=uuid4(),
        chapter_draft_version_no=1,
        export_artifact_id=uuid4(),
        output_path=str(tmp_path / "output" / "chapter-001.md"),
        requires_human_review=False,
    )
    export_artifact = ExportArtifactModel(
        project_id=project.id,
        export_type="markdown",
        source_scope="project",
        source_id=project.id,
        storage_uri=str(tmp_path / "output" / "project.md"),
        checksum="a" * 64,
        version_label="project-current",
    )
    export_artifact.id = uuid4()

    async def fake_get_project_by_slug(session, slug: str) -> ProjectModel:
        return project

    async def fake_load_project_chapters(session, project_id):
        return [chapter]

    async def fake_materialize_latest(session, project_slug: str, **kwargs):
        return materialization_result

    async def fake_run_chapter_pipeline(
        session,
        settings,
        project_slug,
        chapter_number,
        **kwargs,
    ):
        return chapter_result

    async def fake_export_project_markdown(session, settings, project_slug: str, **kwargs):
        output_path = tmp_path / "output" / "project.md"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text("# My Story", encoding="utf-8")
        return export_artifact, output_path

    async def fake_review_project_consistency(
        session,
        settings,
        project_slug: str,
        **kwargs,
    ):
        return (
            type("ProjectReviewResultStub", (), {"verdict": "pass"})(),
            type("ProjectReviewReportStub", (), {"id": uuid4()})(),
            type("ProjectReviewQualityStub", (), {"id": uuid4()})(),
        )

    async def fake_get_latest_planning_artifact(session, project_id, artifact_type):
        return object()

    monkeypatch.setattr(pipeline_services, "get_project_by_slug", fake_get_project_by_slug)
    monkeypatch.setattr(pipeline_services, "_load_project_chapters", fake_load_project_chapters)
    monkeypatch.setattr(
        pipeline_services,
        "materialize_latest_chapter_outline_batch",
        fake_materialize_latest,
    )
    monkeypatch.setattr(pipeline_services, "run_chapter_pipeline", fake_run_chapter_pipeline)
    monkeypatch.setattr(pipeline_services, "export_project_markdown", fake_export_project_markdown)
    monkeypatch.setattr(
        pipeline_services,
        "review_project_consistency",
        fake_review_project_consistency,
    )
    monkeypatch.setattr(
        pipeline_services,
        "materialize_latest_narrative_graph",
        AsyncMock(
            return_value=type(
                "NarrativeGraphResultStub",
                (),
                {"workflow_run_id": uuid4(), "plot_arc_count": 3, "clue_count": 1},
            )()
        ),
    )
    monkeypatch.setattr(
        pipeline_services,
        "materialize_latest_narrative_tree",
        AsyncMock(
            return_value=type(
                "NarrativeTreeResultStub",
                (),
                {"workflow_run_id": uuid4(), "node_count": 16},
            )()
        ),
    )
    monkeypatch.setattr(
        pipeline_services,
        "get_latest_planning_artifact",
        fake_get_latest_planning_artifact,
    )

    session = FakeSession()
    result = await pipeline_services.run_project_pipeline(
        session,
        build_settings(),
        "my-story",
        requested_by="tester",
        materialize_outline=True,
        export_markdown=True,
    )

    workflow_runs = [obj for obj in session.added if isinstance(obj, WorkflowRunModel)]

    assert isinstance(result, ProjectPipelineResult)
    assert result.materialization_workflow_run_id == materialization_result.workflow_run_id
    assert result.export_artifact_id == export_artifact.id
    assert result.requires_human_review is False
    assert len(result.chapter_results) == 1
    assert len(workflow_runs) == 1
    assert workflow_runs[0].status == "completed"


@pytest.mark.asyncio
async def test_run_project_pipeline_blocks_project_consistency_failure_despite_accept_on_stall(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    project = build_project()
    chapter = build_chapter(project.id)
    chapter_result = pipeline_services.ChapterPipelineResult(
        workflow_run_id=uuid4(),
        project_id=project.id,
        chapter_id=chapter.id,
        chapter_number=1,
        scene_results=[],
        chapter_draft_id=uuid4(),
        chapter_draft_version_no=1,
        export_artifact_id=uuid4(),
        output_path=str(tmp_path / "output" / "chapter-001.md"),
        requires_human_review=False,
    )
    export_artifact = ExportArtifactModel(
        project_id=project.id,
        export_type="markdown",
        source_scope="project",
        source_id=project.id,
        storage_uri=str(tmp_path / "output" / "project.md"),
        checksum="c" * 64,
        version_label="project-current",
    )
    export_artifact.id = uuid4()

    async def fake_get_project_by_slug(session, slug: str) -> ProjectModel:
        return project

    async def fake_load_project_chapters(session, project_id):
        return [chapter]

    async def fake_run_chapter_pipeline(
        session,
        settings,
        project_slug,
        chapter_number,
        **kwargs,
    ):
        return chapter_result

    async def fake_export_project_markdown(session, settings, project_slug: str, **kwargs):
        output_path = tmp_path / "output" / "project.md"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text("# My Story", encoding="utf-8")
        return export_artifact, output_path

    async def fake_review_project_consistency(
        session,
        settings,
        project_slug: str,
        **kwargs,
    ):
        return (
            type("ProjectReviewResultStub", (), {"verdict": "attention"})(),
            type("ProjectReviewReportStub", (), {"id": uuid4()})(),
            type("ProjectReviewQualityStub", (), {"id": uuid4()})(),
        )

    monkeypatch.setattr(pipeline_services, "get_project_by_slug", fake_get_project_by_slug)
    monkeypatch.setattr(pipeline_services, "_load_project_chapters", fake_load_project_chapters)
    monkeypatch.setattr(pipeline_services, "run_chapter_pipeline", fake_run_chapter_pipeline)
    monkeypatch.setattr(pipeline_services, "export_project_markdown", fake_export_project_markdown)
    monkeypatch.setattr(
        pipeline_services,
        "review_project_consistency",
        fake_review_project_consistency,
    )
    monkeypatch.setattr(
        pipeline_services,
        "materialize_latest_narrative_graph",
        AsyncMock(
            return_value=type(
                "NarrativeGraphResultStub",
                (),
                {"workflow_run_id": uuid4(), "plot_arc_count": 3, "clue_count": 1},
            )()
        ),
    )
    monkeypatch.setattr(
        pipeline_services,
        "materialize_latest_narrative_tree",
        AsyncMock(
            return_value=type(
                "NarrativeTreeResultStub",
                (),
                {"workflow_run_id": uuid4(), "node_count": 16},
            )()
        ),
    )

    settings = build_settings()
    settings.pipeline.accept_on_stall = True
    result = await pipeline_services.run_project_pipeline(
        FakeSession(),
        settings,
        "my-story",
        requested_by="tester",
        export_markdown=True,
    )

    assert result.requires_human_review is True
    assert result.final_verdict == "attention"


@pytest.mark.asyncio
async def test_run_project_pipeline_blocks_qimao_without_planning_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = build_project()
    project.metadata_json = {
        **(project.metadata_json or {}),
        "platform_target": "七猫小说",
    }
    chapter = build_chapter(project.id)
    child_called = False
    progress_events: list[str] = []

    async def fake_get_project_by_slug(session, slug: str) -> ProjectModel:
        return project

    async def fake_load_project_chapters(session, project_id):
        return [chapter]

    async def fake_run_chapter_pipeline(*args, **kwargs):
        nonlocal child_called
        child_called = True
        raise AssertionError("chapter pipeline should not run when Qimao planning gate fails")

    monkeypatch.setattr(pipeline_services, "get_project_by_slug", fake_get_project_by_slug)
    monkeypatch.setattr(pipeline_services, "_load_project_chapters", fake_load_project_chapters)
    monkeypatch.setattr(pipeline_services, "run_chapter_pipeline", fake_run_chapter_pipeline)

    session = FakeSession()
    with pytest.raises(ValueError, match="Qimao planning gate failed"):
        await pipeline_services.run_project_pipeline(
            session,
            build_settings(),
            "my-story",
            requested_by="tester",
            export_markdown=False,
            materialize_narrative_graph=False,
            materialize_narrative_tree=False,
            progress=lambda event, payload: progress_events.append(event),
        )

    workflow_runs = [obj for obj in session.added if isinstance(obj, WorkflowRunModel)]
    assert child_called is False
    assert project.metadata_json["qimao_planning_gate_report"]["passed"] is False
    assert (
        project.metadata_json["qimao_planning_gate_report"]["findings"][0]["code"]
        == "missing_opening_quality_contract"
    )
    assert workflow_runs[0].status == "failed"
    assert workflow_runs[0].metadata_json["qimao_planning_gate_report"]["passed"] is False
    assert "qimao_planning_gate_failed" in progress_events


def test_qimao_planning_gate_repairs_abstract_contract_from_outline() -> None:
    project = build_project()
    project.metadata_json = {
        **(project.metadata_json or {}),
        "qimao_opening_contract": {
            "protagonist_name": "沈青崖",
            "opening_incident": "开篇以灵异案件建立视觉锚点，展示主角差异化身份。",
            "first_page_conflict": "围绕青帮的新一层压力开始成形。",
            "protagonist_immediate_goal": (
                "第一层驱动力是查明十五年前沈家灭门惨案真相，手刃仇敌。"
                "第二层驱动力是了解自己的血脉真相。"
            ),
            "visible_loss_if_fail": "追查十五年前沈家灭门惨案真相，手刃仇敌。",
            "protagonist_edge": "沈青崖能看见常人不可见的鬼魂和邪祟痕迹。",
            "edge_limit": "重瞳只能看见第一层异常，不能直接锁定幕后主使。",
            "chapter_1_small_turn": "沈青崖主动行动，完成一次局部反制或信息差建立。",
            "chapter_2_reveal": "第二章放出会改变局势判断的新信息。",
            "chapter_3_payoff": "第三章完成一个小回报并打开下一轮危险。",
            "first_10000_loop": "主角行动 -> 得到短回报 -> 引来反压 -> 新钩子",
            "forbidden_opening_modes": ["background_exposition", "normal_day"],
        },
    }
    first = build_chapter(project.id)
    first.title = "验尸房来客"
    first.chapter_goal = "沈青崖在验尸房完成尸检，遭遇第一具尸体的鬼魂现身说法。"
    first.main_conflict = "死者鬼魂声称被冤枉，真凶就在现场，但验尸房里只有活人。"
    first.hook_description = "沈青崖发现死者脖颈处有肉眼不可见的掐痕。"
    second = build_chapter(project.id)
    second.chapter_number = 2
    second.title = "李宅疑云"
    second.main_conflict = "李德盛死前三天曾请道士驱邪，道士却被官府驱逐。"
    third = build_chapter(project.id)
    third.chapter_number = 3
    third.title = "道士之死"
    third.hook_description = "道士临死前用血在河堤上留下一个字：「归」。"

    report = pipeline_services._record_qimao_planning_gate(
        project,
        chapters=[first, second, third],
    )

    assert report is not None
    assert report["passed"] is True
    repaired = project.metadata_json["qimao_opening_contract"]
    assert "验尸房来客" in repaired["first_page_conflict"]
    assert "当场" in repaired["protagonist_immediate_goal"]
    assert project.metadata_json["qimao_opening_contract_status"] == "planned_gate_passed"


def test_record_commercial_planning_readiness_gate_blocks_thin_long_serial(
    tmp_path: Path,
) -> None:
    project = build_project()
    project.target_chapters = 500
    project.metadata_json = {
        **(project.metadata_json or {}),
        "qimao_opening_contract": {"opening_incident": "尸体喊冤，当场逼主角保住证据。"},
    }
    chapters: list[ChapterModel] = []
    for number in (1, 2, 3):
        chapter = build_chapter(project.id)
        chapter.chapter_number = number
        chapter.opening_situation = ""
        chapter.main_conflict = ""
        chapter.hook_description = ""
        chapter.hype_type = "reversal" if number == 1 else None
        chapter.hype_intensity = 0.1
        scene = build_scene(project.id, chapter.id)
        scene.participants = ["沈青崖"]
        scene.purpose = {"story": "独自调查推进剧情"}
        scene.hook_requirement = ""
        chapter.scenes = [scene]
        chapters.append(chapter)

    report = pipeline_services._record_commercial_planning_readiness_gate(
        project,
        chapters=chapters,
        package_root=tmp_path,
    )

    assert report is not None
    assert report["passed"] is False
    codes = {finding["code"] for finding in report["findings"]}
    assert "long_serial_artifacts_missing" in codes
    assert "golden_three_solo_scene_chain" in codes
    assert "golden_three_hype_underpowered" in codes
    assert project.metadata_json["commercial_planning_readiness_status"] == "planned_gate_failed"


@pytest.mark.asyncio
async def test_run_project_pipeline_creates_opening_quality_rewrite_task_for_general_project(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = build_project()
    project.metadata_json = {
        **(project.metadata_json or {}),
        "editor_rejection_reasons": "开篇切入点比较普通，缺乏足够吸引力。",
        "opening_quality_contract": {
            "platform_target": "商业网文签约口径",
            "protagonist_name": "沈姝",
            "opening_incident": "沈姝推门进账房时，族叔正按着账童抢账本，威胁不交就烧掉母亲旧案证据。",
            "first_page_conflict": "前600字内被逼交出账本，否则旧案证据被毁。",
            "protagonist_immediate_goal": "先保住账本并确认谁在灭口。",
            "visible_loss_if_fail": "失败会失去唯一翻案证据。",
            "protagonist_edge": "主角能从账目细节看出隐藏漏洞。",
            "edge_limit": "账本只能救第一轮，不能直接推翻主谋。",
            "chapter_1_small_turn": "主角当众反制逼迫者。",
            "chapter_2_reveal": "逼迫者背后另有主谋。",
            "chapter_3_payoff": "沈姝拿到账房暗格里的第一份签押证据，确认灭口者与族叔相连。",
            "first_10000_loop": "触发冲突 -> 主角行动 -> 收益/代价 -> 新钩子",
            "forbidden_opening_modes": ["background_exposition", "normal_day", "scenery_first"],
        },
    }
    chapter = build_chapter(project.id)
    draft_id = uuid4()
    chapter_draft = ChapterDraftVersionModel(
        chapter_id=chapter.id,
        version_no=1,
        content_md=(
            "天玄大陆有三千年历史，家族制度复杂，世界观设定分为内城与外城。"
            "多年以前，沈姝所在的沈家曾经掌握账房权力，家族由来可以追溯到前朝。"
            "她站在窗前看天气，街道很安静。"
        ),
        word_count=120,
        is_current=True,
    )
    chapter_draft.id = draft_id
    chapter_result = pipeline_services.ChapterPipelineResult(
        workflow_run_id=uuid4(),
        project_id=project.id,
        chapter_id=chapter.id,
        chapter_number=1,
        scene_results=[],
        chapter_draft_id=draft_id,
        chapter_draft_version_no=1,
        export_artifact_id=None,
        output_path=None,
        requires_human_review=False,
    )

    async def fake_get_project_by_slug(session, slug: str) -> ProjectModel:
        return project

    async def fake_load_project_chapters(session, project_id):
        return [chapter]

    async def fake_run_chapter_pipeline(*args, **kwargs):
        return chapter_result

    monkeypatch.setattr(pipeline_services, "get_project_by_slug", fake_get_project_by_slug)
    monkeypatch.setattr(pipeline_services, "_load_project_chapters", fake_load_project_chapters)
    monkeypatch.setattr(pipeline_services, "run_chapter_pipeline", fake_run_chapter_pipeline)

    session = FakeSession(get_map={(ChapterDraftVersionModel, draft_id): chapter_draft})
    with pytest.raises(ValueError, match="Qimao opening gate failed"):
        await pipeline_services.run_project_pipeline(
            session,
            build_settings(),
            "my-story",
            requested_by="tester",
            export_markdown=False,
            materialize_narrative_graph=False,
            materialize_narrative_tree=False,
        )

    rewrite_tasks = [obj for obj in session.added if isinstance(obj, RewriteTaskModel)]
    assert len(rewrite_tasks) == 1
    assert rewrite_tasks[0].trigger_type == "qimao_opening_gate"
    assert rewrite_tasks[0].rewrite_strategy == "qimao_opening_incident_rewrite"
    assert "这不是润色任务" in rewrite_tasks[0].instructions
    assert project.metadata_json["opening_quality_gate_blocked"] is True
    assert project.metadata_json["opening_quality_gate_report"]["passed"] is False
    assert project.metadata_json["qimao_opening_gate_blocked"] is True
    assert project.metadata_json["qimao_opening_gate_report"]["passed"] is False


@pytest.mark.asyncio
async def test_run_project_pipeline_creates_whole_book_quality_rewrite_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = build_project()
    project.metadata_json = {
        **(project.metadata_json or {}),
        "volume_plan": [
            {"volume_number": 1, "arc_ranges": [[1, 4]], "chapter_count_target": 4}
        ],
    }
    chapters: list[ChapterModel] = []
    draft_by_id: dict[object, ChapterDraftVersionModel] = {}
    result_by_number: dict[int, pipeline_services.ChapterPipelineResult] = {}

    def good_chapter(number: int) -> str:
        return (
            f"沈姝在第{number}章刚进门就被新的证据逼到墙边, 对手夺走账页, 威胁她必须让步。"
            "她抓住对方话里的漏洞反制, 抢回一枚关键印章。"
            "这次小胜让她拿到筹码, 却也付出暴露身份的代价。"
            "章末, 门外突然响起新的脚步声, 真正拿走账本的人是谁?"
        )

    for number in range(1, 5):
        chapter = build_chapter(project.id)
        chapter.chapter_number = number
        chapter.title = f"第{number}章"
        chapter.id = uuid4()
        chapters.append(chapter)

        draft = ChapterDraftVersionModel(
            chapter_id=chapter.id,
            version_no=1,
            content_md=(
                good_chapter(number)
                if number < 4
                else "沈姝回到房间, 整理了一天的想法。天色渐暗, 她觉得事情还没有结束。"
            ),
            word_count=200,
            is_current=True,
        )
        draft.id = uuid4()
        draft_by_id[(ChapterDraftVersionModel, draft.id)] = draft
        result_by_number[number] = pipeline_services.ChapterPipelineResult(
            workflow_run_id=uuid4(),
            project_id=project.id,
            chapter_id=chapter.id,
            chapter_number=number,
            scene_results=[],
            chapter_draft_id=draft.id,
            chapter_draft_version_no=1,
            export_artifact_id=None,
            output_path=None,
            requires_human_review=False,
        )

    async def fake_get_project_by_slug(session, slug: str) -> ProjectModel:
        return project

    async def fake_load_project_chapters(session, project_id):
        return chapters

    async def fake_run_chapter_pipeline(
        session,
        settings,
        project_slug,
        chapter_number,
        **kwargs,
    ):
        return result_by_number[chapter_number]

    monkeypatch.setattr(pipeline_services, "get_project_by_slug", fake_get_project_by_slug)
    monkeypatch.setattr(pipeline_services, "_load_project_chapters", fake_load_project_chapters)
    monkeypatch.setattr(pipeline_services, "run_chapter_pipeline", fake_run_chapter_pipeline)

    progress_events: list[str] = []
    session = FakeSession(get_map=draft_by_id)
    with pytest.raises(ValueError, match="Whole-book quality gate failed"):
        await pipeline_services.run_project_pipeline(
            session,
            build_settings(),
            "my-story",
            requested_by="tester",
            export_markdown=False,
            materialize_narrative_graph=False,
            materialize_narrative_tree=False,
            progress=lambda event, payload: progress_events.append(event),
        )

    rewrite_tasks = [obj for obj in session.added if isinstance(obj, RewriteTaskModel)]
    assert len(rewrite_tasks) == 1
    assert rewrite_tasks[0].trigger_type == "whole_book_quality_gate"
    assert rewrite_tasks[0].rewrite_strategy == "chapter_function_rewrite"
    assert "全书质量门禁重写任务" in rewrite_tasks[0].instructions
    assert project.metadata_json["whole_book_quality_gate_blocked"] is True
    assert project.metadata_json["whole_book_quality_report"]["passed"] is False
    assert len(project.metadata_json["whole_book_engagement_ledger"]) == 4
    assert "whole_book_quality_gate_failed" in progress_events


@pytest.mark.asyncio
async def test_run_project_pipeline_emits_chapter_progress_with_title_and_word_counts(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    project = build_project()
    chapter = build_chapter(project.id)
    chapter.target_word_count = 5000
    chapter.title = "暗潮入局"
    materialization_result = type(
        "MaterializationResultStub",
        (),
        {"workflow_run_id": uuid4()},
    )()
    chapter_result = pipeline_services.ChapterPipelineResult(
        workflow_run_id=uuid4(),
        project_id=project.id,
        chapter_id=chapter.id,
        chapter_number=1,
        scene_results=[],
        chapter_draft_id=uuid4(),
        chapter_draft_version_no=1,
        export_artifact_id=uuid4(),
        output_path=str(tmp_path / "output" / "chapter-001.md"),
        requires_human_review=False,
    )

    async def fake_get_project_by_slug(session, slug: str) -> ProjectModel:
        return project

    async def fake_load_project_chapters(session, project_id):
        return [chapter]

    async def fake_materialize_latest(session, project_slug: str, **kwargs):
        return materialization_result

    async def fake_run_chapter_pipeline(
        session,
        settings,
        project_slug,
        chapter_number,
        **kwargs,
    ):
        chapter.current_word_count = 4986
        chapter.title = "暗潮入局"
        return chapter_result

    async def fake_export_project_markdown(session, settings, project_slug: str, **kwargs):
        output_path = tmp_path / "output" / "project.md"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text("# My Story", encoding="utf-8")
        export_artifact = ExportArtifactModel(
            project_id=project.id,
            export_type="markdown",
            source_scope="project",
            source_id=project.id,
            storage_uri=str(output_path),
            checksum="b" * 64,
            version_label="project-current",
        )
        export_artifact.id = uuid4()
        return export_artifact, output_path

    async def fake_review_project_consistency(
        session,
        settings,
        project_slug: str,
        **kwargs,
    ):
        return (
            type("ProjectReviewResultStub", (), {"verdict": "pass"})(),
            type("ProjectReviewReportStub", (), {"id": uuid4()})(),
            type("ProjectReviewQualityStub", (), {"id": uuid4()})(),
        )

    async def fake_get_latest_planning_artifact(session, project_id, artifact_type):
        return object()

    monkeypatch.setattr(pipeline_services, "get_project_by_slug", fake_get_project_by_slug)
    monkeypatch.setattr(pipeline_services, "_load_project_chapters", fake_load_project_chapters)
    monkeypatch.setattr(
        pipeline_services,
        "materialize_latest_chapter_outline_batch",
        fake_materialize_latest,
    )
    monkeypatch.setattr(pipeline_services, "run_chapter_pipeline", fake_run_chapter_pipeline)
    monkeypatch.setattr(pipeline_services, "export_project_markdown", fake_export_project_markdown)
    monkeypatch.setattr(
        pipeline_services,
        "review_project_consistency",
        fake_review_project_consistency,
    )
    monkeypatch.setattr(
        pipeline_services,
        "materialize_latest_narrative_graph",
        AsyncMock(
            return_value=type(
                "NarrativeGraphResultStub",
                (),
                {"workflow_run_id": uuid4(), "plot_arc_count": 3, "clue_count": 1},
            )()
        ),
    )
    monkeypatch.setattr(
        pipeline_services,
        "materialize_latest_narrative_tree",
        AsyncMock(
            return_value=type(
                "NarrativeTreeResultStub",
                (),
                {"workflow_run_id": uuid4(), "node_count": 16},
            )()
        ),
    )
    monkeypatch.setattr(
        pipeline_services,
        "get_latest_planning_artifact",
        fake_get_latest_planning_artifact,
    )

    progress_events: list[tuple[str, dict[str, object] | None]] = []

    def progress(stage: str, payload: dict[str, object] | None = None) -> None:
        progress_events.append((stage, payload))

    session = FakeSession()
    await pipeline_services.run_project_pipeline(
        session,
        build_settings(),
        "my-story",
        requested_by="tester",
        materialize_outline=True,
        export_markdown=True,
        progress=progress,
    )

    started = [payload for stage, payload in progress_events if stage == "chapter_pipeline_started"]
    completed = [payload for stage, payload in progress_events if stage == "chapter_pipeline_completed"]

    assert started == [
        {
            "project_slug": "my-story",
            "chapter_number": 1,
            "progress": "1/1",
            "global_progress": "1/1",
            "target_word_count": 5000,
        }
    ]
    assert completed == [
        {
            "project_slug": "my-story",
            "chapter_number": 1,
            "progress": "1/1",
            "global_progress": "1/1",
            "workflow_run_id": str(chapter_result.workflow_run_id),
            "requires_human_review": False,
            "chapter_draft_version_no": 1,
            "chapter_title": "暗潮入局",
            "word_count": 4986,
            "target_word_count": 5000,
        }
    ]


@pytest.mark.asyncio
async def test_run_project_pipeline_filters_requested_chapter_numbers_and_checkpoints_before_children(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = build_project()
    # Pre-seed invariants so L1 _ensure_project_invariants is a no-op — this
    # test focuses on chapter-dispatch ordering, not invariant seeding (which
    # has its own dedicated tests and its own commit of the seeded payload).
    project.invariants_json = {
        "project_id": str(project.id),
        "language": "zh-CN",
        "pov": "close_third",
        "tense": "past",
        "length_envelope": {
            "min_chars": 5000,
            "target_chars": 6400,
            "max_chars": 7500,
        },
    }
    chapter_1 = build_chapter(project.id)
    chapter_1.status = "complete"
    chapter_2 = build_chapter(project.id)
    chapter_2.id = uuid4()
    chapter_2.chapter_number = 2
    chapter_2.title = "第二章"
    chapter_3 = build_chapter(project.id)
    chapter_3.id = uuid4()
    chapter_3.chapter_number = 3
    chapter_3.title = "第三章"

    sequence: list[str] = []
    processed: list[int] = []

    async def fake_get_project_by_slug(session, slug: str) -> ProjectModel:
        return project

    async def fake_load_project_chapters(session, project_id):
        return [chapter_1, chapter_2, chapter_3]

    async def fake_checkpoint_commit(session) -> None:
        sequence.append("commit")

    async def fake_run_chapter_pipeline(session, settings, project_slug, chapter_number, **kwargs):
        sequence.append(f"chapter:{chapter_number}")
        processed.append(chapter_number)
        return pipeline_services.ChapterPipelineResult(
            workflow_run_id=uuid4(),
            project_id=project.id,
            chapter_id=chapter_2.id,
            chapter_number=chapter_number,
            scene_results=[],
            chapter_draft_id=uuid4(),
            chapter_draft_version_no=1,
            requires_human_review=False,
        )

    async def fake_review_project_consistency(session, settings, project_slug: str, **kwargs):
        return (
            type("ProjectReviewResultStub", (), {"verdict": "pass"})(),
            type("ProjectReviewReportStub", (), {"id": uuid4()})(),
            type("ProjectReviewQualityStub", (), {"id": uuid4()})(),
        )

    async def fake_sync_world_expansion_progress(session, *, project):
        return None

    monkeypatch.setattr(pipeline_services, "get_project_by_slug", fake_get_project_by_slug)
    monkeypatch.setattr(pipeline_services, "_load_project_chapters", fake_load_project_chapters)
    monkeypatch.setattr(pipeline_services, "_checkpoint_commit", fake_checkpoint_commit)
    monkeypatch.setattr(pipeline_services, "run_chapter_pipeline", fake_run_chapter_pipeline)
    monkeypatch.setattr(
        pipeline_services,
        "review_project_consistency",
        fake_review_project_consistency,
    )
    monkeypatch.setattr(
        pipeline_services,
        "sync_world_expansion_progress",
        fake_sync_world_expansion_progress,
    )

    session = FakeSession()
    result = await pipeline_services.run_project_pipeline(
        session,
        build_settings(),
        "my-story",
        requested_by="tester",
        materialize_narrative_graph=False,
        materialize_narrative_tree=False,
        export_markdown=False,
        chapter_numbers={2},
    )

    assert processed == [2]
    assert sequence[0] == "commit"
    assert sequence[1] == "chapter:2"
    assert [item.chapter_number for item in result.chapter_results] == [2]


@pytest.mark.asyncio
async def test_run_autowrite_pipeline_runs_auto_repair_and_reports_outputs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    project = build_project()
    settings = build_settings()
    settings.output.base_dir = str(tmp_path / "output")
    exported_project = tmp_path / "output" / project.slug / "project.md"
    exported_project.parent.mkdir(parents=True, exist_ok=True)
    exported_project.write_text("# My Story", encoding="utf-8")

    async def fake_get_project_by_slug(session, slug: str):
        return None

    async def fake_create_project(session, payload, settings):
        return project

    async def fake_generate_novel_plan(session, settings, project_slug: str, premise: str, **kwargs):
        return type(
            "PlanningResultStub",
            (),
            {
                "workflow_run_id": uuid4(),
                "volume_count": 1,
                "chapter_count": 1,
            },
        )()

    async def fake_materialize_story_bible(session, project_slug: str, **kwargs):
        return type("StoryBibleResultStub", (), {"workflow_run_id": uuid4()})()

    async def fake_materialize_outline(session, project_slug: str, **kwargs):
        return type("OutlineResultStub", (), {"workflow_run_id": uuid4()})()

    async def fake_run_project_pipeline(session, settings, project_slug: str, **kwargs):
        return ProjectPipelineResult(
            workflow_run_id=uuid4(),
            project_id=project.id,
            project_slug=project_slug,
            chapter_results=[],
            review_report_id=uuid4(),
            quality_score_id=uuid4(),
            final_verdict="attention",
            export_artifact_id=None,
            output_path=None,
            requires_human_review=True,
        )

    async def fake_run_project_repair(session, settings, project_slug: str, **kwargs):
        return ProjectRepairResult(
            workflow_run_id=uuid4(),
            project_id=project.id,
            project_slug=project_slug,
            pending_rewrite_task_count=2,
            superseded_task_count=2,
            processed_chapters=[],
            review_report_id=uuid4(),
            quality_score_id=uuid4(),
            final_verdict="pass",
            export_artifact_id=uuid4(),
            output_path=str(exported_project),
            remaining_pending_rewrite_count=0,
            requires_human_review=False,
        )

    progress_events: list[str] = []

    def fake_progress(stage: str, payload: dict[str, object] | None = None) -> None:
        progress_events.append(stage)

    monkeypatch.setattr(pipeline_services, "get_project_by_slug", fake_get_project_by_slug)
    monkeypatch.setattr(pipeline_services, "create_project", fake_create_project)
    monkeypatch.setattr(pipeline_services, "generate_novel_plan", fake_generate_novel_plan)
    monkeypatch.setattr(
        pipeline_services,
        "materialize_latest_story_bible",
        fake_materialize_story_bible,
    )
    monkeypatch.setattr(
        pipeline_services,
        "materialize_latest_chapter_outline_batch",
        fake_materialize_outline,
    )
    monkeypatch.setattr(
        pipeline_services,
        "materialize_latest_narrative_graph",
        AsyncMock(
            return_value=type(
                "NarrativeGraphResultStub",
                (),
                {"workflow_run_id": uuid4(), "plot_arc_count": 3, "clue_count": 1},
            )()
        ),
    )
    monkeypatch.setattr(
        pipeline_services,
        "materialize_latest_narrative_tree",
        AsyncMock(
            return_value=type(
                "NarrativeTreeResultStub",
                (),
                {"workflow_run_id": uuid4(), "node_count": 16},
            )()
        ),
    )
    monkeypatch.setattr(pipeline_services, "run_project_pipeline", fake_run_project_pipeline)
    monkeypatch.setattr(
        "bestseller.services.repair.run_project_repair",
        fake_run_project_repair,
    )

    session = FakeSession()
    result = await pipeline_services.run_autowrite_pipeline(
        session,
        settings,
        project_payload=pipeline_services.ProjectCreate(
            slug=project.slug,
            title=project.title,
            genre=project.genre,
            target_word_count=project.target_word_count,
            target_chapters=project.target_chapters,
        ),
        premise="导航员揭穿帝国谎言。",
        progress=fake_progress,
    )

    assert result.repair_attempted is True
    assert result.repair_workflow_run_id is not None
    assert result.export_status == "exported"
    assert result.output_path == str(exported_project)
    assert result.output_files == [str(exported_project.resolve())]
    assert "auto_repair_started" in progress_events
    assert "auto_repair_completed" in progress_events
    assert progress_events[-1] == "autowrite_completed"


def test_should_use_progressive_pipeline_routes_large_target_chapters() -> None:
    """target_chapters > threshold picks the progressive path even when the
    setting is off — this is the bug that stalled large books during self-heal
    (web used the threshold, worker used the setting, default False)."""
    settings = build_settings()
    settings.pipeline.progressive_planning = False

    small = pipeline_services.ProjectCreate(
        slug="small", title="small", genre="fantasy",
        target_word_count=30000, target_chapters=30,
    )
    assert pipeline_services._should_use_progressive_pipeline(settings, small) is False

    at_threshold = pipeline_services.ProjectCreate(
        slug="edge", title="edge", genre="fantasy",
        target_word_count=30000,
        target_chapters=pipeline_services.PROGRESSIVE_CHAPTER_THRESHOLD,
    )
    assert pipeline_services._should_use_progressive_pipeline(settings, at_threshold) is False

    large = pipeline_services.ProjectCreate(
        slug="large", title="large", genre="fantasy",
        target_word_count=2000000,
        target_chapters=pipeline_services.PROGRESSIVE_CHAPTER_THRESHOLD + 1,
    )
    assert pipeline_services._should_use_progressive_pipeline(settings, large) is True


def test_should_use_progressive_pipeline_respects_explicit_setting() -> None:
    """Explicit progressive_planning=True wins over a small target."""
    settings = build_settings()
    settings.pipeline.progressive_planning = True

    small = pipeline_services.ProjectCreate(
        slug="small", title="small", genre="fantasy",
        target_word_count=10000, target_chapters=10,
    )
    assert pipeline_services._should_use_progressive_pipeline(settings, small) is True


def test_should_use_progressive_pipeline_handles_missing_target() -> None:
    """A missing target_chapters attribute must not trip the progressive path
    (defensive — ProjectCreate's validator already forbids zero, but other
    payload shapes might omit the field)."""
    settings = build_settings()
    settings.pipeline.progressive_planning = False

    class _PayloadWithoutTarget:
        slug = "x"

    assert pipeline_services._should_use_progressive_pipeline(
        settings, _PayloadWithoutTarget()
    ) is False


@pytest.mark.asyncio
async def test_run_autowrite_pipeline_reroutes_large_target_to_progressive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When target_chapters exceeds the threshold, run_autowrite_pipeline
    must delegate to run_progressive_autowrite_pipeline even if the setting
    is off. Regression guard for the web/worker routing divergence."""
    settings = build_settings()
    settings.pipeline.progressive_planning = False

    sentinel = object()
    captured: dict[str, object] = {}

    async def fake_progressive(session, settings_arg, **kwargs):
        captured["called"] = True
        captured["target_chapters"] = kwargs["project_payload"].target_chapters
        return sentinel

    async def fake_non_progressive_guard(*args, **kwargs):
        raise AssertionError("non-progressive path should not be used for large targets")

    monkeypatch.setattr(
        pipeline_services,
        "run_progressive_autowrite_pipeline",
        fake_progressive,
    )
    monkeypatch.setattr(pipeline_services, "generate_novel_plan", fake_non_progressive_guard)

    payload = pipeline_services.ProjectCreate(
        slug="huge", title="huge", genre="fantasy",
        target_word_count=2000000,
        target_chapters=pipeline_services.PROGRESSIVE_CHAPTER_THRESHOLD + 1,
    )
    result = await pipeline_services.run_autowrite_pipeline(
        FakeSession(),
        settings,
        project_payload=payload,
        premise="premise",
    )

    assert result is sentinel
    assert captured["called"] is True
    assert captured["target_chapters"] == pipeline_services.PROGRESSIVE_CHAPTER_THRESHOLD + 1


@pytest.mark.asyncio
async def test_progressive_autowrite_skips_bible_materialization_on_resume(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When a project already has a completed `materialize_story_bible` workflow
    run AND resume is enabled, the resume path must NOT re-run materialization.

    Regression guard for a stall observed on `exorcist-detective-1778051012`
    (chapter 9 of "青囊不语问阴阳"): the worker self-heal kept entering the
    progressive pipeline, hit `materialize_latest_story_bible`, the L2 bible
    completeness gate raised on stricter rules added after foundation, and the
    job retried forever — chapter 9 never resumed.
    """
    project = build_project()
    settings = build_settings()
    settings.pipeline.resume_enabled = True

    completed_bible_run = WorkflowRunModel(
        project_id=project.id,
        workflow_type=pipeline_services.WORKFLOW_TYPE_MATERIALIZE_STORY_BIBLE,
        status="completed",
    )
    completed_bible_run.id = uuid4()

    existing_volume_plan = type(
        "PlanningArtifactStub",
        (),
        {"source_run_id": uuid4(), "content": []},
    )()

    async def fake_get_project_by_slug(session, slug: str):
        return project

    async def fake_get_latest_planning_artifact(session, *, project_id, artifact_type):
        if artifact_type == pipeline_services.ArtifactType.VOLUME_PLAN:
            return existing_volume_plan
        return None

    async def fake_get_latest_completed_workflow_run(session, *, project_id, workflow_type):
        assert project_id == project.id
        assert workflow_type == pipeline_services.WORKFLOW_TYPE_MATERIALIZE_STORY_BIBLE
        return completed_bible_run

    async def fake_materialize_latest_story_bible(*args, **kwargs):
        raise AssertionError(
            "materialize_latest_story_bible must be skipped on resume when a "
            "completed run already exists"
        )

    async def fake_checkpoint_commit(session) -> None:
        return None

    monkeypatch.setattr(pipeline_services, "get_project_by_slug", fake_get_project_by_slug)
    monkeypatch.setattr(
        pipeline_services, "get_latest_planning_artifact", fake_get_latest_planning_artifact
    )
    monkeypatch.setattr(
        pipeline_services,
        "get_latest_completed_workflow_run",
        fake_get_latest_completed_workflow_run,
    )
    monkeypatch.setattr(
        pipeline_services,
        "materialize_latest_story_bible",
        fake_materialize_latest_story_bible,
    )
    monkeypatch.setattr(pipeline_services, "_checkpoint_commit", fake_checkpoint_commit)

    progress_events: list[str] = []

    def fake_progress(stage: str, payload: dict[str, object] | None = None) -> None:
        progress_events.append(stage)

    payload = pipeline_services.ProjectCreate(
        slug=project.slug, title=project.title, genre=project.genre,
        target_word_count=project.target_word_count, target_chapters=project.target_chapters,
    )

    # Empty volume plan exits the per-volume loop after the bible-resume decision,
    # so the pipeline returns cleanly. Any call to the bible materializer would
    # have raised AssertionError above.
    await pipeline_services.run_progressive_autowrite_pipeline(
        FakeSession(), settings,
        project_payload=payload, premise="...", progress=fake_progress,
    )

    assert "foundation_planning_skipped_resume" in progress_events
    assert "story_bible_materialization_skipped_resume" in progress_events
    assert "story_bible_materialization_started" not in progress_events
