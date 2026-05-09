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


@dataclass(frozen=True, slots=True)
class PremiumStateLedgerReport:
    passed: bool
    findings: tuple[PremiumStateFinding, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "findings": [
                {
                    "code": finding.code,
                    "severity": finding.severity,
                    "message": finding.message,
                    "path": finding.path,
                }
                for finding in self.findings
            ],
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
        if not _text(entry, "cost", "backlash", "price") and not _text(
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
        if not _text(entry, "active_choice", "choice"):
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


def validate_premium_state_ledger(ledger: Mapping[str, object] | None) -> PremiumStateLedgerReport:
    payload = _as_mapping(ledger)
    findings: list[PremiumStateFinding] = []
    findings.extend(_validate_progression_events(_as_sequence(payload.get("progression_events"))))
    findings.extend(_validate_rule_events(_as_sequence(payload.get("rule_events"))))
    findings.extend(_validate_faction_reactions(_as_sequence(payload.get("faction_reactions"))))
    findings.extend(_validate_relationship_events(_as_sequence(payload.get("relationship_events"))))
    findings.extend(_validate_agency_debts(_as_sequence(payload.get("agency_debts"))))
    passed = not any(finding.severity == "critical" for finding in findings)
    return PremiumStateLedgerReport(passed=passed, findings=tuple(findings))


__all__ = [
    "PremiumStateFinding",
    "PremiumStateLedgerReport",
    "validate_premium_state_ledger",
]
