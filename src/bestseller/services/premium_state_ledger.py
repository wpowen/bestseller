from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class PremiumStateFinding:
    code: str
    severity: str
    message: str
    path: str

    def to_dict(self) -> dict[str, str]:
        return {
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
            "path": self.path,
        }


@dataclass(frozen=True, slots=True)
class PremiumStateLedgerReport:
    passed: bool
    findings: tuple[PremiumStateFinding, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "findings": [finding.to_dict() for finding in self.findings],
        }


def _as_mapping(value: object) -> dict[str, object]:
    return dict(value) if isinstance(value, Mapping) else {}


def _as_sequence(value: object) -> list[object]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return []


def _text(entry: Mapping[str, object], *keys: str) -> str:
    for key in keys:
        value = entry.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if value is not None and not isinstance(value, (dict, list, tuple, set)):
            text = str(value).strip()
            if text:
                return text
    return ""


_NON_SUBSTANTIVE_TEXTS = {
    "无",
    "暂无",
    "未知",
    "未定",
    "待定",
    "不明",
    "无直接代价",
    "无直接选择",
    "none",
    "n/a",
    "na",
    "unknown",
    "tbd",
    "not yet",
}


def _substantive_text(entry: Mapping[str, object], *keys: str) -> str:
    text = _text(entry, *keys)
    normalized = text.strip().lower()
    return "" if normalized in _NON_SUBSTANTIVE_TEXTS else text


def _number(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return float(text)
        except ValueError:
            return None
    return None


def _finding(code: str, message: str, path: str, severity: str = "critical") -> PremiumStateFinding:
    return PremiumStateFinding(code=code, severity=severity, message=message, path=path)


def _validate_progression_events(events: Sequence[object]) -> list[PremiumStateFinding]:
    findings: list[PremiumStateFinding] = []
    causal_types = {
        "resource_spent",
        "resource_gained",
        "breakthrough",
        "technique_unlock",
        "artifact_unlock",
        "injury",
    }
    for index, raw in enumerate(events):
        entry = _as_mapping(raw)
        path = f"progression_events[{index}]"
        event_type = _text(entry, "event_type", "type", "kind")
        if not event_type:
            findings.append(_finding("progression_event_type_missing", "Missing event_type.", path))
        if not _text(entry, "subject", "character", "owner") and not _text(
            entry,
            "resource_key",
            "resource",
            "realm",
            "technique",
            "artifact",
        ):
            findings.append(
                _finding(
                    "progression_subject_missing",
                    "Progression event needs a subject or affected resource/realm.",
                    path,
                )
            )
        if event_type in causal_types and not _text(entry, "cause", "reason", "trigger"):
            findings.append(
                _finding(
                    "progression_cause_missing",
                    "Progression event needs explicit causal support.",
                    path,
                )
            )
    return findings


def _validate_rule_events(events: Sequence[object]) -> list[PremiumStateFinding]:
    findings: list[PremiumStateFinding] = []
    for index, raw in enumerate(events):
        entry = _as_mapping(raw)
        path = f"rule_events[{index}]"
        if not _text(entry, "rule_code", "id", "name", "rule"):
            findings.append(
                _finding("rule_identity_missing", "Rule event needs a rule id/name.", path)
            )
        if not _text(entry, "visible_effect", "surface_effect", "effect"):
            findings.append(
                _finding("rule_visible_effect_missing", "Rule event needs a visible effect.", path)
            )
        if not _substantive_text(entry, "cost", "backlash", "price") and not _substantive_text(
            entry,
            "exploit_used",
            "exploitation_potential",
            "solution",
        ):
            findings.append(
                _finding(
                    "rule_cost_or_exploit_missing",
                    "Rule event needs cost or exploit path.",
                    path,
                )
            )
    return findings


def _validate_faction_reactions(events: Sequence[object]) -> list[PremiumStateFinding]:
    findings: list[PremiumStateFinding] = []
    for index, raw in enumerate(events):
        entry = _as_mapping(raw)
        path = f"faction_reactions[{index}]"
        faction = _text(entry, "faction", "name", "organization")
        trigger = _text(entry, "trigger", "cause")
        reaction = _text(entry, "reaction", "next_reaction", "response")
        if not faction:
            findings.append(
                _finding("faction_missing", "Faction reaction needs faction name.", path)
            )
        if not trigger:
            findings.append(
                _finding("faction_trigger_missing", "Faction reaction needs trigger.", path)
            )
        if not reaction:
            findings.append(
                _finding("faction_reaction_missing", "Faction reaction needs response.", path)
            )
        elif reaction.replace("!", "").replace(chr(0xFF01), "").strip() in {
            "震惊",
            "所有势力震惊",
            "all factions are shocked",
            "shocked",
        }:
            findings.append(
                _finding(
                    "generic_faction_reaction",
                    "Faction reaction is generic; it must express differentiated interest.",
                    path,
                )
            )
    return findings


def _validate_relationship_events(events: Sequence[object]) -> list[PremiumStateFinding]:
    findings: list[PremiumStateFinding] = []
    valid_axes = {"distance", "trust", "power", "misunderstanding", "promise"}
    for index, raw in enumerate(events):
        entry = _as_mapping(raw)
        path = f"relationship_events[{index}]"
        if not _text(entry, "character_a", "from_character") or not _text(
            entry,
            "character_b",
            "target_character",
            "to_character",
        ):
            findings.append(
                _finding(
                    "relationship_parties_missing",
                    "Relationship event needs both parties.",
                    path,
                )
            )
        axis = _text(entry, "axis", "dimension")
        if axis and axis not in valid_axes:
            findings.append(
                _finding(
                    "relationship_axis_unknown",
                    f"Relationship axis '{axis}' is not one of {sorted(valid_axes)}.",
                    path,
                    severity="warning",
                )
            )
        if not axis:
            findings.append(
                _finding(
                    "relationship_axis_missing",
                    "Relationship event needs changed axis.",
                    path,
                )
            )
        if not _text(entry, "after", "state_after", "result"):
            findings.append(
                _finding(
                    "relationship_after_missing",
                    "Relationship event needs after-state.",
                    path,
                )
            )
        if not _substantive_text(entry, "active_choice", "choice"):
            findings.append(
                _finding(
                    "relationship_active_choice_missing",
                    "Relationship event needs protagonist active choice.",
                    path,
                )
            )
    return findings


def _validate_agency_debts(events: Sequence[object]) -> list[PremiumStateFinding]:
    findings: list[PremiumStateFinding] = []
    for index, raw in enumerate(events):
        entry = _as_mapping(raw)
        path = f"agency_debts[{index}]"
        if not _text(entry, "owner", "character", "subject"):
            findings.append(_finding("agency_debt_owner_missing", "Agency debt needs owner.", path))
        if not _text(entry, "debt", "promise", "obligation"):
            findings.append(
                _finding("agency_debt_missing", "Agency debt needs concrete debt.", path)
            )
        if not _text(entry, "due_window", "due", "deadline"):
            findings.append(
                _finding("agency_debt_due_missing", "Agency debt needs due window.", path)
            )
    return findings


def _validate_entry_events(events: Sequence[object]) -> list[PremiumStateFinding]:
    findings: list[PremiumStateFinding] = []
    power_changing = {"acquired", "learned", "bonded", "used", "upgraded", "restored"}
    cost_recommended = {"used", "upgraded", "restored"}
    for index, raw in enumerate(events):
        entry = _as_mapping(raw)
        path = f"entry_events[{index}]"
        event_type = _text(entry, "event_type", "type", "kind")
        if not _text(entry, "entry_id", "id"):
            findings.append(
                _finding(
                    "entry_event_entry_id_missing",
                    "Entry event needs entry_id.",
                    path,
                )
            )
        if not event_type:
            findings.append(
                _finding(
                    "entry_event_type_missing",
                    "Entry event needs event_type.",
                    path,
                )
            )
        if event_type in power_changing and not _text(entry, "trigger", "cause", "reason"):
            findings.append(
                _finding(
                    "entry_event_trigger_missing",
                    "Power-changing entry event needs trigger.",
                    path,
                )
            )
        if event_type in cost_recommended and not _substantive_text(
            entry,
            "cost_paid",
            "cost",
            "price",
            "backlash",
        ):
            findings.append(
                _finding(
                    "entry_event_cost_missing",
                    "Power-changing entry event should name visible cost.",
                    path,
                    severity="warning",
                )
            )
    return findings


def validate_premium_state_ledger(ledger: Mapping[str, object] | None) -> PremiumStateLedgerReport:
    payload = _as_mapping(ledger)
    findings: list[PremiumStateFinding] = []
    findings.extend(_validate_progression_events(_as_sequence(payload.get("progression_events"))))
    findings.extend(_validate_rule_events(_as_sequence(payload.get("rule_events"))))
    findings.extend(_validate_faction_reactions(_as_sequence(payload.get("faction_reactions"))))
    findings.extend(_validate_relationship_events(_as_sequence(payload.get("relationship_events"))))
    findings.extend(_validate_agency_debts(_as_sequence(payload.get("agency_debts"))))
    findings.extend(_validate_entry_events(_as_sequence(payload.get("entry_events"))))
    passed = not any(finding.severity == "critical" for finding in findings)
    return PremiumStateLedgerReport(passed=passed, findings=tuple(findings))


def _add_resource_balance(
    balances: dict[str, dict[str, float]],
    *,
    owner: str,
    resource: str,
    delta: float,
) -> None:
    owner_balances = balances.setdefault(owner, {})
    owner_balances[resource] = owner_balances.get(resource, 0.0) + delta


def _progression_delta(entry: Mapping[str, object]) -> float | None:
    parsed = _number(entry.get("delta"))
    if parsed is not None:
        return parsed
    event_type = _text(entry, "event_type", "type", "kind")
    if event_type == "resource_spent":
        return -1.0
    if event_type == "resource_gained":
        return 1.0
    return None


def _relationship_key(entry: Mapping[str, object]) -> str:
    character_a = _text(entry, "character_a", "from_character")
    character_b = _text(entry, "character_b", "target_character", "to_character")
    return f"{character_a} -> {character_b}"


def materialize_premium_state_snapshot(
    ledger: Mapping[str, object] | None,
) -> dict[str, Any]:
    """Fold a valid append-only premium ledger into a compact authoritative snapshot."""
    payload = _as_mapping(ledger)
    report = validate_premium_state_ledger(payload)
    snapshot: dict[str, Any] = {
        "passed": report.passed,
        "blocking_findings": [
            finding.to_dict()
            for finding in report.findings
            if finding.severity == "critical"
        ],
        "resource_balances": {},
        "rule_state": {},
        "faction_pressure_queue": [],
        "relationship_state": {},
        "open_agency_debts": [],
    }
    if not report.passed:
        return snapshot

    balances: dict[str, dict[str, float]] = {}
    for raw in _as_sequence(payload.get("progression_events")):
        entry = _as_mapping(raw)
        resource = _text(entry, "resource_key", "resource")
        owner = _text(entry, "subject", "character", "owner") or "global"
        delta = _progression_delta(entry)
        if resource and delta is not None:
            _add_resource_balance(balances, owner=owner, resource=resource, delta=delta)

    rule_state: dict[str, dict[str, object]] = {}
    for raw in _as_sequence(payload.get("rule_events")):
        entry = _as_mapping(raw)
        rule_key = _text(entry, "rule_code", "id", "name", "rule")
        if not rule_key:
            continue
        rule_state[rule_key] = {
            "name": _text(entry, "name", "rule") or rule_key,
            "last_visible_effect": _text(entry, "visible_effect", "surface_effect", "effect"),
            "last_exploit": _text(entry, "exploit_used", "exploitation_potential", "solution"),
            "last_cost": _text(entry, "cost", "backlash", "price"),
            "last_chapter": entry.get("chapter_number"),
        }

    faction_queue: list[dict[str, object]] = []
    for raw in _as_sequence(payload.get("faction_reactions")):
        entry = _as_mapping(raw)
        faction_queue.append(
            {
                "faction": _text(entry, "faction", "name", "organization"),
                "trigger": _text(entry, "trigger", "cause"),
                "reaction": _text(entry, "reaction", "next_reaction", "response"),
                "stance_change": _text(entry, "stance_change", "stance"),
                "next_pressure": _text(entry, "next_pressure", "pressure"),
                "chapter_number": entry.get("chapter_number"),
            }
        )

    relationship_state: dict[str, dict[str, object]] = {}
    for raw in _as_sequence(payload.get("relationship_events")):
        entry = _as_mapping(raw)
        key = _relationship_key(entry)
        state = dict(relationship_state.get(key) or {"axes": {}})
        axes = dict(state.get("axes") or {})
        axis = _text(entry, "axis", "dimension")
        if axis:
            axes[axis] = _text(entry, "after", "state_after", "result")
        state["axes"] = axes
        state["last_active_choice"] = _text(entry, "active_choice", "choice")
        state["last_cost"] = _text(entry, "cost", "price")
        state["last_chapter"] = entry.get("chapter_number")
        relationship_state[key] = state

    open_debts: list[dict[str, object]] = []
    for raw in _as_sequence(payload.get("agency_debts")):
        entry = _as_mapping(raw)
        open_debts.append(
            {
                "owner": _text(entry, "owner", "character", "subject"),
                "debt": _text(entry, "debt", "promise", "obligation"),
                "due_window": _text(entry, "due_window", "due", "deadline"),
                "chapter_number": entry.get("chapter_number"),
            }
        )

    snapshot["resource_balances"] = balances
    snapshot["rule_state"] = rule_state
    snapshot["faction_pressure_queue"] = faction_queue[-20:]
    snapshot["relationship_state"] = relationship_state
    snapshot["open_agency_debts"] = open_debts[-40:]
    return snapshot


__all__ = [
    "PremiumStateFinding",
    "PremiumStateLedgerReport",
    "materialize_premium_state_snapshot",
    "validate_premium_state_ledger",
]
