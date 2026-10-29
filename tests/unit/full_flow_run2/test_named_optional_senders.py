"""Static REDs for three real optional senders; main runs this selection.

Read the actual constructor attached to complete_text or run_tool_loop. Never
import application code, copy prompt text, load settings, or dispatch a request.
"""

import ast
import hashlib
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[3]
CASES = (
    (
        "src/bestseller/services/if_generation.py",
        "_store_chapter_summary",
        "complete_text",
        "if_chapter_summary",
        "summarizer",
    ),
    (
        "src/bestseller/services/material_forge/base.py",
        "_forge_dimension",
        "run_tool_loop",
        "material_forge_dimension",
        "planner",
    ),
    (
        "src/bestseller/services/simulation_oracle_planner.py",
        "augment_story_design_kernel_with_oracle",
        "complete_text",
        "simulation_oracle_kernel_augment",
        "planner",
    ),
)


def _summary(value):
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return {
        "type": type(value).__name__,
        "length": len(encoded),
        "sha256": hashlib.sha256(encoded).hexdigest(),
    }


def _check(actual, expected, *, field, request):
    if type(actual) is type(expected) and actual == expected:
        return
    difference = {
        "field": field,
        "expected": _summary(expected),
        "actual": _summary(actual),
    }
    request.node.user_properties.append(
        ("named_optional_sender_difference", json.dumps(difference, sort_keys=True))
    )
    pytest.fail(json.dumps(difference, sort_keys=True), pytrace=False)


def _call_name(node):
    if not isinstance(node, ast.Call):
        return None
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return None


def _sent_constructor(path, function, transport, request):
    tree = ast.parse((ROOT / path).read_text(encoding="utf-8"), filename=path)
    functions = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef) and node.name == function
    ]
    _check(len(functions), 1, field="source.function_count", request=request)
    function_node = functions[0]
    sends = [
        node.value for node in ast.walk(function_node)
        if isinstance(node, ast.Await) and _call_name(node.value) == transport
    ]
    _check(len(sends), 1, field="source.awaited_send_count", request=request)
    send = sends[0]
    if transport == "complete_text":
        _check(len(send.args), 3, field="send.positional_argument_count", request=request)
        constructor = send.args[2]
    else:
        bases = [item.value for item in send.keywords if item.arg == "base_request"]
        _check(len(bases), 1, field="send.base_request_count", request=request)
        _check(type(bases[0]).__name__, "Name", field="send.base_request_kind", request=request)
        assignments = [
            node for node in ast.walk(function_node)
            if isinstance(node, ast.Assign) and node.lineno < send.lineno
            and any(isinstance(target, ast.Name) and target.id == bases[0].id
                    for target in node.targets)
        ]
        _check(len(assignments), 1, field="send.base_request_assignment_count", request=request)
        constructor = assignments[0].value
    _check(_call_name(constructor), "LLMCompletionRequest",
           field="send.request_constructor", request=request)
    return constructor


def _keyword_literal(constructor, name):
    values = [item.value for item in constructor.keywords if item.arg == name]
    if not values:
        return None
    if len(values) != 1:
        return {"keyword_count": len(values)}
    node = values[0]
    if isinstance(node, ast.Constant):
        return node.value
    return {"node_type": type(node).__name__}


@pytest.mark.parametrize(
    "path,function,transport,template,role", CASES,
    ids=[case[3] for case in CASES],
)
def test_actual_optional_send_has_stable_template_and_exact_role(
    path, function, transport, template, role, request,
):
    constructor = _sent_constructor(path, function, transport, request)
    _check(_keyword_literal(constructor, "logical_role"), role,
           field="send.logical_role", request=request)
    _check(_keyword_literal(constructor, "prompt_template"), template,
           field="send.prompt_template", request=request)
