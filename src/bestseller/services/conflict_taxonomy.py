"""Conflict taxonomy for scene-level diversity constraints.

Each scene is characterized by a 4-tuple `(object, layer, nature, resolvability)`.
A fifth optional field `conflict_id` tags a specific instance (e.g. "林鸢-姜澄-信任裂痕-v2")
so that recurring conflicts can be counted across the book.

The module is pure data + pure helpers — it has zero LLM cost and no IO.

Used by:
  * `deduplication.build_conflict_diversity_block` — prompt-side constraint
  * `context.compute_conflict_history` — reads recent SceneCard.metadata_json
  * `planner` — fills in `conflict_tuple` when it emits a scene contract
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any, Iterable


# ---------------------------------------------------------------------------
# 4 axes — each value is a stable id (persisted to metadata_json)
# ---------------------------------------------------------------------------

# Axis A — 对抗对象
OBJECT_TYPES: dict[str, str] = {
    "self": "自我对抗（内在欲望/身份/恐惧冲突）",
    "person": "人际对抗（一对一对手/情人/兄妹等）",
    "group": "群体对抗（派系/小团体/家族）",
    "society": "社会对抗（体制/阶级/规则）",
    "nature": "自然对抗（环境/天灾/生存）",
    "technology": "技术对抗（系统/算法/器物）",
    "supernatural_fate": "命运/超自然对抗（神明/预言/宿命）",
}

# Axis B — 冲突层次
LAYER_TYPES: dict[str, str] = {
    "inner_desire": "内在欲望层（想要但不能要）",
    "inner_identity": "内在身份层（我是谁 / 不是谁）",
    "personal_relation": "私人关系层（亲密 / 背叛 / 信任）",
    "communal": "社群层（同袍/派系荣誉/群体归属）",
    "institutional": "制度层（法/规/阶/礼教）",
    "cosmic": "宇宙/形而上层（天道/熵/命）",
}

# Axis C — 冲突性质
NATURE_TYPES: dict[str, str] = {
    "antagonistic": "纯对抗（零和）",
    "cooperative_game": "合作博弈（共利但目标有偏差）",
    "moral_dilemma": "道德两难（两个善的抉择）",
    "information_asymmetry": "信息不对称（谁知道什么）",
    "value_clash": "价值碰撞（根本信念冲突）",
    "resource_scarcity": "资源稀缺（非零和但受限）",
    "temporal_irreversible": "时间不可逆（倒计时 / 来不及）",
}

# Axis D — 可解性
RESOLVABILITY_TYPES: dict[str, str] = {
    "resolvable": "可解（赢则解）",
    "tragic_inevitable": "悲剧必然（结构性无解）",
    "dynamic_equilibrium": "动态均衡（此消彼长）",
    "transformative": "蜕变式（关系/自我发生质变）",
}


# Genre-specific sub-pools (女主无 CP 现言向)
GENRE_POOLS: dict[str, list[str]] = {
    "female_lead_no_cp": [
        "self_identity",             # 自我认同（我是主角还是工具）
        "sisterhood_fracture",       # 姐妹/女性盟友裂痕
        "female_lineage",            # 母系/女性世代传承
        "gendered_bias",             # 性别偏见制度性
        "faction_politics",          # 派系政治
        "origin_mystery",            # 身世悬念
        "career_path_choice",        # 事业/志业选择
        "revenge_vs_letgo",          # 复仇与放下
        "refuse_savior_trope",       # 拒绝被拯救
        "intra_female_inequality",   # 女性内部不平等
        "reformer_vs_beneficiary",   # 改革者 vs 既得利益者
    ],
    "cultivation_xianxia": [
        "dao_vs_human_heart",
        "sect_politics",
        "bloodline_destiny",
        "enlightenment_cost",
        "master_disciple_debt",
    ],
    "crime_thriller": [
        "evidence_asymmetry",
        "legal_vs_moral",
        "survivor_vs_system",
        "obsession_vs_procedure",
    ],
    "romance": [
        "commitment_fear",
        "past_trauma_interference",
        "value_divergence",
        "class_barrier",
    ],
    "sci_fi": [
        "human_vs_ai",
        "time_paradox",
        "transhuman_identity",
        "resource_civilization",
    ],
    "epic_fantasy": [
        "prophecy_burden",
        "succession_war",
        "racial_tension",
        "dark_lord_corruption",
    ],
}

# "Emerging" / novel conflict pool — inject at least once every 30 chapters
EMERGING_POOL: list[str] = [
    "civilizational_scale",     # 文明级别冲突
    "information_sovereignty",  # 信息主权
    "institution_vs_individual",
    "algorithmic_fate",         # 算法命运
    "attention_economy",        # 注意力经济
    "memory_ownership",         # 记忆所有权
    "cross_scale_ethics",       # 跨尺度伦理
    "post_truth",               # 后真相
    "derivative_self",          # 衍生自我
]


@dataclass(frozen=True)
class ConflictTuple:
    """Immutable 4-axis conflict signature for one scene."""

    object: str               # Axis A
    layer: str                # Axis B
    nature: str               # Axis C
    resolvability: str        # Axis D
    conflict_id: str | None = None   # optional stable id for recurring conflicts

    def as_dict(self) -> dict[str, str | None]:
        return {
            "object": self.object,
            "layer": self.layer,
            "nature": self.nature,
            "resolvability": self.resolvability,
            "conflict_id": self.conflict_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ConflictTuple | None":
        obj = data.get("object")
        layer = data.get("layer")
        nature = data.get("nature")
        resolv = data.get("resolvability")
        if not (obj and layer and nature and resolv):
            return None
        return cls(
            object=str(obj),
            layer=str(layer),
            nature=str(nature),
            resolvability=str(resolv),
            conflict_id=(str(data["conflict_id"]) if data.get("conflict_id") else None),
        )


# ---------------------------------------------------------------------------
# Similarity — used by post-hoc rewrite triggers
# ---------------------------------------------------------------------------

_SIM_WEIGHTS = {
    "object": 0.20,
    "layer": 0.30,
    "nature": 0.20,
    "conflict_id": 0.30,
}


def conflict_similarity(a: ConflictTuple, b: ConflictTuple) -> float:
    """Weighted equality score ∈ [0, 1].

    Weights: layer 0.3, conflict_id 0.3, object 0.2, nature 0.2.
    (resolvability intentionally excluded — it's a meta property, not a content one.)
    """
    score = 0.0
    if a.object == b.object:
        score += _SIM_WEIGHTS["object"]
    if a.layer == b.layer:
        score += _SIM_WEIGHTS["layer"]
    if a.nature == b.nature:
        score += _SIM_WEIGHTS["nature"]
    if a.conflict_id and b.conflict_id and a.conflict_id == b.conflict_id:
        score += _SIM_WEIGHTS["conflict_id"]
    return score


# ---------------------------------------------------------------------------
# Switching-rule evaluator — returns violations for a candidate tuple
# ---------------------------------------------------------------------------

def evaluate_switching_rules(
    recent: list[ConflictTuple],
    *,
    require_internal_within_5: bool = True,
    forbid_conflict_id_repeat_within: int = 10,
    max_same_id_in_window: int = 3,
) -> dict[str, Any]:
    """Analyze recent conflict history and return what the next scene must avoid.

    Input `recent` is ordered most-recent-first, length ≤ 30.
    """
    if not recent:
        return {
            "must_switch_axis_ab": False,
            "forbid_object": [],
            "forbid_layer": [],
            "forbid_nature": [],
            "forbid_conflict_id": [],
            "needs_internal": False,
            "recent_tuple_count": 0,
        }

    # Rule 1: next scene MUST differ from last scene on Axis A or Axis B.
    last = recent[0]
    forbid_object = [last.object]
    forbid_layer = [last.layer]

    # Rule 2: within 3 scenes A/B/C must each switch at least once.
    # => collect A/B/C values from last 3 scenes; if all same → forbid them all.
    last3 = recent[:3]
    if len(last3) >= 3:
        if len({t.object for t in last3}) == 1:
            forbid_object.append(last3[0].object)
        if len({t.layer for t in last3}) == 1:
            forbid_layer.append(last3[0].layer)

    forbid_nature: list[str] = []
    if len(last3) >= 3 and len({t.nature for t in last3}) == 1:
        forbid_nature.append(last3[0].nature)

    # Rule 3: within 5 scenes must include ≥1 inner_* layer.
    last5 = recent[:5]
    needs_internal = require_internal_within_5 and not any(
        t.layer.startswith("inner_") for t in last5
    )

    # Rule 4: same conflict_id must not appear ≥ max_same_id_in_window in last N scenes.
    win = recent[:forbid_conflict_id_repeat_within]
    id_counts = Counter(t.conflict_id for t in win if t.conflict_id)
    forbid_conflict_id = [
        cid for cid, n in id_counts.items() if n >= max_same_id_in_window
    ]

    return {
        "must_switch_axis_ab": True,
        "forbid_object": sorted(set(forbid_object)),
        "forbid_layer": sorted(set(forbid_layer)),
        "forbid_nature": sorted(set(forbid_nature)),
        "forbid_conflict_id": sorted(forbid_conflict_id),
        "needs_internal": needs_internal,
        "recent_tuple_count": len(recent),
    }


# ---------------------------------------------------------------------------
# Emerging-pool injection cadence
# ---------------------------------------------------------------------------

def should_inject_emerging(
    chapter_number: int,
    last_emerging_chapter: int | None,
    cadence_chapters: int = 30,
) -> bool:
    """Return True if an emerging conflict should be offered this scene."""
    if last_emerging_chapter is None:
        return chapter_number >= 10
    return (chapter_number - last_emerging_chapter) >= cadence_chapters


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def label_for_object(key: str) -> str:
    return OBJECT_TYPES.get(key, key)


def label_for_layer(key: str) -> str:
    return LAYER_TYPES.get(key, key)


def label_for_nature(key: str) -> str:
    return NATURE_TYPES.get(key, key)


def label_for_resolvability(key: str) -> str:
    return RESOLVABILITY_TYPES.get(key, key)


def candidate_pool_for_genre(genre_pool_key: str | None) -> list[str]:
    if not genre_pool_key:
        return []
    return list(GENRE_POOLS.get(genre_pool_key, []))


def all_object_types() -> Iterable[str]:
    return OBJECT_TYPES.keys()


def all_layer_types() -> Iterable[str]:
    return LAYER_TYPES.keys()


def all_nature_types() -> Iterable[str]:
    return NATURE_TYPES.keys()
