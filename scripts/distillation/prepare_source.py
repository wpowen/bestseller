"""Prepare an anonymized distillation package for a source novel.

The script writes two kinds of output:

* repository-safe derived metadata under ``data/distillation/<source_id>``
* private raw chunks and LLM payloads under ``.distillation_private/<source_id>``

The repository output must not include the source title, author, original path,
or raw chapter text. External LLM jobs should read private payloads and write
normalized JSON outputs back through the review process.
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

from bestseller.services.distillation_source_preparer import prepare_source  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_path", type=Path)
    parser.add_argument("--source-id", required=True)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--private-root", type=Path, default=Path(".distillation_private"))
    parser.add_argument(
        "--dedupe-policy",
        choices=("skip", "error", "allow"),
        default="skip",
        help="How to handle a source whose private title key is already registered.",
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
    parser.add_argument(
        "--genre-hint",
        default=None,
        help="Optional broad genre label for private LLM job routing; not a source title.",
    )
    args = parser.parse_args()

    private_root = (
        (args.repo_root / args.private_root).resolve()
        if not args.private_root.is_absolute()
        else args.private_root.resolve()
    )
    result = prepare_source(
        source_path=args.source_path,
        source_id=args.source_id,
        repo_root=args.repo_root.resolve(),
        private_root=private_root,
        dedupe_policy=args.dedupe_policy,
        rights_status=args.rights_status,
        genre_hint=args.genre_hint,
    )
    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
