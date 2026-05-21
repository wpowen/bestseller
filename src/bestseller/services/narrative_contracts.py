"""Deterministic narrative contract gates for planning and drafting.

These gates validate structured planning data before the writer model sees it.
They deliberately avoid LLM review: the goal is to fail closed when the
foundation contract is incomplete, not to discover identity/time issues after
prose has already been generated.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any, Iterable

from bestseller.domain.story_bible import CastSpecInput, normalize_character_gender
from bestseller.domain.workflow import ChapterOutlineBatchInput
from bestseller.services.identity_guard import CharacterIdentity
from bestseller.services.methodology_overlay import (
    methodology_contract_blocks,
    methodology_contract_requires_checks,
    normalize_methodology_contract_mode,
    validate_scene_methodology_contract,
)


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
GENERIC_OPENING_SITUATION_MARKERS = (
    "承接上一章尾钩，主角没有空档去长篇解释设定",
    "主角没有空档去长篇解释设定",
    "承接上一章尾钩",
    "承接上章尾钩",
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
    "出现新的证据、时限或代价",
    "出现新的证据、时限、代价",
    "new evidence, deadline, or cost",
)
FUNCTIONAL_TITLE_PREFIXES_ZH = (
    "暗潮",
    "盲区",
    "裂痕",
    "回声",
    "风眼",
    "余烬",
    "伏线",
    "变局",
    "断点",
    "逆流",
    "边界",
    "悬灯",
    "浮标",
    "锈迹",
    "夜隙",
    "残局",
    "沉渊",
    "灰幕",
    "雾锁",
    "棱线",
    "铁壁",
    "荒火",
    "冷锋",
    "碎影",
)
FUNCTIONAL_TITLE_SUFFIXES_ZH = (
    "初现",
    "入局",
    "投石",
    "试探",
    "铺火",
    "露锋",
    "破冰",
    "起手",
    "掀幕",
    "落子",
    "追索",
    "摸底",
    "拆解",
    "寻隙",
    "探针",
    "回查",
    "溯源",
    "揭层",
    "织网",
    "破壁",
    "加压",
    "围拢",
    "失衡",
    "封锁",
    "死线",
    "逼近",
    "绞杀",
    "窒息",
    "崩弦",
    "缩网",
    "反咬",
    "逆转",
    "偏航",
    "脱钩",
    "换轨",
    "回火",
    "翻盘",
    "倒戈",
    "破局",
    "重铸",
    "爆裂",
    "截断",
    "崩口",
    "闯线",
    "归零",
    "掀牌",
    "决堤",
    "焚天",
    "碎锁",
    "终幕",
)
META_PLANNING_PATTERNS = (
    r"(建立|引入|完善|补足|补全|增加|扩大|深化|完成).{0,14}(世界观|设定|体系|背景|势力线|角色线|关系线|哲学|主题|叙事|闭环|复杂性)",
    r"(建立|引入|完善|补足|补全|增加|扩大|深化|完成).{0,14}(角色|势力|阵营|框架|结构)",
    r"(揭示|确认).{0,14}(身份|世界观|设定|体系|背景|终极悬念)",
    r"(读者|追读|爽点|尾钩|钩子|章节功能|叙事功能|本章功能|本章唯一推进点)",
    r"第\d+章尾钩：围绕",
    r"第\d+章(开场|中段\d*|尾声|结尾).{0,18}围绕",
    r"(本章目标|chapter goal)[:：]",
    r"(Chapter|chapter) \d+ (opening|middle beat|closing hook)",
    r"章内必须落到这件可见事件",
    r"visible chapter event must be",
    r"围绕「[^」]+」出现新的证据",
    r"迫使\S{0,8}下一章立刻行动",
)
GENERIC_SCENE_PURPOSE_MARKERS = (
    "推动本章剧情发展",
    "承接本章主线，补足场景推进、信息释放与结尾钩子",
    "承接上章后果并明确本章行动目标",
    "推动本章局势前进，并换来新的代价或信息",
    "推动本章局势前进",
    "换来新的代价或信息",
    "用更深一层的代价、真相或变化把局势再往前推",
    "采用三层悬念叠加",
    "当前冲突打断式悬念",
    "长期伏笔轻触式悬念",
    "更大威胁露头式悬念",
    "核心压力落到行动层",
    "本章行动目标",
    "具体事件是「开场」",
    "具体事件是「推进」",
    "具体事件是「尾钩」",
    "End every chapter",
)
GENERIC_SCENE_CONTRACT_LABELS = {
    "Opening Beat",
    "Primary Move",
    "Closing Hook",
    "章节开场",
    "章节推进",
    "章节中段",
    "章节结尾",
    "章节补充钩子",
}
GENERIC_SCENE_CONTRACT_SUMMARY_MARKERS = (
    "Carry forward the previous result",
    "Move the chapter forward",
    "End each chapter with",
    "End every chapter with",
    "Chapter N poses question",
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
    "线推进",
    "旧线",
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


def repair_legacy_foundation_identity_locks(
    cast_spec_content: dict[str, Any] | None,
    *,
    identity_hints: Iterable[dict[str, Any]] = (),
    allow_unreliable_defaults: bool = True,
) -> tuple[dict[str, Any] | None, int]:
    """Backfill identity locks for historical CastSpec artifacts.

    New story-bible materialization still fails closed before persistence. This
    repair exists for resume/self-heal paths where an older project already has
    a CastSpec artifact created before foundation identity locks were required.
    """

    if not isinstance(cast_spec_content, dict):
        return cast_spec_content, 0

    hint_index = _identity_index_from_manifest(identity_hints)
    patched = _deepcopy_json_mapping(cast_spec_content)
    repaired = 0
    repaired += _remove_aliases_colliding_with_character_names(patched)
    for character in _iter_raw_cast_character_dicts(patched):
        name = _clean(character.get("name"))
        if not name or not _requires_identity_lock(character):
            continue
        hint = hint_index.get(_normalize_identity_token(name), {})
        gender = (
            _coerce_lock_gender(character.get("gender"))
            or _coerce_lock_gender(hint.get("gender"))
            or _coerce_lock_gender_from_pronouns(character)
            or _coerce_lock_gender_from_pronouns(hint)
        )
        if gender is None:
            gender = _infer_legacy_gender(name, character)
        if gender is None:
            if not allow_unreliable_defaults:
                continue
            gender = "nonbinary"
            character.setdefault("metadata", {})
            if isinstance(character["metadata"], dict):
                character["metadata"].setdefault(
                    "identity_lock_repair",
                    "legacy_resume_default",
                )

        if _coerce_lock_gender(character.get("gender")) != gender:
            character["gender"] = gender
            repaired += 1

        pronoun_zh, pronoun_en = _pronouns_for_gender(gender)
        hint_zh = _clean(hint.get("pronoun_set_zh"))
        hint_en = _clean(hint.get("pronoun_set_en"))
        if not _clean(character.get("pronoun_set_zh")):
            character["pronoun_set_zh"] = hint_zh or pronoun_zh
            repaired += 1
        if not _clean(character.get("pronoun_set_en")):
            character["pronoun_set_en"] = hint_en or pronoun_en
            repaired += 1

    return patched, repaired


def _remove_aliases_colliding_with_character_names(cast_spec_content: dict[str, Any]) -> int:
    """Drop legacy alias/name_variant values that equal another cast member's name."""

    characters = [item for item in _iter_raw_cast_character_dicts(cast_spec_content)]
    name_tokens = {
        _normalize_identity_token(_clean(character.get("name")))
        for character in characters
        if _normalize_identity_token(_clean(character.get("name")))
    }
    if not name_tokens:
        return 0

    repaired = 0
    alias_keys = ("aliases", "alias", "also_known_as", "name_variants", "nicknames")
    for character in characters:
        own_token = _normalize_identity_token(_clean(character.get("name")))
        if not own_token:
            continue
        conflicting_tokens = name_tokens - {own_token}
        for key in alias_keys:
            value = character.get(key)
            if isinstance(value, str):
                alias_token = _normalize_identity_token(value)
                if alias_token in conflicting_tokens:
                    character.pop(key, None)
                    repaired += 1
                continue
            if isinstance(value, list):
                filtered = [
                    item
                    for item in value
                    if not (
                        isinstance(item, str)
                        and _normalize_identity_token(item) in conflicting_tokens
                    )
                ]
                if len(filtered) != len(value):
                    character[key] = filtered
                    repaired += len(value) - len(filtered)
    return repaired


def repair_legacy_scene_contract_pre_draft(
    scene: Any,
    *,
    chapter_number: int | None = None,
) -> int:
    """Mutate legacy scene cards enough to pass deterministic pre-draft gates.

    Older recovered projects can contain template scene labels such as
    ``章节结尾`` and boilerplate hook recipes. Blocking on those rows prevents
    resume from ever reaching the writer. This repair replaces only the
    template fields; it does not invent participants or bypass identity gates.
    """

    repaired = 0
    scene_number = getattr(scene, "scene_number", "?")
    purpose = getattr(scene, "purpose", None)
    purpose_map = dict(purpose) if isinstance(purpose, dict) else {}
    story = _clean(purpose_map.get("story"))

    if (
        not _clean(getattr(scene, "time_label", None))
        or _is_generic_time_label(getattr(scene, "time_label", None))
    ):
        setattr(
            scene,
            "time_label",
            _legacy_scene_time_label(
                scene,
                chapter_number=chapter_number,
                scene_number=scene_number,
                story=story,
            ),
        )
        repaired += 1

    if not story or _contains_marker(story, GENERIC_SCENE_PURPOSE_MARKERS):
        purpose_map["story"] = _legacy_scene_story_purpose(
            scene,
            chapter_number=chapter_number,
            scene_number=scene_number,
            story=story,
        )
        if not _clean(purpose_map.get("emotion")):
            purpose_map["emotion"] = "Escalate pressure through a visible choice, cost, or leverage shift."
        setattr(scene, "purpose", purpose_map)
        repaired += 1

    return repaired


def repair_missing_scene_participants_pre_draft(
    scene: Any,
    *,
    identity_registry: Iterable[CharacterIdentity] = (),
    excluded_names: Iterable[str] = (),
) -> int:
    """Fill legacy participant omissions from deterministic scene context.

    This repair is conservative: it only adds characters already known in the
    identity registry, only when they are alive and identity-resolved. Empty
    participant lists can be seeded from entry/exit state plus purpose text.
    Interactive legacy scenes that already have one participant may also be
    augmented from entry/exit state, because old continuation plans often named
    the interlocutor only in the state delta.
    """

    current = [
        _clean(item)
        for item in (getattr(scene, "participants", None) or [])
        if _clean(item)
    ]

    identity_index = _identity_index_from_registry(identity_registry)
    if not identity_index:
        return 0
    excluded_tokens = {
        _normalize_identity_token(name)
        for name in excluded_names
        if _normalize_identity_token(name)
    }

    candidates: list[str] = list(dict.fromkeys(current))
    seen_tokens = {
        _normalize_identity_token(participant)
        for participant in candidates
        if _normalize_identity_token(participant)
    }

    def _add_candidate(value: object) -> None:
        name = _clean(value)
        if not name:
            return
        identity = identity_index.get(_normalize_identity_token(name))
        if identity is None or not identity.is_alive or not _identity_is_resolved(identity):
            return
        identity_token = _normalize_identity_token(identity.name)
        if identity_token in excluded_tokens:
            return
        if identity_token not in seen_tokens:
            candidates.append(identity.name)
            seen_tokens.add(identity_token)

    if not current:
        for state_name in ("entry_state", "exit_state"):
            state = getattr(scene, state_name, None)
            if isinstance(state, dict):
                for key in state:
                    _add_candidate(key)

    purpose = getattr(scene, "purpose", None) or {}
    if isinstance(purpose, dict):
        story_purpose = _clean(purpose.get("story"))
        if story_purpose:
            for referenced_name in _extract_purpose_character_names(
                story_purpose,
                identity_index,
            ):
                _add_candidate(referenced_name)
            for referenced_name in _extract_purpose_identity_mentions(
                story_purpose,
                identity_index,
            ):
                _add_candidate(referenced_name)

    if not candidates:
        fallback_identity = _primary_draft_ready_identity(
            identity_index.values(),
            excluded_tokens=excluded_tokens,
        )
        if fallback_identity is not None:
            _add_candidate(fallback_identity.name)

    scene_type = _clean(getattr(scene, "scene_type", None)).lower()
    interactive_types = {"dialogue", "confrontation", "conflict", "对话", "冲突", "对峙"}
    if len(candidates) < 2 and scene_type in interactive_types:
        for state_name in ("entry_state", "exit_state"):
            state = getattr(scene, state_name, None)
            if isinstance(state, dict):
                for key in state:
                    _add_candidate(key)
                    if len(candidates) >= 2:
                        break
            if len(candidates) >= 2:
                break

    added_count = len(candidates) - len(current)
    if added_count <= 0:
        return 0

    setattr(scene, "participants", candidates)
    return added_count


def _primary_draft_ready_identity(
    identities: Iterable[CharacterIdentity],
    *,
    excluded_tokens: set[str],
) -> CharacterIdentity | None:
    unique: dict[str, CharacterIdentity] = {}
    for identity in identities:
        token = _normalize_identity_token(identity.name)
        if not token or token in unique:
            continue
        if token in excluded_tokens:
            continue
        if not identity.is_alive or not _identity_is_resolved(identity):
            continue
        unique[token] = identity
    if not unique:
        return None
    return sorted(
        unique.values(),
        key=lambda item: (
            _identity_role_priority(item.role),
            _normalize_identity_token(item.name),
        ),
        reverse=True,
    )[0]


def _extract_chapter_goal(text: str | None) -> str:
    if not text:
        return ""
    match = re.search(r"\[chapter goal:\s*(.*?)\]", text, flags=re.IGNORECASE)
    if match:
        return _clean(match.group(1).rstrip(".。"))
    return ""


def _is_generic_scene_contract_text(value: str | None) -> bool:
    text = _clean(value)
    if not text:
        return True
    if text in GENERIC_SCENE_CONTRACT_LABELS:
        return True
    return _contains_marker(text, GENERIC_SCENE_CONTRACT_SUMMARY_MARKERS)


def _legacy_scene_contract_replacement(
    *,
    chapter_number: int | None,
    scene_number: int | None,
    summary: str | None,
) -> str:
    goal = _extract_chapter_goal(summary)
    suffix = f": {goal}" if goal else "."
    scene_label = (
        f"Chapter {chapter_number} scene {scene_number}"
        if chapter_number and scene_number
        else f"Scene {scene_number or '?'}"
    )
    if scene_number == 1:
        task = "clarifies the immediate objective through visible pressure"
    elif scene_number == 2:
        task = "adds a fresh cost, obstacle, or leverage shift"
    elif scene_number == 3:
        task = "turns the active objective into a concrete threat or forced choice"
    else:
        task = "advances a specific choice, cost, discovery, or relationship shift"
    return f"{scene_label} {task}{suffix}"


def repair_legacy_scene_contract_model_pre_draft(
    scene_contract: Any,
    *,
    chapter_number: int | None = None,
    scene_number: int | None = None,
) -> int:
    """Mutate legacy scene-contract text that leaks planning template labels.

    Recovered long-running projects can contain fields like ``Closing Hook`` in
    ``information_release``. Those labels are not story facts and can trigger
    deterministic contradiction checks as if the scene revealed information
    about closing a door. Replace them with concrete scene tasks before prompts
    and safety gates consume the contract.
    """

    repaired = 0
    contract_chapter = chapter_number or getattr(scene_contract, "chapter_number", None)
    contract_scene = scene_number or getattr(scene_contract, "scene_number", None)
    summary = _clean(getattr(scene_contract, "contract_summary", None))
    replacement = _legacy_scene_contract_replacement(
        chapter_number=contract_chapter,
        scene_number=contract_scene,
        summary=summary,
    )

    if _is_generic_scene_contract_text(getattr(scene_contract, "information_release", None)):
        setattr(scene_contract, "information_release", replacement)
        repaired += 1

    if _is_generic_scene_contract_text(getattr(scene_contract, "tail_hook", None)):
        setattr(
            scene_contract,
            "tail_hook",
            f"{replacement} Leave a visible unresolved consequence for the next beat.",
        )
        repaired += 1

    if _is_generic_scene_contract_text(summary):
        setattr(scene_contract, "contract_summary", replacement)
        repaired += 1

    return repaired


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
        if _is_functional_chapter_title(chapter.title):
            violations.append(
                NarrativeContractViolation(
                    code="PLAN_CHAPTER_TITLE_FUNCTIONAL",
                    location=f"{chapter_location}.title",
                    message=(
                        f"Chapter {chapter.chapter_number} title looks like an internal phase label; "
                        "it must name a concrete story image, object, place, person, or event."
                    ),
                    metadata={"chapter_number": chapter.chapter_number, "title": chapter.title},
                )
            )
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
        elif _has_meta_planning_language(chapter.main_conflict):
            violations.append(
                NarrativeContractViolation(
                    code="PLAN_CHAPTER_CONFLICT_META",
                    location=f"{chapter_location}.main_conflict",
                    message=(
                        f"Chapter {chapter.chapter_number} conflict is written as a planning function; "
                        "it must be a reader-visible obstacle or confrontation."
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
        elif _has_meta_planning_language(chapter.chapter_goal):
            violations.append(
                NarrativeContractViolation(
                    code="PLAN_CHAPTER_GOAL_META",
                    location=f"{chapter_location}.chapter_goal",
                    message=(
                        f"Chapter {chapter.chapter_number} goal is written as an author/planner note; "
                        "it must say what the protagonist visibly tries to do and what changes."
                    ),
                    metadata={"chapter_number": chapter.chapter_number},
                )
            )
        if _clean(chapter.opening_situation) and _contains_marker(
            chapter.opening_situation,
            GENERIC_OPENING_SITUATION_MARKERS,
        ):
            violations.append(
                NarrativeContractViolation(
                    code="PLAN_CHAPTER_OPENING_GENERIC",
                    location=f"{chapter_location}.opening_situation",
                    message=(
                        f"Chapter {chapter.chapter_number} opening situation must name the "
                        "specific carry-over event, location, pressure, and active choice; "
                        "template continuity text is not enough."
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
        elif _has_meta_planning_language(chapter.hook_description):
            violations.append(
                NarrativeContractViolation(
                    code="PLAN_CHAPTER_HOOK_META",
                    location=f"{chapter_location}.hook_description",
                    message=(
                        f"Chapter {chapter.chapter_number} hook is a recipe, not an event; "
                        "it must name the concrete next pressure, choice, reveal, or loss."
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
            elif _has_meta_planning_language(story_purpose):
                violations.append(
                    NarrativeContractViolation(
                        code="PLAN_SCENE_STORY_PURPOSE_META",
                        location=f"{scene_location}.purpose.story",
                        message=(
                            f"Chapter {chapter.chapter_number} scene {scene.scene_number} "
                            "story purpose is written as planning commentary; use a visible action."
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
    excluded_names: Iterable[str] = (),
    methodology_contract_mode: str = "off",
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
    excluded_tokens = {
        _normalize_identity_token(name)
        for name in excluded_names
        if _normalize_identity_token(name)
    }

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
            if _normalize_identity_token(referenced_name) in excluded_tokens:
                continue
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

    methodology_mode = normalize_methodology_contract_mode(methodology_contract_mode)
    if methodology_contract_requires_checks(methodology_mode):
        metadata = getattr(scene, "metadata_json", None) or {}
        overlay = metadata.get("methodology_contract") if isinstance(metadata, dict) else {}
        overlay_findings = validate_scene_methodology_contract(
            overlay,
            chapter_number=chapter_number,
            scene_number=scene_number if isinstance(scene_number, int) else None,
            scene_type=getattr(scene, "scene_type", None),
            participant_count=len(participants),
        )
        target = violations if methodology_contract_blocks(methodology_mode) else warnings
        for finding in overlay_findings:
            target.append(
                NarrativeContractViolation(
                    code=finding.code,
                    location=finding.path,
                    message=finding.message,
                    severity=(
                        finding.severity
                        if methodology_contract_blocks(methodology_mode)
                        else "warning"
                    ),
                    metadata={"methodology_contract_mode": methodology_mode},
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


def _deepcopy_json_mapping(value: dict[str, Any]) -> dict[str, Any]:
    copied: dict[str, Any] = {}
    for key, item in value.items():
        if isinstance(item, dict):
            copied[key] = _deepcopy_json_mapping(item)
        elif isinstance(item, list):
            copied[key] = [
                _deepcopy_json_mapping(child) if isinstance(child, dict) else child
                for child in item
            ]
        else:
            copied[key] = item
    return copied


def _iter_raw_cast_character_dicts(cast_spec: dict[str, Any]) -> Iterable[dict[str, Any]]:
    for key in ("protagonist", "antagonist"):
        value = cast_spec.get(key)
        if isinstance(value, dict):
            yield value
    supporting_cast = cast_spec.get("supporting_cast")
    if isinstance(supporting_cast, list):
        for value in supporting_cast:
            if isinstance(value, dict):
                yield value


def _coerce_lock_gender(value: Any) -> str | None:
    gender = normalize_character_gender(value)
    return None if gender == "unknown" else gender


def _coerce_lock_gender_from_pronouns(character: dict[str, Any]) -> str | None:
    pronoun_text = " ".join(
        _clean(character.get(key)).lower()
        for key in ("pronoun_set_en", "pronoun_set_zh", "pronouns")
    )
    if not pronoun_text:
        return None
    if any(marker in pronoun_text for marker in ("she/her", "she", "her", "她")):
        return "female"
    if any(marker in pronoun_text for marker in ("he/him", "he", "him", "他")):
        return "male"
    if any(
        marker in pronoun_text
        for marker in ("they/them", "they", "them", "it/its", "it", "its", "它", "ta")
    ):
        return "nonbinary"
    return None


def _pronouns_for_gender(gender: str) -> tuple[str, str]:
    if gender == "male":
        return ("他", "he/him")
    if gender == "female":
        return ("她", "she/her")
    return ("ta", "they/them")


_LEGACY_FEMALE_NAME_MARKERS = {
    "aisha",
    "alice",
    "elena",
    "emily",
    "maya",
    "mira",
    "nora",
    "sophie",
    "zoe",
}
_LEGACY_MALE_NAME_MARKERS = {
    "cole",
    "elias",
    "garrett",
    "kade",
    "kane",
    "marcus",
    "silas",
    "victor",
}
_LEGACY_FEMALE_HANZI = set("女母妹姐婉鸢霜棠蝉静蕴璃鹂曼薇荷蘅琳瑶雪婵妍娜莉姝媛")
_LEGACY_MALE_HANZI = set("男父兄弟沉骁域铮彦铎峥屿鹤达朔锋山川杰刚强峰龙")


def _infer_legacy_gender(name: str, character: dict[str, Any]) -> str | None:
    lowered = name.lower()
    if any(marker in lowered for marker in _LEGACY_FEMALE_NAME_MARKERS):
        return "female"
    if any(marker in lowered for marker in _LEGACY_MALE_NAME_MARKERS):
        return "male"

    labels = " ".join(
        _clean(character.get(key))
        for key in ("role", "archetype", "background", "relationship_to_protagonist")
    )
    combined = f"{name} {labels}"
    if any(marker in combined for marker in ("女人", "女性", "女孩", "母亲", "妹妹", "姐姐")):
        return "female"
    if any(marker in combined for marker in ("男人", "男性", "男孩", "父亲", "哥哥", "弟弟")):
        return "male"
    if any(char in _LEGACY_FEMALE_HANZI for char in name):
        return "female"
    if any(char in _LEGACY_MALE_HANZI for char in name):
        return "male"
    return None


def _looks_english_text(value: str) -> bool:
    letters = sum(1 for char in value if char.isascii() and char.isalpha())
    non_ascii = sum(1 for char in value if not char.isascii() and not char.isspace())
    return letters > non_ascii


def _legacy_chapter_scene_prefix(
    *,
    chapter_number: int | None,
    scene_number: Any,
    english: bool,
) -> str:
    if english:
        if chapter_number is not None:
            return f"Chapter {chapter_number} scene {scene_number}"
        return f"Scene {scene_number}"
    if chapter_number is not None:
        return f"第{chapter_number}章第{scene_number}场"
    return f"第{scene_number}场"


def _extract_legacy_chapter_goal(story: str) -> str:
    markers = ("本章目标：", "[chapter goal:")
    for marker in markers:
        start = story.find(marker)
        if start < 0:
            continue
        goal = story[start + len(marker):]
        for end_marker in ("）", ")", "]"):
            end = goal.find(end_marker)
            if end >= 0:
                goal = goal[:end]
                break
        goal = goal.strip(" .。；;]")
        if goal:
            return goal
    return ""


def _legacy_scene_anchor(scene: Any, story: str) -> str:
    for value in (
        _clean(getattr(scene, "title", None)),
        _clean(getattr(scene, "hook_requirement", None)),
        _extract_legacy_chapter_goal(story),
        _clean(getattr(scene, "scene_type", None)),
    ):
        if value:
            return value
    return "legacy recovered scene"


def _legacy_scene_time_label(
    scene: Any,
    *,
    chapter_number: int | None,
    scene_number: Any,
    story: str,
) -> str:
    anchor = _legacy_scene_anchor(scene, story)
    english = _looks_english_text(f"{anchor} {story}")
    prefix = _legacy_chapter_scene_prefix(
        chapter_number=chapter_number,
        scene_number=scene_number,
        english=english,
    )
    return f"{prefix}: {anchor}" if english else f"{prefix}：{anchor}"


def _legacy_scene_story_purpose(
    scene: Any,
    *,
    chapter_number: int | None,
    scene_number: Any,
    story: str,
) -> str:
    anchor = _legacy_scene_anchor(scene, story)
    english = _looks_english_text(f"{anchor} {story}")
    prefix = _legacy_chapter_scene_prefix(
        chapter_number=chapter_number,
        scene_number=scene_number,
        english=english,
    )
    if english:
        return (
            f"{prefix}: turn \"{anchor}\" into a concrete on-page beat with "
            "a visible choice, cost, leverage shift, and next-scene pressure."
        )
    return f"{prefix}围绕「{anchor}」交付可见选择、代价变化、关系/势力位移和下一场压力。"


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
                existing = index.get(normalized)
                if existing is None or _prefer_identity_for_token(
                    identity,
                    existing,
                    normalized_token=normalized,
                ):
                    index[normalized] = identity
    return index


def _prefer_identity_for_token(
    candidate: CharacterIdentity,
    existing: CharacterIdentity,
    *,
    normalized_token: str,
) -> bool:
    """Prefer draft-ready canonical identities over unresolved legacy rows."""

    candidate_resolved = _identity_is_resolved(candidate)
    existing_resolved = _identity_is_resolved(existing)
    if candidate_resolved != existing_resolved:
        return candidate_resolved
    candidate_exact = _normalize_identity_token(candidate.name) == normalized_token
    existing_exact = _normalize_identity_token(existing.name) == normalized_token
    if candidate_exact != existing_exact:
        return candidate_exact
    if candidate.is_alive != existing.is_alive:
        return candidate.is_alive
    candidate_role_score = _identity_role_priority(candidate.role)
    existing_role_score = _identity_role_priority(existing.role)
    if candidate_role_score != existing_role_score:
        return candidate_role_score > existing_role_score
    return len(_clean(candidate.name)) > len(_clean(existing.name))


def _identity_role_priority(role: str) -> int:
    normalized = _clean(role).lower()
    if normalized in {"protagonist", "main", "lead", "主角", "男主", "女主"}:
        return 3
    if normalized in {"family", "ally", "antagonist", "love_interest"}:
        return 2
    if normalized:
        return 1
    return 0


def _identity_is_resolved(identity: CharacterIdentity) -> bool:
    return (
        identity.gender != "unknown"
        and bool(identity.pronoun_set_zh)
        and bool(identity.pronoun_set_en)
    )


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


def _is_functional_chapter_title(value: Any) -> bool:
    title = _clean(value)
    if not title or len(title) > 8:
        return False
    return any(title.startswith(prefix) for prefix in FUNCTIONAL_TITLE_PREFIXES_ZH) and any(
        title.endswith(suffix) for suffix in FUNCTIONAL_TITLE_SUFFIXES_ZH
    )


def _has_meta_planning_language(value: Any) -> bool:
    text = _clean(value)
    if not text:
        return False
    return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in META_PLANNING_PATTERNS)


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


def _extract_purpose_identity_mentions(
    text: str,
    identity_index: dict[str, Any],
) -> tuple[str, ...]:
    """Find already-known identity names/aliases mentioned in free-form prose."""

    if not text:
        return ()

    identities: dict[str, Any] = {}
    first_token_counts: dict[str, int] = {}
    for identity in identity_index.values():
        display_name = _identity_display_name(identity)
        normalized_name = _normalize_identity_token(display_name)
        if not normalized_name or normalized_name in identities:
            continue
        identities[normalized_name] = identity
        first = _english_first_token(display_name)
        if first:
            first_token_counts[first] = first_token_counts.get(first, 0) + 1

    found: list[str] = []
    seen: set[str] = set()
    for identity in identities.values():
        if _purpose_identity_is_inactive(identity):
            continue
        display_name = _identity_display_name(identity)
        tokens = [display_name, *_identity_aliases(identity)]
        first = _english_first_token(display_name)
        if first and first_token_counts.get(first) == 1:
            tokens.append(first)
        if not any(_text_mentions_identity_token(text, token) for token in tokens):
            continue
        key = _normalize_identity_token(display_name)
        if key and key not in seen:
            seen.add(key)
            found.append(display_name)
    return tuple(found)


def _identity_aliases(identity: Any) -> list[str]:
    if isinstance(identity, CharacterIdentity):
        return list(identity.aliases)
    if isinstance(identity, dict):
        return _string_list(identity.get("aliases"))
    return _string_list(getattr(identity, "aliases", []))


def _english_first_token(value: str) -> str:
    parts = [part for part in re.split(r"[^A-Za-z]+", _clean(value)) if part]
    if len(parts) < 2:
        return ""
    first = parts[0]
    return first if len(first) >= 3 else ""


def _text_mentions_identity_token(text: str, token: str) -> bool:
    token = _clean(token)
    if not token:
        return False
    if any(char.isascii() and char.isalpha() for char in token):
        pattern = rf"(?<![A-Za-z0-9_]){re.escape(token)}(?![A-Za-z0-9_])"
        return re.search(pattern, text, flags=re.IGNORECASE) is not None
    return token in text


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
    if isinstance(identity, CharacterIdentity) and not _identity_is_resolved(identity):
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
