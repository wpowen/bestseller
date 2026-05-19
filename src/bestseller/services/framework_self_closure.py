# ruff: noqa: RUF001
"""Framework-level self-closure audit for all supported novel categories.

The benchmark audit answers "what can the framework currently do?".
This module answers the next operational question: for every canonical
category, is there a closed loop from distilled book-level learnings to
planning, category state engines, quality gates, repair tasks, and final
parity validation?

The report is repo-safe. It stores only category names, abstract capabilities,
missing derived assets, and commands/actions needed to fill gaps.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
import json
from pathlib import Path
from typing import Any

from bestseller.services.autonomous_book_repair import AUTONOMOUS_REPAIR_STRATEGY
from bestseller.services.benchmark_capability_audit import (
    build_sample_quality_parity_gate_definition,
    build_taxonomy_bridge,
)
from bestseller.services.category_hard_engines import (
    get_category_hard_engine_contract,
    load_category_hard_engine_contracts,
    run_category_engine_fixture_benchmark,
)
from bestseller.services.distilled_design_reference import find_distilled_design_aggregate_dir
from bestseller.services.distilled_strategy_compiler import compile_distilled_strategy_card
from bestseller.services.story_design_grammars import get_story_design_grammar


BLOCKING_CODES: frozenset[str] = frozenset(
    {
        "novel_category_missing",
        "review_profile_missing",
        "story_design_grammar_missing",
        "hard_engine_contract_missing",
        "category_fixture_benchmark_failed",
        "repair_loop_missing",
    }
)


@dataclass(frozen=True, slots=True)
class ClosureFinding:
    code: str
    severity: str
    message: str
    action: str
    owner_role: str
    evidence: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
            "action": self.action,
            "owner_role": self.owner_role,
            "evidence": dict(self.evidence),
        }


@dataclass(frozen=True, slots=True)
class CategorySelfClosureCard:
    category_key: str
    status: str
    taxonomy_status: str
    distillation_status: str
    aggregate_key: str | None
    maturity_status: str | None
    maturity_score: float | None
    capabilities: Mapping[str, str]
    distillation_supplement: Mapping[str, Any]
    findings: tuple[ClosureFinding, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "category_key": self.category_key,
            "status": self.status,
            "taxonomy_status": self.taxonomy_status,
            "distillation_status": self.distillation_status,
            "aggregate_key": self.aggregate_key,
            "maturity_status": self.maturity_status,
            "maturity_score": self.maturity_score,
            "capabilities": dict(self.capabilities),
            "distillation_supplement": dict(self.distillation_supplement),
            "findings": [finding.to_dict() for finding in self.findings],
        }


@dataclass(frozen=True, slots=True)
class FrameworkSelfClosureReport:
    generated_at: str
    overall_status: str
    category_cards: tuple[CategorySelfClosureCard, ...]
    backlog: tuple[Mapping[str, Any], ...]
    acceptance_gates: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": 1,
            "generated_at": self.generated_at,
            "overall_status": self.overall_status,
            "summary": {
                "category_count": len(self.category_cards),
                "status_counts": dict(Counter(card.status for card in self.category_cards)),
                "distillation_status_counts": dict(
                    Counter(card.distillation_status for card in self.category_cards)
                ),
            },
            "category_cards": [card.to_dict() for card in self.category_cards],
            "backlog": [dict(item) for item in self.backlog],
            "acceptance_gates": dict(self.acceptance_gates),
        }


def _now_iso() -> str:
    return datetime.now(tz=UTC).replace(microsecond=0).isoformat()


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _text(value: object) -> str:
    return str(value).strip() if value is not None else ""


def _dedupe(values: Sequence[object], *, limit: int) -> list[str]:
    out: list[str] = []
    for value in values:
        text = _text(value)
        if text and text not in out:
            out.append(text)
        if len(out) >= limit:
            break
    return out


def build_framework_self_closure_report(
    *,
    repo_root: Path | None = None,
    categories: Sequence[str] | None = None,
) -> FrameworkSelfClosureReport:
    root = repo_root or _repo_root()
    bridge_rows = build_taxonomy_bridge(categories)
    contract_keys = set(load_category_hard_engine_contracts())
    fixture_categories = [
        row.canonical_category
        for row in bridge_rows
        if row.canonical_category in contract_keys
    ]
    fixture_rows = {
        str(row.get("category_key")): row
        for row in run_category_engine_fixture_benchmark(fixture_categories)
    }
    cards = tuple(
        _build_category_card(
            category=row.canonical_category,
            taxonomy_status=row.bridge_status,
            bridge_gaps=row.gaps,
            fixture_row=fixture_rows.get(row.canonical_category, {}),
            repo_root=root,
        )
        for row in bridge_rows
    )
    backlog = tuple(_build_backlog(cards))
    overall_status = _overall_status(cards)
    return FrameworkSelfClosureReport(
        generated_at=_now_iso(),
        overall_status=overall_status,
        category_cards=cards,
        backlog=backlog,
        acceptance_gates={
            "ready_claim_requires": [
                "taxonomy bridge aligned",
                "category hard-engine good fixture passes and bad fixture blocks",
                "category-specific or explicitly approved generic distilled strategy card",
                "premium book gate category hard engine passed",
                "whole-book quality gate passed",
                "sample quality parity gate passed",
                "autonomous repair loop has no critical/high repair tasks remaining",
            ],
            "sample_quality_parity_gate": build_sample_quality_parity_gate_definition(),
            "repair_strategy": AUTONOMOUS_REPAIR_STRATEGY,
        },
    )


def _build_category_card(
    *,
    category: str,
    taxonomy_status: str,
    bridge_gaps: Sequence[str],
    fixture_row: Mapping[str, Any],
    repo_root: Path,
) -> CategorySelfClosureCard:
    findings: list[ClosureFinding] = []
    for gap in bridge_gaps:
        code = _bridge_gap_code(gap)
        findings.append(
            ClosureFinding(
                code=code,
                severity="critical" if code in BLOCKING_CODES else "high",
                message=gap,
                action=_action_for_bridge_gap(category, gap),
                owner_role=_owner_for_code(code),
            )
        )

    contract = get_category_hard_engine_contract(category)
    if contract is None:
        findings.append(
            ClosureFinding(
                code="hard_engine_contract_missing",
                severity="critical",
                message=f"{category} has no category hard-engine contract.",
                action=f"Add `{category}` to category_hard_engines with state ledgers, gates, and chapter updates.",
                owner_role="框架架构负责人",
            )
        )

    if not (
        fixture_row.get("good_fixture_passed") is True
        and fixture_row.get("bad_fixture_blocked") is True
    ):
        findings.append(
            ClosureFinding(
                code="category_fixture_benchmark_failed",
                severity="critical",
                message=f"{category} category hard-engine fixture benchmark is not passing.",
                action="Fix the good/bad fixture benchmark before using this category in autonomous generation.",
                owner_role="评测与质量负责人",
                evidence=dict(fixture_row),
            )
        )

    aggregate_dir = find_distilled_design_aggregate_dir(
        category_key=category,
        repo_root=repo_root,
    )
    strategy_card = compile_distilled_strategy_card(
        category_key=category,
        repo_root=repo_root,
    )
    aggregate_key = strategy_card.aggregate_key if strategy_card else None
    maturity_score = strategy_card.maturity_score if strategy_card else None
    maturity_status = strategy_card.maturity_status if strategy_card else None
    if aggregate_dir is None:
        distillation_status = "missing"
        findings.append(
            ClosureFinding(
                code="distillation_reference_missing",
                severity="high",
                message=f"{category} has no distilled design reference, not even a generic fallback.",
                action=_distillation_action(category),
                owner_role="结构蒸馏负责人",
            )
        )
    elif aggregate_dir.name == category:
        distillation_status = "category_specific"
    else:
        distillation_status = "generic_fallback"
        findings.append(
            ClosureFinding(
                code="category_specific_distillation_missing",
                severity="medium",
                message=(
                    f"{category} currently consumes `{aggregate_dir.name}` instead of a "
                    "category-specific distilled aggregate."
                ),
                action=_distillation_action(category),
                owner_role="结构蒸馏负责人",
                evidence={"fallback_aggregate": aggregate_dir.name},
            )
        )

    if strategy_card is not None and strategy_card.maturity_score < 0.3:
        findings.append(
            ClosureFinding(
                code="distillation_maturity_low",
                severity="medium",
                message=f"{category} distilled strategy maturity is low.",
                action=_distillation_action(category),
                owner_role="结构蒸馏负责人",
                evidence={
                    "maturity_score": strategy_card.maturity_score,
                    "maturity_status": strategy_card.maturity_status,
                },
            )
        )

    capabilities = _capabilities_for(
        taxonomy_status=taxonomy_status,
        contract_present=contract is not None,
        fixture_ok=not any(f.code == "category_fixture_benchmark_failed" for f in findings),
        distillation_status=distillation_status,
        strategy_card_present=strategy_card is not None,
    )
    supplement = _build_distillation_supplement(
        category=category,
        distillation_status=distillation_status,
        aggregate_key=aggregate_key,
        contract=contract,
        strategy_card=strategy_card,
    )
    status = _category_status(findings, capabilities)
    return CategorySelfClosureCard(
        category_key=category,
        status=status,
        taxonomy_status=taxonomy_status,
        distillation_status=distillation_status,
        aggregate_key=aggregate_key,
        maturity_status=maturity_status,
        maturity_score=maturity_score,
        capabilities=capabilities,
        distillation_supplement=supplement,
        findings=tuple(findings),
    )


def _build_distillation_supplement(
    *,
    category: str,
    distillation_status: str,
    aggregate_key: str | None,
    contract: Any,
    strategy_card: Any,
) -> dict[str, Any]:
    grammar = get_story_design_grammar(category)
    strategy_states = (
        list(strategy_card.required_state_variables)
        if strategy_card is not None
        else []
    )
    strategy_vectors = (
        list(strategy_card.required_change_vectors)
        if strategy_card is not None
        else []
    )
    strategy_rewards = list(strategy_card.reader_reward_mix) if strategy_card is not None else []
    selected_mechanisms = (
        [
            {
                "mechanism_id": mechanism.mechanism_id,
                "design_role": mechanism.design_role,
                "binding": mechanism.required_project_specific_binding,
            }
            for mechanism in strategy_card.selected_mechanisms[:6]
        ]
        if strategy_card is not None
        else []
    )
    state_ledgers = list(contract.state_ledger_keys) if contract is not None else []
    hard_gates = list(contract.hard_gate_keys) if contract is not None else []
    chapter_updates = list(contract.chapter_update_keys) if contract is not None else []
    grammar_states = list(grammar.state_variables) if grammar is not None else []
    grammar_vectors = list(grammar.chapter_change_vectors) if grammar is not None else []
    grammar_rewards = list(grammar.reader_rewards) if grammar is not None else []
    missing_assets = []
    if distillation_status != "category_specific":
        missing_assets.extend(
            [
                "category_specific_aggregate_manifest",
                "category_specific_mechanism_registry",
                "category_specific_book_design_registry",
                "category_specific_anti_copy_rules",
            ]
        )
    return {
        "source": (
            "category_specific_distillation"
            if distillation_status == "category_specific"
            else "generic_book_distillation_plus_category_contract"
        ),
        "aggregate_key": aggregate_key,
        "strategy_signal_counts": {
            "required_state_variables": len(strategy_states),
            "required_change_vectors": len(strategy_vectors),
            "reader_reward_mix": len(strategy_rewards),
            "selected_mechanisms": len(selected_mechanisms),
        },
        "planner_must_bind": _dedupe(
            [
                *grammar_states,
                *grammar_vectors,
                *grammar_rewards,
                *state_ledgers,
            ],
            limit=32,
        ),
        "category_state_ledgers": state_ledgers,
        "category_hard_gates": hard_gates,
        "chapter_update_channels": chapter_updates,
        "selected_mechanism_bindings": selected_mechanisms,
        "repair_loop_inputs": _dedupe(
            [
                *hard_gates,
                *chapter_updates,
                "whole_book_quality_gate",
                "premium_book_gate",
                "sample_quality_parity_gate",
                AUTONOMOUS_REPAIR_STRATEGY,
            ],
            limit=24,
        ),
        "missing_category_distillation_assets": missing_assets,
        "acceptance_checks": [
            "planning artifacts include planner_must_bind items as project-specific state, not copied terms",
            "premium_state_snapshot contains category_state_ledgers before long-form continuation",
            "chapter_state_updates emit chapter_update_channels after each generated or repaired chapter",
            "category hard gates pass before sample parity can pass",
            "anti-copy boundaries from the strategy card are enforced during planning and repair",
        ],
    }


def _capabilities_for(
    *,
    taxonomy_status: str,
    contract_present: bool,
    fixture_ok: bool,
    distillation_status: str,
    strategy_card_present: bool,
) -> dict[str, str]:
    return {
        "taxonomy_bridge": "ready" if taxonomy_status == "aligned" else "blocked",
        "book_level_distillation": (
            "ready"
            if distillation_status == "category_specific"
            else "fallback" if strategy_card_present else "blocked"
        ),
        "distilled_strategy_consumption": "ready" if strategy_card_present else "blocked",
        "category_state_engine": "ready" if contract_present and fixture_ok else "blocked",
        "premium_quality_gate": "ready" if contract_present and fixture_ok else "blocked",
        "autonomous_repair_loop": "ready",
        "sample_parity_acceptance": "ready",
    }


def _category_status(
    findings: Sequence[ClosureFinding],
    capabilities: Mapping[str, str],
) -> str:
    if any(finding.code in BLOCKING_CODES for finding in findings):
        return "blocked"
    if any(value == "blocked" for value in capabilities.values()):
        return "blocked"
    if findings or any(value == "fallback" for value in capabilities.values()):
        return "repairable"
    return "closed"


def _overall_status(cards: Sequence[CategorySelfClosureCard]) -> str:
    if any(card.status == "blocked" for card in cards):
        return "blocked"
    if any(card.status == "repairable" for card in cards):
        return "repairable"
    return "closed"


def _bridge_gap_code(gap: str) -> str:
    if "novel category" in gap:
        return "novel_category_missing"
    if "review profile" in gap:
        return "review_profile_missing"
    if "story design grammar" in gap:
        return "story_design_grammar_missing"
    if "distillation bucket" in gap or "bucket" in gap:
        return "distillation_bucket_missing"
    if "prompt pack" in gap:
        return "prompt_pack_missing"
    return "taxonomy_bridge_gap"


def _action_for_bridge_gap(category: str, gap: str) -> str:
    if "novel category" in gap:
        return f"Create `config/novel_categories/{category}.yaml` and resolver tests."
    if "review profile" in gap:
        return f"Add `{category}` to `genre_review_profiles` with weights, signals, rubric, and prompts."
    if "story design grammar" in gap:
        return f"Create `config/story_design_grammars/{category}.yaml`."
    if "distillation bucket" in gap or "bucket" in gap:
        return f"Add `{category}` to the distillation genre bucket allowlist and taxonomy bridge."
    if "prompt pack" in gap:
        return f"Map `{category}` to at least one prompt pack or an approved category grammar fallback."
    return "Resolve the taxonomy bridge gap before marking the category ready."


def _distillation_action(category: str) -> str:
    return (
        "Run safe anonymous distillation for this category and aggregate to "
        f"`data/distillation/aggregates/{category}`; then rerun framework self-closure audit."
    )


def _owner_for_code(code: str) -> str:
    if "distillation" in code:
        return "结构蒸馏负责人"
    if code in {"novel_category_missing", "review_profile_missing", "story_design_grammar_missing"}:
        return "类型体系负责人"
    if "fixture" in code:
        return "评测与质量负责人"
    return "框架架构负责人"


def _build_backlog(cards: Sequence[CategorySelfClosureCard]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    serial = 1
    for card in cards:
        for finding in card.findings:
            rows.append(
                {
                    "id": f"SELF-CLOSURE-{serial:03d}",
                    "priority": _priority_for_finding(finding),
                    "category_key": card.category_key,
                    "code": finding.code,
                    "owner_role": finding.owner_role,
                    "action": finding.action,
                    "acceptance": _acceptance_for_finding(card.category_key, finding),
                }
            )
            serial += 1
    rows.sort(key=lambda row: (str(row["priority"]), str(row["category_key"]), str(row["id"])))
    return rows


def _priority_for_finding(finding: ClosureFinding) -> str:
    if finding.code in BLOCKING_CODES or finding.severity == "critical":
        return "P0"
    if finding.severity == "high":
        return "P1"
    return "P2"


def _acceptance_for_finding(category: str, finding: ClosureFinding) -> str:
    if finding.code == "category_specific_distillation_missing":
        return (
            f"`compile_distilled_strategy_card(category_key='{category}')` resolves "
            f"aggregate_key `{category}` with anti-copy boundaries and state variables."
        )
    if finding.code == "distillation_maturity_low":
        return "Maturity score is >= 0.30 and no fallback placeholders leak into planning artifacts."
    if finding.code in BLOCKING_CODES:
        return "Self-closure audit reports this category as repairable or closed, not blocked."
    return "Finding no longer appears in framework self-closure report."


def render_framework_self_closure_markdown(report: FrameworkSelfClosureReport) -> str:
    payload = report.to_dict()
    summary = payload["summary"]
    lines = [
        "# 小说框架全类型自闭环能力报告",
        "",
        f"Generated at: `{report.generated_at}`",
        f"Overall status: `{report.overall_status}`",
        "",
        "## Summary",
        "",
        f"- Categories: `{summary['category_count']}`",
        f"- Status counts: `{json.dumps(summary['status_counts'], ensure_ascii=False)}`",
        f"- Distillation status counts: `{json.dumps(summary['distillation_status_counts'], ensure_ascii=False)}`",
        "",
        "## Closed Loop",
        "",
        "```mermaid",
        "flowchart LR",
        '  Distill["匿名书籍级蒸馏"] --> Strategy["DistilledStrategyCard"]',
        '  Strategy --> Planning["规划/卷纲/章纲"]',
        '  Planning --> Draft["章节生成/改写"]',
        '  Draft --> Gates["质量门 + 类别硬引擎"]',
        '  Gates --> Repair["自动修补任务"]',
        '  Repair --> Draft',
        '  Gates --> Parity["样本同标验收"]',
        "```",
        "",
        "## Category Cards",
        "",
        "| Category | Status | Taxonomy | Distillation | Aggregate | Maturity | Blocking/Fix Count |",
        "| --- | --- | --- | --- | --- | --- | ---: |",
    ]
    for card in report.category_cards:
        lines.append(
            "| {category} | {status} | {taxonomy} | {distillation} | {aggregate} | {maturity} | {count} |".format(
                category=card.category_key,
                status=card.status,
                taxonomy=card.taxonomy_status,
                distillation=card.distillation_status,
                aggregate=card.aggregate_key or "",
                maturity=(
                    f"{card.maturity_status}/{card.maturity_score:.2f}"
                    if card.maturity_score is not None and card.maturity_status
                    else ""
                ),
                count=len(card.findings),
            )
        )
    lines.extend(["", "## Backlog", ""])
    if report.backlog:
        lines.extend(
            [
                "| ID | P | Category | Code | Owner | Action | Acceptance |",
                "| --- | --- | --- | --- | --- | --- | --- |",
            ]
        )
        for item in report.backlog:
            lines.append(
                "| {id} | {priority} | {category} | {code} | {owner} | {action} | {acceptance} |".format(
                    id=item.get("id", ""),
                    priority=item.get("priority", ""),
                    category=item.get("category_key", ""),
                    code=item.get("code", ""),
                    owner=item.get("owner_role", ""),
                    action=item.get("action", ""),
                    acceptance=item.get("acceptance", ""),
                )
            )
    else:
        lines.append("No backlog items.")
    return "\n".join(lines) + "\n"


def write_framework_self_closure_artifacts(
    report: FrameworkSelfClosureReport,
    *,
    output_dir: Path,
    markdown_path: Path | None = None,
) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "framework_self_closure_report.json"
    md_path = markdown_path or output_dir / "framework_self_closure_report.md"
    json_path.write_text(
        json.dumps(report.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(render_framework_self_closure_markdown(report), encoding="utf-8")
    return json_path, md_path


__all__ = [
    "CategorySelfClosureCard",
    "ClosureFinding",
    "FrameworkSelfClosureReport",
    "build_framework_self_closure_report",
    "render_framework_self_closure_markdown",
    "write_framework_self_closure_artifacts",
]
