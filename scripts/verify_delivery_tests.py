"""Run the fixed offline delivery gate against an explicitly isolated test DB."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "tests/delivery_required.txt"


def snapshot() -> dict[str, str]:
    files = [MANIFEST]
    for folder in ("src/bestseller", "config", "migrations", "tests"):
        files.extend(
            path
            for path in (ROOT / folder).rglob("*")
            if path.is_file() and path.suffix in {".py", ".html", ".yaml", ".yml", ".txt"}
        )
    return {
        str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(set(files))
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report-dir", type=Path, required=True)
    args = parser.parse_args()
    sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tests")]
    from support.database_safety import selected_database_url

    target = selected_database_url()  # Errors omit URL/credentials.
    if target.get_backend_name() != "sqlite":
        parser.error("This offline gate requires an explicit isolated SQLite test target.")
    modules = [
        line.strip()
        for line in MANIFEST.read_text().splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    if not modules or len(modules) != len(set(modules)):
        parser.error("Delivery module manifest must be nonempty and unique.")
    for name in modules:
        if (
            not name.startswith("tests/unit/test_")
            or not name.endswith(".py")
            or ".." in Path(name).parts
            or not (ROOT / name).is_file()
        ):
            parser.error("Invalid required delivery module.")
    output = args.report_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    before = snapshot()
    env = dict(
        os.environ,
        PYTHONDONTWRITEBYTECODE="1",
        PYTEST_DISABLE_PLUGIN_AUTOLOAD="1",
        BESTSELLER_LINEAGE_PG="0",
        PYTHONPATH=os.pathsep.join(
            [str(ROOT / "src"), str(ROOT / "tests"), str(ROOT / "tests/unit")]
        ),
    )
    command = [
        str(ROOT / ".venv/bin/pytest"),
        "-p",
        "pytest_asyncio.plugin",
        "-p",
        "pytest_cov.plugin",
        "-p",
        "no:cacheprovider",
        "--no-cov",
        "-q",
        *modules,
        "--junitxml=" + str(output / "results.xml"),
    ]
    (output / "command.json").write_text(json.dumps(command, indent=2) + "\n")
    with (output / "pytest.log").open("w") as log:
        result = subprocess.run(
            command, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT, check=False
        )
    after = snapshot()
    changed = sorted(
        name for name in before.keys() | after.keys() if before.get(name) != after.get(name)
    )
    suites = list(ET.parse(output / "results.xml").iter("testsuite"))
    counts = {
        key: sum(int(suite.get(key, "0")) for suite in suites)
        for key in ("tests", "failures", "errors", "skipped")
    }
    passed = (
        result.returncode == 0
        and counts["tests"] > 0
        and not changed
        and not any(counts[key] for key in ("failures", "errors", "skipped"))
    )
    report = {
        "at": datetime.now(UTC).isoformat(),
        "passed": passed,
        "exit_code": result.returncode,
        "required_modules": modules,
        "counts": counts,
        "changed_during_test": changed,
        "source_sha256": before,
        "provider_calls": 0,
        "scope": "Offline delivery contracts; not PG or real-model acceptance",
    }
    (output / "verification.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({key: report[key] for key in ("passed", "counts", "changed_during_test")}))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
