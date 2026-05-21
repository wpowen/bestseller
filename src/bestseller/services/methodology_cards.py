"""Load and validate source-traceable writing methodology cards."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator
import yaml

MethodologyCategory = Literal[
    "action_scene",
    "character",
    "foreshadowing",
    "longform_control",
    "mainline",
    "opening",
    "outline",
    "power",
    "progression",
    "surface_subtext",
    "timeline",
    "worldview",
]
MethodologyScope = Literal["asset", "book", "chapter", "project_health", "scene", "volume"]
MethodologyStage = Literal["drafting", "health", "planning", "repair", "review"]
MethodologyMaturity = Literal["deprecated", "draft", "pending_source", "verified"]
MethodologyGateMode = Literal["advisory", "audit_only", "off", "strict", "warn"]
MethodologySourceStatus = Literal["failed", "ok", "partial", "pending"]
MethodologyFindingSeverity = Literal["error", "info", "warning"]


class _MethodologyModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def default_plova_manifest_path() -> Path:
    return _repo_root() / "data" / "methodology_sources" / "plova" / "manifest.yaml"


def default_plova_cards_path() -> Path:
    return _repo_root() / "data" / "methodology_sources" / "plova" / "cards.yaml"


def _normalize_text_tuple(value: tuple[str, ...]) -> tuple[str, ...]:
    normalized = tuple(item.strip() for item in value if item and item.strip())
    if len(normalized) != len(value):
        raise ValueError("items must be non-empty strings")
    if len(set(normalized)) != len(normalized):
        raise ValueError("items must be unique")
    return normalized


class MethodologySourceItem(_MethodologyModel):
    source_id: str = Field(pattern=r"^[a-z0-9]+[.][0-9]{2}$")
    aweme_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    anchor: str = ""
    image_count: int = Field(default=0, ge=0)
    ocr_image_count: int = Field(default=0, ge=0)
    ocr_status: MethodologySourceStatus = "ok"
    category: str = ""
    topics: tuple[str, ...] = Field(default_factory=tuple)
    notes: str = ""

    @field_validator("topics")
    @classmethod
    def _validate_topics(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _normalize_text_tuple(value)

    @model_validator(mode="after")
    def _validate_ocr_counts(self) -> MethodologySourceItem:
        if self.ocr_image_count > self.image_count:
            raise ValueError("ocr_image_count cannot exceed image_count")
        if self.ocr_status == "ok" and self.ocr_image_count == 0:
            raise ValueError("ok OCR items must have at least one OCR image")
        if self.ocr_status in {"failed", "pending"} and self.ocr_image_count > 0:
            raise ValueError("failed or pending OCR items should not carry OCR images")
        return self

    @property
    def is_verified(self) -> bool:
        return self.ocr_status == "ok"

    @property
    def is_pending(self) -> bool:
        return not self.is_verified


class MethodologySourceSet(_MethodologyModel):
    source_set_id: str = Field(min_length=1)
    author: str = Field(min_length=1)
    douyin_id: str = ""
    homepage: str = ""
    source_markdown: str = Field(min_length=1)
    captured_at: str = ""
    total_items: int = Field(default=0, ge=0)
    ocr_items: int = Field(default=0, ge=0)
    pending_items: int = Field(default=0, ge=0)
    total_images: int = Field(default=0, ge=0)
    ocr_images: int = Field(default=0, ge=0)
    failed_images: int = Field(default=0, ge=0)
    items: tuple[MethodologySourceItem, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _validate_counts_and_ids(self) -> MethodologySourceSet:
        ids = [item.source_id for item in self.items]
        if len(set(ids)) != len(ids):
            raise ValueError("source_id values must be unique")

        verified = sum(1 for item in self.items if item.is_verified)
        pending = len(self.items) - verified
        image_count = sum(item.image_count for item in self.items)
        ocr_image_count = sum(item.ocr_image_count for item in self.items)

        if self.total_items and self.total_items != len(self.items):
            raise ValueError("total_items does not match item count")
        if self.ocr_items and self.ocr_items != verified:
            raise ValueError("ocr_items does not match verified OCR source count")
        if self.pending_items and self.pending_items != pending:
            raise ValueError("pending_items does not match pending source count")
        if self.total_images and self.total_images != image_count:
            raise ValueError("total_images does not match item image count")
        if self.ocr_images and self.ocr_images != ocr_image_count:
            raise ValueError("ocr_images does not match item OCR image count")
        return self

    @property
    def by_id(self) -> dict[str, MethodologySourceItem]:
        return {item.source_id: item for item in self.items}

    @property
    def verified_source_ids(self) -> tuple[str, ...]:
        return tuple(item.source_id for item in self.items if item.is_verified)

    @property
    def pending_source_ids(self) -> tuple[str, ...]:
        return tuple(item.source_id for item in self.items if item.is_pending)


class MethodologyGateBinding(_MethodologyModel):
    gate: str = Field(min_length=1)
    default_mode: MethodologyGateMode = "advisory"


class MethodologyCard(_MethodologyModel):
    id: str = Field(pattern=r"^[a-z0-9_.-]+$")
    source_ids: tuple[str, ...] = Field(min_length=1)
    title: str = Field(min_length=1)
    category: MethodologyCategory
    scope: tuple[MethodologyScope, ...] = Field(min_length=1)
    stage: tuple[MethodologyStage, ...] = Field(min_length=1)
    core_claim: str = Field(min_length=1)
    anti_patterns: tuple[str, ...] = Field(default_factory=tuple)
    required_contract_fields: tuple[str, ...] = Field(default_factory=tuple)
    framework_bindings: tuple[str, ...] = Field(min_length=1)
    gate_bindings: tuple[MethodologyGateBinding, ...] = Field(default_factory=tuple)
    maturity: MethodologyMaturity = "draft"

    @field_validator(
        "anti_patterns",
        "framework_bindings",
        "required_contract_fields",
        "scope",
        "source_ids",
        "stage",
    )
    @classmethod
    def _validate_unique_text_tuple(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _normalize_text_tuple(value)


class MethodologyCardDeck(_MethodologyModel):
    cards: tuple[MethodologyCard, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _validate_card_ids(self) -> MethodologyCardDeck:
        ids = [card.id for card in self.cards]
        if len(set(ids)) != len(ids):
            raise ValueError("card id values must be unique")
        return self

    @property
    def by_id(self) -> dict[str, MethodologyCard]:
        return {card.id: card for card in self.cards}

    def get_card(self, card_id: str) -> MethodologyCard:
        try:
            return self.by_id[card_id]
        except KeyError as exc:
            raise KeyError(f"Unknown methodology card: {card_id}") from exc

    def cards_by_category(self, category: str) -> tuple[MethodologyCard, ...]:
        return tuple(card for card in self.cards if card.category == category)

    def cards_for_source(self, source_id: str) -> tuple[MethodologyCard, ...]:
        return tuple(card for card in self.cards if source_id in card.source_ids)

    def cards_for_scope(self, scope: str) -> tuple[MethodologyCard, ...]:
        return tuple(card for card in self.cards if scope in card.scope)

    def cards_for_stage(self, stage: str) -> tuple[MethodologyCard, ...]:
        return tuple(card for card in self.cards if stage in card.stage)


class MethodologyFinding(_MethodologyModel):
    code: str = Field(min_length=1)
    severity: MethodologyFindingSeverity
    message: str = Field(min_length=1)
    card_id: str | None = None
    source_id: str | None = None
    path: str = ""


def _read_yaml_mapping(path: Path) -> dict[str, Any]:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"Unable to read methodology YAML {path}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise ValueError(f"Invalid YAML in methodology file {path}: {exc}") from exc

    if not isinstance(raw, Mapping):
        raise ValueError(f"Methodology YAML must be a mapping: {path}")
    return dict(raw)


def load_methodology_source_set(path: Path | None = None) -> MethodologySourceSet:
    source_path = path or default_plova_manifest_path()
    try:
        return MethodologySourceSet.model_validate(_read_yaml_mapping(source_path))
    except ValidationError as exc:
        raise ValueError(f"Invalid methodology source manifest {source_path}: {exc}") from exc


def load_methodology_cards(path: Path | None = None) -> MethodologyCardDeck:
    card_path = path or default_plova_cards_path()
    try:
        return MethodologyCardDeck.model_validate(_read_yaml_mapping(card_path))
    except ValidationError as exc:
        raise ValueError(f"Invalid methodology cards file {card_path}: {exc}") from exc


def validate_card_sources(
    deck: MethodologyCardDeck, source_set: MethodologySourceSet
) -> tuple[MethodologyFinding, ...]:
    findings: list[MethodologyFinding] = []
    source_by_id = source_set.by_id
    covered_source_ids: set[str] = set()

    for card in deck.cards:
        for source_id in card.source_ids:
            covered_source_ids.add(source_id)
            source = source_by_id.get(source_id)
            if source is None:
                findings.append(
                    MethodologyFinding(
                        code="METHODOLOGY_CARD_SOURCE_MISSING",
                        severity="error",
                        message=f"Card references unknown methodology source {source_id}.",
                        card_id=card.id,
                        source_id=source_id,
                    )
                )
                continue
            if card.maturity == "verified" and source.is_pending:
                findings.append(
                    MethodologyFinding(
                        code="METHODOLOGY_CARD_PENDING_SOURCE_VERIFIED",
                        severity="error",
                        message="Verified methodology card cannot reference a pending OCR source.",
                        card_id=card.id,
                        source_id=source_id,
                    )
                )

    for source in source_set.items:
        if source.is_verified and source.source_id not in covered_source_ids:
            findings.append(
                MethodologyFinding(
                    code="METHODOLOGY_VERIFIED_SOURCE_UNCOVERED",
                    severity="warning",
                    message="Verified OCR source has no methodology card coverage.",
                    source_id=source.source_id,
                )
            )

    return tuple(findings)


def methodology_coverage_summary(
    deck: MethodologyCardDeck, source_set: MethodologySourceSet | None = None
) -> dict[str, Any]:
    covered_source_ids = sorted({source_id for card in deck.cards for source_id in card.source_ids})
    gate_names = sorted({binding.gate for card in deck.cards for binding in card.gate_bindings})
    categories = sorted({card.category for card in deck.cards})

    summary: dict[str, Any] = {
        "cards": len(deck.cards),
        "verified_cards": sum(1 for card in deck.cards if card.maturity == "verified"),
        "pending_cards": sum(1 for card in deck.cards if card.maturity == "pending_source"),
        "gate_backed_cards": sum(1 for card in deck.cards if card.gate_bindings),
        "cards_missing_gate_binding": [card.id for card in deck.cards if not card.gate_bindings],
        "covered_source_count": len(covered_source_ids),
        "covered_source_ids": covered_source_ids,
        "categories": categories,
        "gate_names": gate_names,
    }

    if source_set is None:
        return summary

    verified_source_ids = list(source_set.verified_source_ids)
    pending_source_ids = list(source_set.pending_source_ids)
    source_by_id = source_set.by_id
    unknown_source_ids = [
        source_id for source_id in covered_source_ids if source_id not in source_by_id
    ]
    uncovered_verified_source_ids = [
        source_id for source_id in verified_source_ids if source_id not in covered_source_ids
    ]
    covered_verified_source_ids = [
        source_id for source_id in verified_source_ids if source_id in covered_source_ids
    ]
    coverage_ratio = (
        len(covered_verified_source_ids) / len(verified_source_ids) if verified_source_ids else 1.0
    )

    summary.update(
        {
            "source_items": len(source_set.items),
            "verified_sources": len(verified_source_ids),
            "pending_sources": len(pending_source_ids),
            "pending_source_ids": pending_source_ids,
            "uncovered_verified_source_ids": uncovered_verified_source_ids,
            "unknown_source_ids": unknown_source_ids,
            "verified_source_coverage_ratio": coverage_ratio,
        }
    )
    return summary


__all__ = [
    "MethodologyCard",
    "MethodologyCardDeck",
    "MethodologyFinding",
    "MethodologyGateBinding",
    "MethodologySourceItem",
    "MethodologySourceSet",
    "default_plova_cards_path",
    "default_plova_manifest_path",
    "load_methodology_cards",
    "load_methodology_source_set",
    "methodology_coverage_summary",
    "validate_card_sources",
]
