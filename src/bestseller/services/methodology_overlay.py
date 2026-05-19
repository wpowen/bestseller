"""Methodology execution overlay helpers.

The overlay turns writing-methodology ideas into compact, structured execution
contracts that can travel through planning, materialization, prompts, and
review without adding database tables in the first rollout.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from bestseller.services.writing_profile import is_english_language

_PRESSURE_LABELS_ZH = {
    "deadline": "时限",
    "time_limit": "时限",
    "resource_shortage": "资源不足",
    "exposure_risk": "暴露风险",
    "social_pressure": "社会压力",
    "moral_dilemma": "道德两难",
    "irreversible_cost": "不可逆代价",
    "unknown_threat": "未知威胁",
    "self_restriction": "自身限制",
}

_PRESSURE_LABELS_EN = {
    "deadline": "deadline",
    "time_limit": "deadline",
    "resource_shortage": "resource shortage",
    "exposure_risk": "exposure risk",
    "social_pressure": "social pressure",
    "moral_dilemma": "moral dilemma",
    "irreversible_cost": "irreversible cost",
    "unknown_threat": "unknown threat",
    "self_restriction": "self restriction",
}

METHODOLOGY_CONTRACT_MODES = {"off", "warn", "strict"}

_LOW_SIGNAL_CONTRACT_MARKERS = (
    "推动剧情",
    "推动本章",
    "推进剧情",
    "推进主线",
    "提升紧张感",
    "制造悬念",
    "增强冲突",
    "深化关系",
    "丰富世界观",
    "建立世界观",
    "引出下文",
    "承接上文",
    "新的情况",
    "新的证据、时限或代价",
    "new evidence, deadline, or cost",
    "advance the plot",
    "build tension",
    "deepen the relationship",
)

_CHAPTER_SCOPE_ONLY_KEYS = {
    "pacing_mode",
    "emotion_phase",
    "hooks_to_resolve",
    "resolve_hooks",
    "hooks_to_plant",
    "plant_hooks",
    "is_climax",
    "climax",
    "loop_position",
    "core_loop_position",
}

_SCENE_SCOPE_ONLY_KEYS = {
    "hook_type",
    "tail_hook_type",
    "spotlight_character",
    "focus_character",
    "information_control_mode",
    "reader_knowledge_mode",
    "camera_distance",
    "camera",
    "shot",
    "reveal_mode",
    "information_reveal_mode",
    "signature_image",
    "memorable_image",
    "cut_point",
    "scene_break",
    "ending_cut",
    "action_sequence",
    "action_beats",
}

_STORY_SCOPE_ONLY_KEYS = {
    "ability_origin_contract",
    "ability_contract",
    "power_contract",
    "recognition_anchors",
    "character_recognition",
    "visual_identity",
    "core_loop_contract",
    "relationship_debt_ledger",
}


@dataclass(frozen=True)
class OverlayFinding:
    code: str
    message: str
    severity: str = "major"
    path: str = ""

    def to_dict(self) -> dict[str, str]:
        return {
            "code": self.code,
            "message": self.message,
            "severity": self.severity,
            "path": self.path,
        }


def normalize_methodology_contract_mode(value: Any) -> str:
    rendered = str(value or "").strip().lower().replace("_", "-")
    if rendered in {"warning", "warnings", "warn-only", "warnonly", "legacy"}:
        return "warn"
    if rendered in {"required", "require", "block", "blocking", "enforce", "enforced"}:
        return "strict"
    if rendered in {"disabled", "disable", "false", "0", "none"}:
        return "off"
    return rendered if rendered in METHODOLOGY_CONTRACT_MODES else "warn"


def resolve_methodology_contract_mode(
    project: Any | None = None,
    *,
    settings: Any | None = None,
) -> str:
    """Resolve off/warn/strict with project metadata taking precedence."""

    metadata = as_mapping(getattr(project, "metadata_json", None))
    if metadata.get("methodology_contract_strict") is True:
        return "strict"
    if metadata.get("methodology_contract_warn_only") is True:
        return "warn"
    for key in (
        "methodology_contract_mode",
        "methodology_overlay_mode",
        "methodology_gate_mode",
    ):
        if key in metadata:
            return normalize_methodology_contract_mode(metadata.get(key))
    pipeline = getattr(settings, "pipeline", None)
    if pipeline is not None and hasattr(pipeline, "methodology_contract_mode"):
        return normalize_methodology_contract_mode(
            getattr(pipeline, "methodology_contract_mode")
        )
    return "warn"


def methodology_contract_requires_checks(mode: str | None) -> bool:
    return normalize_methodology_contract_mode(mode) in {"warn", "strict"}


def methodology_contract_blocks(mode: str | None) -> bool:
    return normalize_methodology_contract_mode(mode) == "strict"


def as_mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, list):
        return "；".join(item for item in (text(v) for v in value) if item)
    if isinstance(value, Mapping):
        for key in (
            "description",
            "summary",
            "value",
            "cost",
            "visible_signature",
            "source",
            "trigger",
        ):
            rendered = text(value.get(key))
            if rendered:
                return rendered
        return "；".join(item for item in (text(v) for v in value.values()) if item)
    return str(value).strip()


def text_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [item for item in (text(v) for v in value) if item]
    if isinstance(value, tuple):
        return text_list(list(value))
    rendered = text(value)
    return [rendered] if rendered else []


def first_text(data: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        rendered = text(data.get(key))
        if rendered:
            return rendered
    return ""


def pressure_buffs_from_overlay(
    overlay: Mapping[str, Any] | None,
    *,
    language: str | None = None,
) -> list[str]:
    data = as_mapping(overlay)
    buffs = text_list(
        data.get("conflict_buffs")
        or data.get("pressure_buffs")
        or data.get("buffs")
    )
    stack = data.get("pressure_stack")
    is_en = is_english_language(language)
    labels = _PRESSURE_LABELS_EN if is_en else _PRESSURE_LABELS_ZH
    if isinstance(stack, Mapping):
        for key, raw in stack.items():
            rendered = text(raw)
            if not rendered:
                continue
            label = labels.get(str(key), str(key))
            buffs.append(f"{label}: {rendered}")
    elif isinstance(stack, list):
        buffs.extend(text_list(stack))
    return _unique(buffs)


def normalize_chapter_overlay(value: Any) -> dict[str, Any]:
    data = as_mapping(value)
    if not data:
        return {}
    normalized: dict[str, Any] = {
        "conflict_stakes": first_text(
            data,
            "conflict_stakes",
            "stakes",
            "what_is_lost",
            "failure_cost",
        ),
        "conflict_buffs": pressure_buffs_from_overlay(data),
        "pacing_mode": first_text(data, "pacing_mode", "scene_rhythm", "rhythm"),
        "emotion_phase": first_text(data, "emotion_phase", "pressure_phase"),
        "hooks_to_resolve": text_list(data.get("hooks_to_resolve") or data.get("resolve_hooks")),
        "hooks_to_plant": text_list(data.get("hooks_to_plant") or data.get("plant_hooks")),
        "relationship_debts": text_list(
            data.get("relationship_debts")
            or data.get("relation_debts")
            or data.get("relationship_promises")
            or data.get("relationship_hooks")
        ),
        "is_climax": bool(data.get("is_climax") or data.get("climax")),
        "loop_position": first_text(data, "loop_position", "core_loop_position"),
    }
    return _drop_empty(normalized)


def normalize_scene_overlay(value: Any) -> dict[str, Any]:
    data = as_mapping(value)
    if not data:
        return {}
    normalized: dict[str, Any] = {
        "conflict_stakes": first_text(
            data,
            "conflict_stakes",
            "stakes",
            "what_is_lost",
            "failure_cost",
        ),
        "conflict_buffs": pressure_buffs_from_overlay(data),
        "hook_type": first_text(data, "hook_type", "tail_hook_type"),
        "spotlight_character": first_text(data, "spotlight_character", "focus_character"),
        "information_control_mode": first_text(
            data,
            "information_control_mode",
            "reveal_mode",
            "reader_knowledge_mode",
        ),
        "action_sequence": text_list(data.get("action_sequence") or data.get("action_beats")),
        "camera_distance": first_text(data, "camera_distance", "camera", "shot"),
        "reveal_mode": first_text(data, "reveal_mode", "information_reveal_mode"),
        "signature_image": first_text(data, "signature_image", "memorable_image"),
        "cut_point": first_text(data, "cut_point", "scene_break", "ending_cut"),
        "relationship_debts": text_list(
            data.get("relationship_debts")
            or data.get("relation_debts")
            or data.get("relationship_promises")
            or data.get("relationship_hooks")
        ),
    }
    return _drop_empty(normalized)


def normalize_character_overlay(value: Any) -> dict[str, Any]:
    data = as_mapping(value)
    if not data:
        return {}
    recognition = as_mapping(
        data.get("recognition_anchors")
        or data.get("character_recognition")
        or data.get("visual_identity")
    )
    ability = as_mapping(
        data.get("ability_origin_contract")
        or data.get("ability_contract")
        or data.get("power_contract")
    )
    return _drop_empty(
        {
            "recognition_anchors": _drop_empty(
                {
                    "body_marker": first_text(recognition, "body_marker", "physical_marker"),
                    "object_marker": first_text(recognition, "object_marker", "signature_object"),
                    "voice_marker": first_text(recognition, "voice_marker", "verbal_tic"),
                    "action_marker": first_text(recognition, "action_marker", "tag_memory"),
                    "emotion_tell": first_text(recognition, "emotion_tell", "emotional_tell"),
                }
            ),
            "ability_origin_contract": _drop_empty(
                {
                    "source": first_text(ability, "source", "origin"),
                    "visible_signature": first_text(ability, "visible_signature", "manifestation"),
                    "limit": first_text(ability, "limit", "hard_limit", "constraint"),
                    "cost": first_text(ability, "cost", "visible_cost"),
                    "side_effect": first_text(ability, "side_effect", "backlash"),
                    "growth_trigger": first_text(ability, "growth_trigger", "upgrade_trigger"),
                    "reveal_ladder": text_list(ability.get("reveal_ladder")),
                    "plot_use": first_text(ability, "plot_use", "conflict_use", "narrative_use"),
                }
            ),
        }
    )


def render_overlay_prompt_block(
    *,
    chapter_overlay: Mapping[str, Any] | None = None,
    scene_overlay: Mapping[str, Any] | None = None,
    language: str | None = None,
) -> str:
    is_en = is_english_language(language)
    chapter = normalize_chapter_overlay(chapter_overlay)
    scene = normalize_scene_overlay(scene_overlay)
    if not chapter and not scene:
        return ""

    lines = ["Methodology execution overlay:" if is_en else "方法论执行覆盖层："]
    if chapter:
        lines.append("Chapter:" if is_en else "章节：")
        _append_line(lines, "stakes" if is_en else "筹码", chapter.get("conflict_stakes"))
        _append_list(lines, "pressure buffs" if is_en else "压力叠加", chapter.get("conflict_buffs"))
        _append_line(lines, "pacing" if is_en else "节奏位", chapter.get("pacing_mode"))
        _append_line(lines, "emotion phase" if is_en else "情绪阶段", chapter.get("emotion_phase"))
        _append_list(lines, "hooks to resolve" if is_en else "待消解钩子", chapter.get("hooks_to_resolve"))
        _append_list(lines, "hooks to plant" if is_en else "新植入钩子", chapter.get("hooks_to_plant"))
        _append_list(lines, "relationship debts" if is_en else "关系债务", chapter.get("relationship_debts"))
    if scene:
        lines.append("Scene:" if is_en else "场景：")
        _append_line(lines, "stakes" if is_en else "筹码", scene.get("conflict_stakes"))
        _append_list(lines, "pressure buffs" if is_en else "压力叠加", scene.get("conflict_buffs"))
        _append_line(lines, "hook type" if is_en else "钩子类型", scene.get("hook_type"))
        _append_line(lines, "spotlight" if is_en else "焦点角色", scene.get("spotlight_character"))
        _append_line(lines, "camera distance" if is_en else "镜头距离", scene.get("camera_distance"))
        _append_line(lines, "reveal mode" if is_en else "揭示模式", scene.get("reveal_mode"))
        _append_line(lines, "signature image" if is_en else "标志画面", scene.get("signature_image"))
        _append_line(lines, "cut point" if is_en else "断点", scene.get("cut_point"))
        _append_list(lines, "action beats" if is_en else "动作序列", scene.get("action_sequence"))
        _append_list(lines, "relationship debts" if is_en else "关系债务", scene.get("relationship_debts"))
    return "\n".join(lines)


def validate_recognition_anchors(
    *,
    character_name: str,
    role: str,
    ip_anchor: Any,
    overlay: Mapping[str, Any] | None,
    model_extra: Mapping[str, Any] | None = None,
) -> list[OverlayFinding]:
    role_lower = role.lower()
    if "protagonist" not in role_lower and "antagonist" not in role_lower:
        return []

    overlay_data = normalize_character_overlay(overlay)
    recognition = as_mapping(overlay_data.get("recognition_anchors"))
    extra = as_mapping(model_extra)
    populated = [
        first_text(recognition, "body_marker") or text(extra.get("physical_description")),
        first_text(recognition, "object_marker") or text_list(getattr(ip_anchor, "signature_objects", [])),
        first_text(recognition, "voice_marker") or text_list(getattr(ip_anchor, "quirks", [])),
        first_text(recognition, "action_marker") or text(getattr(ip_anchor, "tag_memory", None)),
        first_text(recognition, "emotion_tell") or text_list(getattr(ip_anchor, "sensory_signatures", [])),
    ]
    count = sum(1 for value in populated if value)
    if count >= 3:
        return []
    return [
        OverlayFinding(
            code="CHARACTER_RECOGNITION_ANCHORS_MISSING",
            path=f"character:{character_name}",
            message=(
                f"{character_name} 只有 {count}/5 类可识别锚点；至少需要身体/物件/声音/动作/情绪外显中的3类。"
            ),
        )
    ]


def validate_ability_origin_contract(
    *,
    character_name: str,
    role: str,
    overlay: Mapping[str, Any] | None,
    project_genre_text: str = "",
) -> list[OverlayFinding]:
    role_lower = role.lower()
    if "protagonist" not in role_lower:
        return []
    genre_lower = project_genre_text.lower()
    requires_power = any(
        token in genre_lower
        for token in (
            "xianxia",
            "cultivation",
            "progression",
            "litrpg",
            "system",
            "power",
            "玄幻",
            "修仙",
            "仙侠",
            "升级",
            "异能",
            "系统",
        )
    )
    if not requires_power:
        return []
    ability = as_mapping(normalize_character_overlay(overlay).get("ability_origin_contract"))
    required = ("source", "visible_signature", "limit", "cost", "growth_trigger", "plot_use")
    missing = [key for key in required if not text(ability.get(key))]
    if not missing:
        return []
    return [
        OverlayFinding(
            code="ABILITY_ORIGIN_CONTRACT_MISSING",
            path=f"character:{character_name}.methodology_overlay.ability_origin_contract",
            message=f"{character_name} 的能力来源合同缺失字段：{', '.join(missing)}。",
        )
    ]


def validate_chapter_methodology_contract(
    overlay: Mapping[str, Any] | None,
    *,
    chapter_number: int | None = None,
) -> list[OverlayFinding]:
    raw = as_mapping(overlay)
    data = normalize_chapter_overlay(raw)
    path = (
        f"chapter_outline_batch.chapters[{chapter_number}].methodology_contract"
        if chapter_number is not None
        else "chapter.methodology_contract"
    )
    findings: list[OverlayFinding] = []
    if not data:
        return [
            OverlayFinding(
                code="CHAPTER_METHODOLOGY_CONTRACT_MISSING",
                path=path,
                message="章节缺少 methodology_contract；需要筹码、压力叠加、节奏位、情绪阶段和钩子计划。",
            )
        ]

    required_text_fields = {
        "conflict_stakes": "筹码/失败代价",
        "pacing_mode": "章节节奏位",
        "emotion_phase": "情绪阶段",
        "loop_position": "核心爽点循环位置",
    }
    for key, label in required_text_fields.items():
        value = data.get(key)
        if not text(value):
            findings.append(
                OverlayFinding(
                    code="CHAPTER_METHODOLOGY_FIELD_MISSING",
                    path=f"{path}.{key}",
                    message=f"章节 methodology_contract 缺少{label}。",
                )
            )
        elif _is_low_signal_contract_value(value):
            findings.append(
                OverlayFinding(
                    code="CHAPTER_METHODOLOGY_FIELD_GENERIC",
                    path=f"{path}.{key}",
                    message=f"章节 methodology_contract 的{label}过于抽象，必须改成读者可见的具体事件/代价。",
                )
            )

    hooks = list(data.get("hooks_to_resolve") or []) + list(data.get("hooks_to_plant") or [])
    if not hooks:
        findings.append(
            OverlayFinding(
                code="CHAPTER_METHODOLOGY_HOOKS_MISSING",
                path=f"{path}.hooks",
                message="章节 methodology_contract 至少需要待消解或新植入钩子。",
            )
        )
    elif any(_is_low_signal_contract_value(item) for item in hooks):
        findings.append(
            OverlayFinding(
                code="CHAPTER_METHODOLOGY_FIELD_GENERIC",
                path=f"{path}.hooks",
                message="章节钩子不能写成泛化目的，必须是具体读者问题或即将兑现的事件。",
            )
        )

    for buff in data.get("conflict_buffs") or []:
        if _is_low_signal_contract_value(buff):
            findings.append(
                OverlayFinding(
                    code="CHAPTER_METHODOLOGY_FIELD_GENERIC",
                    path=f"{path}.conflict_buffs",
                    message="压力叠加不能写成泛化紧张感，必须是具体时限、暴露风险、资源不足、社会压力或两难。",
                )
            )
            break

    misplaced = sorted(set(raw) & (_SCENE_SCOPE_ONLY_KEYS | _STORY_SCOPE_ONLY_KEYS))
    if misplaced:
        findings.append(
            OverlayFinding(
                code="CHAPTER_METHODOLOGY_SCOPE_MISMATCH",
                path=path,
                message=(
                    "章节级 methodology_contract 含有不属于章节层的字段："
                    f"{', '.join(misplaced)}。镜头/断点属于场景层；能力来源/人物识别属于故事层。"
                ),
            )
        )
    return findings


def validate_scene_methodology_contract(
    overlay: Mapping[str, Any] | None,
    *,
    chapter_number: int | None = None,
    scene_number: int | None = None,
    scene_type: str | None = None,
    participant_count: int = 0,
) -> list[OverlayFinding]:
    raw = as_mapping(overlay)
    data = normalize_scene_overlay(raw)
    path = (
        f"scene_card[{chapter_number or '?'}.{scene_number or '?'}].methodology_contract"
    )
    findings: list[OverlayFinding] = []
    if not data:
        return [
            OverlayFinding(
                code="SCENE_METHODOLOGY_CONTRACT_MISSING",
                path=path,
                message="场景缺少 methodology_contract；需要筹码、压力叠加、焦点角色、揭示方式、标志画面和断点。",
            )
        ]

    required_text_fields = {
        "conflict_stakes": "场景筹码/失败代价",
        "hook_type": "钩子类型",
        "spotlight_character": "焦点角色",
        "information_control_mode": "信息控制模式",
        "camera_distance": "镜头距离",
        "reveal_mode": "揭示模式",
        "signature_image": "标志画面",
        "cut_point": "断点",
    }
    for key, label in required_text_fields.items():
        value = data.get(key)
        if not text(value):
            findings.append(
                OverlayFinding(
                    code="SCENE_METHODOLOGY_FIELD_MISSING",
                    path=f"{path}.{key}",
                    message=f"场景 methodology_contract 缺少{label}。",
                )
            )
        elif key in {"conflict_stakes", "signature_image", "cut_point"} and _is_low_signal_contract_value(value):
            findings.append(
                OverlayFinding(
                    code="SCENE_METHODOLOGY_FIELD_GENERIC",
                    path=f"{path}.{key}",
                    message=f"场景 methodology_contract 的{label}过于抽象，必须改成正文可写出的具体画面/事件。",
                )
            )

    if not data.get("conflict_buffs"):
        findings.append(
            OverlayFinding(
                code="SCENE_METHODOLOGY_PRESSURE_STACK_MISSING",
                path=f"{path}.conflict_buffs",
                message="场景 methodology_contract 缺少压力叠加；至少需要一个具体 pressure buff。",
            )
        )
    elif any(_is_low_signal_contract_value(item) for item in data.get("conflict_buffs") or []):
        findings.append(
            OverlayFinding(
                code="SCENE_METHODOLOGY_FIELD_GENERIC",
                path=f"{path}.conflict_buffs",
                message="场景压力叠加不能写成泛化紧张感，必须是具体、可见、会压迫行动的因素。",
            )
        )

    action_scene_types = {"action", "confrontation", "climax", "reveal", "battle", "chase"}
    if str(scene_type or "").strip().lower() in action_scene_types and not data.get("action_sequence"):
        findings.append(
            OverlayFinding(
                code="SCENE_METHODOLOGY_ACTION_SEQUENCE_MISSING",
                path=f"{path}.action_sequence",
                message="动作/对峙/揭示类场景需要 action_sequence，避免正文只写静态说明。",
            )
        )

    if participant_count >= 2 and not data.get("relationship_debts"):
        findings.append(
            OverlayFinding(
                code="SCENE_METHODOLOGY_RELATIONSHIP_DEBT_MISSING",
                path=f"{path}.relationship_debts",
                message="多角色场景需要说明本场推进、偿还或加码的关系债。",
            )
        )

    misplaced = sorted(set(raw) & (_CHAPTER_SCOPE_ONLY_KEYS | _STORY_SCOPE_ONLY_KEYS))
    if misplaced:
        findings.append(
            OverlayFinding(
                code="SCENE_METHODOLOGY_SCOPE_MISMATCH",
                path=path,
                message=(
                    "场景级 methodology_contract 含有不属于场景层的字段："
                    f"{', '.join(misplaced)}。节奏/高潮位置属于章节层；能力来源/人物识别属于故事层。"
                ),
            )
        )
    return findings


def _append_line(lines: list[str], label: str, value: Any) -> None:
    rendered = text(value)
    if rendered:
        lines.append(f"- {label}: {rendered}")


def _append_list(lines: list[str], label: str, value: Any) -> None:
    items = text_list(value)
    if items:
        lines.append(f"- {label}: {'; '.join(items)}")


def _drop_empty(data: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in data.items():
        if isinstance(value, dict):
            nested = _drop_empty(value)
            if nested:
                out[key] = nested
        elif isinstance(value, list):
            cleaned = [item for item in value if text(item)]
            if cleaned:
                out[key] = cleaned
        elif isinstance(value, bool) and value:
            out[key] = value
        elif not isinstance(value, bool) and text(value):
            out[key] = value
    return out


def _is_low_signal_contract_value(value: Any) -> bool:
    rendered = text(value)
    if not rendered:
        return True
    normalized = rendered.lower()
    return any(marker.lower() in normalized for marker in _LOW_SIGNAL_CONTRACT_MARKERS)


def _unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out
