"""Evaluate full lifecycle premium readiness for one generated book."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SRC = _REPO_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from bestseller.services.book_lifecycle_quality_gate import (  # noqa: E402
    build_lifecycle_quality_report_from_closure,
)
from bestseller.settings import load_settings  # noqa: E402


def _json_dump(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _read_json(path: Path) -> dict[str, object]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Expected JSON object in {path}")
    return data


def _default_closure_path(slug: str) -> Path:
    settings = load_settings()
    return (
        Path(settings.output.base_dir)
        / slug
        / "audits"
        / "book-quality-closure"
        / "report.json"
    )


def _default_output_path(slug: str) -> Path:
    settings = load_settings()
    return (
        Path(settings.output.base_dir)
        / slug
        / "audits"
        / "lifecycle-quality"
        / "report.json"
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--slug", required=True, help="Book output slug to evaluate.")
    parser.add_argument(
        "--closure-report",
        type=Path,
        default=None,
        help=(
            "Optional closure report JSON path. Defaults to "
            "output/<slug>/audits/book-quality-closure/report.json."
        ),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Optional lifecycle report output path.",
    )
    parser.add_argument(
        "--no-save",
        action="store_true",
        help="Print the report without writing output/<slug>/audits/lifecycle-quality/report.json.",
    )
    parser.add_argument("--json", action="store_true", help="Print full JSON report.")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    closure_path = args.closure_report or _default_closure_path(args.slug)
    closure = _read_json(closure_path)
    report = build_lifecycle_quality_report_from_closure(closure)
    payload = report.to_dict()
    output_path = args.output or _default_output_path(args.slug)
    if not args.no_save:
        _json_dump(output_path, payload)

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(
            (
                "{slug}: lifecycle={level} passed={passed} "
                "findings={findings} report={path}"
            ).format(
                slug=report.slug,
                level=report.readiness_level,
                passed=report.passed,
                findings=len(report.findings),
                path=output_path if not args.no_save else "not_saved",
            )
        )
        for finding in report.findings[:12]:
            print(
                f"- {finding.severity}/{finding.domain}/{finding.code}: "
                f"{finding.actual} expected {finding.expected}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
