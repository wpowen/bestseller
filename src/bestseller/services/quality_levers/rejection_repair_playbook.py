"""Rejection Repair Playbook loader (``config/rejection_repair_playbook.yaml``).

Provides typed access to the 9 ``cause_id`` → repair-actions tables
used by the editor when a chapter must be regenerated in response to
a platform rejection.

The pipeline glue layer is expected to:

#. resolve a platform-specific phrase to a ``cause_id`` using
   :func:`bestseller.services.quality_levers.platform_profiles.parse_rejection_reason`
#. fetch :class:`RejectionCause` via :func:`get_rejection_cause`
#. invoke :func:`render_repair_actions_block` to obtain a
   prompt-ready fragment for the editor LLM call
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from bestseller.services.quality_levers._loader import (
    as_dict,
    as_int,
    as_str,
    as_str_tuple,
    load_yaml,
)


_CONFIG_FILENAME = "rejection_repair_playbook.yaml"


@dataclass(frozen=True)
class RepairAction:
    """One actionable instruction inside a ``causes.<id>.repair_actions``."""

    action_id: str
    priority: int
    action: str


@dataclass(frozen=True)
class RejectionCause:
    """One ``causes.<id>`` entry."""

    cause_id: str
    display: str
    typical_root_causes: tuple[str, ...]
    diagnosis_checklist: tuple[str, ...]
    repair_actions: tuple[RepairAction, ...]
    replacement_strategy: tuple[str, ...]
    validation_check: tuple[str, ...]


@dataclass(frozen=True)
class RejectionRepairPlaybookConfig:
    """Full typed view over ``rejection_repair_playbook.yaml``."""

    version: str
    causes: dict[str, RejectionCause]
    global_rules: tuple[str, ...]


def _flatten_checklist(raw: object) -> tuple[str, ...]:
    if isinstance(raw, str):
        cleaned = raw.strip()
        return (cleaned,) if cleaned else ()
    if not isinstance(raw, list):
        return ()
    items: list[str] = []
    for entry in raw:
        if isinstance(entry, str):
            cleaned = entry.strip()
            if cleaned:
                items.append(cleaned)
            continue
        if not isinstance(entry, dict):
            continue
        question = as_str(entry.get("q") or entry.get("question"))
        pass_if = as_str(entry.get("pass_if"))
        if question and pass_if:
            items.append(f"{question} (通过: {pass_if})")
        elif question:
            items.append(question)
    return tuple(items)


def _parse_repair_actions(raw: object) -> tuple[RepairAction, ...]:
    if not isinstance(raw, list):
        return ()
    actions: list[RepairAction] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        action_id = as_str(entry.get("id"))
        action_text = as_str(entry.get("action"))
        if not action_text:
            continue
        actions.append(
            RepairAction(
                action_id=action_id,
                priority=as_int(entry.get("priority"), default=99),
                action=action_text,
            )
        )
    actions.sort(key=lambda item: (item.priority, item.action_id))
    return tuple(actions)


def _parse_cause(cause_id: str, raw: object) -> RejectionCause:
    data = as_dict(raw)
    return RejectionCause(
        cause_id=cause_id,
        display=as_str(data.get("display")),
        typical_root_causes=as_str_tuple(data.get("typical_root_causes")),
        diagnosis_checklist=_flatten_checklist(data.get("diagnosis_checklist")),
        repair_actions=_parse_repair_actions(data.get("repair_actions")),
        replacement_strategy=as_str_tuple(data.get("replacement_strategy")),
        validation_check=as_str_tuple(data.get("validation_check")),
    )


@lru_cache(maxsize=1)
def load_rejection_repair_playbook() -> RejectionRepairPlaybookConfig:
    """Return the typed view over ``rejection_repair_playbook.yaml``."""

    raw = load_yaml(_CONFIG_FILENAME)
    causes_raw = as_dict(raw.get("causes"))
    causes: dict[str, RejectionCause] = {}
    for cause_id, cause_raw in causes_raw.items():
        canonical = as_str(cause_id)
        if not canonical:
            continue
        causes[canonical] = _parse_cause(canonical, cause_raw)
    global_rules = as_str_tuple(raw.get("global_rules"))
    return RejectionRepairPlaybookConfig(
        version=as_str(raw.get("version")),
        causes=causes,
        global_rules=global_rules,
    )


def get_rejection_cause(cause_id: str | None) -> RejectionCause | None:
    """Look up a single :class:`RejectionCause` by id."""

    if not cause_id:
        return None
    config = load_rejection_repair_playbook()
    return config.causes.get(cause_id)


def render_repair_actions_block(
    *,
    cause_ids: tuple[str, ...] | list[str] | str | None,
    max_actions: int = 5,
) -> str:
    """Render a prompt fragment listing repair actions for one or more causes.

    Accepts a single ``cause_id`` or an iterable of ids. Duplicate
    actions across causes are de-duplicated by ``(cause_id, action_id)``
    so the editor receives a flat instruction list.
    """

    if cause_ids is None:
        return ""
    if isinstance(cause_ids, str):
        ids: tuple[str, ...] = (cause_ids,)
    else:
        ids = tuple(as_str(item) for item in cause_ids if as_str(item))
    if not ids:
        return ""

    config = load_rejection_repair_playbook()
    lines: list[str] = ["【拒稿整改 · repair playbook】"]
    seen: set[tuple[str, str]] = set()
    emitted = 0
    for cause_id in ids:
        cause = config.causes.get(cause_id)
        if cause is None:
            continue
        lines.append(f"- 原因 {cause.cause_id}: {cause.display}")
        for action in cause.repair_actions:
            key = (cause.cause_id, action.action_id or action.action)
            if key in seen:
                continue
            seen.add(key)
            label = f"  优先级{action.priority} {action.action_id}".rstrip()
            lines.append(f"{label}: {action.action}")
            emitted += 1
            if emitted >= max_actions:
                break
        if emitted >= max_actions:
            break

    if config.global_rules:
        lines.append("- 通用约束: " + "; ".join(config.global_rules))
    return "\n".join(lines)
