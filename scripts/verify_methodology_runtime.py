#!/usr/bin/env python3
"""Verify that compile_methodology produces non-empty runtime blocks."""

from __future__ import annotations

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bestseller.services.methodology_compiler import (
    ChapterPosition,
    MethodologyStage,
    compile_methodology,
)


def main() -> int:
    cases = [
        (
            "PROSE_SCENE+suspense-mystery+ch42",
            {
                "stage": MethodologyStage.PROSE_SCENE,
                "prompt_pack_key": "suspense-mystery",
                "language": "zh-CN",
                "chapter_no": 42,
                "chapter_position": ChapterPosition.MIDGAME,
                "token_budget": 1500,
            },
        ),
        (
            "CONCEPTION+suspense-mystery",
            {
                "stage": MethodologyStage.CONCEPTION,
                "prompt_pack_key": "suspense-mystery",
                "language": "zh-CN",
                "token_budget": 800,
            },
        ),
        (
            "OUTLINE_BOOK+suspense-mystery",
            {
                "stage": MethodologyStage.OUTLINE_BOOK,
                "prompt_pack_key": "suspense-mystery",
                "language": "zh-CN",
                "token_budget": 800,
            },
        ),
        (
            "PROSE_SCENE+None_pack",
            {
                "stage": MethodologyStage.PROSE_SCENE,
                "prompt_pack_key": None,
                "language": "zh-CN",
                "chapter_no": 10,
                "token_budget": 1500,
            },
        ),
    ]

    failures: list[str] = []
    for label, kwargs in cases:
        result = compile_methodology(**kwargs)
        if not result.text:
            failures.append(f"FAIL {label}: empty text (sources={result.used_sources})")
            continue
        if "【题材方法论·" not in result.text:
            failures.append(f"FAIL {label}: missing heading marker (head={result.text[:100]!r})")
            continue
        print(
            f"OK {label}: {len(result.text)} chars, "
            f"{result.estimated_tokens} tokens, sources={result.used_sources}"
        )

    if failures:
        print("\n".join(failures), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
