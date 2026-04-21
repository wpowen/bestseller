#!/usr/bin/env python3
"""
repair_json_keys.py — Repair LLM-generation JSON corruption in chapters.

Corruption patterns:
  A. Narrative text became a key, value is `emotion": "X"` or `emphasis": "X"`
     -> Append key text to `content`, parse value into proper field.
  B. Node id became a key, value is a dict containing the actual proper node.
     -> Replace the parent dict's content with the value dict.
  C. Narrative text became a key, value is empty string.
     -> Append key text to `content`, drop the bad key.

Affects ZH source AND all 3 translation files (same path corruption).
After repair, translations will need re-translation for the appended text.
"""
from __future__ import annotations

import json
import re
import shutil
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).parent.parent
ZH_DIR = ROOT / "output" / "天机录" / "if" / "chapters"
TRANS_DIRS = {
    "en": ROOT / "output" / "天机录" / "translations" / "en" / "chapters",
    "ja": ROOT / "output" / "天机录" / "translations" / "ja" / "chapters",
    "ko": ROOT / "output" / "天机录" / "translations" / "ko" / "chapters",
}

CJK = re.compile(r"[\u4e00-\u9fff]")
EMOTION_VALUE = re.compile(r'^([a-z_]+)":\s*"(.+)$')
ID_KEY = re.compile(r"^[\w_]+_ch\d+_\d+$")


def is_corrupt_key(k: str) -> bool:
    """A key counts as corrupt if it contains 5+ CJK chars or matches an id pattern with object value."""
    return bool(CJK.search(k)) and len(k) > 5


def repair_dict(d: dict, log: list) -> dict:
    """Return a new dict with corrupt keys repaired.

    For Pattern A: extract emotion/emphasis from value, append key text to content.
    For Pattern B: replace dict entirely with value dict.
    For Pattern C: drop key, append text to content.
    """
    new_d: dict[str, Any] = {}
    text_to_append: list[str] = []
    extracted_props: dict[str, Any] = {}
    pattern_b_replacement: dict | None = None

    for k, v in d.items():
        if not is_corrupt_key(k) and not (ID_KEY.match(k) and isinstance(v, dict)):
            new_d[k] = v
            continue

        if isinstance(v, dict):
            pattern_b_replacement = v
            log.append(f"PatternB: replaced node with value dict (key={k[:40]}...)")
            continue

        if isinstance(v, str):
            m = EMOTION_VALUE.match(v.strip())
            if m:
                prop, val = m.group(1), m.group(2).rstrip('"')
                extracted_props[prop] = val
                text_to_append.append(k)
                log.append(f"PatternA: extracted {prop}={val!r}, appended key text")
            else:
                text_to_append.append(k)
                log.append(f"PatternC: appended key text (val={v[:30]!r})")
            continue

        log.append(f"Unknown corruption: key={k[:40]!r} val={type(v).__name__}")

    if pattern_b_replacement is not None:
        return pattern_b_replacement

    if text_to_append:
        existing = new_d.get("content", "")
        if existing and not existing.endswith(("\n", " ")):
            existing += "\n"
        new_d["content"] = existing + "\n".join(text_to_append)
    new_d.update(extracted_props)
    return new_d


def repair_node(obj: Any, log: list) -> Any:
    if isinstance(obj, dict):
        # Detect at this level
        has_corruption = any(
            is_corrupt_key(k) or (ID_KEY.match(k) and isinstance(v, dict))
            for k, v in obj.items()
        )
        if has_corruption:
            obj = repair_dict(obj, log)
        # Recurse
        if isinstance(obj, dict):
            for k in list(obj.keys()):
                obj[k] = repair_node(obj[k], log)
    elif isinstance(obj, list):
        return [repair_node(x, log) for x in obj]
    return obj


def main() -> int:
    affected_files: list[tuple[Path, list[str]]] = []
    backup_root = ROOT / "output" / "天机录" / "amazon" / "quality_audit" / "json_repair_backup"
    backup_root.mkdir(parents=True, exist_ok=True)

    for label, src_dir in [("zh", ZH_DIR), *TRANS_DIRS.items()]:
        for p in sorted(src_dir.glob("ch*.json")):
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                print(f"SKIP (parse fail) {p}: {exc}", file=sys.stderr)
                continue
            log: list[str] = []
            new_data = repair_node(data, log)
            if log:
                # Backup
                backup_path = backup_root / label / p.name
                backup_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy(p, backup_path)
                # Write
                p.write_text(json.dumps(new_data, ensure_ascii=False, indent=2), encoding="utf-8")
                affected_files.append((p, log))

    print(f"\nRepaired {len(affected_files)} files:")
    for p, log in affected_files:
        print(f"  {p.relative_to(ROOT)}: {len(log)} fixes")
        for line in log:
            print(f"    - {line}")

    report = {
        "files_repaired": len(affected_files),
        "details": [
            {"file": str(p.relative_to(ROOT)), "fixes": log}
            for p, log in affected_files
        ],
    }
    report_path = ROOT / "output" / "天机录" / "amazon" / "quality_audit" / "json_repair_report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nReport → {report_path}")
    print(f"Backups → {backup_root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
