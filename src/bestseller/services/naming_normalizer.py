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


# Web-novel cast names so overused — or baked into legacy material packs — that
# generation LLMs keep defaulting to them, collapsing unrelated books onto the
# same handful of protagonists (the recurring 陆沉/宁尘/苏瑶 problem).
#
# Single source of truth (2026-08-18《九姓井口只认我》定罪): the ban used to
# live only on the CAST prompt (conception), but protagonist names are minted
# upstream in the concept-tournament expansion — 陆沉 sailed straight through
# title/premise/blurb before any cast prompt existed. Both sites now import
# this list; do not re-inline it.
CLICHE_NAME_BLOCKLIST: tuple[str, ...] = (
    "陆沉", "陆尘", "陆轩", "陆离", "陆鸣", "陆晨",
    "叶凡", "叶尘", "叶轩", "叶天", "叶辰",
    "林轩", "林动", "林凡", "林夕", "林墨",
    "苏瑶", "苏沐", "苏晴", "苏白",
    "楚风", "楚枫", "萧炎", "萧晨",
    "江晚", "沈追", "顾沉", "宁尘", "方域", "韩立", "秦尘",
)


def render_protagonist_name_ban(*, compact: bool = False) -> str:
    """One-line (compact) or block-form naming ban for zh generation prompts."""

    joined = "、".join(CLICHE_NAME_BLOCKLIST)
    if compact:
        return (
            "主角与配角命名硬约束：禁止使用下列烂大街网文名及仅差一字的近似变体："
            f"{joined}。取贴合本书具体设定的新鲜姓名。"
        )
    return (
        "【命名去重 · 硬约束】\n"
        "以下名字在网文里被严重滥用、或被旧模板固化，主角与主要配角一律禁止使用，"
        "也不要用仅差一字的高度雷同变体：\n"
        f"{joined}。"
    )


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
