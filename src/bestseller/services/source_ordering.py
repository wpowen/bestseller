"""Total order for native source rows; never reorder authored semantic arrays."""

from __future__ import annotations

import hashlib
from typing import Any, Callable, Iterable, TypeVar
from uuid import UUID

T = TypeVar("T")


def source_row_id(row: Any) -> int:
    """Persistent UUID tie-breaker matching PostgreSQL UUID ordering."""
    value = getattr(row, "id", None)
    if isinstance(value, UUID):
        return value.int
    if isinstance(value, str):
        try:
            return UUID(value).int
        except ValueError:
            pass
    error = ValueError("source_ordering_persistent_identity_required")
    error.differences = [
        {
            "field": "source_row.id",
            "expected": {"type": "UUID", "length": 36, "sha256": None},
            "actual": {
                "type": type(value).__name__,
                "length": len(str(value)),
                "sha256": hashlib.sha256(str(value).encode()).hexdigest(),
            },
        }
    ]
    raise error


def ordered_source_rows(
    rows: Iterable[T], *, priority: Callable[[T], tuple] | None = None, reverse: bool = False
) -> list[T]:
    """Keep business priority, then the persistent identity; do not mutate rows."""
    return sorted(
        rows,
        key=lambda row: (*(priority(row) if priority else ()), source_row_id(row)),
        reverse=reverse,
    )
