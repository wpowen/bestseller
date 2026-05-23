"""Post-write dialogue voice gate.

The checks here are deterministic and cheap.  They do not try to replace an
LLM critic; they catch the framework-level failure modes that should never
ship: broad AI filler phrases, generic stage directions, flat ping-pong
rhythm, and missing non-answers.  Character voice is not validated by
requiring exact catchphrase hits; those are prompt examples unless a project
explicitly opts into lexical enforcement.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
import re
from statistics import mean

from bestseller.domain.dialogue_voice import (
    DialogueVoiceDNA,
    DialogueVoiceFinding,
    DialogueVoiceReport,
)
from bestseller.services.dialogue_archetypes import common_dialogue_forbidden_phrases

DIALOGUE_AI_FLAVOR_BLOCK_CODE = "DIALOGUE_AI_FLAVOR"

DIALOGUE_FORBIDDEN_PHRASE = "DIALOGUE_FORBIDDEN_PHRASE"
DIALOGUE_LLM_DEFAULT = "DIALOGUE_LLM_DEFAULT"
DIALOGUE_STAGE_DIR_ABUSE = "DIALOGUE_STAGE_DIR_ABUSE"
DIALOGUE_PING_PONG = "DIALOGUE_PING_PONG"
DIALOGUE_NO_NEGSPACE = "DIALOGUE_NO_NEGSPACE"
DIALOGUE_EXPLICIT_MARKERS_MISSING = "DIALOGUE_EXPLICIT_MARKERS_MISSING"

GENERIC_STAGE_DIRECTIONS: tuple[str, ...] = (
    "冷冷一笑",
    "淡淡地说",
    "冷冷开口",
    "缓缓道",
    "低声道",
    "冷笑",
    "苦笑",
    "微微一笑",
    "笑了笑",
    "勾起嘴角",
    "眼神冷峻",
    "目光如炬",
    "眼神复杂",
    "眼神冰冷",
    "握紧拳头",
    "紧握双手",
    "攥紧",
    "深吸一口气",
    "长舒一口气",
)

GENERIC_STAGE_DIRECTIONS_EN: tuple[str, ...] = (
    "smiled coldly",
    "said calmly",
    "said softly",
    "said quietly",
    "gave a bitter smile",
    "gave a small smile",
    "his eyes hardened",
    "her eyes hardened",
    "his expression was unreadable",
    "her expression was unreadable",
    "clenched his fists",
    "clenched her fists",
    "took a deep breath",
    "let out a breath",
)

NEGATIVE_SPACE_MARKERS: tuple[str, ...] = (
    "没说话",
    "没有说话",
    "不说话",
    "沉默",
    "半晌",
    "良久",
    "顿了顿",
    "停了停",
    "没答",
    "没有回答",
    "不答",
    "岔开",
    "反问",
    "只把",
    "只是把",
    "摇头",
    "摆手",
    "看向",
    "低头",
)

NEGATIVE_SPACE_MARKERS_EN: tuple[str, ...] = (
    "said nothing",
    "didn't answer",
    "did not answer",
    "no answer",
    "silence",
    "silent",
    "looked away",
    "looked down",
    "shook his head",
    "shook her head",
    "shrugged",
    "changed the subject",
    "deflected",
    "instead",
)

_QUOTE_RE = re.compile(r"[“\"](?P<quote>[^”\"]{1,160})[”\"]")


@dataclass(frozen=True)
class DialogueTurn:
    speaker: str | None
    quote: str
    paragraph_index: int

    @property
    def length(self) -> int:
        return len(re.sub(r"\s+", "", self.quote))


def check_dialogue_voice(
    chapter_text: str,
    *,
    chapter_position: int,
    profiles: Sequence[DialogueVoiceDNA],
    language: str | None = None,
) -> DialogueVoiceReport:
    if not chapter_text.strip() or not profiles:
        return DialogueVoiceReport(chapter_position=chapter_position, findings=())

    profile_by_name = {p.character_name: p for p in profiles if p.character_name}
    turns = _extract_dialogue_turns(chapter_text, profile_by_name.keys())
    effective_language = language or _infer_language(chapter_text)
    findings: list[DialogueVoiceFinding] = []

    findings.extend(_check_forbidden_phrases(turns, profiles))
    findings.extend(_check_llm_defaults(turns, language=effective_language))
    findings.extend(_check_stage_direction_abuse(chapter_text, language=effective_language))
    findings.extend(_check_symmetric_rhythm(turns))
    findings.extend(_check_negative_space(chapter_text, turns, language=effective_language))
    findings.extend(_check_explicit_marker_requirements(chapter_text, turns, profiles))

    return DialogueVoiceReport(
        chapter_position=chapter_position,
        findings=tuple(findings),
    )


def _extract_dialogue_turns(
    chapter_text: str,
    character_names: Sequence[str],
) -> tuple[DialogueTurn, ...]:
    turns: list[DialogueTurn] = []
    paragraphs = [p for p in re.split(r"\n+", chapter_text) if p.strip()]
    for idx, paragraph in enumerate(paragraphs):
        for match in _QUOTE_RE.finditer(paragraph):
            quote = match.group("quote").strip()
            if not quote:
                continue
            window_start = max(0, match.start() - 24)
            window_end = min(len(paragraph), match.end() + 24)
            window = paragraph[window_start:window_end]
            speaker = _guess_speaker(window, character_names)
            turns.append(DialogueTurn(speaker=speaker, quote=quote, paragraph_index=idx))
    return tuple(turns)


def _guess_speaker(window: str, character_names: Sequence[str]) -> str | None:
    best: tuple[int, str] | None = None
    for name in character_names:
        if not name or name not in window:
            continue
        pos = window.rfind(name)
        if best is None or pos > best[0]:
            best = (pos, name)
    return best[1] if best else None


def _check_forbidden_phrases(
    turns: Sequence[DialogueTurn],
    profiles: Sequence[DialogueVoiceDNA],
) -> list[DialogueVoiceFinding]:
    profile_by_name = {p.character_name: p for p in profiles}
    findings: list[DialogueVoiceFinding] = []
    for turn in turns:
        if not turn.speaker or turn.speaker not in profile_by_name:
            continue
        profile = profile_by_name[turn.speaker]
        for phrase in profile.all_forbidden_phrases:
            if phrase and phrase in turn.quote:
                findings.append(
                    DialogueVoiceFinding(
                        severity="critical",
                        code=DIALOGUE_FORBIDDEN_PHRASE,
                        detail=f"forbidden phrase '{phrase}' appears in dialogue",
                        character=turn.speaker,
                        line_index=turn.paragraph_index,
                        evidence=phrase,
                    )
                )
    return findings


def _check_llm_defaults(
    turns: Sequence[DialogueTurn],
    *,
    language: str,
) -> list[DialogueVoiceFinding]:
    findings: list[DialogueVoiceFinding] = []
    for turn in turns:
        quote = turn.quote if language.startswith("zh") else turn.quote.lower()
        for phrase in common_dialogue_forbidden_phrases(language):
            needle = phrase if language.startswith("zh") else phrase.lower()
            if needle and needle in quote:
                findings.append(
                    DialogueVoiceFinding(
                        severity="critical",
                        code=DIALOGUE_LLM_DEFAULT,
                        detail=f"generic LLM phrase '{phrase}' appears in dialogue",
                        character=turn.speaker,
                        line_index=turn.paragraph_index,
                        evidence=phrase,
                    )
                )
    return findings


def _check_stage_direction_abuse(
    chapter_text: str,
    *,
    language: str,
) -> list[DialogueVoiceFinding]:
    markers = GENERIC_STAGE_DIRECTIONS if language.startswith("zh") else GENERIC_STAGE_DIRECTIONS_EN
    haystack = chapter_text if language.startswith("zh") else chapter_text.lower()
    hits: list[str] = []
    for phrase in markers:
        needle = phrase if language.startswith("zh") else phrase.lower()
        hits.extend([phrase] * haystack.count(needle))
    if len(hits) < 2:
        return []
    return [
        DialogueVoiceFinding(
            severity="high",
            code=DIALOGUE_STAGE_DIR_ABUSE,
            detail=f"generic stage directions appear {len(hits)} times",
            evidence="、".join(hits[:6]),
        )
    ]


def _check_symmetric_rhythm(turns: Sequence[DialogueTurn]) -> list[DialogueVoiceFinding]:
    if len(turns) < 4:
        return []
    for start in range(0, len(turns) - 3):
        lengths = [max(turn.length, 1) for turn in turns[start : start + 4]]
        avg = mean(lengths)
        variance = mean((length - avg) ** 2 for length in lengths)
        coefficient = variance / (avg * avg) if avg else 0.0
        speakers = [turn.speaker for turn in turns[start : start + 4]]
        alternating = len(set(speakers)) >= 2 if all(speakers) else True
        if coefficient < 0.1 and alternating:
            return [
                DialogueVoiceFinding(
                    severity="high",
                    code=DIALOGUE_PING_PONG,
                    detail=(
                        "four consecutive dialogue turns have near-identical length; "
                        "add interruption, silence, action-answer, or asymmetry"
                    ),
                    line_index=turns[start].paragraph_index,
                    evidence=str(lengths),
                )
            ]
    return []


def _check_negative_space(
    chapter_text: str,
    turns: Sequence[DialogueTurn],
    *,
    language: str,
) -> list[DialogueVoiceFinding]:
    if len(turns) < 4:
        return []
    markers = NEGATIVE_SPACE_MARKERS if language.startswith("zh") else NEGATIVE_SPACE_MARKERS_EN
    haystack = chapter_text if language.startswith("zh") else chapter_text.lower()
    hits = sum(
        haystack.count(marker if language.startswith("zh") else marker.lower())
        for marker in markers
    )
    ratio = hits / max(len(turns), 1)
    if hits >= 2 or ratio >= 0.3:
        return []
    return [
        DialogueVoiceFinding(
            severity="high",
            code=DIALOGUE_NO_NEGSPACE,
            detail=(
                f"negative-space beats too sparse: {hits} marker(s) across "
                f"{len(turns)} dialogue turns"
            ),
            evidence=str(hits),
        )
    ]


def _check_explicit_marker_requirements(
    chapter_text: str,
    turns: Sequence[DialogueTurn],
    profiles: Sequence[DialogueVoiceDNA],
) -> list[DialogueVoiceFinding]:
    turns_by_speaker: dict[str, int] = {}
    for turn in turns:
        if turn.speaker:
            turns_by_speaker[turn.speaker] = turns_by_speaker.get(turn.speaker, 0) + 1
    findings: list[DialogueVoiceFinding] = []
    for profile in profiles:
        count = turns_by_speaker.get(profile.character_name, 0)
        if count < 3:
            continue
        marker_hits = 0
        if profile.enforce_lexical_markers:
            marker_hits += sum(chapter_text.count(phrase) for phrase in profile.pet_phrases)
            marker_hits += sum(chapter_text.count(marker) for marker in profile.regional_markers)
        if profile.enforce_body_tells:
            marker_hits += sum(chapter_text.count(marker) for marker in profile.body_tells)
        if (profile.enforce_lexical_markers or profile.enforce_body_tells) and marker_hits < 1:
            findings.append(
                DialogueVoiceFinding(
                    severity="high",
                    code=DIALOGUE_EXPLICIT_MARKERS_MISSING,
                    detail=(
                        f"{profile.character_name} has explicit marker enforcement "
                        f"but none appeared across {count} dialogue turns"
                    ),
                    character=profile.character_name,
                )
            )
    return findings


def _infer_language(text: str) -> str:
    cjk = len(re.findall(r"[\u4e00-\u9fff]", text))
    ascii_letters = len(re.findall(r"[A-Za-z]", text))
    return "zh-CN" if cjk >= ascii_letters else "en"


__all__ = [
    "DIALOGUE_AI_FLAVOR_BLOCK_CODE",
    "DIALOGUE_FORBIDDEN_PHRASE",
    "DIALOGUE_LLM_DEFAULT",
    "DIALOGUE_EXPLICIT_MARKERS_MISSING",
    "DIALOGUE_NO_NEGSPACE",
    "DIALOGUE_PING_PONG",
    "DIALOGUE_STAGE_DIR_ABUSE",
    "GENERIC_STAGE_DIRECTIONS",
    "NEGATIVE_SPACE_MARKERS",
    "DialogueTurn",
    "check_dialogue_voice",
]
