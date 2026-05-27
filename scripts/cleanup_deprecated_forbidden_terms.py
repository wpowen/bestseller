"""Report deprecated forbidden terms found in project metadata files."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import yaml


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-dir", required=True)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()

    project_dir = Path(args.project_dir)
    policy_path = project_dir / "story-bible" / "forbidden-leaks-policy.yaml"
    policy = yaml.safe_load(policy_path.read_text(encoding="utf-8")) or {}
    deprecated = {str(term) for term in policy.get("deprecated_should_remove") or []}
    if not deprecated:
        print("no deprecated terms configured")
        return 0

    report: list[dict[str, Any]] = []
    for path in sorted(project_dir.rglob("*")):
        if path.suffix.lower() not in {".json", ".yaml", ".yml"}:
            continue
        if path.name == "forbidden-leaks-policy.yaml":
            continue
        text = path.read_text(encoding="utf-8")
        hits = sorted(term for term in deprecated if term in text)
        if not hits:
            continue
        report.append({"path": str(path), "hits": hits})
        if args.write:
            for term in hits:
                text = text.replace(term, "")
            path.write_text(text, encoding="utf-8")

    print(
        json.dumps(
            {"project_dir": str(project_dir), "files": report},
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
