#!/usr/bin/env python3
"""Restore the preserved historical dump into a NEW test DB; never clean/reuse it.

Only writes this run's new isolated database and reports/production-recovery-
2026-10-15/PG-HISTORY*.json/md. No LLM calls or manuscript/credential output.
"""
from __future__ import annotations

import argparse
import asyncio
from contextlib import redirect_stdout, redirect_stderr
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import io
import json
import logging
import os
from pathlib import Path
import re
import subprocess
import sys
import traceback
from uuid import uuid4

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
logging.disable(logging.CRITICAL)

from sqlalchemy import create_engine, event, select, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bestseller.infra.db.models import ProjectModel
from bestseller.services.content_origin_policy import content_hash
from bestseller.services.framework_state_migration import migrate_project_prompt_state, migrate_detector_insertions
from bestseller.services.prompt_source_policy import valid_reference_value
from bestseller.settings import load_settings

BASE = "bestseller_history_test_20261015"
DUMP = ROOT / "output/backups/recovery-20261015/historical-daily/bestseller-20260909T045116Z.dump"
REPORT_DIR = ROOT / "reports/production-recovery-2026-10-15"
INVENTORY = REPORT_DIR / "historical-backup-inventory.json"
CONTAINER = "bestseller-db-1"


class CheckFailure(RuntimeError):
    pass


def require(condition, code):
    if not condition:
        raise CheckFailure(code)


def safe_error(exc):
    result = {"type": type(exc).__name__, "frames": [
        {"file": Path(f.filename).name, "line": f.lineno, "function": f.name}
        for f in traceback.extract_tb(exc.__traceback__)]}
    if isinstance(exc, CheckFailure):
        result["code"] = str(exc)
    original = getattr(exc, "orig", None)
    if original is not None:
        result["cause_type"] = type(original).__name__
        result["sqlstate"] = getattr(original, "sqlstate", None)
    return result


def command(args, *, stdin=None):
    result = subprocess.run(args, stdin=stdin, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            timeout=180, check=False)
    if result.returncode:
        # stderr can contain SQL, row contents or connection details; never emit it.
        raise CheckFailure("container_command_failed_exit_" + str(result.returncode)
                           + "_stderr_sha256_" + hashlib.sha256(result.stderr).hexdigest())
    return result.stdout


def guard_target(target):
    require(bool(re.fullmatch(r"bestseller_history_test_20261015(?:_[a-f0-9]{10})?", target))
            and "test" in target, "invalid_isolated_target")


def sql_guard(target):
    def validate(dbapi, _record):
        cursor = dbapi.cursor()
        try:
            cursor.execute("SELECT current_database()")
            actual = cursor.fetchone()[0]
            require(actual == target and "test" in actual.lower(), "sql_database_guard_rejected")
        finally:
            cursor.close()
    return validate


def provision_and_restore(base_url, report):
    require(base_url.host in {"localhost", "127.0.0.1", "::1"} and not base_url.query,
            "only_local_configured_postgres_allowed")
    require(base_url.drivername == "postgresql+asyncpg", "unexpected_configured_driver")
    # Inspect only mapped ports; do not inspect environment values.
    ports = json.loads(command(["docker", "inspect", "--format", "{{json .NetworkSettings.Ports}}", CONTAINER]))
    require(any(int(p["HostPort"]) == (base_url.port or 5432)
                for p in ports.get("5432/tcp", []) or []), "container_port_does_not_match_settings")
    admin = create_engine(base_url.set(drivername="postgresql+psycopg", database="postgres"),
                          echo=False, hide_parameters=True, isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as c:
            require(c.scalar(text("SELECT current_database()")) == "postgres", "admin_database_mismatch")
            system_id = str(c.scalar(text("SELECT system_identifier FROM pg_control_system()")))
            container_id = command(["docker", "exec", CONTAINER, "psql", "-X", "-U", base_url.username,
                "-d", "postgres", "-At", "-v", "ON_ERROR_STOP=1", "-c",
                "SELECT system_identifier FROM pg_control_system()"] ).decode().strip()
            require(system_id == container_id, "container_and_configured_server_differ")
            existing = set(c.scalars(text("SELECT datname FROM pg_database")))
            target = BASE
            if target in existing:
                report["requested_database_already_existed"] = True
                target = BASE + "_" + uuid4().hex[:10]
                while target in existing:
                    target = BASE + "_" + uuid4().hex[:10]
            else:
                report["requested_database_already_existed"] = False
            guard_target(target)
            report["database"] = target
            # CREATE fails if another creator wins the race; never reuse or clean it.
            c.exec_driver_sql(f'CREATE DATABASE "{target}" TEMPLATE template0')
            report["new_database_created"] = True
    finally:
        admin.dispose()
    target_url = base_url.set(database=target)
    require(make_url(target_url).database == target and "test" in target, "derived_url_guard_failed")
    verify = create_engine(target_url.set(drivername="postgresql+psycopg"), echo=False, hide_parameters=True)
    event.listen(verify, "connect", sql_guard(target))
    try:
        with verify.connect() as c:
            require(c.scalar(text("SELECT current_database()")) == target, "pre_restore_database_mismatch")
            require(c.scalar(text("SELECT count(*) FROM information_schema.tables WHERE table_schema='public'")) == 0,
                    "new_target_not_empty_refuse_restore")
    finally:
        verify.dispose()
    print("new isolated test database verified; restoring preserved dump", flush=True)
    with DUMP.open("rb") as source:
        command(["docker", "exec", "-i", CONTAINER, "pg_restore", "-U", base_url.username,
            "--dbname", target, "--no-owner", "--no-acl", "--exit-on-error", "--single-transaction"], stdin=source)
    report["restore"] = {"no_owner": True, "no_acl": True, "single_transaction": True,
                          "clean_or_drop": False, "production_database_connected": False}
    return target_url


def upgrade_restored_schema(target_url, report):
    from alembic import command as alembic_command
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    head = ScriptDirectory.from_config(cfg).get_current_head()
    target = target_url.database
    engine = create_engine(target_url.set(drivername="postgresql+psycopg"), echo=False, hide_parameters=True)
    event.listen(engine, "connect", sql_guard(target))
    try:
        with engine.connect() as c:
            revision = list(c.scalars(text("SELECT version_num FROM alembic_version")))
    finally:
        engine.dispose()
    report["restored_revision"] = revision
    report["current_code_head"] = head
    if revision == [head]:
        report["schema_upgraded"] = False
        return
    # env.py loads settings itself; override only inside this process, with a
    # guard on EVERY sync connection opened by its migration engine.
    previous = os.environ.get("BESTSELLER__DATABASE__URL")
    os.environ["BESTSELLER__DATABASE__URL"] = target_url.render_as_string(hide_password=False)
    validate = sql_guard(target)
    event.listen(Engine, "connect", validate)
    try:
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            alembic_command.upgrade(cfg, "head")
    finally:
        event.remove(Engine, "connect", validate)
        if previous is None:
            os.environ.pop("BESTSELLER__DATABASE__URL", None)
        else:
            os.environ["BESTSELLER__DATABASE__URL"] = previous
    report["schema_upgraded"] = True


async def inventory(engine):
    counts = {}
    async with engine.connect() as c:
        for table in ("projects", "chapters", "chapter_draft_versions", "scene_draft_versions",
                      "canon_facts", "timeline_events", "planning_artifact_versions", "workflow_runs", "llm_runs"):
            counts[table] = int(await c.scalar(text(f"SELECT count(*) FROM {table}")))
        promotion = [dict(r) for r in (await c.execute(text(
            "SELECT promotion_state, is_current, count(*) AS count FROM chapter_draft_versions "
            "GROUP BY promotion_state,is_current ORDER BY promotion_state,is_current"))).mappings()]
        projects = [dict(r) for r in (await c.execute(text("""
            SELECT p.id::text AS project_id, p.slug, p.genre,
              (SELECT count(*) FROM chapters c WHERE c.project_id=p.id) AS chapters,
              (SELECT count(*) FROM chapter_draft_versions v WHERE v.project_id=p.id) AS chapter_versions,
              (SELECT count(*) FROM chapter_draft_versions v WHERE v.project_id=p.id AND v.is_current) AS current_drafts,
              (SELECT count(*) FROM chapter_draft_versions v WHERE v.project_id=p.id AND v.promotion_state='promoted') AS promoted,
              (SELECT count(*) FROM canon_facts f WHERE f.project_id=p.id) AS canon,
              (SELECT count(*) FROM timeline_events t WHERE t.project_id=p.id) AS timeline,
              (SELECT count(*) FROM scene_draft_versions s WHERE s.project_id=p.id) AS scene_versions
            FROM projects p ORDER BY p.id
        """))).mappings()]
        versions = [dict(r) for r in (await c.execute(text(
            "SELECT project_id::text, chapter_id::text, count(*) AS versions, "
            "min(version_no) AS min_version, max(version_no) AS max_version "
            "FROM chapter_draft_versions GROUP BY project_id,chapter_id ORDER BY project_id,chapter_id"))).mappings()]
        associations = {}
        for table in ("llm_runs", "workflow_runs"):
            associations[table] = dict((await c.execute(text(f"SELECT count(*) AS total, "
                f"count(project_id) AS with_project_id FROM {table}"))).mappings().one())
        revision = list(await c.scalars(text("SELECT version_num FROM alembic_version")))
    return {"counts": counts, "chapter_promotion_distribution": promotion,
            "projects": projects, "chapter_version_distribution": versions,
            "historical_attribution": associations, "revision": revision}


async def snapshot(engine):
    """Return hashes only; manuscript bytes and raw metadata stay inside PG."""
    out = {}
    async with engine.connect() as c:
        for table in ("chapter_draft_versions", "scene_draft_versions", "canon_facts", "timeline_events",
                      "planning_artifact_versions", "workflow_runs", "llm_runs"):
            row = (await c.execute(text(f"SELECT count(*) AS count, md5(coalesce("
                f"string_agg(md5(to_jsonb(t)::text), '' ORDER BY id), '')) AS full_row_fingerprint FROM {table} t"))).mappings().one()
            out[table] = dict(row)
        out["draft_rows"] = {str(r["id"]): dict(r) for r in (await c.execute(text(
            "SELECT id::text, project_id::text, md5(to_jsonb(t)::text) AS full_row_hash, "
            "md5(content_md) AS body_hash, octet_length(content_md) AS body_bytes "
            "FROM chapter_draft_versions t ORDER BY id"))).mappings()}
        out["projects"] = {str(r["id"]): r["full_row_hash"] for r in (await c.execute(text(
            "SELECT id::text, md5(to_jsonb(t)::text) AS full_row_hash FROM projects t ORDER BY id"))).mappings()}
    return out


def project_payload(project):
    return deepcopy([project.metadata_json or {}, project.invariants_json or {}])


def summarize(result, detector):
    return {k: result[k] for k in ("policy", "changed", "unresolved_paths", "retired_reference_keys", "before_hash", "after_hash")} | {
        "receipt_categories": sorted(result["receipts"]), "detector_repairs": detector}


async def verify_project(engine, pid):
    from uuid import UUID
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        project = await s.get(ProjectModel, UUID(pid))
        original = project_payload(project)
        dry = migrate_project_prompt_state(project, apply=False)
        detector_dry = await migrate_detector_insertions(s, project, apply=False)
        require(project_payload(project) == original, "dry_run_changed_project")
        await s.rollback()
    async with factory() as s, s.begin():
        project = await s.get(ProjectModel, UUID(pid), with_for_update=True)
        applied = migrate_project_prompt_state(project, apply=True)
        detector_first = await migrate_detector_insertions(s, project, apply=True)
        require(dry == applied and detector_dry == detector_first, "dry_apply_disagreement")
    async with factory() as s:
        project = await s.get(ProjectModel, UUID(pid))
        after = project_payload(project)
        meta = project.metadata_json
        for key in applied["retired_reference_keys"]:
            require(key not in meta and valid_reference_value(project, key) is None, "old_cache_still_authoritative")
            require(any(row.get("source_values", {}).get(key) == original[0][key]
                        for row in (meta.get("superseded_prompt_sources") or {}).values()),
                    "retired_cache_original_not_archived")
        if "invariants" in applied["receipts"]:
            require(any(row.get("original_invariants") == original[1]
                        for row in (meta.get("framework_state_migrations") or {}).values()),
                    "original_invariants_not_archived")
    async with factory() as s, s.begin():
        project = await s.get(ProjectModel, UUID(pid), with_for_update=True)
        again = migrate_project_prompt_state(project, apply=True)
        detector_again = await migrate_detector_insertions(s, project, apply=True)
        require(not again["changed"] and detector_again == detector_first, "second_apply_not_idempotent")
    async with factory() as s:
        project = await s.get(ProjectModel, UUID(pid))
        require(project_payload(project) == after, "second_pg_readback_drift")
    return {"project_id": pid, "dry_run": summarize(dry, detector_dry),
            "first_apply": summarize(applied, detector_first), "second_apply": summarize(again, detector_again),
            "original_state_sha256": content_hash(original), "persisted_state_sha256": content_hash(after),
            "retired_caches_archived_and_deauthorized": len(applied["retired_reference_keys"]),
            "separate_transactions_and_readbacks": True}


async def run():
    run_id = datetime.now(timezone.utc).strftime("%H%M%S") + "-" + uuid4().hex[:10]
    report = {"run_id": run_id, "status": "RUNNING", "requested_database": BASE,
              "started_at": datetime.now(timezone.utc).isoformat(), "checks": []}
    engine = None
    try:
        records = json.loads(INVENTORY.read_text())["daily_preserved"]
        entry = next(r for r in records if (ROOT / r["preserved_copy"]).resolve() == DUMP.resolve())
        with DUMP.open("rb") as f:
            digest = hashlib.file_digest(f, "sha256").hexdigest()
        require(digest == entry["sha256"] and DUMP.stat().st_size == entry["bytes"], "backup_hash_or_size_mismatch")
        report["backup"] = {"preserved_copy": str(DUMP.relative_to(ROOT)), "sha256": digest,
                            "bytes": DUMP.stat().st_size, "inventory_verified": True}
        base_url = make_url(load_settings().database.url)
        target_url = await asyncio.to_thread(provision_and_restore, base_url, report)
        await asyncio.to_thread(upgrade_restored_schema, target_url, report)
        engine = create_async_engine(target_url, echo=False, hide_parameters=True, connect_args={
            "server_settings": {"application_name": "bestseller_historical_restore_probe",
                                "statement_timeout": "60000", "lock_timeout": "5000"}})
        event.listen(engine.sync_engine, "connect", sql_guard(target_url.database))
        async with engine.connect() as c:
            report["actual_database"] = await c.scalar(text("SELECT current_database()"))
            report["server_version"] = await c.scalar(text("SHOW server_version"))
        report["before"] = await inventory(engine)
        require(report["before"]["counts"]["projects"] > 0, "historical_dump_contains_no_projects")
        before = await snapshot(engine)
        report["before_fingerprints"] = {k: v for k, v in before.items() if k not in {"draft_rows", "projects"}}
        # Prefer two different genres with manuscripts and legacy migration work.
        factory = async_sessionmaker(engine, expire_on_commit=False)
        candidates = []
        async with factory() as s:
            for project in await s.scalars(select(ProjectModel).order_by(ProjectModel.id)):
                preview = migrate_project_prompt_state(project, apply=False)
                bodies = sum(v["project_id"] == str(project.id) for v in before["draft_rows"].values())
                score = 100 * len(preview["retired_reference_keys"]) + 10 * int(preview["changed"]) + min(bodies, 9)
                candidates.append((score, str(project.id), project.genre))
        candidates.sort(reverse=True)
        selected = [candidates[0]]
        distinct = [c for c in candidates[1:] if c[2] != selected[0][2]]
        if distinct:
            selected.append(distinct[0])
        elif len(candidates) > 1:
            selected.append(candidates[1])
        selected_ids = {c[1] for c in selected}
        report["selected_projects"] = [{"project_id": c[1], "genre": c[2], "selection_score": c[0]} for c in selected]
        report["source_migrations"] = []
        for _, pid, _ in selected:
            try:
                evidence = await verify_project(engine, pid)
                report["source_migrations"].append(evidence)
                report["checks"].append({"name": "historical_source_migration_" + pid, "status": "PASS"})
            except Exception as exc:
                report["checks"].append({"name": "historical_source_migration_" + pid,
                                          "status": "FAIL", "error": safe_error(exc)})
            print("historical project migration: " + report["checks"][-1]["status"], flush=True)
        after = await snapshot(engine)
        report["after"] = await inventory(engine)
        report["after_fingerprints"] = {k: v for k, v in after.items() if k not in {"draft_rows", "projects"}}
        report["original_draft_hashes"] = before["draft_rows"]
        checks = {
            "every_original_draft_full_row_and_body_preserved": all(after["draft_rows"].get(k) == v for k, v in before["draft_rows"].items()),
            "unselected_project_full_rows_unchanged": all(after["projects"].get(k) == v for k, v in before["projects"].items() if k not in selected_ids),
            "canon_timeline_scene_planning_workflow_llm_unchanged": all(before[k] == after[k] for k in (
                "scene_draft_versions", "canon_facts", "timeline_events", "planning_artifact_versions", "workflow_runs", "llm_runs")),
            "project_and_chapter_counts_unchanged": all(report["before"]["counts"][k] == report["after"]["counts"][k] for k in ("projects", "chapters")),
        }
        report["checks"].extend({"name": k, "status": "PASS" if v else "FAIL"} for k, v in checks.items())
        retired_count = sum(v["retired_caches_archived_and_deauthorized"] for v in report["source_migrations"])
        report["checks"].append({"name": "historical_legacy_cache_retirement_coverage",
            "status": "PASS" if retired_count else "NOT_APPLICABLE",
            "retired_count": retired_count,
            "reason": "archived_and_deauthorized" if retired_count else
                      "selected_historical_projects_have_no_unverified_reference_cache_keys; no synthetic cache injected"})
        report["new_candidate_count"] = len(set(after["draft_rows"]) - set(before["draft_rows"]))
        report["status"] = "PASS" if all(c["status"] in {"PASS", "NOT_APPLICABLE"} for c in report["checks"]) else "FAIL"
    except Exception as exc:
        report["status"], report["fatal_error"] = "FAIL", safe_error(exc)
    finally:
        if engine is not None:
            await engine.dispose()
    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    report["boundaries"] = ["No production data connection or production restore; new isolated test DB retained.",
        "No drop/clean/reuse of an existing DB; preserved dump is immutable and SHA verified.",
        "No LLM calls, manuscript text output, credentials, project resurrection, or consumer changes.",
        "Historical promotion states are reported as stored; no current reader/human acceptance is implied.",
        "Original backup and original draft rows are retained; only selected project source metadata is migrated."]
    prefix = REPORT_DIR / ("PG-HISTORY-" + run_id)
    with prefix.with_suffix(".json").open("x", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
        f.write("\n")
    lines = ["# 隔离历史恢复验证：" + report["status"], "", "Database: " + report.get("database", "not_created"),
        "", "Backup SHA256: " + report.get("backup", {}).get("sha256", "not_verified"), ""]
    if "before" in report:
        lines += ["恢复后分布：", "", json.dumps(report["before"]["counts"], ensure_ascii=False), "",
                  "按项目的章节、promoted、canon、timeline、版本分布见 JSON 的 before.projects 与 chapter_version_distribution。", ""]
    lines += ["- " + c["status"] + ": " + c["name"] for c in report["checks"]]
    if "fatal_error" in report:
        lines += ["", json.dumps(report["fatal_error"], ensure_ascii=False)]
    lines += ["", *["- " + b for b in report["boundaries"]], "", "结构化证据：" + prefix.name + ".json"]
    with prefix.with_suffix(".md").open("x", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(json.dumps({"status": report["status"], "database": report.get("database"),
                      "report": str(prefix.with_suffix('.json'))}), flush=True)
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True, choices=[BASE])
    parser.parse_args()
    raise SystemExit(asyncio.run(run()))
