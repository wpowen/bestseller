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
        get_map: dict[object, object] | None = None,
    ) -> None:
        self.scalar_results = list(scalar_results or [])
        self.scalars_results = list(scalars_results or [])
        self.get_map = dict(get_map or {})
        self.added: list[object] = []
        self.executed: list[object] = []
        self.is_active = True

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


def build_settings():
    return load_settings(env={})


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
        metadata_json={},
    )
    project.id = uuid4()
    return project


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
async def test_generate_scene_draft_creates_new_current_version(
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
    chapter_draft = ChapterDraftVersionModel(
        project_id=project.id,
        chapter_id=chapter.id,
        version_no=1,
        content_md="# 第1章 失准星图",
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

    artifact, output_path = await export_services.export_project_markdown(
        session,
        settings,
        "my-story",
    )

    assert artifact.id is not None
    assert output_path.exists() is True
    assert output_path.read_text(encoding="utf-8").startswith("# My Story")


@pytest.mark.asyncio
async def test_export_project_docx_writes_artifact(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    project = build_project()
    chapter = build_chapter(project.id)
    chapter.target_word_count = 120
    chapter_draft = ChapterDraftVersionModel(
        project_id=project.id,
        chapter_id=chapter.id,
        version_no=1,
        content_md="# 第1章 失准星图",
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
    chapter_draft = ChapterDraftVersionModel(
        project_id=project.id,
        chapter_id=chapter.id,
        version_no=1,
        content_md="# 第1章 失准星图",
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

    async def fake_prepare_auto_repair(session, *, project, chapter, repairable_codes):
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
