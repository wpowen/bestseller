"""Main-run RED fixtures: real native ledger rows, offline SDK/HTTP only.

This lane authors tests; main runs them. No synthetic operation/approval rows.
SQLite establishes persistence and gateway ordering, not PostgreSQL locking.
"""

from __future__ import annotations

from copy import deepcopy
import json
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import JSON, ForeignKeyConstraint, MetaData, event, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.schema import CreateTable

from bestseller.infra.db import models as m
from bestseller.services import budget, llm
from bestseller.services import chapter_automatic_revision_budget as revision
from bestseller.services.source_bound_planning_content import new_reader_first_binding
from bestseller.settings import BudgetSettings, load_settings


@pytest.fixture
def scope_api():
    for name in ("revision_source_scope", "set_revision_source", "current_revision_source"):
        assert callable(getattr(revision, name, None)), f"Q4 scope API RED: missing {name}"
    return revision


class RevisionGateway:
    def __init__(self, engine, path):
        self.engine = engine
        self.sessions = async_sessionmaker(engine, expire_on_commit=False)
        self.settings = load_settings(env={
            "BESTSELLER__DATABASE__URL": json.dumps(f"sqlite+aiosqlite:///{path}"),
            "BESTSELLER__LLM__MOCK": "false",
        })
        self.settings.artifact_store.local_dir = str(path.parent / "offline_artifacts")
        self.settings.artifact_store.retain_prompt_payloads = False
        for name in ("planner", "writer", "critic", "summarizer", "editor"):
            role = getattr(self.settings.llm, name).model_copy(update={
                "model": "openai/synthetic", "api_base": "https://revision.test.invalid/v1",
                "api_key_env": "Q4_OFFLINE_TEST_KEY", "model_override": None,
                "stream": False, "thinking_type": None, "reasoning_effort": None,
                "max_tokens": 128, "timeout_seconds": 30, "n_candidates": 1,
                "rate_limit_fallback_model": None,
            })
            setattr(self.settings.llm, name, role)
        self.settings.llm.retry = self.settings.llm.retry.model_copy(update={
            "max_attempts": 1, "rate_limit_max_attempts": 1,
            "wait_min_seconds": 0, "wait_max_seconds": 0,
            "rate_limit_fallback_enabled": False, "max_attempts_per_class": {},
        })
        self.settings.budget = BudgetSettings(
            execution_max_calls=20, execution_max_tokens=1_000_000,
            execution_deadline_seconds=3600,
        )
        self.project_id, self.chapter_id, self.source_id = uuid4(), uuid4(), uuid4()
        self.scope = f"project:{self.project_id}"
        self.body = "她把钥匙留在桌上，转身去接孩子。\n\n楼下的雨还没有停。"
        self.sdk_calls = []
        self.wires = []
        self.sql = []
        self.before_wire = None
        self.wire_failure = None

    async def seed(self):
        async with self.sessions.begin() as session:
            session.add(m.ProjectModel(
                id=self.project_id, slug=f"revision-send-{self.project_id.hex}",
                title="Ordinary native source", genre="family", language="zh-CN",
                target_word_count=6000, target_chapters=3,
                metadata_json={"reader_first_planning": new_reader_first_binding("重聚。")},
            ))
            session.add(m.BookProductionControlModel(
                project_id=self.project_id, desired_state="run", command_serial=0,
                resume_pending=False, detail_json={},
            ))
            session.add(m.ChapterModel(
                id=self.chapter_id, project_id=self.project_id, chapter_number=1,
                chapter_goal="作出选择", production_state="blocked",
                metadata_json={"unrelated": {"keep": [3, 1]},
                               "requires_machine_repair": {"reason": "unresolved"}},
            ))
            session.add(m.ChapterDraftVersionModel(
                id=self.source_id, project_id=self.project_id, chapter_id=self.chapter_id,
                version_no=1, content_md=self.body, word_count=25,
                is_current=False, promotion_state="candidate", promotion_metadata={},
            ))

    @property
    def selector(self):
        return {"chapter_id": str(self.chapter_id), "source_draft_id": str(self.source_id)}

    def request(self, *, template="chapter_rewrite", version="3.2-source-selected-voice-editor",
                role="editor", selector=True, operation=None, **changes):
        metadata = {"automatic_revision_source": self.selector} if selector else {}
        return llm.LLMCompletionRequest.model_validate({
            "logical_role": role, "system_prompt": "Return the complete revised passage.",
            "fallback_response": self.body,
            "user_prompt": self.body, "prompt_template": template, "prompt_version": version,
            "project_id": self.project_id, "operation_id": operation or uuid4().hex,
            "metadata": metadata, **changes,
        })

    async def workflow(self):
        identity = uuid4()
        async with self.sessions.begin() as session:
            session.add(m.WorkflowRunModel(
                id=identity, project_id=self.project_id, workflow_type="rewrite",
                status="running", metadata_json={},
            ))
        return identity

    async def call(self, request, *, rollback=False, scope=None):
        with budget.bind_execution_scope(scope or self.scope):
            async with self.sessions() as session:
                result = await llm.complete_text(session, self.settings, request)
                if rollback:
                    await session.rollback()
                else:
                    await session.commit()
                return result

    async def chapter_metadata(self):
        async with self.sessions() as session:
            return deepcopy((await session.get(m.ChapterModel, self.chapter_id)).metadata_json)

    async def journal(self):
        return (await self.chapter_metadata()).get(revision.JOURNAL_KEY)

    async def native(self):
        async with self.sessions() as session:
            parent = await session.get(m.ExecutionBudgetModel, self.scope)
            operations = list(await session.scalars(select(m.ExecutionOperationModel)))
            attempts = list(await session.scalars(select(m.ExecutionAttemptModel)))
            return parent, operations, attempts

    async def sdk(self, **kwargs):
        self.sdk_calls.append(kwargs["model"])
        call = llm._execution_call_var.get()
        assert call is not None and call.attempts, "real native reserve must precede SDK"
        if self.before_wire is not None:
            await self.before_wire(call)

        async def handle(request):
            assert request.url.host == "revision.test.invalid"
            async with self.sessions() as session:
                op = await session.get(m.ExecutionOperationModel, call.claim.key)
                parent = await session.get(m.ExecutionBudgetModel, call.ledger.scope_id)
                self.wires.append({
                    "key": op.key, "owner": op.owner, "claim_owner": call.claim.owner,
                    "scope": op.scope_id, "input_hash": op.input_hash,
                    "status": op.status, "calls_used": parent.calls_used,
                    "journal": await self.journal(),
                })
            if self.wire_failure is not None:
                raise self.wire_failure("offline response unavailable", request=request)
            return httpx.Response(200, json={"offline": True})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            await client.post("https://revision.test.invalid/completions", json={
                key: kwargs[key] for key in ("model", "messages", "max_tokens")
            })
        return SimpleNamespace(
            choices=[SimpleNamespace(
                message=SimpleNamespace(content=self.body, tool_calls=None), finish_reason="stop",
            )], usage=SimpleNamespace(prompt_tokens=7, completion_tokens=9),
        )


@pytest.fixture
async def gateway(tmp_path, monkeypatch):
    path = tmp_path / "q4_revision_send_test.sqlite3"
    assert path.name.endswith("_test.sqlite3")
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    metadata = MetaData()
    models = (
        m.ProjectModel, m.BookProductionControlModel, m.ChapterModel,
        m.ChapterDraftVersionModel, m.WorkflowRunModel, m.WorkflowStepRunModel,
        m.LlmRunModel, m.ExecutionBudgetModel, m.ExecutionOperationModel, m.ExecutionAttemptModel,
    )
    for model in models:
        table = model.__table__.to_metadata(metadata)
        for column in table.columns:
            if isinstance(column.type, JSONB):
                column.type, column.server_default = JSON(), None
        for constraint in list(table.constraints):
            if isinstance(constraint, ForeignKeyConstraint):
                table.constraints.remove(constraint)
    async with engine.begin() as connection:
        for table in metadata.tables.values():
            await connection.execute(CreateTable(table))
    instance = RevisionGateway(engine, path)
    await instance.seed()

    def observe(_connection, _cursor, statement, _parameters, _context, _many):
        instance.sql.append(statement.lower())  # SQL shape only; never parameter/source values.

    event.listen(engine.sync_engine, "before_cursor_execute", observe)
    monkeypatch.setenv("Q4_OFFLINE_TEST_KEY", "offline-fixture-not-a-credential")
    monkeypatch.setattr(llm, "_get_litellm", lambda: SimpleNamespace(acompletion=instance.sdk))
    monkeypatch.setattr(llm, "_ensure_shared_litellm_http_client", lambda: None)
    try:
        yield instance
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", observe)
        await engine.dispose()
