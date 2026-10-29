"""Pre-extraction dispatch equivalence; no gateway, provider or database calls.

The reference below freezes the original complete-stream rule, including its
template exceptions. Compare complete effective inputs, not stream alone: M3.1
reasoning defaults and stream_include_usage also affect paid input identity.
"""

import ast
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import socket
from types import SimpleNamespace
from uuid import uuid4

import pytest

from bestseller.services import llm, model_catalog
from bestseller.services import production_strategy as strategy
from bestseller.services import retained_review_input_proof as retained
from bestseller.settings import LLMRoleSettings, load_settings

pytestmark = pytest.mark.unit
MODELS = ["openai/MiniMax-M3.1-Flash", "openai/MiniMax-M3"]
SPECIAL = ["conception_finalize_continuation_v2", "conception_finalize_adjudication_repair"]
TOOL = {
    "type": "function",
    "function": {
        "name": "submit_evidence",
        "parameters": {
            "type": "object",
            "properties": {"unchanged": {"type": "string"}},
            "required": ["unchanged"],
        },
    },
}


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("stream identity tests forbid PG/provider/network access")

    async def paid_forbidden(*args, **kwargs):
        forbidden()

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(llm, "complete_text", paid_forbidden)
    monkeypatch.setattr(model_catalog, "_runtime_dead_models", {})
    token = retained._HISTORICAL_ROUTE_WITNESSES.set(None)
    try:
        yield
    finally:
        retained._HISTORICAL_ROUTE_WITNESSES.reset(token)


def request_for(logical_role, tools, template="planner_structured_field_correction"):
    return llm.LLMCompletionRequest(
        logical_role=logical_role,
        project_id=uuid4(),
        workflow_run_id=uuid4(),
        prompt_template=template,
        prompt_version="identity-test.v1",
        system_prompt="Exact frozen system. Keep §0 and $ref as source data.",
        user_prompt='{"complete_source":"原始末尾🌕","unknown":null}',
        fallback_response="never dispatched",
        max_tokens_override=8192,
        tools=[deepcopy(TOOL)] if tools else None,
        tool_choice={"type": "function", "function": {"name": "submit_evidence"}}
        if tools
        else None,
    )


def base_role(model, reasoning, stream=False):
    return LLMRoleSettings(
        model=model,
        temperature=0.37,
        max_tokens=32768,
        timeout_seconds=43,
        api_base="https://stream-identity.invalid/v1",
        stream=stream,
        reasoning_effort=reasoning,
        thinking_type=None,
        n_candidates=1,
    )


def original_dispatch(request, role, *, profile, ambient):
    """Frozen inline rule before extraction, independent of the new helpers."""
    complete_project = profile == "complete"
    m31 = role.model.split("/")[-1].casefold() == "minimax-m3.1-flash"
    complete_stream = bool((ambient or complete_project) and m31 and not request.tools)
    if complete_stream:
        changes = {"stream": True}
        if role.reasoning_effort in (None, ""):
            finalize = request.prompt_template in (
                "conception_finalize_continuation_v2",
                "conception_finalize_adjudication_repair",
            )
            complete_writing = complete_project and request.logical_role in (
                "planner",
                "writer",
                "editor",
            )
            if finalize or complete_writing:
                changes["reasoning_effort"] = "medium"
        role = role.model_copy(update=changes)
    return complete_stream, role


def assert_identity(request, expected, actual):
    assert actual.model_dump() == expected.model_dump()
    expected_payload = llm._effective_input_payload(request, expected)
    actual_payload = llm._effective_input_payload(request, actual)
    assert actual_payload == expected_payload
    physical = json.dumps(
        expected_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()
    assert llm._effective_input_hash(request, actual) == hashlib.sha256(physical).hexdigest()
    assert llm._effective_input_hash(request, actual) == llm._effective_input_hash(
        request, expected
    )


@pytest.mark.parametrize("model", MODELS)
@pytest.mark.parametrize("tools", [False, True], ids=["text", "tools"])
@pytest.mark.parametrize("profile", [None, "complete", "research"])
@pytest.mark.parametrize("ambient", [False, True], ids=["no-ambient", "ambient-complete"])
@pytest.mark.parametrize("logical_role", ["planner", "critic", "writer"])
@pytest.mark.parametrize("reasoning", [None, "low"])
@pytest.mark.parametrize("initial_stream", [False, True], ids=["stream-off", "stream-on"])
def test_full_original_dispatch_matrix_preserves_predicate_role_payload_and_hash(
    model, tools, profile, ambient, logical_role, reasoning, initial_stream
):
    request, role = request_for(logical_role, tools), base_role(model, reasoning, initial_stream)
    before_request, before_role = request.model_dump(), role.model_dump()
    predicate, expected = original_dispatch(request, role, profile=profile, ambient=ambient)
    assert (
        strategy.production_stream_enabled(request, role, profile=profile, ambient_complete=ambient)
        is predicate
    )
    actual = strategy.apply_production_stream_settings(
        request, role, profile=profile, ambient_complete=ambient
    )
    assert_identity(request, expected, actual)
    assert request.model_dump() == before_request and role.model_dump() == before_role
    if not predicate:
        assert actual is role


@pytest.mark.parametrize("template", SPECIAL)
@pytest.mark.parametrize("profile", [None, "complete", "research"])
@pytest.mark.parametrize("ambient", [False, True])
@pytest.mark.parametrize("reasoning", [None, "low"])
def test_finalize_template_medium_exception_is_identical_for_critic(
    template, profile, ambient, reasoning
):
    request, role = request_for("critic", False, template), base_role(MODELS[0], reasoning)
    predicate, expected = original_dispatch(request, role, profile=profile, ambient=ambient)
    assert (
        strategy.production_stream_enabled(request, role, profile=profile, ambient_complete=ambient)
        is predicate
    )
    actual = strategy.apply_production_stream_settings(
        request, role, profile=profile, ambient_complete=ambient
    )
    assert_identity(request, expected, actual)
    assert actual.reasoning_effort == ("medium" if predicate and reasoning is None else reasoning)


@pytest.mark.parametrize("reasoning", [None, "low"])
def test_editor_and_empty_tool_list_keep_the_original_text_semantics(reasoning):
    request = request_for("editor", False).model_copy(update={"tools": []})
    role = base_role(MODELS[0], reasoning)
    predicate, expected = original_dispatch(request, role, profile="complete", ambient=False)
    assert predicate and strategy.production_stream_enabled(request, role, profile="complete")
    assert_identity(
        request,
        expected,
        strategy.apply_production_stream_settings(request, role, profile="complete"),
    )


@pytest.mark.parametrize("model", MODELS)
@pytest.mark.parametrize("tools", [False, True], ids=["text", "tools"])
@pytest.mark.parametrize("logical_role", ["planner", "critic", "writer"])
@pytest.mark.parametrize("reasoning", [None, "low"])
@pytest.mark.parametrize("profile", [None, "complete", "research"])
def test_fresh_reader_known_profile_reproduces_original_dispatch_hash(
    model, tools, logical_role, reasoning, profile
):
    request, role = request_for(logical_role, tools), base_role(model, reasoning)
    settings = load_settings(env={}).model_copy(deep=True)
    settings.llm.mock = False
    setattr(settings.llm, logical_role, role)
    metadata = {} if profile is None else {"production_profile": profile}
    project = SimpleNamespace(id=request.project_id, metadata_json=metadata)
    before_settings, before_metadata = settings.model_dump(), deepcopy(metadata)
    _, expected = original_dispatch(request, role, profile=profile, ambient=False)
    # Current ambient work cannot reconfigure a historical request. Its known
    # project profile is the reader's evidence, including absent legacy profile.
    with strategy.protect_downstream("complete", 1):
        actual = retained._fresh_role(settings, project, request, {})
    assert_identity(request, expected, actual)
    assert settings.model_dump() == before_settings and metadata == before_metadata
    if profile == "complete" and model == MODELS[0] and not tools:
        assert actual.stream is True
        assert llm._effective_input_hash(request, actual) != llm._effective_input_hash(
            request, role
        )


@pytest.mark.parametrize("logical_role", ["planner", "critic", "writer"])
def test_fresh_selected_catalog_route_includes_complete_profile_after_model_override(logical_role):
    request = request_for(logical_role, False)
    settings = load_settings(env={}).model_copy(deep=True)
    settings.llm.mock = False
    role = base_role(MODELS[1], "low")
    setattr(settings.llm, logical_role, role)
    entry = model_catalog.ModelCatalogEntry(
        id="selected-stream",
        display_name="isolated",
        vendor="fixture",
        model=MODELS[0],
        api_base="https://stream-identity.invalid/v1",
    )
    project = SimpleNamespace(
        id=request.project_id,
        metadata_json={"llm_model_id": entry.id, "production_profile": "complete"},
    )
    resolved = llm._apply_model_override(role, entry)
    _, expected = original_dispatch(request, resolved, profile="complete", ambient=False)
    actual = retained._fresh_role(settings, project, request, {entry.id: entry})
    assert_identity(request, expected, actual)
    assert actual.model == MODELS[0] and actual.stream


def test_exact_historical_route_does_not_apply_new_profile_stream_policy():
    request = request_for("critic", False, "draft_continuity_hard_safety").model_copy(
        update={"prompt_version": "3.0"}
    )
    settings = load_settings(env={}).model_copy(deep=True)
    settings.llm.mock = False
    base = base_role(MODELS[1], None)
    settings.llm.critic = base
    catalog = {
        key: model_catalog.ModelCatalogEntry(
            id=key,
            display_name=key,
            vendor="fixture",
            model=model,
            api_base="https://stream-identity.invalid/v1",
        )
        for key, model in (("old", MODELS[0]), ("current", MODELS[1]))
    }
    historical = llm._apply_model_override(base, catalog["old"])
    paid_hash = llm._effective_input_hash(request, historical)
    run = SimpleNamespace(
        id=uuid4(),
        project_id=request.project_id,
        model_name=historical.model,
        prompt_hash=paid_hash,
        metadata_json={
            "effective_input_receipt": {
                "schema": "effective-input.v1",
                "hash": paid_hash,
                "model": historical.model,
            }
        },
    )
    retained._remember_historical_route(request, run)
    project = SimpleNamespace(
        id=request.project_id,
        metadata_json={"llm_model_id": "current", "production_profile": "complete"},
    )
    actual = retained._fresh_role(settings, project, request, catalog)
    assert_identity(request, historical, actual)
    assert actual.stream is False and llm._effective_input_hash(request, actual) == paid_hash
    assert (
        strategy.apply_production_stream_settings(request, historical, profile="complete").stream
        is True
    )


def test_gateway_predicate_and_sdk_timeout_branch_keep_the_same_inputs():
    tree = ast.parse(Path(llm.__file__).read_text())
    gateway = next(
        n
        for n in tree.body
        if isinstance(n, ast.AsyncFunctionDef) and n.name == "_complete_text_impl"
    )
    predicate = next(
        n.value
        for n in ast.walk(gateway)
        if isinstance(n, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "complete_stream" for t in n.targets)
    )
    assert isinstance(predicate, ast.Call) and isinstance(predicate.func, ast.Name)
    assert predicate.func.id == "production_stream_enabled"
    apply = next(
        n
        for n in ast.walk(gateway)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Name)
        and n.func.id == "apply_production_stream_settings"
    )
    assert ast.dump(ast.Tuple(elts=predicate.args), include_attributes=False) == ast.dump(
        ast.Tuple(elts=apply.args), include_attributes=False
    )
    assert [ast.dump(k, include_attributes=False) for k in predicate.keywords] == [
        ast.dump(k, include_attributes=False) for k in apply.keywords
    ]
    branch = next(
        n
        for n in ast.walk(gateway)
        if isinstance(n, ast.If) and isinstance(n.test, ast.Name) and n.test.id == "complete_stream"
    )
    timeout = next(
        n
        for n in ast.walk(branch)
        if isinstance(n, ast.Assign)
        and any(
            isinstance(t, ast.Attribute) and t.attr == "stream_total_timeout_seconds"
            for t in n.targets
        )
    )
    expected = ast.parse(
        "max(float(role_settings.timeout_seconds), float(settings.llm.planner.timeout_seconds))",
        mode="eval",
    ).body
    assert ast.dump(timeout.value, include_attributes=False) == ast.dump(
        expected, include_attributes=False
    )
