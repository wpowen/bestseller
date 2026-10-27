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
        await session.scalars(select(PayoffModel).where(PayoffModel.project_id == project.id))
    )
    payoffs.extend(
        _contract_payoffs(
            chapter_contract,
            chapter_number=chapter.chapter_number,
            existing_codes={payoff.payoff_code for payoff in payoffs},
        )
    )
    clues = list(await session.scalars(select(ClueModel).where(ClueModel.project_id == project.id)))
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
    chapter_contract: "ChapterContractRead | None" = None,
) -> ChapterReviewResult:
    """Add payoff-ledger evidence and repair instructions to chapter review.

    ``chapter_contract`` is the read schema object produced by
    :func:`_chapter_contract_read`.  When provided, its
    ``payoff_evidence_paths`` (lifted from
    ``methodology_contract.payoff_evidence_paths``) is folded into both the
    audit dict and the editor rewrite prompt so the writer sees the
    concrete scene references the planner declared.
    """

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
    evidence_paths = (
        list(getattr(chapter_contract, "payoff_evidence_paths", None) or [])
        if chapter_contract is not None
        else []
    )
    evidence_summary = {
        **review_result.evidence_summary,
        "payoff_ledger_audit": payoff_ledger_audit_to_dict(audit, evidence_paths=evidence_paths),
    }
    rewrite_prefix = (
        _payoff_ledger_rewrite_instructions(
            audit,
            chapter_number,
            language=language,
            evidence_paths=evidence_paths,
        )
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


def payoff_ledger_audit_to_dict(
    audit: PayoffLedgerAudit,
    *,
    evidence_paths: "list[dict[str, str]] | None" = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
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
                "is_reference": entry.is_reference,
                "is_superseded": entry.is_superseded,
                "is_obligation": entry.is_obligation,
                "min_setup_distance": entry.min_setup_distance,
            }
            for entry in audit.view
        ],
        "findings": [_finding_to_dict(finding) for finding in audit.all_findings],
    }
    if evidence_paths:
        payload["evidence_paths"] = list(evidence_paths)
    return payload


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
    evidence_paths: "list[dict[str, str]] | None" = None,
) -> str:
    codes = [finding.code for finding in audit.all_findings if _finding_requires_rewrite(finding)]
    inactive_codes = {entry.payoff_code for entry in audit.view if not entry.is_obligation}
    evidence_paths = [
        entry for entry in evidence_paths or [] if entry.get("payoff_code") not in inactive_codes
    ]
    distance_requirements = {
        code: distance
        for finding in audit.setup_distance_findings
        for code, distance in finding.evidence.get("required_setup_distances", {}).items()
    }
    setup_instruction = ""
    if distance_requirements:
        limits = ", ".join(
            f"{code}: {distance}" for code, distance in distance_requirements.items()
        )
        setup_instruction = (
            f"- Honor the explicit setup-distance requirements (chapters): {limits}; "
            "verify the source and existing setup instead of inventing earlier events.\n"
            if is_english_language(language)
            else (
                f"- 按显式合同核查铺垫距离(章): {limits}; "
                "核对来源及已有铺垫，不得编造早前事件。\n"  # noqa: RUF001
            )
        )
    evidence_block = ""
    if evidence_paths:
        if is_english_language(language):
            evidence_block = (
                "\n- Preserve these evidence references from the planner (do "
                "not invent new ones; the chapter must visibly cash them):\n"
                + "\n".join(
                    f"  * {entry.get('payoff_code', '?')}: "
                    f"{entry.get('scene_ref') or entry.get('note') or 'unspecified'}"
                    for entry in evidence_paths
                )
                + "\n"
            )
        else:
            evidence_block = (
                "\n- 保留这些来自策划的兑现证据（不要发明新证据；正文必须可见地兑现它们）：\n"
                + "\n".join(
                    f"  * {entry.get('payoff_code', '?')}："
                    f"{entry.get('scene_ref') or entry.get('note') or '未指定'}"
                    for entry in evidence_paths
                )
                + "\n"
            )
    if is_english_language(language):
        return (
            "[payoff ledger repair]\n"
            f"Fix payoff-ledger deficits in chapter {chapter_number}: {', '.join(codes)}.\n"
            "- Preserve the planned chapter outcome.\n"
            "- If a payoff is due or overdue, visibly cash it, transform it, or "
            "explicitly retire it.\n"
            + setup_instruction
            + "- Do not mention payoff-ledger terminology in prose."
            + evidence_block
        )
    return (
        "【兑现清单修复】\n"
        f"修复第 {chapter_number} 章的兑现清单缺口：{', '.join(codes)}。\n"
        "- 保留既有章节结果，不要重开新支线。\n"
        "- 如果 payoff 到期或超期，正文必须可见地兑现、转化或明确退场。\n"
        + setup_instruction
        + "- 正文禁止出现 payoff ledger、方法论、兑现清单等创作术语。"
        + evidence_block
    )


def render_payoff_ledger_planner_contract(*, language: str | None = None) -> str:
    """Return the methodology-v2 planner contract block when enabled.

    The planner LLM is asked to populate two structured fields in the
    chapter outline's ``methodology_contract`` block:

    * ``payoffs_due``: a list of payoff codes this chapter must cash
      (also lifted into ``ChapterContractRead.methodology_declared_payoffs``).
    * ``payoff_evidence_paths``: a list of objects describing the
      concrete scene-level reference that satisfies each payoff.  Each
      item is read by ``_payoff_ledger_rewrite_instructions`` and folded
      into the editor rewrite prompt, and exposed in
      ``payoff_ledger_audit_to_dict(...)['evidence_paths']`` for review
      observability.

    Downstream, the chapter contract read merges ``payoffs_due`` with the
    ``ChapterContractModel.due_payoff_codes`` column (which itself is
    derived from ``PayoffModel.target_chapter_number``).
    """

    if not is_methodology_v2_enabled():
        return ""
    if is_english_language(language):
        return (
            "[Methodology v2 payoff ledger contract]\n"
            "- Treat `methodology_contract.payoffs_due` as a must-cash list for "
            "explicit source-bound commitments due in this chapter, not references "
            "or superseded plans. Do not invent a due obligation when none exists.\n"
            "- Setup distance has no universal minimum; same-chapter setup and "
            "payment, including chapter 1, are valid. Enforce `min_setup_distance` "
            "only when an explicit source-bound obligation contract requires it.\n"
            "- Use `methodology_contract.payoff_evidence_paths` (a JSON list of "
            "objects with `payoff_code` / `scene_ref` / `note` keys) to record "
            "the concrete scene-level reference that satisfies each payoff. "
            "These objects are read by the editor rewrite prompt and shown in "
            "the review audit's evidence_paths; keep them concrete.\n"
            "- No payoff may be declared paid without a visible in-prose callback "
            "that an attentive reader can point to.\n"
            "- If a payoff is overdue, transform or retire it explicitly instead "
            "of leaving it dangling.\n"
            "- Do not silently introduce new payoffs; every new payoff must be "
            "planted with a code that can be tracked in the rolling ledger.\n"
            "- The payoff ledger and hook ledger are siblings: a payoff cashes "
            "an established promise, which may be established in the same chapter."
        )
    return (
        "【方法论 v2 兑现清单合同】\n"
        "- 把 `methodology_contract.payoffs_due` 当成本章必兑现清单，不是装饰性说明。"
        "仅列出有明确来源合同且本章到期的承诺；引用与已被取代的计划不构成到期义务，"
        "没有到期义务时不要制造义务。\n"
        "- setup distance 没有统一最小章距；同章铺垫并兑现（包括首章）合法。"
        "只有显式且绑定来源的义务合同要求 `min_setup_distance` 时才约束间隔。\n"
        "- 用 `methodology_contract.payoff_evidence_paths`（JSON 列表，"
        "每项含 `payoff_code` / `scene_ref` / `note` 字段）记录本章中"
        "具体兑现该 payoff 的场景引用。这些对象会被 editor 重写 prompt "
        "读取并展示在 review audit 的 evidence_paths 中——必须写具体。\n"
        "- 任何 payoff 未在正文出现可见兑现之前不得标为已支付，"
        "需让有心的读者能找到对应回扣。\n"
        "- 如果 payoff 超期，转化或明确退场，不要让它悬空。\n"
        "- 不要悄悄引入新 payoff；每个新 payoff 必须有可追踪的 code "
        "加入滚动清单。\n"
        "- 兑现清单与钩子清单是兄弟：payoff 兑现已建立的承诺，承诺可以在同章建立。"
    )


__all__ = [
    "compute_payoff_ledger_audit_for_review",
    "merge_payoff_ledger_audit_into_chapter_review",
    "payoff_ledger_audit_to_dict",
    "render_payoff_ledger_planner_contract",
]
