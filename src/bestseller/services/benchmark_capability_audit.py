# ruff: noqa: RUF001
"""Benchmark-sample capability audit for the novel framework.

This module builds a repo-safe assessment from a private local book corpus:

* choose a 40-book benchmark sample set without committing titles or paths;
* align sample categories with the framework's category/profile/grammar layers;
* produce a capability matrix, gap register, and optimization roadmap;
* provide privacy checks for repo-visible artifacts.

The service is intentionally deterministic and LLM-free.  Full structural
distillation can run later through the existing distillation pipeline; this
service creates the assessment scaffold and capability baseline.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
import json
from pathlib import Path
from typing import Any

from bestseller.services.category_hard_engines import (
    load_category_hard_engine_contracts,
    run_category_engine_fixture_benchmark,
)
from bestseller.services.distillation_book_parser import SUPPORTED_FORMATS, parse_source_book
from bestseller.services.distillation_corpus import (
    dedupe_corpus_paths_by_title,
    normalized_title_group_key,
)
from bestseller.services.distillation_genre_classifier import ALLOWED_DISTILLATION_GENRE_BUCKETS
from bestseller.services.genre_review_profiles import load_genre_review_profiles
from bestseller.services.novel_categories import load_novel_category_registry
from bestseller.services.prompt_packs import load_prompt_pack_registry
from bestseller.services.sample_quality_parity_gate import SampleQualityParityThresholds
from bestseller.services.story_design_grammars import load_story_design_grammar_registry

CAPABILITY_DIMENSIONS: tuple[str, ...] = (
    "category_coverage",
    "planning_capability",
    "state_engine",
    "chapter_execution",
    "whole_book_continuity",
    "quality_gates",
    "repair_loop",
    "anti_copy_safety",
)

SUPPORT_LEVELS: tuple[str, ...] = ("ready", "partial", "prompt-only", "unsupported")

TARGET_FILL_CATEGORIES: tuple[str, ...] = (
    "action-progression",
    "strategy-worldbuilding",
    "suspense-mystery",
    "otherworld-cross-system",
    "base-building",
    "relationship-driven",
    "esports-competition",
    "female-growth-ncp",
    "eastern-aesthetic",
)

_CATEGORY_NAMES: dict[str, str] = {
    "action-progression": "升级流 / Action Progression",
    "strategy-worldbuilding": "权谋历史与世界构建 / Strategy Worldbuilding",
    "suspense-mystery": "悬疑规则 / Suspense Mystery",
    "otherworld-cross-system": "异界穿越系统 / Otherworld Cross-System",
    "base-building": "基建经营 / Base Building",
    "relationship-driven": "关系驱动 / Relationship Driven",
    "esports-competition": "电竞游戏 / Esports Competition",
    "female-growth-ncp": "女性成长无CP / Female Growth NCP",
    "eastern-aesthetic": "东方美学 / Eastern Aesthetic",
    "urban-contemporary": "都市职业现实 / Urban Contemporary",
    "science-fiction-progression": "科幻机甲进阶 / Science Fiction Progression",
    "wuxia-jianghu": "武侠江湖 / Wuxia Jianghu",
}

_CATEGORY_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "suspense-mystery",
        (
            "悬疑",
            "推理",
            "侦探",
            "探案",
            "刑侦",
            "谋杀",
            "恐怖",
            "惊悚",
            "诡",
            "鬼",
            "怪谈",
            "灵异",
            "法医",
            "盗墓",
            "冒险屋",
            "恐怖屋",
            "夜不语",
            "阴阳",
            "克苏鲁",
        ),
    ),
    (
        "esports-competition",
        (
            "电竞",
            "网游",
            "网游之",
            "游戏",
            "全职高手",
            "英雄联盟",
            "荣耀",
            "冠军",
            "玩家",
            "跑团",
            "竞技",
        ),
    ),
    (
        "base-building",
        (
            "种田",
            "经营",
            "基建",
            "领主",
            "农家",
            "农场",
            "农庄",
            "农民",
            "农夫",
            "山村",
            "部落",
            "原始人",
            "建设",
            "大龙挂了",
        ),
    ),
    (
        "relationship-driven",
        (
            "言情",
            "恋爱",
            "爱情",
            "情人",
            "王妃",
            "王爷",
            "总裁",
            "婚",
            "宠",
            "夫君",
            "夫人",
            "公主",
            "皇后",
            "宫斗",
            "青春",
            "校园",
            "女友",
            "男友",
            "老婆",
            "东宫",
        ),
    ),
    ("female-growth-ncp", ("大女主", "女帝", "女强", "无CP", "无cp", "女子会", "贵女")),
    (
        "strategy-worldbuilding",
        (
            "三国",
            "大唐",
            "唐朝",
            "大宋",
            "北宋",
            "大明",
            "明朝",
            "秦",
            "汉",
            "帝国",
            "王朝",
            "争霸",
            "权谋",
            "宫廷",
            "御史",
            "宰执",
            "锦衣",
            "不良人",
            "历史",
            "战争",
            "战国",
            "贞观",
            "夺嫡",
        ),
    ),
    (
        "science-fiction-progression",
        (
            "科幻",
            "星际",
            "银河",
            "宇宙",
            "星海",
            "舰",
            "机甲",
            "黑科技",
            "师士",
            "未来世界",
            "外星",
        ),
    ),
    (
        "otherworld-cross-system",
        (
            "异界",
            "异世",
            "穿越",
            "重生",
            "快穿",
            "系统",
            "主神",
            "诸天",
            "位面",
            "时空",
            "无限",
            "末世",
            "末日",
            "随身",
            "万界",
            "二次元",
            "漫威",
        ),
    ),
    ("eastern-aesthetic", ("山海", "聊斋", "古风", "国风", "水墨", "志怪", "狐妖")),
    (
        "wuxia-jianghu",
        (
            "武侠",
            "江湖",
            "掌门",
            "蜀山",
            "华山",
            "唐门",
            "侠",
            "剑客",
            "刀客",
        ),
    ),
    (
        "action-progression",
        (
            "修仙",
            "修真",
            "仙侠",
            "玄幻",
            "升级",
            "武神",
            "武尊",
            "武帝",
            "妖",
            "魔",
            "法师",
            "剑",
            "天尊",
            "至尊",
            "圣王",
            "召唤",
            "御兽",
            "斗破",
            "遮天",
            "完美世界",
            "凡人修仙",
            "万古",
            "巫师",
            "吞噬",
            "星空",
        ),
    ),
    ("urban-contemporary", ("都市", "职场", "娱乐圈", "直播", "公司", "医院", "猎场")),
)

_PROMPT_PACKS_BY_CATEGORY: dict[str, tuple[str, ...]] = {
    "action-progression": (
        "xianxia-upgrade-core",
        "litrpg-progression",
        "urban-cultivation-2.0",
        "urban-power-reversal",
        "system-apocalypse-healer",
        "cozy-litrpg",
    ),
    "strategy-worldbuilding": ("history-strategy", "epic-fantasy", "space-opera", "scifi-starwar"),
    "suspense-mystery": ("suspense-mystery", "psychological-thriller"),
    "otherworld-cross-system": (
        "villainess-reincarnation",
        "cozy-litrpg",
        "system-apocalypse-healer",
        "game-esport",
    ),
    "base-building": ("apocalypse-supply-chain", "cozy-fantasy", "shezhu-bailan-comedy"),
    "relationship-driven": (
        "romance-tension-growth",
        "romantasy",
        "dark-romance",
        "mafia-romance",
        "reverse-harem",
        "entertainment-sweet",
    ),
    "esports-competition": ("game-esport",),
    "female-growth-ncp": ("female-palace", "villainess-reincarnation"),
    "eastern-aesthetic": ("eastern-aesthetic",),
    "urban-contemporary": ("entertainment-sweet", "shezhu-bailan-comedy", "urban-power-reversal"),
    "science-fiction-progression": ("scifi-starwar", "space-opera"),
    "wuxia-jianghu": ("history-strategy", "eastern-aesthetic"),
}

_DISTILLATION_BUCKETS_BY_CATEGORY: dict[str, tuple[str, ...]] = {
    "action-progression": ("action-progression", "eastern-progression-fantasy"),
    "strategy-worldbuilding": ("strategy-worldbuilding", "historical-fiction"),
    "suspense-mystery": ("suspense-mystery",),
    "otherworld-cross-system": ("otherworld-cross-system",),
    "base-building": ("base-building",),
    "relationship-driven": ("romance-relationship",),
    "esports-competition": ("game-esports",),
    "female-growth-ncp": ("female-growth-ncp",),
    "eastern-aesthetic": ("eastern-aesthetic",),
    "urban-contemporary": ("urban-contemporary",),
    "science-fiction-progression": ("science-fiction-progression",),
    "wuxia-jianghu": ("wuxia-jianghu",),
}

_KNOWN_GAPS: dict[str, tuple[str, ...]] = {
    "action-progression": (
        "已建立类别硬引擎契约, 仍需把机会地图、资源变动、派系后续反应接入章节后自动状态折叠。",
    ),
    "strategy-worldbuilding": (
        "已建立类别硬引擎契约, 仍需把制度压力、战役物流、财政与朝堂议程接入真实章节闭环。",
    ),
    "suspense-mystery": (
        "已建立类别硬引擎契约, 仍需用 live 章节验证 rule lattice、证据合法性、嫌疑人/时间线公平性。",
    ),
    "otherworld-cross-system": (
        "已建立类别硬引擎契约, 仍需把跨体系映射、身份债务、异常暴露成本接入章节后状态更新。",
    ),
    "base-building": (
        "已建立类别硬引擎契约, 仍需把 settlement inventory、物流、人口、建筑队列接入生成闭环。",
    ),
    "relationship-driven": (
        "已建立类别硬引擎契约, 仍需把亲密边界、误会拓扑、情感兑现节奏接入章节级 gate。",
    ),
    "esports-competition": (
        "已建立类别硬引擎契约, 仍需把比赛状态、BP/版本、队伍战术和赛事压力接入 match ledger。",
    ),
    "female-growth-ncp": (
        "已建立类别硬引擎契约, 仍需把事业阶梯、社会压力和 agency debt 接入长篇状态闭环。",
    ),
    "eastern-aesthetic": (
        "已建立类别硬引擎契约, 仍需把意象链、礼法压力和诗性物件账本接入 live pilot 验证。",
    ),
    "urban-contemporary": (
        "都市职业/现实题材缺少职业台阶、组织政治、口碑/资本/舆论状态模型。",
    ),
    "science-fiction-progression": (
        "科幻机甲缺少技术约束、能源物流、舰队/战术状态和科研合法性验证。",
    ),
    "wuxia-jianghu": (
        "武侠江湖缺少门派声望、江湖规矩、侠义债务、武学代价的一等类别引擎。",
    ),
}

_DIMENSION_STATIC_SUPPORT: dict[str, dict[str, str]] = {
    "action-progression": {
        "state_engine": "partial",
        "chapter_execution": "partial",
        "whole_book_continuity": "partial",
        "quality_gates": "partial",
        "repair_loop": "partial",
        "anti_copy_safety": "partial",
    },
    "strategy-worldbuilding": {
        "state_engine": "partial",
        "chapter_execution": "partial",
        "whole_book_continuity": "partial",
        "quality_gates": "partial",
        "repair_loop": "partial",
        "anti_copy_safety": "partial",
    },
    "suspense-mystery": {
        "state_engine": "partial",
        "chapter_execution": "partial",
        "whole_book_continuity": "partial",
        "quality_gates": "partial",
        "repair_loop": "partial",
        "anti_copy_safety": "partial",
    },
    "relationship-driven": {
        "state_engine": "partial",
        "chapter_execution": "partial",
        "whole_book_continuity": "partial",
        "quality_gates": "partial",
        "repair_loop": "partial",
        "anti_copy_safety": "partial",
    },
    "female-growth-ncp": {
        "state_engine": "partial",
        "chapter_execution": "partial",
        "whole_book_continuity": "partial",
        "quality_gates": "partial",
        "repair_loop": "partial",
        "anti_copy_safety": "partial",
    },
    "base-building": {
        "state_engine": "prompt-only",
        "chapter_execution": "partial",
        "whole_book_continuity": "partial",
        "quality_gates": "partial",
        "repair_loop": "partial",
        "anti_copy_safety": "partial",
    },
    "otherworld-cross-system": {
        "state_engine": "prompt-only",
        "chapter_execution": "partial",
        "whole_book_continuity": "partial",
        "quality_gates": "prompt-only",
        "repair_loop": "partial",
        "anti_copy_safety": "partial",
    },
    "esports-competition": {
        "state_engine": "prompt-only",
        "chapter_execution": "partial",
        "whole_book_continuity": "partial",
        "quality_gates": "prompt-only",
        "repair_loop": "partial",
        "anti_copy_safety": "partial",
    },
    "eastern-aesthetic": {
        "state_engine": "prompt-only",
        "chapter_execution": "partial",
        "whole_book_continuity": "partial",
        "quality_gates": "prompt-only",
        "repair_loop": "partial",
        "anti_copy_safety": "partial",
    },
    "urban-contemporary": {
        "state_engine": "partial",
        "chapter_execution": "partial",
        "whole_book_continuity": "partial",
        "quality_gates": "partial",
        "repair_loop": "partial",
        "anti_copy_safety": "partial",
    },
    "science-fiction-progression": {
        "state_engine": "partial",
        "chapter_execution": "partial",
        "whole_book_continuity": "partial",
        "quality_gates": "partial",
        "repair_loop": "partial",
        "anti_copy_safety": "partial",
    },
    "wuxia-jianghu": {
        "state_engine": "partial",
        "chapter_execution": "partial",
        "whole_book_continuity": "partial",
        "quality_gates": "partial",
        "repair_loop": "partial",
        "anti_copy_safety": "partial",
    },
}

_CATEGORY_BENCHMARK_FINDINGS: dict[str, dict[str, Any]] = {
    "action-progression": {
        "reader_promise": "主角通过可验证代价和资源积累持续变强。",
        "core_engine": "境界/能力瓶颈 -> 资源争夺 -> 对手升级 -> 阶段突破。",
        "state_variables": ["realm_or_level", "resources", "bottleneck", "faction_pressure"],
        "reward_cadence": "每 3-5 章需要一次可感知收益、线索或战力位移。",
        "risk_patterns": ["无代价升级", "反派只震惊", "资源账本不连续"],
    },
    "strategy-worldbuilding": {
        "reader_promise": "读者看到制度、战争、朝堂和资源博弈如何被主角撬动。",
        "core_engine": "局势压力 -> 策略误判/布局 -> 派系反应 -> 战略后果扩大。",
        "state_variables": ["factions", "institutional_pressure", "logistics", "reputation"],
        "reward_cadence": "每个单元要交付一次局势重估、权力转移或战略反转。",
        "risk_patterns": ["历史背景只做装饰", "计谋靠作者解释", "派系没有后续行动"],
    },
    "suspense-mystery": {
        "reader_promise": "读者能跟随规则、证据和误导逐步逼近真相。",
        "core_engine": "异常/案件 -> 线索链 -> 误导线 -> 规则或真相反转。",
        "state_variables": ["clues", "suspects", "timeline", "rule_costs"],
        "reward_cadence": "每章至少推进一条线索、嫌疑、规则效果或认知反转。",
        "risk_patterns": ["线索不公平", "规则只靠口述", "真相突然空降"],
    },
    "otherworld-cross-system": {
        "reader_promise": "读者看到陌生规则、身份错位和系统收益如何重塑主角选择。",
        "core_engine": "异界规则差异 -> 身份/系统约束 -> 任务或资源收益 -> 世界反噬。",
        "state_variables": ["identity_status", "system_rules", "task_debts", "world_reaction"],
        "reward_cadence": "任务收益必须伴随新规则、新债务或新敌意。",
        "risk_patterns": ["系统万能", "异界只换皮", "身份危机不改变行动"],
    },
    "base-building": {
        "reader_promise": "读者看到据点、资源和人群从脆弱状态逐步变强。",
        "core_engine": "生存缺口 -> 资源调度 -> 建设选择 -> 外部压力验证。",
        "state_variables": ["inventory", "population", "build_queue", "external_threats"],
        "reward_cadence": "每个建设收益必须带来新能力、新消耗或新威胁。",
        "risk_patterns": ["物资凭空出现", "建设无代价", "外部势力不反应"],
    },
    "relationship-driven": {
        "reader_promise": "读者追踪关系距离、误会、承诺和主动选择的真实变化。",
        "core_engine": "欲望/边界 -> 冲突选择 -> 关系轴位移 -> 承诺兑现或延期。",
        "state_variables": ["trust", "distance", "power_balance", "promise_debts"],
        "reward_cadence": "每个关系场景必须改变信任、距离、权力或承诺状态。",
        "risk_patterns": ["关系只暧昧不变化", "主角失去主动性", "误会机械拖延"],
    },
    "esports-competition": {
        "reader_promise": "读者看到版本、战术、团队执行和比赛压力的连续博弈。",
        "core_engine": "版本/对手情报 -> 训练或 BP -> 比赛执行 -> 战术复盘升级。",
        "state_variables": ["match_state", "team_roles", "meta_patch", "tournament_pressure"],
        "reward_cadence": "每场比赛要交付战术发现、配合进步或对手升级。",
        "risk_patterns": ["比赛像普通打架", "队友无功能", "版本和战术不影响结果"],
    },
    "female-growth-ncp": {
        "reader_promise": "读者看到女性主角通过事业、资源和边界选择获得主体性。",
        "core_engine": "社会/事业压力 -> 主动选择 -> 关系与资源重排 -> agency 增强。",
        "state_variables": ["career_stage", "agency_debts", "ally_network", "social_pressure"],
        "reward_cadence": "每个阶段要交付地位、资源、边界或认知上的可见成长。",
        "risk_patterns": ["无CP暗中恋爱化", "事业线空泛", "成长只靠旁人给予"],
    },
    "eastern-aesthetic": {
        "reader_promise": "读者获得东方审美、志怪规则和诗性意象驱动的奇观与余韵。",
        "core_engine": "意象/规则 -> 志怪事件 -> 人情代价 -> 审美化回响。",
        "state_variables": ["motifs", "folk_rules", "aesthetic_debts", "emotional_aftertaste"],
        "reward_cadence": "每个单元要交付新意象、新规则或一次情绪余韵兑现。",
        "risk_patterns": ["只有辞藻没有剧情功能", "志怪规则不稳定", "审美重复"],
    },
    "urban-contemporary": {
        "reader_promise": "读者看到职业台阶、现实利益、关系网络和口碑压力如何改变主角处境。",
        "core_engine": "职业机会 -> 组织博弈 -> 现实代价 -> 口碑/资源位置变化。",
        "state_variables": ["career_stage", "organization_relations", "reputation", "money_contracts"],
        "reward_cadence": "每个单元要交付一次职位、资源、口碑或现实关系的可见位移。",
        "risk_patterns": ["职场像过家家", "金钱合同无后果", "舆论口碑不回流剧情"],
    },
    "science-fiction-progression": {
        "reader_promise": "读者看到科技突破、能源约束、机甲/舰队状态和战场策略的连续进化。",
        "core_engine": "科学约束 -> 研发验证 -> 能源/后勤成本 -> 战术应用与反制。",
        "state_variables": ["tech_constraints", "energy_budget", "research_chain", "fleet_or_mecha_state"],
        "reward_cadence": "每次技术收益必须带来新能力、新成本、新反制或更高层战场问题。",
        "risk_patterns": ["黑科技万能", "能源后勤缺席", "战斗状态不可复盘"],
    },
    "wuxia-jianghu": {
        "reader_promise": "读者看到门派声望、江湖规矩、侠义债务和武学代价如何塑造选择。",
        "core_engine": "江湖规矩 -> 门派/个人选择 -> 侠义债或仇怨 -> 声望与武学代价回流。",
        "state_variables": ["sect_reputation", "jianghu_rules", "martial_costs", "chivalry_debts"],
        "reward_cadence": "每个单元要交付一次名声变化、规矩反噬、武学收益或侠义债兑现。",
        "risk_patterns": ["武功无代价", "江湖规矩只做背景", "门派声望不影响行动"],
    },
}


@dataclass(frozen=True, slots=True)
class BenchmarkSample:
    source_id: str
    source_path: Path
    title_key: str
    canonical_category: str
    sample_reason: str
    processing_status: str
    file_format: str
    chapter_count: int | None = None
    parser_warning_count: int | None = None
    parse_error: str | None = None

    def to_repo_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "source_id": self.source_id,
            "canonical_category": self.canonical_category,
            "sample_reason": self.sample_reason,
            "processing_status": self.processing_status,
            "file_format": self.file_format,
        }
        if self.chapter_count is not None:
            payload["chapter_count"] = self.chapter_count
        if self.parser_warning_count is not None:
            payload["parser_warning_count"] = self.parser_warning_count
        return payload

    def to_private_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "source_path": str(self.source_path),
            "title_key": self.title_key,
            "canonical_category": self.canonical_category,
            "sample_reason": self.sample_reason,
            "processing_status": self.processing_status,
            "file_format": self.file_format,
            "chapter_count": self.chapter_count,
            "parser_warning_count": self.parser_warning_count,
            "parse_error": self.parse_error,
        }


@dataclass(frozen=True, slots=True)
class TaxonomyBridgeRow:
    canonical_category: str
    display_name: str
    novel_category_key: str | None
    review_profile_key: str | None
    story_design_grammar_key: str | None
    distillation_buckets: tuple[str, ...]
    prompt_pack_keys: tuple[str, ...]
    bridge_status: str
    gaps: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "canonical_category": self.canonical_category,
            "display_name": self.display_name,
            "novel_category_key": self.novel_category_key,
            "review_profile_key": self.review_profile_key,
            "story_design_grammar_key": self.story_design_grammar_key,
            "distillation_buckets": list(self.distillation_buckets),
            "prompt_pack_keys": list(self.prompt_pack_keys),
            "bridge_status": self.bridge_status,
            "gaps": list(self.gaps),
        }


@dataclass(frozen=True, slots=True)
class CapabilityMatrixRow:
    canonical_category: str
    sample_count: int
    overall_support: str
    dimension_support: Mapping[str, str]
    evidence: tuple[str, ...]
    gaps: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "canonical_category": self.canonical_category,
            "sample_count": self.sample_count,
            "overall_support": self.overall_support,
            "dimension_support": dict(self.dimension_support),
            "evidence": list(self.evidence),
            "gaps": list(self.gaps),
        }


@dataclass(frozen=True, slots=True)
class GapRegisterItem:
    gap_id: str
    priority: str
    owner_role: str
    canonical_category: str
    impact: str
    recommended_action: str
    acceptance_criteria: str

    def to_dict(self) -> dict[str, str]:
        return {
            "gap_id": self.gap_id,
            "priority": self.priority,
            "owner_role": self.owner_role,
            "canonical_category": self.canonical_category,
            "impact": self.impact,
            "recommended_action": self.recommended_action,
            "acceptance_criteria": self.acceptance_criteria,
        }


@dataclass(frozen=True, slots=True)
class BenchmarkAuditArtifacts:
    generated_at: str
    repo_sample_set: Mapping[str, Any]
    private_sample_set: Mapping[str, Any]
    taxonomy_bridge: Mapping[str, Any]
    capability_report: Mapping[str, Any]
    markdown_report: str
    privacy_violations: tuple[str, ...] = field(default_factory=tuple)


def _now_iso() -> str:
    return datetime.now(tz=UTC).replace(microsecond=0).isoformat()


def _supported_book_files(root: Path) -> list[Path]:
    if not root.exists():
        return []
    supported = {fmt.lower() for fmt in SUPPORTED_FORMATS}
    files: list[Path] = []
    for path in root.rglob("*"):
        if not path.is_file() or any(part.startswith(".") for part in path.parts):
            continue
        ext = path.suffix.lower().removeprefix(".")
        if ext == "markdown":
            ext = "md"
        if ext in supported:
            files.append(path)
    return sorted(files, key=lambda p: str(p).lower())


def infer_benchmark_category(label: str) -> str:
    """Infer a canonical benchmark category from a filename/title-like label."""
    lowered = label.lower()
    best_category = "action-progression"
    best_score = 0
    for category, keywords in _CATEGORY_KEYWORDS:
        score = sum(1 for keyword in keywords if keyword.lower() in lowered)
        if score > best_score:
            best_category = category
            best_score = score
    return best_category if best_score else "action-progression"


def _selected_with_parse_status(
    path: Path,
    *,
    validate_parse: bool,
) -> tuple[str, int | None, int | None, str | None]:
    if not validate_parse:
        return ("selected_pending_parse_validation", None, None, None)
    try:
        parsed = parse_source_book(path)
    except Exception as exc:  # pragma: no cover - exact parser errors are format-dependent.
        return ("parse_error", None, None, str(exc)[:300])
    warnings = len(parsed.parser_warnings)
    return ("parse_ready", len(parsed.chapters), warnings, None)


def _make_sample(
    *,
    source_id: str,
    path: Path,
    sample_reason: str,
    validate_parse: bool,
) -> BenchmarkSample:
    title_key = normalized_title_group_key(path)
    category = infer_benchmark_category(path.stem)
    status, chapter_count, warning_count, parse_error = _selected_with_parse_status(
        path,
        validate_parse=validate_parse,
    )
    return BenchmarkSample(
        source_id=source_id,
        source_path=path.resolve(),
        title_key=title_key,
        canonical_category=category,
        sample_reason=sample_reason,
        processing_status=status,
        file_format=path.suffix.lower().removeprefix(".") or "unknown",
        chapter_count=chapter_count,
        parser_warning_count=warning_count,
        parse_error=parse_error,
    )


def select_benchmark_samples(
    *,
    corpus_dir: Path,
    high_score_dir: Path | None = None,
    target_count: int = 40,
    seed_limit: int = 23,
    validate_parse: bool = False,
) -> list[BenchmarkSample]:
    """Select a deterministic private benchmark sample set.

    The returned objects contain paths/title keys and must be written only to
    private storage. Use ``to_repo_dict`` for repo-visible artifacts.
    """
    if target_count <= 0:
        return []
    seed_files = _supported_book_files(high_score_dir) if high_score_dir else []
    corpus_files = _supported_book_files(corpus_dir)
    canonical_corpus, _siblings = dedupe_corpus_paths_by_title(corpus_files)

    selected_paths: list[tuple[Path, str]] = []
    used_title_keys: set[str] = set()

    for path in seed_files[:seed_limit]:
        key = normalized_title_group_key(path)
        if key in used_title_keys:
            continue
        selected_paths.append((path, "high_score_seed"))
        used_title_keys.add(key)
        if len(selected_paths) >= target_count:
            break

    candidate_by_category: dict[str, list[Path]] = {
        category: [] for category in TARGET_FILL_CATEGORIES
    }
    fallback_candidates: list[Path] = []
    for path in canonical_corpus:
        key = normalized_title_group_key(path)
        if key in used_title_keys:
            continue
        category = infer_benchmark_category(path.stem)
        if category in candidate_by_category:
            candidate_by_category[category].append(path)
        else:
            fallback_candidates.append(path)

    while len(selected_paths) < target_count:
        added = False
        for category in TARGET_FILL_CATEGORIES:
            if len(selected_paths) >= target_count:
                break
            bucket = candidate_by_category[category]
            while bucket:
                path = bucket.pop(0)
                key = normalized_title_group_key(path)
                if key in used_title_keys:
                    continue
                selected_paths.append((path, f"category_fill:{category}"))
                used_title_keys.add(key)
                added = True
                break
        if not added:
            break

    for path in fallback_candidates:
        if len(selected_paths) >= target_count:
            break
        key = normalized_title_group_key(path)
        if key in used_title_keys:
            continue
        selected_paths.append((path, "category_fill:fallback"))
        used_title_keys.add(key)

    return [
        _make_sample(
            source_id=f"benchmark-source-{index:04d}",
            path=path,
            sample_reason=reason,
            validate_parse=validate_parse,
        )
        for index, (path, reason) in enumerate(selected_paths[:target_count], start=1)
    ]


def build_repo_sample_set(
    samples: Sequence[BenchmarkSample],
    *,
    generated_at: str | None = None,
) -> dict[str, Any]:
    category_counts = Counter(sample.canonical_category for sample in samples)
    return {
        "version": 1,
        "generated_at": generated_at or _now_iso(),
        "privacy_policy": {
            "repo_visible": "anonymous source_id, category, status, format, parse counts only",
            "private_only": "source paths, normalized title keys, parser errors",
        },
        "target_count": 40,
        "actual_count": len(samples),
        "category_counts": dict(sorted(category_counts.items())),
        "samples": [sample.to_repo_dict() for sample in samples],
    }


def build_private_sample_set(
    samples: Sequence[BenchmarkSample],
    *,
    generated_at: str | None = None,
) -> dict[str, Any]:
    return {
        "version": 1,
        "generated_at": generated_at or _now_iso(),
        "warning": "PRIVATE: contains local source paths and title keys; do not commit.",
        "samples": [sample.to_private_dict() for sample in samples],
    }


def build_taxonomy_bridge(categories: Iterable[str] | None = None) -> list[TaxonomyBridgeRow]:
    novel_categories = load_novel_category_registry()
    review_profiles = load_genre_review_profiles()
    grammars = load_story_design_grammar_registry()
    prompt_packs = load_prompt_pack_registry()
    allowed_buckets = set(ALLOWED_DISTILLATION_GENRE_BUCKETS)
    selected_categories = tuple(categories or _CATEGORY_NAMES.keys())

    rows: list[TaxonomyBridgeRow] = []
    for category in selected_categories:
        novel_key = category if category in novel_categories else None
        review_key = category if category in review_profiles else None
        grammar_key = category if category in grammars else None
        bucket_keys = tuple(
            bucket
            for bucket in _DISTILLATION_BUCKETS_BY_CATEGORY.get(category, ())
            if bucket in allowed_buckets
        )
        prompt_keys = tuple(
            key for key in _PROMPT_PACKS_BY_CATEGORY.get(category, ()) if key in prompt_packs
        )
        present_count = sum(
            (
                novel_key is not None,
                review_key is not None,
                grammar_key is not None,
                bool(bucket_keys),
                bool(prompt_keys),
            )
        )
        if novel_key and review_key and grammar_key and (bucket_keys or prompt_keys):
            bridge_status = "aligned"
        elif present_count >= 3:
            bridge_status = "near_aligned"
        elif present_count >= 1:
            bridge_status = "fragmented"
        else:
            bridge_status = "missing"
        gaps = tuple(
            _bridge_gaps(
                category,
                novel_key,
                review_key,
                grammar_key,
                bucket_keys,
                prompt_keys,
            )
        )
        rows.append(
            TaxonomyBridgeRow(
                canonical_category=category,
                display_name=_CATEGORY_NAMES.get(category, category),
                novel_category_key=novel_key,
                review_profile_key=review_key,
                story_design_grammar_key=grammar_key,
                distillation_buckets=bucket_keys,
                prompt_pack_keys=prompt_keys,
                bridge_status=bridge_status,
                gaps=gaps,
            )
        )
    return rows


def _bridge_gaps(
    category: str,
    novel_key: str | None,
    review_key: str | None,
    grammar_key: str | None,
    bucket_keys: Sequence[str],
    prompt_keys: Sequence[str],
) -> list[str]:
    gaps: list[str] = []
    if novel_key is None:
        gaps.append(f"{category} 缺少一等 novel category YAML")
    if review_key is None:
        gaps.append(f"{category} 缺少 genre review profile")
    if grammar_key is None:
        gaps.append(f"{category} 缺少 story design grammar")
    if not bucket_keys:
        gaps.append(f"{category} 缺少对齐的 distillation bucket")
    if not prompt_keys:
        gaps.append(f"{category} 缺少明确 prompt pack 入口")
    return gaps


def build_capability_matrix(
    *,
    samples: Sequence[BenchmarkSample],
    bridge_rows: Sequence[TaxonomyBridgeRow],
) -> list[CapabilityMatrixRow]:
    sample_counts = Counter(sample.canonical_category for sample in samples)
    rows: list[CapabilityMatrixRow] = []
    for bridge in bridge_rows:
        dimensions = _dimension_support_for_bridge(bridge)
        row_gaps = bridge.gaps + _KNOWN_GAPS.get(bridge.canonical_category, ())
        overall = _overall_support(dimensions, gaps=row_gaps)
        evidence = _capability_evidence(bridge)
        rows.append(
            CapabilityMatrixRow(
                canonical_category=bridge.canonical_category,
                sample_count=sample_counts.get(bridge.canonical_category, 0),
                overall_support=overall,
                dimension_support=dimensions,
                evidence=evidence,
                gaps=row_gaps,
            )
        )
    return rows


def _dimension_support_for_bridge(bridge: TaxonomyBridgeRow) -> dict[str, str]:
    dimensions: dict[str, str] = {}
    if bridge.novel_category_key and bridge.review_profile_key and bridge.story_design_grammar_key:
        dimensions["category_coverage"] = "ready"
    elif bridge.novel_category_key or bridge.review_profile_key or bridge.story_design_grammar_key:
        dimensions["category_coverage"] = "partial"
    elif bridge.prompt_pack_keys:
        dimensions["category_coverage"] = "prompt-only"
    else:
        dimensions["category_coverage"] = "unsupported"

    if bridge.story_design_grammar_key and bridge.review_profile_key:
        dimensions["planning_capability"] = "partial"
    elif bridge.story_design_grammar_key or bridge.prompt_pack_keys:
        dimensions["planning_capability"] = "prompt-only"
    else:
        dimensions["planning_capability"] = "unsupported"

    static = _DIMENSION_STATIC_SUPPORT.get(bridge.canonical_category, {})
    for dimension in CAPABILITY_DIMENSIONS:
        dimensions.setdefault(dimension, static.get(dimension, "unsupported"))
    if bridge.canonical_category in load_category_hard_engine_contracts():
        if dimensions.get("state_engine") in {"unsupported", "prompt-only"}:
            dimensions["state_engine"] = "partial"
        if dimensions.get("quality_gates") in {"unsupported", "prompt-only"}:
            dimensions["quality_gates"] = "partial"
    return dimensions


def _overall_support(dimensions: Mapping[str, str], *, gaps: Sequence[str]) -> str:
    values = {"ready": 3, "partial": 2, "prompt-only": 1, "unsupported": 0}
    score = sum(values.get(value, 0) for value in dimensions.values()) / max(len(dimensions), 1)
    if score >= 2.6 and not gaps:
        return "ready"
    if score >= 1.8:
        return "partial"
    if score >= 0.9:
        return "prompt-only"
    return "unsupported"


def _capability_evidence(bridge: TaxonomyBridgeRow) -> tuple[str, ...]:
    evidence = []
    if bridge.novel_category_key:
        evidence.append(f"novel category: {bridge.novel_category_key}")
    if bridge.review_profile_key:
        evidence.append(f"review profile: {bridge.review_profile_key}")
    if bridge.story_design_grammar_key:
        evidence.append(f"story grammar: {bridge.story_design_grammar_key}")
    if bridge.distillation_buckets:
        evidence.append("distillation buckets: " + ", ".join(bridge.distillation_buckets))
    if bridge.prompt_pack_keys:
        evidence.append("prompt packs: " + ", ".join(bridge.prompt_pack_keys[:5]))
    if bridge.canonical_category in load_category_hard_engine_contracts():
        evidence.append(f"category hard engine contract: {bridge.canonical_category}")
    evidence.append("global gates: premium_book_gate, whole_book_quality_gate, scorecard")
    return tuple(evidence)


def build_gap_register(matrix_rows: Sequence[CapabilityMatrixRow]) -> list[GapRegisterItem]:
    items: list[GapRegisterItem] = []
    serial = 1
    for row in matrix_rows:
        if row.overall_support == "ready":
            continue
        priority = _priority_for_support(row.overall_support, row.sample_count)
        for gap in row.gaps[:3]:
            items.append(
                GapRegisterItem(
                    gap_id=f"GAP-{serial:03d}",
                    priority=priority,
                    owner_role=_owner_for_gap(gap),
                    canonical_category=row.canonical_category,
                    impact=_impact_for_support(row.overall_support),
                    recommended_action=gap,
                    acceptance_criteria=_acceptance_for_gap(row.canonical_category, gap),
                )
            )
            serial += 1
    return items


def _priority_for_support(support: str, sample_count: int) -> str:
    if sample_count >= 3 and support in {"unsupported", "prompt-only"}:
        return "P0"
    if sample_count >= 1 and support in {"unsupported", "prompt-only"}:
        return "P1"
    if support == "partial":
        return "P1"
    return "P2"


def _owner_for_gap(gap: str) -> str:
    if "distillation" in gap or "bucket" in gap:
        return "结构蒸馏负责人"
    if "review profile" in gap or "novel category" in gap or "story design grammar" in gap:
        return "类型体系负责人"
    if "状态" in gap or "ledger" in gap or "模型" in gap:
        return "框架架构负责人"
    return "评测与质量负责人"


def _impact_for_support(support: str) -> str:
    if support == "unsupported":
        return "框架无法可靠承接该样本类别，只能作为后续研发方向。"
    if support == "prompt-only":
        return "生成主要依赖提示词，缺少结构化状态和硬门禁，长篇稳定性风险高。"
    return "已有基础能力，但与精品样本的机制稳定度仍有差距。"


def _acceptance_for_gap(category: str, gap: str) -> str:
    if "novel category" in gap:
        return f"`config/novel_categories/{category}.yaml` 存在，并被 resolver / tests 覆盖。"
    if "review profile" in gap:
        return f"`resolve_genre_review_profile` 能解析到 `{category}`，并有类别权重与失败信息测试。"
    if "story design grammar" in gap:
        return f"`resolve_story_design_grammar(category_key='{category}')` 返回专用 grammar。"
    if "distillation bucket" in gap or "bucket" in gap:
        return f"distillation classifier、aggregate key、story grammar 对 `{category}` 对齐。"
    return "新增对应结构化模型、写前 gate、章节后状态更新、好/坏 fixture 测试。"


def build_optimization_roadmap(matrix_rows: Sequence[CapabilityMatrixRow]) -> list[dict[str, Any]]:
    weak_categories = [
        row.canonical_category
        for row in matrix_rows
        if row.overall_support in {"prompt-only", "unsupported"}
    ]
    partial_categories = [
        row.canonical_category for row in matrix_rows if row.overall_support == "partial"
    ]
    return [
        {
            "priority": "P0",
            "theme": "样本与 taxonomy 对齐",
            "actions": [
                "固定 40 本匿名精品样本，并把 repo/private artifact 分离。",
                "建立 canonical taxonomy bridge, "
                "统一 category / review profile / grammar / bucket / prompt pack。",
                "为每个样本类别生成 benchmark rubric 骨架和能力矩阵。",
            ],
            "acceptance": "所有 repo-safe artifact 通过隐私检查；每个样本有 canonical category。",
        },
        {
            "priority": "P1",
            "theme": "补齐类别硬引擎",
            "actions": [
                "把 high-sample 弱项类别升级成一等 category 或明确并入上级 category。",
                "为规则悬疑、异界系统、基建经营、电竞、都市职业、武侠江湖补状态模型和 gates。",
                "把精品样本蒸馏结果转为好/坏 fixture benchmark。",
            ],
            "categories": weak_categories[:8],
            "acceptance": "每个 P1 类别至少有一个 good fixture、一个 bad fixture、一个硬门禁测试。",
        },
        {
            "priority": "P2",
            "theme": "生成闭环与榜单级验证",
            "actions": [
                "跑 30 章 model pilot，对照 capability matrix 和 whole-book gate 输出。",
                "把 sample_quality_parity_gate 设为 ready 结论的硬验收条件。",
                "增加 reference-distance evaluator，验证机制相似但表达不近似。",
                "把 repair loop 结果反哺 rubric 与 strategy weighting。",
            ],
            "categories": partial_categories[:8],
            "acceptance": (
                "每个重点类别都有 30 章 pilot 报告、sample-quality parity passed、"
                "repairable findings 和无隐私泄漏证明。"
            ),
        },
    ]


def build_sample_quality_parity_gate_definition() -> dict[str, Any]:
    thresholds = SampleQualityParityThresholds()
    return {
        "required_for_ready": True,
        "status": "defined_not_run",
        "thresholds": thresholds.to_dict(),
        "required_inputs": [
            "30-chapter completed pilot",
            "project review final_verdict=pass",
            "whole_book_quality_report.passed=True",
            "scorecard.quality_score >= threshold",
            "premium_book_gate category_hard_engine.passed=True",
            "reference_distance_score >= threshold",
            "fallback_count=0",
            "exported project markdown",
        ],
        "claim_rule": (
            "未通过 sample_quality_parity_gate 的类别不得标记为 ready, "
            "只能标记为 partial/prompt-only/unsupported。"
        ),
    }


def build_capability_report(
    *,
    samples: Sequence[BenchmarkSample],
    bridge_rows: Sequence[TaxonomyBridgeRow],
    matrix_rows: Sequence[CapabilityMatrixRow],
    generated_at: str | None = None,
) -> dict[str, Any]:
    gap_register = build_gap_register(matrix_rows)
    benchmark_findings = build_benchmark_findings(samples)
    category_contracts = load_category_hard_engine_contracts()
    return {
        "version": 1,
        "generated_at": generated_at or _now_iso(),
        "team": _team_roles(),
        "sample_summary": {
            "sample_count": len(samples),
            "category_counts": dict(
                sorted(Counter(s.canonical_category for s in samples).items())
            ),
            "processing_status_counts": dict(
                sorted(Counter(s.processing_status for s in samples).items())
            ),
        },
        "support_levels": list(SUPPORT_LEVELS),
        "capability_dimensions": list(CAPABILITY_DIMENSIONS),
        "benchmark_findings": benchmark_findings,
        "category_hard_engine_contracts": [
            contract.to_dict()
            for _, contract in sorted(category_contracts.items())
        ],
        "category_engine_fixture_benchmark": run_category_engine_fixture_benchmark(
            sorted(category_contracts)
        ),
        "sample_quality_parity_gate": build_sample_quality_parity_gate_definition(),
        "taxonomy_bridge": [row.to_dict() for row in bridge_rows],
        "capability_matrix": [row.to_dict() for row in matrix_rows],
        "gap_register": [item.to_dict() for item in gap_register],
        "optimization_roadmap": build_optimization_roadmap(matrix_rows),
    }


def build_benchmark_findings(samples: Sequence[BenchmarkSample]) -> list[dict[str, Any]]:
    sample_counts = Counter(sample.canonical_category for sample in samples)
    findings: list[dict[str, Any]] = []
    for category, count in sorted(sample_counts.items()):
        rubric = dict(_CATEGORY_BENCHMARK_FINDINGS.get(category, {}))
        findings.append(
            {
                "canonical_category": category,
                "sample_count": count,
                "reader_promise": rubric.get("reader_promise", "需要后续结构蒸馏补充。"),
                "core_engine": rubric.get("core_engine", "需要后续结构蒸馏补充。"),
                "state_variables": list(rubric.get("state_variables", [])),
                "reward_cadence": rubric.get("reward_cadence", "需要后续结构蒸馏补充。"),
                "risk_patterns": list(rubric.get("risk_patterns", [])),
                "framework_implication": _framework_implication_for_category(category),
            }
        )
    return findings


def _framework_implication_for_category(category: str) -> str:
    gaps = _KNOWN_GAPS.get(category, ())
    if not gaps:
        return "当前框架可作为基础承接, 后续用样本蒸馏结果校准阈值。"
    return gaps[0]


def _team_roles() -> list[dict[str, str]]:
    return [
        {"role": "CEO / 决策负责人", "responsibility": "批准支持等级、优先级和最终路线图。"},
        {"role": "Chief of Staff / 项目经理", "responsibility": "维护任务清单、阶段验收和风险表。"},
        {"role": "样本与版权负责人", "responsibility": "维护私有样本清单，只暴露匿名 source id。"},
        {"role": "类型体系负责人", "responsibility": "维护 canonical taxonomy bridge。"},
        {
            "role": "结构蒸馏负责人",
            "responsibility": "把样本抽象为机制、状态变量、节奏和风险模式。",
        },
        {"role": "框架架构负责人", "responsibility": "把差距转为框架模块、状态模型和 gates。"},
        {"role": "评测与质量负责人", "responsibility": "维护能力矩阵、rubric 和 pass/fail 标准。"},
        {
            "role": "隐私与反抄袭负责人",
            "responsibility": "确认 repo artifact 不含原文、书名、作者或专名链路。",
        },
        {"role": "QA / 验收负责人", "responsibility": "运行测试、隐私检查和报告验收。"},
    ]


def render_capability_markdown(report: Mapping[str, Any]) -> str:
    sample_summary = _as_mapping(report.get("sample_summary"))
    matrix = [_as_mapping(item) for item in _as_sequence(report.get("capability_matrix"))]
    findings = [_as_mapping(item) for item in _as_sequence(report.get("benchmark_findings"))]
    contracts = [
        _as_mapping(item)
        for item in _as_sequence(report.get("category_hard_engine_contracts"))
    ]
    fixture_rows = [
        _as_mapping(item)
        for item in _as_sequence(report.get("category_engine_fixture_benchmark"))
    ]
    parity_gate = _as_mapping(report.get("sample_quality_parity_gate"))
    gaps = [_as_mapping(item) for item in _as_sequence(report.get("gap_register"))]
    roadmap = [_as_mapping(item) for item in _as_sequence(report.get("optimization_roadmap"))]
    category_counts = json.dumps(sample_summary.get("category_counts", {}), ensure_ascii=False)
    status_counts = json.dumps(
        sample_summary.get("processing_status_counts", {}),
        ensure_ascii=False,
    )
    lines = [
        "# 精品样本对标与框架能力评估报告",
        "",
        f"Generated at: `{report.get('generated_at')}`",
        "",
        "## 样本概览",
        "",
        f"- 匿名样本数: {sample_summary.get('sample_count', 0)}",
        f"- 类别分布: `{category_counts}`",
        f"- 处理状态: `{status_counts}`",
        "",
        "## 团队工作流",
        "",
        "```mermaid",
        "flowchart LR",
        '  Books["40本精品样本"] --> Private["私有解析/章节切分"]',
        '  Private --> Distill["匿名结构蒸馏"]',
        '  Distill --> Rubric["类别Benchmark Rubric"]',
        '  Rubric --> Audit["框架能力审计"]',
        '  Audit --> Matrix["能力矩阵"]',
        '  Matrix --> Roadmap["优化路线图"]',
        "```",
        "",
        "## Benchmark Findings",
        "",
        "| Category | Samples | Reader Promise | Core Engine | Reward Cadence |",
        "| --- | ---: | --- | --- | --- |",
    ]
    for item in findings:
        lines.append(
            "| {category} | {sample_count} | {reader_promise} | {core_engine} | "
            "{reward_cadence} |".format(
                category=item.get("canonical_category", ""),
                sample_count=item.get("sample_count", 0),
                reader_promise=item.get("reader_promise", ""),
                core_engine=item.get("core_engine", ""),
                reward_cadence=item.get("reward_cadence", ""),
            )
        )

    lines.extend(
        [
            "",
            "## Category Hard Engines",
            "",
            "| Category | State Ledgers | Hard Gates | Chapter Updates | Fixture Benchmark |",
            "| --- | --- | --- | --- | --- |",
        ]
    )
    fixture_by_category = {
        str(row.get("category_key", "")): row for row in fixture_rows
    }
    for contract in contracts:
        category = str(contract.get("category_key", ""))
        fixture = fixture_by_category.get(category, {})
        fixture_status = (
            "good-pass / bad-block"
            if fixture.get("good_fixture_passed") is True
            and fixture.get("bad_fixture_blocked") is True
            else "needs-review"
        )
        lines.append(
            "| {category} | {state} | {gates} | {updates} | {fixture_status} |".format(
                category=category,
                state=", ".join(str(item) for item in _as_sequence(contract.get("state_ledger_keys"))),
                gates=", ".join(str(item) for item in _as_sequence(contract.get("hard_gate_keys"))),
                updates=", ".join(str(item) for item in _as_sequence(contract.get("chapter_update_keys"))),
                fixture_status=fixture_status,
            )
        )

    parity_thresholds = _as_mapping(parity_gate.get("thresholds"))
    lines.extend(
        [
            "",
            "## Sample Quality Parity Gate",
            "",
            f"- Required for ready: `{parity_gate.get('required_for_ready', True)}`",
            f"- Status: `{parity_gate.get('status', 'defined_not_run')}`",
            (
                "- Thresholds: "
                f"chapters >= {parity_thresholds.get('min_chapter_count', 30)}, "
                f"review >= {parity_thresholds.get('min_review_overall_score', 0.82)}, "
                f"scorecard >= {parity_thresholds.get('min_scorecard_quality_score', 80.0)}, "
                f"reference-distance >= {parity_thresholds.get('min_reference_distance', 0.72)}"
            ),
            f"- Claim rule: {parity_gate.get('claim_rule', '')}",
        ]
    )

    lines.extend(
        [
            "",
            "## Capability Matrix",
            "",
            "| Category | Samples | Overall | Category | Planning | State | Chapter | "
            "Whole-book | Gates | Repair | Anti-copy |",
            "| --- | ---: | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    for row in matrix:
        dims = _as_mapping(row.get("dimension_support"))
        lines.append(
            "| {category} | {sample_count} | {overall} | {category_coverage} | "
            "{planning_capability} | {state_engine} | {chapter_execution} | "
            "{whole_book_continuity} | {quality_gates} | {repair_loop} | "
            "{anti_copy_safety} |".format(
                category=row.get("canonical_category", ""),
                sample_count=row.get("sample_count", 0),
                overall=row.get("overall_support", ""),
                category_coverage=dims.get("category_coverage", ""),
                planning_capability=dims.get("planning_capability", ""),
                state_engine=dims.get("state_engine", ""),
                chapter_execution=dims.get("chapter_execution", ""),
                whole_book_continuity=dims.get("whole_book_continuity", ""),
                quality_gates=dims.get("quality_gates", ""),
                repair_loop=dims.get("repair_loop", ""),
                anti_copy_safety=dims.get("anti_copy_safety", ""),
            )
        )

    lines.extend(["", "## Gap Register", ""])
    if not gaps:
        lines.append("- No gaps detected.")
    else:
        for gap in gaps[:30]:
            lines.append(
                "- `{gap_id}` `{priority}` `{category}` {action} 验收: {acceptance}".format(
                    gap_id=gap.get("gap_id", ""),
                    priority=gap.get("priority", ""),
                    category=gap.get("canonical_category", ""),
                    action=gap.get("recommended_action", ""),
                    acceptance=gap.get("acceptance_criteria", ""),
                )
            )

    lines.extend(["", "## Optimization Roadmap", ""])
    for item in roadmap:
        actions = "；".join(str(action) for action in _as_sequence(item.get("actions")))
        lines.append(f"- `{item.get('priority')}` {item.get('theme')}: {actions}")

    lines.extend(
        [
            "",
            "## 结论",
            "",
            "当前框架已具备 taxonomy bridge、类别 hard-engine contract 和 good/bad fixture benchmark, "
            "能把多数样本类别推进到 partial。"
            "但只有通过 sample_quality_parity_gate 的 30 章 live pilot, "
            "才能宣称达到精品样本同等稳定度；未通过前不能标记为 ready。",
            "",
        ]
    )
    return "\n".join(lines)


def find_repo_privacy_violations(
    repo_artifact: Mapping[str, Any],
    *,
    forbidden_terms: Sequence[str],
) -> tuple[str, ...]:
    """Return forbidden private strings found in a repo-safe artifact."""
    text = json.dumps(repo_artifact, ensure_ascii=False)
    violations = []
    for term in forbidden_terms:
        clean = str(term).strip()
        if len(clean) >= 2 and clean in text:
            violations.append(clean)
    return tuple(sorted(set(violations)))


def build_benchmark_audit_artifacts(
    *,
    corpus_dir: Path,
    high_score_dir: Path | None = None,
    target_count: int = 40,
    seed_limit: int = 23,
    validate_parse: bool = False,
) -> BenchmarkAuditArtifacts:
    generated_at = _now_iso()
    samples = select_benchmark_samples(
        corpus_dir=corpus_dir,
        high_score_dir=high_score_dir,
        target_count=target_count,
        seed_limit=seed_limit,
        validate_parse=validate_parse,
    )
    bridge_categories = sorted(
        set(_CATEGORY_NAMES)
        | {sample.canonical_category for sample in samples}
        | set(TARGET_FILL_CATEGORIES)
    )
    bridge_rows = build_taxonomy_bridge(bridge_categories)
    matrix_rows = build_capability_matrix(samples=samples, bridge_rows=bridge_rows)
    repo_sample_set = build_repo_sample_set(samples, generated_at=generated_at)
    private_sample_set = build_private_sample_set(samples, generated_at=generated_at)
    taxonomy_bridge = {
        "version": 1,
        "generated_at": generated_at,
        "rows": [row.to_dict() for row in bridge_rows],
    }
    capability_report = build_capability_report(
        samples=samples,
        bridge_rows=bridge_rows,
        matrix_rows=matrix_rows,
        generated_at=generated_at,
    )
    private_terms = [sample.source_path.name for sample in samples]
    repo_bundle = {
        "repo_sample_set": repo_sample_set,
        "taxonomy_bridge": taxonomy_bridge,
        "capability_report": capability_report,
    }
    violations = find_repo_privacy_violations(repo_bundle, forbidden_terms=private_terms)
    return BenchmarkAuditArtifacts(
        generated_at=generated_at,
        repo_sample_set=repo_sample_set,
        private_sample_set=private_sample_set,
        taxonomy_bridge=taxonomy_bridge,
        capability_report=capability_report,
        markdown_report=render_capability_markdown(capability_report),
        privacy_violations=violations,
    )


def write_benchmark_audit_artifacts(
    artifacts: BenchmarkAuditArtifacts,
    *,
    repo_output_dir: Path,
    private_sample_path: Path,
    markdown_report_path: Path,
) -> None:
    repo_output_dir.mkdir(parents=True, exist_ok=True)
    private_sample_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_report_path.parent.mkdir(parents=True, exist_ok=True)

    _write_json(repo_output_dir / "benchmark_sample_set.repo.json", artifacts.repo_sample_set)
    _write_json(repo_output_dir / "taxonomy_bridge.json", artifacts.taxonomy_bridge)
    _write_json(repo_output_dir / "capability_report.json", artifacts.capability_report)
    _write_json(private_sample_path, artifacts.private_sample_set)
    markdown_report_path.write_text(artifacts.markdown_report, encoding="utf-8")


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _as_mapping(value: object) -> dict[str, object]:
    return dict(value) if isinstance(value, Mapping) else {}


def _as_sequence(value: object) -> list[object]:
    if value is None or isinstance(value, str | bytes):
        return []
    if isinstance(value, Sequence):
        return list(value)
    return []


__all__ = [
    "CAPABILITY_DIMENSIONS",
    "SUPPORT_LEVELS",
    "BenchmarkAuditArtifacts",
    "BenchmarkSample",
    "build_benchmark_audit_artifacts",
    "build_benchmark_findings",
    "build_capability_matrix",
    "build_taxonomy_bridge",
    "find_repo_privacy_violations",
    "infer_benchmark_category",
    "render_capability_markdown",
    "select_benchmark_samples",
    "write_benchmark_audit_artifacts",
]
