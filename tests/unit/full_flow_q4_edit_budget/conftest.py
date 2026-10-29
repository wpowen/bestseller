"""Q4 budget-only rows. No grade, promotion, current-source or send authority.

These fixtures are for mainselection to execute. This lane only authors them.
SQLite proves JSON persistence/rollback, never PostgreSQL row serialization.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import importlib
import importlib.util
import json
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm.attributes import flag_modified
from sqlalchemy.schema import CreateTable

from bestseller.infra.db.models import (
    BookProductionControlModel,
    ChapterDraftVersionModel,
    ChapterModel,
    ExecutionAttemptModel,
    ExecutionBudgetModel,
    ExecutionOperationModel,
    ProjectModel,
    WorkflowRunModel,
)
from bestseller.services.source_bound_planning_content import new_reader_first_binding
from bestseller.settings import load_settings


def sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@pytest.fixture
def api():
    """An absent implementation is an explicit RED, not a collection error."""
    name = "bestseller.services.chapter_automatic_revision_budget"
    assert importlib.util.find_spec(name) is not None, (
        "Q4 RED: shared automatic revision budget primitive is absent; "
        "mainselection must record this RED before implementation."
    )
    module = importlib.import_module(name)
    assert callable(getattr(module, "reserve_chapter_automatic_revision", None))
    assert issubclass(module.AutomaticRevisionBudgetError, ValueError)
    return module


@compiles(JSONB, "sqlite")
def _sqlite_jsonb(_type, _compiler, **_kwargs):
    return "JSON"


class BudgetRows:
    def __init__(self, engine, path):
        self.engine = engine
        self.sessions = async_sessionmaker(engine, expire_on_commit=False)
        self.settings = load_settings(env={
            "BESTSELLER__DATABASE__URL": json.dumps(f"sqlite+aiosqlite:///{path}"),
            "BESTSELLER__LLM__MOCK": "true",
        })
        self.project_id = uuid4()
        self.chapter_id = uuid4()
        self.source_id = uuid4()
        self.body = "她把钥匙留在桌上，转身去接孩子。\n\n楼下的雨还没有停。"
        self.scope = f"project:{self.project_id}"

    async def seed(self, *, marked=True, scope=None):
        self.scope = scope or self.scope
        metadata = {"unrelated_project": {"keep": [3, 1]}}
        if marked:
            metadata["reader_first_planning"] = new_reader_first_binding("一家人重新吃晚饭。")
        if scope:
            metadata["execution_scope_id"] = scope
        async with self.sessions.begin() as session:
            session.add(ProjectModel(
                id=self.project_id, slug=f"q4-budget-{self.project_id.hex[:12]}",
                title="Ordinary budget rows", genre="family", language="zh-CN",
                target_word_count=6000, target_chapters=3, metadata_json=metadata,
            ))
            session.add(BookProductionControlModel(
                project_id=self.project_id, desired_state="run", command_serial=0,
                resume_pending=False, detail_json={},
            ))
            session.add(ChapterModel(
                id=self.chapter_id, project_id=self.project_id, chapter_number=1,
                chapter_goal="作出选择", production_state="blocked",
                metadata_json={"unrelated_chapter": {"keep": [4, 2]},
                               "requires_machine_repair": {"reason": "fixture_unresolved"}},
            ))
            # Explicit ordinary candidate, not an adopted/current draft.
            session.add(ChapterDraftVersionModel(
                id=self.source_id, project_id=self.project_id, chapter_id=self.chapter_id,
                version_no=1, content_md=self.body, word_count=25, is_current=False,
                promotion_state="candidate", promotion_metadata={},
            ))
            session.add(ExecutionBudgetModel(
                scope_id=self.scope, max_calls=10, max_tokens=10000,
                calls_used=4, tokens_charged=900,
                deadline_at=datetime.now(timezone.utc) + timedelta(hours=1),
                status="active", metadata_json={"keep": {"existing": [1, 2]}},
            ))

    async def operation(self, label, *, scope=None, status="pending"):
        scope = scope or self.scope
        key = sha(f"{scope}\nq4-budget-fixture:{label}")
        async with self.sessions.begin() as session:
            session.add(ExecutionOperationModel(
                key=key, scope_id=scope, input_hash=sha(f"input:{label}"),
                status=status, owner="ordinary-test-row",
                lease_until=datetime.now(timezone.utc) + timedelta(minutes=5),
                result_json=None,
            ))
        return key

    async def source(self, body, *, project_id=None, chapter_id=None, version=2):
        draft_id = uuid4()
        async with self.sessions.begin() as session:
            session.add(ChapterDraftVersionModel(
                id=draft_id, project_id=project_id or self.project_id,
                chapter_id=chapter_id or self.chapter_id, version_no=version,
                content_md=body, word_count=25, is_current=False,
                promotion_state="candidate", promotion_metadata={},
            ))
        return draft_id

    def args(self, native_operation_key, /, **overrides):
        return {"session_factory": self.sessions, "settings": self.settings,
                "project_id": self.project_id, "chapter_id": self.chapter_id,
                "source_draft_id": self.source_id, "operation_key": native_operation_key,
                **overrides}

    async def metadata(self):
        async with self.sessions() as session:
            chapter = await session.get(ChapterModel, self.chapter_id)
            return deepcopy(chapter.metadata_json)

    async def set_metadata(self, metadata):
        async with self.sessions.begin() as session:
            chapter = await session.get(ChapterModel, self.chapter_id)
            chapter.metadata_json = deepcopy(metadata)
            # SQLAlchemy's JSON equality can consider True equal to 1. This
            # fixture deliberately persists corrupt types, so force the write.
            flag_modified(chapter, "metadata_json")

    async def financial_rows(self):
        """Read every mapped column; no ledger APIs or financial mutations."""
        result = {}
        async with self.sessions() as session:
            for model in (ExecutionBudgetModel, ExecutionOperationModel, ExecutionAttemptModel):
                rows = (await session.execute(select(model.__table__))).mappings().all()
                result[model.__tablename__] = sorted(
                    (deepcopy(dict(row)) for row in rows), key=repr,
                )
        return result


@pytest.fixture
async def rows(tmp_path):
    path = tmp_path / "q4_edit_budget_test.sqlite3"
    assert path.name.endswith("_test.sqlite3")
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    async with engine.begin() as connection:
        # Match existing SQLite tests: table DDL only, no PostgreSQL indexes or
        # migrations. ORM rows remain real; FK/concurrency proof is separate.
        for model in (
            ProjectModel, BookProductionControlModel, ChapterModel,
            ChapterDraftVersionModel, WorkflowRunModel, ExecutionBudgetModel,
            ExecutionOperationModel, ExecutionAttemptModel,
        ):
            ddl = str(CreateTable(model.__table__).compile(dialect=connection.dialect))
            await connection.exec_driver_sql(ddl.replace("::jsonb", ""))
    try:
        yield BudgetRows(engine, path)
    finally:
        await engine.dispose()
