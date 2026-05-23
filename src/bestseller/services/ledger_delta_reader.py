"""Read recent story-bible ledgers for chapter prompt delta injection."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Literal

LedgerKind = Literal["event_state", "continuity", "clue"]


class LedgerDeltaStaleError(RuntimeError):
    """Raised when required recent ledger rows are missing."""


@dataclass(frozen=True)
class LedgerDeltaRow:
    kind: LedgerKind
    chapter_no: int
    text: str


@dataclass(frozen=True)
class LedgerDeltaBlock:
    chapter_no: int
    window_start: int
    window_end: int
    rows: tuple[LedgerDeltaRow, ...]

    def render(self) -> str:
        if not self.rows:
            return ""
        lines = [
            "【LedgerDelta: 最近五章状态增量】",
            f"- 覆盖章节: ch{self.window_start}-ch{self.window_end}",
        ]
        labels = {
            "event_state": "event-state",
            "continuity": "continuity",
            "clue": "clue",
        }
        for row in self.rows:
            lines.append(f"- {labels[row.kind]} ch{row.chapter_no}: {row.text}")
        return "\n".join(lines)


_LEDGER_FILES: dict[LedgerKind, str] = {
    "event_state": "event-state-ledger.md",
    "continuity": "continuity-ledger.md",
    "clue": "clue-ledger.md",
}
_CHAPTER_RE = re.compile(r"(?:第\s*)?(\d+)\s*(?:章|ch\b|CH\b)?")


def read_ledger_delta(
    story_bible_dir: str | Path,
    *,
    chapter_no: int,
    window: int = 5,
    require_event_state: bool = True,
) -> LedgerDeltaBlock:
    """Read recent ledger rows for ``chapter_no``.

    For chapter N the required window is max(1, N-window) through N-1.
    ``event-state-ledger.md`` is the blocking source because it carries
    state continuity; clue and continuity ledgers are included when present.
    """

    root = Path(story_bible_dir)
    window_end = chapter_no - 1
    window_start = max(1, chapter_no - window)
    if window_end < window_start:
        return LedgerDeltaBlock(chapter_no=chapter_no, window_start=1, window_end=0, rows=())

    all_rows: list[LedgerDeltaRow] = []
    event_chapters: set[int] = set()
    for kind, filename in _LEDGER_FILES.items():
        rows = _read_rows(root / filename, kind=kind)
        selected = [
            row for row in rows if window_start <= row.chapter_no <= window_end
        ]
        all_rows.extend(selected)
        if kind == "event_state":
            event_chapters = {row.chapter_no for row in selected}

    if require_event_state:
        expected = set(range(window_start, window_end + 1))
        missing = sorted(expected - event_chapters)
        if missing:
            missing_text = ", ".join(f"ch{item}" for item in missing)
            raise LedgerDeltaStaleError(
                "gate_error_ledger_stale: missing event-state ledger rows "
                f"for {missing_text}"
            )

    return LedgerDeltaBlock(
        chapter_no=chapter_no,
        window_start=window_start,
        window_end=window_end,
        rows=tuple(sorted(all_rows, key=lambda row: (row.chapter_no, row.kind))),
    )


def read_ledger_delta_block(story_bible_dir: str | Path, *, chapter_no: int) -> str:
    """Read and render the recent ledger delta block."""

    return read_ledger_delta(story_bible_dir, chapter_no=chapter_no).render()


def _read_rows(path: Path, *, kind: LedgerKind) -> tuple[LedgerDeltaRow, ...]:
    if not path.exists():
        return ()
    rows: list[LedgerDeltaRow] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or _is_table_rule(stripped):
            continue
        chapter_no = _extract_chapter_no(stripped)
        if chapter_no is None:
            continue
        rows.append(
            LedgerDeltaRow(
                kind=kind,
                chapter_no=chapter_no,
                text=_compact_markdown_row(stripped),
            )
        )
    return tuple(rows)


def _extract_chapter_no(text: str) -> int | None:
    if text.startswith("|"):
        cells = [cell.strip() for cell in text.strip("|").split("|")]
        for cell in cells[:3]:
            match = _CHAPTER_RE.search(cell)
            if match:
                return int(match.group(1))
    match = _CHAPTER_RE.search(text)
    return int(match.group(1)) if match else None


def _compact_markdown_row(text: str) -> str:
    if not text.startswith("|"):
        return text
    cells = [cell.strip() for cell in text.strip("|").split("|") if cell.strip()]
    return " / ".join(cells)


def _is_table_rule(text: str) -> bool:
    return bool(text.startswith("|") and set(text.replace("|", "").strip()) <= {"-", ":"})
