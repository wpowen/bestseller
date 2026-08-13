"""模拟目标读者「3秒点不点」LLM 判官 — 构思简介/书名达标循环的人群视角信号。

审计 P1-6：genre_persona 的画像判官（persona_judge_role）此前全库零调用，构思验收
只剩绝对分（绝对分不可信，见 story_appeal.meets_bar 注释）。本模块把画像判官接活：
按题材路由到目标读者画像（男频爽文/女频情绪/中性通用），让 LLM 角色扮演该读者，
只看【书名+简介】3 秒决定点不点；N 采样聚合成 click_rate。

定位 = advisory 并联信号（不替代确定性 blurb gate）：
  * 构思 finalize 初评后跑一次；「不点」的理由并入重生反馈（修黑话/修爽点方向）。
  * 终稿再评一次并持久化进 story_appeal_report["persona_judge"]。
  * ``persona_judge.block_below`` 打开时才与绝对分门并联硬拦（默认 false）。
fail-open：LLM 不可用/全不可解析 → llm_used=False → advisory 放行，绝不误毙。

真机校准（2026-06 scratchpad 旁路验证）：黑话版 2.3 分会点 0/3、去黑话 8.7、
画像定向 8.3 —— 自动复现「看不懂划走」的读者行为。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

# ruff: noqa: ANN401, RUF001, RUF002, RUF003 — Chinese prompts + Any session/settings.
import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

JudgeFn = Callable[[str, str], Awaitable[str]]
"""(system_prompt, user_prompt) -> raw model text."""

_DEFAULTS: dict[str, Any] = {
    "enabled": True,
    "samples": 3,           # N 采样（单次采样噪声大，见记忆 N=3±1.5）
    "click_rate_min": 0.34, # advisory 线：至少 1/3 模拟读者会点
    "block_below": False,   # 默认 advisory；true 时与绝对分门并联硬拦
}


def load_persona_judge_config(config: dict[str, Any] | None = None) -> dict[str, Any]:
    """``story_appeal.yaml`` 的 ``persona_judge`` 段 + 内置默认（缺段也能跑）。"""

    if config is None:
        from bestseller.services.story_appeal import load_story_appeal_config

        config = load_story_appeal_config()
    section = config.get("persona_judge", {}) if isinstance(config, dict) else {}
    merged = dict(_DEFAULTS)
    if isinstance(section, dict):
        merged.update(section)
    return {
        "enabled": bool(merged.get("enabled", True)),
        "samples": max(1, int(merged.get("samples", 3))),
        "click_rate_min": float(merged.get("click_rate_min", 0.34)),
        "block_below": bool(merged.get("block_below", False)),
    }


@dataclass(frozen=True)
class PersonaClickVerdict:
    click: bool
    score: float  # 0-10
    reason: str
    # ── 追读侧信号（2026-08-07 加）────────────────────────────────────────
    # 「会点」不等于「读得下去」。真机 custom-xuanhuan-1786023406：3/3 会点、
    # 均分 8.67 一路绿灯，而三条点击理由都是「这套路太爽了」「套路太对胃口」
    # ——判官在为**可预测性**点赞，而读者抱怨的正是一眼看穿、没有惊喜、观感
    # 反胃。点击判断本身没错（书城 3 秒点击本来就靠套路识别驱动），错在系统里
    # 没有任何一处measure「点进去之后」。这两维和 click 同一次调用产出，零额外
    # 成本，先只上报不设门——阈值要拿真实爆款简介校准过才能生效。
    surprise: float = -1.0   # 0=接下来全能猜到 … 10=完全猜不到；-1=模型没给
    aversion: float = -1.0   # 0=毫无不适 … 10=强烈反胃；-1=模型没给


def _mean_of_present(values: tuple[float, ...]) -> float:
    """只对模型真给了的样本求均值；一个都没有时返回 -1（表示未测量）。"""

    present = [v for v in values if v >= 0.0]
    if not present:
        return -1.0
    return sum(present) / len(present)


@dataclass(frozen=True)
class PersonaClickReport:
    channel: str
    samples: int          # 成功解析的采样数
    clicks: int
    click_rate: float     # clicks/samples；无有效采样时 0.0
    avg_score: float
    reasons: tuple[str, ...]
    llm_used: bool
    avg_surprise: float = -1.0
    avg_aversion: float = -1.0

    def advisory_pass(self, click_rate_min: float) -> bool:
        """fail-open：判官不可用（llm_used=False）绝不误毙。

        刻意只看 click_rate：``surprise``/``aversion`` 是本轮新加的观测量，
        在拿真实爆款校准出阈值之前不参与任何放行判断。
        """

        return (not self.llm_used) or self.click_rate >= float(click_rate_min)

    def to_dict(self) -> dict[str, Any]:
        return {
            "channel": self.channel,
            "samples": self.samples,
            "clicks": self.clicks,
            "click_rate": round(self.click_rate, 3),
            "avg_score": round(self.avg_score, 2),
            "avg_surprise": round(self.avg_surprise, 2),
            "avg_aversion": round(self.avg_aversion, 2),
            "reasons": list(self.reasons),
            "llm_used": self.llm_used,
            "schema_version": "persona-click-judge.v1",
        }


def build_persona_judge_messages(
    *,
    title: str,
    blurb: str,
    genre: str | None,
    sub_genre: str | None = None,
    tags: tuple[str, ...] = (),
    channel: str | None = None,
) -> tuple[str, str]:
    """画像角色扮演判官 (system, user)：该题材的目标读者 3 秒决定点不点。"""

    from bestseller.services.genre_persona import resolve_persona

    p = resolve_persona(genre, sub_genre, tags, channel)
    system = (
        f"{p.persona_judge_role}\n"
        "下面给你一本书的【书名】和【简介】——就像你在书城列表里刷到它。"
        "按你的真实习惯 3 秒内决定：点，还是划走？不要用编辑/作者视角分析，"
        "只凭这个读者的第一反应。\n"
        "点完之后再回答两个问题（这两个不影响你点不点，照实说）：\n"
        "① 光看这段简介，你能猜到接下来大致会发生什么吗？"
        "全都猜得到=0，完全猜不到=10。「熟悉的套路」意味着猜得到，给低分。\n"
        "② 里面有没有哪里让你生理上不舒服、反胃、不想细看？"
        "毫无不适=0，强烈反胃=10。\n"
        '只输出严格 JSON：{"click": true|false, "score": 0到10的吸引力分, '
        '"surprise": 0到10, "aversion": 0到10, '
        '"reason": "一句大白话说为什么点/为什么划走"}'
    )
    user = (
        f"【书名】{title}\n【简介】\n{(blurb or '').strip()[:600]}\n\n"
        "点还是划走？输出严格 JSON。"
    )
    return system, user


def parse_persona_click_verdict(raw: str) -> PersonaClickVerdict | None:
    """宽容解析：裸 JSON / 文本包 JSON；click 容忍字符串；score 夹到 [0,10]。"""

    s = (raw or "").strip()
    payload: Any = None
    try:
        payload = json.loads(s)
    except json.JSONDecodeError:
        start, end = s.find("{"), s.rfind("}")
        if start != -1 and end != -1 and end > start:
            try:
                payload = json.loads(s[start : end + 1])
            except json.JSONDecodeError:
                payload = None
    if not isinstance(payload, dict) or "click" not in payload:
        return None
    click_raw = payload.get("click")
    if isinstance(click_raw, bool):
        click = click_raw
    else:
        click = str(click_raw).strip().lower() in ("true", "1", "yes", "点", "会", "会点")
    try:
        score = float(payload.get("score", 0.0))
    except (TypeError, ValueError):
        score = 0.0
    score = max(0.0, min(10.0, score))

    def _optional_0_10(key: str) -> float:
        """缺字段/不可解析 → -1（未测量），不要伪装成 0（0 是「毫无惊喜」的真值）。"""

        if key not in payload:
            return -1.0
        try:
            return max(0.0, min(10.0, float(payload.get(key))))
        except (TypeError, ValueError):
            return -1.0

    return PersonaClickVerdict(
        click=click,
        score=score,
        reason=str(payload.get("reason", "")),
        surprise=_optional_0_10("surprise"),
        aversion=_optional_0_10("aversion"),
    )


def _make_default_judge(session: Any, settings: Any) -> JudgeFn:
    async def _judge(system_prompt: str, user_prompt: str) -> str:
        from bestseller.services.llm import LLMCompletionRequest, complete_text

        completion = await complete_text(
            session,
            settings,
            LLMCompletionRequest(
                logical_role="critic",
                model_tier="standard",
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                # LLMCompletionRequest.fallback_response 要求非空（min_length=1），
                # 用不含 "click" 键的占位串——parse_persona_click_verdict 解析失败
                # → 该采样丢弃（fail-open），效果等价于原意图的"空文本"。
                fallback_response="{}",
                prompt_template="persona_click_judge",
                prompt_version="v1",
                metadata={"judge_scope": "persona_click"},
                max_tokens_override=300,
            ),
        )
        return completion.content or ""

    return _judge


async def run_persona_click_judge(
    session: Any,
    settings: Any,
    *,
    title: str,
    synopsis: str,
    genre: str | None,
    sub_genre: str | None = None,
    tags: tuple[str, ...] = (),
    channel: str | None = None,
    config: dict[str, Any] | None = None,
    judge: JudgeFn | None = None,
    samples: int | None = None,
) -> PersonaClickReport:
    """N 采样模拟读者点不点，聚合 click_rate/avg_score。永不 raise（fail-open）。"""

    from bestseller.services.genre_persona import resolve_persona

    cfg = load_persona_judge_config(config)
    n = max(1, int(samples if samples is not None else cfg["samples"]))
    persona = resolve_persona(genre, sub_genre, tags, channel)
    system, user = build_persona_judge_messages(
        title=title, blurb=synopsis, genre=genre, sub_genre=sub_genre,
        tags=tags, channel=channel,
    )
    fn = judge if judge is not None else _make_default_judge(session, settings)

    verdicts: list[PersonaClickVerdict] = []
    for i in range(n):
        try:
            raw = await fn(system, user)
        except Exception:
            logger.warning("persona click judge sample %d failed", i, exc_info=True)
            continue
        v = parse_persona_click_verdict(raw)
        if v is not None:
            verdicts.append(v)
    if not verdicts:
        return PersonaClickReport(
            channel=persona.channel, samples=0, clicks=0, click_rate=0.0,
            avg_score=0.0, reasons=(), llm_used=False,
        )
    clicks = sum(1 for v in verdicts if v.click)
    return PersonaClickReport(
        channel=persona.channel,
        samples=len(verdicts),
        clicks=clicks,
        click_rate=clicks / len(verdicts),
        avg_score=sum(v.score for v in verdicts) / len(verdicts),
        avg_surprise=_mean_of_present(tuple(v.surprise for v in verdicts)),
        avg_aversion=_mean_of_present(tuple(v.aversion for v in verdicts)),
        reasons=tuple(v.reason for v in verdicts if v.reason),
        llm_used=True,
    )


__all__ = [
    "JudgeFn",
    "PersonaClickReport",
    "PersonaClickVerdict",
    "build_persona_judge_messages",
    "load_persona_judge_config",
    "parse_persona_click_verdict",
    "run_persona_click_judge",
]
