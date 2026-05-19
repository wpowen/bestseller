from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import shutil
from types import SimpleNamespace
from typing import Any

from bestseller.services.book_listing import (
    build_book_listing_profile,
    write_platform_title_workflow_artifacts,
)


def _load_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _text(value: object) -> str:
    return str(value or "").strip()


def _list(value: object) -> list[str]:
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, list | tuple | set):
        return [_text(item) for item in value if _text(item)]
    return []


def _project_from_metadata(slug: str, metadata: dict[str, Any]) -> SimpleNamespace:
    return SimpleNamespace(
        slug=slug,
        title=_text(metadata.get("primary_title") or metadata.get("title") or slug),
        genre=_text(metadata.get("primary_category") or metadata.get("genre") or "未分类"),
        sub_genre=_text(metadata.get("secondary_category") or metadata.get("sub_genre")),
        audience=_text(metadata.get("channel") or metadata.get("audience")),
        status=_text(metadata.get("serialization_status") or metadata.get("status") or "planning"),
        language=_text(metadata.get("language") or "zh-CN"),
        metadata_json=metadata,
    )


def _writing_profile_from_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    tags = _list(metadata.get("tags"))
    selling_points = _list(metadata.get("selling_points"))
    reader_promise = _list(metadata.get("reader_promise"))
    main_characters = metadata.get("main_characters")
    has_character = (
        isinstance(main_characters, list)
        and bool(main_characters)
        and isinstance(main_characters[0], dict)
    )
    first_character = (
        main_characters[0]
        if has_character
        else {}
    )
    return {
        "market": {
            "platform_target": _text(
                metadata.get("platform_target")
                or metadata.get("target_platform")
                or metadata.get("platform")
                or "全平台"
            ),
            "reader_promise": "; ".join(reader_promise),
            "selling_points": selling_points,
            "trope_keywords": tags[:8],
            "hook_keywords": tags[:4],
        },
        "character": {
            "protagonist_archetype": _text(
                first_character.get("identity") if isinstance(first_character, dict) else ""
            ),
            "protagonist_core_drive": _text(
                first_character.get("goal") if isinstance(first_character, dict) else ""
            ),
            "golden_finger": _text(metadata.get("golden_finger")),
        },
        "world": {"setting_tags": tags},
    }


def _iter_listing_dirs(output_base: Path) -> list[Path]:
    return sorted(
        path
        for path in output_base.glob("*/listing")
        if (path / "book-listing-metadata.json").exists()
    )


def regenerate(output_base: Path, *, backup_legacy_csv: bool = True) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    for listing_dir in _iter_listing_dirs(output_base):
        slug = listing_dir.parent.name
        metadata = _load_json(listing_dir / "book-listing-metadata.json")
        if not metadata:
            continue
        title_csv = listing_dir / "title-candidates.csv"
        legacy_csv = listing_dir / "title-candidates.legacy.csv"
        if backup_legacy_csv and title_csv.exists() and not legacy_csv.exists():
            shutil.copy2(title_csv, legacy_csv)

        profile = build_book_listing_profile(
            project=_project_from_metadata(slug, metadata),
            writing_profile=_writing_profile_from_metadata(metadata),
            story_bible=None,
            output_base_dir=output_base,
        )
        write_platform_title_workflow_artifacts(profile, listing_dir)
        labels = Counter(
            _text(item.get("display_label"))
            for item in profile.get("title_candidates", [])
            if isinstance(item, dict)
        )
        summaries.append(
            {
                "slug": slug,
                "title": profile.get("primary_title"),
                "platform": profile.get("title_workflow", {}).get("platform_label"),
                "candidate_count": len(profile.get("title_candidates") or []),
                "labels": dict(labels),
                "listing_dir": str(listing_dir),
            }
        )
    return summaries


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Regenerate platform-aware title workflows for output listings."
    )
    parser.add_argument(
        "output_base",
        nargs="?",
        default="output",
        help="Output base directory containing <slug>/listing folders.",
    )
    parser.add_argument(
        "--no-backup",
        action="store_true",
        help="Do not preserve an existing title-candidates.csv as title-candidates.legacy.csv.",
    )
    args = parser.parse_args()
    summaries = regenerate(Path(args.output_base).resolve(), backup_legacy_csv=not args.no_backup)
    print(json.dumps({"updated": summaries}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
