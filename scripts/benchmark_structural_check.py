"""Structural quality checks for a long-form xianxia planning package.

Evaluates the pre-prose planning artifacts of a project against the
S1-S6 structural metrics defined in docs/xianxia-benchmark-spec-20260612.md.
Deterministic checks only; semantic judgments (S4 motivation chains, S8
cross-batch coherence) are flagged for manual/LLM review rather than guessed.

Usage:
    uv run python scripts/benchmark_structural_check.py <project-slug> [--json]
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass, field


@dataclass(frozen=True)
class CheckResult:
    metric: str
    name: str
    passed: bool | None  # None = needs manual/LLM review
    detail: str
    evidence: list[str] = field(default_factory=list)


def _load_artifact(slug: str, artifact_type: str) -> dict | list | None:
    proc = subprocess.run(
        ["uv", "run", "bestseller", "planning", "show", slug, artifact_type],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        return None
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return None
    return payload.get("content") if isinstance(payload, dict) else payload


def _non_empty(value: object) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, dict)):
        return len(value) > 0
    return True


def check_s1_power_system(world: dict | None) -> CheckResult:
    if not isinstance(world, dict):
        return CheckResult("S1", "升级体系", False, "world_spec 缺失或不可解析")
    power = world.get("power_system") or {}
    if isinstance(power, str):
        # 与 WorldSpecInput._coerce_power_system 同语义：整段文本会被塞进
        # name，tiers 恒空——视为结构化境界阶梯缺失。
        return CheckResult(
            "S1",
            "升级体系",
            False,
            f"power_system 为自由文本(长度{len(power)})而非结构化境界阶梯，tiers=0 (需≥7)",
            [power[:80]],
        )
    tiers = power.get("tiers") or []
    start = power.get("protagonist_starting_tier") or ""
    ok = len(tiers) >= 7 and _non_empty(start)
    detail = f"境界数={len(tiers)} (需≥7), 主角起始境界={'有' if _non_empty(start) else '缺'}"
    return CheckResult("S1", "升级体系", ok, detail, [str(t) for t in tiers])


def check_s2_factions(world: dict | None, volumes: list | None) -> CheckResult:
    if not isinstance(world, dict):
        return CheckResult("S2", "势力演化", False, "world_spec 缺失")
    factions = world.get("factions") or []
    names = [str(f.get("name", "")) for f in factions if isinstance(f, dict)]
    vol_forces = []
    for v in volumes or []:
        if isinstance(v, dict):
            vol_forces.append(str(v.get("primary_force_name") or ""))
    distinct_forces = len({f for f in vol_forces if f})
    ok = len(names) >= 5 and distinct_forces >= 3
    detail = (
        f"入册势力={len(names)} (需≥5); 十卷 primary_force 去重={distinct_forces} "
        f"(需≥3 以体现格局轮转); 势力状态时间轴字段=schema缺失(已知缺口)"
    )
    return CheckResult("S2", "势力演化", ok, detail, names + [f"卷际主导力量: {vol_forces}"])


def check_s3_foreshadowing(volumes: list | None) -> CheckResult:
    if not volumes:
        return CheckResult("S3", "伏笔回收", False, "volume_plan 缺失")
    planted: list[tuple[int, str]] = []
    paid: list[tuple[int, str]] = []
    for v in volumes:
        if not isinstance(v, dict):
            continue
        no = int(v.get("volume_number") or 0)
        planted += [(no, str(p)) for p in (v.get("foreshadowing_planted") or [])]
        paid += [(no, str(p)) for p in (v.get("foreshadowing_paid_off") or [])]

    def _norm(s: str) -> str:
        return "".join(ch for ch in s if ch.isalnum())[:24]

    long_span = 0
    orphans = []
    for pv, ptext in planted:
        key = _norm(ptext)
        hits = [qv for qv, qtext in paid if key and (key in _norm(qtext) or _norm(qtext)[:12] in key)]
        if hits:
            span = max(hits) - pv
            if span >= 3:
                long_span += 1
        else:
            orphans.append(f"卷{pv}: {ptext[:40]}")
    ok = len(planted) >= 10 and long_span >= 3
    detail = (
        f"登记伏笔={len(planted)} (需≥10); 跨度≥3卷且可匹配回收={long_span} (需≥3); "
        f"字面无法匹配回收的伏笔={len(orphans)} 条(含转述误差,需人工复核)"
    )
    return CheckResult("S3", "伏笔回收", ok, detail, orphans[:10])


def check_s4_cast(cast: dict | None) -> CheckResult:
    if not isinstance(cast, dict):
        return CheckResult("S4", "角色动机", False, "cast_spec 缺失")
    supporting = cast.get("supporting_cast") or []
    rich = 0
    names = []
    for c in supporting:
        if not isinstance(c, dict):
            continue
        has_arc = _non_empty(c.get("evolution_arc")) or _non_empty(c.get("arc_trajectory"))
        has_motive = (
            _non_empty(c.get("motivation")) or _non_empty(c.get("goal")) or _non_empty(c.get("desire"))
        )
        if has_arc and has_motive:
            rich += 1
            names.append(str(c.get("name", "")))
    ok = rich >= 4
    detail = f"弧线+动机双非空的配角={rich} (需≥4); 卷际动机链需人工/判官复核"
    return CheckResult("S4", "角色动机", ok, detail, names)


def check_s5_volume_rhythm(volumes: list | None) -> CheckResult:
    if not volumes:
        return CheckResult("S5", "卷级节奏", False, "volume_plan 缺失")
    hooks = [str(v.get("reader_hook_to_next") or "") for v in volumes if isinstance(v, dict)]
    hook_dupes = [h for h, n in Counter(hooks).items() if n > 1 and h]
    empty_hooks = sum(1 for h in hooks if not h.strip())
    phases = [str(v.get("conflict_phase") or "") for v in volumes if isinstance(v, dict)]
    goals = [str(v.get("volume_goal") or "") for v in volumes if isinstance(v, dict)]

    def _shape_token(g: str) -> str:
        return "".join(ch for ch in g if ch.isalnum())[:10]

    triple_same = any(
        _shape_token(goals[i]) == _shape_token(goals[i + 1]) == _shape_token(goals[i + 2])
        for i in range(max(0, len(goals) - 2))
    )
    ok = not hook_dupes and not triple_same and empty_hooks == 0
    detail = (
        f"reader_hook_to_next 重复句={len(hook_dupes)} 空缺={empty_hooks} (均需0, R9死字段/空字段检测); "
        f"连续3卷同构卷目标={'是' if triple_same else '否'} (需否, R8); conflict_phase序列={phases}"
    )
    return CheckResult("S5", "卷级节奏", ok, detail, hook_dupes[:5])


def check_s6_chapter_writability(batch: list | dict | None) -> CheckResult:
    if batch is None:
        return CheckResult("S6", "章级可写性", False, "chapter_outline_batch 缺失")
    chapters = batch if isinstance(batch, list) else batch.get("chapters") or []
    required = ["opening_situation", "main_conflict", "target_emotion", "hook_type", "causal_contract"]
    total = len(chapters)
    misses: list[str] = []
    scene_participant_ok = 0
    scene_total = 0
    for ch in chapters:
        if not isinstance(ch, dict):
            continue
        for fieldname in required:
            if not _non_empty(ch.get(fieldname)):
                misses.append(f"ch{ch.get('chapter_number')}: {fieldname} 空")
        for sc in ch.get("scenes") or []:
            scene_total += 1
            if isinstance(sc, dict) and len(sc.get("participants") or []) >= 2:
                scene_participant_ok += 1
    fill_ok = not misses
    part_rate = (scene_participant_ok / scene_total) if scene_total else 0.0
    ok = fill_ok and part_rate >= 0.9
    detail = (
        f"章数={total}; 必填字段缺失={len(misses)} (需0, R6); "
        f"场景 participants≥2 比率={part_rate:.0%} (需≥90%, 独角戏场景允许少量)"
    )
    return CheckResult("S6", "章级可写性", ok, detail, misses[:12])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("slug")
    parser.add_argument("--json", action="store_true")
    parser.add_argument(
        "--outline-file",
        help="JSON file with merged chapter outlines (fallback when no chapter_outline_batch artifact)",
    )
    args = parser.parse_args()

    world = _load_artifact(args.slug, "world_spec")
    cast = _load_artifact(args.slug, "cast_spec")
    volumes = _load_artifact(args.slug, "volume_plan")
    batch = _load_artifact(args.slug, "chapter_outline_batch")
    if batch is None and args.outline_file:
        with open(args.outline_file, encoding="utf-8") as fh:
            batch = json.load(fh)

    results = [
        check_s1_power_system(world if isinstance(world, dict) else None),
        check_s2_factions(world if isinstance(world, dict) else None, volumes if isinstance(volumes, list) else None),
        check_s3_foreshadowing(volumes if isinstance(volumes, list) else None),
        check_s4_cast(cast if isinstance(cast, dict) else None),
        check_s5_volume_rhythm(volumes if isinstance(volumes, list) else None),
        check_s6_chapter_writability(batch),
    ]

    if args.json:
        print(json.dumps([r.__dict__ for r in results], ensure_ascii=False, indent=2))
    else:
        for r in results:
            mark = "PASS" if r.passed else ("REVIEW" if r.passed is None else "FAIL")
            print(f"[{mark}] {r.metric} {r.name} — {r.detail}")
            for ev in r.evidence:
                print(f"        · {ev}")
    return 0 if all(r.passed for r in results if r.passed is not None) else 1


if __name__ == "__main__":
    sys.exit(main())
