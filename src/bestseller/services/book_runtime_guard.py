"""Freeze the rules a book is written under, and verify them before each use.

The failure this prevents
-------------------------
Editing a threshold, a prompt pack or a gate config while a book is mid-run made
later chapters die deterministically: chapters 1-50 were produced and validated
under one contract, chapters 51+ were produced under a second one and validated
against a third. Nothing announced the change, so the symptom appeared as
"chapter 51 onwards always fails" — a content bug that no amount of rewriting
could fix.

The shape of the fix, from DeterminFlow's runtime guards: at book start, record
a semantic hash of everything that decides what "correct" means. Then re-derive
and compare that hash *every time it is consumed* — not once at creation. A
mismatch is reported as configuration drift, naming the fields that moved,
instead of being discovered later as an unfixable quality failure.

Two deliberate design choices:

* **Verification runs at consumption, not at creation.** A guard checked only
  when the book was created cannot notice a change made an hour later.
* **``audit_only`` is the default.** Every gate this codebase has added
  hard-blocking first has produced false-positive kills. This one records drift
  and keeps writing until the drift data says the detector is trustworthy;
  ``enforce`` is opt-in per deployment.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Final

logger = logging.getLogger(__name__)

__all__ = [
    "GUARD_METADATA_KEY",
    "BookRuntimeGuard",
    "GuardMode",
    "DriftReport",
    "build_guard",
    "guard_mode",
    "load_guard",
    "store_guard",
    "verify_guard",
]

#: Where the frozen guard lives inside ``projects.metadata``.
GUARD_METADATA_KEY: Final[str] = "book_runtime_guard"

_SCHEMA_VERSION: Final[str] = "book-runtime-guard.v1"


class GuardMode(str, Enum):
    """How loudly drift should be treated."""

    OFF = "off"
    AUDIT_ONLY = "audit_only"
    ENFORCE = "enforce"


def guard_mode() -> GuardMode:
    """Resolve the deployment's drift policy. Defaults to ``audit_only``."""

    raw = (os.getenv("BESTSELLER_BOOK_RUNTIME_GUARD_MODE", "") or "").strip().lower()
    try:
        return GuardMode(raw) if raw else GuardMode.AUDIT_ONLY
    except ValueError:
        logger.warning(
            "unknown BESTSELLER_BOOK_RUNTIME_GUARD_MODE=%r; falling back to audit_only",
            raw,
        )
        return GuardMode.AUDIT_ONLY


def _canonical_hash(value: Any) -> str:
    serialized = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class BookRuntimeGuard:
    """Semantic fingerprints of the contract a book is being written under."""

    fingerprints: Mapping[str, str] = field(default_factory=dict)
    frozen_at: str | None = None
    frozen_by: str | None = None
    schema_version: str = _SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "fingerprints": dict(self.fingerprints),
            "frozen_at": self.frozen_at,
            "frozen_by": self.frozen_by,
            "schema_version": self.schema_version,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any] | None) -> "BookRuntimeGuard | None":
        if not isinstance(raw, Mapping):
            return None
        fingerprints = raw.get("fingerprints")
        if not isinstance(fingerprints, Mapping):
            return None
        return cls(
            fingerprints={str(k): str(v) for k, v in fingerprints.items()},
            frozen_at=_optional_str(raw.get("frozen_at")),
            frozen_by=_optional_str(raw.get("frozen_by")),
            schema_version=str(raw.get("schema_version") or _SCHEMA_VERSION),
        )


@dataclass(frozen=True)
class DriftReport:
    """What changed between the frozen contract and the live one."""

    changed: tuple[str, ...] = ()
    added: tuple[str, ...] = ()
    removed: tuple[str, ...] = ()
    mode: GuardMode = GuardMode.AUDIT_ONLY
    guard_present: bool = True

    @property
    def has_drift(self) -> bool:
        return bool(self.changed or self.added or self.removed)

    @property
    def blocks_production(self) -> bool:
        """Only ``enforce`` mode halts a book; audit mode just records."""

        return self.has_drift and self.mode is GuardMode.ENFORCE

    def describe(self) -> str:
        parts = []
        if self.changed:
            parts.append(f"changed={','.join(self.changed)}")
        if self.added:
            parts.append(f"added={','.join(self.added)}")
        if self.removed:
            parts.append(f"removed={','.join(self.removed)}")
        return "; ".join(parts) or "no drift"

    def to_payload(self) -> dict[str, Any]:
        return {
            "has_drift": self.has_drift,
            "blocks_production": self.blocks_production,
            "mode": self.mode.value,
            "changed": list(self.changed),
            "added": list(self.added),
            "removed": list(self.removed),
            "detail": self.describe(),
        }


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def build_guard(
    contract: Mapping[str, Any],
    *,
    frozen_by: str | None = None,
    now: _dt.datetime | None = None,
) -> BookRuntimeGuard:
    """Fingerprint each part of the contract separately.

    Per-part hashes rather than one blob hash, so a drift report can name what
    moved. "Something changed" is not actionable; "``length_contract`` changed"
    tells an operator whether to re-freeze or roll back.
    """

    fingerprints = {
        str(name): _canonical_hash(value) for name, value in contract.items()
    }
    stamp = (now or _dt.datetime.now(_dt.UTC)).isoformat()
    return BookRuntimeGuard(
        fingerprints=fingerprints, frozen_at=stamp, frozen_by=frozen_by
    )


def store_guard(
    metadata: Mapping[str, Any] | None, guard: BookRuntimeGuard
) -> dict[str, Any]:
    """Return a new metadata mapping carrying ``guard`` (never mutates input)."""

    base: dict[str, Any] = dict(metadata) if isinstance(metadata, Mapping) else {}
    base[GUARD_METADATA_KEY] = guard.to_dict()
    return base


def load_guard(metadata: Mapping[str, Any] | None) -> BookRuntimeGuard | None:
    if not isinstance(metadata, Mapping):
        return None
    return BookRuntimeGuard.from_dict(metadata.get(GUARD_METADATA_KEY))


def verify_guard(
    metadata: Mapping[str, Any] | None,
    contract: Mapping[str, Any],
    *,
    mode: GuardMode | None = None,
) -> DriftReport:
    """Compare the live contract against the frozen one.

    A book with no guard (created before this existed) reports no drift: it is
    unverifiable, not broken, and refusing to write those books would be a
    self-inflicted outage far worse than the drift.
    """

    resolved_mode = mode or guard_mode()
    if resolved_mode is GuardMode.OFF:
        return DriftReport(mode=resolved_mode)

    guard = load_guard(metadata)
    if guard is None:
        return DriftReport(mode=resolved_mode, guard_present=False)

    live = {str(name): _canonical_hash(value) for name, value in contract.items()}
    frozen = dict(guard.fingerprints)

    changed = tuple(
        sorted(key for key in live.keys() & frozen.keys() if live[key] != frozen[key])
    )
    added = tuple(sorted(live.keys() - frozen.keys()))
    removed = tuple(sorted(frozen.keys() - live.keys()))
    return DriftReport(
        changed=changed, added=added, removed=removed, mode=resolved_mode
    )
