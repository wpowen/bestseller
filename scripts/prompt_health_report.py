#!/usr/bin/env python
"""Emit a markdown prompt health report from ``llm_runs``."""

from __future__ import annotations

import argparse
from collections import defaultdict
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, text


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--output", default="-")
    args = parser.parse_args()

    engine = create_engine(args.database_url)
    rows = engine.connect().execute(
        text(
            """
            select prompt_template, prompt_hash, input_tokens, output_tokens, provider,
                   finish_reason, metadata
            from llm_runs
            where prompt_template is not null
            """
        )
    )
    stats: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "count": 0,
            "input": 0,
            "output": 0,
            "fallback": 0,
            "hashes": set(),
            "cache_enabled": 0,
        }
    )
    for row in rows:
        record = row._mapping
        template = record["prompt_template"]
        item = stats[template]
        item["count"] += 1
        item["input"] += int(record["input_tokens"] or 0)
        item["output"] += int(record["output_tokens"] or 0)
        if record["prompt_hash"]:
            item["hashes"].add(record["prompt_hash"])
        if str(record["provider"] or "").startswith("fallback") or record["finish_reason"] == "fallback":
            item["fallback"] += 1
        metadata = record["metadata"] or {}
        if isinstance(metadata, dict) and metadata.get("cache_system"):
            item["cache_enabled"] += 1

    lines = [
        "# Prompt Health Report",
        "",
        "| prompt_template | calls | avg input | avg output | fallback % | distinct prompt hashes | cache_system calls |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for template, item in sorted(stats.items()):
        count = max(1, int(item["count"]))
        lines.append(
            f"| {template} | {item['count']} | {item['input'] / count:.1f} | "
            f"{item['output'] / count:.1f} | {item['fallback'] / count:.1%} | "
            f"{len(item['hashes'])} | {item['cache_enabled']} |"
        )
    output = "\n".join(lines) + "\n"
    if args.output == "-":
        print(output)
    else:
        Path(args.output).write_text(output, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
