from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

_THIS = Path(__file__).resolve()
_SRC = _THIS.parents[2] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from bestseller.services.distillation_assets import aggregate_distillation_packages


def main() -> None:
    parser = argparse.ArgumentParser(description="Aggregate distillation packages.")
    parser.add_argument("package_dirs", nargs="+", type=Path)
    parser.add_argument("--aggregate-key", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--format", choices=("text", "json"), default="text")
    args = parser.parse_args()

    report = aggregate_distillation_packages(
        args.package_dirs,
        output_dir=args.output_dir,
        aggregate_key=args.aggregate_key,
    )
    if args.format == "json":
        print(json.dumps(report.__dict__, ensure_ascii=False, indent=2))
    else:
        print(f"aggregate_key={report.aggregate_key}")
        print(f"output_dir={report.output_dir}")
        print(f"sources={len(report.source_ids)}")
        print(f"material_rows={report.material_rows}")
        print(f"mechanism_rows={report.mechanism_rows}")
        print(f"anti_copy_blocked_combinations={report.anti_copy_blocked_combinations}")
        print(f"grammar_state_variables={report.grammar_state_variables}")
        print(f"grammar_change_vectors={report.grammar_change_vectors}")
        for warning in report.warnings:
            print(f"WARNING: {warning}")
    raise SystemExit(1 if report.warnings else 0)


if __name__ == "__main__":
    main()

