"""Runtime wiring for methodology-v2 payoff ledger audits."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.domain.narrative import ChapterContractRead
from bestseller.domain.review import ChapterReviewFinding, ChapterReviewResult
from bestseller.infra.db.models import (
    ChapterModel,
    ClueModel,
    PayoffModel,
    ProjectModel,
)
from bestseller.services.hook_ledger import AuditFinding, is_methodology_v2_enabled
from bestseller.services.payoff_ledger import (
    PayoffLedgerAudit,
    run_payoff_ledger_audit,
)
from bestseller.services.writing_profile import is_english_language

_REWRITE_CODES = {
    "PAYOFF_DUE_UNRESOLVED",
    "PAYOFF_OVERDUE",
    "PAYOFF_SETUP_TOO_SHORT",
}


@dataclass(frozen=True)
class _ContractPayoff:
    payoff_code: str
    label: str
    description: str
    source_clue_id: Any | None = None
    target_chapter_number: int | None = None
    actual_chapter_number: int | None = None
    status: str = "planned"
    metadata_json: dict[str, Any] = field(default_factory=dict)


async def compute_payoff_ledger_audit_for_review(
    *,
    session: AsyncSession,
    project: ProjectModel,
    chapter: ChapterModel,
    chapter_contract: ChapterContractRead | None,
) -> PayoffLedgerAudit | None:
    """Compute the review-time payoff-ledger audit when methodology v2 is on."""

    if not is_methodology_v2_enabled():
        return None
    payoffs = list(
        await session.scalars(
            select(PayoffModel).where(PayoffModel.project_id == project.id)
        )
    )
    payoffs.extend(
        _contract_payoffs(
            chapter_contract,
            chapter_number=chapter.chapter_number,
            existing_codes={payoff.payoff_code for payoff in payoffs},
        )
    )
    clues = list(
        await session.scalars(
            select(ClueModel).where(ClueModel.project_id == project.id)
        )
    )
    return run_payoff_ledger_audit(
        payoffs,
        current_chapter=chapter.chapter_number,
        source_clues=clues,
    )


def merge_payoff_ledger_audit_into_chapter_review(
    review_result: ChapterReviewResult,
    audit: PayoffLedgerAudit | None,
    *,
    chapter_number: int,
    language: str | None = None,
) -> ChapterReviewResult:
    """Add payoff-ledger evidence and repair instructions to chapter review."""

    if audit is None or not audit.all_findings:
        return review_result

    findings = [
        ChapterReviewFinding(
            category="payoff_ledger",
            severity=_review_severity(finding),
            message=f"{finding.code}: {finding.detail}",
        )
        for finding in audit.all_findings
    ]
    should_rewrite = any(_finding_requires_rewrite(finding) for finding in audit.all_findings)
    severity_max = _max_severity(
        review_result.severity_max,
        (finding.severity for finding in findings),
    )
    evidence_summary = {
        **review_result.evidence_summary,
        "payoff_ledger_audit": payoff_ledger_audit_to_dict(audit),
    }
    rewrite_prefix = (
        _payoff_ledger_rewrite_instructions(audit, chapter_number, language=language)
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


def payoff_ledger_audit_to_dict(audit: PayoffLedgerAudit) -> dict[str, Any]:
    return {
        "closure_rate": round(audit.closure_rate, 3),
        "due_count": audit.due_count,
        "overdue_count": audit.overdue_count,
        "resolved_current_chapter_count": audit.resolved_current_chapter_count,
        "entries": [
            {
                "payoff_code": entry.payoff_code,
                "target_chapter": entry.target_chapter,
                "actual_chapter": entry.actual_chapter,
                "source_clue_code": entry.source_clue_code,
                "source_planted_chapter": entry.source_planted_chapter,
                "status": entry.payoff_status.value,
                "setup_distance_chapters": entry.setup_distance_chapters,
            }
            for entry in audit.view
        ],
        "findings": [_finding_to_dict(finding) for finding in audit.all_findings],
    }


def _contract_payoffs(
    chapter_contract: ChapterContractRead | None,
    *,
    chapter_number: int,
    existing_codes: set[str],
) -> list[_ContractPayoff]:
    if chapter_contract is None:
        return []
    payoffs: list[_ContractPayoff] = []
    for index, text in enumerate(_clean_strings(chapter_contract.due_payoff_codes), start=1):
        code = _contract_payoff_code(text, chapter_number=chapter_number, index=index)
        if code in existing_codes:
            continue
        payoffs.append(
            _ContractPayoff(
                payoff_code=code,
                label=text,
                description=text,
                target_chapter_number=chapter_number,
                metadata_json={"source": "chapter_contract_due_payoff"},
            )
        )
    return payoffs


def _contract_payoff_code(text: str, *, chapter_number: int, index: int) -> str:
    cleaned = "".join(ch for ch in text.strip() if ch.isalnum() or ch in "-_")
    if 1 <= len(cleaned) <= 64:
        return cleaned
    return f"contract:ch{chapter_number}:payoff:{index}"


def _clean_strings(values: Iterable[Any]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(str(value).strip() for value in values if str(value).strip()))


def _finding_to_dict(finding: AuditFinding) -> dict[str, Any]:
    return {
        "code": finding.code,
        "severity": finding.severity,
        "detail": finding.detail,
        "evidence": finding.evidence,
    }


def _finding_requires_rewrite(finding: AuditFinding) -> bool:
    return finding.code in _REWRITE_CODES


def _review_severity(finding: AuditFinding) -> str:
    if finding.severity == "block" or _finding_requires_rewrite(finding):
        return "major"
    return "warning"


def _max_severity(current: str, incoming: Iterable[str]) -> str:
    rank = {"info": 0, "warning": 1, "major": 2, "critical": 3}
    result = current
    for item in incoming:
        if rank.get(item, 0) > rank.get(result, 0):
            result = item
    return result


def _payoff_ledger_rewrite_instructions(
    audit: PayoffLedgerAudit,
    chapter_number: int,
    *,
    language: str | None,
) -> str:
    codes = [
        finding.code
        for finding in audit.all_findings
        if _finding_requires_rewrite(finding)
    ]
    if is_english_language(language):
        return (
            "[payoff ledger repair]\n"
            f"Fix payoff-ledger deficits in chapter {chapter_number}: {', '.join(codes)}.\n"
            "- Preserve the planned chapter outcome.\n"
            "- If a payoff is due or overdue, visibly cash it, transform it, or "
            "explicitly retire it.\n"
            "- If a payoff lands without enough setup, add a concrete callback to "
            "its earlier setup.\n"
            "- Do not mention payoff-ledger terminology in prose."
        )
    return (
        "【兑现账本修复】\n"
        f"修复第 {chapter_number} 章的兑现账本缺口：{', '.join(codes)}。\n"
        "- 保留既有章节结果，不要重开新支线。\n"
        "- 如果 payoff 到期或超期，正文必须可见地兑现、转化或明确退场。\n"
        "- 如果兑现缺少铺垫距离，补出对早前铺垫的具体回扣，而不是空降结果。\n"
        "- 正文禁止出现 payoff ledger、方法论、兑现账本等创作术语。"
    )


__all__ = [
    "compute_payoff_ledger_audit_for_review",
    "merge_payoff_ledger_audit_into_chapter_review",
    "payoff_ledger_audit_to_dict",
]
