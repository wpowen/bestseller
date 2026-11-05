"""Replay saved Q1 dashboard extractions only. Default: JSON dry-run to stdout.

Usage: PYTHONPATH=engine python3 engine/tools/reconcile_q1_state.py ../engine-books/q1 --dry-run
--write saves derived JSON fields; missing extraction or unmapped stage stays Unknown.
"""

import argparse
from copy import deepcopy
import json
from pathlib import Path
import re

import yaml

from bookengine.q1_quality import normalize_dashboard
from bookengine.storage import save

KEYS = ("strength", "wealth", "status", "affiliation", "stage")
UNKNOWN = "Unknown"
DERIVED = ("dashboard_changed", "stage_changes", "volume_issues")


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def canonical_stage(value, bible):
    """Map only literal canonical names/components or explicit aliases; no fuzzy geography."""
    if not isinstance(value, str) or value == UNKNOWN:
        return UNKNOWN
    stages = bible.get("world", {}).get("stages", [])
    if isinstance(stages, dict):
        stages = list(stages.values())
    matches = set()
    for row in stages if isinstance(stages, list) else []:
        name = row.get("stage", row.get("name")) if isinstance(row, dict) else row
        if not isinstance(name, str) or not name.strip():
            continue
        aliases = row.get("aliases", []) if isinstance(row, dict) else []
        aliases = aliases if isinstance(aliases, list) else [aliases]
        parts = [p.strip() for p in re.split(r"[、与及和]", name) if len(p.strip()) >= 2]
        if any(
            isinstance(s, str) and s and (s == value or len(s) >= 2 and s in value) for s in [name, *parts, *aliases]
        ):
            matches.add(name)
    return next(iter(matches)) if len(matches) == 1 else UNKNOWN


def response_dashboard(root, stage, calls):
    """Recover only one independently valid dashboard object; never repair broken JSON."""
    records = [c for c in calls if c.get("stage") == stage]
    if not records or not isinstance(records[-1].get("seq"), int):
        return None
    path = root / f"responses/{records[-1]['seq']:03d}.json"
    try:
        text = read_json(path)["choices"][0]["message"]["content"]
        hits = list(re.finditer(r'"dashboard"\s*:', text))
        if len(hits) != 1:
            return None
        dashboard, _ = json.JSONDecoder().raw_decode(text[hits[0].end() :].lstrip())
        if isinstance(dashboard, dict):
            return normalize_dashboard(dashboard), str(path.relative_to(root)) + "#dashboard"
    except (OSError, ValueError, KeyError, IndexError, TypeError):
        pass
    return None


def extraction(root, row, calls):
    n = row["number"]
    if row.get("writer") == "minimal-b":
        paths = [root / f"steps/qb-ch{n}-post-hard-state.json", root / f"steps/qb-ch{n}-state.json"]
    elif row.get("writer") is None:
        paths = [root / f"steps/q1-ch{n}-final-state.json"]
    else:
        return None, None, "Unknown: unsupported writer"
    # A failed later post-hard extraction invalidates the earlier body's extraction.
    attempted = {c.get("stage") for c in calls}
    path = next((p for p in paths if p.exists() or p.stem in attempted), paths[-1])
    problem = None
    try:
        raw = read_json(path)
    except (OSError, ValueError):
        raw, problem = None, "Unknown: missing/invalid final extraction"
    if not isinstance(raw, dict) or not isinstance(raw.get("state"), dict):
        recovered = response_dashboard(root, path.stem, calls)
        if recovered is not None:
            return *recovered, None
        return None, str(path.relative_to(root)), problem or "Unknown: final extraction has no state"
    # Omitted dashboard in a valid state is an empty reducer patch, not a lost sequence.
    patch = raw["state"].get("dashboard", {})
    if not isinstance(patch, dict):
        return None, str(path.relative_to(root)), "Unknown: malformed dashboard"
    return normalize_dashboard(patch), str(path.relative_to(root)), None


def projected(dashboard, bible):
    result = {k: dashboard.get(k, UNKNOWN) for k in KEYS}
    result["stage"] = canonical_stage(result["stage"], bible)
    return result


def derive_volume(before, after, history):
    known = [k for k in KEYS if before[k] != UNKNOWN and after[k] != UNKNOWN]
    changed = True if any(before[k] != after[k] for k in known) else False if len(known) == len(KEYS) else None
    issues = (
        ["卷末仪表盘未变化"] if changed is False else ["Unknown: 卷末仪表盘变化证据不完整"] if changed is None else []
    )
    if len(history) >= 2:
        stages = [r["stage"] for r in history[-2:]] + [after["stage"]]
        if UNKNOWN in stages:
            issues.append("Unknown: 同主舞台连用检查缺少规范舞台证据")
        elif len(set(stages)) == 1:
            issues.append("同主舞台连用超过2卷")
    return changed, issues


def reconcile_book(root, expected=100):
    root = Path(root)
    bible = yaml.safe_load((root / "story_bible.yaml").read_text(encoding="utf-8"))
    calls = read_json(root / "calls.json") if (root / "calls.json").exists() else []
    initial = normalize_dashboard(bible.get("initial_dashboard", bible.get("dashboard", {})))
    current = {k: initial.get(k, UNKNOWN) for k in KEYS}
    dashboards = {0: deepcopy(current)}
    chapters, updates, gaps = [], [], []

    def propose(path, assignments):
        if not path.exists():
            return
        old = read_json(path)
        changes = []
        for keys, value in assignments:
            prior = old
            for key in keys:
                prior = prior.get(key) if isinstance(prior, dict) else None
            if prior != value:
                changes.append({"keys": keys, "value": deepcopy(value)})
        if changes:
            updates.append(dict(file=str(path.relative_to(root)), changes=changes))

    for n in range(1, expected + 1):
        path = root / f"chapters/{n:03d}.json"
        if path.exists():
            row = read_json(path)
            if row.get("number") != n:
                raise ValueError(f"{path}: chapter number mismatch")
            patch, source, problem = extraction(root, row, calls)
        else:
            patch, source, problem = None, None, "Unknown: missing chapter record"
        if patch is None:
            current = dict.fromkeys(KEYS, UNKNOWN)
            gaps.append(dict(chapter=n, reason=problem, source=source))
        else:
            current.update(patch)
        dashboards[n] = deepcopy(current)
        chapters.append(
            dict(
                number=n,
                source=source,
                available=patch is not None,
                dashboard=deepcopy(current),
                unknown_fields=[k for k in KEYS if current[k] == UNKNOWN],
            )
        )
        if path.exists():
            propose(path, [(["state", "dashboard"], current)])

    volumes = []
    for path in sorted((root / "volumes").glob("*.json")):
        old = read_json(path)
        start, end = old.get("start"), old.get("end")
        if not isinstance(start, int) or not isinstance(end, int) or not 1 <= start <= end <= expected:
            raise ValueError(f"{path}: invalid volume range")
        before, after = projected(dashboards[start - 1], bible), projected(dashboards[end], bible)
        changed, issues = derive_volume(before, after, volumes)
        incomplete = (
            any(start <= g["chapter"] <= end for g in gaps)
            or UNKNOWN in dashboards[start - 1].values()
            or UNKNOWN in dashboards[end].values()
        )
        if incomplete:
            changed = None
            issues = [i for i in issues if i != "卷末仪表盘未变化"]
            issues.append("Unknown: 本卷原始状态序列或边界不完整，保留未验证结论")
        volume = dict(
            number=old.get("number"),
            start=start,
            end=end,
            stage=after["stage"],
            before_dashboard=before,
            after_dashboard=after,
            dashboard_changed=changed,
            volume_issues=issues,
        )
        volumes.append(volume)
        propose(path, [([k], v) for k, v in volume.items() if k not in ("number", "start", "end")])
    pairs = list(zip(volumes, volumes[1:]))
    unknown_pairs = sum(UNKNOWN in (a["stage"], b["stage"]) for a, b in pairs)
    known_changes = sum(a["stage"] != b["stage"] for a, b in pairs if UNKNOWN not in (a["stage"], b["stage"]))
    covered = [n for v in volumes for n in range(v["start"], v["end"] + 1)]
    complete_volumes = covered == list(range(1, expected + 1))
    derived = dict(
        dashboard_changed=[v["dashboard_changed"] for v in volumes],
        stage_changes=None if unknown_pairs or not complete_volumes else known_changes,
        volume_issues=[i for v in volumes for i in v["volume_issues"]],
    )
    if not complete_volumes:
        derived["volume_issues"].append("Unknown: volume coverage is incomplete")
    propose(root / "state.json", [(["dashboard"], current)])
    run_path = root / "run.json"
    old_stats = {}
    if run_path.exists():
        run = read_json(run_path)
        assignments = []
        for section in ("quality", "acceptance"):
            if isinstance(run.get(section), dict):
                old_stats[section] = {k: run[section].get(k) for k in DERIVED}
                assignments.extend(([section, k], v) for k, v in derived.items())
                if isinstance(run[section].get("criteria"), dict):
                    assignments.extend(
                        [
                            ([section, "criteria", "dashboard"], all(v is True for v in derived["dashboard_changed"])),
                            (
                                [section, "criteria", "stage_changes"],
                                derived["stage_changes"] is not None and derived["stage_changes"] >= 2,
                            ),
                        ]
                    )
        propose(run_path, assignments)
    return dict(
        book=root.name,
        replay=dict(
            available=expected - len(gaps),
            total=expected,
            rate=(expected - len(gaps)) / expected,
            complete=not gaps,
            gaps=gaps,
            fully_known_chapters=sum(not r["unknown_fields"] for r in chapters),
        ),
        stage_mapping=dict(
            policy="literal canonical names/components/aliases; never planned volume",
            known_changes=known_changes,
            unknown_pairs=unknown_pairs,
        ),
        old_stats=old_stats,
        derived=derived,
        chapters=chapters,
        volumes=volumes,
        updates=updates,
    )


def apply_updates(root, updates):
    """Apply freshly computed dashboard/statistic changes with the existing atomic save."""
    for update in updates:
        path = Path(root) / update["file"]
        value = read_json(path)
        for change in update["changes"]:
            keys = change["keys"]
            target = value
            for key in keys[:-1]:
                target = target[key]
            target[keys[-1]] = deepcopy(change["value"])
        save(path, value)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path, help="Q1 batch directory or one book directory")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="default; emit a reviewable plan without writes")
    mode.add_argument("--write", action="store_true", help="explicitly apply derived fields only")
    args = parser.parse_args()
    books = (
        [args.root]
        if (args.root / "story_bible.yaml").exists()
        else sorted(p.parent for p in args.root.glob("*/story_bible.yaml"))
    )
    if not books:
        parser.error("no Q1 books found")
    reports = [reconcile_book(book) for book in books]
    if args.write:
        for book, report in zip(books, reports):
            apply_updates(book, report["updates"])
    print(json.dumps(dict(mode="write" if args.write else "dry-run", books=reports), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
