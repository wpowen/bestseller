# ruff: noqa: F811
from copy import deepcopy
from sqlalchemy import select
import pytest
from test_chapter_outline_readiness import rig,sqlite_identity_store  # noqa:F401
from test_prefix_production_entry_fault_replay import entry_case  # noqa:F401
from test_prefix_window_activation_sqlite import setup
from test_chapter_auto_repair import FakeChapter,FakeProject,FakeScene,FakeSession,FakeQualityReport
from bestseller.infra.db.models import ChapterModel,SceneCardModel
from bestseller.services import drafts,pipelines
from bestseller.services.prefix_runtime_target_recovery import restore_prefix_runtime_scene_targets
from bestseller.services.quality_report_claim import graded_text_fingerprint

pytestmark=[pytest.mark.unit,pytest.mark.asyncio]

@pytest.mark.parametrize('code,words,target',[('BLOCK_LOW',900,2400),('BLOCK_HIGH',9500,6400)])
async def test_runtime_length_feedback_never_mutates_pinned_scene_plan(code,words,target,monkeypatch):
    ch=FakeChapter(metadata_json={'outline_prefix_readiness':{'artifact_id':'fixture-only'}})
    scene=FakeScene(chapter_id=ch.id,target_word_count=1600)
    text='文'*words
    report=FakeQualityReport(chapter_id=ch.id,report_json={'blocking_codes':[code],
        'graded':graded_text_fingerprint(text),'length_control':{'word_count':words,'target_words':target,'band':code}})
    # This fixture isolates length feedback; source admission is covered by
    # the real SQLite full/prefix source-reader tests.
    from types import SimpleNamespace
    settings=SimpleNamespace()
    async def verified_source(session, **kwargs):
        assert kwargs['chapter'] is ch and kwargs['scenes']==[scene]
        assert kwargs['settings'] is settings
        return {'status':'available'}
    monkeypatch.setattr('bestseller.services.editable_plan_source.build_editable_plan_source',verified_source)
    triggered,codes=await drafts.maybe_prepare_chapter_auto_repair(FakeSession(reports=[report],current_text=text,scenes=[scene]),project=FakeProject(),chapter=ch,repairable_codes=(code,),settings=settings)
    assert triggered and code in codes and scene.target_word_count==1600
    assert scene.metadata_json['auto_repair_original_target_word_count']==1600
    assert scene.metadata_json['auto_repair_adjusted_target_word_count']!=1600
    assert scene.metadata_json['auto_repair_hint']
    pipelines._clear_explicit_scene_regeneration_residue([scene],chapter_target_word_count=6000,preserve_source_targets=True)
    assert scene.target_word_count==1600


@pytest.mark.parametrize('fault',[None,'unknown_target_origin','other_story_change'])
async def test_sqlite_restore_requires_exact_old_marker_and_known_runtime_origin(entry_case,monkeypatch,fault):
    c=entry_case;await setup(c,monkeypatch);paid=len(c.sends)
    async with c.sessions() as s:
        p=await s.get(__import__('bestseller.infra.db.models',fromlist=['ProjectModel']).ProjectModel,c.project_id)
        chapter=await s.scalar(select(ChapterModel).where(ChapterModel.project_id==p.id))
        scenes=list(await s.scalars(select(SceneCardModel).where(SceneCardModel.chapter_id==chapter.id).order_by(SceneCardModel.scene_number)))
        marker=deepcopy(chapter.metadata_json['outline_prefix_readiness']);original=[row.target_word_count for row in scenes]
        for row in scenes:
            before=row.target_word_count;row.target_word_count=round(before*1.5)
            row.metadata_json={**row.metadata_json,'auto_repair_original_target_word_count':before,
                'auto_repair_adjusted_target_word_count':row.target_word_count,'auto_repair_length_scale':1.5}
        if fault=='unknown_target_origin':scenes[0].metadata_json={}
        if fault=='other_story_change':scenes[0].participants=['New unauthorized role']
        await s.commit()
        if fault:
            with pytest.raises(ValueError):await restore_prefix_runtime_scene_targets(s,c.settings,project=p,chapter=chapter,scenes=scenes)
            assert [row.target_word_count for row in scenes]!=original
        else:
            receipt=await restore_prefix_runtime_scene_targets(s,c.settings,project=p,chapter=chapter,scenes=scenes)
            assert receipt['restored_projection_hash']==marker['projection_hash']
            assert chapter.metadata_json['outline_prefix_readiness']==marker
            assert [row.target_word_count for row in scenes]==original
            assert (await restore_prefix_runtime_scene_targets(s,c.settings,project=p,chapter=chapter,scenes=scenes))['status']=='already_exact'
            await s.commit()
    assert len(c.sends)==paid
