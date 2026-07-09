"""去 AI 味二次清洗 (deslop revise) — production self-review rewrite pass.

The cinematic_pov directive drives the *first* draft's AI-flavor way down, but
single-pass generation still leaves a few sticky discourse tells per draft
(不是X、解释规则/术语、模板化微动作、对仗装腔、回忆式前情、生理套路). This
service runs a bounded **generate→detect→targeted rewrite→re-detect** loop on a
finished draft: each round feeds the detector findings + the full cinematic_pov
rubric back to the writer model and asks it to rewrite ONLY the offending
sentences (plot / characters / length preserved). It stops as soon as the
detector is clean or the round budget is spent, and never returns a shorter or
empty draft (falls back to the previous content on a bad rewrite).

It is the framework's guarantee that a shipped chapter is not AI-flavor-heavy:
the writer prompt reduces, the detector measures, and this pass cleans the
residual the prompt could not.
"""

from __future__ import annotations

import logging

from bestseller.services.ai_flavor.detector import detect
from bestseller.services.llm import LLMCompletionRequest, complete_text
from bestseller.services.quality_levers.cinematic_pov import render_cinematic_pov_block


logger = logging.getLogger(__name__)


_EXTRA_SELF_CHECK = (
    "\n\n【更要逐句自查并改掉——检测器抓不到、但一读就露馅的（重点）】：\n"
    "1) 成段的设定/规则/条款播报（连续多句解释机制、来历、条文：'开卷磬，议事司点人才用'、"
    "'…不得入堂——堂规第卅七条'）→ 压缩成一句话级交代，或让它从人物反应/对白/后果里透出来。"
    "【但必须保留】一句话级的定位句（谁/在哪/在干什么）、因果句（因为/所以/于是）、"
    "转场概述（'十分钟后…'）与视角人物的念头——这些是可读性骨架，删了=白改。\n"
    "2) 把对方路数/算计逐环讲明（'先逼落砚，落砚即离身，离身即失势'）→ 删，只写可见的逼迫动作。\n"
    "3) 单个模板化微动作 / 生理套路（'眼瞳一缩'、'心跳漏了一拍'、'心头一紧'）→ 换成具体、跟当下绑定的身体动作。\n"
    "4) 对仗式装腔（'念的是名字，压的是刀'、'比路宽，比刃窄'、三词碎句堆叠）。\n"
    "5) '不是X，是Y / 这一次不是…'任何否定下定义；孤立到要读者脑补的碎片；回忆式前情概述；结论先行。\n"
    "6) 篇章级车轱辘（最伤可读性）：同一个身体感觉（酸/麻/凉/痒钻进骨缝那种）、"
    "同一个动作（喉结滚一下、指尖抖一下）、或同一句潜台词解读（'她那一下等于告诉他…'），"
    "在一段里反复换皮写了好几遍 → 只留最有力的一次，其余全删；一个静止场景别拉成长篇内心独白"
    "反复体会同一种感觉。感觉词（酸凉痒麻胀痛）排比堆叠成串的，压成一个具体身体反应。\n"
    "7) 「他没X」否定式克制扎堆（他没回头、他没跪、他没刀、他没犹豫——尤其紧张场景里连着甩）"
    "→ 这是最招人厌的 AI 腔。一段里最多留一个真正有戏剧反差的「他没…」，其余改成正向、带含义的"
    "具体动作（'他没刀'→'他空着手'/直接写他手里抓的是什么；'他没犹豫'→直接写他下刀那一下）。\n"
    "8) 单句独段饱和（当前最高频的节奏腔）：把每个动作/心理拍点都单独切成一句、各占一段"
    "（'他坐起来。'/'明天要更冷。'/'数字跳了一格。'/'他愣了一拍。'），整章读起来像分镜脚本而非小说。"
    "→ 单句独段是稀缺重锤，不能当默认节奏。把连续的单句独段并回有起伏的叙述段：动作+反应+环境揉进同一段，"
    "让长短段交替；连续的单行短句不超过 1-2 段，确有顿挫感才单独成段。合并时只动分段与连接，不改情节与对白。\n"
    "   ↳ 节奏锚点白名单（不要碰，避免与节奏修复打架）：全章可以保留 1 处刻意的三连短段加速"
    "（每段 1-8 字、连续出现）和少量 ≤12 字的独立硬停顿段——这些是有意设计的节奏重锤，"
    "只要不是满章泛滥就保留原样，不算单句独段饱和。\n"
    "9) 模板化系统刷屏/数字递增车轱辘（系统流通病）：同一条系统提示/弹窗/计数（如"
    "'【累计：¥0.80】''【¥1.20】''【¥2.80】'…一路往上跳十几行，或同一格式状态栏反复刷）"
    "→ 只保留有信息增量或情节转折的 2-3 次（首次出现、跨过关键档位、最后摊牌），中间纯递增的全删；"
    "把'数字又跳一格'这类反应也合并，别每跳一次写一句。系统提示要服务剧情节拍，不是逐帧打点。\n"
    "10) 翻译腔/欧化句式（旁白高发，一读就是机翻感）：'对…进行…'/'使…得到…'名词化空转 → 还原成直接动词；"
    "评价式被字句（'被…很好地解决'）和'被X所Y'扎堆 → 改主动（动作被字句'被一掌拍飞'是地道中文，不用改）；"
    "'作为一个X，…'开头 → 改中文语序；'堪称/可谓/称得上'连用抬调子 → 能用'是'就用'是'；"
    "破折号——过密（一段两对以上）→ 按职能换成句号/括号/冒号；"
    "'以前…现在…'对比骨架反复推进 → 直接写现在；'形容词+冒号'（'答案很简单：'）替读者下判断 → 删定性词让事实自己说话。"
    "自检法：把句子念出来，哪里不像中国人平时说话，哪里就是翻译腔。\n"
    "11) 高冲击具身动词词族复读（撞/烫/钻/爬/砸/攥/掐/拧/碾/蹿/洇）：这类词单个很有力，"
    "复读就是最扎眼的 AI 腔——全章同一个词最多 2 次、整个词族合计最多 4 次；"
    "超出的换成平实动词（碰/热/进/伸/握/挤）或普通说法，普通动作用普通词。\n"
    "12) 债务化比喻回流（除非本书明确是债务/借贷题材）：描写代价/后果被接受时，"
    "严禁用'认下这笔账/欠条/入账/结算/还债'这类财务记账措辞当修辞框架——即使"
    "设定层的金手指/代价写得干干净净（污染值/反噬/烙印这类具身形态），写手仍可能"
    "在描述'接受代价'这个动作时自己套上记账比喻，这是最容易漏改的一条。"
    "改用具身的非金融意象（反噬、灼烧、印记、感官剥夺）。\n"
    "改写后请自己再过一遍上面 12 条，确认一句不剩。"
)


def _findings_text(content: str, language: str) -> tuple[str, float, int]:
    report = detect(content, language=language)
    lines = "\n".join(
        f"- [{s.category}] 「{s.matched_text[:34]}」：{s.why[:60]}"
        for s in report.spans
    )
    return lines, report.overall_score, len(report.spans)


async def revise_prose_deslop(
    session,
    settings,
    *,
    content: str,
    language: str = "zh-CN",
    project_id=None,
    target_chars: int = 1600,
    rounds: int = 2,
    logical_role: str = "writer",
) -> str:
    """Run the bounded deslop self-review loop; return the cleaned content.

    Pure-ish: re-detects each round and only keeps a rewrite that is non-empty
    and not drastically shorter. Never raises on a bad rewrite — returns the
    best content so far. CJK-only (the rubric is tuned for Chinese prose); for
    English drafts the rubric block is empty so it no-ops after one detect.
    """

    if not content or not content.strip():
        return content
    rubric = render_cinematic_pov_block(language=language)
    if not rubric:  # English / no directive — nothing to enforce
        return content

    # Keep-better bookkeeping: the last round's rewrite was historically never
    # re-detected (accepted on length alone), so a final rewrite that *added*
    # AI-flavor shipped silently. Track the cleanest content seen and fall
    # back to it if the final rewrite measures worse.
    best_content = content
    best_spans: int | None = None

    for _ in range(max(0, rounds)):
        findings, _score, n_spans = _findings_text(content, language)
        if best_spans is None or n_spans < best_spans:
            best_content, best_spans = content, n_spans
        if n_spans == 0:
            break
        system_prompt = (
            "你是最严苛的中文网文编辑，专做去 AI 味改写。下面是写作铁律；逐条核对正文，"
            "把违反的句子改干净，严格保持剧情/人物不变，只动有问题的句子，其余照搬。"
            "字数原则上不减；但若正文有车轱辘重复（同一身体感觉/动作/潜台词解读反复写好几遍、"
            "感觉词排比堆叠），删去这些重复表达使字数下降是正确的，不算砍情节"
            "——情节是'发生了什么'，重复是'同一件事写了几遍'，删后者天经地义。"
            "直接输出改写后的完整正文，不要任何解释或标注。\n\n" + rubric
        )
        user_prompt = (
            "【检测出的 AI 味问题（必须逐条消除）】\n"
            + (findings or "（检测器未标出，但仍按下面自查表清查）")
            + _EXTRA_SELF_CHECK
            + f"\n\n【目标字数】约 {target_chars} 字，不足补足、不许砍情节。\n\n"
            "【待改写正文】\n"
            + content
        )
        request = LLMCompletionRequest(
            logical_role=logical_role,
            model_tier="strong",
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            fallback_response=content,
            prompt_template="scene_writer",
            prompt_version="deslop_revise",
            project_id=project_id,
            max_tokens_override=max(2048, int(target_chars * 2.4)),
        )
        try:
            result = await complete_text(session, settings, request)
        except Exception:
            logger.warning("deslop_revise: rewrite call failed; keeping draft", exc_info=True)
            break
        revised = (result.content or "").strip()
        # Guard: never accept an empty or drastically-truncated rewrite, and
        # never let a rewrite drag an on-target draft below ~70% of the
        # chapter target (which would trip the downstream LENGTH gate and
        # start a rewrite loop).
        length_floor = max(
            len(content) * 0.6,
            min(len(content), int(target_chars * 0.7)),
        )
        if revised and len(revised) >= length_floor:
            content = revised
        else:
            logger.debug("deslop_revise: rewrite too short, keeping previous draft")
            break

    # Final acceptance: the last rewrite has not been measured yet — re-detect
    # and fall back to the cleanest earlier draft if it got worse.
    if content is not best_content:
        _findings, _score, n_final = _findings_text(content, language)
        if best_spans is not None and n_final > best_spans:
            logger.info(
                "deslop_revise: final rewrite regressed (%d→%d spans); keeping best draft",
                best_spans,
                n_final,
            )
            content = best_content
    return content


__all__ = ["revise_prose_deslop"]
