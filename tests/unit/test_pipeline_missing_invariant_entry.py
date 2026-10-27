"""Real pipeline entry preserves invalid contracts and audits missing-only seed."""
from copy import deepcopy
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
from test_chapter_review_admission_sqlite import admission_db  # noqa: F401

from bestseller.infra.db.models import (
    ChapterDraftVersionModel, ExecutionBudgetModel, ProjectModel, WorkflowRunModel,
)
from bestseller.services import pipelines
from bestseller.services.invariants import InvariantSeedError

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


async def test_actual_initializer_persists_audit_not_quality(admission_db):
    d = admission_db
    old = deepcopy(d.report.structured_output)
    await pipelines._ensure_project_invariants(d.session, d.project, d.settings)
    assert d.project.invariants_json
    audits = list(await d.session.scalars(select(WorkflowRunModel).where(
        WorkflowRunModel.workflow_type == 'project_invariant_initialization')))
    assert len(audits) == 1 and audits[0].status == 'completed'
    receipt = audits[0].metadata_json['invariant_initialization']
    assert receipt['changed'] is True and receipt['body_gate_pass_asserted'] is False
    assert d.report.structured_output == old
    await pipelines._ensure_project_invariants(d.session, d.project, d.settings)
    assert len(list(await d.session.scalars(select(WorkflowRunModel).where(
        WorkflowRunModel.workflow_type == 'project_invariant_initialization')))) == 1
    async with d.factory() as reader:
        assert (await reader.get(ProjectModel, d.project.id)).invariants_json == d.project.invariants_json


async def test_actual_chapter_entry_rejects_invalid_before_paid_backfill(admission_db, monkeypatch):
    d = admission_db
    session, settings, project, chapter, draft = d.session, d.settings, d.project, d.chapter, d.draft
    # Use the complete SQLite schema, including operator control/workflow
    # tables, and chapter 1 so unrelated prefix KG recovery cannot mask the
    # invalid-invariant error with its own failure. Provider dispatch is denied
    # by admission_db as well as the backfill boundary below.
    original_body = draft.content_md
    original_budget = (d.budget.calls_used, d.budget.tokens_charged, deepcopy(d.budget.metadata_json))
    chapter.metadata_json = {}
    project.invariants_json = {}
    await session.commit()
    forbidden = AsyncMock(side_effect=AssertionError('Invalid contract reached provider-capable backfill'))
    monkeypatch.setattr(pipelines, '_ensure_emotion_kernel_backfill_for_pipeline', forbidden)
    with pytest.raises(InvariantSeedError, match="Invalid invariants payload"):
        await pipelines.run_chapter_pipeline.__wrapped__(
            session, settings, project.slug, chapter.chapter_number,
            chapter_first=True, supersede_pending_rewrites=False,
        )
    forbidden.assert_not_awaited()
    assert project.invariants_json == {} and draft.is_current
    async with d.factory() as reader:
        assert (await reader.get(ProjectModel, project.id)).invariants_json == {}
        stored = await reader.get(ChapterDraftVersionModel, draft.id)
        assert stored.is_current and stored.content_md == original_body
        budget = await reader.get(ExecutionBudgetModel, d.budget.scope_id)
        assert (budget.calls_used, budget.tokens_charged, budget.metadata_json) == original_budget
