"""Read-only metadata review of P3 interfaces and finite script coverage."""

from __future__ import annotations

# ruff: noqa: ANN001, ANN201 -- Source AST and serialized metadata boundaries.
import ast
from collections import Counter
from collections.abc import Mapping
import hashlib

from .contracts import (
    DEFAULT_LIMITS,
    HASH_SELECTORS,
    TAPE_SCHEMA,
    CallIdentity,
    ScriptContractError,
    TemplateCatalog,
    build_document,
    need,
)
from .inventory import TEMPLATE_NAME, finite_strings, symbol, template_expression

EXPECTED_INTERFACE = {
    "NativeObservation": ["prompt_template", "call_index", "project_id", "workflow_run_id"],
    "Tape.__init__": ["self", "document", "audit_path", "template_observer"],
    "Tape.from_file": ["cls", "path", "audit_path", "template_observer"],
    "NativeTemplateObserver.__call__": ["self", "_request", "native_call"],
    "install_scripted_http": ["tape"],
    "Tape._choose": ["self", "body", "fields", "observation"],
    "Tape._response": ["self", "request", "body", "spec", "asynchronous"],
}


def inspect_transport_source(source):
    """AST only: no import, exec, SDK call or installation of P3 code."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        raise ScriptContractError("SCRIPT_P3_TRANSPORT_PARSE_FAILED") from None
    found, schema = {}, None
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "TAPE_SCHEMA" for t in node.targets
        ):
            schema = node.value.value if isinstance(node.value, ast.Constant) else None
        if isinstance(node, ast.ClassDef):
            if node.name == "NativeObservation":
                found[node.name] = [
                    n.target.id
                    for n in node.body
                    if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name)
                ]
            members = [
                (node.name + "." + n.name, n)
                for n in node.body
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
            ]
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            members = [(node.name, node)]
        else:
            members = []
        for name, member in members:
            if name in EXPECTED_INTERFACE:
                found[name] = [
                    a.arg
                    for a in (*member.args.posonlyargs, *member.args.args, *member.args.kwonlyargs)
                ]
    mismatch = sorted(name for name, args in EXPECTED_INTERFACE.items() if found.get(name) != args)
    if schema != TAPE_SCHEMA:
        mismatch.append("TAPE_SCHEMA")
    return {
        "schema": "full-flow-p3-interface-review.v1",
        "compatible": not mismatch,
        "mismatches": mismatch,
        "source_sha256": hashlib.sha256(source.encode()).hexdigest(),
        "found": found,
        "tape_schema": schema,
        "execution_performed": False,
    }


def review_script_document(inventory, document, *, required_templates=(), limits=DEFAULT_LIMITS):
    """Review already prepared PRIVATE tape; never returns raw response bodies.

    The large source inventory includes optional paths. Missing static candidates
    are distinct from coordinator-selected required templates. A finite script
    covering a name once says nothing about later calls/branches/schema validity.
    """
    catalog = TemplateCatalog(inventory)
    need(
        isinstance(document, Mapping)
        and set(document) == {"schema", "simulation_only", "allowed_endpoints", "entries"},
        "SCRIPT_DOCUMENT_SCHEMA",
    )
    need(
        document["schema"] == TAPE_SCHEMA and document["simulation_only"] is True,
        "SCRIPT_DOCUMENT_SCHEMA",
    )
    entries = document["entries"]
    need(
        type(entries) is list and len(entries) <= limits.max_entries, "SCRIPT_ENTRY_COUNT_UNBOUNDED"
    )
    mapped, unknown, invalid, selected, ids = Counter(), set(), [], [], set()
    for index, entry in enumerate(entries):
        try:
            need(
                type(entry) is dict and set(entry) == {"id", "match", "responses"},
                "SCRIPT_ENTRY_INVALID",
            )
            need(
                type(entry["id"]) is str and bool(entry["id"]) and entry["id"] not in ids,
                "SCRIPT_ENTRY_ID_INVALID",
            )
            ids.add(entry["id"])
            match = entry["match"]
            need(
                type(match) is dict
                and not set(match)
                - (
                    {"prompt_template", "call_index", "project_id", "workflow_run_id"}
                    | HASH_SELECTORS
                ),
                "SCRIPT_SELECTOR_REVIEW_UNSUPPORTED",
            )
            name = match.get("prompt_template")
            identity = CallIdentity(
                name,
                match.get("call_index"),
                match.get("project_id"),
                match.get("workflow_run_id"),
                binding_hashes={key: match[key] for key in HASH_SELECTORS if key in match},
            )
            identity.match(catalog, limits)
            selected.append((identity, entry["responses"]))
            mapped[name] += 1
        except ScriptContractError as error:
            if error.code == "SCRIPT_TEMPLATE_UNCOVERED":
                unknown.add(name)
            invalid.append({"entry_index": index, "code": error.code})
    if selected:
        try:
            build_document(
                catalog, selected, allowed_endpoints=document["allowed_endpoints"], limits=limits
            )
        except ScriptContractError as error:
            invalid.append({"code": error.code})
    else:
        invalid.append({"code": "SCRIPT_ENTRY_COUNT_UNBOUNDED"})
    required = set(required_templates)
    return {
        "schema": "full-flow-script-coverage-review.v1",
        "structurally_reviewed": not invalid,
        "mapped_templates": dict(sorted(mapped.items())),
        "invalid_entries": invalid,
        "uncovered_templates": sorted(unknown),
        "missing_required_templates": sorted(required - set(mapped)),
        "unmapped_static_candidates": sorted(catalog.names - set(mapped)),
        "dynamic_source_sites": deepcopy_metadata(inventory["dynamic_sites"]),
        "native_schema_or_business_acceptance": "Unknown",
        "execution_performed": False,
    }


def deepcopy_metadata(rows):
    return [dict(row) for row in rows]


def inspect_script_source(inventory, source):
    """Find obvious entry/template declarations without executing a P3 generator.

    A string literal's presence is NOT script coverage. This helper highlights
    direct entry(name, template, spec) sites and unresolved dynamic declarations.
    """
    catalog = TemplateCatalog(inventory)
    try:
        tree = ast.parse(source)
    except SyntaxError:
        raise ScriptContractError("SCRIPT_SOURCE_PARSE_FAILED") from None
    constants = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    values = finite_strings(node.value, constants)
                    if values:
                        constants[target.id] = values
    known, unknown, dynamic = set(), set(), []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        candidates = [k.value for k in node.keywords if k.arg in {"template", "prompt_template"}]
        if symbol(node.func).rsplit(".", 1)[-1] in {"entry", "_entry"} and len(node.args) >= 2:
            candidates.append(node.args[1])
        for candidate in candidates:
            values = finite_strings(candidate, constants)
            if not values:
                dynamic.append({"line": node.lineno, "expression": template_expression(candidate)})
            for name in values:
                if TEMPLATE_NAME.fullmatch(name):
                    (known if name in catalog.names else unknown).add(name)
                else:
                    dynamic.append({"line": node.lineno, "expression": "<non-template-literal>"})
    return {
        "schema": "full-flow-script-source-review.v1",
        "source_sha256": hashlib.sha256(source.encode()).hexdigest(),
        "known_declarations": sorted(known),
        "uncovered_declarations": sorted(unknown),
        "dynamic_declarations": dynamic,
        "coverage_proven": False,
        "execution_performed": False,
    }
