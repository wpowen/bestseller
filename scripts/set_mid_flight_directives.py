"""Write mid-flight plotting directives into a project's metadata.

Why this exists
---------------
The 《道种破虚》 rescue plan needs to inject story-level constraints into
every future volume plan (卷 10-13) without modifying the 477 chapters
already written. ``generate_volume_plan`` reads
``project.metadata_json["mid_flight_directives"]`` and appends each entry
as a "HARD CONSTRAINT" at the bottom of the cast-expansion and chapter-
outline prompts so the LLM respects them as editorial mandates.

Usage
-----
    # Add directives
    python scripts/set_mid_flight_directives.py \\
        --slug xianxia-upgrade-1776137730 \\
        --add "卷 10-12: 通过回忆/书信自然回溯母亲早年经历，使第 250 章相遇有铺垫" \\
        --add "卷 10-13: 妹妹（宁微）作为支线视角逐步引入；正面亮相不晚于第 600 章"

    # List current directives
    python scripts/set_mid_flight_directives.py --slug xianxia-upgrade-1776137730 --list

    # Clear all directives
    python scripts/set_mid_flight_directives.py --slug xianxia-upgrade-1776137730 --clear

Idempotent: adding an already-present directive (exact string match) is a
no-op. The metadata_json JSONB column is updated in-place.

Exit codes
----------
0  success
2  project not found / no directives specified
1  unexpected error
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

_THIS = Path(__file__).resolve()
_SRC = _THIS.parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from sqlalchemy import select  # noqa: E402

from bestseller.infra.db.models import ProjectModel  # noqa: E402
from bestseller.infra.db.session import session_scope  # noqa: E402
from bestseller.settings import load_settings  # noqa: E402


async def run(
    slug: str,
    *,
    add: list[str],
    clear: bool,
    list_only: bool,
) -> int:
    settings = load_settings()
    async with session_scope(settings) as session:
        project = (
            await session.execute(
                select(ProjectModel).where(ProjectModel.slug == slug)
            )
        ).scalar_one_or_none()
        if project is None:
            print(f"[set_mid_flight_directives] project '{slug}' not found", file=sys.stderr)
            return 2

        metadata = dict(project.metadata_json or {})
        directives: list[str] = list(metadata.get("mid_flight_directives") or [])

        if list_only:
            if not directives:
                print(f"[set_mid_flight_directives] {slug}: no directives")
            else:
                print(f"[set_mid_flight_directives] {slug}: {len(directives)} directives")
                for idx, d in enumerate(directives, start=1):
                    print(f"  {idx}. {d}")
            return 0

        if clear:
            directives = []
            print(f"[set_mid_flight_directives] {slug}: cleared all directives")

        added = 0
        for directive in add:
            directive = directive.strip()
            if not directive:
                continue
            if directive not in directives:
                directives.append(directive)
                added += 1
                print(f"[set_mid_flight_directives] {slug}: added → {directive}")
            else:
                print(f"[set_mid_flight_directives] {slug}: skip (already present) → {directive}")

        if not clear and not add:
            print(
                "[set_mid_flight_directives] no action requested. "
                "Use --add, --clear, or --list.",
                file=sys.stderr,
            )
            return 2

        metadata["mid_flight_directives"] = directives
        project.metadata_json = metadata
        await session.flush()

        print(
            f"[set_mid_flight_directives] {slug}: saved {len(directives)} directives "
            f"(+{added} new)"
        )
        print(json.dumps(directives, ensure_ascii=False, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--slug", required=True, help="Project slug")
    parser.add_argument(
        "--add",
        action="append",
        default=[],
        metavar="DIRECTIVE",
        help="Directive to add (repeatable). No-op if already present.",
    )
    parser.add_argument(
        "--clear",
        action="store_true",
        help="Remove ALL existing directives before adding new ones.",
    )
    parser.add_argument(
        "--list",
        dest="list_only",
        action="store_true",
        help="Print current directives and exit.",
    )
    args = parser.parse_args(argv)
    return asyncio.run(
        run(
            args.slug,
            add=args.add,
            clear=args.clear,
            list_only=args.list_only,
        )
    )


if __name__ == "__main__":
    sys.exit(main())
