"""Build per-novel repair plans respecting user-specified constraints.

Rules:
- xianxia-upgrade-1776137730: only repair chapters with chapter_no >= 213
  (chapters 1–212 are published to the platform and locked).
- Other 4 novels: repair every finding.
- Only LENGTH_UNDER / LENGTH_OVER findings are in scope for Phase 1
  (CJK/dialog/POV/naming findings are zero per the audit).
"""
from __future__ import annotations

import json
from pathlib import Path

root = Path(__file__).parent

PLANS = {
    "female-no-cp-1776303225":     {"min_chapter": 1},
    "romantasy-1776330993":        {"min_chapter": 1},
    "superhero-fiction-1776147970":{"min_chapter": 1},
    "superhero-fiction-1776301343":{"min_chapter": 1},
    "xianxia-upgrade-1776137730":  {"min_chapter": 213},  # 212 已上架锁定
}

ALLOWED_CODES = {"LENGTH_UNDER", "LENGTH_OVER"}

for slug, cfg in PLANS.items():
    audit = json.loads((root / f"audit-{slug}.json").read_text())
    min_ch = cfg["min_chapter"]

    in_scope: list[dict] = []
    out_of_scope: list[dict] = []
    for f in audit.get("findings", []):
        if f["code"] not in ALLOWED_CODES:
            continue
        ch = f.get("chapter_no")
        if ch is None:
            continue
        if ch < min_ch:
            out_of_scope.append(f)
        else:
            in_scope.append(f)

    print(f"\n{'='*80}")
    print(f"{slug}")
    print(f"  lock threshold: chapter_no >= {min_ch}")
    print(f"  in-scope findings: {len(in_scope)}")
    print(f"  out-of-scope (locked): {len(out_of_scope)}")
    if in_scope:
        under = sorted({f['chapter_no'] for f in in_scope if f['code'] == 'LENGTH_UNDER'})
        over = sorted({f['chapter_no'] for f in in_scope if f['code'] == 'LENGTH_OVER'})
        print(f"  LENGTH_UNDER chapters: {under[:30]}{' ...' if len(under) > 30 else ''}")
        print(f"  LENGTH_OVER  chapters: {over[:30]}{' ...' if len(over) > 30 else ''}")

    # Persist the scoped plan for downstream execution.
    plan_path = root / f"repair-plan-{slug}.json"
    plan_path.write_text(json.dumps({
        "slug": slug,
        "min_chapter": min_ch,
        "in_scope_count": len(in_scope),
        "out_of_scope_count": len(out_of_scope),
        "chapters_length_under": sorted({f['chapter_no'] for f in in_scope if f['code'] == 'LENGTH_UNDER'}),
        "chapters_length_over": sorted({f['chapter_no'] for f in in_scope if f['code'] == 'LENGTH_OVER'}),
    }, ensure_ascii=False, indent=2))
    print(f"  plan saved: {plan_path.name}")
