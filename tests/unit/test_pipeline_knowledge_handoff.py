"""Exercise the pipeline-to-knowledge handoff at the actual service boundary."""
from uuid import uuid4

import pytest

from bestseller.services import feedback, pipelines
from bestseller.services.budget import ExecutionBudgetError
from bestseller.settings import load_settings
from test_adopted_knowledge_lineage import db, stub_llm, canon  # noqa: F401

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


async def test_pipeline_applies_exact_adopted_body_without_paid_savepoint(db, monkeypatch):
    session, project, chapter, draft = db
    settings = load_settings()
    settings.pipeline.enable_chapter_feedback = True
    calls = stub_llm(monkeypatch, feedback, canon())
    result = await pipelines._extract_chapter_knowledge_if_enabled(
        session, settings, project_id=project.id, chapter=chapter,
        chapter_md=draft.content_md, source_chapter_draft_version_id=draft.id,
        workflow_run_id=uuid4(),
    )
    assert len(calls) == 1
    assert result.extraction_status == "ok"
    assert result.canon_facts_created == 1
    assert result.source_lineage["source_chapter_draft_version_id"] == str(draft.id)


async def test_pipeline_does_not_swallow_terminal_budget_stop(db, monkeypatch):
    session, project, chapter, draft = db
    settings = load_settings()
    settings.pipeline.enable_chapter_feedback = True

    async def stopped(*args, **kwargs):
        raise ExecutionBudgetError("execution_cancelled", "test-scope")

    monkeypatch.setattr(feedback, "extract_chapter_feedback", stopped)
    with pytest.raises(ExecutionBudgetError, match="execution_cancelled"):
        await pipelines._extract_chapter_knowledge_if_enabled(
            session, settings, project_id=project.id, chapter=chapter,
            chapter_md=draft.content_md, source_chapter_draft_version_id=draft.id,
            workflow_run_id=uuid4(),
        )
