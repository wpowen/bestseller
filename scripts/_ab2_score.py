"""Phase 3: join blind agent ratings with the hidden arm map, print Δ."""

import json
from statistics import mean

anon = json.load(open("scripts/_ab2_anon.json", encoding="utf-8"))
mp = json.load(open("scripts/_ab2_map.json", encoding="utf-8"))
ratings = json.load(open("scripts/_ab2_ratings.json", encoding="utf-8"))

rows = []
for oid, meta in mp.items():
    r = ratings.get(oid, {})
    rows.append(
        {
            "id": oid,
            "genre": meta["genre"],
            "arm": meta["arm"],
            "score": r.get("score"),
            "purple": bool(r.get("purple", False)),
        }
    )

print(f"{'genre':6} {'arm':10} {'文采均分':>8} {'purple率':>8} {'n':>3}")
agg = {}
for row in rows:
    agg.setdefault((row["genre"], row["arm"]), []).append(row)
summ = {}
for (g, a), items in sorted(agg.items()):
    scored = [x["score"] for x in items if x["score"] is not None]
    avg = mean(scored) if scored else float("nan")
    purp = sum(1 for x in items if x["purple"]) / len(items)
    summ[(g, a)] = (avg, purp)
    print(f"{g:6} {a:10} {avg:8.3f} {purp:8.0%} {len(items):3d}")

print("\n---- Δ(treatment - baseline) ----")
genres = sorted({g for g, _ in summ})
all_b, all_t = [], []
for g in genres:
    b = summ.get((g, "baseline"))
    t = summ.get((g, "treatment"))
    if b and t:
        print(f"{g:6} 文采Δ={t[0] - b[0]:+.3f}  purple {b[1]:.0%}→{t[1]:.0%}")
        all_b.append(b[0])
        all_t.append(t[0])
if all_b:
    print(f"\nOVERALL 文采: baseline={mean(all_b):.3f}  treatment={mean(all_t):.3f}  "
          f"Δ={mean(all_t) - mean(all_b):+.3f}")
