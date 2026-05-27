#!/usr/bin/env python
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
import re
from typing import Any

from bestseller.services.outline_specificity_gate import PLACEHOLDER_BLACKLIST


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit outline and volume-plan specificity.")
    parser.add_argument("--story-bible-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, default=Path("audits"))
    parser.add_argument("--date-stamp", default="20260524")
    args = parser.parse_args()

    args.output_dir.mkdir(parents=True, exist_ok=True)
    contract_report = audit_prewrite_contract(args.story_bible_dir / "prewrite-contract.json")
    volume_report = audit_volume_plan(args.story_bible_dir / "volume-plan.csv")

    contract_path = args.output_dir / f"outline-specificity-baseline-{args.date_stamp}.json"
    volume_path = args.output_dir / f"volume-plan-resolution-baseline-{args.date_stamp}.json"
    contract_path.write_text(
        json.dumps(contract_report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    volume_path.write_text(
        json.dumps(volume_report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(
        json.dumps(
            {"outline": str(contract_path), "volume": str(volume_path)},
            ensure_ascii=False,
        )
    )
    return 0


def audit_prewrite_contract(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    chapters = payload.get("chapters") if isinstance(payload, dict) else {}
    if not isinstance(chapters, dict):
        return []
    rows: list[dict[str, Any]] = []
    for key, chapter in sorted(chapters.items(), key=lambda item: int(item[0])):
        if not isinstance(chapter, dict):
            continue
        texts = [
            _stringify(chapter.get("chapter_objective")),
            _stringify(chapter.get("scene_beats")),
            _stringify(chapter.get("required_evidence")),
            _stringify(chapter.get("required_payoff")),
        ]
        combined = "\n".join(texts)
        hits = [
            pattern
            for pattern in PLACEHOLDER_BLACKLIST
            if pattern and pattern in combined
        ]
        rows.append(
            {
                "chapter_no": int(key),
                "placeholder_hits": len(hits),
                "matched_placeholders": sorted(set(hits)),
                "total_chars": len(combined),
                "specificity_score": max(0.0, round(1.0 - len(hits) * 0.2, 3)),
            }
        )
    return rows


def audit_volume_plan(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            text = "\n".join(str(value or "") for value in row.values())
            volume = row.get("volume") or row.get("volume_no") or len(rows) + 1
            rows.append(
                {
                    "volume_no": int(volume),
                    "char_count": len(text),
                    "named_entity_count": _named_entity_count(text),
                    "time_anchor_count": len(
                        re.findall(
                            r"[0-9]{1,2}:[0-9]{2}|第?[0-9一二三四五六七八九十百]+章",
                            text,
                        )
                    ),
                }
            )
    return rows


def _named_entity_count(text: str) -> int:
    return len(
        re.findall(
            r"[赵钱孙李周吴郑王冯陈褚卫蒋沈韩杨朱秦尤许何吕施张苏林][一-龥]{1,3}|[0-9]{2,4}(?:号|栋|楼|室|门)",
            text,
        )
    )


def _stringify(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(_stringify(item) for item in value)
    if isinstance(value, dict):
        return "\n".join(f"{key}: {_stringify(item)}" for key, item in value.items())
    return str(value)


if __name__ == "__main__":
    raise SystemExit(main())
