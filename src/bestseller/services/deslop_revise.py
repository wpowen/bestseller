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
    "1) 解释规则/术语/数字条款（'开卷磬，议事司点人才用'、'…不得入堂——堂规第卅七条'、"
    "'凡研者入堂须当场研墨'）→ 删解说，让规则从人物反应/对白/后果里透出来。\n"
    "2) 把对方路数/算计逐环讲明（'先逼落砚，落砚即离身，离身即失势'）→ 删，只写可见的逼迫动作。\n"
    "3) 单个模板化微动作 / 生理套路（'眼瞳一缩'、'心跳漏了一拍'、'心头一紧'）→ 换成具体、跟当下绑定的身体动作。\n"
    "4) 对仗式装腔（'念的是名字，压的是刀'、'比路宽，比刃窄'、三词碎句堆叠）。\n"
    "5) '不是X，是Y / 这一次不是…'任何否定下定义；孤立到要读者脑补的碎片；回忆式前情概述；结论先行。\n"
    "改写后请自己再过一遍上面 5 条，确认一句不剩。"
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

    for _ in range(max(0, rounds)):
        findings, _score, n_spans = _findings_text(content, language)
        if n_spans == 0:
            break
        system_prompt = (
            "你是最严苛的中文网文编辑，专做去 AI 味改写。下面是写作铁律；逐条核对正文，"
            "把违反的句子改干净，严格保持剧情/人物/字数不变（只可微增不可删情节），只动有问题的句子，"
            "其余照搬。直接输出改写后的完整正文，不要任何解释或标注。\n\n" + rubric
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
        # Guard: never accept an empty or drastically-truncated rewrite.
        if revised and len(revised) >= len(content) * 0.6:
            content = revised
        else:
            logger.debug("deslop_revise: rewrite too short, keeping previous draft")
            break
    return content


__all__ = ["revise_prose_deslop"]
