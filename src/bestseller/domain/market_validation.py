"""Market validation domain contracts.

These models describe the market-facing validation report for a book concept:
genre heat, competitor scan, title dedup / shell crowding, blurb benchmark and
an advisory verdict. Evidence observations stay separate from judged
conclusions so every verdict line can be traced back to raw platform data.

The report is advisory by design: it never gates or blocks generation.
"""


from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class MarketSectionStatus(StrEnum):
    """Per-section execution status; sections degrade independently."""

    OK = "ok"
    DEGRADED = "degraded"
    SKIPPED = "skipped"


class MarketVerdictBand(StrEnum):
    """Advisory verdict bands. Never used as a hard gate."""

    GO = "go"
    REVISE = "revise"
    NO_GO = "no_go"
    UNKNOWN = "unknown"


class MarketValidationRequest(BaseModel):
    """Input for one validation run; fields are progressively optional.

    Genre-only requests produce heat data; adding concept/title/blurb unlocks
    the corresponding sections.
    """

    model_config = ConfigDict(frozen=True, str_strip_whitespace=True)

    genre_key: str = Field(default="", max_length=100)
    genre_label: str = Field(default="", max_length=100)
    sub_genre_key: str = Field(default="", max_length=100)
    sub_genre_label: str = Field(default="", max_length=100)
    channel: str = Field(default="", max_length=32)
    concept: str = Field(default="", max_length=2000)
    title_candidates: tuple[str, ...] = Field(default=())
    blurb: str = Field(default="", max_length=5000)
    project_slug: str = Field(default="", max_length=200)
    max_competitors: int = Field(default=12, ge=1, le=60)

    @field_validator("title_candidates", mode="before")
    @classmethod
    def _coerce_titles(cls, value: object) -> tuple[str, ...]:
        if value is None:
            return ()
        if isinstance(value, str):
            return (value.strip(),) if value.strip() else ()
        if isinstance(value, (list, tuple, set)):
            return tuple(str(item).strip() for item in value if str(item).strip())
        return (str(value).strip(),) if str(value).strip() else ()

    def digest(self) -> dict[str, Any]:
        return {
            "genre_key": self.genre_key,
            "genre_label": self.genre_label,
            "sub_genre_key": self.sub_genre_key,
            "channel": self.channel,
            "has_concept": bool(self.concept),
            "title_candidates": list(self.title_candidates),
            "has_blurb": bool(self.blurb),
            "project_slug": self.project_slug,
        }


class MarketCategoryRef(BaseModel):
    """One platform-side category a taxonomy genre maps onto."""

    model_config = ConfigDict(frozen=True, str_strip_whitespace=True)

    platform: str = Field(min_length=1, max_length=32)
    channel_label: str = Field(default="", max_length=32)
    category_label: str = Field(min_length=1, max_length=100)
    cat_id: str = Field(default="", max_length=32)
    weight: float = Field(default=1.0, ge=0.0, le=1.0)


class MarketBookObservation(BaseModel):
    """One competitor book row normalized across platforms (raw evidence)."""

    model_config = ConfigDict(str_strip_whitespace=True)

    platform: str = Field(min_length=1, max_length=32)
    source_book_id: str = Field(default="", max_length=128)
    title: str = Field(min_length=1, max_length=500)
    author: str = Field(default="", max_length=200)
    channel: str = Field(default="", max_length=32)
    category: str = Field(default="", max_length=100)
    board_type: str = Field(default="", max_length=64)
    rank: int = Field(default=0, ge=0)
    heat: int = Field(default=0, ge=0)
    heat_label: str = Field(default="", max_length=64)
    heat_delta: int | None = Field(default=None)
    rank_delta: int | None = Field(default=None)
    is_new_entry: bool = False
    word_count: int | None = Field(default=None, ge=0)
    status: str = Field(default="", max_length=64)
    intro: str = Field(default="", max_length=4000)
    tags: list[str] = Field(default_factory=list)
    rating: float | None = Field(default=None, ge=0.0)
    rating_count: int | None = Field(default=None, ge=0)
    source_url: str = Field(default="", max_length=2000)


class GenreHeatSection(BaseModel):
    """Deterministic heat readout for the mapped platform categories."""

    status: MarketSectionStatus = MarketSectionStatus.SKIPPED
    reason: str = Field(default="", max_length=500)
    categories: list[MarketCategoryRef] = Field(default_factory=list)
    sample_size: int = Field(default=0, ge=0)
    heat_p10: int = Field(default=0, ge=0)
    heat_p50: int = Field(default=0, ge=0)
    heat_p90: int = Field(default=0, ge=0)
    new_entry_share: float = Field(default=0.0, ge=0.0, le=1.0)
    rising_share: float = Field(default=0.0, ge=0.0, le=1.0)
    top_books: list[MarketBookObservation] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class CompetitorSimilarity(BaseModel):
    """LLM-judged similarity between our concept and one on-board book."""

    model_config = ConfigDict(str_strip_whitespace=True)

    title: str = Field(min_length=1, max_length=500)
    similarity: str = Field(default="low", max_length=16)
    overlap_points: list[str] = Field(default_factory=list)
    differentiation: list[str] = Field(default_factory=list)


class CompetitorScanSection(BaseModel):
    """Same-genre competitor list plus concept-collision judgments."""

    status: MarketSectionStatus = MarketSectionStatus.SKIPPED
    reason: str = Field(default="", max_length=500)
    competitors: list[MarketBookObservation] = Field(default_factory=list)
    collisions: list[CompetitorSimilarity] = Field(default_factory=list)


class TitleShellStats(BaseModel):
    """Crowding stats for one extracted title shell pattern."""

    model_config = ConfigDict(str_strip_whitespace=True)

    shell_pattern: str = Field(default="", max_length=200)
    board_count: int = Field(default=0, ge=0)
    heat_median: int = Field(default=0, ge=0)
    example_titles: list[str] = Field(default_factory=list)


class TitleCheckFinding(BaseModel):
    """Dedup + shell verdict for one candidate title."""

    model_config = ConfigDict(str_strip_whitespace=True)

    candidate: str = Field(min_length=1, max_length=500)
    exact_hits: list[str] = Field(default_factory=list)
    near_hits: list[str] = Field(default_factory=list)
    web_hits: list[str] = Field(default_factory=list)
    shell: TitleShellStats | None = None
    verdict: str = Field(default="pass", max_length=16)
    reasons: list[str] = Field(default_factory=list)


class TitleCheckSection(BaseModel):
    """Automates the manual title validation SOP steps 1/2/4."""

    status: MarketSectionStatus = MarketSectionStatus.SKIPPED
    reason: str = Field(default="", max_length=500)
    findings: list[TitleCheckFinding] = Field(default_factory=list)
    board_title_length_p50: int = Field(default=0, ge=0)
    board_title_colon_share: float = Field(default=0.0, ge=0.0, le=1.0)


class BlurbShape(BaseModel):
    """Structural shape of a blurb (ours or board-median)."""

    char_count: int = Field(default=0, ge=0)
    sentence_count: int = Field(default=0, ge=0)
    first_sentence: str = Field(default="", max_length=500)
    has_tag_prefix: bool = False


class BlurbBenchmarkSection(BaseModel):
    """Our blurb shape vs the mapped categories' on-board intro shapes."""

    status: MarketSectionStatus = MarketSectionStatus.SKIPPED
    reason: str = Field(default="", max_length=500)
    ours: BlurbShape | None = None
    board_median: BlurbShape | None = None
    warnings: list[str] = Field(default_factory=list)


class MarketVerdictSection(BaseModel):
    """Advisory synthesis. Explicitly a risk screen, not a hit predictor."""

    status: MarketSectionStatus = MarketSectionStatus.SKIPPED
    reason: str = Field(default="", max_length=500)
    score: int = Field(default=0, ge=0, le=100)
    band: MarketVerdictBand = MarketVerdictBand.UNKNOWN
    rationale: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    opportunities: list[str] = Field(default_factory=list)
    judge_model: str = Field(default="", max_length=200)
    fallback_used: bool = False


class MarketValidationReport(BaseModel):
    """Complete advisory market validation report for one concept/book."""

    schema_version: int = 1
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    request: MarketValidationRequest
    platforms_used: list[str] = Field(default_factory=list)
    data_dates: dict[str, str] = Field(default_factory=dict)
    genre_heat: GenreHeatSection = Field(default_factory=GenreHeatSection)
    competitor_scan: CompetitorScanSection = Field(default_factory=CompetitorScanSection)
    title_check: TitleCheckSection = Field(default_factory=TitleCheckSection)
    blurb_benchmark: BlurbBenchmarkSection = Field(default_factory=BlurbBenchmarkSection)
    verdict: MarketVerdictSection = Field(default_factory=MarketVerdictSection)

    def summary(self) -> dict[str, Any]:
        """Compact digest for project metadata backfill and CLI output."""

        return {
            "schema_version": self.schema_version,
            "generated_at": self.generated_at.isoformat(),
            "request": self.request.digest(),
            "platforms_used": self.platforms_used,
            "data_dates": self.data_dates,
            "genre_heat": {
                "status": self.genre_heat.status.value,
                "sample_size": self.genre_heat.sample_size,
                "heat_p50": self.genre_heat.heat_p50,
                "new_entry_share": round(self.genre_heat.new_entry_share, 3),
            },
            "title_check": {
                "status": self.title_check.status.value,
                "verdicts": {
                    finding.candidate: finding.verdict
                    for finding in self.title_check.findings
                },
            },
            "verdict": {
                "status": self.verdict.status.value,
                "score": self.verdict.score,
                "band": self.verdict.band.value,
                "fallback_used": self.verdict.fallback_used,
            },
        }
