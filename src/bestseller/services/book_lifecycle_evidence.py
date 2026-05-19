from __future__ import annotations

from collections.abc import Mapping, Sequence
import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.infra.db.models import CharacterModel, ProjectModel


def _as_mapping(value: object | None) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _as_list(value: object | None) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _string(value: object | None) -> str:
    return str(value or "").strip()


def _identity_token(value: object | None) -> str:
    return "".join(_string(value).lower().split())


def _number(value: object | None) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _range_end(value: object | None) -> int:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        numbers = [_number(item) for item in value]
        return max(numbers, default=0)
    text = _string(value)
    if not text:
        return 0
    numbers = [int(match) for match in re.findall(r"\d+", text)]
    return max(numbers, default=0)


def _volume_plan_metrics(plan: object) -> dict[str, object]:
    volumes = [item for item in _as_list(plan) if isinstance(item, Mapping)]
    max_range_end = 0
    chapter_count_target_sum = 0
    conflict_phases: set[str] = set()
    primary_forces: set[str] = set()
    payoff_count = 0
    hook_count = 0
    for volume in volumes:
        max_range_end = max(
            max_range_end,
            _range_end(
                volume.get("chapter_range")
                or volume.get("chapters")
                or volume.get("range")
            ),
        )
        chapter_count_target_sum += _number(
            volume.get("chapter_count_target") or volume.get("chapter_count")
        )
        phase = _string(volume.get("conflict_phase"))
        if phase:
            conflict_phases.add(phase)
        force = _string(volume.get("primary_force_name"))
        if force:
            primary_forces.add(force)
        if _string(volume.get("core_payoff") or volume.get("volume_climax")):
            payoff_count += 1
        if _string(volume.get("reader_hook_to_next")):
            hook_count += 1
    planned_chapters = max(max_range_end, chapter_count_target_sum)
    return {
        "volume_count": len(volumes),
        "planned_chapters": planned_chapters,
        "conflict_phase_count": len(conflict_phases),
        "primary_force_count": len(primary_forces),
        "payoff_count": payoff_count,
        "hook_count": hook_count,
    }


def _identity_manifest_rows(metadata: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [item for item in _as_list(metadata.get("identity_manifest")) if isinstance(item, dict)]


def _normalize_identity_manifest_aliases(
    manifest: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for entry in manifest:
        token = _identity_token(entry.get("name"))
        if not token:
            continue
        if token not in merged:
            merged[token] = dict(entry)
            order.append(token)
            continue
        target = merged[token]
        target["aliases"] = [
            *_as_list(target.get("aliases")),
            *_as_list(entry.get("aliases")),
        ]
        for key, value in entry.items():
            if key == "aliases":
                continue
            if target.get(key) in (None, "", [], {}):
                target[key] = value
    manifest = [merged[token] for token in order]
    name_tokens = {
        _identity_token(entry.get("name"))
        for entry in manifest
        if _identity_token(entry.get("name"))
    }
    used_alias_tokens: set[str] = set()
    normalized: list[dict[str, Any]] = []
    for entry in manifest:
        next_entry = dict(entry)
        aliases: list[str] = []
        for alias in _as_list(entry.get("aliases")):
            token = _identity_token(alias)
            if not token or token in name_tokens or token in used_alias_tokens:
                continue
            used_alias_tokens.add(token)
            aliases.append(str(alias))
        next_entry["aliases"] = aliases
        normalized.append(next_entry)
    return normalized


def _identity_manifest_duplicate_count(manifest: list[dict[str, Any]]) -> int:
    seen: set[str] = set()
    duplicates = 0
    for entry in manifest:
        tokens = [
            _identity_token(entry.get("name")),
            *[
                _identity_token(alias)
                for alias in _as_list(entry.get("aliases"))
                if _identity_token(alias)
            ],
        ]
        for token in [item for item in tokens if item]:
            if token in seen:
                duplicates += 1
            seen.add(token)
    return duplicates


def _character_identity_fields(character: CharacterModel) -> tuple[str, str]:
    metadata = _as_mapping(getattr(character, "metadata_json", None))
    cast_entry = _as_mapping(metadata.get("cast_entry"))
    gender = _string(metadata.get("gender") or cast_entry.get("gender"))
    pronoun = _string(
        metadata.get("pronoun_set_zh")
        or metadata.get("pronoun_set_en")
        or cast_entry.get("pronoun_set_zh")
        or cast_entry.get("pronoun_set_en")
    )
    return gender, pronoun


def _character_has_personhood(character: CharacterModel) -> bool:
    metadata = _as_mapping(getattr(character, "metadata_json", None))
    return any(
        bool(value)
        for value in (
            getattr(character, "goal", None),
            getattr(character, "fear", None),
            getattr(character, "flaw", None),
            getattr(character, "strength", None),
            metadata.get("ip_anchor"),
            metadata.get("psych_profile"),
            metadata.get("independent_life"),
            metadata.get("tag_memory"),
            metadata.get("character_engine_profile"),
        )
    )


def _character_gate_report(
    *,
    metadata: Mapping[str, Any],
    characters: Sequence[CharacterModel],
    identity_manifest: list[dict[str, Any]],
) -> dict[str, object]:
    total_characters = len([character for character in characters if _string(character.name)])
    identity_covered = 0
    personhood_covered = 0
    missing_identity_names: list[str] = []
    for character in characters:
        if not _string(character.name):
            continue
        gender, pronoun = _character_identity_fields(character)
        if gender and pronoun:
            identity_covered += 1
        elif len(missing_identity_names) < 20:
            missing_identity_names.append(str(character.name))
        if _character_has_personhood(character):
            personhood_covered += 1

    identity_coverage = (
        round(identity_covered / total_characters, 4) if total_characters else 0.0
    )
    personhood_coverage = (
        round(personhood_covered / total_characters, 4) if total_characters else 0.0
    )
    duplicate_count = _identity_manifest_duplicate_count(identity_manifest)
    findings: list[dict[str, object]] = []
    if not identity_manifest:
        findings.append(
            {
                "code": "identity_manifest_missing",
                "severity": "critical",
                "path": "project.metadata.identity_manifest",
                "message": "Locked identity manifest is required before lifecycle writing.",
            }
        )
    if _string(metadata.get("identity_manifest_status")) != "locked":
        findings.append(
            {
                "code": "identity_manifest_not_locked",
                "severity": "critical",
                "path": "project.metadata.identity_manifest_status",
                "message": "Identity manifest must be locked before lifecycle writing.",
            }
        )
    if duplicate_count > 0:
        findings.append(
            {
                "code": "identity_manifest_duplicate_tokens",
                "severity": "critical",
                "path": "project.metadata.identity_manifest",
                "message": "Identity manifest contains duplicate names or aliases.",
                "actual": duplicate_count,
            }
        )
    if total_characters and identity_coverage < 0.90:
        findings.append(
            {
                "code": "character_identity_coverage_below_bar",
                "severity": "critical",
                "path": "characters.metadata.identity",
                "message": "Too many character rows lack frozen gender/pronoun identity data.",
                "expected": ">=0.90",
                "actual": identity_coverage,
            }
        )
    if total_characters and personhood_coverage < 0.60:
        findings.append(
            {
                "code": "character_personhood_coverage_below_bar",
                "severity": "high",
                "path": "characters.personhood",
                "message": "Too many character rows lack usable personhood anchors.",
                "expected": ">=0.60",
                "actual": personhood_coverage,
            }
        )
    if not isinstance(metadata.get("character_drama_map"), Mapping):
        findings.append(
            {
                "code": "character_drama_map_missing",
                "severity": "critical",
                "path": "project.metadata.character_drama_map",
                "message": "Character drama map is required for lifecycle writing.",
            }
        )
    if not isinstance(metadata.get("cast_spec"), Mapping) and not isinstance(
        metadata.get("premium_cast_spec"), Mapping
    ):
        findings.append(
            {
                "code": "cast_spec_missing",
                "severity": "critical",
                "path": "project.metadata.cast_spec",
                "message": "Cast spec or premium cast spec is required for lifecycle writing.",
            }
        )
    return {
        "passed": not any(item.get("severity") == "critical" for item in findings),
        "findings": findings,
        "metrics": {
            "total_characters": total_characters,
            "identity_covered_characters": identity_covered,
            "identity_registry_coverage": identity_coverage,
            "personhood_covered_characters": personhood_covered,
            "personhood_coverage": personhood_coverage,
            "identity_manifest_duplicate_count": duplicate_count,
            "missing_identity_character_names": missing_identity_names,
        },
    }


def build_book_lifecycle_evidence_from_project_state(
    project: ProjectModel,
    characters: Sequence[CharacterModel],
) -> dict[str, object]:
    metadata = _as_mapping(getattr(project, "metadata_json", None))
    volume_plan = metadata.get("premium_volume_plan") or metadata.get("volume_plan")
    volume_metrics = _volume_plan_metrics(volume_plan)
    identity_manifest = _normalize_identity_manifest_aliases(
        _identity_manifest_rows(metadata)
    )
    generated_character_gate = _character_gate_report(
        metadata=metadata,
        characters=characters,
        identity_manifest=identity_manifest,
    )
    anti_copy = _as_mapping(
        metadata.get("anti_copy_report")
        or metadata.get("sample_quality_parity")
        or metadata.get("sample_quality_parity_report")
    )
    planning_report = {
        "category": metadata.get("canonical_category") or metadata.get("category_key"),
        "target_chapters": getattr(project, "target_chapters", 0),
        "planned_chapters": volume_metrics["planned_chapters"],
        "volume_plan_metrics": volume_metrics,
        "story_design_kernel": metadata.get("story_design_kernel"),
        "planning_kernel": metadata.get("planning_kernel"),
        "emotion_driven_kernel": metadata.get("emotion_driven_kernel"),
        "reverse_outline_gate_report": metadata.get("reverse_outline_gate_report"),
        "prewrite_readiness_report": metadata.get("prewrite_readiness_report"),
    }
    character_report = {
        "identity_manifest_status": metadata.get("identity_manifest_status"),
        "identity_manifest": identity_manifest,
        "identity_manifest_count": len(identity_manifest),
        "character_gate_report": generated_character_gate,
        "character_drama_map_present": isinstance(
            metadata.get("character_drama_map"), Mapping
        ),
        "cast_spec_present": isinstance(metadata.get("cast_spec"), Mapping),
        "premium_cast_spec_present": isinstance(metadata.get("premium_cast_spec"), Mapping),
        "identity_registry_coverage": generated_character_gate["metrics"][
            "identity_registry_coverage"
        ],
        "personhood_coverage": generated_character_gate["metrics"][
            "personhood_coverage"
        ],
    }
    anti_copy_report = {
        **dict(anti_copy),
        "source_leak_count": anti_copy.get("source_leak_count", 0),
        "protected_phrase_leak_count": anti_copy.get("protected_phrase_leak_count", 0),
    }
    return {
        "slug": project.slug,
        "planning_report": planning_report,
        "character_report": character_report,
        "anti_copy_report": anti_copy_report,
        "metrics": {
            "target_chapters": getattr(project, "target_chapters", 0),
            "planned_chapters": volume_metrics["planned_chapters"],
            "identity_manifest_count": len(identity_manifest),
            "character_count": generated_character_gate["metrics"]["total_characters"],
            "identity_registry_coverage": generated_character_gate["metrics"][
                "identity_registry_coverage"
            ],
            "personhood_coverage": generated_character_gate["metrics"][
                "personhood_coverage"
            ],
        },
    }


async def build_book_lifecycle_evidence_payload(
    session: AsyncSession,
    project: ProjectModel,
) -> dict[str, object]:
    characters = list(
        (
            await session.execute(
                select(CharacterModel)
                .where(CharacterModel.project_id == project.id)
                .order_by(CharacterModel.name.asc())
            )
        ).scalars()
    )
    return build_book_lifecycle_evidence_from_project_state(project, characters)


__all__ = [
    "build_book_lifecycle_evidence_from_project_state",
    "build_book_lifecycle_evidence_payload",
]
