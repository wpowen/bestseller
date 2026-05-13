from __future__ import annotations

import argparse
from pathlib import Path
import sys

_THIS = Path(__file__).resolve()
_SRC = _THIS.parents[2] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from bestseller.services.distillation_assets import install_story_design_grammar_patch


def main() -> None:
    parser = argparse.ArgumentParser(description="Install a distillation grammar patch.")
    parser.add_argument("patch_path", type=Path)
    parser.add_argument(
        "--grammar-dir",
        type=Path,
        default=Path("config/story_design_grammars"),
    )
    parser.add_argument("--apply", action="store_true", help="Write the grammar file.")
    args = parser.parse_args()

    target = install_story_design_grammar_patch(
        args.patch_path,
        grammar_dir=args.grammar_dir,
        dry_run=not args.apply,
    )
    action = "would write" if not args.apply else "wrote"
    print(f"{action}: {target}")


if __name__ == "__main__":
    main()

