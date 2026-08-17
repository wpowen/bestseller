"""L3 真机端到端验证 — novel-generator 融合 P0 三件套。

What this proves (per docs/开发与验证标准)
------------------------------------------
Against the LIVE docker stack DB, on a real book:

①  story-bible 导出：新增 diagrams.md 生成且 mermaid 块完整；
    既有 6 个 markdown 文件在 before(旧代码)/after(新代码) 间逐字节不变。
②  章节契约回执：真章正文 + 真 scene_cards 跑出结构化对账。
③  伏笔在飞存量：真 clue 行跑出新观测量；legacy 字段 before/after 一致。

Zero-token: all three paths are deterministic — no LLM call exists on them.
Zero side effects: read-only session, never committed; a table-count
snapshot is taken before and after and asserted equal.

Usage
-----
    # 新代码工作树上：
    python scripts/verify_novel_generator_fusion_p0_e2e.py --phase after

    # git stash 后（旧代码）：
    python scripts/verify_novel_generator_fusion_p0_e2e.py --phase before

    # git stash pop 后对比：
    python scripts/verify_novel_generator_fusion_p0_e2e.py --compare

Artifacts land in <out>/{before,after}/ (default: scratch dir under /tmp).
"""

from __future__ import annotations

# ruff: noqa: RUF002 — Chinese punctuation is intentional.
import argparse
import asyncio
import json
from pathlib import Path
import sys
from typing import Any

_THIS = Path(__file__).resolve()
_SRC = _THIS.parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from sqlalchemy import func, select  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402

from bestseller.infra.db.models import (  # noqa: E402
    ChapterDraftVersionModel,
    ChapterModel,
    ClueModel,
    PayoffModel,
    ProjectModel,
    SceneCardModel,
)
from bestseller.infra.db.session import session_scope  # noqa: E402
from bestseller.services.foreshadowing import analyze_foreshadowing_density  # noqa: E402
from bestseller.services.story_bible_export import export_story_bible_to_disk  # noqa: E402
from bestseller.settings import load_settings  # noqa: E402

_LEGACY_BIBLE_FILES = (
    "premise.md",
    "world.md",
    "characters.md",
    "volume-plan.md",
    "plot-arcs.md",
    "writing-profile.md",
)

_SIDE_EFFECT_TABLES = {
    "projects": ProjectModel,
    "chapters": ChapterModel,
    "chapter_draft_versions": ChapterDraftVersionModel,
    "clues": ClueModel,
}


async def _table_counts(session: AsyncSession) -> dict[str, int]:
    counts: dict[str, int] = {}
    for name, model in _SIDE_EFFECT_TABLES.items():
        counts[name] = int(await session.scalar(select(func.count()).select_from(model)))
    from bestseller.infra.db.models import LlmRunModel

    counts["llm_runs"] = int(
        await session.scalar(select(func.count()).select_from(LlmRunModel))
    )
    return counts


async def _pick_project(session: AsyncSession) -> tuple[str, Any]:
    """Project with the most current chapter drafts = the most real book."""
    row = (
        await session.execute(
            select(
                ProjectModel.slug,
                ProjectModel.id,
                func.count(ChapterDraftVersionModel.id).label("n"),
            )
            .join(ChapterDraftVersionModel, ChapterDraftVersionModel.project_id == ProjectModel.id)
            .where(ChapterDraftVersionModel.is_current.is_(True))
            .group_by(ProjectModel.slug, ProjectModel.id)
            .order_by(func.count(ChapterDraftVersionModel.id).desc())
        )
    ).first()
    if row is None:
        raise SystemExit("no project with current chapter drafts found")
    print(f"[pick] project={row.slug} current_drafts={row.n}")
    return row.slug, row.id


async def run_phase(phase: str, out_root: Path) -> int:
    settings = load_settings()
    out_dir = out_root / phase
    out_dir.mkdir(parents=True, exist_ok=True)

    async with session_scope(settings) as session:
        counts_before = await _table_counts(session)
        slug, project_id = await _pick_project(session)

        # ── ① story-bible export ────────────────────────────────
        bible_root = out_dir / "bible"
        dest = await export_story_bible_to_disk(
            session=session,
            project_slug=slug,
            output_root=bible_root,
        )
        print(f"[bible] wrote {dest}")

        # ── ② contract receipt on a real chapter ────────────────
        draft_row = (
            await session.execute(
                select(ChapterDraftVersionModel, ChapterModel)
                .join(ChapterModel, ChapterDraftVersionModel.chapter_id == ChapterModel.id)
                .where(
                    ChapterDraftVersionModel.project_id == project_id,
                    ChapterDraftVersionModel.is_current.is_(True),
                )
                .order_by(ChapterModel.chapter_number.desc())
            )
        ).first()
        receipt_payload: dict[str, Any] = {"available": False}
        if draft_row is not None:
            draft, chapter = draft_row
            scenes = list(
                await session.scalars(
                    select(SceneCardModel)
                    .where(SceneCardModel.chapter_id == chapter.id)
                    .order_by(SceneCardModel.scene_number)
                )
            )
            try:
                from bestseller.services.chapter_contract_receipt import (
                    build_chapter_contract_receipt,
                )

                receipt = build_chapter_contract_receipt(
                    chapter_text=draft.content_md,
                    chapter_number=chapter.chapter_number,
                    scenes=scenes,
                )
                receipt_payload = {"available": True, **receipt.to_dict()}
                print(
                    f"[receipt] ch{chapter.chapter_number}: declared="
                    f"{len(receipt.declared_participants)} missing="
                    f"{list(receipt.missing_participants)} silent="
                    f"{list(receipt.silent_participants)} coverage="
                    f"{receipt.participant_coverage:.2f}"
                )
            except ImportError:
                print("[receipt] module not present (before-phase expected)")
        (out_dir / "receipt.json").write_text(
            json.dumps(receipt_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        # ── ③ foreshadowing inventory on real clues ─────────────
        clues = list(
            await session.scalars(
                select(ClueModel).where(ClueModel.project_id == project_id)
            )
        )
        payoffs = list(
            await session.scalars(
                select(PayoffModel).where(PayoffModel.project_id == project_id)
            )
        )
        frontier = int(
            await session.scalar(
                select(func.max(ChapterModel.chapter_number)).where(
                    ChapterModel.project_id == project_id
                )
            )
            or 0
        )
        density = analyze_foreshadowing_density(
            clues=clues, payoffs=payoffs, total_chapters=max(frontier, 1)
        )
        density_payload = density.model_dump(mode="json")
        (out_dir / "foreshadowing.json").write_text(
            json.dumps(density_payload, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        print(
            f"[foreshadow] clues={len(clues)} frontier=ch{frontier} "
            f"balance={density_payload.get('balance_score')} "
            f"open={density_payload.get('open_clue_count', 'n/a')} "
            f"max_age={density_payload.get('max_open_clue_age_chapters', 'n/a')}"
        )

        counts_after = await _table_counts(session)

    # side-effect + zero-token proof
    if counts_before != counts_after:
        print(f"[FAIL] table counts changed: {counts_before} -> {counts_after}")
        return 1
    print(f"[side-effects] table counts unchanged (incl. llm_runs={counts_after['llm_runs']}) ✓")
    (out_dir / "meta.json").write_text(
        json.dumps({"phase": phase, "slug": slug, "counts": counts_after}, indent=2),
        encoding="utf-8",
    )
    return 0


def compare(out_root: Path) -> int:
    before, after = out_root / "before", out_root / "after"
    failures: list[str] = []

    slug = json.loads((after / "meta.json").read_text())["slug"]
    b_bible = before / "bible" / slug / "story-bible"
    a_bible = after / "bible" / slug / "story-bible"

    # ① legacy files byte-identical; diagrams.md only in after
    for name in _LEGACY_BIBLE_FILES:
        b, a = (b_bible / name).read_bytes(), (a_bible / name).read_bytes()
        status = "IDENTICAL" if b == a else "DIFFERS"
        if b != a:
            failures.append(f"legacy bible file changed: {name}")
        print(f"[compare] {name}: {status}")
    diagrams = a_bible / "diagrams.md"
    if not diagrams.exists():
        failures.append("diagrams.md missing in after")
    else:
        content = diagrams.read_text(encoding="utf-8")
        fences = content.count("```mermaid")
        closed = content.count("```") == fences * 2
        print(f"[compare] diagrams.md: {fences} mermaid block(s), fences closed={closed}")
        if fences and not closed:
            failures.append("diagrams.md has unclosed fences")
    if (b_bible / "diagrams.md").exists():
        failures.append("diagrams.md unexpectedly present in before")

    # ③ foreshadowing legacy fields equal
    b_fs = json.loads((before / "foreshadowing.json").read_text())
    a_fs = json.loads((after / "foreshadowing.json").read_text())
    legacy_keys = set(b_fs)  # before-run keys ARE the legacy contract
    diffs = {k for k in legacy_keys if b_fs.get(k) != a_fs.get(k)}
    if diffs:
        failures.append(f"foreshadowing legacy fields changed: {sorted(diffs)}")
    print(f"[compare] foreshadowing legacy fields ({len(legacy_keys)}): "
          f"{'IDENTICAL' if not diffs else sorted(diffs)}")
    new_keys = sorted(set(a_fs) - legacy_keys)
    print(f"[compare] foreshadowing new fields: {new_keys}")

    # ② receipt only in after
    b_receipt = json.loads((before / "receipt.json").read_text())
    a_receipt = json.loads((after / "receipt.json").read_text())
    if b_receipt.get("available"):
        failures.append("receipt unexpectedly available in before phase")
    if not a_receipt.get("available"):
        failures.append("receipt missing in after phase")
    else:
        print(
            f"[compare] receipt(after): declared={len(a_receipt['declared_participants'])} "
            f"missing={a_receipt['missing_participants']} "
            f"silent={a_receipt['silent_participants']} clean={a_receipt['clean']}"
        )

    if failures:
        print("\n[VERDICT] FAIL")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("\n[VERDICT] PASS — legacy byte-invariant, new observations live on real book data")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=["before", "after"])
    parser.add_argument("--compare", action="store_true")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.compare:
        return compare(args.out.resolve())
    if not args.phase:
        parser.error("need --phase or --compare")
    return asyncio.run(run_phase(args.phase, args.out.resolve()))


if __name__ == "__main__":
    sys.exit(main())
