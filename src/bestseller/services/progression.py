from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

from pydantic import BaseModel, Field

from bestseller.domain.progression import (
    Artifact,
    BreakthroughCauseKind,
    BreakthroughEvent,
    PowerRealm,
    PowerSystem,
    ProgressionBottleneck,
    ProgressionContext,
    ResourceLedger,
    ResourceLedgerEntry,
    Technique,
)
from bestseller.domain.story_bible import (
    CastSpecInput,
    CharacterInput,
    PowerSystemInput,
    VolumePlanEntryInput,
    WorldSpecInput,
)

_REALM_SUFFIXES = (
    "大圆满",
    "初期",
    "中期",
    "后期",
    "巅峰",
    "圆满",
    "期",
    "境",
    "层",
)


class ProgressionFinding(BaseModel):
    code: str = Field(min_length=1, max_length=96)
    severity: Literal["info", "warning", "error"] = "error"
    message: str = Field(min_length=1)
    blocking: bool = True


class ProgressionValidationReport(BaseModel):
    passed: bool
    findings: tuple[ProgressionFinding, ...] = Field(default_factory=tuple)


def _report(findings: Sequence[ProgressionFinding]) -> ProgressionValidationReport:
    return ProgressionValidationReport(
        passed=not any(finding.blocking for finding in findings),
        findings=tuple(findings),
    )


def _finding(
    code: str,
    message: str,
    *,
    severity: Literal["info", "warning", "error"] = "error",
    blocking: bool = True,
) -> ProgressionFinding:
    return ProgressionFinding(
        code=code,
        severity=severity,
        message=message,
        blocking=blocking,
    )


def _normalize_lookup_key(value: str | None) -> str:
    return (value or "").strip().lower()


def _normalize_realm_name(value: str | None) -> str:
    normalized = _normalize_lookup_key(value).replace(" ", "")
    for suffix in _REALM_SUFFIXES:
        if normalized.endswith(suffix):
            return normalized[: -len(suffix)]
    return normalized


def _realm_candidates(realm: PowerRealm) -> tuple[str, ...]:
    return (realm.name, *realm.aliases)


def _realm_matches(realm: PowerRealm, raw_name: str) -> bool:
    needle = _normalize_realm_name(raw_name)
    for candidate in _realm_candidates(realm):
        normalized_candidate = _normalize_realm_name(candidate)
        if needle == normalized_candidate:
            return True
        if normalized_candidate and needle.startswith(normalized_candidate):
            return True
    return False


def _matches_ref(raw_key: str | None, key: str, name: str) -> bool:
    needle = _normalize_lookup_key(raw_key)
    return needle in {_normalize_lookup_key(key), _normalize_lookup_key(name)}


def _as_mapping(value: object) -> dict[str, object]:
    if isinstance(value, BaseModel):
        return value.model_dump()
    if isinstance(value, dict):
        return value
    return {}


def _as_sequence(value: object) -> list[object]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, dict):
        return [
            {**_as_mapping(item), "key": key} if isinstance(item, dict) else item
            for key, item in value.items()
        ]
    return [value]


def _as_string(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        return text or None
    if isinstance(value, (int, float, bool)):
        return str(value)
    return None


def _as_positive_int(value: object, default: int = 0) -> int:
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value.strip())
    return default


def _string_tuple(value: object) -> tuple[str, ...]:
    return tuple(
        text
        for text in (_as_string(item) for item in _as_sequence(value))
        if text is not None
    )


def _bottleneck_severity(value: object) -> Literal["soft", "hard"]:
    return "soft" if _as_string(value) == "soft" else "hard"


def _extra_mapping(model: BaseModel) -> dict[str, object]:
    return dict(model.model_extra or {})


def _coerce_world_spec(value: WorldSpecInput | dict[str, object]) -> WorldSpecInput:
    return value if isinstance(value, WorldSpecInput) else WorldSpecInput.model_validate(value)


def _coerce_cast_spec(value: CastSpecInput | dict[str, object] | None) -> CastSpecInput | None:
    if value is None:
        return None
    return value if isinstance(value, CastSpecInput) else CastSpecInput.model_validate(value)


def _coerce_volume_plan(
    value: Sequence[VolumePlanEntryInput | dict[str, object]] | None,
) -> tuple[VolumePlanEntryInput, ...]:
    if value is None:
        return ()
    entries: list[VolumePlanEntryInput] = []
    for item in value:
        if isinstance(item, VolumePlanEntryInput):
            entries.append(item)
        elif isinstance(item, dict):
            entries.append(VolumePlanEntryInput.model_validate(item))
    return tuple(entries)


def realm_index(system: PowerSystem, realm_name: str) -> int:
    """Return the ordered index of a realm or -1 when the realm is unknown."""
    for index, realm in enumerate(system.ordered_realms):
        if _realm_matches(realm, realm_name):
            return index
    return -1


def _next_realm(system: PowerSystem, realm_name: str) -> PowerRealm | None:
    current_index = realm_index(system, realm_name)
    ordered = system.ordered_realms
    if current_index < 0 or current_index + 1 >= len(ordered):
        return None
    return ordered[current_index + 1]


def resource_balance(ledger: ResourceLedger, resource_key: str) -> int:
    """Return current balance for a resource key from signed ledger entries."""
    normalized_key = _normalize_lookup_key(resource_key)
    return sum(
        entry.amount
        for entry in ledger.entries
        if _normalize_lookup_key(entry.resource_key) == normalized_key
    )


def validate_realm_ladder(system: PowerSystem) -> ProgressionValidationReport:
    """Validate that a power system has a deterministic realm ladder."""
    findings: list[ProgressionFinding] = []
    if not system.realms:
        findings.append(_finding("MISSING_REALMS", f"Power system {system.name} has no realms."))
        return _report(findings)

    seen_orders: set[int] = set()
    seen_names: dict[str, str] = {}
    for realm in system.realms:
        if realm.order in seen_orders:
            findings.append(
                _finding(
                    "DUPLICATE_REALM_ORDER",
                    f"Realm order {realm.order} is reused in power system {system.name}.",
                ),
            )
        seen_orders.add(realm.order)

        seen_in_realm: set[str] = set()
        for candidate in _realm_candidates(realm):
            normalized_candidate = _normalize_realm_name(candidate)
            if normalized_candidate in seen_in_realm:
                continue
            owner = seen_names.get(normalized_candidate)
            if owner is not None and owner != realm.name:
                findings.append(
                    _finding(
                        "DUPLICATE_REALM_NAME",
                        f"Realm name or alias {candidate!r} is duplicated in {system.name}.",
                    ),
                )
            seen_names[normalized_candidate] = realm.name
            seen_in_realm.add(normalized_candidate)

    for bottleneck in system.bottlenecks:
        if realm_index(system, bottleneck.at_realm) < 0:
            findings.append(
                _finding(
                    "UNKNOWN_BOTTLENECK_SOURCE",
                    f"Bottleneck {bottleneck.key} references unknown source realm "
                    f"{bottleneck.at_realm}.",
                ),
            )
        if realm_index(system, bottleneck.target_realm) < 0:
            findings.append(
                _finding(
                    "UNKNOWN_BOTTLENECK_TARGET",
                    f"Bottleneck {bottleneck.key} references unknown target realm "
                    f"{bottleneck.target_realm}.",
                ),
            )

    return _report(findings)


def validate_resource_spend(
    ledger: ResourceLedger,
    resource_key: str,
    amount: int,
) -> ProgressionValidationReport:
    """Validate whether a resource spend is affordable."""
    if amount <= 0:
        return _report(
            [
                _finding(
                    "INVALID_RESOURCE_SPEND",
                    f"Resource spend amount must be positive for {resource_key}.",
                ),
            ],
        )

    balance = resource_balance(ledger, resource_key)
    if balance < amount:
        return _report(
            [
                _finding(
                    "INSUFFICIENT_RESOURCE",
                    f"{ledger.owner} only has {balance} x {resource_key}, cannot spend {amount}.",
                ),
            ],
        )
    return _report([])


def validate_technique_use(
    system: PowerSystem,
    technique: Technique,
    current_realm: str,
) -> ProgressionValidationReport:
    """Validate whether a technique is available at the current realm."""
    if technique.required_realm is None:
        return _report([])

    current_index = realm_index(system, current_realm)
    required_index = realm_index(system, technique.required_realm)
    if current_index < 0:
        return _report(
            [
                _finding(
                    "UNKNOWN_CURRENT_REALM",
                    f"Current realm {current_realm} is not in power system {system.name}.",
                ),
            ],
        )
    if required_index < 0:
        return _report(
            [
                _finding(
                    "UNKNOWN_TECHNIQUE_REQUIREMENT",
                    f"Technique {technique.name} requires unknown realm "
                    f"{technique.required_realm}.",
                ),
            ],
        )
    if current_index < required_index:
        return _report(
            [
                _finding(
                    "TECHNIQUE_PREREQUISITE_UNMET",
                    f"{technique.name} requires {technique.required_realm}; current realm is "
                    f"{current_realm}.",
                ),
            ],
        )
    return _report([])


def validate_artifact_capability(
    artifact: Artifact,
    capability: str,
) -> ProgressionValidationReport:
    """Validate that an artifact is active and exposes a requested capability."""
    findings: list[ProgressionFinding] = []
    if not artifact.active:
        findings.append(_finding("ARTIFACT_INACTIVE", f"Artifact {artifact.name} is inactive."))

    normalized_capabilities = {_normalize_lookup_key(item) for item in artifact.capabilities}
    if _normalize_lookup_key(capability) not in normalized_capabilities:
        findings.append(
            _finding(
                "ARTIFACT_CAPABILITY_MISSING",
                f"Artifact {artifact.name} does not expose capability {capability}.",
            ),
        )
    return _report(findings)


def _find_technique(techniques: Sequence[Technique], raw_key: str | None) -> Technique | None:
    for technique in techniques:
        if _matches_ref(raw_key, technique.key, technique.name):
            return technique
    return None


def _find_artifact(artifacts: Sequence[Artifact], raw_key: str | None) -> Artifact | None:
    for artifact in artifacts:
        if _matches_ref(raw_key, artifact.key, artifact.name):
            return artifact
    return None


def _validate_resource_cause(
    event: BreakthroughEvent,
    ledger: ResourceLedger | None,
    ref_key: str | None,
) -> list[ProgressionFinding]:
    if not ref_key:
        return [
            _finding(
                "MISSING_RESOURCE_REF",
                f"{event.character_name}'s breakthrough lacks a referenced resource.",
            ),
        ]
    if ledger is None:
        return [
            _finding(
                "RESOURCE_CAUSE_UNAVAILABLE",
                f"{event.character_name}'s breakthrough cites {ref_key}, "
                "but no ledger was provided.",
            ),
        ]
    if resource_balance(ledger, ref_key) <= 0:
        return [
            _finding(
                "RESOURCE_CAUSE_UNAVAILABLE",
                f"{event.character_name} has no available {ref_key} for this breakthrough.",
            ),
        ]
    return []


def _validate_technique_cause(
    system: PowerSystem,
    event: BreakthroughEvent,
    techniques: Sequence[Technique],
    ref_key: str | None,
) -> list[ProgressionFinding]:
    if not ref_key:
        return [
            _finding(
                "MISSING_TECHNIQUE_REF",
                f"{event.character_name}'s breakthrough lacks a referenced technique.",
            ),
        ]

    technique = _find_technique(techniques, ref_key)
    if technique is None:
        return [
            _finding(
                "UNKNOWN_TECHNIQUE",
                f"{event.character_name}'s breakthrough cites unknown technique {ref_key}.",
            ),
        ]

    findings = list(validate_technique_use(system, technique, event.from_realm).findings)
    if technique.unlocks_realms:
        target_allowed = any(
            _realm_matches(PowerRealm(name=realm, order=0), event.to_realm)
            for realm in technique.unlocks_realms
        )
        if not target_allowed:
            findings.append(
                _finding(
                    "TECHNIQUE_DOES_NOT_UNLOCK_REALM",
                    f"Technique {technique.name} does not unlock target realm {event.to_realm}.",
                ),
            )
    return findings


def _validate_artifact_cause(
    event: BreakthroughEvent,
    artifacts: Sequence[Artifact],
    ref_key: str | None,
) -> list[ProgressionFinding]:
    if not ref_key:
        return [
            _finding(
                "MISSING_ARTIFACT_REF",
                f"{event.character_name}'s breakthrough lacks a referenced artifact.",
            ),
        ]

    artifact = _find_artifact(artifacts, ref_key)
    if artifact is None:
        return [
            _finding(
                "UNKNOWN_ARTIFACT",
                f"{event.character_name}'s breakthrough cites unknown artifact {ref_key}.",
            ),
        ]

    findings: list[ProgressionFinding] = []
    if not artifact.active:
        findings.append(_finding("ARTIFACT_INACTIVE", f"Artifact {artifact.name} is inactive."))
    if artifact.unlocks_realms:
        target_allowed = any(
            _realm_matches(PowerRealm(name=realm, order=0), event.to_realm)
            for realm in artifact.unlocks_realms
        )
        if not target_allowed:
            findings.append(
                _finding(
                    "ARTIFACT_DOES_NOT_UNLOCK_REALM",
                    f"Artifact {artifact.name} does not unlock target realm {event.to_realm}.",
                ),
            )
    return findings


def _bottleneck_findings(
    system: PowerSystem,
    event: BreakthroughEvent,
    ledger: ResourceLedger | None,
) -> list[ProgressionFinding]:
    findings: list[ProgressionFinding] = []
    event_cause_kinds = {cause.kind for cause in event.causes}
    for bottleneck in system.bottlenecks:
        if not _realm_matches(PowerRealm(name=bottleneck.at_realm, order=0), event.from_realm):
            continue
        if not _realm_matches(PowerRealm(name=bottleneck.target_realm, order=0), event.to_realm):
            continue

        for required_kind in bottleneck.required_cause_kinds:
            if required_kind not in event_cause_kinds:
                findings.append(
                    _finding(
                        "MISSING_BOTTLENECK_CAUSE",
                        f"Breakthrough {event.from_realm}->{event.to_realm} requires "
                        f"{required_kind.value} because of bottleneck {bottleneck.key}.",
                    ),
                )

        for resource_key in bottleneck.required_resource_keys:
            if ledger is None or resource_balance(ledger, resource_key) <= 0:
                findings.append(
                    _finding(
                        "MISSING_BOTTLENECK_RESOURCE",
                        f"Breakthrough {event.from_realm}->{event.to_realm} requires "
                        f"resource {resource_key} because of bottleneck {bottleneck.key}.",
                    ),
                )
    return findings


def validate_breakthrough(
    system: PowerSystem,
    event: BreakthroughEvent,
    *,
    resource_ledger: ResourceLedger | None = None,
    techniques: Sequence[Technique] = (),
    artifacts: Sequence[Artifact] = (),
) -> ProgressionValidationReport:
    """Validate that a breakthrough is earned by prior mechanics."""
    findings = list(validate_realm_ladder(system).findings)

    from_index = realm_index(system, event.from_realm)
    to_index = realm_index(system, event.to_realm)
    if from_index < 0:
        findings.append(
            _finding(
                "UNKNOWN_SOURCE_REALM",
                f"Source realm {event.from_realm} is not in power system {system.name}.",
            ),
        )
    if to_index < 0:
        findings.append(
            _finding(
                "UNKNOWN_TARGET_REALM",
                f"Target realm {event.to_realm} is not in power system {system.name}.",
            ),
        )
    if from_index >= 0 and to_index >= 0 and to_index <= from_index:
        findings.append(
            _finding(
                "INVALID_REALM_TRANSITION",
                f"{event.character_name} cannot break through from {event.from_realm} to "
                f"{event.to_realm}.",
            ),
        )

    if not event.causes:
        findings.append(
            _finding(
                "UNEARNED_BREAKTHROUGH",
                f"{event.character_name}'s breakthrough from {event.from_realm} to "
                f"{event.to_realm} has no causal support.",
            ),
        )
        return _report(findings)

    findings.extend(_bottleneck_findings(system, event, resource_ledger))

    for cause in event.causes:
        if cause.kind is BreakthroughCauseKind.RESOURCE:
            findings.extend(_validate_resource_cause(event, resource_ledger, cause.ref_key))
        elif cause.kind is BreakthroughCauseKind.TECHNIQUE:
            findings.extend(_validate_technique_cause(system, event, techniques, cause.ref_key))
        elif cause.kind is BreakthroughCauseKind.ARTIFACT:
            findings.extend(_validate_artifact_cause(event, artifacts, cause.ref_key))

    return _report(findings)


def _power_system_extras(power_system: PowerSystemInput) -> dict[str, object]:
    return _extra_mapping(power_system)


def _parse_cause_kinds(value: object) -> tuple[BreakthroughCauseKind, ...]:
    kinds: list[BreakthroughCauseKind] = []
    for item in _as_sequence(value):
        raw = _as_string(item)
        if raw is None:
            continue
        try:
            kind = BreakthroughCauseKind(raw)
        except ValueError:
            continue
        if kind not in kinds:
            kinds.append(kind)
    return tuple(kinds)


def _parse_bottlenecks(power_system: PowerSystemInput) -> tuple[ProgressionBottleneck, ...]:
    extras = _power_system_extras(power_system)
    raw_items = extras.get("bottlenecks") or extras.get("progression_bottlenecks")
    bottlenecks: list[ProgressionBottleneck] = []
    for index, raw in enumerate(_as_sequence(raw_items)):
        item = _as_mapping(raw)
        at_realm = _as_string(item.get("at_realm") or item.get("from_realm"))
        target_realm = _as_string(item.get("target_realm") or item.get("to_realm"))
        description = _as_string(
            item.get("description") or item.get("rule") or item.get("reason"),
        )
        if at_realm is None or target_realm is None or description is None:
            continue
        key = _as_string(item.get("key") or item.get("id")) or f"bottleneck_{index + 1}"
        required_resource_keys = _string_tuple(item.get("resources"))
        bottlenecks.append(
            ProgressionBottleneck(
                key=key,
                at_realm=at_realm,
                target_realm=target_realm,
                description=description,
                required_cause_kinds=_parse_cause_kinds(item.get("required_cause_kinds")),
                required_resource_keys=required_resource_keys,
                severity=_bottleneck_severity(item.get("severity")),
            ),
        )
    return tuple(bottlenecks)


def materialize_power_system(world_spec: WorldSpecInput | dict[str, object]) -> PowerSystem:
    """Build a first-class progression system from story-bible world spec."""
    spec = _coerce_world_spec(world_spec)
    raw_system = spec.power_system
    system_name = raw_system.name or "Progression System"
    realms = tuple(
        PowerRealm(name=tier, order=index)
        for index, tier in enumerate(raw_system.tiers)
        if tier and tier.strip()
    )
    notes = tuple(
        note
        for note in (
            raw_system.acquisition_method,
            raw_system.hard_limits,
            spec.power_structure,
        )
        if note
    )
    return PowerSystem(
        key=_normalize_lookup_key(system_name).replace(" ", "_") or "progression",
        name=system_name,
        realms=realms,
        bottlenecks=_parse_bottlenecks(raw_system),
        terminology_notes=notes,
    )


def _parse_techniques(raw_items: object) -> tuple[Technique, ...]:
    techniques: list[Technique] = []
    for raw in _as_sequence(raw_items):
        if isinstance(raw, str):
            techniques.append(Technique(key=raw, name=raw))
            continue
        item = _as_mapping(raw)
        key = _as_string(item.get("key") or item.get("id") or item.get("name"))
        name = _as_string(item.get("name") or key)
        if key is None or name is None:
            continue
        costs = {
            str(cost_key): _as_positive_int(cost_value)
            for cost_key, cost_value in _as_mapping(item.get("costs")).items()
        }
        techniques.append(
            Technique(
                key=key,
                name=name,
                required_realm=_as_string(item.get("required_realm")),
                unlocks_realms=_string_tuple(item.get("unlocks_realms")),
                costs=costs,
                limitation=_as_string(item.get("limitation") or item.get("known_limit")),
            ),
        )
    return tuple(techniques)


def _parse_artifacts(raw_items: object) -> tuple[Artifact, ...]:
    artifacts: list[Artifact] = []
    for raw in _as_sequence(raw_items):
        if isinstance(raw, str):
            artifacts.append(Artifact(key=raw, name=raw))
            continue
        item = _as_mapping(raw)
        key = _as_string(item.get("key") or item.get("id") or item.get("name"))
        name = _as_string(item.get("name") or key)
        if key is None or name is None:
            continue
        artifacts.append(
            Artifact(
                key=key,
                name=name,
                active=bool(item.get("active", True)),
                capabilities=_string_tuple(item.get("capabilities")),
                unlocks_realms=_string_tuple(item.get("unlocks_realms")),
                known_limit=_as_string(item.get("known_limit") or item.get("limitation")),
            ),
        )
    return tuple(artifacts)


def _character_extra_list(character: CharacterInput, key: str) -> list[object]:
    extras = _extra_mapping(character)
    values = list(_as_sequence(extras.get(key)))
    metadata = character.metadata or {}
    values.extend(_as_sequence(metadata.get(key)))
    return values


def _parse_resource_ledger(character: CharacterInput) -> ResourceLedger:
    raw_items: list[object] = []
    raw_items.extend(_character_extra_list(character, "resources"))
    raw_items.extend(_character_extra_list(character, "resource_ledger"))
    entries: list[ResourceLedgerEntry] = []
    for index, raw in enumerate(raw_items):
        if isinstance(raw, str):
            entries.append(
                ResourceLedgerEntry(
                    resource_key=raw,
                    amount=1,
                    chapter_no=0,
                    source="story_bible",
                ),
            )
            continue
        item = _as_mapping(raw)
        resource_key = _as_string(
            item.get("resource_key") or item.get("key") or item.get("name"),
        )
        if resource_key is None:
            continue
        entries.append(
            ResourceLedgerEntry(
                resource_key=resource_key,
                amount=_as_positive_int(item.get("amount"), default=1),
                chapter_no=_as_positive_int(item.get("chapter_no"), default=0),
                source=(
                    _as_string(item.get("source"))
                    or f"story_bible_resource_{index + 1}"
                ),
                reason=_as_string(item.get("reason")),
            ),
        )
    return ResourceLedger(owner=character.name, entries=tuple(entries))


def _character_techniques(character: CharacterInput) -> tuple[Technique, ...]:
    return _parse_techniques(_character_extra_list(character, "techniques"))


def _character_artifacts(character: CharacterInput) -> tuple[Artifact, ...]:
    return _parse_artifacts(_character_extra_list(character, "artifacts"))


def _current_protagonist_realm(
    world_power_system: PowerSystemInput,
    cast_spec: CastSpecInput | None,
    volume_plan: Sequence[VolumePlanEntryInput],
    current_volume: int | None,
) -> tuple[str | None, str | None]:
    protagonist_name = (
        cast_spec.protagonist.name if cast_spec and cast_spec.protagonist else None
    )
    realm = (
        cast_spec.protagonist.power_tier
        if cast_spec and cast_spec.protagonist and cast_spec.protagonist.power_tier
        else world_power_system.protagonist_starting_tier
    )

    if current_volume is None:
        return protagonist_name, realm

    for entry in sorted(volume_plan, key=lambda item: item.volume_number):
        if entry.volume_number < current_volume and entry.volume_resolution.protagonist_power_tier:
            realm = entry.volume_resolution.protagonist_power_tier
        if entry.volume_number == current_volume:
            realm = entry.opening_state.protagonist_power_tier or realm
            break
    return protagonist_name, realm


def _active_bottleneck(
    system: PowerSystem,
    protagonist_realm: str | None,
) -> ProgressionBottleneck | None:
    if protagonist_realm is None:
        return None
    next_realm = _next_realm(system, protagonist_realm)
    for bottleneck in system.bottlenecks:
        if not _realm_matches(PowerRealm(name=bottleneck.at_realm, order=0), protagonist_realm):
            continue
        if next_realm is None or _realm_matches(
            PowerRealm(name=bottleneck.target_realm, order=0),
            next_realm.name,
        ):
            return bottleneck
    if next_realm is None:
        return None
    return ProgressionBottleneck(
        key=f"auto_{realm_index(system, protagonist_realm)}_{next_realm.order}",
        at_realm=protagonist_realm,
        target_realm=next_realm.name,
        description=(
            f"Breakthrough from {protagonist_realm} to {next_realm.name} "
            "requires explicit causal support."
        ),
        severity="soft",
    )


def materialize_progression_context(
    world_spec: WorldSpecInput | dict[str, object],
    cast_spec: CastSpecInput | dict[str, object] | None = None,
    volume_plan: Sequence[VolumePlanEntryInput | dict[str, object]] | None = None,
    *,
    current_volume: int | None = None,
) -> ProgressionContext:
    """Extract current progression state from story-bible planning artifacts."""
    world = _coerce_world_spec(world_spec)
    cast = _coerce_cast_spec(cast_spec)
    volumes = _coerce_volume_plan(volume_plan)
    system = materialize_power_system(world)

    character_realms: dict[str, str] = {}
    resource_ledgers: dict[str, ResourceLedger] = {}
    power_system_extras = _power_system_extras(world.power_system)
    techniques = list(_parse_techniques(power_system_extras.get("techniques")))
    artifacts = list(_parse_artifacts(power_system_extras.get("artifacts")))

    if cast is not None:
        for character in cast.all_characters():
            if character.power_tier:
                character_realms[character.name] = character.power_tier
            ledger = _parse_resource_ledger(character)
            if ledger.entries:
                resource_ledgers[character.name] = ledger
            techniques.extend(_character_techniques(character))
            artifacts.extend(_character_artifacts(character))

    protagonist_name, protagonist_realm = _current_protagonist_realm(
        world.power_system,
        cast,
        volumes,
        current_volume,
    )
    if protagonist_name is not None and protagonist_realm is not None:
        character_realms[protagonist_name] = protagonist_realm

    return ProgressionContext(
        system=system,
        character_realms=character_realms,
        resource_ledgers=resource_ledgers,
        techniques=tuple(techniques),
        artifacts=tuple(artifacts),
        active_bottleneck=_active_bottleneck(system, protagonist_realm),
    )


def build_progression_constraint_block(
    system: PowerSystem,
    character_realms: dict[str, str],
    *,
    language: str = "zh-CN",
) -> str:
    """Render concise progression constraints for writer prompts."""
    if not system.realms or not character_realms:
        return ""

    is_zh = language.lower().startswith("zh")
    ladder = " → ".join(realm.name for realm in system.ordered_realms)
    lines = ["【进阶体系约束】" if is_zh else "[PROGRESSION CONSTRAINTS]"]
    if is_zh:
        lines.append(f"体系: {system.name}")
        lines.append(f"境界阶梯: {ladder}")
        for character_name, realm_name in character_realms.items():
            lines.append(
                f"• {character_name}: 当前「{realm_name}\", "
                "突破必须有资源/功法/顿悟/试炼等因果支撑",
            )
    else:
        lines.append(f"System: {system.name}")
        lines.append(f"Realm ladder: {ladder}")
        for character_name, realm_name in character_realms.items():
            lines.append(
                f"- {character_name}: current '{realm_name}', breakthroughs require explicit "
                "resource/technique/insight/trial causes",
            )
    return "\n".join(lines)


def build_progression_context_block(
    context: ProgressionContext,
    *,
    language: str = "zh-CN",
) -> str:
    """Render progression state for writer prompts."""
    if not context.system.realms and not context.character_realms:
        return ""

    is_zh = language.lower().startswith("zh")
    ladder = " → ".join(realm.name for realm in context.system.ordered_realms)
    lines = ["【进阶体系约束】" if is_zh else "[PROGRESSION CONTEXT]"]

    if is_zh:
        lines.append(f"体系: {context.system.name}")
        if ladder:
            lines.append(f"境界阶梯: {ladder}")
        for character_name, realm_name in context.character_realms.items():
            lines.append(f"• {character_name}: 当前「{realm_name}」")
        if context.active_bottleneck is not None:
            bottleneck = context.active_bottleneck
            lines.append(
                f"当前瓶颈: {bottleneck.at_realm} -> {bottleneck.target_realm}; "
                f"{bottleneck.description}",
            )
        for owner, ledger in context.resource_ledgers.items():
            balances = [
                f"{entry.resource_key}={resource_balance(ledger, entry.resource_key)}"
                for entry in ledger.entries
            ]
            if balances:
                lines.append(f"资源账本/{owner}: " + ", ".join(sorted(set(balances))))
        if context.techniques:
            lines.append(
                "可用功法: "
                + ", ".join(
                    f"{technique.name}"
                    + (f"(需{technique.required_realm})" if technique.required_realm else "")
                    for technique in context.techniques
                ),
            )
        if context.artifacts:
            lines.append(
                "可用法宝: "
                + ", ".join(
                    f"{artifact.name}"
                    + (f"(上限:{artifact.known_limit})" if artifact.known_limit else "")
                    for artifact in context.artifacts
                    if artifact.active
                ),
            )
        lines.append("硬规则: 突破必须引用资源/功法/顿悟/试炼/法宝等明确因果, 不得空升级。")
    else:
        lines.append(f"System: {context.system.name}")
        if ladder:
            lines.append(f"Realm ladder: {ladder}")
        for character_name, realm_name in context.character_realms.items():
            lines.append(f"- {character_name}: current '{realm_name}'")
        if context.active_bottleneck is not None:
            bottleneck = context.active_bottleneck
            lines.append(
                f"Active bottleneck: {bottleneck.at_realm} -> {bottleneck.target_realm}; "
                f"{bottleneck.description}",
            )
        lines.append(
            "Hard rule: breakthroughs must cite explicit resource, technique, insight, "
            "trial, artifact, or equivalent causal support.",
        )
    return "\n".join(lines)
