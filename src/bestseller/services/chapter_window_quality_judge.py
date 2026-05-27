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
            system_prompt=(
                "# ROLE\n"
                "你是长篇连载小说的「滑窗连贯性主审」。\n"
                "你审过 200+ 部签约连载的章节窗口，最擅长在连续 3-5 章里嗅出「模板化重复 / 腔调漂移 / 节奏疲劳」。\n"
                "你的判断标准来自：起点 / 番茄 / 七猫的签约作品规律 + 编辑培训手册 + 5 年退稿经验。\n"
                "\n"
                "# CONTEXT\n"
                "你正在评审最近 N 章的窗口（不是单章）。\n"
                "你的评分决定：这段窗口是 ship 还是 trigger rewrite。\n"
                "窗口审与单章审的核心差异：你看的是「跨章趋势」，不是「单章独立质量」。\n"
                "\n"
                "# CONTEXT · 五个滑窗专属审视维度\n"
                "1. **模板化重复**：首句模板 / 开场结构 / 收尾套路是否跨章雷同（如多章用同一句开篇）\n"
                "2. **人物腔调漂移**：同一角色在窗口内的句式 / 用词 / 反应模式是否前后不一致\n"
                "3. **承接断裂**：上一章末钩子在下一章开头是否被合理接住，还是直接换场？\n"
                "4. **节奏疲劳**：是否连续 3 章高压（无喘息）或连续 3 章低压（无推进）\n"
                "5. **卷目标漂移**：本窗口的章节是否仍在推进当前卷的 milestone，还是已偏离？\n"
                "\n"
                "# TASK\n"
                "对窗口打多维度分（含 material_advancement_score），并产出 blocking / audit / rewrite_plan。\n"
                "\n"
                "# CONSTRAINTS · 评分纪律\n"
                f"- 通过阈值：overall_score ≥ {min_overall:.2f}，不能有 critical blocking。\n"
                "- evidence 硬性规则：blocking_issues 的 evidence 必须引用 content_excerpt 中**真实出现**的正文片段；\n"
                "  不得把 metadata / 历史修复记录 / last_block_codes / auto_repair / retry / gate 标记本身当作当前正文问题。\n"
                "- 判断禁用信号时，evidence 必须含正文里的**原词**；只在合同 / 元数据里出现的词不能阻塞。\n"
                "- material_advancement_score：根据章节 metadata / generation_input 的物料合同，评估窗口内是否连续推进规则、揭示、证据。\n"
                "\n"
                "# THINKING（产 JSON 前在脑内 4 步）\n"
                "1. 浏览窗口内每章的首句 + 末句 + 关键钩子 — 比对是否雷同\n"
                "2. 抽出同一角色在 ≥2 章的对白 / 反应 — 比对腔调一致性\n"
                "3. 检查上一章末钩子是否在下一章合理承接（不是被忽略）\n"
                "4. Reconcile：≥1 critical blocking → overall_score 不应 ≥ 0.75\n"
                "\n"
                "# OUTPUT FORMAT（严格 JSON）\n"
                "返回字段：pass, overall_score, dimension_scores (含 material_advancement_score), "
                "blocking_issues, audit_issues, rewrite_plan\n"
                "每个 issue 含：code, severity, evidence (原文≤30字), required_fix\n"
                "\n"
                "# RUBRIC（评分细则原文）\n"
                f"{rubric.system_prompt}\n"
            ),
            user_prompt=(
                rubric.render_prompt_block()
                + "\n\n## 任务参数\n"
                f"- 通过阈值：overall ≥ {min_overall:.2f}\n"
                f"- 窗口章数：{len(chapters)}\n"
                "\n## 章节窗口\n"
                "```json\n"
                f"{json.dumps(list(chapters), ensure_ascii=False, indent=2, default=str)[:22000]}\n"
                "```\n"
                "\n## 立即开始\n"
                "按 system 中的 5 个滑窗审视维度 + THINKING 步骤思考，输出严格 JSON。"
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
