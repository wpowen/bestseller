#!/usr/bin/env python3
"""Run the deterministic offline quality harness for a chapter bundle.

Example:
    python3 scripts/offline_quality_eval.py \
      --manifest tests/evals/fixtures/sample-manifest.json \
      --output output/offline-quality/sample
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
import json
from pathlib import Path

from bestseller.services.offline_quality_eval import evaluate_manifest, write_report


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--baseline", type=Path)
    args = parser.parse_args(argv)
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    report = evaluate_manifest(
        manifest,
        base_dir=args.manifest.parent,
        baseline_path=args.baseline,
    )
    paths = write_report(report, args.output)
    print(
        json.dumps(
            {
                "status": report["static_status"],
                "paths": {key: str(value) for key, value in paths.items()},
            },
            ensure_ascii=False,
        )
    )
    return 0 if report["static_status"] in {"pass", "warn"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
