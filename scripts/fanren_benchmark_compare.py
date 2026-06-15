"""《凡人修仙传》结构对标评分卡（A1-A9）。

把一个项目的「正文前物料」(world_spec / cast_spec / volume_plan / 章纲样例)
按 docs/fanren-structure-answerkey-20260614.md 的结构 ground-truth 逐项打分，
输出「达成凡人结构 X%」评分卡 + 构建力短板清单。

只比对**结构数字**(境界层数/势力数/伏笔条数与跨度/配角规模/界域层数/反派升级/
金手指自洽/主角形态)，不比对受版权保护的文字表达。每维 0 / 0.5 / 1 分。

Usage:
    uv run python scripts/fanren_benchmark_compare.py <project-slug> [--json]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field

from benchmark_structural_check import _load_artifact, _non_empty

# 行业顶尖判定线 + 不可妥协的三根硬骨架（境界阶梯/伏笔闭环/金手指自洽）。
PASS_RATE = 0.85
CORE_DIMS = ("A2", "A6", "A8")


@dataclass(frozen=True)
class Score:
    code: str
    name: str
    score: float  # 0 / 0.5 / 1
    target: str
    actual: str
    evidence: list[str] = field(default_factory=list)


def _tiers(world: dict | None) -> list:
    if not isinstance(world, dict):
        return []
    power = world.get("power_system") or {}
    if isinstance(power, dict):
        return power.get("tiers") or []
    return []


def a1_span(volumes: list | None, world: dict | None) -> Score:
    vols = [v for v in (volumes or []) if isinstance(v, dict)]
    vol_n = len(vols)
    chap_sum = sum(
        int(v.get("chapter_count_target") or v.get("chapter_count") or 0) for v in vols
    )
    tiers = _tiers(world)
    span_ok = vol_n >= 10 and chap_sum >= 500
    s = 1.0 if span_ok else (0.5 if vol_n >= 6 and chap_sum >= 200 else 0.0)
    return Score(
        "A1", "篇幅跨度", s,
        "≥10卷/≥500章, 主角境界贯穿最底到顶",
        f"卷数={vol_n}, 章数合计={chap_sum}, 境界阶数={len(tiers)}",
    )


def a2_ladder(world: dict | None) -> Score:
    tiers = _tiers(world)
    n = len(tiers)
    if n == 0:
        power = (world or {}).get("power_system")
        return Score("A2", "境界阶梯", 0.0, "≥7大境界,每境gating+cost",
                     f"tiers=0 (power_system={'自由文本' if isinstance(power,str) else '缺失'})")
    # Per-tier cost/bottleneck lives in power_system.tier_progression (preferred)
    # or inline in rich tier dicts (legacy). Count tiers that carry either.
    power = (world or {}).get("power_system") or {}
    progression = power.get("tier_progression") or [] if isinstance(power, dict) else []
    with_cost = 0
    for entry in progression:
        if not isinstance(entry, dict):
            continue
        if _non_empty(entry.get("breakthrough_cost")) or _non_empty(entry.get("bottleneck")):
            with_cost += 1
    # legacy fallback: rich tier dicts carrying cost cues inline
    if with_cost == 0:
        for t in tiers:
            if isinstance(t, dict) and re.search(
                r"代价|cost|寿|瓶颈|bottleneck|天槛|gate|breakthrough|突破",
                json.dumps(t, ensure_ascii=False),
            ):
                with_cost += 1
    cost_rate = with_cost / n if n else 0
    if n >= 7 and cost_rate >= 0.6:
        s = 1.0
    elif n >= 7 or (n >= 5 and cost_rate >= 0.6):
        s = 0.5
    else:
        s = 0.0
    return Score("A2", "境界阶梯", s, "≥7大境界,每境gating+cost",
                 f"境界数={n}, 含代价/瓶颈字段={with_cost}({cost_rate:.0%})",
                 [str(t.get("name") if isinstance(t, dict) else t) for t in tiers])


def a3_stages(volumes: list | None, world: dict | None) -> Score:
    vols = [v for v in (volumes or []) if isinstance(v, dict)]
    forces = {str(v.get("primary_force_name") or "") for v in vols}
    forces.discard("")
    locs = set()
    for v in vols:
        for key in ("primary_location", "stage", "arena", "setting", "location"):
            if v.get(key):
                locs.add(str(v[key]))
    shifts = max(len(forces), len(locs))
    s = 1.0 if shifts >= 5 else (0.5 if shifts >= 3 else 0.0)
    return Score("A3", "舞台/势力切换", s, "≥5次大舞台/格局重组",
                 f"卷际主导力量去重={len(forces)}, 场景去重={len(locs)}",
                 sorted(forces)[:10])


def a4_realms(world: dict | None) -> Score:
    if not isinstance(world, dict):
        return Score("A4", "界域层数", 0.0, "≥3层界域", "world_spec缺失")
    realms = []
    for key in ("realms", "world_layers", "planes", "domains", "tiers_of_world", "界域"):
        v = world.get(key)
        if isinstance(v, list) and v:
            realms = v
            break
    # fallback: scan geography/cosmology free text for layered realm cues
    if not realms:
        blob = json.dumps(world, ensure_ascii=False)
        hits = set(re.findall(r"(浊土|澄洲|上垣|人界|灵界|仙界|下界|上界)", blob))
        n = len(hits)
        s = 1.0 if n >= 3 else (0.5 if n == 2 else 0.0)
        return Score("A4", "界域层数", s, "≥3层界域(递进)",
                     f"显式realms字段=无; 文本界域线索={n}", sorted(hits))
    n = len(realms)
    s = 1.0 if n >= 3 else (0.5 if n == 2 else 0.0)
    return Score("A4", "界域层数", s, "≥3层界域(递进)", f"realms={n}",
                 [str(r.get("name") if isinstance(r, dict) else r) for r in realms])


def a5_cast(cast: dict | None) -> Score:
    if not isinstance(cast, dict):
        return Score("A5", "配角规模", 0.0, "核心≥10动机独立", "cast_spec缺失")
    supporting = cast.get("supporting_cast") or []
    named = [c for c in supporting if isinstance(c, dict) and _non_empty(c.get("name"))]
    with_motive = [
        c for c in named
        if _non_empty(c.get("motivation") or c.get("goal") or c.get("desire"))
    ]
    n = len(with_motive)
    s = 1.0 if n >= 10 else (0.5 if n >= 5 else 0.0)
    return Score("A5", "配角规模", s, "核心≥10且动机独立非空",
                 f"named配角={len(named)}, 动机非空={n}",
                 [str(c.get("name")) for c in with_motive][:15])


def a6_foreshadow(volumes: list | None) -> Score:
    vols = [v for v in (volumes or []) if isinstance(v, dict)]
    planted, paid = [], []
    for v in vols:
        no = int(v.get("volume_number") or 0)
        planted += [(no, str(p)) for p in (v.get("foreshadowing_planted") or [])]
        paid += [(no, str(p)) for p in (v.get("foreshadowing_paid_off") or [])]
    seed_re = re.compile(r"^\s*\[\s*(S[\w-]+)\s*\]")
    plant_vol: dict[str, int] = {}
    for pv, pt in planted:
        m = seed_re.match(pt)
        if m and m.group(1) not in plant_vol:
            plant_vol[m.group(1)] = pv

    def _norm(s: str) -> str:
        return "".join(ch for ch in s if ch.isalnum())[:24]

    linked = long_span = 0
    for qv, qt in paid:
        m = seed_re.match(qt)
        if m and m.group(1) in plant_vol:
            linked += 1
            if qv - plant_vol[m.group(1)] >= 3:
                long_span += 1
            continue
        key = _norm(qt)
        hits = [pv for pv, pt in planted if key and (key in _norm(pt) or _norm(pt)[:12] in key)]
        if hits:
            linked += 1
            if qv - min(hits) >= 3:
                long_span += 1
    if len(planted) >= 10 and linked >= 3 and long_span >= 3:
        s = 1.0
    elif len(planted) >= 6 and linked >= 2:
        s = 0.5
    else:
        s = 0.0
    return Score("A6", "伏笔回收", s, "≥10登记,≥3条跨≥3卷闭环",
                 f"登记={len(planted)}, 关联={linked}, 跨≥3卷={long_span}")


def a7_villain(volumes: list | None) -> Score:
    vols = sorted(
        [v for v in (volumes or []) if isinstance(v, dict)],
        key=lambda v: int(v.get("volume_number") or 0),
    )
    levels = []
    for v in vols:
        for key in ("antagonist_force_name", "primary_antagonist", "antagonist", "primary_force_name"):
            if v.get(key):
                levels.append(str(v[key]))
                break
    distinct = len(set(levels))
    s = 1.0 if distinct >= 4 else (0.5 if distinct >= 2 else 0.0)
    return Score("A7", "反派升级", s, "威胁随境界升级≥4层级",
                 f"卷际反派去重={distinct}", levels[:10])


def a8_goldfinger(world: dict | None, kernel: dict | None) -> Score:
    blobs = []
    if isinstance(world, dict):
        power = world.get("power_system")
        if power:
            blobs.append(json.dumps(power, ensure_ascii=False))
        if world.get("special_mechanics"):
            blobs.append(json.dumps(world["special_mechanics"], ensure_ascii=False))
    if isinstance(kernel, dict):
        blobs.append(json.dumps(kernel, ensure_ascii=False))
    blob = "\n".join(blobs)
    if not blob:
        return Score("A8", "金手指自洽", 0.0, "规则+代价+边界明确", "无 power_system/kernel 可解析")
    # has_rule = the golden finger operates under explicit constraints, not
    # omnipotently. Rules surface as 规则/机制 OR as constraint/conditional
    # phrasing (不可/需/必须/以…计价/前提/受…约束) — the latter is how
    # natural cultivation prose states a rule, so both count.
    has_rule = bool(re.search(
        r"规则|rule|机制|只能|仅|约束|条件|前提|不可|不能|需|必须|须|计价|代偿", blob))
    has_cost = bool(re.search(r"代价|cost|寿|消耗|反噬|损|price|toll", blob))
    has_bound = bool(re.search(r"边界|不能|无法|上限|限制|非全能|失效|终身卡|boundary|cannot", blob))
    hits = sum([has_rule, has_cost, has_bound])
    s = 1.0 if hits == 3 else (0.5 if hits == 2 else 0.0)
    return Score("A8", "金手指自洽", s, "规则+代价+边界三者齐",
                 f"规则={has_rule}, 代价={has_cost}, 边界={has_bound}")


def a9_protagonist(cast: dict | None, kernel: dict | None) -> Score:
    blobs = []
    if isinstance(cast, dict):
        prot = cast.get("protagonist") or cast.get("main_character") or {}
        if prot:
            blobs.append(json.dumps(prot, ensure_ascii=False))
    if isinstance(kernel, dict):
        blobs.append(json.dumps(kernel, ensure_ascii=False))
    blob = "\n".join(blobs)
    if not blob:
        return Score("A9", "主角形态", 0.0, "谨慎隐忍/低光环/靠机变", "无 protagonist/kernel")
    cautious = bool(re.search(r"谨慎|隐忍|小心|低调|藏拙|步步为营|算计|机变|务实|cautious|prudent", blob))
    s = 1.0 if cautious else 0.5
    return Score("A9", "主角形态", s, "凡人流:谨慎隐忍靠机变",
                 f"性格含谨慎/隐忍/机变线索={cautious}")


def compute(slug: str) -> list[Score]:
    world = _load_artifact(slug, "world_spec")
    cast = _load_artifact(slug, "cast_spec")
    volumes = _load_artifact(slug, "volume_plan")
    kernel = _load_artifact(slug, "story_design_kernel")
    world = world if isinstance(world, dict) else None
    cast = cast if isinstance(cast, dict) else None
    volumes = volumes if isinstance(volumes, list) else None
    kernel = kernel if isinstance(kernel, dict) else None
    return [
        a1_span(volumes, world),
        a2_ladder(world),
        a3_stages(volumes, world),
        a4_realms(world),
        a5_cast(cast),
        a6_foreshadow(volumes),
        a7_villain(volumes),
        a8_goldfinger(world, kernel),
        a9_protagonist(cast, kernel),
    ]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("slug")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    scores = compute(args.slug)
    total = sum(s.score for s in scores)
    rate = total / len(scores)
    core_full = all(s.score >= 1.0 for s in scores if s.code in CORE_DIMS)
    verdict = rate >= PASS_RATE and core_full

    if args.json:
        print(json.dumps(
            {"slug": args.slug, "rate": rate, "total": total, "max": len(scores),
             "core_full": core_full, "verdict": verdict,
             "scores": [s.__dict__ for s in scores]},
            ensure_ascii=False, indent=2))
        return 0 if verdict else 1

    print(f"《凡人修仙传》结构对标 — {args.slug}")
    print("=" * 60)
    for s in scores:
        mark = "✅" if s.score >= 1 else ("◐" if s.score >= 0.5 else "❌")
        core = " [核心]" if s.code in CORE_DIMS else ""
        print(f"{mark} {s.code} {s.name}{core}  {s.score}")
        print(f"     目标: {s.target}")
        print(f"     实际: {s.actual}")
        if s.evidence:
            print(f"     证据: {', '.join(s.evidence[:8])}")
    print("=" * 60)
    print(f"构建力达成率 = {total}/{len(scores)} = {rate:.0%}  (顶尖线 {PASS_RATE:.0%})")
    print(f"核心三骨架(A2境界/A6伏笔/A8金手指)全满分 = {'是' if core_full else '否'}")
    print(f"行业顶尖判定 = {'达标 ✅' if verdict else '未达标 ❌'}")
    return 0 if verdict else 1


if __name__ == "__main__":
    sys.exit(main())
