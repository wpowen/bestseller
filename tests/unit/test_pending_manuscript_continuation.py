"""Real SQLite checkpoint/ownership/ledger tests; no network or paid model."""

import asyncio
from copy import deepcopy
from datetime import datetime, timezone, timedelta
from uuid import uuid4, UUID
import pytest
from sqlalchemy import MetaData, JSON, ForeignKeyConstraint, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from bestseller.infra.db import models as m
from bestseller.services import pending_manuscript_continuation as pc, draft_continuity as dc, llm
from bestseller.services.single_dispatch_authorization import bind_grant, authorization_id
from bestseller.settings import load_settings, LLMRoleSettings

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@pytest.fixture
async def db(tmp_path, monkeypatch):
    engine = create_async_engine(
        "sqlite+aiosqlite:///" + str(tmp_path / "pending_lane_test.sqlite3")
    )
    meta = MetaData()
    for model in (
        m.ProjectModel,
        m.WorkflowRunModel,
        m.ChapterModel,
        m.ChapterDraftVersionModel,
        m.ExecutionBudgetModel,
        m.ExecutionOperationModel,
        m.ExecutionAttemptModel,
    ):
        t = model.__table__.to_metadata(meta)
        for c in t.columns:
            if isinstance(c.type, JSONB):
                c.type = JSON()
                c.server_default = None
        for index in t.indexes:
            if (
                t.name == "chapter_draft_versions"
                and index.dialect_options["postgresql"].get("where") is not None
            ):
                index.dialect_options["sqlite"]["where"] = index.dialect_options["postgresql"][
                    "where"
                ]
        for constraint in list(t.constraints):
            if isinstance(constraint, ForeignKeyConstraint):
                t.constraints.remove(constraint)
    async with engine.begin() as conn:
        for t in meta.tables.values():
            await conn.run_sync(t.create)

    async def mode(*a, **kw):
        return True

    async def selected(session, settings, *, project, chapter):
        proof = (project.metadata_json.get("test_references") or {}).get(
            str(chapter.chapter_number)
        )
        if not proof:
            return None
        if project.metadata_json.get("revoked"):
            raise ValueError("approval_revoked")
        d = await session.get(m.ChapterDraftVersionModel, UUID(proof["draft_id"]))
        return d, proof

    async def sources(session, settings, *, project, chapter):
        return {"authored": project.metadata_json.get("authored", "原始来源")}

    monkeypatch.setattr(dc, "draft_mode_enabled", mode)
    monkeypatch.setattr(dc, "select_reference", selected)
    monkeypatch.setattr(dc, "current_sources", sources)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def seed(session, qualified=False):
    p = m.ProjectModel(
        id=uuid4(),
        slug="independent-river-story",
        title="溪桥笔记",
        genre="日常",
        language="zh-CN",
        project_type="linear",
        status="writing",
        target_chapters=12,
        target_word_count=24000,
        metadata_json={
            "execution_scope_id": "scope-untouched",
            "llm_model_id": "independent-model",
        },
    )
    session.add(p)
    await session.flush()
    refs = {}
    for n in range(1, 5):
        c = m.ChapterModel(
            id=uuid4(),
            project_id=p.id,
            volume_id=uuid4(),
            chapter_number=n,
            title="章" + str(n),
            chapter_goal="目标",
            status="draft",
            metadata_json={"finite_credit_used": 2},
        )
        old = m.ChapterDraftVersionModel(
            id=uuid4(),
            project_id=p.id,
            chapter_id=c.id,
            version_no=9 if n == 1 else 4,
            content_md="已验证保留正文" + str(n),
            word_count=8,
            is_current=n != 1,
            promotion_state="candidate",
            assembled_from_scene_draft_ids=[],
        )
        session.add_all([c, old])
        await session.flush()
        if n == 1:
            session.add(
                m.ChapterDraftVersionModel(
                    id=uuid4(),
                    project_id=p.id,
                    chapter_id=c.id,
                    version_no=10,
                    content_md="尚未验证当前稿",
                    word_count=8,
                    is_current=True,
                    promotion_state="candidate",
                    assembled_from_scene_draft_ids=[],
                )
            )
        if n < 4 or qualified:
            refs[str(n)] = {
                "receipt_id": str(uuid4()),
                "draft_id": str(old.id),
                "body_sha256": dc.body_hash(old.content_md),
                "formal_adoption": False,
            }
    p.metadata_json = {**p.metadata_json, "test_references": refs}
    session.add(
        m.ExecutionBudgetModel(
            scope_id="scope-untouched",
            max_calls=64,
            max_tokens=1000000,
            calls_used=316,
            tokens_charged=6112141,
            status="active",
            deadline_at=datetime.now(timezone.utc) - timedelta(days=2),
            metadata_json={},
        )
    )
    await session.commit()
    return p


async def permit(*args):
    return True, None


def stage_handlers(trace, break_after=None):
    out = {}
    for stage in pc.STAGES:

        async def run(session, project, row, outputs, stage=stage):
            trace.append(stage)
            if stage == break_after:
                raise asyncio.CancelledError()
            return {
                "normal_interface": stage,
                "chapter_number": 5,
            }  # transport boundaries only, no fictional adoption

        out[stage] = run
    return out


async def test_current_fourth_waits_then_same_id_wakes_without_rewriting_old_three(db):
    async with db() as s:
        p = await seed(s)
        r = await pc.record_intent(s, load_settings(), project=p, after_chapter=4)
        await s.commit()
        cid = r.id
        pid = p.id
        original = deepcopy(r.metadata_json["binding"])
        trace = []
        queue = Queue()
        first = await pc.enqueue(s, queue, r)
        job = first["job_id"]
        result = await pc.execute(
            s,
            cid,
            settings=load_settings(),
            handlers=stage_handlers(trace),
            permit=permit,
            owner_id="one",
        )
        assert result["state"] == "awaiting_reference" and trace == []
        fourth = await s.scalar(
            select(m.ChapterDraftVersionModel)
            .join(m.ChapterModel, m.ChapterDraftVersionModel.chapter_id == m.ChapterModel.id)
            .where(m.ChapterModel.chapter_number == 4)
        )
        p.metadata_json = {
            **p.metadata_json,
            "test_references": {
                **p.metadata_json["test_references"],
                "4": {
                    "receipt_id": str(uuid4()),
                    "draft_id": str(fourth.id),
                    "body_sha256": dc.body_hash(fourth.content_md),
                    "formal_adoption": False,
                },
            },
        }
        await s.commit()
        queue.finish(job)
        from bestseller.services.autonomous_dispatch import recover_pending_checkpoints

        await recover_pending_checkpoints(s, queue)
        assert queue.calls == 2 and job in queue.results and next(iter(queue.jobs)) != job
        await recover_pending_checkpoints(s, queue)
        assert queue.calls == 2
    async with db() as s:
        result = await pc.execute(
            s,
            cid,
            settings=load_settings(),
            handlers=stage_handlers(trace),
            permit=permit,
            owner_id="restarted",
        )
        assert result["state"] == "completed" and trace == list(pc.STAGES)
        r = await s.get(m.WorkflowRunModel, cid)
        assert r.metadata_json["binding"] == original
        assert (
            r.metadata_json["qualified_references"][0]["draft_id"]
            == original["prior_references"][0]["draft_id"]
        )
        assert (
            await pc.execute(
                s,
                cid,
                settings=load_settings(),
                handlers=stage_handlers(trace),
                permit=permit,
                owner_id="again",
            )
            == result
        )
        assert trace == list(pc.STAGES)
        b = await s.get(m.ExecutionBudgetModel, "scope-untouched")
        assert (b.calls_used, b.tokens_charged, b.max_calls) == (316, 6112141, 64)
        chapters = await s.scalars(select(m.ChapterModel).where(m.ChapterModel.project_id == pid))
        assert all(c.metadata_json["finite_credit_used"] == 2 for c in chapters)


async def test_restart_unknown_never_repeats_started_stage_and_committed_outputs_remain(db):
    trace = []
    async with db() as s:
        p = await seed(s, True)
        r = await pc.record_intent(s, load_settings(), project=p, after_chapter=4)
        await s.commit()
        cid = r.id
        with pytest.raises(asyncio.CancelledError):
            await pc.execute(
                s,
                cid,
                settings=load_settings(),
                handlers=stage_handlers(trace, "materialize"),
                permit=permit,
                owner_id="first",
            )
    async with db() as s:
        result = await pc.execute(
            s,
            cid,
            settings=load_settings(),
            handlers=stage_handlers(trace),
            permit=permit,
            owner_id="restart",
            resume_after_restart=True,
        )
        assert result["reason"] == "pending_interrupted_stage_unknown" and trace == [
            "plan",
            "admit",
            "materialize",
        ]
        r = await s.get(m.WorkflowRunModel, cid)
        assert set(r.metadata_json["stage_outputs"]) == {"plan", "admit"}


@pytest.mark.parametrize("change", ["authored", "revoked", "model", "scope", "body"])
async def test_actual_source_and_authority_change_stop_before_handler(db, change):
    async with db() as s:
        p = await seed(s, True)
        r = await pc.record_intent(s, load_settings(), project=p, after_chapter=4)
        await s.commit()
        key = {"model": "llm_model_id", "scope": "execution_scope_id"}.get(change, change)
        if change == "body":
            d = await s.get(
                m.ChapterDraftVersionModel,
                UUID(p.metadata_json["test_references"]["4"]["draft_id"]),
            )
            d.content_md += "改稿"
        else:
            p.metadata_json = {**p.metadata_json, key: True if change == "revoked" else "changed"}
        await s.commit()
        trace = []
        result = await pc.execute(
            s,
            r.id,
            settings=load_settings(),
            handlers=stage_handlers(trace),
            permit=permit,
            owner_id="one",
        )
        assert result["state"] == "machine_blocked" and trace == []


async def test_two_owners_only_one_reaches_stage(db):
    entered = asyncio.Event()
    release = asyncio.Event()
    trace = []
    async with db() as s:
        p = await seed(s, True)
        r = await pc.record_intent(s, load_settings(), project=p, after_chapter=4)
        await s.commit()
        cid = r.id

    async def slow(session, p, row, outputs):
        trace.append("plan")
        entered.set()
        await release.wait()
        return {"ok": True}

    async def first():
        async with db() as s:
            return await pc.execute(
                s,
                cid,
                settings=load_settings(),
                handlers={**stage_handlers(trace), "plan": slow},
                permit=permit,
                owner_id="first",
            )

    task = asyncio.create_task(first())
    await entered.wait()
    async with db() as s:
        r = await pc.execute(
            s,
            cid,
            settings=load_settings(),
            handlers=stage_handlers(trace),
            permit=permit,
            owner_id="second",
        )
        assert r["state"] == "already_running"
    release.set()
    assert (await task)["state"] == "completed"
    assert trace.count("plan") == 1


class Queue:
    def __init__(self):
        self.jobs = {}
        self.results = set()
        self.calls = 0

    async def enqueue_job(self, name, **kw):
        self.calls += 1
        self.jobs.setdefault(kw["_job_id"], (name, kw))

    async def exists(self, *keys):
        return sum(
            (
                key.removeprefix("arq:job:") in self.jobs
                if key.startswith("arq:job:")
                else key.removeprefix("arq:result:") in self.results
                if key.startswith("arq:result:")
                else False
            )
            for key in keys
        )

    def finish(self, job):
        self.jobs.pop(job, None)
        self.results.add(job)


async def test_real_bind_grant_wakes_only_same_stage_request_and_zero_send_gate(db):
    async with db() as s:
        p = await seed(s, True)
        r = await pc.record_intent(s, load_settings(), project=p, after_chapter=4)
        await s.commit()
        cid = r.id
        queue = Queue()
        first = await pc.enqueue(s, queue, r)
        old_job = first["job_id"]
        req = llm.LLMCompletionRequest(
            logical_role="critic",
            project_id=p.id,
            workflow_run_id=uuid4(),
            system_prompt="边界",
            user_prompt="数据",
            fallback_response="{}",
            prompt_template="fixture",
            prompt_version="1",
            operation_id="exact-stable-operation",
        )
        role = LLMRoleSettings(
            model="openai/fixture",
            api_base="https://example.invalid/v1",
            temperature=0.2,
            max_tokens=128,
            timeout_seconds=10,
        )
        digest = llm._effective_input_hash(req, role)

        async def attempt(session, project, row, outputs):
            await pc.enforce_stage_request(session, req, role, digest)
            return {"ok": True}

        trace = []
        hs = {**stage_handlers(trace), "admit": attempt}
        result = await pc.execute(
            s, cid, settings=load_settings(), handlers=hs, permit=permit, owner_id="first"
        )
        assert result["state"] == "awaiting_authority"
        queue.finish(old_job)
        from bestseller.services.autonomous_dispatch import recover_pending_checkpoints

        await recover_pending_checkpoints(s, queue)
        assert queue.calls == 1 and r.status == "awaiting_authority"
        assert not await pc.wake(s, load_settings(), r)
        prepared = r.metadata_json["prepared_request"]
        b = await s.get(m.ExecutionBudgetModel, "scope-untouched")
        wid = str(req.workflow_run_id)
        binding = {
            "scope_id": "scope-untouched",
            "operation_key": prepared["operation_key"],
            "input_hash": digest,
            "model": role.model,
            "endpoint": role.api_base,
            "workflow_id": wid,
            "user_message_id": "fixture-explicit",
            "prior_workflow_id": wid,
            "prior_journal_sha256": "0" * 64,
            "template": req.prompt_template,
            "version_prefix": req.prompt_version,
            "authority_evidence": {"fixture": True},
        }
        gid = authorization_id(binding["user_message_id"], wid)
        b.metadata_json = bind_grant(b.metadata_json, grant_id=gid, binding=binding)
        await s.commit()
        assert "max_physical_sends" not in b.metadata_json["single_dispatch_grants"][gid]["binding"]
        await recover_pending_checkpoints(s, queue)
        assert queue.calls == 2 and len(queue.jobs) == 1
        new_job = next(iter(queue.jobs))
        assert new_job != old_job and old_job in queue.results
        await recover_pending_checkpoints(s, queue)
        assert queue.calls == 2
        assert (
            r.metadata_json["wake_generation"] == 1 and old_job in r.metadata_json["prior_job_ids"]
        )
        result = await pc.execute(
            s, cid, settings=load_settings(), handlers=hs, permit=permit, owner_id="second"
        )
        assert result["state"] == "completed"
        assert trace.count("plan") == 1
        assert (await s.get(m.ExecutionBudgetModel, "scope-untouched")).metadata_json[
            "single_dispatch_grants"
        ][gid]["status"] == "available"  # no real reserve/send


async def test_self_heal_excludes_only_authorized_pending_project(monkeypatch):
    from test_self_heal import _FakeProject, _FakeSession
    from bestseller.worker import self_heal
    from bestseller.services import execution_windows
    from bestseller.services.creation_configuration import configuration_metadata

    meta = configuration_metadata(
        {},
        {
            "configuration": {"book": {"total_chapters": 12, "chapter_target": 2000}},
            "execution_window": {
                "schema_version": "execution-window.v1",
                "purpose": "opening_window",
                "start": 1,
                "end": 4,
            },
        },
    )
    pending = _FakeProject(uuid4(), "pending-owner", metadata_json=meta, target_chapters=12)
    other = _FakeProject(
        uuid4(),
        "unrelated-normal",
        metadata_json=deepcopy(pending.metadata_json),
        target_chapters=12,
    )
    other.target_word_count = 24000

    async def mode(session, *, project_id, project=None):
        return project_id == pending.id

    async def no_halts(session):
        return set()

    async def opened(*a, **k):
        return None

    async def progress(session, project):
        return {"window_complete": False, "pending": [1, 2, 3, 4], "adopted": []}

    monkeypatch.setattr(dc, "draft_mode_enabled", mode)
    monkeypatch.setattr(self_heal, "halted_project_ids", no_halts)
    monkeypatch.setattr(self_heal, "assert_execution_scope_open", opened)
    monkeypatch.setattr(execution_windows, "window_progress", progress)
    monkeypatch.setattr(execution_windows, "assert_window_runnable", opened)
    result = await self_heal.find_stuck_projects(_FakeSession([pending, other], [], [], []))
    assert [(r.slug, r.reason, r.stuck_at_chapter) for r in result] == [
        ("unrelated-normal", "execution_window_incomplete", 1)
    ]


async def test_real_complete_text_guard_captures_before_claim_or_provider(db, monkeypatch):
    async with db() as s:
        p = await seed(s, True)
        r = await pc.record_intent(s, load_settings(), project=p, after_chapter=4)
        await s.commit()
        # Model override is independently tested; hold it out of this exact gateway boundary test.
        st = load_settings()
        st.llm.mock = True
        p.metadata_json = {**p.metadata_json, "llm_model_id": None}
        await s.commit()
        binding = {**r.metadata_json["binding"]}
        request = llm.LLMCompletionRequest(
            logical_role="critic",
            project_id=p.id,
            workflow_run_id=uuid4(),
            system_prompt="来源数据",
            user_prompt="完整正文",
            fallback_response="{}",
            prompt_template="fixture",
            prompt_version="1",
            operation_id="gateway-exact",
        )

        async def denied(*a, **k):
            raise AssertionError("Claim/reserve/provider forbidden")

        from bestseller.services import budget

        monkeypatch.setattr(budget.ExecutionLedger, "claim", denied)
        monkeypatch.setattr(budget.ExecutionLedger, "reserve", denied)
        monkeypatch.setattr(llm, "_call_litellm", denied)
        with llm.bind_execution_scope("scope-untouched"), pc.exact_stage_requests(binding, "admit"):
            with pytest.raises(pc.AwaitingAuthority) as exc:
                await llm.complete_text(s, st, request)
        assert exc.value.receipt["pre_send"] is True and exc.value.receipt["matching_grants"] == 0


async def test_normal_adapters_route_plan_admit_materialize_activate_writer_to_real_fifth_row(
    db, monkeypatch
):
    from types import SimpleNamespace
    from bestseller.services import (
        pending_manuscript_handlers as adapters,
        chapter_outline_readiness as ready,
        planning_continuity_context as context,
        prefix_source_proposal as proposal,
        workflows,
        creation_configuration as config,
        prefix_window_activation as activation,
        drafts,
    )

    trace = []
    async with db() as s:
        # Add the real artifact table to this isolated database.
        table = m.PlanningArtifactVersionModel.__table__.to_metadata(MetaData())
        for col in table.columns:
            if isinstance(col.type, JSONB):
                col.type = JSON()
                col.server_default = None
        for constraint in list(table.constraints):
            if isinstance(constraint, ForeignKeyConstraint):
                table.constraints.remove(constraint)
        async with s.bind.begin() as conn:
            await conn.run_sync(table.create)
        p = await seed(s, True)
        artifact = m.PlanningArtifactVersionModel(
            id=uuid4(),
            project_id=p.id,
            artifact_type="chapter_outline_batch",
            version_no=1,
            status="approved",
            schema_version="1",
            content={"chapters": [{"chapter_number": 5}]},
            created_by="fixture",
        )
        parent = m.WorkflowRunModel(
            id=uuid4(),
            project_id=p.id,
            workflow_type=ready.WORKFLOW_TYPE,
            status="completed",
            metadata_json={
                "phase": "completed",
                "source_payload": {"chapters": [{"chapter_number": i} for i in range(1, 7)]},
                "artifact_id": str(artifact.id),
            },
        )
        s.add_all([artifact, parent])
        await s.commit()

        async def continuity(*a, **k):
            trace.append("producer")
            return {"actual_selected_references": p.metadata_json["test_references"]}

        async def verify(*a, **k):
            return {"verified": True}

        async def register(*a, **k):
            trace.append("source_proposal")
            return {"approved_scope": [5]}

        async def admit(*a, **k):
            trace.append("admit")
            return {"draft_eligible": True, "artifact_id": str(artifact.id), "chapter_numbers": [5]}

        async def materialize(session, slug, batch, **kw):
            trace.append("materialize")
            assert [v.chapter_number for v in batch.chapters] == [5] and kw[
                "prune_missing_planned"
            ] is False
            session.add(
                m.ChapterModel(
                    id=uuid4(),
                    project_id=p.id,
                    volume_id=uuid4(),
                    chapter_number=5,
                    title="第五工作章",
                    chapter_goal="推进",
                    status="planned",
                    metadata_json={"outline_prefix_readiness": {"artifact_id": str(artifact.id)}},
                )
            )
            await session.flush()
            return SimpleNamespace(model_dump=lambda **k: {"chapter_numbers": [5]})

        def view(project):
            return {
                "configuration": {},
                "configuration_hash": "configuration-before",
                "execution_window_hash": "window-before",
            }

        async def configure(session, project, value, **kw):
            trace.append("configure")
            assert kw == {
                "expected_hash": "configuration-before",
                "expected_window_hash": "window-before",
            }
            return {"status": "saved"}

        async def activate(*a, **k):
            trace.append("activate")
            return {"exact_prefix": True}

        async def writer(session, slug, number, **kw):
            trace.append("writer")
            assert number == 5
            chapter = await session.scalar(
                select(m.ChapterModel).where(
                    m.ChapterModel.project_id == p.id, m.ChapterModel.chapter_number == 5
                )
            )
            d = m.ChapterDraftVersionModel(
                id=uuid4(),
                project_id=p.id,
                chapter_id=chapter.id,
                version_no=1,
                content_md="独立合成第五章；不是生产小说验收。",
                word_count=18,
                is_current=True,
                promotion_state="candidate",
                assembled_from_scene_draft_ids=[],
            )
            session.add(d)
            await session.flush()
            return d

        monkeypatch.setattr(context, "build_authorized_planning_context", continuity)
        monkeypatch.setattr(ready, "verify_outline_prefix_readiness", verify)
        monkeypatch.setattr(proposal, "register_prefix_source_proposal", register)
        monkeypatch.setattr(ready, "approve_chapter_outline_prefix", admit)
        monkeypatch.setattr(workflows, "materialize_chapter_outline_batch", materialize)
        monkeypatch.setattr(config, "configuration_view", view)
        monkeypatch.setattr(config, "save_project_configuration", configure)
        monkeypatch.setattr(activation, "activate_verified_prefix_window", activate)
        monkeypatch.setattr(activation, "verify_activated_prefix_window", verify)
        monkeypatch.setattr(drafts, "generate_chapter_draft_once", writer)
        row = await pc.record_intent(s, load_settings(), project=p, after_chapter=4)
        await s.commit()
        cid = row.id
        result = await pc.execute(
            s,
            cid,
            settings=load_settings(),
            handlers=adapters.handlers(load_settings()),
            permit=permit,
            owner_id="single-normal-owner",
        )
        assert result["state"] == "completed" and result["draft"]["safety_qualified"] is False
        assert trace == [
            "producer",
            "source_proposal",
            "admit",
            "materialize",
            "configure",
            "activate",
            "writer",
        ]
    async with db() as s:
        row = await s.get(m.WorkflowRunModel, cid)
        assert tuple(row.metadata_json["stage_outputs"]) == pc.STAGES
        d = await s.get(
            m.ChapterDraftVersionModel,
            UUID(row.metadata_json["execution_result"]["draft"]["draft_id"]),
        )
        assert d.promotion_state == "candidate"
        await pc.execute(
            s,
            cid,
            settings=load_settings(),
            handlers=adapters.handlers(load_settings()),
            permit=permit,
            owner_id="restart",
        )
        assert trace.count("writer") == 1


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    import socket

    def denied(*args, **kwargs):
        raise AssertionError("pending coordinator regression forbids all network connections")

    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(socket, "create_connection", denied)


async def test_unknown_original_operation_cannot_be_revived_by_new_grant(db):
    async with db() as s:
        p = await seed(s, True)
        row = await pc.record_intent(s, load_settings(), project=p, after_chapter=4)
        await s.commit()
        request = llm.LLMCompletionRequest(
            logical_role="critic",
            project_id=p.id,
            workflow_run_id=uuid4(),
            system_prompt="来源",
            user_prompt="正文",
            fallback_response="{}",
            operation_id="unknown-operation",
        )
        role = LLMRoleSettings(
            model="openai/fixture",
            api_base="https://example.invalid/v1",
            temperature=0.2,
            max_tokens=128,
            timeout_seconds=10,
        )
        digest = llm._effective_input_hash(request, role)
        from hashlib import sha256

        key = sha256(("scope-untouched\n" + request.operation_id).encode()).hexdigest()
        op = m.ExecutionOperationModel(
            key=key,
            scope_id="scope-untouched",
            input_hash=digest,
            status="unknown",
            owner="old-owner",
            lease_until=datetime.now(timezone.utc),
            result_json={"original_unknown": True},
        )
        s.add(op)
        await s.commit()
        with (
            llm.bind_execution_scope("scope-untouched"),
            pc.exact_stage_requests(row.metadata_json["binding"], "admit"),
        ):
            with pytest.raises(ValueError, match="pending_stage_operation_unknown"):
                await pc.enforce_stage_request(s, request, role, digest)
        await s.refresh(op)
        assert op.status == "unknown" and op.result_json == {"original_unknown": True}
