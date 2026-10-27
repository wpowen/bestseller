"""Compile actual recovery SELECTs as PostgreSQL without connecting to PG.

SQLite drives the real service/selector/checkpoint; the ORM event inspects each
statement before SQLite removes PostgreSQL row-lock syntax. Only the existing
fixture's final judge gateway is fake.
"""

from contextlib import contextmanager

import pytest
from sqlalchemy import event
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session
from test_pending_outline_judge_recovery import case as recovery_case  # noqa: F401
from test_pending_outline_judge_recovery import execute, inspect_plan, unchanged_originals

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@contextmanager
def _postgresql_statements(case):
    statements = []

    def observe(state):
        statements.append(
            {
                "select": state.is_select,
                "after_gateway": bool(case.calls),
                "sql": str(state.statement.compile(dialect=postgresql.dialect())),
            }
        )

    event.listen(Session, "do_orm_execute", observe)
    try:
        yield statements
    finally:
        event.remove(Session, "do_orm_execute", observe)


async def test_inspect_all_actual_selects_are_readonly_without_row_locks(
    recovery_case,  # noqa: F811
):
    h = recovery_case
    with _postgresql_statements(h) as statements:
        prepared = await inspect_plan(h)

    assert prepared["status"] == "prepared"
    assert prepared["provider_calls"] == 0 and prepared["database_writes"] is False
    assert h.calls == []
    assert statements and all(row["select"] for row in statements)
    assert all("FOR UPDATE" not in row["sql"].upper() for row in statements)
    # Exercise the complete positive proof rather than only an early return.
    sql = "\n".join(row["sql"] for row in statements)
    for table in (
        "projects",
        "workflow_runs",
        "planning_artifact_versions",
        "execution_budgets",
        "execution_operations",
        "execution_attempts",
    ):
        assert f"FROM {table}" in sql
    await unchanged_originals(h)


async def test_execute_locks_project_and_origin_before_and_after_judge(
    recovery_case,  # noqa: F811
):
    h = recovery_case
    prepared = await inspect_plan(h)
    with _postgresql_statements(h) as statements:
        result = await execute(h, prepared)

    assert result["status"] == "completed" and len(h.calls) == 1
    locked = [row for row in statements if row["select"] and "FOR UPDATE" in row["sql"].upper()]
    for after_gateway in (False, True):
        for table in ("projects", "workflow_runs"):
            assert any(
                row["after_gateway"] is after_gateway and f"FROM {table}" in row["sql"]
                for row in locked
            )
    await unchanged_originals(h)
