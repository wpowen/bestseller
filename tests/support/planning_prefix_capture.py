"""Local, offline Fanqie complete-plan prefix snapshots (e2e17 contract).

This is a test-support snapshot, not a new-book creator or a production restore.
IDs, source content, approvals and paid-return evidence keep their ORIGINAL
identity; cloning them never proves a new conception/model call/human approval.
Only an empty par_P2_ SQLite or explicit par_P3_<nonce>_test_db PG target
can receive the bundle. PG payloads/stages stay in the private P3 mount. No production
budget, book control, draft, review, promotion, knowledge or export is imported.
A stopped ledger placeholder exists solely for FK closure of historical returns.
Missing/changed proof fails the native reader; this tool never regenerates it.
Payloads may contain private prompts: keep bundles local, out of reports/Git.
"""

from __future__ import annotations

import base64
from contextlib import contextmanager
from copy import deepcopy
from datetime import date, datetime
from decimal import Decimal
import hashlib
import inspect as python_inspect
from ipaddress import ip_address
import json
import os
from pathlib import Path, PurePosixPath
import re
import tempfile
from uuid import UUID

from sqlalchemy import event, func, inspect, select, table, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

from bestseller.infra.db import models as db
from bestseller.services.chapter_outline_readiness import (
    _CHAPTER_PLANNING_META,
    _SCENE_PLANNING_META,
)
from bestseller.services.fanqie_short_plan_resume import read_existing_complete_fanqie_plan
from bestseller.services.retained_planning_generation import _Reader
from bestseller.settings import reset_settings_cache

SCHEMA = "par-P2-planning-prefix.v1"
# Current Character has no faction FK. Include Faction only if the owning model
# actually adds that parent edge; never sweep all characters/factions/volumes.
PLANNING_PARENTS = (
    *(
        (db.FactionModel,)
        if any(
            fk.column.table is db.FactionModel.__table__
            for fk in db.CharacterModel.__table__.foreign_keys
        )
        else ()
    ),
    db.CharacterModel,
    db.VolumeModel,
)
MODELS = (
    db.ProjectModel,
    *PLANNING_PARENTS,
    db.WorkflowRunModel,
    db.WorkflowStepRunModel,
    db.PlanningArtifactVersionModel,
    db.ChapterModel,
    db.SceneCardModel,
    db.LlmRunModel,
    db.ExecutionOperationModel,
    db.ExecutionAttemptModel,
    # Required schema for the offline stopped FK sentinel; never copied from source.
    db.ExecutionBudgetModel,
)
TABLE_MODELS = {model.__tablename__: model for model in MODELS}
IMPORT_ORDER = (
    "projects",
    *(model.__tablename__ for model in PLANNING_PARENTS),
    "workflow_runs",
    "workflow_step_runs",
    "planning_artifact_versions",
    "chapters",
    "scene_cards",
    "llm_runs",
    "execution_budgets",
    "execution_operations",
    "execution_attempts",
)
_PG_DATABASE = re.compile(r"par_P3_[a-f0-9]{8,32}_test_db\Z")
_DATABASE_ENV = "BESTSELLER__DATABASE__URL"
_PRIVATE_HOST_ROOT = Path("/Volumes/MACSSD/owen-home/Documents/workspace/bestseller-par-private/P3")
_IDENTITY_SQL = (
    "SELECT current_database(), host(inet_server_addr()), inet_server_port(), "
    "current_setting('transaction_read_only')"
)
ARTIFACT_KINDS = {
    "creation_intent",
    "conception_snapshot",
    "book_spec",
    "world_spec",
    "cast_spec",
    "volume_plan",
    "chapter_outline_batch",
    "fanqie_beat_sheet",
}
WORKFLOW_KINDS = {
    "generate_foundation_plan",
    "generate_novel_plan",
    "complete_foundation_revision",
    "materialize_chapter_outline_batch",
    "materialized_outline_source_admission",
    "generate_fanqie_beat_sheet",
}
PROJECT_META = frozenset(
    """premise writing_profile commercial_brief concept_contract
creation_configuration creation_configuration_hash creation_intent_contract
genre_intent_contract creation_contract_authority
creation_contract_provenance approved_creation_contracts author_intent_manifest
creation_protagonist_name canonical_protagonist_name creation_protagonist_gender
protagonist_embodiment_contract protagonist_identity_manifest identity_manifest
conception_approved creation_canonical_snapshot_id creation_canonical_snapshot_hash
book_design_snapshot book_design_snapshot_hash book_design_snapshot_id
active_foundation_selection execution_scope_id world_spec world_spec_artifact_id
world_spec_version world_spec_source_snapshot_hash world_model creation_intent
source_bound_cast source_bound_planning content_mode pov segment_count unlock_line_ratio
fanqie_short_config fanqie_short_story genre_key platform_target narrative_scale
macro_outline_plan rolling_outline_plan rolling_outline_status target_word_count
story_design story_design_profile story_spine story_engine category_key
prewrite_quality_profile planning_capabilities planning_kernel""".split()
)
PROJECTION_META = (
    frozenset(
        """outline_source_admission fanqie_outline_source_selection
outline_prefix_readiness causal_contract methodology_contract
chapter_phase conflict_phase pacing_mode emotion_phase is_climax
opening_archetype ending_cliff_type primary_emotion conflict_type scene_pacing
location_tag title_register title_source_quote rolling_outline_status
source_snapshot_hash macro_plan_hash""".split()
    )
    | frozenset(_CHAPTER_PLANNING_META)
    | frozenset(_SCENE_PLANNING_META)
)


class PrefixError(ValueError):
    """Fixed codes only; never include a prompt, DSN or source exception string."""

    def __init__(self, code):
        if not isinstance(code, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,100}", code):
            code = "invalid_error_code"
        self.code = "prefix_" + code
        super().__init__(code)


def _need(ok, code):
    if not ok:
        raise PrefixError(code)


def _json(value):
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def _sha(value):
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _record(model, row):
    # Type conversion applies only to SQL scalar columns, never recursively to story JSON.
    result = {}
    for attr in inspect(model).column_attrs:
        value = deepcopy(getattr(row, attr.key))
        if isinstance(value, (UUID, date, datetime, Decimal)):
            value = str(value)
        result[attr.key] = value
    return result


def _decode(model, row):
    result = deepcopy(row)
    mapper = inspect(model)
    _need(set(result) == {a.key for a in mapper.column_attrs}, "column_set_changed")
    for attr in mapper.column_attrs:
        value = result[attr.key]
        if value is None:
            continue
        try:
            typ = attr.columns[0].type.python_type
            if typ is UUID:
                value = UUID(value)
            elif typ is datetime:
                value = datetime.fromisoformat(value)
            elif typ is date:
                value = date.fromisoformat(value)
            elif typ is Decimal:
                value = Decimal(value)
        except (ValueError, TypeError, NotImplementedError):
            raise PrefixError("invalid_scalar") from None
        result[attr.key] = value
    return result


def _relative(ref):
    _need(isinstance(ref, str) and bool(ref) and "\\" not in ref, "payload_path")
    path = PurePosixPath(ref)
    _need(not path.is_absolute() and ".." not in path.parts and str(path) == ref, "payload_path")
    return path


def _safe_file(root, ref):
    path = (root / str(_relative(ref))).resolve()
    _need(path.is_relative_to(root) and path != root, "payload_path")
    return path


async def _source_guard(session):
    _need(not session.new and not session.dirty and not session.deleted, "pending_source_state")
    if session.get_bind().dialect.name == "postgresql":
        # Caller must begin READ ONLY and set timeout BEFORE calling us.
        state = (
            await session.execute(
                text(
                    "SELECT current_setting('transaction_read_only'), "
                    "(SELECT setting::bigint FROM pg_settings WHERE name='statement_timeout')"
                )
            )
        ).one()
        _need(state[0] == "on" and 0 < state[1] <= 30000, "readonly_bounded_transaction_required")
    else:
        _need(session.get_bind().dialect.name == "sqlite", "source_backend")


async def _native(session, settings, project):
    try:
        result = await read_existing_complete_fanqie_plan(session, settings, project=project)
    except (ValueError, TypeError, KeyError, AttributeError, OSError) as error:
        raise PrefixError("source_incomplete") from error
    _need(result is not None and result.get("materialization_reused") is True, "source_incomplete")
    return result


def _target_url(session):
    engine = session.get_bind()
    if engine.dialect.name == "sqlite":
        target = engine.url.database or ":memory:"
        _need(
            target == ":memory:"
            or (Path(target).name.startswith("par_P2_") and "test" in Path(target).stem),
            "isolated_sqlite_required",
        )
        return None
    _need(engine.dialect.name == "postgresql", "isolated_sqlite_required")
    try:
        selected = make_url(os.environ.get(_DATABASE_ENV, ""))
        actual = make_url(engine.url)
        valid = all(
            url.drivername == "postgresql+asyncpg"
            and isinstance(url.database, str)
            and _PG_DATABASE.fullmatch(url.database)
            and url.host
            and str(ip_address(url.host)) == url.host
            and url.port is not None
            and 0 < url.port < 65536
            and not url.query
            for url in (selected, actual)
        )
        valid = valid and (actual.host, actual.port, actual.database) == (
            selected.host,
            selected.port,
            selected.database,
        )
        valid = valid and os.environ.get("E2E_SIM_DATABASE", selected.database) == selected.database
        valid = valid and os.environ.get("E2E_SIM_PG_IP", selected.host) == selected.host
    except (ArgumentError, ValueError, TypeError):
        raise PrefixError("isolated_pg_required") from None
    _need(valid, "isolated_pg_required")
    return selected


def _driver_route(args, params, selected):
    # Explicit network identity only. Native server settings are permitted only
    # for nonrouting application name and numeric timeouts. No DSN, service,
    # hostaddr, async creator, search_path, role or arbitrary session options.
    allowed = {
        "host",
        "port",
        "database",
        "user",
        "password",
        "timeout",
        "command_timeout",
        "prepared_statement_cache_size",
        "statement_cache_size",
        "ssl",
    }
    server_settings = params.get("server_settings")
    if server_settings is not None:
        _need(
            isinstance(server_settings, dict)
            and set(server_settings)
            <= {
                "application_name",
                "statement_timeout",
                "lock_timeout",
                "idle_in_transaction_session_timeout",
            }
            and all(
                isinstance(value, str)
                and "\0" not in value
                and (key == "application_name" or re.fullmatch(r"[0-9]{1,12}", value))
                for key, value in server_settings.items()
            ),
            "pg_driver_redirect_forbidden",
        )
        allowed.add("server_settings")
    _need(
        not args
        and set(params) <= allowed
        and (params.get("host"), params.get("port"), params.get("database"))
        == (selected.host, selected.port, selected.database),
        "pg_driver_redirect_forbidden",
    )


def _check_pg_identity(row, selected):
    _need(
        tuple(row) == (selected.database, selected.host, selected.port, "on"),
        "pg_target_identity",
    )


@contextmanager
def _pg_routing(session, selected):
    if selected is None:
        yield
        return
    engine = session.get_bind()
    # Inspect standard SQLAlchemy creator arguments before any connection. A
    # custom creator/pool is opaque and cannot establish pre-connect routing.
    creator = engine.pool._creator
    _need(
        getattr(creator, "__module__", None) == "sqlalchemy.engine.create"
        and getattr(creator, "__name__", None) == "connect",
        "pg_opaque_creator_forbidden",
    )
    closure = python_inspect.getclosurevars(creator).nonlocals
    _need("cargs_tup" in closure and "cparams" in closure, "pg_opaque_creator_forbidden")
    _driver_route(closure["cargs_tup"], closure["cparams"], selected)

    def before_connect(dialect, record, args, params):
        _target_url(session)
        _driver_route(args, params, selected)

    def checkout(connection, record, proxy):
        _target_url(session)
        # This also verifies pooled/reconnected handles on the write checkout.
        # No application transaction exists at checkout; the probe is rolled back.
        cursor = connection.cursor()
        try:
            cursor.execute("SET TRANSACTION READ ONLY")
            cursor.execute("SET LOCAL statement_timeout = 10000")
            cursor.execute(_IDENTITY_SQL)
            _check_pg_identity(cursor.fetchone(), selected)
        finally:
            cursor.close()
            connection.rollback()

    event.listen(engine, "do_connect", before_connect)
    event.listen(engine, "checkout", checkout)
    try:
        yield
    finally:
        event.remove(engine, "checkout", checkout)
        event.remove(engine, "do_connect", before_connect)


async def guard_prefix_target(session):
    """Verify an explicitly selected target without INSERT or source proof.

    PG requires a fresh, clean Session, explicit BESTSELLER__DATABASE__URL with
    a literal IP/port, asyncpg's standard creator, and par_P3_<hex nonce>_test_db.
    Probe the actual server in READ ONLY and end only this owned probe transaction.
    SQLite preserves P2's caller-owned transaction and namespace behavior.
    """
    selected = _target_url(session)
    _need(not session.new and not session.dirty and not session.deleted, "pending_target_state")
    if selected is None:
        return
    _need(not session.in_transaction(), "fresh_pg_target_session_required")
    with _pg_routing(session, selected):
        try:
            await session.execute(text("SET TRANSACTION READ ONLY"))
            await session.execute(text("SET LOCAL statement_timeout = 10000"))
            result = await session.execute(text(_IDENTITY_SQL))
            _check_pg_identity(result.one(), selected)
        finally:
            await session.rollback()


def _private_payload_root(settings):
    root = Path(settings.artifact_store.local_dir)
    _need(root.is_absolute(), "private_payload_root")
    root = root.resolve()
    _need(
        root == Path("/private-prefix/artifacts")
        or (root.is_relative_to(_PRIVATE_HOST_ROOT) and root != _PRIVATE_HOST_ROOT),
        "private_payload_root",
    )
    return root


async def require_empty_prefix_target(session):
    """All business tables must be empty; PG Alembic version is metadata only."""
    connection = await session.connection()
    pg = session.get_bind().dialect.name == "postgresql"
    schemas = [None]
    if pg:
        schemas = await connection.run_sync(lambda conn: inspect(conn).get_schema_names())
        schemas = [
            schema
            for schema in schemas
            if schema != "information_schema" and not schema.startswith("pg_")
        ]
        _need("public" in schemas, "pg_public_schema_required")
    for schema in schemas:
        names = await connection.run_sync(
            lambda conn, schema=schema: inspect(conn).get_table_names(schema=schema)
        )
        for name in names:
            if pg and schema == "public" and name == "alembic_version":
                columns = await connection.run_sync(
                    lambda conn, name=name, schema=schema: inspect(conn).get_columns(
                        name, schema=schema
                    )
                )
                _need({column["name"] for column in columns} == {"version_num"}, "schema_metadata")
                continue
            _need(
                await session.scalar(select(func.count()).select_from(table(name, schema=schema)))
                == 0,
                "target_not_empty",
            )


def _parent_references(rows):
    references = {model.__tablename__: set() for model in PLANNING_PARENTS}
    for model in (db.ChapterModel, *PLANNING_PARENTS):
        for fk in inspect(model).local_table.foreign_keys:
            name = fk.column.table.name
            if name in references:
                for row in rows[model.__tablename__]:
                    if row.get(fk.parent.key) is not None:
                        references[name].add(str(row[fk.parent.key]))
    return references


async def capture_planning_parents(reader, rows, *, project_id):
    """SELECT only the original FK parents; never replace identity or add proof."""
    while True:
        references = _parent_references(rows)
        added = False
        for model in PLANNING_PARENTS:
            name = model.__tablename__
            captured = {record["id"] for record in rows[name]}
            for key in sorted(references[name] - captured):
                parent = await reader.get(model, UUID(key))
                _need(
                    parent is not None and parent.project_id == UUID(str(project_id)),
                    "planning_parent_closure",
                )
                record = _record(model, parent)
                record["metadata_json"] = {
                    k: v for k, v in (record["metadata_json"] or {}).items() if k in PROJECTION_META
                }
                if model is db.VolumeModel:
                    record["status"] = "planned"
                if model is db.CharacterModel:
                    # Chapter-produced character knowledge/state is not planning.
                    record.update(
                        knowledge_state_json={},
                        arc_state=None,
                        alive_status="alive",
                        death_chapter_number=None,
                        stance=None,
                        stance_locked_until_chapter=None,
                    )
                rows[name].append(record)
                added = True
        if not added:
            return


async def capture_prefix(session, settings, *, project_id, exclude_authorization_ref=False):
    """Ordinary capture stays fail-closed; audit never grants export authority."""
    body = await _prefix_candidate(
        session,
        settings,
        project_id=project_id,
        inspect_payload=lambda ref, document: _no_credentials(document),
    )
    if exclude_authorization_ref:
        from support.planning_prefix_exclusions import exclude_workflow_authorization_ref

        body = exclude_workflow_authorization_ref(body)
    _no_credentials(body)
    return {**body, "sha256": _sha(body)}


async def _prefix_candidate(session, settings, *, project_id, inspect_payload):
    """One native-proven in-memory selection, without writing or sealing a package."""
    await _source_guard(session)
    _need(settings.artifact_store.mode == "local", "local_payload_store_required")
    reader = _Reader(session)
    project = await reader.get(db.ProjectModel, UUID(str(project_id)))
    _need(project is not None and project.project_type == "fanqie_short", "fanqie_project_required")
    reuse = await _native(session, settings, project)
    tables = {name: [] for name in TABLE_MODELS}
    p = _record(db.ProjectModel, project)
    p.update(status="planning", current_chapter_number=0, current_volume_number=1, lock_version=0)
    p["metadata_json"] = {k: v for k, v in p["metadata_json"].items() if k in PROJECT_META}
    tables["projects"] = [p]
    selected_ids = {
        reuse["source_artifact_id"],
        *reuse["foundation_artifact_ids"].values(),
        project.metadata_json["creation_canonical_snapshot_id"],
        *reuse.get("legacy_beat_artifact_ids", []),
    }
    for artifact in await reader.rows(
        db.PlanningArtifactVersionModel, db.PlanningArtifactVersionModel.project_id == project.id
    ):
        if str(artifact.id) in selected_ids:
            _need(artifact.artifact_type in ARTIFACT_KINDS, "artifact_kind")
            tables["planning_artifact_versions"].append(
                _record(db.PlanningArtifactVersionModel, artifact)
            )
    _need(len(tables["planning_artifact_versions"]) == len(selected_ids), "artifact_closure")
    owner_ids = {
        reuse[k]
        for k in (
            "planning_workflow_run_id",
            "outline_workflow_run_id",
            "hard_admission_workflow_run_id",
        )
    }
    owner_ids |= {
        UUID(a["source_run_id"]) for a in tables["planning_artifact_versions"] if a["source_run_id"]
    }
    for wid in sorted(owner_ids, key=str):
        row = await reader.get(db.WorkflowRunModel, wid)
        # A canonical artifact can retain an upstream conception ID without needing
        # its execution workflow. No downstream owner can enter through this edge.
        if row and row.workflow_type in WORKFLOW_KINDS:
            _need(
                row.project_id == project.id
                and row.status in {"completed", "failed", "cancelled", "machine_blocked"},
                "workflow_closure",
            )
            tables["workflow_runs"].append(_record(db.WorkflowRunModel, row))
    captured_owners = {UUID(r["id"]) for r in tables["workflow_runs"]}
    for model in (db.ChapterModel, db.SceneCardModel):
        for row in await reader.rows(model, model.project_id == project.id):
            record = _record(model, row)
            record["status"] = "planned"
            record["metadata_json"] = {
                k: v for k, v in record["metadata_json"].items() if k in PROJECTION_META
            }
            if model is db.ChapterModel:
                record.update(current_word_count=0, revision_count=0, production_state="pending")
            tables[model.__tablename__].append(record)
    await capture_planning_parents(reader, tables, project_id=project.id)
    # Historical call custody only for captured planning workflows.
    runs = await reader.rows(
        db.LlmRunModel,
        db.LlmRunModel.project_id == project.id,
        db.LlmRunModel.workflow_run_id.in_(captured_owners),
    )
    files, op_keys, scopes = {}, set(), set()
    root = Path(settings.artifact_store.local_dir).resolve()
    for row in runs:
        tables["llm_runs"].append(_record(db.LlmRunModel, row))
        receipt = (row.metadata_json or {}).get("execution_receipt") or {}
        if receipt.get("operation_key"):
            op_keys.add(receipt["operation_key"])
        if receipt.get("scope_id"):
            scopes.add(receipt["scope_id"])
        for kind in ("request", "response"):
            ref = getattr(row, kind + "_payload_ref")
            if ref:
                path = _safe_file(root, ref)
                _need(path.is_file(), "payload_missing")
                raw = path.read_bytes()
                # Credential fields are never allowed into an on-disk bundle.
                inspect_payload(ref, json.loads(raw))
                files[ref] = {
                    "base64": base64.b64encode(raw).decode(),
                    "sha256": hashlib.sha256(raw).hexdigest(),
                }
    for row in await reader.rows(
        db.WorkflowStepRunModel, db.WorkflowStepRunModel.workflow_run_id.in_(captured_owners)
    ):
        tables["workflow_step_runs"].append(_record(db.WorkflowStepRunModel, row))
    for row in await reader.rows(
        db.ExecutionAttemptModel, db.ExecutionAttemptModel.operation_key.in_(op_keys)
    ):
        tables["execution_attempts"].append(_record(db.ExecutionAttemptModel, row))
        scopes.add(row.scope_id)
        op_keys.add(
            hashlib.sha256(f"transport-result.v1\n{row.scope_id}\n{row.id}".encode()).hexdigest()
        )
    for key in sorted(op_keys):
        row = await reader.get(db.ExecutionOperationModel, key)
        _need(row is not None and row.scope_id in scopes, "execution_closure")
        tables["execution_operations"].append(_record(db.ExecutionOperationModel, row))
    # Never clone a live or renewable spending allowance.
    for scope in sorted(scopes):
        tables["execution_budgets"].append(
            {
                "scope_id": scope,
                "max_calls": 0,
                "max_tokens": 0,
                "calls_used": 0,
                "tokens_charged": 0,
                "deadline_at": "2000-01-01 00:00:00+00:00",
                "status": "stopped",
                "metadata_json": {"schema": SCHEMA, "offline_fk_sentinel": True},
            }
        )
    for rows in tables.values():
        rows.sort(key=_json)
    body = {
        "schema": SCHEMA,
        "project_id": str(project.id),
        "tables": tables,
        "files": files,
        "source_plan_sha256": _plan_hash(reuse),
        "identity_policy": "original IDs; isolated offline clone; no new production authority",
    }
    return body


def _no_credentials(value, _path=()):
    """Check native schema and authorized story text without altering retained bytes."""
    _credential_walk(value, _path)


def scan_credential_fields(value):
    """Return all blocked locations and scalar properties, never values/hashes."""
    findings = []

    def record(reason, key, item, path):
        def label(part):
            if type(part) is int:
                return part
            if isinstance(part, str) and (
                re.search(r"sk-|gh[pousr]_|AKIA|xox[baprs]-|AIza|ya29\.", part)
                or re.fullmatch(r"[0-9a-fA-F]{32,}", part)
            ):
                return "<dynamic-key>"
            if isinstance(part, str) and re.fullmatch(r"[A-Za-z_$][A-Za-z0-9_$-]{0,99}", part):
                return part
            return "<dynamic-key>"

        tokens = [label(part) for part in path]
        rendered = "$" + "".join(
            f"[{part}]" if type(part) is int else "." + part for part in tokens
        )
        string = isinstance(item, str)
        finding = {
            "reason": reason,
            "field_name": label(key),
            "path": rendered,
            "table": tokens[1] if len(tokens) > 1 and tokens[0] == "tables" else None,
            "structure": tokens[3] if len(tokens) > 3 and tokens[0] == "tables" else "payload",
            "value_type": type(item).__name__,
            "length": len(item) if isinstance(item, (str, list, dict)) else None,
            "character_classes": {
                "ascii_only": item.isascii() if string else None,
                "han": bool(re.search(r"[\u3400-\u4dbf\u4e00-\u9fff\U00020000-\U0002fa1f]", item))
                if string
                else None,
                "letters": bool(re.search(r"[A-Za-z]", item)) if string else None,
                "digits": bool(re.search(r"[0-9]", item)) if string else None,
                "whitespace": any(c.isspace() for c in item) if string else None,
                "punctuation": any(not c.isalnum() and not c.isspace() for c in item)
                if string
                else None,
                "ascii_run_8": bool(re.search(r"[A-Za-z0-9]{8,}", item)) if string else None,
            },
            "key_format": reason == "credential_value",
        }
        findings.append(finding)

    _credential_walk(value, (), observer=record)
    # A blocked field and its token-shaped value can have the same location.
    token_paths = {f["path"] for f in findings if f["reason"] == "credential_value"}
    for finding in findings:
        finding["key_format"] = finding["path"] in token_paths
    return findings


def _credential_walk(value, _path=(), *, observer=None):
    """Shared decisions; only the metadata auditor continues after a rejection."""
    trusted_request = (
        _path == ()
        and isinstance(value, dict)
        and value.get("schema_version") == "llm-payload.v1"
        and value.get("kind") == "request"
        and value.get("logical_role") == "planner"
        and value.get("prompt_template") == "planner_cast_spec"
    )
    native_tool = native_secret = None
    schema_path = (
        "requested",
        "tools",
        "[]",
        "function",
        "parameters",
        "$defs",
        "CharacterInput",
        "properties",
        "secret",
    )
    if trusted_request:
        from bestseller.services.source_bound_planning_submission import planning_submission_tool

        native_tool = planning_submission_tool("cast_spec")
        native_secret = native_tool["function"]["parameters"]["$defs"]["CharacterInput"][
            "properties"
        ]["secret"]

    story_keys = {"secret", "character_secret", "hidden_secret", "backstory_secret"}
    story_anchors = {
        "cast",
        "cast_spec",
        "cast_spec_payload",
        "character",
        "characters",
        "book_spec",
        "book_spec_payload",
        "source_bound_cast",
    }
    non_story = {
        "requested",
        "tools",
        "parameters",
        "properties",
        "$defs",
        "schema",
        "schemas",
        "components",
        "definitions",
        "$schema",
        "json_schema",
        "input_schema",
        "output_schema",
        "default",
        "examples",
        "example",
        "headers",
        "request_headers",
        "response_headers",
        "authorization",
        "auth",
        "credentials",
        "environment",
        "env",
        "config",
        "settings",
        "connection",
        "audit",
        "logs",
        "telemetry",
    }
    credential_pattern = re.compile(
        r"(?:postgres(?:ql)?(?:\+\w+)?|https?)://[^\s/]*:[^\s/]*@"
        r"|sk-(?:proj-|ant-)?[A-Za-z0-9_-]{20,}|(?:Bearer|Basic)\s+\S+"
        r"|AKIA[A-Z0-9]{16}|gh[pousr]_[A-Za-z0-9]{30,}"
        r"|xox[baprs]-[A-Za-z0-9-]{20,}|AIza[A-Za-z0-9_-]{30,}"
        r"|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"
        r"|ya29\.[A-Za-z0-9_-]+"
    )

    def check(value, _path, trusted_tool=False, story_context=False, trace=()):
        if isinstance(value, dict):
            for key, item in value.items():
                story_text = (
                    key in story_keys
                    and story_context
                    and not non_story.intersection(_path)
                    and isinstance(item, str)
                    and re.search(r"[\u3400-\u4dbf\u4e00-\u9fff\U00020000-\U0002fa1f]", item)
                    and not re.search(r"[A-Za-z0-9]{8,}", item)
                    and not credential_pattern.search(item)
                )
                allowed = (
                    (
                        key == "secret"
                        and trusted_tool
                        and _path == schema_path[:-1]
                        and _json(item) == _json(native_secret)
                    )
                    or story_text
                    or not re.search(
                        r"(^|_)(api_key|password|secret|authorization|access_token|credential)($|_)",
                        str(key),
                        re.I,
                    )
                )
                if not allowed:
                    error = PrefixError("credential_field")
                    category = (
                        key
                        if key
                        in {
                            "api_key_env",
                            "rate_limit_fallback_api_key_env",
                            "api_key_header",
                            "secret",
                            "password",
                            "authorization",
                            "access_token",
                            "api_key",
                        }
                        else "other"
                    )
                    # No raw key, path, value, request, or locals are retained.
                    error.credential_rejection = {
                        "field_category": category,
                        "field_sha256": hashlib.sha256(str(key).encode()).hexdigest(),
                        "path_sha256": hashlib.sha256(_json([*_path, key]).encode()).hexdigest(),
                        "path_depth": len(_path) + 1,
                    }
                    if observer is None:
                        raise error
                    observer("credential_field", key, item, (*trace, key))
                child_story = story_context or key in story_anchors
                if _path == ("tables", "planning_artifact_versions", "[]") and key == "content":
                    child_story = value.get("artifact_type") in {"cast_spec", "book_spec"}
                check(item, (*_path, key), trusted_tool, child_story, (*trace, key))
        elif isinstance(value, list):
            for index, item in enumerate(value):
                child_trust = trusted_tool
                if _path == ("requested", "tools"):
                    child_trust = trusted_request and _json(item) == _json(native_tool)
                check(item, (*_path, "[]"), child_trust, story_context, (*trace, index))
        elif isinstance(value, str):
            if credential_pattern.search(value):
                if observer is None:
                    raise PrefixError("credential_value")
                observer("credential_value", _path[-1] if _path else "<root>", value, trace)

    check(value, _path)


def _plan_hash(result):
    return _sha(json.loads(json.dumps(result, default=str)))


def _offline_invariants(rows):
    p = rows["projects"][0]
    _need(
        p["status"] == "planning"
        and p["current_chapter_number"] == 0
        and p["current_volume_number"] == 1
        and p["lock_version"] == 0,
        "project_runtime_state",
    )
    _need(set(p["metadata_json"]) <= PROJECT_META, "project_metadata_allowlist")
    for name, references in _parent_references(rows).items():
        ids = [str(record["id"]) for record in rows[name]]
        _need(set(ids) == references and len(ids) == len(set(ids)), "planning_parent_closure")
        for record in rows[name]:
            _need(set(record["metadata_json"]) <= PROJECTION_META, "parent_metadata_allowlist")
            if name == "volumes":
                _need(record["status"] == "planned", "volume_runtime_state")
            if name == "characters":
                _need(
                    record["knowledge_state_json"] == {}
                    and record["arc_state"] is None
                    and record["alive_status"] == "alive"
                    and record["death_chapter_number"] is None
                    and record["stance"] is None
                    and record["stance_locked_until_chapter"] is None,
                    "character_runtime_state",
                )
    owners = {row["id"] for row in rows["workflow_runs"]}
    chapters = {row["id"] for row in rows["chapters"]}
    for row in rows["workflow_runs"]:
        _need(
            row["workflow_type"] in WORKFLOW_KINDS
            and row["status"] in {"completed", "failed", "cancelled", "machine_blocked"},
            "workflow_kind_or_status",
        )
    for row in rows["planning_artifact_versions"]:
        _need(
            row["artifact_type"] in ARTIFACT_KINDS and row["scope_ref_id"] is None,
            "artifact_kind_or_scope",
        )
    for row in rows["chapters"]:
        _need(
            row["status"] == "planned"
            and row["production_state"] == "pending"
            and row["current_word_count"] == 0
            and row["revision_count"] == 0,
            "chapter_runtime_state",
        )
        _need(set(row["metadata_json"]) <= PROJECTION_META, "chapter_metadata_allowlist")
    for row in rows["scene_cards"]:
        _need(row["chapter_id"] in chapters and row["status"] == "planned", "scene_scope_or_status")
        _need(set(row["metadata_json"]) <= PROJECTION_META, "scene_metadata_allowlist")
    for row in rows["workflow_step_runs"]:
        _need(row["workflow_run_id"] in owners, "step_scope")
    scopes, roots, expected = set(), {}, {}
    for row in rows["llm_runs"]:
        _need(row["workflow_run_id"] in owners, "llm_owner")
        receipt = (row["metadata_json"] or {}).get("execution_receipt") or {}
        if receipt.get("operation_key"):
            scope = receipt.get("scope_id")
            _need(isinstance(scope, str) and scope, "execution_scope")
            scopes.add(scope)
            roots[receipt["operation_key"]] = scope
    expected.update(roots)
    for row in rows["execution_attempts"]:
        _need(
            row["operation_key"] in roots and row["scope_id"] == roots[row["operation_key"]],
            "attempt_scope",
        )
        transport = hashlib.sha256(
            f"transport-result.v1\n{row['scope_id']}\n{row['id']}".encode()
        ).hexdigest()
        expected[transport] = row["scope_id"]
    _need({r["key"] for r in rows["execution_operations"]} == set(expected), "operation_set")
    for row in rows["execution_operations"]:
        _need(row["scope_id"] == expected[row["key"]], "operation_scope")
    _need({r["scope_id"] for r in rows["execution_budgets"]} == scopes, "sentinel_scope")
    for row in rows["execution_budgets"]:
        _need(
            row["status"] == "stopped"
            and row["max_calls"] == row["max_tokens"] == 0
            and row["calls_used"] == row["tokens_charged"] == 0
            and row["deadline_at"].year == 2000
            and row["metadata_json"] == {"schema": SCHEMA, "offline_fk_sentinel": True},
            "live_budget_forbidden",
        )


@contextmanager
def _staged_artifact_settings(stage):
    """Let nested readers that reload settings verify the same staged payloads."""
    key = "BESTSELLER__ARTIFACT_STORE__LOCAL_DIR"
    previous = os.environ.get(key)
    try:
        os.environ[key] = str(stage)
        reset_settings_cache()
        yield
    finally:
        if previous is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = previous
        reset_settings_cache()


async def import_prefix(session, settings, bundle, *, expected_sha256):
    """Import trusted capture into an empty P2 SQLite or explicit isolated P3 PG.

    PG needs a fresh Session; guard_prefix_target ends its own READ ONLY probe.
    The caller still owns the import's commit/rollback. Native complete-plan
    verification is unchanged. No source proofs, allowances or approvals created.
    PG payloads and incomplete stages stay outside the repository in private P3.
    """
    selected = _target_url(session)
    if selected is not None:
        _need(settings.artifact_store.mode == "local", "local_payload_store_required")
        _private_payload_root(settings)
    await guard_prefix_target(session)
    with _pg_routing(session, selected):
        return await _import_prefix(session, settings, bundle, expected_sha256=expected_sha256)


async def _import_prefix(session, settings, bundle, *, expected_sha256):
    engine = session.get_bind()
    _need(settings.artifact_store.mode == "local", "local_payload_store_required")
    _need(isinstance(bundle, dict) and bundle.get("schema") == SCHEMA, "bundle_schema")
    body = {k: v for k, v in bundle.items() if k != "sha256"}
    _need(bundle.get("sha256") == _sha(body) == expected_sha256, "bundle_hash")
    _need(set(bundle["tables"]) == set(TABLE_MODELS), "table_allowlist")
    from support.planning_prefix_exclusions import validate_exclusion_manifest

    validate_exclusion_manifest(bundle)
    _no_credentials(bundle)
    decoded = {}
    for name, rows in bundle["tables"].items():
        model = TABLE_MODELS[name]
        _need(isinstance(rows, list), "row_shape")
        decoded[name] = [_decode(model, r) for r in rows]
        if "project_id" in inspect(model).attrs:
            _need(
                all(str(r["project_id"]) == bundle["project_id"] for r in rows), "foreign_project"
            )
        _need(
            await session.scalar(select(func.count()).select_from(model)) == 0, "target_not_empty"
        )
    _need(
        len(decoded["projects"]) == 1 and str(decoded["projects"][0]["id"]) == bundle["project_id"],
        "project_identity",
    )
    _offline_invariants(decoded)
    await require_empty_prefix_target(session)
    connection = await session.connection()
    root = Path(settings.artifact_store.local_dir).resolve()
    _need(not root.exists(), "payload_root_exists")
    payloads = []
    for ref, record in bundle["files"].items():
        path = _safe_file(root, ref)
        try:
            raw = base64.b64decode(record["base64"], validate=True)
        except (ValueError, TypeError):
            raise PrefixError("payload_encoding") from None
        _need(hashlib.sha256(raw).hexdigest() == record["sha256"], "payload_hash")
        _no_credentials(json.loads(raw))
        payloads.append((path, raw))
    # PG uses native Session transactions and real FK flush order. Only SQLite
    # needs the legacy driver in_transaction workaround to preserve caller rollback.
    if engine.dialect.name == "sqlite":
        raw_connection = await connection.get_raw_connection()
        if not raw_connection.driver_connection.in_transaction:
            await connection.exec_driver_sql("BEGIN")
    root.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    pg = engine.dialect.name == "postgresql"
    if pg:
        _private_payload_root(settings)
        _need(root.parent.stat().st_mode & 0o077 == 0, "private_payload_permissions")
    stage = Path(
        tempfile.mkdtemp(
            prefix="par_P3_incomplete_" if pg else "par_P2_incomplete_", dir=root.parent
        )
    )
    staged_settings = settings.model_copy(deep=True)
    staged_settings.artifact_store.local_dir = str(stage)
    async with session.begin_nested():
        for name in IMPORT_ORDER:
            session.add_all(TABLE_MODELS[name](**r) for r in decoded[name])
            await session.flush()
        # A failure leaves only an explicitly incomplete local diagnostic directory.
        for path, raw in payloads:
            staged_path = stage / path.relative_to(root)
            staged_path.parent.mkdir(parents=True, exist_ok=True)
            with staged_path.open("xb") as stream:
                stream.write(raw)
        project = await session.get(db.ProjectModel, UUID(bundle["project_id"]))
        with _staged_artifact_settings(stage):
            result = await _native(session, staged_settings, project)
        _need(_plan_hash(result) == bundle["source_plan_sha256"], "plan_semantics_changed")
        _need(not root.exists(), "payload_root_exists")
        stage.rename(root)
    return project
