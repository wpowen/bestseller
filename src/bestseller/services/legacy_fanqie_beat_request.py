"""Forensic reconstruction of the old unlabelled BeatSheet request; NEVER send.

This renderer preserves historical instruction bytes only to verify the original
hash. It grants no author controls, planning approval, grade, adoption or credit.
"""

from bestseller.services.llm import LLMCompletionRequest
from bestseller.services.fanqie_short_planner import (
    resolve_prompt_pack,
    render_prompt_pack_fragment,
    select_emotion_stack,
    render_emotion_stack_prompt_block,
    adapt_long_form_resources_for_short,
    render_short_resource_prompt_block,
    _mapping,
    build_short_ideology_compact,
    render_ideology_compact_block,
)

ORIGINAL_SOURCE_SHA256 = "5cd138788b3ca46801d810a8d14634c43f6840df47c9921e12af8b2570054320"


def build_legacy_request(*, project, premise, book_spec, cast_spec):
    ideology_kernel = None
    pack = resolve_prompt_pack(
        "fanqie_short",
        genre=project.genre,
        sub_genre=project.sub_genre,
    )
    fragment = render_prompt_pack_fragment(pack, "planner_beat_sheet") if pack else ""
    emotion_stack = select_emotion_stack(
        premise=premise,
        genre=f"{project.genre} {project.sub_genre or ''}",
    )
    emotion_block = render_emotion_stack_prompt_block(emotion_stack)
    resource_cards = adapt_long_form_resources_for_short(
        book_spec=book_spec,
        cast_spec=cast_spec,
        premise=premise,
    )
    resource_block = render_short_resource_prompt_block(resource_cards)
    metadata = _mapping(getattr(project, "metadata_json", None))
    _ideology_compact = build_short_ideology_compact(ideology_kernel, metadata=metadata)
    ideology_block = (
        render_ideology_compact_block(ideology_kernel, metadata=metadata) if ideology_kernel else ""
    )
    ideology_prompt = f"{ideology_block}\n" if ideology_block else ""
    user_prompt = (
        f"书名：{project.title}\n"
        f"类型：{project.genre} / {project.sub_genre or ''}\n"
        f"梗概：{premise}\n"
        f"段数：{project.target_chapters}\n"
        f"目标全文：{project.target_word_count} 字\n"
        f"{emotion_block}\n"
        f"{ideology_prompt}"
        f"{resource_block}\n"
        "内核要求：仅执行作者明确选择的理念条款，不补造信念碎裂、重建或隐藏结局；"
        "代价方向仅依明确作者选择，不得从候选内核增加义务或豁免。"
        "严禁说教或口号；末段完成信念重建。\n"
        "榜单级硬要求：第1段前50字主角进入压迫现场，若有金手指/异能必须立刻可见并生效；"
        "前100字主角成为视角焦点并出现明确污名/威胁/损失；前200字必须给一次可见小反馈；"
        "前300字出现当前冲突/威胁/代价和第一次小爽点结果，前800字出现第一次动作反应或能力使用；"
        "前30%必须完成压迫-行动-小爆点；末段必须单篇完结，禁止连载式悬念；"
        "能力必须有来源、限制、成长触发和剧情用途；代价依明确作者选择与既有事实。\n"
        "请输出 JSON："
        '{"title","logline","pov","unlock_milestone_segment","beats":[{"segment_number",'
        '"beat_role","purpose","payoff","emotional_turn","opening_contract",'
        '"unlock_contract","ability_cost_contract","payoff_contract","closure_contract",'
        '"continuity_contract"}]}'
        '其中所有 *_contract 字段必须输出为 JSON object，例如 {"summary":"..."}，禁止直接输出字符串。'
    )
    return LLMCompletionRequest(
        logical_role="planner",
        system_prompt=fragment or "你是番茄短故事策划，输出紧凑 beat sheet JSON。",
        user_prompt=user_prompt,
        fallback_response="forensic only; never sent",
        project_id=project.id,
        metadata={"task": "fanqie_beat_sheet"},
    )
