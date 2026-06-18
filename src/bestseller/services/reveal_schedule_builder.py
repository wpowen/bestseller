from __future__ import annotations

from pathlib import Path
import re
from typing import Any

import yaml

# No baked per-book reveal floors: pilot-specific tokens (镜影<name>/扣账人/…)
# were leaking one book's reveal schedule into every project. Reveal floors are
# now derived purely from each book's own series-bible via
# ``_extract_reveal_floor_terms`` below, so the scaffold is genre-neutral.
_DEFAULT_REVEAL_FLOORS: tuple[tuple[str, int, tuple[str, ...]], ...] = ()


def build_reveal_schedule(
    *,
    series_bible_text: str,
    ranking_profile_text: str = "",
    total_chapters: int = 300,
) -> dict[str, Any]:
    """Build a deterministic reveal schedule scaffold from known reveal tokens.

    This is intentionally conservative: it turns explicit high-risk terms into
    auditable reveal ids, then leaves later LLM/human passes to enrich labels.
    """

    combined = f"{series_bible_text}\n{ranking_profile_text}"
    reveals: list[dict[str, Any]] = []
    seen: set[str] = set()
    for reveal_id, earliest, tokens in _DEFAULT_REVEAL_FLOORS:
        if any(token in combined for token in tokens):
            reveals.append(
                {
                    "id": reveal_id,
                    "earliest_chapter": min(max(1, earliest), total_chapters),
                    "tokens": list(tokens),
                }
            )
            seen.add(reveal_id)
    for term in _extract_reveal_floor_terms(combined):
        reveal_id = _slugify_reveal_id(term)
        if reveal_id in seen:
            continue
        reveals.append(
            {
                "id": reveal_id,
                "earliest_chapter": max(6, min(total_chapters, total_chapters // 4)),
                "tokens": [term],
            }
        )
        seen.add(reveal_id)
    return {"schema_version": "reveal-schedule.v1", "reveals": reveals}


def build_reveal_schedule_for_book(book_dir: str | Path) -> dict[str, Any]:
    root = Path(book_dir)
    story_bible_dir = root / "story-bible"
    series_bible = _read_optional(story_bible_dir / "series-bible.md")
    ranking_profile = _read_optional(story_bible_dir / "ranking-capability-profile.md")
    total_chapters = _infer_total_chapters(root)
    return build_reveal_schedule(
        series_bible_text=series_bible,
        ranking_profile_text=ranking_profile,
        total_chapters=total_chapters,
    )


def write_reveal_schedule_for_book(book_dir: str | Path) -> Path:
    root = Path(book_dir)
    story_bible_dir = root / "story-bible"
    story_bible_dir.mkdir(parents=True, exist_ok=True)
    payload = build_reveal_schedule_for_book(root)
    path = story_bible_dir / "reveal-schedule.yaml"
    path.write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return path


def _extract_reveal_floor_terms(text: str) -> tuple[str, ...]:
    terms: list[str] = []
    for line in text.splitlines():
        if not any(marker in line.lower() for marker in ("reveal", "揭", "真相", "floor")):
            continue
        for quoted in re.findall(r"[「《\"]([^」》\"]{2,24})[」》\"]", line):
            terms.append(quoted.strip())
    return tuple(dict.fromkeys(term for term in terms if term))


def _slugify_reveal_id(term: str) -> str:
    ascii_text = re.sub(r"[^0-9A-Za-z]+", "_", term).strip("_").lower()
    if ascii_text:
        return ascii_text
    return "reveal_" + str(abs(hash(term)) % 100000)


def _read_optional(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def _infer_total_chapters(root: Path) -> int:
    chapter_numbers = []
    for path in root.glob("chapter-*.md"):
        match = re.search(r"chapter-(\d+)\.md$", path.name)
        if match:
            chapter_numbers.append(int(match.group(1)))
    return max(chapter_numbers, default=300)


__all__ = [
    "build_reveal_schedule",
    "build_reveal_schedule_for_book",
    "write_reveal_schedule_for_book",
]
