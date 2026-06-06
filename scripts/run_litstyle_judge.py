"""Run the advisory LitStyle-100R 文采 judge on a real chapter file.

Zero pipeline change: calls the critic model directly via litellm (like
``verify_prose_craft_ab.py``) using the judge's *pure* prompt builders +
deterministic AI腔 prior + result parser. Lets you eyeball whether the 9-dim
scoring is sane on real chapters before wiring the judge into reviews.py.

Usage:
    .venv/bin/python scripts/run_litstyle_judge.py path/to/chapter.md [more.md ...]
    .venv/bin/python scripts/run_litstyle_judge.py --genre 都市 chapter.md

Reads CRITIC model from settings (same as the pipeline's critic role).
"""

# ruff: noqa: RUF001

from __future__ import annotations

import argparse
import asyncio
import os

from dotenv import load_dotenv

load_dotenv(".env")

import litellm  # noqa: E402

from bestseller.domain.litstyle_judge import litstyle_result_from_mapping  # noqa: E402
from bestseller.services.judge_genre_context import (  # noqa: E402
    resolve_judge_genre_context,
)
from bestseller.services.litstyle_prose import (  # noqa: E402
    detect_ai_tone,
    load_litstyle_config,
)
from bestseller.services.litstyle_prose_judge import (  # noqa: E402
    _parse_json_object,
    build_litstyle_system_prompt,
    build_litstyle_user_prompt,
)
from bestseller.settings import get_settings  # noqa: E402

litellm.suppress_debug_info = True

SETTINGS = get_settings()
CRITIC = SETTINGS.llm.critic
CRITIC_KEY = os.environ.get(getattr(CRITIC, "api_key_env", "") or "")


async def _judge_one(path: str, genre: str | None) -> None:
    with open(path, encoding="utf-8") as fh:
        text = fh.read().strip()
    if len(text) < 80:
        print(f"[{path}] too short ({len(text)} chars) — skipped")
        return

    config = load_litstyle_config()
    genre_context = resolve_judge_genre_context(genre=genre) if genre else None
    ai_tone = detect_ai_tone(text, config)

    system = build_litstyle_system_prompt(config=config, genre_context=genre_context)
    user = build_litstyle_user_prompt(chapter_number=1, content_md=text, ai_tone=ai_tone)

    resp = await litellm.acompletion(
        model=CRITIC.model,
        api_base=CRITIC.api_base,
        api_key=CRITIC_KEY,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        temperature=0.0,
        max_tokens=2500,
        timeout=180,
    )
    raw = (resp.choices[0].message.content or "").strip()
    result = litstyle_result_from_mapping(
        _parse_json_object(raw),
        config=config,
        ai_tone_prior=ai_tone.deterministic_penalty,
        ai_tone_flagged=ai_tone.flagged,
    )

    print(f"\n================ {path} ================")
    print(f"FinalScore={result.final_score}/100  Level={result.level}  "
          f"(base={result.base_score}, AI腔扣分={result.ai_tone_penalty})")
    print(f"mature={result.is_mature}  high_risk_template={result.is_high_risk_template}")
    print("九维：" + "  ".join(
        f"{dim.display_name}={result.dimension_scores.get(dim.key, 0)}/{dim.max}"
        for dim in config.dimensions
    ))
    print(f"确定性AI腔预扫：flagged={list(ai_tone.flagged)} "
          f"penalty_prior={ai_tone.deterministic_penalty}/{ai_tone.deterministic_penalty_max} "
          f"(对称{ai_tone.symmetric_hits}/抽象{ai_tone.abstract_value_hits}/情感标签{ai_tone.emotion_label_hits})")
    if result.evidence:
        print("证据：" + " | ".join(result.evidence[:5]))
    if result.top_issues:
        print("问题：" + " | ".join(result.top_issues[:3]))
    if result.revision_priority:
        print("修改优先级：")
        for i, action in enumerate(result.revision_priority[:5], start=1):
            print(f"  {i}. {action}")


async def main() -> None:
    parser = argparse.ArgumentParser(description="Run the advisory LitStyle 文采 judge.")
    parser.add_argument("paths", nargs="+", help="chapter .md file(s)")
    parser.add_argument("--genre", default=None, help="题材（用于文采侧重，如 都市/古风/悬疑）")
    args = parser.parse_args()
    for path in args.paths:
        try:
            await _judge_one(path, args.genre)
        except Exception as exc:
            print(f"[{path}] JUDGE FAILED: {type(exc).__name__}: {exc}")


if __name__ == "__main__":
    asyncio.run(main())
