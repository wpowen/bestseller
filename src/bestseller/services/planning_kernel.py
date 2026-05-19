# ruff: noqa: RUF001, E501
"""Authoritative early-planning kernel and prewrite readiness gate.

The writing pipeline already has many draft-time gates. This module focuses on
the earlier failure mode: a project can reach drafting with a thin or generic
macro plan, causing later chapters to converge toward similar beats. The kernel
normalizes all available planning inputs into a small metadata contract and
scores whether the book is ready to start or continue chapter production.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from bestseller.services.distilled_strategy_gate import (
    distilled_strategy_gate_snapshot,
    evaluate_distilled_strategy_consumption,
)
from bestseller.services.emotion_driven_kernel import (
    emotion_driven_kernel_from_dict,
    evaluate_emotion_contracts,
)
from bestseller.services.compliance_boundary_kernel import (
    evaluate_compliance_boundary_kernel,
)
from bestseller.services.public_emotion_kernel import (
    evaluate_public_emotion_kernel,
)
from bestseller.services.ranking_capability_profile import (
    load_ranking_capability_profile_text,
)
from bestseller.services.story_design_kernel import story_design_kernel_from_dict

_KERNEL_VERSION = 1
_BLOCKING_SEVERITIES = {"critical", "high"}
_MIN_PASS_SCORE = 75

_PROGRESSION_MARKERS = (
    "xianxia",
    "cultivation",
    "progression",
    "litrpg",
    "gamelit",
    "仙侠",
    "修仙",
    "玄幻",
    "升级",
    "异能",
    "御兽",
)
_RULE_MYSTERY_MARKERS = (
    "rule",
    "mystery",
    "detective",
    "horror",
    "thriller",
    "occult",
    "规则",
    "悬疑",
    "推理",
    "民俗",
    "诡",
    "怪谈",
    "探案",
)
_RELATIONSHIP_MARKERS = (
    "romance",
    "romantasy",
    "relationship",
    "female",
    "no-cp",
    "言情",
    "女频",
    "女性",
    "无cp",
    "无CP",
    "宫斗",
)

_REPAIR_ACTIONS = {
    "benchmark_alignment_missing": "补齐 3-5 个结构对标或榜单级能力 Profile，再生成正文。",
    "unique_hook_missing": "补齐本书区别于同题材项目的唯一卖点、反套路选择或故事基因组合。",
    "series_engine_missing": "补齐 reader_promise、前三章抓手、章节尾钩和短/长回报节奏。",
    "long_arc_capacity_missing": "为长篇补齐 10 个以上可升级/兑现的长线节点，避免写到中段同质化。",
    "volume_differentiation_missing": "重排卷计划，确保冲突阶段、主压力源、高潮形态和爽点类别轮换。",
    "volume_primary_force_repeats": "重排相邻卷的主压力源，避免连续两卷由同一反派/势力驱动。",
    "progression_engine_missing": "补齐可计量的升级体系、资源账、能力边界和代价。",
    "rule_engine_missing": "补齐规则系统：可见效果、破局路径、违反代价和反噬升级。",
    "relationship_engine_missing": "补齐关系代理：信任、债务、边界、选择和兑现窗口。",
    "story_design_kernel_missing": "补齐 StoryDesignKernel：故事形态、读者承诺、剧情树、变化向量和节拍表。",
    "story_design_contract_invalid": "修复 StoryDesignKernel 校验错误，确保主线、支线依赖、变化向量和节拍表完整。",
    "story_design_contract_thin": "扩展 StoryDesignKernel 的剧情树、变化向量和节拍表，避免只剩概念口号。",
    "distilled_strategy_missing": "先编译 DistilledStrategyCard，再进入最终规划。",
    "distilled_strategy_low_maturity": "将低成熟度蒸馏结果作为方向参考，不要当作硬模板。",
    "distilled_strategy_not_consumed": "把蒸馏机制绑定到本书独有世界规则、人物选择、资源代价或兑现窗口。",
    "distilled_strategy_copy_risk": "重写规划，移除被反抄袭边界命中的组合、开局链或来源特征。",
    "distilled_strategy_state_variables_missing": "把策略卡要求的状态变量写入 StoryDesignKernel、卷纲或章纲。",
    "distilled_worldview_not_bound": "把蒸馏策略要求的世界状态变量、资产、权威声明写入 StoryDesignKernel.worldview_kernel。",
    "emotion_driven_kernel_missing": "补齐 EmotionDrivenKernel：读者情绪承诺、代入链、炸弹合同、反派道德面具和结局纹理。",
    "public_emotion_kernel_missing": "补齐 PublicEmotionKernel：目标群体、公共情绪、未说出口的话、类型转译和读者补偿。",
    "public_emotion_bridge_missing": "补齐公共情绪桥：把群体情绪安全转译为本书专属设定、标题钩子和追读补偿。",
    "compliance_boundary_kernel_missing": "补齐 ComplianceBoundaryKernel：平台/地区策略包、禁止触达区、允许转译和降风险写法。",
    "compliance_boundary_high_risk": "修复公共情绪合规风险：移除真实群体攻击、现实违法攻略、低俗擦边或可识别现实目标。",
    "empathy_contract_missing": "补齐代入链：处境、欲望、感官入口、判断逻辑、合理行动和行动后果。",
    "bomb_contract_not_consumed": "补齐桌下炸弹：读者已知、角色盲区、触发条件、倒计时、严重后果和兑现窗口。",
    "antagonist_moral_contract_thin": "补齐反派的真实善行、隐秘欲望、裂缝、自我辩护和崩塌伤口。",
    "ending_texture_missing": "补齐 HE/BE 结局纹理：核心兑现、不可逆代价、主题回答、未来打开或美感回收。",
}

_DIRECTIVE_TEMPLATES_ZH = {
    "benchmark_alignment_missing": "补齐榜单对标前，正文必须避免泛类型套路；每章至少体现一个本书独有的设定、人物选择或冲突变体。",
    "unique_hook_missing": "本轮写作必须强化唯一卖点：场景冲突、角色选择和章尾钩都要服务于本书区别于同题材作品的核心差异。",
    "series_engine_missing": "每章必须明确 reader promise、短回报和章尾推进钩，禁止只完成情节交代而没有追读驱动。",
    "long_arc_capacity_missing": "后续章节必须持续种植长线伏笔、资源债、关系债或规则债，并记录可兑现窗口，避免中段只靠随机事件续写。",
    "volume_differentiation_missing": "后续卷/章节必须显性更换主压力源、冲突阶段、高潮形态和爽点类型，禁止相邻卷复用同一种推进模型。",
    "volume_primary_force_repeats": "后续卷规划必须更换相邻卷主压力源；当前卷章节也要引入新的外部压力或内部代价，避免同一反派/势力连续驱动。",
    "progression_engine_missing": "升级/成长类章节必须写清资源账、能力边界、代价和阶段性增益，禁止无因升级或只靠口号推进。",
    "rule_engine_missing": "规则/悬疑类章节必须写清可见规则、线索路径、违规代价和破局验证，禁止只用氛围替代可解谜题。",
    "relationship_engine_missing": "关系驱动章节必须写清信任、债务、边界、选择和兑现窗口，禁止关系只当情绪装饰。",
    "story_design_contract_invalid": "当前剧情设计内核不可用，必须先修复主线、支线依赖、变化向量和节拍表，再进入章节规划。",
    "story_design_contract_thin": "当前剧情设计内核过薄，必须增加至少两条相互依赖的剧情线和明确章节状态变化。",
    "distilled_strategy_missing": "当前项目缺少蒸馏策略卡；规划只能使用泛化参考，必须先编译项目专属 DistilledStrategyCard。",
    "distilled_strategy_low_maturity": "当前蒸馏聚合成熟度偏低；只能借用方向，禁止按来源路径硬套。",
    "distilled_strategy_not_consumed": "当前规划没有真正消费蒸馏策略；必须把机制转化为本书独有的世界规则、人物选择、资源代价或兑现窗口。",
    "distilled_strategy_copy_risk": "当前规划命中反抄袭边界；必须更换开局链、机制组合、专名、顺序或场景表达。",
    "distilled_strategy_state_variables_missing": "当前规划缺少蒸馏策略要求的状态变量；后续卷章必须显性追踪这些变量的变化。",
    "distilled_worldview_not_bound": "当前 StoryDesignKernel 未绑定蒸馏世界观状态变量、资产或权威声明；后续必须补齐 worldview_kernel 的结构化世界观账本。",
    "emotion_driven_kernel_missing": "当前项目缺少情绪驱动内核；新规划至少要明确读者等待、主角代入链、桌下炸弹和结局纹理。",
    "public_emotion_kernel_missing": "当前项目缺少公共情绪内核；新规划至少要明确目标群体、公共情绪、类型转译和读者补偿。",
    "public_emotion_bridge_missing": "当前项目缺少可执行公共情绪桥；必须把公共情绪转译为本书专属的设定、标题钩子和追读补偿。",
    "compliance_boundary_kernel_missing": "当前项目缺少合规边界内核；公共情绪进入标题、简介和章纲前必须有安全转译边界。",
    "compliance_boundary_high_risk": "当前公共情绪或标题候选命中高风险合规边界；必须先改写为虚构系统表达。",
    "empathy_contract_missing": "当前情绪合同缺少代入链；章节必须写清人物处境、欲望、感官、判断、行动和后果。",
    "bomb_contract_not_consumed": "当前情绪合同缺少可执行炸弹；章节必须写清信息差、触发条件、倒计时、严重后果和兑现窗口。",
    "antagonist_moral_contract_thin": "反派道德面具过薄；必须写出真实善行、核心欲望、细小裂缝、自我辩护和崩塌伤口。",
    "ending_texture_missing": "结局纹理不完整；必须提前锁定幸福/悲剧兑现方式、不可逆代价、主题回答和回收物。",
}

_DIRECTIVE_TEMPLATES_EN = {
    "benchmark_alignment_missing": "Until benchmark alignment is complete, avoid generic genre beats; every chapter must surface a project-specific setting, choice, or conflict variant.",
    "unique_hook_missing": "Strengthen the unique hook in scene conflict, character choice, and chapter-ending propulsion.",
    "series_engine_missing": "Every chapter must deliver the reader promise, short payoff, and a forward hook; do not merely summarize plot logistics.",
    "long_arc_capacity_missing": "Continue planting long-arc clues, resource debts, relationship debts, or rule debts with clear payoff windows.",
    "volume_differentiation_missing": "Future volumes/chapters must rotate primary pressure, conflict phase, climax shape, and payoff type instead of reusing one progression model.",
    "volume_primary_force_repeats": "Future volume plans must change adjacent primary pressure sources; current chapters should add new external pressure or internal cost.",
    "progression_engine_missing": "Progression chapters must show resource accounting, ability limits, cost, and measurable stage gain.",
    "rule_engine_missing": "Rule/mystery chapters must show visible rules, clue path, violation cost, and verifiable solution logic.",
    "relationship_engine_missing": "Relationship-driven chapters must show trust, debt, boundary, choice, and payoff windows.",
    "story_design_contract_invalid": "Repair the StoryDesignKernel before planning chapters: mainline, dependencies, change vectors, and beat schedule must validate.",
    "story_design_contract_thin": "Expand the StoryDesignKernel with dependent plot lines and concrete chapter state changes.",
    "distilled_strategy_missing": "Compile a project-specific DistilledStrategyCard before final planning.",
    "distilled_strategy_low_maturity": "Use the low-maturity aggregate as directional guidance only, not as a hard template.",
    "distilled_strategy_not_consumed": "Bind distilled mechanisms to project-specific world rules, character choices, resource costs, or payoff windows.",
    "distilled_strategy_copy_risk": "Rewrite the plan to remove blocked source-like combinations, opening chains, names, or scenario order.",
    "distilled_strategy_state_variables_missing": "Track the strategy card's required state variables in the kernel, volume plan, or chapter outlines.",
    "distilled_worldview_not_bound": "Bind distilled worldview state variables, assets, and authority claims into StoryDesignKernel.worldview_kernel.",
    "emotion_driven_kernel_missing": "Add an EmotionDrivenKernel with reader waiting, empathy chains, bomb contracts, antagonist moral masks, and ending texture.",
    "public_emotion_kernel_missing": "Add a PublicEmotionKernel with audience segment, public emotion, genre translation, and reader compensation.",
    "public_emotion_bridge_missing": "Add an executable public-emotion bridge with project-local title hook and payoff.",
    "compliance_boundary_kernel_missing": "Add a ComplianceBoundaryKernel before public emotion enters copy or chapter planning.",
    "compliance_boundary_high_risk": "Rewrite the public-emotion translation to remove real-world target, illegal-instruction, hate, or lowbrow-risk framing.",
    "empathy_contract_missing": "Add an empathy chain: situation, desire, sensory entry, judgment, action, and consequence.",
    "bomb_contract_not_consumed": "Add an executable bomb: reader knowledge, character blindspot, trigger, countdown, consequence, and payoff window.",
    "antagonist_moral_contract_thin": "Give the antagonist real good, hidden desire, cracks, rationalization, and collapse wound.",
    "ending_texture_missing": "Lock the ending texture: fulfillment/tragedy mode, irreversible cost, theme answer, and callback/future image.",
}


@dataclass(frozen=True, slots=True)
class PrewriteReadinessFinding:
    code: str
    severity: str
    message: str
    path: str
    repair_action: str
    evidence: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
            "path": self.path,
            "repair_action": self.repair_action,
            "evidence": dict(self.evidence),
        }


@dataclass(frozen=True, slots=True)
class PrewriteReadinessReport:
    passed: bool
    score: int
    blocking_findings: tuple[PrewriteReadinessFinding, ...]
    warnings: tuple[PrewriteReadinessFinding, ...]
    recommended_repair_actions: tuple[str, ...]
    capability_snapshot: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return prewrite_readiness_report_to_dict(self)


def prewrite_readiness_report_to_dict(
    report: PrewriteReadinessReport,
) -> dict[str, Any]:
    return {
        "passed": report.passed,
        "score": report.score,
        "blocking_findings": [
            finding.to_dict() for finding in report.blocking_findings
        ],
        "warnings": [finding.to_dict() for finding in report.warnings],
        "recommended_repair_actions": list(report.recommended_repair_actions),
        "capability_snapshot": dict(report.capability_snapshot),
    }


def _is_en_language(language: str | None) -> bool:
    return _text(language).lower().startswith("en")


def _finding_codes_from_report(report: object) -> list[str]:
    if isinstance(report, PrewriteReadinessReport):
        findings = [*report.blocking_findings, *report.warnings]
        return [finding.code for finding in findings if finding.code]
    data = _as_mapping(report)
    raw_findings = [
        *_mapping_list(data.get("blocking_findings")),
        *_mapping_list(data.get("warnings")),
    ]
    return [str(item.get("code")) for item in raw_findings if item.get("code")]


def build_prewrite_repair_directives(
    report: object,
    *,
    language: str | None = None,
    max_directives: int = 5,
) -> list[str]:
    """Convert readiness findings into prompt-ready execution constraints."""

    templates = (
        _DIRECTIVE_TEMPLATES_EN
        if _is_en_language(language)
        else _DIRECTIVE_TEMPLATES_ZH
    )
    directives: list[str] = []
    for code in _finding_codes_from_report(report):
        if code in {
            "emotion_driven_kernel_missing",
            "public_emotion_kernel_missing",
            "compliance_boundary_kernel_missing",
        }:
            # Gradual rollout: missing optional planning kernels are visible
            # in telemetry, but should not force legacy projects into repair.
            continue
        directive = templates.get(code)
        if directive and directive not in directives:
            directives.append(directive)
        if len(directives) >= max_directives:
            break
    return directives


def _as_mapping(value: object) -> dict[str, object]:
    return dict(value) if isinstance(value, Mapping) else {}


def _as_sequence(value: object) -> list[object]:
    if value is None or isinstance(value, (str, bytes)):
        return []
    if isinstance(value, Sequence):
        return list(value)
    return []


def _text(value: object) -> str:
    return str(value).strip() if value is not None else ""


def _string_list(value: object) -> list[str]:
    return [_text(item) for item in _as_sequence(value) if _text(item)]


def _mapping_list(value: object) -> list[dict[str, object]]:
    return [_as_mapping(item) for item in _as_sequence(value) if _as_mapping(item)]


def _project_metadata(project_or_metadata: object) -> dict[str, object]:
    if isinstance(project_or_metadata, Mapping):
        direct = _as_mapping(project_or_metadata)
        nested = _as_mapping(direct.get("metadata_json"))
        return nested or _as_mapping(direct.get("metadata")) or direct
    return _as_mapping(
        getattr(project_or_metadata, "metadata_json", None)
        or getattr(project_or_metadata, "metadata", None)
    )


def _project_attr(project: object | None, key: str, default: object = None) -> object:
    if project is None:
        return default
    return getattr(project, key, default)


def _genre_haystack(
    kernel: Mapping[str, object],
    *,
    genre: str | None,
    sub_genre: str | None,
) -> str:
    parts = [
        genre,
        sub_genre,
        kernel.get("genre"),
        kernel.get("sub_genre"),
        _as_mapping(kernel.get("category")).get("category_key"),
    ]
    return " ".join(_text(part).lower() for part in parts if _text(part))


def _matches(haystack: str, markers: Sequence[str]) -> bool:
    return any(marker.lower() in haystack for marker in markers)


def _extract_unique_hook(
    metadata: Mapping[str, object],
    story_facets: Mapping[str, object],
    commercial_brief: Mapping[str, object],
) -> str:
    for key in ("unique_hook", "creative_hook", "hook", "premise_variation"):
        value = _text(metadata.get(key) or commercial_brief.get(key))
        if value:
            return value
    tags = _string_list(story_facets.get("trope_tags"))
    setting = _text(story_facets.get("setting"))
    drive = _text(story_facets.get("narrative_drive"))
    if tags or setting or drive:
        return " / ".join([item for item in (setting, drive, ", ".join(tags[:5])) if item])
    return ""


def _extract_benchmark_works(
    metadata: Mapping[str, object],
    commercial_brief: Mapping[str, object],
    writing_profile: Mapping[str, object],
) -> list[str]:
    style = _as_mapping(writing_profile.get("style"))
    market = _as_mapping(writing_profile.get("market"))
    values: list[str] = []
    for raw in (
        metadata.get("benchmark_works"),
        commercial_brief.get("benchmark_works"),
        style.get("reference_works"),
        market.get("reference_works"),
    ):
        values.extend(_string_list(raw))
    deduped: list[str] = []
    for value in values:
        if value not in deduped:
            deduped.append(value)
    return deduped


def _extract_comparables(metadata: Mapping[str, object]) -> list[str]:
    profile = _as_mapping(
        metadata.get("ranking_capability_profile")
        or metadata.get("commercial_capability_profile")
        or metadata.get("benchmark_capability_profile")
    )
    return _string_list(profile.get("comparables"))


def _volume_plan_list(volume_plan: object) -> list[dict[str, object]]:
    if isinstance(volume_plan, Mapping):
        return _mapping_list(volume_plan.get("volumes"))
    return _mapping_list(volume_plan)


def _payoff_items(volume: Mapping[str, object]) -> list[str]:
    values: list[str] = []
    for key in (
        "core_payoff",
        "reader_payoff",
        "payoff_class",
        "volume_resolution",
        "volume_climax",
        "key_reveal",
        "reader_hook_to_next",
    ):
        text = _text(volume.get(key))
        if text:
            values.append(text)
    values.extend(_string_list(volume.get("foreshadowing_planted")))
    values.extend(_string_list(volume.get("foreshadowing_paid_off")))
    return values


def _series_engine_from(
    book_spec: Mapping[str, object],
    writing_profile: Mapping[str, object],
) -> dict[str, object]:
    engine = _as_mapping(book_spec.get("series_engine"))
    market = _as_mapping(writing_profile.get("market"))
    serialization = _as_mapping(writing_profile.get("serialization"))
    return {
        "core_engine": _text(
            engine.get("core_serial_engine")
            or engine.get("core_engine")
            or engine.get("engine")
        ),
        "reader_promise": _text(
            engine.get("reader_promise") or market.get("reader_promise")
        ),
        "first_three_chapter_hook": _text(
            engine.get("first_three_chapter_hook")
            or serialization.get("first_three_chapter_goal")
            or market.get("opening_strategy")
        ),
        "chapter_hook_strategy": _text(
            engine.get("chapter_ending_hook_strategy")
            or engine.get("chapter_hook_strategy")
            or market.get("chapter_hook_strategy")
        ),
        "payoff_rhythm": _text(
            engine.get("payoff_rhythm")
            or engine.get("short_and_long_payoff_rhythm")
            or market.get("payoff_rhythm")
        ),
    }


def _story_design_kernel_from(
    metadata: Mapping[str, object],
    story_design_kernel: Mapping[str, object] | None,
) -> dict[str, object]:
    raw = _as_mapping(story_design_kernel) or _as_mapping(
        metadata.get("story_design_kernel") or metadata.get("story_design")
    )
    if not raw:
        return {
            "present": False,
            "valid": False,
            "reader_promise": "",
            "unique_hook": "",
            "primary_duties": [],
            "change_vectors": [],
            "plot_line_count": 0,
            "main_plot_line_count": 0,
            "beat_count": 0,
            "worldview_state_variable_count": 0,
            "worldview_asset_count": 0,
            "worldview_authority_claim_count": 0,
            "worldview_distilled_binding_count": 0,
            "reverse_outline_status": "not_started",
        }

    try:
        kernel = story_design_kernel_from_dict(dict(raw))
    except Exception as exc:
        plot_tree = _mapping_list(raw.get("plot_tree"))
        beat_schedule = _mapping_list(raw.get("beat_schedule"))
        premise = _as_mapping(raw.get("premise_contract"))
        shape = _as_mapping(raw.get("shape"))
        worldview = _as_mapping(raw.get("worldview_kernel"))
        return {
            "present": True,
            "valid": False,
            "validation_error": str(exc)[:500],
            "reader_promise": _text(raw.get("reader_promise")),
            "unique_hook": _text(premise.get("unique_hook")),
            "primary_duties": _string_list(shape.get("primary_duties")),
            "change_vectors": _string_list(raw.get("change_vectors")),
            "plot_line_count": len(plot_tree),
            "main_plot_line_count": len(
                [node for node in plot_tree if _text(node.get("line_type")) == "main"]
            ),
            "beat_count": len(beat_schedule),
            "worldview_state_variable_count": len(
                _mapping_list(worldview.get("state_variables"))
            ),
            "worldview_asset_count": len(_mapping_list(worldview.get("asset_ledger"))),
            "worldview_authority_claim_count": len(
                _mapping_list(worldview.get("authority_claims"))
            ),
            "worldview_distilled_binding_count": len(
                _mapping_list(worldview.get("distilled_mechanism_bindings"))
            ),
            "reverse_outline_status": _text(raw.get("reverse_outline_status"))
            or "not_started",
        }

    worldview = kernel.worldview_kernel
    return {
        "present": True,
        "valid": True,
        "reader_promise": kernel.reader_promise,
        "unique_hook": kernel.premise_contract.unique_hook,
        "primary_duties": list(kernel.shape.primary_duties),
        "change_vectors": list(kernel.change_vectors),
        "plot_line_count": len(kernel.plot_tree),
        "main_plot_line_count": len(
            [node for node in kernel.plot_tree if node.line_type == "main"]
        ),
        "beat_count": len(kernel.beat_schedule),
        "worldview_state_variable_count": len(worldview.state_variables)
        if worldview is not None
        else 0,
        "worldview_asset_count": len(worldview.asset_ledger)
        if worldview is not None
        else 0,
        "worldview_authority_claim_count": len(worldview.authority_claims)
        if worldview is not None
        else 0,
        "worldview_distilled_binding_count": len(worldview.distilled_mechanism_bindings)
        if worldview is not None
        else 0,
        "reverse_outline_status": kernel.reverse_outline_status,
    }


def _emotion_driven_kernel_from(
    metadata: Mapping[str, object],
    emotion_driven_kernel: Mapping[str, object] | None,
) -> dict[str, object]:
    raw = _as_mapping(emotion_driven_kernel) or _as_mapping(
        metadata.get("emotion_driven_kernel")
    )
    if not raw:
        return {
            "present": False,
            "valid": False,
            "issue_codes": [],
            "empathy_contract_count": 0,
            "bomb_contract_count": 0,
            "antagonist_moral_contract_count": 0,
            "ending_texture_present": False,
        }

    try:
        kernel = emotion_driven_kernel_from_dict(dict(raw))
        report = evaluate_emotion_contracts(kernel)
    except Exception as exc:
        return {
            "present": True,
            "valid": False,
            "validation_error": str(exc)[:500],
            "issue_codes": ["EMOTION_KERNEL_INVALID"],
            "empathy_contract_count": len(_mapping_list(raw.get("empathy_contracts"))),
            "bomb_contract_count": len(_mapping_list(raw.get("bomb_contracts"))),
            "antagonist_moral_contract_count": len(
                _mapping_list(raw.get("antagonist_moral_contracts"))
            ),
            "ending_texture_present": bool(_as_mapping(raw.get("ending_texture_contract"))),
        }

    return {
        "present": True,
        "valid": report.passed,
        "issue_codes": [issue.code for issue in report.issues],
        "issue_count": len(report.issues),
        "critical_count": report.critical_count,
        "empathy_contract_count": len(kernel.empathy_contracts),
        "bomb_contract_count": len(kernel.bomb_contracts),
        "antagonist_moral_contract_count": len(kernel.antagonist_moral_contracts),
        "ending_texture_present": kernel.ending_texture_contract is not None,
    }


def _public_emotion_kernel_from(metadata: Mapping[str, object]) -> dict[str, object]:
    raw = _as_mapping(metadata.get("public_emotion_kernel"))
    report = evaluate_public_emotion_kernel(raw or None)
    payload = report.to_dict()
    if raw and payload.get("valid"):
        bridges = _mapping_list(raw.get("emotion_bridges"))
        payload["bridge_types"] = _string_list([bridge.get("bridge_type") for bridge in bridges])
        payload["title_hooks"] = _string_list([bridge.get("title_hook") for bridge in bridges])
    return payload


def _compliance_boundary_kernel_from(metadata: Mapping[str, object]) -> dict[str, object]:
    raw = _as_mapping(metadata.get("compliance_boundary_kernel"))
    public_emotion = _as_mapping(metadata.get("public_emotion_kernel"))
    candidate_texts: list[str] = []
    for bridge in _mapping_list(public_emotion.get("emotion_bridges")):
        candidate_texts.extend(
            [
                _text(bridge.get("title_hook")),
                _text(bridge.get("story_hook")),
                _text(bridge.get("genre_translation")),
            ]
        )
    report = evaluate_compliance_boundary_kernel(
        raw or None,
        candidate_texts=[text for text in candidate_texts if text],
    )
    return report.to_dict()


def build_project_planning_kernel(
    project: object | None = None,
    *,
    project_metadata: Mapping[str, object] | None = None,
    book_spec: Mapping[str, object] | None = None,
    world_spec: Mapping[str, object] | None = None,
    cast_spec: Mapping[str, object] | None = None,
    volume_plan: object | None = None,
    story_design_kernel: Mapping[str, object] | None = None,
    emotion_driven_kernel: Mapping[str, object] | None = None,
    public_emotion_kernel: Mapping[str, object] | None = None,
    compliance_boundary_kernel: Mapping[str, object] | None = None,
    output_base_dir: str | Path | None = None,
) -> dict[str, object]:
    """Build a normalized planning contract from all available artifacts."""

    metadata = {
        **_project_metadata(project),
        **_as_mapping(project_metadata),
    }
    if public_emotion_kernel:
        metadata["public_emotion_kernel"] = dict(public_emotion_kernel)
    if compliance_boundary_kernel:
        metadata["compliance_boundary_kernel"] = dict(compliance_boundary_kernel)
    book = _as_mapping(book_spec) or _as_mapping(metadata.get("book_spec"))
    world = _as_mapping(world_spec) or _as_mapping(metadata.get("world_spec"))
    cast = _as_mapping(cast_spec) or _as_mapping(metadata.get("cast_spec"))
    volumes = _volume_plan_list(volume_plan or metadata.get("volume_plan"))
    story_facets = _as_mapping(metadata.get("story_facets"))
    commercial_brief = _as_mapping(metadata.get("commercial_brief"))
    writing_profile = _as_mapping(metadata.get("writing_profile"))

    project_slug = _text(_project_attr(project, "slug", metadata.get("project_slug")))
    ranking_profile_text = load_ranking_capability_profile_text(
        project_slug=project_slug,
        project_metadata=metadata,
        output_base_dir=output_base_dir,
    )

    conflict_phases = [_text(item.get("conflict_phase")) for item in volumes]
    primary_forces = [_text(item.get("primary_force_name")) for item in volumes]
    payoff_anchors: list[str] = []
    for volume in volumes:
        payoff_anchors.extend(_payoff_items(volume))

    series_engine = _series_engine_from(book, writing_profile)
    benchmark_works = _extract_benchmark_works(
        metadata,
        commercial_brief,
        writing_profile,
    )
    story_design = _story_design_kernel_from(metadata, story_design_kernel)
    emotion_driven = _emotion_driven_kernel_from(metadata, emotion_driven_kernel)
    public_emotion = _public_emotion_kernel_from(metadata)
    compliance_boundary = _compliance_boundary_kernel_from(metadata)
    distilled_strategy_card = metadata.get("distilled_strategy_card")
    distilled_strategy_expected = bool(
        distilled_strategy_card
        or metadata.get("distilled_strategy_expected")
        or metadata.get("distilled_design_reference_blocks")
    )
    distilled_strategy: dict[str, object] = {"present": False}
    if distilled_strategy_expected:
        strategy_card_payload = (
            _as_mapping(distilled_strategy_card)
            if isinstance(distilled_strategy_card, Mapping)
            else None
        )
        distilled_strategy_report = evaluate_distilled_strategy_consumption(
            strategy_card_payload,
            story_design_kernel=story_design_kernel or metadata.get("story_design_kernel"),
            volume_plan=volumes,
            chapter_outlines=(
                metadata.get("chapter_outline_batch")
                or metadata.get("chapter_outlines")
                or metadata.get("chapter_outline")
            ),
        )
        distilled_strategy = distilled_strategy_gate_snapshot(distilled_strategy_report)
        if strategy_card_payload:
            required_states = _string_list(
                strategy_card_payload.get("required_state_variables")
            )
            distilled_strategy["required_state_variables"] = required_states
            distilled_strategy["required_state_variable_count"] = len(required_states)

    return {
        "version": _KERNEL_VERSION,
        "project_slug": project_slug,
        "title": _text(_project_attr(project, "title", metadata.get("title"))),
        "genre": _text(_project_attr(project, "genre", metadata.get("genre"))),
        "sub_genre": _text(_project_attr(project, "sub_genre", metadata.get("sub_genre"))),
        "target_chapters": int(
            _project_attr(project, "target_chapters", metadata.get("target_chapters") or 0)
            or 0
        ),
        "category": {"category_key": _text(metadata.get("category_key"))},
        "benchmark": {
            "benchmark_works": benchmark_works,
            "comparables": _extract_comparables(metadata),
            "ranking_profile_present": bool(ranking_profile_text),
            "ranking_profile_excerpt": ranking_profile_text[:6000],
        },
        "creative_positioning": {
            "unique_hook": _extract_unique_hook(
                metadata,
                story_facets,
                commercial_brief,
            ),
            "story_facets_present": bool(story_facets),
            "target_audiences": _string_list(
                metadata.get("target_audiences")
                or commercial_brief.get("target_audiences")
            ),
        },
        "series_engine": series_engine,
        "foundation": {
            "has_book_spec": bool(book),
            "has_world_spec": bool(world),
            "has_cast_spec": bool(cast),
            "protagonist": _as_mapping(
                cast.get("protagonist") or book.get("protagonist")
            ).get("name"),
            "world_rule_count": len(_as_sequence(world.get("rules"))),
            "faction_count": len(_as_sequence(world.get("factions"))),
            "supporting_cast_count": len(_as_sequence(cast.get("supporting_cast"))),
            "has_decision_policy": bool(
                _as_mapping(_as_mapping(cast.get("protagonist")).get("decision_policy"))
                or _as_mapping(_as_mapping(book.get("protagonist")).get("decision_policy"))
            ),
        },
        "volume_strategy": {
            "volume_count": len(volumes),
            "conflict_phases": conflict_phases,
            "primary_forces": primary_forces,
            "unique_conflict_phase_count": len({item for item in conflict_phases if item}),
            "unique_primary_force_count": len({item for item in primary_forces if item}),
            "escalation_anchor_count": len([item for item in payoff_anchors if item]),
            "escalation_anchors": payoff_anchors[:20],
        },
        "story_design": story_design,
        "emotion_driven": emotion_driven,
        "public_emotion": public_emotion,
        "compliance_boundary": compliance_boundary,
        "distilled_strategy": distilled_strategy,
    }


def _finding(
    code: str,
    severity: str,
    message: str,
    path: str,
    *,
    evidence: Mapping[str, Any] | None = None,
) -> PrewriteReadinessFinding:
    return PrewriteReadinessFinding(
        code=code,
        severity=severity,
        message=message,
        path=path,
        repair_action=_REPAIR_ACTIONS.get(code, "补齐该规划能力后重新运行写前门禁。"),
        evidence=dict(evidence or {}),
    )


def _has_series_engine(kernel: Mapping[str, object]) -> bool:
    engine = _as_mapping(kernel.get("series_engine"))
    benchmark = _as_mapping(kernel.get("benchmark"))
    if benchmark.get("ranking_profile_present") and _text(
        benchmark.get("ranking_profile_excerpt")
    ):
        return True
    return bool(
        _text(engine.get("reader_promise"))
        and _text(engine.get("chapter_hook_strategy"))
        and _text(engine.get("payoff_rhythm"))
    )


def _has_progression_engine(kernel: Mapping[str, object]) -> bool:
    foundation = _as_mapping(kernel.get("foundation"))
    if int(foundation.get("world_rule_count") or 0) > 0:
        return True
    # Explicit BookSpec/WorldSpec keys are not retained in the kernel to keep it
    # compact, so a ranking profile can also serve as a prewrite signal here.
    benchmark = _as_mapping(kernel.get("benchmark"))
    text = _text(benchmark.get("ranking_profile_excerpt"))
    return any(marker in text for marker in ("升级", "境界", "资源", "progression", "power"))


def _has_rule_engine(kernel: Mapping[str, object]) -> bool:
    foundation = _as_mapping(kernel.get("foundation"))
    if int(foundation.get("world_rule_count") or 0) > 0:
        return True
    text = _text(_as_mapping(kernel.get("benchmark")).get("ranking_profile_excerpt"))
    return any(marker in text for marker in ("规则", "rule", "case", "clue", "线索"))


def _has_relationship_engine(kernel: Mapping[str, object]) -> bool:
    foundation = _as_mapping(kernel.get("foundation"))
    if int(foundation.get("supporting_cast_count") or 0) > 0:
        return True
    text = _text(_as_mapping(kernel.get("benchmark")).get("ranking_profile_excerpt"))
    return any(marker in text for marker in ("关系", "relationship", "romance", "trust"))


def evaluate_prewrite_readiness(
    kernel: Mapping[str, object],
    *,
    genre: str | None = None,
    sub_genre: str | None = None,
    target_chapters: int | None = None,
) -> PrewriteReadinessReport:
    """Score whether a project has enough early planning to enter drafting."""

    findings: list[PrewriteReadinessFinding] = []
    benchmark = _as_mapping(kernel.get("benchmark"))
    positioning = _as_mapping(kernel.get("creative_positioning"))
    volume_strategy = _as_mapping(kernel.get("volume_strategy"))
    foundation = _as_mapping(kernel.get("foundation"))
    story_design = _as_mapping(kernel.get("story_design"))
    emotion_driven = _as_mapping(kernel.get("emotion_driven"))
    public_emotion = _as_mapping(kernel.get("public_emotion"))
    compliance_boundary = _as_mapping(kernel.get("compliance_boundary"))
    distilled_strategy = _as_mapping(kernel.get("distilled_strategy"))
    target = int(target_chapters or kernel.get("target_chapters") or 0)

    if not (
        _string_list(benchmark.get("benchmark_works"))
        or _string_list(benchmark.get("comparables"))
        or benchmark.get("ranking_profile_present")
    ):
        findings.append(
            _finding(
                "benchmark_alignment_missing",
                "high",
                "Missing benchmark works, comparables, or ranking capability profile.",
                "benchmark",
            )
        )

    if not _text(positioning.get("unique_hook")):
        findings.append(
            _finding(
                "unique_hook_missing",
                "high",
                "Missing a concrete unique hook or story-facet differentiation signal.",
                "creative_positioning.unique_hook",
            )
        )

    if not _has_series_engine(kernel):
        findings.append(
            _finding(
                "series_engine_missing",
                "critical",
                "Missing reader promise plus hook/payoff rhythm.",
                "series_engine",
            )
        )

    volume_count = int(volume_strategy.get("volume_count") or 0)
    escalation_count = int(volume_strategy.get("escalation_anchor_count") or 0)
    if target >= 80 and escalation_count < 10:
        findings.append(
            _finding(
                "long_arc_capacity_missing",
                "high",
                "Long-form target lacks enough escalation/payoff anchors.",
                "volume_strategy.escalation_anchors",
                evidence={"target_chapters": target, "escalation_anchor_count": escalation_count},
            )
        )
    elif target >= 40 and volume_count < 2 and not benchmark.get("ranking_profile_present"):
        findings.append(
            _finding(
                "long_arc_capacity_missing",
                "warning",
                "Medium/long project lacks a multi-volume or profile-backed long-arc plan.",
                "volume_strategy.volume_count",
                evidence={"target_chapters": target, "volume_count": volume_count},
            )
        )

    unique_phase_count = int(volume_strategy.get("unique_conflict_phase_count") or 0)
    unique_force_count = int(volume_strategy.get("unique_primary_force_count") or 0)
    phases = _string_list(volume_strategy.get("conflict_phases"))
    forces = _string_list(volume_strategy.get("primary_forces"))
    if volume_count >= 3 and unique_phase_count <= 1 and unique_force_count <= 1:
        findings.append(
            _finding(
                "volume_differentiation_missing",
                "critical",
                "Volume plan repeats the same conflict phase and primary force.",
                "volume_strategy",
                evidence={"volume_count": volume_count, "phases": phases, "forces": forces},
            )
        )
    elif volume_count >= 3 and any(
        forces[index] and forces[index] == forces[index - 1]
        for index in range(1, len(forces))
    ):
        findings.append(
            _finding(
                "volume_primary_force_repeats",
                "high",
                "Adjacent volumes repeat the same primary pressure source.",
                "volume_strategy.primary_forces",
                evidence={"forces": forces},
            )
        )

    haystack = _genre_haystack(kernel, genre=genre, sub_genre=sub_genre)
    if _matches(haystack, _PROGRESSION_MARKERS) and not _has_progression_engine(kernel):
        findings.append(
            _finding(
                "progression_engine_missing",
                "high",
                "Progression genre lacks a measurable power/resource/rule engine.",
                "foundation.progression_engine",
            )
        )
    if _matches(haystack, _RULE_MYSTERY_MARKERS) and not _has_rule_engine(kernel):
        findings.append(
            _finding(
                "rule_engine_missing",
                "high",
                "Rule/mystery genre lacks a solvable rule or case engine.",
                "foundation.rule_engine",
            )
        )
    if _matches(haystack, _RELATIONSHIP_MARKERS) and not _has_relationship_engine(kernel):
        findings.append(
            _finding(
                "relationship_engine_missing",
                "high",
                "Relationship-driven genre lacks relationship agency scaffolding.",
                "foundation.relationship_engine",
            )
        )

    if not story_design.get("present"):
        findings.append(
            _finding(
                "story_design_kernel_missing",
                "warning",
                "Missing StoryDesignKernel; planning can proceed, but plot design is not state-driven.",
                "story_design",
            )
        )
    elif not story_design.get("valid"):
        findings.append(
            _finding(
                "story_design_contract_invalid",
                "high",
                "StoryDesignKernel is present but fails validation.",
                "story_design",
                evidence={"validation_error": _text(story_design.get("validation_error"))},
            )
        )
    elif (
        int(story_design.get("plot_line_count") or 0) < 1
        or int(story_design.get("beat_count") or 0) < 1
        or len(_string_list(story_design.get("change_vectors"))) < 3
    ):
        findings.append(
            _finding(
                "story_design_contract_thin",
                "warning",
                "StoryDesignKernel lacks enough plot lines, beats, or change vectors.",
                "story_design",
                evidence={
                    "plot_line_count": story_design.get("plot_line_count"),
                    "beat_count": story_design.get("beat_count"),
                    "change_vector_count": len(
                        _string_list(story_design.get("change_vectors"))
                    ),
                },
            )
        )

    public_emotion_codes = set(_string_list(public_emotion.get("issue_codes")))
    if not public_emotion.get("present"):
        findings.append(
            _finding(
                "public_emotion_kernel_missing",
                "warning",
                "Missing PublicEmotionKernel; planning can proceed, but public emotion is not project-scoped.",
                "public_emotion",
            )
        )
    elif "PUBLIC_EMOTION_KERNEL_INVALID" in public_emotion_codes:
        findings.append(
            _finding(
                "public_emotion_kernel_missing",
                "high",
                "PublicEmotionKernel is present but fails validation.",
                "public_emotion",
                evidence={"issue_codes": sorted(public_emotion_codes)},
            )
        )
    elif {
        "PUBLIC_EMOTION_BRIDGE_MISSING",
        "PUBLIC_EMOTION_TARGET_MISSING",
    } & public_emotion_codes:
        severity = (
            "high"
            if "PUBLIC_EMOTION_BRIDGE_MISSING" in public_emotion_codes
            else "warning"
        )
        findings.append(
            _finding(
                "public_emotion_bridge_missing",
                severity,
                "PublicEmotionKernel lacks a complete target segment or emotion bridge.",
                "public_emotion.emotion_bridges",
                evidence={"issue_codes": sorted(public_emotion_codes)},
            )
        )

    compliance_codes = set(_string_list(compliance_boundary.get("issue_codes")))
    if not compliance_boundary.get("present"):
        findings.append(
            _finding(
                "compliance_boundary_kernel_missing",
                "warning",
                "Missing ComplianceBoundaryKernel; public emotion has no explicit safety translation boundary.",
                "compliance_boundary",
            )
        )
    high_risk_compliance = any(
        code
        in compliance_codes
        for code in (
            "COMPLIANCE_BOUNDARY_KERNEL_INVALID",
            "COMPLIANCE_BOUNDARY_HIGH_RISK",
            "COMPLIANCE_REAL_WORLD_REVENGE",
            "COMPLIANCE_IDENTIFIABLE_REAL_TARGET",
            "COMPLIANCE_HATRED_OR_DISCRIMINATION",
            "COMPLIANCE_PROCEDURAL_HARM",
            "COMPLIANCE_SEXUAL_OR_MINOR_RISK",
        )
    )
    if high_risk_compliance:
        severity = (
            "critical"
            if any(
                code
                in compliance_codes
                for code in (
                    "COMPLIANCE_HATRED_OR_DISCRIMINATION",
                    "COMPLIANCE_PROCEDURAL_HARM",
                    "COMPLIANCE_SEXUAL_OR_MINOR_RISK",
                )
            )
            else "high"
        )
        findings.append(
            _finding(
                "compliance_boundary_high_risk",
                severity,
                "Compliance boundary detected high-risk public-emotion translation.",
                "compliance_boundary",
                evidence={"issue_codes": sorted(compliance_codes)},
            )
        )

    distilled_issue_codes = set(_string_list(distilled_strategy.get("issue_codes")))
    if distilled_strategy.get("present") is False and distilled_issue_codes:
        findings.append(
            _finding(
                "distilled_strategy_missing",
                "warning",
                "Distilled design references exist, but no project-specific strategy card was compiled.",
                "distilled_strategy.card",
            )
        )
    if "DISTILLED_STRATEGY_UNSAFE" in distilled_issue_codes:
        findings.append(
            _finding(
                "distilled_strategy_low_maturity",
                "high",
                "Distilled strategy aggregate is unsafe for planning use.",
                "distilled_strategy.maturity",
                evidence={
                    "maturity_score": distilled_strategy.get("maturity_score"),
                    "maturity_status": distilled_strategy.get("maturity_status"),
                },
            )
        )
    elif "DISTILLED_STRATEGY_LOW_MATURITY" in distilled_issue_codes:
        findings.append(
            _finding(
                "distilled_strategy_low_maturity",
                "warning",
                "Distilled strategy aggregate is low maturity and must stay directional.",
                "distilled_strategy.maturity",
                evidence={
                    "maturity_score": distilled_strategy.get("maturity_score"),
                    "maturity_status": distilled_strategy.get("maturity_status"),
                },
            )
        )
    if "DISTILLED_STRATEGY_COPY_RISK" in distilled_issue_codes:
        findings.append(
            _finding(
                "distilled_strategy_copy_risk",
                "critical",
                "Planning artifacts hit anti-copy boundaries from the distilled strategy.",
                "distilled_strategy.anti_copy_boundaries",
            )
        )
    if "DISTILLED_STRATEGY_FALLBACK_LEAK" in distilled_issue_codes:
        findings.append(
            _finding(
                "distilled_strategy_copy_risk",
                "high",
                "Fallback distillation placeholders leaked into planning artifacts.",
                "distilled_strategy.volume_paths",
            )
        )
    if "DISTILLED_STRATEGY_NOT_CONSUMED" in distilled_issue_codes:
        findings.append(
            _finding(
                "distilled_strategy_not_consumed",
                "high",
                "Planning artifacts do not show transformed use of the selected distilled strategy.",
                "distilled_strategy.consumption",
            )
        )
    elif "DISTILLED_STRATEGY_STATE_VARIABLES_MISSING" in distilled_issue_codes:
        findings.append(
            _finding(
                "distilled_strategy_state_variables_missing",
                "warning",
                "Planning artifacts do not track distilled strategy state variables.",
                "distilled_strategy.state_variables",
            )
        )
    enhanced_worldview_count = (
        int(story_design.get("worldview_state_variable_count") or 0)
        + int(story_design.get("worldview_asset_count") or 0)
        + int(story_design.get("worldview_authority_claim_count") or 0)
        + int(story_design.get("worldview_distilled_binding_count") or 0)
    )
    if (
        int(distilled_strategy.get("required_state_variable_count") or 0) > 0
        and bool(story_design.get("valid"))
        and enhanced_worldview_count == 0
    ):
        findings.append(
            _finding(
                "distilled_worldview_not_bound",
                "warning",
                "Distilled strategy expects worldview state variables, but StoryDesignKernel.worldview_kernel has no enhanced bindings.",
                "story_design.worldview_kernel",
                evidence={
                    "required_state_variables": _string_list(
                        distilled_strategy.get("required_state_variables")
                    ),
                    "worldview_state_variable_count": story_design.get(
                        "worldview_state_variable_count"
                    ),
                    "worldview_asset_count": story_design.get("worldview_asset_count"),
                    "worldview_authority_claim_count": story_design.get(
                        "worldview_authority_claim_count"
                    ),
                    "worldview_distilled_binding_count": story_design.get(
                        "worldview_distilled_binding_count"
                    ),
                },
            )
        )

    emotion_issue_codes = set(_string_list(emotion_driven.get("issue_codes")))
    if not emotion_driven.get("present"):
        findings.append(
            _finding(
                "emotion_driven_kernel_missing",
                "warning",
                "Missing EmotionDrivenKernel; planning can proceed, but reader emotion chains are not state-driven.",
                "emotion_driven",
            )
        )
    elif not emotion_driven.get("valid"):
        if "EMOTION_KERNEL_INVALID" in emotion_issue_codes:
            findings.append(
                _finding(
                    "emotion_driven_kernel_missing",
                    "high",
                    "EmotionDrivenKernel is present but fails validation.",
                    "emotion_driven",
                    evidence={"validation_error": _text(emotion_driven.get("validation_error"))},
                )
            )
        if {
            "EMPATHY_CONTRACT_MISSING",
            "EMPATHY_CHAIN_MISSING",
        } & emotion_issue_codes:
            findings.append(
                _finding(
                    "empathy_contract_missing",
                    "high",
                    "EmotionDrivenKernel lacks a complete empathy chain.",
                    "emotion_driven.empathy_contracts",
                    evidence={"issue_codes": sorted(emotion_issue_codes)},
                )
            )
        if {
            "BOMB_TRIGGER_MISSING",
            "BOMB_CONTRACT_INCOMPLETE",
        } & emotion_issue_codes:
            findings.append(
                _finding(
                    "bomb_contract_not_consumed",
                    "high",
                    "EmotionDrivenKernel lacks an executable bomb contract.",
                    "emotion_driven.bomb_contracts",
                    evidence={"issue_codes": sorted(emotion_issue_codes)},
                )
            )
        if "ANTAGONIST_MASK_FLAT" in emotion_issue_codes:
            findings.append(
                _finding(
                    "antagonist_moral_contract_thin",
                    "warning",
                    "Antagonist moral contract is too thin to produce betrayal/memory value.",
                    "emotion_driven.antagonist_moral_contracts",
                    evidence={"issue_codes": sorted(emotion_issue_codes)},
                )
            )
        if {
            "ENDING_TEXTURE_MISSING",
            "ENDING_COST_ERASED",
            "HE_TEXTURE_INCOMPLETE",
            "TRAGEDY_CAUSALITY_WEAK",
            "TRAGEDY_CHOICE_MISSING",
            "ENDING_CALLBACK_MISSING",
        } & emotion_issue_codes:
            findings.append(
                _finding(
                    "ending_texture_missing",
                    "high",
                    "EmotionDrivenKernel lacks a complete HE/BE ending texture.",
                    "emotion_driven.ending_texture_contract",
                    evidence={"issue_codes": sorted(emotion_issue_codes)},
                )
            )

    blocking = tuple(
        finding for finding in findings if finding.severity in _BLOCKING_SEVERITIES
    )
    warnings = tuple(
        finding for finding in findings if finding.severity not in _BLOCKING_SEVERITIES
    )
    penalty = sum(
        20 if finding.severity == "critical" else 12 if finding.severity == "high" else 5
        for finding in findings
    )
    score = max(0, 100 - penalty)
    capability_snapshot = {
        "benchmark_alignment": not any(
            finding.code == "benchmark_alignment_missing" for finding in findings
        ),
        "unique_hook": bool(_text(positioning.get("unique_hook"))),
        "series_engine": _has_series_engine(kernel),
        "long_arc_capacity": not any(
            finding.code == "long_arc_capacity_missing" for finding in findings
        ),
        "volume_differentiation": not any(
            finding.code
            in {"volume_differentiation_missing", "volume_primary_force_repeats"}
            for finding in findings
        ),
        "has_book_spec": bool(foundation.get("has_book_spec")),
        "has_world_spec": bool(foundation.get("has_world_spec")),
        "has_cast_spec": bool(foundation.get("has_cast_spec")),
        "story_design_kernel": bool(story_design.get("valid")),
        "story_state_driven_planning": bool(
            story_design.get("valid")
            and int(story_design.get("beat_count") or 0) > 0
            and bool(_string_list(story_design.get("change_vectors")))
        ),
        "worldview_enhanced_contracts": {
            "state_variables": int(story_design.get("worldview_state_variable_count") or 0),
            "assets": int(story_design.get("worldview_asset_count") or 0),
            "authority_claims": int(
                story_design.get("worldview_authority_claim_count") or 0
            ),
            "distilled_bindings": int(
                story_design.get("worldview_distilled_binding_count") or 0
            ),
        },
        "reverse_outline_ready": _text(story_design.get("reverse_outline_status"))
        == "verified",
        "distilled_strategy_ready": bool(
            distilled_strategy.get("present") and distilled_strategy.get("passed")
        ),
        "emotion_driven_core": bool(
            emotion_driven.get("present") and emotion_driven.get("valid")
        ),
        "public_emotion_core": bool(
            public_emotion.get("present") and public_emotion.get("passed")
        ),
        "public_emotion_contracts": {
            "target_segments": int(public_emotion.get("target_segment_count") or 0),
            "emotion_bridges": int(public_emotion.get("emotion_bridge_count") or 0),
        },
        "compliance_boundary": bool(
            compliance_boundary.get("present") and compliance_boundary.get("passed")
        ),
        "compliance_boundary_policy": {
            "policy_pack_key": _text(compliance_boundary.get("policy_pack_key")),
            "risk_level": _text(compliance_boundary.get("risk_level")),
        },
        "emotion_driven_contracts": {
            "empathy": int(emotion_driven.get("empathy_contract_count") or 0),
            "bomb": int(emotion_driven.get("bomb_contract_count") or 0),
            "antagonist_moral": int(
                emotion_driven.get("antagonist_moral_contract_count") or 0
            ),
            "ending_texture": bool(emotion_driven.get("ending_texture_present")),
        },
    }
    actions: list[str] = []
    for finding in findings:
        if (
            finding.code
            in {
                "emotion_driven_kernel_missing",
                "public_emotion_kernel_missing",
                "compliance_boundary_kernel_missing",
            }
            and finding.severity == "warning"
        ):
            continue
        if finding.repair_action not in actions:
            actions.append(finding.repair_action)

    return PrewriteReadinessReport(
        passed=not blocking and score >= _MIN_PASS_SCORE,
        score=score,
        blocking_findings=blocking,
        warnings=warnings,
        recommended_repair_actions=tuple(actions),
        capability_snapshot=capability_snapshot,
    )


def persist_project_planning_kernel(
    project: object,
    *,
    project_metadata: Mapping[str, object] | None = None,
    book_spec: Mapping[str, object] | None = None,
    world_spec: Mapping[str, object] | None = None,
    cast_spec: Mapping[str, object] | None = None,
    volume_plan: object | None = None,
    story_design_kernel: Mapping[str, object] | None = None,
    emotion_driven_kernel: Mapping[str, object] | None = None,
    public_emotion_kernel: Mapping[str, object] | None = None,
    compliance_boundary_kernel: Mapping[str, object] | None = None,
    output_base_dir: str | Path | None = None,
) -> dict[str, object]:
    """Persist kernel + readiness report into ``project.metadata_json``."""

    metadata = {
        **_project_metadata(project),
        **_as_mapping(project_metadata),
    }
    if public_emotion_kernel:
        metadata["public_emotion_kernel"] = dict(public_emotion_kernel)
    if compliance_boundary_kernel:
        metadata["compliance_boundary_kernel"] = dict(compliance_boundary_kernel)
    kernel = build_project_planning_kernel(
        project,
        project_metadata=metadata,
        book_spec=book_spec,
        world_spec=world_spec,
        cast_spec=cast_spec,
        volume_plan=volume_plan,
        story_design_kernel=story_design_kernel,
        emotion_driven_kernel=emotion_driven_kernel,
        public_emotion_kernel=public_emotion_kernel,
        compliance_boundary_kernel=compliance_boundary_kernel,
        output_base_dir=output_base_dir,
    )
    report = evaluate_prewrite_readiness(
        kernel,
        genre=_text(_project_attr(project, "genre", metadata.get("genre"))) or None,
        sub_genre=_text(_project_attr(project, "sub_genre", metadata.get("sub_genre"))) or None,
        target_chapters=int(
            _project_attr(project, "target_chapters", metadata.get("target_chapters") or 0)
            or 0
        ),
    )
    directives = build_prewrite_repair_directives(
        report,
        language=_text(_project_attr(project, "language", metadata.get("language"))) or None,
    )
    profile_text = _text(_as_mapping(kernel.get("benchmark")).get("ranking_profile_excerpt"))
    next_metadata = {
        **metadata,
        "planning_kernel": kernel,
        "prewrite_readiness_report": prewrite_readiness_report_to_dict(report),
        "prewrite_repair_directives": directives,
    }
    if profile_text and not _text(next_metadata.get("ranking_capability_profile_block")):
        next_metadata["ranking_capability_profile_block"] = profile_text
    project.metadata_json = next_metadata
    return {
        "planning_kernel": kernel,
        "prewrite_readiness_report": prewrite_readiness_report_to_dict(report),
        "prewrite_repair_directives": directives,
    }


__all__ = [
    "PrewriteReadinessFinding",
    "PrewriteReadinessReport",
    "build_prewrite_repair_directives",
    "build_project_planning_kernel",
    "evaluate_prewrite_readiness",
    "persist_project_planning_kernel",
    "prewrite_readiness_report_to_dict",
]
