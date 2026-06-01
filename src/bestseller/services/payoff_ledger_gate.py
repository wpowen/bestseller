"""Hook–payoff ledger: ensure chapters deliver visible payoffs."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Final

PAYOFF_LEDGER_LOW: Final[str] = "PAYOFF_LEDGER_LOW"
PAYOFF_HOOK_ONLY: Final[str] = "PAYOFF_HOOK_ONLY"

_HOOK_SIGNALS = (
    "却",
    "没想到",
    "突然",
    "究竟",
    "到底",
    "门",
    "电话",
    "脚步声",
    "回头",
    "愣",
    "震",
)
_PAYOFF_SIGNALS = (
    "终于",
    "原来",
    "明白",
    "发现",
    "证实",
    "拿到",
    "解开",
    "认出",
    "代价",
    "失去",
    "赢得",
    "当场",
    "证据",
)


@dataclass(frozen=True)
class PayoffLedgerFinding:
    severity: str
    code: str
    detail: str
    hook_hits: int
    payoff_hits: int
    payoff_density: float


@dataclass(frozen=True)
class PayoffLedgerReport:
    chapter_position: int
    finding: PayoffLedgerFinding

    @property
    def passed(self) -> bool:
        return self.finding.severity == "info"


def _count_signals(text: str, signals: tuple[str, ...]) -> int:
    return sum(text.count(s) for s in signals)


def evaluate_payoff_ledger(
    chapter_text: str,
    *,
    chapter_position: int,
    min_payoff_density: float = 0.18,
    min_payoff_hits: int = 2,
    max_hook_only_ratio: float = 4.0,
) -> PayoffLedgerReport:
    """Heuristic hook vs payoff balance on assembled chapter text."""

    body = (chapter_text or "").strip()
    hooks = _count_signals(body, _HOOK_SIGNALS)
    payoffs = _count_signals(body, _PAYOFF_SIGNALS)
    total = max(hooks + payoffs, 1)
    density = payoffs / total

    if payoffs < min_payoff_hits and hooks >= min_payoff_hits:
        return PayoffLedgerReport(
            chapter_position=chapter_position,
            finding=PayoffLedgerFinding(
                severity="critical",
                code=PAYOFF_HOOK_ONLY,
                detail=(
                    f"hooks={hooks} payoffs={payoffs} — chapter teases without landing"
                ),
                hook_hits=hooks,
                payoff_hits=payoffs,
                payoff_density=density,
            ),
        )

    if density < min_payoff_density and hooks > 0:
        return PayoffLedgerReport(
            chapter_position=chapter_position,
            finding=PayoffLedgerFinding(
                severity="critical",
                code=PAYOFF_LEDGER_LOW,
                detail=(
                    f"payoff_density={density:.2f} < {min_payoff_density} "
                    f"(hooks={hooks}, payoffs={payoffs})"
                ),
                hook_hits=hooks,
                payoff_hits=payoffs,
                payoff_density=density,
            ),
        )

    return PayoffLedgerReport(
        chapter_position=chapter_position,
        finding=PayoffLedgerFinding(
            severity="info",
            code="PAYOFF_LEDGER_OK",
            detail=f"payoff_density={density:.2f} hooks={hooks} payoffs={payoffs}",
            hook_hits=hooks,
            payoff_hits=payoffs,
            payoff_density=density,
        ),
    )


__all__ = [
    "PAYOFF_HOOK_ONLY",
    "PAYOFF_LEDGER_LOW",
    "PayoffLedgerFinding",
    "PayoffLedgerReport",
    "evaluate_payoff_ledger",
]
