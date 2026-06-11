"""Deterministic repair for ``NAMING_OUT_OF_POOL`` violations.

The naming gate detects out-of-pool personal names in a draft. Historically
the only remediation was a full-context ``scene_writer_regen`` call (~20k
input tokens) that frequently re-introduced a *different* rogue name — the
single largest token sink observed in long autowrite runs.

Most rogue names are deterministically fixable without any LLM call:

* **Tier 1 — pool variant**: the rogue name is a near-variant of a pool
  name (same surname, ≤1 differing char, e.g. 陆尘 → 陆沉). Substitute the
  canonical pool spelling.
* **Tier 2 — generic referent**: the rogue name is an invented walk-on.
  Substitute a neutral referent (那人 / 对方 / 来人 …), one distinct
  referent per rogue name so dialogue stays attributable.

The normalizer is conservative: it only rewrites names the gate itself
detected, refuses ambiguous tier-1 matches (two pool candidates), and gives
up (returns ``None``) when there are too many distinct rogue names — a sign
the draft has deeper problems that deserve a real regen.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Iterable, Mapping

logger = logging.getLogger(__name__)


# Distinct neutral referents so two normalized walk-ons in one scene stay
# distinguishable. Order matters: earlier entries read more naturally.
_ZH_GENERIC_REFERENTS: tuple[str, ...] = (
    "那人",
    "对方",
    "来人",
    "旁边那人",
)

# Refuse to normalize when a draft has more distinct rogue names than this —
# heavy invention means the scene cast itself drifted and a regen is the
# safer remedy.
MAX_DISTINCT_ROGUE_NAMES = 3


@dataclass(frozen=True)
class NamingNormalization:
    """Outcome of a deterministic naming pass."""

    text: str
    substitutions: dict[str, str] = field(default_factory=dict)
    unresolved: tuple[str, ...] = ()

    @property
    def changed(self) -> bool:
        return bool(self.substitutions)


def _shared_char_count(a: str, b: str) -> int:
    return sum(1 for ca, cb in zip(a, b) if ca == cb)


def _pool_variant_match(rogue: str, pool: Iterable[str]) -> str | None:
    """Return the unique pool name that ``rogue`` is a near-variant of.

    Match rule: same first character (surname), same length, and at most one
    differing character — i.e. a typo/homophone drift of a cast member.
    Ambiguous (≥2 candidates) or no match returns ``None``.
    """

    candidates: list[str] = []
    for name in pool:
        name = (name or "").strip()
        if not name or name == rogue:
            continue
        if len(name) != len(rogue) or len(name) < 2:
            continue
        if name[0] != rogue[0]:
            continue
        if _shared_char_count(name, rogue) >= len(name) - 1:
            candidates.append(name)
    if len(candidates) == 1:
        return candidates[0]
    return None


def normalize_out_of_pool_names(
    text: str,
    *,
    rogue_names: Mapping[str, int],
    allowed_names: Iterable[str],
    language: str = "zh-CN",
) -> NamingNormalization | None:
    """Deterministically substitute rogue names in ``text``.

    Args:
        text: The draft text containing rogue names.
        rogue_names: name → occurrence count, as detected by the naming gate.
        allowed_names: The full naming pool (roster ∪ seed pool).
        language: Only zh is supported; other languages return ``None``.

    Returns:
        ``NamingNormalization`` when a substitution pass ran (it may still
        carry ``unresolved`` names the caller should regen for), or ``None``
        when normalization should not be attempted at all.
    """

    if not text or not rogue_names:
        return None
    if not language.lower().startswith("zh"):
        return None
    distinct = [name for name in rogue_names if name and name.strip()]
    if not distinct or len(distinct) > MAX_DISTINCT_ROGUE_NAMES:
        return None

    pool = [n.strip() for n in allowed_names if n and n.strip()]
    substitutions: dict[str, str] = {}
    unresolved: list[str] = []
    referent_iter = iter(_ZH_GENERIC_REFERENTS)

    # Longer names first so a short rogue name never clobbers a longer one
    # that contains it as a prefix.
    for rogue in sorted(distinct, key=len, reverse=True):
        variant = _pool_variant_match(rogue, pool)
        if variant is not None:
            substitutions[rogue] = variant
            continue
        referent = next(referent_iter, None)
        if referent is None:
            unresolved.append(rogue)
            continue
        substitutions[rogue] = referent

    if not substitutions:
        return None

    normalized = text
    for rogue, replacement in sorted(
        substitutions.items(), key=lambda kv: len(kv[0]), reverse=True
    ):
        normalized = normalized.replace(rogue, replacement)

    logger.info(
        "naming_normalizer: substituted %d rogue name(s) deterministically "
        "(%s)%s",
        len(substitutions),
        ", ".join(f"{k}→{v}" for k, v in substitutions.items()),
        f"; unresolved={unresolved}" if unresolved else "",
    )
    return NamingNormalization(
        text=normalized,
        substitutions=substitutions,
        unresolved=tuple(unresolved),
    )


__all__ = [
    "MAX_DISTINCT_ROGUE_NAMES",
    "NamingNormalization",
    "normalize_out_of_pool_names",
]
