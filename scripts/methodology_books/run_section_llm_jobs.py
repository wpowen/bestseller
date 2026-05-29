"""Run LLM extraction for prepared writing-methodology book sections."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys

_THIS = Path(__file__).resolve()
_SRC = _THIS.parents[2] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from bestseller.services.methodology_book_distillation import (  # noqa: E402
    candidates_to_methodology_cards,
    load_methodology_candidates,
    write_methodology_cards_yaml,
)
from bestseller.services.methodology_book_llm import (  # noqa: E402
    load_methodology_candidate_schema,
    run_pending_methodology_section_jobs_parallel,
)
from bestseller.settings import get_settings, set_runtime_llm_profile  # noqa: E402


async def _run(
    *,
    package_dir: Path,
    repo_root: Path,
    private_root: Path,
    limit: int | None,
    max_section_chars: int | None,
    max_concurrency: int,
    job_timeout_seconds: float | None,
    write_cards: bool,
    min_card_confidence: float,
    runtime_profile: str | None,
) -> int:
    settings = get_settings()
    if runtime_profile:
        set_runtime_llm_profile(settings, runtime_profile)
    schema = load_methodology_candidate_schema(repo_root)
    private_errors_dir = private_root / "errors"

    processed, failures = await run_pending_methodology_section_jobs_parallel(
        package_dir=package_dir,
        repo_root=repo_root,
        private_root=private_root,
        settings=settings,
        schema=schema,
        max_concurrency=max_concurrency,
        limit=limit,
        max_section_chars=max_section_chars,
        private_errors_dir=private_errors_dir,
        job_timeout_seconds=job_timeout_seconds,
    )
    cards_written = False
    if write_cards:
        candidates_path = package_dir / "methodology_candidates.review.jsonl"
        deck = load_methodology_candidates(candidates_path)
        if any(candidate.confidence >= min_card_confidence for candidate in deck.candidates):
            cards = candidates_to_methodology_cards(
                deck,
                min_confidence=min_card_confidence,
            )
            write_methodology_cards_yaml(package_dir / "methodology_cards.review.yaml", cards)
            cards_written = True

    print(
        json.dumps(
            {
                "processed": processed,
                "failures": failures,
                "cards_written": cards_written,
                "candidate_output": str(package_dir / "methodology_candidates.review.jsonl"),
                "card_output": str(package_dir / "methodology_cards.review.yaml"),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 1 if failures else 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--package-dir",
        type=Path,
        required=True,
        help="Path to data/methodology_books/source-NNNN",
    )
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--private-root", type=Path, default=Path(".methodology_private"))
    parser.add_argument(
        "--runtime-profile",
        type=str,
        default=None,
        help="Optional runtime LLM profile to activate before running, e.g. xiaomi-mimo.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Max number of new section jobs to process (default: all pending).",
    )
    parser.add_argument(
        "--max-section-chars",
        type=int,
        default=None,
        help="Prompt sample cap per section (default: hard cap in service).",
    )
    parser.add_argument("--max-concurrency", type=int, default=2)
    parser.add_argument("--job-timeout-seconds", type=float, default=None)
    parser.add_argument(
        "--write-cards",
        action="store_true",
        help="Also promote current review candidates into methodology_cards.review.yaml.",
    )
    parser.add_argument("--min-card-confidence", type=float, default=0.65)
    args = parser.parse_args()

    repo_root = args.repo_root.resolve()
    private_root = (
        (repo_root / args.private_root).resolve()
        if not args.private_root.is_absolute()
        else args.private_root.resolve()
    )
    package_dir = args.package_dir.resolve()
    if not package_dir.is_dir():
        print(f"error: package dir not found: {package_dir}", file=sys.stderr)
        raise SystemExit(2)

    code = asyncio.run(
        _run(
            package_dir=package_dir,
            repo_root=repo_root,
            private_root=private_root,
            limit=args.limit,
            max_section_chars=args.max_section_chars,
            max_concurrency=args.max_concurrency,
            job_timeout_seconds=args.job_timeout_seconds,
            write_cards=args.write_cards,
            min_card_confidence=args.min_card_confidence,
            runtime_profile=args.runtime_profile,
        )
    )
    raise SystemExit(code)


if __name__ == "__main__":
    main()
