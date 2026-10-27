"""Resolve explicit immutable source IDs before mapping a new review wire.

The model judges meaning and scores. The service owns source text and severity
routing. Legacy reports are never repaired, rescored, or silently promoted.
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
import hashlib
import json
from typing import Any
from uuid import UUID

from bestseller.services.commercial_source_evidence import (
    CommercialSources,
    assemble_commercial_sources,
    validate_commercial_evidence,
)

# ruff: noqa: ANN401
WIRE_VERSION = "commercial-review-wire.v2"
ADAPTER_VERSION = "commercial-review-adapter.v2"


def _unique_fields(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate wire field")
        value[key] = item
    return value


def _source_record_aliases(sources: CommercialSources) -> dict[str, list[tuple[str, str]]]:
    """Derive record identities only from an intact native packet and catalog.

    Restore only the producer's declared structural aliases. Authored objects
    and JSON-looking strings remain data. Native assembly rechecks the entire
    catalog, original source binding, authority and exact quotation selection;
    this adds no authentication or physical-custody claim of its own.
    """

    def canonical(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"), allow_nan=False)

    def require(condition: bool) -> None:
        if not condition:
            raise ValueError("source record alias binding mismatch")

    def at(value: Any, pointer: str) -> Any:
        require(isinstance(pointer, str) and pointer.startswith("#/"))
        for encoded in pointer[2:].split("/"):
            key = encoded.replace("~1", "/").replace("~0", "~")
            require(key.replace("~", "~0").replace("/", "~1") == encoded)
            if isinstance(value, list):
                require(key.isascii() and key.isdecimal() and str(int(key)) == key)
                value = value[int(key)]
            else:
                require(isinstance(value, dict))
                value = value[key]
        return value

    try:
        receipt = sources.receipt
        require(hashlib.sha256(sources.text.encode()).hexdigest()
                == receipt.get("assembled_source_sha256"))
        require(len(sources.text) == receipt.get("assembled_characters"))
        packet = json.loads(sources.text, object_pairs_hook=_unique_fields)
        require(isinstance(packet, dict))
        aliases: dict[str, str] = {}
        for alias in receipt["structural_aliases"]:
            require(isinstance(alias, dict) and set(alias) == {"path", "target"})
            path, target = alias["path"], alias["target"]
            require(isinstance(path, str) and path.startswith("#/source_context/")
                    and isinstance(target, str) and target.startswith("#/source_context/")
                    and path not in aliases)
            require(at(packet, path) == {"$ref": target})
            aliases[path] = target

        def restore(node: Any, path: str, trail: frozenset[str]) -> Any:
            if path in aliases:
                require(path not in trail)
                target = aliases[path]
                return restore(at(packet, target), target, trail | {path})
            if isinstance(node, dict):
                return {
                    key: restore(
                        child, path + "/" + key.replace("~", "~0").replace("/", "~1"), trail
                    )
                    for key, child in node.items()
                }
            if isinstance(node, list):
                return [restore(child, f"{path}/{index}", trail)
                        for index, child in enumerate(node)]
            return node

        source = restore(packet["source_context"], "#/source_context", frozenset())
        rebuilt = assemble_commercial_sources(
            chapter_number=packet["chapter_number"], content_md=packet["current_body"],
            previous_chapters=packet["previous_chapters"], source=source,
        )
        require(canonical(dict(rebuilt.catalog)) == canonical(dict(sources.catalog)))
        expected = json.loads(rebuilt.text)
        require(set(packet) == set(expected))
        require(all(canonical(packet[key]) == canonical(expected[key]) for key in expected
                    if key not in {"source_context", "citation_catalog"}))
        rendered = packet["citation_catalog"]
        require(isinstance(rendered, list) and len(rendered) == len(rebuilt.catalog))
        require(canonical({entry["id"]: entry for entry in rendered})
                == canonical({entry["id"]: entry for entry in expected["citation_catalog"]}))
        # Original insertion order can choose a different identical structure
        # for sharing. Verify the retained packet's geometry separately rather
        # than replacing it with a freshly encoded packet.
        geometry = {"assembled_source_sha256", "assembled_characters", "structural_aliases",
                    "rendered_catalog_characters", "section_characters"}
        require(all(canonical(receipt.get(key)) == canonical(value)
                    for key, value in rebuilt.receipt.items()
                    if key not in geometry))
        require(receipt.get("rendered_catalog_characters") == len(canonical(rendered)))
        require(receipt.get("section_characters")
                == {key: len(canonical(value)) for key, value in packet.items()})
        result: dict[str, list[tuple[str, str]]] = {}
        for identifier, entry in rebuilt.catalog.items():
            if entry["kind"] != "observation":
                continue
            pointer = "#/source_context" + entry["source_path"]
            record = at({"source_context": source}, pointer)
            if isinstance(record, dict) and isinstance(record.get("id"), str):
                result.setdefault(record["id"], []).append((identifier, pointer))
        return result
    except (ValueError, TypeError, KeyError, IndexError, AttributeError, RecursionError):
        return {}  # Unverified or ambiguous identity remains a normal invalid citation.


def parse_commercial_wire(
    raw: str, sources: CommercialSources
) -> tuple[dict[str, Any], dict[str, Any], tuple[str, ...]]:
    """Verify ID existence and authority; derive evidence from supplied source only."""
    text = raw.strip()
    if text.startswith("```json\n") and text.endswith("\n```"):
        text = text[8:-4]
    receipt: dict[str, Any] = {
        "adapter_version": ADAPTER_VERSION,
        "wire_version": WIRE_VERSION,
        "raw_report_sha256": hashlib.sha256(raw.encode()).hexdigest(),
        "source_binding": dict(sources.receipt),
        "status": "rejected",
        "scores_modified": False,
        "issues_dropped": 0,
    }

    try:
        parsed = json.loads(text, object_pairs_hook=_unique_fields)
    except (ValueError, TypeError):
        return {}, receipt, ("wire: raw output is not strict JSON",)
    if not isinstance(parsed, dict):
        return {}, receipt, ("wire: report must be a JSON object",)
    if parsed.get("wire_version") != WIRE_VERSION:
        return (
            parsed,
            receipt,
            ("wire: explicit current wire_version required; legacy reports rejected",),
        )
    required = {
        "wire_version",
        "pass",
        "overall_score",
        "dimension_scores",
        "issues",
        "rewrite_plan",
    }
    rules = {key: entry for key, entry in sources.catalog.items() if entry["kind"] == "constraint"}
    if rules:
        required.add("checks")
    errors: list[str] = []
    if set(parsed) != required:
        errors.append("wire: missing or unexpected report fields")
    if not isinstance(parsed.get("rewrite_plan"), Mapping):
        errors.append("wire: rewrite_plan must be an object")

    def cite(identifier: Any, allowed: set[str], path: str) -> dict[str, Any] | None:
        entry = sources.catalog.get(identifier) if isinstance(identifier, str) else None
        if entry is None or entry.get("kind") not in allowed:
            errors.append(f"{path}: unknown or incorrectly typed source ID")
            return None
        value = entry.get("text")
        if not isinstance(value, str) or not value.strip():
            errors.append(f"{path}: immutable source text unavailable")
            return None
        return {"id": identifier, "kind": entry["kind"], "quote": value}

    kinds = {
        "narrative_quality": None,
        "current_fact_conflict": {"current"},
        "prior_fact_conflict": {"prior", "observation"},
        "constraint_violation": {"constraint"},
    }
    issues = parsed.get("issues")
    if not isinstance(issues, list):
        errors.append("issues: must be a list")
        issues = []
    resolved = []
    alias_catalog = None
    derived_aliases = []
    for index, issue in enumerate(issues):
        path = f"issues/{index}"
        if not isinstance(issue, Mapping):
            errors.append(f"{path}: not an issue object")
            continue
        kind = issue.get("assessment_kind")
        if not isinstance(kind, str) or kind not in kinds:
            errors.append(f"{path}: unknown assessment_kind")
            continue
        fields = {"code", "severity", "assessment_kind", "body_id", "reason", "required_fix"}
        if kind != "narrative_quality":
            fields.add("source_id")
        if kind in {"current_fact_conflict", "prior_fact_conflict"}:
            fields.add("temporal_relation")
        if set(issue) != fields:
            errors.append(f"{path}: missing/unexpected fields; redundant legacy fields forbidden")
        for field in ("code", "reason", "required_fix"):
            if not isinstance(issue.get(field), str) or not issue[field].strip():
                errors.append(f"{path}: nonempty {field} required")
        if issue.get("severity") not in ("critical", "high", "medium", "low"):
            errors.append(f"{path}: invalid severity")
        body = cite(issue.get("body_id"), {"current"}, path + "/body_id")
        source_id = issue.get("source_id")
        if (kind == "prior_fact_conflict" and isinstance(source_id, str)
                and source_id not in sources.catalog and source_id.startswith("SRC:")):
            try:
                canonical_record_id = str(UUID(source_id[4:])) == source_id[4:]
            except ValueError:
                canonical_record_id = False
            if canonical_record_id:
                if alias_catalog is None:
                    alias_catalog = _source_record_aliases(sources)
                matches = alias_catalog.get(source_id[4:], [])
                if len(matches) == 1:
                    catalog_id, pointer = matches[0]
                    derived_aliases.append({
                        "schema": "commercial-source-record-alias.v1",
                        "path": "/" + path + "/source_id", "original_id": source_id,
                        "catalog_id": catalog_id, "record_pointer": pointer,
                        "raw_report_sha256": receipt["raw_report_sha256"],
                        "source_binding": deepcopy(dict(sources.receipt)),
                    })
                    source_id = catalog_id
        origin = (
            None
            if kind == "narrative_quality"
            else cite(source_id, kinds[kind], path + "/source_id")
        )
        if kind in {"current_fact_conflict", "prior_fact_conflict"} and issue.get(
            "temporal_relation"
        ) not in {"same_event_or_state", "unexplained_transition"}:
            errors.append(f"{path}: explicit temporal_relation required; progress is not a cap")
        resolved.append((issue, body, origin))
    checks = parsed.get("checks", {})
    resolved_checks = {}
    if rules:
        if not isinstance(checks, Mapping) or set(checks) != set(rules):
            errors.append("checks: exactly the applicable catalog RULE IDs are required")
        else:
            for rule_id, check in checks.items():
                path = "checks/" + rule_id
                if not isinstance(check, Mapping) or set(check) != {"result", "body_id", "reason"}:
                    errors.append(f"{path}: requires result, body_id and reason only")
                    continue
                if check.get("result") not in {"PASS", "FAIL"}:
                    errors.append(f"{path}: result must be PASS or FAIL")
                if not isinstance(check.get("reason"), str) or not check["reason"].strip():
                    errors.append(f"{path}: nonempty reason required")
                resolved_checks[rule_id] = cite(
                    check.get("body_id"), {"current"}, path + "/body_id"
                )
    if errors:
        return parsed, receipt, tuple(errors)

    domain = {
        key: deepcopy(parsed[key])
        for key in ("pass", "overall_score", "dimension_scores", "rewrite_plan")
    }
    domain.update(blocking_issues=[], audit_issues=[], binary_checklist={})
    for issue, body, origin in resolved:
        mapped = {
            key: deepcopy(issue[key])
            for key in ("code", "severity", "assessment_kind", "required_fix")
        }
        mapped.update(body_citations=[body], evidence=body["quote"])
        if issue["assessment_kind"] == "narrative_quality":
            mapped["reader_impact"] = issue["reason"]
        else:
            mapped.update(
                source_citations=[origin],
                source_assertion=origin["quote"],
                current_assertion=body["quote"],
                conflict_reason=issue["reason"],
            )
        if "temporal_relation" in issue:
            mapped["temporal_relation"] = issue["temporal_relation"]
        # Severity has a single meaning: a serious issue cannot hide in audit.
        group = "blocking_issues" if issue["severity"] in {"critical", "high"} else "audit_issues"
        domain[group].append(mapped)
    added_rules = []
    for rule_id, check in checks.items():
        body = resolved_checks[rule_id]
        origin = {"id": rule_id, "kind": "constraint", "quote": rules[rule_id]["text"]}
        domain["binary_checklist"][rule_id] = {
            "result": check["result"],
            "evidence": body["quote"],
            "body_citations": [body],
            "source_citations": [origin],
        }
        if check["result"] != "FAIL" or any(
            i["assessment_kind"] == "constraint_violation"
            and i["severity"] == "critical"
            and i["source_citations"][0]["id"] == rule_id
            for i in domain["blocking_issues"]
        ):
            continue
        domain["blocking_issues"].append(
            {
                "code": "EXPLICIT_RULE_VIOLATION",
                "severity": "critical",
                "path": rule_id,
                "assessment_kind": "constraint_violation",
                "evidence": body["quote"],
                "body_citations": [body],
                "source_citations": [origin],
                "source_assertion": origin["quote"],
                "current_assertion": body["quote"],
                "conflict_reason": check["reason"],
                "required_fix": (
                "Repair only the cited explicit-rule violation, preserving established facts."
            ),
            }
        )
        added_rules.append(rule_id)
    errors = list(validate_commercial_evidence(domain, sources))
    if errors:
        return parsed, receipt, tuple(errors)
    receipt.update(
        status="verified",
        derived_issues_added=len(added_rules),
        derived_issue_rule_ids=added_rules,
        domain_mapping_sha256=hashlib.sha256(
            json.dumps(domain, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
        source_resolution="immutable-catalog-id.v1",
        issue_order_preserved_within_severity_group=True,
        issue_counts={group: len(domain[group]) for group in ("blocking_issues", "audit_issues")},
    )
    if derived_aliases:
        receipt["derived_source_record_aliases"] = derived_aliases
    return domain, receipt, ()
