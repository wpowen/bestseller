#!/usr/bin/env python3
"""Run the production 去AI味 cleanup on an already-written chapter file.

This is the same logic the production pipeline runs after a draft is finalised
(``run_ai_flavor_gate`` → ``needs_deslop_revise`` → ``revise_prose_deslop``),
exposed as a standalone tool so chapters produced by *bypass* paths (benchmark
scripts, hand edits, older runs) can still be cleaned with the latest detector
+ rewrite. Use it as a post-process step for any generation path that does not
already go through ``pipelines.finalize``.

Usage:
    python scripts/deslop_chapter.py output/fanren-bench-v3/chapter-001.md
    python scripts/deslop_chapter.py output/fanren-bench-v3/*.md --rounds 3
    python scripts/deslop_chapter.py FILE --dry-run   # report only, no write
"""

from __future__ import annotations

import argparse
import asyncio
import glob
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from bestseller.infra.db.session import create_engine, create_session_factory
from bestseller.services.ai_flavor.detector import detect
from bestseller.services.ai_flavor_gate import (
    AiFlavorGateConfig,
    needs_deslop_revise,
    run_ai_flavor_gate,
)
from bestseller.services.deslop_revise import revise_prose_deslop
from bestseller.settings import load_settings

_NEG_ACTION = re.compile(r"(?:他|她|它)没(?:有)?(?:去)?[一-鿿]{1,4}(?=[。，,！？\n])")


def _stats(text: str) -> str:
    r = detect(text, language="zh")
    cjk = sum(1 for c in text if "一" <= c <= "鿿")
    neg = len(_NEG_ACTION.findall(text))
    cats = ",".join(sorted({s.category for s in r.spans})) or "干净"
    return f"{cjk}字 score={r.overall_score:.0f} 他没X={neg} [{cats}]"


async def _run(args: argparse.Namespace) -> int:
    paths: list[Path] = []
    for g in args.files:
        paths.extend(Path(p) for p in sorted(glob.glob(g)))
    paths = [p for p in paths if p.is_file() and p.suffix == ".md"]
    if not paths:
        print("no .md files matched", file=sys.stderr)
        return 2

    settings = load_settings()
    cfg = AiFlavorGateConfig(write_audit_file=False)
    engine = create_engine(settings)
    session_factory = create_session_factory(engine=engine)

    try:
        for path in paths:
            # Keep a markdown heading (# 第N章…) out of the rewrite so the model
            # never drops or mangles the title.
            raw = path.read_text(encoding="utf-8")
            head = ""
            body = raw
            m = re.match(r"(#[^\n]*\n+)", raw)
            if m:
                head, body = m.group(1), raw[m.end():]

            outcome = run_ai_flavor_gate(
                chapter_number=0, content_md=body, language="zh-CN",
                config=cfg, llm_rewriter=None, project_output_dir=None,
            )
            before = _stats(body)
            if not needs_deslop_revise(outcome):
                print(f"✓ {path.name}: 已干净，跳过 | {before}")
                continue
            target = sum(1 for c in body if "一" <= c <= "鿿")
            async with session_factory() as session:
                cleaned = await revise_prose_deslop(
                    session, settings, content=body, language="zh-CN",
                    project_id=None, target_chars=target, rounds=args.rounds,
                )
                await session.rollback()
            after = _stats(cleaned)
            if args.dry_run:
                print(f"~ {path.name} (dry-run)\n    before: {before}\n    after : {after}")
            else:
                path.write_text(head + cleaned, encoding="utf-8")
                print(f"✦ {path.name} 已清洗写回\n    before: {before}\n    after : {after}")
    finally:
        await engine.dispose()
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("files", nargs="+", help=".md chapter path(s) or glob(s)")
    ap.add_argument("--rounds", type=int, default=3)
    ap.add_argument("--dry-run", action="store_true", help="report before/after, do not write")
    return asyncio.run(_run(ap.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
