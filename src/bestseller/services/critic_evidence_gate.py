"""Require critic outputs to cite body evidence (anti empty-review)."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Final

CRITIC_MISSING_BODY_EVIDENCE: Final[str] = "CRITIC_MISSING_BODY_EVIDENCE"
CRITIC_EMPTY_REVIEW: Final[str] = "CRITIC_EMPTY_REVIEW"

_EVIDENCE_MARKERS = (
    "EVIDENCE:",
    "证据：",
    "证据:",
    "正文摘录",
    "引用：",
)
_QUOTE_IN_EVIDENCE_RE = re.compile(
    r'(?:EVIDENCE|证据)[:：]\s*.{8,}|「[^」]{4,}」|“[^”]{4,}”|"[^"]{4,}"'
)


@dataclass(frozen=True)
class CriticEvidenceFinding:
    severity: str
    code: str
    detail: str


@dataclass(frozen=True)
class CriticEvidenceReport:
    passed: bool
    findings: tuple[CriticEvidenceFinding, ...]


def validate_critic_commentary(
    commentary: str,
    *,
    chapter_text: str | None = None,
    min_commentary_chars: int = 40,
    require_evidence_line: bool = True,
) -> CriticEvidenceReport:
    """Ensure critic/LLM review text is non-empty and cites the draft."""

    text = (commentary or "").strip()
    findings: list[CriticEvidenceFinding] = []
    if len(text) < min_commentary_chars:
        findings.append(
            CriticEvidenceFinding(
                severity="critical",
                code=CRITIC_EMPTY_REVIEW,
                detail=f"review commentary too short ({len(text)} chars)",
            )
        )
        return CriticEvidenceReport(passed=False, findings=tuple(findings))

    if require_evidence_line:
        has_marker = any(marker in text for marker in _EVIDENCE_MARKERS)
        has_quote = bool(_QUOTE_IN_EVIDENCE_RE.search(text))
        if not (has_marker and has_quote):
            findings.append(
                CriticEvidenceFinding(
                    severity="critical",
                    code=CRITIC_MISSING_BODY_EVIDENCE,
                    detail=(
                        "review must include EVIDENCE/证据 line with a quoted "
                        "excerpt from the draft"
                    ),
                )
            )

    if chapter_text and findings:
        # Optional: verify at least one short substring from evidence appears in body
        pass

    return CriticEvidenceReport(passed=not findings, findings=tuple(findings))


def build_critic_evidence_prompt_suffix(*, language: str = "zh-CN") -> str:
    """Append to critic system prompts — mandatory evidence format."""

    if str(language or "").lower().startswith("zh"):
        return (
            "\n# OUTPUT FORMAT（追加，必填）\n"
            "EVIDENCE: 用引号摘录正文中 1–2 处具体句子（每处 ≥8 字），并说明对应哪条评分维度。\n"
            "禁止只写「30章均满足」类空审；无摘录视为未读正文。\n"
        )
    return (
        "\n# OUTPUT FORMAT (required)\n"
        "EVIDENCE: quote 1-2 concrete sentences from the draft (≥8 chars each) "
        "and map each to a rubric dimension.\n"
    )


__all__ = [
    "CRITIC_EMPTY_REVIEW",
    "CRITIC_MISSING_BODY_EVIDENCE",
    "CriticEvidenceFinding",
    "CriticEvidenceReport",
    "build_critic_evidence_prompt_suffix",
    "validate_critic_commentary",
]
