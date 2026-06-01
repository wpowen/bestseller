"""LLM reader-judge — real read-feel scoring to replace keyword heuristics.

The deterministic persona simulator scores chapters from keyword/structural
signals; its ``prose_quality_score`` channel defaults to a neutral 0.7. This
module produces that channel from an actual LLM read so the persona hard gate
reflects whether a chapter is *readable*, not just keyword-complete.

Design:
  * Single ``complete_text(logical_role="critic")`` call, deterministic temp.
  * Strict JSON rubric: opening pull / payoff density / emotional impact /
    abandon urge. Aggregated into one 0..1 ``prose_quality_score``.
  * Always returns a result (fallback to neutral 0.7 on any failure) — never
    blocks the pipeline on judge errors.
  * Respects the LLM gateway contract (project_id + workflow_run_id +
    fallback_response) per bestseller-dev.mdc.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import logging
import re
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.services.llm import LLMCompletionRequest, complete_text
from bestseller.settings import AppSettings

logger = logging.getLogger(__name__)

_NEUTRAL_SCORE = 0.7

_RUBRIC_WEIGHTS: dict[str, float] = {
    "opening_pull": 0.25,
    "payoff_density": 0.30,
    "emotional_impact": 0.25,
    "anti_abandon": 0.20,
}

_FALLBACK_JSON = json.dumps(
    {
        "opening_pull": 0.7,
        "payoff_density": 0.7,
        "emotional_impact": 0.7,
        "anti_abandon": 0.7,
        "comment": "fallback",
    },
    ensure_ascii=False,
)

_SYSTEM_PROMPT = (
    "你是中文商业连载小说的资深读者评审，只输出严格 JSON。\n"
    "你要像真实读者一样判断这一章读起来是否抓人、是否值得继续追读，"
    "不要被结构完整或关键词齐全迷惑——重点看现场感、兑现、情绪冲击和弃读冲动。\n"
    "评分维度（均为 0..1，越高越好）：\n"
    "1. opening_pull：开篇是否迅速制造钩子/张力，有没有拖沓与信息倾倒。\n"
    "2. payoff_density：本章是否有真实兑现（揭示/对抗结果/代价），而非只抛钩子。\n"
    "3. emotional_impact：情绪是否通过现场动作传递并击中读者，而非形容词堆叠。\n"
    "4. anti_abandon：综合读感下读者不弃读的概率（越高越不想弃）。\n"
    "输出 JSON：{\"opening_pull\":0.0,\"payoff_density\":0.0,"
    "\"emotional_impact\":0.0,\"anti_abandon\":0.0,\"comment\":\"≤30字\"}"
)


@dataclass(frozen=True)
class ReaderJudgeResult:
    prose_quality_score: float
    dimensions: dict[str, float]
    comment: str
    used_llm: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "prose_quality_score": self.prose_quality_score,
            "dimensions": dict(self.dimensions),
            "comment": self.comment,
            "used_llm": self.used_llm,
        }


def _clamp01(value: Any, default: float = _NEUTRAL_SCORE) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    return max(0.0, min(1.0, v))


def _parse_judge_json(raw: str) -> dict[str, Any] | None:
    text = (raw or "").strip()
    if not text:
        return None
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def aggregate_prose_quality(dimensions: dict[str, float]) -> float:
    """Weighted aggregate of rubric dimensions into one 0..1 score."""

    total_weight = 0.0
    acc = 0.0
    for key, weight in _RUBRIC_WEIGHTS.items():
        if key in dimensions:
            acc += dimensions[key] * weight
            total_weight += weight
    if total_weight <= 0:
        return _NEUTRAL_SCORE
    return max(0.0, min(1.0, acc / total_weight))


async def judge_chapter_readability(
    session: AsyncSession,
    settings: AppSettings,
    chapter_text: str,
    *,
    chapter_number: int,
    project_id: UUID | None = None,
    workflow_run_id: UUID | None = None,
    step_run_id: UUID | None = None,
    text_cap_chars: int = 8000,
) -> ReaderJudgeResult:
    """Score one chapter's readability via the critic LLM (fail-open)."""

    body = (chapter_text or "").strip()
    if not body:
        return ReaderJudgeResult(_NEUTRAL_SCORE, {}, "empty", used_llm=False)
    if text_cap_chars > 0 and len(body) > text_cap_chars:
        body = body[:text_cap_chars]

    user_prompt = f"第{chapter_number}章正文：\n{body}\n\n按 system 指定 JSON 输出评分。"
    try:
        completion = await complete_text(
            session,
            settings,
            LLMCompletionRequest(
                logical_role="critic",
                system_prompt=_SYSTEM_PROMPT,
                user_prompt=user_prompt,
                fallback_response=_FALLBACK_JSON,
                prompt_template="reader_judge",
                prompt_version="1.0",
                project_id=project_id,
                workflow_run_id=workflow_run_id,
                step_run_id=step_run_id,
                metadata={"chapter_number": chapter_number},
            ),
        )
    except Exception:
        logger.debug("reader_judge LLM call failed ch%d", chapter_number, exc_info=True)
        return ReaderJudgeResult(_NEUTRAL_SCORE, {}, "llm-error", used_llm=False)

    data = _parse_judge_json(completion.content)
    if data is None:
        return ReaderJudgeResult(_NEUTRAL_SCORE, {}, "parse-error", used_llm=False)

    dimensions = {key: _clamp01(data.get(key)) for key in _RUBRIC_WEIGHTS}
    score = aggregate_prose_quality(dimensions)
    comment = str(data.get("comment") or "")[:60]
    return ReaderJudgeResult(
        prose_quality_score=score,
        dimensions=dimensions,
        comment=comment,
        used_llm=True,
    )


__all__ = [
    "ReaderJudgeResult",
    "aggregate_prose_quality",
    "judge_chapter_readability",
]
