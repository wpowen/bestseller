"""Runtime wiring for methodology-v2 hook ledger audits.

This module keeps :mod:`bestseller.services.hook_ledger` infra-free.  It is the
small adapter that reads existing DB clues plus the current chapter contract,
then folds hook-ledger findings into planner/review flows behind the
``BESTSELLER_METHODOLOGY_V2`` feature flag.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from copy import deepcopy
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
from bestseller.services.creation_contract_authority import select_creation_contracts
from bestseller.services.hook_ledger import (
    AuditFinding,
    HookLedgerAudit,
    is_methodology_v2_enabled,
    run_hook_ledger_audit,
)
from bestseller.services.methodology_overlay import resolve_methodology_contract_mode
from bestseller.services.narrative_obligation_migration import has_explicit_due
from bestseller.services.writing_profile import is_english_language

_REWRITE_CODES = {
    "HOOK_ACTIVE_COUNT_TOO_HIGH",
    "HOOK_PER_CHAPTER_NO_PLANT",
    "HOOK_PER_CHAPTER_NO_RESOLVE",
    "HOOK_DUE_UNRESOLVED",
    "HOOK_REQUIRED_PLANT_MISSING",
    "HOOK_REQUIRED_RESOLVE_MISSING",
    "HOOK_NEXT_COMPRESSION_SEED_MISSING",
}


@dataclass(frozen=True)
class _RuntimeHookLedgerAudit(HookLedgerAudit):
    # Only compute has access to source rows; a plain audit cannot grant due authority.
    explicit_due: dict[str, dict[str, Any]] = field(default_factory=dict)


def explicit_hook_contract_for_chapter(
    project: ProjectModel | None,
    *,
    chapter_number: int,
    stage: str = "review",
) -> dict[str, Any]:
    """Select literal chapter requirements without inheriting a book palette."""
    metadata = getattr(project, "metadata_json", None)
    approved = select_creation_contracts(
        metadata if isinstance(metadata, Mapping) else {}, source_bound=False
    )
    hook = approved.get("creation_intent_contract", {}).get("hook_spec", {})
    for key in ("stage_contracts", "stages"):
        stages = hook.get(key)
        contract = stages.get(stage) if isinstance(stages, Mapping) else None
        if not isinstance(contract, Mapping):
            continue
        chapters = contract.get("chapters")
        if isinstance(chapters, Mapping):
            selected = chapters.get(str(chapter_number))
            return dict(selected) if isinstance(selected, Mapping) else {}
        number = contract.get("chapter_number")
        if type(number) is int and number == chapter_number:
            return dict(contract)
        return {}
    return {}


def render_hook_ledger_planner_contract(
    *, language: str | None = None, project: ProjectModel | None = None
) -> str:
    """Return the methodology-v2 planner contract block when enabled."""

    if not is_methodology_v2_enabled() or resolve_methodology_contract_mode(project) != "strict":
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
        "【方法论 v2 钩子清单合同】\n"
        "- 把 `methodology_contract.hooks_to_resolve` 和 "
        "`methodology_contract.hooks_to_plant` 当成本章 hook ledger delta，"
        "不是装饰性说明。\n"
        "- 每章在条件允许时至少消解一个旧钩子，并至少植入一个新钩子。\n"
        "- 滚动清单保持 3-7 个活跃钩子，避免悬念饥饿或过载。\n"
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
    clues = list(await session.scalars(select(ClueModel).where(ClueModel.project_id == project.id)))
    # Planned contract deltas are requirements, never evidence of planted/paid truth.
    audit = run_hook_ledger_audit(clues, current_chapter=chapter.chapter_number)
    explicit_due = {}
    for clue in clues:
        row = {
            "label": clue.label,
            "metadata_json": clue.metadata_json,
        }
        due = clue.expected_payoff_by_chapter_number
        if has_explicit_due(row, due) and str(
            clue.metadata_json["narrative_obligation"]["source"]["project_id"]
        ) == str(project.id):
            explicit_due[clue.clue_code] = {
                "due_chapter": due,
                "narrative_obligation": deepcopy(clue.metadata_json["narrative_obligation"]),
            }
    return _RuntimeHookLedgerAudit(
        view=audit.view,
        active_count=audit.active_count,
        per_chapter_balance=audit.per_chapter_balance,
        max_age=audit.max_age,
        next_compression_seed=audit.next_compression_seed,
        explicit_due=explicit_due,
    )


def merge_hook_ledger_audit_into_chapter_review(
    review_result: ChapterReviewResult,
    audit: HookLedgerAudit | None,
    *,
    chapter_number: int,
    language: str | None = None,
    project: ProjectModel | None = None,
) -> ChapterReviewResult:
    """Add hook-ledger evidence and targeted repair instructions to review."""

    if audit is None:
        return review_result
    mode = resolve_methodology_contract_mode(project)
    literal = explicit_hook_contract_for_chapter(project, chapter_number=chapter_number)
    applicable = _applicable_findings(
        audit, chapter_number=chapter_number, mode=mode, literal=literal
    )

    findings = [
        ChapterReviewFinding(
            category="hook_ledger",
            severity=_review_severity(finding, chapter_number=chapter_number, mode=mode),
            message=f"{finding.code}: {finding.detail}",
        )
        for finding in applicable
    ]
    should_rewrite = any(
        _finding_requires_rewrite(finding, chapter_number=chapter_number, mode=mode)
        for finding in applicable
    )
    severity_max = _max_severity(
        review_result.severity_max,
        (finding.severity for finding in findings),
    )
    evidence_summary = {
        **review_result.evidence_summary,
        "hook_ledger_audit": {
            **hook_ledger_audit_to_dict(audit),
            "mode": mode,
            "explicit_due": getattr(audit, "explicit_due", {}),
            "effective_findings": [_finding_to_dict(finding) for finding in applicable],
        },
    }
    rewrite_prefix = (
        _hook_ledger_rewrite_instructions(applicable, chapter_number, language=language, mode=mode)
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
        "previous_chapter_had_payoff": (audit.next_compression_seed.previous_chapter_had_payoff),
        "current_chapter_planted_new": (audit.next_compression_seed.current_chapter_planted_new),
        "by_type": {hook_type.value: count for hook_type, count in audit.by_type().items()},
        "findings": [_finding_to_dict(finding) for finding in audit.all_findings],
    }


def _clean_strings(values: Iterable[Any]) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(value.strip() for value in values if isinstance(value, str) and value.strip())
    )


def _finding_to_dict(finding: AuditFinding) -> dict[str, Any]:
    return {
        "code": finding.code,
        "severity": finding.severity,
        "detail": finding.detail,
        "evidence": finding.evidence,
    }


def _applicable_findings(
    audit: HookLedgerAudit,
    *,
    chapter_number: int,
    mode: str,
    literal: Mapping[str, Any],
) -> list[AuditFinding]:
    # Generic count/balance/seed heuristics require the full suite opt-in.
    overridden = set()
    if "hooks_to_plant" in literal:
        overridden.update({"HOOK_PER_CHAPTER_NO_PLANT", "HOOK_NEXT_COMPRESSION_SEED_MISSING"})
    if "hooks_to_resolve" in literal:
        overridden.add("HOOK_PER_CHAPTER_NO_RESOLVE")
    if literal.get("hooks_to_plant") == [] and literal.get("hooks_to_resolve") == []:
        overridden.update({"HOOK_ACTIVE_COUNT_TOO_LOW", "HOOK_ACTIVE_COUNT_TOO_HIGH"})
    findings = [
        f
        for f in audit.all_findings
        if mode != "off" and f.code != "HOOK_OVERDUE" and f.code not in overridden
    ]
    explicit_due = getattr(audit, "explicit_due", {})
    due = [
        e.clue_code
        for e in audit.view
        if e.clue_code in explicit_due
        and explicit_due[e.clue_code]["due_chapter"] == e.expected_payoff_chapter
        and e.planted_chapter is not None
        and e.planted_chapter <= chapter_number
        and e.expected_payoff_chapter is not None
        and e.expected_payoff_chapter <= chapter_number
        and not e.is_cancelled
        and not e.is_resolved
    ]
    if due:
        findings.append(
            AuditFinding(
                code="HOOK_DUE_UNRESOLVED",
                severity="warn",
                detail=f"Due planted hooks remain unresolved: {', '.join(due)}",
                evidence={"due_codes": due, "chapter": chapter_number},
            )
        )
    for key, code, observed in (
        (
            "hooks_to_plant",
            "HOOK_REQUIRED_PLANT_MISSING",
            {
                value
                for e in audit.view
                if e.planted_chapter == chapter_number and not e.is_cancelled
                for value in (e.clue_code, e.label)
            },
        ),
        (
            "hooks_to_resolve",
            "HOOK_REQUIRED_RESOLVE_MISSING",
            {
                value
                for e in audit.view
                if e.actual_paid_chapter == chapter_number and not e.is_cancelled
                for value in (e.clue_code, e.label)
            },
        ),
    ):
        values = literal.get(key)
        required = _clean_strings(values) if isinstance(values, (list, tuple)) else ()
        missing = [value for value in required if value not in observed]
        if missing:
            findings.append(
                AuditFinding(
                    code=code,
                    severity="warn",
                    detail=f"Missing explicit hooks: {', '.join(missing)}",
                    evidence={"required_hooks": missing, "chapter": chapter_number},
                )
            )
    return findings


def _finding_requires_rewrite(
    finding: AuditFinding,
    *,
    chapter_number: int,
    mode: str,
) -> bool:
    if mode == "warn":
        return False
    if finding.code == "HOOK_PER_CHAPTER_NO_RESOLVE" and chapter_number <= 1:
        return False
    return finding.code in _REWRITE_CODES


def _review_severity(finding: AuditFinding, *, chapter_number: int, mode: str) -> str:
    if _finding_requires_rewrite(finding, chapter_number=chapter_number, mode=mode):
        return "major"
    return "info" if mode == "warn" else "warning"


def _max_severity(current: str, incoming: Iterable[str]) -> str:
    rank = {"info": 0, "warning": 1, "major": 2, "critical": 3}
    result = current
    for item in incoming:
        if rank.get(item, 0) > rank.get(result, 0):
            result = item
    return result


def _hook_ledger_rewrite_instructions(
    findings: Iterable[AuditFinding],
    chapter_number: int,
    *,
    language: str | None,
    mode: str,
) -> str:
    required = [
        finding
        for finding in findings
        if _finding_requires_rewrite(finding, chapter_number=chapter_number, mode=mode)
    ]
    codes = [finding.code for finding in required]
    details = "\n".join(f"- {finding.code}: {finding.detail}" for finding in required)
    if is_english_language(language):
        return (
            "[hook ledger repair]\n"
            f"Fix hook-ledger deficits in chapter {chapter_number}: {', '.join(codes)}.\n"
            f"{details}\n"
            "- Preserve the existing plot outcome.\n"
            "- Address only the listed findings and named obligations; do not invent hook quotas.\n"
            "- If an old hook is overdue, visibly resolve, transform, or explicitly retire it.\n"
            "- Do not mention hook-ledger terminology in prose."
        )
    return (
        "【钩子清单修复】\n"
        f"修复第 {chapter_number} 章的钩子清单缺口：{', '.join(codes)}。\n"
        f"{details}\n"
        "- 保留既有剧情结果，不要重开新章。\n"
        "- 仅修复所列问题和具名义务，不得额外编造植入或消解配额。\n"
        "- 如果存在超期钩子，必须在正文中解决、转化或明确退场。\n"
        "- 正文禁止出现 hook ledger、方法论、钩子清单等创作术语。"
    )


__all__ = [
    "compute_hook_ledger_audit_for_review",
    "explicit_hook_contract_for_chapter",
    "hook_ledger_audit_to_dict",
    "merge_hook_ledger_audit_into_chapter_review",
    "render_hook_ledger_planner_contract",
]
