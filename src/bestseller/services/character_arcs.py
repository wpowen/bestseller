"""Character arc taxonomy + 5-layer thinking contract (Stage C).

Targets the diagnosed symptom "人物公式化、无成长弧" — across the sampled
89-chapter novel the protagonist only unlocks information; she never
internally changes.

Two core artefacts:

1. **Arc types** (6) — the shape of the character's internal journey.
2. **Percentile beat table** — where each character's lie / want / need
   should be pressed at each story percentile.
3. **Inner structure schema** — lie / want / need / ghost / truth etc.
4. **5-layer thinking contract** — SENSATION → PERCEPTION → JUDGMENT →
   DECISION → RATIONALIZATION.

All pure-data + pure helpers. No LLM cost, no IO.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable


# ---------------------------------------------------------------------------
# 6 arc types
# ---------------------------------------------------------------------------

ARC_TYPES: dict[str, str] = {
    "POSITIVE_CHANGE":  "正向改变弧（从 lie 到 truth，want 被放下换 need）",
    "FLAT":             "平弧（角色本身不变，改变世界）",
    "DISILLUSIONMENT":  "幻灭弧（从一个 lie 到更悲观的 truth）",
    "FALL":             "堕落弧（从 truth 坠向 lie）",
    "CORRUPTION":       "腐化弧（主动拥抱 lie）",
    "FLAT_NEGATIVE":    "平负弧（角色从未改变，困在 lie 里）",
}


# ---------------------------------------------------------------------------
# Inner structure schema for a character
# ---------------------------------------------------------------------------

INNER_STRUCTURE_FIELDS: tuple[str, ...] = (
    "ghost",              # past defining trauma
    "wound",              # the belief it formed
    "lie_believed",       # core false belief
    "truth_to_learn",     # what the novel must teach
    "want_external",      # surface desire (pseudo-goal)
    "need_internal",      # real need (true goal)
    "fatal_flaw",         # fatal flaw
    "fear_core",          # core fear
    "desire_shadow",      # shadow desire (unspoken)
    "defense_mechanisms", # list of 2-4
)


@dataclass(frozen=True)
class CharacterInnerStructure:
    """Immutable structure carrying a character's arc scaffolding."""

    ghost: str | None = None
    wound: str | None = None
    lie_believed: str | None = None
    truth_to_learn: str | None = None
    want_external: str | None = None
    need_internal: str | None = None
    fatal_flaw: str | None = None
    fear_core: str | None = None
    desire_shadow: str | None = None
    defense_mechanisms: tuple[str, ...] = field(default_factory=tuple)
    arc_type: str = "POSITIVE_CHANGE"

    def as_dict(self) -> dict[str, Any]:
        return {
            **{f: getattr(self, f) for f in INNER_STRUCTURE_FIELDS[:-1]},
            "defense_mechanisms": list(self.defense_mechanisms),
            "arc_type": self.arc_type,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CharacterInnerStructure":
        defenses = data.get("defense_mechanisms") or []
        if not isinstance(defenses, (list, tuple)):
            defenses = []
        return cls(
            ghost=data.get("ghost"),
            wound=data.get("wound"),
            lie_believed=data.get("lie_believed"),
            truth_to_learn=data.get("truth_to_learn"),
            want_external=data.get("want_external"),
            need_internal=data.get("need_internal"),
            fatal_flaw=data.get("fatal_flaw"),
            fear_core=data.get("fear_core"),
            desire_shadow=data.get("desire_shadow"),
            defense_mechanisms=tuple(str(d) for d in defenses if d),
            arc_type=str(data.get("arc_type") or "POSITIVE_CHANGE"),
        )

    def is_complete(self) -> bool:
        """Minimum completeness check — the 4 load-bearing fields."""
        return all([
            self.lie_believed, self.truth_to_learn,
            self.want_external, self.need_internal,
        ])


# ---------------------------------------------------------------------------
# Percentile beat table
# ---------------------------------------------------------------------------

# Ordered list of (percentile_low, percentile_high, beat_key, description).
# A chapter at percentile p triggers beats where low ≤ p ≤ high.
BEAT_TABLE: list[tuple[float, float, str, str]] = [
    (0.00, 0.10, "normal_world",           "Normal World — 展示 lie 的保护壳"),
    (0.10, 0.18, "lie_first_challenged",   "Lie First Challenged — 第一次有人/事说「你错了」"),
    (0.20, 0.30, "first_plot_point",       "First Plot Point — 跨越门槛 + 立誓（常常基于 lie）"),
    (0.32, 0.42, "first_temptation",       "First Temptation — need 招手，被主角拒绝"),
    (0.45, 0.55, "midpoint_false_victory", "Midpoint — 用 lie 的逻辑赢，但伏笔已埋下"),
    (0.58, 0.68, "regression",             "Regression — 退回 lie，且更极端"),
    (0.70, 0.80, "dark_night",             "Dark Night — want 崩塌，lie 彻底失败"),
    (0.78, 0.85, "epiphany",               "Epiphany — 承认 truth"),
    (0.85, 0.97, "climax",                 "Climax — 以 truth 完成 want，或放弃 want"),
    (0.97, 1.01, "new_equilibrium",        "New Equilibrium — 变形后的主角重演首章场景"),
]

BEAT_ORDER: list[str] = [entry[2] for entry in BEAT_TABLE]


def compute_arc_stage_for_chapter(
    chapter_number: int,
    total_chapters: int,
) -> dict[str, Any]:
    """Return the beat(s) active at this chapter's percentile."""
    if total_chapters <= 0:
        total_chapters = max(chapter_number, 1)
    percentile = chapter_number / total_chapters
    active: list[tuple[str, str]] = []
    for low, high, key, desc in BEAT_TABLE:
        if low <= percentile <= high:
            active.append((key, desc))

    # Current "primary" beat is the most advanced one whose low ≤ percentile.
    primary = None
    for low, _, key, _ in BEAT_TABLE:
        if low <= percentile:
            primary = key
    return {
        "percentile": round(percentile, 3),
        "chapter_number": chapter_number,
        "total_chapters": total_chapters,
        "primary_beat": primary,
        "active_beats": [k for k, _ in active],
        "active_beats_description": [d for _, d in active],
    }


def beats_elapsed_before(chapter_number: int, total_chapters: int) -> list[str]:
    """Return beats whose window ended before this chapter."""
    if total_chapters <= 0:
        return []
    percentile = chapter_number / total_chapters
    return [key for low, high, key, _ in BEAT_TABLE if high < percentile]


# ---------------------------------------------------------------------------
# 5-layer thinking contract
# ---------------------------------------------------------------------------

FIVE_LAYER_TEMPLATE_ZH: str = (
    "【五层思考契约 — POV 决策点必须穿过】\n"
    "1. SENSATION 身体感觉：不写「她很怕」——写「胸口像压着湿棉被」「指甲掐进掌心」。\n"
    "2. PERCEPTION 选择性感知：由 lie/flaw 决定她注意到什么、忽略什么。\n"
    "3. JUDGMENT 评判：判断带着 lie 的滤镜——她的「理性」服务于 lie。\n"
    "4. DECISION 决定：明确写出决定的动作/选择。\n"
    "5. RATIONALIZATION 合理化：她给自己讲的故事——暴露 lie 的运作；\n"
    "   若处于 lie 裂缝期（regression/dark_night/epiphany），写出「可是……」的裂痕。"
)

FIVE_LAYER_TEMPLATE_EN: str = (
    "[FIVE-LAYER THINKING CONTRACT — required at POV decision points]\n"
    "1. SENSATION — body sensation (not 'she was afraid' but 'nails biting into her palm').\n"
    "2. PERCEPTION — selective seeing, shaped by lie/flaw.\n"
    "3. JUDGMENT — judgment through the lie's filter.\n"
    "4. DECISION — the concrete choice.\n"
    "5. RATIONALIZATION — the story she tells herself; expose the lie at work.\n"
    "   During lie-fracture beats (regression/dark_night/epiphany), write the 'but…' crack."
)


def render_five_layer_block(*, language: str = "zh-CN") -> str:
    return FIVE_LAYER_TEMPLATE_ZH if language.lower().startswith("zh") else FIVE_LAYER_TEMPLATE_EN


# ---------------------------------------------------------------------------
# Emotion words to forbid based on the character's stage
# (suppresses telling-not-showing)
# ---------------------------------------------------------------------------

FORBIDDEN_EMOTION_WORDS_ZH: frozenset[str] = frozenset({
    "害怕", "恐惧", "愤怒", "伤心", "难过",
    "开心", "高兴", "激动", "绝望", "崩溃",
    "温暖", "寒冷"  # as naked emotional labels
})

FORBIDDEN_EMOTION_WORDS_EN: frozenset[str] = frozenset({
    "afraid", "scared", "angry", "sad", "happy", "joyful",
    "excited", "devastated", "furious", "anxious",
})
