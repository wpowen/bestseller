"""Runtime wiring for methodology-v2 hook ledger audits.

This module keeps :mod:`bestseller.services.hook_ledger` infra-free.  It is the
small adapter that reads existing DB clues plus the current chapter contract,
then folds hook-ledger findings into planner/review flows behind the
``BESTSELLER_METHODOLOGY_V2`` feature flag.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.domain.narrative import ChapterContractRead
from bestseller.domain.review import (
    ChapterReviewFinding,
    ChapterReviewResult,
)
from bestseller.infra.db.models import ChapterModel, ClueModel, ProjectModel
from bestseller.services.hook_ledger import (
    AuditFinding,
    HookLedgerAudit,
    HookType,
    is_methodology_v2_enabled,
    run_hook_ledger_audit,
)
from bestseller.services.writing_profile import is_english_language

_REWRITE_CODES = {
    "HOOK_ACTIVE_COUNT_TOO_HIGH",
    "HOOK_PER_CHAPTER_NO_PLANT",
    "HOOK_PER_CHAPTER_NO_RESOLVE",
    "HOOK_OVERDUE",
    "HOOK_NEXT_COMPRESSION_SEED_MISSING",
}


@dataclass(frozen=True)
class _ContractHookClue:
    clue_code: str
    clue_type: str = "foreshadow"
    label: str = ""
    planted_in_chapter_number: int | None = None
    expected_payoff_by_chapter_number: int | None = None
    actual_paid_off_chapter_number: int | None = None
    status: str = "planted"
    metadata_json: dict[str, Any] = field(default_factory=dict)


def render_hook_ledger_planner_contract(*, language: str | None = None) -> str:
    """Return the methodology-v2 planner contract block when enabled."""

    if not is_methodology_v2_enabled():
        return ""
    if is_english_language(language):
        return (
            "[Methodology v2 hook ledger contract]\n"
            "- Treat `methodology_contract.hooks_to_resolve` and "
            "`methodology_contract.hooks_to_plant` as a chapter ledger delta, "
            "not decorative notes.\n"
            "- Each chapter must resolve at least one previous hook when "
            "possible and plant at least one new hook.\n"
            "- Keep 3-7 active hooks across the rolling ledger; avoid starving "
            "or flooding the reader.\n"
            "- Budget the delta: chapter 1 may plant 2-3 hooks; later chapters "
            "usually plant 1-2 and resolve 1-2. If active hooks would exceed 7, "
            "resolve at least as many as you plant and do not add extra hooks.\n"
            "- No hook may stay unresolved for more than 15 chapters.\n"
            "- After a payoff/release chapter, plant the next compression seed "
            "immediately.\n"
            "- Use the five hook types explicitly in scene "
            "`methodology_contract.hook_type`: information_gap, deadline, "
            "mystery, desire, threat."
        )
    return (
        "【方法论 v2 钩子台账合同】\n"
        "- 把 `methodology_contract.hooks_to_resolve` 和 "
        "`methodology_contract.hooks_to_plant` 当成本章 hook ledger delta，"
        "不是装饰性说明。\n"
        "- 每章在条件允许时至少消解一个旧钩子，并至少植入一个新钩子。\n"
        "- 滚动台账保持 3-7 个活跃钩子，避免悬念饥饿或过载。\n"
        "- 控制本章 delta：第 1 章可植入 2-3 个钩子；第 2 章以后通常只植入 "
        "1-2 个、消解 1-2 个。若活跃钩子会超过 7 个，必须消解数不少于植入数，"
        "且不得额外加钩子。\n"
        "- 任一钩子不得超过 15 章不处理。\n"
        "- 每次 payoff / 释放之后，下一章必须立刻种下新的压缩种子。\n"
        "- 场景级 `methodology_contract.hook_type` 必须落到五类之一："
        "information_gap、deadline、mystery、desire、threat。"
    )


async def compute_hook_ledger_audit_for_review(
    *,
    session: AsyncSession,
    project: ProjectModel,
    chapter: ChapterModel,
    chapter_contract: ChapterContractRead | None,
) -> HookLedgerAudit | None:
    """Compute the review-time hook-ledger audit when methodology v2 is on."""

    if not is_methodology_v2_enabled():
        return None
    clues = list(
        await session.scalars(
            select(ClueModel).where(ClueModel.project_id == project.id)
        )
    )
    clues.extend(_contract_clues(chapter_contract, chapter_number=chapter.chapter_number))
    return run_hook_ledger_audit(clues, current_chapter=chapter.chapter_number)


def merge_hook_ledger_audit_into_chapter_review(
    review_result: ChapterReviewResult,
    audit: HookLedgerAudit | None,
    *,
    chapter_number: int,
    language: str | None = None,
) -> ChapterReviewResult:
    """Add hook-ledger evidence and targeted repair instructions to review."""

    if audit is None or not audit.all_findings:
        return review_result

    findings = [
        ChapterReviewFinding(
            category="hook_ledger",
            severity=_review_severity(finding, chapter_number=chapter_number),
            message=f"{finding.code}: {finding.detail}",
        )
        for finding in audit.all_findings
    ]
    should_rewrite = any(
        _finding_requires_rewrite(finding, chapter_number=chapter_number)
        for finding in audit.all_findings
    )
    severity_max = _max_severity(
        review_result.severity_max,
        (finding.severity for finding in findings),
    )
    evidence_summary = {
        **review_result.evidence_summary,
        "hook_ledger_audit": hook_ledger_audit_to_dict(audit),
    }
    rewrite_prefix = (
        _hook_ledger_rewrite_instructions(audit, chapter_number, language=language)
        if should_rewrite
        else None
    )
    return ChapterReviewResult(
        verdict="rewrite" if should_rewrite else review_result.verdict,
        severity_max=severity_max,
        scores=review_result.scores,
        findings=[*review_result.findings, *findings],
        evidence_summary=evidence_summary,
        rewrite_instructions=(
            f"{rewrite_prefix}\n\n{review_result.rewrite_instructions}"
            if rewrite_prefix and review_result.rewrite_instructions
            else rewrite_prefix or review_result.rewrite_instructions
        ),
    )


def hook_ledger_audit_to_dict(audit: HookLedgerAudit) -> dict[str, Any]:
    return {
        "closure_rate": round(audit.closure_rate, 3),
        "active_count": audit.active_count.active_count,
        "active_min_expected": audit.active_count.min_expected,
        "active_max_expected": audit.active_count.max_expected,
        "plant_count": audit.per_chapter_balance.plant_count,
        "resolve_count": audit.per_chapter_balance.resolve_count,
        "max_age_chapters": audit.max_age.max_age_chapters,
        "overdue_codes": [entry.clue_code for entry in audit.max_age.overdue_entries],
        "previous_chapter_had_payoff": (
            audit.next_compression_seed.previous_chapter_had_payoff
        ),
        "current_chapter_planted_new": (
            audit.next_compression_seed.current_chapter_planted_new
        ),
        "by_type": {hook_type.value: count for hook_type, count in audit.by_type().items()},
        "findings": [_finding_to_dict(finding) for finding in audit.all_findings],
    }


def _contract_clues(
    chapter_contract: ChapterContractRead | None,
    *,
    chapter_number: int,
) -> list[_ContractHookClue]:
    if chapter_contract is None:
        return []
    clues: list[_ContractHookClue] = []
    for index, text in enumerate(_clean_strings(chapter_contract.hooks_to_plant), start=1):
        clues.append(
            _ContractHookClue(
                clue_code=f"contract:ch{chapter_number}:plant:{index}",
                clue_type=_infer_hook_type(text).value,
                label=text,
                planted_in_chapter_number=chapter_number,
                expected_payoff_by_chapter_number=chapter_number + 3,
                metadata_json={
                    "source": "chapter_methodology_contract",
                    "hook_type": _infer_hook_type(text).value,
                },
            )
        )
    for index, text in enumerate(_clean_strings(chapter_contract.hooks_to_resolve), start=1):
        clues.append(
            _ContractHookClue(
                clue_code=f"contract:ch{chapter_number}:resolve:{index}",
                clue_type=_infer_hook_type(text).value,
                label=text,
                planted_in_chapter_number=max(1, chapter_number - 1),
                expected_payoff_by_chapter_number=chapter_number,
                actual_paid_off_chapter_number=chapter_number,
                status="resolved",
                metadata_json={
                    "source": "chapter_methodology_contract",
                    "hook_type": _infer_hook_type(text).value,
                },
            )
        )
    return clues


def _infer_hook_type(text: str) -> HookType:
    lowered = text.lower()
    if any(token in text for token in ("倒计时", "期限", "时限", "截止", "限时")) or any(
        token in lowered for token in ("deadline", "countdown")
    ):
        return HookType.DEADLINE
    if any(token in text for token in ("威胁", "危险", "杀", "追杀", "毁掉", "暴露")) or any(
        token in lowered for token in ("threat", "danger")
    ):
        return HookType.THREAT
    if any(token in text for token in ("想要", "欲望", "渴望", "必须得到")) or "desire" in lowered:
        return HookType.DESIRE
    if any(token in text for token in ("谜", "悬案", "真相", "谁", "为什么")) or any(
        token in lowered for token in ("mystery", "puzzle")
    ):
        return HookType.MYSTERY
    return HookType.INFORMATION_GAP


def _clean_strings(values: Iterable[Any]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(str(value).strip() for value in values if str(value).strip()))


def _finding_to_dict(finding: AuditFinding) -> dict[str, Any]:
    return {
        "code": finding.code,
        "severity": finding.severity,
        "detail": finding.detail,
        "evidence": finding.evidence,
    }


def _finding_requires_rewrite(
    finding: AuditFinding,
    *,
    chapter_number: int,
) -> bool:
    if finding.code == "HOOK_PER_CHAPTER_NO_RESOLVE" and chapter_number <= 1:
        return False
    return finding.code in _REWRITE_CODES


def _review_severity(finding: AuditFinding, *, chapter_number: int) -> str:
    if _finding_requires_rewrite(finding, chapter_number=chapter_number):
        return "major"
    return "warning"


def _max_severity(current: str, incoming: Iterable[str]) -> str:
    rank = {"info": 0, "warning": 1, "major": 2, "critical": 3}
    result = current
    for item in incoming:
        if rank.get(item, 0) > rank.get(result, 0):
            result = item
    return result


def _hook_ledger_rewrite_instructions(
    audit: HookLedgerAudit,
    chapter_number: int,
    *,
    language: str | None,
) -> str:
    codes = [
        finding.code
        for finding in audit.all_findings
        if _finding_requires_rewrite(finding, chapter_number=chapter_number)
    ]
    if is_english_language(language):
        return (
            "[hook ledger repair]\n"
            f"Fix hook-ledger deficits in chapter {chapter_number}: {', '.join(codes)}.\n"
            "- Preserve the existing plot outcome.\n"
            "- Add or clarify at least one concrete planted hook and, when this "
            "is not chapter 1, one concrete resolved hook.\n"
            "- If an old hook is overdue, visibly resolve, transform, or explicitly retire it.\n"
            "- After any payoff, seed the next pressure/question before the chapter closes.\n"
            "- Do not mention hook-ledger terminology in prose."
        )
    return (
        "【钩子台账修复】\n"
        f"修复第 {chapter_number} 章的钩子台账缺口：{', '.join(codes)}。\n"
        "- 保留既有剧情结果，不要重开新章。\n"
        "- 至少补出一个读者可见的新植入钩子；除第 1 章外，还要补出一个旧钩子的可见消解。\n"
        "- 如果存在超期钩子，必须在正文中解决、转化或明确退场。\n"
        "- 如果本章有 payoff / 情绪释放，章末前必须种下下一轮压力或未答问题。\n"
        "- 正文禁止出现 hook ledger、方法论、钩子台账等创作术语。"
    )


__all__ = [
    "compute_hook_ledger_audit_for_review",
    "hook_ledger_audit_to_dict",
    "merge_hook_ledger_audit_into_chapter_review",
    "render_hook_ledger_planner_contract",
]
