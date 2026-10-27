import json
from copy import deepcopy
from types import SimpleNamespace
import pytest
from bestseller.services import conception, llm, minimax_m31_protocol as protocol
from bestseller.settings import LLMRoleSettings
pytestmark=pytest.mark.unit


@pytest.mark.asyncio
async def test_complete_repair_preserves_every_fact_and_review_without_new_tournament(monkeypatch):
    seen=[]
    async def call(*args,**kwargs):
        seen.append(kwargs)
        return {"title_candidates":["修机之后"],"synopsis_candidates":["完整待审简介"],"synopsis":"完整待审简介"},[]
    monkeypatch.setattr(conception,"_llm_call_json",call)
    canon={"story_architecture":{"units":[{"fact":"当夜交钥匙，之后继续修理","ending":"完整告别"}],"tail":"不能截断"*5000}}
    review={"passed":False,"hard_conflicts":[{"scope":"title","fact":"交钥匙后修好","generated":"关店前修好"}],
        "repair_directives":["保留实际发言人","当夜见面，不改明日"]}
    before=deepcopy(canon)
    result,_=await conception._repair_final_packaging_to_explicit_seed(None,None,
        concept_seed="四章内完成寻找、和解、告别",title="关店前修好",premise="冻结原故事",synopsis="待修简介",
        report=review,ctx={"chapter_count":4,"user_hints":{"production_profile":"complete"}},attempt=2,
        canonical_body=canon,quality_feedback={"required":7.5})
    assert len(seen)==1 and seen[0]["template"].endswith("repair_v2")
    data=json.loads(seen[0]["user_prompt"])
    assert data["frozen_canonical_body"]==canon==before and data["confirmed_review"]==review
    assert data["user_story"]=="四章内完成寻找、和解、告别" and data["target_chapters"]==4
    assert data["quality_feedback"]=={"required":7.5}
    assert len(data["output_contract"]["schema"]["title_candidates"])==len(data["output_contract"]["schema"]["synopsis_candidates"])==1
    assert "六个彼此不同" not in seen[0]["system_prompt"]
    assert result["synopsis"]=="完整待审简介"


def test_only_new_m31_repair_changes_effort_and_wire_hash_without_capacity_growth():
    role=LLMRoleSettings(model="openai/MiniMax-M3.1-Flash",temperature=1,max_tokens=32768,
        timeout_seconds=360,stream=True,reasoning_effort="max",api_base="https://api.minimaxi.com/v1")
    request=llm.LLMCompletionRequest(logical_role="editor",system_prompt="s",user_prompt="frozen facts",fallback_response="{}",
        prompt_template="conception_final_packaging_seed_fidelity_repair_v2")
    adjusted=llm._apply_conception_phase_settings(request,role)
    assert role.reasoning_effort=="max" and adjusted.reasoning_effort=="medium"
    assert adjusted.max_tokens==32768 and adjusted.timeout_seconds==360 and adjusted.model==role.model and adjusted.api_base==role.api_base
    payload=llm._effective_input_payload(request,adjusted)
    wire=protocol.wire_options({"max_tokens":32768,"stream":True,"model":adjusted.model},adjusted)
    assert payload["reasoning_effort"]==wire["reasoning_effort"]=="medium"
    assert wire["max_completion_tokens"]==payload["max_tokens"]==32768
    old=request.model_copy(update={"prompt_template":"conception_final_packaging_seed_fidelity_repair"})
    assert llm._apply_conception_phase_settings(old,role) is role
    assert llm._effective_input_hash(old,role)!=llm._effective_input_hash(request,adjusted)
    critic=request.model_copy(update={"prompt_template":"conception_final_packaging_seed_fidelity_judge"})
    assert llm._apply_conception_phase_settings(critic,role) is role
    legacy=role.model_copy(update={"model":"openai/MiniMax-M3"})
    assert llm._apply_conception_phase_settings(request,legacy) is legacy


@pytest.mark.asyncio
async def test_repaired_subject_gets_fresh_review_without_changing_review_prompts(monkeypatch):
    seen=[]
    async def call(*args,**kwargs):
        seen.append(kwargs)
        return {"passed":True,"hard_conflicts":[],"preserved_facts":[]},[]
    monkeypatch.setattr(conception,"_llm_call_json",call)
    common=dict(concept_seed="完整原创意",title="修机之后",premise="当夜交钥匙，之后修好",synopsis="当夜见面告别",canonical_body={"ending":"告别"})
    await conception._judge_final_packaging_seed_fidelity(None,None,ctx={},**common)
    await conception._judge_final_packaging_seed_fidelity(None,None,ctx={"_packaging_candidate_revision_v2":True},**common)
    assert seen[0]["template"]=="conception_final_packaging_seed_fidelity_judge"
    assert seen[1]["template"].startswith(seen[0]["template"]+"_candidate_v2_")
    for field in ("system_prompt","user_prompt","fallback","role"):
        assert seen[0][field]==seen[1][field]
    await conception._arbitrate_final_packaging_seed_fidelity_report(None,None,
        ctx={"_packaging_candidate_revision_v2":True},challenged_report={"passed":False},**common)
    assert seen[2]["template"].endswith(seen[1]["template"].split("_candidate_v2_")[1])
    assert len(seen[2]["template"])<=128
    assert conception._packaging_review_template("review",title="修机之后",synopsis="不同简介",ctx={"_packaging_candidate_revision_v2":True}) != conception._packaging_review_template("review",title="修机之后",synopsis="当夜见面告别",ctx={"_packaging_candidate_revision_v2":True})


def test_confirmed_title_conflict_cannot_return_as_high_score_fallback():
    title="关店前，修好旧收音机";synopsis="当夜交钥匙，之后修好"
    report={"hard_conflicts":[{"scope":"title","classification":"hard_conflict"}],
            "audit_identity":conception._final_packaging_audit_identity(title=title,synopsis=synopsis)}
    assert conception._confirmed_packaging_title_exclusions(report,title=title,synopsis=synopsis)==(title,)
    assert conception._confirmed_packaging_title_exclusions(report,title=title,synopsis="另一个未审对象")==()
    assert conception._confirmed_packaging_title_exclusions({**report,"evaluator_error":"unavailable"},title=title,synopsis=synopsis)==()
    diagnostics=[]
    assert conception._select_final_packaging_title_candidate([title],incumbent=title,title_profile={},
        premise="冻结事实",synopsis=synopsis,tags=(),genre="现实百态",sub_genre="现实生活",
        target_platform="fanqie",config=None,excluded_candidates=(title,),selection_diagnostics=diagnostics)==""
    assert diagnostics and all(x["reason"]=="confirmed_title_fact_conflict" for x in diagnostics)
