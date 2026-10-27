from pathlib import Path
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from test_pipeline_services import FakeSession, build_chapter, build_project, build_settings

from bestseller.domain.pipeline import ChapterPipelineResult
from bestseller.infra.db.models import ChapterDraftVersionModel, ExportArtifactModel
from bestseller.services import adopted_knowledge_resume
from bestseller.services import exports as export_services
from bestseller.services import pipelines as pipeline_services
from bestseller.services.book_closure import evaluate_book_closure
from bestseller.services.invariants import invariants_to_dict, seed_invariants


async def _run_project(monkeypatch, tmp_path: Path, *, strict_export, closure_export):
    project = build_project()
    project.invariants_json = invariants_to_dict(seed_invariants(
        project_id=project.id, language=project.language, words_per_chapter=3000))
    project.target_chapters = 1
    chapter = build_chapter(project.id)
    chapter_result = ChapterPipelineResult(
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
    strict_artifact = ExportArtifactModel(
        project_id=project.id,
        export_type="markdown",
        source_scope="project",
        source_id=project.id,
        storage_uri=str(tmp_path / "output" / "project.md"),
        checksum="s" * 64,
        version_label="project-current",
    )
    strict_artifact.id = uuid4()
    closure_artifact = ExportArtifactModel(
        project_id=project.id,
        export_type="markdown_draft",
        source_scope="project",
        source_id=project.id,
        storage_uri=str(tmp_path / "output" / "project-draft-with-quality-debt.md"),
        checksum="c" * 64,
        version_label="project-draft-quality-debt",
        metadata_json={"publication_ready": False},
    )
    closure_artifact.id = uuid4()
    calls = {"strict": 0, "closure": 0}

    async def fake_get_project_by_slug(session, slug):
        return project

    async def fake_load_project_chapters(session, project_id):
        return [chapter]

    async def fake_run_chapter_pipeline(session, settings, project_slug, chapter_number, **kwargs):
        return chapter_result

    async def fake_strict(session, settings, project_slug, **kwargs):
        calls["strict"] += 1
        if isinstance(strict_export, Exception):
            raise strict_export
        return strict_artifact, tmp_path / "output" / "project.md"

    async def fake_closure(session, settings, project_slug, **kwargs):
        calls["closure"] += 1
        if isinstance(closure_export, Exception):
            raise closure_export
        return closure_artifact, tmp_path / "output" / "project-draft-with-quality-debt.md"

    async def fake_review(*args, **kwargs):
        return (
            type("Review", (), {"verdict": "attention"})(),
            type("Report", (), {"id": uuid4()})(),
            type("Quality", (), {"id": uuid4()})(),
        )

    monkeypatch.setattr(pipeline_services, "get_project_by_slug", fake_get_project_by_slug)
    monkeypatch.setattr(pipeline_services, "_load_project_chapters", fake_load_project_chapters)
    monkeypatch.setattr(pipeline_services, "run_chapter_pipeline", fake_run_chapter_pipeline)
    # This fixture exercises export fallback orchestration, not database-backed
    # resume/adoption readers. Keep its candidate chapter in the writing queue;
    # absent delivery evidence must never become a successful closure verdict.
    monkeypatch.setattr(
        adopted_knowledge_resume, "resume_project_adopted_knowledge", AsyncMock(return_value={})
    )
    monkeypatch.setattr(
        pipeline_services,
        "_select_pending_chapters_for_resume",
        AsyncMock(return_value=([chapter], [])),
    )
    delivery = AsyncMock(return_value=evaluate_book_closure(
        [chapter], expected_chapters=project.target_chapters
    ))
    monkeypatch.setattr(pipeline_services, "settle_project_status_on_closure", delivery)
    monkeypatch.setattr(pipeline_services, "export_project_markdown", fake_strict)
    monkeypatch.setattr(pipeline_services, "export_project_closure_draft_markdown", fake_closure)
    monkeypatch.setattr(pipeline_services, "review_project_consistency", fake_review)
    monkeypatch.setattr(
        pipeline_services,
        "materialize_latest_narrative_graph",
        AsyncMock(
            return_value=type(
                "Graph", (), {"workflow_run_id": uuid4(), "plot_arc_count": 3, "clue_count": 1}
            )()
        ),
    )
    monkeypatch.setattr(
        pipeline_services,
        "materialize_latest_narrative_tree",
        AsyncMock(return_value=type("Tree", (), {"workflow_run_id": uuid4(), "node_count": 16})()),
    )
    events = []
    result = await pipeline_services.run_project_pipeline(
        FakeSession(),
        build_settings(),
        "my-story",
        requested_by="tester",
        export_markdown=True,
        progress=lambda stage, payload: events.append((stage, payload)),
    )
    delivery.assert_awaited_once()
    assert delivery.return_value.is_complete is False
    assert project.status == "revising"
    assert result.requires_human_review is True
    assert "completed_at" not in (project.metadata_json or {})
    return result, calls, events, strict_artifact, closure_artifact


@pytest.mark.asyncio
async def test_strict_failure_falls_back_to_readable_closure_draft(monkeypatch, tmp_path):
    result, calls, events, _, closure = await _run_project(
        monkeypatch, tmp_path, strict_export=ValueError("strict blocked"), closure_export=None
    )
    assert calls == {"strict": 1, "closure": 1}
    assert result.export_artifact_id == closure.id
    assert result.output_path.endswith("project-draft-with-quality-debt.md")
    assert closure.export_type == "markdown_draft"
    assert closure.metadata_json["publication_ready"] is False
    event = next(payload for stage, payload in events if stage == "project_closure_draft_exported")
    assert event["publication_ready"] is False
    assert event["strict_export_blocked"] == "strict blocked"


@pytest.mark.asyncio
async def test_missing_chapter_rejects_closure_and_preserves_no_artifact(monkeypatch, tmp_path):
    result, calls, events, _, _ = await _run_project(
        monkeypatch,
        tmp_path,
        strict_export=ValueError("missing promoted chapter: 2"),
        closure_export=ValueError("missing current draft: 2"),
    )
    assert calls == {"strict": 1, "closure": 1}
    assert result.export_artifact_id is None
    assert result.output_path is None
    event = next(payload for stage, payload in events if stage == "project_export_skipped")
    assert event["reason"] == "missing promoted chapter: 2"
    assert event["closure_draft_reason"] == "missing current draft: 2"


@pytest.mark.asyncio
async def test_successful_strict_export_does_not_call_closure(monkeypatch, tmp_path):
    result, calls, events, strict, _ = await _run_project(
        monkeypatch, tmp_path, strict_export=None, closure_export=AssertionError("must not call")
    )
    assert calls == {"strict": 1, "closure": 0}
    assert result.export_artifact_id == strict.id
    assert result.output_path.endswith("project.md")
    assert not any(stage == "project_closure_draft_exported" for stage, _ in events)


@pytest.mark.asyncio
async def test_closure_rejects_target_twenty_when_only_eight_chapters_exist(monkeypatch):
    project = build_project()
    project.target_chapters = 20
    chapters = []
    drafts = []
    for number in range(1, 9):
        chapter = build_chapter(project.id)
        chapter.chapter_number = number
        chapters.append(chapter)
        draft = ChapterDraftVersionModel(
            project_id=project.id,
            chapter_id=chapter.id,
            version_no=1,
            content_md=f"第{number}章正文",
            word_count=5,
            assembled_from_scene_draft_ids=[str(uuid4())],
            is_current=True,
        )
        draft.id = uuid4()
        drafts.append(draft)

    async def fake_get_project_by_slug(session, slug):
        return project

    monkeypatch.setattr(export_services, "get_project_by_slug", fake_get_project_by_slug)
    session = FakeSession(scalar_results=drafts, scalars_results=[chapters])
    with pytest.raises(ValueError, match="expected contiguous chapters 1-20"):
        await export_services._load_project_closure_draft_payload(session, project.slug)


@pytest.mark.asyncio
async def test_closure_rejects_empty_current_content(monkeypatch):
    project = build_project()
    project.invariants_json = invariants_to_dict(seed_invariants(
        project_id=project.id, language=project.language, words_per_chapter=3000))
    project.target_chapters = 1
    chapter = build_chapter(project.id)
    draft = ChapterDraftVersionModel(
        project_id=project.id,
        chapter_id=chapter.id,
        version_no=1,
        content_md="",
        word_count=0,
        assembled_from_scene_draft_ids=[str(uuid4())],
        is_current=True,
    )
    draft.id = uuid4()

    async def fake_get_project_by_slug(session, slug):
        return project

    monkeypatch.setattr(export_services, "get_project_by_slug", fake_get_project_by_slug)
    session = FakeSession(scalar_results=[draft], scalars_results=[[chapter]])
    with pytest.raises(ValueError, match="正文为空"):
        await export_services.export_project_closure_draft_markdown(
            session, build_settings(), project.slug
        )


@pytest.mark.asyncio
async def test_progressive_strict_export_failure_returns_closure_review_state(
    monkeypatch, tmp_path
):
    """Progressive autowrite exposes the closure artifact as human-review state."""
    from bestseller.infra.db.models import WorkflowRunModel

    project = build_project()
    project.invariants_json = invariants_to_dict(seed_invariants(
        project_id=project.id, language=project.language, words_per_chapter=3000))
    project.target_chapters = 1
    settings = build_settings()
    settings.pipeline.resume_enabled = True
    settings.pipeline.require_foundation_identity_lock = False
    existing_bible_run = WorkflowRunModel(
        project_id=project.id,
        workflow_type=pipeline_services.WORKFLOW_TYPE_MATERIALIZE_STORY_BIBLE,
        status="completed",
    )
    existing_bible_run.id = uuid4()
    volume_plan = type("Artifact", (), {"source_run_id": uuid4(), "content": []})()
    strict_calls = 0
    closure_calls = 0
    events = []

    async def fake_project(session, slug):
        return project

    async def fake_artifact(session, *, project_id, artifact_type, settings=None):
        if artifact_type == pipeline_services.ArtifactType.VOLUME_PLAN:
            return volume_plan
        return None

    async def fake_run(session, *, project_id, workflow_type):
        if workflow_type == pipeline_services.WORKFLOW_TYPE_MATERIALIZE_STORY_BIBLE:
            return existing_bible_run
        return None

    async def fake_strict(*args, **kwargs):
        nonlocal strict_calls
        strict_calls += 1
        raise ValueError("strict export blocked")

    async def fake_closure(*args, **kwargs):
        nonlocal closure_calls
        closure_calls += 1
        return type(
            "ClosureArtifact", (), {"id": uuid4()}
        )(), tmp_path / "project-draft-with-quality-debt.md"

    def fake_progress(stage, payload=None):
        events.append((stage, payload or {}))

    async def fake_checkpoint(session):
        return None

    async def fake_volume_written(*args, **kwargs):
        return False, 0, 0

    monkeypatch.setattr(pipeline_services, "get_project_by_slug", fake_project)
    monkeypatch.setattr(pipeline_services, "get_latest_approved_planning_artifact", fake_artifact)
    monkeypatch.setattr(pipeline_services, "get_latest_completed_workflow_run", fake_run)
    monkeypatch.setattr(pipeline_services, "export_project_markdown", fake_strict)
    monkeypatch.setattr(pipeline_services, "export_project_closure_draft_markdown", fake_closure)
    monkeypatch.setattr(pipeline_services, "_checkpoint_commit", fake_checkpoint)
    monkeypatch.setattr(pipeline_services, "_volume_fully_written", fake_volume_written)

    payload = pipeline_services.ProjectCreate(
        slug=project.slug,
        title=project.title,
        genre=project.genre,
        target_word_count=project.target_word_count,
        target_chapters=project.target_chapters,
    )
    result = await pipeline_services.run_progressive_autowrite_pipeline(
        FakeSession(),
        settings,
        project_payload=payload,
        premise="premise",
        export_markdown=True,
        auto_repair_on_attention=False,
        progress=fake_progress,
    )

    assert strict_calls == 1
    assert closure_calls == 1
    assert result.export_status == "draft_exported_requires_human_review"
    assert result.output_path.endswith("project-draft-with-quality-debt.md")
    completed = next(payload for stage, payload in events if stage == "autowrite_completed")
    assert completed["export_status"] == "draft_exported_requires_human_review"
    assert completed["final_verdict"] == "attention"
    assert result.requires_human_review is True


def test_web_task_manager_treats_closure_export_as_attention():
    from bestseller.web import server as web_server

    payload = {"export_status": "draft_exported_requires_human_review"}
    assert web_server._payload_requires_machine_attention(payload) is True
    assert "draft_exported_requires_human_review" in web_server._ATTENTION_VERDICTS
