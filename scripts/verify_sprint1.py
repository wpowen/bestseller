#!/usr/bin/env python
"""Verify prompt-system Sprint 1 dump artifacts."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any


def _iter_json_files(root: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*.json")):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(value, dict):
            value["_path"] = str(path)
            rows.append(value)
    return rows


def _prompt(row: dict[str, Any], key: str) -> str:
    return str(row.get(key) or row.get("request", {}).get(key) or "")


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: verify_sprint1.py <dump-dir>", file=sys.stderr)
        return 2
    root = Path(sys.argv[1])
    rows = _iter_json_files(root)
    failures: list[str] = []

    by_template: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        template = str(row.get("prompt_template") or row.get("request", {}).get("prompt_template") or "")
        if template:
            by_template.setdefault(template, []).append(row)

    for name in (
        "conception_market",
        "conception_character",
        "conception_world",
        "conception_review",
        "conception_finalize",
    ):
        if not any("【题材方法论·立项】" in _prompt(row, "user_prompt") for row in by_template.get(name, [])):
            failures.append(f"{name}: missing conception methodology block")

    scene_rows = by_template.get("scene_writer", [])
    if scene_rows:
        hashes = {
            hashlib.sha256(_prompt(row, "system_prompt").encode("utf-8")).hexdigest()
            for row in scene_rows[:5]
        }
        if len(hashes) > 1:
            failures.append("scene_writer: first five system_prompt hashes are not stable")
        scene_text = "\n".join(_prompt(row, "user_prompt") + _prompt(row, "system_prompt") for row in scene_rows)
        if "情绪" not in scene_text or "节奏" not in scene_text:
            failures.append("scene_writer: missing emotion/rhythm methodology text")

    for name in ("rolling_summary", "voice_drift_check"):
        for row in by_template.get(name, []):
            if _prompt(row, "system_prompt").startswith("You are a"):
                failures.append(f"{name}: zh dump still has English system prompt")

    if failures:
        print("Sprint 1 verification failed:")
        for failure in failures:
            print(f"- {failure}")
        return 1
    print("Sprint 1 verification passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
