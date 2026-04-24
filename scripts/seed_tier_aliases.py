"""Seed ``projects.invariants_json.power_system`` with tier order + aliases.

Why this exists
---------------
``services.contradiction._check_power_tier_regression`` ranks ``power_tier``
strings by their position in ``invariants_json.power_system.tiers``. When
the project predates that contract, ``power_system`` is missing, the
ranker has no canonical order to lean on, and (after Phase 1b of the
《道种破虚》 rescue plan) every comparison short-circuits to "unknown" — so
no regression warnings fire at all.

This script restores meaningful checks by writing an explicit tier ladder
and a ``tier_aliases`` map that collapses noisy snapshots ("中阶巅峰
（筑基大圆满，触及金丹壁垒）", "mid_tier", "炼气一层"…) onto canonical
labels (炼气 / 筑基 / 金丹 / …).

Usage
-----
    # dry-run: print what would be written, no DB changes
    python scripts/seed_tier_aliases.py \
        --project-slug xianxia-upgrade-1776137730 --dry-run

    # apply (default preset = xianxia-8-tier ladder)
    python scripts/seed_tier_aliases.py \
        --project-slug xianxia-upgrade-1776137730 --apply

    # apply a different preset
    python scripts/seed_tier_aliases.py \
        --project-slug some-other --apply --preset xianxia-8-tier

Idempotent: if ``power_system.tiers`` already matches the chosen preset
the script reports SKIP. Pass ``--force`` to overwrite.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any

_THIS = Path(__file__).resolve()
_SRC = _THIS.parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from sqlalchemy import select  # noqa: E402

from bestseller.infra.db.models import ProjectModel  # noqa: E402
from bestseller.infra.db.session import session_scope  # noqa: E402
from bestseller.settings import load_settings  # noqa: E402


# ---------------------------------------------------------------------------
# Presets
# ---------------------------------------------------------------------------
# Each preset is the literal payload that will be stored under
# invariants_json["power_system"]. Keys mirror the contract that
# services/contradiction.py reads.
#
# Ordering of ``tiers`` is canonical low → high. ``tier_aliases`` maps
# any noisy label encountered in character_state_snapshots onto a
# canonical entry (string in ``tiers``).

PRESETS: dict[str, dict[str, Any]] = {
    # Standard 仙侠/修仙 ladder used by 《道种破虚》 and similar books.
    "xianxia-8-tier": {
        "tiers": [
            "炼气",
            "筑基",
            "金丹",
            "元婴",
            "化神",
            "炼虚",
            "合体",
            "大乘",
        ],
        # Aliases are looked up first by exact match, then by substring
        # (see _canonical in contradiction.py). So we list the most-common
        # exact tokens; modifier-rich variants like "中阶巅峰（筑基大圆满，
        # 触及金丹壁垒）" are caught by substring match against "筑基".
        "tier_aliases": {
            # Old short ladder collapsed onto canonical.
            "低阶": "炼气",
            "中阶": "筑基",
            "高阶": "金丹",
            "顶层": "化神",
            # English / unknown — explicitly map to a sentinel so the
            # ranker treats them as unknown (no compare).
            "mid_tier": "筑基",
            # Stage-numbered 炼气 variants.
            "炼气一层": "炼气",
            "炼气六层": "炼气",
            "炼气九层": "炼气",
            "炼气期": "炼气",
            "炼气期九层": "炼气",
            "炼气巅峰": "炼气",
            # 筑基 variants.
            "筑基初期": "筑基",
            "筑基中期": "筑基",
            "筑基后期": "筑基",
            "筑基九层": "筑基",
            "筑基期": "筑基",
            "筑基巅峰": "筑基",
            "筑基期巅峰": "筑基",
            "筑基初期巅峰": "筑基",
            # 金丹 variants.
            "金丹中期": "金丹",
            "金丹后期": "金丹",
            "金丹期": "金丹",
            "金丹期以上": "金丹",
            # 元婴 variants.
            "元婴后期": "元婴",
            "元婴巅峰": "元婴",
            "元婴期": "元婴",
            # 化神 variants.
            "化神期": "化神",
            "化神期以上": "化神",
            # 道祖-tier — out of normal ladder; map to top.
            "道祖级": "大乘",
            "道祖级别": "大乘",
            "因果道祖": "大乘",
            "因果道祖级别": "大乘",
            "远古存在": "大乘",
            "远古级别": "大乘",
            "远古大能": "大乘",
            "远古级": "大乘",
        },
    },
}


def _power_system_matches(existing: dict[str, Any], preset: dict[str, Any]) -> bool:
    """Idempotency check: are tiers + alias map already what we'd write?"""

    if not isinstance(existing, dict):
        return False
    if list(existing.get("tiers") or []) != list(preset.get("tiers") or []):
        return False
    if dict(existing.get("tier_aliases") or {}) != dict(
        preset.get("tier_aliases") or {}
    ):
        return False
    return True


async def run(
    *,
    slug: str,
    preset_name: str,
    apply: bool,
    force: bool,
) -> int:
    if preset_name not in PRESETS:
        print(
            f"[seed_tier_aliases] unknown preset '{preset_name}'; "
            f"available: {sorted(PRESETS)}",
            file=sys.stderr,
        )
        return 2

    preset = PRESETS[preset_name]
    settings = load_settings()

    async with session_scope(settings) as session:
        stmt = select(ProjectModel).where(ProjectModel.slug == slug)
        project = (await session.execute(stmt)).scalar_one_or_none()
        if project is None:
            print(
                f"[seed_tier_aliases] project '{slug}' not found",
                file=sys.stderr,
            )
            return 2

        invariants = deepcopy(project.invariants_json or {})
        existing = invariants.get("power_system") or {}

        if (
            isinstance(existing, dict)
            and _power_system_matches(existing, preset)
            and not force
        ):
            print(f"[seed_tier_aliases] {slug}: SKIP (already matches preset)")
            return 0

        # Merge: keep any extra power_system keys the project already has
        # (e.g. future fields), only overwrite tiers + tier_aliases.
        merged = dict(existing) if isinstance(existing, dict) else {}
        merged["tiers"] = list(preset["tiers"])
        merged["tier_aliases"] = dict(preset["tier_aliases"])
        invariants["power_system"] = merged

        diff_summary = {
            "tiers": merged["tiers"],
            "tier_aliases_count": len(merged["tier_aliases"]),
        }
        print(
            f"[seed_tier_aliases] {slug}: would write\n"
            f"{json.dumps(diff_summary, ensure_ascii=False, indent=2)}"
        )

        if not apply:
            print(f"[seed_tier_aliases] {slug}: DRY-RUN (no commit)")
            return 0

        project.invariants_json = invariants
        await session.flush()
        print(f"[seed_tier_aliases] {slug}: APPLIED")
        return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-slug", required=True, dest="slug")
    parser.add_argument(
        "--preset",
        default="xianxia-8-tier",
        choices=sorted(PRESETS),
        help="Tier ladder preset to seed.",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true", help="Commit changes")
    mode.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        help="Print intended payload, no commit (default)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite an existing matching preset",
    )
    args = parser.parse_args(argv)

    return asyncio.run(
        run(
            slug=args.slug,
            preset_name=args.preset,
            apply=args.apply,
            force=args.force,
        )
    )


if __name__ == "__main__":
    sys.exit(main())
