"""Deterministic story-capacity gate.

This is a pre-writing structural check, not a quality score.  It asks whether
the supplied design can keep producing causal change at its requested length.
It deliberately does not know any book title, genre, or setting vocabulary.
"""
# ruff: noqa: ANN401

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

LOOP_FIELDS = (
    "input",
    "goal",
    "choice",
    "counter",
    "cost",
    "state_change",
    "payoff",
    "next_pressure",
)
_EMPTY = {"", "none", "无", "无代价", "n/a", "null", "未定", "待定"}
_AXES = ("经营", "升级", "冲突", "关系")
_GENERIC_LOOP_VALUES = {
    "推进剧情",
    "推动主线",
    "完成任务",
    "解决问题",
    "继续发展",
    "下一件事",
}
STORY_ARCHITECTURE_VERSION = "story-architecture.v1"
_ARCHITECTURE_UNIT_FIELDS = (
    *LOOP_FIELDS,
    "id",
    "choice_mode",
    "mechanism_family",
    "counter_mode",
    "cost_mode",
    "state_axis",
)
_ADAPTATION_FIELDS = (
    "after_unit",
    "actor",
    "learned",
    "strategy_change",
    "forces_new_choice",
)
_GENRE_CONTRACT_FIELDS = ("rule", "observable_state", "failure_consequence")
_EFFECT_CONTRACT_FIELDS = ("skill_key", "mechanism", "consequence")
_COMEDY_EFFECT_FIELDS = ("setup", "misalignment", "reaction", "situation_change")


def _text(value: Any) -> str:
    if isinstance(value, str):
        return " ".join(value.split()).strip()
    if isinstance(value, (int, float)):
        return str(value)
    return ""


def _items(value: Any) -> list[Any]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return list(value)
    return []


def _canonical_loop(loop: Mapping[str, Any]) -> tuple[str, ...]:
    return tuple(_text(loop.get(field)).lower() for field in LOOP_FIELDS)


def _mechanism_signature(loop: Mapping[str, Any]) -> tuple[str, ...]:
    """Return the causal role fingerprint, deliberately ignoring surface nouns."""

    return tuple(
        _text(loop.get(field)).lower()
        for field in (
            "choice_mode",
            "mechanism_family",
            "counter_mode",
            "cost_mode",
            "state_axis",
        )
    )


def _validate_loops(raw: Any) -> dict[str, Any]:
    loops = [item for item in _items(raw) if isinstance(item, Mapping)]
    missing: list[dict[str, Any]] = []
    valid: list[Mapping[str, Any]] = []
    for index, loop in enumerate(loops):
        absent = [field for field in LOOP_FIELDS if not _text(loop.get(field))]
        if absent:
            missing.append({"index": index, "fields": absent})
        else:
            valid.append(loop)
    signatures = [_canonical_loop(loop) for loop in valid]
    distinct = len(set(signatures))
    repeated = len(signatures) - distinct
    return {
        "input_loops": len(loops),
        "valid_loops": len(valid),
        "distinct_loops": distinct,
        "repeated_loops": repeated,
        "missing_fields": missing,
        "static_repetition": bool(
            signatures and (distinct == 1 or repeated / len(signatures) > 0.5)
        ),
        "required_fields": list(LOOP_FIELDS),
    }


def _engine_axes(story: Mapping[str, Any]) -> list[str]:
    engine = story.get("story_engine")
    if not isinstance(engine, Mapping):
        return []
    supplied = [_text(axis) for axis in _items(engine.get("axes"))]
    return [axis for axis in _AXES if axis in supplied]


def _has_nonempty_cost(loops: Sequence[Any]) -> bool:
    return any(
        _text(item.get("cost")).lower() not in _EMPTY for item in loops if isinstance(item, Mapping)
    )


def _progression_is_operational(story: Mapping[str, Any]) -> bool:
    entries = _items(story.get("progression"))
    operational = 0
    for item in entries:
        if isinstance(item, Mapping):
            if _text(item.get("name")) and _text(item.get("from")) and _text(item.get("to")):
                operational += 1
        elif _text(item) and _text(item).lower() not in _EMPTY:
            # A prose track is useful only when it contains an observable
            # transition, rather than a generic promise such as "get stronger".
            lowered = _text(item).lower()
            if any(marker in lowered for marker in ("->", "到", "从", "提升", "升级", "阶段")):
                operational += 1
    return operational >= 2


def _relationship_engine_is_operational(story: Mapping[str, Any]) -> bool:
    relationships = [
        item for item in _items(story.get("relationships")) if isinstance(item, Mapping)
    ]
    return any(
        _text(item.get("a"))
        and _text(item.get("b"))
        and _text(item.get("tension"))
        and _text(item.get("change"))
        for item in relationships
    )


def _conflict_engine_is_operational(story: Mapping[str, Any]) -> bool:
    conflicts = [item for item in _items(story.get("conflicts")) if isinstance(item, Mapping)]
    return (
        sum(
            bool(
                _text(item.get("actor")) and _text(item.get("goal")) and _items(item.get("tactics"))
            )
            for item in conflicts
        )
        >= 2
    )


def _seriality_findings(story: Mapping[str, Any]) -> list[str]:
    judge = story.get("seriality_judge")
    if not isinstance(judge, Mapping):
        return []
    findings: list[str] = []
    missing = [field for field in ("renewability", "promise_survival") if field not in judge]
    if missing:
        findings.append("UNKNOWN_SERIALITY_JUDGE")
    for field, code in (
        ("renewability", "LOW_SERIALITY_RENEWABILITY"),
        ("promise_survival", "LOW_SERIALITY_PROMISE_SURVIVAL"),
    ):
        try:
            value = float(judge.get(field))
        except (TypeError, ValueError):
            continue
        if value < 6:
            findings.append(code)
    return findings


def _is_seriality_proof(story: Mapping[str, Any]) -> bool:
    return bool(_text(story.get("repeatable_story_unit"))) and not (
        story.get("chapter_loop") or story.get("causal_loop")
    )


def _is_story_architecture(story: Mapping[str, Any]) -> bool:
    return _text(story.get("schema_version")) == STORY_ARCHITECTURE_VERSION


def _complete_mapping_count(raw: Any, fields: Sequence[str]) -> int:
    return sum(
        1
        for item in _items(raw)
        if isinstance(item, Mapping) and all(_text(item.get(field)) for field in fields)
    )


def _evaluate_story_architecture(
    story: Mapping[str, Any], *, requested: int
) -> dict[str, Any]:
    """Validate one executable architecture contract before planning starts.

    This contract proves that a premise can generate changing choices, learned
    opposition, relationship movement, genre-native state, and selected reader
    effects. It is structural evidence only, never a claim of popularity.
    """

    causal_units = [
        dict(item) for item in _items(story.get("causal_units")) if isinstance(item, Mapping)
    ]
    # Architecture prototypes prove that the engine can change shape; the
    # seriality capacity report separately proves full requested length.  Do
    # not demand hundreds of hand-written prototype loops or pretend four
    # examples alone prove 500 chapters.
    structural_horizon = min(requested, 96) if requested > 0 else 0
    normalized = dict(story)
    normalized.pop("schema_version", None)
    normalized.pop("causal_units", None)
    normalized["chapter_loop"] = causal_units
    base = evaluate_story_viability(normalized, target_chapters=structural_horizon)
    # The legacy gate's axes are management-specific Chinese labels.  The
    # architecture contract proves crossover through causal families,
    # progression, opposition, relationships, genre contracts and effects,
    # so it must not force suspense/science-fiction/political stories to lie
    # that they are all "经营/升级" engines.
    codes = [
        code
        for code in (base.get("blocking_codes") or [])
        if code != "INSUFFICIENT_ENGINE_CROSSOVER"
    ]

    required_units = 4 if requested >= 36 else 3
    complete_units = [
        unit
        for unit in causal_units
        if all(_text(unit.get(field)) for field in _ARCHITECTURE_UNIT_FIELDS)
    ]
    if len(complete_units) < required_units:
        codes.append("INCOMPLETE_STORY_ARCHITECTURE")

    signatures = [_mechanism_signature(unit) for unit in complete_units]
    distinct_signatures = len(set(signatures))
    if signatures and (
        distinct_signatures < min(3, len(signatures))
        or len(signatures) - distinct_signatures > len(signatures) / 2
    ):
        codes.append("STATIC_LOOP_REPETITION")
    choice_modes = {
        _text(unit.get("choice_mode")).lower()
        for unit in complete_units
        if _text(unit.get("choice_mode"))
    }
    if requested >= 36 and len(choice_modes) < 3:
        codes.append("INSUFFICIENT_CHOICE_VARIETY")

    unit_ids = [_text(unit.get("id")) for unit in complete_units]
    seen: set[str] = set()
    disconnected = len(unit_ids) != len(set(unit_ids))
    for index, unit in enumerate(complete_units):
        unit_id = _text(unit.get("id"))
        caused_by = _text(unit.get("caused_by"))
        if index == 0:
            if caused_by:
                disconnected = True
        elif not caused_by or caused_by not in seen:
            disconnected = True
        if unit_id:
            seen.add(unit_id)
    if requested >= 36 and (len(complete_units) < required_units or disconnected):
        codes.append("CAUSAL_CHAIN_DISCONNECTED")

    adaptations = [
        item for item in _items(story.get("opponent_adaptations")) if isinstance(item, Mapping)
    ]
    complete_adaptations = [
        item
        for item in adaptations
        if all(_text(item.get(field)) for field in _ADAPTATION_FIELDS)
        and _text(item.get("after_unit")) in set(unit_ids)
    ]
    if requested >= 36 and len(complete_adaptations) < 2:
        codes.append("MISSING_OPPONENT_ADAPTATION")

    grammar = story.get("grammar")
    grammar = grammar if isinstance(grammar, Mapping) else {}
    required_contracts = [
        _text(item) for item in _items(grammar.get("required_contracts")) if _text(item)
    ]
    genre_contracts = story.get("genre_contracts")
    genre_contracts = genre_contracts if isinstance(genre_contracts, Mapping) else {}
    missing_genre_contracts = [
        key
        for key in required_contracts
        if not isinstance(genre_contracts.get(key), Mapping)
        or not all(
            _text(genre_contracts[key].get(field))
            for field in _GENRE_CONTRACT_FIELDS
        )
    ]
    if requested >= 36 and (not required_contracts or missing_genre_contracts):
        codes.append("MISSING_GENRE_CONTRACT_IMPLEMENTATION")

    selected_effects = {
        _text(item) for item in _items(story.get("selected_effect_skills")) if _text(item)
    }
    effect_contracts = [
        item for item in _items(story.get("effect_contracts")) if isinstance(item, Mapping)
    ]
    effect_by_key = {
        _text(item.get("skill_key")): item
        for item in effect_contracts
        if all(_text(item.get(field)) for field in _EFFECT_CONTRACT_FIELDS)
        and len([value for value in _items(item.get("variation_sources")) if _text(value)]) >= 2
        and len([value for value in _items(item.get("phase_payoffs")) if _text(value)]) >= 3
    }
    if selected_effects - set(effect_by_key):
        codes.append("MISSING_SELECTED_EFFECT_CONTRACT")
    comedy = effect_by_key.get("comedy_engine")
    if "comedy_engine" in selected_effects and (
        not isinstance(comedy, Mapping)
        or not all(_text(comedy.get(field)) for field in _COMEDY_EFFECT_FIELDS)
    ):
        codes.append("MISSING_SELECTED_EFFECT_CONTRACT")

    report = dict(base.get("report") or {})
    report.update(
        {
            "mode": STORY_ARCHITECTURE_VERSION,
            "architecture": {
                "requested_chapters": requested,
                "structural_horizon_chapters": structural_horizon,
                "input_units": len(causal_units),
                "complete_units": len(complete_units),
                "distinct_mechanism_signatures": distinct_signatures,
                "choice_modes": sorted(choice_modes),
                "complete_opponent_adaptations": len(complete_adaptations),
                "required_genre_contracts": required_contracts,
                "missing_genre_contracts": missing_genre_contracts,
                "selected_effect_skills": sorted(selected_effects),
                "implemented_effect_skills": sorted(effect_by_key),
            },
            "evidence_boundary": (
                "This proves minimum executable story architecture only; it does not prove "
                "reader appeal, ranking performance, or commercial success."
            ),
        }
    )
    unique_codes = list(dict.fromkeys(codes))
    ceiling = int(base.get("estimated_chapter_ceiling") or 0)
    return {
        "passed": not unique_codes and ceiling >= structural_horizon,
        "blocking_codes": unique_codes,
        "estimated_chapter_ceiling": ceiling,
        "report": report,
    }


def _evaluate_seriality_proof(
    story: Mapping[str, Any], *, requested: int
) -> dict[str, Any]:
    """Bridge approved ConceptContract seriality proofs into the new gate.

    Existing projects predate the explicit eight-field causal-loop contract.
    They may pass only when the deterministic long-form proof is complete.
    The historical LLM judge stays advisory and becomes blocking evidence only
    when a deterministic defect is already present.
    """

    requirements = (
        ("renewal_sources", 2, "RENEWABLE_STORY_SOURCE_MISSING"),
        ("accumulation_tracks", 2, "ACCUMULATION_TRACK_MISSING"),
        ("phase_transitions", 3, "PHASE_TRANSITION_MISSING"),
        ("opposing_ecology", 2, "MISSING_ADVERSARIAL_ENGINE"),
    )
    codes: list[str] = []
    counts: dict[str, int] = {}
    for field, minimum, code in requirements:
        count = len([item for item in _items(story.get(field)) if _text(item)])
        counts[field] = count
        if requested >= 36 and count < minimum:
            codes.append(code)
    endgame = _text(story.get("endgame_direction"))
    if requested >= 36 and not endgame:
        codes.append("MISSING_ENDGAME_DIRECTION")

    capacity = story.get("capacity_report")
    capacity = dict(capacity) if isinstance(capacity, Mapping) else {}
    try:
        ceiling = int(capacity.get("estimated_chapter_ceiling") or 0)
    except (TypeError, ValueError):
        ceiling = 0
    if requested >= 36 and not capacity:
        codes.append("MISSING_CAPACITY_REPORT")
    elif capacity.get("passed") is not True:
        codes.append("DECLARED_CAPACITY_REPORT_FAILED")
    if ceiling and ceiling < requested:
        codes.append("TARGET_EXCEEDS_PROVEN_CAPACITY")

    seriality_codes = _seriality_findings(story)
    if seriality_codes and codes:
        codes.extend(seriality_codes)
    return {
        "passed": not codes and (not ceiling or ceiling >= requested),
        "blocking_codes": list(dict.fromkeys(codes)),
        "estimated_chapter_ceiling": ceiling or (requested if not codes else 0),
        "report": {
            "mode": "legacy_seriality_proof",
            "requested_chapters": requested,
            "field_counts": counts,
            "endgame_direction": endgame,
            "capacity_report": capacity,
            "seriality_judge": dict(story.get("seriality_judge"))
            if isinstance(story.get("seriality_judge"), Mapping)
            else None,
            "seriality_findings": seriality_codes,
            "evidence_boundary": (
                "Legacy proof validates minimum renewable structure only; new projects "
                "must provide explicit causal loops."
            ),
        },
    }


def evaluate_story_viability(
    story: Mapping[str, Any], target_chapters: int | None = None
) -> dict[str, Any]:
    """Return a stable, JSON-ready viability report.

    The gate is intentionally fail-closed for long-form requests.  It does
    not claim that a passing design is good or commercially successful; it
    only confirms the minimum causal machinery needed to write it honestly.
    """

    requested = target_chapters if target_chapters is not None else story.get("target_chapters", 0)
    try:
        requested = max(0, int(requested))
    except (TypeError, ValueError):
        requested = 0
    if _is_story_architecture(story):
        return _evaluate_story_architecture(story, requested=requested)
    if _is_seriality_proof(story):
        return _evaluate_seriality_proof(story, requested=requested)
    loop_report = _validate_loops(story.get("chapter_loop", story.get("causal_loop")))
    axes = _engine_axes(story)
    loops = [
        item
        for item in _items(story.get("chapter_loop", story.get("causal_loop")))
        if isinstance(item, Mapping)
    ]
    progression = _progression_is_operational(story)
    relationships = _relationship_engine_is_operational(story)
    conflicts = _conflict_engine_is_operational(story)
    costs = _has_nonempty_cost(loops)
    codes: list[str] = []
    seriality_codes = _seriality_findings(story)

    raw_target = target_chapters if target_chapters is not None else story.get("target_chapters")
    if raw_target is None or requested <= 0:
        codes.append("UNKNOWN_TARGET_CHAPTERS")

    engine = story.get("story_engine")
    endgame = (
        engine.get("endgame_direction")
        if isinstance(engine, Mapping)
        else story.get("endgame_direction")
    )
    if not _text(endgame):
        codes.append("MISSING_ENDGAME_DIRECTION")

    if loop_report["input_loops"] == 0 or loop_report["missing_fields"]:
        codes.append("INCOMPLETE_CAUSAL_LOOP")
    if loop_report["static_repetition"]:
        codes.append("STATIC_LOOP_REPETITION")
    generic_values = sum(
        1
        for loop in loops
        for field in LOOP_FIELDS
        if _text(loop.get(field)) in _GENERIC_LOOP_VALUES
    )
    if generic_values >= 2:
        codes.append("WEAK_EVIDENCE_BINDING")
    if requested >= 36 and not relationships:
        codes.append("MISSING_RELATIONAL_ENGINE")
    if requested >= 60 and not costs:
        codes.append("MISSING_COST_TRADEOFF")
    if requested >= 120 and not progression:
        codes.append("MISSING_PROGRESSION_ENGINE")
    if requested >= 36 and len(axes) < 3:
        codes.append("INSUFFICIENT_ENGINE_CROSSOVER")
    if requested >= 60 and not conflicts:
        codes.append("MISSING_ADVERSARIAL_ENGINE")
    if "UNKNOWN_SERIALITY_JUDGE" in seriality_codes:
        codes.append("UNKNOWN_SERIALITY_JUDGE")
    # The historical judge is advisory because its calibration is imperfect.
    # Its low scores become blocking evidence only when deterministic design
    # defects already show that the promise cannot be sustained.
    if seriality_codes and any(code != "UNKNOWN_SERIALITY_JUDGE" for code in codes):
        codes.extend(
            code for code in seriality_codes if code != "UNKNOWN_SERIALITY_JUDGE"
        )

    # Each distinct, fully causal loop can sustain a bounded run.  Additional
    # progression tracks and engine axes extend the ceiling, but never rescue
    # a static loop.  This is capacity estimation, not a popularity score.
    ceiling = loop_report["distinct_loops"] * 16
    if loop_report["static_repetition"]:
        ceiling = min(ceiling, 16)
    if progression:
        ceiling += 16
    if conflicts:
        ceiling += 16
    if relationships:
        ceiling += 16
    if len(axes) >= 4:
        ceiling += 16
    ceiling = max(0, min(200, ceiling))

    report = {
        "requested_chapters": requested,
        "loop_validation": loop_report,
        "engine_axes": axes,
        "capabilities": {
            "progression": progression,
            "cost_tradeoff": costs,
            "adversarial": conflicts,
            "relationships": relationships,
        },
        "seriality_judge": dict(story.get("seriality_judge"))
        if isinstance(story.get("seriality_judge"), Mapping)
        else None,
        "seriality_findings": seriality_codes,
        "endgame_direction": _text(endgame),
        "capacity_basis": (
            "distinct causal loops plus operational progression, conflict, relationship, "
            "and crossover engines"
        ),
    }
    return {
        "passed": not codes and ceiling >= requested,
        "report": report,
        "blocking_codes": codes,
        "estimated_chapter_ceiling": ceiling,
    }


def audit_story_viability(
    story: Mapping[str, Any], target_chapters: int | None = None
) -> dict[str, Any]:
    """Compatibility alias for callers that use audit terminology."""

    return evaluate_story_viability(story, target_chapters=target_chapters)
