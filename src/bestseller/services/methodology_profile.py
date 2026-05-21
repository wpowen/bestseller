"""Project-level methodology profile loading and prompt rendering."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator
import yaml

from bestseller.services.methodology_cards import (
    MethodologyCard,
    MethodologyCardDeck,
    MethodologyFinding,
    MethodologyGateMode,
    default_plova_cards_path,
    load_methodology_cards,
)
from bestseller.services.writing_profile import is_english_language


class _MethodologyProfileModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def default_methodology_profiles_dir() -> Path:
    return _repo_root() / "config" / "methodology_profiles"


def default_methodology_profile_path(profile_id: str = "plova_structured_writing_v1") -> Path:
    return default_methodology_profiles_dir() / f"{profile_id}.yaml"


def _non_empty_unique_tuple(value: tuple[str, ...]) -> tuple[str, ...]:
    normalized = tuple(item.strip() for item in value if item and item.strip())
    if len(normalized) != len(value):
        raise ValueError("items must be non-empty strings")
    if len(set(normalized)) != len(normalized):
        raise ValueError("items must be unique")
    return normalized


class MethodologyProfileCardSetting(_MethodologyProfileModel):
    enabled: bool = True
    gate_mode: MethodologyGateMode | None = None
    priority: int = Field(default=100, ge=0)
    strict_when: tuple[str, ...] = Field(default_factory=tuple)

    @field_validator("strict_when")
    @classmethod
    def _validate_strict_when(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _non_empty_unique_tuple(value)


class MethodologyProfile(_MethodologyProfileModel):
    profile_id: str = Field(pattern=r"^[a-z0-9_.-]+$")
    title: str = Field(min_length=1)
    source_set_id: str = Field(min_length=1)
    card_deck: str = str(default_plova_cards_path().relative_to(_repo_root()))
    default_mode: MethodologyGateMode = "warn"
    max_prompt_cards: int = Field(default=6, ge=1)
    pending_sources: tuple[str, ...] = Field(default_factory=tuple)
    cards: dict[str, MethodologyProfileCardSetting] = Field(default_factory=dict)

    @field_validator("pending_sources")
    @classmethod
    def _validate_pending_sources(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _non_empty_unique_tuple(value)

    @model_validator(mode="after")
    def _validate_cards(self) -> "MethodologyProfile":
        if not self.cards:
            raise ValueError("profile must enable at least one card")
        return self

    def setting_for(self, card_id: str) -> MethodologyProfileCardSetting:
        return self.cards.get(card_id, MethodologyProfileCardSetting())

    def is_enabled(self, card_id: str) -> bool:
        setting = self.cards.get(card_id)
        return bool(setting and setting.enabled)


def _read_yaml_mapping(path: Path) -> dict[str, Any]:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"Unable to read methodology profile {path}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise ValueError(f"Invalid YAML in methodology profile {path}: {exc}") from exc
    if not isinstance(raw, Mapping):
        raise ValueError(f"Methodology profile YAML must be a mapping: {path}")
    return dict(raw)


def load_methodology_profile(path_or_profile_id: Path | str | None = None) -> MethodologyProfile:
    if path_or_profile_id is None:
        path = default_methodology_profile_path()
    elif isinstance(path_or_profile_id, Path):
        path = path_or_profile_id
    else:
        raw = path_or_profile_id.strip()
        path = Path(raw) if raw.endswith((".yaml", ".yml")) else default_methodology_profile_path(raw)
    try:
        return MethodologyProfile.model_validate(_read_yaml_mapping(path))
    except ValidationError as exc:
        raise ValueError(f"Invalid methodology profile {path}: {exc}") from exc


def load_profile_deck(profile: MethodologyProfile) -> MethodologyCardDeck:
    path = Path(profile.card_deck)
    if not path.is_absolute():
        path = _repo_root() / path
    return load_methodology_cards(path)


def validate_methodology_profile(
    profile: MethodologyProfile, deck: MethodologyCardDeck
) -> tuple[MethodologyFinding, ...]:
    deck_by_id = deck.by_id
    findings: list[MethodologyFinding] = []
    for card_id, setting in profile.cards.items():
        if card_id not in deck_by_id:
            findings.append(
                MethodologyFinding(
                    code="METHODOLOGY_PROFILE_CARD_MISSING",
                    severity="error",
                    message=f"Profile references unknown methodology card {card_id}.",
                    card_id=card_id,
                )
            )
        if setting.enabled and card_id in deck_by_id:
            card = deck_by_id[card_id]
            pending_source_ids = [
                source_id for source_id in card.source_ids if source_id in profile.pending_sources
            ]
            if pending_source_ids:
                findings.append(
                    MethodologyFinding(
                        code="METHODOLOGY_PROFILE_PENDING_SOURCE_ENABLED",
                        severity="error",
                        message="Profile cannot enable a card backed by pending sources.",
                        card_id=card_id,
                        source_id=pending_source_ids[0],
                    )
                )
    return tuple(findings)


def gate_mode_for_card(profile: MethodologyProfile, card_id: str) -> MethodologyGateMode:
    setting = profile.cards.get(card_id)
    if setting and setting.gate_mode:
        return setting.gate_mode
    return profile.default_mode


def enabled_cards(
    profile: MethodologyProfile,
    deck: MethodologyCardDeck,
    *,
    stage: str,
    scope: str,
) -> tuple[MethodologyCard, ...]:
    candidates: list[tuple[int, MethodologyCard]] = []
    for card in deck.cards:
        setting = profile.cards.get(card.id)
        if setting is None or not setting.enabled:
            continue
        if stage not in card.stage or scope not in card.scope:
            continue
        if any(source_id in profile.pending_sources for source_id in card.source_ids):
            continue
        candidates.append((setting.priority, card))
    candidates.sort(key=lambda item: (item[0], item[1].id))
    return tuple(card for _, card in candidates)


def render_methodology_profile_block(
    profile: MethodologyProfile,
    deck: MethodologyCardDeck | None = None,
    *,
    stage: str,
    scope: str,
    language: str | None = "zh-CN",
    max_cards: int | None = None,
) -> str:
    active_deck = deck or load_profile_deck(profile)
    selected = enabled_cards(profile, active_deck, stage=stage, scope=scope)
    limit = max_cards if max_cards is not None else profile.max_prompt_cards
    selected = selected[:limit]
    if not selected:
        return ""

    is_en = is_english_language(language)
    lines = [
        (
            f"Methodology profile: {profile.profile_id} ({stage}/{scope})"
            if is_en
            else f"方法论 profile：{profile.profile_id}（{stage}/{scope}）"
        )
    ]
    for card in selected:
        mode = gate_mode_for_card(profile, card.id)
        required = ", ".join(card.required_contract_fields[:4])
        gates = ", ".join(binding.gate for binding in card.gate_bindings[:2])
        if is_en:
            line = f"- {card.id} [{mode}]: {card.core_claim}"
            if required:
                line += f" Required contract: {required}."
            if gates:
                line += f" Gate: {gates}."
        else:
            line = f"- {card.id} [{mode}]：{card.core_claim}"
            if required:
                line += f" 必填合约：{required}。"
            if gates:
                line += f" 对应 gate：{gates}。"
        lines.append(line)
    return "\n".join(lines)


def render_configured_methodology_profile_block(
    *,
    stage: str,
    scope: str,
    language: str | None = "zh-CN",
    profile_id: str | None = None,
) -> str:
    try:
        from bestseller.services.quality_gates_config import get_quality_gates_config

        cfg = get_quality_gates_config().methodology_framework
        if not cfg.enabled or not cfg.cards_enabled:
            return ""
        profile = load_methodology_profile(profile_id or cfg.profile_id)
        deck = load_profile_deck(profile)
        return render_methodology_profile_block(
            profile,
            deck,
            stage=stage,
            scope=scope,
            language=language,
        )
    except Exception:
        return ""


__all__ = [
    "MethodologyProfile",
    "MethodologyProfileCardSetting",
    "default_methodology_profile_path",
    "default_methodology_profiles_dir",
    "enabled_cards",
    "gate_mode_for_card",
    "load_methodology_profile",
    "load_profile_deck",
    "render_configured_methodology_profile_block",
    "render_methodology_profile_block",
    "validate_methodology_profile",
]
