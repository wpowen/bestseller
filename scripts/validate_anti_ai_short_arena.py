#!/usr/bin/env python3
"""Validate the completion artifact for the bounded AI-flavor short arena."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    manifest_path = Path(args.manifest)
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    acceptance = payload.get("acceptance") or {}
    checks = {
        "arena_acceptance": bool(acceptance.get("passed")),
        "call_budget": int(payload.get("llm_calls") or 0) <= 50,
        "generation_matrix": len(payload.get("generation_records") or []) == 30,
        "primary_blind_reviews": len(payload.get("primary_verdicts") or []) == 15,
        "secondary_blind_reviews": len(payload.get("secondary_verdicts") or []) == 5,
        "all_judgements_parse": all(
            bool(item.get("parse_valid"))
            for item in [
                *(payload.get("primary_verdicts") or []),
                *(payload.get("secondary_verdicts") or []),
            ]
        ),
        "winner_not_control": payload.get("winner_id") != "production_control",
    }
    passed = all(checks.values())
    result = {
        "status": "passed" if passed else "failed",
        "passed": passed,
        "summary": (
            "short-sample AI-flavor strategy beat the production control"
            if passed
            else "arena did not satisfy every bounded validation condition"
        ),
        "manifest_path": str(manifest_path),
        "report_path": str(manifest_path.parent / "report.md"),
        "winner_id": payload.get("winner_id"),
        "checks": checks,
        "acceptance": acceptance,
    }
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
