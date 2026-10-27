"""Bounded narrative projection proposals; observations never become a grade.

No provider, database, coercion, alias repair or source-authority inference.
Unreferenced unknown fields are frozen. Exact native references may nominate
existing scalar leaves only inside registered narrative namespaces.
"""

from __future__ import annotations

from copy import deepcopy
import hashlib
import re
from typing import Any

from bestseller.domain.workflow import ChapterOutlineInput, SceneOutlineInput
from bestseller.services.lossless_source_encoding import _copy_json, _json
from bestseller.services.planning_admission_submission import _commentary
from bestseller.services.source_bound_planning_submission import _validate_wire
from bestseller.services.source_complete_outline_json import parse_strict_outline_json
from bestseller.services.tool_call_evidence import same_tool_call_evidence

SCHEMA = "planning-projection-consistency.v1"
TEMPLATE = "planner_outline_source_consistency"
VERSION = "3.8-source-consistency"
TOOL = "submit_outline_consistency_fields"
REGISTRY = "registered-narrative-projection-leaves.v1"
REFERENCE_SELECTION = "exact-reference-producer-closure.v1"
_TOOL_DESCRIPTION = (
    "Return every bound fNNNN scalar, unchanged or corrected. "
    "No extra fields. Observations are untrusted hints. "
    "Never submit a grade or change identities."
)

_CHAPTER_TEXT = {
    "chapter_goal",
    "opening_pressure",
    "opening_situation",
    "main_conflict",
    "required_payoff",
    "hook_description",
    "tail_hook",
    "world_rule_landing",
}
_CHAPTER_LIST = {
    "chapter_concrete_actions",
    "chapter_object_uses",
    "chapter_information_introduced",
    "chapter_information_held_back",
    "key_reveals",
}
# These existing legacy fields have an explicit producer/patch registry. They
# are not inferred from similar names, matching strings or a reviewer's typo.
_LEGACY_CHAPTER = {"payoff", "visible_action_or_reaction", "tail_hook_description"}
_SCENE_TEXT = {
    "time_label",
    "concrete_goal",
    "protagonist_state",
    "hook_requirement",
    "signature_image",
    "cut_point",
    "information_control_mode",
    "object_signal",
}
_SCENE_LIST = {
    "action_sequence",
    "key_dialogue_beats",
    "forbidden_actions",
    "relationship_debts",
    "information_introduced",
    "information_held_back",
}
_CAUSAL = {
    "pressure",
    "resistance",
    "immediate_pressure",
    "opposition",
    "protagonist_choice",
    "protagonist_desire",
    "cost_or_tradeoff",
    "state_change",
    "visible_action_or_reaction",
    "gain_or_reveal",
    "next_reader_desire",
    "final_state",
}
_STATE = {"summary", "pressure", "state_change", "time_position"}
# project_segment_outline copies these exact controls from _scene_field into
# its single scene. Its scene methodology repeats only the four fields below;
# a same-named field elsewhere is not a proved producer copy.
_SCENE_CONTROL_COPIES = {
    "action_sequence",
    "entry_state",
    "exit_state",
    "key_dialogue_beats",
    "forbidden_actions",
    "relationship_debts",
    "signature_image",
    "cut_point",
}
_SCENE_METHOD_COPIES = {
    "conflict_stakes",
    "information_control",
    "signature_image",
    "cut_point",
}
# Only fields read by the existing segment producer belong here. Freeform
# generated children are admitted separately by exact native reference.
_CONTRACT_FIELDS = {
    "opening_contract": {
        "summary",
        "public_pressure",
        "immediate_stakes",
        "resistance",
    },
    "unlock_contract": {"summary", "next_segment_trigger"},
    "ability_cost_contract": {"summary", "cost"},
    "payoff_contract": {"summary", "visible_result"},
    "closure_contract": {"summary", "irreversible_choice", "state_change"},
    "continuity_contract": {"summary", "fact_boundary", "time_position"},
}
_PROTECTED = {
    "_meta",
    "metadata",
    "metadata_json",
    "source",
    "source_binding",
    "source_lineage",
    "book_source_context",
    "selected_creation_contracts",
    "participants",
    "title",
    "pov",
    "pov_type",
    "pov_character",
    "identity",
    "id",
    "chapter_number",
    "scene_number",
    "target_word_count",
    "target_chapters",
    "volume_number",
    "thesis_vector",
    "no_serial_hook",
}
_TYPES = {str: "string", int: "integer", float: "number", bool: "boolean"}
_SCOPE = re.compile(r"chapters\[(0|[1-9][0-9]*)\](?:\.scenes\[(0|[1-9][0-9]*)\])?")


def _need(condition: object, code: str) -> None:
    if not condition:
        raise ValueError("planning_projection_consistency:" + code)


def _hash(value: Any) -> str:  # noqa: ANN401
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _pointer(parts: tuple[str | int, ...]) -> str:
    return "/" + "/".join(str(p).replace("~", "~0").replace("/", "~1") for p in parts)


def _tokens(path: str) -> tuple[str, ...]:
    _need(
        type(path) is str and path.startswith("/") and not re.search(r"~(?![01])", path),
        "pointer_syntax",
    )
    return tuple(p.replace("~1", "/").replace("~0", "~") for p in path[1:].split("/"))


def _protected_path(parts: tuple[str, ...]) -> bool:
    # These are technical/identity namespaces, not narrative authority. An
    # untrusted reference must never make their contents editable.
    return any(
        part in _PROTECTED
        or part.startswith(("_", "source_", "identity_", "pov_", "target_"))
        or part.endswith(
            (
                "_id",
                "_ids",
                "_hash",
                "_sha256",
                "_receipt",
                "_proof",
                "_binding",
                "_lineage",
                "_metadata",
                "_metadata_json",
            )
        )
        for part in parts
    )


def _reference_namespace(parts: tuple[str, ...]) -> tuple[str, ...] | None:
    """Return the producer-copy suffix, or () for a local narrative leaf.

    No child name, value similarity or inferred alias determines eligibility.
    The caller must independently prove that the exact referenced leaf exists
    and is scalar. None denotes a namespace that is not registered here.
    """
    body = parts[2:]
    if len(body) >= 3 and body[0] == "scenes":
        body = body[2:]
        if len(body) > 1 and body[0] in {"purpose", "entry_state", "exit_state"}:
            return ()
        roots = {"methodology_contract"}
    else:
        roots = {"causal_contract", "methodology_contract"}
    if len(body) < 2 or body[0] not in roots:
        return None
    if body[1] == "fanqie_short_v2":
        if len(body) >= 4 and body[2] in _CONTRACT_FIELDS:
            return body[2:]
        return None
    return () if body[0] == "causal_contract" else None


def _at(value: Any, path: str) -> Any:  # noqa: ANN401
    for token in _tokens(path):
        if type(value) is dict and token in value:
            value = value[token]
        elif (
            type(value) is list
            and re.fullmatch(r"0|[1-9][0-9]*", token)
            and int(token) < len(value)
        ):
            value = value[int(token)]
        else:
            raise ValueError("planning_projection_consistency:pointer_missing")
    return value


def _set(value: dict, path: str, replacement: Any) -> None:  # noqa: ANN401
    tokens = _tokens(path)
    parent = value if len(tokens) == 1 else _at(value, _pointer(tokens[:-1]))
    key = int(tokens[-1]) if type(parent) is list else tokens[-1]
    parent[key] = deepcopy(replacement)


def _names(model: type, registered: set[str]) -> set[str]:
    result = set(registered)
    for name in registered:
        field = model.model_fields[name]
        aliases = getattr(field.validation_alias, "choices", [])
        result.update(a for a in aliases if type(a) is str)
        if field.serialization_alias:
            result.add(field.serialization_alias)
    return result


def _inventory(candidate: dict) -> list[dict]:
    chapters = candidate.get("chapters")
    _need(type(chapters) is list and bool(chapters), "chapter_inventory")
    result = []
    for chapter in chapters:
        _need(
            type(chapter) is dict
            and type(chapter.get("chapter_number")) is int
            and chapter["chapter_number"] > 0,
            "chapter_identity",
        )
        scenes = chapter.get("scenes", [])
        _need(type(scenes) is list, "scene_inventory")
        numbers = [s.get("scene_number") if type(s) is dict else None for s in scenes]
        _need(
            all(type(n) is int and n > 0 for n in numbers) and len(numbers) == len(set(numbers)),
            "scene_identity",
        )
        result.append({"chapter_number": chapter["chapter_number"], "scene_numbers": numbers})
    _need(len({r["chapter_number"] for r in result}) == len(result), "duplicate_chapter")
    return result


def _reference_paths(candidate: dict, paths: object) -> list[str]:
    """Validate exact program-selected pointers without repairing or expanding them."""
    _need(type(paths) is list and bool(paths), "reference_paths_required")
    _need(all(type(path) is str for path in paths), "reference_path_string")
    _need(len(paths) == len(set(paths)), "duplicate_reference_path")
    for path in paths:
        parts = _tokens(path)
        _need(
            len(parts) >= 3
            and parts[0] == "chapters"
            and re.fullmatch(r"0|[1-9][0-9]*", parts[1])
            and _pointer(parts) == path,
            "reference_path_scope",
        )
        _need(not _protected_path(parts), "reference_path_protected")
        _need(type(_at(candidate, path)) in _TYPES, "reference_path_scalar")
    return deepcopy(paths)


def _reference_closure(
    candidate: dict,
    paths: list[str],
    registered: dict[str, set[str]],
    producer_roots: dict[int, list[tuple[str | int, ...]]],
) -> tuple[dict[str, set[str]], dict[str, set[str]], list[dict]]:
    selected: dict[str, set[str]] = {}
    groups: dict[str, set[str]] = {}
    bindings = []
    for path in paths:
        parts = _tokens(path)
        suffix = _reference_namespace(parts)
        _need(path in registered or suffix is not None, "reference_path_not_narrative")
        i = int(parts[1])
        family = (
            f"chapter/{i}/fanqie-contract/" + _pointer(suffix)[1:]
            if suffix
            else "exact-reference" + path
        )
        copies = [parts]
        if suffix:
            copies = [(*root, "fanqie_short_v2", *suffix) for root in producer_roots[i]]
        # A field family is an exact scalar suffix, including its list index.
        # The producer has one scene; multiple scenes give no correspondence
        # proof and must not be joined merely because their field names match.
        if len(candidate["chapters"][i].get("scenes", [])) == 1:
            # These differently named leaves are copied explicitly by the
            # segment producer; no other section or same-named key is an alias.
            aliases = {
                ("opening_contract", "immediate_stakes"): ("conflict_stakes",),
                ("continuity_contract", "fact_boundary"): ("information_control",),
            }
            tail = None
            if suffix in aliases:
                tail = aliases[suffix]
            elif suffix and len(suffix) >= 2 and suffix[1] in _SCENE_CONTROL_COPIES:
                tail = suffix[1:]
            elif parts[2:4] == ("scenes", "0"):
                local = parts[4:]
                if local and local[0] in _SCENE_CONTROL_COPIES | _SCENE_METHOD_COPIES:
                    tail = local
                elif (
                    len(local) >= 2
                    and local[0] == "methodology_contract"
                    and local[1] in _SCENE_METHOD_COPIES
                ):
                    tail = local[1:]
            has_contracts = any(
                type(_at(candidate, _pointer(root)).get("fanqie_short_v2")) is dict
                for root in producer_roots[i]
            )
            if tail and has_contracts:
                family = f"chapter/{i}/scene-control/" + _pointer(tail)[1:]
                copies = [parts, ("chapters", i, "scenes", 0, *tail)]
                if tail[0] in _SCENE_CONTROL_COPIES:
                    copies.extend(
                        (*root, "fanqie_short_v2", section, *tail)
                        for root in producer_roots[i]
                        for section in _CONTRACT_FIELDS
                    )
                if tail[0] in _SCENE_METHOD_COPIES:
                    copies.append(("chapters", i, "scenes", 0, "methodology_contract", *tail))
                for contract_suffix, scene_suffix in aliases.items():
                    if tail == scene_suffix:
                        copies.extend(
                            (*root, "fanqie_short_v2", *contract_suffix)
                            for root in producer_roots[i]
                        )
        admitted = []
        for copy_path in sorted({_pointer(copy) for copy in copies}):
            try:
                value = _at(candidate, copy_path)
            except ValueError:
                continue
            _need(not _protected_path(_tokens(copy_path)), "reference_closure_protected")
            _need(type(value) in _TYPES, "reference_closure_scalar")
            selected.setdefault(copy_path, set()).add(family)
            groups.setdefault(family, set()).add(copy_path)
            admitted.append(copy_path)
        _need(path in admitted, "reference_closure_missing")
        bindings.append(
            {
                "path": path,
                "paths": sorted(admitted),
                "family": family,
                "selection_source": "reference_paths",
            }
        )
    return selected, groups, bindings


def build_projection_consistency_contract(
    candidate: dict,
    observations: list,
    *,
    source_authority: object = None,
    reference_paths: list[str] | None = None,
) -> dict:
    """Select existing registered story leaves; retain invalid hints verbatim."""
    candidate, observations, source_authority = (
        _copy_json(v) for v in (candidate, observations, source_authority)
    )
    _need(type(candidate) is dict, "candidate_object")
    inventory = _inventory(candidate)
    if reference_paths is not None:
        reference_paths = _reference_paths(candidate, reference_paths)
    _need(
        type(observations) is list
        and bool(observations)
        and all(type(o) is dict for o in observations),
        "original_observations_required",
    )
    chapters = candidate["chapters"]
    affected, references, invalid = set(), [], []

    def scope(path: Any) -> None:  # noqa: ANN401
        match = _SCOPE.fullmatch(path) if type(path) is str else None
        if match and int(match[1]) < len(chapters):
            i = int(match[1])
            if match[2] is None or int(match[2]) < len(chapters[i].get("scenes", [])):
                affected.add(i)

    for oi, observation in enumerate(observations):
        _need(
            type(observation.get("hard_violations", [])) is list
            and type(observation.get("projection_checks", [])) is list,
            "observation_fields",
        )
        for issue in observation.get("hard_violations", []):
            _need(type(issue) is dict, "observation_issue")
            scope(issue.get("path"))
        for check in observation.get("projection_checks", []):
            _need(type(check) is dict, "observation_projection")
            if check.get("status") == "conflict":
                scope(check.get("path"))
        for ii, issue in enumerate(observation.get("hard_violations", [])):
            refs = issue.get("source_refs", [])
            _need(type(refs) is list, "observation_references")
            for ri, ref in enumerate(refs):
                row = {
                    "observation_index": oi,
                    "issue_index": ii,
                    "reference_index": ri,
                    "value": deepcopy(ref),
                }
                if type(ref) is not str or not ref.startswith("/"):
                    invalid.append({**row, "reason": "reference_syntax"})
                    continue
                if not ref.startswith("/candidate_payload/"):
                    references.append({**row, "status": "external_unverified"})
                    continue
                path = ref[len("/candidate_payload") :]
                try:
                    _at(candidate, path)
                    parts = _tokens(path)
                    _need(
                        len(parts) >= 2
                        and parts[0] == "chapters"
                        and re.fullmatch(r"0|[1-9][0-9]*", parts[1]),
                        "candidate_reference_scope",
                    )
                except ValueError:
                    invalid.append({**row, "reason": "candidate_path_missing_or_invalid"})
                    continue
                affected.add(int(parts[1]))
                references.append({**row, "path": path, "status": "candidate_present"})
    if reference_paths is not None:
        affected = {int(_tokens(path)[1]) for path in reference_paths}
    _need(bool(affected), "no_existing_affected_projection")
    selected: dict[str, set[str]] = {}
    groups: dict[str, set[str]] = {}
    producer_roots: dict[int, list[tuple[str | int, ...]]] = {}

    def add(value: Any, parts: tuple[str | int, ...], family: str) -> None:  # noqa: ANN401
        if type(value) in _TYPES:
            path = _pointer(parts)
            selected.setdefault(path, set()).add(family)
            groups.setdefault(family, set()).add(path)
        elif type(value) is list:
            for index, item in enumerate(value):
                if type(item) in _TYPES:
                    add(item, (*parts, index), family)

    def fields(obj: dict, parts: tuple[str | int, ...], keys: set[str], family: str) -> None:
        for key in sorted(keys & obj.keys()):
            add(obj[key], (*parts, key), family + "/" + key)

    def state_fields(obj: dict, parts: tuple[str | int, ...], family: str) -> None:
        for key in ("entry_state", "exit_state"):
            if type(obj.get(key)) is dict:
                fields(obj[key], (*parts, key), _STATE, family + "/" + key)

    chapter_fields = _names(ChapterOutlineInput, _CHAPTER_TEXT | _CHAPTER_LIST) | _LEGACY_CHAPTER
    scene_fields = _names(SceneOutlineInput, _SCENE_TEXT | _SCENE_LIST)
    for i in sorted(affected):
        chapter = chapters[i]
        cp = ("chapters", i)
        family = f"chapter/{i}"
        fields(chapter, cp, chapter_fields, family)
        causal = chapter.get("causal_contract")
        if type(causal) is dict:
            fields(causal, (*cp, "causal_contract"), _CAUSAL, family + "/causal")
        # The producer copies these six named contracts into all these roots.
        # Their paths, not current string equality, establish the copy family.
        roots = [
            (chapter.get(key), (*cp, key)) for key in ("causal_contract", "methodology_contract")
        ]
        for j, scene in enumerate(chapter.get("scenes", [])):
            sp = (*cp, "scenes", j)
            fields(
                scene,
                sp,
                scene_fields | {"conflict_stakes", "information_control"},
                family + "/scene",
            )
            state_fields(scene, sp, family + "/scene")
            if type(scene.get("purpose")) is dict:
                fields(scene["purpose"], (*sp, "purpose"), {"story"}, family + "/purpose")
            roots.append((scene.get("methodology_contract"), (*sp, "methodology_contract")))
        producer_roots[i] = [parts for root, parts in roots if type(root) is dict]
        for root, parts in roots:
            if type(root) is not dict:
                continue
            fields(root, parts, {"fanqie_short_payoff"}, family + "/payoff")
            fields(
                root,
                parts,
                scene_fields | {"conflict_stakes", "information_control"},
                family + "/scene",
            )
            state_fields(root, parts, family + "/scene")
            contracts = root.get("fanqie_short_v2")
            if type(contracts) is not dict:
                continue
            for name, keys in _CONTRACT_FIELDS.items():
                body = contracts.get(name)
                if type(body) is not dict:
                    continue
                bp = (*parts, "fanqie_short_v2", name)
                group = family + "/fanqie-contract/" + name
                fields(body, bp, keys | _SCENE_TEXT | _SCENE_LIST, group)
                state_fields(body, bp, group)
    dynamic = []
    for row in references:
        if row["status"] != "candidate_present":
            continue
        path = row["path"]
        parts = _tokens(path)
        if _protected_path(parts) or type(_at(candidate, path)) not in _TYPES:
            continue
        suffix = _reference_namespace(parts)
        if suffix is None:
            continue
        i = int(parts[1])
        if reference_paths is not None and i not in affected:
            continue
        family = (
            f"chapter/{i}/fanqie-contract/" + "/".join(suffix)
            if suffix
            else f"chapter/{i}/exact-native-reference" + path
        )
        copies = [parts]
        if suffix:
            copies = [(*root, "fanqie_short_v2", *suffix) for root in producer_roots[i]]
        admitted = []
        for copy in copies:
            copy_path = _pointer(copy)
            try:
                value = _at(candidate, copy_path)
            except ValueError:
                continue
            if type(value) in _TYPES:
                add(value, copy, family)
                admitted.append(copy_path)
        dynamic.append({**deepcopy(row), "paths": sorted(admitted), "family": family})
    if reference_paths is not None:
        selected, groups, dynamic = _reference_closure(
            candidate, reference_paths, selected, producer_roots
        )
    _need(bool(selected), "no_registered_narrative_leaves")
    uncovered = []
    for row in references:
        if row["status"] != "candidate_present":
            continue
        path = row["path"]
        if _protected_path(_tokens(path)):
            row["status"] = "frozen_reference"
        elif not any(p == path or p.startswith(path + "/") for p in selected):
            if reference_paths is not None:
                row["status"] = "unselected_reference"
            else:
                row["status"] = "uncovered_reference"
                uncovered.append(deepcopy(row))
    paths = sorted(selected)
    ids = {f"f{i:04d}": path for i, path in enumerate(paths, 1)}
    bindings, properties = [], {}
    for key, path in ids.items():
        before = _at(candidate, path)
        schema = {"type": _TYPES[type(before)]}
        if type(before) is str and before.strip():
            schema["pattern"] = r"\S"
        properties[key] = schema
        bindings.append(
            {
                "id": key,
                "path": path,
                "before_sha256": _hash(before),
                "value_type": type(before).__name__,
                "families": sorted(selected[path]),
            }
        )
    contract = {
        "schema": SCHEMA,
        "template": TEMPLATE,
        "version": VERSION,
        "tool_name": TOOL,
        "registry": REGISTRY,
        "tool_description_sha256": _hash(_TOOL_DESCRIPTION),
        "candidate_sha256": _hash(candidate),
        "observations": observations,
        "observations_sha256": _hash(observations),
        "source_authority": source_authority,
        "source_authority_sha256": _hash(source_authority),
        "inventory": inventory,
        "range_body_sha256": _hash(chapters),
        "affected_chapters": [
            {"index": i, "chapter_number": chapters[i]["chapter_number"]} for i in sorted(affected)
        ],
        "paths": paths,
        "id_to_path": ids,
        "bindings": bindings,
        "projection_groups": {k: sorted(v) for k, v in sorted(groups.items())},
        "references": references,
        "exact_reference_bindings": dynamic,
        "invalid_references": invalid,
        "uncovered_references": uncovered,
        "parameters": {
            "type": "object",
            "properties": properties,
            "required": list(ids),
            "additionalProperties": False,
        },
        "grade_asserted": False,
        "source_authority_granted": False,
        "full_domain_readiness_and_new_admission_required": True,
    }
    if reference_paths is not None:
        contract["reference_selection"] = {
            "schema": REFERENCE_SELECTION,
            "reference_paths": reference_paths,
            "reference_paths_sha256": _hash(reference_paths),
            "closure_paths_sha256": _hash(paths),
            "closure_groups_sha256": _hash(contract["projection_groups"]),
            "source_authority_granted": False,
        }
    contract["contract_sha256"] = _hash(contract)
    return contract


def tools_for_projection_consistency(contract: dict) -> list:
    """One native flat scalar object; no story text is put into tool instructions."""
    _need(
        type(contract) is dict
        and contract.get("schema") == SCHEMA
        and contract.get("template") == TEMPLATE
        and contract.get("version") == VERSION
        and contract.get("tool_name") == TOOL,
        "contract_identity",
    )
    _need(
        contract.get("contract_sha256")
        == _hash({k: v for k, v in contract.items() if k != "contract_sha256"}),
        "contract_changed",
    )
    _need(not contract.get("uncovered_references"), "uncovered_projection")
    _need(
        contract.get("tool_description_sha256") == _hash(_TOOL_DESCRIPTION),
        "tool_description_changed",
    )
    if "reference_selection" in contract:
        selection = contract["reference_selection"]
        _need(
            type(selection) is dict
            and selection.get("schema") == REFERENCE_SELECTION
            and type(selection.get("reference_paths")) is list
            and selection.get("reference_paths_sha256") == _hash(selection["reference_paths"])
            and selection.get("closure_paths_sha256") == _hash(contract["paths"])
            and selection.get("closure_groups_sha256") == _hash(contract["projection_groups"])
            and selection.get("source_authority_granted") is False,
            "reference_selection_changed",
        )
    return [
        {
            "type": "function",
            "function": {
                "name": TOOL,
                "description": _TOOL_DESCRIPTION,
                "parameters": deepcopy(contract["parameters"]),
            },
        }
    ]


def parse_projection_consistency(
    completion: object,
    *,
    candidate: dict,
    contract: dict,
) -> tuple[dict, dict]:
    """Derive only a new ungraded candidate; all physical/financial proof is external."""
    reference_paths = None
    if "reference_selection" in contract:
        selection = contract["reference_selection"]
        _need(
            type(selection) is dict
            and selection.get("schema") == REFERENCE_SELECTION
            and type(selection.get("reference_paths")) is list,
            "reference_selection_identity",
        )
        reference_paths = selection["reference_paths"]
    expected = build_projection_consistency_contract(
        candidate,
        contract["observations"],
        source_authority=contract["source_authority"],
        reference_paths=reference_paths,
    )
    _need(_json(contract) == _json(expected), "stale_contract")
    tools_for_projection_consistency(expected)
    dump = getattr(completion, "model_dump", None)
    native = _copy_json(dump(mode="json") if callable(dump) else completion)
    _need(
        type(native) is dict
        and native.get("finish_reason") in {"stop", "tool_calls", "end_turn", "completed"},
        "incomplete_native_return",
    )
    content, calls, raw = (native.get(k) for k in ("content", "tool_calls", "raw_message"))
    _need(type(content) is str and type(calls) is list and len(calls) == 1, "single_native_call")
    _need(native.get("function_call") is None, "legacy_function_call")
    _need(
        type(raw) is dict and raw.get("role") == "assistant" and raw.get("function_call") is None,
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
        and function["name"] == TOOL
        and type(function["arguments"]) is str,
        "native_function",
    )
    arguments = function["arguments"]
    _need(not arguments.lstrip().startswith("```"), "native_arguments_json_only")
    submitted = parse_strict_outline_json(arguments)
    _validate_wire(submitted, expected["parameters"], expected["parameters"])
    result, changed, substantive = deepcopy(candidate), [], []
    for key, path in expected["id_to_path"].items():
        before, after = _at(candidate, path), submitted[key]
        _need(type(before) is type(after), "scalar_type_changed")
        if _hash(before) != _hash(after):
            changed.append(path)
            if type(before) is not str or (
                "".join(c for c in before if c.isalnum())
                != "".join(c for c in after if c.isalnum())
            ):
                substantive.append(path)
        _set(result, path, after)
    _need(bool(substantive), "no_narrative_change")
    restored = deepcopy(result)
    for path in expected["paths"]:
        _set(restored, path, _at(candidate, path))
    _need(_json(restored) == _json(candidate), "non_target_changed")
    _need(_inventory(result) == expected["inventory"], "inventory_changed")
    receipt = {
        "schema": "planning-projection-consistency-derived.v1",
        "template": TEMPLATE,
        "version": VERSION,
        "tool_name": TOOL,
        "tool_call_id": call["id"],
        "contract_sha256": expected["contract_sha256"],
        "before_candidate_sha256": expected["candidate_sha256"],
        "candidate_sha256": _hash(result),
        "before_range_body_sha256": expected["range_body_sha256"],
        "range_body_sha256": _hash(result["chapters"]),
        "inventory_sha256": _hash(expected["inventory"]),
        "observations_sha256": expected["observations_sha256"],
        "source_authority_sha256": expected["source_authority_sha256"],
        "native_completion": native,
        "native_completion_sha256": _hash(native),
        "raw_arguments": arguments,
        "raw_arguments_sha256": hashlib.sha256(arguments.encode()).hexdigest(),
        "raw_message_sha256": _hash(raw),
        "tool_calls_sha256": _hash(calls),
        "submitted_fields_sha256": _hash(submitted),
        "changed_paths": changed,
        "non_whitespace_changed_paths": substantive,
        "non_formatting_changed_paths": substantive,
        "commentary_status": commentary,
        "non_targets_unchanged": True,
        "inverse_verified": True,
        "array_structure_unchanged": True,
        "semantic_validation": "not_evaluated",
        "grade_asserted": False,
        "source_authority_granted": False,
        "physical_custody_verified": False,
        "full_domain_readiness_and_new_admission_required": True,
    }
    if "reference_selection" in expected:
        receipt["reference_selection"] = deepcopy(expected["reference_selection"])
    return result, receipt
