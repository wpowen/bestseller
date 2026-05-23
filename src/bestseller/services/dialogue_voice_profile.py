"""Parse character dialogue voice profiles from story-bible material."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
import logging
from pathlib import Path
import re
from typing import Any

import yaml

from bestseller.domain.dialogue_voice import (
    DialogueContextModulation,
    DialogueVoiceDNA,
    NegativeSpaceRule,
)
from bestseller.services.dialogue_archetypes import (
    COMMON_DIALOGUE_FORBIDDEN_PHRASES,
    infer_dialogue_archetype,
    instantiate_archetype,
)

logger = logging.getLogger(__name__)


def load_dialogue_voice_profiles(
    cast_md_path: str | Path,
    *,
    role_profiles: Sequence[Any] = (),
) -> tuple[DialogueVoiceDNA, ...]:
    """Load dialogue voice profiles from ``cast-and-promises.md``.

    Explicit ``voice_dna`` YAML wins.  When a character has no explicit block,
    the parser assigns a framework-level archetype from role clues, so dialogue
    voice remains a framework feature instead of a per-book manual patch.
    """

    path = Path(cast_md_path)
    if not path.exists():
        return ()
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        logger.warning("dialogue voice cast read failed: %s", exc)
        return ()
    return parse_dialogue_voice_profiles(text, role_profiles=role_profiles)


def parse_dialogue_voice_profiles(
    cast_md_text: str,
    *,
    role_profiles: Sequence[Any] = (),
) -> tuple[DialogueVoiceDNA, ...]:
    role_by_name = {str(getattr(p, "name", "")).strip(): p for p in role_profiles}
    profiles: list[DialogueVoiceDNA] = []
    sections = re.split(r"\n##\s+", cast_md_text)
    for section in sections[1:]:
        first_line, _, rest = section.partition("\n")
        name = first_line.strip()
        if not name:
            continue
        role_profile = role_by_name.get(name)
        abilities = tuple(getattr(role_profile, "abilities", ()) or ())
        reader_promise = str(getattr(role_profile, "reader_promise", "") or "")
        explicit = _extract_voice_payload(rest)
        if explicit:
            profile = dialogue_voice_from_mapping(name, explicit)
        else:
            archetype = _extract_inline_archetype(rest) or infer_dialogue_archetype(
                name=name,
                abilities=abilities,
                reader_promise=reader_promise,
                section_text=rest,
            )
            profile = instantiate_archetype(archetype, name)
        if profile is None:
            continue
        profiles.append(profile)
    return tuple(profiles)


def dialogue_voice_from_mapping(
    character_name: str,
    payload: Mapping[str, Any],
) -> DialogueVoiceDNA | None:
    archetype = str(
        payload.get("archetype")
        or payload.get("voice_archetype")
        or payload.get("inherits")
        or ""
    ).strip()
    overrides = _payload_to_overrides(payload)
    if archetype:
        profile = instantiate_archetype(archetype, character_name, overrides=overrides)
        if profile is not None:
            return profile
    return DialogueVoiceDNA(character_name=character_name, **overrides)


def _payload_to_overrides(payload: Mapping[str, Any]) -> dict[str, Any]:
    forbidden = _as_tuple(payload.get("forbidden_phrases"))
    inherit_forbidden = _as_tuple(payload.get("forbidden_phrases_inherit"))
    if inherit_forbidden:
        forbidden = tuple(dict.fromkeys((*forbidden, *inherit_forbidden)))
    if not forbidden:
        forbidden = COMMON_DIALOGUE_FORBIDDEN_PHRASES

    overrides: dict[str, Any] = {
        "archetype": str(payload.get("archetype") or payload.get("voice_archetype") or "").strip(),
        "register": str(payload.get("register") or ""),
        "voice_traits": _as_tuple(
            payload.get("voice_traits")
            or payload.get("speech_traits")
            or payload.get("dialogue_traits")
        ),
        "lexical_strategy": str(
            payload.get("lexical_strategy")
            or payload.get("diction_strategy")
            or payload.get("word_choice_strategy")
            or ""
        ),
        "sentence_length_zh": _length_pair(payload.get("sentence_length_zh")),
        "syntax_quirks": _as_tuple(payload.get("syntax_quirks")),
        "rhythm_rules": _as_tuple(payload.get("rhythm_rules")),
        "relationship_rules": _as_tuple(payload.get("relationship_rules")),
        "genre_adaptations": _as_tuple(payload.get("genre_adaptations")),
        "pet_phrases": _as_tuple(payload.get("pet_phrases") or payload.get("pet_phrases_pool")),
        "forbidden_phrases": forbidden,
        "enforce_lexical_markers": bool(payload.get("enforce_lexical_markers") or False),
        "vocab_ceiling": str(payload.get("vocab_ceiling") or ""),
        "vocab_floor": str(payload.get("vocab_floor") or ""),
        "speech_speed": str(payload.get("speech_speed") or payload.get("pace") or ""),
        "body_tells": _as_tuple(payload.get("body_tells") or payload.get("body_tells_pool")),
        "enforce_body_tells": bool(payload.get("enforce_body_tells") or False),
        "taboo_topics": _as_tuple(payload.get("taboo_topics")),
        "context_modulation": _parse_context_modulation(payload.get("context_modulation")),
        "negative_space": _parse_negative_space(payload.get("negative_space")),
        "regional_markers": _as_tuple(
            payload.get("regional_markers")
            or payload.get("region_markers")
            or payload.get("dialect_markers")
        ),
        "accent_profile": str(
            payload.get("accent_profile")
            or payload.get("accent")
            or payload.get("dialect")
            or ""
        ),
        "interpretation_rules": _as_tuple(
            payload.get("interpretation_rules")
            or payload.get("interpreting_rules")
            or payload.get("口译规则")
        ),
        "code_switching": str(payload.get("code_switching") or ""),
    }
    return {
        key: value
        for key, value in overrides.items()
        if value not in ("", (), None)
    }


def _extract_voice_payload(section: str) -> dict[str, Any] | None:
    fence = re.search(
        r"```(?:yaml|yml)?\s*\n(.*?voice_dna\s*:\s*.*?)(?:\n```)",
        section,
        re.DOTALL | re.IGNORECASE,
    )
    if fence:
        return _safe_yaml_payload(fence.group(1))

    label = re.search(
        r"(?:^|\n)(?:voice_dna|声纹|对白声纹)\s*[：:]\s*\n(?P<body>(?:[ \t]+.+\n?)+)",
        section,
    )
    if label:
        body = "voice_dna:\n" + label.group("body")
        return _safe_yaml_payload(body)

    inline = re.search(r"(?:voice_archetype|声纹原型|原型)\s*[：:]\s*([^\n]+)", section)
    if inline:
        return {"archetype": inline.group(1).strip()}
    return None


def _safe_yaml_payload(raw: str) -> dict[str, Any] | None:
    try:
        data = yaml.safe_load(raw) or {}
    except yaml.YAMLError as exc:
        logger.warning("dialogue voice yaml parse failed: %s", exc)
        return None
    if not isinstance(data, Mapping):
        return None
    payload = data.get("voice_dna") if "voice_dna" in data else data
    return dict(payload) if isinstance(payload, Mapping) else None


def _extract_inline_archetype(section: str) -> str | None:
    match = re.search(r"(?:voice_archetype|声纹原型|原型)\s*[：:]\s*([^\n]+)", section)
    return match.group(1).strip() if match else None


def _as_tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        normalized = value.replace("，", ",").replace("、", ",").replace("；", ",")
        return tuple(part.strip() for part in normalized.split(",") if part.strip())
    if isinstance(value, Iterable) and not isinstance(value, Mapping):
        return tuple(str(item).strip() for item in value if str(item).strip())
    return (str(value).strip(),) if str(value).strip() else ()


def _length_pair(value: Any) -> tuple[int, int] | None:
    if value is None:
        return None
    if isinstance(value, str):
        nums = [int(n) for n in re.findall(r"\d+", value)[:2]]
        return (nums[0], nums[1]) if len(nums) == 2 else None
    if isinstance(value, Sequence) and len(value) >= 2:
        try:
            return (int(value[0]), int(value[1]))
        except (TypeError, ValueError):
            return None
    return None


def _parse_context_modulation(value: Any) -> tuple[DialogueContextModulation, ...]:
    if not isinstance(value, Mapping):
        return ()
    items: list[DialogueContextModulation] = []
    for key, raw in value.items():
        if isinstance(raw, Mapping):
            items.append(
                DialogueContextModulation(
                    context=str(key),
                    sentence_length_zh=_length_pair(raw.get("sentence_length_zh")),
                    pace=str(raw.get("pace") or raw.get("speech_speed") or ""),
                    sample=str(raw.get("sample") or ""),
                    pet_phrase_density=str(raw.get("pet_phrase_density") or ""),
                    body_tell=str(raw.get("body_tell") or ""),
                )
            )
        else:
            items.append(DialogueContextModulation(context=str(key), sample=str(raw)))
    return tuple(items)


def _parse_negative_space(value: Any) -> tuple[NegativeSpaceRule, ...]:
    if not value:
        return ()
    rules: list[NegativeSpaceRule] = []
    if isinstance(value, Mapping):
        for key, response in value.items():
            rules.append(NegativeSpaceRule(condition=str(key), response=str(response)))
        return tuple(rules)
    if isinstance(value, Iterable) and not isinstance(value, str):
        for item in value:
            if isinstance(item, Mapping):
                condition = str(item.get("condition") or item.get("when") or "")
                response = str(item.get("response") or item.get("answer") or "")
                if condition or response:
                    rules.append(NegativeSpaceRule(condition=condition, response=response))
            elif str(item).strip():
                rules.append(NegativeSpaceRule(condition="", response=str(item).strip()))
    return tuple(rules)


__all__ = [
    "dialogue_voice_from_mapping",
    "load_dialogue_voice_profiles",
    "parse_dialogue_voice_profiles",
]
