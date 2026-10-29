"""Actual SQLite rows and native ledger/gateway; only SDK/HTTP is offline.

Ordinary source candidates have no grade, promotion, current flag or approval.
Scripted critic responses test parsing/custody, never real semantic quality.
The authoring worker does not execute this fixture; main owns every test run.
"""

from collections import deque
from copy import deepcopy
import json
from pathlib import Path
import socket
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import JSON, ForeignKeyConstraint, MetaData, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.schema import CreateTable

from bestseller.domain.enums import WorkflowStatus
from bestseller.infra.db import models as m
from bestseller.services import budget, llm, reader_judge
from bestseller.services.source_bound_planning_content import new_reader_first_binding
from bestseller.services.workflows import create_workflow_run
from bestseller.settings import BudgetSettings, load_settings
from support.database_safety import require_test_engine_url

from .support import SOURCE, SafeDifference, apis, equal, require, sha, summary


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    """Bound diagnostics to this directory; never publish prose/SQL parameters."""
    outcome = yield
    report = outcome.get_result()
    if Path(str(item.path)).parent != Path(__file__).parent or not report.failed or call.excinfo is None:
        return
    error = call.excinfo.value
    differences = error.differences if isinstance(error, SafeDifference) else [{
        "field": f"{item.name}.{call.when}", "expected": summary("successful test phase"),
        "actual": summary({"exception_type": type(error).__name__, "message": str(error)}),
    }]
    detail = {"code": getattr(error, "code", type(error).__name__), "phase": call.when,
              "differences": differences}
    report.user_properties.append(("safe_reader_differences", json.dumps(detail, sort_keys=True)))
    report.longrepr = json.dumps(detail, ensure_ascii=False, sort_keys=True)
    report.sections = []


class NativeReaderCase:
    def __init__(self, engine, path):
        self.engine = engine
        self.sessions = async_sessionmaker(engine, expire_on_commit=False)
        self.settings = load_settings(env={
            "BESTSELLER__DATABASE__URL": json.dumps(f"sqlite+aiosqlite:///{path}"),
            "BESTSELLER__LLM__MOCK": "false",
        })
        self.settings.artifact_store.local_dir = str(path.parent / "offline_artifacts")
        self.settings.artifact_store.retain_prompt_payloads = True
        for name in ("planner", "writer", "critic", "summarizer", "editor"):
            role = getattr(self.settings.llm, name).model_copy(update={
                "model": "openai/synthetic", "api_base": "https://reader.test.invalid/v1",
                "api_key_env": "READER_OFFLINE_TEST_KEY", "model_override": None,
                "stream": False, "thinking_type": None, "reasoning_effort": None,
                "max_tokens": 4096, "timeout_seconds": 30, "n_candidates": 1,
                "rate_limit_fallback_model": None,
            })
            setattr(self.settings.llm, name, role)
        self.settings.llm.retry = self.settings.llm.retry.model_copy(update={
            "max_attempts": 1, "rate_limit_max_attempts": 1, "wait_min_seconds": 0,
            "wait_max_seconds": 0, "rate_limit_fallback_enabled": False,
            "max_attempts_per_class": {},
        })
        self.settings.budget = BudgetSettings(execution_max_calls=20,
            execution_max_tokens=1_000_000, execution_deadline_seconds=3600)
        self.project_id, self.chapter_id, self.source_id = uuid4(), uuid4(), uuid4()
        self.scope = f"project:{self.project_id}"
        self.body = SOURCE
        self.answers = deque()
        self.calls, self.wires = [], []
        self.wire_failure = None
        self.before_wire = None

    async def seed(self):
        async with self.sessions.begin() as session:
            session.add(m.ProjectModel(id=self.project_id, slug=f"reader-{self.project_id.hex}",
                title="普通家庭故事", genre="family", language="zh-CN", target_word_count=12000,
                target_chapters=4, metadata_json={
                    "reader_first_planning": new_reader_first_binding("姐妹共同修屋。"),
                    "execution_scope_id": self.scope,
                }))
            session.add(m.BookProductionControlModel(project_id=self.project_id,
                desired_state="run", command_serial=0, resume_pending=False, detail_json={}))
            session.add(m.ChapterModel(id=self.chapter_id, project_id=self.project_id,
                chapter_number=1, chapter_goal="以共同决定修复关系", production_state="blocked",
                metadata_json={"unrelated": {"retain": [3, 1]},
                    "requires_machine_repair": {"reason": "unresolved"}}))
            session.add(m.ChapterDraftVersionModel(id=self.source_id, project_id=self.project_id,
                chapter_id=self.chapter_id, version_no=1, content_md=self.body,
                word_count=len(self.body), is_current=False, promotion_state="candidate",
                promotion_metadata={}))
        self.workflow_id = await self.workflow()

    async def workflow(self, *, project_id=None, chapter_id=None):
        with budget.bind_execution_scope(self.scope):
            async with self.sessions.begin() as session:
                run = await create_workflow_run(session, project_id=project_id or self.project_id,
                    workflow_type="rewrite", status=WorkflowStatus.RUNNING,
                    scope_type="chapter", scope_id=chapter_id or self.chapter_id,
                    requested_by="offline component fixture")
                return run.id

    def selectors(self, **changes):
        return {"project_id": self.project_id, "chapter_id": self.chapter_id,
                "source_draft_id": self.source_id, "workflow_run_id": self.workflow_id, **changes}

    async def freeze(self, *, rollback=False, **changes):
        api = apis()
        with budget.bind_execution_scope(self.scope):
            async with self.sessions() as session:
                receipt = await api.freeze_reader_revision_anchors(
                    session, self.settings, **self.selectors(**changes))
                if rollback:
                    await session.rollback()
                else:
                    await session.commit()
                return receipt

    async def verify(self, candidate_text, candidate_llm_run_id, **changes):
        api = apis()
        with budget.bind_execution_scope(self.scope):
            async with self.sessions() as session:
                receipt = await api.verify_reader_revision_preservation(session, self.settings,
                    candidate_text=candidate_text, candidate_llm_run_id=candidate_llm_run_id,
                    **self.selectors(**changes))
                await session.commit()
                return receipt

    async def candidate(self, text, *, project_id=None, workflow_id=None):
        """Native known-body request, including the native shared-round reserve."""
        self.answers.append(text)
        pid = project_id or self.project_id
        request = llm.LLMCompletionRequest(logical_role="editor", system_prompt="Return the full revised chapter.",
            user_prompt=self.body, fallback_response="unavailable", prompt_template="chapter_rewrite",
            prompt_version="3.2-source-selected-voice-editor", project_id=pid,
            workflow_run_id=workflow_id or self.workflow_id, operation_id=f"fixture-candidate:{uuid4()}",
            metadata={"automatic_revision_source": {
                "chapter_id": str(self.chapter_id), "source_draft_id": str(self.source_id)}})
        with budget.bind_execution_scope(self.scope), llm.bind_project_prompt_audit(pid,
                reason="offline component exact candidate custody"):
            async with self.sessions() as session:
                result = await llm.complete_text(session, self.settings, request)
                await session.commit()
                return result

    async def source(self, body, version_no):
        async with self.sessions.begin() as session:
            draft = m.ChapterDraftVersionModel(project_id=self.project_id, chapter_id=self.chapter_id,
                version_no=version_no, content_md=body, word_count=len(body), is_current=False,
                promotion_state="candidate", promotion_metadata={})
            session.add(draft)
            await session.flush()
            return draft.id

    async def chapter_metadata(self):
        async with self.sessions() as session:
            return deepcopy((await session.get(m.ChapterModel, self.chapter_id)).metadata_json)

    async def ledger(self):
        async with self.sessions() as session:
            parent = await session.get(m.ExecutionBudgetModel, self.scope)
            operations = list(await session.scalars(select(m.ExecutionOperationModel)))
            attempts = list(await session.scalars(select(m.ExecutionAttemptModel)))
            return {"calls": parent.calls_used if parent else 0,
                    "tokens": parent.tokens_charged if parent else 0,
                    "status": parent.status if parent else None,
                    "operations": {row.key: (row.status, row.input_hash, row.owner,
                        deepcopy(row.result_json)) for row in operations},
                    "attempts": {row.id: (row.status, row.operation_key, row.input_hash,
                        row.reserved_tokens, row.actual_tokens) for row in attempts}}

    async def protected(self):
        async with self.sessions() as session:
            project = await session.get(m.ProjectModel, self.project_id)
            chapter = await session.get(m.ChapterModel, self.chapter_id)
            drafts = list(await session.scalars(select(m.ChapterDraftVersionModel)))
            return {"project": deepcopy(project.metadata_json), "state": chapter.production_state,
                    "unrelated": deepcopy(chapter.metadata_json["unrelated"]),
                    "repair": deepcopy(chapter.metadata_json["requires_machine_repair"]),
                    "drafts": {str(row.id): (row.content_md, row.version_no, row.is_current,
                        row.promotion_state, row.promotion_score, deepcopy(row.promotion_metadata)) for row in drafts}}

    async def physical(self, run_id):
        async with self.sessions() as session:
            run = await session.get(m.LlmRunModel, run_id)
            require("physical.run", run is not None)
            require("physical.response_ref", bool(run.response_payload_ref))
            root = Path(self.settings.artifact_store.local_dir)
            path = root / run.response_payload_ref
            return run, path, json.loads(path.read_text())

    def files(self):
        root = Path(self.settings.artifact_store.local_dir)
        return {str(p.relative_to(root)): sha(p.read_text()) for p in root.rglob("*.json")}

    async def sdk(self, **kwargs):
        call = llm._execution_call_var.get()
        require("native.claim_before_sdk", call is not None and bool(call.attempts))
        if self.before_wire is not None:
            await self.before_wire(call)

        async def handle(request):
            equal("wire.offline_host", "reader.test.invalid", request.url.host)
            async with self.sessions() as session:
                op = await session.get(m.ExecutionOperationModel, call.claim.key)
                self.wires.append({"key": op.key, "owner": op.owner,
                    "claim_owner": call.claim.owner, "status": op.status,
                    "scope": op.scope_id, "input_hash": op.input_hash,
                    "chapter": await self.chapter_metadata()})
            if self.wire_failure is not None:
                raise self.wire_failure("offline outcome unavailable", request=request)
            return httpx.Response(200, json={"offline": True})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            await client.post("https://reader.test.invalid/completions", json={
                key: kwargs[key] for key in ("model", "messages", "max_tokens")})
        require("offline.scripted_answer", bool(self.answers))
        value = self.answers.popleft()
        content = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
            content=content, tool_calls=None), finish_reason="stop")],
            usage=SimpleNamespace(prompt_tokens=37, completion_tokens=43))


@pytest.fixture
async def case(tmp_path, monkeypatch):
    path = tmp_path / "reader_revision_test.sqlite3"
    url = f"sqlite+aiosqlite:///{path}"
    require_test_engine_url(url)
    engine = create_async_engine(url)
    metadata = MetaData()
    for model in (m.ProjectModel, m.BookProductionControlModel, m.ChapterModel,
                  m.ChapterDraftVersionModel, m.WorkflowRunModel, m.WorkflowStepRunModel,
                  m.LlmRunModel, m.ExecutionBudgetModel, m.ExecutionOperationModel, m.ExecutionAttemptModel):
        table = model.__table__.to_metadata(metadata)
        for column in table.columns:
            if isinstance(column.type, JSONB):
                column.type, column.server_default = JSON(), None
        # This is the existing SQLite gateway fixture's DDL-only adapter.
        # Ownership is checked against actual ORM rows, not disabled guards.
        for constraint in list(table.constraints):
            if isinstance(constraint, ForeignKeyConstraint):
                table.constraints.remove(constraint)
    async with engine.begin() as connection:
        for table in metadata.tables.values():
            await connection.execute(CreateTable(table))
    instance = NativeReaderCase(engine, path)

    def no_socket(*args, **kwargs):
        raise AssertionError("offline fixture forbids network IO")

    native_complete = llm.complete_text

    async def observe_complete(session, settings, request):
        instance.calls.append(request.model_copy(deep=True))
        return await native_complete(session, settings, request)

    monkeypatch.setattr(socket.socket, "connect", no_socket)
    monkeypatch.setattr(socket.socket, "connect_ex", no_socket)
    monkeypatch.setenv("READER_OFFLINE_TEST_KEY", "offline-fixture-not-a-credential")
    monkeypatch.setattr(llm, "_get_litellm", lambda: SimpleNamespace(acompletion=instance.sdk))
    monkeypatch.setattr(llm, "_ensure_shared_litellm_http_client", lambda: None)
    monkeypatch.setattr(reader_judge, "complete_text", observe_complete)
    try:
        await instance.seed()
        yield instance
    finally:
        await engine.dispose()
