"""LLM adjudication layer for context-dependent deterministic gate findings.

Brittle regex/heuristic gates (e.g. the common-sense causality gate) emit
*candidate* findings that can be false positives, because a regex cannot read
context — a car-crash victim's bleeding looks identical to an "unexplained"
nosebleed. Historically such a candidate could force a rewrite or hard-block
publication with no model in the loop, and the LLM verdict could only escalate
toward rewrite, never clear a false positive.

This module inverts that for the *context-dependent* gates only: the
deterministic detector still runs and produces candidates, but before a
candidate is allowed to block, an LLM reads the surrounding prose and either
CONFIRMs it (a real defect) or DISMISSes it (a false positive given context).

Structural gates (length floor, scene provenance, exact-duplicate paragraphs,
unfinished-artifact markers) are intentionally NOT adjudicated — they are
objective and stay deterministic. Only categories in ``ADJUDICABLE_CATEGORIES``
are sent to the model.

Safety: the LLM call is fail-closed. If adjudication is disabled, errors, or
returns an unparseable verdict for a finding, that finding is **kept**
(treated as CONFIRMed), so a model hiccup can never silently ship bad prose.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Protocol, Sequence
from uuid import UUID

from bestseller.services.llm import LLMCompletionRequest, complete_text

# Finding categories whose verdicts depend on reading prose context and are
# therefore worth an LLM second opinion before they may hard-block.
ADJUDICABLE_CATEGORIES = frozenset({"common_sense"})

# Only high/medium findings actually block, so only those are worth adjudicating.
_BLOCKING_SEVERITIES = frozenset({"high", "medium"})


class _Finding(Protocol):
    category: str
    severity: str
    message: str


@dataclass(frozen=True)
class AdjudicationResult:
    """Outcome of adjudicating one chapter's adjudicable findings."""

    confirmed: list[Any] = field(default_factory=list)
    dismissed: list[Any] = field(default_factory=list)
    reasons: dict[int, str] = field(default_factory=dict)
    reviewer: str = "none"

    @property
    def changed(self) -> bool:
        return bool(self.dismissed)


def is_adjudicable(finding: Any) -> bool:
    return (
        getattr(finding, "category", "") in ADJUDICABLE_CATEGORIES
        and getattr(finding, "severity", "") in _BLOCKING_SEVERITIES
    )


def partition_findings(findings: Sequence[Any]) -> tuple[list[Any], list[Any]]:
    """Split into (adjudicable, non-adjudicable) preserving order."""

    adjudicable = [f for f in findings if is_adjudicable(f)]
    other = [f for f in findings if not is_adjudicable(f)]
    return adjudicable, other


def build_adjudication_prompts(
    findings: Sequence[Any],
    *,
    text: str,
    genre: str | None,
    sub_genre: str | None,
    language: str | None = None,
) -> tuple[str, str]:
    """Build (system, user) prompts asking the model to CONFIRM/DISMISS each finding."""

    is_en = (language or "").lower().startswith("en")
    if is_en:
        system = (
            "You are a pre-publication QA reviewer for web fiction. An automated "
            "checker (keyword/regex based) flagged the issues below; because it cannot "
            "read context, some may be false positives. For EACH issue decide CONFIRM "
            "(a genuine defect that should block) or DISMISS (reasonable in context — a "
            "false positive). Be strict: DISMISS only when the surrounding prose clearly "
            "explains or justifies the flagged phenomenon. Judge by the prose itself, not "
            "by what you wish it said."
        )
        header = f"Genre: {genre or 'unknown'} / {sub_genre or ''}\n\nFlagged issues:\n"
        fmt = (
            "\nFor each numbered issue output exactly one line:\n"
            "<number>: CONFIRM|DISMISS - <short reason>\n"
        )
        prose_label = "\n--- CHAPTER PROSE ---\n"
    else:
        system = (
            "你是网文出版前的质检复核员。下面这些问题是自动检测器（基于关键词/正则）报出来的，"
            "由于它读不懂上下文，可能存在误报。请你逐条判断：CONFIRM（确实是缺陷，应当拦截）"
            "或 DISMISS（结合上下文其实合理，是误报）。判断要严格：只有当正文上下文确实能解释或"
            "交代该现象时才 DISMISS。以正文实际写到的内容为准，不要脑补。"
        )
        header = f"题材：{genre or '未知'} / {sub_genre or ''}\n\n被标记的问题：\n"
        fmt = (
            "\n请逐条输出，每条恰好一行：\n"
            "<编号>: CONFIRM|DISMISS - <简短理由>\n"
        )
        prose_label = "\n--- 章节正文 ---\n"

    lines = [
        f"{i + 1}. [{getattr(f, 'category', '')}/{getattr(f, 'severity', '')}] "
        f"{getattr(f, 'message', '')}"
        for i, f in enumerate(findings)
    ]
    user = header + "\n".join(lines) + fmt + prose_label + (text or "")
    return system, user


_VERDICT_RE = re.compile(
    r"^\s*(\d+)\s*[:：.、)]\s*\**\s*(CONFIRM|DISMISS)\b",
    re.IGNORECASE | re.MULTILINE,
)


def parse_adjudication_response(
    response: str,
    count: int,
    *,
    fail_closed: bool = True,
) -> list[bool]:
    """Return a list of ``confirmed`` flags, one per finding (1-indexed in text).

    Unparseable / missing entries default to ``fail_closed`` (True = keep the
    finding). A finding is dismissed only on an explicit, parseable DISMISS.
    """

    confirmed = [fail_closed] * count
    for match in _VERDICT_RE.finditer(response or ""):
        idx = int(match.group(1)) - 1
        if 0 <= idx < count:
            confirmed[idx] = match.group(2).upper() == "CONFIRM"
    return confirmed


async def adjudicate_findings(
    session: Any,
    settings: Any,
    project: Any,
    *,
    chapter_number: int,
    text: str,
    findings: Sequence[Any],
    workflow_run_id: UUID | None = None,
    step_run_id: UUID | None = None,
) -> AdjudicationResult:
    """Adjudicate the adjudicable findings in ``findings`` via the critic LLM.

    Returns an :class:`AdjudicationResult` whose ``dismissed`` list holds the
    candidate findings the model judged to be false positives in context. All
    non-adjudicable findings, and any adjudicable finding the model CONFIRMs (or
    that cannot be parsed), are left untouched (fail-closed).
    """

    adjudicable, _other = partition_findings(findings)
    if not adjudicable:
        return AdjudicationResult()

    pipeline = getattr(settings, "pipeline", settings)
    if not bool(getattr(pipeline, "gate_llm_adjudication_enabled", True)):
        return AdjudicationResult()

    system_prompt, user_prompt = build_adjudication_prompts(
        adjudicable,
        text=text,
        genre=getattr(project, "genre", None),
        sub_genre=getattr(project, "sub_genre", None),
        language=getattr(project, "language", None),
    )
    # Fail-closed fallback: a response that confirms everything → nothing dismissed.
    fallback = "\n".join(f"{i + 1}: CONFIRM" for i in range(len(adjudicable)))

    try:
        completion = await complete_text(
            session,
            settings,
            LLMCompletionRequest(
                logical_role="critic",
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                fallback_response=fallback,
                prompt_template="gate_adjudication",
                prompt_version="1.0",
                project_id=getattr(project, "id", None),
                workflow_run_id=workflow_run_id,
                step_run_id=step_run_id,
                metadata={
                    "project_slug": getattr(project, "slug", None),
                    "chapter_number": chapter_number,
                    "candidate_count": len(adjudicable),
                },
            ),
        )
        response = completion.content or fallback
        reviewer = completion.model_name
    except Exception:
        response = fallback
        reviewer = "fallback-error"

    confirmed_flags = parse_adjudication_response(
        response, len(adjudicable), fail_closed=True
    )
    confirmed: list[Any] = []
    dismissed: list[Any] = []
    for finding, is_confirmed in zip(adjudicable, confirmed_flags):
        (confirmed if is_confirmed else dismissed).append(finding)

    return AdjudicationResult(
        confirmed=confirmed,
        dismissed=dismissed,
        reviewer=reviewer,
    )


__all__ = [
    "ADJUDICABLE_CATEGORIES",
    "AdjudicationResult",
    "adjudicate_findings",
    "build_adjudication_prompts",
    "is_adjudicable",
    "parse_adjudication_response",
    "partition_findings",
]
