from __future__ import annotations

# ruff: noqa: ANN401,RUF001
from collections.abc import Mapping, Sequence
import re
from typing import Any

from bestseller.domain.planning_readiness import (
    PlanningReadinessFinding,
    PlanningReadinessReport,
)
from bestseller.services.genre_neutral_signals import object_sensory_shortcut_hits
from bestseller.services.protagonist_decision_agent import (
    evaluate_protagonist_decision_protocol,
)

_VOLUME_REQUIRED_FIELDS = {
    "goal": ("goal", "volume_goal", "core_goal", "mission"),
    "obstacle": ("obstacle", "main_obstacle", "resistance"),
    "climax": ("climax", "volume_climax", "final_confrontation"),
    "resolution": ("resolution", "volume_resolution", "payoff"),
    "reveal_budget": ("reveal_budget", "reveal_schedule", "information_budget"),
    "chapter_count_target": ("chapter_count_target", "chapter_count", "chapters"),
}

_CHAPTER_REQUIRED_FIELDS = {
    "chapter_function": ("chapter_function", "chapter_event_role"),
    "conflict": ("main_conflict", "conflict", "core_conflict"),
    "protagonist_choice": ("protagonist_choice",),
    "visible_action": ("visible_action", "visible_action_or_reaction"),
    "cost": ("cost", "cost_or_tradeoff"),
    "gain_reveal": ("gain_or_reveal", "required_payoff"),
    "state_change": ("state_change",),
    "next_reader_desire": ("next_reader_desire", "hook_description"),
}

_SCENE_REQUIRED_FIELDS = {
    "story_task": ("story_task", "story", "purpose.story"),
    "emotion_task": ("emotion_task", "emotion", "purpose.emotion"),
    "participants": ("participants",),
    "entry_state": ("entry_state",),
    "exit_state": ("exit_state",),
    "conflict_stakes": ("conflict_stakes", "stakes"),
    "information_control": ("information_control", "information_control_mode"),
    "signature_image": ("signature_image",),
    "cut_point": ("cut_point", "breakpoint"),
    "target_word_count": ("target_word_count",),
}

_FRONT_REQUIRED_FIELDS = {
    "opening_pressure": ("opening_pressure", "opening_situation", "pressure"),
    "protagonist_flaw": ("protagonist_flaw", "human_flaw"),
    "payoff": ("payoff", "required_payoff", "gain_or_reveal"),
    "tail_hook": ("tail_hook", "hook_description", "next_reader_desire"),
}

_PHONE_OPENING_PATTERN = re.compile(r"(电话|手机|微信|短信|语音|来电)")
_IN_PERSON_OPENING_PATTERN = re.compile(r"(楼下|门口|电梯|走廊|屋里|现场|房间|巷口|楼道|进门|推门)")
_LATE_NIGHT_DELIVERY_PATTERN = re.compile(
    r"(快递|配送单|寄件单|运单|揽收|派送)[^。！？\n]{0,40}(23[:：]\d{2}|凌晨|深夜|半夜|子时)"
    r"|(?:23[:：]\d{2}|凌晨|深夜|半夜|子时)[^。！？\n]{0,40}(快递|配送单|寄件单|运单|揽收|派送)"
)
_IMPOSSIBLE_DELIVERY_MARKER = re.compile(r"(不可能|异常|伪造|自助柜|系统延迟|补录|死后|被改过|不是活人)")


def evaluate_planning_readiness(
    *,
    volume_plan: Mapping[str, Any] | Sequence[Mapping[str, Any]] | None = None,
    chapter_outlines: Sequence[Mapping[str, Any] | Any] = (),
    material_anchors: Sequence[str] = (),
    front_chapter_limit: int = 10,
) -> PlanningReadinessReport:
    blocking: list[PlanningReadinessFinding] = []
    audit: list[PlanningReadinessFinding] = []
    missing_keys: list[str] = []

    volume_entries: list[Mapping[str, Any]] = []
    if isinstance(volume_plan, Mapping):
        volume_entries = _mapping_list(volume_plan.get("volumes"))
        if not volume_entries:
            volume_entries = [volume_plan]
    elif isinstance(volume_plan, Sequence) and not isinstance(volume_plan, (str, bytes)):
        volume_entries = [item for item in volume_plan if isinstance(item, Mapping)]

    for index, volume in enumerate(volume_entries, start=1):
        for canonical, aliases in _VOLUME_REQUIRED_FIELDS.items():
            if _first_present(volume, aliases) is None:
                missing_keys.append(f"volume_plan[{index}].{canonical}")
                blocking.append(
                    PlanningReadinessFinding(
                        code="PLANNING_VOLUME_FIELD_MISSING",
                        severity="critical",
                        message=f"Volume {index} is missing {canonical}.",
                        path=f"volume_plan[{index - 1}].{canonical}",
                        repair_hint="补齐卷目标、阻力、高潮、解决、揭示预算和章节数量目标。",
                    )
                )

    for chapter_index, chapter in enumerate(chapter_outlines):
        chapter_no = _int_value(_get(chapter, "chapter_number", "chapter_no")) or chapter_index + 1
        chapter_contract = _mapping_or_empty(
            _get(chapter, "causal_contract", "event_cycle_contract", "methodology_contract")
        )
        chapter_source = _merged(chapter, chapter_contract)
        for canonical, aliases in _CHAPTER_REQUIRED_FIELDS.items():
            if _first_present(chapter_source, aliases) is None:
                missing_keys.append(f"chapters[{chapter_no}].{canonical}")
                blocking.append(
                    PlanningReadinessFinding(
                        code="PLANNING_CHAPTER_FIELD_MISSING",
                        severity="critical" if chapter_no <= front_chapter_limit else "high",
                        message=f"Chapter {chapter_no} lacks executable {canonical}.",
                        path=f"chapters[{chapter_index}].{canonical}",
                        repair_hint="章纲必须写清功能、冲突、选择、动作、代价、获得、状态变化和下一章欲望。",
                    )
                )

        methodology_contract = _mapping_or_empty(_get(chapter, "methodology_contract"))
        decision_protocol = _mapping_or_empty(
            methodology_contract.get("decision_protocol")
            or _get(chapter, "decision_protocol")
        )
        # Decision-protocol completeness is surfaced as ADVISORY here, never as a
        # deterministic hard block. Requiring the full first-person decision
        # protocol on every front chapter falsely killed legitimate short-form
        # paths (e.g. fanqie fallback segment outlines that carry no protocol) and
        # reproduced the "new gate kills good books" pattern. Decision intelligence
        # is still enforced at the LLM outline/chapter judge layer.
        decision_report = evaluate_protagonist_decision_protocol(
            decision_protocol,
            chapter_number=chapter_no,
            blocking=False,
        )
        for issue in (*decision_report.blocking_findings, *decision_report.audit_findings):
            finding = PlanningReadinessFinding(
                code=issue.code,
                severity=issue.severity,
                message=issue.message,
                path=f"chapters[{chapter_index}].{issue.path}",
                repair_hint=issue.repair_hint,
                blocking=issue.blocking,
            )
            if issue.blocking:
                blocking.append(finding)
            else:
                audit.append(finding)
        for field_name in decision_report.missing_fields:
            missing_keys.append(
                f"chapters[{chapter_no}].methodology_contract.decision_protocol.{field_name}"
            )

        if chapter_no <= front_chapter_limit:
            for canonical, aliases in _FRONT_REQUIRED_FIELDS.items():
                if _first_present(chapter_source, aliases) is None:
                    missing_keys.append(f"front_ten[{chapter_no}].{canonical}")
                    blocking.append(
                        PlanningReadinessFinding(
                            code="PLANNING_FRONT_TEN_FIELD_MISSING",
                            severity="critical",
                            message=f"Front-ten chapter {chapter_no} lacks {canonical}.",
                            path=f"chapters[{chapter_index}].{canonical}",
                            repair_hint="前十章必须明确开篇压力、主角破绽、阶段爽点/兑现和章尾钩子。",
                        )
                    )
            # Decision audit fields intentionally contain rejected options,
            # unknowns and contingency language.  They are evidence for the
            # decision judge, not planned story events; including them in
            # plausibility keyword scans creates self-hits (for example an
            # ``异常就撤退`` backup falsely legitimising an impossible delivery).
            chapter_text = _stringify(_without_decision_protocol(chapter))
            opening_text = _stringify(
                _first_present(chapter_source, ("opening_pressure", "opening_situation", "pressure"))
            )
            first_scene_text = _stringify(_first_scene(chapter))
            if (
                chapter_no <= 3
                and _PHONE_OPENING_PATTERN.search(opening_text)
                and not _IN_PERSON_OPENING_PATTERN.search(first_scene_text)
            ):
                blocking.append(
                    PlanningReadinessFinding(
                        code="PLANNING_WEAK_MEDIATED_OPENING",
                        severity="critical",
                        message=(
                            f"Chapter {chapter_no} golden-three opening is locked "
                            "to phone/message mediation instead of visible pressure."
                        ),
                        path=f"chapters[{chapter_index}].opening_situation",
                        repair_hint=(
                            "黄金三章优先用当面可见的压力开局：现场画面、物件异常"
                            "或面对面冲突，让事件发生在人物眼前而不是电话/消息里。"
                        ),
                    )
                )
            if (
                _LATE_NIGHT_DELIVERY_PATTERN.search(chapter_text)
                and not _IMPOSSIBLE_DELIVERY_MARKER.search(chapter_text)
            ):
                blocking.append(
                    PlanningReadinessFinding(
                        code="PLANNING_REAL_WORLD_PLAUSIBILITY_GAP",
                        severity="critical",
                        message=(
                            f"Chapter {chapter_no} uses late-night courier/delivery "
                            "logic without making it an impossible evidence clue."
                        ),
                        path=f"chapters[{chapter_index}].real_world_plausibility",
                        repair_hint="删除夜间寄送/揽收硬伤，或明确写成伪造、死后生成、自助柜补录、系统延迟等异常证据。",
                    )
                )
            if object_sensory_shortcut_hits(chapter_text) >= 3 and not _has_key(
                chapter_source,
                "object_signal_contract",
            ):
                blocking.append(
                    PlanningReadinessFinding(
                        code="PLANNING_OBJECT_SIGNAL_UNBOUNDED",
                        severity="critical",
                        message=(
                            f"Chapter {chapter_no} repeatedly uses object heat as "
                            "a shortcut without defining signal meaning and limits."
                        ),
                        path=f"chapters[{chapter_index}].object_signal_contract",
                        repair_hint="补齐物件信号契约，说明触发条件、含义、代价和不能做什么，并替换重复发烫。",
                    )
                )
            # Knowledge-boundary leaks (an ordinary character speaking specialist
            # rules) are book-specific and now enforced genre-neutrally by the
            # outline_commercial_judge ("认知边界" constraint), so the deterministic
            # check that hardcoded one detective book's cast + jargon was removed to
            # stop it leaking that cast into every project.

        scenes = _sequence_value(_get(chapter, "scenes", "scene_beats"))
        if not scenes:
            missing_keys.append(f"chapters[{chapter_no}].scenes")
            blocking.append(
                PlanningReadinessFinding(
                    code="PLANNING_CHAPTER_SCENES_MISSING",
                    severity="critical",
                    message=f"Chapter {chapter_no} has no executable scenes.",
                    path=f"chapters[{chapter_index}].scenes",
                    repair_hint="至少拆出一个带故事任务、情绪任务、冲突、图像和切点的场景卡。",
                )
            )
            continue
        for scene_index, scene in enumerate(scenes):
            scene_map = _mapping_or_empty(scene)
            purpose = _mapping_or_empty(scene_map.get("purpose"))
            methodology = _mapping_or_empty(scene_map.get("methodology_contract"))
            scene_source = _merged(scene_map, purpose, methodology)
            for canonical, aliases in _SCENE_REQUIRED_FIELDS.items():
                if _first_present(scene_source, aliases) is None:
                    missing_keys.append(
                        f"chapters[{chapter_no}].scenes[{scene_index + 1}].{canonical}"
                    )
                    blocking.append(
                        PlanningReadinessFinding(
                            code="PLANNING_SCENE_FIELD_MISSING",
                            severity="critical",
                            message=(
                                f"Chapter {chapter_no} scene {scene_index + 1} "
                                f"lacks {canonical}."
                            ),
                            path=f"chapters[{chapter_index}].scenes[{scene_index}].{canonical}",
                            repair_hint="场景卡必须能直接控制读者看到的人、事、物、冲突、信息释放、画面和断点。",
                        )
                    )

    if material_anchors and chapter_outlines:
        anchor_text = "\n".join(str(anchor) for anchor in material_anchors if str(anchor).strip())
        outline_text = "\n".join(_stringify(chapter) for chapter in chapter_outlines)
        if anchor_text and not any(anchor in outline_text for anchor in _anchor_terms(anchor_text)):
            audit.append(
                PlanningReadinessFinding(
                    code="PLANNING_MATERIAL_ANCHORS_NOT_REFERENCED",
                    severity="high",
                    message=(
                        "Project material anchors exist but are not visible in "
                        "chapter planning."
                    ),
                    path="chapter_outlines",
                    repair_hint="把项目题材锚、核心物件、规则或人物债务写进卷纲/章纲/场景卡。",
                    blocking=False,
                )
            )

    return PlanningReadinessReport.from_findings(
        blocking_findings=blocking,
        audit_findings=audit,
        missing_context_keys=missing_keys,
        metrics={
            "volume_count": len(volume_entries),
            "chapter_count": len(chapter_outlines),
            "material_anchor_count": len(material_anchors),
            "front_chapter_limit": front_chapter_limit,
        },
    )


def evaluate_chapter_outline_batch_planning_readiness(
    batch: Any,
    *,
    volume_plan: Mapping[str, Any] | Sequence[Mapping[str, Any]] | None = None,
    material_anchors: Sequence[str] = (),
) -> PlanningReadinessReport:
    raw_chapters: Any
    if isinstance(batch, Mapping):
        raw_chapters = batch.get("chapters")
    else:
        raw_chapters = getattr(batch, "chapters", None)
    if isinstance(raw_chapters, Mapping):
        chapter_sources = list(raw_chapters.values())
    elif isinstance(raw_chapters, Sequence) and not isinstance(raw_chapters, (str, bytes, bytearray)):
        chapter_sources = list(raw_chapters)
    else:
        chapter_sources = []

    chapter_payloads = [
        chapter.model_dump(mode="json", by_alias=True)
        if hasattr(chapter, "model_dump")
        else _mapping_or_empty(chapter)
        for chapter in chapter_sources
    ]
    return evaluate_planning_readiness(
        volume_plan=volume_plan,
        chapter_outlines=chapter_payloads,
        material_anchors=material_anchors,
    )


def _merged(*items: Any) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for item in items:
        if isinstance(item, Mapping):
            merged.update(item)
    return merged


def _first_present(source: Mapping[str, Any], aliases: Sequence[str]) -> Any:
    for alias in aliases:
        if "." in alias:
            current: Any = source
            for part in alias.split("."):
                if not isinstance(current, Mapping):
                    current = None
                    break
                current = current.get(part)
            if _is_meaningful(current):
                return current
            continue
        value = source.get(alias)
        if _is_meaningful(value):
            return value
    return None


def _get(source: Any, *names: str) -> Any:
    for name in names:
        if isinstance(source, Mapping) and name in source:
            return source.get(name)
        value = getattr(source, name, None)
        if value is not None:
            return value
    return None


def _without_decision_protocol(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json", by_alias=True)
    if isinstance(value, Mapping):
        return {
            str(key): _without_decision_protocol(item)
            for key, item in value.items()
            if str(key) != "decision_protocol"
        }
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_without_decision_protocol(item) for item in value]
    return value


def _first_scene(chapter: Any) -> Any:
    scenes = _sequence_value(_get(chapter, "scenes", "scene_beats"))
    return scenes[0] if scenes else None


def _front_hookish_text(chapter: Any) -> str:
    scenes = _sequence_value(_get(chapter, "scenes", "scene_beats"))
    parts = [
        _stringify(_get(chapter, "tail_hook", "hook_description", "next_reader_desire")),
    ]
    for scene in scenes[:4]:
        methodology = _mapping_or_empty(_get(scene, "methodology_contract"))
        parts.extend(
            [
                _stringify(_get(scene, "hook", "hook_requirement")),
                _stringify(methodology.get("cut_point") or methodology.get("breakpoint")),
            ]
        )
    return "\n".join(part for part in parts if part)


def _has_key(source: Mapping[str, Any], key: str) -> bool:
    return _is_meaningful(source.get(key))


def _is_meaningful(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (Sequence, Mapping)) and not isinstance(value, (bytes, bytearray)):
        return bool(value)
    return True


def _mapping_or_empty(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if hasattr(value, "model_dump"):
        payload = value.model_dump(mode="json")
        return dict(payload) if isinstance(payload, Mapping) else {}
    return {}


def _mapping_list(value: Any) -> list[Mapping[str, Any]]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [item for item in value if isinstance(item, Mapping)]
    return []


def _sequence_value(value: Any) -> list[Any]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return list(value)
    return []


def _int_value(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _stringify(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, Mapping):
        return "\n".join(f"{key}: {_stringify(item)}" for key, item in value.items())
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        return "\n".join(_stringify(item) for item in value)
    return str(value or "")


def _anchor_terms(text: str) -> tuple[str, ...]:
    terms = [part.strip() for part in text.replace("，", ",").replace("、", ",").split(",")]
    return tuple(term for term in terms if len(term) >= 2)[:20]
