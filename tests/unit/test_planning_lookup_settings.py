"""Explicit settings reach selected-source validation; legacy callers remain valid."""
from types import SimpleNamespace
from uuid import uuid4
import pytest
from sqlalchemy import JSON, MetaData, ForeignKeyConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from bestseller.infra.db.models import ProjectModel, PlanningArtifactVersionModel, WorkflowRunModel, ChapterDraftVersionModel
from bestseller.domain.enums import ArtifactType
from bestseller.services import workflows, foundation_selection, pipelines
from bestseller.settings import load_settings

pytestmark=[pytest.mark.unit,pytest.mark.asyncio]

@pytest.fixture
async def lookup_case(tmp_path,monkeypatch):
    path=tmp_path/'planning_lookup_settings_test.sqlite3'
    settings=load_settings(env={});settings.database.url=f'sqlite+aiosqlite:///{path}'
    settings.artifact_store.local_dir=str(tmp_path/'explicit-retained')
    engine=create_async_engine(settings.database.url)
    async with engine.begin() as c:
        assert (await c.execute(text('PRAGMA database_list'))).first()[2]==str(path)
        for model in (ProjectModel,WorkflowRunModel,ChapterDraftVersionModel):
            table=model.__table__.to_metadata(MetaData())
            for col in table.c:
                if isinstance(col.type,JSONB):col.type=JSON()
                if col.server_default is not None and '::' in str(col.server_default.arg):col.server_default=None
            for constraint in list(table.constraints):
                if isinstance(constraint,ForeignKeyConstraint):table.constraints.remove(constraint)
            await c.run_sync(table.create)
    project=ProjectModel(id=uuid4(),slug='unrelated-retained-settings',title='Tide station',genre='mystery',
        target_chapters=8,target_word_count=20000,metadata_json={'active_foundation_selection':{'fixture_pointer':True}})
    artifact=PlanningArtifactVersionModel(id=uuid4(),project_id=project.id,artifact_type='world_spec',
        status='approved',version_no=1,content={})
    factory=async_sessionmaker(engine,expire_on_commit=False)
    async with factory() as s:s.add(project);await s.commit()
    seen=[]
    async def selected(session,*,project,settings):
        seen.append(settings)
        return {'world_spec':artifact}
    monkeypatch.setattr(foundation_selection,'load_selected_foundation_artifacts',selected)
    yield SimpleNamespace(settings=settings,factory=factory,project=project,artifact=artifact,seen=seen)
    await engine.dispose()

async def test_explicit_configuration_never_reloads_ambient(lookup_case,monkeypatch):
    h=lookup_case
    def forbidden():raise AssertionError('ambient settings must not replace explicit settings')
    monkeypatch.setattr(workflows,'load_settings',forbidden)
    async with h.factory() as s:
        row=await workflows.get_latest_approved_planning_artifact(s,project_id=h.project.id,
            artifact_type=ArtifactType.WORLD_SPEC,settings=h.settings)
    assert row is h.artifact and h.seen==[h.settings]

async def test_old_call_without_settings_uses_existing_runtime_fallback(lookup_case,monkeypatch):
    h=lookup_case;ambient=h.settings.model_copy(deep=True);effective=ambient.model_copy(deep=True)
    monkeypatch.setattr(workflows,'load_settings',lambda:ambient)
    def profile(given):assert given is ambient;return effective
    monkeypatch.setattr(workflows,'apply_runtime_llm_profile',profile)
    async with h.factory() as s:
        await workflows.get_latest_approved_planning_artifact(s,project_id=h.project.id,artifact_type=ArtifactType.WORLD_SPEC)
    assert h.seen==[effective]

@pytest.mark.parametrize('fault',['missing','invalid'])
async def test_explicit_configuration_cannot_bypass_selected_source_failure(lookup_case,monkeypatch,fault):
    h=lookup_case
    async def selected(session,*,project,settings):
        assert settings is h.settings
        if fault=='invalid':raise ValueError('physical_proof_mismatch')
        return None
    monkeypatch.setattr(foundation_selection,'load_selected_foundation_artifacts',selected)
    async with h.factory() as s:
        with pytest.raises(ValueError,match='physical_proof_mismatch|foundation_selection_pointer_unresolved'):
            await workflows.get_latest_approved_planning_artifact(s,project_id=h.project.id,
                artifact_type=ArtifactType.WORLD_SPEC,settings=h.settings)

async def test_structural_resume_probe_forwards_explicit_settings(lookup_case,monkeypatch):
    h=lookup_case;seen=[]
    async def lookup(session,*,project_id,artifact_type,settings):
        assert project_id==h.project.id and artifact_type==ArtifactType.WORLD_SPEC
        seen.append(settings);return None
    monkeypatch.setattr(pipelines,'get_latest_approved_planning_artifact',lookup)
    async with h.factory() as s:
        assert not await pipelines._world_snapshot_requires_foundation_repair(s,project=h.project,settings=h.settings)
    assert seen==[h.settings]

async def test_current_world_consumer_forwards_same_explicit_settings(lookup_case,monkeypatch):
    h=lookup_case;seen=[]
    async def lookup(session,*,project_id,artifact_type,settings):
        seen.append(settings);return h.artifact
    monkeypatch.setattr(pipelines,'get_latest_approved_planning_artifact',lookup)
    monkeypatch.setattr(pipelines,'assert_current_world_snapshot',lambda project,artifact:artifact)
    async with h.factory() as s:
        assert await pipelines._get_current_world_spec_artifact(s,project=h.project,settings=h.settings) is h.artifact
    assert seen==[h.settings]

@pytest.mark.parametrize('fault',[None,'no_pin','different_world','newer_failure','late_failure','selected_not_complete'])
async def test_exact_later_selected_cohort_supersedes_only_older_failed_generation(lookup_case,monkeypatch,fault):
    from datetime import datetime,timezone,timedelta
    h=lookup_case;now=datetime.now(timezone.utc)
    failed=WorkflowRunModel(id=uuid4(),project_id=h.project.id,workflow_type='generate_foundation_plan',
        status='failed',scope_type='project',scope_id=h.project.id,created_at=now-timedelta(hours=2),updated_at=now-timedelta(hours=1))
    selected=WorkflowRunModel(id=uuid4(),project_id=h.project.id,workflow_type='complete_foundation_revision',
        status='completed',scope_type='project',scope_id=h.project.id,created_at=now,updated_at=now)
    h.artifact.source_run_id=selected.id
    h.project.metadata_json={'active_foundation_selection':{'workflow_id':str(selected.id),
        'artifact_ids':{'world_spec':str(h.artifact.id)}}}
    if fault=='no_pin':h.project.metadata_json={}
    if fault=='different_world':h.project.metadata_json['active_foundation_selection']['artifact_ids']['world_spec']=str(uuid4())
    if fault=='newer_failure':failed.created_at=now+timedelta(minutes=1);failed.updated_at=failed.created_at
    if fault=='late_failure':failed.updated_at=now+timedelta(minutes=1)
    if fault=='selected_not_complete':selected.status='running'
    async def getter(session,*,project_id,artifact_type,settings):
        assert settings is h.settings
        return h.artifact  # Physical verification is exercised with restored real N separately.
    monkeypatch.setattr(pipelines,'get_latest_approved_planning_artifact',getter)
    async with h.factory() as s:
        s.add_all([failed,selected]);await s.commit()
        needed=await pipelines._world_snapshot_requires_foundation_repair(s,project=h.project,settings=h.settings)
        assert needed is (fault is not None)
        assert (await s.get(WorkflowRunModel,failed.id)).status=='failed'
