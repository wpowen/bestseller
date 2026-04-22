"""Summarize audit + scorecard JSON into a human-readable table."""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

SLUGS = [
    "female-no-cp-1776303225",
    "romantasy-1776330993",
    "superhero-fiction-1776147970",
    "superhero-fiction-1776301343",
    "xianxia-upgrade-1776137730",
]

root = Path(__file__).parent

print("=" * 100)
print("SCORECARD SUMMARY")
print("=" * 100)
print(
    f"{'slug':<32} {'score':>6} {'chapters':>9} {'missing':>7} {'blocked':>7} "
    f"{'len_cv':>6} {'cjk':>4} {'dialog':>6} {'pov':>4}"
)
print("-" * 100)
for slug in SLUGS:
    score_file = root / f"scorecard-{slug}.json"
    if not score_file.exists():
        print(f"{slug:<32}  [missing]")
        continue
    d = json.loads(score_file.read_text())
    print(
        f"{slug:<32} "
        f"{d['quality_score']:>6.1f} "
        f"{d['total_chapters']:>9d} "
        f"{d['missing_chapters']:>7d} "
        f"{d['chapters_blocked']:>7d} "
        f"{d['length_cv']:>6.3f} "
        f"{d['cjk_leak_chapters']:>4d} "
        f"{d['dialog_integrity_violations']:>6d} "
        f"{d['pov_drift_chapters']:>4d}"
    )

print()
print("=" * 100)
print("AUDIT FINDINGS BREAKDOWN BY CODE (per novel)")
print("=" * 100)

for slug in SLUGS:
    audit_file = root / f"audit-{slug}.json"
    if not audit_file.exists():
        print(f"\n{slug}: [missing]")
        continue
    d = json.loads(audit_file.read_text())
    findings = d.get("findings", [])
    if not findings:
        print(f"\n{slug}: CLEAN (0 findings)")
        continue

    code_counter: Counter[str] = Counter(f["code"] for f in findings)
    print(f"\n{slug} — {len(findings)} finding(s):")
    for code, count in code_counter.most_common():
        print(f"  {code:<30} {count:>4}")

    # Missing chapter numbers (for precise regen planning)
    gaps = sorted([f["chapter_no"] for f in findings if f["code"] == "CHAPTER_GAP"])
    if gaps:
        # Ranges for readability
        ranges = []
        start = gaps[0]
        prev = start
        for n in gaps[1:]:
            if n == prev + 1:
                prev = n
                continue
            ranges.append((start, prev))
            start = n
            prev = n
        ranges.append((start, prev))
        summary = ", ".join(
            f"{a}" if a == b else f"{a}-{b}" for a, b in ranges
        )
        print(f"  → missing chapters: {summary}")

    length_short = sorted(
        [f["chapter_no"] for f in findings if f["code"] == "LENGTH_UNDER"]
    )
    if length_short:
        print(f"  → short chapters (LENGTH_UNDER): {length_short[:20]}{' ...' if len(length_short) > 20 else ''}")

    cjk_leak = sorted(
        [f["chapter_no"] for f in findings if f["code"] == "LANG_LEAK_CJK_IN_EN"]
    )
    if cjk_leak:
        print(f"  → CJK leak chapters: {cjk_leak[:20]}{' ...' if len(cjk_leak) > 20 else ''}")
