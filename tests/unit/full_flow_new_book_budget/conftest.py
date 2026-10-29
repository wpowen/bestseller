"""Main-run RED only: native SQLite ledger, no SDK/network/model execution."""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime, timedelta
import hashlib
import json
from uuid import uuid4

import pytest
from sqlalchemy import JSON, ForeignKeyConstraint, MetaData, func, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.schema import CreateTable

from bestseller.infra.db import models as m
from bestseller.services import budget
from bestseller.services import execution_authorization as authorization
from bestseller.settings import BudgetSettings

SELECTOR = "execution_budget_grant_id"
MODEL = "openai/MiniMax-M3.1-Flash"
ENDPOINT = "https://api.minimaxi.com/v1"
CALLS = 400
TOKENS = 20_000_000
KEY = "new_book_budget_grant"


def payload_digest(payload):
    body = {key: value for key, value in payload.items() if key != SELECTOR}
    encoded = json.dumps(
        body, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def diagnostic_summary(value):
    encoded = json.dumps(value, sort_keys=True, default=str).encode()
    return {
        "type": type(value).__name__,
        "length": len(value) if isinstance(value, (str, list, dict, bytes)) else None,
        "sha256": hashlib.sha256(encoded).hexdigest(),
    }


def utc(value):
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


@pytest.fixture
def api():
    for name in ("prepare_new_book_budget", "consume_new_book_budget"):
        assert callable(getattr(authorization, name, None)), f"new-book budget RED: missing {name}"
    return authorization


@pytest.fixture
def clock(monkeypatch):
    class Clock(datetime):
        instant = datetime(2026, 10, 29, 1, 0, tzinfo=UTC)

        @classmethod
        def now(cls, tz=None):
            value = cls.instant
            return value.replace(tzinfo=None) if tz is None else value.astimezone(tz)

        @classmethod
        def advance(cls, **delta):
            cls.instant += timedelta(**delta)

    monkeypatch.setattr(authorization, "datetime", Clock)
    monkeypatch.setattr(budget, "datetime", Clock)
    return Clock


class NewBookCase:
    def __init__(self, engine, api):
        self.engine, self.api = engine, api
        self.sessions = async_sessionmaker(engine, expire_on_commit=False)
        self.authorization_id = str(uuid4())
        self.payload = {
            "client_schema_version": "quickstart-fixture-v1",
            "client_intent_manifest": {"schema_version": "quickstart-fixture-v1"},
            "creation_mode": "fanqie_short",
            "genre_key": "family",
            "selection": {"channel": "female", "genre": "family", "tags": ["reunion"]},
            "concept_seed": "她带着钥匙回家，决定说出实情。",
            "chapter_count": 4,
            "target_words": 8000,
            "llm_model_id": "minimax-m3.1-flash",
            "production_profile": "complete",
            "autonomous_book": True,
            "draft_mode": False,
            "stop_after_conception": False,
            "story_enhancers": {"effect_skills": ["emotional_choice"]},
        }

    async def prepare(self, **changes):
        args = {
            "authorization_id": self.authorization_id,
            "actor": "offline-test-operator",
            "authorization_ref": "offline-explicit-newbook-400-20M",
            "payload": deepcopy(self.payload),
            "model": MODEL,
            "endpoint": ENDPOINT,
        }
        args.update(changes)
        return await self.api.prepare_new_book_budget(self.engine, **args)

    def post(self, grant, payload=None):
        return {
            **deepcopy(self.payload if payload is None else payload), SELECTOR: grant["grant_id"],
        }

    async def consume(self, grant, *, payload=None, **route):
        return await self.api.consume_new_book_budget(
            self.engine, payload=self.post(grant, payload),
            model=route.get("model", MODEL), endpoint=route.get("endpoint", ENDPOINT),
        )

    async def active(self):
        grant = await self.prepare()
        assert await self.consume(grant) == grant
        return grant

    def ledger(self, grant, **limits):
        return budget.ExecutionLedger(self.engine, grant["scope_id"], BudgetSettings(**limits))

    async def complete(self, ledger, identity, *, tokens=10):
        digest = hashlib.sha256(identity.encode()).hexdigest()
        claim = await ledger.claim(identity, digest)
        attempt = await ledger.reserve(claim, MODEL, digest, tokens, endpoint=ENDPOINT)
        result = {"content": "offline retained result", "model_name": MODEL}
        await ledger.settle(attempt, "completed", actual_tokens=tokens)
        await ledger.finish(claim, result)
        return claim, attempt, result

    async def snapshot(self, scope):
        async with self.sessions() as session:
            row = await session.get(m.ExecutionBudgetModel, scope)
            return {
                "scope_id": row.scope_id, "status": row.status,
                "max_calls": row.max_calls, "max_tokens": row.max_tokens,
                "calls_used": row.calls_used, "tokens_charged": row.tokens_charged,
                "deadline_at": utc(row.deadline_at).isoformat(),
                "metadata": deepcopy(row.metadata_json),
            }

    async def count(self, model):
        async with self.sessions() as session:
            return await session.scalar(select(func.count()).select_from(model))


@pytest.fixture
async def case(api, clock, tmp_path):
    path = tmp_path / "new_book_budget_test.sqlite3"
    assert path.name.endswith("_test.sqlite3")
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    metadata = MetaData()
    for model in (
        m.ProjectModel, m.WorkflowRunModel, m.LlmRunModel,
        m.ExecutionBudgetModel, m.ExecutionOperationModel, m.ExecutionAttemptModel,
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
    try:
        yield NewBookCase(engine, api)
    finally:
        await engine.dispose()
