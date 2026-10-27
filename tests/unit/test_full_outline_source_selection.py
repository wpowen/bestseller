"""Current projection markers choose exact reviewed history, never latest by date."""
# ruff: noqa: F811 -- Imported pytest fixtures.
from copy import deepcopy
from uuid import uuid4

import pytest
from sqlalchemy import select
from test_fanqie_materialized_plan_resume import (  # noqa: F401
    integrated_modules, materialized_case, plan_case, store,
)
from bestseller.infra.db import models as db
from bestseller.services.chapter_outline_readiness import outline_prefix_projection_hash
from bestseller.services.prompt_source_policy import content_hash

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


async def install_markers(c):
    async with c.factory() as session:
        source=await session.get(db.PlanningArtifactVersionModel,c.original.outline.id)
        source.source_run_id=c.stable_id
        chapters=list(await session.scalars(select(db.ChapterModel).where(db.ChapterModel.project_id==c.pid)))
        for chapter in chapters:
            scenes=list(await session.scalars(select(db.SceneCardModel).where(db.SceneCardModel.chapter_id==chapter.id).order_by(db.SceneCardModel.scene_number)))
            chapter.metadata_json={**chapter.metadata_json,'outline_source_admission':{
                'artifact_id':str(source.id),'workflow_run_id':str(c.stable_id),
                'projection_hash':outline_prefix_projection_hash(chapter,scenes),
                'source_content_sha256':content_hash(source.content)}}
        extra=db.PlanningArtifactVersionModel(project_id=c.pid,artifact_type='chapter_outline_batch',
            version_no=99,status='approved',schema_version='1.0',content=deepcopy(source.content))
        session.add(extra);await session.commit()
        return extra.id


async def read(c):
    async with c.factory() as session:
        project=await session.get(db.ProjectModel,c.pid)
        return await c.helper.read_existing_complete_fanqie_plan(session,c.settings,project=project)


async def test_current_all_chapter_marker_preserves_and_ignores_unselected_approved_history(plan_case):
    c=plan_case;extra=await install_markers(c)
    result=await read(c)
    assert result['source_artifact_id']==str(c.original.outline.id)
    assert result['hard_admission_workflow_run_id']==c.stable_id
    assert result['chapters_created']==result['scenes_created']==0
    assert len(c.sent)==1
    async with c.factory() as session:
        assert (await session.get(db.PlanningArtifactVersionModel,extra)).status=='approved'
        assert (await session.get(db.PlanningArtifactVersionModel,c.original.outline.id)).status=='approved'
        assert (await session.get(db.ChapterDraftVersionModel,c.draft_id)).content_md=='The original observer records a faint winter signal.'


@pytest.mark.parametrize('fault',['missing','mixed_source','wrong_owner','wrong_hash','wrong_projection','empty_marker'])
async def test_marker_ambiguity_or_changed_proof_never_falls_back_to_another_version(plan_case,fault):
    c=plan_case;await install_markers(c)
    async with c.factory() as session:
        chapter=await session.get(db.ChapterModel,c.original.chapters[0].id)
        meta=deepcopy(chapter.metadata_json);marker=meta['outline_source_admission']
        if fault=='missing':del meta['outline_source_admission']
        if fault=='empty_marker':meta['outline_source_admission']={}
        if fault=='mixed_source':marker['artifact_id']=str(uuid4())
        if fault=='wrong_owner':marker['workflow_run_id']=str(uuid4())
        if fault=='wrong_hash':marker['source_content_sha256']='0'*64
        if fault=='wrong_projection':marker['projection_hash']='0'*64
        chapter.metadata_json=meta;await session.commit()
    with pytest.raises(ValueError,match='full_source_selection'):
        await read(c)
    assert len(c.sent)==1


async def test_context_entry_selects_bound_plan_and_revalidates_paid_proof(plan_case):
    from bestseller.services.outline_source_entry import (
        current_outline_artifact, require_current_outline_source_admission,
    )
    c=plan_case
    extra=await install_markers(c)
    async with c.factory() as session:
        project=await session.get(db.ProjectModel,c.pid)
        chapter=await session.get(db.ChapterModel,c.original.chapters[0].id)
        selected=await current_outline_artifact(session,project=project,chapter=chapter)
        assert selected.id==c.original.outline.id and selected.id!=extra
        receipt=await require_current_outline_source_admission(session,c.settings,
            project=project,chapter=chapter)
        assert receipt['workflow_run_id']==str(c.stable_id)
    assert len(c.sent)==1


async def test_source_binding_for_fresh_task_retains_current_plan_without_rebinding_history(plan_case):
    from bestseller.services.reviews import rewrite_source_binding
    c=plan_case
    await install_markers(c)
    async with c.factory() as session:
        chapter=await session.get(db.ChapterModel,c.original.chapters[0].id)
        draft=await session.get(db.ChapterDraftVersionModel,c.draft_id)
        original_body=draft.content_md
        value=rewrite_source_binding(draft,chapter=chapter)
        assert value['source_plan_binding']==chapter.metadata_json['outline_source_admission']
        assert value['parent_draft_id']==str(c.draft_id)
        assert draft.content_md==original_body
        value['source_plan_binding']['projection_hash']='0'*64
        assert chapter.metadata_json['outline_source_admission']['projection_hash']!='0'*64


@pytest.mark.parametrize('target',['chapter','scene'])
async def test_public_reviewer_rejects_wrong_marker_before_context_fallback_or_model(plan_case,monkeypatch,target):
    from unittest.mock import AsyncMock
    from bestseller.services import reviews
    c=plan_case
    await install_markers(c)
    async with c.factory() as session:
        project=await session.get(db.ProjectModel,c.pid)
        chapter=await session.get(db.ChapterModel,c.original.chapters[0].id)
        scenes=list(await session.scalars(select(db.SceneCardModel).where(db.SceneCardModel.chapter_id==chapter.id)))
        draft=await session.get(db.ChapterDraftVersionModel,c.draft_id)
        metadata=deepcopy(chapter.metadata_json)
        metadata['outline_source_admission']['workflow_run_id']=str(uuid4())
        chapter.metadata_json=metadata
        await session.commit()
        async def forbidden(*args,**kwargs):raise AssertionError('review reached context/model with stale plan')
        monkeypatch.setattr(reviews,'build_chapter_writer_context',forbidden)
        monkeypatch.setattr(reviews,'build_scene_writer_context',forbidden)
        monkeypatch.setattr(reviews,'complete_text',forbidden)
        if target=='chapter':
            monkeypatch.setattr(reviews,'_load_chapter_context',AsyncMock(return_value=(project,chapter,None,scenes,draft)))
            call=reviews.review_chapter_draft(session,c.settings,project.slug,chapter.chapter_number)
        else:
            monkeypatch.setattr(reviews,'_load_scene_context',AsyncMock(return_value=(project,chapter,scenes[0],None,draft)))
            call=reviews.review_scene_draft(session,c.settings,project.slug,chapter.chapter_number,scenes[0].scene_number)
        with pytest.raises(ValueError,match='outline_source_admission_required'):await call
    assert len(c.sent)==1
