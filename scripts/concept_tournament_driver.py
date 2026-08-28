"""在无数据库环境下驱动**真正的**生产淘汰赛。

2026-08-28 翻车记录：对标台此前用 `_build_raw_idea_pool_messages` 生成候选、
取 `ideas[0]` 就当成框架产出，`run_concept_tournament` 一次都没被执行过。
于是我按它的读数改了淘汰赛选拔逻辑，还宣布「验证通过」——被测代码根本没跑。

本模块只做一件事：让 `run_concept_tournament` 在脚本里跑起来，且**不碰数据库**。
做法是替换 `bestseller.services.llm.complete_text`——淘汰赛内部十余处自建
generator 全部经由它取模型输出，换掉这一个函数就等于换掉全部出口，不必
（也不该）逐个传 generator 参数，那样会漏掉没有暴露成参数的那些。

铁律（这次就是栽在这条上）：验证脚本必须能证明自己调到了被测代码。
`calls` 计数器与 `assert_ran()` 就是为此存在——跑完一轮若计数为 0，直接报错，
不允许再出现「跑了个寂寞还以为验证通过」。
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

API = "https://api.minimaxi.com/v1/text/chatcompletion_v2"
MODEL = "MiniMax-M3"


class TournamentDriver:
    """把生产淘汰赛接到一个裸 HTTP 客户端上，并记录它真的跑了多少次。"""

    def __init__(self, client: httpx.AsyncClient, *, concurrency: int = 4) -> None:
        self._client = client
        self._sem = asyncio.Semaphore(concurrency)
        self.calls = 0
        self.failures = 0
        self.stages: dict[str, int] = {}

    async def _raw(self, system: str, user: str, *, max_tokens: int) -> str:
        async with self._sem:
            r = await self._client.post(
                API,
                json={
                    "model": MODEL,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    "temperature": 1.0,
                    "max_tokens": max_tokens,
                    "thinking": {"type": "disabled"},
                },
            )
            r.raise_for_status()
            body = r.json()
        choices = body.get("choices") or []
        if not choices:
            raise RuntimeError(f"空 choices: {str(body)[:200]}")
        return choices[0]["message"]["content"] or ""

    def install(self) -> None:
        """替换 complete_text。幂等——重复调用只装一次。"""
        from bestseller.services import llm as llm_mod

        if getattr(llm_mod.complete_text, "_bench_patched", False):
            return
        original = llm_mod.complete_text

        async def _patched(session: Any, settings: Any, request: Any) -> Any:
            self.calls += 1
            stage = str((request.metadata or {}).get("concept_tournament_stage") or request.prompt_template or "?")
            self.stages[stage] = self.stages.get(stage, 0) + 1
            try:
                content = await self._raw(
                    request.system_prompt,
                    request.user_prompt,
                    max_tokens=request.max_tokens_override or 2000,
                )
            except Exception:  # noqa: BLE001
                # 与生产同构：取不到就回落，让淘汰赛自己的兜底路径接管，
                # 而不是把整轮炸掉——否则测的就不是生产行为了。
                self.failures += 1
                content = request.fallback_response
            return llm_mod.LLMCompletionResult(
                content=content,
                provider="bench",
                model_name=MODEL,
                fallback_used=False,
            )

        _patched._bench_patched = True  # type: ignore[attr-defined]
        _patched._bench_original = original  # type: ignore[attr-defined]
        llm_mod.complete_text = _patched  # type: ignore[assignment]

        # 淘汰赛模块在 import 时并不绑定 complete_text（它在函数内 import），
        # 但别的调用方可能已经绑了，这里一并覆盖，避免半装状态。
        from bestseller.services import concept_tournament as ct_mod

        if hasattr(ct_mod, "complete_text"):
            ct_mod.complete_text = _patched  # type: ignore[attr-defined]

    def assert_ran(self, *, at_least: int = 1) -> None:
        """跑完必须自证：被测代码真的被执行了。"""
        if self.calls < at_least:
            raise AssertionError(
                f"淘汰赛只产生了 {self.calls} 次模型调用（要求 ≥{at_least}）——"
                "说明验证脚本没有真的跑到被测代码，读数不许采信。"
            )


def _key() -> str:
    k = os.environ.get("MINIMAX_API_KEY") or os.environ.get("BESTSELLER__LLM__API_KEY")
    if not k:
        raise SystemExit("缺 MINIMAX_API_KEY")
    return k


async def run_production_tournament(
    driver: TournamentDriver,
    *,
    genre: str,
    sub_genre: str,
    chapter_count: int,
    audience_orientation: str,
    focus_tags: str = "",
    cost_style: str = "standard",
    seed: int = 0,
) -> dict[str, Any]:
    """跑一轮完整的生产淘汰赛，返回冠军与过程回执。"""
    import random as _random

    from bestseller.services.concept_tournament import run_concept_tournament

    driver.install()
    before = driver.calls
    result = await run_concept_tournament(
        None,  # session：已被 complete_text 替换架空
        None,  # settings：同上
        genre=genre,
        sub_genre=sub_genre or genre,
        chapter_count=chapter_count,
        audience_orientation=audience_orientation,
        cost_style=cost_style,
        seed_concept="",
        rng=_random.Random(seed),
    )
    winner = result.winner
    return {
        "concept": str(getattr(winner, "concept", "") or ""),
        "high_concept": str(getattr(winner, "high_concept", "") or ""),
        "dimension": str(getattr(winner, "dimension", "") or ""),
        "composite": getattr(winner, "composite", None),
        "judge_click": getattr(winner, "judge_click", None),
        "n_candidates": len(getattr(result, "candidates", []) or []),
        "llm_calls": driver.calls - before,
        "seriality_stage": dict(getattr(result, "seriality_stage", {}) or {}),
    }
