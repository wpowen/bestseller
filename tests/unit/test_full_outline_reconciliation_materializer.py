"""Full-source reconciliation retains bodies/history and normal gate boundaries."""
from copy import deepcopy
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.schema import CreateTable
from test_adopted_knowledge_lineage import db  # noqa: F401
from test_unadopted_outline_reconciliation import migration, _raw  # noqa: F401

from bestseller.infra.db import models
from bestseller.services import materialized_outline_revision as revision
from bestseller.services import workflows
from bestseller.services.chapter_outline_readiness import outline_prefix_projection_hash
from bestseller.services.prompt_source_policy import content_hash

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@pytest.fixture
async def full_case(migration, monkeypatch):  # noqa: F811 - imported fixture
    r = migration
    conn = await r.session.connection()
    ddl = str(CreateTable(models.BookProductionControlModel.__table__, if_not_exists=True)
        .compile(dialect=conn.dialect))
    await conn.exec_driver_sql(ddl.replace('::jsonb',''))
    r.source.content = {**r.batch.model_dump(mode='json'), '_meta': {
        'full_outline_revision': {'schema': revision.REVISION_SCHEMA}}}
    r.chapter.metadata_json = {**r.chapter.metadata_json,
        'outline_prefix_readiness': {'artifact_id': str(uuid4()), 'projection_hash': 'old-prefix'}}
    await r.session.commit()
    for row in [r.chapter,*r.scenes,*r.drafts,*r.scene_drafts]:
        await r.session.refresh(row)
    proof = {'schema': revision.REVISION_SCHEMA, 'workflow_run_id': str(uuid4()),
        'source_artifact_id': str(r.source.id),
        'source_content_sha256': content_hash(r.source.content),
        'candidate_hash': content_hash(r.batch.model_dump(mode='json')),
        'chapter_numbers': [2], 'source_admission': {'draft_eligible': True,
            'schema': 'boundary-fixture-only; shared native proof tested separately'}}
    calls=[]
    async def validate(session, settings, project, artifact):
        assert session is r.session and artifact.id == r.source.id
        calls.append(deepcopy(proof))
        return deepcopy(proof)
    monkeypatch.setattr(revision,'validate_full_outline_revision_for_materialization',validate)
    return r, proof, calls


async def run(r, **kwargs):
    return await workflows.materialize_chapter_outline_batch(r.session, r.project.slug,
        r.batch, source_artifact_id=r.source.id, reconcile_unadopted_chapter_numbers=[2],
        requested_by='source-reconciliation-fixture', **kwargs)


async def test_full_source_preserves_all_bodies_raw_preimages_and_supersedes_prefix(full_case):
    r, proof, calls = full_case
    bodies = [_raw(x) for x in [*r.drafts,*r.scene_drafts]]
    before = deepcopy(r.chapter.metadata_json)
    result = await run(r)
    await r.session.commit()
    await r.session.refresh(r.chapter)
    for x in [*r.drafts,*r.scene_drafts]: await r.session.refresh(x)
    assert bodies == [_raw(x) for x in [*r.drafts,*r.scene_drafts]]
    marker = r.chapter.metadata_json['outline_source_admission']
    assert marker == {'artifact_id':str(r.source.id),
        'workflow_run_id':proof['workflow_run_id'],
        'projection_hash':outline_prefix_projection_hash(r.chapter,r.scenes),
        'source_content_sha256':proof['source_content_sha256']}
    assert 'outline_prefix_readiness' not in r.chapter.metadata_json
    assert before['outline_prefix_readiness'] in r.chapter.metadata_json['outline_prefix_readiness_history']
    owner = await r.session.get(models.WorkflowRunModel,result.workflow_run_id)
    event=owner.metadata_json['unadopted_outline_reconciliation_history'][0]
    assert event['previous_raw']['chapter']['chapter_goal']=='Old goal'
    assert event['source_admission_receipt']==proof
    assert 'prefix_receipt' not in event
    assert len(calls)==2


async def test_full_reconciliation_rechecks_after_provider_phase_and_never_writes_stale_proof(full_case,monkeypatch):
    r, proof, calls=full_case
    before=[_raw(x) for x in [r.chapter,*r.scenes,*r.drafts]]
    original=revision.validate_full_outline_revision_for_materialization
    async def changed(*args,**kwargs):
        result=await original(*args,**kwargs)
        if len(calls)>1:result['candidate_hash']='0'*64
        return result
    monkeypatch.setattr(revision,'validate_full_outline_revision_for_materialization',changed)
    with pytest.raises(ValueError,match='changed_during_review'):await run(r)
    assert [_raw(x) for x in [r.chapter,*r.scenes,*r.drafts]]==before


async def test_full_source_requires_exact_whole_scope_before_materializer_creation(full_case):
    r, proof, calls=full_case
    proof['chapter_numbers']=[1,2]
    with pytest.raises(ValueError,match='exact_scope'):await run(r)
    assert not list(await r.session.scalars(select(models.WorkflowRunModel)))


async def test_completed_projection_resume_does_not_repeat_gate_or_identity(full_case,monkeypatch):
    r, proof, calls=full_case
    result=await run(r);await r.session.commit()
    from bestseller.services import materialized_outline_source_recovery as recovery
    checks=[]
    async def readback(session,settings,**kwargs):
        assert kwargs['materialization_workflow_id']==result.workflow_run_id
        assert kwargs['artifact_id']==r.source.id
        checks.append(kwargs)
    monkeypatch.setattr(recovery,'_read_sources',readback)
    async def forbidden(*args,**kwargs):raise AssertionError('completed projection reran gates')
    monkeypatch.setattr(workflows,'_prepare_unadopted_outline_reconciliation',forbidden)
    again=await run(r)
    assert again.workflow_run_id==result.workflow_run_id and len(checks)==1
    assert len(list(await r.session.scalars(select(models.WorkflowRunModel))))==1
