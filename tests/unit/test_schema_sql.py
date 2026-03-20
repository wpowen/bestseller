from __future__ import annotations

import pytest

from bestseller.infra.db.base import Base
from bestseller.infra.db import models as db_models
from bestseller.infra.db.schema import render_schema_sql, render_schema_statements


pytestmark = pytest.mark.unit


def test_render_schema_sql_contains_extensions_and_core_tables() -> None:
    sql = render_schema_sql()

    assert "CREATE EXTENSION IF NOT EXISTS pgcrypto;" in sql
    assert "CREATE EXTENSION IF NOT EXISTS vector;" in sql
    assert "CREATE TABLE projects" in sql
    assert "CREATE TABLE planning_artifact_versions" in sql
    assert "CREATE TABLE world_rules" in sql
    assert "CREATE TABLE characters" in sql
    assert "CREATE TABLE chapters" in sql
    assert "CREATE TABLE scene_cards" in sql
    assert "CREATE TABLE canon_facts" in sql
    assert "CREATE TABLE rewrite_impacts" in sql
    assert "CREATE TABLE workflow_runs" in sql


def test_metadata_registers_expected_tables() -> None:
    assert "projects" in Base.metadata.tables
    assert "world_rules" in Base.metadata.tables
    assert "characters" in Base.metadata.tables
    assert "scene_draft_versions" in Base.metadata.tables
    assert "rewrite_impacts" in Base.metadata.tables
    assert "retrieval_chunks" in Base.metadata.tables
    assert db_models.ProjectModel.__tablename__ == "projects"


def test_render_schema_statements_includes_extensions_and_indexes() -> None:
    statements = render_schema_statements()

    assert "CREATE EXTENSION IF NOT EXISTS pgcrypto;" in statements
    assert any("CREATE TABLE projects" in statement for statement in statements)
    assert any("CREATE INDEX idx_retrieval_chunks_embedding" in statement for statement in statements)
