"""Platform Profiles loader (``config/platform_profiles.yaml``).

Provides typed access to per-platform writing rules (七猫 / 起点 / 番茄):

* ``VoicePreference`` — sentence / paragraph / dialogue tuning
* ``PacingPreference`` — per-chapter word range, payoff cadence
* ``OpeningSigningGate`` — hard position gates for the signing sample
* ``PulseWords`` — heart-rate lexicon used by detector modules
* ``OpeningHook`` — entries of the cross-platform hook bank

Two utility helpers are exposed for the pipeline glue layer:

* :func:`resolve_platform_id` — normalise free-form platform names
* :func:`parse_rejection_reason` — map a platform rejection phrase to
  the internal ``cause_id`` consumed by ``rejection_repair_playbook``
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from bestseller.services.quality_levers._loader import (
    as_dict,
    as_int,
    as_str,
    as_str_tuple,
    load_yaml,
    normalize_platform_id,
)


_CONFIG_FILENAME = "platform_profiles.yaml"


@dataclass(frozen=True)
class VoicePreference:
    """Platform-specific voice / dialogue tuning."""

    sentence_style: str
    paragraph_length: str
    dialogue_ratio: str
    emotional_expression: str
    pacing_unit: str
    taboo: tuple[str, ...]


@dataclass(frozen=True)
class PacingPreference:
    """Platform-specific chapter pacing and payoff cadence."""

    chapter_word_count: str
    payoff_density: str
    hook_density: str
    cliffhanger_position: str
    chapter_word_min: int
    chapter_word_max: int


@dataclass(frozen=True)
class HardPositionGate:
    """A single ``前 N 字内必须 X`` rule from the signing sample."""

    position_words: int
    rule: str


@dataclass(frozen=True)
class OpeningSigningGate:
    """Platform-specific signing-sample hard gates."""

    sample_words: int
    hard_position_gates: tuple[HardPositionGate, ...]
    first_three_chapters_must: tuple[str, ...]
    first_10000_words_must: tuple[str, ...]


@dataclass(frozen=True)
class PlatformProfile:
    """All tunings derived from one ``platforms.<id>`` entry."""

    platform_id: str
    display_name: str
    audience_pattern: str
    reading_environment: str
    voice_preference: VoicePreference
    pacing_preference: PacingPreference
    opening_signing_gate: OpeningSigningGate
    rejection_signals_to_cause_map: dict[str, str]


@dataclass(frozen=True)
class OpeningHook:
    """An entry from the cross-platform ``opening_hook_bank``."""

    hook_id: str
    name: str
    pattern: str
    strength: int
    example_first_line: str = ""


@dataclass(frozen=True)
class PulseWords:
    """Heart-rate lexicon used by quality detectors."""

    body_signal: tuple[str, ...]
    internal_pulse: tuple[str, ...]
    active_decision: tuple[str, ...]

    @property
    def all_words(self) -> tuple[str, ...]:
        return self.body_signal + self.internal_pulse + self.active_decision


@dataclass(frozen=True)
class PlatformProfilesConfig:
    """Full typed view over ``platform_profiles.yaml``."""

    version: str
    platforms: dict[str, PlatformProfile]
    opening_hooks: tuple[OpeningHook, ...]
    pulse_words: PulseWords


def _parse_hard_position_gates(raw: object) -> tuple[HardPositionGate, ...]:
    if not isinstance(raw, list):
        return ()
    gates: list[HardPositionGate] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        position_str = as_str(entry.get("position"))
        position_value = "".join(ch for ch in position_str if ch.isdigit())
        rule = as_str(entry.get("rule"))
        if not rule:
            continue
        gates.append(
            HardPositionGate(
                position_words=as_int(position_value, default=0),
                rule=rule,
            )
        )
    return tuple(gates)


def _parse_voice_preference(raw: object) -> VoicePreference:
    data = as_dict(raw)
    return VoicePreference(
        sentence_style=as_str(data.get("sentence_style")),
        paragraph_length=as_str(data.get("paragraph_length")),
        dialogue_ratio=as_str(data.get("dialogue_ratio")),
        emotional_expression=as_str(data.get("emotional_expression")),
        pacing_unit=as_str(data.get("pacing_unit")),
        taboo=as_str_tuple(data.get("taboo")),
    )


def _parse_chapter_word_range(text: str) -> tuple[int, int]:
    """Parse a string like ``"2500-4000"`` into ``(min, max)``.

    Falls back to ``(0, 0)`` when parsing fails so callers can detect
    a missing range with a simple ``range.min == 0`` check.
    """

    if not text:
        return (0, 0)
    parts = text.replace("，", ",").split("-")
    if len(parts) != 2:
        return (0, 0)
    try:
        return (int(parts[0].strip()), int(parts[1].strip()))
    except ValueError:
        return (0, 0)


def _parse_pacing_preference(raw: object) -> PacingPreference:
    data = as_dict(raw)
    word_count = as_str(data.get("chapter_word_count"))
    word_min, word_max = _parse_chapter_word_range(word_count)
    return PacingPreference(
        chapter_word_count=word_count,
        payoff_density=as_str(data.get("payoff_density")),
        hook_density=as_str(data.get("hook_density")),
        cliffhanger_position=as_str(data.get("cliffhanger_position")),
        chapter_word_min=word_min,
        chapter_word_max=word_max,
    )


def _parse_opening_signing_gate(raw: object) -> OpeningSigningGate:
    data = as_dict(raw)
    return OpeningSigningGate(
        sample_words=as_int(data.get("sample_words"), default=10000),
        hard_position_gates=_parse_hard_position_gates(data.get("hard_position_gates")),
        first_three_chapters_must=as_str_tuple(data.get("first_three_chapters_must")),
        first_10000_words_must=as_str_tuple(data.get("first_10000_words_must")),
    )


def _parse_platform(platform_id: str, raw: object) -> PlatformProfile:
    data = as_dict(raw)
    cause_map_raw = as_dict(data.get("rejection_signals_to_cause_map"))
    cause_map: dict[str, str] = {
        as_str(key): as_str(val)
        for key, val in cause_map_raw.items()
        if as_str(key) and as_str(val)
    }
    return PlatformProfile(
        platform_id=platform_id,
        display_name=as_str(data.get("display_name"), default=platform_id),
        audience_pattern=as_str(data.get("audience_pattern")),
        reading_environment=as_str(data.get("reading_environment")),
        voice_preference=_parse_voice_preference(data.get("voice_preference")),
        pacing_preference=_parse_pacing_preference(data.get("pacing_preference")),
        opening_signing_gate=_parse_opening_signing_gate(
            data.get("opening_signing_gate")
        ),
        rejection_signals_to_cause_map=cause_map,
    )


def _parse_opening_hooks(raw: object) -> tuple[OpeningHook, ...]:
    bank = as_dict(raw)
    hooks_raw = bank.get("hook_types")
    if not isinstance(hooks_raw, list):
        return ()
    hooks: list[OpeningHook] = []
    for entry in hooks_raw:
        if not isinstance(entry, dict):
            continue
        hook_id = as_str(entry.get("id"))
        if not hook_id:
            continue
        hooks.append(
            OpeningHook(
                hook_id=hook_id,
                name=as_str(entry.get("name")),
                pattern=as_str(entry.get("pattern")),
                strength=as_int(entry.get("strength"), default=0),
                example_first_line=as_str(entry.get("example_first_line")),
            )
        )
    return tuple(hooks)


def _parse_pulse_words(raw: object) -> PulseWords:
    data = as_dict(raw)
    return PulseWords(
        body_signal=as_str_tuple(data.get("body_signal")),
        internal_pulse=as_str_tuple(data.get("internal_pulse")),
        active_decision=as_str_tuple(data.get("active_decision")),
    )


@lru_cache(maxsize=1)
def load_platform_profiles() -> PlatformProfilesConfig:
    """Return the typed view over ``config/platform_profiles.yaml``."""

    raw = load_yaml(_CONFIG_FILENAME)
    platforms_raw = as_dict(raw.get("platforms"))
    platforms: dict[str, PlatformProfile] = {}
    for platform_id, profile_raw in platforms_raw.items():
        canonical = as_str(platform_id)
        if not canonical:
            continue
        platforms[canonical] = _parse_platform(canonical, profile_raw)
    return PlatformProfilesConfig(
        version=as_str(raw.get("version")),
        platforms=platforms,
        opening_hooks=_parse_opening_hooks(raw.get("opening_hook_bank")),
        pulse_words=_parse_pulse_words(raw.get("pulse_words")),
    )


def resolve_platform_id(platform: str | None) -> str | None:
    """Public re-export of :func:`_loader.normalize_platform_id`."""

    return normalize_platform_id(platform)


def parse_rejection_reason(
    *,
    platform: str | None,
    reason_text: str,
) -> str | None:
    """Map a platform rejection phrase to the internal ``cause_id``.

    Returns ``None`` when no matching signal is found so the caller
    can decide whether to skip the repair playbook or fall back to a
    generic ``weak_attraction`` cause.
    """

    platform_id = normalize_platform_id(platform)
    if not platform_id:
        return None
    config = load_platform_profiles()
    profile = config.platforms.get(platform_id)
    if profile is None:
        return None
    text = (reason_text or "").strip()
    if not text:
        return None
    # Prefer exact match, then substring containment, then containment
    # of the signal inside the input (cheap fuzzy match).
    cause_map = profile.rejection_signals_to_cause_map
    if text in cause_map:
        return cause_map[text]
    for signal, cause_id in cause_map.items():
        if signal and (signal in text or text in signal):
            return cause_id
    return None


def render_platform_profile_block(
    *,
    platform: str | None,
    chapter_number: int,
    language: str | None = None,
) -> str:
    """Render a prompt fragment summarising the platform tuning.

    Returns an empty string when the platform cannot be resolved or the
    project is in English, mirroring the legacy ``methodology.py``
    behaviour.
    """

    if (language or "").lower().startswith("en"):
        return ""
    platform_id = normalize_platform_id(platform)
    if not platform_id:
        return ""
    config = load_platform_profiles()
    profile = config.platforms.get(platform_id)
    if profile is None:
        return ""

    lines: list[str] = [f"【平台档案 · {profile.display_name}】"]
    voice = profile.voice_preference
    if voice.sentence_style or voice.dialogue_ratio:
        lines.append(
            "- 风格: "
            + "; ".join(
                segment
                for segment in (
                    f"句法 {voice.sentence_style}" if voice.sentence_style else "",
                    f"段长 {voice.paragraph_length}" if voice.paragraph_length else "",
                    f"对白比 {voice.dialogue_ratio}" if voice.dialogue_ratio else "",
                    f"情绪 {voice.emotional_expression}" if voice.emotional_expression else "",
                )
                if segment
            )
        )
    pacing = profile.pacing_preference
    if pacing.chapter_word_count:
        lines.append(
            f"- 节奏: 章字数 {pacing.chapter_word_count}; "
            f"勾子密度 {pacing.hook_density}; 章末勾子 {pacing.cliffhanger_position}"
        )
    if voice.taboo:
        lines.append("- 禁忌: " + "; ".join(voice.taboo))

    if chapter_number <= 1 and profile.opening_signing_gate.hard_position_gates:
        gate_lines = [
            f"前{gate.position_words}字: {gate.rule}"
            for gate in profile.opening_signing_gate.hard_position_gates
        ]
        lines.append("- 第一章签约门槛: " + "; ".join(gate_lines))
    if chapter_number <= 3 and profile.opening_signing_gate.first_three_chapters_must:
        lines.append(
            "- 前三章: "
            + "; ".join(profile.opening_signing_gate.first_three_chapters_must)
        )
    if chapter_number <= 10 and profile.opening_signing_gate.first_10000_words_must:
        lines.append(
            "- 前万字: "
            + "; ".join(profile.opening_signing_gate.first_10000_words_must)
        )

    return "\n".join(lines)
