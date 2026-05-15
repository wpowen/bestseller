"""Layer 2 surgical patch — generate editor LLM tasks from a Layer 1 audit CSV.

Workflow::

    # Step 1: Layer 1 audit (no LLM)
    python scripts/quality_levers_retrofit_audit.py --slug exorcist-detective-1778428166

    # Step 2: dry-run — see exactly which paragraphs / sentences need patching
    python scripts/quality_levers_retrofit_patch.py \\
        --slug exorcist-detective-1778428166 \\
        --priority critical,high \\
        --dry-run

    # Step 3: execute (will call editor LLM once per patch point)
    python scripts/quality_levers_retrofit_patch.py \\
        --slug exorcist-detective-1778428166 \\
        --priority critical,high \\
        --execute

The script reads ``output/<slug>/audits/quality-retrofit/window-*.csv`` (the
Layer 1 output) and for every chapter whose ``priority`` matches the filter
emits a precise patch task: which paragraphs hold the violation, which
``cause_id`` from the ``rejection_repair_playbook`` to apply, and what the
expected character-budget for the patched output is.

``--execute`` actually calls the editor LLM via the framework's
``services.llm`` wrapper. Without it the script is fully read-only and
produces a JSON patch plan suitable for human review.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT / "src"))

from bestseller.services.quality_levers import (  # noqa: E402
    detect_psychological_dumping,
    scan_abstract_sensory_terms,
    scan_banned_patterns,
)


_PARAGRAPH_SPLIT_RE = re.compile(r"\n\s*\n")


@dataclass
class PatchPoint:
    """One precise patch instruction the editor LLM should execute."""

    chapter_number: int
    cause_id: str
    location: str  # "paragraph 7" / "sentence containing 阴森" etc.
    issue_summary: str
    snippet: str
    repair_action_summary: str
    expected_max_chars_delta: int


@dataclass
class ChapterPatchPlan:
    """Aggregate of patch points for one chapter."""

    slug: str
    chapter_number: int
    priority: str
    cause_ids: tuple[str, ...]
    patch_points: list[PatchPoint] = field(default_factory=list)


def _load_audit_rows(slug: str) -> list[dict[str, str]]:
    audit_dir = _REPO_ROOT / "output" / slug / "audits" / "quality-retrofit"
    if not audit_dir.exists():
        return []
    csv_files = sorted(audit_dir.glob("window-*.csv"))
    if not csv_files:
        return []
    # Use the most recent (highest end-chapter) audit.
    rows: list[dict[str, str]] = []
    with csv_files[-1].open(encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            rows.append(row)
    return rows


def _read_chapter(slug: str, chapter_number: int) -> tuple[str, list[str]]:
    """Return the chapter text + paragraphs (without the heading line)."""

    path = _REPO_ROOT / "output" / slug / f"chapter-{chapter_number:03d}.md"
    text = path.read_text(encoding="utf-8")
    body = "\n".join(
        line for line in text.split("\n") if not line.startswith("# ")
    )
    paragraphs = [p.strip() for p in _PARAGRAPH_SPLIT_RE.split(body) if p.strip()]
    return text, paragraphs


def _patch_points_for_banned_patterns(
    chapter_number: int,
    paragraphs: list[str],
    chapter_text: str,
) -> list[PatchPoint]:
    """Generate one patch task per AI-voice ban-pattern hit."""

    result = scan_banned_patterns(chapter_text)
    points: list[PatchPoint] = []
    for hit in result.hits:
        # Find the first paragraph that triggered this pattern so the
        # editor knows exactly where to look. We keep the search cheap —
        # if multiple paragraphs match we only record the first; the
        # editor can re-scan.
        target_index = next(
            (
                index
                for index, paragraph in enumerate(paragraphs, start=1)
                if _pattern_matches(hit.pattern_id, paragraph)
            ),
            0,
        )
        snippet = paragraphs[target_index - 1] if target_index else ""
        points.append(
            PatchPoint(
                chapter_number=chapter_number,
                cause_id="ai_voice",
                location=(
                    f"paragraph {target_index}"
                    if target_index
                    else "scan whole chapter"
                ),
                issue_summary=f"AI-voice pattern '{hit.pattern_id}' fired {hit.count}x",
                snippet=snippet[:120],
                repair_action_summary=(
                    f"按 rejection_repair_playbook.ai_voice 替换该模式; "
                    f"具体: pattern_id={hit.pattern_id}"
                ),
                expected_max_chars_delta=40,
            )
        )
    return points


def _pattern_matches(pattern_id: str, text: str) -> bool:
    """Lightweight inline copy of the regex shortcuts used by ``scan_banned_patterns``."""

    rules: dict[str, str] = {
        "parallel_action": r"一边[^\n]{0,40}一边",
        "not_only_but_also": r"不仅[^\n]{0,40}还",
        "looks_like_actually": r"看似[^\n]{0,40}实则",
        "smooth_transition": (
            r"那不是最要命的|最要命的是|更要命的是|更糟的是|更重要的是"
        ),
        "emotion_label": r"他感到|她感到|他心想|她心想|他意识到|她意识到|忽然明白",
        "explanatory_dialogue": r"这意味着|这是因为|原来是|原来如此",
        "weak_verbs": r"做了一个|做出了|进行了一次|实施了|实现了",
        "cliched_metaphor": r"像[^\n，。；！？]{1,10}一样[^\n，。；！？]{0,15}",
    }
    regex = rules.get(pattern_id)
    if not regex:
        return False
    return bool(re.search(regex, text))


def _patch_points_for_abstract_sensory(
    chapter_number: int,
    paragraphs: list[str],
    chapter_text: str,
) -> list[PatchPoint]:
    result = scan_abstract_sensory_terms(chapter_text)
    points: list[PatchPoint] = []
    for word, count in result.hits:
        target_index = next(
            (
                index
                for index, paragraph in enumerate(paragraphs, start=1)
                if word in paragraph
            ),
            0,
        )
        snippet = paragraphs[target_index - 1] if target_index else ""
        points.append(
            PatchPoint(
                chapter_number=chapter_number,
                cause_id="weak_prose",
                location=f"paragraph {target_index}" if target_index else "search '{word}'",
                issue_summary=f"abstract sensory term '{word}' x{count}",
                snippet=snippet[:120],
                repair_action_summary=(
                    f"将 '{word}' 替换为具体物件 / 动作描写 (按 sensory_inventory 的 "
                    "scene_type 必带感官)"
                ),
                expected_max_chars_delta=60,
            )
        )
    return points


def _patch_points_for_dumping(
    chapter_number: int,
    chapter_text: str,
) -> list[PatchPoint]:
    result = detect_psychological_dumping(chapter_text)
    points: list[PatchPoint] = []
    for hit in result.hits:
        points.append(
            PatchPoint(
                chapter_number=chapter_number,
                cause_id="weak_immersion",
                location=f"paragraph {hit.paragraph_index} (length={hit.cjk_length})",
                issue_summary=(
                    f"psychological dumping: {hit.cjk_length} CJK chars + "
                    f"{hit.background_marker_count} background markers"
                ),
                snippet=hit.snippet,
                repair_action_summary=(
                    "拆分为'动作-停顿-动作'多段，把背景信息后移到对话或动作触发"
                ),
                expected_max_chars_delta=200,
            )
        )
    return points


def build_chapter_patch_plan(
    slug: str,
    chapter_number: int,
    priority: str,
    cause_ids: tuple[str, ...],
) -> ChapterPatchPlan:
    chapter_text, paragraphs = _read_chapter(slug, chapter_number)
    points: list[PatchPoint] = []
    if "ai_voice" in cause_ids:
        points.extend(_patch_points_for_banned_patterns(chapter_number, paragraphs, chapter_text))
    if "weak_prose" in cause_ids:
        points.extend(_patch_points_for_abstract_sensory(chapter_number, paragraphs, chapter_text))
    if "weak_immersion" in cause_ids:
        points.extend(_patch_points_for_dumping(chapter_number, chapter_text))
    return ChapterPatchPlan(
        slug=slug,
        chapter_number=chapter_number,
        priority=priority,
        cause_ids=cause_ids,
        patch_points=points,
    )


def _parse_priority(value: str) -> set[str]:
    parts = {item.strip() for item in value.split(",") if item.strip()}
    if not parts:
        return {"critical"}
    valid = {"critical", "high", "medium", "ok"}
    bad = parts - valid
    if bad:
        raise SystemExit(f"Unknown priority value(s): {sorted(bad)} (allowed: {sorted(valid)})")
    return parts


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate or execute Layer-2 surgical patches based on a Layer-1 audit."
    )
    parser.add_argument("--slug", required=True)
    parser.add_argument(
        "--priority",
        default="critical,high",
        help="Comma list (default: critical,high). Allowed: critical|high|medium|ok",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Only plan patches for the first N matching chapters (0 = all).",
    )
    parser.add_argument(
        "--out-plan",
        default=None,
        help=(
            "Override plan-output path. Default: "
            "output/<slug>/audits/quality-retrofit/patch-plan.json"
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        help="Plan only — no LLM calls (default).",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help=(
            "Actually invoke the editor LLM for each patch point. "
            "(Implementation is intentionally deferred to a follow-up patch — "
            "this flag currently warns and exits non-zero.)"
        ),
    )
    args = parser.parse_args()

    if args.execute:
        # Keeping the executor as an explicit follow-up keeps this script
        # safe to run by default; integrating it requires the editor LLM
        # role + JSON-mode response handling, which lives in services/llm.
        print(
            "[execute] not implemented in this revision. Use --dry-run to "
            "produce the patch plan; then wire it into services.editor next.",
            file=sys.stderr,
        )
        return 2

    priorities = _parse_priority(args.priority)
    rows = _load_audit_rows(args.slug)
    if not rows:
        print(
            f"No audit rows found under output/{args.slug}/audits/quality-retrofit/. "
            f"Run quality_levers_retrofit_audit.py first.",
            file=sys.stderr,
        )
        return 1

    matching = [row for row in rows if row.get("priority") in priorities]
    if args.limit and args.limit > 0:
        matching = matching[: args.limit]
    if not matching:
        print(
            f"No chapters match priority filter ({sorted(priorities)}).",
            file=sys.stderr,
        )
        return 0

    plans: list[ChapterPatchPlan] = []
    for row in matching:
        chapter_number = int(row["chapter_number"])
        cause_ids = tuple(
            cause for cause in (row.get("cause_ids") or "").split(";") if cause
        )
        try:
            plan = build_chapter_patch_plan(
                args.slug, chapter_number, row["priority"], cause_ids
            )
            plans.append(plan)
        except FileNotFoundError:
            print(
                f"  ⚠ ch{chapter_number:03d}: chapter file missing — skipped",
                file=sys.stderr,
            )

    out_path = Path(args.out_plan) if args.out_plan else (
        _REPO_ROOT / "output" / args.slug / "audits" / "quality-retrofit" / "patch-plan.json"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(
            [
                {
                    "slug": plan.slug,
                    "chapter_number": plan.chapter_number,
                    "priority": plan.priority,
                    "cause_ids": list(plan.cause_ids),
                    "patch_point_count": len(plan.patch_points),
                    "patch_points": [
                        {
                            "cause_id": p.cause_id,
                            "location": p.location,
                            "issue_summary": p.issue_summary,
                            "snippet": p.snippet,
                            "repair_action_summary": p.repair_action_summary,
                            "expected_max_chars_delta": p.expected_max_chars_delta,
                        }
                        for p in plan.patch_points
                    ],
                }
                for plan in plans
            ],
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    total_points = sum(len(plan.patch_points) for plan in plans)
    print(
        f"Planned {total_points} patch points across {len(plans)} chapters "
        f"(priority filter: {sorted(priorities)})"
    )
    print(f"Plan : {out_path}")
    print("(dry-run only — pass --execute when the editor LLM hook lands)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
