"""Deterministic narrative contract gates for planning and drafting.

These gates validate structured planning data before the writer model sees it.
They deliberately avoid LLM review: the goal is to fail closed when the
foundation contract is incomplete, not to discover identity/time issues after
prose has already been generated.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from bestseller.domain.story_bible import CastSpecInput
from bestseller.domain.workflow import ChapterOutlineBatchInput
from bestseller.services.identity_guard import CharacterIdentity


BLOCKING_SEVERITIES = {"critical", "major"}
GENERIC_TIME_LABELS = {
    "章节开场",
    "章节中段",
    "章节结尾",
    "章节补充钩子",
}
GENERIC_CHAPTER_GOAL_MARKERS = (
    "推动本章剧情发展",
    "「生存压力」",
    "生存压力代表的势力角力",
    "完成本章的",
    "一种环境或体系层面的威胁出现",
    "宁尘过去埋下的秘密突然浮现",
)
GENERIC_CONFLICT_MARKERS = (
    "「生存压力」",
    "生存压力代表的势力角力",
    "完成本章的",
    "的核心阻力：一种环境或体系层面的威胁出现",
)
GENERIC_HOOK_MARKERS = (
    "采用三层悬念叠加",
    "每10章设置大钩子",
    "尾声把「尾钩」转化为下一章必须处理的新压力",
    "具体事件是「尾钩」",
)
GENERIC_SCENE_PURPOSE_MARKERS = (
    "推动本章剧情发展",
    "承接本章主线，补足场景推进、信息释放与结尾钩子",
    "承接上章后果并明确本章行动目标",
    "用更深一层的代价、真相或变化把局势再往前推",
    "具体事件是「开场」",
    "具体事件是「推进」",
    "具体事件是「尾钩」",
)
REFERENCE_ONLY_PURPOSE_MARKERS = (
    "旧照",
    "旧案",
    "照片",
    "遗照",
    "档案",
    "卷宗",
    "线索",
    "记录",
    "信纸",
    "纸条",
    "字迹",
    "留言",
    "遗言",
    "临死话",
    "当年",
    "曾经",
    "曾替",
    "曾为",
    "生前",
    "留下",
)
NON_PERSON_ENTITY_MARKERS = {
    "artifact",
    "environment",
    "faction",
    "force",
    "group",
    "location",
    "object",
    "organization",
    "place",
    "system",
    "systemic",
}


@dataclass(frozen=True)
class NarrativeContractViolation:
    code: str
    location: str
    message: str
    severity: str = "critical"
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "location": self.location,
            "message": self.message,
            "severity": self.severity,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class NarrativeContractReport:
    gate_name: str
    violations: tuple[NarrativeContractViolation, ...] = ()
    warnings: tuple[NarrativeContractViolation, ...] = ()

    @property
    def blocking_violations(self) -> tuple[NarrativeContractViolation, ...]:
        return tuple(
            violation
            for violation in self.violations
            if violation.severity in BLOCKING_SEVERITIES
        )

    @property
    def passed(self) -> bool:
        return not self.blocking_violations

    @property
    def blocks(self) -> bool:
        return not self.passed

    def to_dict(self) -> dict[str, Any]:
        return {
            "gate_name": self.gate_name,
            "passed": self.passed,
            "violations": [violation.to_dict() for violation in self.violations],
            "warnings": [warning.to_dict() for warning in self.warnings],
        }

    def error_message(self, *, project_slug: str = "", artifact: str = "") -> str:
        prefix = f"{self.gate_name} failed"
        if project_slug:
            prefix += f" for project '{project_slug}'"
        if artifact:
            prefix += f" while validating {artifact}"
        details = "; ".join(
            f"{violation.code}@{violation.location}: {violation.message}"
            for violation in self.blocking_violations[:8]
        )
        if len(self.blocking_violations) > 8:
            details += f"; ... +{len(self.blocking_violations) - 8} more"
        return f"{prefix}: {details}"

    def raise_for_blocks(self, *, project_slug: str = "", artifact: str = "") -> None:
        if self.blocks:
            raise ValueError(self.error_message(project_slug=project_slug, artifact=artifact))


def validate_foundation_identity_contract(
    cast_spec_content: dict[str, Any] | None,
) -> NarrativeContractReport:
    """Validate CastSpec identity locks before persistence."""

    if cast_spec_content is None:
        return NarrativeContractReport(gate_name="foundation_identity_contract")

    try:
        cast_spec = CastSpecInput.model_validate(cast_spec_content)
    except Exception as exc:
        return NarrativeContractReport(
            gate_name="foundation_identity_contract",
            violations=(
                NarrativeContractViolation(
                    code="FOUNDATION_CAST_SCHEMA_INVALID",
                    location="cast_spec",
                    message=f"CastSpec cannot be parsed: {exc}",
                ),
            ),
        )

    violations: list[NarrativeContractViolation] = []
    alias_owner: dict[str, str] = {}

    for location, character in _iter_cast_characters(cast_spec):
        data = character.model_dump(mode="json")
        name = _clean(data.get("name"))
        if not name:
            violations.append(
                NarrativeContractViolation(
                    code="FOUNDATION_IDENTITY_NAME_MISSING",
                    location=location,
                    message="Cast character is missing a stable name.",
                )
            )
            continue

        if not _requires_identity_lock(data):
            continue

        gender = _clean(data.get("gender")).lower() or "unknown"
        if gender == "unknown":
            violations.append(
                NarrativeContractViolation(
                    code="FOUNDATION_IDENTITY_GENDER_MISSING",
                    location=f"{location}.gender",
                    message=f"Character '{name}' must have a locked non-unknown gender.",
                    metadata={"character": name},
                )
            )

        pronoun_zh = _clean(data.get("pronoun_set_zh"))
        pronoun_en = _clean(data.get("pronoun_set_en"))
        if not pronoun_zh or not pronoun_en:
            violations.append(
                NarrativeContractViolation(
                    code="FOUNDATION_IDENTITY_PRONOUN_MISSING",
                    location=f"{location}.pronouns",
                    message=f"Character '{name}' must have both Chinese and English pronoun sets.",
                    metadata={
                        "character": name,
                        "pronoun_set_zh": pronoun_zh,
                        "pronoun_set_en": pronoun_en,
                    },
                )
            )

        for alias in (name, *_character_aliases(data)):
            normalized = _normalize_identity_token(alias)
            if not normalized:
                continue
            owner = alias_owner.get(normalized)
            if owner and owner != name:
                violations.append(
                    NarrativeContractViolation(
                        code="FOUNDATION_IDENTITY_ALIAS_COLLISION",
                        location=f"{location}.aliases",
                        message=f"Alias/name '{alias}' is shared by '{owner}' and '{name}'.",
                        metadata={"alias": alias, "first_owner": owner, "second_owner": name},
                    )
                )
            alias_owner[normalized] = name

    return NarrativeContractReport(
        gate_name="foundation_identity_contract",
        violations=tuple(violations),
    )


def build_identity_manifest(cast_spec_content: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Build a compact manifest that downstream plan gates can consume."""

    if cast_spec_content is None:
        return []
    cast_spec = CastSpecInput.model_validate(cast_spec_content)
    manifest: list[dict[str, Any]] = []
    for _, character in _iter_cast_characters(cast_spec):
        data = character.model_dump(mode="json")
        name = _clean(data.get("name"))
        if not name:
            continue
        manifest.append(
            {
                "name": name,
                "role": _clean(data.get("role")),
                "gender": _clean(data.get("gender")) or "unknown",
                "pronoun_set_zh": _clean(data.get("pronoun_set_zh")),
                "pronoun_set_en": _clean(data.get("pronoun_set_en")),
                "aliases": _character_aliases(data),
            }
        )
    return manifest


def validate_chapter_plan_contract(
    batch: ChapterOutlineBatchInput,
    *,
    identity_manifest: Iterable[dict[str, Any]] = (),
    require_identity_registry: bool = True,
) -> NarrativeContractReport:
    """Validate a chapter outline batch before materializing DB rows."""

    violations: list[NarrativeContractViolation] = []
    warnings: list[NarrativeContractViolation] = []
    identity_index = _identity_index_from_manifest(identity_manifest)

    if require_identity_registry and not identity_index:
        violations.append(
            NarrativeContractViolation(
                code="PLAN_IDENTITY_REGISTRY_MISSING",
                location="project.identity_manifest",
                message="Chapter planning requires a locked identity manifest before outline materialization.",
            )
        )

    if not batch.chapters:
        violations.append(
            NarrativeContractViolation(
                code="PLAN_CHAPTER_BATCH_EMPTY",
                location="chapter_outline_batch.chapters",
                message="Chapter outline batch contains no chapters.",
            )
        )

    seen_scene_signatures: dict[tuple[Any, ...], str] = {}
    for chapter_index, chapter in enumerate(batch.chapters):
        chapter_location = f"chapter_outline_batch.chapters[{chapter_index}]"
        if not _clean(chapter.main_conflict):
            violations.append(
                NarrativeContractViolation(
                    code="PLAN_CHAPTER_CONFLICT_MISSING",
                    location=f"{chapter_location}.main_conflict",
                    message=f"Chapter {chapter.chapter_number} is missing a concrete main conflict.",
                )
            )
        elif _contains_marker(chapter.main_conflict, GENERIC_CONFLICT_MARKERS):
            violations.append(
                NarrativeContractViolation(
                    code="PLAN_CHAPTER_CONFLICT_GENERIC",
                    location=f"{chapter_location}.main_conflict",
                    message=(
                        f"Chapter {chapter.chapter_number} main conflict uses a placeholder/template "
                        "instead of a concrete obstacle."
                    ),
                    metadata={"chapter_number": chapter.chapter_number},
                )
            )
        if _contains_marker(chapter.chapter_goal, GENERIC_CHAPTER_GOAL_MARKERS):
            violations.append(
                NarrativeContractViolation(
                    code="PLAN_CHAPTER_GOAL_GENERIC",
                    location=f"{chapter_location}.chapter_goal",
                    message=(
                        f"Chapter {chapter.chapter_number} goal is generic; it must name the unique "
                        "action, pressure, and state change for this chapter."
                    ),
                    metadata={"chapter_number": chapter.chapter_number},
                )
            )
        if not _clean(chapter.hook_description) or _contains_marker(
            chapter.hook_description,
            GENERIC_HOOK_MARKERS,
        ):
            violations.append(
                NarrativeContractViolation(
                    code="PLAN_CHAPTER_HOOK_GENERIC",
                    location=f"{chapter_location}.hook_description",
                    message=(
                        f"Chapter {chapter.chapter_number} hook must be a concrete next-pressure event, "
                        "not a hook recipe or placeholder."
                    ),
                    metadata={"chapter_number": chapter.chapter_number},
                )
            )
        if not chapter.scenes:
            violations.append(
                NarrativeContractViolation(
                    code="PLAN_CHAPTER_SCENES_MISSING",
                    location=f"{chapter_location}.scenes",
                    message=f"Chapter {chapter.chapter_number} has no scene cards.",
                )
            )
            continue

        for scene_index, scene in enumerate(chapter.scenes):
            scene_location = f"{chapter_location}.scenes[{scene_index}]"
            participants = [_clean(item) for item in scene.participants if _clean(item)]

            if not _clean(scene.time_label):
                violations.append(
                    NarrativeContractViolation(
                        code="PLAN_SCENE_TIME_MISSING",
                        location=f"{scene_location}.time_label",
                        message=(
                            f"Chapter {chapter.chapter_number} scene {scene.scene_number} "
                            "must have an explicit timeline label."
                        ),
                    )
                )
            elif _is_generic_time_label(scene.time_label):
                violations.append(
                    NarrativeContractViolation(
                        code="PLAN_SCENE_TIME_GENERIC",
                        location=f"{scene_location}.time_label",
                        message=(
                            f"Chapter {chapter.chapter_number} scene {scene.scene_number} "
                            "uses a generic timeline label; use story-world time/place anchoring."
                        ),
                    )
                )
            if not participants:
                violations.append(
                    NarrativeContractViolation(
                        code="PLAN_SCENE_PARTICIPANTS_MISSING",
                        location=f"{scene_location}.participants",
                        message=(
                            f"Chapter {chapter.chapter_number} scene {scene.scene_number} "
                            "must name its active participants."
                        ),
                    )
                )
            elif identity_index:
                for participant in participants:
                    if _normalize_identity_token(participant) not in identity_index:
                        violations.append(
                            NarrativeContractViolation(
                                code="PLAN_SCENE_UNKNOWN_PARTICIPANT",
                                location=f"{scene_location}.participants",
                                message=(
                                    f"Participant '{participant}' is not in the locked identity manifest."
                                ),
                                metadata={
                                    "chapter_number": chapter.chapter_number,
                                    "scene_number": scene.scene_number,
                                    "participant": participant,
                                },
                            )
                        )

            purpose = scene.purpose if isinstance(scene.purpose, dict) else {}
            story_purpose = _clean(purpose.get("story"))
            emotion_purpose = _clean(purpose.get("emotion"))
            if not story_purpose:
                violations.append(
                    NarrativeContractViolation(
                        code="PLAN_SCENE_STORY_PURPOSE_MISSING",
                        location=f"{scene_location}.purpose.story",
                        message=(
                            f"Chapter {chapter.chapter_number} scene {scene.scene_number} "
                            "must define the story purpose it advances."
                        ),
                    )
                )
            elif _contains_marker(story_purpose, GENERIC_SCENE_PURPOSE_MARKERS):
                violations.append(
                    NarrativeContractViolation(
                        code="PLAN_SCENE_STORY_PURPOSE_GENERIC",
                        location=f"{scene_location}.purpose.story",
                        message=(
                            f"Chapter {chapter.chapter_number} scene {scene.scene_number} "
                            "story purpose still contains a placeholder scene task."
                        ),
                        metadata={
                            "chapter_number": chapter.chapter_number,
                            "scene_number": scene.scene_number,
                        },
                    )
                )
            for referenced_name in _extract_purpose_character_names(
                story_purpose,
                identity_index,
            ):
                if _normalize_identity_token(referenced_name) not in {
                    _normalize_identity_token(participant)
                    for participant in participants
                }:
                    violations.append(
                        NarrativeContractViolation(
                            code="PLAN_SCENE_PURPOSE_CHARACTER_NOT_IN_PARTICIPANTS",
                            location=f"{scene_location}.participants",
                            message=(
                                f"Scene purpose names '{referenced_name}', but the scene "
                                "participants do not include that character."
                            ),
                            metadata={
                                "chapter_number": chapter.chapter_number,
                                "scene_number": scene.scene_number,
                                "character": referenced_name,
                            },
                        )
                    )
            if not emotion_purpose:
                warnings.append(
                    NarrativeContractViolation(
                        code="PLAN_SCENE_EMOTION_PURPOSE_MISSING",
                        location=f"{scene_location}.purpose.emotion",
                        message=(
                            f"Chapter {chapter.chapter_number} scene {scene.scene_number} "
                            "has no emotional purpose; this often creates repetitive neutral scenes."
                        ),
                        severity="warning",
                    )
                )

            signature = (
                _clean(scene.scene_type).lower(),
                _normalize_identity_token(",".join(sorted(participants))),
                _clean(scene.time_label).lower(),
                story_purpose[:80],
            )
            if all(signature):
                previous = seen_scene_signatures.get(signature)
                current = f"ch{chapter.chapter_number}.sc{scene.scene_number}"
                if previous:
                    warnings.append(
                        NarrativeContractViolation(
                            code="PLAN_SCENE_PATTERN_REPEATED",
                            location=scene_location,
                            message=f"Scene pattern repeats {previous}: {current}.",
                            severity="warning",
                            metadata={"previous": previous, "current": current},
                        )
                    )
                else:
                    seen_scene_signatures[signature] = current

    return NarrativeContractReport(
        gate_name="chapter_plan_contract",
        violations=tuple(violations),
        warnings=tuple(warnings),
    )


def validate_scene_contract_pre_draft(
    scene: Any,
    *,
    identity_registry: Iterable[CharacterIdentity] = (),
    require_identity_registry: bool = False,
) -> NarrativeContractReport:
    """Validate a persisted scene card before drafting prose."""

    violations: list[NarrativeContractViolation] = []
    warnings: list[NarrativeContractViolation] = []
    # Avoid touching unloaded ORM relationships here; this gate is used inside
    # async SQLAlchemy code where accidental lazy-loads raise MissingGreenlet.
    chapter_obj = getattr(scene, "__dict__", {}).get("chapter")
    chapter_number = getattr(chapter_obj, "chapter_number", None)
    scene_number = getattr(scene, "scene_number", "?")
    location = f"scene_card[{chapter_number or '?'}.{scene_number}]"

    participants = [_clean(item) for item in (getattr(scene, "participants", None) or []) if _clean(item)]
    identity_index = _identity_index_from_registry(identity_registry)

    if not _clean(getattr(scene, "time_label", None)):
        violations.append(
            NarrativeContractViolation(
                code="PREDRAFT_SCENE_TIME_MISSING",
                location=f"{location}.time_label",
                message=f"Scene {scene_number} must have an explicit timeline label before drafting.",
            )
        )
    elif _is_generic_time_label(getattr(scene, "time_label", None)):
        violations.append(
            NarrativeContractViolation(
                code="PREDRAFT_SCENE_TIME_GENERIC",
                location=f"{location}.time_label",
                message=f"Scene {scene_number} uses a generic timeline label before drafting.",
            )
        )
    if not participants:
        violations.append(
            NarrativeContractViolation(
                code="PREDRAFT_SCENE_PARTICIPANTS_MISSING",
                location=f"{location}.participants",
                message=f"Scene {scene_number} must name active participants before drafting.",
            )
        )
    elif identity_index:
        for participant in participants:
            identity = identity_index.get(_normalize_identity_token(participant))
            if identity is None:
                violations.append(
                    NarrativeContractViolation(
                        code="PREDRAFT_SCENE_UNKNOWN_PARTICIPANT",
                        location=f"{location}.participants",
                        message=f"Participant '{participant}' is not in the identity registry.",
                        metadata={"participant": participant},
                    )
                )
                continue
            if identity.gender == "unknown":
                violations.append(
                    NarrativeContractViolation(
                        code="PREDRAFT_IDENTITY_GENDER_UNRESOLVED",
                        location=f"{location}.participants",
                        message=f"Participant '{identity.name}' still has unknown gender.",
                        metadata={"participant": participant, "character": identity.name},
                    )
                )
            if not identity.pronoun_set_zh or not identity.pronoun_set_en:
                violations.append(
                    NarrativeContractViolation(
                        code="PREDRAFT_IDENTITY_PRONOUN_UNRESOLVED",
                        location=f"{location}.participants",
                        message=f"Participant '{identity.name}' is missing a pronoun set.",
                        metadata={"participant": participant, "character": identity.name},
                    )
                )
    elif require_identity_registry:
        violations.append(
            NarrativeContractViolation(
                code="PREDRAFT_IDENTITY_REGISTRY_MISSING",
                location="project.identity_registry",
                message="Scene drafting requires an identity registry when participants are present.",
            )
        )
    elif participants:
        warnings.append(
            NarrativeContractViolation(
                code="PREDRAFT_IDENTITY_REGISTRY_EMPTY",
                location="project.identity_registry",
                message="No identity registry was available for participant validation.",
                severity="warning",
            )
        )

    purpose = getattr(scene, "purpose", None) or {}
    if not isinstance(purpose, dict) or not _clean(purpose.get("story")):
        violations.append(
            NarrativeContractViolation(
                code="PREDRAFT_SCENE_STORY_PURPOSE_MISSING",
                location=f"{location}.purpose.story",
                message=f"Scene {scene_number} must define a concrete story purpose before drafting.",
            )
        )
    elif _contains_marker(_clean(purpose.get("story")), GENERIC_SCENE_PURPOSE_MARKERS):
        violations.append(
            NarrativeContractViolation(
                code="PREDRAFT_SCENE_STORY_PURPOSE_GENERIC",
                location=f"{location}.purpose.story",
                message=f"Scene {scene_number} still contains a placeholder story purpose before drafting.",
            )
        )
    else:
        story_purpose = _clean(purpose.get("story"))
        participant_tokens = {
            _normalize_identity_token(participant)
            for participant in participants
        }
        for referenced_name in _extract_purpose_character_names(
            story_purpose,
            identity_index,
        ):
            if _normalize_identity_token(referenced_name) not in participant_tokens:
                violations.append(
                    NarrativeContractViolation(
                        code="PREDRAFT_SCENE_PURPOSE_CHARACTER_NOT_IN_PARTICIPANTS",
                        location=f"{location}.participants",
                        message=(
                            f"Scene purpose names '{referenced_name}', but that character "
                            "is not listed as an active participant before drafting."
                        ),
                        metadata={
                            "participant": referenced_name,
                            "scene_number": scene_number,
                        },
                    )
                )

    return NarrativeContractReport(
        gate_name="pre_draft_scene_contract",
        violations=tuple(violations),
        warnings=tuple(warnings),
    )


def _iter_cast_characters(cast_spec: CastSpecInput) -> Iterable[tuple[str, Any]]:
    if cast_spec.protagonist is not None:
        yield "cast_spec.protagonist", cast_spec.protagonist
    if cast_spec.antagonist is not None:
        yield "cast_spec.antagonist", cast_spec.antagonist
    for index, character in enumerate(cast_spec.supporting_cast):
        yield f"cast_spec.supporting_cast[{index}]", character


def _requires_identity_lock(character: dict[str, Any]) -> bool:
    metadata = character.get("metadata")
    if isinstance(metadata, dict) and metadata.get("identity_contract_exempt") is True:
        return False

    entity_type = _clean(character.get("entity_type") or character.get("type")).lower()
    role = _clean(character.get("role")).lower()
    species = _clean(character.get("species")).lower()
    labels = {entity_type, role, species}
    return not any(label in NON_PERSON_ENTITY_MARKERS for label in labels if label)


def _character_aliases(character: dict[str, Any]) -> list[str]:
    aliases: list[str] = []
    for key in ("aliases", "alias", "also_known_as", "name_variants", "nicknames"):
        value = character.get(key)
        if isinstance(value, str):
            aliases.append(value)
        elif isinstance(value, (list, tuple)):
            aliases.extend(item for item in value if isinstance(item, str))
    return list(dict.fromkeys(alias.strip() for alias in aliases if alias.strip()))


def _identity_index_from_manifest(
    identity_manifest: Iterable[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for identity in identity_manifest:
        if not isinstance(identity, dict):
            continue
        tokens = [_clean(identity.get("name")), *_string_list(identity.get("aliases"))]
        for token in tokens:
            normalized = _normalize_identity_token(token)
            if normalized:
                index[normalized] = identity
    return index


def _identity_index_from_registry(
    identity_registry: Iterable[CharacterIdentity],
) -> dict[str, CharacterIdentity]:
    index: dict[str, CharacterIdentity] = {}
    for identity in identity_registry:
        tokens = [identity.name, *identity.aliases]
        for token in tokens:
            normalized = _normalize_identity_token(token)
            if normalized:
                index[normalized] = identity
    return index


def _string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple)):
        return [item for item in value if isinstance(item, str)]
    return []


def _clean(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


def _contains_marker(value: Any, markers: Iterable[str]) -> bool:
    text = _clean(value)
    return any(marker in text for marker in markers)


def _is_generic_time_label(value: Any) -> bool:
    label = _clean(value)
    if not label:
        return True
    if label in GENERIC_TIME_LABELS:
        return True
    return label.startswith("章节场景")


def _normalize_identity_token(value: Any) -> str:
    return "".join(_clean(value).lower().split())


def _extract_purpose_character_names(
    text: str,
    identity_index: dict[str, Any],
) -> tuple[str, ...]:
    """Extract character-like names that appear inside a scene purpose."""

    if not text:
        return ()
    try:
        from bestseller.services.output_validator import (
            _ZH_NAME_RE,
            _strip_leading_conjunction,
            _strip_role_suffix,
            _trim_zh_name_candidate,
        )
    except Exception:
        return ()

    found: list[str] = []
    seen: set[str] = set()
    for match in _ZH_NAME_RE.finditer(text):
        if _is_reference_only_purpose_mention(text, match.start(), match.end()):
            continue
        cleaned = _trim_zh_name_candidate(match.group(0))
        if not cleaned:
            continue
        variants = [cleaned]
        role_stripped = _strip_role_suffix(cleaned)
        if role_stripped:
            variants.insert(0, role_stripped)
        conj_stripped = _strip_leading_conjunction(cleaned)
        if conj_stripped:
            variants.insert(0, conj_stripped)

        resolved: str | None = None
        for variant in variants:
            if not variant:
                continue
            resolved = _resolve_purpose_identity_name(variant, identity_index)
            if resolved:
                break
        if not resolved:
            continue
        key = _normalize_identity_token(resolved)
        if key and key not in seen:
            seen.add(key)
            found.append(resolved)
    return tuple(found)


def _is_reference_only_purpose_mention(text: str, start: int, end: int) -> bool:
    window = text[max(0, start - 8): min(len(text), end + 12)]
    return any(marker in window for marker in REFERENCE_ONLY_PURPOSE_MARKERS)


def _identity_display_name(identity: Any) -> str:
    if isinstance(identity, CharacterIdentity):
        return _clean(identity.name)
    if isinstance(identity, dict):
        return _clean(identity.get("name"))
    return _clean(getattr(identity, "name", ""))


def _resolve_purpose_identity_name(
    candidate: str,
    identity_index: dict[str, Any],
) -> str | None:
    identity = identity_index.get(_normalize_identity_token(candidate))
    if identity is None or _purpose_identity_is_inactive(identity):
        return None
    return _identity_display_name(identity) or candidate


def _purpose_identity_is_inactive(identity: Any) -> bool:
    if isinstance(identity, CharacterIdentity):
        return not identity.is_alive
    if isinstance(identity, dict):
        status = _clean(
            identity.get("alive_status")
            or identity.get("status")
            or identity.get("lifecycle_status")
        ).lower()
        return status in {"dead", "deceased", "死亡", "已死亡"}
    return bool(getattr(identity, "is_alive", True)) is False


def _match_allowed_identity_name(
    candidate: str,
    allowed_names: frozenset[str],
) -> str | None:
    if not candidate or not allowed_names:
        return None
    normalized = _normalize_identity_token(candidate)
    for name in allowed_names:
        name_norm = _normalize_identity_token(name)
        if normalized == name_norm:
            return name
        if normalized.startswith(name_norm) or name_norm.startswith(normalized):
            return name
    return None
