from __future__ import annotations

# ruff: noqa: RUF001, ANN401
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
import yaml

from bestseller.infra.db.models import ChapterDraftVersionModel, ChapterModel, ProjectModel


@dataclass(frozen=True)
class NgramUsageStats:
    ngram: str
    total_count: int
    chapters_seen: tuple[int, ...]
    last_chapter: int


@dataclass(frozen=True)
class NgramOveruseReport:
    overused: tuple[NgramUsageStats, ...]
    rising: tuple[NgramUsageStats, ...]
    safe_count: int


_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_MARKDOWN_RE = re.compile(r"^#{1,6}\s+|[*_`>#\[\]()]")


async def compute_ngram_overuse(
    session: AsyncSession,
    project: ProjectModel,
    *,
    chapter_number_upto: int | None = None,
    min_ngram: int = 3,
    max_ngram: int = 5,
    hot_threshold: int = 8,
    rising_threshold: int = 4,
    exclude_proper_nouns: bool = True,
) -> NgramOveruseReport:
    rows = await _load_current_chapter_texts(
        session,
        project,
        chapter_number_upto=chapter_number_upto,
    )
    excluded = (
        _load_project_proper_nouns(Path("output") / str(project.slug or ""))
        if exclude_proper_nouns
        else frozenset()
    )
    return compute_ngram_overuse_from_chapters(
        rows,
        min_ngram=min_ngram,
        max_ngram=max_ngram,
        hot_threshold=hot_threshold,
        rising_threshold=rising_threshold,
        excluded_ngrams=excluded,
    )


async def _load_current_chapter_texts(
    session: AsyncSession,
    project: ProjectModel,
    *,
    chapter_number_upto: int | None,
) -> tuple[tuple[int, str], ...]:
    query = (
        select(ChapterModel.chapter_number, ChapterDraftVersionModel.content_md)
        .join(
            ChapterDraftVersionModel,
            ChapterDraftVersionModel.chapter_id == ChapterModel.id,
        )
        .where(
            ChapterModel.project_id == project.id,
            ChapterDraftVersionModel.is_current.is_(True),
        )
        .order_by(ChapterModel.chapter_number.asc())
    )
    if chapter_number_upto is not None:
        query = query.where(ChapterModel.chapter_number < int(chapter_number_upto))
    result = await session.execute(query)
    return tuple((int(ch), str(text or "")) for ch, text in result.all())


def compute_ngram_overuse_from_chapters(
    chapter_texts: Sequence[tuple[int, str]],
    *,
    min_ngram: int = 3,
    max_ngram: int = 5,
    hot_threshold: int = 8,
    rising_threshold: int = 4,
    excluded_ngrams: Iterable[str] = (),
) -> NgramOveruseReport:
    if not chapter_texts:
        return NgramOveruseReport(overused=(), rising=(), safe_count=0)

    excluded = {item for item in excluded_ngrams if item}
    total_counter: Counter[str] = Counter()
    chapter_counter: dict[int, Counter[str]] = {}
    seen_by_ngram: dict[str, set[int]] = defaultdict(set)

    for chapter_number, text in chapter_texts:
        grams = Counter(_iter_cjk_ngrams(text, min_ngram=min_ngram, max_ngram=max_ngram))
        for ngram in tuple(grams):
            if ngram in excluded:
                del grams[ngram]
        chapter_counter[int(chapter_number)] = grams
        total_counter.update(grams)
        for ngram in grams:
            seen_by_ngram[ngram].add(int(chapter_number))

    def _stats(item: tuple[str, int]) -> NgramUsageStats:
        ngram, count = item
        chapters_seen = tuple(sorted(seen_by_ngram.get(ngram, ())))
        return NgramUsageStats(
            ngram=ngram,
            total_count=int(count),
            chapters_seen=chapters_seen,
            last_chapter=max(chapters_seen) if chapters_seen else 0,
        )

    overused = tuple(
        _stats(item)
        for item in sorted(total_counter.items(), key=lambda kv: (-kv[1], kv[0]))
        if item[1] >= hot_threshold
    )
    recent_chapters = tuple(sorted(chapter_counter)[-5:])
    recent_counter: Counter[str] = Counter()
    for chapter_number in recent_chapters:
        recent_counter.update(chapter_counter[chapter_number])
    rising = tuple(
        _stats((ngram, total_counter[ngram]))
        for ngram, recent_count in sorted(
            recent_counter.items(), key=lambda kv: (-kv[1], kv[0])
        )
        if recent_count >= rising_threshold and total_counter[ngram] < hot_threshold
    )
    unsafe = {item.ngram for item in overused} | {item.ngram for item in rising}
    safe_count = sum(1 for ngram in total_counter if ngram not in unsafe)
    return NgramOveruseReport(overused=overused, rising=rising, safe_count=safe_count)


def render_ngram_avoidance_block(
    report: NgramOveruseReport,
    *,
    language: str = "zh-CN",
    max_overused: int = 12,
    max_rising: int = 12,
) -> str:
    """Render a writer-facing prompt block.

    Overlapping ngrams are collapsed: when a longer ngram is in the list,
    its strict substrings (which trivially share count) are dropped so the
    prompt doesn't get flooded with synonymous bans (e.g. "林渊盯" /
    "林渊盯着" / "林渊盯着铜" all describing the same overuse).
    """
    overused = _dedupe_overlapping_ngrams(report.overused)
    rising = _dedupe_overlapping_ngrams(report.rising)
    if not overused and not rising:
        return ""
    is_en = str(language or "").lower().startswith("en")
    if is_en:
        lines = [
            "[Cross-chapter repeated phrase ban]",
            "Avoid phrases that have appeared too often across the book.",
        ]
        if overused:
            lines.append("Banned:")
            lines.extend(
                f'  "{item.ngram}" ({item.total_count} uses) - recast the sentence.'
                for item in overused[:max_overused]
            )
        if rising:
            lines.append("Use sparingly:")
            lines.append(
                "  " + ", ".join(f'"{item.ngram}"' for item in rising[:max_rising])
            )
        return "\n".join(lines)

    lines = [
        "【全书重复词禁用清单（避免读者疲劳）】",
        "本书已经出现过太多次的固定搭配，本章必须改用其他写法表达同一意思：",
    ]
    if overused:
        lines.append("")
        lines.append("绝对禁止（已出现 ≥8 次）：")
        lines.extend(
            f'  "{item.ngram}" → 换动作、换句式或删掉重复描写（全书 {item.total_count} 次）'
            for item in overused[:max_overused]
        )
    if rising:
        lines.append("")
        lines.append("慎用（最近 5 章升温）：")
        lines.append(
            "  " + "、".join(f'"{item.ngram}"' for item in rising[:max_rising])
        )
    lines.append("")
    lines.append("写作时主动构造同义但用词不同的句子；必须换 ngram 形态。")
    return "\n".join(lines)


def _dedupe_overlapping_ngrams(
    items: Sequence[NgramUsageStats],
) -> tuple[NgramUsageStats, ...]:
    """Keep only the longest ngram in each overlap cluster.

    Two ngrams "overlap" when one is a strict substring of the other AND
    their ``total_count`` is identical (the substring's extra count comes
    from the same prose occurrences). When both conditions hold the shorter
    one is redundant for the writer prompt.

    When counts differ — e.g. "林渊" (50) vs "林渊盯着" (16) — both are
    kept because they describe distinct overuse problems.
    """
    if not items:
        return ()
    # Sort longest-first so the first time we see an ngram it's the maximal form.
    ordered = sorted(items, key=lambda u: (-len(u.ngram), -int(u.total_count)))
    kept: list[NgramUsageStats] = []
    for candidate in ordered:
        is_redundant = any(
            candidate.ngram != kept_item.ngram
            and candidate.ngram in kept_item.ngram
            and int(candidate.total_count) == int(kept_item.total_count)
            for kept_item in kept
        )
        if is_redundant:
            continue
        kept.append(candidate)
    # Restore original ranking order (by total_count desc, length desc as tie).
    kept.sort(key=lambda u: (-int(u.total_count), -len(u.ngram), u.ngram))
    return tuple(kept)


def _iter_cjk_ngrams(text: str, *, min_ngram: int, max_ngram: int) -> Iterable[str]:
    chars = _CJK_RE.findall(_MARKDOWN_RE.sub("", text or ""))
    lo = max(1, int(min_ngram))
    hi = max(lo, int(max_ngram))
    for size in range(lo, hi + 1):
        if len(chars) < size:
            continue
        for index in range(0, len(chars) - size + 1):
            yield "".join(chars[index : index + size])


def _load_project_proper_nouns(project_dir: Path) -> frozenset[str]:
    path = project_dir / "story-bible" / "canonical-terms.yaml"
    if not path.exists():
        return frozenset()
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return frozenset()
    terms: set[str] = set()
    _collect_terms(payload, terms)
    return frozenset(term for term in terms if len(term) >= 2)


def _collect_terms(value: Any, terms: set[str]) -> None:
    if isinstance(value, Mapping):
        category = str(value.get("category") or value.get("type") or "").lower()
        if category in {"character", "place", "location", "object"}:
            for key in ("term", "name", "canonical", "canonical_name"):
                raw = str(value.get(key) or "").strip()
                if raw:
                    terms.add(raw)
            aliases = value.get("aliases")
            if isinstance(aliases, list | tuple):
                terms.update(str(item).strip() for item in aliases if str(item).strip())
        for item in value.values():
            _collect_terms(item, terms)
    elif isinstance(value, list | tuple):
        for item in value:
            _collect_terms(item, terms)


__all__ = [
    "NgramOveruseReport",
    "NgramUsageStats",
    "compute_ngram_overuse",
    "compute_ngram_overuse_from_chapters",
    "render_ngram_avoidance_block",
]
