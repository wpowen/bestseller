"""A platform matrix cannot silently replace the candidate under revision."""
from types import SimpleNamespace
from uuid import uuid4

import pytest

from bestseller.services import planner

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
@pytest.mark.parametrize('lineage_mismatch', [False, True])
@pytest.mark.parametrize('candidate_decision', ['pass', 'revise', 'reject', None])
async def test_full_refresh_adopts_only_the_reviewed_candidate(
    monkeypatch, candidate_decision, lineage_mismatch,
):
    old = '开局修表逆袭' if lineage_mismatch else '我用旧账本逼出旧账本'
    model_title = '老钟修好之前'
    template = '开局维修铺逆袭'
    project = SimpleNamespace(
        id=uuid4(), slug='fixture', language='zh-CN', title=old,
        metadata_json={'unrelated': 'preserved'},
    )
    if lineage_mismatch:
        project.metadata_json['title_revision_status'] = {'adopted_title': '回不去的钟声'}
    settings = SimpleNamespace(generation=SimpleNamespace(title_llm_revision_enabled=True))
    run_id = uuid4()
    monkeypatch.setattr(planner, '_build_story_grounded_title_profile', lambda *a, **k: {
        'primary_title': old, 'title_anchor_groups': {'object': ['老钟']},
    })
    monkeypatch.setattr(
        planner, 'build_story_grounded_title_revision_messages', lambda *a, **k: ('sys', 'user'),
    )
    monkeypatch.setattr(planner, 'finalize_revised_title', lambda *a, **k: (model_title, True))

    async def complete(*a, **k):
        return SimpleNamespace(content=model_title, llm_run_id=run_id)

    monkeypatch.setattr(planner, 'complete_text', complete)

    def workflow(profile, **kwargs):
        evaluations = {} if candidate_decision is None else {
            model_title: {'decision': candidate_decision},
        }
        return {
            'recommended_primary_title': {
                'title': old if lineage_mismatch and profile['primary_title'] == old else template,
                'title_evaluation': {'decision': 'pass'},
            },
            'candidate_evaluations': evaluations,
            'candidates': [{'title': template}],
            'platform_label': '测试平台',
        }

    monkeypatch.setattr(planner, 'build_platform_title_workflow', workflow)
    result = await planner._refresh_foundation_project_title(
        None, settings, project=project, premise='修钟匠修复老钟', book_spec_payload={},
        cast_spec_payload={}, story_design_payload={}, volume_plan_payload=[],
        prewrite_payload=None, workflow_run_id=uuid4(),
    )
    assert result[2] == run_id
    assert project.metadata_json['unrelated'] == 'preserved'
    assert project.title != template
    evidence = project.metadata_json['title_revision_status']
    if candidate_decision == 'pass':
        assert result[:2] == (model_title, True)
        assert project.title == model_title
        assert evidence['adopted_title'] == model_title
        assert evidence['was_revised'] is True
        assert project.metadata_json['title_workflow_primary']['title'] == model_title
        recommended = project.metadata_json['title_workflow']['recommended_primary_title']
        assert recommended['title'] == model_title
    else:
        assert result[:2] == (old, False)
        assert project.title == old
        assert evidence['adopted_title'] == old
        assert evidence['rejected_title'] == model_title
        assert evidence['was_revised'] is False
        assert evidence['quality_status'] == 'needs_revision'
        assert 'title_workflow_primary' not in project.metadata_json


@pytest.mark.asyncio
@pytest.mark.parametrize('pending', [
    {'quality_status': 'needs_revision'}, {'blocked': 'quality_failed'}, {'retryable': True},
    {
        'quality_status': 'unchanged_or_revised', 'retryable': False,
        'llm_run_id': str(uuid4()), 'was_revised': False,
    },
])
async def test_pending_quality_review_bypasses_static_pass_and_returns_feedback(
    monkeypatch, pending,
):
    old, rejected, new = '街口钟表铺', '开局钟表铺逆袭', '老钟修好之前'
    feedback = '补充人物实际动作, 删除泛化逆袭承诺'
    project = SimpleNamespace(
        id=uuid4(), slug='fixture', language='zh-CN', title=old,
        metadata_json={
            'title_workflow': {}, 'title_candidates': [],
            'title_revision_status': {
                **pending, 'adopted_title': old, 'rejected_title': rejected,
                'title_evaluation': {'feedback': {'revision_prompt': feedback}},
            },
        },
    )
    monkeypatch.setattr(planner, '_build_story_grounded_title_profile', lambda *a, **k: {
        'primary_title': old,
    })
    monkeypatch.setattr(
        planner, 'build_story_grounded_title_revision_messages', lambda *a, **k: ('sys', 'base'),
    )
    monkeypatch.setattr(planner, 'finalize_revised_title', lambda *a, **k: (new, True))
    monkeypatch.setattr(planner, 'build_platform_title_workflow', lambda p, **k: {
        'recommended_primary_title': {
            'title': p['primary_title'], 'title_evaluation': {'decision': 'pass'},
        },
        'candidate_evaluations': {new: {'decision': 'pass'}},
    })
    requests = []

    async def complete(session, settings, request):
        requests.append(request)
        return SimpleNamespace(content=new, llm_run_id=uuid4())

    monkeypatch.setattr(planner, 'complete_text', complete)
    result = await planner._refresh_foundation_project_title(
        None, SimpleNamespace(generation=SimpleNamespace(title_llm_revision_enabled=True)),
        project=project, premise='修钟匠修复老钟', book_spec_payload={}, cast_spec_payload={},
        story_design_payload={}, volume_plan_payload=[], prewrite_payload=None,
        workflow_run_id=uuid4(),
    )
    assert result[:2] == (new, True)
    assert len(requests) == 1
    assert requests[0].user_prompt.startswith('base')
    assert rejected in requests[0].user_prompt
    assert feedback in requests[0].user_prompt


@pytest.mark.asyncio
async def test_no_adoptable_candidate_does_not_clear_pending_quality(monkeypatch):
    old = '街口修表逆袭'
    raw = '候选甲\n候选乙'
    prior_feedback = '需要具体人物行动'
    project = SimpleNamespace(
        id=uuid4(), slug='fixture', language='zh-CN', title=old,
        metadata_json={'title_revision_status': {
            'quality_status': 'needs_revision', 'retryable': True,
            'title_evaluation': {'feedback': {'revision_prompt': prior_feedback}},
        }},
    )
    monkeypatch.setattr(planner, '_build_story_grounded_title_profile', lambda *a, **k: {})
    monkeypatch.setattr(
        planner, 'build_story_grounded_title_revision_messages', lambda *a, **k: ('sys', 'user'),
    )
    monkeypatch.setattr(planner, 'build_platform_title_workflow', lambda *a, **k: {
        'recommended_primary_title': {'title': old, 'title_evaluation': {'decision': 'pass'}},
    })
    monkeypatch.setattr(planner, 'finalize_revised_title', lambda *a, **k: (old, False))
    run_id = uuid4()

    async def complete(*args):
        return SimpleNamespace(content=raw, llm_run_id=run_id)

    monkeypatch.setattr(planner, 'complete_text', complete)
    result = await planner._refresh_foundation_project_title(
        None, SimpleNamespace(generation=SimpleNamespace(title_llm_revision_enabled=True)),
        project=project, premise='修钟匠修复老钟', book_spec_payload={}, cast_spec_payload={},
        story_design_payload={}, volume_plan_payload=[], prewrite_payload=None,
        workflow_run_id=uuid4(),
    )
    assert result[:3] == (old, False, run_id)
    evidence = project.metadata_json['title_revision_status']
    assert evidence['quality_status'] == 'needs_revision'
    assert evidence['retryable'] is True
    assert evidence['blocked'] == 'title_revision_no_candidate_adopted'
    assert evidence['rejected_candidate_text'] == raw
    assert evidence['title_evaluation']['feedback']['revision_prompt'] == prior_feedback
    assert project.title == old
