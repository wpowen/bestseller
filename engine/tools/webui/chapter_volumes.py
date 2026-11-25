"""Read saved volume boundaries for the reader's table of contents."""

import re

from tools.chapter_outputs import load


def chapter_volumes(folder, bible, limit, safe):
    ranges = bible.get("volumes")
    ranges = list(ranges) if isinstance(ranges, list) else []
    # A continued book keeps the opening bible; completed volumes have their own checkpoints.
    memories = safe(folder / "volumes")
    completed = []
    for path in memories.glob("*.json"):
        match = re.fullmatch(r"([0-9]{2,})-([0-9]{3,})\.json", path.name)
        if match:
            row = load(safe(path))
            if (
                isinstance(row, dict)
                and row.get("fact_source") == "final_body"
                and row.get("number") == int(match[1])
                and row.get("end") == int(match[2])
            ):
                completed.append(row)
    ranges.extend(sorted(completed, key=lambda row: (row["number"], row["end"])))
    result = {}
    for row in ranges:
        if not isinstance(row, dict):
            continue
        volume, start, end = (row.get(field) for field in ("number", "start", "end"))
        if all(type(value) is int for value in (volume, start, end)) and volume > 0 and 0 < start <= end:
            result.update((chapter, volume) for chapter in range(start, min(end, limit) + 1))
    return result
