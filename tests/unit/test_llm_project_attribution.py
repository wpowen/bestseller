"""Explicit attribution on isolated SQLite; no live gateway or database."""
from copy import deepcopy
from uuid import UUID, uuid4

import pytest
from sqlalchemy import JSON, MetaData, func, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.schema import CreateTable

from bestseller.infra.db.models import LlmRunModel, ProjectModel, WorkflowRunModel
from bestseller.services.llm_project_attribution import (
    llm_attribution_row_hash,
    reconcile_llm_project_attribution,
)

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@pytest.fixture
async def db(tmp_path):
    path = tmp_path / "llm_attribution_test.sqlite3"
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")

    def create(connection):
        for model in (ProjectModel, WorkflowRunModel, LlmRunModel):
            table = model.__table__.to_metadata(MetaData())
            for column in table.c:
                if isinstance(column.type, JSONB):
                    column.type = JSON()
                if column.server_default and "::jsonb" in str(column.server_default.arg):
                    column.server_default = None
            connection.execute(CreateTable(table, include_foreign_key_constraints=[]))

    async with engine.begin() as connection:
        await connection.run_sync(create)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    project_id, workflow_id = uuid4(), uuid4()
    scope = f"conception:{uuid4()}"
    async with factory() as session:
        session.add(ProjectModel(
            id=project_id, slug="attribution-test", title="Audit fixture", genre="custom",
            target_word_count=10000, target_chapters=3,
            metadata_json={"execution_scope_id": scope, "retain": "original"},
        ))
        session.add(WorkflowRunModel(
            id=workflow_id, project_id=project_id, workflow_type="chapter_review",
            status="completed", metadata_json={},
        ))
        rows = [LlmRunModel(
            project_id=project_id if i < 11 else None,
            workflow_run_id=None if i < 5 else workflow_id,
            logical_role="critic", provider="fixture", model_name="fixture",
            prompt_template="window" if i >= 11 else "older",
            input_tokens=100 + i, output_tokens=50 + i,
            request_payload_ref=f"original-request-{i}", response_payload_ref=f"original-reply-{i}",
            metadata_json={"origin": {"historical": i}, "execution_receipt": {"scope_id": scope}},
        ) for i in range(13)]
        session.add_all(rows)
        await session.commit()
        options = {
            "project_id": project_id, "scope_id": scope,
            "expected_row_hashes": {r.id: llm_attribution_row_hash(r) for r in rows[11:]},
            "actor": "fixture-operator", "evidence_ref": "saved-exact-workflow-fk.json",
            "idempotency_key": "operator-attribution-1",
        }
        hashes = {r.id: llm_attribution_row_hash(r) for r in rows}
    try:
        yield factory, options, hashes
    finally:
        await engine.dispose()


async def test_default_dry_run_changes_no_rows_or_receipt(db):
    factory, options, before = db
    async with factory() as session:
        result = await reconcile_llm_project_attribution(session, **options)
        assert result["status"] == "dry_run" and result["would_change"] == 2
        assert result["changed"] == 0 and not session.dirty
        assert all(r["before_row_sha256"] != r["after_row_sha256"] for r in result["rows"])
        await session.commit()
    async with factory() as session:
        assert {r.id: llm_attribution_row_hash(r) for r in await session.scalars(
            select(LlmRunModel))} == before
        project = await session.get(ProjectModel, options["project_id"])
        assert "llm_project_attribution_receipts" not in project.metadata_json


async def test_apply_reopen_repeat_preserves_columns_and_old_five(db):
    factory, options, before = db
    async with factory() as session:
        originals = {r.id: {a.key: deepcopy(getattr(r, a.key)) for a in r.__mapper__.column_attrs}
                     for r in await session.scalars(select(LlmRunModel))}
        count = select(func.count()).select_from(LlmRunModel).where(
            LlmRunModel.project_id == options["project_id"])
        assert await session.scalar(count) == 11
        first = await reconcile_llm_project_attribution(session, **options, apply=True)
        assert first["status"] == "applied" and first["changed"] == 2
        await session.commit()
    async with factory() as session:
        assert await session.scalar(count) == 13
        rows = list(await session.scalars(select(LlmRunModel)))
        after = {r.id: llm_attribution_row_hash(r) for r in rows}
        for row in rows:
            old = originals[row.id]
            if row.id not in options["expected_row_hashes"]:
                assert after[row.id] == before[row.id]
                continue
            for field, value in old.items():
                if field not in {"project_id", "metadata_json"}:
                    assert getattr(row, field) == value
            assert {k: v for k, v in row.metadata_json.items()
                    if k != "project_attribution_receipts"} == old["metadata_json"]
            note = row.metadata_json["project_attribution_receipts"][0]
            assert note["original_project_id"] is None
        second = await reconcile_llm_project_attribution(session, **options, apply=True)
        assert second["status"] == "already_bound" and second["changed"] == 0
        assert second["receipt"] == first["receipt"] and not session.dirty
        await session.commit()
    async with factory() as session:
        assert {r.id: llm_attribution_row_hash(r) for r in await session.scalars(
            select(LlmRunModel))} == after
        project = await session.get(ProjectModel, options["project_id"])
        history = project.metadata_json["llm_project_attribution_receipts"]
        assert len(history) == 1
        for row in history[0]["rows"]:
            assert row["before_row_sha256"] == before[UUID(row["row_id"])]
            assert row["after_row_sha256"] == after[UUID(row["row_id"])]


@pytest.mark.parametrize("case", [
    "stale_hash", "wrong_scope", "row_scope", "foreign_workflow", "null_workflow",
    "missing_workflow", "foreign_existing_project", "missing_row", "unbound_workflow",
])
async def test_invalid_batch_refuses_before_any_attribution(db, case):
    factory, options, _ = db
    options = deepcopy(options)
    async with factory() as session:
        row = await session.get(LlmRunModel, next(iter(options["expected_row_hashes"])))
        if case == "stale_hash":
            row.output_tokens += 1
        elif case == "wrong_scope":
            options["scope_id"] = f"project:{uuid4()}"
        elif case == "row_scope":
            row.metadata_json = {
                **row.metadata_json, "execution_receipt": {"scope_id": "foreign"},
            }
        elif case in {"foreign_workflow", "unbound_workflow"}:
            workflow = await session.get(WorkflowRunModel, row.workflow_run_id)
            workflow.project_id = uuid4() if case == "foreign_workflow" else None
        elif case == "null_workflow":
            row.workflow_run_id = None
        elif case == "missing_workflow":
            row.workflow_run_id = uuid4()
        elif case == "foreign_existing_project":
            row.project_id = uuid4()
        elif case == "missing_row":
            options["expected_row_hashes"][uuid4()] = "a" * 64
        await session.commit()
        before = {r.id: llm_attribution_row_hash(r) for r in await session.scalars(
            select(LlmRunModel))}
        with pytest.raises(ValueError, match="attribution_"):
            await reconcile_llm_project_attribution(session, **options, apply=True)
        assert not session.dirty
        assert {r.id: llm_attribution_row_hash(r) for r in await session.scalars(
            select(LlmRunModel))} == before


async def test_apply_rollback_preserves_all_rows(db):
    factory, options, before = db
    async with factory() as session:
        await reconcile_llm_project_attribution(session, **options, apply=True)
        await session.rollback()
    async with factory() as session:
        assert {r.id: llm_attribution_row_hash(r) for r in await session.scalars(
            select(LlmRunModel))} == before


async def test_operator_key_cannot_change_evidence(db):
    factory, options, _ = db
    async with factory() as session:
        await reconcile_llm_project_attribution(session, **options, apply=True)
        await session.commit()
    async with factory() as session:
        with pytest.raises(ValueError, match="attribution_idempotency_conflict"):
            await reconcile_llm_project_attribution(
                session, **{**options, "evidence_ref": "different-evidence"}, apply=True,
            )
        assert not session.dirty


async def test_already_bound_without_receipt_is_never_rewritten(db):
    factory, options, _ = db
    async with factory() as session:
        row = await session.scalar(select(LlmRunModel).where(
            LlmRunModel.project_id == options["project_id"],
            LlmRunModel.workflow_run_id.is_not(None),
        ))
        original = llm_attribution_row_hash(row)
        options = {**options, "expected_row_hashes": {row.id: original}}
        result = await reconcile_llm_project_attribution(session, **options, apply=True)
        assert result["status"] == "already_bound" and not session.dirty
        assert llm_attribution_row_hash(row) == original


async def test_selected_old_workflow_null_is_refused_even_when_project_is_bound(db):
    factory, options, before = db
    async with factory() as session:
        row = await session.scalar(select(LlmRunModel).where(
            LlmRunModel.workflow_run_id.is_(None),
        ))
        options = {**options, "expected_row_hashes": {row.id: llm_attribution_row_hash(row)}}
        with pytest.raises(ValueError, match="attribution_workflow_required"):
            await reconcile_llm_project_attribution(session, **options, apply=True)
        assert not session.dirty
        assert {r.id: llm_attribution_row_hash(r) for r in await session.scalars(
            select(LlmRunModel))} == before


async def test_mixed_already_bound_and_null_batch_is_idempotent(db):
    factory, options, _ = db
    async with factory() as session:
        existing = await session.scalar(select(LlmRunModel).where(
            LlmRunModel.project_id == options["project_id"],
            LlmRunModel.workflow_run_id.is_not(None),
        ))
        original = llm_attribution_row_hash(existing)
        options = {**options, "expected_row_hashes": {
            **options["expected_row_hashes"], existing.id: original,
        }}
        first = await reconcile_llm_project_attribution(session, **options, apply=True)
        assert first["changed"] == 2
        await session.commit()
    async with factory() as session:
        repeat = await reconcile_llm_project_attribution(session, **options, apply=True)
        assert repeat["status"] == "already_bound" and not session.dirty
        assert repeat["receipt"] == first["receipt"]
        assert llm_attribution_row_hash(await session.get(LlmRunModel, existing.id)) == original
