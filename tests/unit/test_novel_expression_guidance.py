from dataclasses import replace
import pytest
from bestseller.services.ai_flavor_gate import AiFlavorGateConfig
from bestseller.services.novel_expression_guidance import (
    KEY, attach_revision_guidance, expression_guidance_blocks, render_novel_expression_guidance,
)
from bestseller.services.prompt_compiler import PromptBlock, PromptProvenance, compile_prompt, estimate_prompt_tokens

pytestmark=pytest.mark.unit


def test_optional_atomic_guide_uses_only_remaining_budget():
    config=AiFlavorGateConfig(expression_guidance_enabled=True, expression_guidance_max_tokens=900)
    guide=expression_guidance_blocks(config=config)[0]
    fact=PromptBlock(key='current_world',channel='user',layer='scene_spec',authority=90,
                     instruction_family='world',required=True,trim_policy='preserve',source='test',
                     text=('A seal cannot undo a dated event. '*20).strip(),provenance=PromptProvenance(kind='user_input'))
    roomy=compile_prompt([fact,guide],total_budget_tokens=3000,safety_margin=0,require_provenance=True)
    assert KEY in roomy.report.kept and guide.text in roomy.user and fact.text in roomy.user
    tight=compile_prompt([fact,guide],total_budget_tokens=estimate_prompt_tokens(fact.text)+10,safety_margin=0,require_provenance=True)
    assert KEY in tight.report.dropped and KEY not in tight.report.truncated
    assert fact.text in tight.user and tight.report.required_complete
    capped=expression_guidance_blocks(config=replace(config,expression_guidance_max_tokens=1))
    result=compile_prompt([fact,*capped],total_budget_tokens=3000,safety_margin=0,require_provenance=True)
    assert KEY in result.report.dropped and fact.text in result.user
    assert expression_guidance_blocks(config=replace(config,expression_guidance_enabled=False))==()


def test_revision_disable_and_no_spare_budget_leave_original_bytes(monkeypatch):
    import bestseller.services.novel_expression_guidance as service
    config=AiFlavorGateConfig(expression_guidance_enabled=True)
    blocks=expression_guidance_blocks(mode='revision',config=config)
    monkeypatch.setattr(service,'expression_guidance_blocks',lambda **kw:blocks)
    system='Existing editor protocol.'; user='第三章 雨夜\n\n门外不是哥哥，而是穿着哥哥外套的陌生人。\n\n钥匙、铜牌、旧信。'
    s,u,receipt=attach_revision_guidance(system_prompt=system,user_prompt=user,output_tokens=16384)
    assert s==system and u.startswith(user+'\n\n') and receipt['included']
    assert receipt['added_model_calls']==receipt['added_rewrite_rounds']==0
    s,u,receipt=attach_revision_guidance(system_prompt=system,user_prompt=user,output_tokens=65536)
    assert (s,u)==(system,user) and not receipt['included']
    monkeypatch.setattr(service,'expression_guidance_blocks',lambda **kw:())
    assert attach_revision_guidance(system_prompt=system,user_prompt=user,output_tokens=16384)[:2]==(system,user)


def test_creation_and_revision_have_distinct_permissions_without_new_checks():
    creation=render_novel_expression_guidance('creation'); revision=render_novel_expression_guidance('revision')
    assert '允许合乎来源的新行动、信息、动机与心理发展' in creation
    assert '不增事实、动机或心理' not in creation
    assert '不增事实、动机或心理' in revision
    assert '不拆段' in revision and '不拆段' not in creation
    assert '一次局部表达检查' in revision and '不限制有证据的情节纠错' in revision
    assert '不并段' in revision and '不改段落边界或顺序' in revision
    for text in (creation,revision):
        for clause in ['真实反转','对白','心理','书信','条件','时间','因果']:
            assert clause in text
    assert '不评分、不触发重写' in creation
    assert '不加轮次、评分或说明' in revision
    assert render_novel_expression_guidance('creation',language='en-US')==''


def test_final_whole_request_guard_can_withdraw_only_guide(monkeypatch):
    import bestseller.services.novel_expression_guidance as service
    import bestseller.services.source_bound_planning_content as capacity
    blocks=expression_guidance_blocks(mode='revision',config=AiFlavorGateConfig(expression_guidance_enabled=True))
    monkeypatch.setattr(service,'expression_guidance_blocks',lambda **kw:blocks)
    seen=[]
    def reject(system,user,*,tools,output_tokens):
        seen.append((system,user,tools,output_tokens))
        raise capacity.SourceBoundPlanningCapacityError({'total_budget':65536,'budget_passed':False})
    monkeypatch.setattr(capacity,'assert_source_bound_planning_request_capacity',reject)
    system='Exact protocol'; user='Facts: '+('English archive. '*30)+'中文阈值'
    s,u,receipt=attach_revision_guidance(system_prompt=system,user_prompt=user,output_tokens=16384)
    assert (s,u)==(system,user)
    assert seen==[(system,user+'\n\n'+blocks[0].text,None,16384)]
    assert receipt['reason']=='final_request_capacity' and not receipt['included']


def test_optional_guide_never_blocks_a_scene_without_declared_output_target(monkeypatch):
    from bestseller.services.drafts import prose_output_max_tokens_for_target
    import bestseller.services.novel_expression_guidance as service
    blocks=expression_guidance_blocks(mode='revision',config=AiFlavorGateConfig(expression_guidance_enabled=True))
    monkeypatch.setattr(service,'expression_guidance_blocks',lambda **kw:blocks)
    reserve=prose_output_max_tokens_for_target(None)
    assert reserve is None
    system,user,receipt=attach_revision_guidance(system_prompt='Existing editor',user_prompt='Existing scene',output_tokens=reserve)
    assert (system,user)==('Existing editor','Existing scene')
    assert not receipt['included'] and receipt['reason']=='output_reserve_unavailable'
