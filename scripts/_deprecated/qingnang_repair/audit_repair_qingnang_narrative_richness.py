"""Audit and wire Narrative Richness rules for 《青囊不语问阴阳》.

Usage:
    uv run python scripts/audit_repair_qingnang_narrative_richness.py
    uv run python scripts/audit_repair_qingnang_narrative_richness.py --apply
"""

from __future__ import annotations

# ruff: noqa: E501
import argparse
import asyncio
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
import json
from pathlib import Path
import sys
from typing import Any
from uuid import NAMESPACE_DNS, UUID, uuid5

from sqlalchemy import select, update

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SRC = _REPO_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from bestseller.domain.calendar_system import CalendarSystem, Festival  # noqa: E402
from bestseller.domain.crowd_dynamics import CrowdScene  # noqa: E402
from bestseller.domain.cultural_texture import (  # noqa: E402
    CulturalTextureModule,
    MaterialPaletteItem,
)
from bestseller.domain.ensemble_arc import (  # noqa: E402
    EnsembleArcKernel,
    EnsembleCharacterArc,
    IntersectionPoint,
)
from bestseller.domain.ethical_dilemma import EthicalDilemmaKernel, EthicalDilemmaSlot  # noqa: E402
from bestseller.domain.geography import GeographyKernel, Region, RouteEdge  # noqa: E402
from bestseller.domain.honorific_system import HonorificSystem  # noqa: E402
from bestseller.domain.lineage_system import LineageKernel, LineageNode  # noqa: E402
from bestseller.domain.meta_layer import MetaLayerContract  # noqa: E402
from bestseller.domain.mystery_anchor import (  # noqa: E402
    MysteryAnchor,
    MysteryAnchorKernel,
    RevealMilestone,
)
from bestseller.domain.religious_organization import ReligiousOrganization  # noqa: E402
from bestseller.domain.zeitgeist import ZeitgeistContract  # noqa: E402
from bestseller.infra.db.models import (  # noqa: E402
    ChapterDraftVersionModel,
    ChapterModel,
    CharacterModel,
    ProjectModel,
    RewriteTaskModel,
)
from bestseller.infra.db.session import session_scope  # noqa: E402
from bestseller.services.cultural_texture_density_gate import (  # noqa: E402
    scan_cultural_texture_density,
)
from bestseller.services.ensemble_arc_progress_gate import scan_ensemble_arc_progress  # noqa: E402
from bestseller.services.ethical_dilemma_slot_gate import scan_ethical_dilemma_slots  # noqa: E402
from bestseller.services.geography_continuity_gate import scan_geography_continuity  # noqa: E402
from bestseller.services.kernel_composer import (  # noqa: E402
    NarrativeRichnessKernels,
    render_narrative_richness_prompt_block,
)
from bestseller.services.mystery_anchor_reveal_gate import scan_mystery_anchor_reveals  # noqa: E402
from bestseller.settings import load_settings  # noqa: E402

PROJECT_SLUG = "exorcist-detective-1778051012"
REPAIR_SOURCE = "qingnang_narrative_richness_20260523"

REGION_TERMS: dict[str, tuple[str, ...]] = {
    "十七栋镜局": ("十七栋", "303", "302", "305", "二十三层", "困魂镜", "镜局"),
    "城西现实调查线": ("太平间", "城西分局", "苏警官", "苏婉宁", "证物科", "尸检"),
    "城北旧货市场": ("旧货市场", "A区127", "城北旧货市场", "快递单"),
    "林家老宅井口": ("老宅", "井口", "林家祖坟", "衣冠冢", "半卷青囊"),
    "三族民俗腹地": ("三族", "张家", "钱家", "出马", "古镇", "狐仙令"),
}

CONSEQUENCE_ECHO_TERMS = (
    "代价",
    "后果",
    "入账",
    "反噬",
    "失去",
    "欠",
    "债",
    "救",
    "死",
    "牺牲",
)

KERNEL_FILE_MAP = {
    "geography-kernel.json": "geography_kernel",
    "cultural-texture-module.json": "cultural_texture_module",
    "mystery-anchor-kernel.json": "mystery_anchor_kernel",
    "ethical-dilemma-kernel.json": "ethical_dilemma_kernel",
    "ensemble-arc-kernel.json": "ensemble_arc_kernel",
    "lineage-kernel.json": "lineage_kernel",
    "crowd-scene.json": "crowd_scene",
    "calendar-system.json": "calendar_module",
    "honorific-system.json": "honorific_system_module",
    "religious-organization.json": "religious_organization_module",
    "zeitgeist-contract.json": "zeitgeist_contract",
    "meta-layer-contract.json": "meta_layer_contract",
}


@dataclass
class ChapterAudit:
    chapter: int
    title: str
    regions: list[str] = field(default_factory=list)
    cultural_findings: list[dict[str, Any]] = field(default_factory=list)
    geography_findings: list[dict[str, Any]] = field(default_factory=list)
    landed_palette_items: list[str] = field(default_factory=list)
    landed_dilemma: bool = False
    repair_reasons: list[str] = field(default_factory=list)


def _dump(model: Any) -> dict[str, Any]:
    return model.model_dump(mode="json")


def materialize_qingnang_kernel_files(story_bible_dir: Path, context_payload: dict[str, Any]) -> dict[str, str]:
    kernels_dir = story_bible_dir / "kernels"
    kernels_dir.mkdir(parents=True, exist_ok=True)
    written: dict[str, str] = {}
    for filename, key in KERNEL_FILE_MAP.items():
        payload = context_payload.get(key)
        if payload is None:
            continue
        path = kernels_dir / filename
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        written[filename] = str(path)
    return written


def classify_narrative_richness_report(
    *,
    applied: bool,
    repair_target_count: int,
    materialized_kernel_count: int,
) -> dict[str, Any]:
    """Return gate-style status so a dry-run zero-finding report is not treated as green."""

    if repair_target_count:
        return {
            "verdict": "blocked",
            "coverage": 0.9 if applied else 0.65,
            "passed": False,
            "status_reason": "narrative richness findings still require chapter repair",
        }
    if not applied:
        return {
            "verdict": "warn_only",
            "coverage": 0.5,
            "passed": False,
            "status_reason": "audit ran without --apply; kernels and prompt inputs were not persisted",
        }
    if materialized_kernel_count <= 0:
        return {
            "verdict": "blocked",
            "coverage": 0.8,
            "passed": False,
            "status_reason": "audit applied but no story-bible kernel files were materialized",
        }
    return {
        "verdict": "pass",
        "coverage": 1.0,
        "passed": True,
        "status_reason": "narrative richness kernels are persisted and no chapter repair targets remain",
    }


def _stable_uuid(label: str) -> UUID:
    return uuid5(NAMESPACE_DNS, f"bestseller.qingnang.{label}")


def _character_id(characters: dict[str, UUID], name: str) -> UUID:
    return characters.get(name) or _stable_uuid(name)


def _build_geography() -> GeographyKernel:
    return GeographyKernel(
        regions=[
            Region(
                name="十七栋镜局",
                climate="潮冷封闭",
                terrain="旧楼镜面空间",
                demographics="租客、看房人、物业与三族旧账残影混杂",
                dominant_faction="困魂镜",
                surface_economy=["凶宅转手", "旧楼物业", "镜债回执"],
                cultural_signature="门牌、镜面、潮气和血字共同构成第一卷核心空间。",
            ),
            Region(
                name="城西现实调查线",
                climate="城市冷光",
                terrain="分局、太平间、证物库与医院走廊",
                demographics="警方、法医、受害者家属和现实证据链",
                dominant_faction="现实调查方",
                surface_economy=["证物封存", "尸检报告", "监控档案"],
                cultural_signature="现实制度压力把灵异事件压成可举证的嫌疑。",
            ),
            Region(
                name="城北旧货市场",
                climate="灰尘与霉味",
                terrain="旧货铺、快递点、镜框和民俗器物流转地",
                demographics="旧货商、倒卖人、张家开门线索人",
                dominant_faction="张家开门人",
                surface_economy=["旧镜", "铜器", "快递流转"],
                cultural_signature="器物来路比人证更可靠，所有旧物都可能带账。",
            ),
            Region(
                name="林家老宅井口",
                climate="阴湿祖宅",
                terrain="井口、祠堂、衣冠冢与青囊残页",
                demographics="林家旧人、镜影和执卷权争夺者",
                dominant_faction="林家记账人",
                surface_economy=["青囊残页", "林家印", "血沁铜钱"],
                cultural_signature="血脉、真名和执卷权在这里被重新核账。",
            ),
            Region(
                name="三族民俗腹地",
                climate="香火与旧契并存",
                terrain="古镇、祖坟、堂口和三族契约遗址",
                demographics="林家、张家、钱家、出马仙家及契约旁支",
                dominant_faction="三族旧契",
                surface_economy=["香火", "契书", "镇族器物"],
                cultural_signature="民俗组织不是背景板，而是每条债路的制度来源。",
            ),
        ],
        routes=[
            RouteEdge(region_a="十七栋镜局", region_b="城西现实调查线", kind="官道", days_typical=1, hazard_level=3, hazard_kinds=["伪证", "警方盘查", "回执污染"]),
            RouteEdge(region_a="十七栋镜局", region_b="城北旧货市场", kind="官道", days_typical=1, hazard_level=2, hazard_kinds=["旧镜流转", "张家线人"]),
            RouteEdge(region_a="城西现实调查线", region_b="城北旧货市场", kind="官道", days_typical=1, hazard_level=2, hazard_kinds=["证物追踪", "快递单核验"]),
            RouteEdge(region_a="城北旧货市场", region_b="林家老宅井口", kind="秘径", days_typical=1, hazard_level=4, hazard_kinds=["镜影抢先认账", "半卷青囊反噬"]),
            RouteEdge(region_a="林家老宅井口", region_b="三族民俗腹地", kind="山道", days_typical=2, hazard_level=4, hazard_kinds=["旧契反噬", "香火断绝"]),
            RouteEdge(region_a="城西现实调查线", region_b="三族民俗腹地", kind="官道", days_typical=2, hazard_level=3, hazard_kinds=["证据失真", "组织施压"]),
            RouteEdge(region_a="十七栋镜局", region_b="三族民俗腹地", kind="秘径", days_typical=1, hazard_level=4, hazard_kinds=["旧契显形", "张钱林三姓互证"]),
            RouteEdge(region_a="十七栋镜局", region_b="林家老宅井口", kind="秘径", days_typical=1, hazard_level=5, hazard_kinds=["镜影抢路", "父亲半卷青囊牵引"]),
            RouteEdge(region_a="城西现实调查线", region_b="林家老宅井口", kind="官道", days_typical=1, hazard_level=3, hazard_kinds=["伪证追踪", "身份核验"]),
            RouteEdge(region_a="城北旧货市场", region_b="三族民俗腹地", kind="官道", days_typical=1, hazard_level=3, hazard_kinds=["旧物牵出契约源头"]),
        ],
        capital_region="城西现实调查线",
        protagonist_origin="林家老宅井口",
        protagonist_current="十七栋镜局",
    )


def _build_culture() -> CulturalTextureModule:
    return CulturalTextureModule(
        palette=[
            MaterialPaletteItem(category="tool", name="康熙铜钱", sensory_hook="铜钱", class_signal="林家记账工具"),
            MaterialPaletteItem(category="tool", name="青囊秘卷", sensory_hook="青囊", class_signal="执卷人凭证"),
            MaterialPaletteItem(category="tool", name="风水罗盘", sensory_hook="罗盘", class_signal="方位核账工具"),
            MaterialPaletteItem(category="ornament", name="回执镜片", sensory_hook="回执", class_signal="离局凭证"),
            MaterialPaletteItem(category="ornament", name="黄铜镜框", sensory_hook="黄铜", class_signal="旧镜入口"),
            MaterialPaletteItem(category="food", name="腐甜潮气", sensory_hook="腐香", class_signal="凶宅气味"),
            MaterialPaletteItem(category="clothing", name="灰色工装", sensory_hook="灰色工装", class_signal="物业与旧楼劳动者"),
            MaterialPaletteItem(category="music", name="三短一长敲击", sensory_hook="三短一长", class_signal="镜债信号"),
            MaterialPaletteItem(category="vehicle", name="旧电梯", sensory_hook="电梯", class_signal="楼层边界"),
            MaterialPaletteItem(category="tool", name="油灯木牌", sensory_hook="油灯", class_signal="钱家守镜仪式"),
        ],
        daily_rituals=[
            "铜钱定方位",
            "青囊显字核账",
            "咬破指尖以血代印",
            "进门先看门槛与镜面",
            "遇反光物先遮后验",
        ],
        taboo_behaviors=[
            "空口认账",
            "直视来路不明的镜面",
            "未经核验触碰回执",
            "把青囊当万能法术书",
        ],
        aesthetic_zeitgeist="民俗悬疑的质感来自旧楼潮气、铜器发热、纸页显字、现实证据和阴债规则同场冲突。",
        applicable_categories=["民俗悬疑", "惊悚灵异", "驱魔探案综合", "suspense-mystery"],
    )


def _build_worldview_modules() -> dict[str, Any]:
    calendar = CalendarSystem(
        calendar_type="mixed",
        major_festivals=[
            Festival(name="中元节", season="秋", activities=["祭祖", "封镜", "核旧账"], symbolism="阴阳交汇，三十年布局激活。", plot_hooks=["三十年布局触发条件", "三族旧契重启"]),
            Festival(name="子时", season="夜", activities=["入门", "显字", "认账"], symbolism="镜局最容易开门的时间点。", plot_hooks=["十五分钟凶宅", "子时镜局"]),
            Festival(name="三月十七", season="春", activities=["翻旧账", "查账页编号"], symbolism="林家第一笔镜债坐标与账页编号重合。", plot_hooks=["0317账页编号"]),
        ],
        seasonal_phases=["梅雨潮湿", "中元前夜", "旧城拆迁期"],
        forbidden_dates=["中元节前不得空口认账", "子时不得独自照镜"],
        applicable_categories=["民俗悬疑", "惊悚灵异"],
    )
    honorific = HonorificSystem(
        superior_to_inferior={"elder->junior": "孩子", "mentor->protagonist": "林家的孩子"},
        inferior_to_superior={"protagonist->elder": "婆婆", "junior->master": "师父"},
        peer_address={"ally->ally": "你", "police->protagonist": "林渊"},
        kinship_terms={"junior->father": "父亲", "junior->grandfather": "爷爷"},
        civil_to_military={"civil->police": "苏警官", "protagonist->police": "苏警官"},
        monastic_or_religious={"lay->keeper": "守镜人", "junior->lineage": "林家记账人"},
        forbidden_addresses=["大师您老人家", "万能天师", "青囊外挂"],
        applicable_categories=["民俗悬疑", "惊悚灵异"],
    )
    religion = ReligiousOrganization(
        name="三族旧契",
        deities=["林家青囊", "张家门契", "钱家守镜钱", "出马仙香火"],
        core_doctrine="债不可消，只能核、改、转、暂封；认账必须有因果和代价。",
        ritual_calendar=["中元节", "子时", "三月十七"],
        hierarchy=["执卷人", "开门人", "守镜人", "出马弟子", "欠账人"],
        sacred_sites=["十七栋镜局", "林家老宅井口", "三族民俗腹地"],
        conflict_with=["困魂镜", "镜影林渊", "现实调查方"],
        schism_history="林家封镜后，张家开门、钱家守镜与出马仙香火各自隐瞒代价，导致三族旧契被镜债反向利用。",
        applicable_categories=["民俗悬疑", "惊悚灵异"],
    )
    return {
        "calendar_module": _dump(calendar),
        "honorific_system_module": _dump(honorific),
        "religious_organization_module": _dump(religion),
    }


def _build_mystery() -> MysteryAnchorKernel:
    return MysteryAnchorKernel(
        anchors=[
            MysteryAnchor(
                question="困魂镜第一笔镜债究竟是谁欠下的？",
                stake_if_solved="真相会改写林家、张家、钱家三族责任，决定十七栋能否暂封。",
                reveal_milestones=[
                    RevealMilestone(volume=1, fraction_revealed=0.35, reveal_kind="partial_truth", description="十七栋、否认者先入账、回执外溢和三族旧契露出。"),
                    RevealMilestone(volume=2, fraction_revealed=0.55, reveal_kind="partial_truth", description="林家老宅井口与父亲半卷青囊证明第一笔债并非单一凶宅。"),
                    RevealMilestone(volume=7, fraction_revealed=0.85, reveal_kind="partial_truth", description="三族契约完整版曝光。"),
                    RevealMilestone(volume=10, fraction_revealed=1.0, reveal_kind="full_reveal", description="青囊账本背后的真正记账人浮出。"),
                ],
                false_lead_plan=["王建业", "张建军", "镜影林渊"],
                final_payoff_chapter_range=(451, 500),
            ),
            MysteryAnchor(
                question="林正淳为什么失踪，半卷青囊落在谁手里？",
                stake_if_solved="林渊执卷权与父亲生死状态会被重新定义。",
                reveal_milestones=[
                    RevealMilestone(volume=1, fraction_revealed=0.2, reveal_kind="hint", description="父亲落款、旧账名和镜中留言散落在十七栋。"),
                    RevealMilestone(volume=2, fraction_revealed=0.65, reveal_kind="partial_truth", description="林家老宅井口抢回父亲半卷青囊使用权。"),
                    RevealMilestone(volume=6, fraction_revealed=0.8, reveal_kind="partial_truth", description="七日灯照出父亲在镜中的留言。"),
                    RevealMilestone(volume=10, fraction_revealed=1.0, reveal_kind="full_reveal", description="父亲抵债入门的完整因果被核清。"),
                ],
                false_lead_plan=["父亲失踪", "镜影林渊"],
                final_payoff_chapter_range=(430, 500),
            ),
        ],
        inter_anchor_dependencies={
            "林正淳为什么失踪，半卷青囊落在谁手里？": ["困魂镜第一笔镜债究竟是谁欠下的？"]
        },
        applicable_categories=["民俗悬疑", "惊悚灵异", "suspense-mystery"],
    )


def _build_ethical(characters: dict[str, UUID]) -> EthicalDilemmaKernel:
    protagonist = _character_id(characters, "林渊")
    su = _character_id(characters, "苏婉宁")
    chen = _character_id(characters, "陈默")
    qian = _character_id(characters, "钱婆婆")
    return EthicalDilemmaKernel(
        slots=[
            EthicalDilemmaSlot(chapter_window=(1, 12), dilemma_kind="law_vs_compassion", competing_values=("救人", "认账代价"), involved_characters=[protagonist, chen], intended_choice="open", consequence_for_unchosen="被救者可能活下去，但林渊会承接更多镜债。"),
            EthicalDilemmaSlot(chapter_window=(13, 24), dilemma_kind="loyalty_vs_truth", competing_values=("林家旧账", "现实证据"), involved_characters=[protagonist, su], intended_choice="open", consequence_for_unchosen="隐瞒会保护民俗线，公开会让警方与镜债同时逼近。"),
            EthicalDilemmaSlot(chapter_window=(25, 36), dilemma_kind="belief_vs_kin", competing_values=("父亲真相", "无辜者安全"), involved_characters=[protagonist], intended_choice="open", consequence_for_unchosen="追父亲线会放大十七栋风险，救人会错失半卷青囊线索。"),
            EthicalDilemmaSlot(chapter_window=(37, 48), dilemma_kind="one_vs_many", competing_values=("单人归来", "半数归人"), involved_characters=[protagonist, qian], intended_choice="open", consequence_for_unchosen="保一个人会牺牲半门机会，保半数会让未归者入账更深。"),
            EthicalDilemmaSlot(chapter_window=(49, 60), dilemma_kind="self_vs_collective", competing_values=("执卷人身份", "众人脱困"), involved_characters=[protagonist, su], intended_choice="open", consequence_for_unchosen="证明自己会拖慢救援，放弃身份会让镜影抢先认账。"),
            EthicalDilemmaSlot(chapter_window=(61, 72), dilemma_kind="short_term_vs_long", competing_values=("眼前通关", "长期代价"), involved_characters=[protagonist], intended_choice="open", consequence_for_unchosen="短期破局会留下更大镜债，长期布局会牺牲当前安全。"),
        ],
        minimum_cadence_chapters=12,
        applicable_categories=["民俗悬疑", "惊悚灵异", "suspense-mystery"],
    )


def _build_ensemble(characters: dict[str, UUID]) -> EnsembleArcKernel:
    return EnsembleArcKernel(
        arcs=[
            EnsembleCharacterArc(
                owner_id=_character_id(characters, "苏婉宁"),
                arc_kind="transformation",
                private_goal="用现实证据解释十七栋异常，保护调查边界。",
                private_obstacle="证据不断被回执污染，她必须决定是否与林渊共犯式合作。",
                private_payoff="现实证据链首次压过镜债伪证。",
                pov_chapters=[6, 8, 9, 12, 53, 64],
                intersect_main=[
                    IntersectionPoint(chapter=6, effect_on_mainline="现实调查压力压入十七栋。"),
                    IntersectionPoint(chapter=12, effect_on_mainline="质疑林渊是否亲口认账。"),
                    IntersectionPoint(chapter=64, effect_on_mainline="身份线与镜影伪证再度相撞。"),
                ],
                standalone_value="一名警察从怀疑者变成愿意承担制度代价的证人。",
                final_state="成为现实证据线与民俗规则线的桥。",
            ),
            EnsembleCharacterArc(
                owner_id=_character_id(characters, "陈默"),
                arc_kind="redemption",
                private_goal="救小雨并弄清父亲旧账。",
                private_obstacle="他的否认和替认不断把自己推回镜眼。",
                private_payoff="承认父辈旧账后，为小雨和十七栋归人争取半门机会。",
                pov_chapters=[4, 7, 10, 11, 13, 16],
                intersect_main=[
                    IntersectionPoint(chapter=4, effect_on_mainline="小雨镜中危机暴露陈默旧账。"),
                    IntersectionPoint(chapter=10, effect_on_mainline="陈默否认与替认规则被核清。"),
                ],
                standalone_value="普通受害者在认账与救人之间完成自我承担。",
                final_state="第一卷后成为镜眼规则的重要活证。",
            ),
            EnsembleCharacterArc(
                owner_id=_character_id(characters, "钱婆婆"),
                arc_kind="stoic",
                private_goal="守住钱家守镜代价，不让回执外溢扩大。",
                private_obstacle="钱家寿数和三姓钱改账路都要求她牺牲。",
                private_payoff="交出守镜完整代价，帮助林渊理解改账路不是消债。",
                pov_chapters=[6, 18, 37, 49, 50, 55],
                intersect_main=[
                    IntersectionPoint(chapter=6, effect_on_mainline="三族守镜规矩第一次明确。"),
                    IntersectionPoint(chapter=49, effect_on_mainline="暂封主镜门前交出关键代价。"),
                ],
                standalone_value="守镜老人用一生证明规则不是正义，只是代价秩序。",
                final_state="以死后回执继续牵引钱家线。",
            ),
        ],
        coverage_target=0.10,
        applicable_categories=["民俗悬疑", "惊悚灵异"],
    )


def _build_p2(characters: dict[str, UUID]) -> dict[str, Any]:
    lineage = LineageKernel(
        schools={
            "南茅林家": [
                LineageNode(person_id=_character_id(characters, "林渊"), school="南茅林家", generation=4, role="disciple", parent_master=_character_id(characters, "林正淳"), school_rule_violations=[]),
                LineageNode(person_id=_character_id(characters, "林正淳"), school="南茅林家", generation=3, role="master", parent_master=None, school_rule_violations=["抵债入门"]),
            ],
            "钱家守镜": [
                LineageNode(person_id=_character_id(characters, "钱婆婆"), school="钱家守镜", generation=3, role="elder", parent_master=None, school_rule_violations=[]),
            ],
        },
        inter_school_treaties=["三族旧契：林家记账、张家开门、钱家守镜"],
        school_rules={"南茅林家": ["青囊只记因果，不替人赎罪"], "钱家守镜": ["三姓钱改账路，不消债"]},
    )
    crowd = CrowdScene(
        crowd_size_class="medium",
        initial_mood="惊疑",
        triggering_event="十七栋镜债外溢到现实证据链",
        mood_arc=["围观", "怀疑林渊", "恐慌", "被证据反转"],
        rumor_seed="林渊监控分身杀人",
        factional_split=["警方", "租客", "旧楼住户", "三族旁支"],
        resolution="leader_emerges",
    )
    zeitgeist = ZeitgeistContract(
        label="旧城拆迁期的债务焦虑",
        core_anxiety="旧楼、旧契和旧物被城市更新掀开后，没人愿意承认自己继承了前人的债。",
        dominant_aspiration="用可验证证据和可承担代价重建秩序。",
        aesthetic_pressure="每个灵异画面都必须落到旧楼、铜器、纸页、镜面、证物或身体代价上。",
        social_mobility_rule="能往上走的人不是无债者，而是愿意把旧账核清并承担代价的人。",
        volume_injections={1: "十七栋是小世界，所有旧账先压在一栋楼里。", 2: "老宅井口把私人身世扩成家族债。"},
        applicable_categories=["民俗悬疑", "惊悚灵异"],
    )
    meta = MetaLayerContract(
        layer_type="volume_epigraph",
        placement="每卷开头或案后短回执。",
        narrative_function="用账页格式收束已核清因果，同时留下下一笔未核债。",
        voice_rule="只能像青囊账页、案后回执或页边批注，不得像作者解释。",
        spoiler_boundary="不得剧透最终债主、林正淳完整真相或青囊背后的记账人。",
        payoff_targets=["困魂镜第一笔镜债", "林正淳半卷青囊", "三族旧契"],
    )
    return {
        "lineage_kernel": _dump(lineage),
        "crowd_scene": _dump(crowd),
        "zeitgeist_contract": _dump(zeitgeist),
        "meta_layer_contract": _dump(meta),
    }


def _build_context(characters: dict[str, UUID]) -> tuple[NarrativeRichnessKernels, dict[str, Any]]:
    worldview_modules = _build_worldview_modules()
    payload = {
        "geography_kernel": _dump(_build_geography()),
        "cultural_texture_module": _dump(_build_culture()),
        "ensemble_arc_kernel": _dump(_build_ensemble(characters)),
        "mystery_anchor_kernel": _dump(_build_mystery()),
        "ethical_dilemma_kernel": _dump(_build_ethical(characters)),
        **_build_p2(characters),
    }
    return NarrativeRichnessKernels.model_validate(payload), worldview_modules


def _infer_regions(text: str) -> list[str]:
    hits: list[tuple[int, str]] = []
    for region, terms in REGION_TERMS.items():
        positions = [text.find(term) for term in terms if term in text]
        if positions:
            hits.append((min(pos for pos in positions if pos >= 0), region))
    return [region for _, region in sorted(hits)]


def _dilemma_landed(text: str, slot: EthicalDilemmaSlot) -> bool:
    a, b = slot.competing_values
    if a in text and b in text:
        return True
    if slot.dilemma_kind == "law_vs_compassion":
        return "救" in text and ("认账" in text or "代价" in text or "镜债" in text)
    if slot.dilemma_kind == "loyalty_vs_truth":
        return ("林家" in text or "父亲" in text) and ("证据" in text or "苏婉宁" in text)
    if slot.dilemma_kind == "belief_vs_kin":
        return "父亲" in text and ("救" in text or "小雨" in text or "陈默" in text)
    if slot.dilemma_kind == "one_vs_many":
        return ("半数" in text or "众人" in text or "归人" in text) and ("救" in text or "代价" in text)
    if slot.dilemma_kind == "self_vs_collective":
        return ("执卷" in text or "身份" in text) and ("众人" in text or "脱困" in text or "救" in text)
    if slot.dilemma_kind == "short_term_vs_long":
        return ("通关" in text or "眼前" in text) and ("长期" in text or "代价" in text or "三族" in text)
    return False


def _find_followup_consequence_echo(chapter_no: int, chapter_texts: dict[int, str], *, lookahead: int = 3) -> list[str]:
    echoes: list[str] = []
    for follow_chapter in range(chapter_no + 1, chapter_no + lookahead + 1):
        text = chapter_texts.get(follow_chapter, "")
        hits = [term for term in CONSEQUENCE_ECHO_TERMS if term in text]
        if hits:
            echoes.append(f"ch{follow_chapter} echoes consequence terms: {', '.join(hits[:4])}")
    return echoes


def _chapter_for_window(rows: list[tuple[ChapterModel, ChapterDraftVersionModel]], window: list[int]) -> ChapterModel | None:
    start, end = window
    candidates = [chapter for chapter, _ in rows if start <= int(chapter.chapter_number) <= end]
    return candidates[-1] if candidates else None


def _task_instruction(chapter: ChapterModel, reasons: list[str], context_preview: str) -> str:
    reason_text = "\n".join(f"- {reason}" for reason in reasons)
    return f"""【Narrative Richness 修复任务｜第{chapter.chapter_number}章《{chapter.title or ''}》】
修复目标：不改本章核心事件和既有因果，只补足《青囊不语问阴阳》的世界肌理、民俗材质、地理/证据链连续性、长线悬念或伦理两难落点。

当前命中的缺口：
{reason_text}

必须执行：
1. 至少落地一个本书专属材质锚：青囊、铜钱、罗盘、回执、黄铜镜框、旧电梯、油灯、三短一长敲击等；必须是可见动作、触感、气味或声音，不是设定说明。
2. 如果本章发生空间转移，明确从“十七栋镜局 / 城西现实调查线 / 城北旧货市场 / 林家老宅井口 / 三族民俗腹地”中的哪里到哪里，补一句路由或证据来源。
3. 如果本章处在伦理 slot 内，必须让两种价值都可辩护：救人 vs 认账代价、林家旧账 vs 现实证据、父亲真相 vs 无辜者安全等，并写出未选择一方的后果。
4. 悬念推进必须服务两个长线锚：困魂镜第一笔镜债、林正淳半卷青囊；禁止另开无关怪谈。
5. 修复方式应是局部改写或补 1-3 段，不要整章换案、换人、换主线。

当前可注入的规则摘要：
{context_preview}
"""


async def _load(session: Any) -> tuple[ProjectModel, list[tuple[ChapterModel, ChapterDraftVersionModel]], dict[str, UUID]]:
    project = await session.scalar(select(ProjectModel).where(ProjectModel.slug == PROJECT_SLUG))
    if project is None:
        raise RuntimeError(f"Project {PROJECT_SLUG!r} not found")
    rows = (
        await session.execute(
            select(ChapterModel, ChapterDraftVersionModel)
            .join(ChapterDraftVersionModel, ChapterDraftVersionModel.chapter_id == ChapterModel.id)
            .where(
                ChapterModel.project_id == project.id,
                ChapterDraftVersionModel.is_current.is_(True),
            )
            .order_by(ChapterModel.chapter_number)
        )
    ).all()
    characters = {
        name: cid
        for cid, name in (
            await session.execute(
                select(CharacterModel.id, CharacterModel.name).where(CharacterModel.project_id == project.id)
            )
        ).all()
    }
    return project, rows, characters


async def run(*, apply: bool, replace_existing: bool) -> dict[str, Any]:
    settings = load_settings()
    async with session_scope(settings) as session:
        project, rows, characters = await _load(session)
        context, worldview_modules = _build_context(characters)
        context_payload = _dump(context)
        culture = context.cultural_texture_module
        geography = context.geography_kernel
        ethical = context.ethical_dilemma_kernel
        mystery = context.mystery_anchor_kernel
        ensemble = context.ensemble_arc_kernel
        if culture is None or geography is None or ethical is None or mystery is None or ensemble is None:
            raise RuntimeError("Narrative richness context failed to build")
        materialized_kernel_files: dict[str, str] = {}

        chapter_audits: list[ChapterAudit] = []
        landed_dilemmas: list[int] = []
        consequence_echoes: dict[int, list[str]] = {}
        chapter_texts: dict[int, str] = {}
        revealed_ledger: list[str] = []

        for chapter, draft in rows:
            chapter_no = int(chapter.chapter_number)
            text = draft.content_md or ""
            chapter_texts[chapter_no] = text
            regions = _infer_regions(text)
            geo_report = scan_geography_continuity(geography, chapter_regions=regions, chapter_no=chapter_no)
            cultural_report = scan_cultural_texture_density(
                culture,
                chapter_text=text,
                chapter_no=chapter_no,
                category="民俗悬疑",
            )
            audit = ChapterAudit(
                chapter=chapter_no,
                title=chapter.title or "",
                regions=regions,
                cultural_findings=[asdict(f) for f in cultural_report.findings],
                geography_findings=[asdict(f) for f in geo_report.findings],
                landed_palette_items=list(cultural_report.landed_palette_items),
            )
            for slot in ethical.slots:
                if slot.chapter_window[0] <= chapter_no <= slot.chapter_window[1] and _dilemma_landed(text, slot):
                    audit.landed_dilemma = True
                    landed_dilemmas.append(chapter_no)
                    if any(term in text for term in ("代价", "后果", "入账", "反噬", "失去")):
                        consequence_echoes[chapter_no] = ["chapter text contains explicit consequence language"]
            if not audit.landed_palette_items:
                audit.repair_reasons.append("missing_palette_landing")
            if not regions:
                audit.repair_reasons.append("missing_region_context")
            audit.repair_reasons.extend(f"geography:{f.code}" for f in geo_report.findings)
            audit.repair_reasons.extend(f"culture:{f.code}" for f in cultural_report.findings if f.severity == "critical")
            chapter_audits.append(audit)
            revealed_ledger.append(f"ch{chapter_no} {chapter.title or ''}: {text[:1000]}")

        for chapter_no in landed_dilemmas:
            if not consequence_echoes.get(chapter_no):
                echoes = _find_followup_consequence_echo(chapter_no, chapter_texts)
                if echoes:
                    consequence_echoes[chapter_no] = echoes

        ethical_report = scan_ethical_dilemma_slots(
            ethical,
            total_chapters=max(int(chapter.chapter_number) for chapter, _ in rows),
            landed_chapters=sorted(set(landed_dilemmas)),
            consequence_echoes=consequence_echoes,
        )
        mystery_reports = {
            str(volume): [asdict(f) for f in scan_mystery_anchor_reveals(mystery, volume=volume, revealed_ledger=revealed_ledger).findings]
            for volume in (1, 2)
        }
        ensemble_report = scan_ensemble_arc_progress(
            ensemble,
            total_chapters=int(project.target_chapters or max(int(chapter.chapter_number) for chapter, _ in rows)),
            category="民俗悬疑",
        )
        prompt_preview = render_narrative_richness_prompt_block(context, chapter_no=max(int(chapter.chapter_number) for chapter, _ in rows) + 1)
        prompt_preview = prompt_preview[:1800]

        created_tasks: list[str] = []
        repair_targets: dict[int, list[str]] = {}
        for audit in chapter_audits:
            if audit.repair_reasons:
                repair_targets[audit.chapter] = list(dict.fromkeys(audit.repair_reasons))
        for finding in ethical_report.findings:
            window = finding.payload.get("window")
            if isinstance(window, list) and len(window) == 2:
                target = _chapter_for_window(rows, [int(window[0]), int(window[1])])
                if target is not None:
                    repair_targets.setdefault(int(target.chapter_number), []).append(f"ethical:{finding.code}:{window[0]}-{window[1]}")
        for volume, findings in mystery_reports.items():
            for finding in findings:
                if finding.get("severity") == "critical":
                    target_no = 50 if volume == "1" else min(71, max(int(chapter.chapter_number) for chapter, _ in rows))
                    repair_targets.setdefault(target_no, []).append(f"mystery:{finding.get('code')}:volume-{volume}")

        if apply:
            metadata = dict(project.metadata_json or {})
            metadata["narrative_richness_kernels"] = context_payload
            metadata["narrative_richness_context"] = context_payload
            metadata["narrative_richness_audit"] = {
                "source": REPAIR_SOURCE,
                "applied_at": datetime.now(UTC).isoformat(),
                "chapter_count": len(rows),
                "repair_target_count": len(repair_targets),
            }
            story_design = dict(metadata.get("story_design_kernel") or {})
            worldview = dict(story_design.get("worldview_kernel") or {})
            worldview["cultural_texture_module"] = context_payload["cultural_texture_module"]
            worldview.update(worldview_modules)
            story_design["worldview_kernel"] = worldview
            metadata["story_design_kernel"] = story_design
            project.metadata_json = metadata
            materialized_kernel_files = materialize_qingnang_kernel_files(
                Path(settings.output.base_dir) / PROJECT_SLUG / "story-bible",
                {**context_payload, **worldview_modules},
            )

            if replace_existing:
                await session.execute(
                    update(RewriteTaskModel)
                    .where(
                        RewriteTaskModel.project_id == project.id,
                        RewriteTaskModel.status.in_(["pending", "queued"]),
                        RewriteTaskModel.metadata_json["repair_source"].as_string() == REPAIR_SOURCE,
                    )
                    .values(status="superseded")
                )
            chapter_by_no = {int(chapter.chapter_number): chapter for chapter, _ in rows}
            for chapter_no, reasons in sorted(repair_targets.items()):
                chapter = chapter_by_no.get(chapter_no)
                if chapter is None:
                    continue
                task = RewriteTaskModel(
                    project_id=project.id,
                    trigger_type="narrative_richness_gate_repair",
                    trigger_source_id=chapter.id,
                    rewrite_strategy="targeted_edit",
                    priority=2 if chapter_no <= 12 else 3,
                    status="pending",
                    instructions=_task_instruction(chapter, reasons, prompt_preview),
                    context_required=["current_chapter", "story_bible", "prior_chapter_tail", "narrative_richness_kernels"],
                    metadata_json={
                        "repair_source": REPAIR_SOURCE,
                        "chapter_number": chapter_no,
                        "reasons": reasons,
                    },
                )
                session.add(task)
                await session.flush()
                created_tasks.append(str(task.id))

        status = classify_narrative_richness_report(
            applied=apply,
            repair_target_count=len(repair_targets),
            materialized_kernel_count=len(materialized_kernel_files),
        )
        summary = {
            "project_slug": PROJECT_SLUG,
            "applied": apply,
            **status,
            "chapter_count": len(rows),
            "metadata_had_narrative_richness": any(
                key in (project.metadata_json or {})
                for key in ("narrative_richness_context", "narrative_richness_kernels")
            ),
            "palette_missing_chapters": [a.chapter for a in chapter_audits if not a.landed_palette_items],
            "region_missing_chapters": [a.chapter for a in chapter_audits if not a.regions],
            "geography_findings": sum(len(a.geography_findings) for a in chapter_audits),
            "ethical_findings": [asdict(f) for f in ethical_report.findings],
            "mystery_findings": mystery_reports,
            "ensemble_findings": [asdict(f) for f in ensemble_report.findings],
            "repair_targets": {str(k): v for k, v in sorted(repair_targets.items())},
            "created_tasks": created_tasks,
            "materialized_kernel_files": materialized_kernel_files,
            "landed_palette_counter": Counter(
                item for audit in chapter_audits for item in audit.landed_palette_items
            ),
            "prompt_preview": prompt_preview,
            "chapters": [asdict(audit) for audit in chapter_audits],
        }

    out_dir = Path(settings.output.base_dir) / PROJECT_SLUG / "audits"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    report_path = out_dir / f"narrative-richness-audit-{stamp}.json"
    report_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=dict), encoding="utf-8")
    latest_path = out_dir / "narrative-richness-audit-latest.json"
    latest_path.write_text(report_path.read_text(encoding="utf-8"), encoding="utf-8")
    md_path = out_dir / "narrative-richness-audit-latest.md"
    md_path.write_text(_render_markdown(summary), encoding="utf-8")
    summary["report_path"] = str(report_path)
    summary["latest_path"] = str(latest_path)
    summary["markdown_path"] = str(md_path)
    return summary


def _render_markdown(summary: dict[str, Any]) -> str:
    lines = [
        "# Narrative Richness Audit — 青囊不语问阴阳",
        "",
        f"- Verdict: {summary['verdict']}",
        f"- Coverage: {summary['coverage']}",
        f"- Passed: {summary['passed']}",
        f"- Status reason: {summary['status_reason']}",
        f"- Applied: {summary['applied']}",
        f"- Chapters audited: {summary['chapter_count']}",
        f"- Palette missing chapters: {summary['palette_missing_chapters']}",
        f"- Region missing chapters: {summary['region_missing_chapters']}",
        f"- Geography findings: {summary['geography_findings']}",
        f"- Ethical findings: {len(summary['ethical_findings'])}",
        f"- Repair targets: {len(summary['repair_targets'])}",
        f"- Created tasks: {len(summary['created_tasks'])}",
        "",
        "## Repair Targets",
    ]
    for chapter, reasons in summary["repair_targets"].items():
        lines.append(f"- ch{chapter}: {', '.join(reasons)}")
    lines.extend(["", "## Prompt Preview", "", "```text", summary["prompt_preview"], "```", ""])
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="Persist metadata and create rewrite tasks")
    parser.add_argument("--replace-existing", action=argparse.BooleanOptionalAction, default=True)
    args = parser.parse_args()
    summary = asyncio.run(run(apply=args.apply, replace_existing=args.replace_existing))
    print(json.dumps({k: v for k, v in summary.items() if k != "chapters"}, ensure_ascii=False, indent=2, default=dict))


if __name__ == "__main__":
    main()
