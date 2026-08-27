"""简介的「市场分布落位」检查（T2，2026-08-27）。

用户报简介「没有吸引力、不通顺」，而框架自评 83.9 分判通过——分歧在标准。
本模块不判「好不好」，只判**「像不像榜单书」**：拿 2219 条真实榜单简介
（番茄，2026-08-26 快照）算分布，我们的简介落在 p5/p95 之外即报。

为什么这个判据可信：它有**真正的负对照**。同一批指标上，n=1139 榜单男频
vs n=32 框架产出：

    含「我」      54.3%  vs  0%
    含感叹号      64.4%  vs  0%
    含对白        46.1%  vs  12.5%
    【】开头      50.6%  vs  0%
    段落数中位       9   vs  4

近乎全有全无。框架写的是**第三人称书面陈述体（内容提要）**，榜单写的是
**对读者说话的口播腔**。这不是文笔差距，是语体错位。

⚠️ 单日快照。热度是活数据，跨日重复采样后再固化阈值（2026-08-08 定案）。
⚠️ 只留痕不发杀权（本仓库对新检测器的规矩）。
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

_BASELINE_PATH = (
    Path(__file__).resolve().parents[3] / "data" / "market_baseline" / "blurb_distribution.json"
)

FEATURES: dict[str, Any] = {
    "tagline_bracket": lambda d: 1.0 if d.lstrip().startswith(("【", "（", "(", "『")) else 0.0,
    "exclamation": lambda d: float(d.count("！")),
    "question": lambda d: float(d.count("？")),
    "dialogue": lambda d: float(len(re.findall(r'[“"『]', d))),
    "first_person": lambda d: float(d.count("我")),
    "paragraphs": lambda d: float(len([p for p in d.split("\n") if p.strip()])),
    "chars": lambda d: float(len(d)),
    "digit_density": lambda d: len(re.findall(r"[0-9一二三四五六七八九十百千万]", d)) * 100.0 / max(1, len(d)),
}

# 这些维度「偏低」才是问题（榜单书有、我们没有）；chars/digit_density 两头都要看。
_LOW_IS_BAD = frozenset(
    {"tagline_bracket", "exclamation", "question", "dialogue", "first_person", "paragraphs"}
)

# 口播语体的五个标记。**单看一项没有意义**——不少榜单书也缺其中某一项
# （所以 p05 恰好是 0，按分位判永远不触发；这是本模块第一版的设计错误）。
# 有意义的是**复合计数**：
#     男频榜单 n=1139  中位 3 项，0 项的只占 9.5%，≥3 项的占 53%
#     框架生成 n=32    中位 0 项，0 项的占 78%，≥3 项的占 0%
# 阈值取 ≥1（榜单里只有 9.5% 达不到，保守线），中位 3 记为目标值。
VOICE_MARKERS: dict[str, Any] = {
    "tagline_bracket": lambda d: d.lstrip().startswith(("【", "（", "(", "『")),
    "exclamation": lambda d: "！" in d,
    "question": lambda d: "？" in d,
    "dialogue": lambda d: bool(re.search(r'[“"『]', d)),
    "first_person": lambda d: "我" in d,
}
VOICE_MIN = 1
VOICE_TARGET = 3


def blurb_voice_score(blurb: str) -> tuple[int, list[str]]:
    """口播语体标记命中数与命中项。"""

    text = str(blurb or "")
    hit = [name for name, fn in VOICE_MARKERS.items() if fn(text)]
    return len(hit), hit


@lru_cache(maxsize=1)
def load_baseline(path: str | None = None) -> dict[str, Any]:
    target = Path(path) if path else _BASELINE_PATH
    if not target.exists():  # 基线缺失时静默跳过，绝不拖垮建书
        return {}
    try:
        return json.loads(target.read_text(encoding="utf-8"))
    except Exception:  # pragma: no cover
        return {}


def evaluate_blurb_market_fit(
    blurb: str,
    *,
    channel: str = "男频",
    baseline: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """回执恒非空——没有回执就分不清「查了没问题」和「压根没查」。"""

    data = baseline if baseline is not None else load_baseline()
    band = data.get(channel) or data.get("男频") or {}
    text = str(blurb or "")
    if not text or not band:
        return {"checked": False, "reason": "no_baseline" if not band else "empty_blurb",
                "channel": channel, "findings": [], "features": {}}
    # 连续特征只**观测**不报警。实测（框架 32 条 vs 榜单男频 1139 条）：
    #     voice_register  框架命中 78%  榜单误伤  9%   分离 8.2x  ← 真信号
    #     chars           框架命中  0%  榜单误伤 10%              ← 纯噪声
    #     digit_density   框架命中  0%  榜单误伤 10%              ← 纯噪声
    # 按分位判连续量，按构造就有 ~10% 落在 p5/p95 外；它们一条都没抓到框架的
    # 问题，只贡献误伤。留数不留杀权。
    feats: dict[str, float] = {}
    findings: list[dict[str, Any]] = []
    for name, fn in FEATURES.items():
        if not isinstance(band.get(name), dict):
            continue
        feats[name] = round(float(fn(text)), 2)

    voice, voice_hits = blurb_voice_score(text)
    if voice < VOICE_MIN:
        findings.append({
            "feature": "voice_register",
            "value": voice,
            "side": "below_min",
            "min": VOICE_MIN,
            "median": VOICE_TARGET,
            "why": "简介是第三人称书面陈述体（内容提要），榜单简介是对读者说话的口播腔",
        })
    return {"checked": True, "channel": channel, "sample": band.get("_sample"),
            "features": feats, "voice_score": voice, "voice_hits": voice_hits,
            "findings": findings, "passed": not findings}
