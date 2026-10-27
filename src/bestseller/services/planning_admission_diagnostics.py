"""Pure, non-authorizing protocol feedback for retained planning submissions."""

from collections.abc import Callable, Iterable, Mapping
import json
import re

from pydantic import ValidationError

_PATH = r"chapters\[\d+\](?:\.[A-Za-z_][A-Za-z_0-9]*|\[\d+\])*"
_CATALOG_LIMIT = 32


def collect_protocol_feedback(
    raw: str,
    candidate: Mapping,
    supplied: Mapping | None,
    grade_validator: Callable | None = None,
) -> list[dict]:
    """Collect protocol errors without repairing input or returning a grade.

    Optional grade_validator has the existing _grade(raw, candidate, supplied)
    signature. It must be pure; it remains authoritative, never a replacement
    for normal admission. Allowed-value catalogs are bounded, diagnostics are
    not capped. An empty list does not establish semantic correctness.
    """
    # Lazy imports allow the admission service to import this function itself.
    from bestseller.services import planning_draft_admission as admission
    from bestseller.services.prompt_source_policy import content_hash

    errors = []

    def catalog(values: Iterable[str]) -> dict:
        values = sorted(set(values))
        return {
            "values": values[:_CATALOG_LIMIT],
            "total": len(values),
            "omitted": max(0, len(values) - _CATALOG_LIMIT),
            "sha256": content_hash(values),
        }

    def add(loc: Iterable, code: str, value: object, **details: object) -> None:
        errors.append({"loc": list(loc), "code": code, "value": value, **details})

    class Pairs(list):
        pass

    class Nonfinite(str):
        pass

    def unpack(value: object, loc: tuple = ()) -> object:
        if isinstance(value, Pairs):
            result = {}
            for key, item in value:
                if key in result:
                    add((*loc, key), "admission_duplicate_json_key", key)
                result[key] = unpack(item, (*loc, key))
            return result
        if isinstance(value, list):
            return [unpack(item, (*loc, i)) for i, item in enumerate(value)]
        if isinstance(value, Nonfinite):
            add(loc, "admission_nonfinite_json", str(value))
            return None
        return value

    try:
        parsed = unpack(json.loads(raw, object_pairs_hook=Pairs, parse_constant=Nonfinite))
    except (ValueError, TypeError) as exc:
        add(
            (),
            "admission_json_invalid",
            None,
            error_type=type(exc).__name__,
            line=getattr(exc, "lineno", None),
            column=getattr(exc, "colno", None),
        )
        return errors
    json_invalid = bool(errors)
    try:
        grade = admission._Grade.model_validate(parsed)
    except ValidationError as exc:
        for issue in exc.errors(include_input=False, include_url=False, include_context=False):
            value = parsed
            try:
                for part in issue["loc"]:
                    value = value[part]
            except (KeyError, IndexError, TypeError):
                value = None
            # Do not echo a full source/object for a schema-level error.
            if isinstance(value, (dict, list)):
                value = {"type": type(value).__name__, "sha256": content_hash(value)}
            add(issue["loc"], "admission_schema_invalid", value, schema_code=issue["type"])
        data = parsed
    else:
        data = grade.model_dump(mode="json")
    if not isinstance(data, Mapping):
        return errors

    targets = {
        item["path"]: item["projection_hash"]
        for item in admission.projection_audit_targets(candidate)
    }
    allowed_targets = catalog(targets)
    violations = data.get("hard_violations")
    checks = data.get("projection_checks")
    violations = violations if isinstance(violations, list) else []
    checks = checks if isinstance(checks, list) else []
    paths = {
        v["path"] for v in violations if isinstance(v, Mapping) and isinstance(v.get("path"), str)
    }
    if type(data.get("draft_eligible")) is bool and isinstance(data.get("hard_violations"), list):
        if data["draft_eligible"] != (not violations):
            add(("draft_eligible",), "admission_inconsistent_grade", data["draft_eligible"])

    seen = set()
    for i, check in enumerate(checks):
        if not isinstance(check, Mapping):
            continue
        loc = ("projection_checks", i)
        path = check.get("path")
        if not isinstance(path, str):
            continue
        if path in seen:
            add((*loc, "path"), "admission_duplicate_projection_check", path)
        seen.add(path)
        if path not in targets:
            add(
                (*loc, "path"),
                "admission_projection_coverage_mismatch",
                path,
                allowed_target_scopes=allowed_targets,
            )
        elif check.get("projection_hash") != targets[path]:
            add(
                (*loc, "projection_hash"),
                "admission_projection_hash_mismatch",
                check.get("projection_hash"),
                expected=targets[path],
            )
        related = check.get("related_violation_paths", [])
        if not isinstance(related, list):
            continue
        prior = set()
        for j, ref in enumerate(related):
            if not isinstance(ref, str):
                continue
            reason = "repeated" if ref in prior else "dangling" if ref not in paths else None
            if reason:
                add(
                    (*loc, "related_violation_paths", j),
                    "admission_projection_dependency_invalid",
                    ref,
                    reason=reason,
                    allowed_violation_paths=catalog(paths),
                )
            prior.add(ref)
        if check.get("status") == "consistent" and related:
            add(
                (*loc, "related_violation_paths"),
                "admission_projection_dependency_invalid",
                related,
                reason="consistent_has_dependencies",
            )
        if check.get("status") == "conflict":
            if data.get("draft_eligible") is True:
                add((*loc, "status"), "admission_projection_conflict_eligible", "conflict")
            if not related and not any(
                p == path or p.startswith(path + ".") or p.startswith(path + "[") for p in paths
            ):
                add((*loc, "status"), "admission_projection_conflict_unexplained", "conflict")
    for path in sorted(targets.keys() - seen):
        add(
            ("projection_checks",), "admission_projection_coverage_mismatch", path, reason="missing"
        )

    source_catalog = supplied.get("source_authority") if isinstance(supplied, Mapping) else None
    if supplied is not None and not isinstance(source_catalog, Mapping):
        add(
            ("supplied", "source_authority"),
            "admission_diagnostic_context_invalid",
            None,
            reason="source_authority_required",
        )
    source_catalog = source_catalog if isinstance(source_catalog, Mapping) else {}
    authority = admission._authority_roles(source_catalog)

    def path_error(path: str) -> str:
        bare = path[10:] if path.startswith("candidate:") else path
        pattern = (r"volumes\[\d+\](?:\.[A-Za-z_][A-Za-z_0-9]*|\[\d+\])*"
                   if isinstance(candidate, Mapping) and "volumes" in candidate and "chapters" not in candidate else _PATH)
        if re.fullmatch(pattern, bare) is None:
            return "single_candidate_path_required"
        if path.startswith("candidate:"):
            return "candidate_prefix_not_allowed"
        return "missing_or_out_of_bounds"

    for i, violation in enumerate(violations):
        if not isinstance(violation, Mapping):
            continue
        path = violation.get("path")
        if isinstance(path, str) and path not in targets:
            add(
                ("hard_violations", i, "path"),
                "admission_violation_scope_invalid",
                path,
                allowed_target_scopes=allowed_targets,
            )
        if isinstance(path, str) and not admission._candidate_path_exists(candidate, path):
            add(
                ("hard_violations", i, "path"),
                "admission_violation_path_invalid",
                path,
                reason=path_error(path),
                candidate_prefix_present=path.startswith("candidate:"),
                allowed_target_scopes=allowed_targets,
            )
        refs = violation.get("source_refs")
        code = violation.get("code")
        if not isinstance(refs, list) or not isinstance(code, str):
            continue
        roles = admission._SOURCE_ROLES_BY_CODE.get(code, set())
        for j, ref in enumerate(refs):
            if not isinstance(ref, str):
                continue
            loc = ("hard_violations", i, "source_refs", j)
            role = authority.get(ref)
            if ref.startswith("candidate:"):
                if admission._candidate_path_exists(candidate, ref[10:]):
                    role = "candidate_order"
                else:
                    add(
                        loc,
                        "admission_candidate_ref_invalid",
                        ref,
                        reason=path_error(ref[10:]),
                        allowed_target_scopes=allowed_targets,
                    )
            elif not ref.startswith("/") or re.search(r"~(?:[^01]|$)", ref):
                add(
                    loc,
                    "admission_source_pointer_invalid",
                    ref,
                    reason="absolute_json_pointer_required",
                )
            if supplied is not None and role not in roles:
                prefixes = {
                    prefix
                    for allowed_role, groups in source_catalog.items()
                    if allowed_role in roles
                    for prefix in groups
                }
                add(
                    loc,
                    "admission_source_authority_invalid",
                    ref,
                    actual_role=role,
                    allowed_roles=sorted(roles),
                    allowed_authority_prefixes=catalog(prefixes),
                )

    if grade_validator is not None and not json_invalid:
        try:
            grade_validator(raw, candidate, supplied)
        except (ValueError, TypeError) as exc:
            if not any(item["code"] == str(exc) for item in errors):
                if not isinstance(exc, ValidationError):
                    add(
                        (),
                        "admission_validator_rejected",
                        None,
                        validator_code=str(exc),
                        error_type=type(exc).__name__,
                    )
    return errors
