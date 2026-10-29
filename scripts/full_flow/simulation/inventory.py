"""AST-only template inventory. No application imports or prompt/body exports.

This is a conservative source inventory, not a dynamic call graph or a claim
that every optional repair/enhancer runs in normal quickstart. Dynamic sites
remain explicit until a bounded concrete script supplies their actual names.
"""

from __future__ import annotations

# ruff: noqa: ANN001, ANN201, ANN202, ANN204 -- AST visitor and serialized metadata boundaries.
import argparse
import ast
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import re
import sys

INVENTORY_SCHEMA = "full-flow-template-inventory.v1"
TEMPLATE_NAME = re.compile(r"[A-Za-z0-9_.:-]{1,128}\Z")


def symbol(node):
    """Only symbolic references; never serialize prompt/response literals."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return symbol(node.value) + "." + node.attr
    if isinstance(node, ast.Call):
        return symbol(node.func) + "(...)"
    if isinstance(node, ast.Constant):
        return "<literal>"
    if isinstance(node, ast.JoinedStr):
        return "<f-string>"
    return "<" + type(node).__name__ + ">"


def finite_strings(node, constants):
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return {node.value}
    if isinstance(node, ast.Name):
        return constants.get(node.id, set())
    if isinstance(node, ast.IfExp):
        return finite_strings(node.body, constants) | finite_strings(node.orelse, constants)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = finite_strings(node.left, constants), finite_strings(node.right, constants)
        if len(left) * len(right) <= 128:
            return {a + b for a in left for b in right}
    if isinstance(node, ast.JoinedStr):
        parts = {""}
        for item in node.values:
            values = finite_strings(
                item.value if isinstance(item, ast.FormattedValue) else item, constants
            )
            if not values or len(parts) * len(values) > 128:
                return set()
            parts = {a + b for a in parts for b in values}
        return parts
    return set()


def template_expression(node):
    """Render only template expressions, retaining unresolved placeholders."""
    if isinstance(node, ast.JoinedStr):
        return "".join(
            item.value
            if isinstance(item, ast.Constant) and isinstance(item.value, str)
            else "{" + symbol(item.value) + "}"
            if isinstance(item, ast.FormattedValue)
            else "{unresolved}"
            for item in node.values
        )
    return symbol(node)


def expression_sha256(node, generators=None):
    """Seal expression semantics without exporting literals or line positions."""
    expression = ast.dump(node, include_attributes=False)
    # A local naming helper is part of the expression's semantics. In
    # particular, changing a helper's return suffix must invalidate its callers.
    helpers = {
        item.func.id: ast.dump(generators[item.func.id], include_attributes=False)
        for item in ast.walk(node)
        if isinstance(item, ast.Call) and isinstance(item.func, ast.Name)
        and generators and item.func.id in generators
    }
    if helpers:
        expression = json.dumps([expression, helpers], sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(expression.encode()).hexdigest()


def phase_for(template, path=""):
    if any(x in template for x in ("title", "blurb", "listing", "persona_click")):
        return "title_listing"
    if template.startswith(("conception", "concept_", "story_architect", "appeal_", "premise_")):
        return "conception"
    if template.startswith(
        ("planner_", "planning_", "outline_", "commercial_planning", "prewrite_")
    ):
        return "planning"
    if any(x in template for x in ("window_quality", "volume_quality")):
        return "window_review"
    if "chapter_commercial" in template:
        return "commercial_review"
    if template in {"chapter_review", "scene_review"}:
        return "primary_review"
    if any(x in template for x in ("feedback", "bible", "knowledge", "summary", "snapshot")):
        return "knowledge"
    if any(x in template for x in ("semantic_review", "closure_preflight", "whole_book", "book_")):
        return "whole_book"
    if any(
        x in template
        for x in ("writer", "rewrite", "continuity", "ending_repair", "opening_repair")
    ):
        return "draft_rewrite"
    return "auxiliary_optional"


def _historical(path):
    return any("history" in part or part in {"prior-bytes", "bytes"} for part in path.parts)


def scan_repository(root):
    root = Path(root).resolve(strict=True)
    templates = defaultdict(list)
    unresolved, files, failures, unnamed = [], [], [], []
    services = root / "src/bestseller/services"
    for path in sorted(services.rglob("*.py")):
        if path.is_symlink():
            raise ValueError("SCRIPT_SOURCE_SYMLINK")
        rel = path.relative_to(root).as_posix()
        raw = path.read_bytes()
        try:
            tree = ast.parse(raw, filename=rel)
        except (SyntaxError, UnicodeError) as error:
            failures.append(
                {
                    "file": rel,
                    "code": "SCRIPT_SOURCE_PARSE_FAILED",
                    "line": getattr(error, "lineno", None),
                }
            )
            continue
        historical = _historical(path.relative_to(services))
        generators = {
            node.name: node for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        request_constructors = {"LLMCompletionRequest"}
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "bestseller.services.llm":
                request_constructors.update(
                    alias.asname or alias.name for alias in node.names
                    if alias.name == "LLMCompletionRequest"
                )
        # Only unambiguous module-level constants are resolved. Function-local
        # variable names are never treated as global template identities.
        constants = {}
        positional_templates = defaultdict(set)
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for index, arg in enumerate((*node.args.posonlyargs, *node.args.args)):
                    if arg.arg in {"template", "prompt_template"}:
                        positional_templates[node.name].add(index)
        for node in tree.body:
            value = node.value if isinstance(node, (ast.Assign, ast.AnnAssign)) else None
            targets = (
                node.targets
                if isinstance(node, ast.Assign)
                else ([node.target] if isinstance(node, ast.AnnAssign) else [])
            )
            for target in targets:
                if isinstance(target, ast.Name) and value is not None:
                    values = finite_strings(value, constants)
                    if values:
                        constants[target.id] = values
        tools, schemas = set(), set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                ref = symbol(node.func)
                if ref.endswith((".model_json_schema", ".model_validate")):
                    schemas.add(ref)
                if "tool" in ref.lower() and not ref.startswith("<"):
                    tools.add(ref)
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if re.fullmatch(r"(?:submit|judge)_[a-z0-9_]{2,100}", node.value):
                    tools.add(node.value)
        files.append(
            {
                "file": rel,
                "sha256": hashlib.sha256(raw).hexdigest(),
                "historical": historical,
                "tool_references": sorted(tools),
                "schema_references": sorted(schemas),
            }
        )
        for node in tree.body:
            if not isinstance(node, ast.Assign):
                continue
            labels = [t.id for t in node.targets if isinstance(t, ast.Name)]
            declared = any(label.endswith(("TEMPLATE", "TEMPLATES")) for label in labels)
            declared |= path.name == "book_request_purpose.py" and bool(
                set(labels) & {"PRIMARY", "REPAIR"}
            )
            if declared:
                # Set/list members are declarations, not dispatch evidence.
                for item in ast.walk(node.value):
                    if (
                        isinstance(item, ast.Constant)
                        and isinstance(item.value, str)
                        and ("_" in item.value and TEMPLATE_NAME.fullmatch(item.value))
                    ):
                        templates[item.value].append(
                            {
                                "file": rel,
                                "line": node.lineno,
                                "function": "",
                                "call": "<declaration>",
                                "keyword": "declared_template_set",
                                "historical": historical,
                            }
                        )

        class Visitor(ast.NodeVisitor):
            def __init__(self, relative, archived, module_constants, positions, is_planner):
                self.scope = []
                self.scope_nodes = []
                self.relative = relative
                self.archived = archived
                self.constants = module_constants
                self.positions = positions
                self.is_planner = is_planner

            def visit_FunctionDef(self, node):
                self.scope.append(node.name)
                self.scope_nodes.append(node)
                self.generic_visit(node)
                self.scope_nodes.pop()
                self.scope.pop()

            def visit_AsyncFunctionDef(self, node):
                self.visit_FunctionDef(node)

            def visit_Dict(self, node):
                # LLMCompletionRequest(**plan['request']) includes real title
                # dispatches that a keyword-only call scan would miss.
                for key, value in zip(node.keys, node.values, strict=True):
                    if isinstance(key, ast.Constant) and key.value == "prompt_template":
                        site = {
                            "file": self.relative,
                            "line": node.lineno,
                            "function": ".".join(self.scope),
                            "call": "<request-dict-or-metadata>",
                            "keyword": "prompt_template",
                            "historical": self.archived,
                        }
                        names = finite_strings(value, self.constants)
                        if not names:
                            unresolved.append(
                                {
                                    **site,
                                    "expression": template_expression(value),
                                    "expression_sha256": expression_sha256(value, generators),
                                    "status": "dynamic_unresolved",
                                }
                            )
                        for name in sorted(names):
                            if TEMPLATE_NAME.fullmatch(name):
                                templates[name].append(site)
                self.generic_visit(node)

            def visit_Call(self, node):
                call = symbol(node.func)
                arguments = [(k.arg, k.value) for k in node.keywords]
                is_request_constructor = (
                    call in request_constructors or call.endswith(".LLMCompletionRequest")
                )
                if is_request_constructor:
                    explicit = [value for kind, value in arguments if kind == "prompt_template"]
                    is_none = bool(explicit and isinstance(explicit[0], ast.Constant)
                                   and explicit[0].value is None)
                    if not explicit or is_none:
                        kind = ("explicit_none" if is_none else "kwargs"
                                if any(key is None for key, _ in arguments) else "missing_keyword")
                        # Binding the enclosing function detects a previously
                        # read-only probe being changed into a paid sender.
                        scope = self.scope_nodes[-1] if self.scope_nodes else tree
                        binding = json.dumps([
                            expression_sha256(node, generators),
                            ast.dump(scope, include_attributes=False),
                        ], separators=(",", ":"))
                        unnamed.append({
                            "file": self.relative,
                            "line": node.lineno,
                            "function": ".".join(self.scope),
                            "call": call,
                            "keyword": "prompt_template",
                            "historical": self.archived,
                            "kind": kind,
                            "expression_sha256": hashlib.sha256(binding.encode()).hexdigest(),
                        })
                local_name = node.func.id if isinstance(node.func, ast.Name) else None
                positions = self.positions.get(local_name, set())
                if len(positions) == 1:
                    position = next(iter(positions))
                    if position < len(node.args):
                        arguments.append(("template", node.args[position]))
                for kind, value in arguments:
                    if (is_request_constructor and kind == "prompt_template"
                            and isinstance(value, ast.Constant) and value.value is None):
                        continue  # Report once, through the strict unnamed census.
                    if kind not in {"prompt_template", "template"} and not (
                        kind == "logical_name" and self.is_planner
                    ):
                        continue
                    site = {
                        "file": self.relative,
                        "line": node.lineno,
                        "function": ".".join(self.scope),
                        "call": call,
                        "keyword": kind,
                        "historical": self.archived,
                    }
                    names = finite_strings(value, self.constants)
                    if not names:
                        expression = template_expression(value)
                        if kind == "logical_name":
                            expression = "planner_" + expression
                        unresolved.append(
                            {**site, "expression": expression,
                             "expression_sha256": expression_sha256(value, generators),
                             "status": "dynamic_unresolved"}
                        )
                    for name in sorted(names):
                        if not TEMPLATE_NAME.fullmatch(name):
                            unresolved.append(
                                {
                                    **site,
                                    "expression": "<non-template-literal>",
                                    "expression_sha256": expression_sha256(value, generators),
                                    "status": "non_template_argument",
                                }
                            )
                            continue
                        actual = "planner_" + name if kind == "logical_name" else name
                        templates[actual].append(site)
                        if call == "_llm_call_json":
                            templates[actual + "_repair"].append({**site, "derived": "json_retry"})
                self.generic_visit(node)

        Visitor(rel, historical, constants, positional_templates, path.name == "planner.py").visit(
            tree
        )
    rows = [
        {
            "template": name,
            "phase": phase_for(name),
            "sites": sites,
            "historical_only": all(site["historical"] for site in sites),
            "coverage": "not_compared_to_p3_script",
        }
        for name, sites in sorted(templates.items())
    ]
    body = {
        "schema": INVENTORY_SCHEMA,
        "method": "AST-only conservative source occurrence scan",
        "parser_python": ".".join(map(str, sys.version_info[:3])),
        "runtime_reachability": "Unknown; optional/repair sites are included",
        "templates": rows,
        "dynamic_sites": unresolved,
        "unnamed_send_sites": unnamed,
        "source_files": files,
        "parse_failures": failures,
    }
    body["inventory_sha256"] = hashlib.sha256(
        json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()
    return body


def changed_sources(root, inventory):
    root = Path(root).resolve(strict=True)
    rows = []
    expected = {row["file"]: row["sha256"] for row in inventory["source_files"]}
    actual = {
        p.relative_to(root).as_posix(): p for p in (root / "src/bestseller/services").rglob("*.py")
    }
    for name in sorted(set(expected) | set(actual)):
        path = actual.get(name)
        if (
            path is None
            or path.is_symlink()
            or hashlib.sha256(path.read_bytes()).hexdigest() != expected.get(name)
        ):
            rows.append(name)
    return rows


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    result = scan_repository(args.root)
    # Static metadata only. Generated story responses use private_store.py.
    output = args.output.absolute()
    allowed = args.root.resolve() / "scripts/full_flow/simulation"
    if not output.resolve().is_relative_to(allowed.resolve()) or output.is_symlink():
        raise ValueError("SCRIPT_INVENTORY_OUTPUT_OUTSIDE_OWNERSHIP")
    with output.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
