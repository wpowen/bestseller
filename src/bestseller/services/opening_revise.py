"""七猫开篇门禁·就地有界重写 (qimao opening inline revise).

The opening-quality gate flags a weak opening (protagonist not the reader's
focus in the first screen, no felt conflict, weak章末钩) and queues a
``RewriteTaskModel`` for a worker/human to pick up. During an **autonomous
forward-writing run** nobody consumes that task inline, so a flagged opening
used to ship unchanged and drag through the whole book.

This service closes that loop the same way ``deslop_revise`` does for
AI-flavor: one bounded ``generate→rewrite`` pass on the opening chapter using
the gate's own rewrite instructions. The caller re-evaluates the gate and keeps
the rewrite only if it improves. This module is intentionally pure-ish: it never
raises, never returns an empty or drastically-truncated draft (falls back to the
original), so wiring it into the hot path cannot break generation.
"""

from __future__ import annotations

import logging

from bestseller.services.llm import LLMCompletionRequest, complete_text


logger = logging.getLogger(__name__)


_SYSTEM_PROMPT = (
    "你是最擅长网文开篇的中文作者，专做开篇重建。按【七猫开篇门禁重写任务】重写开篇："
    "第一屏就让主角成为读者视角焦点，给出可感冲突与主角的即时目标，章末留强钩；"
    "严格保持本章核心事件与人物设定不变，只重写、不解说、不加分析。"
    "直接输出改写后的完整正文，不要任何解释或标注。"
)


async def revise_opening_qimao(
    session,
    settings,
    *,
    content: str,
    instructions: str,
    project_id=None,
    logical_role: str = "writer",
) -> str:
    """Run one bounded opening rewrite; return revised content or the original.

    Never raises and never returns an empty / drastically-shorter draft — the
    caller re-runs the gate and decides whether to keep the result, so a bad
    rewrite is always safe to discard.
    """

    if not content or not content.strip():
        return content
    user_prompt = (
        (instructions or "").strip()
        + "\n\n【待重写正文】\n"
        + content
    )
    request = LLMCompletionRequest(
        logical_role=logical_role,
        model_tier="strong",
        system_prompt=_SYSTEM_PROMPT,
        user_prompt=user_prompt,
        fallback_response=content,
        prompt_template="scene_writer",
        prompt_version="qimao_opening_revise",
        project_id=project_id,
        max_tokens_override=max(2048, int(len(content) * 2.4)),
    )
    try:
        result = await complete_text(session, settings, request)
    except Exception:
        logger.warning(
            "revise_opening_qimao: rewrite call failed; keeping draft",
            exc_info=True,
        )
        return content
    revised = (result.content or "").strip()
    # Guard: never accept an empty or drastically-truncated rewrite. The opening
    # gate re-eval in the caller is the quality check; this only protects length.
    if revised and len(revised) >= len(content) * 0.7:
        return revised
    logger.debug("revise_opening_qimao: rewrite too short, keeping previous draft")
    return content


__all__ = ["revise_opening_qimao"]
