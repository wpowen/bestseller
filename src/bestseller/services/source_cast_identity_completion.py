"""Complete only unresolved secondary identity leaves in retained Cast parts.

The model supplies every added value. No gender is guessed from a name, role or
audience; no name/roster/history is edited. The unchanged downstream foundation
and independent semantic gates decide admission, not this repair receipt.
"""

from __future__ import annotations

from copy import deepcopy
import re

from bestseller.services.narrative_contracts import (
    repair_commercial_zh_identity_policy,
    repair_legacy_foundation_identity_locks,
    validate_foundation_identity_contract,
)
from bestseller.services.prompt_source_policy import content_hash

POLICY = "source-bound-cast-identity-leaves.v1"
_LOCATION = re.compile(r"^cast_spec\.(antagonist|supporting_cast\[(\d+)\])\.(gender|pronouns)$")
_FIELDS = ("gender", "pronoun_set_zh", "pronoun_set_en")
_PAIRS = {"male": ("他", "he/him"), "female": ("她", "she/her"), "nonbinary": ("他", "they/them")}


def _require(value, code):
    if not value:
        raise ValueError("cast_identity_completion:" + code)


def _member(payload, match):
    return (
        payload["supporting_cast"][int(match[2])] if match[2] is not None else payload["antagonist"]
    )


def diagnose(project, projection, payload, packet):
    """Use the real post-bind identity view; no repair or approval is performed."""
    if not validate_foundation_identity_contract(deepcopy(payload)).blocking_violations:
        return []
    from bestseller.services import planner as p
    from bestseller.services.source_bound_cast_candidate import bind_completed_source_bound_cast
    from bestseller.services.source_bound_planning_content import _sources

    binding = _sources(project)["canonical_snapshot"]
    _require(
        binding and packet.get("approved_sources", {}).get("canonical_snapshot") == binding,
        "current_frozen_source_required",
    )
    premise = binding["content"].get("premise")
    _require(isinstance(premise, str) and bool(premise.strip()), "canonical_premise_required")
    canonical = p._source_bound_canonical_protagonist(project, premise)
    bound = bind_completed_source_bound_cast(
        project=project, projection=projection, raw=payload, canonical_protagonist=canonical
    )
    # These are the same existing, source-bound preparations used immediately
    # before the final native identity gate. Their disposable output is diagnosis.
    effective, _ = repair_legacy_foundation_identity_locks(bound, allow_unreliable_defaults=False)
    effective, _ = repair_commercial_zh_identity_policy(
        effective,
        language=getattr(project, "language", None) or "zh-CN",
        premise_text=p._cast_identity_policy_premise(project),
    )
    report = validate_foundation_identity_contract(effective)
    targets = {}
    for violation in report.blocking_violations:
        _require(
            violation.code
            in {"FOUNDATION_IDENTITY_GENDER_MISSING", "FOUNDATION_IDENTITY_PRONOUN_MISSING"},
            "non_identity_leaf_failure",
        )
        match = _LOCATION.fullmatch(violation.location)
        _require(match is not None, "protagonist_or_unbound_target")
        member = _member(effective, match)
        raw_member = _member(payload, match)
        _require(member["name"] == raw_member["name"], "identity_target_moved")
        target = targets.setdefault(
            member["name"],
            {
                "name": member["name"],
                "fields": set(),
                "effective_identity": {key: deepcopy(member.get(key)) for key in _FIELDS},
                "violations": [],
            },
        )
        target["fields"].update(
            _FIELDS if match[3] == "gender" else (key for key in _FIELDS[1:] if not member.get(key))
        )
        target["violations"].append(violation.to_dict())
    return [{**value, "fields": sorted(value["fields"])} for value in targets.values()]


def identity_schema(person, fields):
    from bestseller.services.source_cast_parts import person_schema

    schema = person_schema(person)
    definition = schema["$defs"]["CharacterInput"]
    values = {
        "gender": list(_PAIRS),
        "pronoun_set_zh": ["他", "她"],
        "pronoun_set_en": ["he/him", "she/her", "they/them"],
    }
    for field in fields:
        _require(field in _FIELDS, "non_identity_field")
        definition["properties"][field] = {"type": "string", "enum": values[field]}
    definition["required"] = list(dict.fromkeys([*definition.get("required", []), *fields]))
    return schema


def check_patch(before, after, target):
    _require(
        isinstance(after, dict) and set(before) == set(after) == {"person"}, "part_shape_changed"
    )
    original, candidate = deepcopy(before["person"]), deepcopy(after["person"])
    _require(candidate.get("name") == original.get("name") == target["name"], "name_changed")
    gender = candidate.get("gender")
    _require(
        gender in _PAIRS
        and (candidate.get("pronoun_set_zh"), candidate.get("pronoun_set_en")) == _PAIRS[gender],
        "identity_pair_inconsistent",
    )
    for field in target["fields"]:
        original.pop(field, None)
        candidate.pop(field, None)
    _require(original == candidate, "non_target_fields_changed")


async def complete_missing(
    session,
    settings,
    *,
    project,
    projection,
    payload,
    people,
    persons,
    receipts,
    completions,
    documents,
    packet,
    recipe_hash,
    workflow_run_id,
    before_dispatch,
):
    targets = diagnose(project, projection, payload, packet)
    if not targets:
        return payload, None
    from bestseller.services.source_cast_field_repair import repair
    from bestseller.services.source_cast_parts import assemble

    by_name = {person["name"]: person for person in people}
    corrected = deepcopy(persons)
    repairs = []
    for target in targets:
        _require(target["name"] in by_name, "person_not_in_paid_roster")
        person = by_name[target["name"]]
        _require(person["source_slot"] != "protagonist", "protagonist_identity_protected")
        part_id = person["id"]
        source = person["source"]
        for field in target["fields"]:
            _require(source.get(field) in (None, "", "unknown"), "explicit_identity_protected")
        original = {"person": deepcopy(persons[part_id])}
        schema = identity_schema(person, target["fields"])
        doc = deepcopy(documents[part_id])
        prior_receipt = deepcopy(next(row for row in receipts if row["part_id"] == part_id))
        doc["identity_completion_contract"] = {
            "schema": POLICY,
            "target": deepcopy(target),
            "source_person": deepcopy(source),
            "instruction": "Supply only the unresolved identity leaves as a compatible "
            "present-person proposal. Unknown is absence, not proof of any gender. "
            "Do not infer gender from a name/occupation/audience. Choose an ordinary "
            "identity only where frozen sources leave it open; never replace an explicit "
            "source identity or invent past facts. Preserve all other values. "
            "This is not approval and the "
            "complete candidate must pass independent source semantic review.",
            "prior_part_receipt": prior_receipt,
        }
        value, proof = await repair(
            session,
            settings,
            project=project,
            part_system="You are correcting exact unresolved Cast identity leaves. "
            "Return only the requested path mapping. All frozen sources and non-target "
            "values remain binding; propose no new person or story event.",
            original_document=doc,
            schema=schema,
            value=original,
            base_completion=completions[part_id],
            recipe_hash=recipe_hash,
            part_id=part_id + ":identity",
            workflow_run_id=workflow_run_id,
            before_dispatch=before_dispatch,
            derived_input_origin={
                "schema": "cast-derived-person-input.v1",
                "prior_part_receipt": prior_receipt,
            }
            if prior_receipt.get("field_correction_receipt")
            or prior_receipt.get("retained_person_receipt")
            else None,
        )
        check_patch(original, value, target)
        corrected[part_id] = deepcopy(value["person"])
        repairs.append(
            {
                "part_id": part_id,
                "name": target["name"],
                "target_fields": target["fields"],
                "field_correction_receipt": proof,
                "quality_approval_granted": False,
            }
        )
    # Reassemble only original layout and model-produced people. Normal full
    # wire, post-bind identity and independent semantic gates still follow.
    links = deepcopy(documents["links"]["retained_links_payload"])
    result = assemble(people, corrected, links)
    _require(not diagnose(project, projection, result, packet), "identity_completion_incomplete")
    return result, {
        "schema": POLICY,
        "before_sha256": content_hash(payload),
        "after_sha256": content_hash(result),
        "repairs": repairs,
        "new_whole_person_generation_calls": 0,
        "quality_approval_granted": False,
        "author_approval_asserted": False,
        "all_other_fields_preserved": True,
    }
