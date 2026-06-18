from __future__ import annotations

import logging
from uuid import UUID

from pydantic import BaseModel, Field
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.infra.db.models import (
    CanonFactModel,
    ChapterModel,
    TimelineEventModel,
)
from bestseller.services.llm import LLMCompletionRequest, complete_text
from bestseller.services.prompt_input_formatter import (
    group_facts_by_type,
    render_task_header,
)
from bestseller.settings import AppSettings

logger = logging.getLogger(__name__)


_ROLLING_SUMMARY_SYSTEM_ZH = """# ROLE
你是一位长篇小说"连贯性主编"。
你的工作不是给读者写"剧情回顾"，而是给**下游写手 LLM**写一份"如果你忘了前文，看这一份就够了"的工作记忆。
你做过的最长一本书 800 章，每 50 章为下一段写手生成一份滚动摘要。
你深知：摘要不是流水账，而是"取舍纪律"的产物——只留下下游不能丢的信息。

# CONTEXT
- 下游消费者：另一个 LLM（写后续章节）
- 它需要快速捕获的 4 类信息：
  1. 哪些角色发生了**不可逆变化**（不能反悔的）
  2. 哪些**线索/伏笔**已埋下但尚未回收
  3. 哪些**规则**被建立或破坏
  4. 当前的**未解钩子清单**

# TASK
基于给定的 canon_facts 与 timeline_events，输出一份 ≤800 中文字摘要。
**写给下游写手看，不是写给读者看**——禁止文学化、禁止悬念腔、禁止主观评价。

# CONSTRAINTS（违反即重写）
- 字数硬上限：800 中文字
- 必须 4 段格式（## 角色状态 / ## 已埋未回收的线索 / ## 已建立或破坏的规则 / ## 当前未解钩子），缺段不合格
- 禁止虚构：所有信息必须能追溯到给定 facts/events
- 禁止主观评价词（精彩、令人惊讶、引人入胜、扣人心弦…）
- 禁止剧透未来章节
- 第三人称、过去时、短句优先

# THINKING（产出前在脑内 4 步）
1. 浏览所有 facts，按"角色变化 / 物件变化 / 关系变化 / 规则变化"分桶
2. 标记"高密度章节"（含 ≥3 个变化的章）
3. 抽出 5-15 条"下游写手不能忘"的核心
4. 用第三人称 + 过去时 + 短句写

# OUTPUT FORMAT（严格 4 段 markdown，无前言无后语）
## 角色状态
- 角色 A：…（变化）
- 角色 B：…

## 已埋未回收的线索
- 线索 X（首次出现 chN）：…
- 线索 Y（首次出现 chN）：…

## 已建立或破坏的规则
- R-XXX：…（chN 建立 / chN 破坏）

## 当前未解钩子
- 钩子 1：…
- 钩子 2：…

# EXAMPLE（合格摘要的样子 —— 仅示意结构，人名/物件以本书实际为准）
## 角色状态
- 主角：在 ch5 失去关键信物一角；ch7 第一次被对手锁定行踪；对某盟友从警惕转为有限协作。
- 某盟友：从立场中立转为可对主角半信半疑的协作者。
- 某配角：ch1 被卷入险境，ch4 被主角确认仍存活。

## 已埋未回收的线索
- 父辈遗留的关键物件（ch3 首次提及，下落不明）
- 与开篇同款的旧物（ch1 末，来源未交代）
"""

_ROLLING_SUMMARY_SYSTEM_EN = """# ROLE
You are a long-form fiction continuity editor.
Your output is **working memory for a downstream chapter-writing LLM**, not a reader-facing recap.

# CONTEXT
Downstream consumer needs: irreversible character changes / unresolved foreshadowing /
established or broken rules / current open hooks.

# TASK
Produce ≤800-word summary. Four sections, no preamble.

# CONSTRAINTS
- ≤800 words total
- Strict 4-section format (Character State / Unresolved Foreshadowing / Rules Established or Broken / Open Hooks)
- No fabrication — every claim must trace to given facts/events
- No subjective praise ("brilliant", "compelling")
- Third person, past tense, short sentences

# OUTPUT FORMAT
## Character State
- ...

## Unresolved Foreshadowing
- ...

## Rules Established or Broken
- ...

## Open Hooks
- ...
"""


def _build_system_prompt(language: str) -> str:
    if str(language or "").lower().startswith("en"):
        return _ROLLING_SUMMARY_SYSTEM_EN
    return _ROLLING_SUMMARY_SYSTEM_ZH


class RollingSummaryResult(BaseModel):
    project_id: UUID
    from_chapter: int
    to_chapter: int
    fact_count_before: int = 0
    summary_fact_created: bool = False
    summary_text: str = ""


async def compress_knowledge_window(
    session: AsyncSession,
    settings: AppSettings,
    project_id: UUID,
    from_chapter: int,
    to_chapter: int,
    *,
    workflow_run_id: UUID | None = None,
    language: str = "zh-CN",
) -> RollingSummaryResult:
    """Compress canon facts and timeline events from a chapter range into a rolling summary.

    This creates a single CanonFact with fact_type='rolling_summary' that condenses
    the knowledge from the specified chapter range. Original facts are preserved but
    can be deprioritized in context assembly for chapters far beyond the summary range.
    """
    # Gather existing facts in range
    facts = list(
        await session.scalars(
            select(CanonFactModel).where(
                CanonFactModel.project_id == project_id,
                CanonFactModel.is_current.is_(True),
                CanonFactModel.valid_from_chapter_no >= from_chapter,
                CanonFactModel.valid_from_chapter_no <= to_chapter,
                CanonFactModel.fact_type != "rolling_summary",
            )
        )
    )
    # Gather timeline events in range
    events = list(
        await session.scalars(
            select(TimelineEventModel).where(
                TimelineEventModel.project_id == project_id,
                TimelineEventModel.chapter_id.in_(
                    select(ChapterModel.id).where(
                        ChapterModel.project_id == project_id,
                        ChapterModel.chapter_number >= from_chapter,
                        ChapterModel.chapter_number <= to_chapter,
                    )
                ),
            ).order_by(TimelineEventModel.story_order.asc())
        )
    )

    if not facts and not events:
        return RollingSummaryResult(
            project_id=project_id,
            from_chapter=from_chapter,
            to_chapter=to_chapter,
        )

    # ── Build user prompt using the standard formatter ──────────────────
    # facts: bucket by fact_type so the LLM sees grouped info (角色变化 /
    # 物件变化 / 关系变化 / 规则变化), each bucket capped.
    facts_md = group_facts_by_type(
        facts[:200],
        type_attr="fact_type",
        max_per_group=40,
    )
    event_lines = "\n".join(
        f"- ch{e.story_order} · {e.event_name}" for e in events[:100]
    )
    event_truncated = (
        f"\n\n_（仅显示前 100 / 共 {len(events)} 条）_"
        if len(events) > 100
        else ""
    )

    user_prompt = (
        render_task_header(
            from_chapter=from_chapter,
            to_chapter=to_chapter,
            fact_count_total=len(facts),
            event_count_total=len(events),
        )
        + "\n\n## Canon Facts（按 fact_type 分桶）\n"
        + facts_md
        + "\n\n## Timeline Events（按 story_order 排序）\n"
        + event_lines
        + event_truncated
        + "\n\n## 立即开始\n"
        "按 system 中的 4 段格式输出摘要。"
        "禁止前言、禁止后语、禁止文学化修饰。"
    )

    response = await complete_text(
        session,
        settings,
        LLMCompletionRequest(
            logical_role="summarizer",
            system_prompt=_build_system_prompt(language),
            user_prompt=user_prompt,
            fallback_response=f"Rolling summary for chapters {from_chapter}-{to_chapter}: {len(facts)} facts and {len(events)} events.",
            prompt_template="rolling_summary",
            project_id=project_id,
            workflow_run_id=workflow_run_id,
        ),
    )
    summary_text = response.content.strip()

    # Create a rolling_summary canon fact
    summary_fact = CanonFactModel(
        project_id=project_id,
        subject_type="project",
        subject_label=f"rolling_summary_ch{from_chapter}_to_ch{to_chapter}",
        predicate="rolling_summary",
        fact_type="rolling_summary",
        value_json={"summary": summary_text, "from_chapter": from_chapter, "to_chapter": to_chapter},
        confidence=0.9,
        source_type="generated",
        valid_from_chapter_no=from_chapter,
        valid_to_chapter_no=to_chapter,
        is_current=True,
        tags=["rolling_summary"],
    )
    session.add(summary_fact)
    await session.flush()

    logger.info(
        "Created rolling summary for project %s chapters %d-%d (%d facts, %d events compressed)",
        project_id,
        from_chapter,
        to_chapter,
        len(facts),
        len(events),
    )

    return RollingSummaryResult(
        project_id=project_id,
        from_chapter=from_chapter,
        to_chapter=to_chapter,
        fact_count_before=len(facts),
        summary_fact_created=True,
        summary_text=summary_text,
    )
