"""Prospective v5 native selectors; no dispatch, persistence or old-grade repair."""

# ruff: noqa: RUF001 -- Versioned Chinese protocol instructions.

from copy import deepcopy
import hashlib
import json
import re

from bestseller.services import planning_admission_ref_protocol as v4
from bestseller.services import planning_draft_admission as admission
from bestseller.services.planning_admission_submission import (
    _shape,
    parse_planning_admission,
    planning_admission_tool,
)
from bestseller.services.retained_planning_clauses import resolve_pointer

SCHEMA = "planning-draft-admission.v5"
REFERENCE_POLICY = "planning-admission-exact-leaf-ids.v1"
NORMALIZATION_SCHEMA = "planning-admission-id-normalization.v1"
SYSTEM = admission.SYSTEM + (
    "本轮显式采用v5，以下仅覆盖旧引用语法和提交形状，不改变硬问题类别或来源权限。"
    "唯一提交渠道仍为submit_planning_admission工具，禁止content提交JSON或第二份评分。"
    "source_reference_catalog是本次完整input的精确叶子ID目录，不是新增故事要求。"
    "每条hard_violations额外必须含非空evidence_refs：照录候选证据叶子的短ID，"
    "至少一个位于该path章/场景内；跨场景证据可额外引用其他实际候选叶子。"
    "source_refs只填本code工具分支允许的来源短ID；候选证据不能充当能力规则或作者要求。"
    "candidate_order仅保留原CONTINUITY_CONFLICT和SCENE_NOT_EXECUTABLE的计划顺序权限，"
    "不证明已发生事实或作者授权。两种refs均只准目录ID，不准pointer、别名或自行缩写。"
    "evidence须为本path内evidence_refs叶子的逐字连续片段，source_requirement须为"
    "合法source_refs叶子的逐字连续片段；解释放projection_checks.explanation。"
    "非字符串标量仅可逐字引用其JSON值，None、空值、技术provenance及advisory不获得硬权限。"
    "每条违反必须保留code/path/evidence/source_requirement/source_refs/evidence_refs六项。"
    "完整原候选、来源、未知字段和历史诊断均保留；历史UNKNOWN、False和耗尽的协议不重解、"
    "不补别名、不删除坏引用。新输入是否可付费、是否有真正候选变化由外层另行验证。"
)


def _need(condition: bool, code: str) -> None:
    if not condition:
        raise ValueError("admission_ids_v5:" + code)


def _json(value: object) -> str:
    # Reuse the exact v4 JSON/key/scalar boundary, including typed scalar hashes.
    list(v4._leaves(value))
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def _sha(value: object) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


POLICY = _sha(
    {
        "schema": SCHEMA,
        "system": SYSTEM,
        "hard_base_policy": admission.HARD_ADMISSION_POLICY,
        "v4_catalog_policy": v4.POLICY,
        "grade_schema": admission._Grade.model_json_schema(),
        "reference_policy": REFERENCE_POLICY,
        "normalization_schema": NORMALIZATION_SCHEMA,
        "native_only": True,
        "ids": "sorted exact v4 scalar-leaf pointers; no aliases",
        "evidence": (
            "required candidate_order; local quoted witness; cross-scope additions retained"
        ),
        "source": "unchanged allowed roles per code; quoted leaf requirement",
    }
)


def reference_contract(supplied: dict) -> dict:
    """Recompute the complete ID bijection and validate any stored catalog."""
    _need(type(supplied) is dict and supplied.get("schema") == SCHEMA, "explicit_opt_in_required")
    _need(supplied.get("hard_admission_policy") == POLICY, "policy")
    _json(supplied)
    base = {
        key: deepcopy(value) for key, value in supplied.items() if key != "source_reference_catalog"
    }
    old = deepcopy(base)
    old.update(schema=v4.SCHEMA, hard_admission_policy=v4.POLICY)
    original = v4.reference_contract(old)
    catalog = {
        f"r{index:06d}": deepcopy(row)
        for index, (_, row) in enumerate(sorted(original["catalog"].items()), start=1)
    }
    contract = {
        "schema": REFERENCE_POLICY,
        "policy": POLICY,
        "input_sha256": _sha(base),
        "candidate_sha256": original["candidate_sha256"],
        "projection_targets_sha256": original["projection_targets_sha256"],
        "v4_catalog_sha256": original["catalog_sha256"],
        "catalog_sha256": _sha(catalog),
        "catalog": catalog,
    }
    if "source_reference_catalog" in supplied:
        _need(
            _json(supplied["source_reference_catalog"]) == _json(contract), "stored_catalog_changed"
        )
    return contract


def v5_input(supplied: dict) -> dict:
    """Explicit new-input rendering from the original v3 input, never a receipt."""
    _need(type(supplied) is dict and supplied.get("schema") == admission.SCHEMA, "base_schema")
    result = v4.v4_input(supplied)
    result.pop("source_reference_catalog")
    result.update(schema=SCHEMA, hard_admission_policy=POLICY)
    result["source_reference_catalog"] = reference_contract(result)
    return result


def reference_tool(grade_schema: dict, supplied: dict) -> tuple[dict, dict]:
    """Code-specific authority enums and a separate required candidate witness."""
    _need(
        _json(grade_schema) == _json(admission._Grade.model_json_schema()), "grade_schema_changed"
    )
    contract = reference_contract(supplied)
    tool = planning_admission_tool(grade_schema, supplied["projection_audit_targets"])
    properties = tool["function"]["parameters"]["properties"]
    original = properties["hard_violations"]["items"]
    evidence = [key for key, row in contract["catalog"].items() if row["role"] == "candidate_order"]
    _need(bool(evidence), "candidate_evidence_unavailable")
    branches = []
    for code in sorted(admission._SOURCE_ROLES_BY_CODE):
        sources = [key for key, row in contract["catalog"].items() if code in row["allowed_codes"]]
        if not sources:
            continue
        branch = deepcopy(original)
        branch["properties"]["code"] = {"type": "string", "enum": [code]}
        branch["properties"]["source_refs"] = {
            "type": "array",
            "minItems": 1,
            "uniqueItems": True,
            "items": {"type": "string", "enum": sources},
        }
        branch["properties"]["evidence_refs"] = {
            "type": "array",
            "minItems": 1,
            "uniqueItems": True,
            "items": {"type": "string", "enum": evidence},
        }
        branch["required"] = list(branch["properties"])
        branch["additionalProperties"] = False
        branches.append(branch)
    _need(bool(branches), "source_authority_unavailable")
    properties["hard_violations"]["items"] = {"oneOf": branches}
    return tool, contract


def _resolve_refs(refs: object, catalog: dict, supplied: dict) -> list[dict]:
    _need(
        type(refs) is list and bool(refs) and all(type(ref) is str for ref in refs),
        "reference_list",
    )
    _need(len(refs) == len(set(refs)), "duplicate_reference")
    result = []
    for ref in refs:
        _need(ref in catalog, "reference_not_in_catalog")
        row = catalog[ref]
        value = resolve_pointer(supplied, row["pointer"])
        _need(_sha(value) == row["value_sha256"], "reference_value_changed")
        result.append({"id": ref, **deepcopy(row), "value": deepcopy(value)})
    return result


def _quoted(text: str, rows: list[dict]) -> bool:
    return any(
        text in (row["value"] if type(row["value"]) is str else _json(row["value"])) for row in rows
    )


def parse_reference_submission(
    completion: dict, *, supplied: dict, contract: dict
) -> tuple[str, dict]:
    """Normalize only newly submitted v5 IDs; custody and grade publication are external."""
    _need(
        type(completion) is dict and completion.get("finish_reason") in {"stop", "tool_calls"},
        "incomplete_completion",
    )
    calls = completion.get("tool_calls")
    _need(type(calls) is list and len(calls) == 1, "native_submission_required")
    current = reference_contract(supplied)
    _need(_json(current) == _json(contract), "stale_contract")
    normalized = None
    resolved = []

    def validate(payload: dict) -> None:
        nonlocal normalized
        _need(type(payload) is dict, "payload_fields")
        normalized = deepcopy(payload)
        violations = payload.get("hard_violations")
        _need(type(violations) is list, "hard_violations")
        for index, violation in enumerate(violations):
            _need(
                type(violation) is dict
                and set(violation)
                == {
                    "code",
                    "path",
                    "evidence",
                    "source_requirement",
                    "source_refs",
                    "evidence_refs",
                },
                "violation_fields",
            )
            # Validate the old native shape without mutating any original field.
            normalized["hard_violations"][index].pop("evidence_refs")
        _shape(normalized)
        targets = {row["path"] for row in supplied["projection_audit_targets"]}
        for index, violation in enumerate(violations):
            _need(violation["path"] in targets, "violation_scope")
            source = _resolve_refs(violation["source_refs"], current["catalog"], supplied)
            evidence = _resolve_refs(violation["evidence_refs"], current["catalog"], supplied)
            _need(
                all(violation["code"] in row["allowed_codes"] for row in source), "source_authority"
            )
            _need(
                all(row["role"] == "candidate_order" for row in evidence), "candidate_evidence_role"
            )
            parts = re.findall(r"[A-Za-z_][A-Za-z_0-9]*|\d+", violation["path"])
            prefix = "/candidate_payload/" + "/".join(parts) + "/"
            local = [row for row in evidence if row["pointer"].startswith(prefix)]
            _need(bool(local), "candidate_evidence_scope")
            _need(_quoted(violation["evidence"], local), "candidate_evidence_unbound")
            _need(_quoted(violation["source_requirement"], source), "source_requirement_unbound")
            normalized["hard_violations"][index]["source_refs"] = [row["pointer"] for row in source]
            resolved.append(
                {
                    "violation_index": index,
                    "path": violation["path"],
                    "code": violation["code"],
                    "evidence_refs": evidence,
                    "source_refs": source,
                }
            )
        admission._grade(_json(normalized), supplied["candidate_payload"], supplied)

    raw, physical = parse_planning_admission(completion, payload_validator=validate)
    _need(physical.get("channel") == "tool", "native_submission_required")
    derived_raw = _json(normalized)
    tool, _ = reference_tool(admission._Grade.model_json_schema(), supplied)
    return derived_raw, {
        "schema": NORMALIZATION_SCHEMA,
        "selector_schema": SCHEMA,
        "policy": POLICY,
        "reference_policy": REFERENCE_POLICY,
        "physical_submission": physical,
        "raw_arguments": raw,
        "raw_arguments_sha256": hashlib.sha256(raw.encode("utf-8")).hexdigest(),
        "original_payload": deepcopy(physical["parsed_payload"]),
        "physical_return_sha256": _sha(completion),
        "reference_contract": current,
        "tool_sha256": _sha(tool),
        "resolved_violations": resolved,
        "normalized_payload": deepcopy(normalized),
        "normalized_raw_grade_sha256": hashlib.sha256(derived_raw.encode("utf-8")).hexdigest(),
        "normalized_grade_sha256": _sha(normalized),
        "text_modified": False,
        "issues_dropped": 0,
        "evidence_refs_preserved_in_receipt": True,
        "semantic_entailment": "not_evaluated",
        "new_grade_or_adoption": False,
        "old_receipts_reinterpreted": False,
    }


def protocol_feedback(completion: dict, supplied: dict) -> list[dict]:
    """All resolvable v5 reference errors, never a patch or a source authority.

    Full allowed-ID sets occur once in a leading context record. Each issue
    names its exact set and hash; selected known leaves retain their typed
    values. No alias is resolved and no reasoning is read as a submission.
    """
    flags = {"diagnostic_only": True, "auto_fix": False, "grade_asserted": False}
    issues = []
    sets = {}

    def issue(loc: list[str | int], code: str, **details: object) -> None:
        issues.append(
            {
                "schema": "planning-admission-id-feedback.v1",
                "loc": loc,
                "code": code,
                **flags,
                **details,
            }
        )

    try:
        current = reference_contract(supplied)
    except (ValueError, TypeError, KeyError) as exc:
        issue([], "input_contract_invalid", detail=str(exc))
        return issues

    def allowed(name: str, values: list[str]) -> dict:
        record = {"values": values, "total": len(values), "sha256": _sha(values)}
        sets[name] = record
        return {"allowed_id_set": name, "allowed_ids_sha256": record["sha256"]}

    catalog = current["catalog"]
    witnesses = [key for key, row in catalog.items() if row["role"] == "candidate_order"]
    evidence_set = allowed("evidence:candidate_order", witnesses)
    source_sets = {
        code: allowed(
            "source:" + code, [key for key, row in catalog.items() if code in row["allowed_codes"]]
        )
        for code in sorted(admission._SOURCE_ROLES_BY_CODE)
    }
    bindings = {}

    def binding(ref: str) -> dict:
        if ref not in bindings:
            row = catalog[ref]
            value = resolve_pointer(supplied, row["pointer"])
            _need(_sha(value) == row["value_sha256"], "reference_value_changed")
            bindings[ref] = {"id": ref, **deepcopy(row), "value": deepcopy(value)}
        return deepcopy(bindings[ref])

    def result() -> list[dict]:
        if not issues:
            return []
        return [
            {
                "schema": "planning-admission-id-feedback.v1",
                "loc": [],
                "code": "current_reference_id_context",
                **flags,
                "input_sha256": current["input_sha256"],
                "catalog_sha256": current["catalog_sha256"],
                "allowed_id_sets": sets,
            },
            *issues,
        ]

    if type(completion) is not dict or completion.get("finish_reason") not in {
        "stop",
        "tool_calls",
    }:
        issue(["finish_reason"], "incomplete_completion")
        return result()
    calls = completion.get("tool_calls")
    if type(calls) is not list or len(calls) != 1:
        issue(["tool_calls"], "native_submission_required")
        return result()
    try:
        _, physical = parse_planning_admission(completion, payload_validator=lambda _payload: None)
    except ValueError as exc:
        issue(["tool_calls"], "native_submission_invalid", detail=str(exc))
        return result()
    payload = physical["parsed_payload"]
    if type(payload) is not dict:
        issue([], "payload_fields")
        return result()
    root_fields = {"draft_eligible", "hard_violations", "editorial_notes", "projection_checks"}
    for field in sorted(root_fields - payload.keys()):
        issue([field], "missing_field")
    for field in sorted(payload.keys() - root_fields):
        issue([field], "extra_field")
    violations = payload.get("hard_violations")
    if type(violations) is not list:
        issue(["hard_violations"], "hard_violations_type")
        return result()
    targets = {row["path"] for row in supplied["projection_audit_targets"]}
    fields = {"code", "path", "evidence", "source_requirement", "source_refs", "evidence_refs"}
    for index, violation in enumerate(violations):
        loc = ["hard_violations", index]
        if type(violation) is not dict:
            issue(loc, "violation_type")
            continue
        code = violation.get("code")
        code_known = type(code) is str and code in source_sets
        for field in sorted(fields - violation.keys()):
            details = (
                evidence_set
                if field == "evidence_refs"
                else source_sets.get(code, {})
                if field == "source_refs" and code_known
                else {}
            )
            issue([*loc, field], "missing_field", **details)
        for field in sorted(violation.keys() - fields):
            issue([*loc, field], "extra_field")
        if not code_known:
            issue([*loc, "code"], "violation_code", allowed_codes=list(source_sets))
        scope = violation.get("path")
        scope_known = type(scope) is str and scope in targets
        if not scope_known:
            issue([*loc, "path"], "violation_scope", allowed_paths=sorted(targets))
        for field in ("evidence", "source_requirement"):
            if field in violation and (
                type(violation[field]) is not str or not violation[field].strip()
            ):
                issue([*loc, field], "violation_text")
        selected = {}
        for field in ("source_refs", "evidence_refs"):
            if field not in violation:
                continue
            refs = violation[field]
            permitted = (
                evidence_set
                if field == "evidence_refs"
                else source_sets.get(code)
                if code_known
                else None
            )
            details = permitted or {}
            if type(refs) is not list or not refs:
                issue([*loc, field], "reference_list", **details)
                continue
            good = []
            seen = set()
            for position, ref in enumerate(refs):
                at = [*loc, field, position]
                if type(ref) is not str:
                    issue(at, "reference_type", value=ref, **details)
                    continue
                if ref in seen:
                    issue(at, "duplicate_reference", value=ref, **details)
                seen.add(ref)
                if ref not in catalog:
                    issue(at, "reference_not_in_catalog", value=ref, **details)
                    continue
                actual = binding(ref)
                eligible = (
                    actual["role"] == "candidate_order"
                    if field == "evidence_refs"
                    else code_known and code in actual["allowed_codes"]
                )
                if not eligible:
                    issue(
                        at,
                        "candidate_evidence_role"
                        if field == "evidence_refs"
                        else "source_authority",
                        value=ref,
                        actual_binding=actual,
                        **details,
                    )
                else:
                    good.append(actual)
            selected[field] = good
        source = selected.get("source_refs", [])
        requirement = violation.get("source_requirement")
        if (
            source
            and type(requirement) is str
            and requirement.strip()
            and not _quoted(requirement, source)
        ):
            issue(
                [*loc, "source_requirement"],
                "source_requirement_unbound",
                actual_bindings=source,
                **source_sets[code],
            )
        evidence = selected.get("evidence_refs", [])
        if scope_known and evidence:
            prefix = (
                "/candidate_payload/"
                + "/".join(re.findall(r"[A-Za-z_][A-Za-z_0-9]*|\d+", scope))
                + "/"
            )
            local_ids = [key for key in witnesses if catalog[key]["pointer"].startswith(prefix)]
            local_set = allowed("evidence:local:" + scope, local_ids)
            local = [row for row in evidence if row["pointer"].startswith(prefix)]
            if not local:
                issue(
                    [*loc, "evidence_refs"],
                    "candidate_evidence_scope",
                    actual_bindings=evidence,
                    **local_set,
                )
            text = violation.get("evidence")
            if local and type(text) is str and text.strip() and not _quoted(text, local):
                issue(
                    [*loc, "evidence"],
                    "candidate_evidence_unbound",
                    actual_bindings=local,
                    **local_set,
                )
    if not issues:
        # Preserve the exact shared shape/projection/grade checks as the final
        # boundary. This is diagnostic validation, never a published grade.
        try:
            parse_reference_submission(completion, supplied=supplied, contract=current)
        except ValueError as exc:
            issue([], "submission_invalid", detail=str(exc))
    return result()


__all__ = [
    "parse_reference_submission",
    "protocol_feedback",
    "reference_contract",
    "reference_tool",
    "v5_input",
]
