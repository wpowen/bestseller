"""真书结构画像与对标基线（榜单对标闭环 P3.3）。

对每章计算**确定性**结构指标（零 LLM 成本），按 T1/T2 层聚合成参考分布，
供整书闸门做 advisory 偏离度对比。基线文件只含聚合统计（repo 安全），
不含任何真书文本。

指标说明：
- chars            章字数（去空白）
- dialogue_ratio   对话行占比（「…」/"…" 行）
- avg_paragraph    平均段长（字）
- avg_sentence     平均句长（字，按。！？切）
- short_sentence_ratio  超短句（≤6字）占比 — 碎句癖检测同源
- ending_hook      章末钩子形态（问句/转折/短促句 → 1，平铺收尾 → 0）
"""

from __future__ import annotations

import json
import logging
import re
import statistics
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_BASELINE_PATH = Path("data/benchmark_capability/structure_baseline.json")

_SENTENCE_SPLIT_RE = re.compile(r"[。！？!?]+")
_DIALOGUE_LINE_RE = re.compile(r"[「『“\"]")
_HOOK_ENDING_RE = re.compile(r"([？?！!—…]|—)\s*$")


@dataclass(frozen=True)
class ChapterStructureProfile:
    chars: int
    dialogue_ratio: float
    avg_paragraph: float
    avg_sentence: float
    short_sentence_ratio: float
    ending_hook: int


def profile_chapter(text: str) -> ChapterStructureProfile | None:
    """Deterministic per-chapter structure metrics; None for trivial input."""
    body = (text or "").strip()
    if len(body) < 200:
        return None
    paragraphs = [p.strip() for p in body.splitlines() if p.strip()]
    if not paragraphs:
        return None
    dialogue_lines = sum(1 for p in paragraphs if _DIALOGUE_LINE_RE.search(p))
    sentences = [s.strip() for s in _SENTENCE_SPLIT_RE.split(body) if s.strip()]
    sentence_lengths = [len(s) for s in sentences] or [len(body)]
    short = sum(1 for n in sentence_lengths if n <= 6)
    tail = paragraphs[-1][-40:]
    last_sentence_len = sentence_lengths[-1] if sentence_lengths else 0
    ending_hook = int(bool(_HOOK_ENDING_RE.search(tail)) or last_sentence_len <= 12)
    return ChapterStructureProfile(
        chars=sum(len(p) for p in paragraphs),
        dialogue_ratio=round(dialogue_lines / len(paragraphs), 3),
        avg_paragraph=round(statistics.fmean(len(p) for p in paragraphs), 1),
        avg_sentence=round(statistics.fmean(sentence_lengths), 1),
        short_sentence_ratio=round(short / len(sentence_lengths), 3),
        ending_hook=ending_hook,
    )


_METRIC_KEYS = (
    "chars",
    "dialogue_ratio",
    "avg_paragraph",
    "avg_sentence",
    "short_sentence_ratio",
    "ending_hook",
)


def aggregate_profiles(profiles: list[ChapterStructureProfile]) -> dict[str, Any]:
    """Aggregate chapter profiles into {metric: {median, p25, p75, mean}}."""
    if not profiles:
        return {}
    rows = [asdict(p) for p in profiles]
    aggregated: dict[str, Any] = {"n_chapters": len(profiles)}
    for key in _METRIC_KEYS:
        values = [row[key] for row in rows]
        quartiles = statistics.quantiles(values, n=4) if len(values) >= 4 else None
        aggregated[key] = {
            "median": round(statistics.median(values), 3),
            "mean": round(statistics.fmean(values), 3),
            "p25": round(quartiles[0], 3) if quartiles else None,
            "p75": round(quartiles[2], 3) if quartiles else None,
        }
    return aggregated


def load_structure_baseline(path: Path | None = None) -> dict[str, Any]:
    """Load tiered baseline; empty dict when missing (advisory feature)."""
    baseline_path = path or DEFAULT_BASELINE_PATH
    try:
        return json.loads(Path(baseline_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def compare_to_baseline(
    profiles: list[ChapterStructureProfile],
    baseline: dict[str, Any] | None = None,
    *,
    tier: str = "t2",
) -> list[str]:
    """Advisory deviations of a generated book vs the tier baseline.

    Returns human-readable findings; empty list = no notable deviation or no
    baseline available. Never raises — this is advisory-only by design.
    """
    resolved = baseline if baseline is not None else load_structure_baseline()
    tier_baseline = (resolved or {}).get(tier) or {}
    if not tier_baseline or not profiles:
        return []
    own = aggregate_profiles(profiles)
    findings: list[str] = []
    for key, label, low_msg, high_msg in (
        (
            "dialogue_ratio",
            "对话占比",
            "明显低于真书基线 — 可能旁白/叙述过载",
            "明显高于真书基线 — 可能场景骨架化",
        ),
        (
            "short_sentence_ratio",
            "超短句占比",
            "低于真书基线",
            "高于真书基线 — 碎句癖风险",
        ),
        (
            "avg_paragraph",
            "平均段长",
            "低于真书基线 — 段落过碎",
            "高于真书基线 — 大段压迫感",
        ),
    ):
        reference = tier_baseline.get(key) or {}
        p25, p75 = reference.get("p25"), reference.get("p75")
        value = (own.get(key) or {}).get("median")
        if value is None or p25 is None or p75 is None:
            continue
        if value < p25:
            findings.append(f"{label} {value} {low_msg}（基线 p25={p25}）")
        elif value > p75:
            findings.append(f"{label} {value} {high_msg}（基线 p75={p75}）")
    hook_reference = (tier_baseline.get("ending_hook") or {}).get("mean")
    own_hook = (own.get("ending_hook") or {}).get("mean")
    if hook_reference is not None and own_hook is not None and own_hook < hook_reference - 0.2:
        findings.append(
            f"章末钩子率 {own_hook} 低于真书基线均值 {hook_reference} — 检查 cliffhanger 兑现"
        )
    return findings
