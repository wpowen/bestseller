"""Main-only backup/owned restore preparation; NOT executed or release approval.

Invocation (after Main independently verifies every authorized release gate):
  python scripts/full_flow/production_backup.py \
    --gate-manifest PRIVATE_JSON --gate-sha256 SHA256 \
    --isolation-plan EXISTING_PLAN --plan-sha256 SHA256 \
    --source-container-id FULL_ID --source-user ROLE --source-database DB \
    --execute-after-main-gates

Minimal input contract for Main's *new attestation*, not replacement receipts:
  main_prevalidated: true; candidate_sha: full commit;
  application_source_sha256: complete path/SHA map under APPLICATION_ROOTS;
  evidence: nonempty [{path: absolute ORIGINAL receipt/document, sha256: hash}];
  l1: {decision: "passed" | "documented_gt_one_day_skip", evidence_sha256: hash}.
For the authorized skip, also require l1.elapsed_seconds > 86400 and
l1.full_path_unverified=true. The referenced evidence must explicitly document
Main's decision. The tool binds those bytes; it does not infer success, inspect
old receipt schemas, manufacture PASS labels, or rewrite any existing receipt.
Main remains responsible for actual L0/all cores/L1-or-authorized-skip and all
other user gates. A descendant HEAD is allowed only with identical application
sources. Pin the manifest SHA after review; there is no implicit latest receipt.

Explicit 2026-10-29 17:10 pre-release backup mode replaces only the admission:
  use --execute-pre-release-backup instead of --execute-after-main-gates.
  Manifest: scope="production_backup_before_release_only", candidate_sha,
  application_source_sha256 (src/**/*.py and config/**), and
  authorization={path: original USER-AUTHORIZATION-20261029-1710.md, sha256}.
  The original authorization path/SHA are pinned below. No L0/L1 assertion is
  required or emitted. Source ancestry and src/config drift remain checked;
  dirty harness files do not block this separately authorized backup.

Reuse isolation.verify and RunState. All new files are private 0600/directories
0700 outside the checkout. Full custom pg_dump, schema and count/hash samples
share one exported READ ONLY snapshot. Restore keeps owner/ACL, creates ONLY a
fresh owned DB, and compares schema SQL + all table counts + five row hashes per
table. Missing roles/extensions fail closed; this tool never creates roles,
changes permissions, starts apps/containers, prunes, drops or deletes anything.
Only paired pg_dump restrict/unrestrict random tokens are schema-normalized.
No global-role backup or external-artifact backup; samples are not full data
hashes. Main alone executes later; static readiness is not restore evidence.
"""

from __future__ import annotations

import argparse
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import stat
import subprocess
import sys
import time
import traceback
from typing import Any, BinaryIO

from isolation import DOCKER, OWNER, PRIVATE, ROOT, command, verify

sys.path.insert(0, str(ROOT / "tests"))
from support.cp1_rehearsal import HarnessInvariantError, RunState, require, summary

APPLICATION_ROOTS = (
    "src", "config", "resources", "templates", "migrations", "scripts/full_flow",
    "pyproject.toml", "alembic.ini",
)
INTERNAL = Path("/System/Volumes/Data")
SHA = re.compile(r"[0-9a-f]{64}\Z")
NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,62}\Z")
BACKUP_AUTHORIZATION = (
    ROOT / "reports/full-flow-2026-10-28/USER-AUTHORIZATION-20261029-1710.md"
)
BACKUP_AUTHORIZATION_SHA = "fc24dd5f143bb14c0c458b70f04592338e9c764947c1ce422c47b5dfc330e969"


def file_sha(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def equal(field: str, expected: object, actual: object) -> None:
    require(type(actual) is type(expected) and expected == actual, field, expected, actual)


def exclusive(path: Path) -> BinaryIO:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    return os.fdopen(fd, "wb")


def save(path: Path, value: object) -> None:
    with exclusive(path) as stream:
        stream.write(json.dumps(value, ensure_ascii=False, sort_keys=True).encode() + b"\n")
        stream.flush()
        os.fsync(stream.fileno())


def pinned(path: Path, digest: str) -> bytes:
    require(path.is_absolute() and path.is_file() and not path.is_symlink(),
            "input.regular_absolute_file", True, False)
    raw = path.read_bytes()
    require(bool(SHA.fullmatch(digest)), "input.sha256.shape", "sha256", digest)
    equal("input.sha256", digest, hashlib.sha256(raw).hexdigest())
    return raw


def run(args: list[str], *, field: str, timeout: int = 30,
        data: bytes | None = None, stdin: BinaryIO | None = None,
        stdout: BinaryIO | None = None) -> bytes:
    result = subprocess.run(  # noqa: S603 -- fixed native binaries, no shell or passwords.
        args, input=data, stdin=stdin, stdout=stdout or subprocess.PIPE,
        stderr=subprocess.PIPE, timeout=timeout, check=False,
    )
    require(result.returncode == 0, field, {"exit": 0}, {
        "exit": result.returncode, "stderr": summary(result.stderr),
        "stdout": summary(result.stdout or b""),
    })
    return result.stdout or b""


def admit_backup_scope(gate: dict, decision_bytes: bytes | None, *,
                       pre_release: bool, after_main_gates: bool) -> dict:
    """Pure admission: the explicit backup grant never becomes release evidence."""
    require(type(pre_release) is bool and type(after_main_gates) is bool
            and pre_release != after_main_gates,
            "invocation.one_authorization_mode", "exactly one explicit mode",
            [pre_release, after_main_gates])
    if not pre_release:
        equal("gate.main_prevalidated", True, gate.get("main_prevalidated"))
        return {"mode": "after_main_gates", "deployment_authority": False}
    equal("authorization.scope", "production_backup_before_release_only", gate.get("scope"))
    ref = gate.get("authorization")
    equal("authorization.reference_type", "dict", type(ref).__name__)
    equal("authorization.path", str(BACKUP_AUTHORIZATION), ref.get("path"))
    equal("authorization.sha256", BACKUP_AUTHORIZATION_SHA, ref.get("sha256"))
    equal("authorization.bytes_type", "bytes", type(decision_bytes).__name__)
    equal("authorization.original_bytes_sha256", ref["sha256"],
          hashlib.sha256(decision_bytes).hexdigest())
    return {"mode": "pre_release_backup", "scope": gate["scope"],
            "authorization_sha256": ref["sha256"], "release_gates_verified": False,
            "l1": "not_evaluated", "deployment_authority": False}


def gate_binding(args: argparse.Namespace) -> dict:
    gate = json.loads(pinned(args.gate_manifest, args.gate_sha256))
    pre_release = getattr(args, "execute_pre_release_backup", False)
    decision = pinned(BACKUP_AUTHORIZATION, BACKUP_AUTHORIZATION_SHA) if pre_release else None
    admission = admit_backup_scope(gate, decision, pre_release=pre_release,
                                   after_main_gates=args.execute_after_main_gates)
    roots = ("src", "config") if pre_release else APPLICATION_ROOTS
    candidate = gate["candidate_sha"]
    require(bool(re.fullmatch(r"[0-9a-f]{40}", candidate)), "gate.commit", "full SHA", candidate)
    base = ["/usr/bin/git", "-C", str(ROOT)]
    run([*base, "merge-base", "--is-ancestor", candidate, "HEAD"], field="gate.commit_ancestry")
    equal("gate.application_drift", b"", run(
        [*base, "diff", "--name-only", candidate, "--", *roots], field="git.diff"))
    equal("gate.untracked_application", b"", run(
        [*base, "ls-files", "--others", "--exclude-standard", "--", *roots],
        field="git.untracked"))
    names = run([*base, "ls-tree", "-r", "--name-only", candidate, "--", *roots],
                field="git.source_files").decode().splitlines()
    actual = {}
    for name in names:
        path = ROOT / name
        require(path.is_file() and not path.is_symlink(), "gate.regular_source", True, False)
        if not pre_release or name.startswith("config/") or path.suffix == ".py":
            actual[name] = file_sha(path)
    require(bool(actual), "gate.source_fingerprints", "nonempty", actual)
    equal("gate.application_source_sha256", gate["application_source_sha256"], actual)
    if pre_release:
        return {"candidate_sha": candidate, "manifest_sha256": args.gate_sha256,
                "admission": admission}
    require(type(gate.get("evidence")) is list and bool(gate["evidence"]),
            "gate.original_evidence", "nonempty reference list", gate.get("evidence"))
    for ref in gate["evidence"]:
        pinned(Path(ref["path"]), ref["sha256"])  # Any existing receipt format; bytes untouched.
    l1 = gate["l1"]
    require(l1["evidence_sha256"] in {ref["sha256"] for ref in gate["evidence"]},
            "gate.l1.documented_evidence", "pinned original evidence", l1["evidence_sha256"])
    require(l1["decision"] in {"passed", "documented_gt_one_day_skip"},
            "gate.l1.decision", ["passed", "documented_gt_one_day_skip"], l1["decision"])
    if l1["decision"] == "documented_gt_one_day_skip":
        elapsed = l1.get("elapsed_seconds")
        require(type(elapsed) in (int, float) and elapsed > 86400,
                "gate.l1.elapsed_seconds", ">86400, explicitly attested by Main", elapsed)
        equal("gate.l1.full_path_unverified", True, l1.get("full_path_unverified"))
    return {"candidate_sha": candidate, "manifest_sha256": args.gate_sha256,
            "evidence_sha256": [ref["sha256"] for ref in gate["evidence"]], "l1": l1}


def disk_gate(out: Path) -> None:
    internal, external = shutil.disk_usage(INTERNAL).free, shutil.disk_usage(out).free
    require(internal >= 5_000_000_000, "disk.internal_bytes", ">=5000000000", internal)
    require(external > 20_000_000_000, "disk.external_bytes", ">20000000000", external)
    require(out.stat().st_dev != INTERNAL.stat().st_dev, "disk.external_device", True, False)


def pg_args(container: str, user: str, database: str, tool: str,
            *, readonly: bool, seconds: int = 1800) -> list[str]:
    options = ("-c timezone=UTC -c datestyle=ISO,YMD -c extra_float_digits=3 "
               "-c bytea_output=hex -c standard_conforming_strings=on "
               "-c lock_timeout=15000 -c statement_timeout=1800000 ")
    options += "-c default_transaction_read_only=" + ("on" if readonly else "off")
    return [DOCKER, "exec", "-i", container, "env", "-i", "PATH=/usr/local/bin:/usr/bin:/bin",
            "LC_ALL=C", "PGOPTIONS=" + options, "timeout", "--signal=TERM", "--kill-after=10s",
            str(seconds) + "s", tool, "--no-password", "--host=/var/run/postgresql",
            "--port=5432", "--username=" + user, "--dbname=" + database]


def sql(container: str, user: str, database: str, query: str, *, readonly: bool = True) -> bytes:
    return run([*pg_args(container, user, database, "psql", readonly=readonly),
                "-X", "-qAt", "-v", "ON_ERROR_STOP=1"],
               field="psql.read" if readonly else "psql.owned_database_create",
               timeout=1820, data=query.encode())


def literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def record_failure(state: RunState, error: BaseException, step: str) -> None:
    # RunState retains typed operands. Feed it a sanitized exception so its
    # private traceback also cannot echo credentials, argv, or original values.
    differences = getattr(error, "differences", None) or [{
        "field": step, "expected": summary("success"), "actual": summary(str(error)),
    }]
    code = str(error) if isinstance(error, HarnessInvariantError) else type(error).__name__
    safe = RuntimeError(code)
    safe.differences = differences
    state.failure(safe, step=step)
    state.data.update(root_exception_type=type(error).__name__,
                      safe_stack=[{"file": Path(f.filename).name, "line": f.lineno,
                                   "function": f.name}
                                  for f in traceback.extract_tb(error.__traceback__)])
    state.persist()


@contextmanager
def exported_snapshot(args: argparse.Namespace, state: RunState, out: Path) -> Iterator[str]:
    process, primary = None, None
    try:
        with exclusive(out / "snapshot-id.txt") as stream:
            process = subprocess.Popen(  # noqa: S603 -- fixed read-only psql, no shell.
                [*pg_args(args.source_container_id, args.source_user, args.source_database,
                          "psql", readonly=True, seconds=7200),
                 "-X", "-qAt", "-v", "ON_ERROR_STOP=1"],
                stdin=subprocess.PIPE, stdout=stream, stderr=subprocess.PIPE,
            )
            process.stdin.write(b"BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;\n"
                                b"SELECT pg_export_snapshot();\n")
            process.stdin.flush()
            until = time.monotonic() + 30
            while (not (out / "snapshot-id.txt").read_bytes().strip()
                   and process.poll() is None and time.monotonic() < until):
                time.sleep(0.1)
            snapshot = (out / "snapshot-id.txt").read_text().strip()
            require(bool(re.fullmatch(r"[0-9A-Fa-f]+-[0-9A-Fa-f]+-[0-9]+", snapshot)),
                    "snapshot.exported_id", "native snapshot ID", snapshot)
            equal("snapshot.holder_running", None, process.poll())
            yield snapshot
    except BaseException as error:
        primary = error
        raise
    finally:
        if process is not None:
            try:
                if process.poll() is None:
                    process.stdin.write(b"ROLLBACK;\n\\q\n")
                    process.stdin.flush()
                process.stdin.close()
                equal("snapshot.holder_exit", 0, process.wait(timeout=20))
                state.data["snapshot_stderr"] = summary(process.stderr.read())
                process.stderr.close()
                state.data["snapshot_holder_exited"] = True
            except BaseException as cleanup:
                state.data["snapshot_holder_exited"] = False
                state.data["snapshot_cleanup_error"] = summary(str(cleanup))
                if process.poll() is None:
                    # Host client only; the remote timeout is independently bounded.
                    process.terminate()
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=10)
                if primary is None:
                    raise


CENSUS_SQL = r"""
SELECT json_build_object('foreign_tables', count(*)) FROM pg_class c
JOIN pg_namespace n ON n.oid=c.relnamespace
WHERE c.relkind='f' AND n.nspname !~ '^pg_' AND n.nspname <> 'information_schema';
SELECT format(
  'SELECT json_build_object(''schema'',%L,''table'',%L,''kind'',%L,''populated'',%L::boolean,
   ''rows'',%s,''sample_sha256'',%s);',
  n.nspname,c.relname,c.relkind,c.relispopulated,
  CASE WHEN c.relispopulated THEN format('(SELECT count(*) FROM %I.%I)',n.nspname,c.relname)
       ELSE '0' END,
  CASE WHEN c.relispopulated THEN format(
   '(SELECT coalesce(json_agg(h ORDER BY h),''[]''::json) FROM
     (SELECT encode(sha256(convert_to(to_jsonb(t)::text,''UTF8'')),''hex'') h
      FROM %I.%I t ORDER BY h LIMIT 5) s)',n.nspname,c.relname)
       ELSE '''[]''::json' END)
FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
WHERE c.relkind IN ('r','p','m') AND n.nspname !~ '^pg_' AND n.nspname <> 'information_schema'
ORDER BY n.nspname,c.relname
\gexec
"""


def census(container: str, user: str, database: str, snapshot: str | None = None) -> list[Any]:
    query = "BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;\n"
    if snapshot:
        query += "SET TRANSACTION SNAPSHOT " + literal(snapshot) + ";\n"
    raw = sql(container, user, database, query + CENSUS_SQL + "\nROLLBACK;\n")
    rows = [json.loads(line) for line in raw.decode().splitlines() if line.strip()]
    require(bool(rows), "census.available", True, False)
    equal("census.foreign_tables", {"foreign_tables": 0}, rows[0])
    require(len(rows) > 1, "census.user_tables", ">0", len(rows) - 1)
    return rows[1:]


def schema_bytes(raw: bytes) -> bytes:
    # Recent pg_dump adds an unpredictable *client* security token. Preserve
    # every SQL byte; normalize only a single matched pair of those meta-lines.
    tokens = re.findall(rb"(?m)^\\(unrestrict|restrict) ([A-Za-z0-9]+)$", raw)
    if tokens:
        require(
            len(tokens) == 2
            and tokens[0][0] == b"restrict"
            and tokens[1] == (b"unrestrict", tokens[0][1]),
            "schema.psql_restrict_pair",
            "one matching pair",
            summary(raw),
        )
        raw = re.sub(rb"(?m)^\\(unrestrict|restrict) [A-Za-z0-9]+$", rb"\\\1 BACKUP_TOKEN", raw)
    return raw


def dump(args: list[str], path: Path, *, field: str) -> dict[str, Any]:
    with exclusive(path) as stream:
        run(args, field=field, timeout=1820, stdout=stream)
        stream.flush()
        os.fsync(stream.fileno())
    equal(field + ".mode", 0o600, stat.S_IMODE(path.stat().st_mode))
    require(path.stat().st_size > 0, field + ".size", ">0", path.stat().st_size)
    return {"sha256": file_sha(path), "size_bytes": path.stat().st_size}



def execute(args: argparse.Namespace, state: RunState, out: Path) -> None:
    state.checkpoint("main_gate_binding")
    require(args.execute_after_main_gates or getattr(args, "execute_pre_release_backup", False),
            "invocation.explicit_backup_ack", True, False)
    require(__debug__, "isolation.assertions_enabled", True, False)
    equal("worktree", "int-worktree", ROOT.name)
    for key in ("source_user", "source_database"):
        require(bool(NAME.fullmatch(getattr(args, key))), "source." + key, "SQL name", False)
    require(bool(SHA.fullmatch(args.source_container_id)), "source.container_id", "full ID", False)
    state.data["gate_binding"] = gate_binding(args)
    state.checkpoint("disk_docker_isolation_preflight")
    disk_gate(out)
    command(["version", "--format", "{{.Server.Version}}"], timeout=15)
    plan = json.loads(pinned(args.isolation_plan, args.plan_sha256))
    equal("isolation.owner", OWNER, plan.get("owner"))
    verify(plan)
    target = plan["container_ids"]["pg"]
    require(target != args.source_container_id, "restore.distinct_container", True, False)
    source = json.loads(command(["container", "inspect", args.source_container_id]).stdout)[0]
    equal("source.container.id", args.source_container_id, source["Id"])
    equal("source.running", True, source["State"]["Running"])
    equal("source.compose.service", "db",
          source["Config"]["Labels"].get("com.docker.compose.service"))
    require(not {m.get("Name") for m in source["Mounts"] if m["Type"] == "volume"}
            .intersection(plan["volumes"].values()), "restore.distinct_volumes", True, False)
    for tool in ("pg_dump", "pg_restore"):
        equal("postgres.client." + tool,
              command(["exec", args.source_container_id, tool, "--version"]).stdout,
              command(["exec", target, tool, "--version"]).stdout)
    identity = json.loads(sql(args.source_container_id, args.source_user, args.source_database,
        "SELECT json_build_object('database',current_database(),'user',current_user,"
        "'encoding',pg_encoding_to_char(encoding),'collate',datcollate,'ctype',datctype,"
        "'locale_provider',to_jsonb(d)->>'datlocprovider',"
        "'read_only',current_setting('transaction_read_only')) "
        "FROM pg_database d WHERE datname=current_database();"))
    equal("source.database", args.source_database, identity["database"])
    equal("source.user", args.source_user, identity["user"])
    equal("source.read_only", "on", identity["read_only"])
    require(identity["locale_provider"] in (None, "c"), "source.locale_provider",
            "libc (including older PostgreSQL)", identity["locale_provider"])
    state.checkpoint("consistent_source_backup")
    archive, schema = out / "database.pgdump", out / "source-schema.sql"
    with exported_snapshot(args, state, out) as snapshot:
        before = census(args.source_container_id, args.source_user, args.source_database, snapshot)
        save(out / "source-census.json", before)
        common = pg_args(args.source_container_id, args.source_user, args.source_database,
                         "pg_dump", readonly=True)
        disk_gate(out)
        state.data["archive"] = dump([*common, "--format=custom", "--snapshot=" + snapshot],
                                     archive, field="pg_dump.full")
        with archive.open("rb") as stream:
            equal("archive.magic", b"PGDMP", stream.read(5))
        dump([*common, "--schema-only", "--snapshot=" + snapshot], schema, field="pg_dump.schema")
    state.checkpoint("owned_restore_preflight")
    disk_gate(out)
    gate_binding(args)
    verify(plan)
    database = "full_flow_backup_" + secrets.token_hex(12) + "_test_db"
    state.data["restore"] = {"container_id": target, "database": database,
                             "created": False, "preserved_on_exit": True}
    state.checkpoint("create_owned_unique_database")
    # The generated name cannot collide silently: CREATE (never IF NOT EXISTS).
    sql(target, "postgres", plan["database"],
        'CREATE DATABASE "' + database + '" WITH TEMPLATE template0 ENCODING '
        + literal(identity["encoding"]) + " LC_COLLATE " + literal(identity["collate"])
        + " LC_CTYPE " + literal(identity["ctype"]) + ";", readonly=False)
    state.data["restore"]["created"] = True
    state.checkpoint("restore_full_archive")
    with archive.open("rb") as stream:
        run([*pg_args(target, "postgres", database, "pg_restore", readonly=False),
             "--exit-on-error", "--single-transaction"], field="pg_restore.full",
            timeout=1820, stdin=stream)
    state.checkpoint("exact_schema_and_count_verification")
    after = census(target, "postgres", database)
    save(out / "restored-census.json", after)
    equal("restore.relation_census", [(r["schema"], r["table"]) for r in before],
          [(r["schema"], r["table"]) for r in after])
    for index, (expected, actual) in enumerate(zip(before, after, strict=True)):
        for field in ("kind", "populated", "rows", "sample_sha256"):
            equal(f"restore.relation[{index}]." + field, expected[field], actual[field])
    restored_schema = out / "restored-schema.sql"
    dump([*pg_args(target, "postgres", database, "pg_dump", readonly=True), "--schema-only"],
         restored_schema, field="pg_dump.restored_schema")
    equal("restore.complete_schema_sql", schema_bytes(schema.read_bytes()),
          schema_bytes(restored_schema.read_bytes()))
    equal("archive.unchanged_sha256", state.data["archive"]["sha256"], file_sha(archive))
    gate_binding(args)
    verify(plan)
    disk_gate(out)
    state.checkpoint("backup_and_restore_verified", schema_exact=True, all_table_counts_exact=True,
                     sample_hashes_exact=True, sample_limit_per_table=5,
                     full_content_equality="Unknown: counts and samples only",
                     owner_acl_restored=True, deployment_authority=False)


class Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        require(False, "invocation.arguments", "complete explicit arguments", message)


def main() -> int:
    os.umask(0o077)
    state = None
    try:
        # Bootstrap evidence before all argument/disk/Docker/gate preflight.
        require(not PRIVATE.resolve().is_relative_to(ROOT.parents[1].resolve()),
                "evidence.outside_checkout", True, False)
        PRIVATE.mkdir(mode=0o700, parents=True, exist_ok=True)
        require(not PRIVATE.is_symlink(), "evidence.private_root_symlink", False, True)
        equal("evidence.private_mode", 0o700, stat.S_IMODE(PRIVATE.stat().st_mode))
        label = "production-backup-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        out = PRIVATE / (label + "-" + secrets.token_hex(6))
        out.mkdir(mode=0o700)
        state = RunState(out / "RESULT.json")
        state.data.update(schema="full-flow-production-backup.v1", production_sql_writes=0,
                          application_starts=0, model_calls=0, deployment_authority=False)
        parser = Parser(description=__doc__)
        for name in ("gate-manifest", "isolation-plan"):
            parser.add_argument("--" + name, type=Path, required=True)
        for name in ("gate-sha256", "plan-sha256", "source-container-id",
                     "source-user", "source-database"):
            parser.add_argument("--" + name, required=True)
        modes = parser.add_mutually_exclusive_group(required=True)
        modes.add_argument("--execute-after-main-gates", action="store_true")
        modes.add_argument("--execute-pre-release-backup", action="store_true")
        execute(parser.parse_args(), state, out)
    except BaseException as error:
        if state is None:
            print(json.dumps({"status": "失败", "root_exception_type": type(error).__name__,
                              "root_error_code": "BACKUP_JOURNAL_UNAVAILABLE",
                              "message": summary(str(error)), "evidence_persisted": False}))
            return 1
        record_failure(state, error, state.data.get("current_stage", "initializing"))
    state.finish(final_checks_complete=not state.failed)
    print(json.dumps({"status": state.data["status"], "receipt": str(state.path)}))
    return 1 if state.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
