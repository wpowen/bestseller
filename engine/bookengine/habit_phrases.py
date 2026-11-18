"""Reference-only counts of sentence habits that read as machine-written (eval-74, 《扫雪剑起》).

Counts are an Inference for a reader to check, never a gate: the same pattern can be fine once.
"""

import re

PATTERNS = {
    "fear_desire_statement": r"最怕|最想",
    "injury_touch": r"按住[^。，！？\n]{0,4}(?:伤|肩|臂|腕)",
    "not_but": r"不是[^。！？\n“”]{1,20}?(?:而是|，是)",
    "knows_more": r"他(?:更|也)?(?:知道|清楚)",
}
# More than this many hits in one chapter marks the chapter for reading.
CHAPTER_LIMIT = {"fear_desire_statement": 1, "injury_touch": 2, "not_but": 3, "knows_more": 2}


def habit_phrase_checkup(chapters):
    rows = []
    for chapter in chapters:
        text = str(chapter.get("text") or "")
        counts = {kind: len(re.findall(pattern, text)) for kind, pattern in PATTERNS.items()}
        rows.append(dict(chapter=chapter.get("number"), counts=counts))
    totals = {kind: sum(row["counts"][kind] for row in rows) for kind in PATTERNS}
    heavy = {kind: [row["chapter"] for row in rows if row["counts"][kind] > CHAPTER_LIMIT[kind]] for kind in PATTERNS}
    return dict(
        reference_only=True,
        status="Inference",
        totals=totals,
        chapters_with_any={kind: sum(1 for row in rows if row["counts"][kind]) for kind in PATTERNS},
        heavy_chapters=heavy,
        limits=CHAPTER_LIMIT,
    )
