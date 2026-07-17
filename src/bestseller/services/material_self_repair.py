"""Plan framework-level material repairs without mutating files.

The repair planner is intentionally side-effect free. It converts registry and
reference findings into a typed action list that an LLM/material worker can
execute, review, and re-run until the integrity gate passes.
"""
# ruff: noqa: RUF001

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from bestseller.services.material_entity_registry import (
    EntityRegistry,
    EntityStatus,
    EntityType,
    build_entity_registry,
)
from bestseller.services.material_injection_orchestrator import collect_material_blocks
from bestseller.services.material_reference_scanner import (
    ReferenceProblem,
    scan_material_references,
)

MaterialRepairActionType = Literal[
    "replace_deprecated_reference",
    "create_missing_entity_placeholder",
    "merge_duplicate_entity",
    "expand_missing_chapter_material",
    "complete_placeholder_entity",
]


@dataclass(frozen=True)
class MaterialRepairAction:
    action_type: MaterialRepairActionType
    target: str
    reason: str
    source_path: str = ""
    confidence: str = "medium"
    requires_llm: bool = True
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "action_type": self.action_type,
            "target": self.target,
            "reason": self.reason,
            "source_path": self.source_path,
            "confidence": self.confidence,
            "requires_llm": self.requires_llm,
            "payload": dict(self.payload),
        }


@dataclass(frozen=True)
class MaterialSelfRepairPlan:
    project_dir: str
    actions: tuple[MaterialRepairAction, ...]
    blocking: bool
    metrics: dict[str, int]

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_dir": self.project_dir,
            "blocking": self.blocking,
            "metrics": dict(self.metrics),
            "actions": [
                action.to_dict()
                for action in self.actions
            ],
        }


def plan_material_self_repair(
    project_dir: Path,
    *,
    chapter_number: int | None = None,
    chapter_position: str | None = None,
    prompt_pack_key: str | None = None,
) -> MaterialSelfRepairPlan:
    """Return the next repair actions needed to close material integrity."""

    root = project_dir.resolve()
    registry = build_entity_registry(root)
    problems = scan_material_references(root, registry)
    actions: list[MaterialRepairAction] = []

    for problem in problems:
        action = _action_from_problem(problem, registry)
        if action is not None:
            actions.append(action)

    actions.extend(_duplicate_record_actions(registry))
    actions.extend(_placeholder_completion_actions(registry))
    if chapter_number is not None:
        actions.extend(
            _missing_chapter_material_actions(
                root,
                chapter_number=chapter_number,
                chapter_position=chapter_position,
                prompt_pack_key=prompt_pack_key,
            )
        )

    deduped = _dedupe_actions(actions)
    metrics = {
        "problem_count": len(problems),
        "action_count": len(deduped),
        "llm_action_count": sum(1 for action in deduped if action.requires_llm),
        "blocking_action_count": sum(
            1
            for action in deduped
            if action.action_type
            in {
                "replace_deprecated_reference",
                "create_missing_entity_placeholder",
                "expand_missing_chapter_material",
                "complete_placeholder_entity",
            }
        ),
    }
    return MaterialSelfRepairPlan(
        project_dir=root.as_posix(),
        actions=tuple(deduped),
        blocking=metrics["blocking_action_count"] > 0,
        metrics=metrics,
    )


def _action_from_problem(
    problem: ReferenceProblem,
    registry: EntityRegistry,
) -> MaterialRepairAction | None:
    if problem.problem == "deprecated":
        replacement = _candidate_replacement(problem.referenced_name, registry)
        return MaterialRepairAction(
            action_type="replace_deprecated_reference",
            target=problem.referenced_name,
            source_path=f"{problem.file}:{problem.line_no}",
            reason="material references a deprecated or retired entity",
            confidence="high" if replacement else "medium",
            requires_llm=replacement is None,
            payload={
                "replacement": replacement,
                "context": problem.context,
                "policy": "replace if canonical replacement is clear; otherwise ask LLM to update the material note without resurrecting retired canon",
            },
        )
    if problem.problem == "unknown":
        inferred_type = _infer_entity_type(problem.referenced_name, problem.file, problem.context)
        return MaterialRepairAction(
            action_type="create_missing_entity_placeholder",
            target=problem.referenced_name,
            source_path=f"{problem.file}:{problem.line_no}",
            reason="material references an entity/rule/clue that is not registered",
            confidence="medium",
            requires_llm=True,
            payload={
                "entity_type": inferred_type.value,
                "context": problem.context,
                "minimum_fields": _minimum_fields_for_type(inferred_type),
            },
        )
    if problem.problem == "duplicate_canonical":
        return MaterialRepairAction(
            action_type="merge_duplicate_entity",
            target=problem.referenced_name,
            source_path=f"{problem.file}:{problem.line_no}",
            reason="material points at a duplicate canonical entity source",
            confidence="high",
            requires_llm=False,
            payload={"context": problem.context},
        )
    return None


def _duplicate_record_actions(registry: EntityRegistry) -> list[MaterialRepairAction]:
    actions: list[MaterialRepairAction] = []
    for record in registry.records:
        if record.status != EntityStatus.DUPLICATE:
            continue
        actions.append(
            MaterialRepairAction(
                action_type="merge_duplicate_entity",
                target=record.canonical_name,
                source_path=", ".join(record.source_files),
                reason="duplicate material file shadows the canonical entity",
                confidence="high",
                requires_llm=False,
                payload={"source_files": list(record.source_files)},
            )
        )
    return actions


def _placeholder_completion_actions(registry: EntityRegistry) -> list[MaterialRepairAction]:
    actions: list[MaterialRepairAction] = []
    for record in registry.records:
        if record.status != EntityStatus.PLACEHOLDER:
            continue
        actions.append(
            MaterialRepairAction(
                action_type="complete_placeholder_entity",
                target=record.canonical_name,
                source_path=", ".join(record.source_files),
                reason="placeholder material exists but lacks enough usable canon for generation",
                confidence="high",
                requires_llm=True,
                payload={
                    "entity_type": record.type.value,
                    "minimum_fields": _minimum_fields_for_type(record.type),
                },
            )
        )
    return actions


def _missing_chapter_material_actions(
    project_dir: Path,
    *,
    chapter_number: int,
    chapter_position: str | None,
    prompt_pack_key: str | None,
) -> list[MaterialRepairAction]:
    blocks = collect_material_blocks(
        project_dir,
        chapter_number=chapter_number,
        chapter_position=chapter_position,
        prompt_pack_key=prompt_pack_key,
        total_token_budget=4000,
    )
    present = {block.key for block in blocks if block.content.strip()}
    required = {"required_rules", "required_reveals", "required_evidence"}
    missing = tuple(sorted(required - present))
    if not missing:
        return []
    return [
        MaterialRepairAction(
            action_type="expand_missing_chapter_material",
            target=f"chapter:{chapter_number}",
            source_path="story-bible",
            reason="chapter generation has no complete material obligation packet",
            confidence="medium",
            requires_llm=True,
            payload={
                "missing_blocks": list(missing),
                "required_blocks": sorted(required),
                "instruction": (
                    "LLM must expand story-bible material first, then rerun material injection "
                    "and chapter generation. Do not invent chapter prose to cover missing canon."
                ),
            },
        )
    ]


def _candidate_replacement(name: str, registry: EntityRegistry) -> str | None:
    cleaned = str(name).strip()
    if not cleaned:
        return None

    # Priority 1: explicit project-level alias overrides
    # (story-bible/canonical-aliases.yaml). Operators use this to hand-curate
    # deprecated→canonical mappings that the registry cannot infer on its own
    # (e.g., 镜中局 → 镜中局张家开门人 when the successor was renamed and the
    # old name has no canonical record left).
    override = _project_alias_override(cleaned, registry)
    if override is not None:
        return override

    # Priority 2: registry aliases. A deprecated name often appears as an
    # alias of the surviving canonical record (e.g., 林渊 has alias
    # ("林逸",)). The previous implementation only looked at canonical_name
    # and silently missed these matches, forcing them into the LLM queue.
    for record in registry.records:
        if record.status != EntityStatus.ACTIVE:
            continue
        if record.canonical_name == cleaned:
            continue
        if cleaned in record.aliases:
            return record.canonical_name

    # Priority 3: substring fallback against active canonical names.
    active = [
        record.canonical_name
        for record in registry.records
        if record.status == EntityStatus.ACTIVE and record.canonical_name != cleaned
    ]
    if not active:
        return None
    for candidate in active:
        if cleaned in candidate or candidate in cleaned:
            return candidate
    return None


def _project_alias_override(cleaned: str, registry: EntityRegistry) -> str | None:
    """Read story-bible/canonical-aliases.yaml for hand-curated overrides.

    Format::

        mappings:
          - deprecated: 旧机制名
            canonical: 完整机制名
          - deprecated: 旧人名
            canonical: 当前人名

    A mapping is only honored when the ``canonical`` target exists in the
    registry as an ACTIVE record — preventing operators from resurrecting
    retired entities through this file.
    """
    project_dir = _project_dir_for_registry(registry)
    if project_dir is None:
        return None
    path = project_dir / "story-bible" / "canonical-aliases.yaml"
    if not path.exists():
        return None
    try:
        import yaml  # type: ignore[import-not-found]
    except ImportError:
        return None
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return None
    mappings = payload.get("mappings") if isinstance(payload, dict) else None
    if not isinstance(mappings, list):
        return None
    for entry in mappings:
        if not isinstance(entry, dict):
            continue
        deprecated = str(entry.get("deprecated") or "").strip()
        canonical = str(entry.get("canonical") or "").strip()
        if not deprecated or not canonical or deprecated != cleaned:
            continue
        target = registry.by_name.get(canonical)
        if target is None or target.status != EntityStatus.ACTIVE:
            continue
        return canonical
    return None


def _project_dir_for_registry(registry: EntityRegistry) -> Path | None:
    """Best-effort: derive project root from any source file path the registry knows."""
    for record in registry.records:
        for source in record.source_files:
            candidate = Path(source)
            if "story-bible" in candidate.parts:
                idx = candidate.parts.index("story-bible")
                return Path(*candidate.parts[:idx]) if idx > 0 else None
            if "obsidian-vault" in candidate.parts:
                idx = candidate.parts.index("obsidian-vault")
                return Path(*candidate.parts[:idx]) if idx > 0 else None
    return None


def _infer_entity_type(name: str, file: str, context: str) -> EntityType:
    value = f"{name} {file} {context}"
    if _contains_any(value, ("R-", "规则", "rule")):
        return EntityType.RULE
    if _contains_any(value, ("C-", "CLUE", "线索", "证据")):
        return EntityType.CLUE
    if _contains_any(value, ("地点", "楼", "井", "市场", "太平间", "医院")):
        return EntityType.LOCATION
    # 题材中性的实体名词标记(删侦探/账本专属 铜钱/罗盘/账,补通用+修仙类通用物)
    if _contains_any(value, ("物件", "镜", "牌", "器", "符", "印", "剑", "炉", "珠", "令")):
        return EntityType.OBJECT
    return EntityType.CHARACTER


def _minimum_fields_for_type(entity_type: EntityType) -> tuple[str, ...]:
    fields = {
        EntityType.CHARACTER: ("identity", "role", "current_state", "first_allowed_chapter"),
        EntityType.OBJECT: ("physical_anchor", "owner_or_location", "rule_effect", "cost"),
        EntityType.LOCATION: ("spatial_anchor", "access_path", "current_state", "plot_function"),
        EntityType.RULE: ("visible_effect", "solution", "cost", "future_use"),
        EntityType.CLUE: ("surface_form", "where_found", "points_to", "payoff_chapter"),
        EntityType.REVEAL: ("tokens", "earliest_chapter", "setup_chapters", "payoff"),
        EntityType.HOOK: ("visible_hook", "reader_question", "payoff_window"),
        EntityType.FACTION: ("agenda", "resources", "boundary", "conflict_role"),
    }
    return fields.get(entity_type, ("definition", "plot_function"))


def _contains_any(value: str, tokens: tuple[str, ...]) -> bool:
    return any(token in value for token in tokens)


def _dedupe_actions(actions: list[MaterialRepairAction]) -> tuple[MaterialRepairAction, ...]:
    seen: set[tuple[str, str, str]] = set()
    deduped: list[MaterialRepairAction] = []
    for action in actions:
        key = (action.action_type, action.target, action.source_path)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(action)
    return tuple(deduped)


__all__ = [
    "MaterialRepairAction",
    "MaterialRepairActionType",
    "MaterialSelfRepairPlan",
    "plan_material_self_repair",
]
