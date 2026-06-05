# ruff: noqa: ANN401, I001, RUF001
"""Deterministic scene-beat planning for anti-slop prose prompts.

The design goal is to keep abstract chapter contracts out of the prose-writing
prompt. This planner turns the current scene card and contract hints into
visible camera beats: place, people, physical events, sensory anchors, and an
in-scene ending.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
import re
from typing import Any

import yaml

from bestseller.domain.scene_beat import (
    BeatCamera,
    BeatDialoguePlan,
    SceneBeat,
    SceneBeatSheet,
)


_DEFAULT_BANNED_DEVICES = [
    "心理大段解释",
    "抽象群像列举",
    "提及本章/本卷/章末",
    "提及章节机制词或营销词",
    "总结这一场发生了什么",
]

_DEFAULT_FORBIDDEN_EXPLICIT = ["关心", "命运", "翻盘", "转向", "章节机制词"]


def build_scene_beat_sheet(
    *,
    chapter_number: int,
    scene_number: int,
    scene_title: str | None = None,
    scene_type: str | None = None,
    time_label: str | None = None,
    participants: Sequence[str] | None = None,
    chapter_goal: str | None = None,
    story_purpose: str | None = None,
    emotion_purpose: str | None = None,
    entry_state: Mapping[str, Any] | None = None,
    exit_state: Mapping[str, Any] | None = None,
    scene_contract: Mapping[str, Any] | None = None,
    chapter_contract: Mapping[str, Any] | None = None,
    word_target: int | None = None,
) -> SceneBeatSheet:
    """Create a small, stable beat sheet from local scene metadata.

    The output deliberately avoids design-language labels in prose-facing text.
    If no rich contract exists, it still produces useful camera instructions
    from scene title, purpose, state, and participants.
    """

    people = [str(p).strip() for p in (participants or []) if str(p).strip()]
    if not people:
        people = ["视角人物"]
    location = _location_from(scene_contract, entry_state) or (scene_title or "当前场景地点")
    time = str(time_label or _value_from(entry_state, "time") or "当前时间")
    per_beat_words = _word_budget(word_target)

    opening_events = _event_list(
        _value_from(entry_state, "visible")
        or _value_from(entry_state, "state")
        or _value_from(scene_contract, "entry")
        or scene_title
        or story_purpose
        or "人物进入当前处境"
    )
    action_events = _event_list(
        _value_from(scene_contract, "external_event")
        or _value_from(scene_contract, "turn")
        or _value_from(scene_contract, "conflict")
        or story_purpose
        or chapter_goal
        or "人物做出一个会改变局面的动作"
    )
    payoff_events = _event_list(
        _value_from(exit_state, "visible")
        or _value_from(exit_state, "state")
        or _value_from(scene_contract, "exit")
        or _value_from(chapter_contract, "ending")
        or "最后留下一个未完成的具体动作"
    )

    beats = [
        SceneBeat(
            beat_id=f"C{chapter_number:03d}-S{scene_number:02d}-B1",
            beat_type="opening",
            camera=BeatCamera(location=location, time=time, weather=_weather_hint(entry_state)),
            characters_present=people[:3],
            external_event=_visible_lines(
                opening_events,
                fallback="让读者先看见人物处境，而不是听解释",
            ),
            interior_reaction=_interior_lines(emotion_purpose, people[0]),
            sensory_anchor=_sensory_anchor(scene_title, entry_state),
            dialogue_lines=BeatDialoguePlan(
                count=1 if len(people) > 1 else 0,
                speaker=people[1] if len(people) > 1 else "",
                intent="试探、施压或误判视角人物",
                forbidden_explicit=list(_DEFAULT_FORBIDDEN_EXPLICIT),
            ),
            beat_payoff=[_payoff_line(story_purpose, "建立当前压力")],
            banned_devices=list(_DEFAULT_BANNED_DEVICES),
            word_budget=per_beat_words,
        ),
        SceneBeat(
            beat_id=f"C{chapter_number:03d}-S{scene_number:02d}-B2",
            beat_type=_middle_type(scene_type),
            camera=BeatCamera(location=location, time=time, weather=_weather_hint(entry_state)),
            characters_present=people[:3],
            external_event=_visible_lines(action_events, fallback="用一个可见动作推进冲突"),
            interior_reaction=_interior_lines(emotion_purpose, people[0]),
            sensory_anchor=_sensory_anchor(scene_title, scene_contract),
            dialogue_lines=BeatDialoguePlan(
                count=2 if len(people) > 1 else 0,
                speaker=people[0],
                intent="用潜台词回应压力，不解释真实动机",
                forbidden_explicit=list(_DEFAULT_FORBIDDEN_EXPLICIT),
            ),
            beat_payoff=[_payoff_line(story_purpose, "让冲突产生具体后果")],
            banned_devices=list(_DEFAULT_BANNED_DEVICES),
            word_budget=per_beat_words,
        ),
        SceneBeat(
            beat_id=f"C{chapter_number:03d}-S{scene_number:02d}-B3",
            beat_type="cliff",
            camera=BeatCamera(location=location, time=time, weather=_weather_hint(exit_state)),
            characters_present=people[:3],
            external_event=_visible_lines(
                payoff_events,
                fallback="章场收束在动作、画面或揭示的一帧",
            ),
            interior_reaction=_interior_lines(emotion_purpose, people[0]),
            sensory_anchor=_sensory_anchor(scene_title, exit_state),
            dialogue_lines=BeatDialoguePlan(
                count=1 if len(people) > 1 else 0,
                speaker=people[0],
                intent="只留下反应或短句，不解释意义",
                forbidden_explicit=list(_DEFAULT_FORBIDDEN_EXPLICIT),
            ),
            beat_payoff=["最后只写发生了什么，不写它意味着什么"],
            banned_devices=list(_DEFAULT_BANNED_DEVICES),
            word_budget=per_beat_words,
            ending_format=_ending_format(scene_type),
        ),
    ]
    return SceneBeatSheet(
        chapter_number=chapter_number,
        scene_number=scene_number,
        beats=beats,
    )


def persist_scene_beat_sheet(
    sheet: SceneBeatSheet,
    story_bible_dir: str | Path,
) -> Path:
    """Persist one scene beat sheet as an auditable story-bible YAML artifact."""

    output_dir = Path(story_bible_dir) / "scene-beats"
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"ch{sheet.chapter_number:04d}-s{sheet.scene_number:02d}.yaml"
    path.write_text(
        yaml.safe_dump(
            scene_beat_sheet_to_persisted_dict(sheet),
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return path


def scene_beat_sheet_to_persisted_dict(sheet: SceneBeatSheet) -> dict[str, Any]:
    first = sheet.beats[0]
    last = sheet.beats[-1]
    return {
        "chapter_no": sheet.chapter_number,
        "scene_no": sheet.scene_number,
        "opening_pattern": _opening_pattern(first),
        "camera_beats": [
            {
                "beat_id": beat.beat_id,
                "beat_type": beat.beat_type,
                "location": beat.camera.location,
                "time_anchor": beat.camera.time,
                "characters_present": list(beat.characters_present),
                "external_event": list(beat.external_event),
                "sensory_anchor": dict(beat.sensory_anchor),
                "beat_payoff": list(beat.beat_payoff),
            }
            for beat in sheet.beats
        ],
        "named_entities": _named_entities(sheet),
        "time_anchor": first.camera.time,
        "ending_hook_target": "; ".join(last.external_event),
    }


def load_persisted_scene_beat(path: str | Path) -> dict[str, Any]:
    loaded = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return loaded if isinstance(loaded, dict) else {}


def _word_budget(word_target: int | None) -> tuple[int, int]:
    if not word_target or word_target <= 0:
        return (350, 550)
    low = max(180, int(word_target * 0.25))
    high = max(low + 80, int(word_target * 0.45))
    return (low, high)


def _middle_type(scene_type: str | None) -> str:
    text = (scene_type or "").lower()
    if "dialog" in text or "conversation" in text or "对话" in text:
        return "dialogue"
    if "reveal" in text or "揭" in text:
        return "reveal"
    return "action"


def _ending_format(scene_type: str | None) -> str:
    text = (scene_type or "").lower()
    if "reveal" in text or "揭" in text:
        return "reveal"
    if "dialog" in text or "quiet" in text or "日常" in text:
        return "image"
    return "action"


def _value_from(payload: Mapping[str, Any] | None, key: str) -> Any:
    if not isinstance(payload, Mapping):
        return None
    value = payload.get(key)
    if value:
        return value
    for nested_key in ("summary", "goal", "description", "external", "visible_event"):
        value = payload.get(nested_key)
        if value:
            return value
    return None


def _location_from(*payloads: Mapping[str, Any] | None) -> str:
    for payload in payloads:
        if not isinstance(payload, Mapping):
            continue
        for key in ("location", "place", "setting", "地点", "场景"):
            value = payload.get(key)
            if value:
                return str(value)
    return ""


def _event_list(value: Any) -> list[str]:
    if isinstance(value, str):
        parts = [p.strip(" -\t") for p in value.replace("；", ";").split(";")]
        return [p for p in parts if p][:3]
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        return [str(item).strip() for item in value if str(item).strip()][:3]
    if isinstance(value, Mapping):
        return [f"{k}: {v}" for k, v in value.items() if v][:3]
    return [str(value)] if value else []


def _visible_lines(events: Sequence[str], *, fallback: str) -> list[str]:
    cleaned = [_sanitize_design_terms(event) for event in events if event.strip()]
    return cleaned[:3] or [fallback]


def _interior_lines(emotion_purpose: str | None, pov_name: str) -> list[str]:
    if not emotion_purpose:
        return [f"{pov_name}的反应只能通过手势、停顿、呼吸或短句表现"]
    return [
        f"{pov_name}: {_sanitize_design_terms(emotion_purpose)}"
        "（不要明说，用动作或短句表现）"
    ]


def _sensory_anchor(label: str | None, payload: Mapping[str, Any] | None) -> dict[str, str]:
    anchors: dict[str, str] = {}
    if isinstance(payload, Mapping):
        for key in ("smell", "touch", "sound", "sight"):
            value = payload.get(key)
            if value:
                anchors[key] = str(value)
    if not anchors:
        anchors = {
            "sight": label or "场景中最先进入视线的具体物件",
            "sound": "近处一个能打断沉默的声音",
            "touch": "人物身体接触到的冷、热、硬或疼",
        }
    return anchors


def _weather_hint(payload: Mapping[str, Any] | None) -> str:
    if not isinstance(payload, Mapping):
        return ""
    value = payload.get("weather") or payload.get("atmosphere")
    return str(value) if value else ""


def _payoff_line(purpose: str | None, fallback: str) -> str:
    return _sanitize_design_terms(purpose) if purpose else fallback


def _sanitize_design_terms(text: str | None) -> str:
    if not text:
        return ""
    cleaned = str(text)
    replacements = {
        "本章主线": "当前可见冲突",
        "主线": "当前冲突",
        "副线": "旁支动作",
        "长线": "未完成线索",
        "钩子": "未完成动作",
        "卖点": "可见吸引力",
        "承诺": "可见期待",
        "翻盘": "局面反转",
        "命运转向": "处境改变",
    }
    for old, new in replacements.items():
        cleaned = cleaned.replace(old, new)
    return cleaned


def _opening_pattern(beat: SceneBeat) -> str:
    text = "\n".join([beat.camera.time, beat.camera.location, *beat.external_event])
    if any(ch.isdigit() for ch in text) or any(term in text for term in ("时", "点", "分钟")):
        return "time_anchor"
    if any(
        term in text
        for term in ("证据", "物证", "线索", "痕迹", "凭证", "遗物", "现场", "尸体", "失踪")
    ):
        return "realistic_evidence"
    if beat.characters_present:
        return "character_pressure"
    return "environmental_pressure"


def _named_entities(sheet: SceneBeatSheet) -> list[str]:
    entities: list[str] = []
    for beat in sheet.beats:
        entities.extend(beat.characters_present)
        for value in (beat.camera.location, beat.camera.time):
            if value:
                entities.append(value)
        for event in beat.external_event:
            entities.extend(_extract_quoted_or_numbered_entities(event))
    return list(dict.fromkeys(item for item in entities if item))


_QUOTED_OR_NUMBERED_ENTITY = re.compile(
    r"[「『“”\"'《【]([^」』“”\"'》】\n]{1,12})[」』“”\"'》】]"  # quoted names/objects
    r"|((?<![0-9])\d{2,4}(?:室|号|栋|楼|区|层|街|路|班|队|期)?)"  # numbered identifiers
)


def _extract_quoted_or_numbered_entities(text: str) -> list[str]:
    """Pull quoted phrases and numbered identifiers out of beat text — genre-neutral.

    Previously this hardcoded one detective book's room numbers and props (303 / 青囊
    / 罗盘 …), so it extracted nothing for any other book. Now it actually does what
    its name says, for any genre."""

    entities: list[str] = []
    for match in _QUOTED_OR_NUMBERED_ENTITY.finditer(text or ""):
        entity = (match.group(1) or match.group(2) or "").strip()
        if entity:
            entities.append(entity)
    return list(dict.fromkeys(entities))


__all__ = [
    "build_scene_beat_sheet",
    "load_persisted_scene_beat",
    "persist_scene_beat_sheet",
    "scene_beat_sheet_to_persisted_dict",
]
