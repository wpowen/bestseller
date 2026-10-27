"""Planner prompt identifiers must fit the real gateway request contract."""

import ast
from pathlib import Path

import pytest

from bestseller.services import planner
from bestseller.services.llm import LLMCompletionRequest

pytestmark = pytest.mark.unit


def test_planner_literal_prompt_versions_fit_gateway_schema():
    tree = ast.parse(Path(planner.__file__).read_text())
    versions = [
        keyword.value.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        for keyword in node.keywords
        if keyword.arg == "prompt_version"
        and isinstance(keyword.value, ast.Constant)
        and isinstance(keyword.value.value, str)
    ]
    assert versions
    for version in versions:
        LLMCompletionRequest(
            logical_role="planner",
            system_prompt="Plan source-bound assets.",
            user_prompt="Expand the approved setting.",
            fallback_response="{}",
            prompt_version=version,
        )
