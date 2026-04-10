"""Chinese AI-flavor detection and removal (anti-slop).

Three-tier system inspired by autonovel's mechanical screening:
- Tier 1 (kill-on-sight): Remove the sentence containing the phrase
- Tier 2 (suspicious-in-clusters): Flag when 3+ appear in a scene
- Tier 3 (zero-information filler): Informational only, not auto-removed

All detection is regex/substring-based — zero LLM cost.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


# ── Tier 1: Kill-on-sight ────────────────────────────────────────────────
# These phrases are unmistakable AI-generation artifacts. A sentence
# containing one should be removed outright.

_TIER1_KILL_PHRASES: tuple[str, ...] = (
    # 空洞总结式
    "显而易见",
    "毫无疑问",
    "不言而喻",
    "众所周知",
    "毋庸置疑",
    "值得注意的是",
    "不可否认",
    # 陈腐比喻
    "令人叹为观止",
    "如同打翻了五味瓶",
    "仿佛被一只无形的手",
    "宛如一颗石子投入平静的湖面",
    "宛如一把锋利的刀",
    "如同一记重锤",
    "心中五味杂陈",
    "百感交集",
    "思绪万千",
    # 过度抒情套路
    "泪水模糊了双眼",
    "泪水在眼眶中打转",
    "泪水夺眶而出",
    "心如刀割",
    "心如死灰",
    "万箭穿心",
    "如释重负地长舒一口气",
    # 万能过渡
    "时间仿佛在这一刻静止",
    "空气仿佛凝固了",
    "世界仿佛安静了下来",
)

# ── Tier 2: Suspicious in clusters ───────────────────────────────────────
# Individual use is fine; 3+ occurrences in a single scene suggests
# the LLM fell into a pattern loop.

_TIER2_CLUSTER_PHRASES: tuple[str, ...] = (
    # 虚弱修饰副词
    "不禁",
    "忍不住",
    "情不自禁",
    "缓缓",
    "轻轻",
    "深深",
    "悠悠",
    "微微",
    "淡淡",
    "默默",
    "静静",
    # 模板式微表情
    "眼眶微红",
    "嘴角微扬",
    "嘴角上扬",
    "嘴角勾起",
    "眉头微皱",
    "眉头紧锁",
    "瞳孔骤缩",
    "瞳孔微缩",
    # 万能内心描写
    "心头一紧",
    "心头一震",
    "心中一沉",
    "心头涌起",
    "内心深处",
    "脑海中闪过",
    "脑海中浮现",
    # 万能时间标记
    "在这一刻",
    "在那一瞬间",
    "就在这时",
    "下一秒",
)

_TIER2_CLUSTER_THRESHOLD = 3  # flag when this many distinct tier2 phrases appear

# ── Tier 3: Zero-information filler ──────────────────────────────────────
# These words add no information. Not auto-removed, but counted for
# quality scoring.

_TIER3_FILLER_PHRASES: tuple[str, ...] = (
    "某种意义上",
    "从某种程度上来说",
    "不得不说",
    "说实话",
    "坦白说",
    "事实上",
    "总而言之",
    "换句话说",
    "毕竟",
)

# ── Chinese sentence splitting ───────────────────────────────────────────
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[。！？…\n])")


@dataclass
class AiSlopReport:
    """Result of AI-flavor detection on a piece of text."""

    tier1_hits: list[str] = field(default_factory=list)
    tier2_hits: list[str] = field(default_factory=list)
    tier2_cluster_count: int = 0
    tier3_hits: list[str] = field(default_factory=list)
    tier2_over_threshold: bool = False

    @property
    def has_severe_slop(self) -> bool:
        return len(self.tier1_hits) > 0

    @property
    def has_cluster_slop(self) -> bool:
        return self.tier2_over_threshold

    @property
    def slop_score(self) -> float:
        """0.0 = clean, 1.0 = maximum slop. Used for quality scoring."""
        t1 = min(len(self.tier1_hits) * 0.3, 1.0)
        t2 = min(self.tier2_cluster_count * 0.05, 0.5) if self.tier2_over_threshold else 0.0
        t3 = min(len(self.tier3_hits) * 0.02, 0.2)
        return min(t1 + t2 + t3, 1.0)


def detect_ai_slop(content_md: str) -> AiSlopReport:
    """Detect AI-flavor phrases at all three tiers. Zero LLM cost."""
    report = AiSlopReport()
    if not content_md:
        return report

    for phrase in _TIER1_KILL_PHRASES:
        if phrase in content_md:
            report.tier1_hits.append(phrase)

    distinct_t2 = 0
    for phrase in _TIER2_CLUSTER_PHRASES:
        count = content_md.count(phrase)
        if count > 0:
            report.tier2_hits.append(phrase)
            distinct_t2 += 1
            report.tier2_cluster_count += count
    report.tier2_over_threshold = distinct_t2 >= _TIER2_CLUSTER_THRESHOLD

    for phrase in _TIER3_FILLER_PHRASES:
        if phrase in content_md:
            report.tier3_hits.append(phrase)

    return report


def strip_tier1_slop(content_md: str) -> str:
    """Remove sentences containing tier-1 kill-on-sight phrases.

    Operates at sentence granularity (split on 。！？…) to avoid
    destroying paragraph flow. If a sentence is the only one in a
    paragraph, the entire paragraph is removed.
    """
    if not content_md:
        return content_md

    cleaned_paragraphs: list[str] = []
    for paragraph in content_md.split("\n"):
        if not paragraph.strip():
            cleaned_paragraphs.append(paragraph)
            continue
        has_kill = any(phrase in paragraph for phrase in _TIER1_KILL_PHRASES)
        if not has_kill:
            cleaned_paragraphs.append(paragraph)
            continue
        # Split into sentences and keep only clean ones
        sentences = _SENTENCE_SPLIT_RE.split(paragraph)
        kept = [
            s for s in sentences
            if s.strip() and not any(phrase in s for phrase in _TIER1_KILL_PHRASES)
        ]
        if kept:
            cleaned_paragraphs.append("".join(kept))
        # else: entire paragraph was slop — drop it

    result = "\n".join(cleaned_paragraphs)
    result = re.sub(r"\n{3,}", "\n\n", result)
    return result.strip()
