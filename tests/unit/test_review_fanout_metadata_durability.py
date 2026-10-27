"""Actual review entry + independent SQL session + fresh committed readback."""
from copy import deepcopy
import pytest
from sqlalchemy import select
from sqlalchemy.schema import CreateTable
from test_chapter_review_commercial_checkpoint import review_db  # noqa:F401
from bestseller.infra.db.models import ChapterModel, WorkflowRunModel
from bestseller.services import chapter_llm_quality_judge as commercial, reviews
from bestseller.domain.llm_quality_judge import LLMQualityJudgeResult

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@pytest.mark.parametrize('raise_after_commit',[False,True])
async def test_completed_or_partial_fanout_metadata_survives_caller_writes(review_db,monkeypatch,raise_after_commit):
    db=review_db
    async with db.session.bind.begin() as conn:
        await conn.exec_driver_sql(str(CreateTable(WorkflowRunModel.__table__).compile(dialect=conn.dialect)).replace('::jsonb',''))
    old_pointer={'workflow_run_id':'old-owner','binding_sha256':'old-proof'}
    new_pointer={'workflow_run_id':'new-owner','binding_sha256':'new-proof'}
    db.chapter.metadata_json={**db.chapter.metadata_json,'commercial_component_cohorts':{'body':old_pointer}}
    owner=WorkflowRunModel(project_id=db.project.id,workflow_type='chapter_pipeline',status='running',
        scope_type='chapter',scope_id=db.chapter.id,metadata_json={'protected_history':['keep']})
    db.session.add(owner)
    await db.session.commit()
    original_body=db.draft.content_md

    async def independent_fanout(caller,settings,**kwargs):
        assert caller is db.session and not caller.in_transaction()
        async with db.factory() as independent:
            chapter=await independent.get(ChapterModel,db.chapter.id)
            workflow=await independent.get(WorkflowRunModel,owner.id)
            chapter.metadata_json={**chapter.metadata_json,'commercial_component_cohorts':{'body':deepcopy(new_pointer)}}
            workflow.metadata_json={**workflow.metadata_json,'commercial_component_cohorts':{
                'body':{'slots':{'sample0':{'state':'completed','report':{'raw':'retained'}}}}}}
            await independent.commit()
        if raise_after_commit:
            raise RuntimeError('fixture partial fanout after durable component commit')
        return LLMQualityJudgeResult(passed=True,overall_score=.9)

    monkeypatch.setattr(commercial,'judge_chapter_commercial_quality_stable',independent_fanout)
    await reviews.review_chapter_draft(db.session,db.settings,db.project.slug,2,workflow_run_id=owner.id)
    db.chapter.metadata_json={**db.chapter.metadata_json,'caller_later_step':'retained'}
    owner.metadata_json={**owner.metadata_json,'caller_later_step':'retained'}
    await db.session.commit()
    async with db.factory() as reader:
        chapter=await reader.get(ChapterModel,db.chapter.id)
        workflow=await reader.get(WorkflowRunModel,owner.id)
        assert chapter.metadata_json['commercial_component_cohorts']['body']==new_pointer
        assert chapter.metadata_json['protected']=='keep'
        assert workflow.metadata_json['commercial_component_cohorts']['body']['slots']['sample0']['report']=={'raw':'retained'}
        assert workflow.metadata_json['protected_history']==['keep']
        assert chapter.metadata_json['caller_later_step']==workflow.metadata_json['caller_later_step']=='retained'
    assert db.draft.content_md==original_body
