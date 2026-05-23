from __future__ import annotations

# ruff: noqa: RUF001
from collections.abc import Mapping, Sequence
from pathlib import Path
import re
from typing import Any

from bestseller.domain.gate_verdict import GateFinding, GateVerdict
from bestseller.services.cross_kernel_consistency_gate import evaluate_cross_kernel_consistency
from bestseller.services.duplicate_passage_gate import evaluate_duplicate_passages
from bestseller.services.forbidden_terms_drift_gate import evaluate_forbidden_terms_drift
from bestseller.services.identity_freezer_gate import evaluate_identity_freezer
from bestseller.services.kernel_composer import KernelComposer
from bestseller.services.kernel_integration_validator import validate_kernel_file_integration
from bestseller.services.paragraph_coherence_gate import evaluate_paragraph_coherence
from bestseller.services.prewrite_contract_gate import evaluate_prewrite_contract_coverage
from bestseller.services.ranking_readiness import RankingReadinessFinding
from bestseller.services.voice_profile_coverage_gate import evaluate_voice_profile_coverage

_PROJECT_VOICE_PROFILE_ROLES = (
    "protagonist",
    "deuteragonist",
    "antagonist",
    "ally",
    "mentor",
    "rival",
    "confidant",
    "family",
    "romantic",
    "love_interest",
)


def evaluate_output_systemic_readiness(
    package_dir: str | Path,
    *,
    target_chapters: int | None = None,
    identity_registry: Sequence[Mapping[str, Any]] | None = None,
    identity_registry_locked: bool = False,
) -> tuple[RankingReadinessFinding, ...]:
    """Run system-level package gates and return ranking-readiness findings.

    Commercial package gates can pass on the currently written text while the
    surrounding production contract is still incomplete. This runner surfaces
    those cross-cutting failures at the project readiness layer.
    """

    root = Path(package_dir)
    story_bible_dir = root / "story-bible"
    chapter_texts = _load_chapter_texts(root)
    findings: list[RankingReadinessFinding] = []

    _append_verdict(findings, validate_kernel_file_integration(story_bible_dir))

    if chapter_texts:
        _append_verdict(
            findings,
            _evaluate_prewrite_contracts(
                story_bible_dir=story_bible_dir,
                chapter_numbers=tuple(sorted(chapter_texts)),
            ),
        )
        _append_verdict(
            findings,
            evaluate_forbidden_terms_drift(
                chapter_texts,
                guardrails_path=story_bible_dir / "canon-guardrails.json",
            ),
        )
        _append_verdict(findings, evaluate_duplicate_passages(chapter_texts))
        _append_verdict(findings, evaluate_paragraph_coherence(chapter_texts))

    if story_bible_dir.exists():
        try:
            kernels = KernelComposer(story_bible_dir).load_for_chapter()
            _append_verdict(
                findings,
                evaluate_cross_kernel_consistency(kernels, total_chapters=target_chapters),
            )
        except Exception as exc:
            _append_verdict(
                findings,
                GateVerdict(
                    gate_name="cross_kernel_consistency",
                    verdict="error",
                    coverage=0.0,
                    findings=(
                        GateFinding(
                            code="kernel_consistency_error",
                            severity="critical",
                            message=f"kernel consistency evaluation failed: {exc}",
                            repair_action="repair persisted kernel JSON before ranking readiness",
                        ),
                    ),
                ),
            )

    if identity_registry is not None:
        freezer_registry = (
            _mark_identity_registry_locked(identity_registry)
            if identity_registry_locked
            else identity_registry
        )
        _append_verdict(findings, evaluate_identity_freezer(freezer_registry))
        _append_verdict(
            findings,
            evaluate_voice_profile_coverage(
                identity_registry,
                required_roles=_PROJECT_VOICE_PROFILE_ROLES,
            ),
        )

    return tuple(findings)


def _append_verdict(
    findings: list[RankingReadinessFinding],
    verdict: GateVerdict,
) -> None:
    if verdict.passed:
        return
    severity = "critical" if verdict.critical_count else "high"
    if verdict.verdict == "warn_only":
        severity = "medium"
    code_counts: dict[str, int] = {}
    for finding in verdict.findings:
        code_counts[finding.code] = code_counts.get(finding.code, 0) + 1
    findings.append(
        RankingReadinessFinding(
            code=verdict.gate_name,
            severity=severity,  # type: ignore[arg-type]
            scope=f"systemic_gate.{verdict.gate_name}",
            message=(
                f"{verdict.gate_name} returned {verdict.verdict} "
                f"with {len(verdict.findings)} finding(s)."
            ),
            suggestion=_systemic_gate_suggestion(verdict.gate_name),
            evidence={
                "verdict": verdict.verdict,
                "coverage": verdict.coverage,
                "critical_count": verdict.critical_count,
                "finding_count": len(verdict.findings),
                "code_counts": code_counts,
                "metrics": dict(verdict.metrics),
                "sample_findings": [
                    {
                        "code": finding.code,
                        "severity": finding.severity,
                        "message": finding.message,
                        "path": finding.path,
                    }
                    for finding in verdict.findings[:10]
                ],
            },
        )
    )


def _evaluate_prewrite_contracts(
    *,
    story_bible_dir: Path,
    chapter_numbers: tuple[int, ...],
) -> GateVerdict:
    all_findings: list[GateFinding] = []
    scan_numbers = (*chapter_numbers, chapter_numbers[-1] + 1)
    for chapter_no in scan_numbers:
        report = evaluate_prewrite_contract_coverage(
            chapter_no=chapter_no,
            story_bible_dir=story_bible_dir,
        )
        all_findings.extend(report.findings)
    return GateVerdict(
        gate_name="prewrite_contract_coverage",
        verdict="blocked" if all_findings else "pass",
        coverage=0.0 if all_findings else 1.0,
        findings=tuple(all_findings),
        metrics={"chapter_count_scanned": len(scan_numbers)},
    )


def _load_chapter_texts(root: Path) -> dict[int, str]:
    chapter_texts: dict[int, str] = {}
    for path in sorted(root.glob("chapter-*.md")):
        match = re.search(r"(\d+)", path.stem)
        if match is None:
            continue
        chapter_texts[int(match.group(1))] = path.read_text(encoding="utf-8")
    return chapter_texts


def _mark_identity_registry_locked(
    identity_registry: Sequence[Mapping[str, Any]],
) -> tuple[Mapping[str, Any], ...]:
    return tuple({**dict(item), "locked": True} for item in identity_registry)


def _systemic_gate_suggestion(gate_name: str) -> str:
    suggestions = {
        "kernel_file_integration": "补齐 durable kernel 内容后再做项目评级。",
        "prewrite_contract_coverage": "补齐逐章 prewrite_anchor，至少覆盖已写章节和下一章。",
        "forbidden_terms_drift_gate": "替换或解释禁用漂移词，再重跑禁词扫描。",
        "duplicate_passage_gate": "删除或重写重复段落，避免拼接/双重渲染残留。",
        "paragraph_coherence_gate": "优先修复高密度章节的悬浮主语和过薄段落。",
        "cross_kernel_consistency": "对齐 kernel 长线 payoff、地理和章节规划范围。",
        "identity_freezer_gate": "冻结正典身份、别名、角色定位和状态。",
        "voice_profile_coverage": "补齐主要命名角色 voice profile 或 voice_dna。",
    }
    return suggestions.get(gate_name, "Resolve this systemic gate before ranking promotion.")
