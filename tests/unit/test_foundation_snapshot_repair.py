from __future__ import annotations

import copy
from types import SimpleNamespace
from uuid import uuid4

import pytest

from bestseller.services import pipelines
from bestseller.services.book_design import ensure_project_book_design_snapshot, assert_current_world_snapshot
from test_pipeline_services import build_project, build_settings, FakeSession


class Session:
    def __init__(self, draft=None):
        self.draft = draft

    async def scalar(self, statement):
        return self.draft


def project_and_world(*, stale=False):
    project = build_project()
    snapshot = ensure_project_book_design_snapshot(project)
    world = SimpleNamespace(status='approved', content={'_meta': {
        'source_snapshot_hash': 'superseded' if stale else snapshot.source_hash,
    }})
    return project, world


def patch_lookup(monkeypatch, world):
    async def latest(*args, **kwargs):
        return world
    monkeypatch.setattr(pipelines, 'get_latest_approved_planning_artifact', latest)


@pytest.mark.asyncio
@pytest.mark.parametrize('stale', [True, False])
async def test_only_stale_world_requests_zero_prose_rebuild(monkeypatch, stale):
    project, world = project_and_world(stale=stale)
    before = copy.deepcopy(project.metadata_json)
    patch_lookup(monkeypatch, world)
    assert await pipelines._world_snapshot_requires_foundation_repair(Session(), project=project) is stale
    assert project.metadata_json == before


@pytest.mark.asyncio
async def test_any_draft_prevents_snapshot_rebuild(monkeypatch):
    project, world = project_and_world(stale=True)
    patch_lookup(monkeypatch, world)
    with pytest.raises(ValueError, match='WORLD_SPEC_SNAPSHOT_MISMATCH'):
        await pipelines._world_snapshot_requires_foundation_repair(Session(uuid4()), project=project)


@pytest.mark.asyncio
async def test_corrupt_snapshot_is_not_rebuilt(monkeypatch):
    project, world = project_and_world(stale=True)
    project.metadata_json['book_design_snapshot']['source_hash'] = '0' * 64
    patch_lookup(monkeypatch, world)
    with pytest.raises(ValueError, match='snapshot hash mismatch'):
        await pipelines._world_snapshot_requires_foundation_repair(Session(), project=project)


@pytest.mark.asyncio
@pytest.mark.parametrize('has_draft', [False, True])
async def test_failed_rebuild_cannot_be_hidden_by_old_readiness(monkeypatch, has_draft):
    project, world = project_and_world()
    project.metadata_json['prewrite_readiness_report'] = {'passed': True}
    patch_lookup(monkeypatch, world)
    class FailedRunSession:
        def __init__(self):
            self.values = iter([SimpleNamespace(status='failed'), uuid4() if has_draft else None])
        async def scalar(self, statement):
            return next(self.values)
    if has_draft:
        with pytest.raises(ValueError, match='FOUNDATION_REPAIR_AFTER_PROSE_FORBIDDEN'):
            await pipelines._world_snapshot_requires_foundation_repair(FailedRunSession(), project=project)
    else:
        assert await pipelines._world_snapshot_requires_foundation_repair(FailedRunSession(), project=project)


@pytest.mark.asyncio
@pytest.mark.parametrize('interrupted', [False, True])
async def test_foundation_cast_cleanup_preserves_generated_world_source(monkeypatch, interrupted):
    project, world = project_and_world()
    before = copy.deepcopy(project.metadata_json['book_design_snapshot'])
    settings = build_settings()
    cast = SimpleNamespace(id=uuid4(), content={'protagonist': {'name': '沈砚'}})

    async def get_project(*args):
        return project
    async def latest(*args, artifact_type, **kwargs):
        return cast if artifact_type == pipelines.ArtifactType.CAST_SPEC else None
    async def foundation(*args, **kwargs):
        assert project.metadata_json['foundation_truth_status'] == 'superseded_pending_materialization'
        if interrupted:
            raise RuntimeError('interrupted_foundation')
        return SimpleNamespace(workflow_run_id=uuid4(), volume_count=1)
    async def cleanup(session, project, **kwargs):
        # Mirrors the runtime identity cleanup that formerly changed the source
        # snapshot when the pipeline rebuilt it after generating WorldSpec.
        project.metadata_json = {**project.metadata_json, 'entities': []}
        project.metadata_json.pop('identity_manifest', None)
        return {}
    async def materialize(*args, **kwargs):
        assert project.metadata_json['book_design_snapshot'] == before
        assert_current_world_snapshot(project, world)
        raise RuntimeError('verified_materialization_boundary')
    async def noop(*args, **kwargs):
        return None

    monkeypatch.setattr(pipelines, 'get_project_by_slug', get_project)
    monkeypatch.setattr(pipelines, 'get_latest_approved_planning_artifact', latest)
    monkeypatch.setattr(pipelines, 'generate_foundation_plan', foundation)
    monkeypatch.setattr(pipelines, '_checkpoint_commit', noop)
    monkeypatch.setattr(pipelines, 'get_latest_completed_workflow_run', noop)
    monkeypatch.setattr(pipelines, 'materialize_latest_story_bible', materialize)
    monkeypatch.setattr('bestseller.services.story_bible.supersede_materialized_cast_for_foundation', cleanup)
    payload = pipelines.ProjectCreate(slug=project.slug, title=project.title, genre=project.genre,
        target_word_count=project.target_word_count, target_chapters=project.target_chapters)
    with pytest.raises(RuntimeError, match='interrupted_foundation' if interrupted else 'verified_materialization_boundary'):
        await pipelines.run_progressive_autowrite_pipeline(FakeSession(), settings,
            project_payload=payload, premise='approved premise', force_foundation_replan=True)
    assert project.metadata_json['foundation_truth_status'] == 'superseded_pending_materialization'
