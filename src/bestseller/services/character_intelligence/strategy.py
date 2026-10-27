# ruff: noqa: RUF001
"""Distillation-backed character strategy synthesis.

The distilled strategy card is intentionally project-level.  This module turns
that project signal into a smaller character-facing contract that can be fused
into CastSpec-derived character profiles without requiring prompt code to know
about raw aggregate rows, grammar patches, or compiler internals.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

_AXIS_ORDER: tuple[str, ...] = (
    "agency",
    "identity_pressure",
    "relationship_debt",
    "antagonist_misread",
    "dialogue_function",
)

_POLICY_KEYS: tuple[str, ...] = (
    "agency_policy",
    "identity_pressure",
    "relationship_policy",
    "antagonist_policy",
    "dialogue_policy",
)

_CHARACTER_MATERIAL_DIMENSIONS = {
    "character_archetypes",
    "character_templates",
    "emotion_arcs",
    "dialogue_styles",
}


def _as_mapping(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _as_list(value: object) -> list[object]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    return [value]


def _text(value: object) -> str:
    return str(value).strip() if value is not None else ""


def _contains_any(value: object, needles: Sequence[str]) -> bool:
    haystack = _text(value).lower()
    return any(needle in haystack for needle in needles)


def _dedupe(values: Sequence[object], *, limit: int | None = None) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = _text(value)
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
        if limit is not None and len(out) >= limit:
            break
    return out


def _string_items(value: object, *, limit: int | None = None) -> list[str]:
    items: list[str] = []
    for raw in _as_list(value):
        if isinstance(raw, Mapping):
            for key in ("var_id", "id", "name", "title", "summary", "text", "description"):
                text = _text(raw.get(key))
                if text:
                    items.append(text)
                    break
        else:
            items.append(_text(raw))
    return _dedupe(items, limit=limit)


def _state_id(value: object) -> str:
    if isinstance(value, Mapping):
        for key in ("var_id", "state_variable", "id", "name"):
            text = _text(value.get(key))
            if text:
                return text
    return _text(value)


def _material_slug(row: Mapping[str, Any]) -> str:
    return _text(row.get("slug") or row.get("mechanism_id") or row.get("name"))


def _material_summary(row: Mapping[str, Any], *, limit: int = 140) -> str:
    text = _text(row.get("narrative_summary") or row.get("summary") or row.get("name"))
    return text[:limit].rstrip() + "..." if len(text) > limit else text


def _selected_mechanisms(card: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [_as_mapping(item) for item in _as_list(card.get("selected_mechanisms"))]


def _collect_state_variables(
    *,
    grammar: Mapping[str, Any],
    material_entries: Sequence[Mapping[str, Any]],
    card: Mapping[str, Any],
) -> list[str]:
    values: list[object] = []
    values.extend(_state_id(item) for item in _as_list(grammar.get("state_variables")))
    values.extend(_state_id(item) for item in _as_list(card.get("required_state_variables")))
    for row in material_entries:
        content = _as_mapping(row.get("content_json"))
        values.extend(_state_id(item) for item in _as_list(content.get("state_variables")))
    return _dedupe(values, limit=24)


def _character_design_paths(
    *,
    material_entries: Sequence[Mapping[str, Any]],
    card: Mapping[str, Any],
) -> list[str]:
    paths: list[object] = [*_string_items(card.get("character_design_paths"), limit=8)]
    for row in material_entries:
        dimension = _text(row.get("dimension"))
        if dimension in _CHARACTER_MATERIAL_DIMENSIONS:
            paths.append(_material_summary(row))
        elif _contains_any(_material_slug(row), ("identity", "relationship", "misread")):
            paths.append(_material_summary(row))
    return _dedupe(paths, limit=10)


def _risk_controls(
    *,
    grammar: Mapping[str, Any],
    material_entries: Sequence[Mapping[str, Any]],
    card: Mapping[str, Any],
) -> list[str]:
    controls: list[object] = []
    controls.extend(_string_items(grammar.get("forbidden_defaults"), limit=10))
    controls.extend(_string_items(card.get("anti_copy_boundaries"), limit=8))
    for row in material_entries:
        dimension = _text(row.get("dimension"))
        if dimension != "anti_cliche_patterns":
            content = _as_mapping(row.get("content_json"))
            guardrail = _text(content.get("guardrail") or content.get("required_cost"))
            if guardrail:
                controls.append(guardrail)
            continue
        controls.append(_material_summary(row))
        content = _as_mapping(row.get("content_json"))
        controls.extend(_string_items(content.get("forbidden_patterns"), limit=4))
        controls.extend(_string_items(content.get("replacement_policy"), limit=4))
    return _dedupe(controls, limit=16)


def _axis_for_signal(signal: str) -> str | None:
    value = signal.lower()
    if any(token in value for token in ("dialogue", "exposition", "voice", "speech")):
        return "dialogue_function"
    if any(
        token in value
        for token in (
            "identity",
            "predecessor",
            "host",
            "autonomy",
            "self-determination",
            "core_lie",
            "lie_truth",
        )
    ):
        return "identity_pressure"
    if any(
        token in value for token in ("relationship", "group", "commitment", "trust", "bond", "ally")
    ):
        return "relationship_debt"
    if any(
        token in value
        for token in (
            "antagonist",
            "villain",
            "misread",
            "reckoning",
            "authority",
            "faction",
            "enemy",
        )
    ):
        return "antagonist_misread"
    if any(
        token in value
        for token in (
            "agency",
            "active",
            "knowledge",
            "asymmetry",
            "choice",
            "crisis",
            "cross_system",
            "problem",
        )
    ):
        return "agency"
    return None


def _required_axes(
    *,
    state_variables: Sequence[str],
    contracts: Sequence[str],
    character_paths: Sequence[str],
    selected: Sequence[Mapping[str, Any]],
) -> list[str]:
    axes: list[str] = []
    signals: list[str] = [*state_variables, *contracts, *character_paths]
    signals.extend(_text(row.get("mechanism_id")) for row in selected)
    signals.extend(_text(row.get("adaptation_instruction")) for row in selected)
    for signal in signals:
        axis = _axis_for_signal(signal)
        if axis:
            axes.append(axis)
    if not axes:
        axes.append("agency")
    return [axis for axis in _AXIS_ORDER if axis in set(axes)]


def _agency_policy(
    *,
    state_variables: Sequence[str],
    contracts: Sequence[str],
    material_entries: Sequence[Mapping[str, Any]],
    risk_controls: Sequence[str],
) -> dict[str, Any]:
    modes: list[object] = ["active_choice_under_pressure"]
    if any(_contains_any(item, ("knowledge", "asymmetry")) for item in state_variables):
        modes.append("knowledge_application")
    if any(
        _contains_any(item, ("cross_system", "rule-arbitrage", "规则套利"))
        for item in state_variables
    ):
        modes.append("cross_system_rule_arbitrage")
    for row in material_entries:
        slug = _material_slug(row)
        if _contains_any(slug, ("rule-arbitrage", "arbitrage")):
            modes.append("cross_system_rule_arbitrage")
        if _contains_any(slug, ("misread", "outsider")):
            modes.append("exploit_local_misread")

    must_act = 0
    if any(_contains_any(item, ("first three", "first 3", "前三")) for item in contracts):
        must_act = 3

    passive_controls = [
        item
        for item in risk_controls
        if _contains_any(item, ("passive", "被动", "receiving", "reception"))
    ]
    return {
        "must_act_within_chapters": must_act or None,
        "default_problem_solving_modes": _dedupe(modes, limit=5),
        "choice_with_cost_required": True,
        "forbidden_passive_modes": passive_controls[:4],
        "evidence_axes": [
            item
            for item in state_variables
            if _axis_for_signal(item) in {"agency", "antagonist_misread"}
        ][:6],
    }


def _identity_pressure_policy(
    *,
    state_variables: Sequence[str],
    contracts: Sequence[str],
    character_paths: Sequence[str],
    material_entries: Sequence[Mapping[str, Any]],
    risk_controls: Sequence[str],
) -> dict[str, Any]:
    debt_sources: list[object] = []
    for row in material_entries:
        slug = _material_slug(row)
        if _contains_any(slug, ("identity", "host", "predecessor")):
            debt_sources.append(_material_summary(row))
    debt_sources.extend(
        path
        for path in character_paths
        if _contains_any(path, ("identity", "身份", "predecessor", "host"))
    )

    return {
        "required_external_pressure": any(
            _contains_any(item, ("external pressure", "外部压力", "forcing visible choice"))
            for item in [*contracts, *risk_controls]
        ),
        "choice_axis": (
            "predecessor_loyalty vs self_determination"
            if any(
                _contains_any(item, ("identity", "predecessor", "host")) for item in state_variables
            )
            else "old_self_protection vs chosen_identity"
        ),
        "debt_sources": _dedupe(debt_sources, limit=5),
        "forbidden_resolution_modes": [
            item
            for item in risk_controls
            if _contains_any(item, ("identity", "internal reflection", "内心反省"))
        ][:4],
        "track_axes": [
            item for item in state_variables if _axis_for_signal(item) == "identity_pressure"
        ][:5],
    }


def _relationship_policy(
    *,
    state_variables: Sequence[str],
    contracts: Sequence[str],
    character_paths: Sequence[str],
) -> dict[str, Any]:
    return {
        "reciprocal_commitment_required": any(
            _contains_any(item, ("reciprocal commitment", "承诺", "group formation"))
            for item in contracts
        ),
        "cost_or_promise_required": True,
        "track_axes": [
            item
            for item in state_variables
            if _axis_for_signal(item) == "relationship_debt"
            or _contains_any(item, ("faction_standing",))
        ][:6],
        "usable_design_paths": [
            item
            for item in character_paths
            if _contains_any(item, ("relationship", "group", "bond", "ally", "承诺"))
        ][:5],
    }


def _antagonist_policy(
    *,
    state_variables: Sequence[str],
    contracts: Sequence[str],
    material_entries: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    misread_sources: list[object] = []
    for row in material_entries:
        slug = _material_slug(row)
        if _contains_any(slug, ("misread", "antagonist", "authority", "reckoning")):
            misread_sources.append(_material_summary(row))

    return {
        "visible_reaction_required": any(
            _contains_any(item, ("visible antagonist reaction", "antagonist reaction", "反应"))
            for item in contracts
        ),
        "on_screen_consequence_required": any(
            _contains_any(item, ("on-screen", "on screen", "在场", "屏幕")) for item in contracts
        ),
        "misread_payoff_required": bool(misread_sources)
        or any(_contains_any(item, ("misread", "authority")) for item in state_variables),
        "resolution_differentiation_required": any(
            _contains_any(item, ("distinct", "different characters", "villain-specific"))
            for item in contracts
        ),
        "misread_sources": _dedupe(misread_sources, limit=5),
        "track_axes": [
            item for item in state_variables if _axis_for_signal(item) == "antagonist_misread"
        ][:6],
    }


def _dialogue_policy(
    *,
    contracts: Sequence[str],
    card: Mapping[str, Any],
    author_craft_entries: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    craft_controls = _string_items(card.get("craft_controls"), limit=8)
    for row in author_craft_entries:
        craft_controls.extend(_string_items(row.get("dialogue_system"), limit=4))
        craft_controls.extend(_string_items(row.get("exposition_strategy"), limit=3))
    craft_controls = _dedupe(craft_controls, limit=10)
    control_blob = "\n".join(craft_controls)
    return {
        "exposition_through_conflict": any(
            _contains_any(item, ("active scene application", "obstacle confrontation"))
            for item in contracts
        ),
        "reader_surrogate_questions_allowed": _contains_any(
            control_blob,
            ("reader_surrogate", "reader surrogate"),
        ),
        "antagonist_exposition_has_higher_stakes": _contains_any(
            control_blob,
            ("antagonist_exposition", "antagonist exposition"),
        ),
        "max_revelations_before_break": (
            3 if _contains_any(control_blob, ("three revelations", "3")) else None
        ),
        "craft_controls": craft_controls[:6],
    }


def _evidence(
    *,
    grammar: Mapping[str, Any],
    material_entries: Sequence[Mapping[str, Any]],
    card: Mapping[str, Any],
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    aggregate_key = _text(card.get("aggregate_key") or grammar.get("key"))
    if aggregate_key:
        out.append({"type": "aggregate", "id": aggregate_key})
    for row in material_entries[:8]:
        summary = _material_summary(row, limit=110)
        if not summary:
            continue
        out.append(
            {
                "type": _text(row.get("dimension")) or "material",
                "id": _material_slug(row),
                "summary": summary,
            }
        )
    for mechanism in _selected_mechanisms(card):
        if _text(mechanism.get("design_role")) != "character_pressure":
            continue
        out.append(
            {
                "type": "selected_mechanism",
                "id": _text(mechanism.get("mechanism_id")),
                "summary": _text(mechanism.get("adaptation_instruction")),
            }
        )
    return out[:12]


def normalize_character_strategy(strategy: Mapping[str, Any] | None) -> dict[str, Any]:
    """Return a stable character strategy payload or ``{}`` for empty input."""

    data = _as_mapping(strategy)
    if not data:
        return {}
    normalized = dict(data)
    normalized.setdefault("version", 1)
    normalized.setdefault("source", "distillation_character_intelligence")
    normalized["required_axes"] = _dedupe(
        _string_items(normalized.get("required_axes")), limit=len(_AXIS_ORDER)
    )
    for key in _POLICY_KEYS:
        normalized[key] = _as_mapping(normalized.get(key))
    normalized["reader_reward_contracts"] = _dedupe(
        _string_items(normalized.get("reader_reward_contracts")), limit=10
    )
    normalized["risk_controls"] = _dedupe(_string_items(normalized.get("risk_controls")), limit=16)
    normalized["evidence"] = [
        _as_mapping(item) for item in _as_list(normalized.get("evidence")) if _as_mapping(item)
    ][:12]
    return normalized


def build_character_strategy_from_distillation(
    *,
    grammar: Mapping[str, Any] | None = None,
    material_entries: Sequence[Mapping[str, Any]] = (),
    distilled_strategy_card: Mapping[str, Any] | None = None,
    author_craft_entries: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Build a project-level character strategy from distilled assets.

    The output is deliberately compact: it contains only the character-facing
    obligations that should affect CastSpec fusion, scene prompts, and chapter
    audits.
    """

    grammar_map = _as_mapping(grammar)
    card = _as_mapping(distilled_strategy_card)
    materials = [_as_mapping(item) for item in material_entries if isinstance(item, Mapping)]
    author_craft = [_as_mapping(item) for item in author_craft_entries if isinstance(item, Mapping)]

    state_variables = _collect_state_variables(
        grammar=grammar_map,
        material_entries=materials,
        card=card,
    )
    contracts = _dedupe(
        [
            *_string_items(grammar_map.get("required_contracts"), limit=16),
            *_string_items(card.get("plan_consumption_checks"), limit=8),
        ],
        limit=18,
    )
    character_paths = _character_design_paths(material_entries=materials, card=card)
    selected = _selected_mechanisms(card)
    risk_controls = _risk_controls(grammar=grammar_map, material_entries=materials, card=card)
    reader_rewards = _dedupe(
        [
            *_string_items(grammar_map.get("reader_rewards"), limit=10),
            *_string_items(card.get("reader_reward_mix"), limit=10),
        ],
        limit=12,
    )

    payload = {
        "version": 1,
        "source": "distillation_character_intelligence",
        "required_axes": _required_axes(
            state_variables=state_variables,
            contracts=contracts,
            character_paths=character_paths,
            selected=selected,
        ),
        "state_variables": state_variables,
        "required_contracts": contracts,
        "character_design_paths": character_paths,
        "reader_reward_contracts": reader_rewards,
        "agency_policy": _agency_policy(
            state_variables=state_variables,
            contracts=contracts,
            material_entries=materials,
            risk_controls=risk_controls,
        ),
        "identity_pressure": _identity_pressure_policy(
            state_variables=state_variables,
            contracts=contracts,
            character_paths=character_paths,
            material_entries=materials,
            risk_controls=risk_controls,
        ),
        "relationship_policy": _relationship_policy(
            state_variables=state_variables,
            contracts=contracts,
            character_paths=character_paths,
        ),
        "antagonist_policy": _antagonist_policy(
            state_variables=state_variables,
            contracts=contracts,
            material_entries=materials,
        ),
        "dialogue_policy": _dialogue_policy(
            contracts=contracts,
            card=card,
            author_craft_entries=author_craft,
        ),
        "risk_controls": risk_controls,
        "evidence": _evidence(grammar=grammar_map, material_entries=materials, card=card),
    }
    return normalize_character_strategy(payload)


class ReferenceProjectionContext:
    """A metadata view retaining the complete real project context for receipts.

    Assigning projections to this view never mutates an ORM row. It also avoids
    reconstructing a partial project from metadata and losing fingerprint inputs.
    """

    def __init__(self, project: object, metadata: Mapping[str, Any]) -> None:
        self._project = project
        self.metadata_json = dict(metadata)

    def __getattr__(self, key: str) -> object:
        return getattr(self._project, key)


def reference_projection_value(
    metadata: Mapping[str, Any] | None, key: str, *, project: object | None = None,
) -> object | None:
    """Only code-side current receipts authorize stored reference projections.

    A metadata-only entry point cannot prove the full input fingerprint. Neither
    a field's presence nor an embedded source/explicit claim supplies authority.
    """
    if project is None or not getattr(project, "id", None):
        return None
    from bestseller.services.prompt_source_policy import valid_reference_value

    context = ReferenceProjectionContext(project, _as_mapping(metadata))
    return valid_reference_value(context, key)


def adopt_reference_projection(
    project: object, key: str, *, adopted_by: str, required: bool = False,
) -> dict[str, Any]:
    """Record an explicit author action; generators must never call this.

    A registered reference receipt proves current origin, not author adoption.
    Only this separate decision can make a reference a required content contract.
    """
    if key not in {"distilled_strategy_card", "character_strategy"} or not adopted_by.strip():
        raise ValueError("a supported projection and adopting author are required")
    value = reference_projection_value(project.metadata_json, key, project=project)
    if value is None:
        raise ValueError("cannot adopt an unavailable reference projection")
    from bestseller.services.prompt_source_policy import content_hash

    record = {"policy": "reference-adoption.v1", "project_id": str(project.id),
              "content_hash": content_hash(value), "adopted_by": adopted_by.strip(),
              "required": bool(required)}
    metadata = dict(project.metadata_json)
    adoptions = dict(metadata.get("reference_projection_adoptions") or {})
    adoptions[key] = record
    metadata["reference_projection_adoptions"] = adoptions
    project.metadata_json = metadata
    return record


def _reference_adoption_record(metadata: Mapping[str, Any], key: str) -> dict[str, Any]:
    record = _as_mapping(_as_mapping(metadata.get("reference_projection_adoptions")).get(key))
    if (record.get("policy") != "reference-adoption.v1"
        or not isinstance(record.get("adopted_by"), str)
        or not record["adopted_by"].strip()
        or not isinstance(record.get("project_id"), str)
        or not record["project_id"]
        or not isinstance(record.get("content_hash"), str)
        or len(record["content_hash"]) != 64):
        return {}
    return record


def reference_projection_adopted(project: object | None, key: str) -> bool:
    """A previous author decision never authorizes a different new projection."""
    if project is None or not getattr(project, "id", None):
        return False
    metadata = _as_mapping(project.metadata_json)
    record = _reference_adoption_record(metadata, key)
    value = reference_projection_value(metadata, key, project=project)
    from bestseller.services.prompt_source_policy import content_hash
    return bool(record and record.get("project_id") == str(project.id)
                and value is not None and record["content_hash"] == content_hash(value))


def reference_projection_required(project: object | None, key: str) -> bool:
    if project is None or not getattr(project, "id", None):
        return False
    record = _reference_adoption_record(_as_mapping(project.metadata_json), key)
    return bool(record and record.get("project_id") == str(project.id)
                and record.get("required") is True)


class AdoptedReferenceUnavailable(RuntimeError):
    terminal = True
    code = "adopted_reference_unavailable"


class ReferenceContextRequiredError(AdoptedReferenceUnavailable):
    code = "reference_context_required"


def required_reference_projection(
    metadata: Mapping[str, Any] | None, key: str, *, project: object | None = None,
) -> object | None:
    """A missing optional reference is quiet; a missing adopted contract is explicit."""
    if project is None or not getattr(project, "id", None):
        record = _reference_adoption_record(_as_mapping(metadata), key)
        if record.get("required") is True:
            raise ReferenceContextRequiredError(
                f"Project context is required to verify recorded {key} adoption; "
                "the record does not itself authorize reference consumption."
            )
        return None
    if not reference_projection_required(project, key):
        return None
    value = reference_projection_value(metadata, key, project=project)
    from bestseller.services.prompt_source_policy import content_hash
    record = project.metadata_json["reference_projection_adoptions"][key]
    if value is None or record["content_hash"] != content_hash(value):
        raise AdoptedReferenceUnavailable(
            f"Author-adopted {key} is unavailable or changed; "
            "restore its source or explicitly revise adoption."
        )
    return value


def character_strategy_from_project_metadata(
    metadata: Mapping[str, Any] | None, *, project: object | None = None,
) -> dict[str, Any]:
    """Resolve a verified stored choice, then derive from a verified source card."""

    for key in ("character_strategy", "distilled_strategy_card"):
        required_reference_projection(metadata, key, project=project)
    explicit = normalize_character_strategy(_as_mapping(
        reference_projection_value(metadata, "character_strategy", project=project)
    ))
    if explicit:
        return explicit
    card = _as_mapping(
        reference_projection_value(metadata, "distilled_strategy_card", project=project)
    )
    if card:
        return build_character_strategy_from_distillation(distilled_strategy_card=card)
    return {}


def adopted_character_strategy(project: object) -> dict[str, Any]:
    """Resolve only the author's current choice for behavior-profile generation."""
    metadata = project.metadata_json
    available = character_strategy_from_project_metadata(metadata, project=project)
    if reference_projection_adopted(project, "character_strategy"):
        return available
    if reference_projection_adopted(project, "distilled_strategy_card"):
        return build_character_strategy_from_distillation(
            distilled_strategy_card=reference_projection_value(
                metadata, "distilled_strategy_card", project=project,
            ),
        )
    return {}
