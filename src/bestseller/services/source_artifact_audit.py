"""Pre-draft source artifact audit.

This module checks planning/source files before chapter repair or batch
generation. It is intentionally file-based so legacy output folders can be
audited before deeper pipeline integration.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from bestseller.services.quality_levers.detectors import (
    count_cjk_chars,
    count_latin_words,
)


DEFAULT_SOURCE_ARTIFACT_NAMES = (
    "project.md",
    "listing.md",
    "book-listing.md",
    "story-bible.md",
    "story_bible.md",
    "bible.md",
    "rules.md",
    "outline.md",
    "chapter-outline.md",
    "chapter_outline.md",
)
DEFAULT_LEGACY_POLLUTION_TERMS = (
    "玩家",
    "副本",
    "主神",
    "无限流",
    "系统面板",
    "任务奖励",
    "游戏提示",
)
BLOCKING_SEVERITIES = {"critical", "high"}


@dataclass(frozen=True, slots=True)
class SourceArtifactFinding:
    code: str
    severity: str
    message: str
    artifact_path: str
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
            "artifact_path": self.artifact_path,
            "evidence": dict(self.evidence),
        }


@dataclass(frozen=True, slots=True)
class SourceArtifactAuditReport:
    slug: str
    passed: bool
    artifact_count: int
    findings: tuple[SourceArtifactFinding, ...] = ()

    @property
    def blocking_findings(self) -> tuple[SourceArtifactFinding, ...]:
        return tuple(
            finding
            for finding in self.findings
            if finding.severity in BLOCKING_SEVERITIES
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "slug": self.slug,
            "passed": self.passed,
            "artifact_count": self.artifact_count,
            "findings": [finding.to_dict() for finding in self.findings],
            "blocking_findings": [
                finding.to_dict() for finding in self.blocking_findings
            ],
        }


def discover_source_artifacts(
    slug: str,
    *,
    output_dir: Path,
    artifact_names: Sequence[str] = DEFAULT_SOURCE_ARTIFACT_NAMES,
) -> tuple[Path, ...]:
    """Return planning/source artifact files for a legacy output folder."""

    base = output_dir / slug
    if not base.exists():
        return ()
    allowed = {name.lower() for name in artifact_names}
    files: list[Path] = []
    for path in base.rglob("*"):
        if not path.is_file():
            continue
        name = path.name.lower()
        if name.startswith("chapter-"):
            continue
        if name in allowed or any(token in name for token in ("bible", "listing")):
            files.append(path)
    return tuple(sorted(files))


def audit_source_artifacts(
    slug: str,
    *,
    output_dir: Path,
    expected_language: str | None = None,
    expected_platform: str | None = None,
    expected_category: str | None = None,
    forbidden_terms: Sequence[str] = DEFAULT_LEGACY_POLLUTION_TERMS,
) -> SourceArtifactAuditReport:
    artifacts = discover_source_artifacts(slug, output_dir=output_dir)
    findings: list[SourceArtifactFinding] = []
    if not artifacts:
        findings.append(
            SourceArtifactFinding(
                code="SOURCE_ARTIFACTS_MISSING",
                severity="high",
                message="No planning/source artifacts were found before repair.",
                artifact_path=str(output_dir / slug),
            )
        )
        return SourceArtifactAuditReport(
            slug=slug,
            passed=False,
            artifact_count=0,
            findings=tuple(findings),
        )

    for artifact in artifacts:
        text = _read_text(artifact)
        if not text:
            continue
        findings.extend(
            _forbidden_term_findings(
                artifact,
                text,
                forbidden_terms=forbidden_terms,
            )
        )
        language = _infer_language(text)
        if expected_language and not _language_matches(expected_language, language):
            findings.append(
                SourceArtifactFinding(
                    code="SOURCE_LANGUAGE_MISMATCH",
                    severity="critical",
                    message=(
                        f"Expected {expected_language}, but artifact reads as {language}."
                    ),
                    artifact_path=str(artifact),
                    evidence={"expected": expected_language, "actual": language},
                )
            )
        if expected_platform and expected_platform.lower() not in text.lower():
            findings.append(
                SourceArtifactFinding(
                    code="SOURCE_PLATFORM_MISSING",
                    severity="medium",
                    message=f"Expected platform '{expected_platform}' is not visible.",
                    artifact_path=str(artifact),
                    evidence={"expected_platform": expected_platform},
                )
            )
        if expected_category and expected_category.lower() not in text.lower():
            findings.append(
                SourceArtifactFinding(
                    code="SOURCE_CATEGORY_MISSING",
                    severity="medium",
                    message=f"Expected category '{expected_category}' is not visible.",
                    artifact_path=str(artifact),
                    evidence={"expected_category": expected_category},
                )
            )

    blocking = [item for item in findings if item.severity in BLOCKING_SEVERITIES]
    return SourceArtifactAuditReport(
        slug=slug,
        passed=not blocking,
        artifact_count=len(artifacts),
        findings=tuple(findings),
    )


def source_artifact_audit_report_to_dict(
    report: SourceArtifactAuditReport,
) -> dict[str, Any]:
    return report.to_dict()


def _forbidden_term_findings(
    artifact: Path,
    text: str,
    *,
    forbidden_terms: Sequence[str],
) -> list[SourceArtifactFinding]:
    hits = {
        term: text.count(term)
        for term in forbidden_terms
        if term and text.count(term) > 0
    }
    if not hits:
        return []
    return [
        SourceArtifactFinding(
            code="SOURCE_FORBIDDEN_TERM",
            severity="critical",
            message="Source artifact contains deprecated or forbidden planning terms.",
            artifact_path=str(artifact),
            evidence={"term_counts": hits},
        )
    ]


def _infer_language(text: str) -> str:
    cjk_chars = count_cjk_chars(text)
    latin_words = count_latin_words(text)
    if latin_words >= 80 and latin_words > cjk_chars * 2:
        return "en-US"
    return "zh-CN"


def _language_matches(expected: str, actual: str) -> bool:
    expected_family = expected.strip().lower().split("-", 1)[0]
    actual_family = actual.strip().lower().split("-", 1)[0]
    return bool(expected_family and expected_family == actual_family)


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""
