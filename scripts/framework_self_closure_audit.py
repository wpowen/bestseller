"""Generate the framework-level self-closure report for all novel categories."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SRC = _REPO_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from bestseller.services.framework_self_closure import (  # noqa: E402
    build_framework_self_closure_report,
    write_framework_self_closure_artifacts,
)


def _parse_categories(value: str | None) -> list[str] | None:
    if not value:
        return None
    return [part.strip() for part in value.split(",") if part.strip()]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--category", help="Comma-separated category keys; default is all canonical categories.")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=_REPO_ROOT / "data" / "framework_self_closure",
    )
    parser.add_argument(
        "--markdown",
        type=Path,
        default=_REPO_ROOT / "docs" / "framework-self-closure.md",
    )
    parser.add_argument("--json", action="store_true", help="Print JSON to stdout.")
    args = parser.parse_args()

    report = build_framework_self_closure_report(
        repo_root=_REPO_ROOT,
        categories=_parse_categories(args.category),
    )
    json_path, md_path = write_framework_self_closure_artifacts(
        report,
        output_dir=args.output_dir,
        markdown_path=args.markdown,
    )
    if args.json:
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(f"overall_status={report.overall_status}")
        print(f"json={json_path}")
        print(f"markdown={md_path}")
    return 0 if report.overall_status in {"closed", "repairable"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
