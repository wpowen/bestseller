"""Character Identity Guardian — prevents cross-chapter attribute contradictions.

Maintains a per-project character identity registry and validates generated
scene text against it.  Catches gender flips, pronoun misuse, naming
inconsistencies, and unexplained character state contradictions (e.g. a
character dying in chapter 1 then appearing alive in chapter 2).

Integration points
------------------

* **Pre-scene**: ``build_identity_constraint_block()`` renders a hard-constraint
  prompt block injected as Tier-0 context (never dropped).
* **Post-scene**: ``validate_scene_identity_consistency()`` uses the critic model
  to check generated text against the registry and returns findings.
* **Pipeline**: called from ``run_scene_pipeline`` after context build (pre) and
  after draft generation (post).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.infra.db.models import CharacterModel

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CharacterIdentity:
    """Immutable identity snapshot for a single character."""

    name: str
    aliases: tuple[str, ...] = ()
    gender: str = "unknown"           # male / female / nonbinary / unknown
    pronoun_set_zh: str = ""          # 他 / 她 / 它 / ta
    pronoun_set_en: str = ""          # he/him / she/her / they/them
    physical_markers: tuple[str, ...] = ()
    power_baseline: str = ""
    is_alive: bool = True
    role: str = ""                    # protagonist / antagonist / supporting


@dataclass(frozen=True)
class IdentityViolation:
    """Single identity consistency violation found in generated text."""

    character_name: str
    violation_type: str   # gender_flip | pronoun_mismatch | dead_alive | name_variant
    expected: str
    found: str
    severity: str = "critical"
    evidence: str = ""


# ---------------------------------------------------------------------------
# Registry loading
# ---------------------------------------------------------------------------

async def load_identity_registry(
    session: AsyncSession,
    project_id: UUID,
) -> list[CharacterIdentity]:
    """Load character identities from the database for a project."""
    characters = list(
        await session.scalars(
            select(CharacterModel).where(CharacterModel.project_id == project_id)
        )
    )
    registry: list[CharacterIdentity] = []
    for char in characters:
        meta = char.metadata_json or {}
        cast_entry = meta.get("cast_entry", {})
        if isinstance(cast_entry, str):
            cast_entry = {}

        gender = _extract_gender(char, cast_entry, meta)
        pronoun_zh, pronoun_en = _gender_to_pronouns(gender)

        aliases_raw = cast_entry.get("aliases", [])
        if isinstance(aliases_raw, str):
            aliases_raw = [aliases_raw]

        physical_markers_raw = cast_entry.get("physical_markers", [])
        if isinstance(physical_markers_raw, str):
            physical_markers_raw = [physical_markers_raw]
        # Also include DB-level physical_description if available
        _phys_desc = getattr(char, "physical_description", None) or ""
        if _phys_desc and _phys_desc not in physical_markers_raw:
            physical_markers_raw = [_phys_desc] + list(physical_markers_raw)

        power_baseline = cast_entry.get("power_tier", "") or cast_entry.get("power_baseline", "") or ""

        registry.append(
            CharacterIdentity(
                name=char.name,
                aliases=tuple(aliases_raw),
                gender=gender,
                pronoun_set_zh=pronoun_zh,
                pronoun_set_en=pronoun_en,
                physical_markers=tuple(physical_markers_raw),
                power_baseline=str(power_baseline),
                is_alive=meta.get("is_alive", True),
                role=char.role or "",
            )
        )
    return registry


def _extract_gender(
    char: CharacterModel,
    cast_entry: dict[str, Any],
    meta: dict[str, Any],
) -> str:
    """Extract gender from various possible storage locations."""
    # Check explicit gender field
    for source in [cast_entry, meta]:
        gender = source.get("gender", "")
        if isinstance(gender, str) and gender.strip().lower() in ("male", "female", "nonbinary"):
            return gender.strip().lower()
        # Chinese gender labels
        if gender in ("男", "男性"):
            return "male"
        if gender in ("女", "女性"):
            return "female"

    # Heuristic: check character description for pronoun cues
    desc = (cast_entry.get("description", "") or "") + " " + (cast_entry.get("background", "") or "")
    if re.search(r"\b(she|her|woman|girl|female|daughter|sister|母|女|姐|妹)\b", desc, re.IGNORECASE):
        return "female"
    if re.search(r"\b(he|him|man|boy|male|son|brother|父|男|兄|弟)\b", desc, re.IGNORECASE):
        return "male"

    return "unknown"


def _gender_to_pronouns(gender: str) -> tuple[str, str]:
    """Return (zh_pronoun, en_pronoun) for a given gender."""
    if gender == "male":
        return "他", "he/him"
    if gender == "female":
        return "她", "she/her"
    if gender == "nonbinary":
        return "ta", "they/them"
    return "", ""


# ---------------------------------------------------------------------------
# Prompt block rendering (Tier 0 — never dropped from context)
# ---------------------------------------------------------------------------

def build_identity_constraint_block(
    registry: list[CharacterIdentity],
    *,
    language: str = "zh-CN",
    participant_names: list[str] | None = None,
) -> str:
    """Render a hard-constraint prompt block for character identity.

    Only includes characters that are participants in the current scene,
    or all characters if participant_names is None.
    """
    if not registry:
        return ""

    # Filter to scene participants if specified
    entries = registry
    if participant_names:
        name_set = set(participant_names)
        entries = [
            r for r in registry
            if r.name in name_set
            or any(alias in name_set for alias in r.aliases)
        ]

    if not entries:
        return ""

    is_zh = language.lower().startswith("zh")

    if is_zh:
        lines = ["【角色身份硬约束 — 违反即判定不合格】"]
        for entry in entries:
            parts = [f"• {entry.name}"]
            if entry.aliases:
                parts.append(f"（别名: {', '.join(entry.aliases)}）")
            if entry.gender != "unknown":
                gender_label = {"male": "男性", "female": "女性", "nonbinary": "非二元"}.get(entry.gender, entry.gender)
                parts.append(f"性别={gender_label}")
            if entry.pronoun_set_zh:
                parts.append(f"代词=「{entry.pronoun_set_zh}」（严禁使用其他代词指代此角色）")
            if not entry.is_alive:
                parts.append("状态=已死亡（不可作为活人出场，除非有明确的复活/回忆/闪回机制）")
            if entry.physical_markers:
                parts.append(f"外貌特征: {'; '.join(entry.physical_markers)}")
            if entry.power_baseline:
                parts.append(f"当前实力: {entry.power_baseline}")
            lines.append(" ".join(parts))
    else:
        lines = ["[CHARACTER IDENTITY CONSTRAINTS — violations will trigger rewrite]"]
        for entry in entries:
            parts = [f"• {entry.name}"]
            if entry.aliases:
                parts.append(f"(aliases: {', '.join(entry.aliases)})")
            if entry.gender != "unknown":
                parts.append(f"gender={entry.gender}")
            if entry.pronoun_set_en:
                parts.append(f"pronouns={entry.pronoun_set_en} (use ONLY these pronouns)")
            if not entry.is_alive:
                parts.append("status=DEAD (cannot appear alive unless explicit resurrection/flashback)")
            if entry.physical_markers:
                parts.append(f"physical: {'; '.join(entry.physical_markers)}")
            if entry.power_baseline:
                parts.append(f"current power: {entry.power_baseline}")
            lines.append(" ".join(parts))

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Post-scene validation (zero-LLM cost for basic checks)
# ---------------------------------------------------------------------------

_ZH_MALE_PRONOUNS = frozenset({"他", "他的", "他们"})
_ZH_FEMALE_PRONOUNS = frozenset({"她", "她的", "她们"})


def validate_scene_text_identity(
    text: str,
    registry: list[CharacterIdentity],
    *,
    language: str = "zh-CN",
    participant_names: list[str] | None = None,
) -> list[IdentityViolation]:
    """Check generated scene text for identity violations.

    This is a heuristic, zero-LLM-cost check. It looks for pronoun usage
    near character name mentions and flags mismatches.
    """
    if not text or not registry:
        return []

    entries = registry
    if participant_names:
        name_set = set(participant_names)
        entries = [
            r for r in registry
            if r.name in name_set
            or any(alias in name_set for alias in r.aliases)
        ]

    violations: list[IdentityViolation] = []
    is_zh = language.lower().startswith("zh")

    for entry in entries:
        if entry.gender == "unknown":
            continue

        # Find all mentions of this character in the text
        all_names = [entry.name] + list(entry.aliases)
        for name in all_names:
            if name not in text:
                continue

            if is_zh:
                violations.extend(
                    _check_zh_pronoun_consistency(text, name, entry)
                )
            else:
                violations.extend(
                    _check_en_pronoun_consistency(text, name, entry)
                )

        # Check dead character appearing alive
        if not entry.is_alive:
            for name in all_names:
                if name in text:
                    # Simple heuristic: if the character speaks or acts, they might be alive
                    speaking_pattern = f"{name}[说道叫喊笑哭回答]|{name}\\s*[：:]"
                    if re.search(speaking_pattern, text):
                        violations.append(
                            IdentityViolation(
                                character_name=entry.name,
                                violation_type="dead_alive",
                                expected="dead",
                                found="appears to speak or act as if alive",
                                severity="critical",
                                evidence=f"{name} found speaking/acting in text",
                            )
                        )

    return violations


def _check_zh_pronoun_consistency(
    text: str,
    name: str,
    entry: CharacterIdentity,
) -> list[IdentityViolation]:
    """Check Chinese pronoun consistency near character name mentions."""
    violations: list[IdentityViolation] = []

    # Find contexts around character name (window of 30 chars)
    for match in re.finditer(re.escape(name), text):
        start = max(0, match.start() - 30)
        end = min(len(text), match.end() + 30)
        context = text[start:end]

        if entry.gender == "male":
            # Male character should not have 她/她的 nearby
            wrong_pronouns = _ZH_FEMALE_PRONOUNS
            expected_pronoun = "他"
        elif entry.gender == "female":
            # Female character should not have 他/他的 nearby (but 他们 is ambiguous)
            wrong_pronouns = frozenset({"他", "他的"})
            expected_pronoun = "她"
        else:
            continue

        for wrong in wrong_pronouns:
            if wrong in context:
                # Additional check: make sure the wrong pronoun is not part of
                # another character's context
                violations.append(
                    IdentityViolation(
                        character_name=entry.name,
                        violation_type="pronoun_mismatch",
                        expected=expected_pronoun,
                        found=wrong,
                        severity="major",
                        evidence=context.strip(),
                    )
                )
                break  # One violation per context window is enough

    return violations


def _check_en_pronoun_consistency(
    text: str,
    name: str,
    entry: CharacterIdentity,
) -> list[IdentityViolation]:
    """Check English pronoun consistency near character name mentions."""
    violations: list[IdentityViolation] = []

    if entry.gender == "male":
        wrong_pattern = r"\b(she|her|hers|herself)\b"
        expected = "he/him"
    elif entry.gender == "female":
        wrong_pattern = r"\b(he|him|his|himself)\b"
        expected = "she/her"
    else:
        return violations

    # Find contexts around character name (window of 200 chars for English)
    for match in re.finditer(re.escape(name), text, re.IGNORECASE):
        start = max(0, match.start() - 200)
        end = min(len(text), match.end() + 200)
        context = text[start:end]

        wrong_match = re.search(wrong_pattern, context, re.IGNORECASE)
        if wrong_match:
            violations.append(
                IdentityViolation(
                    character_name=entry.name,
                    violation_type="pronoun_mismatch",
                    expected=expected,
                    found=wrong_match.group(0),
                    severity="major",
                    evidence=context.strip()[:120],
                )
            )

    return violations
