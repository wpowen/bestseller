# ruff: noqa: RUF001
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from bestseller.domain.anti_commonsense_hook import HookSpec


def coerce_hook_spec(value: object) -> HookSpec | None:
    if isinstance(value, HookSpec):
        return value
    if isinstance(value, Mapping):
        try:
            return HookSpec.model_validate(dict(value))
        except Exception:
            return None
    return None


def hook_spec_from_metadata(metadata: Mapping[str, Any] | None) -> HookSpec | None:
    if not isinstance(metadata, Mapping):
        return None
    return coerce_hook_spec(metadata.get("hook_spec"))


def stash_hook_spec_on_project(
    project: object,
    spec: HookSpec,
    *,
    score_payload: Mapping[str, Any] | None = None,
) -> None:
    metadata = getattr(project, "metadata_json", None)
    if not isinstance(metadata, dict):
        metadata = {}
    metadata = dict(metadata)
    metadata["hook_spec"] = spec.model_dump(mode="json")
    if score_payload is not None:
        metadata["hook_strength_gate"] = dict(score_payload)
    project.metadata_json = metadata  # type: ignore[attr-defined]


def render_hook_spec_prompt_block(
    spec: HookSpec | None,
    *,
    language: str = "zh-CN",
) -> str:
    if spec is None:
        return ""
    if language.startswith("en"):
        lines = [
            "[Anti-Commonsense HookSpec — hard premise contract]",
            f"one_liner: {spec.one_liner}",
            f"core_rule: {spec.core_rule}",
            f"base_desire -> reversal: {spec.base_desire} -> {spec.reversal}",
            "constraints:",
            *[f"- {key}: {value}" for key, value in spec.constraints.items()],
            "anti_cheat:",
            *[f"- {item}" for item in spec.anti_cheat],
            "costs:",
            *[f"- {item}" for item in spec.costs],
            f"misunderstanding: {spec.misunderstanding or 'none'}",
            "arc_engine:",
            *[f"- {item}" for item in spec.arc_engine],
            "Propagate this contract into BookSpec.logline/series_engine, "
            "WorldSpec.rules/power_system.hard_limits, volume escalation, and "
            "chapter methodology_contract conflict_stakes/conflict_buffs.",
        ]
    else:
        lines = [
            "【反常识 HookSpec — 命题硬合同】",
            f"一句话钩子：{spec.one_liner}",
            f"核心规则：{spec.core_rule}",
            f"正常欲望→反转：{spec.base_desire} → {spec.reversal}",
            "限制：",
            *[f"- {key}: {value}" for key, value in spec.constraints.items()],
            "反作弊：",
            *[f"- {item}" for item in spec.anti_cheat],
            "代价：",
            *[f"- {item}" for item in spec.costs],
            f"误解机制：{spec.misunderstanding or '无'}",
            "连载升级轴：",
            *[f"- {item}" for item in spec.arc_engine],
            "必须向下传播到 BookSpec.logline/series_engine、"
            "WorldSpec.rules/power_system.hard_limits、卷升级、章节 "
            "methodology_contract 的 conflict_stakes/conflict_buffs。",
        ]
    return "\n".join(lines).strip()


def apply_hook_to_book_spec(book_spec: dict[str, Any], spec: HookSpec | None) -> dict[str, Any]:
    if spec is None:
        return book_spec
    result = dict(book_spec)
    result["logline"] = spec.one_liner
    result["unique_hook"] = spec.one_liner
    result["anti_commonsense_hook"] = spec.model_dump(mode="json")
    series_engine = result.get("series_engine")
    if not isinstance(series_engine, dict):
        series_engine = {}
    series_engine = dict(series_engine)
    series_engine.setdefault("core_serial_engine", spec.core_rule)
    series_engine["reader_promise"] = spec.one_liner
    series_engine["first_three_chapter_hook"] = spec.core_rule
    series_engine["chapter_ending_hook_strategy"] = (
        "Every successful use of the core rule creates a visible cost, "
        "misunderstanding, or anti-cheat pressure."
    )
    series_engine["anti_commonsense_constraints"] = dict(spec.constraints)
    series_engine["anti_cheat_rules"] = list(spec.anti_cheat)
    series_engine["cost_engine"] = list(spec.costs)
    series_engine["misunderstanding_engine"] = spec.misunderstanding
    result["series_engine"] = series_engine
    return result


def apply_hook_to_world_spec(world_spec: dict[str, Any], spec: HookSpec | None) -> dict[str, Any]:
    if spec is None:
        return world_spec
    result = dict(world_spec)
    rules = result.get("rules")
    if not isinstance(rules, list):
        rules = []
    rules = list(rules)
    rule_id = f"hook_{spec.mechanism_key}"[:32]
    rule_payload = {
        "rule_id": rule_id,
        "name": "反常识钩子核心规则",
        "description": spec.core_rule,
        "story_consequence": "；".join(spec.constraints.values()) or spec.reversal,
        "exploitation_potential": "不可利用方式：" + "；".join(spec.anti_cheat),
    }
    if not any(isinstance(item, dict) and item.get("rule_id") == rule_id for item in rules):
        rules.append(rule_payload)
    result["rules"] = rules
    power_system = result.get("power_system")
    if not isinstance(power_system, dict):
        power_system = {}
    power_system = dict(power_system)
    hard_limits = str(power_system.get("hard_limits") or "").strip()
    hook_limits = "；".join([*spec.constraints.values(), *spec.anti_cheat])
    power_system["hard_limits"] = f"{hard_limits}；{hook_limits}".strip("；")
    result["power_system"] = power_system
    result["anti_commonsense_hook"] = spec.model_dump(mode="json")
    return result


def apply_hook_to_volume_plan(
    volume_plan: list[dict[str, Any]] | dict[str, Any],
    spec: HookSpec | None,
) -> list[dict[str, Any]] | dict[str, Any]:
    if spec is None:
        return volume_plan
    if isinstance(volume_plan, dict):
        volumes = volume_plan.get("volumes")
        if isinstance(volumes, list):
            updated = dict(volume_plan)
            updated["volumes"] = apply_hook_to_volume_plan(volumes, spec)
            return updated
        return volume_plan
    updated_plan: list[dict[str, Any]] = []
    axes = list(spec.arc_engine)
    costs = "；".join(spec.costs)
    for idx, raw in enumerate(volume_plan):
        if not isinstance(raw, dict):
            updated_plan.append(raw)
            continue
        item = dict(raw)
        item["hook_arc_engine"] = axes
        if axes:
            item.setdefault("anti_commonsense_escalation_axis", axes[idx % len(axes)])
        resolution = item.get("volume_resolution")
        if isinstance(resolution, dict):
            resolution = dict(resolution)
            existing_cost = str(resolution.get("cost_paid") or "").strip()
            resolution["cost_paid"] = (
                f"{existing_cost}；{costs}".strip("；") if costs else existing_cost
            )
            item["volume_resolution"] = resolution
        updated_plan.append(item)
    return updated_plan


def hook_outline_extra_constraints(spec: HookSpec | None, *, language: str = "zh-CN") -> list[str]:
    if spec is None:
        return []
    if language.startswith("en"):
        constraints = [
            f"Core anti-commonsense one-liner: {spec.one_liner}",
            "Every chapter methodology_contract.conflict_stakes must echo one cost: "
            f"{'; '.join(spec.costs)}",
            "Every chapter methodology_contract.conflict_buffs must include one hook "
            "constraint or anti-cheat pressure: "
            f"{'; '.join([*spec.constraints.values(), *spec.anti_cheat])}",
        ]
        if spec.misunderstanding:
            constraints.append(
                "At least one scene per early chapter should carry "
                "dramatic_irony_intent/reveal_mode from: "
                f"{spec.misunderstanding}"
            )
    else:
        constraints = [
            f"反常识一句话钩子：{spec.one_liner}",
            "每章 methodology_contract.conflict_stakes 必须呼应一个代价："
            f"{'；'.join(spec.costs)}",
            "每章 methodology_contract.conflict_buffs 必须包含一个限制或反作弊压力："
            f"{'；'.join([*spec.constraints.values(), *spec.anti_cheat])}",
        ]
        if spec.misunderstanding:
            constraints.append(
                "黄金三章至少每章一场体现 dramatic_irony_intent/reveal_mode："
                f"{spec.misunderstanding}"
            )
    return [item for item in constraints if item.strip()]


__all__ = [
    "apply_hook_to_book_spec",
    "apply_hook_to_volume_plan",
    "apply_hook_to_world_spec",
    "coerce_hook_spec",
    "hook_outline_extra_constraints",
    "hook_spec_from_metadata",
    "render_hook_spec_prompt_block",
    "stash_hook_spec_on_project",
]
