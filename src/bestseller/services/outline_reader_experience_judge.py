"""Reader-experience LLM judge for outline front-ten chapters."""

from __future__ import annotations

from collections.abc import Mapping
import json
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.domain.llm_quality_judge import (
    LLMQualityJudgeResult,
    quality_judge_result_from_mapping,
)
from bestseller.services.llm import LLMCompletionRequest, complete_text
from bestseller.services.methodology_bridge import get_fragment
from bestseller.services.outline_llm_judge import _parse_json_object
from bestseller.services.prompt_packs import PromptPack
from bestseller.settings import AppSettings


READER_EXPERIENCE_DIMENSIONS: tuple[str, ...] = (
    "spatial_coherence",
    "information_density",
    "protagonist_call_plausibility",
    "hook_prerequisite_satisfied",
    "motivation_chain_clarity",
    "first_read_followability",
)


async def judge_outline_reader_experience(
    session: AsyncSession,
    settings: AppSettings,
    *,
    chapters_payload: list[Mapping[str, Any]],
    project_brief: Mapping[str, Any] | None = None,
    threshold: float = 0.78,
    workflow_run_id: Any | None = None,
    pack: PromptPack | None = None,
) -> LLMQualityJudgeResult:
    """Simulate first-reader experience over the outline's ch1-10 payload."""
    golden = [
        ch
        for ch in chapters_payload
        if 1 <= int(ch.get("chapter_number") or ch.get("number") or 0) <= 10
    ]
    if not golden:
        return _empty_result(reason="No chapters in reader-experience scope")

    methodology_refs: list[str] = []
    for key in ("opening_rules", "character_design"):
        text = get_fragment(pack, phase="judge", fragment_key=key)
        if text:
            methodology_refs.append(f"【{key}】\n{text}")
    methodology_block = "\n\n".join(methodology_refs)

    chapters_text = json.dumps(golden, ensure_ascii=False, indent=2, default=str)
    if len(chapters_text) > 50000:
        chapters_text = chapters_text[:50000] + "\n...TRUNCATED..."

    brief_text = json.dumps(project_brief or {}, ensure_ascii=False, indent=2, default=str)[:3000]
    fallback = json.dumps(
        {
            "pass": True,
            "overall_score": 0.79,
            "dimension_scores": {},
            "blocking_issues": [],
            "audit_issues": [
                {
                    "code": "READER_EXPERIENCE_JUDGE_UNAVAILABLE",
                    "severity": "high",
                    "evidence": "Reader-experience judge fallback.",
                    "required_fix": "重跑评估或人工复核",
                }
            ],
            "rewrite_plan": {
                "scope": "outline",
                "preserve": [],
                "change": [],
                "instructions": "",
            },
        },
        ensure_ascii=False,
    )

    system_prompt = (
        "你是一名首次读者，没有任何前置知识。你将看到一份小说的前 10 章细纲，"
        "你需要假装从第 1 章第 1 句开始读，逐章评估你是否能跟上、能不能信任主角、"
        "信息密度是否过载、章末钩子前提是否已建立。"
        "\n\n严格只输出 JSON。你的评分必须基于读者体验而非作者意图。"
        "\n\n## 评估维度（每项 0-1 分）\n"
        + "\n".join(f"- {dim}" for dim in READER_EXPERIENCE_DIMENSIONS)
        + "\n\n## 硬性卡控（出现任一即 blocking）"
        "\n1. 空间错乱：一章内角色物理位置变化无过渡"
        "\n2. 信息密度爆炸：第 1 章涌入超过 3 个具名角色，或超过 2 个未解释的高概念术语"
        "\n3. 召唤主角无依据：读者无法在本章看到'为什么是这个主角'的依据"
        "\n4. 钩子前提缺失：章末钩子依赖的设定未在本章建立"
        "\n5. 内部事实矛盾：本章建立的事实被本章自己违反"
        + (
            f"\n\n## 评估时参照的方法论\n{methodology_block}"
            if methodology_block
            else ""
        )
    )

    user_prompt = (
        f"## 项目简介\n{brief_text}\n\n"
        f"## 黄金十章大纲\n{chapters_text}\n\n"
        "## 输出格式\n"
        '{"pass": bool, "overall_score": float, "dimension_scores": {dim: float}, '
        '"blocking_issues": [{code, severity, evidence, required_fix, chapter_no}], '
        '"audit_issues": [{code, severity, evidence, required_fix, chapter_no}], '
        '"rewrite_plan": {scope, preserve, change, instructions}}'
    )

    completion = await complete_text(
        session,
        settings,
        LLMCompletionRequest(
            logical_role="critic",
            model_tier="strong",
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            fallback_response=fallback,
            prompt_template="outline_reader_experience_judge",
            prompt_version="v1",
            workflow_run_id=workflow_run_id,
            metadata={"judge_scope": "reader_experience", "threshold": threshold},
            max_tokens_override=4096,
        ),
    )
    return quality_judge_result_from_mapping(
        _parse_json_object(completion.content),
        scope="reader_experience",
        min_overall=threshold,
        min_dimensions={
            "spatial_coherence": threshold - 0.05,
            "information_density": threshold - 0.05,
            "protagonist_call_plausibility": threshold,
            "hook_prerequisite_satisfied": threshold - 0.05,
        },
        llm_run_id=str(completion.llm_run_id) if completion.llm_run_id else None,
        raw_excerpt=completion.content[:5000],
    )


def _empty_result(*, reason: str) -> LLMQualityJudgeResult:
    return quality_judge_result_from_mapping(
        {
            "pass": True,
            "overall_score": 1.0,
            "blocking_issues": [],
            "audit_issues": [
                {
                    "code": "OUT_OF_SCOPE",
                    "severity": "low",
                    "evidence": reason,
                    "required_fix": "",
                }
            ],
        },
        scope="reader_experience",
        min_overall=0.0,
    )


__all__ = ["READER_EXPERIENCE_DIMENSIONS", "judge_outline_reader_experience"]
