"""A whole-book advisory candidate cannot bypass per-chapter edit accounting."""
from types import SimpleNamespace
from uuid import uuid4
import pytest
from bestseller.services import fanqie_short_pipeline as pipeline, deslop_revise
from bestseller.services.source_bound_planning_content import new_reader_first_binding

pytestmark = pytest.mark.unit

@pytest.mark.asyncio
async def test_marked_whole_advisory_preserves_adopted_text_and_defers_to_chapter_edits(monkeypatch):
    calls = []
    async def forbidden(*args, **kwargs):
        calls.append(1)
        return "unadopted whole body"
    from bestseller.services import ai_flavor_gate, quality_gates_config
    monkeypatch.setattr(deslop_revise, "revise_prose_deslop", forbidden)
    monkeypatch.setattr(ai_flavor_gate,"run_ai_flavor_gate",lambda **kw: SimpleNamespace(before_score=80))
    monkeypatch.setattr(ai_flavor_gate,"needs_deslop_revise",lambda *a: True)
    monkeypatch.setattr(quality_gates_config,"get_quality_gates_config",lambda:SimpleNamespace(
        ai_flavor=SimpleNamespace(enabled=True,deslop_revise_enabled=True)))
    class Settings:
        artifact_store=SimpleNamespace(retain_prompt_payloads=False)
        def model_copy(self, **kw):return self
    project=SimpleNamespace(id=uuid4(),slug="independent-advisory",language="zh-CN",
        metadata_json={"reader_first_planning":new_reader_first_binding("家庭团聚")})
    text="她坐回桌边，把汤递给母亲。"
    events=[]
    result=await pipeline._deslop_whole_short_story(None,Settings(),text,project=project,
        workflow_run_id=uuid4(),progress=lambda name,payload:events.append((name,payload)))
    assert result==text and calls==[]
    assert events[-1][0]=="fanqie_deslop_chapter_scope_required"
    assert events[-1][1]["quality_pass_asserted"] is False
