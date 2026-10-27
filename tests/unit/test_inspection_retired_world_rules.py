"""Current Bible reads distinguish proven withdrawal from malformed prose.

Real isolated SQLite, exact-source continuity blocks and the actual projection
planner/reprojector; only seed-time LLM transport is deterministic and local.
"""

import asyncio
from copy import deepcopy
import hashlib
import socket
from uuid import uuid4

from pydantic import ValidationError
import pytest
from sqlalchemy import event, inspect, select
from sqlalchemy.schema import CreateTable
from test_adopted_bible_lineage import db as db
from test_adopted_bible_lineage import knowledge_db as knowledge_db
from test_adopted_bible_lineage import stub_llm, update
from test_knowledge_downstream_source_dependencies import _next_chapter, _replace_source

from bestseller.infra.db.models import (
    DeferredRevealModel,
    ExpansionGateModel,
    FactionModel,
    LocationModel,
    VolumeFrontierModel,
    WorldBackboneModel,
    WorldRuleModel,
)
from bestseller.services import inspection, llm
from bestseller.services import story_bible_projection as projection
from bestseller.services.adopted_chapter_source import select_adopted_chapter_source

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setenv("BESTSELLER_LINEAGE_PG", "0")

    def forbidden(*_args, **_kwargs):
        raise AssertionError("Inspection tests forbid network/provider access")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(llm, "complete_text", forbidden)
    monkeypatch.setattr(llm, "_call_litellm", forbidden)


def _payload(description="Only the brass key opens this door."):
    return {
        "world_updates": [
            {
                "rule_or_location": "brass key rule",
                "description": description,
            }
        ]
    }


async def _apply(book, monkeypatch, description="Only the brass key opens this door."):
    with monkeypatch.context() as seed_patch:
        calls = stub_llm(seed_patch, _payload(description))
        result = await update(book)
    # Updating an existing world rule can be classified empty because the
    # updater counts additions, while still retaining/replaying this payload.
    assert result["extraction_status"] in {"ok", "empty"}, result
    assert len(calls) == 1
    assert (
        book[2].metadata_json["story_bible_update_receipts"][-1]["candidate_payload"][
            "world_updates"
        ]
        == _payload(description)["world_updates"]
    )
    await book[0].commit()


async def _withdraw(book):
    session, project, chapter, draft = book
    block = {
        "draft_id": str(draft.id),
        "body_sha256": hashlib.sha256(draft.content_md.encode()).hexdigest(),
        "passed": False,
    }
    chapter.metadata_json = {
        **chapter.metadata_json,
        "continuity_admission_by_source": {
            f"{draft.id}:{block['body_sha256']}": block,
        },
    }
    await session.commit()
    assert await select_adopted_chapter_source(session, chapter=chapter, lock=False) == (None, None)
    async with session.begin_nested():
        assert await projection.reproject_adopted_bible(session, project)
    await session.commit()


@pytest.fixture
async def seeded(db, monkeypatch):
    assert db[0].bind.dialect.name == "sqlite"
    await _apply(db, monkeypatch)
    rule = await db[0].scalar(select(WorldRuleModel).where(WorldRuleModel.project_id == db[1].id))
    assert rule.metadata_json[projection.BASE]["created"] is True
    return db, rule


@pytest.fixture
async def retired(seeded):
    book, rule = seeded
    retained = deepcopy(rule.metadata_json["story_bible_update_history"])
    await _withdraw(book)
    assert rule.description == ""
    assert rule.metadata_json["story_bible_update_history"] == retained
    changes, legacy, active, _ = await projection._plan(book[0], book[1].id)
    assert not legacy and not active
    assert any(
        row.id == rule.id and desired == projection._fields(rule) for row, desired in changes
    )
    return seeded


async def _overview_tables(session):
    connection = await session.connection()
    assert connection.dialect.name == "sqlite"
    for model in (
        LocationModel,
        FactionModel,
        WorldBackboneModel,
        VolumeFrontierModel,
        DeferredRevealModel,
        ExpansionGateModel,
    ):
        ddl = str(
            CreateTable(model.__table__, if_not_exists=True).compile(dialect=connection.dialect)
        )
        await connection.exec_driver_sql(ddl.replace("::jsonb", ""))
    await session.commit()


def _row_state(row):
    return deepcopy({attr.key: getattr(row, attr.key) for attr in inspect(type(row)).column_attrs})


async def test_no_creation_candidates_returns_input_without_planning(db, monkeypatch):
    async def forbidden(*_args, **_kwargs):
        raise AssertionError("No created=True candidates must not invoke the planner")

    monkeypatch.setattr(projection, "_plan", forbidden)
    authored = WorldRuleModel(
        id=uuid4(),
        project_id=db[1].id,
        rule_code="authored",
        name="Authored",
        description="",
        metadata_json={},
    )
    noncreated = WorldRuleModel(
        id=uuid4(),
        project_id=db[1].id,
        rule_code="base",
        name="Authored baseline",
        description="Original rule",
        metadata_json={projection.BASE: {"created": False}},
    )
    for rows in ([], [authored], [noncreated]):
        assert await inspection._world_rules_for_current_read(db[0], db[1].id, rows) is rows


async def test_current_created_rule_remains_visible(seeded):
    book, rule = seeded
    assert await inspection._world_rules_for_current_read(book[0], book[1].id, [rule]) == [rule]
    await _overview_tables(book[0])
    overview = await inspection.build_story_bible_overview(book[0], book[1].slug)
    assert [row.description for row in overview.world_rules] == [rule.description]
    rule.description = ""
    await book[0].commit()
    # Active-but-invalid materialized prose cannot turn into a hidden rule.
    assert await inspection._world_rules_for_current_read(book[0], book[1].id, [rule]) == [rule]
    with pytest.raises(ValidationError):
        await inspection.build_story_bible_overview(book[0], book[1].slug)


async def test_retired_helper_is_batched_and_read_only(retired, monkeypatch):
    book, rule = retired
    session, project, chapter, draft = book
    rows = [rule]
    before = [_row_state(row) for row in (project, chapter, draft, rule)]
    statements, plans = [], []
    real_plan = projection._plan

    async def counted(*args, **kwargs):
        plans.append(1)
        return await real_plan(*args, **kwargs)

    async def forbidden(*_args, **_kwargs):
        raise AssertionError("A current read must not flush, commit or reproject")

    def record(_conn, _cursor, statement, _parameters, _context, _executemany):
        statements.append(statement)

    monkeypatch.setattr(projection, "_plan", counted)
    monkeypatch.setattr(projection, "reproject_adopted_bible", forbidden)
    with monkeypatch.context() as read_patch:
        read_patch.setattr(session, "flush", forbidden)
        read_patch.setattr(session, "commit", forbidden)
        event.listen(session.bind.engine.sync_engine, "before_cursor_execute", record)
        try:
            assert await inspection._world_rules_for_current_read(session, project.id, rows) == []
        finally:
            event.remove(session.bind.engine.sync_engine, "before_cursor_execute", record)
    assert plans == [1]
    assert statements and all(
        statement.lstrip().upper().startswith("SELECT") for statement in statements
    )
    assert [_row_state(row) for row in (project, chapter, draft, rule)] == before
    assert rows == [rule] and await session.get(WorldRuleModel, rule.id) is rule


async def test_overview_excludes_only_proven_retired_rule(retired):
    book, rule = retired
    session, project, *_ = book
    authored = WorldRuleModel(
        project_id=project.id,
        rule_code="authored",
        name="Authored rule",
        description="An independent authored constraint.",
        metadata_json={},
    )
    session.add(authored)
    await session.commit()
    await _overview_tables(session)
    before = [_row_state(row) for row in (*book[1:], rule, authored)]
    overview = await inspection.build_story_bible_overview(session, project.slug)
    assert [row.rule_code for row in overview.world_rules] == [authored.rule_code]
    assert overview.model_dump(mode="json")["world_rules"][0]["description"] == authored.description
    assert [_row_state(row) for row in (*book[1:], rule, authored)] == before
    assert await session.get(WorldRuleModel, rule.id) is rule


@pytest.mark.parametrize("kind", ["authored", "unproven_created_flag", "empty_creation_history"])
async def test_unproven_blank_overview_still_raises_validation_error(db, kind):
    session, project, *_ = db
    rule = WorldRuleModel(
        project_id=project.id,
        rule_code="blank",
        name="Invalid blank",
        description="",
        metadata_json={}
        if kind != "unproven_created_flag"
        else {
            projection.BASE: {"created": 1},
        },
    )
    if kind == "empty_creation_history":
        rule.id = uuid4()
        projection.capture_bible_baseline(rule, created=True)
    session.add(rule)
    await session.commit()
    await _overview_tables(session)
    with pytest.raises(ValidationError) as caught:
        await inspection.build_story_bible_overview(session, project.slug)
    assert any(
        error["loc"] == ("description",) and error["type"] == "string_too_short"
        for error in caught.value.errors()
    )
    assert rule.description == ""


@pytest.mark.parametrize(
    "defect", ["owner", "raw_source", "payload", "stale_fields", "history_shape"]
)
async def test_malformed_or_stale_retirement_fails_closed(retired, defect):
    book, rule = retired
    metadata = deepcopy(rule.metadata_json)
    if defect == "owner":
        metadata[projection.BASE]["project_id"] = str(uuid4())
    elif defect == "raw_source":
        metadata["story_bible_update_history"][0]["source_lineage"]["source_body_sha256"] = "0" * 64
    elif defect == "payload":
        metadata["story_bible_update_history"][0]["update"]["description"] = (
            "Not in the retained receipt"
        )
    elif defect == "stale_fields":
        rule.description = "Withdrawn stale prose must not be silently hidden."
    else:
        metadata["story_bible_update_history"] = [None]
    rule.metadata_json = metadata
    await book[0].commit()
    await book[0].refresh(rule)
    before = _row_state(rule)
    with pytest.raises(projection.BibleProjectionAttributionError):
        await inspection._world_rules_for_current_read(book[0], book[1].id, [rule])
    assert _row_state(rule) == before


async def test_later_active_shorter_contribution_survives_creator_withdrawal(seeded, monkeypatch):
    book, rule = seeded
    later = await _next_chapter(book, "A later observer confirms the key's remaining constraint.")
    await _apply(later, monkeypatch, "Brass only.")
    assert rule.description == "Only the brass key opens this door."
    await _withdraw(book)
    assert rule.description == "Brass only."
    assert await inspection._world_rules_for_current_read(book[0], book[1].id, [rule]) == [rule]


async def test_fresh_adoption_reactivates_same_retained_identity(retired, monkeypatch):
    book, rule = retired
    old_id = rule.id
    old_history = deepcopy(rule.metadata_json["story_bible_update_history"])
    replacement = await _replace_source(
        book, "The current adopted version establishes the new brass-key constraint."
    )
    await _apply(replacement, monkeypatch, "Newly adopted brass-key constraint.")
    current = list(
        await book[0].scalars(select(WorldRuleModel).where(WorldRuleModel.project_id == book[1].id))
    )
    assert len(current) == 1 and current[0].id == old_id
    assert current[0].metadata_json["story_bible_update_history"][:-1] == old_history
    assert await inspection._world_rules_for_current_read(book[0], book[1].id, current) == current


async def test_planner_runtime_failure_and_cancellation_propagate(retired, monkeypatch):
    book, rule = retired
    for error_type in (RuntimeError, asyncio.CancelledError):
        error = error_type("read interrupted")

        async def interrupted(*_args, _error=error, **_kwargs):
            raise _error

        monkeypatch.setattr(projection, "_plan", interrupted)
        with pytest.raises(error_type) as caught:
            await inspection._world_rules_for_current_read(book[0], book[1].id, [rule])
        assert caught.value is error
