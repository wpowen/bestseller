#!/usr/bin/env python3
"""Import a local markdown design document into a project's design dossier.

The document lands as a versioned ``creative_exploration`` planning artifact
(or any other type via ``--artifact-type``) so it shows up on
``/design/{slug}`` alongside pipeline-generated artifacts. Import is
idempotent: identical content reuses the existing approved version.

Usage:
    scripts/import_design_doc.py --project-slug zhaoshen-hr-v3-1781180702 \
        --file docs/story-bible-神仙都是我招的.md --title "故事圣经" \
        [--notes "人工讨论沉淀"] [--artifact-type creative_exploration]
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from bestseller.domain.enums import ArtifactType  # noqa: E402
from bestseller.domain.planning import PlanningArtifactCreate  # noqa: E402
from bestseller.infra.db.session import session_scope  # noqa: E402
from bestseller.services.projects import import_planning_artifact  # noqa: E402


async def run(args: argparse.Namespace) -> int:
    doc_path = Path(args.file)
    if not doc_path.is_file():
        print(f"error: file not found: {doc_path}", file=sys.stderr)
        return 1
    body = doc_path.read_text(encoding="utf-8")
    if not body.strip():
        print(f"error: file is empty: {doc_path}", file=sys.stderr)
        return 1

    content = {
        "format": "markdown",
        "title": args.title,
        "body": body,
        "source_path": str(doc_path),
        "_meta": {"input_hash": hashlib.sha256(body.encode("utf-8")).hexdigest()},
    }
    payload = PlanningArtifactCreate(
        artifact_type=ArtifactType(args.artifact_type),
        content=content,
        notes=args.notes,
    )

    async with session_scope() as session:
        version = await import_planning_artifact(session, args.project_slug, payload)
        await session.commit()
        print(
            f"imported '{args.title}' -> {args.project_slug} "
            f"[{version.artifact_type} v{version.version_no}, status={version.status}]"
        )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-slug", required=True)
    parser.add_argument("--file", required=True, help="markdown file to import")
    parser.add_argument("--title", required=True, help="human-readable document title")
    parser.add_argument("--notes", default=None)
    parser.add_argument(
        "--artifact-type",
        default=ArtifactType.CREATIVE_EXPLORATION.value,
        choices=[t.value for t in ArtifactType],
    )
    return asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
