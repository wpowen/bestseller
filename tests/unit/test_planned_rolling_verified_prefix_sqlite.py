"""Default rolling approval remains strict; scoped proof authorizes only its rows."""
from copy import deepcopy
from uuid import UUID
import pytest
from sqlalchemy import select
from test_bounded_prefix_consumer_sqlite import consumer as consumer  # noqa:F401
from test_bounded_prefix_consumer_sqlite import bounded as bounded,volume as volume,harness as harness,prefix_rig as prefix_rig  # noqa:F401
from bestseller.infra.db import models as m
from bestseller.services import chapter_outline_readiness as ready
from bestseller.services.rolling_outline import _canonical_hash,load_rolling_outline_plan

pytestmark=[pytest.mark.unit,pytest.mark.asyncio]


@pytest.mark.parametrize('fault',[None,'marker_missing','body_changed','false_grade','scene_changed'])
async def test_only_exact_current_materialized_prefix_consumes_planned_window(consumer,fault):
    h=consumer;bundle=await h.review();published=await h.consume(bundle)
    await h.materialize(UUID(published['artifact_id']))
    async with h.session_factory() as s:
        p=await s.get(m.ProjectModel,h.project.id)
        meta=deepcopy(p.metadata_json);raw=meta['rolling_outline_plan'];raw['status']='planned'
        raw['plan_hash']=_canonical_hash({k:v for k,v in raw.items() if k!='plan_hash'})
        p.metadata_json=meta
        chapter=await s.scalar(select(m.ChapterModel).where(m.ChapterModel.project_id==p.id))
        if fault=='marker_missing':chapter.metadata_json={}
        elif fault=='body_changed':
            artifact=await s.get(m.PlanningArtifactVersionModel,UUID(published['artifact_id']))
            body=deepcopy(artifact.content);body['chapters'][0]['goal']='Different after review';artifact.content=body
        elif fault=='false_grade':
            wf=await s.get(m.WorkflowRunModel,h.workflow.id);state=deepcopy(wf.metadata_json)
            state['final_report']['pass']=False;wf.metadata_json=state
        elif fault=='scene_changed':
            scene=await s.scalar(select(m.SceneCardModel).where(m.SceneCardModel.chapter_id==chapter.id));scene.title='Unreviewed replacement'
        await s.commit()
        before=deepcopy(p.metadata_json['rolling_outline_plan'])
        with pytest.raises(ValueError,match='not approved'):
            load_rolling_outline_plan(meta['macro_outline_plan'],raw,
                source_snapshot_hash=raw['source_snapshot_hash'])
        kwargs=dict(project=p,raw_macro=meta['macro_outline_plan'],raw_plan=raw,
            source_snapshot_hash=raw['source_snapshot_hash'])
        if fault:
            with pytest.raises(ValueError):await ready.load_rolling_plan_for_prefix_consumption(s,h.settings,**kwargs)
        else:
            macro,plan,numbers=await ready.load_rolling_plan_for_prefix_consumption(s,h.settings,**kwargs)
            assert numbers=={1} and plan.status=='planned' and plan.window_end>1
        assert p.metadata_json['rolling_outline_plan']==before
        assert len(h.wires)==1
