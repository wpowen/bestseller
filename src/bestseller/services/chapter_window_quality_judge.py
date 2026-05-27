from __future__ import annotations

from collections.abc import Mapping, Sequence

# ruff: noqa: ANN401,RUF001
import json
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.domain.llm_quality_judge import (
    LLMQualityIssue,
    LLMQualityJudgeResult,
    quality_judge_result_from_mapping,
)
from bestseller.services.chapter_llm_quality_judge import _parse_json_object
from bestseller.services.judge_rubrics import get_judge_rubric
from bestseller.services.llm import LLMCompletionRequest, complete_text
from bestseller.settings import AppSettings


_INTERNAL_TELEMETRY_MARKERS = (
    "auto_repair",
    "last_block_codes",
    "retention_retry",
    "front10_framework",
    "fanqie_long_ranking_block",
    "repair_flags",
    "repair_attempts",
)
_WINDOW_FORBIDDEN_SIGNAL_TERMS = (
    "铜钱发烫",
    "发烫",
    "发热",
    "烫意",
    "滚烫",
    "变热",
    "热得",
    "热得像",
    "烫得像",
    "烧开",
    "高温",
    "灼热",
    "炭火",
    "掌心的旧伤开始发烫",
    "账页烫",
    "青囊烫",
    "铜钱烫",
)


async def judge_chapter_window_quality(
    session: AsyncSession,
    settings: AppSettings,
    *,
    chapters: Sequence[Mapping[str, Any]],
    min_overall: float = 0.79,
    workflow_run_id: Any | None = None,
) -> LLMQualityJudgeResult:
    fallback = json.dumps(
        {
            "pass": False,
            "overall_score": 0.0,
            "dimension_scores": {},
            "blocking_issues": [
                {
                    "code": "WINDOW_JUDGE_UNAVAILABLE",
                    "severity": "critical",
                    "evidence": "LLM window judge returned fallback content.",
                    "required_fix": "重新运行滑窗评测，避免连续章节重复或漂移漏检。",
                }
            ],
            "rewrite_plan": {"scope": "window", "instructions": "重新评测最近章节窗口。"},
        },
        ensure_ascii=False,
    )
    rubric = get_judge_rubric("chapter_window")
    completion = await complete_text(
        session,
        settings,
        LLMCompletionRequest(
            logical_role="critic",
            model_tier="strong",
            system_prompt=rubric.system_prompt,
            user_prompt=(
                rubric.render_prompt_block()
                + "\n\n"
                "评测最近章节窗口是否存在模板化重复、人物腔调漂移、承接断裂、节奏疲劳、卷目标漂移。"
                f"通过阈值 overall >= {min_overall:.2f}，且不能有 critical。"
                "返回字段：pass, overall_score, dimension_scores, "
                "blocking_issues, audit_issues, rewrite_plan。\n"
                "硬性证据规则：blocking_issues 的 evidence 必须引用 content_excerpt 中真实出现的正文片段；"
                "不得把 metadata、历史修复记录、last_block_codes、auto_repair、retry、gate 标记本身当作当前正文问题。"
                "如果判断禁用信号，evidence 必须包含正文里的原词；只在合同或元数据里出现的词不能阻塞。\n"
                "章节窗口：\n"
                f"{json.dumps(list(chapters), ensure_ascii=False, indent=2, default=str)[:22000]}"
            ),
            fallback_response=fallback,
            prompt_template="chapter_window_quality_judge",
            prompt_version="v1",
            workflow_run_id=workflow_run_id,
            metadata={"judge_scope": "window", "window_size": len(chapters), "rubric": rubric.name},
            max_tokens_override=4096,
        ),
    )
    result = quality_judge_result_from_mapping(
        _parse_json_object(completion.content),
        scope="window",
        min_overall=min_overall,
        min_dimensions={},
        llm_run_id=str(completion.llm_run_id) if completion.llm_run_id else None,
        raw_excerpt=completion.content[:6000],
    )
    return _downgrade_unsupported_window_blockers(result, chapters)


def _downgrade_unsupported_window_blockers(
    result: LLMQualityJudgeResult,
    chapters: Sequence[Mapping[str, Any]],
) -> LLMQualityJudgeResult:
    content_blob = "\n".join(str(item.get("content_excerpt") or "") for item in chapters)
    kept: list[LLMQualityIssue] = []
    downgraded: list[LLMQualityIssue] = []
    for issue in result.blocking_issues:
        if _is_unsupported_window_blocker(issue, content_blob):
            downgraded.append(
                issue.model_copy(
                    update={
                        "severity": "low",
                        "required_fix": (
                            (issue.required_fix + "\n") if issue.required_fix else ""
                        )
                        + "降级原因：该滑窗阻塞项缺少当前正文片段支撑，不能作为自动重写依据。",
                    }
                )
            )
        else:
            kept.append(issue)
    if not downgraded:
        return result
    return result.model_copy(
        update={
            "passed": True if not kept else result.passed,
            "blocking_issues": tuple(kept),
            "audit_issues": (*result.audit_issues, *downgraded),
        }
    )


def _is_unsupported_window_blocker(issue: LLMQualityIssue, content_blob: str) -> bool:
    code = issue.code.upper()
    evidence_text = " ".join(
        part
        for part in (issue.evidence, issue.required_fix, issue.path)
        if isinstance(part, str) and part
    )
    evidence_lc = evidence_text.lower()
    if any(marker in evidence_lc for marker in _INTERNAL_TELEMETRY_MARKERS):
        return True
    if "FORBIDDEN_SIGNAL" in code:
        return not any(term and term in content_blob for term in _WINDOW_FORBIDDEN_SIGNAL_TERMS)
    return False
