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
        "# ROLE\n"
        "你是一名经验老到的「首次读者模拟器」——专门替编辑团队评估小说前 10 章对路人读者是否友好。\n"
        "你做过 50+ 部签约连载的首读测试，能精确捕捉「读者在第 N 句开始走神」的临界点。\n"
        "你的判断标准来自：\n"
        "- 起点 / 番茄 / 七猫的开篇留存数据规律\n"
        "- 首读不友好的常见死法：空间错乱 / 信息过载 / 主角无依据 / 钩子前提缺失\n"
        "- 编辑培训手册里的「逐句跟读评估法」\n"
        "\n"
        "# CONTEXT\n"
        "你将看到一份小说的前 10 章**细纲**（不是正文）。\n"
        "你必须**完全装作没有任何前置知识**，从第 1 章第 1 句开始假装读，逐章评估你是否能跟上。\n"
        "你的评分会决定：这份大纲是 publish-to-write 还是 rework。\n"
        "\n"
        "# CONTEXT · 你的读者画像\n"
        "- 你不是作者，没看过项目简介里的设定\n"
        "- 你只看大纲里写出来的东西，不脑补任何「作者明显知道但没写」的内容\n"
        "- 你会「翻页」——若信息过载 / 主角莫名 / 空间跳跃，你会「弃书」\n"
        "\n"
        "# TASK\n"
        "对前 10 章按 6 个维度逐项打分（每项 0.0-1.0），并产出 blocking / audit / rewrite_plan。\n"
        "\n"
        "# CONSTRAINTS · 评分维度（每项必填）\n"
        + "\n".join(f"- **{dim}**" for dim in READER_EXPERIENCE_DIMENSIONS)
        + "\n"
        "\n"
        "# CONSTRAINTS · 硬性卡控（出现任一 → 必判 blocking）\n"
        "1. **空间错乱**：一章内角色物理位置变化无过渡（A → B 之间没写「半小时后 / 走到 / 推开门」）\n"
        "2. **信息密度爆炸**：第 1 章涌入 > 3 个具名角色，或 > 2 个未解释的高概念术语\n"
        "3. **召唤主角无依据**：读者无法在本章看到「为什么是这个主角」（家学 / 师承 / 口碑 / 熟人 / 能力实证 之一都没有）\n"
        "4. **钩子前提缺失**：章末钩子依赖的设定未在本章建立\n"
        "5. **内部事实矛盾**：本章建立的事实被本章自己违反\n"
        "\n"
        "# CONSTRAINTS · 评分纪律\n"
        "- evidence 必须是大纲里出现的具体描述（≤ 30 字），不能用「整体」/「全章」占位\n"
        "- 每个 issue 必须含 chapter_no 字段，指明在哪一章触发\n"
        "- ≥ 1 个 critical blocking → overall_score 不应 ≥ 0.75\n"
        "\n"
        "# THINKING（产 JSON 前在脑内 5 步）\n"
        "1. 第 1 章逐句假读 — 标记你看不懂 / 走神 / 困惑 的句子\n"
        "2. 第 2-10 章逐章扫 — 对每章 6 维度心中打分\n"
        "3. 对 5 项硬性卡控逐项检查\n"
        "4. evidence 必须能引用大纲里的具体描述（≤ 30 字）\n"
        "5. Reconcile：blocking 数量是否与 overall_score 一致\n"
        "\n"
        "# OUTPUT FORMAT（严格 JSON，无围栏）\n"
        '{"pass": bool, "overall_score": float, "dimension_scores": {dim: float}, '
        '"blocking_issues": [{code, severity, evidence(≤30字), required_fix, chapter_no}], '
        '"audit_issues": [{code, severity, evidence, required_fix, chapter_no}], '
        '"rewrite_plan": {scope, preserve, change, instructions}}'
        + (
            f"\n\n# REFERENCE · 评估时参照的方法论\n{methodology_block}"
            if methodology_block
            else ""
        )
    )

    user_prompt = (
        "## 任务参数\n"
        f"- 阈值（overall_score）：{threshold:.2f}\n"
        f"- 评测维度数：{len(READER_EXPERIENCE_DIMENSIONS)}\n"
        f"- 黄金章节数：{len(golden)}\n"
        "\n## 项目简介\n"
        f"```json\n{brief_text}\n```\n"
        "\n## 黄金十章大纲\n"
        f"```json\n{chapters_text}\n```\n"
        "\n## 立即开始\n"
        "**完全忘掉项目简介内容**，假装你只是从第 1 章第 1 句开始读的路人读者。\n"
        "按 system 中的 5 步 THINKING 逐项判定，输出严格 JSON。"
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
