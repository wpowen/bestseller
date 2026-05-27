#!/usr/bin/env python3
"""Emit a dry-run material self-repair plan for a project package."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from bestseller.services.material_self_repair import plan_material_self_repair


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("project_dir", type=Path)
    parser.add_argument("--chapter", type=int, default=None)
    parser.add_argument("--chapter-position", default=None)
    parser.add_argument("--prompt-pack-key", default=None)
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args()

    plan = plan_material_self_repair(
        args.project_dir,
        chapter_number=args.chapter,
        chapter_position=args.chapter_position,
        prompt_pack_key=args.prompt_pack_key,
    )
    print(
        json.dumps(
            plan.to_dict(),
            ensure_ascii=False,
            indent=2 if args.pretty else None,
            sort_keys=True,
        )
    )
    return 2 if plan.blocking else 0


if __name__ == "__main__":
    raise SystemExit(main())
