"""Signature Scene domain — every-N-chapter memorable scene mandates.

A bestselling serialized novel earns ~60% of its identifiability from
5-10 signature scenes that readers can:

* recall by name (the "剑开天门" scene)
* screenshot and share
* be referenced in fan art, derived works, marketing

The framework cannot manufacture these by luck — they need to be
**planned** into the chapter slots from the outline phase, then enforced
by a prompt-level mandate during writing.

This module defines the contracts. ``services.signature_scene_planner``
generates the actual schedule.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class SignatureSceneArchetype(str, Enum):
    """Canonical signature-scene shapes seen across top-tier serial fiction."""

    REVELATION = "revelation"                  # 真相揭开/身世翻转
    SACRIFICE = "sacrifice"                    # 为他人/为信念赴死
    CONFRONTATION = "confrontation"            # 决战/正邪对峙
    OATH_BOUND = "oath_bound"                  # 立誓/血誓/契约
    DEFIANCE = "defiance"                      # 螳臂当车式抗令
    REUNION = "reunion"                        # 久别重逢/前缘再续
    BETRAYAL = "betrayal"                      # 至亲/挚友的背叛
    APOTHEOSIS = "apotheosis"                  # 觉醒/破界/超凡
    FAREWELL = "farewell"                      # 永诀/送别
    UNVEILING_NAME = "unveiling_name"          # 真名/真身揭示


class SignatureSceneStake(str, Enum):
    """The emotional stake the scene operates on."""

    LIFE_DEATH = "life_death"
    LOVE_LOSS = "love_loss"
    LOYALTY_HONOR = "loyalty_honor"
    IDENTITY_TRUTH = "identity_truth"
    POWER_AUTHORITY = "power_authority"
    FREEDOM_BONDAGE = "freedom_bondage"


class SignatureSceneMandate(BaseModel):
    """A planned mandate for one signature-scene slot."""

    model_config = ConfigDict(str_strip_whitespace=True)

    chapter_position: int = Field(ge=1)
    archetype: SignatureSceneArchetype
    stake: SignatureSceneStake
    title_hint: str = Field(default="", max_length=200)
    summary: str = Field(default="", max_length=2000)

    must_include_image: list[str] = Field(default_factory=list)
    must_include_line: list[str] = Field(default_factory=list)
    must_invert: list[str] = Field(default_factory=list)
    payoff_targets: list[str] = Field(default_factory=list)

    intensity_target: float = Field(default=0.75, ge=0, le=1)
    shareability_target: float = Field(default=0.7, ge=0, le=1)

    @field_validator(
        "must_include_image",
        "must_include_line",
        "must_invert",
        "payoff_targets",
        mode="before",
    )
    @classmethod
    def _coerce_lists(cls, value: object) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            return [v.strip() for v in value.split(";") if v.strip()]
        if isinstance(value, (list, tuple, set)):
            return [str(v).strip() for v in value if str(v).strip()]
        return [str(value).strip()] if str(value).strip() else []

    def to_prompt_card(self) -> dict[str, Any]:
        return {
            "chapter_position": self.chapter_position,
            "archetype": self.archetype.value,
            "stake": self.stake.value,
            "title_hint": self.title_hint,
            "summary": self.summary,
            "must_include_image": list(self.must_include_image),
            "must_include_line": list(self.must_include_line),
            "must_invert": list(self.must_invert),
            "payoff_targets": list(self.payoff_targets),
            "intensity_target": self.intensity_target,
            "shareability_target": self.shareability_target,
        }


class SignatureScenePlan(BaseModel):
    """A complete plan of signature-scene slots for a book."""

    model_config = ConfigDict(str_strip_whitespace=True)

    total_chapters: int = Field(ge=1)
    cadence: int = Field(ge=1)
    mandates: list[SignatureSceneMandate] = Field(default_factory=list)

    def mandate_for_chapter(
        self, chapter_position: int
    ) -> SignatureSceneMandate | None:
        for mandate in self.mandates:
            if mandate.chapter_position == chapter_position:
                return mandate
        return None

    def upcoming(self, chapter_position: int, *, lookahead: int = 1) -> list[SignatureSceneMandate]:
        """Mandates strictly *after* ``chapter_position``, in chapter order."""

        return [
            m
            for m in self.mandates
            if m.chapter_position > chapter_position
        ][:lookahead]
