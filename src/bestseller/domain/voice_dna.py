"""Voice Signature DNA domain contracts.

These models capture an author's prose fingerprint — the **反平均** signal
that distinguishes top-tier serial fiction from professionally-competent
average output.

A ``VoiceDNA`` is produced offline from one or more reference texts by
``services.voice_signature``. It is then injected into chapter prompts as a
generation constraint, *not* an after-the-fact gate. This is the core
distinction from ``voice_drift`` — drift checks if a character still sounds
like itself; DNA defines what "sound" the whole book should have.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class SentenceLengthHistogram(BaseModel):
    """Percentile breakdown of sentence character counts."""

    model_config = ConfigDict(str_strip_whitespace=True)

    p10: float = Field(ge=0)
    p25: float = Field(ge=0)
    p50: float = Field(ge=0)
    p75: float = Field(ge=0)
    p90: float = Field(ge=0)
    mean: float = Field(ge=0)
    stddev: float = Field(ge=0)
    short_ratio: float = Field(default=0.0, ge=0, le=1)
    long_ratio: float = Field(default=0.0, ge=0, le=1)


class RhetoricSignals(BaseModel):
    """Frequency of identifiable rhetorical devices per 1000 characters."""

    model_config = ConfigDict(str_strip_whitespace=True)

    simile_per_kchar: float = Field(default=0.0, ge=0)
    parallelism_per_kchar: float = Field(default=0.0, ge=0)
    rhetorical_question_per_kchar: float = Field(default=0.0, ge=0)
    ellipsis_per_kchar: float = Field(default=0.0, ge=0)
    exclamation_per_kchar: float = Field(default=0.0, ge=0)
    interjection_per_kchar: float = Field(default=0.0, ge=0)


class PacingSignature(BaseModel):
    """Distribution of narrative modes (dialogue / action / interior / description)."""

    model_config = ConfigDict(str_strip_whitespace=True)

    dialogue_ratio: float = Field(default=0.0, ge=0, le=1)
    action_ratio: float = Field(default=0.0, ge=0, le=1)
    interior_ratio: float = Field(default=0.0, ge=0, le=1)
    description_ratio: float = Field(default=0.0, ge=0, le=1)
    avg_paragraph_chars: float = Field(default=0.0, ge=0)
    avg_paragraphs_per_kchar: float = Field(default=0.0, ge=0)


class VoiceDNA(BaseModel):
    """Author/book voice signature suitable for prompt injection."""

    model_config = ConfigDict(str_strip_whitespace=True)

    source_id: str = Field(min_length=1, max_length=200)
    source_label: str = Field(default="", max_length=400)
    sample_chars: int = Field(default=0, ge=0)

    sentence_length: SentenceLengthHistogram
    rhetoric: RhetoricSignals
    pacing: PacingSignature

    rare_char_density: float = Field(default=0.0, ge=0, le=1)
    classical_marker_density: float = Field(default=0.0, ge=0, le=1)
    catchphrases: list[str] = Field(default_factory=list)
    favorite_openers: list[str] = Field(default_factory=list)
    favorite_closers: list[str] = Field(default_factory=list)
    taboo_phrases: list[str] = Field(default_factory=list)

    style_register: str = Field(default="", max_length=64)
    confidence: float = Field(default=0.5, ge=0, le=1)
    notes: list[str] = Field(default_factory=list)

    @field_validator(
        "catchphrases",
        "favorite_openers",
        "favorite_closers",
        "taboo_phrases",
        "notes",
        mode="before",
    )
    @classmethod
    def _coerce_lists(cls, value: object) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            normalized = value.replace("，", ",").replace("、", ",")
            return [item.strip() for item in normalized.split(",") if item.strip()]
        if isinstance(value, (list, tuple, set)):
            return [str(item).strip() for item in value if str(item).strip()]
        return [str(value).strip()] if str(value).strip() else []

    def to_prompt_card(self) -> dict[str, Any]:
        """Compact, prompt-friendly representation."""

        return {
            "source_label": self.source_label,
            "register": self.style_register,
            "sentence_length": {
                "median": self.sentence_length.p50,
                "p10_p90": [self.sentence_length.p10, self.sentence_length.p90],
                "short_ratio": self.sentence_length.short_ratio,
                "long_ratio": self.sentence_length.long_ratio,
            },
            "rhetoric_per_kchar": self.rhetoric.model_dump(),
            "pacing": self.pacing.model_dump(),
            "rare_char_density": self.rare_char_density,
            "classical_marker_density": self.classical_marker_density,
            "catchphrases": list(self.catchphrases),
            "favorite_openers": list(self.favorite_openers),
            "favorite_closers": list(self.favorite_closers),
            "taboo_phrases": list(self.taboo_phrases),
            "confidence": self.confidence,
        }


class VoiceDNADiff(BaseModel):
    """Drift between a target DNA and an observed sample DNA."""

    model_config = ConfigDict(str_strip_whitespace=True)

    overall_drift: float = Field(default=0.0, ge=0, le=1)
    sentence_length_drift: float = Field(default=0.0, ge=0, le=1)
    rhetoric_drift: float = Field(default=0.0, ge=0, le=1)
    pacing_drift: float = Field(default=0.0, ge=0, le=1)
    rare_char_drift: float = Field(default=0.0, ge=0, le=1)
    missing_catchphrases: list[str] = Field(default_factory=list)
    forbidden_phrases_hit: list[str] = Field(default_factory=list)
    analysis: str = Field(default="", max_length=4000)

    @property
    def needs_correction(self) -> bool:
        return self.overall_drift > 0.35 or bool(self.forbidden_phrases_hit)
