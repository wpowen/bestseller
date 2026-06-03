from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
import sys
from typing import Any

import yaml

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from bestseller.infra.db.session import session_scope
from bestseller.services.material_library import MaterialEntry, insert_entry


DEFAULT_SOURCE = (
    Path(__file__).resolve().parents[1]
    / "data"
    / "title_methodology"
    / "platform_title_patterns_2026q2.yaml"
)


def _load_yaml(path: Path) -> dict[str, Any]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(payload, dict):
        raise ValueError(f"Expected mapping YAML: {path}")
    return payload


def _entry_from_pattern(source: dict[str, Any], key: str, pattern: dict[str, Any]) -> MaterialEntry:
    label = str(pattern.get("platform_label") or key).strip()
    group = str(pattern.get("group") or "").strip()
    syntax = [str(item).strip() for item in pattern.get("syntax") or [] if str(item).strip()]
    avoid = [str(item).strip() for item in pattern.get("avoid") or [] if str(item).strip()]
    token_bags = pattern.get("token_bags") if isinstance(pattern.get("token_bags"), dict) else {}
    chars = pattern.get("preferred_chars") or []
    avg = pattern.get("average_chars")
    narrative = (
        f"{label} 书名规律：{group}；偏好长度 {chars or '未标注'}，"
        f"样本均值 {avg or '未标注'} 字。句法：{'；'.join(syntax)}。"
        f"避雷：{'；'.join(avoid)}。"
    )
    return MaterialEntry(
        dimension="platform_title_patterns",
        slug=f"2026q2-{key}",
        name=f"{label} 2026Q2 书名方法论",
        narrative_summary=narrative,
        genre=None,
        sub_genre=None,
        tags=[
            "书名",
            "平台标题",
            "命名方法论",
            key,
            label,
            group,
        ],
        content_json={
            "platform_key": key,
            "platform_label": label,
            "methodology_group": group,
            "average_chars": avg,
            "preferred_chars": chars,
            "syntax": syntax,
            "token_bags": token_bags,
            "avoid": avoid,
            "source_report": source.get("source_report"),
            "source_name": source.get("source_name"),
            "observation_window": source.get("observation_window"),
            "method_summary": source.get("method_summary"),
        },
        source_type="research_report",
        source_citations=[
            {
                "type": "local_report",
                "title": str(source.get("source_name") or "platform title report"),
                "path": str(source.get("source_report") or ""),
                "observed": str(source.get("observation_window") or ""),
            }
        ],
        confidence=0.86,
        coverage_score=0.9,
    )


async def seed(source_path: Path, *, dry_run: bool = False) -> list[MaterialEntry]:
    source = _load_yaml(source_path)
    patterns = source.get("patterns")
    if not isinstance(patterns, dict):
        raise ValueError(f"Missing patterns mapping: {source_path}")
    entries = [
        _entry_from_pattern(source, str(key), pattern)
        for key, pattern in patterns.items()
        if isinstance(pattern, dict)
    ]
    if dry_run:
        return entries
    async with session_scope() as session:
        written = []
        for entry in entries:
            written.append(await insert_entry(session, entry))
        return written


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Seed platform title methodology patterns into material_library."
    )
    parser.add_argument("--source", default=str(DEFAULT_SOURCE), help="YAML source file.")
    parser.add_argument("--dry-run", action="store_true", help="Print rows without writing.")
    args = parser.parse_args()

    entries = asyncio.run(seed(Path(args.source).resolve(), dry_run=args.dry_run))
    for entry in entries:
        print(f"{entry.dimension}\t{entry.slug}\t{entry.name}")
    print(f"{'[DRY RUN] ' if args.dry_run else ''}platform_title_patterns={len(entries)}")


if __name__ == "__main__":
    main()
