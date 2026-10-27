"""Validate source-bound outlines without manufacturing story content.

Only exact declared participant aliases can change. Models are constructed as
validation views, never serialized back over authored/paid JSON. No IO or LLM.
"""

# ruff: noqa: ANN001, ANN201, ANN202 -- Strict JSON/model boundary adapters.
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from typing import Annotated

from pydantic import TypeAdapter, ValidationError

from bestseller.domain.workflow import (
    ChapterOutlineBatchInput,
    ChapterOutlineInput,
    SceneOutlineInput,
)
from bestseller.services.narrative_contracts import validate_chapter_plan_contract

POLICY = "source-bound-outline-integrity.v1"


def _json(value):
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def _hash(value):
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


class SourceBoundOutlineIntegrityError(ValueError):
    def __init__(self, report) -> None:
        self.report = deepcopy(report)
        super().__init__("source_bound_outline_integrity_invalid")


def _violation(code, path, message, **metadata: object):
    return {
        "code": code,
        "location": path,
        "message": message,
        "severity": "critical",
        "metadata": metadata,
    }


def _report(violations, warnings=(), receipt=None):
    return {
        "gate_name": POLICY,
        "passed": not violations,
        "violations": deepcopy(violations),
        "warnings": deepcopy(list(warnings)),
        "source_integrity_receipt": deepcopy(receipt),
    }


def _keys(name, field):
    alias = field.validation_alias
    choices = alias.choices if hasattr(alias, "choices") else [alias]
    return list(
        dict.fromkeys(
            [
                name,
                *[x for x in choices if isinstance(x, str)],
                *([field.serialization_alias] if field.serialization_alias else []),
            ]
        )
    )


def _view(model, raw, path, violations):
    """Bypass coercive before validators; validate each supplied field strictly."""
    if type(raw) is not dict:
        violations.append(
            _violation("PLAN_SOURCE_OBJECT_REQUIRED", path, "Expected a JSON object.")
        )
        return None
    values = {}
    required = {
        ChapterOutlineBatchInput: {"batch_name", "chapters"},
        ChapterOutlineInput: {
            "chapter_number",
            "volume_number",
            "title",
            "chapter_goal",
            "opening_situation",
            "main_conflict",
            "hook_description",
            "causal_contract",
            "scenes",
            "target_word_count",
        },
        SceneOutlineInput: {
            "scene_number",
            "scene_type",
            "time_label",
            "participants",
            "purpose",
            "entry_state",
            "exit_state",
            "target_word_count",
        },
    }[model]
    for name, field in model.model_fields.items():
        present = [key for key in _keys(name, field) if key in raw]
        if not present:
            if field.is_required() or name in required:
                violations.append(
                    _violation(
                        "PLAN_SOURCE_FIELD_MISSING",
                        path + "/" + name,
                        "Required source field is absent; "
                        "no default or derived value is authorized.",
                        accepted_keys=_keys(name, field),
                    )
                )
            continue
        if len({_json(raw[key]) for key in present}) != 1:
            violations.append(
                _violation(
                    "PLAN_SOURCE_ALIAS_CONFLICT",
                    path + "/" + name,
                    "Supplied structural aliases disagree; preserve both for explicit repair.",
                    keys=present,
                )
            )
            continue
        value, key = deepcopy(raw[present[0]]), present[0]
        location = path + "/" + key
        if name in {"chapters", "scenes"}:
            if type(value) is not list:
                violations.append(
                    _violation("PLAN_SOURCE_FIELD_TYPE", location, "Expected an array.")
                )
                continue
            nested = ChapterOutlineInput if name == "chapters" else SceneOutlineInput
            values[name] = [
                _view(nested, item, f"{location}/{i}", violations) for i, item in enumerate(value)
            ]
            continue
        try:
            annotation = (
                Annotated[field.annotation, *field.metadata] if field.metadata else field.annotation
            )
            values[name] = TypeAdapter(annotation).validate_python(value, strict=True)
        except ValidationError as exc:
            violations.extend(
                _violation(
                    "PLAN_SOURCE_FIELD_TYPE",
                    location,
                    error["msg"],
                    type_error=error["type"],
                    field_location=list(error["loc"]),
                )
                for error in exc.errors()
            )
    return model.model_construct(**values)


def prepare_source_bound_outline(
    payload,
    *,
    volume_number,
    expected_count,
    chapter_number_offset,
    identity_manifest,
    existing_titles=(),
):
    """Return full projected JSON, a non-authoritative validation view and receipt.

    Optional omitted fields remain absent in JSON. Required fields below match
    current executable consumers, not all legacy optional/defaulted fields.
    """
    try:
        _json(payload).encode("utf-8")
        _json(identity_manifest).encode("utf-8")
    except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
        raise SourceBoundOutlineIntegrityError(
            _report(
                [
                    _violation(
                        "PLAN_SOURCE_JSON_INVALID",
                        "",
                        "Source must be full finite JSON, without coercion.",
                    )
                ]
            )
        ) from exc
    original, result = deepcopy(payload), deepcopy(payload)
    violations, aliases = [], []
    manifest = {}
    for identity in identity_manifest:
        if type(identity) is not dict or type(identity.get("name")) is not str:
            violations.append(
                _violation(
                    "PLAN_IDENTITY_REGISTRY_INVALID",
                    "identity_manifest",
                    "Canonical identities must have explicit string names.",
                )
            )
            continue
        name = identity["name"]
        declared = identity.get("aliases", [])
        if type(declared) is not list:
            violations.append(
                _violation(
                    "PLAN_IDENTITY_REGISTRY_INVALID",
                    "identity_manifest",
                    "Aliases must be an explicit array of exact strings.",
                )
            )
            continue
        for token in [name, *declared]:
            if type(token) is not str or not token:
                violations.append(
                    _violation(
                        "PLAN_IDENTITY_REGISTRY_INVALID",
                        "identity_manifest",
                        "Identity tokens must be exact nonempty strings.",
                    )
                )
            elif token in manifest and manifest[token] != name:
                violations.append(
                    _violation(
                        "PLAN_IDENTITY_ALIAS_AMBIGUOUS",
                        "identity_manifest",
                        "An exact token names multiple canonical identities.",
                        token=token,
                    )
                )
            else:
                manifest[token] = name
    if not manifest:
        violations.append(
            _violation(
                "PLAN_IDENTITY_REGISTRY_MISSING",
                "identity_manifest",
                "A positive locked identity manifest is required.",
            )
        )
    chapters = result.get("chapters") if type(result) is dict else None
    if type(chapters) is list:
        if len(chapters) != expected_count:
            violations.append(
                _violation(
                    "PLAN_SOURCE_CHAPTER_COUNT",
                    "/chapters",
                    "Exact requested chapter count is required; no padding or cropping.",
                    expected=expected_count,
                    actual=len(chapters),
                )
            )
        for i, chapter in enumerate(chapters):
            if type(chapter) is not dict:
                continue
            if (
                type(chapter.get("chapter_number")) is not int
                or chapter["chapter_number"] != chapter_number_offset + i
            ):
                violations.append(
                    _violation(
                        "PLAN_SOURCE_CHAPTER_NUMBER",
                        f"/chapters/{i}/chapter_number",
                        "Chapter numbers must already match the requested contiguous scope.",
                        expected=chapter_number_offset + i,
                    )
                )
            if (
                type(chapter.get("volume_number")) is not int
                or chapter["volume_number"] != volume_number
            ):
                violations.append(
                    _violation(
                        "PLAN_SOURCE_VOLUME_NUMBER",
                        f"/chapters/{i}/volume_number",
                        "Volume number must already match the requested scope.",
                        expected=volume_number,
                    )
                )
            for name in ("title", "opening_situation"):
                keys = _keys(name, ChapterOutlineInput.model_fields[name])
                field = next((key for key in keys if key in chapter), name)
                if type(chapter.get(field)) is not str or not chapter[field].strip():
                    violations.append(
                        _violation(
                            "PLAN_SOURCE_FIELD_EMPTY",
                            f"/chapters/{i}/{field}",
                            "Missing story text requires explicit repair, "
                            "never a synthesized fallback.",
                        )
                    )
            goal = chapter.get("chapter_goal", chapter.get("goal"))
            if type(goal) is not str or not goal.strip():
                violations.append(
                    _violation(
                        "PLAN_CHAPTER_GOAL_MISSING",
                        f"/chapters/{i}/goal",
                        "A concrete supplied goal is required; no default story goal.",
                    )
                )
            if not isinstance(chapter.get("causal_contract"), dict) or not any(
                chapter.get("causal_contract", {}).values()
            ):
                violations.append(
                    _violation(
                        "PLAN_SOURCE_CAUSAL_CONTRACT_MISSING",
                        f"/chapters/{i}/causal_contract",
                        "A supplied causal contract is required; no inferred causes or costs.",
                    )
                )
            for j, scene in enumerate(
                chapter.get("scenes", []) if type(chapter.get("scenes")) is list else []
            ):
                if type(scene) is not dict:
                    continue
                for state in ("entry_state", "exit_state"):
                    if type(scene.get(state)) is not dict or not scene[state]:
                        violations.append(
                            _violation(
                                "PLAN_SOURCE_STATE_MISSING",
                                f"/chapters/{i}/scenes/{j}/{state}",
                                "Full supplied state is required; no inference from another field.",
                            )
                        )
                if type(scene.get("participants")) is not list:
                    continue
                for k, token in enumerate(scene["participants"]):
                    path = f"/chapters/{i}/scenes/{j}/participants/{k}"
                    if type(token) is not str or token not in manifest:
                        violations.append(
                            _violation(
                                "PLAN_SCENE_UNKNOWN_PARTICIPANT",
                                path,
                                "Use only an exact declared canonical name or plain alias.",
                                participant=token,
                            )
                        )
                    elif token != manifest[token]:
                        canonical = manifest[token]
                        scene["participants"][k] = canonical
                        aliases.append(
                            {
                                "path": path,
                                "before": token,
                                "after": canonical,
                                "before_sha256": _hash(token),
                                "after_sha256": _hash(canonical),
                            }
                        )
    batch = _view(ChapterOutlineBatchInput, result, "", violations)
    receipt = {
        "schema": POLICY,
        "original_sha256": _hash(original),
        "hash_encoding": "canonical-json-utf8",
        "projected_sha256": _hash(result),
        "identity_manifest_sha256": _hash(identity_manifest),
        "aliases": aliases,
        "story_values_preserved": True,
        "defaults_inserted": False,
        "semantic_repair_performed": False,
        "author_authority": False,
        "grade_asserted": False,
    }
    receipt["receipt_sha256"] = _hash(receipt)
    if violations:
        raise SourceBoundOutlineIntegrityError(_report(violations, receipt=receipt))
    actual = validate_chapter_plan_contract(
        batch, identity_manifest=identity_manifest, require_identity_registry=True
    )
    if actual.blocks:
        report = actual.to_dict()
        report["source_integrity_receipt"] = receipt
        raise SourceBoundOutlineIntegrityError(report)
    # Title collision validation is pure; source-bound never invokes title rescue.
    from bestseller.services.title_dedup import find_title_collisions

    collision = find_title_collisions(
        [(ch.chapter_number, ch.title) for ch in batch.chapters],
        existing_titles=existing_titles or (),
    )
    if not collision.ok:
        positions = {ch.chapter_number: j for j, ch in enumerate(batch.chapters)}
        raise SourceBoundOutlineIntegrityError(
            _report(
                [
                    _violation(
                        "PLAN_SOURCE_TITLE_COLLISION",
                        f"/chapters/{positions[item.chapter_number]}/title",
                        "Title collision requires explicit repair.",
                        chapter_number=item.chapter_number,
                        title=item.candidate_title,
                        conflict_title=item.conflict_title,
                    )
                    for item in collision.collisions
                ],
                receipt=receipt,
            )
        )
    return result, batch, receipt


def verify_validation_view(batch, before):
    """A remaining legacy checker must not validate a repaired surrogate view."""
    if _json(batch.model_dump(mode="json")) != _json(before):
        raise SourceBoundOutlineIntegrityError(
            _report(
                [
                    _violation(
                        "PLAN_SOURCE_VALIDATOR_MUTATION",
                        "",
                        "A validator altered its view; explicit repair required.",
                    )
                ]
            )
        )
