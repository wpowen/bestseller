#!/usr/bin/env python3
"""Validate the *production* AI-flavor gate logic on real chapters.

This drives the exact sequence ``pipelines.py`` runs after a chapter draft is
finalised — ``get_quality_gates_config().ai_flavor`` + ``run_ai_flavor_gate``
with NO llm_rewriter (offline, free, same as production) — and reports the
decision / score / edits so we can check the behaviour matches expectations:

* good fresh prose  -> decision=pass, 0 edits (prose untouched), low score
* mechanical staccato/epiphany -> flagged, but rhythm-family capped so it can't
  alone force a block; genuine over-reliance (epiphany) can still approach block

Usage:
    python scripts/ai_flavor_gate_validate.py FILE [FILE ...]
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from bestseller.services.ai_flavor_gate import run_ai_flavor_gate  # noqa: E402
from bestseller.services.quality_gates_config import get_quality_gates_config  # noqa: E402


def main() -> int:
    files = [Path(a) for a in sys.argv[1:]]
    files = [f for f in files if f.is_file()]
    if not files:
        print("usage: ai_flavor_gate_validate.py FILE [FILE ...]", file=sys.stderr)
        return 2

    cfg = get_quality_gates_config().ai_flavor
    print(
        f"gate config: enabled={cfg.enabled} block_cn={cfg.block_score_cn} "
        f"warn_cn={cfg.warn_score_cn} llm_rewrite={cfg.llm_rewrite_enabled} "
        f"block_on_residual={cfg.block_on_residual}"
    )
    print(f"{'file':<34}{'decision':>9}{'before':>8}{'after':>7}{'edits':>6}  damaged?")
    print("-" * 78)
    for f in files:
        text = f.read_text(encoding="utf-8")
        outcome = run_ai_flavor_gate(
            chapter_number=0,
            content_md=text,
            language="zh-CN",
            config=cfg,
            llm_rewriter=None,  # production passes none -> offline static fixes only
            project_output_dir=None,
        )
        # "damaged?" = did the gate rewrite/delete any prose on this file?
        damaged = "YES" if outcome.patched_text is not None else "no"
        print(
            f"{f.name:<34}{outcome.decision:>9}{outcome.before_score:>8.1f}"
            f"{outcome.after_score:>7.1f}{len(outcome.edits):>6}  {damaged}"
        )
        for edit in outcome.edits:
            print(f"      edit[{edit.strategy}] {edit.before[:40]!r} -> {edit.after[:40]!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
