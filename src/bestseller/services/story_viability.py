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
