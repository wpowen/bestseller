"""Audit reader-visible adjacent chapter logic for 《青囊不语问阴阳》."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SRC = _REPO_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from bestseller.services.reader_logic_gate import evaluate_reader_logic_seam  # noqa: E402

PROJECT_SLUG = "exorcist-detective-1778051012"


def _load_chapters(root: Path) -> list[tuple[int, Path, str]]:
    chapter_paths = sorted(root.glob("chapter-*.md"))
    chapters: list[tuple[int, Path, str]] = []
    for path in chapter_paths:
        try:
            number = int(path.stem.split("-")[-1])
        except ValueError:
            continue
        chapters.append((number, path, path.read_text(encoding="utf-8")))
    return chapters


def run(*, output_root: Path, write: bool) -> dict[str, Any]:
    project_root = output_root / PROJECT_SLUG
    chapters = _load_chapters(project_root)
    by_number = {number: text for number, _path, text in chapters}
    findings: list[dict[str, Any]] = []
    for number, _path, text in chapters:
        prev_text = by_number.get(number - 1)
        if prev_text is None:
            continue
        report = evaluate_reader_logic_seam(
            prev_text,
            text,
            prev_chapter=number - 1,
            current_chapter=number,
        )
        for finding in report.findings:
            findings.append(
                {
                    "code": finding.code,
                    "severity": finding.severity,
                    "prev_chapter": finding.prev_chapter,
                    "current_chapter": finding.current_chapter,
                    "message": finding.message,
                    "evidence": finding.evidence,
                }
            )

    payload = {
        "project_slug": PROJECT_SLUG,
        "verdict": "pass" if not findings else "blocked",
        "passed": not findings,
        "chapter_count": len(chapters),
        "finding_count": len(findings),
        "findings": findings,
    }
    if write:
        audit_dir = project_root / "audits"
        audit_dir.mkdir(parents=True, exist_ok=True)
        latest = audit_dir / "reader-logic-audit-latest.json"
        latest.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        payload["report_path"] = str(latest)
    return payload


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-root", default="output")
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    print(
        json.dumps(
            run(output_root=Path(args.output_root), write=args.write),
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
