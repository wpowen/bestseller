#!/usr/bin/env python
from __future__ import annotations

import argparse
from pathlib import Path
import re
from typing import Any

import yaml


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Materialize deterministic forward state promises."
    )
    parser.add_argument("--book-dir", type=Path, required=True)
    parser.add_argument("--current-chapter", type=int, required=True)
    parser.add_argument("--window", type=int, default=5)
    args = parser.parse_args()

    story_bible_dir = args.book_dir / "story-bible"
    ledger_path = story_bible_dir / "event-state-ledger.md"
    schedule_path = story_bible_dir / "reveal-schedule.yaml"
    schedule = load_reveal_schedule_tokens(schedule_path)
    entries = draft_forward_promises(
        book_dir=args.book_dir,
        current_chapter=args.current_chapter,
        window=args.window,
        reveal_tokens=schedule,
    )
    append_forward_promises(ledger_path, entries)
    print(f"materialized {len(entries)} forward promises to {ledger_path}")
    return 0


def draft_forward_promises(
    *,
    book_dir: Path,
    current_chapter: int,
    window: int = 5,
    reveal_tokens: dict[str, list[str]] | None = None,
) -> list[dict[str, Any]]:
    recent_text = _recent_chapter_text(book_dir, current_chapter)
    anchors = _extract_anchors(recent_text)
    if not anchors:
        anchors = ["林渊", "青囊", "罗盘"]
    reveal_tokens = reveal_tokens or {}
    entries: list[dict[str, Any]] = []
    for offset in range(1, window + 1):
        chapter_no = current_chapter + offset
        anchor = anchors[(offset - 1) % len(anchors)]
        locked_tokens = [
            token
            for reveal_id, tokens in reveal_tokens.items()
            if _earliest_from_id(reveal_id) > chapter_no
            for token in tokens
        ]
        entries.append(
            {
                "chapter_no": chapter_no,
                "promise": f"{anchor}必须承接ch{current_chapter}已落地状态继续推进。",
                "rollback_forbidden": (
                    "不得回滚已确认死亡、获救、入账、借脸、物证归属或时钟位置。"
                ),
                "locked_reveal_tokens": locked_tokens[:5],
            }
        )
    return entries


def append_forward_promises(ledger_path: Path, entries: list[dict[str, Any]]) -> None:
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    text = (
        ledger_path.read_text(encoding="utf-8")
        if ledger_path.exists()
        else "# Event State Ledger\n"
    )
    if "## Forward Promises" not in text:
        text = text.rstrip() + "\n\n## Forward Promises (N+1..N+5)\n"
        text += "| 章 | 下一章只能怎么续 | 禁止回滚 | 锁定 reveal |\n"
        text += "| --- | --- | --- | --- |\n"
    existing = {entry["chapter_no"] for entry in _parse_forward_entries(text)}
    lines = [text.rstrip()]
    for entry in entries:
        if int(entry["chapter_no"]) in existing:
            continue
        locked = "、".join(entry.get("locked_reveal_tokens") or ()) or "-"
        lines.append(
            f"| 第 {entry['chapter_no']} 章 | {entry['promise']} | "
            f"{entry['rollback_forbidden']} | {locked} |"
        )
    ledger_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def load_reveal_schedule_tokens(path: Path) -> dict[str, list[str]]:
    if not path.exists():
        return {}
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        return {}
    out: dict[str, list[str]] = {}
    for item in payload.get("reveals") or ():
        if not isinstance(item, dict):
            continue
        reveal_id = str(item.get("id") or "")
        earliest = int(item.get("earliest_chapter") or 1)
        out[f"{reveal_id}@{earliest}"] = [str(token) for token in item.get("tokens") or ()]
    return out


def _recent_chapter_text(book_dir: Path, current_chapter: int) -> str:
    parts: list[str] = []
    for chapter_no in range(max(1, current_chapter - 2), current_chapter + 1):
        path = book_dir / f"chapter-{chapter_no:03d}.md"
        if path.exists():
            parts.append(path.read_text(encoding="utf-8")[:4000])
    return "\n".join(parts)


def _extract_anchors(text: str) -> list[str]:
    candidates = re.findall(r"[林苏王张陈钱周沈][一-龥]{1,3}|青囊|罗盘|铜钱|账印|回执", text)
    return list(dict.fromkeys(candidates))[:8]


def _earliest_from_id(reveal_key: str) -> int:
    if "@" not in reveal_key:
        return 1
    try:
        return int(reveal_key.rsplit("@", 1)[1])
    except ValueError:
        return 1


def _parse_forward_entries(text: str) -> list[dict[str, int]]:
    entries: list[dict[str, int]] = []
    if "## Forward Promises" not in text:
        return entries
    section = text.split("## Forward Promises", 1)[1]
    for match in re.finditer(r"第\s*([0-9]{1,4})\s*章", section):
        entries.append({"chapter_no": int(match.group(1))})
    return entries


if __name__ == "__main__":
    raise SystemExit(main())
