from __future__ import annotations

from collections.abc import Mapping, Sequence

# ruff: noqa: ANN401,RUF001
import json
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.domain.llm_quality_judge import (
    LLMQualityJudgeResult,
    quality_judge_result_from_mapping,
)
from bestseller.services.chapter_llm_quality_judge import _parse_json_object
from bestseller.services.judge_rubrics import get_judge_rubric
from bestseller.services.llm import LLMCompletionRequest, complete_text
from bestseller.services.word_targets import (
    model_output_token_ceiling,
    resolve_llm_role_max_tokens,
    resolve_llm_role_model,
)
from bestseller.settings import AppSettings


async def judge_volume_quality_checkpoint(
    session: AsyncSession,
    settings: AppSettings,
    *,
    volume_plan: Mapping[str, Any],
    chapter_summaries: Sequence[Mapping[str, Any]],
    min_volume_alignment: float = 0.80,
    current_chapter_number: int | None = None,
    volume_checkpoint_interval: int | None = None,
    volume_checkpoint_min_chapters: int | None = None,
    workflow_run_id: Any | None = None,
) -> LLMQualityJudgeResult:
    chapter_window_text = json.dumps(
        list(chapter_summaries),
        ensure_ascii=False,
        indent=2,
        default=str,
    )[:22000]
    volume_plan_text = json.dumps(
        volume_plan,
        ensure_ascii=False,
        indent=2,
        default=str,
    )[:8000]
    chapter_numbers = _chapter_numbers_from_summaries(chapter_summaries)
    current_number = current_chapter_number or (max(chapter_numbers) if chapter_numbers else None)
    volume_stage_text = _render_volume_stage_text(
        current_chapter_number=current_number,
        chapter_numbers=chapter_numbers,
        checkpoint_interval=volume_checkpoint_interval,
        checkpoint_min_chapters=volume_checkpoint_min_chapters,
    )
    fallback = json.dumps(
        {
            "pass": False,
            "overall_score": 0.0,
            "dimension_scores": {"volume_alignment": 0.0},
            "blocking_issues": [
                {
                    "code": "VOLUME_JUDGE_UNAVAILABLE",
                    "severity": "critical",
                    "evidence": "LLM volume checkpoint judge returned fallback content.",
                    "required_fix": "重新运行卷级评测，确认章节没有偏离卷目标。",
                }
            ],
            "rewrite_plan": {"scope": "volume", "instructions": "重新评估卷目标对齐。"},
        },
        ensure_ascii=False,
    )
    rubric = get_judge_rubric("volume_checkpoint")
    completion = await complete_text(
        session,
        settings,
        LLMCompletionRequest(
            logical_role="critic",
            model_tier="strong",
            system_prompt=(
                "# ROLE\n"
                "你是长篇商业小说的「卷级阶段性主审」。\n"
                "你审过 100+ 部签约连载的卷级 checkpoint，最擅长判断章节是否仍在轨服务于卷目标。\n"
                "你深知签约连载的卷规划逻辑：milestone / 揭示预算 / 角色状态承诺 / 阻碍递进。\n"
                "\n"
                "# CONTEXT\n"
                "这是**阶段性**卷级检查，不是要求当前章节兑现整卷结局。\n"
                "你只评估"当前章节窗口按进度**应该**承担的目标"——绝不提前兑现未来章节 / 未来揭示 / 卷高潮 / 卷尾钩子。\n"
                "\n"
                "# CONTEXT · 卷级评审专属维度\n"
                "- **volume_alignment**：当前窗口是否仍在服务卷目标、阻碍、高潮、解决、揭示预算\n"
                "- **material_advancement_score**：是否推进卷计划里的揭示 / 规则 / 证据合同\n"
                "- **stage_drift**：是否偏离当前阶段应完成的 milestone\n"
                "\n"
                "# TASK\n"
                "对窗口与卷计划对齐情况打分，并产出 blocking / audit / rewrite_plan。\n"
                "\n"
                "# CONSTRAINTS · 评分纪律\n"
                f"- 通过阈值：volume_alignment ≥ {min_volume_alignment:.2f}，无 critical blocking。\n"
                "- 不得要求提前兑现未来章节、未来揭示、卷高潮、卷尾钩子。\n"
                "- blocking_issues 必须引用**当前窗口**真实文本证据和**当前阶段应完成**的合同。\n"
                "- dimension_scores 必须包含 volume_alignment；可判断时必须包含 material_advancement_score。\n"
                "- 其他维度可补充，但不能替代 volume_alignment。\n"
                "\n"
                "# THINKING（产 JSON 前在脑内 4 步）\n"
                "1. 读卷计划 — 标记当前章节范围"应该完成什么"\n"
                "2. 读章节窗口 — 比对实际完成 vs 应该完成的 delta\n"
                "3. 区分"未完成但属于未来章节"与"应完成但未做"——只对后者扣分\n"
                "4. Reconcile：≥1 critical → overall_score 不应 ≥ 0.75\n"
                "\n"
                "# OUTPUT FORMAT（严格 JSON）\n"
                "字段：pass, overall_score, dimension_scores (必含 volume_alignment + material_advancement_score), "
                "blocking_issues, audit_issues, rewrite_plan\n"
                "\n"
                "# RUBRIC（评分细则原文）\n"
                f"{rubric.system_prompt}\n"
            ),
            user_prompt=(
                rubric.render_prompt_block()
                + "\n\n## 任务参数\n"
                f"- 通过阈值：volume_alignment ≥ {min_volume_alignment:.2f}\n"
                f"- 章节窗口大小：{len(chapter_summaries)}\n"
                "\n## 阶段信息\n"
                f"```\n{volume_stage_text}\n```\n"
                "\n## 卷计划\n"
                f"```\n{volume_plan_text}\n```\n"
                "\n## 章节窗口\n"
                f"```\n{chapter_window_text}\n```\n"
                "\n## 立即开始\n"
                "按 system 中的 4 步 THINKING 思考，输出严格 JSON。"
            ),
            fallback_response=fallback,
            prompt_template="volume_quality_checkpoint_judge",
            prompt_version="v1",
            workflow_run_id=workflow_run_id,
            metadata={
                "judge_scope": "volume",
                "chapter_count": len(chapter_summaries),
                "threshold": min_volume_alignment,
                "rubric": rubric.name,
            },
            max_tokens_override=_critic_judge_max_tokens(settings),
        ),
    )
    payload = _normalize_volume_quality_payload(_parse_json_object(completion.content))
    return quality_judge_result_from_mapping(
        payload,
        scope="volume",
        min_overall=min_volume_alignment,
        min_dimensions={"volume_alignment": min_volume_alignment},
        llm_run_id=str(completion.llm_run_id) if completion.llm_run_id else None,
        raw_excerpt=completion.content[:6000],
    )


def _normalize_volume_quality_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize provider-friendly volume dimension names into the contract key."""

    data = dict(payload)
    raw_scores = data.get("dimension_scores")
    if not isinstance(raw_scores, Mapping):
        return data
    scores = dict(raw_scores)
    if "volume_alignment" in scores:
        data["dimension_scores"] = scores
        return data

    numeric_scores: list[float] = []
    for raw in scores.values():
        try:
            score = float(raw)
        except (TypeError, ValueError):
            continue
        if score > 10.0:
            score = score / 100.0
        elif score > 1.0:
            score = score / 10.0
        numeric_scores.append(max(0.0, min(1.0, score)))
    if numeric_scores:
        # Conservative aggregation: all returned volume subdimensions must be
        # healthy for the synthetic volume_alignment to pass.
        scores["volume_alignment"] = min(numeric_scores)
    data["dimension_scores"] = scores
    return data


def _chapter_numbers_from_summaries(chapter_summaries: Sequence[Mapping[str, Any]]) -> list[int]:
    numbers: list[int] = []
    for item in chapter_summaries:
        try:
            number = int(item.get("chapter_number") or 0)
        except (TypeError, ValueError):
            continue
        if number > 0:
            numbers.append(number)
    return numbers


def _render_volume_stage_text(
    *,
    current_chapter_number: int | None,
    chapter_numbers: Sequence[int],
    checkpoint_interval: int | None,
    checkpoint_min_chapters: int | None,
) -> str:
    if chapter_numbers:
        window = f"{min(chapter_numbers)}-{max(chapter_numbers)}"
    else:
        window = "unknown"
    interval_text = str(checkpoint_interval or "unknown")
    min_text = str(checkpoint_min_chapters or "unknown")
    current_text = str(current_chapter_number or "unknown")
    return (
        f"当前章节号：{current_text}；当前窗口章节：{window}；"
        f"卷级检查间隔：每 {interval_text} 章；最早硬检查章节：{min_text}。\n"
        "评审原则：只判断当前窗口是否完成本阶段应有的铺垫、阻碍递进和读者承诺；"
        "未来章节计划只能作为方向校验，不能变成当前章节阻塞项。"
    )


def _critic_judge_max_tokens(settings: AppSettings) -> int:
    configured = resolve_llm_role_max_tokens(settings, role="critic")
    if configured and configured > 0:
        return configured
    model_ceiling = model_output_token_ceiling(
        resolve_llm_role_model(settings, role="critic")
    )
    if model_ceiling and model_ceiling > 0:
        return model_ceiling
    return 8192
