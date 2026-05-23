"""Character Role Compliance Gate — detect role drift in a chapter.

Catches the failure mode where a chapter writes a character outside
their declared identity. The trigger case: ch1 of 青囊不语问阴阳 closed
with "他就要把那笔账查清" (detective tone) without using the protagonist's
declared abilities (阴阳眼 / 罗盘 / 青囊 / 符 / 账法). cast file
explicitly forbids "被鬼追着跑的普通受害者; 他必须用方法论主动拆局".

The gate reads ``cast-and-promises.md``, parses each character's:
    * **外显能力** (declared abilities)
    * **禁止** (forbidden patterns / tones)
    * **读者承诺** (reader promise — informs tone expectations)

For each on-page character (subject name appears N times in chapter):
    1. Forbidden patterns / phrases hit → critical violation
    2. Character is present but NONE of their declared abilities appear
       in the chapter → high "skill_absent" violation
    3. Optional: tone-mismatch — if a character's role is supernatural
       and the chapter uses pure detective vocabulary → high

Block code: ``CHARACTER_ROLE_DRIFT`` — eligible for auto-repair.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
import logging
from pathlib import Path
import re

from bestseller.domain.dialogue_voice import DialogueVoiceDNA

logger = logging.getLogger(__name__)


CHARACTER_ROLE_DRIFT_BLOCK_CODE = "CHARACTER_ROLE_DRIFT"


@dataclass(frozen=True)
class CharacterProfile:
    """One character's declared identity contract."""

    name: str
    abilities: tuple[str, ...]
    inner_wound: str = ""
    reader_promise: str = ""
    forbidden_phrases: tuple[str, ...] = ()
    # Tone vocabulary the character SHOULD use (e.g. 风水师 → 阴阳眼/罗盘/符/青囊)
    expected_tone_markers: tuple[str, ...] = ()
    # Tone vocabulary that conflicts with the character's role
    conflicting_tone_markers: tuple[str, ...] = ()
    # Character-level dialogue voice DNA. This is framework-level speech
    # behavior, separate from role/ability compliance.
    dialogue_voice: DialogueVoiceDNA | None = None
    # Minimum presence threshold (mentions) to consider "on page"
    on_page_threshold: int = 2


@dataclass(frozen=True)
class RoleDriftFinding:
    character: str
    severity: str  # "critical" | "high"
    drift_type: str
    detail: str
    evidence: str = ""


@dataclass(frozen=True)
class CharacterRoleReport:
    chapter_position: int
    findings: tuple[RoleDriftFinding, ...]

    @property
    def passed(self) -> bool:
        return not any(f.severity == "critical" for f in self.findings)

    @property
    def has_critical(self) -> bool:
        return any(f.severity == "critical" for f in self.findings)


# ---------- cast loader ----------


# Generic tone vocabulary maps — used when cast file doesn't enumerate them.
_FENG_SHUI_TONE_MARKERS: tuple[str, ...] = (
    "阴阳眼", "罗盘", "青囊", "符", "符纸", "黄符", "符法", "符箓",
    "茅山", "镇魂", "三短一长",
    "铜钱", "桃木", "朱砂", "镜眼",
    "认账", "账法", "账页",
    "方位", "坤位", "兑位", "震位",
    "气", "煞", "煞气", "阴气",
)

_DETECTIVE_TONE_MARKERS: tuple[str, ...] = (
    "查案", "破案", "审讯", "审问", "侦查", "线索链", "证据链",
    "立案", "案情", "凶手", "嫌疑人",
)


def load_character_profiles(
    cast_md_path: str | Path,
) -> tuple[CharacterProfile, ...]:
    """Parse cast-and-promises.md into CharacterProfile tuples.

    The markdown follows a known pattern: ``## CharacterName`` followed
    by labelled paragraphs like:
        外显能力：...
        内在伤口：...
        读者承诺：...
        禁止：...
    """

    path = Path(cast_md_path)
    if not path.exists():
        return ()

    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        logger.warning("cast md read failed: %s", exc)
        return ()

    profiles: list[CharacterProfile] = []
    sections = re.split(r"\n##\s+", text)
    for section in sections[1:]:  # first section is doc header
        first_line, _, rest = section.partition("\n")
        name = first_line.strip()
        if not name:
            continue

        abilities = _extract_list_after_label(rest, ("外显能力",))
        forbidden = _extract_list_after_label(rest, ("禁止",))
        inner_wound = _extract_first_after_label(rest, ("内在伤口",))
        reader_promise = _extract_first_after_label(rest, ("读者承诺", "承诺"))

        # Heuristic tone markers for known role types:
        expected_tone, conflicting_tone = _infer_tone_markers(
            abilities, reader_promise
        )

        profiles.append(
            CharacterProfile(
                name=name,
                abilities=abilities,
                inner_wound=inner_wound,
                reader_promise=reader_promise,
                forbidden_phrases=forbidden,
                expected_tone_markers=expected_tone,
                conflicting_tone_markers=conflicting_tone,
            )
        )

    try:
        from bestseller.services.dialogue_voice_profile import (
            parse_dialogue_voice_profiles,
        )

        voice_profiles = parse_dialogue_voice_profiles(text, role_profiles=profiles)
        voice_by_name = {profile.character_name: profile for profile in voice_profiles}
        profiles = [
            replace(profile, dialogue_voice=voice_by_name.get(profile.name))
            for profile in profiles
        ]
    except Exception:
        logger.debug("dialogue voice profile parse failed (non-fatal)", exc_info=True)

    return tuple(profiles)


def _extract_list_after_label(text: str, labels: tuple[str, ...]) -> tuple[str, ...]:
    for label in labels:
        match = re.search(rf"{label}[：:](.+?)(?=\n\n|\n##|\Z)", text, re.DOTALL)
        if match:
            raw = match.group(1).strip()
            # Split by 、 ， , ; ； . newlines
            parts = re.split(r"[、，,;；\n]+", raw)
            return tuple(p.strip().rstrip("。") for p in parts if p.strip())
    return ()


def _extract_first_after_label(text: str, labels: tuple[str, ...]) -> str:
    for label in labels:
        match = re.search(rf"{label}[：:](.+?)(?=\n\n|\n##|\Z)", text, re.DOTALL)
        if match:
            return match.group(1).strip()
    return ""


def _infer_tone_markers(
    abilities: tuple[str, ...],
    reader_promise: str,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Infer expected + conflicting tone markers from abilities + promise."""

    ability_text = " ".join(abilities) + " " + reader_promise

    expected: list[str] = []
    conflicting: list[str] = []

    feng_shui_signals = (
        "阴阳眼", "罗盘", "青囊", "符", "茅山", "镜眼", "账页", "推理",
    )
    if any(sig in ability_text for sig in feng_shui_signals):
        expected.extend(_FENG_SHUI_TONE_MARKERS)
        # 风水师 不应该写成纯侦探腔
        conflicting.extend(_DETECTIVE_TONE_MARKERS)

    return tuple(dict.fromkeys(expected)), tuple(dict.fromkeys(conflicting))


# ---------- gate ----------


def check_character_role_compliance(
    chapter_text: str,
    *,
    chapter_position: int,
    profiles: Sequence[CharacterProfile],
) -> CharacterRoleReport:
    """Scan chapter for role drift across all listed characters."""

    if not chapter_text.strip() or not profiles:
        return CharacterRoleReport(
            chapter_position=chapter_position, findings=()
        )

    findings: list[RoleDriftFinding] = []

    for profile in profiles:
        if not profile.name:
            continue
        mention_count = chapter_text.count(profile.name)
        if mention_count < profile.on_page_threshold:
            continue

        # 1. Forbidden phrases / patterns
        for forbidden in profile.forbidden_phrases:
            if not forbidden:
                continue
            # Try as regex first (some禁止 are descriptive, others may be regex).
            hit_str = ""
            try:
                pattern = re.compile(forbidden)
                m = pattern.search(chapter_text)
                if m:
                    hit_str = m.group(0)
            except re.error:
                if forbidden in chapter_text:
                    hit_str = forbidden
            if hit_str:
                findings.append(
                    RoleDriftFinding(
                        character=profile.name,
                        severity="critical",
                        drift_type="forbidden_pattern",
                        detail=(
                            f"{profile.name}: '禁止' rule violated — "
                            f"found '{hit_str}' (rule: {forbidden[:60]!r})"
                        ),
                        evidence=hit_str,
                    )
                )

        # 2. Abilities present in chapter?
        if profile.abilities:
            abilities_hit = sum(
                1 for a in profile.abilities if a and a in chapter_text
            )
            if abilities_hit == 0:
                # The character is on-page but uses NONE of their declared
                # abilities. That's role drift.
                findings.append(
                    RoleDriftFinding(
                        character=profile.name,
                        severity="high",
                        drift_type="ability_absent",
                        detail=(
                            f"{profile.name} is on page ({mention_count} mentions) "
                            f"but none of declared abilities used: "
                            f"{list(profile.abilities)[:5]}"
                        ),
                    )
                )

        # 3. Conflicting tone markers (e.g. 风水师 写成纯侦探腔)
        if profile.conflicting_tone_markers and profile.expected_tone_markers:
            conflict_hits = sum(
                1
                for marker in profile.conflicting_tone_markers
                if marker in chapter_text
            )
            expected_hits = sum(
                1
                for marker in profile.expected_tone_markers
                if marker in chapter_text
            )
            if conflict_hits >= 1 and expected_hits == 0:
                findings.append(
                    RoleDriftFinding(
                        character=profile.name,
                        severity="high",
                        drift_type="tone_mismatch",
                        detail=(
                            f"{profile.name} chapter uses conflicting tone "
                            f"({conflict_hits} hits, e.g. detective vocabulary) "
                            f"but expected tone (e.g. supernatural craft) "
                            f"is absent"
                        ),
                    )
                )

    return CharacterRoleReport(
        chapter_position=chapter_position,
        findings=tuple(findings),
    )


def render_character_role_block(
    profiles: Sequence[CharacterProfile],
    *,
    language: str = "zh-CN",
    max_chars_per_profile: int = 200,
) -> str:
    """Render character role contracts as a writing-prompt block."""

    if not profiles:
        return ""

    if language.lower().startswith("zh"):
        lines = ["【角色定位锁定 — 必须遵守】"]
        for profile in profiles[:8]:
            if not profile.abilities and not profile.forbidden_phrases:
                continue
            lines.append(f"  · {profile.name}:")
            if profile.abilities:
                lines.append(
                    "      能力（必须有 ≥ 1 项实际使用）: "
                    + "、".join(profile.abilities[:5])
                )
            if profile.forbidden_phrases:
                lines.append(
                    "      严禁: "
                    + "、".join(
                        f"'{p[:30]}'" for p in profile.forbidden_phrases[:3]
                    )
                )
            if profile.expected_tone_markers:
                lines.append(
                    "      期望腔调（≥ 1 个出现）: "
                    + "、".join(profile.expected_tone_markers[:6])
                )
            if profile.conflicting_tone_markers:
                lines.append(
                    "      冲突腔调（避免）: "
                    + "、".join(profile.conflicting_tone_markers[:4])
                )
        lines.append(
            "- 重写后角色行为必须命中其能力之一，禁止用侦探腔代替术法腔。"
        )
        return "\n".join(lines)

    return "[Character role contracts]"


def render_character_role_violations_block(
    report: CharacterRoleReport,
    *,
    language: str = "zh-CN",
) -> str:
    """Render violations for rewrite prompt."""

    if not report.findings:
        return ""
    if language.lower().startswith("zh"):
        lines = ["【角色定位门禁 — 本章必须修复】"]
        for f in report.findings[:6]:
            sev = "✗" if f.severity == "critical" else "⚠"
            lines.append(f"  · {sev} [{f.drift_type}] {f.detail}")
        lines.append(
            "- 重写时必须让在场角色使用其声明的能力（如阴阳眼/罗盘/青囊/符），"
            "避免侦探腔（如查案/破案/审讯）替代术法腔。"
        )
        return "\n".join(lines)
    return f"[Character role drift: {len(report.findings)} findings]"


__all__ = [
    "CHARACTER_ROLE_DRIFT_BLOCK_CODE",
    "CharacterProfile",
    "CharacterRoleReport",
    "RoleDriftFinding",
    "check_character_role_compliance",
    "load_character_profiles",
    "render_character_role_block",
    "render_character_role_violations_block",
]
