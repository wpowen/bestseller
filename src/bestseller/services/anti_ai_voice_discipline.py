"""Single source of truth for the 反AI腔 (anti-AI-voice) prose discipline.

Why this module exists
----------------------
This discipline used to be a bare string literal inside ``drafts.py``'s zh writer
system prompt. That made "inject it into the writer" and "inject it into the
rewriter" two unrelated code paths, so every anti-AI-flavor fix (2026-07-04,
07-08, 07-13) landed on the writer only. Meanwhile the rewrite prompts
(``scene_rewrite`` / ``chapter_rewrite``) — which produced **76% of shipped
prose** on a measured 24-chapter book — carried *zero* anti-AI rules, and their
output showed 1.75x the embodied-verb tic density and 2.07x the conclusion-first
density of writer output.

Any new rule added here reaches every prose-producing path at once. Do not
re-inline these rules into a prompt; import and render instead.

Scope
-----
The embodied-verb budget is scope-sensitive. The writer emits one *scene* at a
time and a chapter assembles 2-3 scenes, so a per-scene cap of 2 silently
becomes 6+ per chapter — above the detector's own ``verb_tic_spam`` threshold.
Callers rewriting a whole chapter must pass ``scope="chapter"`` so the budget is
stated against the text the model actually holds.
"""

from __future__ import annotations

from typing import Literal

from bestseller.services.writing_profile import is_english_language

Scope = Literal["scene", "chapter"]

_SCOPE_LABEL: dict[str, str] = {"scene": "全场", "chapter": "全章"}
# A chapter holds 2-3 scenes; keep the per-scene budget intact rather than
# letting it multiply by the scene count.
_VERB_CAP: dict[str, int] = {"scene": 2, "chapter": 4}
_SIMILE_CAP: dict[str, int] = {"scene": 1, "chapter": 3}
_MEASURE_CAP: dict[str, int] = {"scene": 2, "chapter": 5}


def render_anti_ai_voice_discipline(
    *,
    language: str | None = None,
    scope: Scope = "scene",
) -> str:
    """Render the 反AI腔 discipline block for a prose system prompt.

    Returns ``""`` for English (the rules are zh-specific lexical/syntactic
    prescriptions and have no English equivalent), so callers can concatenate
    unconditionally.
    """

    if is_english_language(language):
        return ""

    where = _SCOPE_LABEL.get(scope, _SCOPE_LABEL["scene"])
    verb_cap = _VERB_CAP.get(scope, _VERB_CAP["scene"])
    simile_cap = _SIMILE_CAP.get(scope, _SIMILE_CAP["scene"])
    measure_cap = _MEASURE_CAP.get(scope, _MEASURE_CAP["scene"])

    return (
        "# CONTEXT · 语体与用词建议（服从作品与人物声音，不单独触发重写）\n"
        "- 关键节点不要结论先行/总分总：冲突、选择、揭示、关系变化或情绪转折，"
        "先落人物目标→具体动作→本场对象→可见结果/他人反馈，再视需要用一句判断收束；"
        "不要先报结论、情绪标签或场面总结再补描写。过场、位移、时间跳跃、重复动作、"
        "背景定位和必要心理导航允许一两句平实概括。\n"
        "- 修饰语不按词类一刀切：只删改不增加新信息的程度强化、替读者贴情绪或下判断、"
        "与动作含义重复、近距离复读的修饰；保留承担时间、范围、否定、转折、概率、因果、"
        "视角、声音事实或人物口吻的词。目标不是副词归零，而是提高每个修饰语的信息密度。\n"
        "- 仅当中性旁白虚设误解且无事实作用时，对举式定义（“不是……而是……”“看似……实则……”“与其说……不如说……”）是最容易被"
        "一眼认出的AI腔：它替读者先否定一个错误理解，再颁布正确答案。改写成只写成立的那一面的"
        "实物细节，让错误理解由读者自己排除；真要对比就拆成两个独立短句，中间不要用转折词粘住。\n"
        "- 不要用“没做什么”当叙事主句空转（“他没动”“他没抬头”“她没出声”“他没接话”）："
        "若否定本身改变事实、范围或因果就保留；否则改写成他此刻**实际在做**的那个动作，"
        "让克制由动作透出来。\n"
        "- 身体反应不是情绪的默认替身，也不是每段必备的节拍。只有当身体变化会限制下一步动作、"
        "是前文已建立的线索、或立刻造成可见后果时才写；否则改写为人物对本场物件作出的选择，"
        "并写出该选择改变了什么。不得用通用身体反应、感官枚举、精确数字或陈词比喻补位；"
        "新增细节必须改变信息、因果、人物或节奏，不得从固定词表挑词填空。\n"
        "- 真实反转、否定条件、人物对白、自辩心理、书信与既定修辞不适用上述空转句式删改建议。\n"
        "- 语体按当前作者与作品选择；仅在选定现代白话网文时：先把话说清楚，再谈修辞。禁止文白夹杂的压缩腔"
        "（连续出现省略主语、省略量词的短句会让读者出戏）。\n"
        "- 不在生成提示里列举高冲击具身动词，避免黑名单反向激活；同一个高冲击具身动词"
        f"{where}最多 {verb_cap} 次；写感官时用平实动词（闻到/听见/看见/摸到）不丢人，"
        "复读高冲击动词才是最重的 AI 腔。\n"
        f"- 通感与陌生化比喻是味精：{where}≤{simile_cap}处，且必须贴合当下事件；"
        "严禁感官动词错配的怪喻（如“香味撞上来”“蒸汽舀进脑仁”——读者只会出戏）。\n"
        f"- 度量腔限用：“半寸/一寸/三分/半息”这类精确度量{where}≤{measure_cap}次，"
        "不要每个动作都带尺子。\n"
        "- 句子向前流动地叙事，不要为了节奏感把每个动作/心理拍点切成一句各占一段"
        "（整章像分镜脚本就是最刺眼的AI腔）；长短句交错，短句是偶尔的重锤，不是默认节奏。\n"
    )




def render_compact_writer_discipline(
    *,
    language: str | None = None,
    scope: Scope = "chapter",
) -> str:
    """Ablation-winning short discipline (~250 CJK chars).

    Used by chapter-first lean profile. Keeps the four rules that scored
    6.8–7.8 in dose-response; drops the long anti-AI enumeration that
    pushed writers into compliance-form prose.
    """

    if is_english_language(language):
        return (
            "You are a commercial fiction writer. One criterion only: will the "
            "reader click next?\n"
            "Rules: show action/perception before conclusions; never narrate "
            "via 'didn't X' filler; same high-impact verb ≤4 times per chapter; "
            "make the reader care what someone wants and fears.\n"
            "Output prose only.\n"
        )

    verb_cap = _VERB_CAP.get(scope, _VERB_CAP["chapter"])
    # 不再自带「你是一位……作者」角色句：本块总是拼在已有 ROLE 定义之后
    # （chapter-first system 头部已经声明过写手身份和留存判定标准），真机
    # prompt review（2026-08-07）发现同一 system 里出现两个"你是"角色定义、
    # 留存标准也重复了一遍——纯浪费且显得拼装。本块只负责四条纪律。
    return (
        "写作纪律（只有四条）：\n"
        "- 关键节点按目标→动作→本场对象→结果写，判断放在动作之后；过场可直接概括。"
        "删改重复动作含义或替读者贴情绪的低信息修饰语；保留时间、范围、否定、转折、概率、"
        "因果和人物口吻所需的词。\n"
        "- 不要用\"没做什么\"空转；若否定改变事实或因果就保留，否则直接写实际动作。\n"
        f"- 高冲击动词全章合计别超过 {verb_cap} 次——是总量限制，"
        "不是换着花样用不同的强动词；绝大多数动作用平实动词写"
        "（看见/听见/摸到/响/落/晃），无生命的东西只做它物理上真会做的事，"
        "不要给声音、影子、石头安排人的肢体动作；不得用通用身体反应、感官堆积或比喻套话补位。\n"
        "- 让读者关心某个人。人物要有想要的东西、怕失去的东西，读者得能感觉到。\n"
        "\n"
        "只输出正文，不要提纲、评语或说明。\n"
    )


__all__ = ["render_anti_ai_voice_discipline", "render_compact_writer_discipline"]
