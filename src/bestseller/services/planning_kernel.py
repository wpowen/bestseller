# ruff: noqa: RUF001
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

from bestseller.services.ranking_capability_profile import (
    load_ranking_capability_profile_text,
)

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


def build_project_planning_kernel(
    project: object | None = None,
    *,
    project_metadata: Mapping[str, object] | None = None,
    book_spec: Mapping[str, object] | None = None,
    world_spec: Mapping[str, object] | None = None,
    cast_spec: Mapping[str, object] | None = None,
    volume_plan: object | None = None,
    output_base_dir: str | Path | None = None,
) -> dict[str, object]:
    """Build a normalized planning contract from all available artifacts."""

    metadata = {
        **_project_metadata(project),
        **_as_mapping(project_metadata),
    }
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
    }
    actions: list[str] = []
    for finding in findings:
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
    output_base_dir: str | Path | None = None,
) -> dict[str, object]:
    """Persist kernel + readiness report into ``project.metadata_json``."""

    metadata = {
        **_project_metadata(project),
        **_as_mapping(project_metadata),
    }
    kernel = build_project_planning_kernel(
        project,
        project_metadata=metadata,
        book_spec=book_spec,
        world_spec=world_spec,
        cast_spec=cast_spec,
        volume_plan=volume_plan,
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
    setattr(project, "metadata_json", next_metadata)
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
