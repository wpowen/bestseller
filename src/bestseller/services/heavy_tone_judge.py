"""事件级沉重度检察官 —— 校验任务，不是评分任务。

2026-08-23 真机（验证书 8 原稿，用户勾的是 tone=light + 喜剧引擎 + 代价档
minimal）：

    「每夜只卖十碗……这十碗面是给十个当晚必死之人吃的，主角要在他们咽气前
      当面问清怎么死……三年来攒下的『人头账』……把每一个将死之人当棋子拆骨」

`_creation_intent_content_violations` 判定零违规——它数的是
`_HEAVY_TONE_MARKERS` 那 11 个**情绪形容词**（黑暗/压抑/绝望/尸体…），这段
一个都没命中。

同一教训第三次复发。`concept_tournament.py` 里 2026-08-13 就写着「情绪词表
测不出**用事件写的沉重**」，当时补的 `_COERCION_STAKE_PATTERNS` 只覆盖人质
与限期处刑两种事件形状；本例（每夜一批必死之人）不在其中。继续往词表里加词
是打地鼠，真正缺的是**按事件判**的判据。

设计沿用同日已验证的 blurb 逻辑轴：窄任务（只查一件事）+ 正例与反例边界
（防冤案）+ 引文必须逐字接地于输入（引不出原文的按幻觉丢弃）+ fail-open。

按「新检测器只挣重生和留痕」：本模块的发现**不进 detected、不毙书**，只换
一次重写并留痕。杀权留给既有的确定性判据。
"""

from __future__ import annotations

from dataclasses import dataclass

# ruff: noqa: RUF001, RUF002, RUF003 — 中文 prompt 是刻意的。
import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

_QUOTE_MIN_RUN = 6

#: 模型在 ``why`` 里自己撤回这条发现时的措辞。2026-08-23 真机（验证书 8）：
#: 第三条 finding 的理由逐字是「…本稿此句仅为暗战氛围，不构成事件冲突，
#: 不报。」——引文接地、格式合法，于是被原样收下并混进重写反馈，写手会被
#: 要求整改一处判官自己都说不算数的句子。判决语义写在 why 里，解析必须读它。
#:
#: 判据只作用于 ``why``（判决语），绝不作用于 ``quote``（故事原文）——否则
#: 正文里一句「他不构成威胁」就能把真发现误杀。
_SELF_NEGATION_MARKERS: tuple[str, ...] = (
    "不报",
    "不构成",
    "不算冲突",
    "不算病",
    "无冲突",
    "不予认定",
)


@dataclass(frozen=True)
class HeavyToneFinding:
    """一条**通过引文核对**的事件级沉重证据。"""

    quote: str
    why: str

    def to_dict(self) -> dict[str, Any]:
        return {"quote": self.quote, "why": self.why}


def build_heavy_tone_messages(text: str) -> tuple[str, str]:
    """(system, user) —— 只查一件事：故事**事件**是否与轻松调性冲突。

    ⚠️ prompt 只描述事件类别与结构，不点名任何具体母题词：否定式指令点名
    母题词等于种词（2026-08-06 定案，写手会把禁令清单当灵感抄走）。
    """

    system = (
        "你是选题校对员。用户明确选了**轻松**基调。只查一件事："
        "这段构思在**事件层面**是否与轻松冲突——看故事里真实发生了什么，"
        "不看有没有阴郁的形容词。\n"
        "算冲突的事件类别：\n"
        "- 成批的人被安排在故事里死去，或主角的日常工作就是处理将死之人；\n"
        "- 有人被扣押、被逼到限期内交命；\n"
        "- 主角靠清点、交易或消耗他人的性命来推进目标。\n"
        "不算冲突的：单次的、已经过去的、发生在故事开始前的死亡；"
        "打斗中的伤亡；被喜剧化处理、当场被消解的危险；"
        "只有阴冷形容词而事件本身不重的写法。\n"
        "不评价文笔，不提建议。没有就输出空列表，**宁缺勿滥，不要硬凑**。\n"
        "每条必须给出一段**逐字引用的原文**（≤40字，必须能在原文里原样找到，"
        "一个字都不能改），引不出原文的不要报。\n"
        '只输出 JSON：{"heavy_events": [{"quote": "...", '
        '"why": "一句话说明这是什么事件、为什么与轻松冲突"}]}'
    )
    user = f"【构思】\n{str(text or '').strip()}\n\n只查上述这一件事。输出严格 JSON。"
    return system, user


def _normalise(s: str) -> str:
    return "".join(str(s or "").split())


def _grounded(quote: str, haystack: str) -> bool:
    """长公共连续片段判据（与 blurb_coherence_judge 同款，理由同）。"""

    q = _normalise(quote)
    if not q:
        return False
    if q in haystack:
        return True
    need = max(_QUOTE_MIN_RUN, int(len(q) * 0.6))
    if len(q) < need:
        return False
    for length in range(len(q), need - 1, -1):
        for start in range(0, len(q) - length + 1):
            if q[start : start + length] in haystack:
                return True
    return False


def parse_and_verify_heavy_tone(
    raw: str, *, source_text: str
) -> tuple[tuple[HeavyToneFinding, ...], int]:
    """解析模型输出并逐条核对引文；返回 (核实的发现, 丢弃的幻觉数)。"""

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
    items = payload.get("heavy_events") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        return (), 0

    haystack = _normalise(source_text)
    verified: list[HeavyToneFinding] = []
    dropped = 0
    for item in items:
        if not isinstance(item, dict):
            dropped += 1
            continue
        quote = str(item.get("quote") or "").strip()
        why = str(item.get("why") or "")
        if not quote or not _grounded(quote, haystack):
            dropped += 1  # 幻觉引文，丢弃——可证伪性的核心保险
            continue
        if any(marker in why for marker in _SELF_NEGATION_MARKERS):
            dropped += 1  # 判官自己撤回了这条，不能进整改反馈
            continue
        verified.append(HeavyToneFinding(quote=quote[:60], why=why[:120]))
    return tuple(verified), dropped


def render_heavy_tone_feedback(findings: tuple[HeavyToneFinding, ...]) -> str:
    """给重写用的整改行（一条一行，带原文引用）。空发现返回空串。"""

    if not findings:
        return ""
    lines = [
        f"- 「{f.quote}」：{f.why}。这是事件层面的沉重，"
        "改成同样有张力但不靠死亡清算推进的处境。"
        for f in findings
    ]
    return (
        "\n【调性事件冲突 — 用户选的是轻松基调】\n"
        + "\n".join(lines)
        + "\n把这些事件换掉，不要只删阴郁的形容词。\n"
    )


async def detect_heavy_tone_events(
    session: Any,
    settings: Any,
    *,
    text: str,
    tone_preference: str,
    project_id: Any = None,
) -> tuple[HeavyToneFinding, ...]:
    """只在 tone=light 时开火。永不 raise（fail-open：判官不可用视为无发现）。"""

    if str(tone_preference or "").strip().lower() != "light":
        return ()
    if not str(text or "").strip():
        return ()
    try:
        from bestseller.services.llm import (
            LLMCompletionRequest,
            complete_text,
        )

        system, user = build_heavy_tone_messages(text)
        completion = await complete_text(
            session,
            settings,
            LLMCompletionRequest(
                logical_role="critic",
                model_tier="standard",
                system_prompt=system,
                user_prompt=user,
                fallback_response='{"heavy_events": []}',
                prompt_template="heavy_tone_event_prosecutor",
                prompt_version="v1",
                max_tokens_override=500,
                project_id=project_id,
            ),
        )
        findings, dropped = parse_and_verify_heavy_tone(
            completion.content or "", source_text=text
        )
        if dropped:
            logger.info("heavy-tone judge dropped %d ungrounded quote(s)", dropped)
        return findings
    except Exception:
        logger.warning("heavy tone judge failed (fail-open)", exc_info=True)
        return ()


__all__ = [
    "HeavyToneFinding",
    "build_heavy_tone_messages",
    "detect_heavy_tone_events",
    "parse_and_verify_heavy_tone",
    "render_heavy_tone_feedback",
]
