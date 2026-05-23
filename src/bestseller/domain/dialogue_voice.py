"""Dialogue voice contracts for character-level speech control.

This module is intentionally separate from ``domain.voice_dna``.  The
existing VoiceDNA captures an author/book prose signature; these contracts
capture how individual characters speak, dodge, reveal, and carry regional
color inside dialogue.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class DialogueContextModulation:
    """How a character's voice shifts in one dialogue context."""

    context: str
    sentence_length_zh: tuple[int, int] | None = None
    pace: str = ""
    sample: str = ""
    pet_phrase_density: str = ""
    body_tell: str = ""


@dataclass(frozen=True)
class NegativeSpaceRule:
    """A character-specific non-answer pattern."""

    condition: str
    response: str


@dataclass(frozen=True)
class DialogueVoiceDNA:
    """A promptable and checkable character dialogue fingerprint."""

    character_name: str
    archetype: str = ""
    register: str = ""
    voice_traits: tuple[str, ...] = ()
    lexical_strategy: str = ""
    sentence_length_zh: tuple[int, int] = (3, 18)
    syntax_quirks: tuple[str, ...] = ()
    rhythm_rules: tuple[str, ...] = ()
    relationship_rules: tuple[str, ...] = ()
    genre_adaptations: tuple[str, ...] = ()
    pet_phrases: tuple[str, ...] = ()
    forbidden_phrases: tuple[str, ...] = ()
    enforce_lexical_markers: bool = False
    vocab_ceiling: str = ""
    vocab_floor: str = ""
    speech_speed: str = "中"
    body_tells: tuple[str, ...] = ()
    enforce_body_tells: bool = False
    taboo_topics: tuple[str, ...] = ()
    context_modulation: tuple[DialogueContextModulation, ...] = ()
    negative_space: tuple[NegativeSpaceRule, ...] = ()
    regional_markers: tuple[str, ...] = ()
    accent_profile: str = ""
    interpretation_rules: tuple[str, ...] = ()
    code_switching: str = ""

    @property
    def all_forbidden_phrases(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(p for p in self.forbidden_phrases if p))

    @property
    def has_distinctive_markers(self) -> bool:
        return bool(
            self.voice_traits
            or self.syntax_quirks
            or self.rhythm_rules
            or self.relationship_rules
            or self.body_tells
        )

    def clone_for_character(
        self,
        character_name: str,
        *,
        overrides: dict[str, Any] | None = None,
    ) -> DialogueVoiceDNA:
        payload = {
            "character_name": character_name,
            "archetype": self.archetype,
            "register": self.register,
            "voice_traits": self.voice_traits,
            "lexical_strategy": self.lexical_strategy,
            "sentence_length_zh": self.sentence_length_zh,
            "syntax_quirks": self.syntax_quirks,
            "rhythm_rules": self.rhythm_rules,
            "relationship_rules": self.relationship_rules,
            "genre_adaptations": self.genre_adaptations,
            "pet_phrases": self.pet_phrases,
            "forbidden_phrases": self.forbidden_phrases,
            "enforce_lexical_markers": self.enforce_lexical_markers,
            "vocab_ceiling": self.vocab_ceiling,
            "vocab_floor": self.vocab_floor,
            "speech_speed": self.speech_speed,
            "body_tells": self.body_tells,
            "enforce_body_tells": self.enforce_body_tells,
            "taboo_topics": self.taboo_topics,
            "context_modulation": self.context_modulation,
            "negative_space": self.negative_space,
            "regional_markers": self.regional_markers,
            "accent_profile": self.accent_profile,
            "interpretation_rules": self.interpretation_rules,
            "code_switching": self.code_switching,
        }
        if overrides:
            payload.update(overrides)
        return DialogueVoiceDNA(**payload)


@dataclass(frozen=True)
class DialogueVoiceFinding:
    severity: str
    code: str
    detail: str
    character: str | None = None
    line_index: int | None = None
    evidence: str = ""


@dataclass(frozen=True)
class DialogueVoiceReport:
    chapter_position: int
    findings: tuple[DialogueVoiceFinding, ...] = field(default_factory=tuple)

    @property
    def passed(self) -> bool:
        return not any(f.severity == "critical" for f in self.findings)

    @property
    def has_critical(self) -> bool:
        return any(f.severity == "critical" for f in self.findings)


__all__ = [
    "DialogueContextModulation",
    "DialogueVoiceDNA",
    "DialogueVoiceFinding",
    "DialogueVoiceReport",
    "NegativeSpaceRule",
]
