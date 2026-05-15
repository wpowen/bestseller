# ruff: noqa: RUF001
"""Bridge distilled strategy assets into structured worldview contracts.

This module is intentionally pure: callers pass in a strategy card payload and
aggregate material rows, and the bridge returns JSON-compatible fragments that
can be embedded into ``StoryDesignKernel.worldview_kernel``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


def build_distilled_worldview_bindings(
    strategy_card: Mapping[str, Any] | object | None,
    *,
    aggregate_materials: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build WorldviewKernel extension payload from distilled strategy data."""

    card = _as_mapping_model(strategy_card)
    materials = [_as_mapping(item) for item in aggregate_materials or ()]
    aggregate_key = _text(card.get("aggregate_key")) or "unknown"
    selected = [_as_mapping_model(item) for item in _as_list(card.get("selected_mechanisms"))]
    materials_by_slug = {
        _text(row.get("slug") or row.get("mechanism_id") or row.get("name")): row
        for row in materials
        if _text(row.get("slug") or row.get("mechanism_id") or row.get("name"))
    }

    anti_copy_boundaries = _anti_copy_boundaries(card, materials)
    distilled_mechanism_bindings: list[dict[str, Any]] = []
    state_variables: list[dict[str, Any]] = []
    asset_ledger: list[dict[str, Any]] = []
    authority_claims: list[dict[str, Any]] = []
    scene_templates: list[dict[str, Any]] = []

    state_sources: dict[str, list[str]] = {}
    for state in _string_items(card.get("required_state_variables")):
        state_sources.setdefault(state, [])

    for mechanism in selected:
        mechanism_id = _text(mechanism.get("mechanism_id"))
        if not mechanism_id:
            continue
        material = materials_by_slug.get(mechanism_id, {})
        content = _as_mapping(material.get("content_json"))
        mechanism_states = _dedupe(
            [
                *_string_items(content.get("state_variables")),
                *_string_items(mechanism.get("state_variables")),
            ]
        )
        required_cost = _first_text(
            content.get("required_cost"),
            content.get("guardrail"),
            mechanism.get("required_cost"),
        )
        for state in mechanism_states:
            state_sources.setdefault(state, []).append(mechanism_id)
        distilled_mechanism_bindings.append(
            {
                "aggregate_key": aggregate_key,
                "mechanism_id": mechanism_id,
                "design_role": _text(mechanism.get("design_role")) or "world_pressure",
                "source_confidence": _confidence(mechanism),
                "required_project_binding": _first_text(
                    mechanism.get("required_project_specific_binding"),
                    mechanism.get("required_project_binding"),
                    material.get("narrative_summary"),
                    default="绑定到本项目的世界规则、人物选择、资源代价或兑现窗口。",
                ),
                "state_variables": mechanism_states,
                "required_cost": required_cost,
                "anti_copy_boundaries": anti_copy_boundaries,
            }
        )

    for row in materials:
        content = _as_mapping(row.get("content_json"))
        mechanism_id = _text(row.get("slug") or row.get("mechanism_id") or row.get("name"))
        for state in _string_items(content.get("state_variables")):
            state_sources.setdefault(state, [])
            if mechanism_id:
                state_sources[state].append(mechanism_id)

        if _is_asset_row(row):
            asset_ledger.append(_asset_from_row(row))
        if _is_authority_claim_row(row):
            authority_claims.append(_authority_claim_from_row(row))
        if _text(row.get("dimension")) == "scene_templates":
            scene_templates.append(_scene_template_from_row(row))

    for key in _dedupe(state_sources.keys()):
        state_variables.append(
            {
                "key": key,
                "variable_type": _state_variable_type(key),
                "current_value": "",
                "desired_direction": "track_visible_change",
                "change_triggers": _change_triggers_for_state(key, materials),
                "failure_mode": "该世界状态变量没有在卷纲或章纲中发生可见变化。",
                "source_mechanism_ids": _dedupe(state_sources.get(key, [])),
            }
        )

    return {
        "distilled_mechanism_bindings": distilled_mechanism_bindings,
        "state_variables": state_variables,
        "asset_ledger": _dedupe_rows(asset_ledger, key="key"),
        "authority_claims": _dedupe_rows(authority_claims, key="target"),
        "scene_templates": _dedupe_rows(scene_templates, key="key"),
        "anti_copy_boundaries": anti_copy_boundaries,
    }


def _asset_from_row(row: Mapping[str, Any]) -> dict[str, Any]:
    content = _as_mapping(row.get("content_json"))
    key = _text(row.get("slug") or row.get("name"))
    summary = _text(row.get("narrative_summary") or row.get("name"))
    cost = _first_text(
        content.get("required_cost"),
        content.get("guardrail"),
        default="世界资产的收益必须附带维护成本、暴露风险或敌对关注。",
    )
    return {
        "key": key,
        "asset_type": _first_tag(row) or _text(row.get("dimension")) or "world_asset",
        "value": summary or key,
        "cost": cost,
        "exposure_risk": cost,
        "attention_sources": _attention_sources(row),
        "source_mechanism_ids": [key] if key else [],
    }


def _authority_claim_from_row(row: Mapping[str, Any]) -> dict[str, Any]:
    content = _as_mapping(row.get("content_json"))
    key = _text(row.get("slug") or row.get("name"))
    name = _text(row.get("name")) or key
    summary = _text(row.get("narrative_summary"))
    return {
        "claimant": name,
        "target": key,
        "claim_basis": summary or name,
        "legitimacy": "distilled_world_pressure",
        "conflict_with": _string_items(content.get("conflict_with")),
        "escalation_path": _first_text(
            content.get("chapter_use"),
            content.get("required_cost"),
            content.get("guardrail"),
            default=summary or "让身份、势力或权威声索逐步升级。",
        ),
    }


def _scene_template_from_row(row: Mapping[str, Any]) -> dict[str, Any]:
    content = _as_mapping(row.get("content_json"))
    key = _text(row.get("slug") or row.get("name"))
    required_change = _dedupe(
        [
            *_string_items(content.get("required_change")),
            *_string_items(content.get("scene_outputs")),
        ]
    ) or ["world_state_change"]
    return {
        "key": key,
        "template_name": _text(row.get("name")) or key,
        "use_case": _text(row.get("narrative_summary")) or key,
        "required_change": required_change,
        "source_mechanism_ids": [key] if key else [],
    }


def _anti_copy_boundaries(
    card: Mapping[str, Any],
    materials: Sequence[Mapping[str, Any]],
) -> list[str]:
    values: list[str] = [*_string_items(card.get("anti_copy_boundaries"))]
    for row in materials:
        content = _as_mapping(row.get("content_json"))
        values.extend(_string_items(content.get("blocked_elements")))
        values.extend(_string_items(content.get("anti_copy_boundaries")))
        replacement = _text(content.get("replacement_rule"))
        if replacement:
            values.append(replacement)
    return _dedupe(values)


def _change_triggers_for_state(
    state_key: str,
    materials: Sequence[Mapping[str, Any]],
) -> list[str]:
    triggers: list[str] = []
    for row in materials:
        content = _as_mapping(row.get("content_json"))
        if state_key in _string_items(content.get("state_variables")):
            triggers.append(
                _text(row.get("name"))
                or _text(row.get("slug"))
                or _text(row.get("narrative_summary"))
            )
    return _dedupe([trigger for trigger in triggers if trigger]) or [
        "章节显性改变该世界状态变量"
    ]


def _is_asset_row(row: Mapping[str, Any]) -> bool:
    haystack = _row_haystack(row)
    return any(token in haystack for token in ("asset", "resource", "artifact", "资产", "资源"))


def _is_authority_claim_row(row: Mapping[str, Any]) -> bool:
    haystack = _row_haystack(row)
    return any(
        token in haystack
        for token in (
            "identity",
            "faction",
            "authority",
            "claim",
            "family",
            "pressure",
            "身份",
            "势力",
            "权威",
            "声索",
            "家族",
        )
    )


def _state_variable_type(key: str) -> str:
    normalized = key.lower()
    if any(token in normalized for token in ("count", "attention", "risk", "debt")):
        return "counter"
    if any(token in normalized for token in ("level", "tier", "stage")):
        return "tiered"
    return "state"


def _row_haystack(row: Mapping[str, Any]) -> str:
    return " ".join(
        [
            _text(row.get("dimension")),
            _text(row.get("slug")),
            _text(row.get("name")),
            _text(row.get("narrative_summary")),
            " ".join(_string_items(row.get("tags"))),
        ]
    ).lower()


def _attention_sources(row: Mapping[str, Any]) -> list[str]:
    tags = _string_items(row.get("tags"))
    return [
        tag
        for tag in tags
        if tag in {"escalation", "authority-ladder", "family-pressure", "faction"}
    ][:4]


def _first_tag(row: Mapping[str, Any]) -> str:
    tags = _string_items(row.get("tags"))
    return tags[0] if tags else ""


def _confidence(value: Mapping[str, Any]) -> float:
    try:
        return max(0.0, min(float(value.get("source_confidence") or 0.0), 1.0))
    except (TypeError, ValueError):
        return 0.0


def _as_mapping_model(value: object) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if hasattr(value, "model_dump"):
        dumped = value.model_dump(mode="json")  # type: ignore[attr-defined]
        return dict(dumped) if isinstance(dumped, Mapping) else {}
    return {}


def _as_mapping(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _as_list(value: object) -> list[object]:
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return []


def _string_items(value: object) -> list[str]:
    return [_text(item) for item in _as_list(value) if _text(item)]


def _first_text(*values: object, default: str = "") -> str:
    for value in values:
        text = _text(value)
        if text:
            return text
    return default


def _text(value: object) -> str:
    return str(value).strip() if value is not None else ""


def _dedupe(values: Sequence[object]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = _text(value)
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return result


def _dedupe_rows(rows: Sequence[dict[str, Any]], *, key: str) -> list[dict[str, Any]]:
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for row in rows:
        identity = _text(row.get(key))
        if identity and identity not in seen:
            seen.add(identity)
            result.append(row)
    return result
