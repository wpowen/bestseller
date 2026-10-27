"""Pure denial/preparation and inert runtime lifecycle checks; no PG/provider."""

from contextlib import nullcontext
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
    / "reports/continuous-recovery-2026-10-16/advance_fresh_chapters.py"
)
spec = importlib.util.spec_from_file_location("fresh_chapters_operator_subject", PATH)
op = importlib.util.module_from_spec(spec)
spec.loader.exec_module(op)


@pytest.fixture
def local(tmp_path):
    task_id, project_id, workflow = str(uuid4()), str(uuid4()), str(uuid4())
    payload = {"slug": "unrelated-fresh-fiction", "language": "zh-CN", "genre": "fantasy"}
    task = {"task_id": task_id, "payload": payload, "status": "completed"}
    state = {
        "schema": "fresh-acceptance-intake.v1",
        "task_id": task_id,
        "scope": "conception:" + task_id,
        "project_slug": payload["slug"],
        "request": {"chapter_count": 30},
        "envelope_sha256": op.digest(payload),
        "input_provenance": {"chapter_count": {"value": 30}, "words_per_chapter": {"value": 2600}},
        "authorization_key": "original-authorization",
    }
    state_path, task_path = tmp_path / "state.json", tmp_path / "tasks.json"
    state_path.write_text(json.dumps(state))
    task_path.write_text(json.dumps([task]))
    foundation = tmp_path / "foundation.json"
    argv = [
        "--project-id",
        project_id,
        "--chapter",
        "1",
        "--state",
        str(state_path),
        "--tasks",
        str(task_path),
        "--foundation-ref",
        str(foundation),
        "--foundation-sha256",
        "not-yet-written",
        "--parent-review-ref",
        "parent:actual-review",
        "--receipt-dir",
        str(tmp_path),
    ]
    args = op.parser().parse_args(argv)
    owner = op.fresh.identity(args)[2]
    selected = {
        kind: {
            "id": str(uuid4()),
            "hash": op.digest({"kind": kind}),
            "source_run_id": workflow,
            "version_no": 2,
        }
        for kind in op.fresh.KINDS
    }
    association = {
        "id": workflow,
        "project_id": project_id,
        "status": "completed",
        "workflow_type": "generate_foundation_plan",
        "artifact_ids": {kind: row["id"] for kind, row in selected.items()},
    }
    after = {
        "project_id": project_id,
        "foundation": selected,
        "completed_foundation_workflows": [workflow],
        "foundation_associations": {workflow: association},
    }
    receipt = {
        "schema": op.fresh.SCHEMA,
        "stage": "foundation",
        "status": "completed",
        "database_read": True,
        "identity": owner,
        "after": after,
    }
    foundation.write_text(json.dumps(receipt))
    args.foundation_sha256 = op.file_hash(foundation)
    return SimpleNamespace(
        args=args,
        argv=argv,
        state=state,
        task=task,
        receipt=receipt,
        foundation=foundation,
        owner=owner,
        selected=selected,
        workflow=workflow,
    )


def test_default_preparation_never_imports_runtime_or_connects(local, monkeypatch, capsys):
    def forbidden(*args, **kwargs):
        raise AssertionError("runtime must not be entered")

    monkeypatch.setattr(op, "helpers", forbidden)
    monkeypatch.setattr(op, "execute", forbidden)
    argv = [*local.argv, "--foundation-sha256", local.args.foundation_sha256]
    assert op.main(argv) == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan["status"] == "prepared_only" and plan["stage"] == "readback"
    assert plan["database_access"] is False and plan["provider_calls"] == 0
    assert plan["bounds"] == {
        "target_chapters": 30,
        "words_per_chapter": 2600,
        "acceptance_chapters": [1, 2, 3],
    }
    assert {p.name for p in local.foundation.parent.iterdir()} == {
        "foundation.json",
        "tasks.json",
        "state.json",
    }
    assert not hasattr(local.args, "timeout_seconds") and not hasattr(local.args, "max_calls")


@pytest.mark.parametrize(
    "flag,value",
    [
        ("--chapter", "4"),
        ("--chapter", "0"),
        ("--target-chapters", "3"),
        ("--target-chapters", "50"),
        ("--words-per-chapter", "1800"),
        ("--stage", "plan"),
    ],
)
def test_parser_bounds(local, flag, value):
    with pytest.raises(SystemExit):
        op.parser().parse_args([*local.argv, flag, value])


@pytest.mark.parametrize(
    "change", ["schema", "status", "database_read", "owner", "project", "partial", "association"]
)
def test_incomplete_or_foreign_foundation_refused_before_database(local, change):
    receipt = deepcopy(local.receipt)
    if change in {"schema", "status", "database_read"}:
        receipt[change] = False
    elif change == "owner":
        receipt["identity"]["scope"] = "another-scope"
    elif change == "project":
        receipt["after"]["project_id"] = str(uuid4())
    elif change == "partial":
        receipt["after"]["foundation"].pop("cast_spec")
    else:
        receipt["after"]["foundation_associations"][local.workflow]["artifact_ids"][
            "world_spec"
        ] = str(uuid4())
    local.foundation.write_text(json.dumps(receipt))
    local.args.foundation_sha256 = op.file_hash(local.foundation)
    with pytest.raises(op.fresh.Refused):
        op.proposal(local.args)


def test_frozen_files_and_selected_source_changes_are_denied(local):
    local.foundation.write_text(local.foundation.read_text() + " ")
    with pytest.raises(op.fresh.Refused, match="foundation_receipt_changed"):
        op.proposal(local.args)
    local.args.foundation_sha256 = op.file_hash(local.foundation)
    plan = op.proposal(local.args)
    changed = deepcopy(local.receipt["after"])
    changed["foundation"]["cast_spec"]["hash"] = "0" * 64
    with pytest.raises(op.fresh.Refused, match="selected_foundation_changed"):
        op.verify_foundation(plan, changed)
    state_path = Path(local.args.state)
    state = json.loads(state_path.read_text())
    state["scope"] = "conception:" + str(uuid4())
    state_path.write_text(json.dumps(state))
    with pytest.raises(op.fresh.Refused, match="original_scope_required"):
        op.proposal(local.args)


def _before(local):
    return {
        **deepcopy(local.receipt["after"]),
        "stage_authority_sha256": "exact-authority",
        "budget": {
            "execution_open": True,
            "max_calls": 141,
            "max_tokens": 123456,
            "deadline_at": "historical",
            "provider_authorization": {"mode": "completion_bound"},
            "calls_used": 158,
            "tokens_charged": 1377494,
        },
        "inflight": [],
        "active_workflows": [],
        "metadata": {
            "conception_approved": True,
            "creation_canonical_snapshot_id": "canonical",
            "creation_canonical_snapshot_hash": "hash",
        },
        "snapshot": {"id": "canonical"},
        "options": {"snapshot": {"content_hash": "hash"}},
        "chapters": [{"number": 1}, {"number": 2}],
        "prefix_verified": True,
        "predecessor_drafts": {"old-draft": "complete-row-hash"},
        "predecessors": {"1": {"adopted": True, "knowledge_closed": True}},
        "evidence": {
            "adopted": True,
            "knowledge_closed": True,
            "draft_id": "exact-draft",
            "body_sha256": "exact-body",
        },
    }


@pytest.mark.parametrize(
    "guard",
    [
        "stale",
        "scope_closed",
        "inflight",
        "active",
        "terminal",
        "canonical",
        "source",
        "fourth",
        "prior_adoption",
        "prior_knowledge",
        "prefix",
    ],
)
def test_write_denials_do_not_waive_real_preconditions(local, guard):
    args = deepcopy(local.args)
    args.stage, args.chapter = "write", 2
    before = _before(local)
    reviewed = deepcopy(before)
    if guard == "stale":
        before["stage_authority_sha256"] = "changed"
    elif guard == "scope_closed":
        before["budget"]["execution_open"] = False
    elif guard == "inflight":
        before["inflight"] = ["unknown-return"]
    elif guard == "active":
        before["active_workflows"] = ["running"]
    elif guard == "terminal":
        before["metadata"]["execution_terminal"] = {"terminal": True}
    elif guard == "canonical":
        before["metadata"]["conception_approved"] = False
    elif guard == "source":
        before["options"]["snapshot"]["content_hash"] = "changed"
    elif guard == "fourth":
        before["chapters"].append({"number": 4})
    elif guard in {"prior_adoption", "prior_knowledge"}:
        before["predecessors"]["1"][
            "adopted" if guard == "prior_adoption" else "knowledge_closed"
        ] = False
    else:
        before["prefix_verified"] = False
    with pytest.raises(op.fresh.Refused):
        op.admit(args, {}, before, reviewed)


@pytest.mark.parametrize(
    "change", ["old_row", "counter", "ceiling", "authorization", "adoption", "knowledge", "body"]
)
def test_committed_readback_must_prove_history_adoption_and_knowledge(local, change):
    plan = op.proposal(local.args)
    before = _before(local)
    after = deepcopy(before)
    local.args.stage = "knowledge"
    if change == "old_row":
        after["predecessor_drafts"]["old-draft"] = "changed"
    elif change == "counter":
        after["budget"]["calls_used"] -= 1
    elif change == "ceiling":
        after["budget"]["max_calls"] += 1
    elif change == "authorization":
        after["budget"]["provider_authorization"] = {"reset": True}
    elif change == "body":
        after["evidence"]["body_sha256"] = "new-body"
    else:
        after["evidence"]["adopted" if change == "adoption" else "knowledge_closed"] = False
    with pytest.raises(op.fresh.Refused):
        op.verify_after(local.args, plan, before, after)


async def test_execute_review_is_checked_before_engine_creation(local, monkeypatch):
    from bestseller.infra.db import session as database

    def forbidden(*args, **kwargs):
        raise AssertionError("engine must not be created")

    monkeypatch.setattr(database, "create_engine", forbidden)
    plan = op.proposal(local.args)
    with pytest.raises(op.fresh.Refused, match="explicit_execute_required"):
        await op.execute(local.args, plan)
    local.args.execute = True
    with pytest.raises(op.fresh.Refused, match="review_current_plan_first"):
        await op.execute(local.args, plan)
    local.args.reviewed_plan_sha256 = plan["plan_sha256"]
    monkeypatch.setattr(op, "proposal", lambda _: {**plan, "plan_sha256": "changed-source"})
    with pytest.raises(op.fresh.Refused, match="source_or_inputs_changed"):
        await op.execute(local.args, plan)


@pytest.mark.parametrize("stage", ["readback", "write", "knowledge"])
@pytest.mark.parametrize("borrowed", [False, True])
@pytest.mark.parametrize("failure", [False, True])
async def test_inert_stage_lifecycle_and_independent_readbacks(
    local, monkeypatch, stage, borrowed, failure
):
    from sqlalchemy.ext import asyncio as sqlasync

    from bestseller import settings as settings_module
    from bestseller.infra.db import session as database
    from bestseller.services import production_control

    events, receipts = [], []
    args = local.args
    args.stage, args.execute = stage, True
    plan = op.proposal(args)
    args.reviewed_plan_sha256 = plan["plan_sha256"]
    before = _before(local)
    pool = object()
    monkeypatch.setattr(database, "_global_engine", pool if borrowed else None)
    settings = settings_module.load_settings().model_copy(deep=True)
    settings.generation.target_chapters = 30
    settings.generation.words_per_chapter.target = 2600
    monkeypatch.setattr(settings_module, "load_settings", lambda: settings)
    monkeypatch.setattr(settings_module, "apply_runtime_llm_profile", lambda x: x)

    async def init(_):
        events.append("init_pool")
        database._global_engine = pool

    async def shutdown():
        events.append("shutdown_pool")
        database._global_engine = None

    monkeypatch.setattr(database, "init_db", AsyncMock(side_effect=init))
    monkeypatch.setattr(database, "shutdown_db", AsyncMock(side_effect=shutdown))
    engine = SimpleNamespace(dispose=AsyncMock())
    monkeypatch.setattr(database, "create_engine", lambda _: engine)

    class Session:
        bind = SimpleNamespace(dialect=SimpleNamespace(name="sqlite"))

        async def __aenter__(self):
            events.append("session")
            if stage != "readback":
                assert database._global_engine is pool
            return self

        async def __aexit__(self, *args):
            pass

        async def get(self, *args):
            return SimpleNamespace(
                id=UUID(local.args.project_id),
                slug=local.owner["project_slug"],
                metadata_json={"execution_scope_id": local.state["scope"]},
            )

        async def commit(self):
            events.append("commit")

        async def rollback(self):
            events.append("rollback")

    from uuid import UUID

    monkeypatch.setattr(sqlasync, "async_sessionmaker", lambda *a, **k: Session)
    monkeypatch.setattr(
        op,
        "helpers",
        lambda: SimpleNamespace(safe_exception_diagnostic=lambda e: {"type": type(e).__name__}),
    )
    monkeypatch.setattr(op, "reviewed_inspection", lambda *a: before)
    monkeypatch.setattr(op, "gateway_guard", lambda *a: nullcontext())
    monkeypatch.setattr(
        production_control,
        "load_control_state",
        AsyncMock(return_value=SimpleNamespace(halted=False)),
    )

    async def observe(*a):
        events.append("observe")
        return deepcopy(before)

    async def perform(*a):
        events.append("perform")
        if failure:
            raise ValueError("inert_service_failure")
        return {"inert": True}

    monkeypatch.setattr(op, "observe", observe)
    monkeypatch.setattr(op, "perform", perform)

    async def rollback(*a):
        events.append("save_failure")

    monkeypatch.setattr(database, "_rollback_and_save_failures", rollback)

    def append(_, receipt):
        receipts.append(deepcopy(receipt))
        path = local.foundation.parent / f"receipt-{len(receipts)}.json"
        path.write_text(json.dumps(receipt, default=str))
        return path

    monkeypatch.setattr(op.fresh, "append_receipt", append)
    if failure and stage != "readback":
        with pytest.raises(ValueError, match="inert_service_failure"):
            await op.execute(args, plan)
        assert "save_failure" in events and "commit" not in events
        assert receipts[-1]["status"] == "failed"
    else:
        await op.execute(args, plan)
        assert receipts[-1]["status"] == "completed"
        assert ("perform" in events) == (stage != "readback")
    assert events.count("observe") == (1 if stage == "readback" else 2)
    assert "selection" not in events
    if stage != "readback" and not borrowed:
        assert events[0] == "init_pool" and events[-1] == "shutdown_pool"
    else:
        database.init_db.assert_not_awaited()
        database.shutdown_db.assert_not_awaited()
    engine.dispose.assert_awaited_once()


@pytest.mark.parametrize(
    "stage,wrong",
    [
        ("readback", None),
        ("write", "project"),
        ("knowledge", "scope"),
        ("write", "workflow"),
        ("write", "owner"),
    ],
)
async def test_gateway_denials_are_before_provider(stage, wrong, monkeypatch):
    from bestseller.services import llm
    from bestseller.services.llm import bind_execution_scope

    project_id, wid = uuid4(), uuid4()
    scope = "conception:" + str(uuid4())
    paid = AsyncMock(side_effect=AssertionError("provider must not be called"))
    monkeypatch.setattr(llm, "complete_text", paid)
    request = SimpleNamespace(project_id=project_id, workflow_run_id=wid)
    if wrong == "project":
        request.project_id = uuid4()
    if wrong == "workflow":
        request.workflow_run_id = None
    session = SimpleNamespace(
        get=AsyncMock(
            return_value=SimpleNamespace(
                project_id=uuid4() if wrong == "owner" else project_id,
                metadata_json={"execution_scope_id": scope},
            )
        )
    )
    with (
        bind_execution_scope("other" if wrong == "scope" else scope),
        op.gateway_guard(stage, project_id, scope),
    ):
        with pytest.raises(op.fresh.Refused):
            await llm.complete_text(session, None, request)
    paid.assert_not_awaited()


def test_reviewed_readback_is_bound_to_exact_file_sources_and_chapter(local):
    plan = op.proposal(local.args)
    receipt = {
        key: plan[key]
        for key in (
            "identity",
            "project_id",
            "chapter",
            "sources_sha256",
            "operator_sha256",
            "foundation_sha256",
        )
    }
    receipt.update(
        schema=op.SCHEMA,
        stage="readback",
        status="completed",
        database_read=True,
        after=_before(local),
    )
    path = local.foundation.parent / "readback.json"
    path.write_text(json.dumps(receipt))
    local.args.inspection_ref = str(path)
    local.args.inspection_sha256 = op.file_hash(path)
    assert op.reviewed_inspection(local.args, plan) == receipt["after"]
    for key, value in (
        ("chapter", 3),
        ("sources_sha256", "changed"),
        ("database_read", False),
        ("status", "failed"),
    ):
        changed = {**receipt, key: value}
        path.write_text(json.dumps(changed))
        local.args.inspection_sha256 = op.file_hash(path)
        with pytest.raises(op.fresh.Refused):
            op.reviewed_inspection(local.args, plan)
    path.write_text(json.dumps(receipt))
    with pytest.raises(op.fresh.Refused, match="inspection_file_changed"):
        op.reviewed_inspection(local.args, plan)


@pytest.mark.parametrize("stage,adopted", [("write", False), ("write", True), ("knowledge", True)])
async def test_perform_reuses_normal_entrypoints_and_exact_adoption(
    local, monkeypatch, stage, adopted
):
    from uuid import UUID

    local.args.stage = stage
    project = SimpleNamespace(id=UUID(local.args.project_id), slug=local.owner["project_slug"])
    session = SimpleNamespace(get=AsyncMock(return_value=project))
    pipeline, knowledge = (
        AsyncMock(return_value={"pipeline": True}),
        AsyncMock(return_value={"knowledge": True}),
    )
    generic = SimpleNamespace(
        run_chapter_pipeline=pipeline,
        knowledge=knowledge,
        serializable=lambda x: x,
        progress=object(),
    )
    monkeypatch.setattr(op, "helpers", lambda: generic)
    result = await op.perform(session, object(), local.args, {}, {"evidence": {"adopted": adopted}})
    if stage == "knowledge":
        knowledge.assert_awaited_once()
        pipeline.assert_not_awaited()
        assert result == {"knowledge": True}
    elif adopted:
        knowledge.assert_not_awaited()
        pipeline.assert_not_awaited()
        assert result["reused"] is True
    else:
        pipeline.assert_awaited_once()
        assert pipeline.call_args.args[2:4] == (project.slug, 1)
        assert pipeline.call_args.kwargs["chapter_first"] is True
        assert pipeline.call_args.kwargs["supersede_pending_rewrites"] is False
        assert pipeline.call_args.kwargs["export_markdown"] is False


async def test_observe_calls_actual_prefix_verification_boundary(local, monkeypatch):
    from uuid import UUID

    from bestseller.infra.db.models import ProjectModel
    from bestseller.services import chapter_outline_readiness as readiness

    plan = op.proposal(local.args)
    before = _before(local)
    before["authority_sha256"] = "full-current-source-authority"
    policy = {"mode": "completion_bound"}
    before["authorization_receipt"] = {"authorization": policy}
    monkeypatch.setattr(op.fresh, "observation", AsyncMock(return_value=before))
    generic = SimpleNamespace(
        validate_authorization=lambda _: policy,
        protection=AsyncMock(return_value={}),
        evidence=AsyncMock(return_value={"adopted": False, "knowledge_closed": False}),
    )
    monkeypatch.setattr(op, "helpers", lambda: generic)
    project = SimpleNamespace(id=UUID(local.args.project_id), slug=local.owner["project_slug"])
    artifact_id = str(uuid4())
    chapter = SimpleNamespace(
        id=uuid4(), metadata_json={"outline_prefix_readiness": {"artifact_id": artifact_id}}
    )

    async def get(model, _):
        return project if model is ProjectModel else object()

    session = SimpleNamespace(get=get, scalar=AsyncMock(return_value=chapter))
    verifier = AsyncMock(side_effect=ValueError("prefix_materialized_projection_changed"))
    monkeypatch.setattr(readiness, "verify_outline_prefix_readiness", verifier)
    with pytest.raises(ValueError, match="prefix_materialized_projection_changed"):
        await op.observe(session, None, local.state, local.task, local.args, plan)
    verifier.assert_awaited_once_with(
        session, project=project, artifact_id=UUID(artifact_id), chapter_number=1
    )


def test_failed_stage_readback_preserves_pending_outcome_without_promoting_grade(local):
    plan = op.proposal(local.args)
    local.args.stage = "write"
    before = _before(local)
    before["evidence"] = {"adopted": False, "knowledge_closed": False}
    op.verify_after(local.args, plan, before, deepcopy(before), completed=False)
    with pytest.raises(op.fresh.Refused, match="write_completed_without_exact_adoption"):
        op.verify_after(local.args, plan, before, deepcopy(before))
