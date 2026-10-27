"""Optional novel expression advice; no detector, body editor or model calls.

Adapted from lieflat-less-ai-tone at 27d29232f10124db904ca9c0536d0b67cb3b2833.
License and adaptation boundary: third_party/lieflat-less-ai-tone/NOTICE.md.
"""
from hashlib import sha256
from typing import Literal

from bestseller.services.prompt_compiler import PromptBlock, PromptProvenance, compile_prompt, estimate_prompt_tokens

VERSION = "novel-expression-guidance.v2"
KEY = "optional_novel_expression_guidance"


def render_novel_expression_guidance(mode: Literal['creation', 'revision'], *, language=None) -> str:
    if str(language or 'zh').lower().startswith('en'):
        return ''
    common = (
        "【小说表达建议·可选】服从设定、任务、视角与既定声音。"
        "保留真实反转、对白口吻、心理节奏、有用修辞、书信/告示/记录格式与编号，"
        "以及否定、条件、时间、因果、线索、情绪强度和认知边界；声音不明就不改。"
    )
    if mode == 'creation':
        return common + (
            "允许合乎来源的新行动、信息、动机与心理发展。避免空总结、虚设误解再反驳及同义复述，"
            "不为文风缩短篇幅或跳过情节。仅建议，不评分、不触发重写。"
        )
    if mode != 'revision':
        raise ValueError('unknown_expression_guidance_mode')
    return common + (
        "仅在现有修订内作一次局部表达检查：清理空结论与重复，未涉句不改；"
        "不增事实、动机或心理，不拆段、不并段、不改段落边界或顺序；代词只澄清已知对象，歧义不明保留。"
        "风险时保持原文。此建议不限制有证据的情节纠错；不加轮次、评分或说明。"
    )


def expression_guidance_blocks(*, mode='creation', language=None, config=None):
    if config is None:
        from bestseller.services.quality_gates_config import get_quality_gates_config
        config = get_quality_gates_config().ai_flavor
    if not config.expression_guidance_enabled:
        return ()
    text = render_novel_expression_guidance(mode, language=language)
    if not text:
        return ()
    return (PromptBlock(key=KEY, channel='user', layer='craft', authority=1,
        instruction_family='novel.expression.optional', required=False, trim_policy='drop',
        max_tokens=config.expression_guidance_max_tokens, source=VERSION, text=text,
        provenance=PromptProvenance(kind='derived', source_id=VERSION,
                                    source_hash=sha256(text.encode()).hexdigest())),)


def attach_revision_guidance(*, system_prompt, user_prompt, output_tokens, language=None):
    """Use only spare budget; an absent guide never blocks an existing request."""
    blocks = expression_guidance_blocks(mode='revision', language=language)
    receipt = {'version': VERSION, 'mode': 'revision', 'included': False,
               'added_model_calls': 0, 'added_rewrite_rounds': 0}
    if not blocks:
        return system_prompt, user_prompt, {**receipt, 'reason': 'disabled_or_language'}
    if type(output_tokens) is not int or output_tokens <= 0:
        return system_prompt, user_prompt, {**receipt, 'reason': 'output_reserve_unavailable'}
    guide = blocks[0]
    receipt['content_sha256'] = guide.provenance.source_hash
    # Same existing whole-request cost guard; this never expands that guard.
    allowance = int((65536 - int(output_tokens)) / 1.5) - 512
    base = estimate_prompt_tokens(system_prompt) + estimate_prompt_tokens(user_prompt)
    if allowance <= base:
        return system_prompt, user_prompt, {**receipt, 'reason': 'no_spare_budget'}
    # Existing system text stays byte-for-byte outside this optional USER
    # addition. Account for its tokens without minting framework provenance.
    original = PromptBlock(key='original_task', channel='user', layer='scene_spec', authority=100,
        instruction_family='original_task', required=False, trim_policy='preserve',
        source='existing-rewrite', text=user_prompt,
        provenance=PromptProvenance(kind='legacy'))
    compiled = compile_prompt([original, guide],
        total_budget_tokens=max(1, allowance-estimate_prompt_tokens(system_prompt)),
        safety_margin=0, require_provenance=True)
    if 'original_task' not in compiled.report.kept:
        return system_prompt, user_prompt, {**receipt, 'reason': 'no_spare_budget'}
    if KEY not in compiled.report.kept:
        return system_prompt, user_prompt, {**receipt, 'reason': compiled.report.drop_reasons.get(KEY,'budget')}
    final_user = user_prompt + '\n\n' + guide.text
    from bestseller.services.source_bound_planning_content import (
        SourceBoundPlanningCapacityError, assert_source_bound_planning_request_capacity,
    )
    try:
        capacity = assert_source_bound_planning_request_capacity(
            system_prompt, final_user, tools=None, output_tokens=int(output_tokens),
        )
    except SourceBoundPlanningCapacityError as exc:
        return system_prompt, user_prompt, {**receipt, 'reason': 'final_request_capacity',
                                           'capacity': exc.receipt}
    return system_prompt, final_user, {
        **receipt, 'included': True, 'guide_tokens': estimate_prompt_tokens(guide.text),
        'capacity': capacity, 'compiler': compiled.report.model_dump(mode='json')}
