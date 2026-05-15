from __future__ import annotations

from collections.abc import Mapping, Sequence
import re

from bestseller.domain.entry_system import (
    EntryDefinition,
    EntryRegistry,
    EntryStateSnapshot,
    EntrySystemKernel,
)

_INACTIVE_STATES = {"lost", "spent", "deprecated", "paid_off", "sealed"}


def _as_mapping(value: object) -> dict[str, object]:
    return dict(value) if isinstance(value, Mapping) else {}


def _as_sequence(value: object) -> list[object]:
    if value is None:
        return []
    if isinstance(value, list | tuple):
        return list(value)
    return [value]


def _text(value: object) -> str:
    if isinstance(value, str):
        return value.strip()
    if value is None or isinstance(value, bool):
        return ""
    return str(value).strip()


def _dedupe(values: Sequence[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        text = _text(value)
        if text and text not in seen:
            seen.add(text)
            out.append(text)
    return tuple(out)


def _slug(text: object, *, fallback: str) -> str:
    raw = _text(text) or fallback
    asciiish = re.sub(r"[^a-zA-Z0-9_\-\u4e00-\u9fff]+", "-", raw).strip("-").lower()
    return asciiish or fallback


def _target_chapters_from_kernel(
    kernel: EntrySystemKernel,
    target_chapters: int | None,
) -> int:
    if target_chapters and target_chapters > 0:
        return target_chapters
    raw = kernel.coverage_targets.get("target_chapters")
    try:
        parsed = int(raw)
    except (TypeError, ValueError):
        return 60
    return parsed if parsed > 0 else 60


def build_entry_coverage_matrix(
    kernel: EntrySystemKernel,
    target_chapters: int | None = None,
    genre: str | None = None,
) -> dict[str, object]:
    """Derive compact quantity targets from a book-specific kernel."""

    chapters = _target_chapters_from_kernel(kernel, target_chapters)
    existing = _as_mapping(kernel.coverage_targets)
    active_types = [item.type for item in kernel.taxonomy]
    pillar_target = int(existing.get("pillar_entries") or min(max(3, len(active_types)), 8))
    supporting_target = int(existing.get("supporting_entries") or (50 if chapters <= 80 else 120))
    per_type_floor = 1 if chapters <= 20 else 2
    type_counts: dict[str, dict[str, int]] = {}
    for index, type_key in enumerate(active_types):
        type_counts[type_key] = {
            "pillar": 1 if index < pillar_target else 0,
            "supporting": per_type_floor,
        }
    return {
        "target_chapters": chapters,
        "genre": genre,
        "active_types": active_types,
        "pillar_entries": pillar_target,
        "supporting_entries": supporting_target,
        "type_counts": type_counts,
        "per_volume_minimums": existing.get(
            "per_volume_minimums",
            {
                "new_entries": 4 if chapters > 20 else 2,
                "entry_state_changes": 8 if chapters > 20 else 3,
                "major_entry_payoffs": 1,
            },
        ),
    }


def _ladder_for_type(kernel: EntrySystemKernel, type_key: str) -> str | None:
    for ladder in kernel.grade_ladders:
        if type_key in ladder.applies_to:
            return ladder.ladder_key
    return None


def _first_grade_for_ladder(kernel: EntrySystemKernel, ladder_key: str | None) -> str | None:
    if not ladder_key:
        return None
    ladder = kernel.ladders_by_key.get(ladder_key)
    if not ladder or not ladder.levels:
        return None
    return ladder.levels[0].key


def _fallback_entry_for_type(
    kernel: EntrySystemKernel,
    *,
    type_key: str,
    label: str,
    tier: str,
    suffix: str,
) -> EntryDefinition:
    ladder_key = _ladder_for_type(kernel, type_key)
    current_grade = _first_grade_for_ladder(kernel, ladder_key)
    entry_id = f"{type_key}-{suffix}"
    return EntryDefinition(
        entry_id=entry_id,
        type=type_key,
        name=f"{label}{'支柱' if tier == 'pillar' else '辅助'}词条",
        tier=tier,
        taxonomy_ref=type_key,
        grade_ladder_ref=ladder_key,
        current_grade=current_grade,
        visibility="planned",
        origin="fallback_registry",
        capabilities=(f"承担 {type_key} 的{tier}叙事功能",),
        limits=("必须遵循词条体系代价规则",),
        costs=kernel.cost_model.default_cost_types[:2],
        narrative_roles=(tier,),
        allowed_uses=("伏笔", "状态变化", "兑现"),
        forbidden_uses=("无代价解决主线问题",),
        future_payoff_path=("规划阶段生成,后续章节必须具体化",),
    )


def build_fallback_entry_registry(
    kernel: EntrySystemKernel,
    coverage_matrix: Mapping[str, object] | None = None,
    project_metadata: Mapping[str, object] | None = None,
) -> EntryRegistry:
    """Generate a compact deterministic registry for prompt and gate use."""

    matrix = dict(coverage_matrix or build_entry_coverage_matrix(kernel))
    pillar_limit = int(matrix.get("pillar_entries") or 3)
    entries: list[EntryDefinition] = []
    for index, taxonomy in enumerate(kernel.taxonomy):
        if index < pillar_limit:
            entries.append(
                _fallback_entry_for_type(
                    kernel,
                    type_key=taxonomy.type,
                    label=taxonomy.label,
                    tier="pillar",
                    suffix="pillar",
                )
            )
        entries.append(
            _fallback_entry_for_type(
                kernel,
                type_key=taxonomy.type,
                label=taxonomy.label,
                tier="supporting",
                suffix="support",
            )
        )
    if project_metadata:
        entries.extend(entries_from_progression_metadata(project_metadata))
    return merge_entry_registries(EntryRegistry(entries=tuple(entries), coverage_matrix=matrix))


def _power_system_from_metadata(project_metadata: Mapping[str, object]) -> dict[str, object]:
    metadata = _as_mapping(project_metadata)
    for container in (
        metadata,
        _as_mapping(metadata.get("world_spec")),
        _as_mapping(metadata.get("premium_world_spec")),
        _as_mapping(metadata.get("book_spec")),
    ):
        power_system = _as_mapping(container.get("power_system"))
        if power_system:
            return power_system
    return {}


def _named_entries(
    values: object,
    *,
    type_key: str,
    taxonomy_ref: str,
    tier: str = "supporting",
) -> list[EntryDefinition]:
    entries: list[EntryDefinition] = []
    for index, raw in enumerate(_as_sequence(values), start=1):
        data = _as_mapping(raw)
        name = _text(data.get("name") or raw)
        if not name:
            continue
        entry_id = f"{type_key}-{_slug(name, fallback=str(index))}"
        entries.append(
            EntryDefinition(
                entry_id=entry_id,
                type=type_key,
                name=name,
                tier=tier,
                taxonomy_ref=taxonomy_ref,
                visibility="planned",
                origin="progression_metadata",
                capabilities=_dedupe(
                    [
                        _text(data.get("capability")),
                        _text(data.get("effect")),
                        f"承载 {type_key} 体系功能",
                    ]
                ),
                limits=_dedupe([_text(data.get("limit")), "必须有获取条件和使用代价"]),
                costs=_dedupe([_text(data.get("cost"))]),
                narrative_roles=(type_key,),
            )
        )
    return entries


def entries_from_progression_metadata(
    project_metadata: Mapping[str, object],
) -> tuple[EntryDefinition, ...]:
    """Convert common progression metadata into concrete entry definitions."""

    power_system = _power_system_from_metadata(project_metadata)
    if not power_system:
        return ()
    entries: list[EntryDefinition] = []
    system_name = _text(power_system.get("name") or power_system.get("title") or "核心修行体系")
    entries.append(
        EntryDefinition(
            entry_id=f"cultivation_method-{_slug(system_name, fallback='core')}",
            type="cultivation_method",
            name=system_name,
            tier="pillar",
            taxonomy_ref="cultivation_method",
            visibility="planned",
            origin="progression_metadata",
            capabilities=("定义主角成长路径",),
            limits=("突破必须有瓶颈和代价",),
            narrative_roles=("growth_engine",),
        )
    )
    entries.extend(
        _named_entries(
            power_system.get("techniques") or power_system.get("skills"),
            type_key="technique",
            taxonomy_ref="technique",
        )
    )
    entries.extend(
        _named_entries(
            power_system.get("artifacts") or power_system.get("items"),
            type_key="artifact",
            taxonomy_ref="artifact",
        )
    )
    entries.extend(
        _named_entries(
            power_system.get("resources") or power_system.get("materials"),
            type_key="resource",
            taxonomy_ref="resource",
        )
    )
    entries.extend(
        _named_entries(
            power_system.get("realms") or power_system.get("realm_ladder"),
            type_key="cultivation_method",
            taxonomy_ref="cultivation_method",
        )
    )
    return tuple(entries)


def entries_from_project_materials(
    materials: Sequence[Mapping[str, object]],
    kernel: EntrySystemKernel,
) -> tuple[EntryDefinition, ...]:
    """Convert project material anchors into entries when their type is known."""

    valid_types = kernel.taxonomy_by_type
    entries: list[EntryDefinition] = []
    for index, material in enumerate(materials, start=1):
        content = _as_mapping(material.get("content_json") or material.get("content"))
        type_key = _text(
            material.get("entry_type") or content.get("entry_type") or content.get("type")
        )
        if type_key not in valid_types:
            continue
        name = _text(material.get("name") or content.get("name"))
        if not name:
            continue
        entry_id = _text(material.get("slug")) or f"{type_key}-{_slug(name, fallback=str(index))}"
        entries.append(
            EntryDefinition(
                entry_id=entry_id,
                type=type_key,
                name=name,
                tier=_text(content.get("tier")) or "supporting",
                taxonomy_ref=type_key,
                grade_ladder_ref=_ladder_for_type(kernel, type_key),
                visibility=_text(content.get("visibility")) or "planned",
                origin="project_material",
                capabilities=_dedupe(
                    _as_sequence(content.get("capabilities") or content.get("effects"))
                ),
                limits=_dedupe(_as_sequence(content.get("limits"))),
                costs=_dedupe(_as_sequence(content.get("costs"))),
                narrative_roles=_dedupe(_as_sequence(content.get("narrative_roles"))),
                source_blueprint_ids=_dedupe(_as_sequence(content.get("source_blueprint_ids"))),
            )
        )
    return tuple(entries)


def merge_entry_registries(*registries: EntryRegistry) -> EntryRegistry:
    entries_by_id: dict[str, EntryDefinition] = {}
    coverage_matrix: dict[str, object] = {}
    for registry in registries:
        coverage_matrix.update(registry.coverage_matrix)
        for entry in registry.entries:
            entries_by_id.setdefault(entry.entry_id, entry)
    return EntryRegistry(
        entries=tuple(entries_by_id.values()),
        coverage_matrix=coverage_matrix,
    )


def _state_mapping(current_state: object) -> dict[str, dict[str, object]]:
    if isinstance(current_state, EntryStateSnapshot):
        return current_state.entry_states
    data = _as_mapping(current_state)
    states = _as_mapping(data.get("entry_states"))
    return {key: _as_mapping(value) for key, value in states.items()}


def _entry_is_active(entry: EntryDefinition, state: Mapping[str, object] | None) -> bool:
    if not state:
        return True
    return _text(state.get("state") or state.get("current_state")).lower() not in _INACTIVE_STATES


def render_entry_registry_prompt_block(
    registry: EntryRegistry | Mapping[str, object] | None,
    current_state: object = None,
    *,
    max_entries: int = 12,
) -> str:
    """Render active registry entries for planner and writer prompts."""

    if registry is None:
        return ""
    if isinstance(registry, Mapping):
        registry = EntryRegistry.model_validate(registry)
    states = _state_mapping(current_state)
    lines = ["【词条注册表】"]
    rendered = 0
    for entry in registry.entries:
        state = states.get(entry.entry_id)
        if not _entry_is_active(entry, state):
            continue
        state_label = _text((state or {}).get("state")) or entry.visibility
        grade = _text((state or {}).get("current_grade")) or _text(entry.current_grade)
        parts = [
            f"{entry.entry_id}/{entry.name}",
            f"type={entry.type}",
            f"tier={entry.tier}",
            f"state={state_label}",
        ]
        if grade:
            parts.append(f"grade={grade}")
        if entry.capabilities:
            parts.append("cap=" + "、".join(entry.capabilities[:2]))
        if entry.limits:
            parts.append("limit=" + "、".join(entry.limits[:2]))
        lines.append("  - " + " | ".join(parts))
        rendered += 1
        if rendered >= max_entries:
            break
    return "\n".join(lines) if rendered else ""


def entry_registry_from_dict(data: dict[str, object]) -> EntryRegistry:
    return EntryRegistry.model_validate(data)


def entry_registry_to_dict(registry: EntryRegistry) -> dict[str, object]:
    return registry.model_dump(mode="json")


__all__ = [
    "build_entry_coverage_matrix",
    "build_fallback_entry_registry",
    "entries_from_progression_metadata",
    "entries_from_project_materials",
    "entry_registry_from_dict",
    "entry_registry_to_dict",
    "merge_entry_registries",
    "render_entry_registry_prompt_block",
]
