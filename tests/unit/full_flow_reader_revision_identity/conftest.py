"""Real SQLite, normal workflow producer and native LLM ledger; offline wire only.

No workflow, receipt, shared-budget or preservation verifier is replaced. The
observers delegate to the actual functions. Redis/HTTP adapters are in-memory;
this proves persisted identity/order, not Redis availability or PG locking.
"""

# ruff: noqa: RUF001

from copy import deepcopy
import json
from pathlib import Path
import socket
from types import SimpleNamespace
from uuid import UUID

from full_flow_emotional_preservation.conftest import NativeReaderCase
from full_flow_emotional_preservation.support import (
    SOURCE,
    SafeDifference,
    anchor_answer,
    equal,
    require,
    sha,
    summary,
    verify_answer,
)
import httpx
import pytest
from sqlalchemy import JSON, ForeignKeyConstraint, MetaData, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.schema import CreateTable
from test_web_pipeline_owner import Redis

from bestseller.infra.db import models as m
from bestseller.services import (
    budget,
    chapter_revision,
    drafts,
    litstyle_prose_judge,
    llm,
    reader_judge,
    workflows,
)
from bestseller.services import web_pipeline_owner as owner
from bestseller.services.litstyle_prose import load_litstyle_config
from bestseller.services.source_bound_planning_content import new_reader_first_binding
from support.database_safety import require_test_engine_url


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    if (
        Path(str(item.path)).parent != Path(__file__).parent
        or not report.failed
        or call.excinfo is None
    ):
        return
    error = call.excinfo.value
    differences = (
        error.differences
        if isinstance(error, SafeDifference)
        else [
            {
                "field": f"{item.name}.{call.when}",
                "expected": summary("successful test phase"),
                "actual": summary({"type": type(error).__name__, "message": str(error)}),
            }
        ]
    )
    report.longrepr = json.dumps(
        {"code": getattr(error, "code", type(error).__name__), "differences": differences},
        sort_keys=True,
    )
    report.sections = []


class ReaderRevisionCase(NativeReaderCase):
    def __init__(self, engine, path):
        super().__init__(engine, path)
        self.settings.output.base_dir = str(path.parent / "offline_output")
        self.slug = f"reader-identity-{self.project_id.hex}"
        self.operation = "issue"
        self.candidate_text = SOURCE.replace("第0只纸箱", "第一只纸箱")
        self.marked = True
        self.created = []
        self.creation_arguments = []
        self.boundaries = []
        self.volta = []
        self.active_request = None
        self.fail_template = None
        self.accept = True
        self.first_segment_oversize = False
        self.writer_text = None
        self.lost_anchors = ()
        self.before_wire = self.check_durable_binding

    async def seed(self):
        # Ordinary existing current draft; no score, approval or commercial proof.
        async with self.sessions.begin() as session:
            session.add(
                m.ProjectModel(
                    id=self.project_id,
                    slug=self.slug,
                    title="普通家庭故事",
                    genre="family",
                    language="zh-CN",
                    target_word_count=12000,
                    target_chapters=4,
                    metadata_json={
                        "reader_first_planning": new_reader_first_binding("姐妹共同修屋。"),
                        "execution_scope_id": self.scope,
                    },
                )
            )
            session.add(
                m.BookProductionControlModel(
                    project_id=self.project_id,
                    desired_state="run",
                    command_serial=0,
                    resume_pending=False,
                    detail_json={},
                )
            )
            session.add(
                m.ChapterModel(
                    id=self.chapter_id,
                    project_id=self.project_id,
                    chapter_number=1,
                    chapter_goal="以共同决定修复关系",
                    production_state="blocked",
                    metadata_json={
                        "unrelated": {"retain": [3, 1]},
                        "requires_machine_repair": {"reason": "unresolved"},
                    },
                )
            )
            session.add(
                m.ChapterDraftVersionModel(
                    id=self.source_id,
                    project_id=self.project_id,
                    chapter_id=self.chapter_id,
                    version_no=1,
                    content_md=self.body,
                    word_count=len(self.body),
                    is_current=True,
                    promotion_state="candidate",
                    promotion_metadata={},
                )
            )
        async with self.sessions.begin() as session:
            task = await chapter_revision.create_chapter_revision_task(
                session,
                self.slug,
                1,
                operation=self.operation,
                instructions="目标字数：1-30000字\n保持人物的共同决定。",
            )
            self.task_id = task.id
            task.metadata_json = {**task.metadata_json, "unrelated": {"retain": [9, 2]}}
            self.original_task_metadata = deepcopy(task.metadata_json)

    async def apply(self, *, rollback=False):
        with (
            budget.bind_execution_scope(self.scope),
            llm.bind_project_prompt_audit(
                self.project_id,
                reason="offline reader revision native identity fixture",
            ),
        ):
            async with (
                owner.web_pipeline_owner(self.settings, self.slug),
                self.sessions() as session,
            ):
                try:
                    result = await chapter_revision.apply_chapter_revision_task(
                        session,
                        self.settings,
                        self.slug,
                        1,
                        rewrite_task_id=self.task_id,
                        operation=self.operation,
                    )
                    if rollback:
                        await session.rollback()
                    else:
                        await session.commit()
                    return result
                except Exception:
                    await session.rollback()
                    raise

    async def check_durable_binding(self, call):
        if not self.marked:
            return
        request = self.active_request
        require("request.workflow_uuid", isinstance(request.workflow_run_id, UUID))
        async with self.sessions() as session:
            task = await session.get(m.RewriteTaskModel, self.task_id)
            workflow = await session.get(m.WorkflowRunModel, request.workflow_run_id)
            source = await session.get(m.ChapterDraftVersionModel, self.source_id)
            require("workflow.durable_before_sdk", workflow is not None)
            require("workflow.normal_creator", workflow.id in self.created)
            equal("workflow.project", self.project_id, workflow.project_id)
            equal("workflow.type", "reader_chapter_revision", workflow.workflow_type)
            equal("workflow.scope_type", "chapter", workflow.scope_type)
            equal("workflow.chapter", self.chapter_id, workflow.scope_id)
            equal("workflow.status_at_send", "running", workflow.status)
            equal(
                "workflow.idempotency",
                f"reader-chapter-revision:{self.task_id}",
                workflow.idempotency_key,
            )
            equal(
                "task.workflow",
                str(workflow.id),
                task.metadata_json.get("reader_revision_workflow_run_id"),
            )
            for key, value in self.original_task_metadata.items():
                equal("task.retained." + key, value, task.metadata_json.get(key))
            metadata = workflow.metadata_json
            for key, value in {
                "rewrite_task_id": str(self.task_id),
                "source_chapter_draft_id": str(self.source_id),
                "source_body_sha256": sha(self.body),
                "operation": self.operation,
                "execution_scope_id": self.scope,
            }.items():
                equal("workflow.binding." + key, value, metadata.get(key))
            equal("source.body", self.body, source.content_md)
            token = self.redis.store.get("bestseller:pipeline-live:" + self.slug)
            require("owner.acquired", bool(token))
            equal("owner.persisted", token, metadata.get("execution_owner"))
            equal(
                "owner.native",
                {
                    "schema": "acquired-pipeline-owner.v1",
                    "project_id": str(self.project_id),
                    "project_slug": self.slug,
                    "execution_scope_id": self.scope,
                    "token": token,
                    "lease_key": "bestseller:pipeline-live:" + self.slug,
                },
                metadata.get("native_pipeline_owner"),
            )
            self.boundaries.append(
                {
                    "workflow_id": workflow.id,
                    "task_id": task.id,
                    "template": request.prompt_template,
                }
            )

    def answer(self, request):
        template = request.prompt_template
        if template == "reader_revision_anchors":
            return anchor_answer(self.body)
        if template == "reader_revision_preservation":
            return verify_answer(
                anchor_answer(self.body)["anchors"], self.candidate_text, lost=self.lost_anchors
            )
        if template == "reader_chapter_issue_acceptance":
            packet = json.loads(request.user_prompt)
            return {
                "checks": [
                    {
                        "requirement": value,
                        "pass": self.accept,
                        "evidence": "" if not self.accept else packet["candidate"][-25:],
                        "reason": "Offline parser fixture; not a reader quality judgment.",
                    }
                    for value in packet["requirements"]
                ]
            }
        if template == "litstyle_prose_judge":
            return {
                **{dim.key: dim.max // 2 for dim in load_litstyle_config().dimensions},
                "ai_tone_penalty": 0,
                "evidence": ["林秋", "妹妹", "旧木板"],
                "top_issues": [],
                "revision_priority": ["保持人物动作。"],
            }
        if template == "reader_issue_segment_plan":
            packet = json.loads(request.user_prompt)
            return {
                "prior_facts": [],
                "editable_events": ["姐妹修屋"],
                "segments": [
                    {
                        "number": index + 1,
                        "budget": size,
                        "goal": "承担选择",
                        "entry": "开始修屋",
                        "exit": "继续修屋",
                        "requirement_indexes": list(range(len(packet["requirements"])))
                        if index == 0
                        else [],
                    }
                    for index, size in enumerate(packet["budgets"])
                ],
            }
        if template in {"reader_issue_segment_writer", "reader_issue_segment_compress"}:
            packet = json.loads(request.user_prompt)
            size = packet["current_segment"]["budget"]
            if template == "reader_issue_segment_writer" and self.first_segment_oversize:
                size *= 2
            phrase = "她把木板放在窗边妹妹接住了另一头"
            return (phrase * (size // len(phrase) + 1))[:size]
        if template in {"reader_chapter_issue_edit", "reader_chapter_polish"}:
            return self.writer_text if self.writer_text is not None else self.candidate_text
        if template == "content_validation":
            return self.candidate_text
        raise SafeDifference("unexpected.template", "declared reader revision template", template)

    async def workflow_rows(self):
        async with self.sessions() as session:
            return list(await session.scalars(select(m.WorkflowRunModel)))


@pytest.fixture
async def revision_case(tmp_path, monkeypatch):
    path = tmp_path / "reader_revision_identity_test.sqlite3"
    url = f"sqlite+aiosqlite:///{path}"
    require_test_engine_url(url)
    engine = create_async_engine(url)
    metadata = MetaData()
    for model in (
        m.ProjectModel,
        m.BookProductionControlModel,
        m.ChapterModel,
        m.ChapterDraftVersionModel,
        m.RewriteTaskModel,
        m.WorkflowRunModel,
        m.WorkflowStepRunModel,
        m.LlmRunModel,
        m.ExecutionBudgetModel,
        m.ExecutionOperationModel,
        m.ExecutionAttemptModel,
    ):
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
    case = ReaderRevisionCase(engine, path)
    case.redis = Redis()
    monkeypatch.setattr(owner.Redis, "from_url", lambda *args, **kwargs: case.redis)

    def no_socket(*args, **kwargs):
        raise AssertionError("native SQLite fixture forbids external network")

    monkeypatch.setattr(socket.socket, "connect", no_socket)
    monkeypatch.setattr(socket.socket, "connect_ex", no_socket)
    monkeypatch.setenv("READER_OFFLINE_TEST_KEY", "offline-fixture-not-a-credential")
    monkeypatch.setattr(llm, "_get_litellm", lambda: SimpleNamespace(acompletion=case.sdk))
    monkeypatch.setattr(llm, "_ensure_shared_litellm_http_client", lambda: None)
    native_complete = llm.complete_text
    native_creator = workflows.create_workflow_run

    async def observe_complete(session, settings, request):
        case.active_request = request.model_copy(deep=True)
        case.calls.append(case.active_request)
        case.answers.clear()
        case.answers.append(case.answer(request))
        case.wire_failure = (
            httpx.ReadTimeout if request.prompt_template == case.fail_template else None
        )
        return await native_complete(session, settings, request)

    async def observe_creator(session, **kwargs):
        result = await native_creator(session, **kwargs)
        case.created.append(result.id)
        case.creation_arguments.append(deepcopy(kwargs))
        return result

    for module in (chapter_revision, drafts, litstyle_prose_judge, reader_judge):
        monkeypatch.setattr(module, "complete_text", observe_complete)
    monkeypatch.setattr(workflows, "create_workflow_run", observe_creator)
    if hasattr(chapter_revision, "create_workflow_run"):
        monkeypatch.setattr(chapter_revision, "create_workflow_run", observe_creator)
    for name in ("freeze_reader_revision_anchors", "verify_reader_revision_preservation"):
        original = getattr(reader_judge, name)

        async def observe_volta(session, settings, _name=name, _native=original, **ids):
            async with case.sessions() as reader:
                row = await reader.get(m.WorkflowRunModel, ids["workflow_run_id"])
                require("volta.workflow_already_committed", row is not None)
                equal("volta.chapter", case.chapter_id, row.scope_id)
            result = await _native(session, settings, **ids)
            case.volta.append({"api": _name, "ids": deepcopy(ids), "receipt": deepcopy(result)})
            return result

        monkeypatch.setattr(reader_judge, name, observe_volta)
        if hasattr(chapter_revision, name):
            monkeypatch.setattr(chapter_revision, name, observe_volta)
    try:
        await case.seed()
        yield case
    finally:
        await engine.dispose()
