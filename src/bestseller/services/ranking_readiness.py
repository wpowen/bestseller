# ruff: noqa: ANN401, RUF001

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

Severity = Literal["critical", "high", "medium", "low", "info"]
Tier = Literal["flagship", "strong_project", "vertical_viable", "immature"]


@dataclass(frozen=True, slots=True)
class TextScoreDimension:
    key: str
    label: str
    weight: float
    high_score_standard: str


@dataclass(frozen=True, slots=True)
class PrelaunchDimensionScore:
    key: str
    label: str
    weight: float
    raw_score: float
    weighted_score: float
    high_score_standard: str
    evidence: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "weight": self.weight,
            "raw_score": round(self.raw_score, 2),
            "weighted_score": round(self.weighted_score, 2),
            "high_score_standard": self.high_score_standard,
            "evidence": self.evidence,
        }


@dataclass(frozen=True, slots=True)
class RankingReadinessFinding:
    code: str
    severity: Severity
    scope: str
    message: str
    suggestion: str
    evidence: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity,
            "scope": self.scope,
            "message": self.message,
            "suggestion": self.suggestion,
            "evidence": dict(self.evidence),
        }


@dataclass(frozen=True, slots=True)
class PrelaunchTextAssessment:
    score: float
    dimensions: tuple[PrelaunchDimensionScore, ...]
    findings: tuple[RankingReadinessFinding, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "score": round(self.score, 2),
            "dimensions": [item.to_dict() for item in self.dimensions],
            "findings": [item.to_dict() for item in self.findings],
        }


@dataclass(frozen=True, slots=True)
class BehaviorMetricSpec:
    key: str
    label: str
    target: float
    module: str
    direction: Literal["higher", "lower"] = "higher"
    scale: Literal["rate", "amount"] = "rate"


@dataclass(frozen=True, slots=True)
class BehaviorMetricResult:
    key: str
    label: str
    value: float | None
    target: float
    score_ratio: float | None
    passed: bool | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "value": None if self.value is None else round(self.value, 4),
            "target": self.target,
            "score_ratio": None if self.score_ratio is None else round(self.score_ratio, 4),
            "passed": self.passed,
        }


@dataclass(frozen=True, slots=True)
class BehaviorModuleScore:
    key: str
    label: str
    weight: float
    score: float | None
    metrics: tuple[BehaviorMetricResult, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "weight": self.weight,
            "score": None if self.score is None else round(self.score, 2),
            "metrics": [item.to_dict() for item in self.metrics],
        }


@dataclass(frozen=True, slots=True)
class ReaderBehaviorAssessment:
    score: float | None
    modules: tuple[BehaviorModuleScore, ...]
    findings: tuple[RankingReadinessFinding, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "score": None if self.score is None else round(self.score, 2),
            "modules": [item.to_dict() for item in self.modules],
            "findings": [item.to_dict() for item in self.findings],
        }


@dataclass(frozen=True, slots=True)
class RankingReadinessReport:
    project_slug: str | None
    title: str | None
    maturity_score: float
    tier: Tier
    passed: bool
    action: str
    scoring_basis: Literal["text_only", "text_plus_behavior"]
    text_assessment: PrelaunchTextAssessment
    behavior_assessment: ReaderBehaviorAssessment
    findings: tuple[RankingReadinessFinding, ...]
    productization_plan: Mapping[str, Any]
    marketing_assets: Mapping[str, Any]
    ip_readiness: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_slug": self.project_slug,
            "title": self.title,
            "maturity_score": round(self.maturity_score, 2),
            "tier": self.tier,
            "passed": self.passed,
            "action": self.action,
            "scoring_basis": self.scoring_basis,
            "text_assessment": self.text_assessment.to_dict(),
            "behavior_assessment": self.behavior_assessment.to_dict(),
            "findings": [item.to_dict() for item in self.findings],
            "productization_plan": dict(self.productization_plan),
            "marketing_assets": dict(self.marketing_assets),
            "ip_readiness": dict(self.ip_readiness),
        }


TEXT_SCORE_DIMENSIONS: tuple[TextScoreDimension, ...] = (
    TextScoreDimension("character", "人物塑造", 15, "主角有欲望、缺口、能动性和独特声音。"),
    TextScoreDimension(
        "opening_hook",
        "开篇钩子",
        10,
        "前三章至少成立任务、危机、秘密或关系倒挂之一。",
    ),
    TextScoreDimension("pacing_structure", "节奏与结构", 15, "每章有局部目标，长线有连续升级链。"),
    TextScoreDimension(
        "conflict_reversal",
        "冲突与反转",
        12,
        "冲突升级来自规则和人物选择，不靠巧合硬拧。",
    ),
    TextScoreDimension(
        "world_consistency",
        "世界观自洽",
        8,
        "核心规则少而硬，且直接作用于人物决策。",
    ),
    TextScoreDimension("language_voice", "语言风格", 10, "声音可辨识、稳定，并服务阅读效率。"),
    TextScoreDimension("theme_depth", "主题深度", 10, "主题进入人物后果，而不是外贴口号。"),
    TextScoreDimension(
        "original_selling_point",
        "原创卖点",
        8,
        "一句话能讲清主角、目标、阻力和独特规则。",
    ),
    TextScoreDimension("ending_closure", "结局处理", 5, "情节闭合、情绪回收与主题照应同时成立。"),
    TextScoreDimension("ip_potential", "IP潜力", 7, "角色标签、场景、视觉母题与衍生空间明确。"),
)

_TEXT_DIMENSION_BY_KEY = {item.key: item for item in TEXT_SCORE_DIMENSIONS}

BEHAVIOR_MODULES: Mapping[str, tuple[str, float, tuple[BehaviorMetricSpec, ...]]] = {
    "trial": (
        "试读吸引力",
        30,
        (
            BehaviorMetricSpec("first_chapter_completion_rate", "首章完成率", 0.65, "trial"),
            BehaviorMetricSpec("first_three_arrival_rate", "前3章到达率", 0.45, "trial"),
            BehaviorMetricSpec("bookshelf_rate", "加书架/收藏率", 0.12, "trial"),
        ),
    ),
    "retention": (
        "追更黏性",
        30,
        (
            BehaviorMetricSpec("chapter_10_arrival_rate", "第10章到达率", 0.32, "retention"),
            BehaviorMetricSpec("seven_day_follow_rate", "7日追更率", 0.35, "retention"),
            BehaviorMetricSpec(
                "break_sensitivity_rate",
                "断更敏感/流失率",
                0.15,
                "retention",
                direction="lower",
            ),
        ),
    ),
    "payment": (
        "付费转化",
        25,
        (
            BehaviorMetricSpec("first_pay_conversion_rate", "首付转化率", 0.10, "payment"),
            BehaviorMetricSpec("paid_retention_rate", "付费后留存", 0.55, "payment"),
            BehaviorMetricSpec("arppu", "ARPPU", 32.0, "payment", scale="amount"),
        ),
    ),
    "spread": (
        "传播扩散",
        15,
        (
            BehaviorMetricSpec("comment_rate", "评论率", 0.03, "spread"),
            BehaviorMetricSpec("share_rate", "分享率", 0.015, "spread"),
            BehaviorMetricSpec("role_topic_rate", "角色话题浓度", 0.03, "spread"),
            BehaviorMetricSpec("derivative_creation_rate", "二创率", 0.005, "spread"),
        ),
    ),
}

_TEXT_REPAIR_ACTIONS: Mapping[str, str] = {
    "character": "重写主角欲望、缺口、主动选择和声音标签，让读者三章内记住人。",
    "opening_hook": "把背景说明后移，前三章必须先完成立人、立局、立钩和立风格。",
    "pacing_structure": "为每章补局部目标和信息变化，每8-12章设置一次中型反转。",
    "conflict_reversal": "让反转从规则和人物选择自然长出，减少误会、巧合和硬转向。",
    "world_consistency": "压缩设定数量，保留会改变选择、代价和破局方式的硬规则。",
    "language_voice": "建立角色声纹和叙述句法，避免只有辞藻、没有可辨识声音。",
    "theme_depth": "把主题落到人物命运和行动后果，不用议论替代叙事。",
    "original_selling_point": "重写一句话概念：主角 + 目标 + 阻碍 + 独特规则。",
    "ending_closure": "提前规划卷终/书终的情节闭合、情绪回收和余味。",
    "ip_potential": "补齐角色视觉标签、名场面、可复用世界规则和关系结构。",
}

_TIER_ACTIONS: Mapping[Tier, str] = {
    "flagship": "旗舰级潜力稿：重点立项、优先资源、提前做IP预判。",
    "strong_project": "强项目：进入重点打磨，冲刺旗舰。",
    "vertical_viable": "可做垂类项目：结合品类与渠道定向开发。",
    "immature": "未成熟：优先重写人物、钩子或结构。",
}


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


def _compact_text(value: Any) -> str:
    return " ".join(_clean_text(value).split())


def _get_value(source: Any, key: str, default: Any = None) -> Any:
    if isinstance(source, Mapping):
        return source.get(key, default)
    return getattr(source, key, default)


def _get_nested(source: Any, *keys: str, default: Any = None) -> Any:
    current = source
    for key in keys:
        current = _get_value(current, key, None)
        if current is None:
            return default
    return current


def _model_dump(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, Mapping):
        return dict(value)
    if hasattr(value, "model_dump"):
        dumped = value.model_dump(mode="json")
        return dict(dumped) if isinstance(dumped, Mapping) else {}
    try:
        return dict(vars(value))
    except TypeError:
        return {}


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        parts = value.replace("，", ",").split(",")
        return _dedupe_texts(parts)
    if isinstance(value, Sequence) and not isinstance(value, str | bytes):
        return _dedupe_texts([str(item) for item in value if item is not None])
    return []


def _dedupe_texts(values: Sequence[Any]) -> list[str]:
    result: list[str] = []
    for value in values:
        text = _compact_text(value)
        if text and text not in result:
            result.append(text)
    return result


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def _raw_score(value: Any, *, default: float = 1.0) -> float:
    try:
        return _clamp(float(value), 1.0, 5.0)
    except (TypeError, ValueError):
        return default


def _normalize_rate(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if numeric > 1.0:
        numeric = numeric / 100.0
    return _clamp(numeric, 0.0, 1.0)


def _normalize_amount(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        return None


def _ratio_to_target(
    value: float,
    *,
    target: float,
    direction: Literal["higher", "lower"],
) -> float:
    if target <= 0:
        return 1.0
    if direction == "lower":
        if value <= target:
            return 1.0
        return _clamp(target / value, 0.0, 1.0)
    return _clamp(value / target, 0.0, 1.0)


def compute_prelaunch_text_assessment(
    dimension_scores: Mapping[str, Any],
    *,
    evidence: Mapping[str, str] | None = None,
) -> PrelaunchTextAssessment:
    """Compute the report's 100-point text maturity score.

    Scores use the research report policy: each dimension is graded 1-5 and
    then converted by ``raw / 5 * weight``.
    """

    evidence = evidence or {}
    dimensions: list[PrelaunchDimensionScore] = []
    findings: list[RankingReadinessFinding] = []
    total = 0.0
    for dim in TEXT_SCORE_DIMENSIONS:
        missing = dim.key not in dimension_scores
        raw = _raw_score(dimension_scores.get(dim.key), default=1.0)
        weighted = raw / 5.0 * dim.weight
        total += weighted
        dimensions.append(
            PrelaunchDimensionScore(
                key=dim.key,
                label=dim.label,
                weight=dim.weight,
                raw_score=raw,
                weighted_score=weighted,
                high_score_standard=dim.high_score_standard,
                evidence=evidence.get(dim.key, ""),
            )
        )
        if missing:
            findings.append(
                RankingReadinessFinding(
                    code=f"{dim.key}_score_missing",
                    severity="medium",
                    scope=f"text.{dim.key}",
                    message=f"{dim.label}没有提供评分，已按最低成熟度计入。",
                    suggestion=_TEXT_REPAIR_ACTIONS[dim.key],
                )
            )
        elif raw < 3.0:
            findings.append(
                RankingReadinessFinding(
                    code=f"{dim.key}_weak",
                    severity="high" if raw < 2.5 else "medium",
                    scope=f"text.{dim.key}",
                    message=f"{dim.label}低于可商业化稳定线。",
                    suggestion=_TEXT_REPAIR_ACTIONS[dim.key],
                    evidence={"raw_score": raw, "weighted_score": round(weighted, 2)},
                )
            )
    return PrelaunchTextAssessment(
        score=round(total, 2),
        dimensions=tuple(dimensions),
        findings=tuple(findings),
    )


def compute_reader_behavior_assessment(
    metrics: Mapping[str, Any] | None,
) -> ReaderBehaviorAssessment:
    """Compute the report's post-launch behavior score.

    Rates accept either ratios (``0.65``) or percentages (``65``). Missing
    modules are not scored until data exists; they are surfaced as findings so
    product teams can wire the funnel without unfairly penalizing pre-launch
    projects.
    """

    metrics = metrics or {}
    modules: list[BehaviorModuleScore] = []
    findings: list[RankingReadinessFinding] = []
    weighted_total = 0.0
    available_weight = 0.0

    for module_key, (module_label, module_weight, specs) in BEHAVIOR_MODULES.items():
        metric_results: list[BehaviorMetricResult] = []
        ratios: list[float] = []
        for spec in specs:
            value = (
                _normalize_rate(metrics.get(spec.key))
                if spec.scale == "rate"
                else _normalize_amount(metrics.get(spec.key))
            )
            ratio = (
                None
                if value is None
                else _ratio_to_target(value, target=spec.target, direction=spec.direction)
            )
            passed = None if ratio is None else ratio >= 1.0
            metric_results.append(
                BehaviorMetricResult(
                    key=spec.key,
                    label=spec.label,
                    value=value,
                    target=spec.target,
                    score_ratio=ratio,
                    passed=passed,
                )
            )
            if ratio is not None:
                ratios.append(ratio)
                if ratio < 0.85:
                    findings.append(
                        RankingReadinessFinding(
                            code=(
                                f"{spec.key}_above_target"
                                if spec.direction == "lower"
                                else f"{spec.key}_below_target"
                            ),
                            severity="high" if ratio < 0.65 else "medium",
                            scope=f"behavior.{module_key}.{spec.key}",
                            message=f"{spec.label}没有达到榜单级观察值。",
                            suggestion=_behavior_suggestion(spec.key),
                            evidence={
                                "value": round(value, 4),
                                "target": spec.target,
                                "score_ratio": round(ratio, 4),
                            },
                        )
                    )
        module_score = None
        if ratios:
            module_score = round(sum(ratios) / len(ratios) * 100.0, 2)
            weighted_total += module_score * module_weight
            available_weight += module_weight
        else:
            findings.append(
                RankingReadinessFinding(
                    code=f"{module_key}_behavior_data_missing",
                    severity="info",
                    scope=f"behavior.{module_key}",
                    message=f"{module_label}还没有可用数据。",
                    suggestion="上线后补齐漏斗埋点，不用主观编辑判断替代真实读者行为。",
                )
            )
        modules.append(
            BehaviorModuleScore(
                key=module_key,
                label=module_label,
                weight=module_weight,
                score=module_score,
                metrics=tuple(metric_results),
            )
        )

    score = None if available_weight <= 0 else round(weighted_total / available_weight, 2)
    return ReaderBehaviorAssessment(
        score=score,
        modules=tuple(modules),
        findings=tuple(findings),
    )


def _behavior_suggestion(metric_key: str) -> str:
    suggestions = {
        "first_chapter_completion_rate": (
            "重写首章入口：用主角选择、即时危险和明确利益替代背景说明。"
        ),
        "first_three_arrival_rate": "前三章完成立人、立局、立钩、立风格，避免世界观介绍拖慢。",
        "bookshelf_rate": "增强章节尾承诺和短期任务，让读者相信继续读会有明确回报。",
        "chapter_10_arrival_rate": "前十章至少安排三次升级节点和一次不可逆转折。",
        "seven_day_follow_rate": "提高追更节奏稳定性，每3-5章有小回收，每8-12章有中型反转。",
        "break_sensitivity_rate": "补强断更后的回流钩子和上章回收，降低追更中断损失。",
        "first_pay_conversion_rate": "把付费点放在第一次不可逆抉择或真正进入主任务之后。",
        "paid_retention_rate": "付费后立即交付新信息、新代价或新胜利，避免收费后铺垫。",
        "arppu": "检查付费章节密度、更新频率和中段回报，不要只靠开篇刺激。",
        "comment_rate": "增加可讨论的人设标签、争议选择、关系变化和名场面。",
        "share_rate": "准备更清晰的一句话钩子和短视频化冲突片段。",
        "role_topic_rate": "补角色声纹、标签和关系张力，让读者能围绕人物讨论。",
        "derivative_creation_rate": "补视觉母题、名场面和可复用世界规则。",
    }
    return suggestions.get(metric_key, "复核该指标对应的读者回报链。")


def maturity_tier(score: float) -> Tier:
    if score >= 85.0:
        return "flagship"
    if score >= 78.0:
        return "strong_project"
    if score >= 70.0:
        return "vertical_viable"
    return "immature"


def _coerce_external_gate_findings(
    findings: Sequence[Mapping[str, Any] | RankingReadinessFinding] | None,
) -> tuple[RankingReadinessFinding, ...]:
    result: list[RankingReadinessFinding] = []
    for item in findings or ():
        if isinstance(item, RankingReadinessFinding):
            result.append(item)
            continue
        if not isinstance(item, Mapping):
            continue
        code = _clean_text(item.get("code"))
        if not code:
            continue
        severity = _clean_text(item.get("severity")).lower()
        if severity not in {"critical", "high", "medium", "low", "info"}:
            severity = "medium"
        result.append(
            RankingReadinessFinding(
                code=f"external_gate_{code}",
                severity=severity,  # type: ignore[arg-type]
                scope=_clean_text(item.get("scope")) or "external_gate",
                message=_clean_text(item.get("message") or item.get("detail"))
                or "External gate finding.",
                suggestion=_clean_text(item.get("suggestion"))
                or "Resolve the gate finding before ranking promotion.",
                evidence=_model_dump(item.get("evidence")),
            )
        )
    return tuple(result)


def evaluate_ranking_readiness(
    *,
    project_slug: str | None = None,
    title: str | None = None,
    dimension_scores: Mapping[str, Any],
    dimension_evidence: Mapping[str, str] | None = None,
    behavior_metrics: Mapping[str, Any] | None = None,
    marketing_assets: Mapping[str, Any] | None = None,
    ip_readiness: Mapping[str, Any] | None = None,
    external_gate_findings: Sequence[Mapping[str, Any] | RankingReadinessFinding] | None = None,
) -> RankingReadinessReport:
    text = compute_prelaunch_text_assessment(
        dimension_scores,
        evidence=dimension_evidence,
    )
    behavior = compute_reader_behavior_assessment(behavior_metrics)
    if behavior.score is None:
        maturity_score = text.score
        scoring_basis: Literal["text_only", "text_plus_behavior"] = "text_only"
    else:
        maturity_score = round(text.score * 0.60 + behavior.score * 0.40, 2)
        scoring_basis = "text_plus_behavior"
    external_findings = _coerce_external_gate_findings(external_gate_findings)
    gate_severities = {finding.severity for finding in external_findings}
    if "critical" in gate_severities:
        maturity_score = min(maturity_score, 69.0)
    elif "high" in gate_severities:
        maturity_score = min(maturity_score, 77.0)
    tier = maturity_tier(maturity_score)
    findings = [*text.findings, *behavior.findings, *external_findings]
    if tier == "immature":
        findings.append(
            RankingReadinessFinding(
                code="maturity_below_platform_bar",
                severity="high",
                scope="ranking_readiness",
                message="综合成熟度低于可做垂类项目线。",
                suggestion="优先重写人物、开篇钩子和升级结构，再考虑投放或长篇生产。",
                evidence={"maturity_score": maturity_score},
            )
        )
    return RankingReadinessReport(
        project_slug=project_slug,
        title=title,
        maturity_score=maturity_score,
        tier=tier,
        passed=tier != "immature" and "critical" not in gate_severities,
        action=_TIER_ACTIONS[tier],
        scoring_basis=scoring_basis,
        text_assessment=text,
        behavior_assessment=behavior,
        findings=tuple(findings),
        productization_plan=_build_productization_plan(text, behavior, tier),
        marketing_assets=marketing_assets or {},
        ip_readiness=ip_readiness or {},
    )


def build_listing_marketing_asset_pack(
    profile: Mapping[str, Any],
    *,
    story_bible: Any | None = None,
) -> dict[str, Any]:
    """Create the 15s / 45s / 90s material layer recommended by the report."""

    language = _clean_text(profile.get("language"))
    is_en = language.lower().startswith("en")
    title = _clean_text(profile.get("primary_title")) or ("Untitled" if is_en else "未命名作品")
    logline = _compact_text(profile.get("logline") or profile.get("shelf_intro"))
    tags = _string_list(profile.get("tags"))
    reader_promise = _string_list(profile.get("reader_promise"))
    characters = [
        _clean_text(item.get("name"))
        for item in (profile.get("main_characters") or [])
        if isinstance(item, Mapping) and _clean_text(item.get("name"))
    ]
    rules = _story_rule_names(story_bible)
    first_character = characters[0] if characters else ("the protagonist" if is_en else "主角")
    tag_text = " / ".join(tags[:4]) if is_en else "、".join(tags[:4])
    promise_text = " / ".join(reader_promise[:3]) if is_en else "、".join(reader_promise[:3])
    rule_text = " / ".join(rules[:3]) if is_en else "、".join(rules[:3])

    if is_en:
        concept_script = (
            logline
            or f"{first_character} enters a high-pressure {tag_text} story."
        )
        world_script = (
            rule_text
            or tag_text
            or "the world forces visible consequences for every decision"
        )
        scripts = [
            {
                "duration_seconds": 15,
                "angle": "concept_hook",
                "script": f"{title}: {concept_script}",
            },
            {
                "duration_seconds": 45,
                "angle": "character_conflict",
                "script": (
                    f"Start with {first_character}, then push the core relationship pressure: "
                    f"{promise_text or 'every choice creates a sharper cost and a stronger hook'}."
                ),
            },
            {
                "duration_seconds": 90,
                "angle": "world_or_issue",
                "script": (
                    f"Explain the rule system or social question behind {title}: "
                    f"{world_script}."
                ),
            },
        ]
        slots = [
            "one-sentence concept",
            "character relationship conflict",
            "rule/world/issue explainer",
            "memorable scene read-aloud",
            "reader discussion prompt",
        ]
    else:
        concept_script = logline or f"{first_character}开局撞进{tag_text}高压局。"
        scripts = [
            {
                "duration_seconds": 15,
                "angle": "concept_hook",
                "script": f"《{title}》：{concept_script}",
            },
            {
                "duration_seconds": 45,
                "angle": "character_conflict",
                "script": (
                    f"先抛{first_character}的处境，再放大关系压力："
                    f"{promise_text or '每次选择都有代价，每章都给新的追读钩子'}。"
                ),
            },
            {
                "duration_seconds": 90,
                "angle": "world_or_issue",
                "script": (
                    f"拆《{title}》背后的世界规则或议题："
                    f"{rule_text or tag_text or '这个世界会让每个选择付出可见后果'}。"
                ),
            },
        ]
        slots = [
            "15秒概念钩子",
            "45秒角色关系冲突",
            "90秒世界观/议题亮点",
            "名场面朗读",
            "读者讨论题",
        ]

    return {
        "short_video_scripts": scripts,
        "material_slots": slots,
        "recommended_cadence": "launch + first-week serial reuse",
    }


def build_listing_ip_readiness(
    profile: Mapping[str, Any],
    *,
    story_bible: Any | None = None,
) -> dict[str, Any]:
    characters = [
        item
        for item in (profile.get("main_characters") or [])
        if isinstance(item, Mapping) and _clean_text(item.get("name"))
    ]
    tags = _string_list(profile.get("tags"))
    promo_copy = _string_list(profile.get("promo_copy"))
    locations = _story_location_names(story_bible)
    rules = _story_rule_names(story_bible)
    visual_motifs = _dedupe_texts(
        _string_list(profile.get("visual_motifs"))
        + locations[:4]
        + rules[:4]
        + tags[:4]
    )
    checks = [
        {
            "code": "character_tags",
            "passed": len(characters) >= 1,
            "message": "至少需要一个可被记住的核心角色标签。",
        },
        {
            "code": "visual_motifs",
            "passed": len(visual_motifs) >= 3,
            "message": "至少需要三个可视化母题、场景或规则。",
        },
        {
            "code": "promo_hooks",
            "passed": len(promo_copy) >= 3,
            "message": "至少需要三条可外投的宣传钩子。",
        },
    ]
    score = round(sum(1 for item in checks if item["passed"]) / len(checks) * 100)
    return {
        "score": score,
        "status": "ready" if score >= 100 else ("needs_attention" if score >= 67 else "blocked"),
        "checks": checks,
        "visual_motifs": visual_motifs[:8],
        "character_tags": [
            {
                "name": _clean_text(item.get("name")),
                "role": _clean_text(item.get("role")),
                "appeal": _clean_text(item.get("appeal") or item.get("identity")),
            }
            for item in characters[:8]
        ],
    }


def evaluate_project_ranking_readiness(
    project: Any,
    *,
    writing_profile: Any | None = None,
    story_bible: Any | None = None,
    listing_profile: Mapping[str, Any] | None = None,
    behavior_metrics: Mapping[str, Any] | None = None,
    scorecard_quality_score: float | None = None,
    premium_gate_score: float | None = None,
    external_gate_findings: Sequence[Mapping[str, Any] | RankingReadinessFinding] | None = None,
) -> RankingReadinessReport:
    listing = dict(listing_profile or {})
    scores, evidence = derive_project_dimension_scores(
        project,
        writing_profile=writing_profile,
        story_bible=story_bible,
        listing_profile=listing,
        scorecard_quality_score=scorecard_quality_score,
        premium_gate_score=premium_gate_score,
    )
    marketing_assets = build_listing_marketing_asset_pack(listing, story_bible=story_bible)
    ip_readiness = build_listing_ip_readiness(listing, story_bible=story_bible)
    return evaluate_ranking_readiness(
        project_slug=_clean_text(_get_value(project, "slug")) or None,
        title=_clean_text(_get_value(project, "title")) or None,
        dimension_scores=scores,
        dimension_evidence=evidence,
        behavior_metrics=behavior_metrics,
        marketing_assets=marketing_assets,
        ip_readiness=ip_readiness,
        external_gate_findings=external_gate_findings,
    )


def derive_project_dimension_scores(
    project: Any,
    *,
    writing_profile: Any | None = None,
    story_bible: Any | None = None,
    listing_profile: Mapping[str, Any] | None = None,
    scorecard_quality_score: float | None = None,
    premium_gate_score: float | None = None,
) -> tuple[dict[str, float], dict[str, str]]:
    metadata = _model_dump(_get_value(project, "metadata_json", {}))
    profile = _model_dump(writing_profile)
    story = _model_dump(story_bible)
    listing = dict(listing_profile or {})
    characters = _sequence(story.get("characters"))
    world_rules = _sequence(story.get("world_rules"))
    locations = _sequence(story.get("locations"))
    factions = _sequence(story.get("factions"))
    relationships = _sequence(story.get("relationships"))
    volume_frontiers = _sequence(story.get("volume_frontiers"))
    deferred_reveals = _sequence(story.get("deferred_reveals"))
    world_backbone = _model_dump(story.get("world_backbone"))

    scores: dict[str, float] = {}
    evidence: dict[str, str] = {}

    protagonist = _find_protagonist(characters)
    character_signals = sum(
        1
        for key in ("goal", "fear", "flaw", "arc_trajectory", "voice_profile", "moral_framework")
        if _non_empty(_get_value(protagonist, key))
    )
    fallback_character = sum(
        1
        for value in (
            _get_nested(profile, "character", "protagonist_archetype"),
            _get_nested(profile, "character", "protagonist_core_drive"),
            _get_nested(profile, "character", "golden_finger"),
        )
        if _non_empty(value)
    )
    scores["character"] = _score_from_count(character_signals, thresholds=(1, 2, 4, 5))
    if not characters and fallback_character:
        scores["character"] = max(scores["character"], 3.0 + min(1.0, fallback_character / 3))
    evidence["character"] = f"characters={len(characters)}, protagonist_signals={character_signals}"

    hook_signals = sum(
        1
        for value in (
            listing.get("logline"),
            listing.get("short_intro"),
            _get_nested(profile, "market", "opening_contract"),
            _get_nested(profile, "serialization", "first_three_chapter_goal"),
        )
        if len(_compact_text(value)) >= 20
    )
    hook_signals += 1 if len(_string_list(listing.get("promo_copy"))) >= 3 else 0
    scores["opening_hook"] = _score_from_count(hook_signals, thresholds=(1, 2, 3, 4))
    evidence["opening_hook"] = f"hook_signals={hook_signals}"

    structure_signals = sum(
        1
        for value in (
            _get_nested(profile, "serialization", "scene_drive_rule"),
            _get_nested(profile, "serialization", "chapter_ending_rule"),
            _get_nested(profile, "market", "payoff_rhythm"),
            metadata.get("whole_book_quality_report"),
        )
        if _non_empty(value)
    )
    if scorecard_quality_score is not None and scorecard_quality_score >= 80:
        structure_signals += 1
    scores["pacing_structure"] = _score_from_count(structure_signals, thresholds=(1, 2, 3, 4))
    evidence["pacing_structure"] = (
        f"structure_signals={structure_signals}, scorecard={scorecard_quality_score}"
    )

    conflict_signals = sum(
        1
        for value in (
            _get_value(project, "dramatic_question"),
            _get_nested(profile, "market", "chapter_hook_strategy"),
            _get_nested(profile, "character", "antagonist_mode"),
            metadata.get("reader_promise"),
            listing.get("reader_promise"),
        )
        if _non_empty(value)
    )
    if relationships:
        conflict_signals += 1
    scores["conflict_reversal"] = _score_from_count(conflict_signals, thresholds=(1, 2, 3, 4))
    evidence["conflict_reversal"] = f"conflict_signals={conflict_signals}"

    hard_rules = sum(
        1
        for item in world_rules
        if _non_empty(_get_value(item, "story_consequence"))
        or _non_empty(_get_value(item, "exploitation_potential"))
    )
    world_signals = sum(
        (
            bool(world_backbone),
            hard_rules >= 1,
            len(locations) >= 2,
            len(factions) >= 1,
        )
    )
    if premium_gate_score is not None and premium_gate_score >= 80:
        world_signals += 1
    scores["world_consistency"] = _score_from_count(world_signals, thresholds=(1, 2, 3, 4))
    evidence["world_consistency"] = (
        f"world_rules={len(world_rules)}, hard_rules={hard_rules}, "
        f"premium_gate={premium_gate_score}"
    )

    language_signals = sum(
        1
        for value in (
            _get_nested(profile, "style", "prose_style"),
            _get_nested(profile, "style", "sentence_style"),
            _get_nested(profile, "style", "tone_keywords"),
            _get_nested(profile, "style", "info_density"),
            _get_nested(profile, "style", "custom_rules"),
        )
        if _non_empty(value)
    )
    scores["language_voice"] = _score_from_count(language_signals, thresholds=(1, 2, 3, 4))
    evidence["language_voice"] = f"language_signals={language_signals}"

    theme_signals = sum(
        1
        for value in (
            _get_value(project, "theme_statement"),
            _get_value(project, "dramatic_question"),
            world_backbone.get("thematic_melody"),
            world_backbone.get("core_promise"),
        )
        if _non_empty(value)
    )
    scores["theme_depth"] = _score_from_count(theme_signals, thresholds=(1, 2, 3, 4))
    evidence["theme_depth"] = f"theme_signals={theme_signals}"

    selling_signals = sum(
        (
            len(_compact_text(listing.get("logline"))) >= 20,
            len(_string_list(listing.get("tags"))) >= 5,
            len(_sequence(listing.get("title_candidates"))) >= 20,
            len(_string_list(listing.get("reader_promise"))) >= 2,
        )
    )
    scores["original_selling_point"] = _score_from_count(
        selling_signals,
        thresholds=(1, 2, 3, 4),
    )
    evidence["original_selling_point"] = f"selling_signals={selling_signals}"

    closure_signals = sum(
        (
            bool(volume_frontiers),
            bool(deferred_reveals),
            _non_empty(metadata.get("volume_plan")),
            _non_empty(metadata.get("ending_plan")),
        )
    )
    scores["ending_closure"] = _score_from_count(closure_signals, thresholds=(1, 2, 3, 4))
    evidence["ending_closure"] = f"closure_signals={closure_signals}"

    marketing_assets = build_listing_marketing_asset_pack(listing, story_bible=story_bible)
    ip = build_listing_ip_readiness(listing, story_bible=story_bible)
    ip_signals = sum(
        (
            ip.get("score", 0) >= 67,
            len(locations) >= 2,
            len(characters) >= 2,
            len(marketing_assets.get("short_video_scripts", [])) >= 3,
        )
    )
    scores["ip_potential"] = _score_from_count(ip_signals, thresholds=(1, 2, 3, 4))
    evidence["ip_potential"] = f"ip_signals={ip_signals}, ip_score={ip.get('score')}"

    return scores, evidence


def _sequence(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, Sequence) and not isinstance(value, str | bytes):
        return list(value)
    return []


def _non_empty(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, Mapping):
        return bool(value)
    if isinstance(value, Sequence) and not isinstance(value, str | bytes):
        return bool(value)
    return True


def _find_protagonist(characters: Sequence[Any]) -> Any | None:
    for character in characters:
        role = _clean_text(_get_value(character, "role")).lower()
        if "protagonist" in role or "主角" in role:
            return character
    return characters[0] if characters else None


def _score_from_count(count: int, *, thresholds: tuple[int, int, int, int]) -> float:
    if count >= thresholds[3]:
        return 5.0
    if count >= thresholds[2]:
        return 4.0
    if count >= thresholds[1]:
        return 3.0
    if count >= thresholds[0]:
        return 2.0
    return 1.0


def _story_rule_names(story_bible: Any | None) -> list[str]:
    story = _model_dump(story_bible)
    return _dedupe_texts(
        [
            _get_value(item, "name")
            for item in _sequence(story.get("world_rules"))
            if _get_value(item, "name")
        ]
    )


def _story_location_names(story_bible: Any | None) -> list[str]:
    story = _model_dump(story_bible)
    return _dedupe_texts(
        [
            _get_value(item, "name")
            for item in _sequence(story.get("locations"))
            if _get_value(item, "name")
        ]
    )


def _build_productization_plan(
    text: PrelaunchTextAssessment,
    behavior: ReaderBehaviorAssessment,
    tier: Tier,
) -> dict[str, Any]:
    weak_dimensions = [
        item.key
        for item in text.dimensions
        if item.raw_score < 3.5
    ]
    missing_behavior_modules = [
        item.key
        for item in behavior.modules
        if item.score is None
    ]
    return {
        "tier_action": _TIER_ACTIONS[tier],
        "立项看板": [
            "一句话概念",
            "目标读者",
            "主角欲望与缺口",
            "前3章任务",
            "前10章关键事件",
            "世界规则",
            "主要卖点",
            "付费节点建议",
            "风险判断",
        ],
        "优先修复维度": weak_dimensions,
        "试读漏斗": {
            "required_metrics": [
                "first_chapter_completion_rate",
                "first_three_arrival_rate",
                "bookshelf_rate",
            ],
            "missing_modules": missing_behavior_modules,
        },
        "连载监控": [
            "chapter_10_arrival_rate",
            "seven_day_follow_rate",
            "break_sensitivity_rate",
            "first_pay_conversion_rate",
        ],
        "营销素材中台": [
            "15秒概念钩子",
            "45秒角色关系冲突",
            "90秒世界观/议题亮点",
            "名场面朗读",
            "读者讨论题",
        ],
        "IP_readiness": ["角色视觉标签", "可复用世界规则", "至少三个名场面", "关系讨论结构"],
    }
