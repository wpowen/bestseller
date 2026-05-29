"""Deterministic methodology lineage selector.

This is the planning-time owner of methodology selection.  Draft/review stages
should consume the persisted lineage and must not re-run this selector.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any

from bestseller.services.hook_ledger import is_methodology_v2_enabled
from bestseller.services.methodology_lineage import AppliedMethodology, MethodologyLineage

DEFAULT_BUDGET_CARDS = 6
DEFAULT_BUDGET_TOKENS = 900

_REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_INVENTORY_PATH = _REPO_ROOT / "data" / "methodology_unified" / "inventory.jsonl"

_CRAFT_SLOT_ALIASES: dict[str, str] = {
    "ending_hook_engine": "hook_ledger",
    "emotion_pressure_engine": "pacing_compression_engine",
    "project_health_monitor": "premise_engine",
}

_KNOWN_SLOTS = {
    "premise_engine",
    "character_change_tracker",
    "worldview_theme",
    "scene_causality_engine",
    "hook_ledger",
    "payoff_ledger",
    "pacing_compression_engine",
    "opening_three_function",
    "pov_distance_controller",
    "dialogue_subtext_engine",
    "revision_repair_engine",
}

_ROLE_SLOT_PROFILE: dict[str, dict[str, tuple[str, ...]]] = {
    "opening": {
        "must": ("opening_three_function", "hook_ledger", "scene_causality_engine"),
        "should": ("character_change_tracker", "pov_distance_controller"),
        "nice": ("dialogue_subtext_engine",),
    },
    "setup": {
        "must": ("scene_causality_engine", "hook_ledger"),
        "should": (
            "character_change_tracker",
            "pov_distance_controller",
            "dialogue_subtext_engine",
        ),
        "nice": ("pacing_compression_engine",),
    },
    "escalation": {
        "must": ("scene_causality_engine", "hook_ledger", "pacing_compression_engine"),
        "should": ("character_change_tracker", "pov_distance_controller"),
        "nice": ("dialogue_subtext_engine",),
    },
    "pivot": {
        "must": ("scene_causality_engine", "payoff_ledger", "hook_ledger"),
        "should": ("pov_distance_controller", "dialogue_subtext_engine"),
        "nice": ("character_change_tracker",),
    },
    "payoff": {
        "must": ("payoff_ledger", "hook_ledger", "scene_causality_engine"),
        "should": ("pov_distance_controller", "pacing_compression_engine"),
        "nice": ("dialogue_subtext_engine",),
    },
    "climax": {
        "must": ("payoff_ledger", "scene_causality_engine", "hook_ledger"),
        "should": (
            "pov_distance_controller",
            "dialogue_subtext_engine",
            "pacing_compression_engine",
        ),
        "nice": ("character_change_tracker",),
    },
    "denouement": {
        "must": ("payoff_ledger", "character_change_tracker", "hook_ledger"),
        "should": ("dialogue_subtext_engine", "pov_distance_controller"),
        "nice": ("scene_causality_engine",),
    },
}

_WEAK_INDICATOR_TO_SLOT: dict[str, str] = {
    "scene_causality_score": "scene_causality_engine",
    "hook_ledger_closure_rate": "hook_ledger",
    "ending_hook_score": "hook_ledger",
    "setup_payoff_score": "payoff_ledger",
    "payoff_ledger_closure_rate": "payoff_ledger",
    "pov_stability_score": "pov_distance_controller",
    "pov_distance_drift_ratio": "pov_distance_controller",
    "dialogue_subtext_score": "dialogue_subtext_engine",
    "character_want_need_coverage": "character_change_tracker",
    "character_change_score": "character_change_tracker",
    "compression_ratio_compliance": "pacing_compression_engine",
    "repair_trigger_rate": "revision_repair_engine",
}

_VERIFIABILITY_RANK = {"strict": 3, "heuristic": 2, "advisory": 1}
_COVERAGE_RANK = {"runtime_active": 3, "runtime_dormant": 2, "not_runtime": 1}


@dataclass(frozen=True)
class MethodologyInventoryRule:
    rule_id: str
    source: str
    title: str
    craft_function: str
    slot: str
    binding_stage: tuple[str, ...]
    binding_artifact: tuple[str, ...]
    indicator_targets: tuple[str, ...]
    text_snippet: str
    coverage_status: str
    similarity_cluster_id: str

    @classmethod
    def from_json(cls, payload: Mapping[str, Any]) -> MethodologyInventoryRule | None:
        craft_function = str(payload.get("craft_function") or "").strip()
        slot = _normalize_slot(craft_function, payload)
        if slot not in _KNOWN_SLOTS:
            return None
        rule_id = str(payload.get("rule_id") or "").strip()
        binding_artifact = _strings(payload.get("binding_artifact"))
        if not rule_id or not binding_artifact:
            return None
        return cls(
            rule_id=rule_id,
            source=str(payload.get("source") or "").strip(),
            title=str(payload.get("title") or "").strip(),
            craft_function=craft_function,
            slot=slot,
            binding_stage=_strings(payload.get("binding_stage")),
            binding_artifact=binding_artifact,
            indicator_targets=_strings(payload.get("indicator_targets")),
            text_snippet=str(payload.get("text_snippet") or "").strip(),
            coverage_status=str(payload.get("coverage_status") or "").strip(),
            similarity_cluster_id=str(payload.get("similarity_cluster_id") or "").strip(),
        )


def load_methodology_inventory(
    inventory_path: Path | str = DEFAULT_INVENTORY_PATH,
) -> tuple[MethodologyInventoryRule, ...]:
    path = Path(inventory_path)
    rules: list[MethodologyInventoryRule] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, start=1):
            text = line.strip()
            if not text:
                continue
            try:
                raw = json.loads(text)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid methodology inventory JSONL at line {line_no}.") from exc
            if not isinstance(raw, Mapping):
                continue
            rule = MethodologyInventoryRule.from_json(raw)
            if rule is not None:
                rules.append(rule)
    return tuple(rules)


def derive_chapter_role(
    *,
    chapter_no: int,
    chapter_outline: object | None = None,
    explicit_role: str | None = None,
) -> str:
    role = _normalize_role(explicit_role)
    if role:
        return role

    if chapter_outline is not None:
        for attr in ("chapter_role", "chapter_event_role", "event_cycle_role"):
            role = _normalize_role(getattr(chapter_outline, attr, None))
            if role:
                return role
        methodology_contract = getattr(chapter_outline, "methodology_contract", None)
        if isinstance(methodology_contract, Mapping) and methodology_contract.get("is_climax"):
            return "climax"
        required_payoff = str(getattr(chapter_outline, "required_payoff", "") or "").strip()
        if required_payoff:
            return "payoff"

    if chapter_no <= 3:
        return "opening"
    if chapter_no % 12 == 0:
        return "climax"
    if chapter_no % 6 == 0:
        return "pivot"
    return "escalation" if chapter_no % 3 == 0 else "setup"


def select_methodology_lineage(
    *,
    stage: str = "outline_chapter",
    scope: str = "chapter",
    chapter_no: int,
    chapter_role: str,
    genre_profile: str,
    weak_indicators: Mapping[str, float] | None = None,
    budget_cards: int = DEFAULT_BUDGET_CARDS,
    budget_tokens: int = DEFAULT_BUDGET_TOKENS,
    inventory_rules: Sequence[MethodologyInventoryRule] | None = None,
    inventory_path: Path | str = DEFAULT_INVENTORY_PATH,
) -> MethodologyLineage:
    """Select a reproducible, budget-limited methodology lineage."""

    if budget_cards < 1:
        raise ValueError("budget_cards must be >= 1.")
    if budget_tokens < 1:
        raise ValueError("budget_tokens must be >= 1.")

    role = derive_chapter_role(chapter_no=chapter_no, explicit_role=chapter_role)
    rules = (
        tuple(inventory_rules)
        if inventory_rules is not None
        else load_methodology_inventory(inventory_path)
    )
    weak_slots = _weak_slots(weak_indicators or {})
    slot_priority = _slot_priority(role, weak_slots)

    selected: list[AppliedMethodology] = []
    selected_slots: set[str] = set()
    seen_clusters: set[tuple[str, str]] = set()

    for level in ("must", "should", "nice"):
        for slot in slot_priority[level]:
            if len(selected) >= budget_cards or slot in selected_slots:
                continue
            rule = _best_rule_for_slot(
                rules,
                slot=slot,
                stage=stage,
                scope=scope,
                genre_profile=genre_profile,
                seen_clusters=seen_clusters,
            )
            if rule is None:
                continue
            selected.append(
                _rule_to_applied(
                    rule,
                    priority=level,
                    weak_slot=slot in weak_slots,
                    genre_profile=genre_profile,
                )
            )
            selected_slots.add(slot)
            if rule.similarity_cluster_id:
                seen_clusters.add((slot, rule.similarity_cluster_id))

    seed_payload = {
        "stage": stage,
        "scope": scope,
        "chapter_no": chapter_no,
        "chapter_role": role,
        "genre_profile": genre_profile,
        "budget_cards": budget_cards,
        "budget_tokens": budget_tokens,
        "weak_slots": sorted(weak_slots),
        "selected": [item.rule_id for item in selected],
    }
    seed = hashlib.sha256(
        json.dumps(seed_payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()[:16]
    return MethodologyLineage(
        chapter_no=chapter_no,
        genre_profile=genre_profile,
        chapter_role=role,
        selected=tuple(selected),
        selection_seed=seed,
        budget_tokens=budget_tokens,
        budget_cards=budget_cards,
    )


def select_lineage_for_chapter_outline(
    *,
    project: object,
    chapter_outline: object,
    weak_indicators: Mapping[str, float] | None = None,
) -> MethodologyLineage | None:
    """Planner/materialization adapter behind ``BESTSELLER_METHODOLOGY_V2``."""

    if not is_methodology_v2_enabled():
        return None
    chapter_no = int(getattr(chapter_outline, "chapter_number", 0) or 0)
    if chapter_no < 1:
        return None
    genre_profile = _genre_profile(project)
    role = derive_chapter_role(chapter_no=chapter_no, chapter_outline=chapter_outline)
    return select_methodology_lineage(
        stage="outline_chapter",
        scope="chapter",
        chapter_no=chapter_no,
        chapter_role=role,
        genre_profile=genre_profile,
        weak_indicators=weak_indicators,
    )


def _best_rule_for_slot(
    rules: Sequence[MethodologyInventoryRule],
    *,
    slot: str,
    stage: str,
    scope: str,
    genre_profile: str,
    seen_clusters: set[tuple[str, str]],
) -> MethodologyInventoryRule | None:
    candidates = [
        rule
        for rule in rules
        if rule.slot == slot
        and _stage_matches(rule, stage)
        and (
            not rule.similarity_cluster_id
            or (slot, rule.similarity_cluster_id) not in seen_clusters
        )
    ]
    if not candidates:
        return None
    return sorted(
        candidates,
        key=lambda rule: _rule_sort_key(
            rule,
            stage=stage,
            scope=scope,
            genre_profile=genre_profile,
        ),
    )[0]


def _rule_to_applied(
    rule: MethodologyInventoryRule,
    *,
    priority: str,
    weak_slot: bool,
    genre_profile: str,
) -> AppliedMethodology:
    verifiability = _verifiability(rule)
    gate_mode = _gate_mode(verifiability, priority=priority, weak_slot=weak_slot)
    hint = _application_hint(rule, genre_profile=genre_profile)
    why = f"{priority} slot for chapter role"
    if weak_slot:
        why = f"{why}; reinforced by weak indicators"
    return AppliedMethodology(
        rule_id=rule.rule_id,
        slot=rule.slot,
        craft_function=rule.slot,
        target_artifact_path=rule.binding_artifact[0],
        application_hint=hint,
        evidence_fields=_evidence_fields(rule),
        verifiability=verifiability,
        gate_mode=gate_mode,
        indicator_targets=rule.indicator_targets,
        source_lineage=rule.source,
        why_selected=why,
    )


def _slot_priority(role: str, weak_slots: set[str]) -> dict[str, tuple[str, ...]]:
    profile = _ROLE_SLOT_PROFILE.get(role, _ROLE_SLOT_PROFILE["setup"])
    must = list(profile["must"])
    should = list(profile["should"])
    nice = list(profile["nice"])
    for slot in sorted(weak_slots):
        if slot in must:
            continue
        if slot in should:
            should.remove(slot)
        if slot in nice:
            nice.remove(slot)
        must.append(slot)
    return {
        "must": tuple(must),
        "should": tuple(should),
        "nice": tuple(nice),
    }


def _weak_slots(weak_indicators: Mapping[str, float]) -> set[str]:
    slots: set[str] = set()
    for indicator, value in weak_indicators.items():
        try:
            numeric_value = float(value)
        except (TypeError, ValueError):
            continue
        if numeric_value >= 0.75:
            continue
        slot = _WEAK_INDICATOR_TO_SLOT.get(str(indicator))
        if slot:
            slots.add(slot)
    return slots


def _rule_sort_key(
    rule: MethodologyInventoryRule,
    *,
    stage: str,
    scope: str,
    genre_profile: str,
) -> tuple[int, int, int, int, str]:
    verifiability = _verifiability(rule)
    genre_fit = _genre_fit(rule, genre_profile)
    stage_fit = 1 if stage in rule.binding_stage else 0
    scope_fit = 1 if any(scope in artifact for artifact in rule.binding_artifact) else 0
    coverage = _COVERAGE_RANK.get(rule.coverage_status, 0)
    return (
        -_VERIFIABILITY_RANK[verifiability],
        -genre_fit,
        -coverage,
        -(stage_fit + scope_fit),
        rule.rule_id,
    )


def _normalize_slot(craft_function: str, payload: Mapping[str, Any]) -> str:
    if craft_function in _KNOWN_SLOTS:
        return craft_function
    if craft_function in _CRAFT_SLOT_ALIASES:
        return _CRAFT_SLOT_ALIASES[craft_function]
    artifacts = " ".join(_strings(payload.get("binding_artifact"))).lower()
    title = str(payload.get("title") or "").lower()
    text = f"{artifacts} {title}"
    if "world" in text:
        return "worldview_theme"
    if "premise" in text or "reader_promise" in text:
        return "premise_engine"
    return craft_function


def _stage_matches(rule: MethodologyInventoryRule, stage: str) -> bool:
    if stage in rule.binding_stage:
        return True
    if stage == "outline_chapter" and "prose_scene" in rule.binding_stage:
        return True
    return False


def _verifiability(rule: MethodologyInventoryRule) -> str:
    if rule.slot in {"hook_ledger", "payoff_ledger", "opening_three_function"}:
        return "strict"
    if rule.slot == "revision_repair_engine":
        return "strict"
    if rule.slot == "scene_causality_engine" and "scene_causality_score" in rule.indicator_targets:
        return "strict"
    if rule.slot == "pov_distance_controller" and any(
        target in rule.indicator_targets
        for target in ("pov_stability_score", "pov_distance_drift_ratio")
    ):
        return "strict"
    if rule.indicator_targets:
        return "heuristic"
    return "advisory"


def _gate_mode(verifiability: str, *, priority: str, weak_slot: bool) -> str:
    if verifiability == "advisory":
        return "warn" if weak_slot else "advisory"
    if weak_slot and verifiability == "strict":
        return "block"
    if verifiability == "strict" and priority == "must":
        return "block"
    return "warn"


def _evidence_fields(rule: MethodologyInventoryRule) -> tuple[str, ...]:
    if rule.slot == "hook_ledger":
        return ("methodology_contract.hooks_to_resolve", "methodology_contract.hooks_to_plant")
    if rule.slot == "payoff_ledger":
        return ("due_payoff_codes", "planted_clue_codes", "payoff_ledger.entries")
    if rule.slot == "scene_causality_engine":
        return ("causal_contract", "methodology_contract.conflict_stakes")
    if rule.slot == "pov_distance_controller":
        return ("scene.methodology_contract.camera_distance", "pov_stability_score")
    if rule.slot == "dialogue_subtext_engine":
        return ("scene.key_dialogue_beats", "dialogue_subtext_score")
    if rule.slot == "character_change_tracker":
        return ("chapter_contract.character_delta", "character_change_score")
    if rule.slot == "opening_three_function":
        return ("chapter_outline[0..2]", "opening_gate_findings")
    if rule.slot == "pacing_compression_engine":
        return ("methodology_contract.pacing_mode", "compression_ratio_compliance")
    if rule.slot == "revision_repair_engine":
        return ("rewrite_task.repair_domain", "review.findings")
    fields = tuple(rule.binding_artifact[:2])
    return fields or ("methodology_lineage",)


def _application_hint(rule: MethodologyInventoryRule, *, genre_profile: str) -> str:
    base = rule.text_snippet or rule.title or rule.rule_id
    if genre_profile:
        return f"{base} Genre fit: {genre_profile}."
    return base


def _genre_fit(rule: MethodologyInventoryRule, genre_profile: str) -> int:
    genre = genre_profile.lower()
    if not genre:
        return 0
    haystack = f"{rule.title} {rule.text_snippet} {rule.source}".lower()
    return 1 if any(part and part in haystack for part in genre.replace("/", " ").split()) else 0


def _normalize_role(value: object) -> str:
    raw = str(value or "").strip().lower()
    if not raw:
        return ""
    aliases = {
        "start": "opening",
        "intro": "opening",
        "beginning": "opening",
        "build": "setup",
        "rising": "escalation",
        "escalate": "escalation",
        "turn": "pivot",
        "twist": "pivot",
        "reveal": "payoff",
        "release": "payoff",
        "resolution": "denouement",
        "aftermath": "denouement",
        "ending": "denouement",
    }
    role = aliases.get(raw, raw)
    return role if role in _ROLE_SLOT_PROFILE else ""


def _genre_profile(project: object) -> str:
    genre = str(getattr(project, "genre", "") or "").strip()
    sub_genre = str(getattr(project, "sub_genre", "") or "").strip()
    if genre and sub_genre:
        return f"{genre}/{sub_genre}"
    return genre or sub_genre or "general"


def _strings(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if not isinstance(value, Sequence):
        return (str(value),)
    return tuple(str(item).strip() for item in value if str(item).strip())
