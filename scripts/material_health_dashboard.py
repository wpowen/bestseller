#!/usr/bin/env python
"""Build a markdown material health dashboard for an exported project."""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
import sys

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SRC = _REPO_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from bestseller.services.material_entity_registry import (  # noqa: E402
    EntityStatus,
    EntityType,
    build_entity_registry,
)
from bestseller.services.material_referential_integrity_gate import (  # noqa: E402
    evaluate_material_referential_integrity,
)


def main() -> int:
    args = _parse_args()
    project_dir = args.project_dir or Path("output") / args.slug
    registry = build_entity_registry(project_dir)
    integrity = evaluate_material_referential_integrity(project_dir)
    report = _render_report(args.slug, project_dir, registry, integrity)
    output = args.output or Path(".audit-reports") / (
        f"{datetime.now().date().isoformat()}-material-health-{args.slug}.md"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(report, encoding="utf-8")
    print(output)
    return 0


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--slug", required=True)
    parser.add_argument("--project-dir", type=Path)
    parser.add_argument("--before", type=Path, help="Reserved for before/after comparison reports.")
    parser.add_argument("--after", type=Path, help="Reserved for before/after comparison reports.")
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def _render_report(slug: str, project_dir: Path, registry: object, integrity: object) -> str:
    counts_by_type = {
        entity_type.value: sum(1 for record in registry.records if record.type == entity_type)
        for entity_type in EntityType
    }
    counts_by_status = {
        status.value: sum(1 for record in registry.records if record.status == status)
        for status in EntityStatus
    }
    chapter_count = len(list(project_dir.glob("chapter-*.md")))
    continuity_count = _continuity_count(project_dir)
    continuity_coverage = continuity_count / chapter_count if chapter_count else 0.0
    metrics = dict(integrity.metrics)
    novelty = _score(
        [
            _presence(project_dir / "story-bible" / "chapter_signature_audit.md"),
            _presence(Path("config/cultural_archetypes/urban_modern.yaml")),
            _presence(project_dir / "story-bible" / "kernels"),
            1.0 if counts_by_type.get("reveal", 0) else 0.0,
        ]
    )
    logic = _score([
        1.0 if counts_by_type.get("rule", 0) else 0.0,
        1.0 if counts_by_type.get("clue", 0) else 0.0,
        1.0 if integrity.verdict == "pass" else 0.0,
    ])
    readability = _score([continuity_coverage, 0.75 if chapter_count else 0.0])
    appeal = _score([novelty, 1.0 if counts_by_type.get("reveal", 0) else 0.0])
    lines = [
        f"# Material Health Dashboard — {slug}",
        "",
        f"- project_dir: `{project_dir}`",
        f"- generated_at: `{datetime.now().isoformat(timespec='seconds')}`",
        "",
        "## Material Inventory",
        "",
        "| type | count |",
        "| --- | ---: |",
        *[f"| {key} | {value} |" for key, value in sorted(counts_by_type.items())],
        "",
        "## Status Inventory",
        "",
        "| status | count |",
        "| --- | ---: |",
        *[f"| {key} | {value} |" for key, value in sorted(counts_by_status.items())],
        "",
        "## Referential Integrity",
        "",
        f"- verdict: `{integrity.verdict}`",
        f"- deprecated: `{metrics.get('deprecated_count', 0)}`",
        f"- unknown: `{metrics.get('unknown_count', 0)}`",
        f"- duplicate canonical: `{metrics.get('duplicate_canonical_count', 0)}`",
        "",
        "## Continuity Ledger",
        "",
        f"- chapter files: `{chapter_count}`",
        f"- ledger entries: `{continuity_count}`",
        f"- coverage: `{continuity_coverage:.2%}`",
        "",
        "## Four Goals",
        "",
        "| goal | score |",
        "| --- | ---: |",
        f"| 逻辑严谨 | {logic:.0%} |",
        f"| 可读性 | {readability:.0%} |",
        f"| 吸引力 | {appeal:.0%} |",
        f"| 新颖性 | {novelty:.0%} |",
    ]
    return "\n".join(lines) + "\n"


def _continuity_count(project_dir: Path) -> int:
    for path in (
        project_dir / "story-bible" / "continuity-ledger.yaml",
        project_dir / "story-bible" / "continuity-ledger.yml",
    ):
        if path.exists():
            return path.read_text(encoding="utf-8", errors="ignore").count("chapter_no:")
    return 0


def _presence(path: Path) -> float:
    return 1.0 if path.exists() else 0.0


def _score(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


if __name__ == "__main__":
    raise SystemExit(main())
