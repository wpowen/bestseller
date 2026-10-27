"""Offline operator tests: no real DB, provider, network or persisted authority."""

from __future__ import annotations

from copy import deepcopy
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

PATH = (
    Path(__file__).resolve().parents[2]
    / "reports/continuous-recovery-2026-10-16/prepare_commercial_successor.py"
)
SPEC = importlib.util.spec_from_file_location("prepare_commercial_successor_operator", PATH)
operator = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(operator)
pytestmark = pytest.mark.unit


def state():
    return {
        "tables": {"chapters": {"chapter": "original"}, "workflow_runs": {"owner": "old"}},
        "scope_ledger": {"execution_operations": ["paid-root"]},
        "global_counts": {"workflow_runs": 4, "llm_runs": 8},
    }


def test_cli_defaults_offline_and_apply_requires_explicit_execute():
    args = operator.parse_args([])
    assert not args.execute and not args.apply and args.chapter == 2
    with pytest.raises(SystemExit):
        operator.parse_args(["--apply"])
    with pytest.raises(SystemExit):
        operator.parse_args(["--execute"])
    assert operator.parse_args(["--execute", "--reason", "protocol change"]).apply is False
    assert (
        operator.parse_args(["--execute", "--apply", "--reason", "protocol change"]).apply is True
    )


def test_default_main_never_imports_services_or_initializes_database(monkeypatch, capsys):
    def forbidden():
        raise AssertionError("offline mode loaded production dependencies")

    monkeypatch.setattr(operator, "services", forbidden)
    assert operator.main([]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["passed"] and not result["database_initialized"]
    assert result["provider_calls_performed"] == 0
    assert result["live_authorization_verified"] is False


def test_readback_allows_only_one_new_audit_workflow_and_replay():
    before, after = state(), state()
    after["tables"]["workflow_runs"]["authorization"] = "receipt-hash"
    after["global_counts"]["workflow_runs"] += 1
    operator.assert_only_authorization(before, after, "authorization")
    operator.assert_only_authorization(after, deepcopy(after), "authorization")
    operator.assert_only_authorization(before, deepcopy(before))
    with pytest.raises(operator.OperatorBlocked):
        operator.assert_only_authorization(before, after)


@pytest.mark.parametrize(
    "kind", ["pointer", "owner", "ledger", "llm", "second_workflow", "updated_auth"]
)
def test_readback_rejects_any_other_original_row_or_ledger_change(kind):
    before, after = state(), state()
    after["tables"]["workflow_runs"]["authorization"] = "receipt-hash"
    after["global_counts"]["workflow_runs"] += 1
    if kind == "pointer":
        after["tables"]["chapters"]["chapter"] = "moved"
    elif kind == "owner":
        after["tables"]["workflow_runs"]["owner"] = "rewritten"
    elif kind == "ledger":
        after["scope_ledger"]["execution_operations"].append("new-spend")
    elif kind == "llm":
        after["global_counts"]["llm_runs"] += 1
    elif kind == "second_workflow":
        after["tables"]["workflow_runs"]["other"] = "second"
    else:
        before = deepcopy(after)
        after["tables"]["workflow_runs"]["authorization"] = "changed-existing"
    with pytest.raises(operator.OperatorBlocked):
        operator.assert_only_authorization(before, after, "authorization")


def test_payload_refs_are_bound_to_configured_root_including_symlinks(tmp_path):
    root = tmp_path / "artifacts"
    root.mkdir()
    payload = root / "request.json"
    payload.write_text("{}")
    outside = tmp_path / "outside.json"
    outside.write_text("{}")
    (root / "escape.json").symlink_to(outside)
    assert operator.physical_path(root, "request.json") == payload
    assert operator.physical_path(root, str(payload)) == payload
    for ref in ("../outside.json", "escape.json", str(outside), ""):
        with pytest.raises(operator.OperatorBlocked):
            operator.physical_path(root, ref)


@pytest.mark.asyncio
async def test_capture_uses_baseexception_and_restores_preparation_patch():
    original = AsyncMock()
    recovery = SimpleNamespace(prepare_commercial_component_slot=original)

    async def judge(session, settings, **kwargs):
        try:
            return await recovery.prepare_commercial_component_slot(session, **kwargs)
        except ValueError:
            pytest.fail("sentinel was swallowed as a domain error")

    result = await operator.capture_prepared(
        SimpleNamespace(judge_chapter_commercial_quality=judge),
        recovery,
        object(),
        None,
        {"captured": "actual-boundary"},
    )
    assert result == {"captured": "actual-boundary"}
    assert recovery.prepare_commercial_component_slot is original
    original.assert_not_awaited()


@pytest.mark.asyncio
async def test_capture_refuses_early_unavailable_return():
    with pytest.raises(operator.OperatorBlocked, match="judge_did_not_reach"):
        await operator.capture_prepared(
            SimpleNamespace(judge_chapter_commercial_quality=AsyncMock(return_value=None)),
            SimpleNamespace(prepare_commercial_component_slot=AsyncMock()),
            object(),
            None,
            {},
        )


@pytest.mark.asyncio
async def test_actual_judge_prepared_kwargs_are_captured_before_gateway(monkeypatch):
    from bestseller.services import chapter_llm_quality_judge as judge
    from bestseller.services import commercial_judge_recovery as recovery
    from bestseller.settings import load_settings

    settings = load_settings()
    critic = settings.llm.critic.model_dump(mode="json")
    monkeypatch.setattr(
        judge, "_commercial_effective_critic_configuration", AsyncMock(return_value=critic)
    )
    gateway = AsyncMock(side_effect=AssertionError("provider forbidden"))
    monkeypatch.setattr(judge, "complete_text", gateway)
    ids = {name: uuid4() for name in ("project_id", "chapter_id", "draft_id", "workflow_run_id")}
    prepared = await operator.capture_prepared(
        judge,
        recovery,
        object(),
        settings,
        {
            **ids,
            "chapter_number": 7,
            "content_md": "A botanist repairs a greenhouse valve.",
            "generation_input": {"story_bible": {"world": "orbital greenhouse"}},
            "sample_slot": "chapter-quality.independent-v2:0",
            "commercial_cohort_samples": 3,
            "commercial_retained_only": True,
        },
    )
    binding = operator.prepared_binding(prepared, "original-fixture-scope")
    assert binding["draft_id"] == str(ids["draft_id"])
    assert binding["scope_id"] == "original-fixture-scope"
    assert binding["samples"] == 3
    assert len(prepared["current_input_hash"]) == 64
    assert prepared["sources"].receipt["truncated"] is False
    gateway.assert_not_awaited()


@pytest.mark.asyncio
async def test_export_denier_blocks_canonical_and_already_imported_alias():
    # Load the same global deny function used by execute, without touching DB.
    import sys

    from bestseller.services import chapter_llm_quality_judge as judge
    from bestseller.services import llm

    directory = str(operator.ROOT / "reports/production-recovery-2026-10-15")
    monkey_path = directory not in sys.path
    if monkey_path:
        sys.path.insert(0, directory)
    try:
        from export_and_readback import deny_model_calls

        audit = {"blocked_gateway_attempts": 0}
        original = llm.complete_text
        assert judge.complete_text is original
        with deny_model_calls(audit):
            for call in (llm.complete_text, judge.complete_text):
                with pytest.raises(RuntimeError):
                    await call(None, None)
        assert audit["blocked_gateway_attempts"] == 2
        assert judge.complete_text is llm.complete_text is original
    finally:
        if monkey_path:
            sys.path.remove(directory)


@pytest.mark.asyncio
@pytest.mark.parametrize("apply", [False, True])
async def test_transaction_preflight_rollback_or_one_persist_and_independent_readback(
    monkeypatch, apply
):
    before = state()
    uid = uuid4()
    receipt = {
        "schema": "commercial-protocol-successor.v1",
        "policy_target_key": "policy",
        "target": {"draft_id": "same-body"},
    }
    chapter, project = SimpleNamespace(id=uuid4()), SimpleNamespace(id=uuid4())
    owner = SimpleNamespace(id=uuid4())
    session = SimpleNamespace(flush=AsyncMock(), rollback=AsyncMock(), commit=AsyncMock())
    reader = SimpleNamespace(rollback=AsyncMock(), get=AsyncMock())

    class Scope:
        def __init__(self, value):
            self.value = value

        async def __aenter__(self):
            return self.value

        async def __aexit__(self, *args):
            return False

    factory_values = iter((session, reader))

    def factory():
        return Scope(next(factory_values))

    row = SimpleNamespace(
        id=uid,
        workflow_type="commercial_protocol_successor",
        metadata_json={"receipt": receipt, "consumed_owner_id": None},
    )
    reader.get.return_value = row
    runtime = SimpleNamespace(
        successor_source_manifest=lambda: {"runtime": "hash"},
        prepare_successor_authorization=AsyncMock(return_value=receipt),
        persist_successor_authorization=AsyncMock(return_value=row),
    )
    deps = SimpleNamespace(
        runtime=runtime,
        judge=object(),
        recovery=object(),
        models=SimpleNamespace(WorkflowRunModel=object),
    )
    monkeypatch.setattr(operator, "uuid5", lambda *_: uid)
    monkeypatch.setattr(
        operator,
        "load_original",
        AsyncMock(
            return_value=(
                project,
                chapter,
                object(),
                owner,
                "key",
                {"workflow_run_id": str(owner.id)},
                {"binding": {}},
            )
        ),
    )
    monkeypatch.setattr(operator, "real_judge_inputs", AsyncMock(return_value=({}, {})))
    source = object()
    monkeypatch.setattr(
        operator,
        "capture_prepared",
        AsyncMock(
            return_value={
                "sources": source,
                "effective_critic": {"model": "fixture"},
                "current_input_hash": "new-hash",
            }
        ),
    )
    monkeypatch.setattr(operator, "prepared_binding", lambda *_: {"scope_id": "original"})
    monkeypatch.setattr(operator, "precheck_payload_roots", AsyncMock())
    monkeypatch.setattr(operator, "verify_physical_inventory", AsyncMock(return_value={}))
    monkeypatch.setattr(operator, "code_manifest", lambda *_: {"operator": "hash"})
    after = deepcopy(before)
    if apply:
        after["tables"]["workflow_runs"][str(uid)] = "audit-row"
        after["global_counts"]["workflow_runs"] += 1
    values = [before, before, after, after] if apply else [before, before, before]
    monkeypatch.setattr(operator, "snapshot", AsyncMock(side_effect=values))
    audit = {"blocked_gateway_attempts": 0, "code_before": {"operator": "hash"}}
    args = SimpleNamespace(
        chapter=2, cohort_key=None, actor="authorized", reason="changed protocol", apply=apply
    )
    await operator.authorize(
        factory,
        SimpleNamespace(artifact_store=SimpleNamespace(local_dir="configured-root")),
        deps,
        {"scope": "original"},
        args,
        audit,
    )
    assert audit["independent_readback_verified"] and audit["original_rows_and_pointers_unchanged"]
    assert audit["committed"] is apply
    assert runtime.prepare_successor_authorization.await_args.kwargs["source_manifest"] == {
        "runtime": "hash"
    }
    if apply:
        session.commit.assert_awaited_once()
        runtime.persist_successor_authorization.assert_awaited_once()
        assert (
            runtime.prepare_successor_authorization.await_args
            == runtime.persist_successor_authorization.await_args
        )
    else:
        session.rollback.assert_awaited_once()
        session.commit.assert_not_awaited()
        runtime.persist_successor_authorization.assert_not_awaited()


@pytest.mark.asyncio
async def test_preparation_failure_rolls_back_without_persisting(monkeypatch):
    session = SimpleNamespace(rollback=AsyncMock(), commit=AsyncMock())

    class Scope:
        async def __aenter__(self):
            return session

        async def __aexit__(self, *args):
            return False

    monkeypatch.setattr(operator, "snapshot", AsyncMock(return_value=state()))
    monkeypatch.setattr(
        operator,
        "load_original",
        AsyncMock(side_effect=operator.OperatorBlocked("missing-pointer")),
    )
    persist = AsyncMock()
    with pytest.raises(operator.OperatorBlocked, match="missing-pointer"):
        await operator.authorize(
            lambda: Scope(),
            None,
            SimpleNamespace(runtime=SimpleNamespace(persist_successor_authorization=persist)),
            {},
            SimpleNamespace(chapter=2, cohort_key=None),
            {},
        )
    session.rollback.assert_awaited_once()
    session.commit.assert_not_awaited()
    persist.assert_not_awaited()
