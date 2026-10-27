"""Rejected revisions must not be reported as completed rewrites."""
from types import SimpleNamespace
from uuid import uuid4

import pytest

from bestseller.domain.enums import WorkflowStatus
from bestseller.services import pipelines

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
@pytest.mark.parametrize('failed', [True, False])
async def test_rewrite_receipt_and_event_agree_with_task_outcome(monkeypatch, failed):
    draft = SimpleNamespace(id=uuid4(), version_no=2, word_count=1617)
    evidence = {'takeover_violation_codes': ['min_word_count'], 'took_current_decision': False}
    task = SimpleNamespace(
        id=uuid4(), status='failed' if failed else 'completed',
        error_log='quality gate rejected; current draft preserved' if failed else None,
        metadata_json=evidence,
    )
    steps, events = [], []

    async def record(session, **kwargs):
        steps.append(kwargs)

    monkeypatch.setattr(pipelines, 'create_workflow_step_run', record)
    await pipelines._record_chapter_rewrite_outcome(
        None, workflow_run_id=uuid4(), step_name='rewrite_chapter_v1', step_order=4,
        chapter_draft=draft, rewrite_task=task, project_slug='fixture', chapter_number=2,
        iteration=1, progress=lambda stage, payload: events.append((stage, payload)),
    )
    assert len(steps) == len(events) == 1
    step = steps[0]
    assert step['status'] == (WorkflowStatus.FAILED if failed else WorkflowStatus.COMPLETED)
    assert events[0][0] == ('chapter_rewrite_rejected' if failed else 'chapter_rewrite_completed')
    assert step['output_ref']['chapter_draft_id'] == str(draft.id)
    assert step['output_ref']['word_count'] == 1617
    assert step['error_message'] == task.error_log
    if failed:
        assert step['output_ref']['current_draft_preserved'] is True
        assert step['output_ref']['rejection_evidence'] == evidence
    assert task.status == ('failed' if failed else 'completed')
