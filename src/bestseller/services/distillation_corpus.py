"""Corpus-level helpers for distillation batch preparation."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from bestseller.services.distillation_book_parser import normalize_title_key

# Lower rank = preferred when multiple files map to the same normalized title.
# Prefer plain text over container formats so corpus dirs that are mostly .txt
# (e.g. ``Ebook/``) win over duplicate .epub/.mobi in other trees.
_FORMAT_RANK: dict[str, int] = {
    "txt": 0,
    "epub": 1,
    "md": 2,
    "markdown": 2,
    "html": 3,
    "htm": 3,
    "xhtml": 3,
    "mobi": 10,
    "azw3": 11,
}


def file_format_preference_key(path: Path) -> tuple[int, str]:
    """Sort key: preferred format first, then stable path tie-break."""
    ext = path.suffix.lower().removeprefix(".")
    if ext == "markdown":
        ext = "md"
    rank = _FORMAT_RANK.get(ext, 50)
    return (rank, str(path.resolve()).lower())


def normalized_title_group_key(path: Path) -> str:
    """Group sibling formats of the same work (filename stem, edition noise stripped)."""
    return normalize_title_key(path.stem) or path.stem.strip().lower()


def dedupe_corpus_paths_by_title(
    files: list[Path],
) -> tuple[list[Path], list[dict[str, str]]]:
    """Pick one file per normalized title; prefer EPUB/TXT over MOBI/AZW3.

    Returns (canonical_paths, sibling_records) where each sibling record has keys:
    ``title_key``, ``chosen_path``, ``skipped_path``.
    """
    groups: dict[str, list[Path]] = defaultdict(list)
    for path in files:
        groups[normalized_title_group_key(path)].append(path)

    canonical: list[Path] = []
    siblings: list[dict[str, str]] = []
    for title_key, group in sorted(groups.items(), key=lambda kv: kv[0].lower()):
        chosen = min(group, key=file_format_preference_key)
        canonical.append(chosen)
        for path in group:
            if path != chosen:
                siblings.append(
                    {
                        "title_key": title_key,
                        "chosen_path": str(chosen.resolve()),
                        "skipped_path": str(path.resolve()),
                    }
                )

    canonical.sort(key=lambda p: str(p.resolve()).lower())
    return canonical, siblings
