from __future__ import annotations

# ruff: noqa: ANN401
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

_FRONT_REQUIRED_ALIASES: dict[str, tuple[str, ...]] = {
    "stakes": ("stakes", "conflict_stakes"),
    "pressure_stack": ("pressure_stack", "conflict_buffs", "pressure_buffs", "pressure"),
    "focus_character": ("focus_character", "spotlight_character", "pov_character"),
    "reveal_mode": ("reveal_mode", "information_control_mode"),
    "signature_image": ("signature_image",),
    "breakpoint": ("breakpoint", "cut_point"),
}


@dataclass(frozen=True)
class ChapterContractMaterializationReport:
    changed: bool
    filled_fields: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "changed": self.changed,
            "filled_fields": list(self.filled_fields),
        }


@dataclass(frozen=True)
class SceneContractMaterializationChange:
    scene_number: int
    filled_fields: tuple[str, ...]
    unresolved_fields: tuple[str, ...]

    @property
    def changed(self) -> bool:
        return bool(self.filled_fields)

    @property
    def complete(self) -> bool:
        return not self.unresolved_fields

    def to_dict(self) -> dict[str, Any]:
        return {
            "scene_number": self.scene_number,
            "filled_fields": list(self.filled_fields),
            "unresolved_fields": list(self.unresolved_fields),
            "changed": self.changed,
            "complete": self.complete,
        }


@dataclass(frozen=True)
class ChapterSceneContractMaterializationReport:
    changed: bool
    complete: bool
    scene_changes: tuple[SceneContractMaterializationChange, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "changed": self.changed,
            "complete": self.complete,
            "scene_changes": [change.to_dict() for change in self.scene_changes],
            "unresolved_scene_count": sum(
                1 for change in self.scene_changes if not change.complete
            ),
        }


def materialize_chapter_scene_contracts(
    *,
    chapter: Any,
    scenes: Sequence[Any],
    front_chapter_limit: int = 10,
    source: str = "chapter_scene_contract_materializer",
) -> ChapterSceneContractMaterializationReport:
    """Persist writer-facing scene contract aliases before chapter drafting.

    The planner stores canonical methodology overlay keys such as
    ``conflict_buffs`` and ``spotlight_character``.  Chapter-first drafting and
    predraft gates need a compact acceptance-facing view too, otherwise the
    framework can reject usable scene cards or fail to show the writer the
    exact pressure, focus, payoff, and exit hook it must land.
    """

    chapter_number = int(getattr(chapter, "chapter_number", 0) or 0)
    scene_changes: list[SceneContractMaterializationChange] = []
    for scene in scenes:
        if chapter_number <= 0 or chapter_number > front_chapter_limit:
            scene_changes.append(
                SceneContractMaterializationChange(
                    scene_number=int(getattr(scene, "scene_number", 0) or 0),
                    filled_fields=(),
                    unresolved_fields=(),
                )
            )
            continue
        scene_changes.append(_materialize_scene(scene, source=source))

    changed = any(change.changed for change in scene_changes)
    complete = all(change.complete for change in scene_changes)
    if changed:
        _mark_chapter_materialized(chapter, scene_changes, complete=complete, source=source)
    return ChapterSceneContractMaterializationReport(
        changed=changed,
        complete=complete,
        scene_changes=tuple(scene_changes),
    )


def materialize_chapter_contract_from_chapter(
    *,
    chapter: Any,
    chapter_contract: Any | None,
    source: str = "chapter_scene_contract_materializer",
) -> ChapterContractMaterializationReport:
    """Keep writer-facing chapter contracts aligned with the current chapter plan."""

    if chapter_contract is None:
        return ChapterContractMaterializationReport(changed=False, filled_fields=())

    filled: list[str] = []
    desired_summary = _text(getattr(chapter, "chapter_goal", None))
    if desired_summary and _text(getattr(chapter_contract, "contract_summary", None)) != desired_summary:
        chapter_contract.contract_summary = desired_summary
        filled.append("contract_summary")

    desired_opening = _text(getattr(chapter, "opening_situation", None))
    if desired_opening:
        opening_state = dict(getattr(chapter_contract, "opening_state", None) or {})
        if _text(opening_state.get("opening_situation")) != desired_opening:
            opening_state["opening_situation"] = desired_opening
            chapter_contract.opening_state = opening_state
            filled.append("opening_state.opening_situation")

    desired_conflict = _text(getattr(chapter, "main_conflict", None))
    if desired_conflict and _text(getattr(chapter_contract, "core_conflict", None)) != desired_conflict:
        chapter_contract.core_conflict = desired_conflict
        filled.append("core_conflict")

    desired_emotion = _text(getattr(chapter, "chapter_emotion_arc", None))
    if desired_emotion and _text(getattr(chapter_contract, "emotional_shift", None)) != desired_emotion:
        chapter_contract.emotional_shift = desired_emotion
        filled.append("emotional_shift")

    desired_information = _chapter_information_release(chapter)
    if desired_information and _text(getattr(chapter_contract, "information_release", None)) != desired_information:
        chapter_contract.information_release = desired_information
        filled.append("information_release")

    desired_hook = _text(getattr(chapter, "hook_description", None))
    if desired_hook and _text(getattr(chapter_contract, "closing_hook", None)) != desired_hook:
        chapter_contract.closing_hook = desired_hook
        filled.append("closing_hook")

    if filled:
        metadata = dict(getattr(chapter_contract, "metadata_json", None) or {})
        metadata["chapter_contract_materialized_at"] = datetime.now(UTC).isoformat()
        metadata["chapter_contract_materialized_by"] = source
        metadata["chapter_contract_materialized_fields"] = list(dict.fromkeys(filled))
        chapter_contract.metadata_json = metadata

    return ChapterContractMaterializationReport(
        changed=bool(filled),
        filled_fields=tuple(dict.fromkeys(filled)),
    )


def _materialize_scene(
    scene: Any,
    *,
    source: str,
) -> SceneContractMaterializationChange:
    scene_number = int(getattr(scene, "scene_number", 0) or 0)
    metadata = dict(getattr(scene, "metadata_json", None) or {})
    contract = dict(_mapping(metadata.get("methodology_contract")))
    purpose = _mapping(getattr(scene, "purpose", None))

    filled: list[str] = []
    unresolved: list[str] = []
    for target_field in _FRONT_REQUIRED_ALIASES:
        if _truthy(contract.get(target_field)) or _truthy(metadata.get(target_field)):
            continue
        value = _derive_scene_contract_value(target_field, scene, metadata, contract, purpose)
        if value:
            contract[target_field] = value
            filled.append(target_field)
        else:
            unresolved.append(target_field)

    gate_defaults = {
        "gate_function": _derive_gate_function(scene, contract),
        "visible_progress": _first_text(
            (metadata, contract, purpose),
            "visible_progress",
            "visible_action_or_reaction",
            "story",
        ),
        "reader_payoff": _first_text(
            (metadata, contract, purpose),
            "reader_payoff",
            "signature_image",
            "reader_hook",
            "emotion",
        ),
        "ending_hook_payload": _first_text(
            (metadata, contract, purpose),
            "ending_hook_payload",
            "cut_point",
            "breakpoint",
            "reader_hook",
        )
        or _text(getattr(scene, "hook_requirement", None)),
    }
    for key, value in gate_defaults.items():
        if key not in metadata and value:
            metadata[key] = value
            filled.append(key)

    if filled:
        metadata["methodology_contract"] = contract
        metadata["chapter_scene_contract_materialized_at"] = datetime.now(UTC).isoformat()
        metadata["chapter_scene_contract_materialized_by"] = source
        scene.metadata_json = metadata
    return SceneContractMaterializationChange(
        scene_number=scene_number,
        filled_fields=tuple(dict.fromkeys(filled)),
        unresolved_fields=tuple(unresolved),
    )


def _derive_scene_contract_value(
    target_field: str,
    scene: Any,
    metadata: Mapping[str, Any],
    contract: Mapping[str, Any],
    purpose: Mapping[str, Any],
) -> Any:
    if target_field == "stakes":
        return _first_text(
            (contract, metadata, purpose),
            "conflict_stakes",
            "failure_cost",
            "story",
        )
    if target_field == "pressure_stack":
        return _first_list_or_text(
            (contract, metadata, purpose),
            "conflict_buffs",
            "pressure_buffs",
            "pressure",
            "reader_hook",
        )
    if target_field == "focus_character":
        return _first_text(
            (contract, metadata, purpose),
            "spotlight_character",
            "pov_character",
            "emotion_target",
        ) or _first_participant(scene)
    if target_field == "reveal_mode":
        return _first_text(
            (contract, metadata, purpose),
            "information_control_mode",
            "reader_knowledge_mode",
            "reader_hook",
        )
    if target_field == "signature_image":
        return _first_text(
            (contract, metadata, purpose),
            "signature_image",
            "memorable_image",
            "reader_hook",
        )
    if target_field == "breakpoint":
        return _first_text(
            (contract, metadata, purpose),
            "cut_point",
            "scene_break",
            "ending_cut",
            "reader_hook",
        ) or _text(getattr(scene, "hook_requirement", None))
    return ""


def _derive_gate_function(scene: Any, contract: Mapping[str, Any]) -> str:
    scene_number = int(getattr(scene, "scene_number", 0) or 0)
    hook_type = _text(contract.get("hook_type")).lower()
    scene_type = _text(getattr(scene, "scene_type", None)).lower()
    if scene_number <= 1:
        return "opening_pull: immediate pressure, visible anomaly, and forced action"
    if "reveal" in hook_type or "reveal" in scene_type:
        return "information_release: one concrete fact changes the next action"
    if scene_number >= 4 or "hook" in hook_type:
        return "ending_hook_effectiveness: add a new visual threat or evidence"
    return "commercial_pull: force a visible choice, cost, or rule test"


def _mark_chapter_materialized(
    chapter: Any,
    scene_changes: Sequence[SceneContractMaterializationChange],
    *,
    complete: bool,
    source: str,
) -> None:
    metadata = dict(getattr(chapter, "metadata_json", None) or {})
    metadata["chapter_scene_contract_materialization"] = {
        "source": source,
        "complete": complete,
        "materialized_at": datetime.now(UTC).isoformat(),
        "scene_changes": [change.to_dict() for change in scene_changes],
    }
    if complete and metadata.get("blocked_by_chapter_predraft_quality_gate"):
        for key in (
            "blocked_by_chapter_predraft_quality_gate",
            "chapter_predraft_quality_block_codes",
            "chapter_predraft_quality_hint",
        ):
            metadata.pop(key, None)
    chapter.metadata_json = metadata


def _chapter_information_release(chapter: Any) -> str:
    """本章信息释放 = 章纲声明的 information_revealed，不含章末钩子。

    2026-08-07 真机 prompt review：hook 此前被追加进 information_release，而
    closing_hook 又单独赋成同一个 hook——information_revealed 为空时，同一句话
    在写手 prompt 的章节契约里逐字出现三次（information_release / closing_hook /
    hooks_to_plant）。信息释放和章末钩子是两个语义字段，钩子自有其位。
    """

    items: list[str] = []
    for item in list(getattr(chapter, "information_revealed", None) or []):
        rendered = _render_information_item(item)
        if rendered:
            items.append(rendered)
    hook = _text(getattr(chapter, "hook_description", None))
    joined = "；".join(_unique_texts(items))
    if joined and hook:
        # 章纲把钩子也塞进 revealed 列表时同样去重。
        joined = "；".join(t for t in _unique_texts(items) if t != hook) or joined
    return joined


def _render_information_item(item: Any) -> str:
    if isinstance(item, Mapping):
        for key in ("summary", "description", "fact", "value", "text", "label"):
            rendered = _text(item.get(key))
            if rendered:
                return rendered
        return _text("; ".join(f"{key}={value}" for key, value in item.items() if value))
    return _text(item)


def _unique_texts(items: Sequence[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for item in items:
        rendered = _text(item)
        if not rendered or rendered in seen:
            continue
        seen.add(rendered)
        result.append(rendered)
    return result


def _first_text(items: Sequence[Mapping[str, Any]], *keys: str) -> str:
    for key in keys:
        for item in items:
            rendered = _text(item.get(key))
            if rendered:
                return rendered
    return ""


def _first_list_or_text(items: Sequence[Mapping[str, Any]], *keys: str) -> Any:
    for key in keys:
        for item in items:
            value = item.get(key)
            if isinstance(value, list) and any(_text(part) for part in value):
                return [part for part in (_text(raw) for raw in value) if part]
            rendered = _text(value)
            if rendered:
                return rendered
    return ""


def _first_participant(scene: Any) -> str:
    participants = getattr(scene, "participants", None) or []
    for participant in participants:
        rendered = _text(participant)
        if rendered:
            return rendered
    return ""


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _truthy(value: Any) -> bool:
    if isinstance(value, list):
        return any(_text(item) for item in value)
    return bool(_text(value))


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, list):
        return "; ".join(part for part in (_text(item) for item in value) if part)
    if isinstance(value, Mapping):
        for key in ("value", "description", "summary", "text"):
            rendered = _text(value.get(key))
            if rendered:
                return rendered
    return str(value).strip()
