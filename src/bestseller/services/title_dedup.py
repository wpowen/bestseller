"""Title uniqueness and content-driven derivation for chapter outlines.

Three responsibilities:

1. **Dedup primitives**: exact and near-duplicate (character n-gram Jaccard)
   detection for chapter titles, so the same title cannot appear twice in
   one novel and "失衡" / "失控" style near-clones are caught.

2. **Content-driven derivation**: `derive_title_from_content` extracts a
   concrete 2-6 character noun phrase from a chapter's own
   ``main_conflict``/``hook_description``/``unique_beat``/``chapter_goal``
   so the planner fallback never needs to pick from a fixed pool indexed
   by chapter number.

3. **Collision reporting**: `TitleCollisionError` carries the conflicting
   chapter numbers so the repair loop in ``planner.py`` can produce
   targeted regeneration directives.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field


DEFAULT_NEAR_DUP_THRESHOLD = 0.7
DEFAULT_NGRAM_N = 2
TITLE_MIN_LEN = 2
TITLE_MAX_LEN = 6


class TitleCollisionError(Exception):
    """Raised when generated chapter titles collide with each other or with
    titles already persisted for the project.

    The ``collisions`` field carries enough information to produce a
    repair directive for each conflicting chapter without re-doing the
    detection work.
    """

    def __init__(
        self,
        message: str,
        *,
        collisions: list["TitleCollision"],
    ) -> None:
        super().__init__(message)
        self.collisions = collisions


@dataclass(frozen=True)
class TitleCollision:
    """A single (candidate, conflicting-prior) collision.

    Attributes
    ----------
    chapter_number:
        The chapter being generated that proposed a colliding title.
    candidate_title:
        The colliding title text the planner produced.
    conflict_title:
        The prior title (from earlier in this batch or from previously
        persisted chapters) that this candidate collides with.
    conflict_chapter_number:
        The chapter number of the prior title, when known. ``None`` when
        the conflict was a within-batch duplicate detected before each
        side had a stable chapter number.
    similarity:
        Jaccard similarity of the two titles. ``1.0`` for exact match,
        anything ``>= near_dup_threshold`` for near-duplicate.
    """

    chapter_number: int
    candidate_title: str
    conflict_title: str
    conflict_chapter_number: int | None
    similarity: float


@dataclass
class TitleDedupReport:
    """Summary of one dedup pass — useful for logging and tests."""

    accepted: list[str] = field(default_factory=list)
    collisions: list[TitleCollision] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.collisions


# ---------------------------------------------------------------------------
# n-gram similarity
# ---------------------------------------------------------------------------


def char_ngrams(text: str, n: int = DEFAULT_NGRAM_N) -> set[str]:
    """Return the set of character n-grams of ``text``.

    Whitespace is stripped; otherwise the input is used verbatim. CJK,
    Latin, and mixed text all work uniformly because we slice by
    character, not byte.
    """

    stripped = (text or "").strip()
    if not stripped:
        return set()
    if len(stripped) < n:
        return {stripped}
    return {stripped[i : i + n] for i in range(len(stripped) - n + 1)}


def jaccard_similarity(
    a: str, b: str, *, n: int = DEFAULT_NGRAM_N
) -> float:
    """Jaccard similarity over character n-grams.

    Returns 1.0 for identical strings, 0.0 when one side is empty,
    and a value in ``[0, 1]`` otherwise. Symmetric.
    """

    if a == b and a:
        return 1.0
    ga = char_ngrams(a, n)
    gb = char_ngrams(b, n)
    if not ga or not gb:
        return 0.0
    inter = len(ga & gb)
    union = len(ga | gb)
    if union == 0:
        return 0.0
    return inter / union


# ---------------------------------------------------------------------------
# collision detection
# ---------------------------------------------------------------------------


def find_title_collisions(
    candidates: Iterable[tuple[int, str]],
    *,
    existing_titles: Iterable[tuple[int | None, str]] = (),
    near_dup_threshold: float = DEFAULT_NEAR_DUP_THRESHOLD,
) -> TitleDedupReport:
    """Scan ``candidates`` for collisions against ``existing_titles`` and
    against each other.

    Parameters
    ----------
    candidates:
        Iterable of ``(chapter_number, title)`` pairs to check, typically
        the chapters in one freshly-generated volume outline.
    existing_titles:
        Iterable of ``(chapter_number_or_None, title)`` pairs already
        committed for this project. ``chapter_number`` is optional and
        only used to enrich collision reporting.
    near_dup_threshold:
        Two titles are considered near-duplicates when their n-gram
        Jaccard similarity is ``>= near_dup_threshold``. Set to ``1.0``
        to only catch exact matches.

    Returns
    -------
    A :class:`TitleDedupReport`. The ``ok`` property is true iff no
    collisions were found. When collisions exist, each one is reported
    once (deduplicating by candidate chapter so the repair loop gets a
    single actionable instruction per chapter).
    """

    if near_dup_threshold <= 0:
        # Caller asked to disable the check; trust the planner.
        return TitleDedupReport(accepted=[t for _, t in candidates])

    existing_pairs: list[tuple[int | None, str]] = [
        (cn, (t or "").strip()) for cn, t in existing_titles if (t or "").strip()
    ]

    accepted: list[str] = []
    seen_in_batch: list[tuple[int, str]] = []
    collisions: list[TitleCollision] = []
    reported_for_chapter: set[int] = set()

    for chapter_number, raw_title in candidates:
        title = (raw_title or "").strip()
        if not title:
            # Existence is enforced elsewhere; skip empties here.
            continue

        # Exact match vs existing — highest priority, cheapest check.
        collision = _find_collision_in_list(
            chapter_number,
            title,
            [(cn, t) for cn, t in existing_pairs],
            near_dup_threshold,
        )
        if collision is None:
            # Within-batch check
            collision = _find_collision_in_list(
                chapter_number,
                title,
                [(cn, t) for cn, t in seen_in_batch],
                near_dup_threshold,
            )

        if collision is not None and chapter_number not in reported_for_chapter:
            collisions.append(collision)
            reported_for_chapter.add(chapter_number)
            continue

        accepted.append(title)
        seen_in_batch.append((chapter_number, title))

    return TitleDedupReport(accepted=accepted, collisions=collisions)


def _find_collision_in_list(
    chapter_number: int,
    title: str,
    pool: list[tuple[int | None, str]],
    near_dup_threshold: float,
) -> TitleCollision | None:
    """Return the first collision of ``title`` against ``pool``, if any."""

    # Pass 1: exact match (cheap, deterministic).
    for prior_cn, prior_title in pool:
        if prior_title == title:
            return TitleCollision(
                chapter_number=chapter_number,
                candidate_title=title,
                conflict_title=prior_title,
                conflict_chapter_number=prior_cn,
                similarity=1.0,
            )

    # Pass 2: near-duplicate via Jaccard. Skip if threshold is 1.0.
    if near_dup_threshold >= 1.0:
        return None

    best: TitleCollision | None = None
    for prior_cn, prior_title in pool:
        sim = jaccard_similarity(title, prior_title)
        if sim >= near_dup_threshold and (best is None or sim > best.similarity):
            best = TitleCollision(
                chapter_number=chapter_number,
                candidate_title=title,
                conflict_title=prior_title,
                conflict_chapter_number=prior_cn,
                similarity=sim,
            )
    return best


# ---------------------------------------------------------------------------
# content-driven title derivation (fallback path)
# ---------------------------------------------------------------------------


# Words that are *always* too generic to use as a title — these are the
# patterns that bloomed across multiple existing books. The list is
# intentionally narrower than a "ban list" — it covers tokens that, when
# they appear as the *suffix* of a 2-character title, indicate a
# template-shaped output rather than a content-shaped one.
_GENERIC_SUFFIX_TOKENS_ZH: tuple[str, ...] = (
    "试探", "入局", "初现", "落子", "起手", "掀幕", "铺火", "破冰",
    "露锋", "投石", "拆解", "溯源", "揭层", "探针", "回查", "摸底",
    "寻隙", "织网", "破壁", "追索", "加压", "失衡", "绞杀", "窒息",
    "封锁", "逼近", "崩弦", "死线", "契机", "锁链", "加注", "试招",
    "试压", "逆鳞", "炸场", "再起", "收网", "下注", "开局",
)


def is_template_shaped_title(title: str) -> bool:
    """Heuristic: does ``title`` look like a "noun + function-suffix"
    template output rather than a content-derived phrase?

    Used as a hint when the planner repeats prior outputs; this is not a
    hard validation step on its own (we delegate that to the dedup pass
    and the prompt). Returns True for 4-character titles whose last 2
    characters are in the generic-suffix list.
    """

    if not title:
        return False
    s = title.strip()
    if len(s) == 4:
        for tok in _GENERIC_SUFFIX_TOKENS_ZH:
            if s.endswith(tok):
                return True
    return False


# Regex for spans we want to extract as candidate titles. Matches:
#  - bracketed proper nouns like "「青鸾令」" / "《阴阳道典》"
#  - *bounded* CJK noun-like runs (must have a non-CJK boundary on at
#    least one side, otherwise we'd happily slice the first 4 chars of
#    a long unpunctuated sentence — guaranteed to produce template-
#    shaped or particle-leading garbage like "一件小债" / "的软肋").
#  - Capitalized Latin proper noun runs.
_CJK_BOUNDED_NOUN_RE = re.compile(r"(?:^|[^一-鿿])([一-鿿]{2,6})(?=[^一-鿿]|$)")
_BRACKET_NOUN_RE = re.compile(r"[「『《【]([^」』》】]{2,8})[」』》】]")
_LATIN_NOUN_RE = re.compile(r"[A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+){0,2}")

# CJK characters that almost never start a usable title — particles,
# pronouns, conjunctions, and copulas. If extraction starts here, the
# fragment is a clipped phrase, not a noun.
_BAD_LEADING_CHARS: frozenset[str] = frozenset(
    "的了在是和与或但因所而从这那这那他她你我们之其也都已被把让让叫"
)


# Tokens that the planner often emits inside main_conflict / hook prose
# but which would make poor titles on their own (action verbs, vague
# narrative function words, throwaway connectors). We use this list to
# filter extraction candidates, *not* to ban LLM outputs.
_NON_TITLE_TOKENS: frozenset[str] = frozenset({
    "他", "她", "你", "我", "我们", "他们", "她们", "自己",
    "之前", "之后", "这里", "那里", "什么", "怎么", "为什么",
    "必须", "应该", "可以", "不能", "已经", "尚未",
    "推进", "继续", "开始", "结束", "完成", "失败", "成功",
    "but", "and", "the", "this", "that", "with", "from",
})


def derive_title_from_content(
    *,
    main_conflict: str | None = None,
    hook_description: str | None = None,
    unique_beat: str | None = None,
    chapter_goal: str | None = None,
    language: str | None = None,
    exclude: Iterable[str] = (),
) -> str | None:
    """Extract a 2-6 character concrete noun phrase from a chapter's own
    content fields, in priority order:

    1. ``unique_beat`` — typically the most distinct event description.
    2. ``main_conflict`` — what's actually happening this chapter.
    3. ``hook_description`` — the chapter-end pull-forward.
    4. ``chapter_goal`` — the protagonist's intent.

    For each source, we look for:
    - Quoted/bracketed proper nouns (``「青鸾令」``, ``《阴阳道典》``).
    - *Bounded* CJK noun-shaped runs of 2-6 characters: the run must
      have a non-CJK boundary on at least one side so we never slice
      the first N chars of an unpunctuated sentence.
    - Latin capitalized proper noun runs (for English projects).

    Parameters
    ----------
    exclude:
        Optional iterable of titles already used elsewhere in the same
        batch. Candidates equal to any value in ``exclude`` are skipped,
        so dedup-aware callers (notably
        :func:`_fallback_chapter_outline_batch`) get a different title
        per chapter even when the underlying content templates cycle.

    Returns
    -------
    A 2-6 character / 1-4 word title, or ``None`` when nothing usable
    could be extracted. The caller is expected to escalate (e.g., raise
    ``PlannerFallbackError``) rather than silently substitute a
    fixed-pool word — the whole point of this module is to prevent
    fixed-pool substitution.
    """

    is_english = (language or "").lower().startswith("en")
    sources = (unique_beat, main_conflict, hook_description, chapter_goal)
    exclude_set: set[str] = {s for s in exclude if isinstance(s, str) and s.strip()}

    for raw in sources:
        if not isinstance(raw, str):
            continue
        text = raw.strip()
        if not text:
            continue

        # 1) Bracketed proper nouns are always preferred.
        for m in _BRACKET_NOUN_RE.finditer(text):
            cand = m.group(1).strip()
            cand = _truncate_to_title_len(cand)
            if _is_acceptable_title_candidate(cand) and cand not in exclude_set:
                return cand

        # 2) Latin capitalized runs (for English projects).
        if is_english:
            for m in _LATIN_NOUN_RE.finditer(text):
                cand = m.group(0).strip()
                # Latin titles allow up to 3 words; collapse whitespace.
                cand = " ".join(cand.split())
                if _is_acceptable_latin_title(cand) and cand not in exclude_set:
                    return cand

        # 3) Bounded CJK noun runs — prefer 3-4 character runs, fall back to 2.
        # The regex captures only runs that touch a non-CJK boundary, so
        # an unpunctuated sentence yields zero matches (correct: there is
        # no extractable noun phrase, return None and try the next source).
        for desired_len in (4, 3, 5, 6, 2):
            for m in _CJK_BOUNDED_NOUN_RE.finditer(text):
                cand = m.group(1)
                if len(cand) != desired_len:
                    continue
                if _is_acceptable_title_candidate(cand) and cand not in exclude_set:
                    return cand

    return None


def _truncate_to_title_len(s: str) -> str:
    s = (s or "").strip()
    if len(s) > TITLE_MAX_LEN:
        return s[:TITLE_MAX_LEN]
    return s


def _is_acceptable_title_candidate(cand: str) -> bool:
    if not cand:
        return False
    if len(cand) < TITLE_MIN_LEN or len(cand) > TITLE_MAX_LEN:
        return False
    if cand in _NON_TITLE_TOKENS:
        return False
    if cand[0] in _BAD_LEADING_CHARS:
        # Phrases like "的软肋" / "在最深" / "了之后" mean the regex sliced
        # off a particle-leading fragment — never a real noun phrase.
        return False
    if is_template_shaped_title(cand):
        return False
    return True


def _is_acceptable_latin_title(cand: str) -> bool:
    if not cand:
        return False
    # 2-6 words for Latin, ~30 char cap.
    if not (1 <= len(cand.split()) <= 4):
        return False
    if len(cand) > 32:
        return False
    if cand.lower() in _NON_TITLE_TOKENS:
        return False
    return True
