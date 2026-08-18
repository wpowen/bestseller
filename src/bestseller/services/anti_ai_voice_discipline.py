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
        "# CONTEXT · 语体与用词纪律（反AI腔，违反会被判重写）\n"
        "- 不要结论先行/总分总：禁止先抛出判断、情绪标签或场面总结、再用描写去补证。"
        "先写正在发生的具体动作与感知，结论让读者自己得出，能不说就不说。"
        "删掉一切替读者算账、下定论的句子（如“他算了一笔账”“这一刻他明白了”）。\n"
        "- 对举式定义（“不是……而是……”“看似……实则……”“与其说……不如说……”）是最容易被"
        "一眼认出的AI腔：它替读者先否定一个错误理解，再颁布正确答案。改写成只写成立的那一面的"
        "实物细节，让错误理解由读者自己排除；真要对比就拆成两个独立短句，中间不要用转折词粘住。\n"
        "- 不要用“没做什么”当叙事主句（“他没动”“他没抬头”“她没出声”“他没接话”）："
        "这是用否定去暗示克制，读者读到的只是空白。改写成他此刻**实际在做**的那个动作，"
        "克制由这个动作本身透出来。\n"
        "- 身体反应不是情绪的默认替身，也不是每段必备的节拍。只有当身体变化会限制下一步动作、"
        "是前文已建立的线索、或立刻造成可见后果时才写；否则改写为人物对本场物件作出的选择，"
        "并写出该选择改变了什么。不得从固定身体反应词表挑词填空。\n"
        "- 语体=现代白话网文：先把话说清楚，再谈修辞。禁止文白夹杂的压缩腔"
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
        "- 不要结论先行：先写正在发生的动作和感知，判断留给读者自己得出。"
        "删掉替读者下定论的句子。\n"
        "- 不要用\"没做什么\"当叙事主句（\"他没动\"\"她没出声\"）："
        "直接写他此刻实际在做的动作。\n"
        f"- 高冲击动词全章合计别超过 {verb_cap} 次——是总量限制，"
        "不是换着花样用不同的强动词；绝大多数动作用平实动词写"
        "（看见/听见/摸到/响/落/晃），无生命的东西只做它物理上真会做的事，"
        "不要给声音、影子、石头安排人的肢体动作。\n"
        "- 让读者关心某个人。人物要有想要的东西、怕失去的东西，读者得能感觉到。\n"
        "\n"
        "只输出正文，不要提纲、评语或说明。\n"
    )


__all__ = ["render_anti_ai_voice_discipline", "render_compact_writer_discipline"]
