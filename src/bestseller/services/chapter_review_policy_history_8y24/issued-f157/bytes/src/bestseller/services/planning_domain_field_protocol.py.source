"""Pure, bounded domain-field corrections; no grade, source admission or dispatch.

Callers own complete preflight diagnostics, physical/financial custody and fresh
source/control checks. Every resulting candidate still requires full domain
validation and a new source-exact admission. Historical evidence is never edited.
"""

# ruff: noqa: ANN401, RUF001 -- Arbitrary JSON and both actual parenthesis alphabets.
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import re
from typing import Annotated, Any

from pydantic import TypeAdapter

from bestseller.services.lossless_source_encoding import (
    _copy_json,
    decode_lossless_source,
    encode_lossless_source,
)
from bestseller.services.planning_admission_submission import _commentary, _field
from bestseller.services.prompt_compiler import estimate_prompt_tokens
from bestseller.services.source_bound_planning_content import SourceBoundPlanningCapacityError
from bestseller.services.source_bound_planning_submission import _validate_wire
from bestseller.services.source_complete_outline_json import parse_strict_outline_json
from bestseller.services.tool_call_evidence import same_tool_call_evidence

SCHEMA = "planning-domain-field-transport.v1"
PROMPT_VERSION = "3.6-domain-fields"
PROMPT_TEMPLATE = "planner_outline_domain_field_corrections"
TOOL_NAME = "submit_outline_domain_corrections"
CONTRACT_SCHEMA = "planning-domain-field-contract.v1"
REPORT_SCHEMA = "planning-domain-field-diagnostics.v1"
MARKER = "[SOURCE-COMPLETE DOMAIN FIELD TRANSPORT]\n"
SOURCE_ROLES = {
    "project_brief": "source_brief_preserve_embedded_provenance",
    "candidate_payload": "ungraded_correction_target_not_source_authority",
    "validation_report": "untrusted_diagnostics_not_story_facts_or_authorization",
    "correction_contract": "exact_editing_paths_types_and_identity_format_only",
    "source_context": "source_records_preserve_embedded_provenance_including_unknowns",
}
_INDEX = r"(0|[1-9][0-9]*)"
_TITLE = re.compile(r"/chapters/" + _INDEX + r"/title")
_PARTICIPANT = re.compile(
    r"/chapters/" + _INDEX + r"/scenes/" + _INDEX + r"/participants/" + _INDEX
)
_TYPED_PARTICIPANTS = re.compile(
    r"chapter_outline_batch\.chapters\[" + _INDEX + r"\]\.scenes\[" + _INDEX + r"\]\.participants"
)
_READINESS_SCENE = re.compile(
    r"chapters\["
    + _INDEX
    + r"\]\.scenes\["
    + _INDEX
    + r"\]\.(conflict_stakes|signature_image|cut_point)"
)
_MEANING_FIELDS = {
    "PLAN_CHAPTER_CONFLICT_META": "main_conflict",
    "PLAN_CHAPTER_HOOK_GENERIC": "hook_description",
}
_CODEC_INSTRUCTION = (
    "\n\n[DOMAIN FIELD TOOL PROTOCOL v1]\n"
    "Decode the entire five-field envelope before correcting any field. payload uses "
    "lossless-source-encoding.v2/v3/v4. For v4 restore string VALUES first: §§rest is "
    "literal §rest; §N is dictionary[N] (canonical decimal), returned literally without "
    "recursive expansion. Restore ONLY object keys using key_aliases, never string "
    "values. For v3 restore object keys using key_aliases. Then decode v2: ONLY listed "
    'references[i].path replaces {"$ref":i} with the completed references[i].target '
    "value using RFC 6901. Unlisted authored $ref is literal data. Preserve all unknown "
    "keys, nulls, scalar types, array order, old raw metadata and original source roles. "
    "Historical candidates, unknown attribution and diagnostics gain no authority. "
    "Submit exactly one native submit_outline_domain_corrections function call with "
    '{"corrections":{exact_RFC6901_path:complete_value}}. Include every contract path '
    "and no other path. Do not submit a patches document or a JSON text fallback. "
    "Participants arrays must equal their single allowed complete array: preserve "
    "count, order and every existing canonical identity. Parenthetical qualifiers "
    "are retained audit text, never new author/canon facts. Text corrections require "
    "new model-authored values supported by actual sources, not invented defaults or "
    "new plot obligations. Change nothing outside the contract. This submission is "
    "ungraded data; full domain validation and new source-exact admission remain required."
)


def _need(condition: object, code: str) -> None:
    if not condition:
        raise ValueError("planning_domain_fields:" + code)


def _json(value: object) -> str:
    return json.dumps(
        _copy_json(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _hash(value: object) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _text_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _tokens(path: str) -> list[str]:
    _need(
        type(path) is str and path.startswith("/") and re.search(r"~(?![01])", path) is None,
        "pointer",
    )
    return [part.replace("~1", "/").replace("~0", "~") for part in path[1:].split("/")]


def _at(value: Any, path: str) -> Any:
    for token in _tokens(path):
        if type(value) is list:
            _need(
                re.fullmatch(_INDEX, token) is not None and int(token) < len(value), "pointer_index"
            )
            value = value[int(token)]
        else:
            _need(type(value) is dict and token in value, "pointer_missing")
            value = value[token]
    return value


def _dict_leaf(value: dict, path: str) -> tuple[dict, str]:
    """Resolve an existing object parent without requiring its final key."""
    parts = _tokens(path)
    parent_path = path.rpartition("/")[0]
    parent = _at(value, parent_path) if parent_path else value
    _need(type(parent) is dict, "leaf_parent_object_required")
    return parent, parts[-1]


def _set(value: dict, path: str, incoming: Any) -> None:
    parts = _tokens(path)
    parent_path = "/" + "/".join(part.replace("~", "~0").replace("/", "~1") for part in parts[:-1])
    parent = _at(value, parent_path) if len(parts) > 1 else value
    key = parts[-1]
    if type(parent) is list:
        _need(re.fullmatch(_INDEX, key) is not None and int(key) < len(parent), "pointer_index")
        parent[int(key)] = deepcopy(incoming)
    else:
        _need(type(parent) is dict and key in parent, "pointer_missing")
        parent[key] = deepcopy(incoming)


def prepare_domain_candidate(
    before_candidate: dict,
    native_patch_document: str | dict,
) -> tuple[dict, dict]:
    """Apply actual strict patches, wrapping only nonempty scene state strings.

    The whole native document is reversibly bound, not repaired or normalized.
    An applier that strips a wrapped summary is rejected. No domain model dump
    or inferred/default story value is substituted into the returned JSON.
    """
    from bestseller.services import planner

    before = _copy_json(before_candidate)
    _need(type(before) is dict, "candidate_object")
    native = (
        parse_strict_outline_json(native_patch_document)
        if type(native_patch_document) is str
        else _copy_json(native_patch_document)
    )
    _need(
        type(native) is dict
        and set(native) == {"patches"}
        and type(native["patches"]) is list
        and bool(native["patches"]),
        "patch_document",
    )
    chapters = before.get("chapters")
    _need(type(chapters) is list and bool(chapters), "chapters")
    owners = {}
    for i, chapter in enumerate(chapters):
        _need(
            type(chapter) is dict
            and type(chapter.get("chapter_number")) is int
            and chapter["chapter_number"] > 0
            and chapter["chapter_number"] not in owners,
            "chapter_owner",
        )
        owners[chapter["chapter_number"]] = i
        scenes = chapter.get("scenes")
        _need(type(scenes) is list and all(type(s) is dict for s in scenes), "scenes")
        numbers = [s.get("scene_number") for s in scenes]
        _need(
            all(type(n) is int and n > 0 for n in numbers) and len(set(numbers)) == len(numbers),
            "scene_owner",
        )
    adapted = deepcopy(native)
    targets, mappings = [], []
    registered = planner._outline_patch_protocol(before)["fields"]
    for index, patch in enumerate(adapted["patches"]):
        _need(type(patch) is dict, "patch_object")
        scope = patch.get("scope")
        required = {"chapter_number", "scope", "field", "value"}
        if scope == "scene":
            required.add("scene_number")
        _need(
            set(patch) == required
            and type(patch["chapter_number"]) is int
            and patch["chapter_number"] in owners,
            "patch_owner_or_fields",
        )
        field = patch["field"]
        _need(
            type(scope) is str
            and scope in registered
            and type(field) is str
            and field in registered[scope],
            "patch_field",
        )
        i = owners[patch["chapter_number"]]
        path = f"/chapters/{i}"
        if scope == "scene":
            n = patch["scene_number"]
            _need(type(n) is int, "patch_scene_owner")
            matches = [j for j, s in enumerate(chapters[i]["scenes"]) if s["scene_number"] == n]
            _need(len(matches) == 1, "patch_scene_owner")
            path += f"/scenes/{matches[0]}"
        elif scope == "causal_contract":
            path += "/causal_contract"
        container = _at(before, path)
        _need(type(container) is dict, "patch_container")
        path += "/" + field.replace("~", "~0").replace("/", "~1")
        _need(
            all(
                path != t["path"]
                and not path.startswith(t["path"] + "/")
                and not t["path"].startswith(path + "/")
                for t in targets
            ),
            "patch_overlap",
        )
        targets.append(
            {"path": path, "existed": field in container, "before": deepcopy(container.get(field))}
        )
        if scope == "scene" and field in {"entry_state", "exit_state"}:
            value = patch["value"]
            if type(value) is str:
                _need(bool(value.strip()), "empty_state_string")
                patch["value"] = {"summary": value}
                mappings.append(
                    {
                        "patch_index": index,
                        "path": path,
                        "original_text": value,
                        "original_text_sha256": _text_hash(value),
                    }
                )
            else:
                _need(type(value) is dict, "state_value")
    inverse = deepcopy(adapted)
    for mapping in mappings:
        inverse["patches"][mapping["patch_index"]]["value"] = mapping["original_text"]
    _need(_json(inverse) == _json(native), "inverse_patch_changed")
    candidate = planner._apply_outline_commercial_patches(before, adapted["patches"], strict=True)
    for mapping in mappings:
        state = _at(candidate, mapping["path"])
        _need(
            type(state) is dict
            and type(state.get("summary")) is str
            and state["summary"] == mapping["original_text"],
            "summary_text_changed",
        )
    restored = deepcopy(candidate)
    for target in targets:
        if target["existed"]:
            _set(restored, target["path"], target["before"])
        else:
            parent_path, _, encoded = target["path"].rpartition("/")
            parent = _at(restored, parent_path)
            parent.pop(encoded.replace("~1", "/").replace("~0", "~"), None)
    _need(_json(restored) == _json(before), "non_target_changed")
    return candidate, {
        "schema": "planning-domain-state-representation.v1",
        "before_candidate_sha256": _hash(before),
        "native_patch_sha256": _hash(native),
        "adapted_patch_sha256": _hash(adapted),
        "inverse_patch_sha256": _hash(inverse),
        "candidate_sha256": _hash(candidate),
        "state_wrappers": mappings,
        "target_paths": [t["path"] for t in targets],
        "non_targets_unchanged": True,
        "inverse_verified": True,
        "grade_asserted": False,
        "source_authority_granted": False,
    }


def combine_domain_validation_reports(*reports: dict) -> dict:
    """Keep every supplied layer intact; never synthesize a diagnostic candidate.

    The caller must collect all current layers, including a direct typed chapter
    plan validation view when the source-integrity validator exits early.
    """
    _need(bool(reports), "reports_required")
    original = [_copy_json(report) for report in reports]
    violations, warnings = [], []
    for report in original:
        _need(
            type(report) is dict and type(report.get("passed")) is bool,
            "report_shape",
        )
        if "violations" in report:
            _need(
                type(report["violations"]) is list and type(report.get("warnings", [])) is list,
                "report_shape",
            )
            violations.extend(deepcopy(report["violations"]))
            warnings.extend(deepcopy(report.get("warnings", [])))
        else:
            blocking, audit = report.get("blocking_findings"), report.get("audit_findings")
            _need(
                type(blocking) is list
                and type(audit) is list
                and all(type(v) is dict and v.get("blocking") is True for v in blocking)
                and all(type(v) is dict and v.get("blocking") is False for v in audit),
                "readiness_report_shape",
            )
            violations.extend(deepcopy(blocking))
            warnings.extend(deepcopy(audit))
    return {
        "gate_name": REPORT_SCHEMA,
        "passed": all(r["passed"] for r in original),
        "violations": violations,
        "warnings": warnings,
        "reports": original,
        "grade_asserted": False,
    }


def _identities(manifest: list) -> tuple[list[str], dict[str, set[str]]]:
    _need(type(manifest) is list and bool(manifest), "identity_manifest_list")
    names, tokens = [], {}
    for row in manifest:
        _need(
            type(row) is dict
            and type(row.get("name")) is str
            and bool(row["name"].strip())
            and row["name"] not in names,
            "identity_name",
        )
        name = row["name"]
        names.append(name)
        aliases = row.get("aliases", [])
        _need(
            type(aliases) is list and all(type(a) is str and bool(a.strip()) for a in aliases),
            "identity_aliases",
        )
        for token in [name, *aliases]:
            tokens.setdefault(token, set()).add(name)
    return names, tokens


def _balanced_note(text: str) -> bool:
    if not text or text[0] not in "(（":
        return False
    stack = []
    for i, char in enumerate(text):
        if char in "(（":
            stack.append(")" if char == "(" else "）")
        elif char in ")）":
            if not stack or stack.pop() != char:
                return False
            if not stack and i != len(text) - 1:
                return False
    return not stack and bool(text[1:-1].strip())


def _identity_array(
    value: list, names: list[str], tokens: dict[str, set[str]]
) -> tuple[list, list]:
    _need(type(value) is list and bool(value), "participants_array")
    expected, mappings = [], []
    for index, label in enumerate(value):
        _need(type(label) is str, "participant_label")
        if label in names:
            expected.append(label)
            continue
        matches = []
        for token, owners in tokens.items():
            if label.startswith(token):
                suffix = label[len(token) :]
                if _balanced_note(suffix.lstrip(" \t")):
                    matches.append((token, owners, suffix))
        resolved = set().union(*(owners for _, owners, _ in matches))
        _need(len(resolved) == 1, "participant_identity_ambiguous_or_unknown")
        name = next(iter(resolved))
        expected.append(name)
        mappings.append(
            {
                "position": index,
                "raw_label": label,
                "canonical_name": name,
                "matched_declared_tokens": sorted({t for t, _, _ in matches}),
                "qualifiers_are_author_facts": False,
            }
        )
    return expected, mappings


def _string_schema(field_name: str, *, scene: bool = False) -> dict:
    from bestseller.domain.narrative import SceneContractRead
    from bestseller.domain.workflow import ChapterOutlineInput, SceneOutlineInput

    model = (
        (SceneContractRead if field_name == "conflict_stakes" else SceneOutlineInput)
        if scene
        else ChapterOutlineInput
    )
    _need(field_name in model.model_fields, "model_field")
    field = model.model_fields[field_name]
    annotation = (
        Annotated[field.annotation, *field.metadata] if field.metadata else field.annotation
    )
    schema = TypeAdapter(annotation).json_schema()
    if "anyOf" in schema:
        matches = [item for item in schema["anyOf"] if item.get("type") == "string"]
        _need(len(matches) == 1, "chapter_string_schema")
        schema = matches[0]
    _need(schema.get("type") == "string", "chapter_string_schema")
    return {**schema, "minLength": max(1, schema.get("minLength", 0)), "pattern": r"\S"}


def tools_for_domain_corrections(
    candidate: dict,
    validation_report: dict,
    identity_manifest: list,
) -> tuple[list[dict], dict]:
    """Derive finite targets from current actual critical diagnostics only."""
    candidate, report, manifest = (
        _copy_json(v)
        for v in (
            candidate,
            validation_report,
            identity_manifest,
        )
    )
    _need(
        type(candidate) is dict
        and type(report) is dict
        and report.get("passed") is False
        and type(report.get("violations")) is list
        and bool(report["violations"]),
        "rejected_report_required",
    )
    if "reports" in report:
        rebuilt = combine_domain_validation_reports(*report["reports"])
        _need(_json(report) == _json(rebuilt), "combined_report_mismatch")
    names, tokens = _identities(manifest)
    properties, targets, identity_mappings, reported_identity_positions = {}, {}, {}, {}
    diagnostic_paths = []
    for violation in report["violations"]:
        _need(
            type(violation) is dict and violation.get("severity") == "critical",
            "unknown_or_noncritical_violation",
        )
        code = violation.get("code")
        location = violation.get("location", violation.get("path"))
        _need(type(code) is str and type(location) is str, "violation_fields")
        if code == "PLAN_SOURCE_FIELD_EMPTY" and (match := _TITLE.fullmatch(location)):
            path, field = location, "title"
            previous = _at(candidate, path)
            _need(
                previous is None or (type(previous) is str and not previous.strip()),
                "title_diagnostic_stale",
            )
            properties[path] = _string_schema(field)
        elif code == "PLAN_SCENE_UNKNOWN_PARTICIPANT":
            indexed = _PARTICIPANT.fullmatch(location)
            typed = _TYPED_PARTICIPANTS.fullmatch(location)
            _need(indexed is not None or typed is not None, "participant_location")
            if indexed is not None:
                raw_label = _at(candidate, location)
                path = location.rsplit("/", 1)[0]
                positions = {int(indexed.group(3))}
            else:
                path = f"/chapters/{typed.group(1)}/scenes/{typed.group(2)}/participants"
                metadata = violation.get("metadata")
                _need(
                    type(metadata) is dict and type(metadata.get("participant")) is str,
                    "typed_participant_metadata",
                )
                raw_label = metadata["participant"]
                array = _at(candidate, path)
                _need(type(array) is list, "participants_array")
                positions = {i for i, label in enumerate(array) if label == raw_label}
                _need(bool(positions), "typed_participant_diagnostic_stale")
                for key, owner_path in (
                    ("chapter_number", f"/chapters/{typed.group(1)}/chapter_number"),
                    (
                        "scene_number",
                        f"/chapters/{typed.group(1)}/scenes/{typed.group(2)}/scene_number",
                    ),
                ):
                    if key in metadata:
                        _need(
                            type(metadata[key]) is int
                            and metadata[key] == _at(candidate, owner_path),
                            "typed_participant_owner",
                        )
            _need(
                type(raw_label) is str and raw_label not in tokens, "participant_diagnostic_stale"
            )
            current = _at(candidate, path)
            expected, mappings = _identity_array(current, names, tokens)
            properties[path] = {
                "type": "array",
                "items": {"type": "string", "enum": names},
                "minItems": len(current),
                "maxItems": len(current),
                "enum": [expected],
            }
            identity_mappings[path] = {
                "raw_labels": deepcopy(current),
                "expected_canonical_array": expected,
                "mappings": mappings,
            }
            reported_identity_positions.setdefault(path, set()).update(positions)
        elif code == "PLANNING_SCENE_FIELD_MISSING":
            match = _READINESS_SCENE.fullmatch(location)
            _need(
                match is not None and violation.get("blocking") is True, "readiness_scene_location"
            )
            field = match.group(3)
            path = f"/chapters/{match.group(1)}/scenes/{match.group(2)}"
            if field == "conflict_stakes":
                path += "/methodology_contract"
            path += "/" + field
            parent, key = _dict_leaf(candidate, path)
            previous = parent.get(key)
            _need(
                previous is None or (type(previous) is str and not previous.strip()),
                "readiness_diagnostic_stale",
            )
            properties[path] = _string_schema(field, scene=True)
        elif code in _MEANING_FIELDS:
            field = _MEANING_FIELDS[code]
            match = re.fullmatch(
                r"chapter_outline_batch\.chapters\[" + _INDEX + r"\]\." + field, location
            )
            pointer = re.fullmatch(r"/chapters/" + _INDEX + "/" + field, location)
            _need(match is not None or pointer is not None, "meaning_field_location")
            path = f"/chapters/{(match or pointer).group(1)}/{field}"
            _need(type(_at(candidate, path)) is str, "meaning_field_type")
            properties[path] = _string_schema(field)
        else:
            raise ValueError("planning_domain_fields:unsupported_critical:" + code)
        parent, key = _dict_leaf(candidate, path)
        if key in parent:
            before = {"path": path, "before_sha256": _hash(parent[key]), "diagnostics": []}
        else:
            _need(code == "PLANNING_SCENE_FIELD_MISSING", "missing_leaf_not_reported")
            before = {
                "path": path,
                "before_present": False,
                "before_sha256": _hash(
                    {"schema": "planning-domain-field-missing-leaf.v1", "present": False}
                ),
                "diagnostics": [],
            }
        target = targets.setdefault(path, before)
        target["diagnostics"].append(deepcopy(violation))
        diagnostic_paths.append({"code": code, "diagnostic_path": location, "storage_path": path})
    for path, mapping in identity_mappings.items():
        _need(
            reported_identity_positions[path] == {r["position"] for r in mapping["mappings"]},
            "participant_diagnostics_incomplete",
        )
    paths = sorted(properties)
    parameters = {
        "type": "object",
        "properties": {
            "corrections": {
                "type": "object",
                "properties": {p: properties[p] for p in paths},
                "required": paths,
                "additionalProperties": False,
            }
        },
        "required": ["corrections"],
        "additionalProperties": False,
    }
    contract = {
        "schema": CONTRACT_SCHEMA,
        "tool_name": TOOL_NAME,
        "candidate_sha256": _hash(candidate),
        "validation_report_sha256": _hash(report),
        "identity_manifest_sha256": _hash(manifest),
        "paths": paths,
        "targets": [targets[p] for p in paths],
        "parameters": parameters,
        "identity_format_mappings": identity_mappings,
        "diagnostic_to_storage_paths": diagnostic_paths,
        "grade_asserted": False,
        "source_authority_granted": False,
        "full_domain_and_new_admission_required": True,
    }
    tool = {
        "type": "function",
        "function": {
            "name": TOOL_NAME,
            "strict": True,
            "description": "Submit every exact correction path once. Identity arrays have one "
            "permitted complete value; text is new source-bound model work, never approval.",
            "parameters": deepcopy(parameters),
        },
    }
    return [tool], contract


def _verify_contract(candidate: dict, report: dict, manifest: list, contract: dict) -> None:
    _, expected = tools_for_domain_corrections(candidate, report, manifest)
    _need(_json(expected) == _json(contract), "contract_mismatch")


def apply_domain_corrections(
    candidate: dict,
    corrections: dict,
    *,
    validation_report: dict,
    identity_manifest: list,
    contract: dict,
) -> tuple[dict, dict]:
    """Apply only model-supplied exact fields; no aliases, normalization or grade."""
    _verify_contract(candidate, validation_report, identity_manifest, contract)
    corrections = _copy_json(corrections)
    parameters = contract["parameters"]
    _validate_wire({"corrections": corrections}, parameters, parameters)
    for path, value in corrections.items():
        if type(value) is str:
            _need(bool(value.strip()), "empty_text_correction")
        elif path in contract["identity_format_mappings"]:
            expected = contract["identity_format_mappings"][path]["expected_canonical_array"]
            _need(_json(value) == _json(expected), "identity_array_changed")
    result = deepcopy(candidate)
    added_paths = [
        target["path"]
        for target in contract["targets"]
        if target.get("before_present", True) is False
    ]
    for path, value in corrections.items():
        if path in added_paths:
            parent, key = _dict_leaf(result, path)
            _need(key not in parent, "absent_leaf_changed")
            parent[key] = deepcopy(value)
        else:
            _set(result, path, value)
    restored = deepcopy(result)
    for path in contract["paths"]:
        if path in added_paths:
            parent, key = _dict_leaf(restored, path)
            del parent[key]
        else:
            _set(restored, path, _at(candidate, path))
    _need(_json(restored) == _json(candidate), "non_target_changed")
    receipt = {
        "schema": "planning-domain-field-derived-correction.v1",
        "before_candidate_sha256": _hash(candidate),
        "corrections_sha256": _hash(corrections),
        "candidate_sha256": _hash(result),
        "contract_sha256": _hash(contract),
        "paths": deepcopy(contract["paths"]),
        "non_targets_unchanged": True,
        "identity_format_mappings": deepcopy(contract["identity_format_mappings"]),
        "grade_asserted": False,
        "source_authority_granted": False,
        "full_domain_and_new_admission_required": True,
    }
    if added_paths:
        receipt.update(added_paths=added_paths, absent_leaf_inverse_verified=True)
    return result, receipt


def parse_domain_corrections(
    completion: Any,
    *,
    candidate: dict,
    validation_report: dict,
    identity_manifest: list,
    contract: dict,
) -> tuple[dict, dict]:
    """Parse one complete native function with exact raw/normalized evidence."""
    _verify_contract(candidate, validation_report, identity_manifest, contract)
    finish = _field(completion, "finish_reason")
    _need(finish in {"tool_calls", "stop", "end_turn", "completed"}, "incomplete_native_return")
    content, calls, raw = (
        _field(completion, key)
        for key in (
            "content",
            "tool_calls",
            "raw_message",
        )
    )
    _need(type(content) is str and type(calls) is list and len(calls) == 1, "single_native_call")
    _need(_field(completion, "function_call") is None, "legacy_function_call")
    if raw is not None:
        _need(
            type(raw) is dict
            and raw.get("role") == "assistant"
            and raw.get("function_call") is None,
            "raw_assistant_message",
        )
        _need(same_tool_call_evidence(raw.get("tool_calls"), calls), "raw_tool_conflict")
        _need(
            raw.get("content") == content or (raw.get("content") is None and content == ""),
            "raw_content_conflict",
        )
    commentary = _commentary(content)
    call = calls[0]
    _need(
        type(call) is dict
        and set(call) == {"id", "type", "function"}
        and type(call["id"]) is str
        and bool(call["id"].strip())
        and call["type"] == "function",
        "native_call_shape",
    )
    function = call["function"]
    _need(
        type(function) is dict
        and set(function) == {"name", "arguments"}
        and function["name"] == TOOL_NAME
        and type(function["arguments"]) is str,
        "native_function",
    )
    text = function["arguments"]
    _need(not text.lstrip().startswith("```"), "native_arguments_json_only")
    arguments = parse_strict_outline_json(text)
    _need(set(arguments) == {"corrections"}, "argument_fields")
    corrections = arguments["corrections"]
    apply_domain_corrections(
        candidate,
        corrections,
        validation_report=validation_report,
        identity_manifest=identity_manifest,
        contract=contract,
    )
    return deepcopy(corrections), {
        "schema": "planning-domain-field-native-submission.v1",
        "channel": "native_tool",
        "tool_name": TOOL_NAME,
        "tool_call_id": call["id"],
        "raw_arguments": text,
        "raw_arguments_sha256": _text_hash(text),
        "raw_message_present": raw is not None,
        "raw_message_status": "missing" if raw is None else "present",
        "raw_message_sha256": _hash(raw),
        "tool_calls_sha256": _hash(calls),
        "contract_sha256": _hash(contract),
        "corrections_sha256": _hash(corrections),
        "commentary_status": commentary,
        "tools_executed": 0,
        "grade_asserted": False,
        "physical_custody_verified": False,
    }


def _system() -> str:
    from bestseller.services.outline_llm_judge import OUTLINE_SOURCE_AUTHORITY_RULES

    return (
        "Correct only the supplied finite domain-field targets against complete sources."
        + OUTLINE_SOURCE_AUTHORITY_RULES
        + _CODEC_INSTRUCTION
    )


def _bounds(max_chars: int, max_estimated_tokens: int) -> None:
    for name, value in (("max_chars", max_chars), ("max_estimated_tokens", max_estimated_tokens)):
        _need(type(value) is int and 0 < value <= 1_000_000, name + "_invalid")


def _transport_receipt(
    system: str, user: str, wire: dict, original: dict, max_chars: int, max_estimated_tokens: int
) -> dict:
    capacity = {
        "schema": "planning-domain-field-capacity.v1",
        "max_combined_chars": max_chars,
        "max_estimated_tokens": max_estimated_tokens,
        "truncated": False,
        "combined_chars": len(system) + len(user),
        "estimated_tokens": estimate_prompt_tokens(system) + estimate_prompt_tokens(user),
    }
    if (
        capacity["combined_chars"] > max_chars
        or capacity["estimated_tokens"] > max_estimated_tokens
    ):
        raise SourceBoundPlanningCapacityError(capacity)
    packet = _json(wire["payload"])
    _need(user.count(packet) == 1, "packet_offset_ambiguous")
    start = user.index(packet)
    return {
        "schema": SCHEMA,
        "prompt_version": PROMPT_VERSION,
        "prompt_template": PROMPT_TEMPLATE,
        "codec_schema": wire["payload"]["schema"],
        "logical_sha256": _hash(original),
        "field_sha256": {k: _hash(v) for k, v in original.items()},
        "wire_sha256": _hash(wire),
        "source_roles_sha256": _hash(SOURCE_ROLES),
        "system_prompt_sha256": _text_hash(system),
        "user_prompt_sha256": _text_hash(user),
        "packet_sha256": _text_hash(packet),
        "packet_start": start,
        "packet_end": start + len(packet),
        "offset_unit": "unicode_code_points",
        "max_chars": max_chars,
        "max_estimated_tokens": max_estimated_tokens,
        "prompt_capacity": capacity,
        "source_complete": True,
        "roundtrip_verified": True,
        "source_authority_granted": False,
        "grade_asserted": False,
    }


def _logical_valid(original: dict) -> None:
    _need(type(original) is dict and set(original) == set(SOURCE_ROLES), "logical_fields")
    _need(
        all(
            type(original[k]) is dict
            for k in (
                "candidate_payload",
                "validation_report",
                "correction_contract",
                "source_context",
            )
        ),
        "logical_object_fields",
    )
    contract = original["correction_contract"]
    _need(
        contract.get("schema") == CONTRACT_SCHEMA
        and contract.get("tool_name") == TOOL_NAME
        and contract.get("candidate_sha256") == _hash(original["candidate_payload"])
        and contract.get("validation_report_sha256") == _hash(original["validation_report"]),
        "logical_contract_binding",
    )


def build_domain_field_prompts(
    *,
    project_brief: Any,
    candidate: dict,
    validation_report: dict,
    contract: dict,
    source_context: dict,
    max_chars: int = 120_000,
    max_estimated_tokens: int = 60_000,
) -> tuple[str, str, dict]:
    """Encode all five original fields; model capacity/retention are outer guards."""
    _bounds(max_chars, max_estimated_tokens)
    original = _copy_json(
        {
            "project_brief": project_brief,
            "candidate_payload": candidate,
            "validation_report": validation_report,
            "correction_contract": contract,
            "source_context": source_context,
        }
    )
    _logical_valid(original)
    packet = encode_lossless_source(original, version=4)
    _need(_json(decode_lossless_source(packet)) == _json(original), "roundtrip_changed")
    wire = {
        "schema": SCHEMA,
        "source_roles": deepcopy(SOURCE_ROLES),
        "logical_sha256": _hash(original),
        "payload": packet,
    }
    system, user = _system(), MARKER + _json(wire)
    return (
        system,
        user,
        _transport_receipt(system, user, wire, original, max_chars, max_estimated_tokens),
    )


def decode_domain_field_prompts(
    *,
    system_prompt: str,
    user_prompt: str,
    transport_receipt: dict | None = None,
    max_chars: int = 120_000,
    max_estimated_tokens: int = 60_000,
) -> dict:
    """Reconstruct from complete wire; bounds must be independently supplied again."""
    _bounds(max_chars, max_estimated_tokens)
    _need(
        system_prompt == _system() and type(user_prompt) is str and user_prompt.startswith(MARKER),
        "prompt_protocol",
    )
    text = user_prompt[len(MARKER) :]
    wire = parse_strict_outline_json(text)
    _need(
        _json(wire) == text
        and set(wire)
        == {
            "schema",
            "source_roles",
            "logical_sha256",
            "payload",
        }
        and wire["schema"] == SCHEMA
        and _json(wire["source_roles"]) == _json(SOURCE_ROLES),
        "wire_envelope",
    )
    _need(
        type(wire["payload"]) is dict
        and wire["payload"].get("schema")
        in {
            "lossless-source-encoding.v2",
            "lossless-source-encoding.v3",
            "lossless-source-encoding.v4",
        },
        "codec_schema",
    )
    original = decode_lossless_source(wire["payload"])
    _logical_valid(original)
    _need(_hash(original) == wire["logical_sha256"], "logical_hash")
    expected = _transport_receipt(
        system_prompt, user_prompt, wire, original, max_chars, max_estimated_tokens
    )
    if transport_receipt is not None:
        _need(_json(transport_receipt) == _json(expected), "transport_receipt_mismatch")
    return original


__all__ = [
    "PROMPT_TEMPLATE",
    "PROMPT_VERSION",
    "SCHEMA",
    "TOOL_NAME",
    "apply_domain_corrections",
    "build_domain_field_prompts",
    "combine_domain_validation_reports",
    "decode_domain_field_prompts",
    "parse_domain_corrections",
    "prepare_domain_candidate",
    "tools_for_domain_corrections",
]
