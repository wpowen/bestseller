"""Read-only comparison of planned and observed chapter types."""

import json
from pathlib import Path

from .chapter_rhythm import chapter_type


def _plan_type(book, number):
    path = Path(book) / "writing_outlines" / f"{number:03d}.json"
    if not path.is_file():
        return "", "missing_outline"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return "", "invalid_outline"
    return chapter_type(value), "" if chapter_type(value) else "missing_planned_type"


def merge_rhythm_checkup(book, chapters, findings):
    """Add reference-only plan/body alignment without changing source files."""
    mismatches, unknown, matched = [], [], 0
    for chapter in chapters:
        number = chapter["number"]
        planned, reason = _plan_type(book, number)
        actual = chapter_type(chapter.get("meta", {}))
        if reason or not actual:
            unknown.append(
                dict(
                    chapter=number,
                    reason=reason or "missing_actual_type",
                    planned_type=planned or None,
                    actual_type=actual or None,
                    status="Unknown",
                )
            )
        elif planned == actual:
            matched += 1
        else:
            mismatches.append(
                dict(
                    chapter=number,
                    planned_type=planned,
                    actual_type=actual,
                    reason=f"保存的正文章型为{actual}，章纲要求{planned}",
                    status="Inference",
                )
            )
    result = dict(findings)
    rhythm = dict(result.get("chapter_rhythm", {}))
    rhythm["outline_alignment"] = dict(
        matched=matched,
        compared=matched + len(mismatches),
        mismatch_chapters=[row["chapter"] for row in mismatches],
        mismatches=mismatches,
        unknown=unknown,
        basis="saved writing outline type versus saved observed body type; reference only",
    )
    result["chapter_rhythm"] = rhythm
    return result
