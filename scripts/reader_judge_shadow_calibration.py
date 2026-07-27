"""Report what enforcing the reader-judge voice floors would have done (plan B1).

Read-only. Pulls the ``reader_judge`` blobs the shadow judge already wrote into
chapter metadata and projects, per candidate threshold, how many chapters a
hard floor would have blocked. Nothing is scored, written, or gated here.

Prerequisite: the shadow judge must actually have run, i.e.
``reader_quality_gate.enable_llm_reader_judge: true`` with
``reader_judge_audit_only: true``. Without that the report will correctly say
it has no sample rather than inventing one.

Usage:

  python scripts/reader_judge_shadow_calibration.py                  # whole library
  python scripts/reader_judge_shadow_calibration.py --slug my-book   # one book
  python scripts/reader_judge_shadow_calibration.py --json out.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sqlalchemy import select  # noqa: E402

from bestseller.infra.db.models import ChapterModel, ProjectModel  # noqa: E402
from bestseller.infra.db.session import session_scope  # noqa: E402
from bestseller.services.reader_judge_calibration import (  # noqa: E402
    DEFAULT_THRESHOLDS,
    calibrate_voice_axes,
)
from bestseller.settings import load_settings  # noqa: E402


async def _collect(slug: str | None) -> list[dict]:
    settings = load_settings()
    async with session_scope(settings) as session:
        stmt = select(ChapterModel.metadata_json)
        if slug:
            project_id = await session.scalar(
                select(ProjectModel.id).where(ProjectModel.slug == slug)
            )
            if project_id is None:
                raise SystemExit(f"project not found: {slug}")
            stmt = stmt.where(ChapterModel.project_id == project_id)
        rows = await session.execute(stmt)
        return [row[0] or {} for row in rows.all()]


def _render(report: dict) -> str:
    lines = [
        "reader-judge voice-axis shadow calibration",
        f"  chapters: {report['chapters_judged']} judged / "
        f"{report['chapters_total']} total",
        "",
    ]
    for axis, stats in report["axes"].items():
        if not stats["samples"]:
            lines.append(f"  {axis}: no samples")
            continue
        lines.append(
            f"  {axis}: n={stats['samples']} mean={stats['mean']} "
            f"p10={stats['p10']} p25={stats['p25']} median={stats['median']} "
            f"p90={stats['p90']}"
        )
        for threshold in report["thresholds"]:
            rate = stats["would_block_rate"].get(threshold, 0.0)
            count = stats["would_block"].get(threshold, 0)
            marker = " <- configured" if threshold == report["target_threshold"] else ""
            lines.append(
                f"      floor {threshold:.2f}: would block {count} "
                f"({rate:.0%}){marker}"
            )
    lines.extend(
        [
            "",
            f"  ready_to_enforce: {report['ready_to_enforce']}",
            f"  {report['recommendation']}",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--slug", default=None, help="calibrate one project only")
    parser.add_argument("--json", dest="json_out", default=None)
    parser.add_argument(
        "--thresholds",
        default=",".join(str(t) for t in DEFAULT_THRESHOLDS),
        help="comma-separated candidate floors",
    )
    args = parser.parse_args()

    thresholds = tuple(
        float(part) for part in str(args.thresholds).split(",") if part.strip()
    )
    metadatas = asyncio.run(_collect(args.slug))
    report = calibrate_voice_axes(metadatas, thresholds=thresholds)

    print(_render(report))
    if args.json_out:
        out = Path(args.json_out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"\n  json -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
