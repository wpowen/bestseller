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
        if args.architecture_only:
            from bestseller.services.concept_tournament import (
                _build_engine_kernel_messages,
                _default_generator,
                _parse_json_object,
                _story_architecture_audit,
            )

            system, user = _build_engine_kernel_messages(
                genre=args.genre,
                sub_genre=args.sub_genre,
                lane="生产架构验证",
                chapter_count=args.chapters,
                seed_concept=args.seed,
                audience_orientation=args.audience,
                tone_preference=args.tone,
                effect_skills=tuple(args.effect_skill),
            )
            generator = await _default_generator(
                session,
                settings,
                template="concept_tournament_engine_kernel",
                max_tokens=int(config.get("engine_kernel_max_tokens", 8000)),
                model_catalog_key=str(config.get("generation_model_key") or "") or None,
            )
            raw, llm_run_id = await generator(system, user)
            card = _parse_json_object(raw) or {}
            audit = _story_architecture_audit(
                card,
                genre=args.genre,
                sub_genre=args.sub_genre,
                chapter_count=args.chapters,
                effect_skills=tuple(args.effect_skill),
            )
            payload = {
                "mode": "architecture_only",
                "llm_run_id": str(llm_run_id) if llm_run_id else None,
                "card": card,
                "audit": audit,
            }
            passed = bool(audit.get("passed"))
        else:
            result = await run_concept_tournament(
                session,
                settings,
                genre=args.genre,
                sub_genre=args.sub_genre,
                chapter_count=args.chapters,
                seed_concept=args.seed,
                audience_orientation=args.audience,
                tone_preference=args.tone,
                effect_skills=tuple(args.effect_skill),
                config=config,
            )
            payload = result.to_dict()
            passed = result.winner is not None
    rendered = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered + "\n", encoding="utf-8")
        print(output.resolve())
    else:
        print(rendered)
    return 0 if passed else 2


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--genre", required=True)
    parser.add_argument("--sub-genre", default="")
    parser.add_argument("--chapters", type=int, default=500)
    parser.add_argument("--seed", default="")
    parser.add_argument("--audience", default="")
    parser.add_argument("--tone", default="")
    parser.add_argument("--effect-skill", action="append", default=[])
    parser.add_argument("--architecture-only", action="store_true")
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
