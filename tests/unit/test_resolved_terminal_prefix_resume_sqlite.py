"""Actual SQLite/readiness consumer; synthetic HTTP response, never real N prose."""
from copy import deepcopy
from datetime import timedelta
from uuid import uuid4
import pytest
from sqlalchemy import select
from test_bounded_prefix_consumer_sqlite import consumer as consumer  # noqa:F401
from test_bounded_prefix_consumer_sqlite import bounded as bounded,volume as volume,harness as harness,prefix_rig as prefix_rig  # noqa:F401
from bestseller.infra.db.models import ProjectModel,WorkflowRunModel,PlanningArtifactVersionModel
from bestseller.services import chapter_outline_readiness as ready,foundation_selection

pytestmark=[pytest.mark.unit,pytest.mark.asyncio]

@pytest.mark.parametrize('fault',[None,'receipt_removed','body_changed','successor_crosslink'])
async def test_verified_successor_resolves_old_failure_but_corruption_blocks(consumer,monkeypatch,fault):
    h=consumer
    # The source fixture boundary is explicit. Published review/transport/
    # retained files and the normal readiness verifier execute unmodified.
    async def selected(session,*,project,settings):return h.r.foundation
    monkeypatch.setattr(foundation_selection,'load_selected_foundation_artifacts',selected)
    async with h.session_factory() as s:
        p=await s.get(ProjectModel,h.project.id)
        p.metadata_json={**p.metadata_json,'active_foundation_selection':{'synthetic_source_fixture':True}}
        edit=await s.scalar(select(WorkflowRunModel).where(WorkflowRunModel.workflow_type=='operator_outline_edit'))
        history=deepcopy(edit.metadata_json['previous_state'])
        await s.commit()
    bundle=await h.review();published=await h.consume(bundle)
    async with h.session_factory() as s:
        successor=await s.get(WorkflowRunModel,h.workflow.id)
        old=WorkflowRunModel(id=uuid4(),project_id=h.project.id,workflow_type=ready.WORKFLOW_TYPE,
            scope_type='project',scope_id=h.project.id,status='machine_blocked',current_step='prefix_judge_rejected',
            metadata_json=history,created_at=successor.created_at-timedelta(hours=1),updated_at=successor.created_at-timedelta(hours=1))
        s.add(old);await s.commit()
        artifact=await s.get(PlanningArtifactVersionModel,__import__('uuid').UUID(published['artifact_id']))
        if fault=='successor_crosslink':
            s.add(WorkflowRunModel(id=uuid4(),project_id=h.project.id,workflow_type=ready.WORKFLOW_TYPE,
                scope_type='project',scope_id=h.project.id,status='completed',current_step='completed',
                metadata_json=deepcopy(successor.metadata_json),created_at=successor.created_at+timedelta(minutes=1)))
            await s.commit()
        elif fault:
            body=deepcopy(artifact.content)
            if fault=='receipt_removed':body['_meta'].pop('sourcebound_readiness_receipt')
            else:body['chapters'][0]['goal']='Modified after paid review'
            artifact.content=body;await s.commit()
        p=await s.get(ProjectModel,h.project.id,populate_existing=True)
        if fault:
            with pytest.raises(ValueError):await ready.current_terminal_prefix_blocker(s,h.settings,project=p)
        else:
            assert await ready.current_terminal_prefix_blocker(s,h.settings,project=p) is None
            assert await ready.current_terminal_prefix_blocker(s,h.settings,project=p) is None
        assert (await s.get(WorkflowRunModel,old.id)).status=='machine_blocked'
        assert len(h.wires)==1  # Only intercepted fixture HTTP, no successor/recovery send.
