"""Fanqie ranking market intelligence domain contracts.

These models describe public ranking observations and anonymized craft
summaries. They intentionally keep raw evidence separate from generated
planning artifacts so market analysis can be audited and recomputed.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class FanqieRankingBook(BaseModel):
    """One book row normalized from a public Fanqie ranking source."""

    model_config = ConfigDict(str_strip_whitespace=True)

    source_book_id: str = Field(min_length=1, max_length=128)
    title: str = Field(min_length=1, max_length=500)
    author: str = Field(default="", max_length=500)
    category: str = Field(default="", max_length=200)
    channel: str = Field(default="fanqie", max_length=64)
    board_type: str = Field(default="reading", max_length=64)
    rank: int = Field(ge=1)
    reader_count: int = Field(default=0, ge=0)
    reader_count_label: str = Field(default="", max_length=64)
    tags: list[str] = Field(default_factory=list)
    status: str = Field(default="", max_length=64)
    latest_chapter: str = Field(default="", max_length=500)
    word_count: int | None = Field(default=None, ge=0)
    intro: str = Field(default="", max_length=10000)
    source_url: str = Field(default="", max_length=2000)
    raw: dict[str, Any] = Field(default_factory=dict)

    @field_validator("tags", mode="before")
    @classmethod
    def _coerce_tags(cls, value: object) -> list[str]:
        return _coerce_string_list(value)


class FanqieRankingSnapshot(BaseModel):
    """A fetched ranking board plus normalized rows."""

    model_config = ConfigDict(str_strip_whitespace=True)

    source: str = Field(default="fanqiehub", max_length=64)
    source_url: str = Field(default="", max_length=2000)
    board_type: str = Field(default="reading", max_length=64)
    category: str = Field(default="", max_length=200)
    channel: str = Field(default="fanqie", max_length=64)
    data_date: date
    fetched_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    books: list[FanqieRankingBook] = Field(default_factory=list)
    raw_payload: dict[str, Any] = Field(default_factory=dict)

    @field_validator("books")
    @classmethod
    def _sort_books(cls, books: list[FanqieRankingBook]) -> list[FanqieRankingBook]:
        return sorted(books, key=lambda book: book.rank)

    @property
    def sample_size(self) -> int:
        return len(self.books)

    @property
    def top_titles(self) -> list[str]:
        return [book.title for book in self.books[:10]]


class FanqieCompetitorProfile(BaseModel):
    """Book-level market readout distilled from one ranking row."""

    model_config = ConfigDict(str_strip_whitespace=True)

    source_book_id: str = Field(min_length=1, max_length=128)
    title: str = Field(min_length=1, max_length=500)
    author: str = Field(default="", max_length=500)
    category: str = Field(default="", max_length=200)
    board_type: str = Field(default="reading", max_length=64)
    rank: int = Field(ge=1)
    reader_count: int = Field(default=0, ge=0)
    premise_signals: list[str] = Field(default_factory=list)
    setting_signals: list[str] = Field(default_factory=list)
    protagonist_signals: list[str] = Field(default_factory=list)
    conflict_signals: list[str] = Field(default_factory=list)
    hook_patterns: list[str] = Field(default_factory=list)
    structure_patterns: list[str] = Field(default_factory=list)
    writing_style_signals: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)
    anti_copy_constraints: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    raw_refs: dict[str, Any] = Field(default_factory=dict)

    @field_validator(
        "premise_signals",
        "setting_signals",
        "protagonist_signals",
        "conflict_signals",
        "hook_patterns",
        "structure_patterns",
        "writing_style_signals",
        "evidence",
        "anti_copy_constraints",
        mode="before",
    )
    @classmethod
    def _coerce_lists(cls, value: object) -> list[str]:
        return _coerce_string_list(value)


class FanqieCategoryProfile(BaseModel):
    """Aggregated category-level market pattern profile."""

    model_config = ConfigDict(str_strip_whitespace=True)

    category: str = Field(min_length=1, max_length=200)
    board_type: str = Field(default="reading", max_length=64)
    channel: str = Field(default="fanqie", max_length=64)
    data_date: date
    sample_size: int = Field(ge=0)
    reader_heat_stats: dict[str, float] = Field(default_factory=dict)
    dominant_settings: list[str] = Field(default_factory=list)
    protagonist_archetypes: list[str] = Field(default_factory=list)
    hook_patterns: list[str] = Field(default_factory=list)
    structure_patterns: list[str] = Field(default_factory=list)
    payoff_patterns: list[str] = Field(default_factory=list)
    style_guidelines: list[str] = Field(default_factory=list)
    safety_notes: list[str] = Field(default_factory=list)
    evidence_profile_ids: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)

    @field_validator(
        "dominant_settings",
        "protagonist_archetypes",
        "hook_patterns",
        "structure_patterns",
        "payoff_patterns",
        "style_guidelines",
        "safety_notes",
        "evidence_profile_ids",
        mode="before",
    )
    @classmethod
    def _coerce_lists(cls, value: object) -> list[str]:
        return _coerce_string_list(value)


class FanqieCraftProfile(BaseModel):
    """Anonymous craft card that can be injected into planning prompts."""

    model_config = ConfigDict(str_strip_whitespace=True)

    category: str = Field(min_length=1, max_length=200)
    board_type: str = Field(default="reading", max_length=64)
    source_profile_ids: list[str] = Field(default_factory=list)
    allowed_style_principles: list[str] = Field(default_factory=list)
    disallowed_copy_targets: list[str] = Field(default_factory=list)
    hook_rules: list[str] = Field(default_factory=list)
    pacing_rules: list[str] = Field(default_factory=list)
    structure_rules: list[str] = Field(default_factory=list)
    sentence_style: str = Field(default="", max_length=1000)
    paragraph_style: str = Field(default="", max_length=1000)
    dialogue_ratio_hint: float | None = Field(default=None, ge=0.0, le=1.0)
    safety_boundary: str = Field(default="", max_length=2000)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)

    @field_validator(
        "source_profile_ids",
        "allowed_style_principles",
        "disallowed_copy_targets",
        "hook_rules",
        "pacing_rules",
        "structure_rules",
        mode="before",
    )
    @classmethod
    def _coerce_lists(cls, value: object) -> list[str]:
        return _coerce_string_list(value)

    def to_prompt_card(self) -> dict[str, Any]:
        """Return a compact, source-anonymized prompt payload."""

        return {
            "category": self.category,
            "board_type": self.board_type,
            "allowed_style_principles": self.allowed_style_principles,
            "disallowed_copy_targets": self.disallowed_copy_targets,
            "hook_rules": self.hook_rules,
            "pacing_rules": self.pacing_rules,
            "structure_rules": self.structure_rules,
            "sentence_style": self.sentence_style,
            "paragraph_style": self.paragraph_style,
            "dialogue_ratio_hint": self.dialogue_ratio_hint,
            "safety_boundary": self.safety_boundary,
            "confidence": self.confidence,
        }


class FanqieMarketAnalysisBundle(BaseModel):
    """Complete market analysis bundle for one ranking snapshot."""

    snapshot: FanqieRankingSnapshot
    competitor_profiles: list[FanqieCompetitorProfile] = Field(default_factory=list)
    category_profile: FanqieCategoryProfile
    craft_profile: FanqieCraftProfile

    def to_artifact_payload(self) -> dict[str, Any]:
        return {
            "snapshot": self.snapshot.model_dump(mode="json"),
            "competitor_profiles": [
                profile.model_dump(mode="json") for profile in self.competitor_profiles
            ],
            "category_profile": self.category_profile.model_dump(mode="json"),
            "craft_profile": self.craft_profile.model_dump(mode="json"),
        }

    def summary(self) -> dict[str, Any]:
        return {
            "source": self.snapshot.source,
            "category": self.snapshot.category,
            "board_type": self.snapshot.board_type,
            "data_date": self.snapshot.data_date.isoformat(),
            "sample_size": self.snapshot.sample_size,
            "top_titles": self.snapshot.top_titles,
            "dominant_settings": self.category_profile.dominant_settings,
            "hook_patterns": self.category_profile.hook_patterns,
            "structure_patterns": self.category_profile.structure_patterns,
            "craft_confidence": self.craft_profile.confidence,
        }


def _coerce_string_list(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        normalized = value.replace("\uff0c", ",").replace("\u3001", ",")
        return [item.strip() for item in normalized.split(",") if item.strip()]
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()] if str(value).strip() else []
