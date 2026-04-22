"""L1 Project Invariants.

Immutable per-project contract seeded once at project creation and read by
every downstream stage (prompt construction, validation, audit). The point is
to make decisions *once*, centrally, and deny later stages the ability to
improvise them on the fly.

This module is dependency-light: it owns only pure data structures plus the
seed logic that turns a ``ProjectModel`` + ``AppSettings`` into a
``ProjectInvariants`` instance. Persistence lives in the caller (e.g.
``pipelines.run_project_pipeline``) via ``ProjectModel.invariants_json``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal, Mapping
from uuid import UUID

from bestseller.services.hype_engine import (
    HypeScheme,
    hype_scheme_from_dict,
    hype_scheme_from_preset_overrides,
    hype_scheme_to_dict,
)


# ---------------------------------------------------------------------------
# Enumerations — kept string-valued for easy JSON (de)serialization.
# ---------------------------------------------------------------------------

class OpeningArchetype(str, Enum):
    HUMILIATION = "humiliation"
    CRISIS = "crisis"
    ENCOUNTER = "encounter"
    CONTRAST = "contrast"
    SECRET_REVEAL = "secret_reveal"
    IDENTITY_FALL = "identity_fall"
    BROKEN_ENGAGEMENT = "broken_engagement"
    BANISHMENT = "banishment"
    BETRAYAL = "betrayal"
    SUDDEN_POWER = "sudden_power"
    RITUAL_INTERRUPTED = "ritual_interrupted"
    MUNDANE_DAY = "mundane_day"


class CliffhangerType(str, Enum):
    REVELATION = "revelation"
    INTRUSION = "intrusion"
    DECISION = "decision"
    BODY_REACTION = "body_reaction"
    NEW_CHARACTER = "new_character"
    POWER_SHIFT = "power_shift"
    ENVIRONMENTAL = "environmental"
    INTERNAL_CRISIS = "internal_crisis"


NamingStyle = Literal[
    "cjk_2char", "cjk_3char", "latinate", "saxon", "mixed_allowed"
]
PovStyle = Literal["first", "close_third", "omniscient"]
TenseStyle = Literal["past", "present"]


# ---------------------------------------------------------------------------
# Invariant sub-structures.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class LengthEnvelope:
    """Allowed chapter character/word range. `min`/`max` are hard walls."""

    min_chars: int
    target_chars: int
    max_chars: int

    def __post_init__(self) -> None:
        if not (self.min_chars < self.target_chars < self.max_chars):
            raise ValueError(
                f"LengthEnvelope is not monotonic: "
                f"min={self.min_chars} target={self.target_chars} max={self.max_chars}"
            )


@dataclass(frozen=True)
class NamingScheme:
    style: NamingStyle
    seed_pool: tuple[str, ...]
    reserved_surnames: tuple[str, ...] = ()
    validator_regex: str = ""


@dataclass(frozen=True)
class CliffhangerPolicy:
    no_repeat_within: int = 3
    allowed_types: tuple[CliffhangerType, ...] = tuple(CliffhangerType)


# ---------------------------------------------------------------------------
# ProjectInvariants — the frozen top-level contract.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ProjectInvariants:
    project_id: UUID
    language: Literal["zh-CN", "en"]
    length_envelope: LengthEnvelope
    pov: PovStyle = "close_third"
    tense: TenseStyle = "past"
    naming_scheme: NamingScheme | None = None
    opening_archetype_pool: tuple[OpeningArchetype, ...] = tuple(OpeningArchetype)
    cliffhanger_policy: CliffhangerPolicy = field(default_factory=CliffhangerPolicy)
    vocab_caps: Mapping[str, int] = field(default_factory=dict)
    banned_formulaic_phrases: tuple[str, ...] = ()
    forced_methodology_fragments: tuple[str, ...] = ()
    antagonist_uniqueness: bool = True
    hype_scheme: HypeScheme = field(default_factory=HypeScheme)


class InvariantSeedError(RuntimeError):
    """Raised when invariant seeding cannot produce a valid contract."""


# ---------------------------------------------------------------------------
# Serialization — JSONB ↔ ProjectInvariants round-trip.
# ---------------------------------------------------------------------------

def invariants_to_dict(inv: ProjectInvariants) -> dict[str, Any]:
    """Serialize invariants to a JSON-safe dict for persistence."""

    return {
        "project_id": str(inv.project_id),
        "language": inv.language,
        "pov": inv.pov,
        "tense": inv.tense,
        "length_envelope": {
            "min_chars": inv.length_envelope.min_chars,
            "target_chars": inv.length_envelope.target_chars,
            "max_chars": inv.length_envelope.max_chars,
        },
        "naming_scheme": (
            None
            if inv.naming_scheme is None
            else {
                "style": inv.naming_scheme.style,
                "seed_pool": list(inv.naming_scheme.seed_pool),
                "reserved_surnames": list(inv.naming_scheme.reserved_surnames),
                "validator_regex": inv.naming_scheme.validator_regex,
            }
        ),
        "opening_archetype_pool": [a.value for a in inv.opening_archetype_pool],
        "cliffhanger_policy": {
            "no_repeat_within": inv.cliffhanger_policy.no_repeat_within,
            "allowed_types": [t.value for t in inv.cliffhanger_policy.allowed_types],
        },
        "vocab_caps": dict(inv.vocab_caps),
        "banned_formulaic_phrases": list(inv.banned_formulaic_phrases),
        "forced_methodology_fragments": list(inv.forced_methodology_fragments),
        "antagonist_uniqueness": inv.antagonist_uniqueness,
        "hype_scheme": hype_scheme_to_dict(inv.hype_scheme),
    }


def invariants_from_dict(data: Mapping[str, Any]) -> ProjectInvariants:
    """Deserialize invariants from a persisted JSONB payload.

    Raises ``InvariantSeedError`` if mandatory keys are missing or values are
    out of range — we fail loud rather than silently degrading to defaults.
    """

    try:
        project_id = UUID(str(data["project_id"]))
        language = data["language"]
        env_raw = data["length_envelope"]
        length_envelope = LengthEnvelope(
            min_chars=int(env_raw["min_chars"]),
            target_chars=int(env_raw["target_chars"]),
            max_chars=int(env_raw["max_chars"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise InvariantSeedError(f"Invalid invariants payload: {exc}") from exc

    naming_scheme: NamingScheme | None = None
    ns_raw = data.get("naming_scheme")
    if ns_raw:
        naming_scheme = NamingScheme(
            style=ns_raw["style"],
            seed_pool=tuple(ns_raw.get("seed_pool") or ()),
            reserved_surnames=tuple(ns_raw.get("reserved_surnames") or ()),
            validator_regex=ns_raw.get("validator_regex", ""),
        )

    opening_pool = tuple(
        OpeningArchetype(value)
        for value in (data.get("opening_archetype_pool") or [a.value for a in OpeningArchetype])
    )

    cp_raw = data.get("cliffhanger_policy") or {}
    cliffhanger_policy = CliffhangerPolicy(
        no_repeat_within=int(cp_raw.get("no_repeat_within", 3)),
        allowed_types=tuple(
            CliffhangerType(value)
            for value in (cp_raw.get("allowed_types") or [t.value for t in CliffhangerType])
        ),
    )

    return ProjectInvariants(
        project_id=project_id,
        language=language,
        pov=data.get("pov", "close_third"),
        tense=data.get("tense", "past"),
        length_envelope=length_envelope,
        naming_scheme=naming_scheme,
        opening_archetype_pool=opening_pool,
        cliffhanger_policy=cliffhanger_policy,
        vocab_caps=dict(data.get("vocab_caps") or {}),
        banned_formulaic_phrases=tuple(data.get("banned_formulaic_phrases") or ()),
        forced_methodology_fragments=tuple(data.get("forced_methodology_fragments") or ()),
        antagonist_uniqueness=bool(data.get("antagonist_uniqueness", True)),
        hype_scheme=hype_scheme_from_dict(data.get("hype_scheme")),
    )


# ---------------------------------------------------------------------------
# Seeding — build invariants from project + settings + config defaults.
# ---------------------------------------------------------------------------

def _derive_length_envelope(settings_words: Any, language: str | None) -> LengthEnvelope:
    """Convert the existing ``generation.words_per_chapter`` block into a
    character-level envelope.

    The validator works on characters so it applies uniformly to Chinese and
    English drafts. The heuristic: for CJK languages 1 "word" ≈ 1.8 chars
    (given typical 2-char compounds); for English 1 word ≈ 5.5 chars.

    ``language`` may be None on legacy ProjectModel rows; treat it as
    Chinese (the default) so the envelope stays deterministic.
    """

    words_min = int(getattr(settings_words, "min", 5000))
    words_target = int(getattr(settings_words, "target", 6400))
    words_max = int(getattr(settings_words, "max", 7500))

    language_str = str(language or "zh-CN")
    if language_str.lower().startswith("en"):
        scale = 5.5
    else:
        scale = 1.0

    return LengthEnvelope(
        min_chars=int(words_min * scale),
        target_chars=int(words_target * scale),
        max_chars=int(words_max * scale),
    )


def infer_pov_from_sample(
    sample_text: str, language: str | None = None
) -> PovStyle:
    """Return the most plausible POV given a block of chapter text.

    Counts first-person vs third-person pronouns in the *narrative* portion
    (dialogue inside paired quotes is stripped first, because characters
    legitimately say "I" inside their own speech regardless of narrator POV).

    Returns ``"first"`` when first-person clearly dominates (ratio ≥ 1.5×),
    otherwise ``"close_third"``. We deliberately never infer
    ``"omniscient"`` — that's a deliberate authorial choice, not something
    to detect from pronouns.
    """

    import re as _re

    if not sample_text:
        return "close_third"

    lang = (language or "").lower()
    # Strip paired quotes so dialogue doesn't contaminate the signal.
    if lang.startswith("en"):
        # Straight double + curly double + single quotes.
        narrative = _re.sub(
            r"[\u201c\u201d\"][^\u201c\u201d\"]*[\u201c\u201d\"]",
            " ",
            sample_text,
        )
        first = len(_re.findall(r"\b(?:I|me|my|mine|myself)\b", narrative))
        third = len(_re.findall(
            r"\b(?:he|she|him|her|his|hers|himself|herself)\b",
            narrative,
            _re.IGNORECASE,
        ))
    else:
        # Chinese: 「」, 『』, curly double, curly single, straight double.
        narrative = _re.sub(
            r"[「『\u201c\u2018\"]"
            r"[^」』\u201d\u2019\"]*"
            r"[」』\u201d\u2019\"]",
            " ",
            sample_text,
        )
        first = sum(narrative.count(t) for t in ("我", "吾", "朕", "余"))
        third = sum(narrative.count(t) for t in ("他", "她", "它"))

    # Require a clear majority + a nonzero minimum signal to switch from default.
    if first >= 30 and first >= int(third * 1.5):
        return "first"
    return "close_third"


def seed_invariants(
    *,
    project_id: UUID,
    language: str | None,
    words_per_chapter: Any,
    pov: PovStyle | str = "close_third",
    tense: TenseStyle | str = "past",
    overrides: Mapping[str, Any] | None = None,
) -> ProjectInvariants:
    """Seed a ``ProjectInvariants`` from project data + global defaults.

    ``overrides`` is a free-form dict that can set any of: ``length_envelope``,
    ``naming_scheme``, ``banned_formulaic_phrases``, etc.  Used by the pipeline
    to bolt on category-specific adjustments without modifying this module.
    """

    overrides = dict(overrides or {})
    length_envelope = overrides.get("length_envelope") or _derive_length_envelope(
        words_per_chapter, language
    )

    # Normalize POV and language literals to the narrow set we support.
    pov_normalized: PovStyle
    if pov in ("first", "close_third", "omniscient"):
        pov_normalized = pov  # type: ignore[assignment]
    else:
        pov_normalized = "close_third"

    tense_normalized: TenseStyle
    tense_normalized = "present" if str(tense).lower().startswith("present") else "past"

    lang_normalized: Literal["zh-CN", "en"]
    if str(language).lower().startswith("en"):
        lang_normalized = "en"
    else:
        lang_normalized = "zh-CN"

    # hype_scheme precedence: explicit HypeScheme → raw preset overrides dict →
    # empty scheme (engine no-op). The preset path lets pipelines.py pass the
    # genre preset's writing_profile_overrides directly without pre-building.
    hype_scheme_override = overrides.get("hype_scheme")
    if isinstance(hype_scheme_override, HypeScheme):
        hype_scheme = hype_scheme_override
    elif isinstance(hype_scheme_override, Mapping):
        hype_scheme = hype_scheme_from_dict(hype_scheme_override)
    else:
        hype_scheme = hype_scheme_from_preset_overrides(
            overrides.get("preset_overrides")
        )

    return ProjectInvariants(
        project_id=project_id,
        language=lang_normalized,
        pov=pov_normalized,
        tense=tense_normalized,
        length_envelope=length_envelope,
        naming_scheme=overrides.get("naming_scheme"),
        opening_archetype_pool=tuple(overrides.get("opening_archetype_pool") or OpeningArchetype),
        cliffhanger_policy=overrides.get("cliffhanger_policy") or CliffhangerPolicy(),
        vocab_caps=dict(overrides.get("vocab_caps") or {}),
        banned_formulaic_phrases=tuple(overrides.get("banned_formulaic_phrases") or ()),
        forced_methodology_fragments=tuple(
            overrides.get("forced_methodology_fragments") or ()
        ),
        antagonist_uniqueness=bool(overrides.get("antagonist_uniqueness", True)),
        hype_scheme=hype_scheme,
    )
