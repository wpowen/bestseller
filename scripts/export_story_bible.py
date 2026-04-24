"""Export a project's story bible to disk for out-of-band review.

Why this exists
---------------
The Web UI shows the story bible interactively, but reviewers (humans or
ChatGPT-as-second-opinion) often want a flat, diffable, copy-pasteable
view. This CLI walks the project's DB state + latest planning artifacts
and writes 6 markdown files plus raw JSON dumps to:

::

    <out_dir>/<slug>/story-bible/
        characters.md
        world.md
        premise.md
        volume-plan.md
        plot-arcs.md
        writing-profile.md
        raw/*.json

This is read-only. The autowrite pipeline never reads these files; the DB
is canonical. Run as needed; re-running overwrites in place.

Usage
-----
    python scripts/export_story_bible.py \
        --project-slug xianxia-upgrade-1776137730

    # custom destination
    python scripts/export_story_bible.py \
        --project-slug xianxia-upgrade-1776137730 \
        --out-dir /tmp/bible-snapshot

Exit codes
----------
0  success
2  project not found / invalid args
1  unexpected error
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

_THIS = Path(__file__).resolve()
_SRC = _THIS.parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from bestseller.infra.db.session import session_scope  # noqa: E402
from bestseller.services.story_bible_export import (  # noqa: E402
    export_story_bible_to_disk,
)
from bestseller.settings import load_settings  # noqa: E402


async def run(slug: str, out_dir: Path) -> int:
    settings = load_settings()
    async with session_scope(settings) as session:
        try:
            dest = await export_story_bible_to_disk(
                session=session,
                project_slug=slug,
                output_root=out_dir,
            )
        except ValueError as exc:
            print(f"[export_story_bible] {exc}", file=sys.stderr)
            return 2
    print(f"[export_story_bible] {slug}: wrote {dest}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-slug", required=True, dest="slug")
    parser.add_argument(
        "--out-dir",
        default="output",
        type=Path,
        help="Root directory; story-bible is written under <out-dir>/<slug>/.",
    )
    args = parser.parse_args(argv)
    return asyncio.run(run(slug=args.slug, out_dir=args.out_dir.resolve()))


if __name__ == "__main__":
    sys.exit(main())
