from uuid import uuid4
import pytest
from bestseller.services import litstyle_prose_judge as lit,world_law_consistency_gate as world,llm
from bestseller.settings import load_settings
from test_world_model_pipeline import WORLD_MODEL

pytestmark=[pytest.mark.unit,pytest.mark.asyncio]

async def test_litstyle_stable_forwards_actual_project_and_workflow(monkeypatch):
    sent=[];pid,wid=uuid4(),uuid4()
    async def complete(s,st,request):
        sent.append(request)
        return llm.LLMCompletionResult(content='{}',provider='fixture',model_name='fixture',llm_run_id=uuid4(),finish_reason='stop')
    monkeypatch.setattr(lit,'complete_text',complete)
    await lit.judge_chapter_litstyle_stable(None,load_settings(),chapter_number=1,content_md='港口的灯亮了。',samples=2,project_id=pid,workflow_run_id=wid)
    assert len(sent)==2 and all(r.project_id==pid and r.workflow_run_id==wid for r in sent)

async def test_world_law_forwards_actual_project_and_workflow(monkeypatch):
    sent=[];pid,wid=uuid4(),uuid4()
    async def complete(s,st,request):
        sent.append(request)
        return llm.LLMCompletionResult(content='{"violations":[]}',provider='fixture',model_name='fixture',llm_run_id=uuid4(),finish_reason='stop')
    monkeypatch.setattr(llm,'complete_text',complete)
    await world.evaluate_world_law_consistency_llm(None,load_settings(),'主角驾驭飞行穿过空域与地面车辆',world_model=WORLD_MODEL,project_id=pid,workflow_run_id=wid)
    assert len(sent)==1 and sent[0].project_id==pid and sent[0].workflow_run_id==wid
