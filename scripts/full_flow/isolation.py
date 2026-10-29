"""Create only owned, internal full-flow test resources. No production access."""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import time

ROOT = Path(__file__).resolve().parents[2]
PRIVATE = ROOT.parents[2] / "bestseller-par-private/int-full-flow-20261028"
REPORT = ROOT / "reports/full-flow-2026-10-28"
DOCKER = "/Applications/Docker.app/Contents/Resources/bin/docker"
OWNER = "codex-int-full-flow-20261028"


def command(args, *, check=True, timeout=30):
    result = subprocess.run([DOCKER, *args], capture_output=True, text=True, timeout=timeout)
    if check and result.returncode:
        # Docker's env-file contents and credentials never enter the report.
        raise RuntimeError(
            "ISOLATION_DOCKER_FAILED:"
            + args[0]
            + ":"
            + hashlib.sha256(result.stderr.encode()).hexdigest()
        )
    return result


def save(path, value):
    path.write_text(json.dumps(value, sort_keys=True, indent=2) + "\n")
    os.chmod(path, 0o600)


def env_file(path, values):
    if path.exists():
        actual = dict(line.split("=", 1) for line in path.read_text().splitlines())
        assert actual == {k: str(v) for k, v in values.items()}
        return
    with open(path, "x", opener=lambda p, flags: os.open(p, flags, 0o600)) as f:
        for key, value in values.items():
            assert "\n" not in str(value)
            f.write(f"{key}={value}\n")


def preflight():
    if shutil.disk_usage("/System/Volumes/Data").free < 5_000_000_000:
        raise RuntimeError("STOP_DISK_BELOW_5GB")
    command(["version", "--format", "{{.Server.Version}}"], timeout=15)


def inspect(kind, name):
    result = command([kind, "inspect", name])
    return json.loads(result.stdout)[0]


def verify(plan):
    network = inspect("network", plan["network"])
    assert network["Internal"] is True and network["Labels"]["fullflow.owner"] == OWNER
    assert set(network.get("Containers", {})) <= set(plan["container_ids"].values())
    for role in ("pg", "redis"):
        row = inspect("container", plan[role])
        assert row["Id"] == plan["container_ids"][role]
        assert row["Config"]["Labels"]["fullflow.owner"] == OWNER
        assert not row["HostConfig"]["PortBindings"] and not row["HostConfig"]["PublishAllPorts"]
        assert set(row["NetworkSettings"]["Networks"]) == {plan["network"]}
        assert {m["Name"] for m in row["Mounts"] if m["Type"] == "volume"} == {
            plan["volumes"][role]
        }
    return {
        "schema": "full-flow-isolation-readback.v1",
        "internal": True,
        "host_ports": 0,
        "production_connections": 0,
        "owner": OWNER,
        "database": plan["database"],
    }


def create(resume=None):
    preflight()
    if resume:
        path = Path(resume).resolve()
        assert path.is_relative_to(PRIVATE) and path.name == "PLAN.json"
        plan = json.loads(path.read_text())
        private = Path(plan["private_root"])
        assert private == path.parent and plan["owner"] == OWNER
        network = inspect("network", plan["network"])
        assert network["Internal"] and network["Labels"]["fullflow.owner"] == OWNER
        prefix = plan["network"].removesuffix("-internal")
        assert prefix.startswith("int-full-flow-")
        for role in ("pg", "redis"):
            name = prefix + "-" + role + "-data"
            volume = inspect("volume", name)
            assert volume["Labels"]["fullflow.owner"] == OWNER
            plan["volumes"][role] = name
        if (private / "pg.env").exists():
            values = dict(
                line.split("=", 1) for line in (private / "pg.env").read_text().splitlines()
            )
            password = values["POSTGRES_PASSWORD"]
        else:
            password = secrets.token_urlsafe(32)
    else:
        label = (
            datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            + "-"
            + secrets.token_hex(3)
        )
        prefix = "int-full-flow-" + label.lower()
        private = PRIVATE / label
        private.mkdir(parents=True, mode=0o700, exist_ok=False)
        for name in ("artifacts", "output", "evidence", "tmp", "config"):
            (private / name).mkdir(mode=0o700)
        shutil.copy2(ROOT / "config/default.yaml", private / "config/default.yaml")
        password = secrets.token_urlsafe(32)
        plan = {
            "schema": "full-flow-isolation-plan.v1",
            "owner": OWNER,
            "label": label,
            "private_root": str(private),
            "network": prefix + "-internal",
            "pg": prefix + "-pg",
            "redis": prefix + "-redis",
            "database": "full_flow_" + secrets.token_hex(6) + "_test_db",
            "volumes": {},
            "container_ids": {},
        }
        save(private / "PLAN.json", plan)
        command(
            [
                "network",
                "create",
                "--internal",
                "--label",
                "fullflow.owner=" + OWNER,
                plan["network"],
            ]
        )
        for role in ("pg", "redis"):
            name = prefix + "-" + role + "-data"
            command(["volume", "create", "--label", "fullflow.owner=" + OWNER, name])
            plan["volumes"][role] = name
            save(private / "PLAN.json", plan)
    env_file(
        private / "pg.env",
        {
            "POSTGRES_DB": plan["database"],
            "POSTGRES_USER": "postgres",
            "POSTGRES_PASSWORD": password,
        },
    )
    for role, image, args in (
        ("pg", "ankane/pgvector:latest", []),
        ("redis", "redis:7-alpine", ["redis-server", "--appendonly", "yes"]),
    ):
        image_id = command(["image", "inspect", "--format", "{{.Id}}", image]).stdout.strip()
        data = "/var/lib/postgresql/data" if role == "pg" else "/data"
        create_args = [
            "create",
            "--pull",
            "never",
            "--name",
            plan[role],
            "--label",
            "fullflow.owner=" + OWNER,
            "--network",
            plan["network"],
            "--mount",
            f"type=volume,src={plan['volumes'][role]},dst={data}",
        ]
        if role == "pg":
            create_args += ["--env-file", str(private / "pg.env")]
        if role not in plan["container_ids"]:
            identity = command([*create_args, image_id, *args]).stdout.strip()
            plan["container_ids"][role] = identity
        else:
            row = inspect("container", plan[role])
            assert (
                row["Id"] == plan["container_ids"][role]
                and row["Config"]["Labels"]["fullflow.owner"] == OWNER
            )
        save(private / "PLAN.json", plan)
        command(["start", plan[role]])
    deadline = time.monotonic() + 45
    while command(
        ["exec", plan["pg"], "pg_isready", "-U", "postgres", "-d", plan["database"]], check=False
    ).returncode:
        if time.monotonic() >= deadline:
            raise RuntimeError("ISOLATION_PG_START_TIMEOUT")
        time.sleep(0.5)
    command(
        [
            "exec",
            plan["pg"],
            "psql",
            "-U",
            "postgres",
            "-d",
            plan["database"],
            "-v",
            "ON_ERROR_STOP=1",
            "-c",
            "CREATE EXTENSION IF NOT EXISTS vector",
        ]
    )
    env_file(
        private / "app-offline.env",
        {
            "BESTSELLER__DATABASE__URL": f"postgresql+asyncpg://postgres:{password}@{plan['pg']}:5432/{plan['database']}",
            "BESTSELLER__REDIS__URL": f"redis://{plan['redis']}:6379/0",
            "BESTSELLER__ARTIFACT_STORE__LOCAL_DIR": "/private/artifacts",
            "BESTSELLER__ARTIFACT_STORE__RETAIN_PROMPT_PAYLOADS": "true",
            "BESTSELLER__OUTPUT__BASE_DIR": "/private/output",
            "BESTSELLER__BUDGET__EXECUTION_MAX_CALLS": "400",
            "BESTSELLER__BUDGET__EXECUTION_MAX_TOKENS": "20000000",
            "BESTSELLER__BUDGET__EXECUTION_DEADLINE_SECONDS": "86400",
            "MINIMAX_API_KEY": "full-flow-offline-fixture-not-real",
            "BESTSELLER_LLM_RUNTIME_PROFILE_PATH": "/private/absent-runtime-profile.json",
            "LITELLM_LOCAL_MODEL_COST_MAP": "true",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONPATH": (
                "/fullflow/src:/fullflow/tests:/fullflow/tests/unit:"
                "/fullflow/tests/support:/fullflow:/opt/testdeps"
            ),
            "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
            "COVERAGE_CORE": "pytrace",
        },
    )
    readback = verify(plan)
    save(private / "READBACK.json", readback)
    save(
        REPORT / "ISOLATION.json",
        {**plan, "readback": readback, "stage": "L0/L1 offline; no real model credentials"},
    )
    print(json.dumps({"plan": str(private / "PLAN.json"), "readback": readback}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["create"])
    parser.add_argument("--plan")
    args = parser.parse_args()
    create(args.plan)
