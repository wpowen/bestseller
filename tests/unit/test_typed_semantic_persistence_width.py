"""Persistence-width regression: no book seed, operator run or model call.

The optional PostgreSQL case uses a temporary VARCHAR boundary table only.
It is a DB boundary fixture, never a native book artifact or literary grade.
"""
import ast
from copy import deepcopy
from datetime import datetime
import hashlib
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from sqlalchemy import String, inspect as sa_inspect, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.engine import make_url

from bestseller.infra.db import models

ROOT = Path(__file__).resolve().parents[2]
OPERATORS = (
    "select_original_four_paid_title.py",
    "preview_original_four_selection_capacity.py",
    "review_original_four_typed_evidence.py",
)
OPERATOR_DIR = ROOT / "reports/framework-recovery-2026-10-21"
SERVICE_DIR = ROOT / "src/bestseller/services"
pytestmark = pytest.mark.unit


def _tree(name):
    return ast.parse((OPERATOR_DIR / name).read_text(), filename=name)


def _constants(tree):
    return {t.id: n.value for n in tree.body if isinstance(n, ast.Assign)
            for t in n.targets if isinstance(t, ast.Name)}


def _value(node, values, constants):
    """Read literal routing expressions, without importing/running an operator."""
    if isinstance(node, ast.Name):
        return values[node.id] if node.id in values else _value(constants[node.id], values, constants)
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Dict):
        return {_value(k, values, constants): _value(v, values, constants)
                for k, v in zip(node.keys, node.values)}
    if isinstance(node, (ast.Tuple, ast.List)):
        return [_value(v, values, constants) for v in node.elts]
    if isinstance(node, ast.Attribute):
        return getattr(_value(node.value, values, constants), node.attr)
    if isinstance(node, ast.Subscript):
        return _value(node.value, values, constants)[_value(node.slice, values, constants)]
    if isinstance(node, ast.Compare) and len(node.ops) == 1 and isinstance(node.ops[0], ast.Eq):
        return _value(node.left, values, constants) == _value(node.comparators[0], values, constants)
    if isinstance(node, ast.IfExp):
        return _value(node.body if _value(node.test, values, constants) else node.orelse, values, constants)
    raise AssertionError("Nonliteral persistence tag: " + ast.unparse(node))


def _schemas():
    result = {}
    for kind, filename in (("body", "fanqie_short_semantic_evidence_selection.py"),
                           ("title", "fanqie_short_title_semantic_judge.py")):
        tree = ast.parse((SERVICE_DIR / filename).read_text())
        result[kind] = ast.literal_eval(_constants(tree)["SCHEMA"])
    return result


def _helpers(kind):
    """Compile only pure guard helpers; main/inputs/imports never execute."""
    tree = _tree(OPERATORS[2])
    constants = _constants(tree)
    schemas = _schemas()
    ns = {"m": models, "String": String, "inspect": sa_inspect,
          "UUID": UUID, "datetime": datetime, "json": json, "hashlib": hashlib,
          "body": SimpleNamespace(SCHEMA=schemas["body"]),
          "title": SimpleNamespace(SCHEMA=schemas["title"]), "deepcopy": deepcopy}
    for name, node in constants.items():
        try:
            ns[name] = _value(node, {**ns, "kind": kind}, constants)
        except (KeyError, AttributeError, AssertionError, TypeError):
            pass  # UUID identities etc are irrelevant to the width guard.
    functions = [n for n in tree.body if isinstance(n, ast.FunctionDef)]
    exec(compile(ast.fix_missing_locations(ast.Module(body=functions, type_ignores=[])),
                 "<pure-persistence-width-helpers-only>", "exec"), ns)
    guards = [n for n in functions if ("persist" in n.name or "width" in n.name)
              and "kind" in [a.arg for a in n.args.args]]
    assert guards, "Add an executable pure persistence-width preflight(kind) before complete_text"
    return tree, ns, guards


@pytest.mark.parametrize("kind", ["body", "title"])
def test_leaf_column_tag_fits_actual_orm_without_shortening_content_schema(kind):
    tree = _tree(OPERATORS[2])
    schemas = _schemas()
    column = models.PlanningArtifactVersionModel.__table__.c.schema_version
    assert isinstance(column.type, String) and column.type.length == 32
    assert len(schemas[kind]) > column.type.length  # Reproduces the real hazard.
    values = {"kind": kind, "args": SimpleNamespace(kind=kind),
              "content": {"schema": schemas[kind]},
              "body": SimpleNamespace(SCHEMA=schemas["body"]),
              "title": SimpleNamespace(SCHEMA=schemas["title"])}
    constructors = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
                    and isinstance(n.func, ast.Attribute)
                    and n.func.attr == "PlanningArtifactVersionModel"]
    assert constructors, "The actual artifact persistence site must be covered"
    for call in constructors:
        tag = _value(next(k.value for k in call.keywords if k.arg == "schema_version"),
                     values, _constants(tree))
        assert tag == "selection.1" and len(tag) <= column.type.length
    content = next(n.value for n in ast.walk(tree) if isinstance(n, ast.Assign)
                   and any(isinstance(t, ast.Name) and t.id == "content" for t in n.targets)
                   and isinstance(n.value, ast.Dict))
    full_schema = next(v for k, v in zip(content.keys, content.values)
                       if isinstance(k, ast.Constant) and k.value == "schema")
    assert _value(full_schema, values, _constants(tree)) == schemas[kind]


BOUNDED_WRITES = (
    ("WorkflowRunModel", "workflow_type"), ("WorkflowRunModel", "status"),
    ("WorkflowRunModel", "scope_type"), ("WorkflowRunModel", "requested_by"),
    ("PlanningArtifactVersionModel", "artifact_type"),
    ("PlanningArtifactVersionModel", "status"),
    ("PlanningArtifactVersionModel", "schema_version"),
    ("PlanningArtifactVersionModel", "created_by"),
    ("LlmRunModel", "logical_role"), ("LlmRunModel", "prompt_version"),
)


@pytest.mark.parametrize("kind", ["body", "title"])
@pytest.mark.parametrize("model_name,field", BOUNDED_WRITES)
def test_pre_send_guard_reads_actual_column_width_and_rejects_overflow(monkeypatch, kind, model_name, field):
    _, ns, guards = _helpers(kind)
    for guard in guards:
        result = ns[guard.name](kind)
        if guard.name == "validate_persistence_fields":
            assert result["artifact_schema_version"] == "selection.1"
    column = getattr(models, model_name).__table__.c[field]
    assert isinstance(column.type, String) and column.type.length
    # Changing real mapped metadata only in this test proves the guard is not
    # a hardcoded 32 check or a no-op; monkeypatch restores it before the next case.
    monkeypatch.setattr(column.type, "length", 1)
    with pytest.raises((ValueError, AssertionError)):
        for guard in guards:
            ns[guard.name](kind)


def test_all_new_operator_sends_are_dominated_by_width_preflight():
    for filename in OPERATORS:
        tree = _tree(filename)
        sends = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
                 and isinstance(n.func, ast.Attribute) and n.func.attr == "complete_text"]
        if not sends:
            continue  # Zero-send title adoption/capacity preview has no paid boundary.
        _, _, guards = _helpers("body")
        names = {g.name for g in guards}
        inputs = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == "inputs")
        assert any(isinstance(n, ast.Expr) and isinstance(n.value, ast.Call)
                   and isinstance(n.value.func, ast.Name) and n.value.func.id in names
                   for n in inputs.body), "Width guard must be unconditional in inputs(), not after payment"
        parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
        for send in sends:
            child = send
            dominated = False
            while child in parents:
                parent = parents[child]
                for field in ("body", "orelse", "finalbody"):
                    sequence = getattr(parent, field, [])
                    if isinstance(sequence, list) and child in sequence:
                        earlier = sequence[:sequence.index(child)]
                        dominated |= any(isinstance(n, ast.Await) and isinstance(n.value, ast.Call)
                                         and isinstance(n.value.func, ast.Name) and n.value.func.id == "inputs"
                                         for stmt in earlier for n in ast.walk(stmt))
                child = parent
            assert dominated, "Every complete_text needs a preceding awaited guarded inputs()"


def test_sqlite_varchar_acceptance_is_not_postgresql_width_evidence():
    """Local DB boundary fixture: SQLite does not enforce VARCHAR(32)."""
    with sqlite3.connect(":memory:") as connection:
        connection.execute("CREATE TABLE width_fixture (schema_version VARCHAR(32), content TEXT)")
        for schema in _schemas().values():
            connection.execute("INSERT INTO width_fixture VALUES (?, ?)",
                               (schema, json.dumps({"schema": schema})))
        assert all(len(row[0]) > 32 for row in connection.execute("SELECT schema_version FROM width_fixture"))


@pytest.mark.asyncio
async def test_postgresql_real_varchar32_boundary_in_one_temporary_transaction(postgresql_test_settings):
    """Explicit test PG only; no application tables, seeds or migrations."""
    settings = postgresql_test_settings
    selected = make_url(settings.database.url)
    assert "_test_" in selected.database, "Use an explicitly isolated _test_ database"
    engine = create_async_engine(settings.database.url)
    try:
        async with engine.connect() as connection:
            async with connection.begin():
                actual = await connection.scalar(text("SELECT current_database()"))
                assert actual == selected.database and "_test_" in actual
                width = models.PlanningArtifactVersionModel.__table__.c.schema_version.type.length
                assert width == 32
                table = "typed_semantic_width_test_" + uuid4().hex
                await connection.execute(text(f"CREATE TEMP TABLE {table} "
                    f"(schema_version VARCHAR({width}) NOT NULL, content JSONB NOT NULL) ON COMMIT DROP"))
                for value in ("selection.1", "x" * width):
                    await connection.execute(text(f"INSERT INTO {table} VALUES (:tag, CAST(:content AS JSONB))"),
                        {"tag": value, "content": json.dumps({"schema": _schemas()["body"]})})
                for value in ("x" * (width + 1), *_schemas().values()):
                    with pytest.raises(DBAPIError) as caught:
                        async with connection.begin_nested():
                            await connection.execute(text(f"INSERT INTO {table} VALUES (:tag, '{{}}'::jsonb)"),
                                                     {"tag": value})
                    original = caught.value.orig
                    assert (getattr(original, "sqlstate", None) or getattr(original, "pgcode", None)) == "22001"
                assert await connection.scalar(text(f"SELECT count(*) FROM {table}")) == 2
                assert await connection.scalar(text(f"SELECT content->>'schema' FROM {table} LIMIT 1")) == _schemas()["body"]
                # Roll back even this session-local fixture; no permanent objects.
                await connection.rollback()
    finally:
        await engine.dispose()
