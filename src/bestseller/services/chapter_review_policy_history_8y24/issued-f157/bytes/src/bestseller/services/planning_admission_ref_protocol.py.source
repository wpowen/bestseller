"""Staged chapter-only v4 selectors. No dispatch, persistence or grade migration."""

# ruff: noqa: RUF001 -- Chinese protocol instructions retain native punctuation.

from collections.abc import Iterator
from copy import deepcopy
import hashlib
import json
from uuid import UUID

from bestseller.services import planning_draft_admission as admission
from bestseller.services.planning_admission_submission import (
    parse_planning_admission,
    planning_admission_tool,
)

SCHEMA = "planning-draft-admission.v4"
REFERENCE_POLICY = "planning-admission-exact-leaf-refs.v1"
SYSTEM = admission.SYSTEM + (
    "本轮为显式选择的v4叶子引用协议。source_reference_catalog仅是本次完整input的路径索引，"
    "不是额外故事要求。source_refs只能逐字选择工具enum内的完整实际叶子路径，禁止补层、"
    "删层、别名、父对象、组合路径或自行缩写。本v4版本覆盖前文候选source_refs的candidate:点号规则："
    "所有source_refs唯一语法是实际RFC6901 JSON pointer；候选以/candidate_payload/chapters/开头，"
    "外部来源仍用原始JSON pointer。~和/在键内分别编码为~0和~1，不作任何别名归一化。"
    "权限仍由原role/code规则决定，引用存在不证明语义成立。"
    "None、空值和未知内容不自动成为事实或禁令；完整原始候选及来源才是审查对象。"
)


def _need(condition: bool, code: str) -> None:
    if not condition:
        raise ValueError("admission_refs_v4:" + code)


def _json(value: object) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def _sha(value: object) -> str:
    return hashlib.sha256(_json(value).encode()).hexdigest()


POLICY = _sha(
    {
        "schema": SCHEMA,
        "system": SYSTEM,
        "base_policy": admission.HARD_ADMISSION_POLICY,
        "grade_schema": admission._Grade.model_json_schema(),
        "reference_policy": REFERENCE_POLICY,
        "refs": "exact scalar leaves; unchanged authority; no alias or container selection",
    }
)


def _leaves(
    value: object, parts: tuple[str | int, ...] = ()
) -> Iterator[tuple[tuple[str | int, ...], object]]:
    if isinstance(value, dict):
        _need(all(type(key) is str for key in value), "json_object_keys")
        for key in sorted(value):
            yield from _leaves(value[key], (*parts, key))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _leaves(child, (*parts, index))
    else:
        _need(value is None or type(value) in {str, int, float, bool}, "json_scalar")
        _json(value)
        yield parts, value


def _pointer(parts: tuple[str | int, ...]) -> str:
    return "/" + "/".join(str(part).replace("~", "~0").replace("/", "~1") for part in parts)


def _candidate_role(parts: tuple[str | int, ...], value: object) -> str:
    fields = [part for part in parts if isinstance(part, str)]
    if (
        not parts
        or parts[0] != "chapters"
        or any(
            part in {"_meta", "metadata", "planned_frontiers"}
            or part.endswith(("_hash", "_sha256"))
            for part in fields
        )
    ):
        return "provenance"
    if value is None or value == "":
        return "unasserted"
    return "candidate_order"


def _authority(base: dict) -> dict:
    result = admission._source_authority(base)
    if base["schema"] == SCHEMA:
        for parts, value in _leaves(base["candidate_payload"]):
            role = _candidate_role(parts, value)
            if role == "unasserted":
                continue
            result.setdefault(role, {}).setdefault("/candidate_payload", []).append(_pointer(parts))
    return {
        role: {prefix: sorted(suffixes) for prefix, suffixes in sorted(groups.items())}
        for role, groups in sorted(result.items())
    }


def _permissions(authority: dict) -> dict:
    return {
        role: sorted(
            code for code, roles in admission._SOURCE_ROLES_BY_CODE.items() if role in roles
        )
        for role in authority
    }


def reference_contract(supplied: dict) -> dict:
    """Catalog the complete input without giving it financial or grade authority.

    Empty containers have no scalar reference. Unknown/None values remain in
    the full input. Every candidate key uses exact RFC6901 escaping.
    """
    _need(isinstance(supplied, dict), "input_object")
    list(_leaves(supplied))  # Reject non-JSON values, nonfinite numbers and non-string keys.
    base = {key: value for key, value in supplied.items() if key != "source_reference_catalog"}
    _need(base.get("schema") in {admission.SCHEMA, SCHEMA}, "schema")
    expected_policy = POLICY if base["schema"] == SCHEMA else admission.HARD_ADMISSION_POLICY
    _need(base.get("hard_admission_policy") == expected_policy, "policy")
    candidate = base["candidate_payload"]
    _need(admission._candidate_kind(candidate) == "chapters", "chapter_only")
    targets = admission.projection_audit_targets(candidate)
    _need(_json(targets) == _json(base.get("projection_audit_targets")), "projection_targets")
    authority = _authority(base)
    _need(_json(authority) == _json(base.get("source_authority")), "authority_changed")
    allowed = _permissions(authority)
    _need(
        _json(allowed) == _json(base.get("source_authority_allowed_codes")), "permissions_changed"
    )
    roles = admission._authority_roles(authority)
    catalog = {}
    for parts, value in _leaves(base):
        pointer = _pointer(parts)
        if parts[0] == "candidate_payload":
            ref, role = pointer, _candidate_role(parts[1:], value)
        elif pointer in roles:
            ref, role = pointer, roles[pointer]
        else:
            continue
        _need(ref not in catalog, "duplicate_reference")
        catalog[ref] = {
            "pointer": pointer,
            "role": role,
            "allowed_codes": sorted(
                code for code, roles in admission._SOURCE_ROLES_BY_CODE.items() if role in roles
            ),
            "value_sha256": _sha(value),
        }
    catalog = dict(sorted(catalog.items()))
    contract = {
        "schema": REFERENCE_POLICY,
        "input_sha256": _sha(base),
        "candidate_sha256": _sha(candidate),
        "catalog_sha256": _sha(catalog),
        "projection_targets_sha256": _sha(targets),
        "catalog": catalog,
    }
    if "source_reference_catalog" in supplied:
        _need(
            _json(supplied["source_reference_catalog"]) == _json(contract_view(contract)),
            "stored_catalog_changed",
        )
    return contract


def contract_view(contract: dict) -> dict:
    """Hashes only in the prompt; exact eligible paths already occur in tool enum."""
    return {key: deepcopy(value) for key, value in contract.items() if key != "catalog"}


def v4_input(supplied: dict) -> dict:
    """Explicit opt-in input rendering only; does not authorize use on an old WF."""
    _need(supplied.get("schema") == admission.SCHEMA, "base_schema")
    reference_contract(supplied)
    result = deepcopy(supplied)
    result.update(schema=SCHEMA, hard_admission_policy=POLICY)
    result["source_authority"] = _authority(result)
    result["source_authority_allowed_codes"] = _permissions(result["source_authority"])
    result["source_reference_catalog"] = contract_view(reference_contract(result))
    return result


def reference_tool(grade_schema: dict, supplied: dict) -> tuple[dict, dict]:
    """Existing native Grade shape, with one finite full-path source_refs enum."""
    _need(supplied.get("schema") == SCHEMA, "explicit_opt_in_required")
    contract = reference_contract(supplied)
    tool = planning_admission_tool(grade_schema, supplied["projection_audit_targets"])
    refs = [ref for ref, row in contract["catalog"].items() if row["allowed_codes"]]
    _need(bool(refs), "empty_reference_enum")
    field = tool["function"]["parameters"]["properties"]["hard_violations"]["items"]["properties"][
        "source_refs"
    ]
    field.update(uniqueItems=True)
    field["items"] = {"type": "string", "enum": refs}
    # No story values, source-provided descriptions or new instructions in the tool.
    return tool, contract


def parse_reference_submission(
    completion: dict, *, supplied: dict, contract: dict
) -> tuple[str, dict]:
    """Preserve physical native arguments; exact refs then existing strict semantics.

    This is protocol validity only. The producer must separately prove the
    paid operation and fresh input/control before publishing any new receipt.
    """
    _need(supplied.get("schema") == SCHEMA, "explicit_opt_in_required")
    _need(completion.get("finish_reason") in {"stop", "tool_calls"}, "incomplete_completion")
    current = reference_contract(supplied)
    _need(_json(current) == _json(contract), "stale_contract")

    def validate(payload: dict) -> None:
        from bestseller.services.planning_admission_submission import _shape

        _shape(payload)
        for violation in payload["hard_violations"]:
            refs = violation["source_refs"]
            _need(len(refs) == len(set(refs)), "duplicate_source_ref")
            for ref in refs:
                row = current["catalog"].get(ref)
                _need(row is not None, "reference_not_in_catalog")
                _need(violation["code"] in row["allowed_codes"], "reference_authority")
        admission._grade(_json(payload), supplied["candidate_payload"], supplied)

    raw, physical = parse_planning_admission(completion, payload_validator=validate)
    return raw, {
        **physical,
        "selector_schema": SCHEMA,
        "reference_policy": REFERENCE_POLICY,
        "reference_binding": {key: value for key, value in current.items() if key != "catalog"},
        "refs_modified": False,
        "semantic_entailment": "not_evaluated",
        "new_grade_or_adoption": False,
    }


def require_changed_candidate(
    previous: dict,
    current: dict,
    *,
    previous_artifact_id: UUID | str,
    current_artifact_id: UUID | str,
) -> dict:
    """Necessary content-change check, not a dispatcher or DB artifact verifier."""
    _need(
        UUID(str(previous_artifact_id)) != UUID(str(current_artifact_id)), "new_artifact_required"
    )
    _need(previous.get("project_id") == current.get("project_id"), "project_changed")
    old, new = reference_contract(previous), reference_contract(current)

    def semantic_rows(contract: dict) -> dict:
        return {
            ref: row["value_sha256"]
            for ref, row in contract["catalog"].items()
            if row["role"] == "candidate_order"
        }

    before, after = semantic_rows(old), semantic_rows(new)
    changed = sorted(
        ref for ref in before.keys() | after.keys() if before.get(ref) != after.get(ref)
    )
    _need(bool(changed), "actual_candidate_change_required")
    return {
        "previous_artifact_id": str(previous_artifact_id),
        "current_artifact_id": str(current_artifact_id),
        "old_candidate_sha256": old["candidate_sha256"],
        "new_candidate_sha256": new["candidate_sha256"],
        "changed_candidate_refs": changed,
        "new_grade_or_adoption": False,
    }
