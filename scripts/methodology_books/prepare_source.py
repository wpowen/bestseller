"""Prepare a writing-methodology book for safe methodology distillation.

This writes repo-safe manifests under ``data/methodology_books/<source-id>/``
and private raw/prompt payloads under ``.methodology_private/<source-id>/``.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

_THIS = Path(__file__).resolve()
_SRC = _THIS.parents[2] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from bestseller.services.methodology_book_distillation import (  # noqa: E402
    prepare_methodology_book,
    validate_methodology_book_package,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_path", type=Path)
    parser.add_argument("--source-id", required=True)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--private-root", type=Path, default=Path(".methodology_private"))
    parser.add_argument(
        "--dedupe-policy",
        choices=("skip", "error", "allow"),
        default="skip",
    )
    parser.add_argument(
        "--rights-status",
        default="user_supplied_for_analysis",
        choices=(
            "unknown",
            "user_supplied_for_analysis",
            "licensed",
            "public_domain",
            "do_not_process",
        ),
    )
    parser.add_argument("--language-hint", default=None)
    parser.add_argument(
        "--validate",
        action="store_true",
        help="Validate the generated repo package after preparation.",
    )
    args = parser.parse_args()

    private_root = (
        (args.repo_root / args.private_root).resolve()
        if not args.private_root.is_absolute()
        else args.private_root.resolve()
    )
    result = prepare_methodology_book(
        source_path=args.source_path,
        source_id=args.source_id,
        repo_root=args.repo_root.resolve(),
        private_root=private_root,
        dedupe_policy=args.dedupe_policy,
        rights_status=args.rights_status,
        language_hint=args.language_hint,
    )
    payload = result.to_dict()
    if args.validate and result.repo_dir:
        payload["validation_errors"] = list(
            validate_methodology_book_package(Path(result.repo_dir))
        )
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
