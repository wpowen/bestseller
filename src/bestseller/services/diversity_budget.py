"""L3/L5 DiversityBudget — per-project rotation tracker.

Bugs addressed (plan §2):

* **Bug 5** (similar openings) — ``next_opening()`` picks an
  ``OpeningArchetype`` not used in the last ``no_repeat_within`` chapters.
* **Bug 7** (hot vocab repetition — e.g. ``shard`` × 18/章) — ``register_vocab``
  counts tokens per chapter; ``hot_vocab`` returns the top-N words in the
  last N chapters to feed the L3 prompt constructor as a banned-word list.
* **Bug 10** (cliffhanger repetition) — ``next_cliffhanger()`` and
  ``recent_cliffhangers()`` support the L5 CliffhangerRotationCheck.

Design notes:

* **Plain-dataclass (mutable)** — registrations append-in-place. Callers
  persist via the async helpers at the bottom of this module.
* **JSONB round-trip first** — state must survive DB save/load as the
  DB column is the source of truth; in-memory is just a working copy.
* **Capped history** — ``vocab_freq`` keeps at most the last
  ``VOCAB_HISTORY_MAX_CHAPTERS`` chapters to stop unbounded JSONB growth.
* **Language-aware tokenization** — English lower-case words; Chinese
  2–4-char CJK runs (cheap bigram proxy, not real segmentation).
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.infra.db.models import DiversityBudgetModel
from bestseller.services.hype_engine import (
    HypeDensityBand,
    HypeMoment,
    HypeRecipe,
    HypeType,
    hype_moment_from_dict,
    hype_moment_to_dict,
)
from bestseller.services.invariants import (
    CliffhangerPolicy,
    CliffhangerType,
    OpeningArchetype,
)


logger = logging.getLogger(__name__)


VOCAB_HISTORY_MAX_CHAPTERS = 10
DEFAULT_HOT_VOCAB_WINDOW = 5
DEFAULT_HOT_VOCAB_TOP_N = 20
DEFAULT_HOT_VOCAB_MIN_COUNT = 3

# Title n-gram cooldown tracking (Bug "X决堤" / 风暴 / 异变 套路重复).
# Per-project ledger maps each 2–3 char CJK n-gram seen in a chapter title to
# the most-recent chapter it appeared in. We compare against the *current*
# chapter at title-generation time and reject (or surface as a constraint to
# the planner) any candidate n-gram still inside the cooldown window.
TITLE_NGRAM_MIN_LEN = 2
TITLE_NGRAM_MAX_LEN = 3
DEFAULT_TITLE_COOLDOWN_CHAPTERS = 75
TITLE_PATTERN_HISTORY_MAX = 2000  # cap pruning bound for unbounded growth


# ---------------------------------------------------------------------------
# Tokenization helpers.
# ---------------------------------------------------------------------------


_EN_WORD_RE = re.compile(r"[A-Za-z][A-Za-z'\-]{1,}")
# CJK bigrams — cheap word proxy for Chinese.
_CJK_NGRAM_RE = re.compile(r"[\u4e00-\u9fff]{2,4}")
# English boilerplate we never want to ban (too common, zero diversity signal).
_EN_STOPWORDS = frozenset(
    {
        "the", "and", "but", "for", "nor", "yet", "so", "a", "an",
        "in", "on", "at", "by", "to", "of", "is", "it", "its",
        "he", "she", "her", "his", "him", "they", "them", "their",
        "was", "were", "been", "be", "had", "has", "have", "do", "did",
        "not", "no", "if", "as", "that", "this", "these", "those",
        "with", "from", "into", "onto", "about", "over", "under",
        "will", "would", "could", "should", "can", "may", "might",
        "up", "down", "out", "off", "then", "than", "just",
    }
)


def _english_tokens(text: str) -> list[str]:
    return [
        m.group(0).lower()
        for m in _EN_WORD_RE.finditer(text)
        if m.group(0).lower() not in _EN_STOPWORDS
    ]


def _chinese_ngrams(text: str) -> list[str]:
    # Only emit the longest CJK run for each match to avoid double-counting.
    return [m.group(0) for m in _CJK_NGRAM_RE.finditer(text)]


# CJK runs at least ``TITLE_NGRAM_MIN_LEN`` long; we then enumerate every
# substring of length [MIN, MAX] from each run. Punctuation/spaces split runs
# naturally because the regex matches CJK characters only.
_CJK_RUN_RE = re.compile(r"[\u4e00-\u9fff]+")


def extract_title_ngrams(title: str) -> tuple[str, ...]:
    """Return the unique 2–3 char CJK n-grams found in ``title``.

    Used by the title-pattern cooldown: each n-gram is the unit we track.
    For a title like ``"血脉决堤"`` we emit ``("血脉", "脉决", "决堤", "血脉决",
    "脉决堤")``. ASCII titles return empty (no n-gram analysis).
    """
    if not title:
        return ()
    grams: list[str] = []
    seen: set[str] = set()
    for run in _CJK_RUN_RE.findall(title):
        if len(run) < TITLE_NGRAM_MIN_LEN:
            continue
        max_n = min(TITLE_NGRAM_MAX_LEN, len(run))
        for n in range(TITLE_NGRAM_MIN_LEN, max_n + 1):
            for i in range(0, len(run) - n + 1):
                gram = run[i : i + n]
                if gram in seen:
                    continue
                seen.add(gram)
                grams.append(gram)
    return tuple(grams)


def extract_tokens(text: str, language: str | None) -> list[str]:
    """Return frequency-counting units for ``text``.

    Returns lowercase English words (with stopwords removed) for English
    projects, and 2–4-char CJK runs for everything else. The goal is cheap
    repetition detection, not full NLP.
    """
    lang = str(language or "zh-CN").lower()
    if lang.startswith("en"):
        return _english_tokens(text)
    return _chinese_ngrams(text)


# ---------------------------------------------------------------------------
# Data classes.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OpeningUse:
    """One recorded opening archetype, keyed by chapter."""

    chapter_no: int
    archetype: OpeningArchetype


@dataclass(frozen=True)
class CliffhangerUse:
    """One recorded cliffhanger type, keyed by chapter."""

    chapter_no: int
    kind: CliffhangerType


@dataclass
class DiversityBudget:
    """Mutable per-project rotation tracker.

    * ``openings_used`` / ``cliffhangers_used`` are append-only logs keyed
      by chapter_no. Duplicates for the same chapter are allowed (the
      caller owns idempotency by clearing + re-registering when reworking
      a chapter).
    * ``titles_used`` is a flat list — duplicate detection lives at call
      sites.
    * ``vocab_freq`` maps ``str(chapter_no) → {token: count}``. We keep
      strings for JSONB-safety (dict keys must be strings).
    """

    project_id: UUID
    openings_used: list[OpeningUse] = field(default_factory=list)
    cliffhangers_used: list[CliffhangerUse] = field(default_factory=list)
    titles_used: list[str] = field(default_factory=list)
    vocab_freq: dict[str, dict[str, int]] = field(default_factory=dict)
    hype_moments: list[HypeMoment] = field(default_factory=list)
    # Maps each CJK title n-gram to the most recent chapter where it appeared.
    # Used by ``title_pattern_cooldown_violations`` to block repeated patterns
    # like 《血脉决堤》→《灵脉决堤》→《道心决堤》 within a sliding chapter window.
    title_patterns: dict[str, int] = field(default_factory=dict)

    # -- queries ---------------------------------------------------------

    def recent_openings(self, n: int) -> tuple[OpeningArchetype, ...]:
        if n <= 0:
            return ()
        return tuple(u.archetype for u in self.openings_used[-n:])

    def recent_cliffhangers(self, n: int) -> tuple[CliffhangerType, ...]:
        if n <= 0:
            return ()
        return tuple(u.kind for u in self.cliffhangers_used[-n:])

    def recent_hype_types(self, n: int) -> tuple[HypeType, ...]:
        """Return the last ``n`` hype types, most-recent-last."""
        if n <= 0:
            return ()
        return tuple(m.hype_type for m in self.hype_moments[-n:])

    def recent_recipe_keys(self, n: int) -> tuple[str, ...]:
        """Return the last ``n`` recipe keys, most-recent-last; skips None."""
        if n <= 0:
            return ()
        return tuple(
            m.recipe_key for m in self.hype_moments[-n:] if m.recipe_key
        )

    def next_opening(
        self,
        pool: Sequence[OpeningArchetype] | None = None,
        *,
        no_repeat_within: int = 3,
    ) -> OpeningArchetype:
        """Return a pool archetype absent from the last ``no_repeat_within``.

        Fallback (all pool members recently used): returns the least
        recently used pool member. Raises ``ValueError`` if the pool is
        empty.
        """

        # `None` means "caller wants the default"; an explicit empty pool
        # is a programming error and should surface rather than get silently
        # substituted.
        ordered_pool: tuple[OpeningArchetype, ...] = (
            tuple(OpeningArchetype) if pool is None else tuple(pool)
        )
        if not ordered_pool:
            raise ValueError("OpeningArchetype pool is empty")

        recent = set(self.recent_openings(no_repeat_within))
        unused = [a for a in ordered_pool if a not in recent]
        if unused:
            return unused[0]

        # All recently used — return the LRU within the pool.
        # Build recency index (latest usage has highest idx).
        recency: dict[OpeningArchetype, int] = {}
        for idx, use in enumerate(self.openings_used):
            recency[use.archetype] = idx
        # Among pool members, the one with the smallest recency wins.
        return min(
            ordered_pool,
            key=lambda a: recency.get(a, -1),
        )

    def next_cliffhanger(
        self,
        policy: CliffhangerPolicy | None = None,
        *,
        fallback: bool = True,
    ) -> CliffhangerType:
        """Pick a cliffhanger kind respecting the given policy.

        ``fallback=True`` returns the least-recently-used allowed type when
        every allowed type has been used inside the window;
        ``fallback=False`` raises ``ValueError`` instead.
        """

        policy = policy or CliffhangerPolicy()
        allowed: tuple[CliffhangerType, ...] = (
            tuple(policy.allowed_types)
            if policy.allowed_types
            else tuple(CliffhangerType)
        )
        if not allowed:
            raise ValueError("Cliffhanger policy has no allowed types")

        recent = set(self.recent_cliffhangers(policy.no_repeat_within))
        unused = [k for k in allowed if k not in recent]
        if unused:
            return unused[0]
        if not fallback:
            raise ValueError(
                "All allowed cliffhangers used within "
                f"{policy.no_repeat_within}-chapter window"
            )
        # LRU fallback within the allowed set.
        recency: dict[CliffhangerType, int] = {}
        for idx, use in enumerate(self.cliffhangers_used):
            recency[use.kind] = idx
        return min(allowed, key=lambda k: recency.get(k, -1))

    def next_hype(
        self,
        recipe_deck: Iterable[HypeRecipe],
        band: HypeDensityBand,
        *,
        recipe_memory: int = 5,
        history: int = 5,
    ) -> HypeRecipe | None:
        """Pick the next ``HypeRecipe`` from ``recipe_deck`` given band + history.

        Thin wrapper over ``select_recipe_for_chapter`` that reads
        most-recent-first from the budget's own ``hype_moments`` log.
        Returns ``None`` when the deck is empty (engine no-op).
        """
        from bestseller.services.hype_engine import select_recipe_for_chapter

        deck_list = list(recipe_deck)
        if not deck_list:
            return None

        recent_types = list(reversed(self.recent_hype_types(history)))
        recent_keys = list(reversed(self.recent_recipe_keys(history)))
        return select_recipe_for_chapter(
            band,
            deck_list,
            recent_recipe_keys=[k for k in recent_keys],
            recent_hype_types=[t for t in recent_types],
            recipe_memory=recipe_memory,
        )

    def hot_vocab(
        self,
        *,
        window: int = DEFAULT_HOT_VOCAB_WINDOW,
        top: int = DEFAULT_HOT_VOCAB_TOP_N,
        min_count: int = DEFAULT_HOT_VOCAB_MIN_COUNT,
    ) -> tuple[str, ...]:
        """Return top-N tokens across the last ``window`` chapters.

        Tokens appearing fewer than ``min_count`` times total are dropped
        to suppress false positives (a word must be *actually* hot to
        deserve banning).
        """

        if not self.vocab_freq or window <= 0 or top <= 0:
            return ()
        ordered_keys = sorted(self.vocab_freq.keys(), key=_chapter_key)
        sample = ordered_keys[-window:]
        agg: Counter[str] = Counter()
        for key in sample:
            for word, count in self.vocab_freq.get(key, {}).items():
                agg[word] += count
        return tuple(w for w, c in agg.most_common(top) if c >= min_count)

    # -- mutators --------------------------------------------------------

    def register_opening(
        self, chapter_no: int, archetype: OpeningArchetype
    ) -> None:
        self.openings_used.append(OpeningUse(int(chapter_no), archetype))

    def register_cliffhanger(
        self, chapter_no: int, kind: CliffhangerType
    ) -> None:
        self.cliffhangers_used.append(CliffhangerUse(int(chapter_no), kind))

    def register_hype_moment(
        self,
        chapter_no: int,
        hype_type: HypeType,
        recipe_key: str | None,
        intensity: float,
    ) -> None:
        """Append a ``HypeMoment`` row to the budget log.

        Called by the pipeline after a chapter finalises (once we know the
        final hype_type/recipe/intensity), mirroring
        ``register_opening`` / ``register_cliffhanger``.
        """
        self.hype_moments.append(
            HypeMoment(
                chapter_no=int(chapter_no),
                hype_type=hype_type,
                recipe_key=str(recipe_key) if recipe_key else None,
                intensity=float(intensity),
            )
        )

    def register_title(self, title: str, chapter_no: int = 0) -> None:
        title = (title or "").strip()
        if not title:
            return
        self.titles_used.append(title)
        if chapter_no > 0:
            for gram in extract_title_ngrams(title):
                self.title_patterns[gram] = max(
                    self.title_patterns.get(gram, 0), chapter_no
                )
            # Prune oldest entries to cap unbounded JSONB growth.
            if len(self.title_patterns) > TITLE_PATTERN_HISTORY_MAX:
                sorted_items = sorted(
                    self.title_patterns.items(), key=lambda kv: kv[1]
                )
                keep = sorted_items[len(sorted_items) - TITLE_PATTERN_HISTORY_MAX :]
                self.title_patterns = dict(keep)

    def title_pattern_cooldown_violations(
        self,
        candidate: str,
        current_chapter: int,
        *,
        cooldown_chapters: int = DEFAULT_TITLE_COOLDOWN_CHAPTERS,
    ) -> list[str]:
        """Return n-grams from ``candidate`` still within the cooldown window.

        An empty list means the candidate title is safe to use.  A non-empty
        list names the specific n-grams that have been seen within the last
        ``cooldown_chapters`` chapters and should trigger a title regen.

        Parameters
        ----------
        candidate
            The proposed chapter title to check.
        current_chapter
            The chapter number being planned (used to compute recency).
        cooldown_chapters
            A pattern is considered "in cooldown" if it last appeared in
            chapter ``>= current_chapter - cooldown_chapters``.
        """
        if not candidate or cooldown_chapters <= 0:
            return []
        threshold = current_chapter - cooldown_chapters
        violations: list[str] = []
        for gram in extract_title_ngrams(candidate):
            last_seen = self.title_patterns.get(gram)
            if last_seen is not None and last_seen >= threshold:
                violations.append(gram)
        return violations

    def register_vocab(
        self, chapter_no: int, text: str, language: str | None
    ) -> None:
        """Tokenize ``text`` and record per-chapter frequency.

        Pruning keeps at most ``VOCAB_HISTORY_MAX_CHAPTERS`` chapters in
        memory / JSONB.
        """

        key = str(int(chapter_no))
        tokens = extract_tokens(text or "", language)
        counts: Counter[str] = Counter(tokens)
        self.vocab_freq[key] = dict(counts)

        if len(self.vocab_freq) > VOCAB_HISTORY_MAX_CHAPTERS:
            ordered = sorted(self.vocab_freq.keys(), key=_chapter_key)
            overflow = len(self.vocab_freq) - VOCAB_HISTORY_MAX_CHAPTERS
            for old_key in ordered[:overflow]:
                del self.vocab_freq[old_key]

    def register_chapter(
        self,
        chapter_no: int,
        *,
        opening: OpeningArchetype | None = None,
        cliffhanger: CliffhangerType | None = None,
        title: str | None = None,
        text: str | None = None,
        language: str | None = None,
        hype_type: HypeType | None = None,
        hype_recipe_key: str | None = None,
        hype_intensity: float | None = None,
    ) -> None:
        """One-call registration after a chapter finalises.

        Any of the kwargs may be ``None``; only the provided ones are
        recorded. This is the primary API for pipeline code.
        """

        if opening is not None:
            self.register_opening(chapter_no, opening)
        if cliffhanger is not None:
            self.register_cliffhanger(chapter_no, cliffhanger)
        if title is not None:
            self.register_title(title, chapter_no)
        if text is not None:
            self.register_vocab(chapter_no, text, language)
        if hype_type is not None:
            self.register_hype_moment(
                chapter_no,
                hype_type,
                hype_recipe_key,
                hype_intensity if hype_intensity is not None else 0.0,
            )

    # -- serialization ---------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "openings_used": [
                {"chapter_no": u.chapter_no, "archetype": u.archetype.value}
                for u in self.openings_used
            ],
            "cliffhangers_used": [
                {"chapter_no": u.chapter_no, "kind": u.kind.value}
                for u in self.cliffhangers_used
            ],
            "titles_used": list(self.titles_used),
            "vocab_freq": {
                key: dict(counts) for key, counts in self.vocab_freq.items()
            },
            "hype_moments": [
                hype_moment_to_dict(m) for m in self.hype_moments
            ],
            "title_patterns": dict(self.title_patterns),
        }

    @classmethod
    def from_dict(
        cls, project_id: UUID, data: Mapping[str, Any] | None
    ) -> "DiversityBudget":
        """Rehydrate from JSONB; unknown enum values are dropped silently."""

        data = data or {}
        openings: list[OpeningUse] = []
        for row in data.get("openings_used") or []:
            try:
                openings.append(
                    OpeningUse(
                        chapter_no=int(row["chapter_no"]),
                        archetype=OpeningArchetype(row["archetype"]),
                    )
                )
            except (KeyError, ValueError, TypeError):
                logger.debug("Skipping malformed opening row: %r", row)
        cliffhangers: list[CliffhangerUse] = []
        for row in data.get("cliffhangers_used") or []:
            try:
                cliffhangers.append(
                    CliffhangerUse(
                        chapter_no=int(row["chapter_no"]),
                        kind=CliffhangerType(row["kind"]),
                    )
                )
            except (KeyError, ValueError, TypeError):
                logger.debug("Skipping malformed cliffhanger row: %r", row)
        titles = [str(t) for t in (data.get("titles_used") or []) if t]
        vocab_raw: Mapping[str, Any] = data.get("vocab_freq") or {}
        vocab: dict[str, dict[str, int]] = {}
        for key, counts in vocab_raw.items():
            if not isinstance(counts, Mapping):
                continue
            safe_counts: dict[str, int] = {}
            for word, count in counts.items():
                try:
                    safe_counts[str(word)] = int(count)
                except (TypeError, ValueError):
                    continue
            vocab[str(key)] = safe_counts
        hype_moments: list[HypeMoment] = []
        for row in data.get("hype_moments") or []:
            moment = hype_moment_from_dict(row)
            if moment is not None:
                hype_moments.append(moment)
        raw_patterns: Mapping[str, Any] = data.get("title_patterns") or {}
        title_patterns: dict[str, int] = {}
        for gram, last_ch in raw_patterns.items():
            try:
                title_patterns[str(gram)] = int(last_ch)
            except (TypeError, ValueError):
                continue
        return cls(
            project_id=project_id,
            openings_used=openings,
            cliffhangers_used=cliffhangers,
            titles_used=titles,
            vocab_freq=vocab,
            hype_moments=hype_moments,
            title_patterns=title_patterns,
        )


def _chapter_key(raw: str) -> int:
    """Sort key that treats str keys as integers when possible."""

    try:
        return int(raw)
    except (TypeError, ValueError):
        return 10**9


# ---------------------------------------------------------------------------
# Repository — async DB helpers.
# ---------------------------------------------------------------------------


async def load_diversity_budget(
    session: AsyncSession, project_id: UUID
) -> DiversityBudget:
    """Fetch the budget row for ``project_id``; return empty if missing."""

    result = await session.execute(
        select(DiversityBudgetModel).where(
            DiversityBudgetModel.project_id == project_id
        )
    )
    row = result.scalar_one_or_none()
    if row is None:
        return DiversityBudget(project_id=project_id)
    return DiversityBudget.from_dict(
        project_id,
        {
            "openings_used": row.openings_used,
            "cliffhangers_used": row.cliffhangers_used,
            "titles_used": row.titles_used,
            "vocab_freq": row.vocab_freq,
            "hype_moments": getattr(row, "hype_moments", None) or [],
            "title_patterns": getattr(row, "title_patterns", None) or {},
        },
    )


async def save_diversity_budget(
    session: AsyncSession, budget: DiversityBudget
) -> None:
    """Upsert ``budget`` into ``diversity_budgets`` (project_id is the PK)."""

    payload = budget.to_dict()
    values: dict[str, Any] = {
        "project_id": budget.project_id,
        "openings_used": payload["openings_used"],
        "cliffhangers_used": payload["cliffhangers_used"],
        "titles_used": payload["titles_used"],
        "vocab_freq": payload["vocab_freq"],
    }
    update_cols: dict[str, Any] = {}
    if hasattr(DiversityBudgetModel, "hype_moments"):
        values["hype_moments"] = payload["hype_moments"]
    if hasattr(DiversityBudgetModel, "title_patterns"):
        values["title_patterns"] = payload["title_patterns"]
    stmt = pg_insert(DiversityBudgetModel).values(**values)
    update_cols = {
        "openings_used": stmt.excluded.openings_used,
        "cliffhangers_used": stmt.excluded.cliffhangers_used,
        "titles_used": stmt.excluded.titles_used,
        "vocab_freq": stmt.excluded.vocab_freq,
    }
    if hasattr(DiversityBudgetModel, "hype_moments"):
        update_cols["hype_moments"] = stmt.excluded.hype_moments
    if hasattr(DiversityBudgetModel, "title_patterns"):
        update_cols["title_patterns"] = stmt.excluded.title_patterns
    stmt = stmt.on_conflict_do_update(
        index_elements=["project_id"],
        set_=update_cols,
    )
    await session.execute(stmt)


# ---------------------------------------------------------------------------
# Prompt block rendering — consumed by the scene prompt builder.
# ---------------------------------------------------------------------------


def render_budget_diversity_block(
    budget: "DiversityBudget | None",
    *,
    language: str | None,
    is_chapter_opener: bool = False,
    is_chapter_closer: bool = False,
    opening_window: int = 3,
    cliffhanger_window: int = 3,
    vocab_window: int = DEFAULT_HOT_VOCAB_WINDOW,
    vocab_top: int = DEFAULT_HOT_VOCAB_TOP_N,
    vocab_min_count: int = DEFAULT_HOT_VOCAB_MIN_COUNT,
) -> str | None:
    """Render a prompt block from structured ``DiversityBudget`` signals.

    Complementary (not a replacement) to the ``deduplication.py`` heuristic
    blocks: those surface raw text; this surfaces the project's structured
    rotation state (hot vocab, opening archetype enum, cliffhanger enum).

    Returns ``None`` when there is nothing useful to say so callers can
    skip injection without growing the context budget.

    Parameters
    ----------
    budget
        Project budget, loaded via ``load_diversity_budget``. ``None`` or
        fully-empty budget → return ``None``.
    language
        Project language code. Controls prose language of the rendered
        block. Anything starting with ``"zh"`` → Chinese; otherwise English.
    is_chapter_opener
        True when this scene is the chapter opener (scene 1). Opening
        archetype guidance is only relevant at the opening; injecting it
        elsewhere wastes context tokens.
    is_chapter_closer
        True when this scene is the final scene of the chapter.
        Cliffhanger guidance is targeted at the closer only.
    opening_window, cliffhanger_window
        Show the last N archetypes to avoid. Must match the invariant
        policy the L5 rotation checks use, or the prompt and the gate
        disagree.
    vocab_window, vocab_top, vocab_min_count
        Forwarded to ``DiversityBudget.hot_vocab``.
    """

    if budget is None:
        return None

    hot = budget.hot_vocab(
        window=vocab_window,
        top=vocab_top,
        min_count=vocab_min_count,
    )
    recent_openings = (
        budget.recent_openings(opening_window) if is_chapter_opener else ()
    )
    recent_cliffhangers = (
        budget.recent_cliffhangers(cliffhanger_window)
        if is_chapter_closer
        else ()
    )

    if not hot and not recent_openings and not recent_cliffhangers:
        return None

    is_zh = not str(language or "").lower().startswith("en")

    lines: list[str] = []
    if is_zh:
        lines.append("【多样性预算 — 本章必须避开以下重复】")
        if hot:
            lines.append(
                "· 禁用高频词（近 "
                f"{vocab_window} 章累计出现 ≥ {vocab_min_count} 次，请用同义替换）："
                + "、".join(hot)
            )
        if recent_openings:
            names = "、".join(a.value for a in recent_openings)
            lines.append(
                f"· 近期开篇原型（本章开场不得再用）：{names}"
            )
        if recent_cliffhangers:
            names = "、".join(c.value for c in recent_cliffhangers)
            lines.append(
                f"· 近期章末悬念类型（本章结尾不得再用）：{names}"
            )
    else:
        lines.append("[DIVERSITY BUDGET — this chapter MUST avoid the following]")
        if hot:
            lines.append(
                f"- Banned hot vocab (last {vocab_window} chapters, count ≥ "
                f"{vocab_min_count} — substitute with synonyms): "
                + ", ".join(hot)
            )
        if recent_openings:
            names = ", ".join(a.value for a in recent_openings)
            lines.append(
                f"- Recent opening archetypes (do NOT reuse for this opening): {names}"
            )
        if recent_cliffhangers:
            names = ", ".join(c.value for c in recent_cliffhangers)
            lines.append(
                f"- Recent cliffhanger kinds (do NOT reuse at chapter end): {names}"
            )

    return "\n".join(lines)


__all__ = [
    "DEFAULT_HOT_VOCAB_MIN_COUNT",
    "DEFAULT_HOT_VOCAB_TOP_N",
    "DEFAULT_HOT_VOCAB_WINDOW",
    "DEFAULT_TITLE_COOLDOWN_CHAPTERS",
    "DiversityBudget",
    "OpeningUse",
    "CliffhangerUse",
    "VOCAB_HISTORY_MAX_CHAPTERS",
    "extract_title_ngrams",
    "extract_tokens",
    "load_diversity_budget",
    "render_budget_diversity_block",
    "save_diversity_budget",
]

# Re-export hype primitives so call-sites that only touch the budget layer
# don't need to know about the engine internals.
__all__.extend(["HypeMoment", "HypeType"])
