from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

_THIS = Path(__file__).resolve()
_SRC = _THIS.parents[2] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from bestseller.services.distillation_assets import validate_distillation_package


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate an anonymized distillation package.")
    parser.add_argument("package_dir", type=Path)
    parser.add_argument("--format", choices=("text", "json"), default="text")
    args = parser.parse_args()

    report = validate_distillation_package(args.package_dir)
    if args.format == "json":
        print(json.dumps(report.__dict__, ensure_ascii=False, indent=2))
    else:
        status = "OK" if report.ok else "FAILED"
        print(f"{status}: {report.package_dir}")
        print(f"source_id={report.source_id}")
        print(f"material_rows={report.material_rows}")
        print(f"mechanism_rows={report.mechanism_rows}")
        print(f"volume_rows={report.volume_rows}")
        print(f"chapter_jobs={report.chapter_jobs}")
        for error in report.errors:
            print(f"ERROR: {error}")
    raise SystemExit(0 if report.ok else 1)


if __name__ == "__main__":
    main()

