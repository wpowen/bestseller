#!/usr/bin/env python3
"""Print lifecycle-quality findings in a reviewable format."""
# ruff: noqa: ANN401

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def _short(value: Any, limit: int = 220) -> str:
    if isinstance(value, (list, dict)):
        text = json.dumps(value, ensure_ascii=False)
    else:
        text = str(value)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--slug", required=True)
    parser.add_argument("--report", default=None)
    args = parser.parse_args()

    path = Path(args.report or f"output/{args.slug}/audits/lifecycle-quality/report.json")
    data = json.loads(path.read_text(encoding="utf-8"))
    findings = data.get("findings") or []

    print(f"report={path}")
    print(
        f"slug={data.get('slug')} "
        f"passed={data.get('passed')} "
        f"level={data.get('readiness_level')}"
    )
    print(f"\n=== {len(findings)} findings ===")
    for index, finding in enumerate(findings, start=1):
        if not isinstance(finding, dict):
            print(f"\nFinding {index}/{len(findings)}: {_short(finding)}")
            continue
        severity = finding.get("severity", "?")
        code = finding.get("code") or finding.get("type") or finding.get("id") or "?"
        print(f"\nFinding {index}/{len(findings)}: [{severity}] {code}")
        for key in (
            "message",
            "summary",
            "affected_chapters",
            "affected_volumes",
            "affected_scope",
            "evidence",
            "remediation",
        ):
            if finding.get(key):
                print(f"  {key}: {_short(finding[key])}")

    print(f"\n=== thresholds ({len(data.get('thresholds') or {})}) ===")
    for key, value in sorted((data.get("thresholds") or {}).items()):
        print(f"  {key} = {_short(value)}")

    print(f"\n=== metrics ({len(data.get('metrics') or {})}) ===")
    for key, value in sorted((data.get("metrics") or {}).items()):
        print(f"  {key} = {_short(value)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
