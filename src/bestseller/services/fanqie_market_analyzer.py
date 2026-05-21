"""Deterministic Fanqie market profile builders."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from statistics import median

from bestseller.domain.fanqie_market import (
    FanqieCategoryProfile,
    FanqieCompetitorProfile,
    FanqieCraftProfile,
    FanqieMarketAnalysisBundle,
    FanqieRankingBook,
    FanqieRankingSnapshot,
)


def build_competitor_profile(book: FanqieRankingBook) -> FanqieCompetitorProfile:
    """Build a book-level competitor profile from ranking metadata."""

    text = " ".join([book.title, book.intro, " ".join(book.tags), book.category])
    hook_patterns = _infer_hook_patterns(text)
    structure_patterns = _infer_structure_patterns(text)
    style_signals = _infer_style_signals(book)
    confidence = _confidence_for_book(book)
    return FanqieCompetitorProfile(
        source_book_id=book.source_book_id,
        title=book.title,
        author=book.author,
        category=book.category,
        board_type=book.board_type,
        rank=book.rank,
        reader_count=book.reader_count,
        premise_signals=_infer_premise_signals(text),
        setting_signals=_infer_setting_signals(text),
        protagonist_signals=_infer_protagonist_signals(text),
        conflict_signals=_infer_conflict_signals(text),
        hook_patterns=hook_patterns,
        structure_patterns=structure_patterns,
        writing_style_signals=style_signals,
        evidence=[_evidence_line(book)],
        anti_copy_constraints=[
            "Do not reuse the title, author voice, named characters, "
            f"or exclusive setting of {book.title}."
        ],
        confidence=confidence,
        raw_refs={"source_book_id": book.source_book_id, "rank": book.rank},
    )


def build_market_analysis_bundle(
    snapshot: FanqieRankingSnapshot,
    *,
    competitor_limit: int | None = None,
) -> FanqieMarketAnalysisBundle:
    """Build the complete deterministic analysis bundle for one snapshot."""

    competitor_profiles = build_competitor_profiles(snapshot, limit=competitor_limit)
    category_profile = build_category_profile(snapshot, competitor_profiles)
    craft_profile = build_craft_profile(category_profile)
    return FanqieMarketAnalysisBundle(
        snapshot=snapshot,
        competitor_profiles=competitor_profiles,
        category_profile=category_profile,
        craft_profile=craft_profile,
    )


def build_competitor_profiles(
    snapshot: FanqieRankingSnapshot,
    *,
    limit: int | None = None,
) -> list[FanqieCompetitorProfile]:
    """Build competitor profiles for a snapshot, ordered by rank."""

    books = snapshot.books[:limit] if limit else snapshot.books
    return [build_competitor_profile(book) for book in books]


def build_category_profile(
    snapshot: FanqieRankingSnapshot,
    profiles: list[FanqieCompetitorProfile] | None = None,
) -> FanqieCategoryProfile:
    """Aggregate book profiles into one category market profile."""

    resolved_profiles = profiles if profiles is not None else build_competitor_profiles(snapshot)
    reader_counts = [float(book.reader_count) for book in snapshot.books if book.reader_count >= 0]
    return FanqieCategoryProfile(
        category=snapshot.category or _dominant_category(snapshot),
        board_type=snapshot.board_type,
        channel=snapshot.channel,
        data_date=snapshot.data_date,
        sample_size=len(snapshot.books),
        reader_heat_stats=_reader_heat_stats(reader_counts),
        dominant_settings=_top_common(
            signal for profile in resolved_profiles for signal in profile.setting_signals
        ),
        protagonist_archetypes=_top_common(
            signal for profile in resolved_profiles for signal in profile.protagonist_signals
        ),
        hook_patterns=_top_common(
            signal for profile in resolved_profiles for signal in profile.hook_patterns
        ),
        structure_patterns=_top_common(
            signal for profile in resolved_profiles for signal in profile.structure_patterns
        ),
        payoff_patterns=_top_common(
            signal for profile in resolved_profiles for signal in profile.conflict_signals
        ),
        style_guidelines=_top_common(
            signal for profile in resolved_profiles for signal in profile.writing_style_signals
        ),
        safety_notes=[
            "Use category-level abstractions only; do not imitate a named "
            "living author's exact prose.",
            "Keep evidence rows linked to source snapshots for audit and recomputation.",
        ],
        evidence_profile_ids=[profile.source_book_id for profile in resolved_profiles],
        confidence=_category_confidence(snapshot, resolved_profiles),
    )


def build_craft_profile(category_profile: FanqieCategoryProfile) -> FanqieCraftProfile:
    """Compile a category profile into an anonymous prompt-ready craft card."""

    return FanqieCraftProfile(
        category=category_profile.category,
        board_type=category_profile.board_type,
        source_profile_ids=category_profile.evidence_profile_ids,
        allowed_style_principles=_dedupe(
            [
                *category_profile.style_guidelines,
                "Short paragraphs with fast causal turns.",
                "Dialogue should expose pressure, leverage, and consequence.",
            ]
        ),
        disallowed_copy_targets=[
            "No book-title reuse.",
            "No named-character reuse.",
            "No exact living-author prose imitation.",
            "No source-specific setting transplant without redesign.",
        ],
        hook_rules=_dedupe(
            [
                *category_profile.hook_patterns,
                "Open with a visible pressure event before explanation.",
            ]
        ),
        pacing_rules=_dedupe(
            [
                *category_profile.payoff_patterns,
                "Every chapter should convert pressure into a small gain, loss, or reveal.",
            ]
        ),
        structure_rules=_dedupe(
            [
                *category_profile.structure_patterns,
                "Core mechanism must produce repeatable conflict, cost, and feedback.",
            ]
        ),
        sentence_style="Lean, concrete, low exposition; prefer action and dialogue over essaying.",
        paragraph_style="Short blocks; each paragraph changes pressure, knowledge, or leverage.",
        dialogue_ratio_hint=0.45,
        safety_boundary=(
            "Use anonymous craft mechanics and category rhythms, not source-specific prose."
        ),
        confidence=category_profile.confidence,
    )


def _infer_premise_signals(text: str) -> list[str]:
    return _dedupe(
        [
            *_keyword_labels(
                text,
                {
                    "high_concept_mechanism": ("系统", "面板", "游戏", "时停", "规则", "改运"),
                    "public_reversal": ("打脸", "曝光", "认罪", "自爆", "翻车", "反击"),
                    "resource_wish": ("每天", "六千万", "花钱", "奖励", "到账"),
                    "state_cooperation": ("上交", "国家", "官方", "组织"),
                    "longevity_or_time": ("长生", "万古", "时停", "轮回"),
                },
            )
        ]
    )


def _infer_setting_signals(text: str) -> list[str]:
    return _dedupe(
        _keyword_labels(
            text,
            {
                "urban_power": ("都市高武", "高武", "异能", "精神病院", "邪神"),
                "xuanhuan_brainhole": ("玄幻脑洞", "修仙", "成仙", "长生", "万古"),
                "suspense_case": ("破案", "警花", "悬疑", "真相", "案"),
                "wealth_or_workplace": ("县城", "豪门", "名媛", "公司", "妇医"),
                "animal_or_farm": ("农场", "动物园", "动物"),
            },
        )
    )


def _infer_protagonist_signals(text: str) -> list[str]:
    return _dedupe(
        _keyword_labels(
            text,
            {
                "system_holder": ("系统", "面板", "技能", "解锁"),
                "underdog_counterattacker": ("废材", "背锅", "未遂", "开局", "反击"),
                "professional_solver": ("医生", "警", "破案", "妇医", "专家"),
                "cautious_survivor": ("苟", "长生", "万古", "低调"),
                "institution_builder": ("国家", "上交", "组织", "官方"),
            },
        )
    )


def _infer_conflict_signals(text: str) -> list[str]:
    return _dedupe(
        _keyword_labels(
            text,
            {
                "public_exposure_payoff": ("曝光", "自爆", "认罪", "真相", "打脸"),
                "ability_feedback_payoff": ("解锁", "到账", "升级", "成神", "成仙"),
                "case_resolution_payoff": ("破案", "证据", "真相", "警"),
                "survival_pressure": ("邪神", "诡异", "杀", "危机", "末日"),
                "status_counterattack": ("废材", "豪门", "名媛", "背锅", "跳楼"),
            },
        )
    )


def _infer_hook_patterns(text: str) -> list[str]:
    patterns = _keyword_labels(
        text,
        {
            "title_as_mechanism": ("系统", "时停", "每天", "开局", "改运", "挖矿"),
            "opening_crisis_first": ("跳楼", "邪神", "开局", "背锅", "查出"),
            "impossible_promise": ("只能", "不对劲", "也得", "万古", "成神"),
            "public_identity_pressure": ("名媛", "警花", "国家", "豪门", "妇医"),
        },
    )
    return _dedupe(patterns or ["clear commercial hook in title or first conflict"])


def _infer_structure_patterns(text: str) -> list[str]:
    patterns = _keyword_labels(
        text,
        {
            "repeatable_mechanism_loop": ("系统", "面板", "挖矿", "每天", "改运"),
            "threat_escalation_ladder": ("邪神", "诡异", "危机", "杀", "末日"),
            "case_unit_chain": ("破案", "证据", "真相", "案"),
            "cultivation_accumulation": ("修仙", "成仙", "长生", "万古", "剑", "灵墟"),
            "public_reversal_chain": ("打脸", "曝光", "自爆", "豪门", "名媛"),
        },
    )
    return _dedupe(patterns or ["pressure -> action -> feedback loop"])


def _infer_style_signals(book: FanqieRankingBook) -> list[str]:
    signals = ["platform_fast_reading"]
    if len(book.title) <= 18:
        signals.append("high_density_title")
    if book.intro and len(book.intro) < 260:
        signals.append("compressed_premise_intro")
    if book.tags:
        signals.append("genre_tags_must_be_visible")
    return signals


def _keyword_labels(text: str, label_terms: dict[str, tuple[str, ...]]) -> list[str]:
    return [label for label, terms in label_terms.items() if any(term in text for term in terms)]


def _reader_heat_stats(reader_counts: list[float]) -> dict[str, float]:
    if not reader_counts:
        return {"max": 0.0, "avg": 0.0, "median": 0.0, "min": 0.0}
    return {
        "max": max(reader_counts),
        "avg": sum(reader_counts) / len(reader_counts),
        "median": float(median(reader_counts)),
        "min": min(reader_counts),
    }


def _category_confidence(
    snapshot: FanqieRankingSnapshot,
    profiles: list[FanqieCompetitorProfile],
) -> float:
    if not profiles:
        return 0.0
    coverage = min(1.0, len(profiles) / max(5, len(snapshot.books)))
    evidence = sum(profile.confidence for profile in profiles) / len(profiles)
    return round((coverage * 0.4) + (evidence * 0.6), 3)


def _confidence_for_book(book: FanqieRankingBook) -> float:
    score = 0.35
    if book.title:
        score += 0.15
    if book.tags:
        score += 0.15
    if book.intro:
        score += 0.2
    if book.reader_count > 0:
        score += 0.1
    if book.category:
        score += 0.05
    return min(round(score, 3), 0.95)


def _evidence_line(book: FanqieRankingBook) -> str:
    readers = f", readers={book.reader_count}" if book.reader_count else ""
    return f"rank={book.rank}, title={book.title}, category={book.category}{readers}"


def _dominant_category(snapshot: FanqieRankingSnapshot) -> str:
    categories = [book.category for book in snapshot.books if book.category]
    return Counter(categories).most_common(1)[0][0] if categories else "unknown"


def _top_common(items: Iterable[str], *, limit: int = 8) -> list[str]:
    counter = Counter(str(item) for item in items if str(item).strip())
    return [item for item, _count in counter.most_common(limit)]


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        stripped = item.strip()
        if stripped and stripped not in seen:
            seen.add(stripped)
            result.append(stripped)
    return result
