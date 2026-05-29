"""Reverse the focus-qingnang pause and re-enable autowrite on other books.

Background: ``scripts/_deprecated/qingnang_repair/focus_qingnang_deepseek_repair.py``
froze every project except ``exorcist-detective-1778051012`` so the qingnang
repair loop could monopolize LLM capacity. The freeze sets these per-project
metadata keys + downgrades status::

    metadata_json:
      production_paused: true
      production_pause_reason: "focus_qingnang_*"
      generation_resume_blocked_until_repair_audit: true   # set by ad-hoc DB edit
      focus_pause:
        previous_status: <prior project.status>
        ...
    status: "paused"

Rewrite tasks + workflow runs were ALSO mass-cancelled / paused. This script
reverses the project-side gating so the 「续写」 button on the frontend can
re-trigger the autowrite → closure → repair self-chain. By default it does
NOT touch rewrite/workflow rows (they were stale by now — fresh closure
runs will create new ones).

Usage::

    # Dry run (default): print what would change
    uv run python scripts/clear_focus_pause.py

    # Apply to all paused projects except qingnang (the protected target)
    uv run python scripts/clear_focus_pause.py --apply

    # Apply only to one project
    uv run python scripts/clear_focus_pause.py --apply --slug xianxia-upgrade-1776137730

    # Include the protected target too (rare — would let qingnang restart from quickstart)
    uv run python scripts/clear_focus_pause.py --apply --include-target
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import UTC, datetime
import json
from pathlib import Path
import sys
from typing import Any

from sqlalchemy import select

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SRC = _REPO_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from bestseller.infra.db.models import ProjectModel  # noqa: E402
from bestseller.infra.db.session import session_scope  # noqa: E402
from bestseller.settings import load_settings  # noqa: E402

PROTECTED_TARGET_SLUG = "exorcist-detective-1778051012"

# Metadata keys the focus-pause script set on each non-target project.
# Cleared on resume. ``focus_pause`` retained for audit but moved under
# ``focus_pause_history`` so we don't lose the original previous_status.
_PAUSE_FLAGS = (
    "production_paused",
    "production_pause_reason",
    "generation_resume_blocked_until_repair_audit",
    "structural_repair_required",
)


def _clear_pause_metadata(
    raw: dict[str, Any] | None,
    *,
    now: str,
) -> tuple[dict[str, Any], str | None]:
    """Return (new_metadata, restored_previous_status).

    The previous_status is read from ``focus_pause.previous_status`` so the
    project goes back to the status it had before the focus freeze. When that
    field is absent we leave ``status`` alone and let the caller decide.
    """
    metadata = dict(raw or {})
    focus = dict(metadata.get("focus_pause") or {})
    previous_status = focus.get("previous_status") if isinstance(focus, dict) else None

    for key in _PAUSE_FLAGS:
        metadata.pop(key, None)

    if focus:
        history = list(metadata.get("focus_pause_history") or [])
        history.append({**focus, "resumed_at": now})
        metadata["focus_pause_history"] = history[-10:]  # cap audit trail
        metadata.pop("focus_pause", None)

    metadata["focus_pause_cleared_at"] = now
    return metadata, str(previous_status) if previous_status else None


async def run(
    *,
    apply: bool,
    slug_filter: str | None,
    include_target: bool,
) -> dict[str, Any]:
    settings = load_settings()
    now = datetime.now(UTC).isoformat()
    summary: dict[str, Any] = {
        "applied": apply,
        "slug_filter": slug_filter,
        "include_target": include_target,
        "now": now,
        "projects_resumed": [],
        "projects_skipped_already_clear": [],
        "projects_skipped_protected_target": [],
    }
    async with session_scope(settings) as session:
        query = select(ProjectModel).order_by(ProjectModel.slug)
        if slug_filter:
            query = query.where(ProjectModel.slug == slug_filter)
        projects = (await session.scalars(query)).all()
        for project in projects:
            metadata = dict(project.metadata_json or {})
            is_paused = any(metadata.get(flag) for flag in _PAUSE_FLAGS)

            if project.slug == PROTECTED_TARGET_SLUG and not include_target:
                if is_paused:
                    summary["projects_skipped_protected_target"].append(
                        {
                            "slug": project.slug,
                            "current_status": project.status,
                            "current_pause_reason": metadata.get("production_pause_reason"),
                        }
                    )
                continue

            if not is_paused:
                summary["projects_skipped_already_clear"].append({"slug": project.slug})
                continue

            new_metadata, restored_status = _clear_pause_metadata(metadata, now=now)
            row = {
                "slug": project.slug,
                "previous_status": project.status,
                "restored_status": restored_status,
                "cleared_flags": [
                    flag for flag in _PAUSE_FLAGS if metadata.get(flag)
                ],
                "previous_pause_reason": metadata.get("production_pause_reason"),
            }
            summary["projects_resumed"].append(row)

            if apply:
                project.metadata_json = new_metadata
                if restored_status:
                    project.status = restored_status

    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually write changes; default is dry-run.",
    )
    parser.add_argument(
        "--slug",
        default=None,
        help="Only act on this project slug (default: every paused project).",
    )
    parser.add_argument(
        "--include-target",
        action="store_true",
        help=(
            "Also unpause the protected target "
            f"({PROTECTED_TARGET_SLUG}). Rare — only use if you intentionally "
            "want to re-trigger quickstart on qingnang."
        ),
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print full JSON summary instead of human-friendly text.",
    )
    args = parser.parse_args()

    summary = asyncio.run(
        run(
            apply=bool(args.apply),
            slug_filter=args.slug,
            include_target=bool(args.include_target),
        )
    )

    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return

    label = "APPLIED" if summary["applied"] else "DRY-RUN"
    resumed = summary["projects_resumed"]
    skipped_clear = summary["projects_skipped_already_clear"]
    skipped_target = summary["projects_skipped_protected_target"]

    print(f"[{label}] focus-pause clear")
    print(f"  to resume: {len(resumed)}")
    for row in resumed:
        restored = row["restored_status"] or "<unchanged>"
        print(
            f"    - {row['slug']}: status {row['previous_status']!r} -> "
            f"{restored!r}, cleared flags={row['cleared_flags']}, "
            f"reason was {row['previous_pause_reason']!r}"
        )
    if skipped_clear:
        print(f"  already clear: {len(skipped_clear)}")
        for row in skipped_clear[:5]:
            print(f"    - {row['slug']}")
        if len(skipped_clear) > 5:
            print(f"    ... +{len(skipped_clear) - 5} more")
    if skipped_target:
        print(f"  protected target skipped: {len(skipped_target)}")
        for row in skipped_target:
            print(f"    - {row['slug']} (use --include-target to override)")

    if not summary["applied"]:
        print("\nDry-run. Re-run with --apply to persist.")


if __name__ == "__main__":
    main()
