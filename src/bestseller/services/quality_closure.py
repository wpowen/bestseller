"""Independent closure checks for quality findings."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class QualityClosureReport:
    status: str
    previous_blocking_codes: tuple[str, ...]
    remaining_blocking_codes: tuple[str, ...]
    resolved_codes: tuple[str, ...]

    @property
    def closed(self) -> bool:
        return self.status == "closed"

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "previous_blocking_codes": list(self.previous_blocking_codes),
            "remaining_blocking_codes": list(self.remaining_blocking_codes),
            "resolved_codes": list(self.resolved_codes),
        }


def evaluate_quality_closure(
    previous_blocking_codes: Iterable[str],
    current_blocking_codes: Iterable[str],
) -> QualityClosureReport:
    previous = tuple(dict.fromkeys(str(code) for code in previous_blocking_codes if code))
    current = tuple(dict.fromkeys(str(code) for code in current_blocking_codes if code))
    current_set = set(current)
    resolved = tuple(code for code in previous if code not in current_set)
    status = "closed" if previous and not current else "open" if current else "clean"
    return QualityClosureReport(
        status=status,
        previous_blocking_codes=previous,
        remaining_blocking_codes=current,
        resolved_codes=resolved,
    )


__all__ = ["QualityClosureReport", "evaluate_quality_closure"]
