#!/usr/bin/env python3
"""Assert that the latest scene prompt trace contains methodology markers."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

REQUIRED_MARKERS_IN_USER = ("【题材方法论·正文场景】",)
OPTIONAL_MARKERS_IN_USER = (
    "【writing_methodology·scene】",
    "【prompt_pack.scene_writer】",
)


def _prompt_text(data: dict[str, Any], key: str) -> str:
    prompts = data.get("prompts")
    if isinstance(prompts, dict):
        return str(prompts.get(key) or "")
    previews = data.get("prompt_previews")
    if isinstance(previews, dict):
        if key == "system":
            return str(previews.get("system_head") or "")
        return f"{previews.get('user_head') or ''}\n{previews.get('user_tail') or ''}"
    return ""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--slug", required=True)
    parser.add_argument("--trace-dir", default=None, help="Override output/<slug>/traces")
    args = parser.parse_args()

    trace_dir = Path(args.trace_dir or f"output/{args.slug}/traces")
    candidates = sorted(
        trace_dir.glob("scene-prompt-*.json"),
        key=lambda path: path.stat().st_mtime,
    )
    if not candidates:
        print(f"FAIL no scene-prompt trace under {trace_dir}", file=sys.stderr)
        return 2

    latest = candidates[-1]
    data = json.loads(latest.read_text(encoding="utf-8"))
    system_prompt = _prompt_text(data, "system")
    user_prompt = _prompt_text(data, "user")
    missing_required = [m for m in REQUIRED_MARKERS_IN_USER if m not in user_prompt]
    present_optional = [m for m in OPTIONAL_MARKERS_IN_USER if m in user_prompt]

    print(f"checking: {latest}")
    print(
        f"mode={data.get('mode')} "
        f"system_chars={len(system_prompt)} "
        f"user_chars={len(user_prompt)}"
    )
    print(f"optional present: {present_optional}")
    if missing_required:
        print(f"FAIL missing required markers in user_prompt: {missing_required}", file=sys.stderr)
        return 1
    print("OK all required markers present")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
