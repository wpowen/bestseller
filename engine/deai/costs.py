"""Read-only accounting for saved de-AI reports and request ledgers."""

from collections import Counter, defaultdict
import argparse
import json
from pathlib import Path


def _phase(stage):
    if stage.startswith("recall22-initial-") or stage == "diagnostics":
        return "diagnosis"
    if stage.startswith("recall22-recheck-"):
        return "recheck"
    if stage.startswith("recall22-candidates-") or stage.startswith(("dialogue-scene", "actor-")):
        return "rewrite"
    if stage.startswith("recall22-facts") or stage.startswith("dialogue-"):
        return "verification"
    if stage.startswith(("inventory", "dialogue-cast")):
        return "recall"
    return "other"


def _category(stage):
    for prefix in ("recall22-initial-", "recall22-recheck-"):
        if stage.startswith(prefix):
            return stage[len(prefix) :].split("-", 1)[0]
    if stage.startswith("recall22-candidates-"):
        return "candidates"
    if stage.startswith("recall22-facts"):
        return "facts"
    return stage.split("-", 1)[0] or "unknown"


def _row_totals(rows):
    result = dict(calls=0, prompt_tokens=0, completion_tokens=0, unknown_usage=0)
    for row in rows:
        usage = row.get("usage") or {}
        result["calls"] += 1
        result["prompt_tokens"] += usage.get("prompt_tokens") or 0
        result["completion_tokens"] += usage.get("completion_tokens") or 0
        result["unknown_usage"] += int(usage.get("prompt_tokens") is None or usage.get("completion_tokens") is None)
    return result


def analyze_book(book):
    """Return aggregate cost, adoption, terminal-state, and failure evidence."""
    book = Path(book)
    ledgers = sorted((book / "final/.deai").glob("*/http/ledger.json"))
    rows = []
    for path in ledgers:
        value = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(value, list):
            rows.extend(row for row in value if isinstance(row, dict))

    phases = defaultdict(list)
    stages = defaultdict(list)
    for row in rows:
        stage = str(row.get("stage") or "unknown")
        phases[_phase(stage)].append(row)
        stages[(_phase(stage), _category(stage))].append(row)

    hits = Counter()
    edits = Counter()
    adopted_chapters = defaultdict(set)
    statuses = Counter()
    failures = []
    for path in sorted((book / "final").glob("chapter-*.deai.json")):
        report = json.loads(path.read_text(encoding="utf-8"))
        chapter = path.name.removeprefix("chapter-").removesuffix(".deai.json")
        statuses[str(report.get("status") or "unknown")] += 1
        for hit in report.get("hits", []):
            if isinstance(hit, dict):
                hits[str(hit.get("type") or "unknown")] += 1
        for edit in report.get("edits", []):
            if not isinstance(edit, dict):
                continue
            types = edit.get("types") or [edit.get("type") or "unknown"]
            for kind in set(str(kind) for kind in types if kind):
                edits[kind] += 1
                adopted_chapters[kind].add(chapter)
        failure = (report.get("checks") or {}).get("failure")
        if isinstance(failure, dict):
            failures.append(
                dict(
                    chapter=int(chapter),
                    status=report.get("status"),
                    stage=failure.get("stage"),
                    reason=failure.get("reason"),
                )
            )

    phase_totals = {}
    for name, phase_rows in sorted(phases.items()):
        values = _row_totals(phase_rows)
        values["tokens"] = values["prompt_tokens"] + values["completion_tokens"]
        phase_totals[name] = values
    stage_totals = {}
    for (phase, category), stage_rows in sorted(stages.items()):
        values = _row_totals(stage_rows)
        values["tokens"] = values["prompt_tokens"] + values["completion_tokens"]
        stage_totals[f"{phase}:{category}"] = values

    categories = {}
    for kind in sorted(set(hits) | set(edits)):
        categories[kind] = dict(
            hits=hits[kind], adopted_edits=edits[kind], adopted_chapters=len(adopted_chapters[kind])
        )
    return dict(
        totals=_row_totals(rows),
        phases=phase_totals,
        stages=stage_totals,
        rule_categories=categories,
        statuses=dict(sorted(statuses.items())),
        failures=failures,
    )


def markdown(result):
    totals = result["totals"]
    lines = [
        "| 阶段 | 调用 | 输入 tokens | 输出 tokens | 合计 tokens | 未知用量 |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for name, values in sorted(result["phases"].items(), key=lambda item: -item[1]["tokens"]):
        lines.append(
            f"| {name} | {values['calls']} | {values['prompt_tokens']} | {values['completion_tokens']} | "
            f"{values['tokens']} | {values['unknown_usage']} |"
        )
    lines.extend(
        [
            f"| **总计** | **{totals['calls']}** | **{totals['prompt_tokens']}** | "
            f"**{totals['completion_tokens']}** | **{totals['prompt_tokens'] + totals['completion_tokens']}** | "
            f"**{totals['unknown_usage']}** |",
            "",
            "| 规则类别 | 命中 | 采用修改 | 采用章节 |",
            "|---|---:|---:|---:|",
        ]
    )
    for name, values in sorted(result["rule_categories"].items(), key=lambda item: (-item[1]["hits"], item[0])):
        lines.append(f"| {name} | {values['hits']} | {values['adopted_edits']} | {values['adopted_chapters']} |")
    return "\n".join(lines) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("book")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    result = analyze_book(args.book)
    print(json.dumps(result, ensure_ascii=False, indent=2) if args.json else markdown(result), end="")


if __name__ == "__main__":
    main()
