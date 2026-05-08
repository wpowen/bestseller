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
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.infra.db.models import CharacterModel, ProjectModel

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
    """Load character identities from the database for a project.

    The characters table is the primary registry, but repaired/resumed
    projects can have a newer locked identity manifest before the character
    rows have been fully materialized. Merge that manifest as a fallback so
    pre-draft validation does not reject legitimate planned participants.
    """
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
        default_pronoun_zh, default_pronoun_en = _gender_to_pronouns(gender)
        pronoun_zh = _extract_pronoun(
            "pronoun_set_zh",
            cast_entry,
            meta,
            default=default_pronoun_zh,
        )
        pronoun_en = _extract_pronoun(
            "pronoun_set_en",
            cast_entry,
            meta,
            default=default_pronoun_en,
        )

        aliases_raw: list[str] = []
        for _raw_aliases in (
            cast_entry.get("aliases"),
            meta.get("aliases") if isinstance(meta, dict) else None,
        ):
            if isinstance(_raw_aliases, str):
                aliases_raw.append(_raw_aliases)
            elif isinstance(_raw_aliases, (list, tuple)):
                aliases_raw.extend(item for item in _raw_aliases if isinstance(item, str))
        aliases_raw = list(dict.fromkeys(alias.strip() for alias in aliases_raw if alias.strip()))

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
                is_alive=_character_is_alive(char, meta),
                role=char.role or "",
            )
        )
    seen_tokens = {
        _identity_registry_token(name)
        for entry in registry
        for name in _identity_names(entry)
        if _identity_registry_token(name)
    }
    project = await session.get(ProjectModel, project_id)
    manifest = None
    if project is not None and isinstance(project.metadata_json, dict):
        manifest = project.metadata_json.get("identity_manifest")
    if isinstance(manifest, list):
        for item in manifest:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            if not name:
                continue
            aliases = tuple(
                str(alias).strip()
                for alias in item.get("aliases", [])
                if isinstance(alias, str) and alias.strip()
            )
            tokens = {_identity_registry_token(name)}
            tokens.update(_identity_registry_token(alias) for alias in aliases)
            tokens.discard("")
            if tokens and tokens.issubset(seen_tokens):
                continue
            gender = _normalize_gender_label(item.get("gender"))
            default_pronoun_zh, default_pronoun_en = _gender_to_pronouns(gender)
            alive_status = str(item.get("alive_status") or item.get("status") or "").lower()
            registry.append(
                CharacterIdentity(
                    name=name,
                    aliases=aliases,
                    gender=gender,
                    pronoun_set_zh=str(item.get("pronoun_set_zh") or default_pronoun_zh),
                    pronoun_set_en=str(item.get("pronoun_set_en") or default_pronoun_en),
                    role=str(item.get("role") or ""),
                    is_alive=alive_status not in {"dead", "deceased", "死亡", "已死亡"},
                )
            )
            seen_tokens.update(tokens)
    return registry


def _identity_registry_token(value: str) -> str:
    return "".join(str(value or "").strip().lower().split())


def _extract_gender(
    char: CharacterModel,
    cast_entry: dict[str, Any],
    meta: dict[str, Any],
) -> str:
    """Extract gender from various possible storage locations."""
    for source in (cast_entry, meta):
        gender = _normalize_gender_label(source.get("gender") if isinstance(source, dict) else None)
        if gender != "unknown":
            return gender

    # Heuristic fallback for legacy rows where gender was never persisted. Keep
    # it narrow and use all character fields, because old cast specs often put
    # "师姐/妹妹/父亲" in role/background instead of a structured field.
    desc = _character_identity_text(char, cast_entry, meta)
    inferred = _infer_gender_from_text(desc)
    if inferred != "unknown":
        return inferred
    return "unknown"


def _normalize_gender_label(value: Any) -> str:
    if value is None or value == "":
        return "unknown"
    if not isinstance(value, str):
        return "unknown"
    raw = value.strip()
    if not raw:
        return "unknown"
    lowered = raw.lower()
    compact = re.sub(r"[\s_\-]+", "", lowered)
    if lowered in {"nonbinary", "non-binary", "non binary", "nb", "they/them", "genderfluid", "neutral"}:
        return "nonbinary"
    if compact in {"nonbinary", "theythem"} or any(marker in raw for marker in ("非二元", "无性别", "中性")):
        return "nonbinary"
    if lowered in {"male", "m", "man", "boy", "masculine", "he/him", "he", "him"}:
        return "male"
    if compact in {"male", "hehim"} or raw in {"男", "男性", "男主"}:
        return "male"
    if lowered in {"female", "f", "woman", "girl", "feminine", "she/her", "she", "her"}:
        return "female"
    if compact in {"female", "sheher"} or raw in {"女", "女性", "女主"}:
        return "female"
    return "unknown"


def _character_is_alive(char: CharacterModel, metadata: dict[str, Any]) -> bool:
    alive_status = str(getattr(char, "alive_status", "") or "").strip().lower()
    if alive_status in {"dead", "deceased", "死亡", "已死亡"}:
        return False
    if alive_status in {"alive", "missing", "unknown"}:
        return True
    if isinstance(metadata, dict) and "is_alive" in metadata:
        return bool(metadata.get("is_alive"))
    return True


def _extract_pronoun(
    key: str,
    cast_entry: dict[str, Any],
    meta: dict[str, Any],
    *,
    default: str,
) -> str:
    for source in (cast_entry, meta):
        value = source.get(key) if isinstance(source, dict) else None
        if isinstance(value, str) and value.strip():
            return value.strip()
    return default


def _character_identity_text(
    char: CharacterModel,
    cast_entry: dict[str, Any],
    meta: dict[str, Any],
) -> str:
    parts: list[str] = []
    for value in (
        getattr(char, "role", None),
        getattr(char, "background", None),
        getattr(char, "goal", None),
        getattr(char, "fear", None),
        getattr(char, "flaw", None),
        getattr(char, "strength", None),
        getattr(char, "secret", None),
        getattr(char, "arc_trajectory", None),
        getattr(char, "arc_state", None),
    ):
        if isinstance(value, str) and value.strip():
            parts.append(value.strip())
    for source in (cast_entry, meta):
        if not isinstance(source, dict):
            continue
        for key in (
            "role",
            "description",
            "background",
            "goal",
            "relationship_to_protagonist",
            "title",
            "identity",
        ):
            value = source.get(key)
            if isinstance(value, str) and value.strip():
                parts.append(value.strip())
    return " ".join(parts)


def _infer_gender_from_text(text: str) -> str:
    if not text:
        return "unknown"
    lowered = text.lower()
    if re.search(r"\b(she|her|woman|girl|female|daughter|sister|mother|wife|fiancee)\b", lowered):
        return "female"
    if re.search(r"\b(he|him|man|boy|male|son|brother|father|husband|fiance)\b", lowered):
        return "male"
    female_hits = ("女主", "女性", "少女", "女子", "姑娘", "师姐", "师妹", "姐姐", "妹妹", "母亲", "妻子", "未婚妻")
    male_hits = ("男主", "男性", "少年", "男子", "师兄", "师弟", "哥哥", "弟弟", "父亲", "丈夫", "未婚夫")
    if any(marker in text for marker in female_hits):
        return "female"
    if any(marker in text for marker in male_hits):
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
            if _entry_matches_name_set(r, name_set)
            or _entry_mentioned_in_text(r, text)
        ]

    violations: list[IdentityViolation] = []
    is_zh = language.lower().startswith("zh")
    names_by_entry = {entry.name: _identity_names(entry) for entry in entries}

    for entry in entries:
        if entry.gender == "unknown":
            continue

        # Find all mentions of this character in the text
        all_names = names_by_entry[entry.name]
        other_entries = [other for other in entries if other.name != entry.name]
        other_names = [
            name
            for other in other_entries
            for name in names_by_entry[other.name]
        ]
        for name in all_names:
            if name not in text:
                continue

            if is_zh:
                violations.extend(
                    _check_zh_pronoun_consistency(
                        _strip_zh_quoted_dialogue(text),
                        name,
                        entry,
                        other_entries=other_entries,
                        other_names=other_names,
                    )
                )
            else:
                violations.extend(
                    _check_en_pronoun_consistency(text, name, entry, other_names=other_names)
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


def _identity_names(entry: CharacterIdentity) -> list[str]:
    names: list[str] = []
    for raw in (entry.name, *entry.aliases):
        if isinstance(raw, str):
            name = raw.strip()
            if name and name not in names:
                names.append(name)
    return names


def _entry_matches_name_set(entry: CharacterIdentity, name_set: set[str]) -> bool:
    return any(name in name_set for name in _identity_names(entry))


def _entry_mentioned_in_text(entry: CharacterIdentity, text: str) -> bool:
    if not text:
        return False
    lowered = text.lower()
    for name in _identity_names(entry):
        if name in text or name.lower() in lowered:
            return True
    return False


def _check_zh_pronoun_consistency(
    text: str,
    name: str,
    entry: CharacterIdentity,
    *,
    other_entries: list[CharacterIdentity] | None = None,
    other_names: list[str] | None = None,
) -> list[IdentityViolation]:
    """Check Chinese pronoun consistency near character name mentions."""
    violations: list[IdentityViolation] = []

    competing_names = [item for item in (other_names or []) if item and item != name]
    competing_entries = list(other_entries or [])

    # Find contexts after character name. Keep this deliberately high precision:
    # Chinese mixed-gender scenes often contain object pronouns right after a
    # name ("苏瑶看着他"), and those must not be treated as a gender flip.
    for match in re.finditer(re.escape(name), text):
        if _zh_name_match_embedded_in_longer_name(text, name, match.start(), competing_names):
            continue
        start = match.start()
        left_context = text[max(0, start - 14):start]
        if _zh_name_mention_is_likely_object(left_context):
            continue
        end = min(len(text), match.end() + 60)
        context = text[start:end]
        right_context = text[match.end():end]

        if entry.gender == "male":
            # Male character should not have 她/她的 nearby
            wrong_pronouns = _ZH_FEMALE_PRONOUNS
            expected_pronoun = "他"
            found_gender = "female"
        elif entry.gender == "female":
            # Female character should not have 他/他的 nearby (but 他们 is ambiguous)
            wrong_pronouns = frozenset({"他", "他的"})
            expected_pronoun = "她"
            found_gender = "male"
        else:
            continue

        for wrong in sorted(wrong_pronouns, key=len, reverse=True):
            wrong_pos = _find_zh_pronoun(right_context, wrong)
            if wrong_pos < 0:
                continue
            before_wrong = right_context[:wrong_pos]
            if any(other in before_wrong for other in competing_names):
                continue
            if _zh_context_already_shifted_to_gender(before_wrong, found_gender=found_gender):
                continue
            if not _zh_wrong_pronoun_is_likely_subject(
                right_context,
                wrong_pos,
                wrong,
                found_gender=found_gender,
                competing_entries=competing_entries,
            ):
                continue
            if wrong in context:
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


_ZH_QUOTED_DIALOGUE_RE = re.compile(r"[“\"「『][^”\"」』]{0,500}[”\"」』]")
_ZH_STRONG_BOUNDARY_RE = re.compile(r"[。！？；\n]")
_ZH_PRONOUN_OBJECT_PREFIXES = (
    "从",
    "向",
    "朝",
    "对",
    "给",
    "把",
    "将",
    "被",
    "让",
    "替",
    "为",
    "问",
    "看",
    "看着",
    "盯着",
    "瞪着",
    "扫过",
    "扫向",
    "落在",
    "递给",
    "塞给",
    "交给",
    "松开",
    "握住",
    "攥住",
    "碰到",
    "触到",
    "直视",
    "背对着",
    "看向",
    "冲",
    "冲着",
    "拍了拍",
    "抓住",
    "拉住",
    "护住",
    "挡住",
    "救下",
    "带着",
    "跟着",
    "等着",
    "找到",
    "想到",
    "知道",
    "以为",
    "认得",
    "听见",
    "逼近",
    "压向",
    "指向",
)
_ZH_NAME_OBJECT_PREFIXES = _ZH_PRONOUN_OBJECT_PREFIXES + (
    "看到",
    "看见",
    "望见",
    "瞧见",
    "发现",
    "遇见",
    "撞见",
    "面对",
    "追上",
    "扶住",
    "扶起",
    "伸向",
    "刺向",
    "砸向",
    "落到",
)
_ZH_FEMALE_CONTEXT_MARKERS = (
    "她",
    "女人",
    "女子",
    "少女",
    "女弟子",
    "姑娘",
    "师姐",
    "师妹",
    "丫鬟",
    "侍女",
)
_ZH_MALE_CONTEXT_MARKERS = (
    "他",
    "男人",
    "男子",
    "少年",
    "青年",
    "男弟子",
    "师兄",
    "师弟",
)
_ZH_SUBJECT_FOLLOW_VERBS = (
    "说",
    "问",
    "道",
    "笑",
    "抬",
    "低",
    "转",
    "走",
    "看",
    "盯",
    "伸",
    "握",
    "摇",
    "点",
    "咳",
    "吐",
    "退",
    "进",
    "站",
    "跪",
    "坐",
    "开口",
    "回答",
    "冷笑",
    "沉声",
    "轻声",
    "皱眉",
    "愣住",
    "沉默",
    "起身",
    "离去",
    "转身",
    "俯身",
)
_ZH_POSSESSIVE_SUBJECT_NOUNS = (
    "目光",
    "眼睛",
    "声音",
    "脸色",
    "手",
    "手指",
    "身体",
    "肩",
    "嘴角",
    "神情",
    "气息",
    "灵力",
    "剑",
)


def _strip_zh_quoted_dialogue(text: str) -> str:
    """Remove short quoted dialogue before heuristic pronoun scans."""
    return _ZH_QUOTED_DIALOGUE_RE.sub(" ", text)


def _find_zh_pronoun(text: str, pronoun: str) -> int:
    if pronoun in {"他", "她"}:
        match = re.search(re.escape(pronoun) + r"(?!们|的)", text)
        return match.start() if match else -1
    return text.find(pronoun)


def _zh_name_match_embedded_in_longer_name(
    text: str,
    name: str,
    start: int,
    competing_names: list[str],
) -> bool:
    """Avoid matching short aliases inside longer registered names."""
    if not name:
        return True
    for other in competing_names:
        if len(other) <= len(name) or name not in other:
            continue
        if text.startswith(other, start):
            return True
    return False


def _zh_name_mention_is_likely_object(left_context: str) -> bool:
    prefix = left_context[-6:]
    return any(prefix.endswith(marker) for marker in _ZH_NAME_OBJECT_PREFIXES)


def _zh_context_already_shifted_to_gender(before_wrong: str, *, found_gender: str) -> bool:
    markers = _ZH_FEMALE_CONTEXT_MARKERS if found_gender == "female" else _ZH_MALE_CONTEXT_MARKERS
    return any(marker in before_wrong for marker in markers)


def _zh_wrong_pronoun_is_likely_subject(
    right_context: str,
    wrong_pos: int,
    wrong: str,
    *,
    found_gender: str,
    competing_entries: list[CharacterIdentity],
) -> bool:
    before = right_context[:wrong_pos]
    after = right_context[wrong_pos + len(wrong):]
    prefix = before[-5:]

    if any(prefix.endswith(marker) for marker in _ZH_PRONOUN_OBJECT_PREFIXES):
        return False

    # If the wrong-gender pronoun starts a fresh sentence and that gender has
    # another participant in the scene, it is ambiguous rather than a reliable
    # identity violation.
    has_competing_found_gender = any(entry.gender == found_gender for entry in competing_entries)
    if _ZH_STRONG_BOUNDARY_RE.search(before):
        return False

    next_text = after[:12]
    has_non_boundary_bridge = bool(re.sub(r"[\s，,。！？；;：:]+", "", before))
    if wrong in {"他", "她"}:
        if has_competing_found_gender and has_non_boundary_bridge:
            return False
        return bool(
            not before.strip()
            or before.rstrip()[-1:] in "，,。！？；;：:\n"
            or next_text.startswith(_ZH_SUBJECT_FOLLOW_VERBS)
            or any(next_text.startswith(verb) for verb in _ZH_SUBJECT_FOLLOW_VERBS)
        )

    if wrong in {"他的", "她的"}:
        if any(next_text.startswith(noun) for noun in _ZH_POSSESSIVE_SUBJECT_NOUNS):
            if has_competing_found_gender and has_non_boundary_bridge:
                return False
            return True
        return bool(not before.strip())

    return False


def _check_en_pronoun_consistency(
    text: str,
    name: str,
    entry: CharacterIdentity,
    *,
    other_names: list[str] | None = None,
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

    competing_names = [item for item in (other_names or []) if item and item.lower() != name.lower()]

    # Find contexts after character name (window of 200 chars for English)
    for match in re.finditer(re.escape(name), text, re.IGNORECASE):
        start = match.start()
        end = min(len(text), match.end() + 200)
        context = text[start:end]
        right_context = text[match.end():end]

        wrong_match = re.search(wrong_pattern, right_context, re.IGNORECASE)
        if wrong_match:
            before_wrong = right_context[:wrong_match.start()].lower()
            if any(other.lower() in before_wrong for other in competing_names):
                continue
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
