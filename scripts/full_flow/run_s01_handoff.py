"""Launch native S01 handoff in a new owned clone; 5 scripted SDK returns, 2 reentries."""

import datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess

from isolation import DOCKER, OWNER, REPORT, ROOT, preflight, verify
from sqlalchemy.engine import make_url

preflight()
plan = json.loads((REPORT / "ISOLATION.json").read_text())
verify(plan)
private = Path(plan["private_root"])
at = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
label = "s01-native-handoff-" + at
out = private / "evidence" / label
out.mkdir(mode=0o700)
source = Path(
    "/Volumes/MACSSD/owen-home/Documents/workspace/bestseller-v3-private-20261027-g0a/s01-snapshot-v2/inputs/artifacts"
)
assert source.is_dir() and not source.is_symlink()
# S01 must replay the captured normal settings, not today's defaults.
# These JSON configuration blocks contain routes/settings, never key values;
# all actual provider-key environment variables remain offline dummy values.
original_env = Path(
    "/Volumes/MACSSD/owen-home/Documents/workspace/bestseller-v3-private-20261027-g0a/runs/s01-red/application.env"
)
values = dict(
    line.split("=", 1)
    for line in original_env.read_text().splitlines()
    if "=" in line and not line.lstrip().startswith("#")
)
allowed = (
    "BESTSELLER__LLM",
    "BESTSELLER__GENERATION",
    "BESTSELLER__HOOK_ENGINE",
    "BESTSELLER__PIPELINE",
    "BESTSELLER__QUALITY",
    "BESTSELLER__RETRIEVAL",
)
selected = {key: values[key] for key in allowed}
base_values = dict(
    line.split("=", 1) for line in (private / "app-offline.env").read_text().splitlines()
)
source_url = make_url(base_values["BESTSELLER__DATABASE__URL"]).set(
    database="full_flow_s01_20261028_test_db"
)
for value in selected.values():
    json.loads(value)
legacy_env = out / "snapshot-settings-offline.env"
with open(legacy_env, "x", opener=lambda p, flags: os.open(p, flags, 0o600)) as stream:
    for key, value in selected.items():
        assert "\n" not in value
        stream.write(key + "=" + value + "\n")
    stream.write(
        "BESTSELLER__DATABASE__URL=" + source_url.render_as_string(hide_password=False) + "\n"
    )
name = "int-full-flow-s01-handoff-" + at.lower()
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
    "/tmp:rw,nosuid,nodev,size=256m",
    "--tmpfs",
    "/app:rw,nosuid,nodev,size=64m",
    "--workdir",
    "/fullflow",
    "--mount",
    f"type=bind,src={ROOT},dst=/fullflow,readonly",
    "--mount",
    f"type=bind,src={private},dst=/private",
    "--mount",
    f"type=bind,src={source},dst=/s01-input-artifacts,readonly",
    "--env-file",
    str(private / "app-offline.env"),
    "--env-file",
    str(legacy_env),
    "--env",
    "FULL_FLOW_S01_EVIDENCE=/private/evidence/" + label,
    "sha256:9685577fa4d1a017afabd1d37de48903631c35c1910479702ec2856dc93bb797",
    "python",
    "-B",
    "scripts/full_flow/s01_native_handoff.py",
]
with (out / "runner.log").open("w") as log:
    process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT)
    try:
        code = process.wait(timeout=900)
    except subprocess.TimeoutExpired:
        subprocess.run([DOCKER, "stop", "--time", "10", name], capture_output=True, timeout=20)
        process.wait(timeout=30)
        code = 124
assert subprocess.run([DOCKER, "container", "inspect", name], capture_output=True).returncode != 0
result = out / "RESULT.json"
if result.exists():
    row = json.loads(result.read_text())
else:
    row = {
        "status": "失败",
        "root_error_code": "S01_DIAGNOSTIC_ENTRYPOINT_OR_TIMEOUT",
        "root_exception_type": "HarnessError",
        "failure_differences": [
            {
                "field": "entrypoint.exit",
                "expected": {"type": "int", "summary": 0},
                "actual": {"type": "int", "summary": code},
            }
        ],
        "failed_workflow_type": "S01 read-only diagnostic",
        "failed_workflow_step": "entrypoint",
        "last_successful_llm_template": "Unknown: zero model calls",
        "script_consumed_index": 0,
    }
row.update(
    snapshot_normal_settings_sha256=hashlib.sha256(original_env.read_bytes()).hexdigest(),
    simulation="five normal SDK review calls in own DB clone, no real model; not full-book or full HTTP closure",
    container_exit_code=code,
    traceback_path=str(out / "runner.log"),
    model_calls=0,
    production_access=False,
    test_container_exited=True,
)
(REPORT / (label + ".json")).write_text(json.dumps(row, ensure_ascii=False, indent=2) + "\n")
print(
    json.dumps(
        {
            k: row.get(k)
            for k in ("status", "root_error_code", "container_exit_code", "traceback_path")
        },
        ensure_ascii=False,
    )
)
raise SystemExit(code or (0 if row.get("status") == "通过" else 1))
