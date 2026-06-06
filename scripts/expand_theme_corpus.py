"""Grow the genre-agnostic theme corpus toward the 1000-2000 target.

For each of the 13 motifs, ask the planner LLM for K NEW thematic propositions
(子题) that are distinct from the ones already in ``config/theme_corpus.yaml``,
genre-agnostic, concrete, and could each anchor a different book. Dedupes against
the existing corpus (normalized) and writes the additions to a separate file so
the curated seed is never clobbered.

Run:  .venv/bin/python scripts/expand_theme_corpus.py [K_per_motif]
Output: config/theme_corpus_generated.yaml  (review, then merge into the seed)
Needs .env with MINIMAX_API_KEY (or the planner role's key). With no key it is a
no-op that explains what it would do.
"""

# ruff: noqa: E402, RUF001, E501, ANN001

from __future__ import annotations

import asyncio
import json
import os
import re
import sys

from dotenv import load_dotenv

load_dotenv(".env")

import litellm

from bestseller.services.ideology_library import load_motif_library
from bestseller.settings import get_settings

litellm.suppress_debug_info = True

_S = get_settings()
PLANNER = _S.llm.planner
KEY = os.environ.get(getattr(PLANNER, "api_key_env", "") or "")
K = int(sys.argv[1]) if len(sys.argv) > 1 else 12
OUT = "config/theme_corpus_generated.yaml"

_SEM = asyncio.Semaphore(4)


def _norm(text: str) -> str:
    return re.sub(r"[\s，。、！？；：,.!?;:\"'「」『』]+", "", text or "")


async def _gen_for_motif(motif, existing: list[str]) -> list[str]:
    sys_p = (
        "你是中文小说主题设计师。只输出一个 JSON 数组(字符串数组), 不要解释。"
        "每条是一句可作为一本书『核心理念/子题』的命题, 必须：与题材无关(不绑定任何具体题材)、"
        "具体有锋芒、彼此不同、且不同于已给清单。每条 ≤ 30 字。"
    )
    usr = (
        f"母题：{motif.display_name} —— {motif.one_line}\n"
        f"该母题的信念弧：信「{motif.belief_initial}」→ 碎「{motif.belief_shatter}」→ 立「{motif.belief_reconstruction}」\n"
        f"已有命题(必须避开、不得近义重复)：\n" + "\n".join(f"- {e}" for e in existing) + "\n\n"
        f"请再写 {K} 条全新的、互不相同的命题, 只输出 JSON 字符串数组。"
    )
    async with _SEM:
        try:
            r = await litellm.acompletion(
                model=PLANNER.model, api_base=PLANNER.api_base, api_key=KEY,
                messages=[{"role": "system", "content": sys_p}, {"role": "user", "content": usr}],
                temperature=0.95, max_tokens=900, timeout=180,
            )
        except Exception as e:
            print(f"  [{motif.key}] FAIL {type(e).__name__}: {str(e)[:80]}")
            return []
    raw = (r.choices[0].message.content or "").strip()
    m = re.search(r"\[.*\]", raw, re.DOTALL)
    try:
        arr = json.loads(m.group(0) if m else raw)
    except Exception:
        return []
    return [str(x).strip() for x in arr if isinstance(x, str) and str(x).strip()]


async def main() -> None:
    lib = load_motif_library()
    if not KEY:
        print("No planner API key in .env — would generate "
              f"{K}×{len(lib.motifs)} = {K * len(lib.motifs)} new genre-agnostic propositions "
              "and write them to", OUT)
        return

    print(f"Expanding theme corpus: +{K} per motif, planner={PLANNER.model}\n")
    seen = {_norm(t.proposition) for t in lib.themes}
    lines: list[str] = ["# Generated theme additions — REVIEW then merge into theme_corpus.yaml",
                        "version: theme-corpus.generated", "themes:"]
    total = 0
    for motif in lib.motifs:
        existing = [t.proposition for t in lib.themes_for_motif(motif.key)]
        props = await _gen_for_motif(motif, existing)
        kept = 0
        for i, p in enumerate(props):
            if _norm(p) in seen:
                continue
            seen.add(_norm(p))
            lines.append(f'  - {{id: {motif.key}_gen_{i:02d}, motif: {motif.key}, tone: "", proposition: "{p}"}}')
            kept += 1
            total += 1
        print(f"  {motif.display_name:<8} +{kept}")
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"\nWrote {total} new propositions to {OUT} (review + merge). "
          f"Corpus would grow {len(lib.themes)} → {len(lib.themes) + total}.")


if __name__ == "__main__":
    asyncio.run(main())
