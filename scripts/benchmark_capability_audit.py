"""Build the repo-safe benchmark capability audit artifacts.

The private sample set contains local paths/title keys and is written under
``.distillation_private`` by default. Repo-visible outputs contain only
anonymous source ids, categories, status, capability evidence, and roadmap data.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

_THIS = Path(__file__).resolve()
_SRC = _THIS.parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from bestseller.services.benchmark_capability_audit import (  # noqa: E402
    build_benchmark_audit_artifacts,
    write_benchmark_audit_artifacts,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--corpus-dir",
        type=Path,
        default=Path("/Volumes/书籍/Ebook"),
        help="Corpus root used to fill category coverage.",
    )
    parser.add_argument(
        "--high-score-dir",
        type=Path,
        default=Path("/Volumes/书籍/Ebook/高评分小说"),
        help="High-score seed directory.",
    )
    parser.add_argument("--target-count", type=int, default=40)
    parser.add_argument("--seed-limit", type=int, default=23)
    parser.add_argument(
        "--repo-output-dir",
        type=Path,
        default=Path("data/benchmark_capability"),
    )
    parser.add_argument(
        "--private-sample-path",
        type=Path,
        default=Path(".distillation_private/benchmark_sample_set.private.json"),
    )
    parser.add_argument(
        "--markdown-report-path",
        type=Path,
        default=Path("docs/benchmark-capability-assessment.md"),
    )
    parser.add_argument(
        "--validate-parse",
        action="store_true",
        help="Run full parser validation on selected samples. Slower, but checks chapter split.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print summary JSON without writing artifacts.",
    )
    args = parser.parse_args()

    artifacts = build_benchmark_audit_artifacts(
        corpus_dir=args.corpus_dir,
        high_score_dir=args.high_score_dir,
        target_count=args.target_count,
        seed_limit=args.seed_limit,
        validate_parse=args.validate_parse,
    )
    summary = {
        "generated_at": artifacts.generated_at,
        "sample_count": artifacts.repo_sample_set.get("actual_count"),
        "category_counts": artifacts.repo_sample_set.get("category_counts"),
        "privacy_violation_count": len(artifacts.privacy_violations),
        "repo_output_dir": str(args.repo_output_dir),
        "markdown_report_path": str(args.markdown_report_path),
        "private_sample_path": str(args.private_sample_path),
    }
    if artifacts.privacy_violations:
        summary["privacy_violations"] = list(artifacts.privacy_violations)
        print(json.dumps(summary, ensure_ascii=False, indent=2), file=sys.stderr)
        return 2

    if not args.dry_run:
        write_benchmark_audit_artifacts(
            artifacts,
            repo_output_dir=args.repo_output_dir,
            private_sample_path=args.private_sample_path,
            markdown_report_path=args.markdown_report_path,
        )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
