"""Explicit author fixtures for optional-method contract tests, never mocks."""
from copy import deepcopy


def explicit_planning_metadata(*capabilities, metadata=None):
    result = deepcopy(metadata or {})
    creation = result.setdefault("creation_intent_contract", {})
    creation.setdefault("field_sources", {})["hook_spec"] = "explicit"
    hook = creation.setdefault("hook_spec", {})
    hook["planning_capabilities"] = {
        "schema": "planning-capabilities.v1",
        "selected": {capability: True for capability in capabilities},
    }
    return result
