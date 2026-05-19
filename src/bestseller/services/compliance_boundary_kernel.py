"""Project-local compliance boundary contracts.

This module provides a configurable risk boundary for public-emotion driven
planning. It is a writing-system safety aid, not legal advice or a replacement
for platform review.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator


COMPLIANCE_BOUNDARY_KERNEL_VERSION = 1


def _config_path() -> Path:
    return Path(__file__).resolve().parents[3] / "config" / "compliance_policy_packs.yaml"


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, list):
        return "；".join(item for item in (_text(item) for item in value) if item)
    if isinstance(value, dict):
        parts = [item for item in (_text(v) for v in value.values()) if item]
        return "；".join(parts)
    return str(value).strip()


def _text_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [part.strip() for part in value.replace("，", ",").split(",") if part.strip()]
    if isinstance(value, (list, tuple, set)):
        return [text for text in (_text(item) for item in value) if text]
    text = _text(value)
    return [text] if text else []


class ComplianceBoundaryKernel(BaseModel, frozen=True):
    """Project-scoped compliance and translation boundary."""

    model_config = ConfigDict(extra="ignore")

    version: int = COMPLIANCE_BOUNDARY_KERNEL_VERSION
    policy_pack_key: str = "cn-mainland-general"
    jurisdiction: str = "CN-mainland"
    platform: str = "general"
    risk_level: str = "low"
    allowed_translations: list[str] = Field(default_factory=list)
    forbidden_translations: list[str] = Field(default_factory=list)
    mitigation_rules: list[str] = Field(default_factory=list)
    title_risk_terms: list[str] = Field(default_factory=list)
    story_risk_terms: list[str] = Field(default_factory=list)
    compliance_notes: str = ""

    @model_validator(mode="before")
    @classmethod
    def _normalize_aliases(cls, value: Any) -> Any:
        data = _mapping(value)
        if not data:
            return value
        for key in (
            "allowed_translations",
            "forbidden_translations",
            "mitigation_rules",
            "title_risk_terms",
            "story_risk_terms",
        ):
            data[key] = _text_list(data.get(key))
        return data


@dataclass(frozen=True)
class ComplianceBoundaryIssue:
    code: str
    severity: str
    path: str
    message: str
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ComplianceBoundaryGateReport:
    present: bool
    valid: bool
    issues: tuple[ComplianceBoundaryIssue, ...] = ()
    policy_pack_key: str = ""
    risk_level: str = ""
    issue_codes: tuple[str, ...] = ()

    @property
    def passed(self) -> bool:
        return self.valid and not any(
            issue.severity in {"critical", "high"} for issue in self.issues
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "present": self.present,
            "valid": self.valid,
            "passed": self.passed,
            "policy_pack_key": self.policy_pack_key,
            "risk_level": self.risk_level,
            "issue_codes": list(self.issue_codes),
            "issues": [
                {
                    "code": issue.code,
                    "severity": issue.severity,
                    "path": issue.path,
                    "message": issue.message,
                    "evidence": dict(issue.evidence),
                }
                for issue in self.issues
            ],
        }


@dataclass(frozen=True)
class ComplianceTextRisk:
    code: str
    severity: str
    term: str
    text: str
    category: str


@lru_cache(maxsize=1)
def load_compliance_policy_packs() -> dict[str, Any]:
    path = _config_path()
    if not path.exists():
        return {}
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return raw if isinstance(raw, dict) else {}


def resolve_compliance_policy_pack(policy_pack_key: str | None) -> dict[str, Any]:
    registry = load_compliance_policy_packs()
    packs = registry.get("policy_packs") if isinstance(registry.get("policy_packs"), dict) else {}
    key = _text(policy_pack_key) or str(registry.get("default_policy_pack") or "")
    pack = packs.get(key) if isinstance(packs, dict) else None
    if isinstance(pack, dict):
        return dict(pack)
    fallback_key = str(registry.get("default_policy_pack") or "cn-mainland-general")
    fallback = packs.get(fallback_key) if isinstance(packs, dict) else None
    return dict(fallback) if isinstance(fallback, dict) else {}


def compliance_boundary_kernel_from_dict(data: dict[str, Any]) -> ComplianceBoundaryKernel:
    return ComplianceBoundaryKernel.model_validate(data)


def compliance_boundary_kernel_to_dict(kernel: ComplianceBoundaryKernel) -> dict[str, Any]:
    return kernel.model_dump(mode="json")


def evaluate_compliance_boundary_kernel(
    kernel: ComplianceBoundaryKernel | dict[str, Any] | None,
    *,
    candidate_texts: list[str] | tuple[str, ...] = (),
) -> ComplianceBoundaryGateReport:
    if not kernel:
        issues: list[ComplianceBoundaryIssue] = [
            ComplianceBoundaryIssue(
                code="COMPLIANCE_BOUNDARY_KERNEL_MISSING",
                severity="warning",
                path="compliance_boundary_kernel",
                message=(
                    "Missing project-local compliance boundary kernel; planning can proceed, "
                    "but public-emotion translation has no explicit policy pack."
                ),
            )
        ]
        for risk in scan_compliance_texts(candidate_texts, None):
            issues.append(
                ComplianceBoundaryIssue(
                    code=risk.code,
                    severity=risk.severity,
                    path="candidate_texts",
                    message=(
                        "Candidate text hits a configured compliance risk term "
                        "under the default policy pack."
                    ),
                    evidence={
                        "term": risk.term,
                        "category": risk.category,
                        "text": risk.text,
                    },
                )
            )
        return ComplianceBoundaryGateReport(
            present=False,
            valid=False,
            issues=tuple(issues),
            policy_pack_key=_text(
                load_compliance_policy_packs().get("default_policy_pack")
            ),
            issue_codes=tuple(issue.code for issue in issues),
        )
    try:
        parsed = (
            compliance_boundary_kernel_from_dict(dict(kernel))
            if isinstance(kernel, dict)
            else kernel
        )
    except ValidationError as exc:
        issue = ComplianceBoundaryIssue(
            code="COMPLIANCE_BOUNDARY_KERNEL_INVALID",
            severity="high",
            path="compliance_boundary_kernel",
            message="ComplianceBoundaryKernel is present but fails schema validation.",
            evidence={"validation_error": str(exc)},
        )
        return ComplianceBoundaryGateReport(
            present=True,
            valid=False,
            issues=(issue,),
            issue_codes=(issue.code,),
        )

    issues: list[ComplianceBoundaryIssue] = []
    if parsed.risk_level.lower() in {"high", "critical", "blocked"}:
        severity = "critical" if parsed.risk_level.lower() in {"critical", "blocked"} else "high"
        issues.append(
            ComplianceBoundaryIssue(
                code="COMPLIANCE_BOUNDARY_HIGH_RISK",
                severity=severity,
                path="compliance_boundary_kernel.risk_level",
                message="Project compliance boundary is marked high risk.",
                evidence={"risk_level": parsed.risk_level},
            )
        )
    if not parsed.allowed_translations:
        issues.append(
            ComplianceBoundaryIssue(
                code="COMPLIANCE_ALLOWED_TRANSLATIONS_MISSING",
                severity="warning",
                path="compliance_boundary_kernel.allowed_translations",
                message="No safe fictional translation rules are defined.",
            )
        )
    if not parsed.forbidden_translations:
        issues.append(
            ComplianceBoundaryIssue(
                code="COMPLIANCE_FORBIDDEN_TRANSLATIONS_MISSING",
                severity="warning",
                path="compliance_boundary_kernel.forbidden_translations",
                message="No forbidden translation boundaries are defined.",
            )
        )
    for risk in scan_compliance_texts(candidate_texts, parsed):
        issues.append(
            ComplianceBoundaryIssue(
                code=risk.code,
                severity=risk.severity,
                path="candidate_texts",
                message="Candidate text hits a configured compliance risk term.",
                evidence={"term": risk.term, "category": risk.category, "text": risk.text},
            )
        )

    codes = tuple(issue.code for issue in issues)
    return ComplianceBoundaryGateReport(
        present=True,
        valid=True,
        issues=tuple(issues),
        policy_pack_key=parsed.policy_pack_key,
        risk_level=parsed.risk_level,
        issue_codes=codes,
    )


def scan_compliance_texts(
    texts: list[str] | tuple[str, ...],
    kernel: ComplianceBoundaryKernel | dict[str, Any] | None = None,
) -> list[ComplianceTextRisk]:
    parsed = _coerce_kernel(kernel)
    pack = resolve_compliance_policy_pack(parsed.policy_pack_key if parsed else None)
    categories = pack.get("risk_terms") if isinstance(pack.get("risk_terms"), dict) else {}
    extra_terms = {
        "project_title_risk_terms": {
            "severity": "high",
            "terms": parsed.title_risk_terms if parsed else [],
        },
        "project_story_risk_terms": {
            "severity": "high",
            "terms": parsed.story_risk_terms if parsed else [],
        },
    }
    risks: list[ComplianceTextRisk] = []
    for raw_text in texts:
        text = _text(raw_text)
        if not text:
            continue
        for category, raw_spec in {**categories, **extra_terms}.items():
            spec = raw_spec if isinstance(raw_spec, dict) else {}
            severity = _text(spec.get("severity")) or "warning"
            code = _text(spec.get("code")) or f"COMPLIANCE_TEXT_RISK_{category.upper()}"
            for term in _text_list(spec.get("terms")):
                if term and term in text:
                    risks.append(
                        ComplianceTextRisk(
                            code=code,
                            severity=severity,
                            term=term,
                            text=text,
                            category=category,
                        )
                    )
    return risks


def _coerce_kernel(
    kernel: ComplianceBoundaryKernel | dict[str, Any] | None,
) -> ComplianceBoundaryKernel | None:
    if kernel is None:
        return None
    if isinstance(kernel, ComplianceBoundaryKernel):
        return kernel
    try:
        return compliance_boundary_kernel_from_dict(dict(kernel))
    except (TypeError, ValidationError):
        return None


def render_compliance_boundary_prompt_block(
    kernel: ComplianceBoundaryKernel | dict[str, Any] | None,
    *,
    language: str = "zh-CN",
) -> str:
    parsed = _coerce_kernel(kernel)
    if parsed is None:
        return ""
    is_en = language.lower().startswith("en")
    lines = (
        ["[compliance_boundary | project-local safety translation]"]
        if is_en
        else ["【compliance_boundary · 本书合规转译边界】"]
    )
    lines.append(
        (
            f"- Policy pack: {parsed.policy_pack_key}; jurisdiction={parsed.jurisdiction}; "
            f"platform={parsed.platform}; risk={parsed.risk_level}"
        )
        if is_en
        else (
            f"- 策略包: {parsed.policy_pack_key}; 地区={parsed.jurisdiction}; "
            f"平台={parsed.platform}; 风险={parsed.risk_level}"
        )
    )
    if parsed.allowed_translations:
        label = "Allowed translations" if is_en else "允许转译"
        lines.append(f"- {label}: {'; '.join(parsed.allowed_translations[:5])}")
    if parsed.forbidden_translations:
        label = "Forbidden translations" if is_en else "禁止转译"
        lines.append(f"- {label}: {'; '.join(parsed.forbidden_translations[:5])}")
    if parsed.mitigation_rules:
        label = "Mitigations" if is_en else "降风险写法"
        lines.append(f"- {label}: {'; '.join(parsed.mitigation_rules[:5])}")
    return "\n".join(lines)


def build_compliance_boundary_kernel_seed(
    *,
    platform: str | None = None,
    policy_pack_key: str = "cn-mainland-general",
) -> dict[str, Any]:
    pack = resolve_compliance_policy_pack(policy_pack_key)
    return {
        "version": COMPLIANCE_BOUNDARY_KERNEL_VERSION,
        "policy_pack_key": policy_pack_key,
        "jurisdiction": _text(pack.get("jurisdiction")) or "CN-mainland",
        "platform": _text(platform) or _text(pack.get("platform")) or "general",
        "risk_level": "low",
        "allowed_translations": _text_list(pack.get("default_allowed_translations")),
        "forbidden_translations": _text_list(pack.get("default_forbidden_translations")),
        "mitigation_rules": _text_list(pack.get("default_mitigation_rules")),
        "title_risk_terms": [],
        "story_risk_terms": [],
        "compliance_notes": "Policy pack is a writing safety aid and does not replace legal/platform review.",
    }


__all__ = [
    "COMPLIANCE_BOUNDARY_KERNEL_VERSION",
    "ComplianceBoundaryGateReport",
    "ComplianceBoundaryIssue",
    "ComplianceBoundaryKernel",
    "ComplianceTextRisk",
    "build_compliance_boundary_kernel_seed",
    "compliance_boundary_kernel_from_dict",
    "compliance_boundary_kernel_to_dict",
    "evaluate_compliance_boundary_kernel",
    "load_compliance_policy_packs",
    "render_compliance_boundary_prompt_block",
    "resolve_compliance_policy_pack",
    "scan_compliance_texts",
]
