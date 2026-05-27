"""Audit file-backed canon pipeline state for 《青囊不语问阴阳》."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
import re
from typing import Any

PROJECT_SLUG = "exorcist-detective-1778051012"


def count_markdown_table_rows(path: Path) -> int:
    if not path.exists():
        return 0
    rows = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped.startswith("|") or not stripped.endswith("|"):
            continue
        if re.fullmatch(r"\|[\s:\-|]+\|", stripped):
            continue
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        if not cells or all(cell in {"", "---"} for cell in cells):
            continue
        if any(cell.lower() in {"chapter", "章末", "id", "线索"} for cell in cells):
            continue
        rows += 1
    return rows


def last_event_state_chapter(path: Path) -> int | None:
    if not path.exists():
        return None
    matches: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        if not cells:
            continue
        match = re.search(r"第\s*(\d+)\s*章", cells[0])
        if match:
            matches.append(match.group(1))
    if not matches:
        return None
    return max(int(match) for match in matches)


def last_clue_id(path: Path) -> str | None:
    if not path.exists():
        return None
    matches = re.findall(r"\bC-(\d{3,})\b", path.read_text(encoding="utf-8"))
    if not matches:
        return None
    highest = max(int(match) for match in matches)
    return f"C-{highest:03d}"


def validate_volume_plan(path: Path, expected_volume_count: int = 10) -> tuple[bool, list[str]]:
    findings: list[str] = []
    if not path.exists():
        return False, ["volume_plan_missing"]
    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if len(rows) != expected_volume_count:
        findings.append("volume_plan_wrong_volume_count")
    for row in rows:
        try:
            volume = int(row.get("volume") or 0)
            start = int(row.get("start_chapter") or 0)
            end = int(row.get("end_chapter") or 0)
        except ValueError:
            findings.append("volume_plan_non_numeric_range")
            continue
        expected_start = (volume - 1) * 50 + 1
        expected_end = volume * 50
        if start == 1 and end >= expected_volume_count * 50 and volume == 1:
            findings.append("volume_plan_collapsed_to_full_book")
        if volume and (start, end) != (expected_start, expected_end):
            findings.append(f"volume_{volume}_range_mismatch")
    return not findings, findings


def build_report(story_bible_dir: Path, *, expected_volume_count: int = 10) -> dict[str, Any]:
    continuity_path = story_bible_dir / "continuity-ledger.md"
    event_state_path = story_bible_dir / "event-state-ledger.md"
    clue_path = story_bible_dir / "clue-ledger.md"
    batch_path = story_bible_dir / "batch-queue.csv"
    volume_path = story_bible_dir / "volume-plan.csv"

    volume_valid, volume_findings = validate_volume_plan(volume_path, expected_volume_count)
    batch_status = "missing"
    if batch_path.exists():
        with batch_path.open(encoding="utf-8", newline="") as handle:
            batch_rows = list(csv.DictReader(handle))
        if not batch_rows:
            batch_status = "empty"
        elif len(batch_rows) == 1 and str(batch_rows[0].get("status") or "").lower() == "empty":
            batch_status = "empty"
        else:
            batch_status = "present"

    findings = list(volume_findings)
    continuity_rows = count_markdown_table_rows(continuity_path)
    event_last = last_event_state_chapter(event_state_path)
    clue_last = last_clue_id(clue_path)
    if continuity_rows == 0:
        findings.append("continuity_ledger_empty")
    if event_last is None or event_last < 75:
        findings.append("event_state_ledger_stale")
    if clue_last is None or clue_last < "C-026":
        findings.append("clue_ledger_stale")
    if batch_status == "empty":
        findings.append("batch_queue_empty")

    verdict = "pass" if not findings else "blocked"
    return {
        "project_slug": PROJECT_SLUG,
        "story_bible_dir": str(story_bible_dir),
        "verdict": verdict,
        "coverage": 1.0,
        "passed": verdict == "pass",
        "continuity_ledger_rows": continuity_rows,
        "event_state_last_chapter": event_last,
        "clue_last_id": clue_last,
        "batch_queue_status": batch_status,
        "volume_plan_valid": volume_valid,
        "findings": sorted(set(findings)),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--story-bible-dir", type=Path)
    parser.add_argument("--output-base", type=Path, default=Path("output"))
    args = parser.parse_args()

    story_bible_dir = args.story_bible_dir or args.output_base / PROJECT_SLUG / "story-bible"
    report = build_report(story_bible_dir)
    out_dir = args.output_base / PROJECT_SLUG / "audits"
    out_dir.mkdir(parents=True, exist_ok=True)
    report_path = out_dir / "canon-pipeline-state-latest.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({**report, "report_path": str(report_path)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
