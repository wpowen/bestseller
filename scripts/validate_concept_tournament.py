"""Run the production concept tournament without creating or planning a book."""

# ruff: noqa: E402, I001 -- bootstrap src before project imports.

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from bestseller.infra.db.session import session_scope
from bestseller.services.concept_tournament import run_concept_tournament
from bestseller.services.concept_tournament import load_concept_tournament_config
from bestseller.settings import load_settings


async def _run(args: argparse.Namespace) -> int:
    settings = load_settings()
    config = dict(load_concept_tournament_config())
    if args.generation_model:
        config["generation_model_key"] = args.generation_model
    if args.judge_model:
        config["judge_model_key"] = args.judge_model
    if args.prompt_mode:
        config["candidate_prompt_mode"] = args.prompt_mode
    async with session_scope(settings) as session:
        result = await run_concept_tournament(
            session,
            settings,
            genre=args.genre,
            sub_genre=args.sub_genre,
            chapter_count=args.chapters,
            seed_concept=args.seed,
            config=config,
        )

    payload = result.to_dict()
    rendered = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered + "\n", encoding="utf-8")
        print(output.resolve())
    else:
        print(rendered)
    return 0 if result.winner is not None else 2


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--genre", required=True)
    parser.add_argument("--sub-genre", default="")
    parser.add_argument("--chapters", type=int, default=500)
    parser.add_argument("--seed", default="")
    parser.add_argument("--generation-model", help="model_catalog id for candidates/expansion")
    parser.add_argument("--judge-model", help="model_catalog id for both strict judge stages")
    parser.add_argument(
        "--prompt-mode",
        choices=("current", "lean_story_package", "native_baseline", "engine_first"),
        help="candidate-generation prompt arm; downstream judges stay identical",
    )
    parser.add_argument("--output")
    return asyncio.run(_run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
