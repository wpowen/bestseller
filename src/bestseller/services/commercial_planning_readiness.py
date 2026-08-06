"""Commercial planning readiness gate for long-form serial fiction.

This gate sits between outline materialization and prose generation.  The
existing Qimao planning gate validates the opening promise stored in project
metadata; this module validates that the chapter and scene plans actually
implement that promise in the first three chapters.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from functools import lru_cache
import json
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ScenePlanProbe:
    scene_number: int
    scene_type: str = ""
    title: str = ""
    participants: tuple[str, ...] = ()
    purpose: str = ""
    entry_state: str = ""
    exit_state: str = ""
    hook_requirement: str = ""
    contract_context: str = ""


@dataclass(frozen=True)
class ChapterPlanProbe:
    chapter_number: int
    title: str = ""
    chapter_goal: str = ""
    opening_situation: str = ""
    main_conflict: str = ""
    hook_description: str = ""
    hype_type: str = ""
    hype_intensity: float | None = None
    contract_context: str = ""
    scenes: tuple[ScenePlanProbe, ...] = ()


@dataclass(frozen=True)
class CommercialPlanningFinding:
    code: str
    severity: str
    message: str
    scope: str
    chapter_no: int | None = None
    scene_no: int | None = None
    evidence: dict[str, Any] = field(default_factory=dict)
    suggestion: str = ""


@dataclass(frozen=True)
class CommercialPlanningReadinessReport:
    passed: bool
    findings: tuple[CommercialPlanningFinding, ...]
    strong_golden_hype_chapters: int
    golden_three_hooked_chapters: int
    golden_three_external_pressure_chapters: int
    checked_chapters: tuple[int, ...]


_LONG_SERIAL_REQUIRED_ARTIFACTS: tuple[str, ...] = (
    "story-bible/series-brief.md",
    "story-bible/reader-desire-map.md",
    "story-bible/series-bible.md",
    "story-bible/continuity-ledger.md",
    "story-bible/batch-queue.csv",
    "story-bible/volume-plan.csv",
)

_INSTITUTIONAL_PRESSURE_TERMS: tuple[str, ...] = (
    "巡查",
    "排查",
    "查验",
    "审核",
    "审查",
    "稽核",
    "稽查",
    "复核",
    "冻结",
    "扣减",
    "削减",
    "逐出",
    "清退",
    "没收",
    "断供",
    "收紧",
    "限制",
    "禁入",
    "封禁",
    "追责",
    "撤销",
    "取消资格",
    "收回权限",
    "停用",
    "停发",
    "停供",
    "inspection",
    "audit",
    "review",
    "freeze",
    "suspend",
    "revoke",
    "expel",
    "restrict",
)

_ENVIRONMENTAL_PRESSURE_TERMS: tuple[str, ...] = (
    "坍塌",
    "塌方",
    "洪水",
    "火势",
    "暴雨",
    "风暴",
    "缺氧",
    "断电",
    "断粮",
    "泄漏",
    "失控",
    "过载",
    "锁死",
    "封闭",
    "下沉",
    "collapse",
    "flood",
    "wildfire",
    "storm",
    "outage",
    "leak",
    "overload",
)

_EXTERNAL_COUNTER_FORCE_TERMS: tuple[str, ...] = (
    *_INSTITUTIONAL_PRESSURE_TERMS,
    *_ENVIRONMENTAL_PRESSURE_TERMS,
)

_CONCRETE_PRESSURE_TERMS: tuple[str, ...] = (
    *_EXTERNAL_COUNTER_FORCE_TERMS,
    "逼",
    "否则",
    "威胁",
    "抢",
    "夺",
    "拦",
    "押",
    "抓",
    "追",
    "杀",
    "烧",
    "灭口",
    "封锁",
    "扣押",
    "当场",
    "现场",
    "结案",
    "证据",
    "尸体",
    "验尸",
    "凶手",
    "官府",
    "管家",
    "道士",
    "鬼",
    "魂",
    "损失",
    "代价",
    "暴露",
    "反制",
    "揭穿",
    # ──────────────────────────────────────────────────────────────
    # 2026-05-26 — 民俗驱魔 / 悬疑侦探 / 心理博弈 类 main_conflict 张力词。
    # 之前 ch51/52 等以"试探/暗藏/怀疑/信任 vs 警惕"为冲突主线时被判
    # ``abstract_chapter_conflict``。这类心理博弈+真伪辨别的张力在悬疑/驱魔
    # 题材里就是合格的章节冲突，不应被当成"过于抽象"。
    # ──────────────────────────────────────────────────────────────
    "试探",      # probing / testing — investigation tension
    "暗藏",      # hidden agenda
    "暗中",      # covert / behind-the-back
    "隐瞒",      # concealing
    "怀疑",      # suspicion
    "试图",      # attempting
    "诓骗",      # deceiving / luring
    "设局",      # setting a trap
    "圈套",      # snare / trap
    "陷阱",      # trap
    "假装",      # pretending
    "假意",      # false intent
    # 2026-06-25 去通用性污染：删 镜中/镜债（一本悬疑书的私货机制，被误标"genre
    # canonical"塞进通用压力词表，给每本书的商业压力分加权）。题材压力词由
    # _genre_pressure_terms 按题材注入，不在此硬编码单本书机制。
    "异象",      # supernatural sign
    "异动",      # anomalous motion
    "异常",      # abnormal
    "阵法",      # ritual circle / formation (genre)
    "法器",      # talisman / ritual artifact
    "阴阳",      # yin-yang vision (genre)
    "邪祟",      # malevolent spirits
    "诡异",      # uncanny
    "诡计",      # scheme / trick
    "盘问",      # interrogation
    "对峙",      # standoff
    "周旋",      # maneuver / sparring
    "试图",      # attempting (rep ok, frozenset dedupes)
    # ──────────────────────────────────────────────────────────────
    # 死亡 / 消除类 — 死去/死亡/先死 这类结局威胁属于最直接的压力词。
    # 镜局/入局/出局 属于"进入危险博弈"类型，同样是具体冲突信号。
    # ──────────────────────────────────────────────────────────────
    "死亡",      # death
    "死去",      # die / pass away
    "先死",      # die first (game-death rule)
    "会死",      # will die
    "濒死",      # near death
    "入局",      # enter the deadly game
    "出局",      # eliminated from game
    # 2026-06-25 去通用性污染：删 镜局（同上，单本书私货机制）
    "被杀",      # killed
    "遇害",      # murdered / harmed
    "夺命",      # life-taking
    "索命",      # demanding one's life
    "threat",
    "forced",
    "or else",
    "evidence",
    "killer",
    "attack",
    "chase",
    "blocked",
    "exposed",
    "probe",
    "deceit",
    "ruse",
    "trap",
    "suspect",
    "conceal",
    "interrogate",
    "standoff",
    "die",
    "death",
    "dead",
)

_LIVE_PRESSURE_TERMS: tuple[str, ...] = (
    *_EXTERNAL_COUNTER_FORCE_TERMS,
    "官府",
    "巡捕",
    "捕头",
    "上峰",
    "上司",
    "管家",
    "族叔",
    "掌门",
    "长老",
    "敌人",
    "凶手",
    "活人",
    "黑影",
    "幕后",
    "报案",
    "告发",
    "扣押",
    "递解",
    "封锁",
    "灭口",
    "抢走",
    "夺走",
    "逼他",
    "逼迫",
    "威胁",
    "leader",
    "officer",
    "superior",
    "killer",
    "enemy",
    "witness",
    "blackmail",
    "arrest",
    "confiscate",
    "silence",
)

_VISIBLE_LOSS_TERMS: tuple[str, ...] = (
    "否则",
    "失败",
    "失去",
    "丢",
    "会死",
    "杀死",
    "死亡",
    "烧掉",
    "毁掉",
    "抹掉",
    "灭口",
    "封存",
    "结案",
    "天亮",
    "日落",
    "期限",
    "倒计时",
    "两个时辰",
    "三天",
    "七天",
    "停职",
    "递解",
    "暴露",
    "冻结",
    "扣减",
    "削减",
    "逐出",
    "清退",
    "没收",
    "断供",
    "禁入",
    "封禁",
    "撤销",
    "取消资格",
    "收回权限",
    "停用",
    "停发",
    "停供",
    "坍塌",
    "塌方",
    "洪水",
    "火势",
    "缺氧",
    "断电",
    "断粮",
    "泄漏",
    "失控",
    "过载",
    "锁死",
    "封闭",
    "下沉",
    "证据被毁",
    "真凶脱身",
    "魂飞魄散",
    "or else",
    "deadline",
    "before dawn",
    "sunset",
    "lose",
    "destroyed",
    "burned",
    "case closed",
    "freeze",
    "suspend",
    "revoke",
    "expel",
    "restrict",
    "collapse",
    "flood",
    "outage",
    "leak",
    "overload",
)

_AGENCY_TERMS: tuple[str, ...] = (
    "反制",
    "逼问",
    "质问",
    "拒绝",
    "锁门",
    "藏起",
    "夺回",
    "抢回",
    "保住",
    "揭穿",
    "当场",
    "追",
    "查封",
    "立刻",
    "决定",
    "选择",
    "冒险",
    "赌",
    "交易",
    "放弃",
    "改用",
    "转用",
    "申诉",
    "提交",
    "拆除",
    "关闭",
    "切断",
    "撤离",
    "绕开",
    "公开",
    "取证",
    "保护",
    "counter",
    "refuse",
    "force",
    "choose",
    "risk",
    "protect",
    "expose",
)

_HOOK_TERMS: tuple[str, ...] = (
    "钩子",
    "悬念",
    "章尾",
    "下一章",
    "未解",
    "谁",
    "真相",
    "更深",
    "反转",
    "露出",
    "留下",
    "hook",
    "cliffhanger",
    "reveal",
    "mystery",
    "question",
)

_GENERIC_PLAN_TERMS: tuple[str, ...] = (
    "推进剧情",
    "推进主线",
    "推进真相揭示",
    "推动本章局势前进",
    "展开冲突",
    "承接上文",
    "引出下文",
    "完成本章目标",
    "advance the plot",
    "move the story forward",
    "advance the chapter spine",
)

_SOLO_PASSIVITY_TERMS: tuple[str, ...] = (
    "独自",
    "一个人",
    "回想",
    "思考",
    "整理",
    "查看",
    "调查",
    "完成尸检",
    "观察",
    "记录",
    "alone",
    "thinks",
    "observes",
    "investigates",
)

_BLOCKING_SEVERITIES = {"critical"}


def _text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, Mapping):
        return " ".join(_text(v) for v in value.values()).strip()
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return " ".join(_text(v) for v in value).strip()
    return str(value).strip()


def _jsonish_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    except TypeError:
        return _text(value)


def _string_tuple(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        stripped = value.strip()
        return (stripped,) if stripped else ()
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        result: list[str] = []
        for item in value:
            text = _text(item)
            if text:
                result.append(text)
        return tuple(result)
    text = _text(value)
    return (text,) if text else ()


def _contains_any(text: str, terms: tuple[str, ...]) -> bool:
    haystack = text.lower()
    return any(term.lower() in haystack for term in terms)


def _meaningful_participant_count(participants: Sequence[str]) -> int:
    return len({item.strip() for item in participants if item and item.strip()})


def _scene_text(scene: ScenePlanProbe) -> str:
    return " ".join(
        item
        for item in (
            scene.scene_type,
            scene.title,
            " ".join(scene.participants),
            scene.purpose,
            scene.entry_state,
            scene.exit_state,
            scene.hook_requirement,
            scene.contract_context,
        )
        if item
    )


def _chapter_text(chapter: ChapterPlanProbe) -> str:
    scene_block = " ".join(_scene_text(scene) for scene in chapter.scenes)
    return " ".join(
        item
        for item in (
            chapter.title,
            chapter.chapter_goal,
            chapter.opening_situation,
            chapter.main_conflict,
            chapter.hook_description,
            chapter.hype_type,
            chapter.contract_context,
            scene_block,
        )
        if item
    )


def _chapter_has_hook(chapter: ChapterPlanProbe) -> bool:
    if chapter.hook_description.strip():
        return True
    return any(scene.hook_requirement.strip() for scene in chapter.scenes)


def _chapter_has_external_pressure(chapter: ChapterPlanProbe) -> bool:
    if _contains_any(
        " ".join((chapter.main_conflict, chapter.contract_context)),
        _LIVE_PRESSURE_TERMS,
    ):
        return True
    for scene in chapter.scenes:
        if _meaningful_participant_count(scene.participants) >= 2:
            return True
        if _contains_any(_scene_text(scene), _LIVE_PRESSURE_TERMS):
            return True
    return False


def _chapter_is_solo_chain(
    chapter: ChapterPlanProbe,
    concrete_pressure_terms: tuple[str, ...] = _CONCRETE_PRESSURE_TERMS,
) -> bool:
    if not chapter.scenes:
        return False
    every_scene_solo = all(
        _meaningful_participant_count(scene.participants) <= 1
        for scene in chapter.scenes
    )
    if not every_scene_solo:
        return False
    scene_text = " ".join(_scene_text(scene) for scene in chapter.scenes)
    pressure_text = " ".join(
        (scene_text, chapter.main_conflict, chapter.contract_context)
    )
    # A first-three chapter that only has the protagonist "discovering clues"
    # is still low-pressure even when the clue text mentions a corpse, killer,
    # or evidence.  It needs an active counter-force or an explicit loss.
    if not _contains_any(pressure_text, _LIVE_PRESSURE_TERMS):
        return True
    if not _contains_any(pressure_text, _VISIBLE_LOSS_TERMS):
        return True
    if not _contains_any(pressure_text, concrete_pressure_terms):
        return True
    # Investigation/observation language is only passive when the same scene
    # lacks a decisive response.  Treating those words as an unconditional
    # veto misclassifies active appeals, refusals, rescues, and countermeasures.
    return _contains_any(scene_text, _SOLO_PASSIVITY_TERMS) and not _contains_any(
        scene_text, _AGENCY_TERMS
    )


def _chapter_has_generic_plan(chapter: ChapterPlanProbe) -> bool:
    return _contains_any(_chapter_text(chapter), _GENERIC_PLAN_TERMS)


def _chapter_has_visible_loss(chapter: ChapterPlanProbe) -> bool:
    return _contains_any(_chapter_text(chapter), _VISIBLE_LOSS_TERMS)


def _chapter_has_protagonist_agency(chapter: ChapterPlanProbe) -> bool:
    text = _chapter_text(chapter)
    if not _contains_any(text, _AGENCY_TERMS):
        return False
    protagonist_markers = ("主角", "她", "他", "protagonist")
    if _contains_any(text, protagonist_markers):
        return True
    # The readiness probe does not receive a separate cast manifest.  Scene
    # participants are therefore the authoritative, project-agnostic names we
    # can use to attribute an explicit action without hard-coding surnames.
    participant_names = {
        participant.strip()
        for scene in chapter.scenes
        for participant in scene.participants
        if participant and participant.strip()
    }
    return any(name in text for name in participant_names)


def _finding(
    code: str,
    severity: str,
    message: str,
    scope: str,
    *,
    chapter_no: int | None = None,
    scene_no: int | None = None,
    evidence: dict[str, Any] | None = None,
    suggestion: str = "",
) -> CommercialPlanningFinding:
    return CommercialPlanningFinding(
        code=code,
        severity=severity,
        message=message,
        scope=scope,
        chapter_no=chapter_no,
        scene_no=scene_no,
        evidence=evidence or {},
        suggestion=suggestion,
    )


def _package_artifact_findings(
    package_root: Path | None,
    *,
    target_chapters: int,
    long_serial_min_chapters: int,
) -> list[CommercialPlanningFinding]:
    if target_chapters < long_serial_min_chapters or package_root is None:
        return []
    if not package_root.exists():
        return [
            _finding(
                "commercial_package_root_missing",
                "high",
                "Long-form project has no output package directory yet.",
                "output_package",
                evidence={"package_root": str(package_root)},
                suggestion="Export the story bible/package before promotion, then rerun the gate.",
            )
        ]
    missing = [
        relative for relative in _LONG_SERIAL_REQUIRED_ARTIFACTS
        if not (package_root / relative).exists()
    ]
    if not missing:
        return []
    return [
        _finding(
            "long_serial_artifacts_missing",
            "critical",
            "Long-form commercial package is missing durable planning artifacts.",
            "output_package.story_bible",
            evidence={"missing": missing, "package_root": str(package_root)},
            suggestion=(
                "Generate/export the series brief, reader desire map, series bible, "
                "continuity ledger, batch queue, and volume plan before drafting/promotion."
            ),
        )
    ]


def chapter_plan_probe_from_mapping(value: Mapping[str, Any]) -> ChapterPlanProbe:
    scenes = tuple(
        scene_plan_probe_from_mapping(item)
        for item in value.get("scenes", []) or []
        if isinstance(item, Mapping)
    )
    hype_intensity: float | None
    try:
        hype_intensity = (
            float(value["hype_intensity"])
            if value.get("hype_intensity") is not None
            else None
        )
    except (TypeError, ValueError):
        hype_intensity = None
    return ChapterPlanProbe(
        chapter_number=int(value.get("chapter_number") or value.get("number") or 0),
        title=_text(value.get("title")),
        chapter_goal=_text(value.get("chapter_goal") or value.get("goal")),
        opening_situation=_text(value.get("opening_situation")),
        main_conflict=_text(value.get("main_conflict")),
        hook_description=_text(value.get("hook_description")),
        hype_type=_text(value.get("hype_type")),
        hype_intensity=hype_intensity,
        contract_context=_jsonish_text(
            {
                key: value.get(key)
                for key in ("causal_contract", "methodology_contract")
                if value.get(key)
            }
        ),
        scenes=scenes,
    )


def scene_plan_probe_from_mapping(value: Mapping[str, Any]) -> ScenePlanProbe:
    return ScenePlanProbe(
        scene_number=int(value.get("scene_number") or value.get("number") or 0),
        scene_type=_text(value.get("scene_type") or value.get("type")),
        title=_text(value.get("title")),
        participants=_string_tuple(value.get("participants")),
        purpose=_jsonish_text(value.get("purpose")),
        entry_state=_jsonish_text(value.get("entry_state")),
        exit_state=_jsonish_text(value.get("exit_state")),
        hook_requirement=_text(value.get("hook_requirement")),
        contract_context=_jsonish_text(
            {
                key: value.get(key)
                for key in (
                    "methodology_contract",
                    "action_sequence",
                    "concrete_goal",
                    "protagonist_state",
                    "cut_point",
                )
                if value.get(key)
            }
        ),
    )


@lru_cache(maxsize=64)
def _genre_pressure_terms(genre: str, sub_genre: str | None) -> tuple[str, ...]:
    """Genre-specific concrete-conflict vocabulary, pulled from the resolved genre
    review profile so a book's conflicts are scored against ITS genre's pressure
    words. Without this the shared ``_CONCRETE_PRESSURE_TERMS`` skews toward the one
    detective book it was hand-tuned on, and e.g. an 升级流 plan whose conflict is
    碾压/突破/夺宝/打脸 gets falsely flagged ``abstract_chapter_conflict``."""

    if not (genre or sub_genre):
        return ()
    try:
        from bestseller.services.genre_review_profiles import (
            resolve_genre_review_profile,
        )

        profile = resolve_genre_review_profile(genre or "", sub_genre)
        sk = profile.signal_keywords
        terms = {
            *sk.conflict_terms_zh,
            *sk.conflict_terms_en,
            *sk.hook_terms_zh,
            *sk.hook_terms_en,
        }
        return tuple(term for term in terms if term)
    except Exception:
        return ()


def _augmented_concrete_pressure_terms(
    genre: str | None, sub_genre: str | None
) -> tuple[str, ...]:
    return _CONCRETE_PRESSURE_TERMS + _genre_pressure_terms(genre or "", sub_genre)


def evaluate_commercial_planning_readiness(
    chapters: Sequence[ChapterPlanProbe | Mapping[str, Any]],
    *,
    target_chapters: int | None = None,
    package_root: str | Path | None = None,
    min_golden_hype_intensity: float = 7.0,
    min_strong_hype_chapters: int = 2,
    long_serial_min_chapters: int = 50,
    require_package_artifacts: bool = True,
    genre: str | None = None,
    sub_genre: str | None = None,
) -> CommercialPlanningReadinessReport:
    """Validate the first three chapter/scene plans against commercial needs.

    ``genre``/``sub_genre`` make the concrete-pressure check genre-aware so plans are
    scored against their own genre's conflict vocabulary, not one book's hand-tuned
    detective terms. Omitting them preserves the legacy (genre-neutral core) behaviour.
    """

    concrete_pressure_terms = _augmented_concrete_pressure_terms(genre, sub_genre)

    probes = tuple(
        chapter
        if isinstance(chapter, ChapterPlanProbe)
        else chapter_plan_probe_from_mapping(chapter)
        for chapter in chapters
    )
    by_number = {chapter.chapter_number: chapter for chapter in probes}
    golden = tuple(by_number[number] for number in (1, 2, 3) if number in by_number)
    target = int(target_chapters or 0)
    root = Path(package_root) if package_root is not None else None

    findings: list[CommercialPlanningFinding] = []
    if require_package_artifacts:
        findings.extend(
            _package_artifact_findings(
                root,
                target_chapters=target,
                long_serial_min_chapters=long_serial_min_chapters,
            )
        )

    if len(golden) < 3 and target >= 3:
        findings.append(
            _finding(
                "golden_three_plan_incomplete",
                "high",
                "First-three chapter plans are incomplete, so retention cannot be evaluated.",
                "chapters.1_3",
                evidence={
                    "present": [chapter.chapter_number for chapter in golden],
                    "required": [1, 2, 3],
                },
                suggestion="Materialize chapters 1-3 before entering commercial drafting.",
            )
        )

    strong_hype_count = 0
    hooked_count = 0
    external_pressure_count = 0

    for chapter in golden:
        chapter_no = chapter.chapter_number
        if not chapter.opening_situation.strip():
            findings.append(
                _finding(
                    "missing_opening_situation",
                    "critical",
                    "Golden-three chapter has no immediate opening situation.",
                    f"chapter.{chapter_no}.opening_situation",
                    chapter_no=chapter_no,
                    suggestion=(
                        "Start the chapter from an active incident, "
                        "not recap or atmosphere."
                    ),
                )
            )
        if not chapter.main_conflict.strip():
            findings.append(
                _finding(
                    "missing_main_conflict",
                    "critical",
                    "Golden-three chapter has no explicit main conflict.",
                    f"chapter.{chapter_no}.main_conflict",
                    chapter_no=chapter_no,
                    suggestion=(
                        "Name who pressures the protagonist, what they want, "
                        "and what is lost if they win."
                    ),
                )
            )
        elif not _contains_any(chapter.main_conflict, concrete_pressure_terms):
            # Fallback: check hook_description and scene text. When the planner
            # produces identical boilerplate for main_conflict/chapter_goal/
            # opening_situation (a known LLM planning failure mode), the hook
            # and scene-purpose fields often still encode concrete conflict
            # signals (e.g. 镜局启动/先死/老张死亡).  Only flag as abstract
            # when BOTH main_conflict AND the broader chapter context lack
            # concrete pressure terms.
            _hook_scene_text = " ".join(
                [chapter.hook_description]
                + [_scene_text(scene) for scene in chapter.scenes]
            )
            if not _contains_any(_hook_scene_text, concrete_pressure_terms):
                findings.append(
                    _finding(
                        "abstract_chapter_conflict",
                        "critical",
                        "Main conflict is too abstract for a commercial opening chapter.",
                        f"chapter.{chapter_no}.main_conflict",
                        chapter_no=chapter_no,
                        evidence={"main_conflict": chapter.main_conflict},
                        suggestion=(
                            "Rewrite the conflict around a concrete threat: person, "
                            "demand, deadline, evidence, loss."
                        ),
                    )
                )
        if not _chapter_has_visible_loss(chapter):
            findings.append(
                _finding(
                    "golden_three_visible_loss_missing",
                    "critical",
                    "Golden-three chapter lacks a concrete deadline or visible loss.",
                    f"chapter.{chapter_no}.stakes",
                    chapter_no=chapter_no,
                    suggestion=(
                        "Name what is destroyed, lost, exposed, or closed if "
                        "the protagonist fails in this chapter."
                    ),
                )
            )
        if not _chapter_has_protagonist_agency(chapter):
            findings.append(
                _finding(
                    "golden_three_protagonist_agency_missing",
                    "critical",
                    "Golden-three chapter does not require a decisive protagonist action.",
                    f"chapter.{chapter_no}.agency",
                    chapter_no=chapter_no,
                    suggestion=(
                        "Make the protagonist choose, refuse, bargain, expose, "
                        "protect, or counterattack on the page."
                    ),
                )
            )

        has_hook = _chapter_has_hook(chapter)
        if has_hook:
            hooked_count += 1
        else:
            findings.append(
                _finding(
                    "missing_chapter_hook_plan",
                    "critical",
                    "Golden-three chapter has no planned chapter/scene hook.",
                    f"chapter.{chapter_no}.hook",
                    chapter_no=chapter_no,
                    suggestion=(
                        "Give the chapter a visible unanswered question or reversal "
                        "that forces the next click."
                    ),
                )
            )

        if not chapter.scenes:
            findings.append(
                _finding(
                    "golden_three_scenes_missing",
                    "critical",
                    "Golden-three chapter has no scene cards.",
                    f"chapter.{chapter_no}.scenes",
                    chapter_no=chapter_no,
                    suggestion="Materialize scene cards before drafting the chapter.",
                )
            )
        else:
            last_scene = sorted(chapter.scenes, key=lambda item: item.scene_number)[-1]
            if not last_scene.hook_requirement.strip():
                findings.append(
                    _finding(
                        "golden_three_scene_hook_missing",
                        "high",
                        "Last scene lacks an explicit hook requirement.",
                        f"chapter.{chapter_no}.scenes.{last_scene.scene_number}.hook_requirement",
                        chapter_no=chapter_no,
                        scene_no=last_scene.scene_number,
                        suggestion=(
                            "Make the final scene carry a named reversal, reveal, "
                            "or unresolved danger."
                        ),
                    )
                )
            if _chapter_is_solo_chain(chapter, concrete_pressure_terms):
                findings.append(
                    _finding(
                        "golden_three_solo_scene_chain",
                        "critical",
                        "Golden-three scene chain is effectively solo and low-pressure.",
                        f"chapter.{chapter_no}.scenes",
                        chapter_no=chapter_no,
                        evidence={
                            "participant_counts": [
                                _meaningful_participant_count(scene.participants)
                                for scene in chapter.scenes
                            ],
                        },
                        suggestion=(
                            "Add an external pressure agent to at least one scene "
                            "and bind it to a visible loss."
                        ),
                    )
                )
            if _chapter_has_generic_plan(chapter):
                findings.append(
                    _finding(
                        "generic_golden_three_scene_plan",
                        "high",
                        "Golden-three plan still contains generic planner language.",
                        f"chapter.{chapter_no}",
                        chapter_no=chapter_no,
                        suggestion=(
                            "Replace generic planner language with concrete actions, "
                            "evidence, reversals, and costs."
                        ),
                    )
                )

        if _chapter_has_external_pressure(chapter):
            external_pressure_count += 1
        else:
            findings.append(
                _finding(
                    "golden_three_external_pressure_missing",
                    "critical",
                    "Golden-three chapter lacks an external pressure source.",
                    f"chapter.{chapter_no}",
                    chapter_no=chapter_no,
                    suggestion=(
                        "Put the protagonist against a named person/force "
                        "with a demand and deadline."
                    ),
                )
            )

        if chapter.hype_type.strip() and (
            chapter.hype_intensity is not None
            and chapter.hype_intensity >= min_golden_hype_intensity
        ):
            strong_hype_count += 1
        else:
            findings.append(
                _finding(
                    "weak_chapter_hype_assignment",
                    "high",
                    "Golden-three chapter lacks a strong assigned hype moment.",
                    f"chapter.{chapter_no}.hype",
                    chapter_no=chapter_no,
                    evidence={
                        "hype_type": chapter.hype_type,
                        "hype_intensity": chapter.hype_intensity,
                        "min_golden_hype_intensity": min_golden_hype_intensity,
                    },
                    suggestion=(
                        "Assign a concrete hype type and intensity >= 7.0 "
                        "for the opening chapters."
                    ),
                )
            )

    if golden and strong_hype_count < min_strong_hype_chapters:
        findings.append(
            _finding(
                "golden_three_hype_underpowered",
                "critical",
                "Too few first-three chapters have strong planned hype moments.",
                "chapters.1_3.hype",
                evidence={
                    "strong_hype_count": strong_hype_count,
                    "required": min_strong_hype_chapters,
                    "min_golden_hype_intensity": min_golden_hype_intensity,
                },
                suggestion=(
                    "Make at least two of chapters 1-3 carry a strong reversal, "
                    "reveal, win, or threat escalation."
                ),
            )
        )

    passed = not any(finding.severity in _BLOCKING_SEVERITIES for finding in findings)
    return CommercialPlanningReadinessReport(
        passed=passed,
        findings=tuple(findings),
        strong_golden_hype_chapters=strong_hype_count,
        golden_three_hooked_chapters=hooked_count,
        golden_three_external_pressure_chapters=external_pressure_count,
        checked_chapters=tuple(sorted(chapter.chapter_number for chapter in golden)),
    )


def commercial_planning_readiness_report_to_dict(
    report: CommercialPlanningReadinessReport,
) -> dict[str, Any]:
    return {
        "passed": report.passed,
        "strong_golden_hype_chapters": report.strong_golden_hype_chapters,
        "golden_three_hooked_chapters": report.golden_three_hooked_chapters,
        "golden_three_external_pressure_chapters": (
            report.golden_three_external_pressure_chapters
        ),
        "checked_chapters": list(report.checked_chapters),
        "findings": [
            {
                "code": finding.code,
                "severity": finding.severity,
                "message": finding.message,
                "scope": finding.scope,
                "chapter_no": finding.chapter_no,
                "scene_no": finding.scene_no,
                "evidence": finding.evidence,
                "suggestion": finding.suggestion,
            }
            for finding in report.findings
        ],
    }


__all__ = [
    "ChapterPlanProbe",
    "CommercialPlanningFinding",
    "CommercialPlanningReadinessReport",
    "ScenePlanProbe",
    "chapter_plan_probe_from_mapping",
    "commercial_planning_readiness_report_to_dict",
    "evaluate_commercial_planning_readiness",
    "scene_plan_probe_from_mapping",
]
