"""Deterministic market analysis: genre heat, title checks, blurb benchmark.

Pure functions over normalized observations — no IO, no LLM — so every number
in the report can be recomputed from the persisted evidence.
"""

# ruff: noqa: RUF001 — Chinese market vocabulary is intentional.
from __future__ import annotations

from collections.abc import Mapping, Sequence
import re
import statistics
from typing import Any

from bestseller.domain.market_validation import (
    BlurbBenchmarkSection,
    BlurbShape,
    GenreHeatSection,
    MarketBookObservation,
    MarketCategoryRef,
    MarketSectionStatus,
    TitleCheckFinding,
    TitleCheckSection,
    TitleShellStats,
)
from bestseller.services.market_validation.config import (
    TitleCheckConfig,
    TitleShellRule,
)

_TITLE_NOISE_RE = re.compile(r"[\s，。！？!?：:·、【】《》（）()\[\]\-—…~·]+")
_SENTENCE_SPLIT_RE = re.compile(r"[。！？!?；;\n]+")
_COLON_RE = re.compile(r"[：:]")


def normalize_title(title: str) -> str:
    return _TITLE_NOISE_RE.sub("", (title or "").strip()).lower()


def title_distance(a: str, b: str) -> int:
    """Levenshtein distance between normalized titles."""

    left, right = normalize_title(a), normalize_title(b)
    if left == right:
        return 0
    if not left or not right:
        return max(len(left), len(right))
    previous = list(range(len(right) + 1))
    for i, ch_a in enumerate(left, start=1):
        current = [i]
        for j, ch_b in enumerate(right, start=1):
            cost = 0 if ch_a == ch_b else 1
            current.append(
                min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + cost)
            )
        previous = current
    return previous[-1]


#: Calibrated 2026-08-10 on the live boards: 36 distinct real books across
#: 东方玄幻 / 都市异能 / 仙侠, all 630 pairwise overlaps computed. Two DIFFERENT
#: books on the same board peak at 0.080 (p99 0.056, median 0.012). The bar sits
#: at ~2x that ceiling, so a hit means "overlaps one specific on-board book far
#: more than any two distinct on-board books overlap each other" — a statement
#: that stands on the negative distribution alone.
#:
#: The positive side is only weakly sampled (same-book paraphrases score 0.49+,
#: but those share literal text). The consequence is deliberately soft — a hit
#: DEMOTES an idea, never rejects it — so an over-eager threshold costs at most
#: a reordering among diverse alternatives.
MARKET_COLLISION_THRESHOLD = 0.15


def _content_bigrams(text: str) -> set[str]:
    """CJK character bigrams — the same primitive the outline dedup uses."""

    runs = re.findall(r"[一-鿿]{2,}", text or "")
    grams: set[str] = set()
    for run in runs:
        for i in range(len(run) - 1):
            grams.add(run[i : i + 2])
    return grams


def concept_market_overlap(concept: str, competitor_text: str) -> float:
    """Deterministic overlap between our concept and one on-board book.

    Character-bigram Jaccard, the same primitive already used for outline event
    dedup and title dedup. Deliberately NOT an LLM call: this runs over the
    whole raw-idea pool on every book, and the tournament already spends its
    LLM budget on judging.
    """

    ours = _content_bigrams(concept)
    theirs = _content_bigrams(competitor_text)
    if not ours or not theirs:
        return 0.0
    return len(ours & theirs) / len(ours | theirs)


def concept_market_collisions(
    concept: str,
    competitors: Sequence[Mapping[str, Any]],
    *,
    threshold: float = MARKET_COLLISION_THRESHOLD,
    limit: int = 3,
) -> list[tuple[str, float]]:
    """On-board books our concept is a near-duplicate of, strongest first.

    Answers the first question of the documented validation process — *does the
    market already have this book?* — at the only point where the answer can
    still change anything: before the tournament spends an expansion slot on it.

    The competitor rows never reach a prompt. That is the whole reason this is a
    deterministic screen and not a prompt block: quoting rival premises at the
    generator is precisely how the framework seeded its own recurring motifs
    (see ``concept_tournament.render_cliche_avoidance_block``, which refuses to
    quote its own ban bank for the same reason).
    """

    scored: list[tuple[str, float]] = []
    for row in competitors or ():
        if not isinstance(row, Mapping):
            continue
        title = str(row.get("title") or "").strip()
        tags = " ".join(str(t) for t in (row.get("tags") or []))
        blob = f"{title} {row.get('intro') or ''} {tags}"
        score = concept_market_overlap(concept, blob)
        if score >= threshold:
            scored.append((title or "(untitled)", round(score, 4)))
    scored.sort(key=lambda item: -item[1])
    return scored[:limit]


def extract_title_shell(
    title: str, shells: tuple[TitleShellRule, ...]
) -> TitleShellRule | None:
    """Return the first shell rule matching the title, if any."""

    stripped = (title or "").strip()
    if not stripped:
        return None
    for rule in shells:
        try:
            if re.search(rule.pattern, stripped):
                return rule
        except re.error:
            continue
    return None


def _percentile(sorted_values: list[int], fraction: float) -> int:
    if not sorted_values:
        return 0
    index = min(
        len(sorted_values) - 1, max(0, round(fraction * (len(sorted_values) - 1)))
    )
    return int(sorted_values[index])


def build_genre_heat(
    observations: list[MarketBookObservation],
    categories: list[MarketCategoryRef],
    *,
    min_sample: int,
    top_books: int,
) -> GenreHeatSection:
    if not categories:
        return GenreHeatSection(
            status=MarketSectionStatus.SKIPPED,
            reason="题材未映射到任何平台分类",
        )
    if not observations:
        return GenreHeatSection(
            status=MarketSectionStatus.DEGRADED,
            reason="平台数据不可用或样本为空",
            categories=categories,
        )

    heats = sorted(book.heat for book in observations)
    deltas = [book.heat_delta for book in observations if book.heat_delta is not None]
    rising_share = (
        sum(1 for delta in deltas if delta > 0) / len(deltas) if deltas else 0.0
    )
    new_entry_share = sum(1 for book in observations if book.is_new_entry) / len(
        observations
    )
    status = MarketSectionStatus.OK
    reason = ""
    if len(observations) < min_sample:
        status = MarketSectionStatus.DEGRADED
        reason = f"样本过薄（{len(observations)} < {min_sample}），结论仅供参考"

    ranked = sorted(observations, key=lambda book: book.heat, reverse=True)
    return GenreHeatSection(
        status=status,
        reason=reason,
        categories=categories,
        sample_size=len(observations),
        heat_p10=_percentile(heats, 0.1),
        heat_p50=_percentile(heats, 0.5),
        heat_p90=_percentile(heats, 0.9),
        new_entry_share=new_entry_share,
        rising_share=rising_share,
        top_books=ranked[:top_books],
    )


def _shell_stats(
    rule: TitleShellRule,
    candidate: str,
    board_books: list[MarketBookObservation],
) -> TitleShellStats:
    candidate_normalized = normalize_title(candidate)
    matches = [
        book
        for book in board_books
        if normalize_title(book.title) != candidate_normalized
        and extract_title_shell(book.title, (rule,)) is not None
    ]
    heats = sorted(book.heat for book in matches)
    return TitleShellStats(
        shell_pattern=rule.name,
        board_count=len(matches),
        heat_median=int(statistics.median(heats)) if heats else 0,
        example_titles=[book.title for book in matches[:5]],
    )


def check_titles(
    candidates: list[str] | tuple[str, ...],
    board_books: list[MarketBookObservation],
    web_hits: dict[str, list[str]],
    config: TitleCheckConfig,
) -> TitleCheckSection:
    cleaned = [title.strip() for title in candidates if str(title).strip()]
    if not cleaned:
        return TitleCheckSection(
            status=MarketSectionStatus.SKIPPED, reason="没有候选书名"
        )

    findings: list[TitleCheckFinding] = []
    for candidate in cleaned:
        exact_hits: list[str] = []
        near_hits: list[str] = []
        candidate_normalized = normalize_title(candidate)
        for book in board_books:
            board_normalized = normalize_title(book.title)
            if not board_normalized:
                continue
            if board_normalized == candidate_normalized:
                exact_hits.append(f"{book.platform}:{book.title}")
                continue
            distance = title_distance(candidate, book.title)
            contains = (
                min(len(candidate_normalized), len(board_normalized))
                >= config.core_token_min_len + 1
                and (
                    candidate_normalized in board_normalized
                    or board_normalized in candidate_normalized
                )
            )
            if distance <= config.near_distance_max or contains:
                near_hits.append(f"{book.platform}:{book.title}")

        shell_rule = extract_title_shell(candidate, config.shells)
        shell = (
            _shell_stats(shell_rule, candidate, board_books) if shell_rule else None
        )
        candidate_web_hits = list(web_hits.get(candidate, ()))

        reasons: list[str] = []
        verdict = "pass"
        shell_verdict_relevant = (
            shell is not None
            and shell_rule is not None
            and not getattr(shell_rule, "advisory_only", False)
            and shell.board_count >= config.shell_crowd_min_books
        )
        if exact_hits:
            verdict = "fail"
            reasons.append(f"榜单同名：{exact_hits[:3]}")
        elif shell_verdict_relevant and shell is not None:
            if shell.heat_median < config.shell_weak_heat_median:
                verdict = "fail"
                reasons.append(
                    f"同壳「{shell.shell_pattern}」拥挤且流量差"
                    f"（{shell.board_count} 本，热度中位数 {shell.heat_median}）——壳已废，须换壳"
                )
            else:
                verdict = "caution"
                reasons.append(
                    f"同壳「{shell.shell_pattern}」拥挤但仍有流量"
                    f"（{shell.board_count} 本，热度中位数 {shell.heat_median}）——需极端差异化"
                )
        if verdict == "pass" and (near_hits or candidate_web_hits):
            verdict = "caution"
            if near_hits:
                reasons.append(f"榜单近名：{near_hits[:3]}")
            if candidate_web_hits:
                reasons.append("站内检索发现近似占用")

        findings.append(
            TitleCheckFinding(
                candidate=candidate,
                exact_hits=exact_hits,
                near_hits=near_hits,
                web_hits=candidate_web_hits,
                shell=shell,
                verdict=verdict,
                reasons=reasons,
            )
        )

    board_lengths = sorted(
        len(normalize_title(book.title)) for book in board_books if book.title.strip()
    )
    colon_share = (
        sum(1 for book in board_books if _COLON_RE.search(book.title)) / len(board_books)
        if board_books
        else 0.0
    )
    return TitleCheckSection(
        status=MarketSectionStatus.OK,
        findings=findings,
        board_title_length_p50=_percentile(board_lengths, 0.5),
        board_title_colon_share=colon_share,
    )


def _blurb_shape(text: str) -> BlurbShape:
    stripped = (text or "").strip()
    sentences = [part for part in _SENTENCE_SPLIT_RE.split(stripped) if part.strip()]
    return BlurbShape(
        char_count=len(stripped),
        sentence_count=len(sentences),
        first_sentence=sentences[0][:200] if sentences else "",
        has_tag_prefix=stripped.startswith("【"),
    )


def benchmark_blurb(
    blurb: str,
    board_intros: list[str],
    *,
    min_board_samples: int,
) -> BlurbBenchmarkSection:
    if not (blurb or "").strip():
        return BlurbBenchmarkSection(
            status=MarketSectionStatus.SKIPPED, reason="没有简介输入"
        )
    usable = [intro for intro in board_intros if (intro or "").strip()]
    ours = _blurb_shape(blurb)
    if len(usable) < min_board_samples:
        return BlurbBenchmarkSection(
            status=MarketSectionStatus.DEGRADED,
            reason=f"榜单简介样本过薄（{len(usable)} < {min_board_samples}）",
            ours=ours,
        )

    shapes = [_blurb_shape(intro) for intro in usable]
    board_median = BlurbShape(
        char_count=int(statistics.median(shape.char_count for shape in shapes)),
        sentence_count=int(statistics.median(shape.sentence_count for shape in shapes)),
        first_sentence="",
        has_tag_prefix=(
            sum(1 for shape in shapes if shape.has_tag_prefix) / len(shapes) > 0.5
        ),
    )

    warnings: list[str] = []
    if board_median.char_count > 0:
        ratio = ours.char_count / board_median.char_count
        if ratio > 2.0 or ratio < 0.4:
            warnings.append(
                f"简介长度 {ours.char_count} 字与榜单中位 {board_median.char_count} 字偏离过大"
            )
    if board_median.sentence_count > 0:
        sentence_ratio = ours.sentence_count / board_median.sentence_count
        if sentence_ratio > 2.5:
            warnings.append(
                f"句子数 {ours.sentence_count} 远超榜单中位 {board_median.sentence_count}，疑似碎句"
            )
    if board_median.has_tag_prefix and not ours.has_tag_prefix:
        warnings.append("该分类榜单简介普遍带【标签】前缀，我们没有")

    return BlurbBenchmarkSection(
        status=MarketSectionStatus.OK,
        ours=ours,
        board_median=board_median,
        warnings=warnings,
    )
