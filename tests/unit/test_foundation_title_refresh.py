"""Progressive planning invokes title review but packaging failure stays P1."""
from __future__ import annotations

import ast
import inspect
from types import SimpleNamespace
from uuid import uuid4

import pytest

from bestseller.services import planner

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
@pytest.mark.parametrize('revised', [False, True])
async def test_shared_title_review_preserves_result_and_receipt(monkeypatch, revised):
    project = SimpleNamespace(title='街口维修铺', metadata_json={'other': 'preserved'})
    run_id = uuid4() if revised else None
    expected = '修好这一家' if revised else project.title
    calls = []

    async def refresh(*args, **kwargs):
        calls.append(kwargs)
        if revised:
            project.title = expected
        evidence = {'skipped': '' if revised else 'current_title_passed_full_title_workflow'}
        return expected, revised, run_id, evidence

    monkeypatch.setattr(planner, '_refresh_story_grounded_project_title', refresh)
    result = await planner._refresh_foundation_project_title(
        None, SimpleNamespace(), project=project, premise='修理工收徒', workflow_run_id=uuid4(),
    )
    assert result[:3] == (expected, revised, run_id)
    assert project.title == expected
    assert len(calls) == 1
    assert calls[0]['premise'] == '修理工收徒'
    assert project.metadata_json['other'] == 'preserved'
    assert result[3]['retryable'] is False


@pytest.mark.asyncio
@pytest.mark.parametrize('raises', [False, True])
async def test_failed_revision_records_retry_without_blocking_foundation(monkeypatch, raises):
    project = SimpleNamespace(title='我用旧账本逼出旧账本', metadata_json={})
    original = project.title

    async def refresh(*args, **kwargs):
        if raises:
            raise RuntimeError('provider unavailable')
        return original, False, None, {'blocked': 'title_workflow_primary_not_pass'}

    monkeypatch.setattr(planner, '_refresh_story_grounded_project_title', refresh)
    result = await planner._refresh_foundation_project_title(
        None, SimpleNamespace(), project=project,
    )
    assert result[:3] == (original, False, None)
    assert project.title == original
    assert result[3]['quality_status'] == 'needs_revision'
    assert result[3]['retryable'] is True
    assert result[3]['readability_issue'] == 'repeated_method_result'
    assert project.metadata_json['title_revision_status'] == result[3]


def test_progressive_foundation_reviews_title_before_compiling_promotional_brief():
    tree = ast.parse(inspect.getsource(planner.generate_foundation_plan))
    calls = {
        node.func.id: node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert calls['_run_prewrite_readiness_gate'] < calls['_refresh_foundation_project_title']
    assert calls['_refresh_foundation_project_title'] < calls['_generate_promotional_brief']
