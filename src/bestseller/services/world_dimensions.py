"""Typed loader + baseline selector for the world-model derivation layer.

One asset, one role:

* ``config/world_model_dimensions.yaml`` — the genre-NEUTRAL *machine*: a fixed
  table of social dimensions phrased as **questions** (value/violence/transport/
  food …), the baseline-substrate catalogue, and the anti-homogenisation rule.

**The table contains zero genre knowledge.** Every concrete world fact (a
currency name, a faction, a magic rule) is *derived at run time* from the book's
own axioms — never stored here. Switching genre = switching fuel, not the
machine. This is the structural cure for cross-book homogenisation: two
different premises necessarily diff their baseline differently, so they cannot
produce the same world.

Dependency-light (no LLM, no DB). Mirrors ``services/ideology_library.py``.
"""


from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from functools import lru_cache
import re

from bestseller.services.quality_levers._loader import (
    as_dict,
    as_str,
    as_str_tuple,
    load_yaml,
)

_CONFIG_FILENAME = "world_model_dimensions.yaml"
_DEFAULT_BASELINE = "fully_invented"


@dataclass(frozen=True)
class WorldDimension:
    """One social dimension — a genre-neutral question, not an answer."""

    key: str
    question: str
    ripple_hint: str
    order: int


@dataclass(frozen=True)
class WorldBaseline:
    """A reality substrate the speculative axiom is diffed against."""

    key: str
    label: str
    description: str


@dataclass(frozen=True)
class WorldDimensionTable:
    """Typed, cached view over ``world_model_dimensions.yaml``."""

    version: int
    dimensions: tuple[WorldDimension, ...]
    baselines: tuple[WorldBaseline, ...]
    baseline_signals: Mapping[str, tuple[str, ...]]
    anti_homogenization_principle: str

    def dimension_keys(self) -> tuple[str, ...]:
        return tuple(d.key for d in self.dimensions)

    def baseline(self, key: str) -> WorldBaseline | None:
        for base in self.baselines:
            if base.key == key:
                return base
        return None


@lru_cache(maxsize=1)
def load_world_dimensions() -> WorldDimensionTable:
    """Load + cache the dimension table. Never raises on missing optional keys."""

    raw = load_yaml(_CONFIG_FILENAME)
    dims: list[WorldDimension] = []
    for entry in raw.get("dimensions", []) or []:
        data = as_dict(entry)
        key = as_str(data.get("key"))
        if not key:
            continue
        order_raw = data.get("order", 2)
        try:
            order = int(order_raw)
        except (TypeError, ValueError):
            order = 2
        dims.append(
            WorldDimension(
                key=key,
                question=as_str(data.get("question")),
                ripple_hint=as_str(data.get("ripple_hint")),
                order=max(1, order),
            )
        )

    baselines: list[WorldBaseline] = []
    for entry in raw.get("baselines", []) or []:
        data = as_dict(entry)
        key = as_str(data.get("key"))
        if not key:
            continue
        baselines.append(
            WorldBaseline(
                key=key,
                label=as_str(data.get("label")) or key,
                description=as_str(data.get("description")),
            )
        )

    signals_raw = as_dict(raw.get("baseline_signals"))
    signals: dict[str, tuple[str, ...]] = {}
    for base_key, words in signals_raw.items():
        signals[str(base_key)] = as_str_tuple(words)

    anti = as_dict(raw.get("anti_homogenization"))

    return WorldDimensionTable(
        version=int(raw.get("version", 1) or 1),
        dimensions=tuple(dims),
        baselines=tuple(baselines),
        baseline_signals=signals,
        anti_homogenization_principle=as_str(anti.get("principle")),
    )


# ---------------------------------------------------------------------------
# Baseline selection (heuristic — deterministic fallback only)
# ---------------------------------------------------------------------------


def select_baseline(
    *,
    genre: str | None = None,
    premise: str = "",
    table: WorldDimensionTable | None = None,
) -> tuple[str, str]:
    """Pick the reality substrate to diff against, returning ``(key, rationale)``.

    Heuristic keyword vote over the era-substrate signals. This is the
    deterministic *fallback*; in production the LLM is asked to confirm/choose
    the baseline (it is the smarter judge), so this never hard-binds a genre to
    story content — only to a mundane era substrate.
    """

    table = table or load_world_dimensions()
    haystack = f"{genre or ''} {premise or ''}"
    scores: dict[str, int] = {}
    for base_key, words in table.baseline_signals.items():
        hits = sum(1 for w in words if w and w in haystack)
        if hits:
            scores[base_key] = hits
    if not scores:
        base = table.baseline(_DEFAULT_BASELINE)
        label = base.label if base else _DEFAULT_BASELINE
        return _DEFAULT_BASELINE, f"无明确时代信号,默认使用『{label}』底座(待 LLM 确认)。"
    best = max(scores, key=lambda k: scores[k])
    base = table.baseline(best)
    label = base.label if base else best
    return best, f"由题材/前提信号判定底座为『{label}』(命中 {scores[best]} 个信号词)。"


# ---------------------------------------------------------------------------
# Prompt block (pure)
# ---------------------------------------------------------------------------


def render_dimensions_prompt_block(table: WorldDimensionTable | None = None) -> str:
    """Render the dimension questions as a numbered prompt block."""

    table = table or load_world_dimensions()
    lines = ["【世界维度表(逐维差分,只问不答,答案由公理推演)】"]
    for idx, dim in enumerate(table.dimensions, start=1):
        lines.append(
            f"{idx}. [{dim.key}] {dim.question} (至少推到第{dim.order}阶;涟漪:{dim.ripple_hint})"
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Anti-homogenisation scoring (pure)
# ---------------------------------------------------------------------------

_TOKEN_RE = re.compile(r"[一-鿿A-Za-z0-9]+")


def _tokens(text: str) -> set[str]:
    """Char-level CJK shingles + latin words — language-agnostic anchoring set."""

    out: set[str] = set()
    for chunk in _TOKEN_RE.findall(text or ""):
        if chunk.isascii():
            if len(chunk) >= 2:
                out.add(chunk.lower())
        else:
            # CJK: 2-gram shingles capture compound nouns ("灵石", "空域").
            if len(chunk) == 1:
                out.add(chunk)
            for i in range(len(chunk) - 1):
                out.add(chunk[i : i + 2])
    return out


def law_specificity(law_text: str, axioms: Sequence[str]) -> float:
    """How strongly a derived law is anchored to THIS book's axioms.

    Returns the fraction of the law's tokens that also appear in the axiom set
    (0.0-1.0). A law that merely restates a universal trope with no premise-
    specific token scores low; a law grounded in the book's concrete axioms
    scores high. This needs no genre/trope blocklist — it is a pure anchoring
    measure, so it stays genre-neutral.
    """

    law_tokens = _tokens(law_text)
    if not law_tokens:
        return 0.0
    axiom_tokens: set[str] = set()
    for axiom in axioms:
        axiom_tokens |= _tokens(axiom)
    if not axiom_tokens:
        return 0.0
    overlap = law_tokens & axiom_tokens
    return round(len(overlap) / len(law_tokens), 4)


def text_similarity(a: str, b: str) -> float:
    """Jaccard similarity over token sets — used for cross-model distinctness."""

    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    return round(len(ta & tb) / len(ta | tb), 4)


def corpus_distinctness(texts: Iterable[str]) -> float:
    """1 - mean pairwise Jaccard across texts (higher = more distinct)."""

    items = [t for t in texts if t]
    if len(items) < 2:
        return 1.0
    sims: list[float] = []
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            sims.append(text_similarity(items[i], items[j]))
    if not sims:
        return 1.0
    return round(1.0 - (sum(sims) / len(sims)), 4)


__all__ = [
    "WorldBaseline",
    "WorldDimension",
    "WorldDimensionTable",
    "corpus_distinctness",
    "law_specificity",
    "load_world_dimensions",
    "render_dimensions_prompt_block",
    "select_baseline",
    "text_similarity",
]
