#!/usr/bin/env python3
"""trim_prompt_packs.py — Strip B-class and C-class fragments from prompt packs.

Batch 3, Stage 5 of the multi-dimensional material library refactor.

== What gets removed ==

B-class fragments (in ``fragments:`` section):
    planner_book_spec, planner_world_spec, planner_cast_spec,
    planner_volume_plan, planner_outline

    These were script-level plot instructions injected directly into the
    Planner LLM context, causing every same-genre project to start with
    identical story structure.  They are replaced by Material Forge §slug
    references when ``enable_reference_style_generation`` is on.

C-class overrides (in ``writing_profile_overrides.character:``):
    golden_finger, growth_curve, tropes

    These hardcode protagonist archetype details that produce character
    clones across same-genre books.  The sanitize function in
    ``writing_profile.py`` already strips them from the merged profile;
    removing them from YAML makes the intent explicit.

C-class root fields:
    obligatory_scenes

    Removing mandatory scene codes eliminates the genre-cloning constraint
    that forced every xianxia book to contain ``first_breakthrough``,
    ``face_slap``, etc.

== Usage ==

    # Dry-run (default) — shows what would change, writes nothing:
    python scripts/trim_prompt_packs.py

    # Apply — write trimmed files in-place:
    python scripts/trim_prompt_packs.py --apply

    # Limit to specific pack(s):
    python scripts/trim_prompt_packs.py --apply --pack xianxia-upgrade-core

    # Only trim one category:
    python scripts/trim_prompt_packs.py --apply --fragments-only
    python scripts/trim_prompt_packs.py --apply --overrides-only
    python scripts/trim_prompt_packs.py --apply --obligatory-only
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

import yaml

# ── B-class fragment keys ──────────────────────────────────────────────────
_B_CLASS_FRAGMENT_KEYS: frozenset[str] = frozenset(
    {
        "planner_book_spec",
        "planner_world_spec",
        "planner_cast_spec",
        "planner_volume_plan",
        "planner_outline",
    }
)

# ── C-class character override keys ───────────────────────────────────────
_C_CLASS_CHARACTER_KEYS: frozenset[str] = frozenset(
    {
        "golden_finger",
        "growth_curve",
        "tropes",
    }
)

# ── Pack directory ─────────────────────────────────────────────────────────
_PACK_DIR = Path(__file__).resolve().parents[1] / "config" / "prompt_packs"


# ── Core trim logic ────────────────────────────────────────────────────────


def trim_pack(
    raw: dict[str, Any],
    *,
    trim_fragments: bool = True,
    trim_overrides: bool = True,
    trim_obligatory: bool = True,
) -> tuple[dict[str, Any], list[str]]:
    """Return a trimmed copy of *raw* and a list of removed field descriptions.

    The original dict is not mutated.
    """
    removed: list[str] = []
    result: dict[str, Any] = dict(raw)

    # ── B-class fragments ──────────────────────────────────────────────────
    if trim_fragments and isinstance(raw.get("fragments"), dict):
        old_frags = dict(raw["fragments"])
        new_frags = {k: v for k, v in old_frags.items() if k not in _B_CLASS_FRAGMENT_KEYS}
        for key in old_frags:
            if key in _B_CLASS_FRAGMENT_KEYS:
                removed.append(f"fragments.{key}")
        result["fragments"] = new_frags

    # ── C-class character overrides ────────────────────────────────────────
    if trim_overrides and isinstance(raw.get("writing_profile_overrides"), dict):
        wpo = dict(raw["writing_profile_overrides"])
        if isinstance(wpo.get("character"), dict):
            old_char = dict(wpo["character"])
            new_char = {k: v for k, v in old_char.items() if k not in _C_CLASS_CHARACTER_KEYS}
            for key in old_char:
                if key in _C_CLASS_CHARACTER_KEYS:
                    removed.append(f"writing_profile_overrides.character.{key}")
            if new_char:
                wpo["character"] = new_char
            else:
                del wpo["character"]
                removed.append("writing_profile_overrides.character (emptied, removed)")
        result["writing_profile_overrides"] = wpo

    # ── C-class obligatory_scenes (root level) ─────────────────────────────
    if trim_obligatory and "obligatory_scenes" in raw:
        del result["obligatory_scenes"]
        removed.append("obligatory_scenes")

    return result, removed


# ── YAML round-trip helpers ────────────────────────────────────────────────


def _load_yaml(path: Path) -> dict[str, Any]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return raw if isinstance(raw, dict) else {}


def _dump_yaml(data: dict[str, Any]) -> str:
    """Dump to YAML with consistent formatting."""
    return yaml.dump(
        data,
        allow_unicode=True,
        default_flow_style=False,
        sort_keys=False,
        width=120,
    )


# ── CLI ────────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Trim B-class / C-class fragments from prompt pack YAML files.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        default=False,
        help="Write trimmed files in-place (default: dry-run only).",
    )
    parser.add_argument(
        "--pack",
        metavar="KEY",
        action="append",
        dest="packs",
        help="Limit to specific pack key(s). May be repeated.",
    )
    parser.add_argument(
        "--fragments-only",
        action="store_true",
        default=False,
        help="Only trim B-class planner_* fragments.",
    )
    parser.add_argument(
        "--overrides-only",
        action="store_true",
        default=False,
        help="Only trim C-class character override fields.",
    )
    parser.add_argument(
        "--obligatory-only",
        action="store_true",
        default=False,
        help="Only trim obligatory_scenes.",
    )
    args = parser.parse_args(argv)

    # Determine which categories to trim
    any_specific = args.fragments_only or args.overrides_only or args.obligatory_only
    do_fragments = args.fragments_only or not any_specific
    do_overrides = args.overrides_only or not any_specific
    do_obligatory = args.obligatory_only or not any_specific

    if not _PACK_DIR.exists():
        print(f"ERROR: prompt pack directory not found: {_PACK_DIR}", file=sys.stderr)
        return 1

    yaml_files = sorted(_PACK_DIR.glob("*.yaml"))
    if not yaml_files:
        print("No YAML files found.", file=sys.stderr)
        return 1

    # Filter by requested pack keys
    if args.packs:
        requested = set(args.packs)
        yaml_files = [
            p for p in yaml_files
            if (yaml.safe_load(p.read_text(encoding="utf-8")) or {}).get("key") in requested
        ]
        if not yaml_files:
            print(f"ERROR: no packs found for keys: {sorted(requested)}", file=sys.stderr)
            return 1

    changed_count = 0
    clean_count = 0
    total = 0

    for path in yaml_files:
        total += 1
        raw = _load_yaml(path)
        pack_key = raw.get("key", path.stem)

        trimmed, removed = trim_pack(
            raw,
            trim_fragments=do_fragments,
            trim_overrides=do_overrides,
            trim_obligatory=do_obligatory,
        )

        if not removed:
            clean_count += 1
            continue

        changed_count += 1
        print(f"\n{'─' * 60}")
        print(f"Pack: {pack_key} ({path.name})")
        for item in removed:
            print(f"  - REMOVE {item}")

        if args.apply:
            path.write_text(_dump_yaml(trimmed), encoding="utf-8")
            print(f"  ✓ Written: {path.name}")
        else:
            print(f"  (dry-run — pass --apply to write)")

    print(f"\n{'=' * 60}")
    print(
        f"Summary: {total} packs scanned, "
        f"{changed_count} need trimming, "
        f"{clean_count} already clean."
    )
    if not args.apply and changed_count:
        print("Run with --apply to apply changes.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
