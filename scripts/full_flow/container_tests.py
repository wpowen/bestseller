"""Owned Linux/PG tests, isolated from host production services and files."""

import argparse
import datetime
import json
from pathlib import Path
import subprocess
import sys

from isolation import DOCKER, OWNER, PRIVATE, REPORT, ROOT, preflight, verify

parser = argparse.ArgumentParser()
parser.add_argument("stage")
parser.add_argument("selection")
parser.add_argument("--database", choices=("bestseller_repair_test_20261015",))
args = parser.parse_args()
preflight()
plan = json.loads((REPORT / "ISOLATION.json").read_text())
verify(plan)
private = Path(plan["private_root"])
at = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
label = args.stage + "-" + at
evidence = private / "evidence" / label
evidence.mkdir(mode=0o700)
modules = [x for x in Path(args.selection).read_text().splitlines() if x and not x.startswith("#")]
assert modules and all((ROOT / x.split("::")[0]).exists() for x in modules)
database_env = []
if args.database:
    from sqlalchemy.engine import make_url
    import os
    original_env = dict(line.split("=", 1) for line in (private / "app-offline.env").read_text().splitlines())
    url = make_url(original_env["BESTSELLER__DATABASE__URL"])
    assert url.host == plan["pg"]
    override = evidence / "database.env"
    with open(override, "x", opener=lambda p, flags: os.open(p, flags, 0o600)) as stream:
        stream.write("BESTSELLER__DATABASE__URL=" + url.set(database=args.database).render_as_string(hide_password=False) + "\n")
    database_env = ["--env-file", str(override)]
name = "int-full-flow-test-" + at.lower()
command = [
    DOCKER,
    "run",
    "--rm",
    "--pull",
    "never",
    "--name",
    name,
    "--label",
    "fullflow.owner=" + OWNER,
    "--network",
    plan["network"],
    "--read-only",
    "--tmpfs",
    "/tmp:rw,nosuid,nodev,size=512m",
    "--tmpfs",
    "/app:rw,nosuid,nodev,size=64m",
    "--workdir",
    "/fullflow",
    "--mount",
    f"type=bind,src={ROOT},dst=/fullflow,readonly",
    "--mount",
    f"type=bind,src={private},dst=/private",
    "--mount",
    f"type=bind,src={PRIVATE / 'test-deps-v1'},dst=/opt/testdeps,readonly",
    "--env-file",
    str(private / "app-offline.env"),
    *database_env,
    "--env",
    "TMPDIR=/tmp",
    "--env",
    "FULL_FLOW_TEST_EVIDENCE=/private/evidence/" + label,
    "sha256:9685577fa4d1a017afabd1d37de48903631c35c1910479702ec2856dc93bb797",
    "python",
    "-m",
    "pytest",
    "-p",
    "pytest_cov.plugin",
    "-p",
    "pytest_asyncio.plugin",
    "-p",
    "scripts.full_flow.pytest_evidence",
    "--no-cov",
    "-q",
    "-o",
    "cache_dir=/tmp/pytest-cache",
    "--basetemp=/tmp/pytest-run",
    "--junitxml=/private/evidence/" + label + "/junit.xml",
    "--tb=short",
    *modules,
]
with (evidence / "pytest.log").open("w") as log:
    process = subprocess.Popen(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
    try:
        code = process.wait(timeout=1800)
    except subprocess.TimeoutExpired:
        subprocess.run([DOCKER, "stop", "--time", "10", name], capture_output=True, timeout=20)
        process.wait(timeout=30)
        code = 124
# A timeout cannot leave an active runner, and no early PASS is emitted.
active = subprocess.run([DOCKER, "container", "inspect", name], capture_output=True, text=True)
assert active.returncode != 0, "ISOLATED_TEST_CONTAINER_STILL_PRESENT"
row = {
    "schema": "full-flow-pg-test-run.v1",
    "stage": args.stage,
    "at_utc": at,
    "exit_code": code,
    "status": "通过" if code == 0 else "失败",
    "modules": modules,
    "production_access": False,
    "real_model_calls": 0,
    "traceback_path": str(evidence / "pytest.log"),
    "junit": str(evidence / "junit.xml"),
    "test_container_exited": True,
}
if code:
    import hashlib
    import xml.etree.ElementTree as ET

    rows = []
    if (evidence / "junit.xml").exists():
        for test in ET.parse(evidence / "junit.xml").iter("testcase"):
            failure = test.find("failure")
            if failure is None:
                failure = test.find("error")
            if failure is not None:
                msg = failure.get("message", "")
                rows.append(
                    {
                        "test": test.get("classname", "") + "::" + test.get("name", ""),
                        "root_exception_type": failure.get("type", "Unknown"),
                        "root_error_code": msg.split("\n")[0][:180]
                        if len(msg) < 180
                        else "sha256:" + hashlib.sha256(msg.encode()).hexdigest(),
                        "failure_differences": [
                            {
                                "field": test.get("name", ""),
                                "expected": {"type": "test_result", "summary": "PASS"},
                                "actual": {
                                    "type": failure.tag,
                                    "length": len(msg),
                                    "sha256": hashlib.sha256(msg.encode()).hexdigest(),
                                },
                            }
                        ],
                    }
                )
    detail = evidence / "failures.jsonl"
    if detail.exists():
        rows = [json.loads(line) for line in detail.read_text().splitlines()]
    row["failures"] = rows
    row["root_error_code"] = rows[0]["root_error_code"] if rows else "PYTEST_STARTUP_OR_TIMEOUT"
    row.update(
        failed_workflow_type="isolated PG test suite",
        failed_workflow_step=args.stage,
        last_successful_llm_template="Unknown: zero real model calls",
        script_consumed_index=0,
    )
(REPORT / (label + ".json")).write_text(json.dumps(row, ensure_ascii=False, indent=2) + "\n")
print(
    json.dumps(
        row if not code else {k: v for k, v in row.items() if k not in ("modules", "failures")},
        ensure_ascii=False,
    )
)
if not code:
    print((evidence / "pytest.log").read_text()[-500:])
sys.exit(code)
