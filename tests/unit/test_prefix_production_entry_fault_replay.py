"""Actual prefix entry, file SQLite and real complete_text/ledger/cache.

Only _call_litellm is synthetic: no network/provider/PG/Redis. The inherited
`rig` supplies a typed narrative-validator adapter (not literary acceptance).
Foundation lookup, WF creation, saves, lineage and real judge parsing are used.
The concurrency barrier wraps the real judge; save observation never replaces
its implementation. Red tests assert monotonic committed business state.
"""

from __future__ import annotations

import asyncio
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from uuid import UUID

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.schema import CreateTable
from test_adopted_knowledge_lineage import _jsonb  # noqa: F401
from test_chapter_outline_readiness import (
    rig,  # noqa: F401
    sqlite_identity_store,  # noqa: F401
)
from test_prefix_contract_repair import case as helper_case  # noqa: F401
from test_prefix_contract_repair import patches as helper_patches

from bestseller.infra.db.models import (
    ChapterDraftVersionModel,
    ChapterModel,
    ExecutionAttemptModel,
    ExecutionBudgetModel,
    ExecutionOperationModel,
    LlmRunModel,
    PlanningArtifactVersionModel,
    ProjectModel,
    WorkflowRunModel,
)
from bestseller.services import chapter_outline_readiness as prefix
from bestseller.services import llm, projects, workflows
from bestseller.services import outline_llm_judge as judge
from bestseller.services import prefix_contract_repair as repair
from bestseller.services.budget import bind_execution_scope
from bestseller.services.prompt_source_policy import content_hash
from bestseller.settings import load_settings

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]
_GATEWAY = llm.complete_text
_JUDGE = judge.judge_outline_commercial_readiness
_CREATE = workflows.create_workflow_run
_APPROVED = workflows.get_latest_approved_planning_artifact
_IMPORT = projects.import_planning_artifact
_MODELS = (
    ProjectModel,
    WorkflowRunModel,
    ChapterModel,
    ChapterDraftVersionModel,
    PlanningArtifactVersionModel,
    ExecutionBudgetModel,
    ExecutionOperationModel,
    ExecutionAttemptModel,
    LlmRunModel,
)


class Interrupted(BaseException):
    """Exit boundary without the service's Exception handler saving stale state."""


def _settings(root):
    settings = load_settings(env={}).model_copy(deep=True)
    settings.llm.mock = False
    for role in (settings.llm.planner, settings.llm.critic):
        role.model = "openai/synthetic-prefix-entry"
        role.model_override = None
        role.rate_limit_fallback_model = None
    settings.budget.execution_max_calls = 20
    settings.budget.execution_max_tokens = 1_000_000
    settings.budget.execution_deadline_seconds = 60
    settings.artifact_store.mode = "local"
    settings.artifact_store.local_dir = Path(root) / "physical"
    settings.artifact_store.retain_prompt_payloads = True
    return settings


def _install(monkeypatch, sends, *, passed=False):
    monkeypatch.setattr(prefix, "complete_text", _GATEWAY)
    monkeypatch.setattr(judge, "complete_text", _GATEWAY)
    monkeypatch.setattr(prefix, "judge_outline_commercial_readiness", _JUDGE)
    monkeypatch.setattr(prefix, "create_workflow_run", _CREATE)
    monkeypatch.setattr(prefix, "get_latest_approved_planning_artifact", _APPROVED)
    monkeypatch.setattr(prefix, "import_planning_artifact", _IMPORT)
    monkeypatch.setattr(judge, "resolve_commercial_judge_model_key", lambda _: None)

    async def synthetic(request, role):
        sends.append(
            {
                "template": request.prompt_template,
                "wf": str(request.workflow_run_id),
                "slot": request.sample_slot,
                "operation": request.operation_id,
            }
        )
        if request.prompt_template == "outline_commercial_judge":
            score = 0.99 if passed else 0.1
            payload = {
                "pass": passed,
                "overall_score": score,
                "dimension_scores": dict.fromkeys(
                    (
                        "commercial_pull",
                        "opening_pull",
                        "logic_consistency",
                        "decision_intelligence",
                        "knowledge_boundary",
                        "real_world_plausibility",
                        "methodology_compliance",
                        "scene_execution",
                    ),
                    score,
                ),
                "blocking_issues": [],
            }
        elif request.prompt_template == "chapter_outline_prefix_surgical_repair":
            # Pure function of exact paid input: retries yield the same patch.
            payload = {
                "patches": [
                    {
                        "chapter_number": 1,
                        "scope": "chapter",
                        "field": "goal",
                        "value": "Inspect sealed register "
                        + content_hash(request.user_prompt)[:12]
                        + ".",
                    }
                ]
            }
        elif request.prompt_template == "prefix-independent-sample-fixture":
            payload = {"observation": "synthetic independent draw"}
        else:
            raise AssertionError("unexpected model boundary: " + str(request.prompt_template))
        text = json.dumps(payload, ensure_ascii=False)
        return text, 11, 7, "stop", None, llm._build_raw_assistant_message(text, None)

    monkeypatch.setattr(llm, "_call_litellm", synthetic)


@pytest.fixture
async def entry_case(rig, tmp_path, monkeypatch):  # noqa: F811
    from bestseller.domain.book_design_snapshot import build_book_design_snapshot
    from bestseller.services.outline_recovery_checkpoint import recovery_binding
    from bestseller.services.rolling_outline import build_macro_plan, build_rolling_outline_plan

    path = tmp_path / "prefix_entry_test.sqlite3"
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}", connect_args={"timeout": 1})
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.exec_driver_sql("PRAGMA journal_mode=WAL")
        for model in _MODELS:
            ddl = str(CreateTable(model.__table__).compile(dialect=connection.dialect))
            await connection.exec_driver_sql(ddl.replace("::jsonb", ""))
    settings = _settings(tmp_path)
    sends = []
    _install(monkeypatch, sends)
    async with sessions() as session:
        design = build_book_design_snapshot(
            creation_intent={
                "genre_intent": {
                    "genre_key": "mystery",
                    "genre_label": "Mystery",
                    "source": "legacy_inferred",
                },
                "chapter_count": 50,
                "language": "en",
            },
            protagonist="Courier",
            tone="measured",
            target_words=130000,
            chapter_count=50,
        )
        metadata = deepcopy(rig.project.metadata_json)
        metadata.update(
            book_design_snapshot=design.model_dump(mode="json"),
            book_design_snapshot_hash=design.source_hash,
            book_design_snapshot_id=design.snapshot_id,
        )
        macro = build_macro_plan(
            [{"chapter_number": n, "anchor": f"Anchor {n}"} for n in range(1, 51)]
        )
        metadata["rolling_outline_plan"] = build_rolling_outline_plan(
            macro,
            current_state_snapshot={"current_chapter": 0},
            next_macro_anchor=macro.slots[9].to_dict(),
            source_snapshot_hash=design.source_hash,
            window_size=9,
        ).to_dict()
        record = metadata["outline_commercial_judge_checkpoints"]["window"]["best"]
        record["binding"] = recovery_binding(metadata, 1, [1, 9])
        record["report"]["outline_repair_dependency_hash"] = record["binding"]["dependency_hash"]
        project = ProjectModel(
            id=rig.project.id,
            slug=rig.project.slug,
            title=rig.project.title,
            genre=rig.project.genre,
            language="en",
            target_chapters=50,
            target_word_count=130000,
            metadata_json=metadata,
        )
        session.add(project)
        for kind, asset in rig.foundation.items():
            content = deepcopy(asset.content)
            content["_meta"]["source_snapshot_hash"] = design.source_hash
            session.add(
                PlanningArtifactVersionModel(
                    id=asset.id,
                    project_id=project.id,
                    artifact_type=kind,
                    status="approved",
                    version_no=1,
                    schema_version="1.0",
                    source_run_id=asset.source_run_id,
                    content=content,
                )
            )
        await session.commit()

        # Initialize through the public entry, stopping at its first real
        # judge boundary after committed candidate+policy checkpoints.
        async def stop(*args, **kwargs):
            raise Interrupted

        monkeypatch.setattr(prefix, "judge_outline_commercial_readiness", stop)
        with pytest.raises(Interrupted):
            with bind_execution_scope(f"project:{project.id}"):
                await prefix.approve_chapter_outline_prefix(
                    session,
                    settings,
                    project=project,
                    source_payload=rig.source,
                    through_chapter=1,
                    max_repair_rounds=2,
                    requested_by="synthetic-test",
                )
        wf = await session.scalar(
            select(WorkflowRunModel).where(WorkflowRunModel.workflow_type == prefix.WORKFLOW_TYPE)
        )
        wf_id = wf.id
        await session.rollback()
    monkeypatch.setattr(prefix, "judge_outline_commercial_readiness", _JUDGE)
    case = SimpleNamespace(
        engine=engine,
        sessions=sessions,
        path=path,
        root=tmp_path,
        settings=settings,
        sends=sends,
        source=deepcopy(rig.source),
        project_id=rig.project.id,
        wf_id=wf_id,
        scope=f"project:{rig.project.id}",
    )
    try:
        yield case
    finally:
        await engine.dispose()


async def _entry(c, *, outer="operator-first", session=None):
    if session is None:
        async with c.sessions() as owned:
            return await _entry(c, outer=outer, session=owned)
    project = await session.get(ProjectModel, c.project_id)
    with bind_execution_scope(c.scope), llm.bind_execution_operation(outer):
        return await prefix.approve_chapter_outline_prefix(
            session,
            c.settings,
            project=project,
            source_payload=c.source,
            through_chapter=1,
            max_repair_rounds=2,
            workflow_run_id=c.wf_id,
            requested_by="synthetic-test",
            allow_editorial_debt=False,
        )


async def _snapshot(c):
    async with c.sessions() as observer:
        wf = await observer.get(WorkflowRunModel, c.wf_id)
        budgets = list(await observer.scalars(select(ExecutionBudgetModel)))
        operations = list(await observer.scalars(select(ExecutionOperationModel)))
        attempts = list(await observer.scalars(select(ExecutionAttemptModel)))
        runs = list(await observer.scalars(select(LlmRunModel)))
        artifacts = list(await observer.scalars(select(PlanningArtifactVersionModel)))
        return {
            "state": deepcopy(wf.metadata_json),
            "status": wf.status,
            "budget": [(b.scope_id, b.calls_used, b.tokens_charged) for b in budgets],
            "operations": sorted(
                (o.key, o.status, o.input_hash, content_hash(o.result_json)) for o in operations
            ),
            "attempts": sorted(
                (a.id, a.operation_key, a.status, a.input_hash, a.reserved_tokens, a.actual_tokens)
                for a in attempts
            ),
            "runs": sorted((str(r.id), str(r.workflow_run_id), r.prompt_hash) for r in runs),
            "artifacts": sorted(str(a.id) for a in artifacts),
        }


@pytest.mark.parametrize("delayed_boundary", ["judge", "repair"])
async def test_late_same_wf_result_cannot_regress_committed_candidate_or_terminal(
    entry_case, monkeypatch, delayed_boundary
):
    """RED until prefix state saves have a stale-state fence (no xfail)."""
    c = entry_case
    entered, release = asyncio.Event(), asyncio.Event()
    stale_commits = []
    real_save = prefix._save
    real_patch = prefix.complete_text
    delayed_once = False

    async def wait_if_slow(kind):
        nonlocal delayed_once
        if (
            kind == delayed_boundary
            and not delayed_once
            and asyncio.current_task().get_name() == "stale-prefix-owner"
        ):
            delayed_once = True
            entered.set()
            await asyncio.wait_for(release.wait(), 3)

    async def judge_boundary(*args, **kwargs):
        await wait_if_slow("judge")
        return await _JUDGE(*args, **kwargs)

    async def patch_boundary(*args, **kwargs):
        await wait_if_slow("repair")
        return await real_patch(*args, **kwargs)

    async def observed_save(session, workflow, state):
        await real_save(session, workflow, state)
        if release.is_set() and asyncio.current_task().get_name() == "stale-prefix-owner":
            stale_commits.append(await _snapshot(c))

    monkeypatch.setattr(prefix, "judge_outline_commercial_readiness", judge_boundary)
    monkeypatch.setattr(prefix, "complete_text", patch_boundary)
    monkeypatch.setattr(prefix, "_save", observed_save)
    slow = asyncio.create_task(_entry(c, outer="outer-A"), name="stale-prefix-owner")
    try:
        await asyncio.wait_for(entered.wait(), 3)
        winner = await asyncio.wait_for(_entry(c, outer="outer-B"), 4)
        before = await _snapshot(c)
        assert not winner["passed"] and before["state"]["phase"] == "rejected"
        assert len(before["state"]["candidates"]) == 3
        assert len(before["state"]["patch_attempts"]) == 2
        physical_before = len(c.sends)
        release.set()
        try:
            await asyncio.wait_for(slow, 4)
        except prefix.OutlinePrefixReadinessError:
            # A future source fix may explicitly refuse the stale owner.
            pass
        after = await _snapshot(c)
        assert len(c.sends) == physical_before == 5, "stale replay sent a fresh request"
        assert after["budget"] == before["budget"]
        assert after["operations"] == before["operations"]
        assert after["attempts"] == before["attempts"]
        regressions = [
            {
                "phase": s["state"]["phase"],
                "status": s["status"],
                "candidates": len(s["state"]["candidates"]),
                "repairs": len(s["state"]["patch_attempts"]),
            }
            for s in stale_commits
            if s["state"] != before["state"] or s["status"] != before["status"]
        ]
        assert not regressions, f"stale committed state overwrote terminal: {regressions}"
        assert after["state"] == before["state"] and after["status"] == before["status"]
    finally:
        release.set()
        if not slow.done():
            slow.cancel()
        await asyncio.gather(slow, return_exceptions=True)


async def test_terminal_replay_new_outer_operator_does_not_send_or_grow(entry_case):
    c = entry_case
    first = await _entry(c)
    before = await _snapshot(c)
    assert not first["passed"] and len(c.sends) == 5
    for outer in ("different-outer-WF", "another-outer-WF"):
        assert await _entry(c, outer=outer) == first
        assert await _snapshot(c) == before
    assert len(c.sends) == 5


async def _outer_workflow(c, label):
    async with c.sessions() as session:
        row = await _CREATE(
            session,
            project_id=c.project_id,
            workflow_type="prefix-entry-outer-fixture",
            status=prefix.WorkflowStatus.RUNNING,
            scope_type="project",
            scope_id=c.project_id,
            requested_by=label,
        )
        await session.commit()
        return row.id


async def test_completed_public_entry_new_outer_wfs_keep_original_paid_owner(
    entry_case, monkeypatch
):
    c = entry_case
    _install(monkeypatch, c.sends, passed=True)
    outer_a = await _outer_workflow(c, "outer-A")
    first = await _entry(c, outer=str(outer_a))
    before = await _snapshot(c)
    assert first["passed"] and before["status"] == "completed"
    assert len(c.sends) == 1 and len(before["artifacts"]) == 5
    for label in ("outer-B", "outer-C"):
        outer = await _outer_workflow(c, label)
        assert outer != outer_a != c.wf_id
        assert await _entry(c, outer=str(outer)) == first
        assert await _snapshot(c) == before
    assert len(c.sends) == 1
    assert {wf for _, wf, _ in before["runs"]} == {str(c.wf_id)}


async def test_import_flushed_then_raise_cannot_commit_approved_orphan(entry_case, monkeypatch):
    """A real flushed import must roll back while the failed paid review stays."""
    c = entry_case
    _install(monkeypatch, c.sends, passed=True)
    before = await _snapshot(c)
    flushed = []

    async def import_then_fail(session, slug, payload):
        artifact = await _IMPORT(session, slug, payload)
        await session.flush()
        assert artifact.status == "approved" and artifact.source_run_id == c.wf_id
        # Prove the row really reached SQL inside the publication transaction.
        assert await session.get(PlanningArtifactVersionModel, artifact.id) is artifact
        async with c.sessions() as observer:
            assert await observer.get(PlanningArtifactVersionModel, artifact.id) is None
        flushed.append(artifact.id)
        raise RuntimeError("synthetic failure after actual artifact flush")

    monkeypatch.setattr(prefix, "import_planning_artifact", import_then_fail)
    with pytest.raises(RuntimeError, match="after actual artifact flush"):
        await _entry(c)
    after = await _snapshot(c)
    assert len(flushed) == 1 and len(c.sends) == 1
    assert after["status"] == "failed"
    assert after["state"]["phase"] != "completed" and not after["state"].get("artifact_id")
    assert len(after["state"]["candidates"]) == len(before["state"]["candidates"]) == 1
    assert not after["state"]["patch_attempts"]
    assert after["budget"][0][1:] == (1, 18)
    assert len(after["attempts"]) == 1 and len(after["runs"]) == 1
    assert after["artifacts"] == before["artifacts"], "failed publication committed approved orphan"
    async with c.sessions() as observer:
        assert await observer.get(PlanningArtifactVersionModel, flushed[0]) is None

    # Retry the same owner: cached review, one publication, no second payment.
    monkeypatch.setattr(prefix, "import_planning_artifact", _IMPORT)
    recovered = await _entry(c, outer="publication-recovery-outer")
    saved = await _snapshot(c)
    assert recovered["passed"] and saved["status"] == "completed"
    assert len(saved["artifacts"]) == len(before["artifacts"]) + 1
    assert saved["budget"] == after["budget"] and saved["attempts"] == after["attempts"]
    assert saved["operations"] == after["operations"] and len(c.sends) == 1
    assert await _entry(c, outer="completed-publication-replay") == recovered
    assert await _snapshot(c) == saved


async def test_actual_contract_helper_acquires_project_before_each_wf_lock(helper_case):  # noqa: F811
    """Trace real SQLite execution order; NOT PostgreSQL row-lock/deadlock proof.

    Uses the existing real contract-validator fixture. Its completion adapter
    writes an inert LlmRun (not the complete_text/financial proof used above).
    Session methods delegate to SQL; only the lock-request order is observed.
    """
    c = helper_case
    c.outcomes.append(helper_patches())
    helper_wf_locks = []
    project_locked = False
    async with c.factory() as session:
        get, scalar, commit, rollback = (
            session.get,
            session.scalar,
            session.commit,
            session.rollback,
        )

        async def ordered_get(model, identity, **kwargs):
            nonlocal project_locked
            locking = bool(kwargs.get("with_for_update"))
            if model is WorkflowRunModel and locking:
                assert project_locked, "WF get(FOR UPDATE) preceded project lock"
            result = await get(model, identity, **kwargs)
            if model is ProjectModel and locking and result is not None:
                project_locked = True
            return result

        async def ordered_scalar(statement, *args, **kwargs):
            nonlocal project_locked
            entities = [item.get("entity") for item in statement.column_descriptions]
            locking = statement._for_update_arg is not None
            if WorkflowRunModel in entities and locking:
                helper_wf_locks.append(project_locked)
                assert project_locked, (
                    "contract helper locked WF before project in this transaction"
                )
            result = await scalar(statement, *args, **kwargs)
            if ProjectModel in entities and locking and result is not None:
                project_locked = True
            return result

        async def observed_commit():
            nonlocal project_locked
            await commit()
            project_locked = False

        async def observed_rollback():
            nonlocal project_locked
            await rollback()
            project_locked = False

        session.get = ordered_get
        session.scalar = ordered_scalar
        session.commit = observed_commit
        session.rollback = observed_rollback
        workflow = await session.get(WorkflowRunModel, c.workflow.id)
        result = await repair.validate_prefix_candidate(
            session,
            load_settings(),
            project=c.project,
            workflow=workflow,
            state=c.state,
            candidate=c.candidate,
            cast=c.cast,
            contents=c.contents,
            continuity=None,
            max_repair_rounds=2,
        )
    assert len(helper_wf_locks) == 3 and all(helper_wf_locks)
    assert result["chapters"][0]["scenes"][0]["participants"] == ["Courier", "Guard"]
    assert len(c.calls) == 1 and c.state["hard_patch_attempts"][0]["status"] == "validated"


async def test_paid_return_outer_rollback_reuses_original_owner_from_new_outer(
    entry_case, monkeypatch
):
    c = entry_case
    original_save = prefix._save

    async def rollback_after_paid(session, workflow, state):
        if "report" in state["candidates"][-1]:
            await session.rollback()
            raise Interrupted
        await original_save(session, workflow, state)

    monkeypatch.setattr(prefix, "_save", rollback_after_paid)
    with pytest.raises(Interrupted):
        await _entry(c)
    before = await _snapshot(c)
    assert len(c.sends) == 1 and not before["runs"]
    assert before["budget"][0][1:] == (1, 18)
    assert "report" not in before["state"]["candidates"][-1]
    monkeypatch.setattr(prefix, "_save", original_save)
    await _entry(c, outer="new-outer-WF-with-original-prefix-owner")
    after = await _snapshot(c)
    assert len(c.sends) == 5 and after["budget"][0][1:] == (5, 90)
    assert len(after["runs"]) == 5
    assert {wf for _, wf, _ in after["runs"]} == {str(c.wf_id)}
    assert {s["wf"] for s in c.sends} == {str(c.wf_id)}
    assert set(before["attempts"]) <= set(after["attempts"])


async def test_independent_slots_not_collapsed_but_same_slot_cross_outer_reuses(entry_case):
    c = entry_case
    owners = [await _outer_workflow(c, label) for label in ("A", "B", "C")]

    async def sample(slot, outer):
        async with c.sessions() as session:
            with (
                bind_execution_scope(c.scope),
                llm.bind_execution_operation(str(outer)),
                llm.bind_execution_operation("fixed-component-owner"),
            ):
                result = await _GATEWAY(
                    session,
                    c.settings,
                    llm.LLMCompletionRequest(
                        project_id=c.project_id,
                        workflow_run_id=outer,
                        logical_role="critic",
                        system_prompt="independent sample fixture",
                        user_prompt="same semantic input",
                        prompt_template="prefix-independent-sample-fixture",
                        prompt_version="1",
                        sample_slot=slot,
                        fallback_response="unused",
                    ),
                )
                await session.commit()
                return result

    one = await sample(0, owners[0])
    two = await sample(1, owners[1])
    repeated = await sample(0, owners[2])
    assert len(c.sends) == 2
    assert one.llm_run_id == repeated.llm_run_id != two.llm_run_id
    assert (
        one.metadata["execution_receipt"]["operation_key"]
        != two.metadata["execution_receipt"]["operation_key"]
    )
    snap = await _snapshot(c)
    assert snap["budget"][0][1:] == (2, 36) and len(snap["attempts"]) == 2
    assert repeated.metadata["paid_call_origin"]["workflow_run_id"] == str(owners[0])
    assert {wf for _, wf, _ in snap["runs"]} == set(map(str, owners[:2]))


@pytest.mark.parametrize("cut", ["paid-before-save", "terminal-committed"])
async def test_real_process_exit_public_entry_replay_keeps_paid_cursor(entry_case, cut):
    """Real os._exit, new interpreter, actual entry and complete_text cache."""
    c = entry_case
    config = c.root / "entry-test-config.json"
    config.write_text(
        json.dumps(
            {
                "source": c.source,
                "project_id": str(c.project_id),
                "wf_id": str(c.wf_id),
                "scope": c.scope,
                "db": str(c.path),
            }
        ),
        encoding="utf-8",
    )
    env = {
        **os.environ,
        "BESTSELLER__DATABASE__URL": "sqlite+aiosqlite:////private/tmp/prefix_child_test.sqlite3",
        "BESTSELLER_LLM_RUNTIME_PROFILE_PATH": str(c.root / "no-runtime-profile.json"),
        "BESTSELLER_LINEAGE_PG": "0",
        "PYTHONDONTWRITEBYTECODE": "1",
    }

    async def child(mode):
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            str(Path(__file__).resolve()),
            str(config),
            mode,
            cut,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), 4)
        except BaseException:
            process.kill()
            await process.wait()
            raise
        assert process.returncode == (86 if mode == "interrupt" else 0), (
            stdout.decode()[-1000:] + stderr.decode()[-3000:]
        )

    await child("interrupt")
    stopped = await _snapshot(c)
    assert stopped["budget"][0][1] == (1 if cut == "paid-before-save" else 5)
    if cut == "paid-before-save":
        assert "report" not in stopped["state"]["candidates"][-1]
        assert not stopped["runs"]  # telemetry transaction died, paid receipt did not
    else:
        assert stopped["state"]["phase"] == "rejected"
    await child("resume")
    resumed = await _snapshot(c)
    assert resumed["state"]["phase"] == "rejected"
    assert resumed["budget"][0][1:] == (5, 90)
    assert len(resumed["attempts"]) == 5 and len(resumed["runs"]) == 5
    assert len(resumed["state"]["patch_attempts"]) == 2
    assert {wf for _, wf, _ in resumed["runs"]} == {str(c.wf_id)}
    await child("resume")
    assert await _snapshot(c) == resumed
    sent = (c.root / "synthetic-sends.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(sent) == 5  # persisted outside the dying process; no in-memory cache


async def _child_main(config_path, mode, cut):
    """Standalone child uses only the explicit file SQLite test DB."""
    from bestseller.domain.workflow import ChapterOutlineBatchInput

    config = json.loads(Path(config_path).read_text(encoding="utf-8"))
    root = Path(config_path).parent
    db = Path(config["db"])
    assert db.parent == root and db.name.endswith("_test.sqlite3")
    engine = create_async_engine(f"sqlite+aiosqlite:///{db}", connect_args={"timeout": 1})
    c = SimpleNamespace(
        sessions=async_sessionmaker(engine, expire_on_commit=False),
        settings=_settings(root),
        source=config["source"],
        project_id=UUID(config["project_id"]),
        wf_id=UUID(config["wf_id"]),
        scope=config["scope"],
    )
    monkeypatch = pytest.MonkeyPatch()

    class Sends(list):
        def append(self, value):
            with (root / "synthetic-sends.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(value) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
            super().append(value)

    _install(monkeypatch, Sends())

    def typed(payload, **kwargs):
        # Same declared narrative fixture boundary as parent rig; not a mock
        # judge, provider parser, financial proof or workflow implementation.
        assert kwargs["expected_count"] == len(payload["chapters"])
        return ChapterOutlineBatchInput.model_validate(payload).model_dump(
            mode="json", by_alias=True
        )

    monkeypatch.setattr(prefix.planner, "_validate_generated_volume_outline_or_raise", typed)
    save = prefix._save

    async def exit_at_checkpoint(session, workflow, state):
        if (
            mode == "interrupt"
            and cut == "paid-before-save"
            and "report" in state["candidates"][-1]
        ):
            os._exit(86)
        await save(session, workflow, state)
        if mode == "interrupt" and cut == "terminal-committed" and state["phase"] == "rejected":
            os._exit(86)

    monkeypatch.setattr(prefix, "_save", exit_at_checkpoint)
    try:
        await _entry(c, outer="resumed-outer" if mode == "resume" else "first-outer")
    finally:
        monkeypatch.undo()
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(_child_main(*sys.argv[1:]))
