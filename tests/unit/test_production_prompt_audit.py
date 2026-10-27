"""Production audit lifetime and real local retention, without DB/provider IO."""

import asyncio
import inspect
import json
from pathlib import Path
import socket
from uuid import uuid4

import pytest

from bestseller.services import llm
from bestseller.services.production_prompt_audit import (
    bind_resolved_project_prompt_audit,
    project_prompt_audit_scope,
)
from bestseller.settings import load_settings

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Production audit tests forbid DB/provider/network IO")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(llm, "complete_text", forbidden)
    monkeypatch.setattr(llm, "_call_litellm", forbidden)


@pytest.fixture
def retention(tmp_path):
    settings = load_settings(env={})
    settings.artifact_store.retain_prompt_payloads = True
    settings.artifact_store.mode = "local"
    settings.artifact_store.local_dir = str(tmp_path)

    def persist(project_id, *, workflow_id=None, user_prompt="local prompt"):
        return llm._persist_prompt_payloads(
            settings=settings,
            request=llm.LLMCompletionRequest(
                logical_role="writer",
                project_id=project_id,
                workflow_run_id=workflow_id or uuid4(),
                prompt_template="scene_writer",
                prompt_version="audit-scope-test",
                system_prompt="system",
                user_prompt=user_prompt,
                fallback_response="unused",
            ),
            llm_run_id=uuid4(),
            configured_model="openai/audit-test",
            effective_model="openai/audit-test",
            provider="openai",
            effective_temperature=0.3,
            effective_max_tokens=100,
            effective_timeout_seconds=10,
            effective_stream=False,
            effective_thinking_type=None,
            effective_reasoning_effort=None,
            content="saved paid body",
            tool_call_count=0,
            finish_reason="stop",
            fallback_used=False,
            fallback_source=None,
            input_tokens=1,
            output_tokens=2,
        )

    return settings, persist, tmp_path


def test_binding_requires_active_decorated_invocation():
    with pytest.raises(RuntimeError, match="active_invocation_required"):
        bind_resolved_project_prompt_audit(uuid4(), reason="unscoped")


async def test_decorator_preserves_signature_return_and_does_not_infer_project():
    @project_prompt_audit_scope
    async def parent(project_id, *, metadata):
        """Parent documentation."""
        assert llm._project_prompt_audit.get() is None
        return project_id, metadata

    project_id = uuid4()
    metadata = {"project_audited": True, "project_id": str(project_id)}
    assert await parent(project_id, metadata=metadata) == (project_id, metadata)
    assert parent.__name__ == "parent" and parent.__doc__ == "Parent documentation."
    assert inspect.signature(parent) == inspect.signature(parent.__wrapped__)


async def test_one_project_idempotent_reason_and_conflicting_project_fail_closed():
    project_id = uuid4()

    @project_prompt_audit_scope
    async def parent():
        bind_resolved_project_prompt_audit(project_id, reason="  normal parent  ")
        bind_resolved_project_prompt_audit(project_id, reason="another reason")
        assert llm._project_prompt_audit.get() == (project_id, "normal parent")
        with pytest.raises(ValueError, match="project_changed"):
            bind_resolved_project_prompt_audit(uuid4(), reason="foreign project")
        assert llm._project_prompt_audit.get() == (project_id, "normal parent")

    await parent()
    assert llm._project_prompt_audit.get() is None
    with pytest.raises(RuntimeError, match="active_invocation_required"):
        bind_resolved_project_prompt_audit(project_id, reason="after return")


@pytest.mark.parametrize("project_id", [None, "123", 1, True, {"project_id": "123"}])
async def test_non_uuid_never_binds(project_id):
    @project_prompt_audit_scope
    async def parent():
        with pytest.raises(ValueError, match="project_uuid_required"):
            bind_resolved_project_prompt_audit(project_id, reason="normal parent")
        assert llm._project_prompt_audit.get() is None
        valid_id = uuid4()
        bind_resolved_project_prompt_audit(valid_id, reason="resolved later")
        assert llm._project_prompt_audit.get()[0] == valid_id

    await parent()
    assert llm._project_prompt_audit.get() is None


@pytest.mark.parametrize("reason", [None, "", " \n\t", 1, True])
async def test_invalid_reason_never_binds_even_on_same_project_repeat(reason):
    project_id = uuid4()

    @project_prompt_audit_scope
    async def parent():
        with pytest.raises(ValueError, match="reason_required"):
            bind_resolved_project_prompt_audit(project_id, reason=reason)
        assert llm._project_prompt_audit.get() is None
        bind_resolved_project_prompt_audit(project_id, reason="valid")
        with pytest.raises(ValueError, match="reason_required"):
            bind_resolved_project_prompt_audit(project_id, reason=reason)
        assert llm._project_prompt_audit.get() == (project_id, "valid")

    await parent()
    assert llm._project_prompt_audit.get() is None


@pytest.mark.parametrize("same_project", [False, True])
@pytest.mark.parametrize("raises", [False, True])
async def test_nested_decorated_invocation_restores_outer(same_project, raises):
    outer_id = uuid4()
    inner_id = outer_id if same_project else uuid4()

    @project_prompt_audit_scope
    async def inner():
        assert llm._project_prompt_audit.get() == (outer_id, "outer")
        bind_resolved_project_prompt_audit(inner_id, reason="inner")
        assert llm._project_prompt_audit.get() == (inner_id, "inner")
        if raises:
            raise LookupError("inner failure")

    @project_prompt_audit_scope
    async def outer():
        bind_resolved_project_prompt_audit(outer_id, reason="outer")
        if raises:
            with pytest.raises(LookupError, match="inner failure"):
                await inner()
        else:
            await inner()
        assert llm._project_prompt_audit.get() == (outer_id, "outer")
        bind_resolved_project_prompt_audit(outer_id, reason="still outer")

    await outer()
    assert llm._project_prompt_audit.get() is None


async def test_exception_restores_preexisting_audit_and_next_invocation():
    outer_id, project_id = uuid4(), uuid4()

    @project_prompt_audit_scope
    async def parent(*, fail):
        bind_resolved_project_prompt_audit(project_id, reason="parent")
        if fail:
            raise LookupError("failure")
        return llm._project_prompt_audit.get()

    with llm.bind_project_prompt_audit(outer_id, reason="preexisting"):
        with pytest.raises(LookupError, match="failure"):
            await parent(fail=True)
        assert llm._project_prompt_audit.get() == (outer_id, "preexisting")
        assert await parent(fail=False) == (project_id, "parent")
        assert llm._project_prompt_audit.get() == (outer_id, "preexisting")
    assert llm._project_prompt_audit.get() is None


async def test_cancellation_restores_outer_and_invalidates_closed_invocation():
    outer_id, inner_id = uuid4(), uuid4()
    entered, never = asyncio.Event(), asyncio.Event()
    seen = []

    @project_prompt_audit_scope
    async def inner():
        bind_resolved_project_prompt_audit(inner_id, reason="inner")
        entered.set()
        await never.wait()

    async def task():
        with llm.bind_project_prompt_audit(outer_id, reason="outer"):
            try:
                await inner()
            finally:
                seen.append(llm._project_prompt_audit.get())
                with pytest.raises(RuntimeError, match="active_invocation_required"):
                    bind_resolved_project_prompt_audit(inner_id, reason="closed")

    pending = asyncio.create_task(task())
    await entered.wait()
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending
    assert seen == [(outer_id, "outer")]
    assert llm._project_prompt_audit.get() is None


async def test_concurrent_decorated_tasks_keep_distinct_projects_and_restore_creator():
    outer_id = uuid4()
    entered = [asyncio.Event(), asyncio.Event()]
    release = asyncio.Event()

    @project_prompt_audit_scope
    async def parent(project_id, index):
        bind_resolved_project_prompt_audit(project_id, reason=f"parent {index}")
        entered[index].set()
        await release.wait()
        assert llm._project_prompt_audit.get() == (project_id, f"parent {index}")

    async def task(project_id, index):
        await parent(project_id, index)
        assert llm._project_prompt_audit.get() == (outer_id, "creator")

    with llm.bind_project_prompt_audit(outer_id, reason="creator"):
        tasks = [asyncio.create_task(task(uuid4(), index)) for index in range(2)]
        try:
            await asyncio.gather(*(event.wait() for event in entered))
            assert llm._project_prompt_audit.get() == (outer_id, "creator")
        finally:
            release.set()
            await asyncio.gather(*tasks)
        assert llm._project_prompt_audit.get() == (outer_id, "creator")
    assert llm._project_prompt_audit.get() is None


async def test_undecorated_child_cannot_mutate_inherited_parent_stack():
    project_id = uuid4()

    async def child():
        with pytest.raises(RuntimeError, match="active_invocation_required"):
            bind_resolved_project_prompt_audit(uuid4(), reason="foreign task")
        assert llm._project_prompt_audit.get() == (project_id, "parent")

    @project_prompt_audit_scope
    async def parent():
        bind_resolved_project_prompt_audit(project_id, reason="parent")
        await asyncio.create_task(child())
        assert llm._project_prompt_audit.get() == (project_id, "parent")

    await parent()
    assert llm._project_prompt_audit.get() is None


async def test_actual_retainer_keeps_matching_project_and_skips_foreign_and_closed(retention):
    settings, persist, root = retention
    project_id, workflow_id = uuid4(), uuid4()
    settings_before = settings.model_dump(mode="json")

    async def descendant():
        request_ref, response_ref, reason, replayable = persist(project_id, workflow_id=workflow_id)
        assert reason is None and replayable
        request = json.loads((root / request_ref).read_text())
        response = json.loads((root / response_ref).read_text())
        assert request["project_id"] == str(project_id)
        assert request["workflow_run_id"] == str(workflow_id)
        assert request["messages"]["user"]["text"] == "local prompt"
        assert response["content"]["text"] == "saved paid body"
        before = sorted(root.rglob("*.json"))
        assert persist(uuid4()) == (None, None, "outside_conception_scope", False)
        assert sorted(root.rglob("*.json")) == before

    @project_prompt_audit_scope
    async def parent():
        bind_resolved_project_prompt_audit(project_id, reason="normal production parent")
        await descendant()

    assert persist(project_id) == (None, None, "outside_conception_scope", False)
    await parent()
    assert llm._project_prompt_audit.get() is None
    assert persist(project_id) == (None, None, "outside_conception_scope", False)
    assert len(list(root.rglob("*.json"))) == 2
    assert settings.model_dump(mode="json") == settings_before


@pytest.mark.parametrize("policy", ["disabled", "nonlocal", "legacy_bound"])
async def test_actual_retainer_keeps_existing_policy_and_legacy_limit(retention, policy):
    settings, persist, root = retention
    project_id = uuid4()
    if policy == "disabled":
        settings.artifact_store.retain_prompt_payloads = False
    elif policy == "nonlocal":
        settings.artifact_store.mode = "s3"
    before = settings.model_dump(mode="json")

    @project_prompt_audit_scope
    async def parent():
        bind_resolved_project_prompt_audit(project_id, reason="normal parent")
        return persist(project_id, user_prompt="x" * 120_001)

    request_ref, response_ref, reason, replayable = await parent()
    if policy in {"disabled", "nonlocal"}:
        assert (request_ref, response_ref, reason, replayable) == (
            None,
            None,
            "disabled" if policy == "disabled" else "unsupported_store_mode",
            False,
        )
        assert not list(root.rglob("*.json"))
    else:
        assert reason is None and not replayable
        request = json.loads((root / request_ref).read_text())
        user = request["messages"]["user"]
        assert len(user["text"]) == 120_000
        assert user["truncated"] and user["original_chars"] == 120_001
        assert not request["replayable"]
        assert Path(root / response_ref).is_file()
    assert settings.model_dump(mode="json") == before
