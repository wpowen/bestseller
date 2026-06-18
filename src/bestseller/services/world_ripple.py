"""Dynamic ripple layer — the world model's "case law".

The static world model is the constitution (derived at planning). This layer
keeps the world ALIVE at run time: when a chapter's prose makes contact with a
:class:`WorldStateVariable`'s ``change_triggers``, that variable's
``current_value`` is advanced and stamped with the chapter, so later chapters
read an up-to-date world state instead of a frozen one.

Pure core (:func:`compute_state_ripples`) + a thin, fail-safe persistence hook
(:func:`apply_world_state_ripples`) that writes back into
``project.metadata_json["story_design_kernel"]["worldview_kernel"]``.
"""

# ruff: noqa: RUF001, E501, ANN401

from __future__ import annotations

from collections.abc import Mapping, Sequence
import logging
from typing import Any

from bestseller.services.world_dimensions import _tokens

logger = logging.getLogger(__name__)

_MIN_SHARED_SHINGLES = 2
_MAX_VALUE_LEN = 120


def _trigger_fires(trigger: str, chapter_tokens: set[str]) -> bool:
    """Conservative contact test between a change trigger and the chapter prose."""

    trig_tokens = _tokens(trigger)
    if not trig_tokens:
        return False
    shared = trig_tokens & chapter_tokens
    if len(trig_tokens) <= 2:
        return trig_tokens <= chapter_tokens  # short trigger must fully appear
    return len(shared) >= _MIN_SHARED_SHINGLES


def _cascade_targets(var: Mapping[str, Any]) -> list[str]:
    raw = var.get("cascades_to") or var.get("affects") or var.get("propagates_to")
    if isinstance(raw, str):
        return [raw.strip()] if raw.strip() else []
    if isinstance(raw, (list, tuple)):
        return [str(x).strip() for x in raw if str(x).strip()]
    return []


def compute_state_ripples(
    state_variables: Sequence[Mapping[str, Any]],
    chapter_text: str,
    *,
    chapter_number: int,
) -> list[dict[str, Any]]:
    """Return per-variable updates this chapter advances — directional + cascading.

    A variable updates when one of its ``change_triggers`` makes surface contact
    with the prose (a *primary* update). It then CASCADES along ``cascades_to`` to
    the variables it causally drives (筑基 ⇒ 寿元 + 实力 + 飞行解锁), so one milestone
    propagates across the world instead of stamping a single note. Each update
    records the variable's ``desired_direction`` so the move is directional, not a
    bare timestamp. Conservative + loop-safe (BFS with a visited set).
    """

    chapter_tokens = _tokens(chapter_text)
    if not chapter_tokens:
        return []
    by_key: dict[str, Mapping[str, Any]] = {
        str(v.get("key")).strip(): v
        for v in state_variables
        if isinstance(v, Mapping) and str(v.get("key") or "").strip()
    }

    fired_trigger: dict[str, str] = {}
    for key, var in by_key.items():
        triggers = var.get("change_triggers") or []
        if isinstance(triggers, str):
            triggers = [triggers]
        hit = next(
            (str(t) for t in triggers if isinstance(t, str) and _trigger_fires(str(t), chapter_tokens)),
            None,
        )
        if hit is not None:
            fired_trigger[key] = hit
    if not fired_trigger:
        return []

    # BFS: primary fired variables first, then cascade along cascades_to.
    order: list[tuple[str, str | None]] = []  # (key, cascaded_from)
    seen: set[str] = set()
    queue: list[tuple[str, str | None]] = [(k, None) for k in fired_trigger]
    while queue:
        key, src = queue.pop(0)
        if key in seen or key not in by_key:
            continue
        seen.add(key)
        order.append((key, src))
        for nxt in _cascade_targets(by_key[key]):
            if nxt not in seen and nxt in by_key:
                queue.append((nxt, key))

    updates: list[dict[str, Any]] = []
    for key, src in order:
        var = by_key[key]
        prev = str(var.get("current_value") or "").strip()
        direction = str(var.get("desired_direction") or "").strip()
        dir_tag = f"[{direction[:10]}]" if direction else ""
        if src is None:
            cause = f"由「{fired_trigger[key][:24]}」推进"
        else:
            cause = f"因「{src}」级联推进"
        stamp = f"第{chapter_number}章:{cause}{dir_tag}"
        new_value = stamp if not prev else f"{prev}；{stamp}"
        if len(new_value) > _MAX_VALUE_LEN:
            new_value = new_value[-_MAX_VALUE_LEN:]
        updates.append(
            {
                "key": key,
                "previous_value": prev,
                "current_value": new_value,
                "direction": direction,
                "triggered_by": fired_trigger.get(key, f"cascade:{src}"),
                "cascaded_from": src,
                "chapter": chapter_number,
            }
        )
    return updates


def apply_ripples_to_kernel(
    story_design_kernel: dict[str, Any],
    chapter_text: str,
    *,
    chapter_number: int,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Return ``(updated_kernel, updates)`` with state variables advanced in place.

    Pure (no I/O): operates on a copy of the kernel dict so callers stay
    immutable-friendly. Safe on malformed input — returns the input + ``[]``.
    """

    if not isinstance(story_design_kernel, Mapping):
        return dict(story_design_kernel) if isinstance(story_design_kernel, dict) else {}, []
    kernel = dict(story_design_kernel)
    worldview = kernel.get("worldview_kernel")
    if not isinstance(worldview, Mapping):
        return kernel, []
    state_vars = worldview.get("state_variables")
    if not isinstance(state_vars, list) or not state_vars:
        return kernel, []

    updates = compute_state_ripples(state_vars, chapter_text, chapter_number=chapter_number)
    if not updates:
        return kernel, []

    by_key = {u["key"]: u for u in updates}
    new_vars: list[Any] = []
    for var in state_vars:
        if isinstance(var, Mapping) and str(var.get("key") or "") in by_key:
            u = by_key[str(var.get("key"))]
            merged = dict(var)
            merged["current_value"] = u["current_value"]
            merged["last_updated_chapter"] = chapter_number
            new_vars.append(merged)
        else:
            new_vars.append(var)

    new_worldview = dict(worldview)
    new_worldview["state_variables"] = new_vars
    kernel["worldview_kernel"] = new_worldview
    return kernel, updates


async def apply_world_state_ripples(
    session: Any,
    project: Any,
    *,
    chapter_number: int,
    chapter_text: str,
) -> int:
    """Advance the project's world state variables from a written chapter.

    Fully fail-safe: any error is swallowed (returns 0). Reads + writes
    ``project.metadata_json["story_design_kernel"]``.
    """

    try:
        metadata = getattr(project, "metadata_json", None)
        if not isinstance(metadata, dict):
            return 0
        sdk = metadata.get("story_design_kernel")
        if not isinstance(sdk, dict):
            return 0
        updated_kernel, updates = apply_ripples_to_kernel(
            sdk, chapter_text, chapter_number=chapter_number
        )
        if not updates:
            return 0
        project.metadata_json = {**metadata, "story_design_kernel": updated_kernel}
        logger.info(
            "world ripple ch%d: advanced %d state variable(s): %s",
            chapter_number,
            len(updates),
            ", ".join(u["key"] for u in updates),
        )
        return len(updates)
    except Exception:
        logger.debug("world_state ripple failed (non-fatal)", exc_info=True)
        return 0


__all__ = [
    "apply_ripples_to_kernel",
    "apply_world_state_ripples",
    "compute_state_ripples",
]
