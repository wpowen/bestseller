"""Render scene beat sheets into prose-writing prompt blocks."""

from __future__ import annotations

from bestseller.domain.scene_beat import SceneBeat, SceneBeatSheet


def render_scene_beat_sheet_block(
    sheet: SceneBeatSheet | None,
    *,
    language: str = "zh-CN",
) -> str:
    if sheet is None or not sheet.beats:
        return ""
    if language.lower().startswith("en"):
        return _render_en(sheet)
    return _render_zh(sheet)


def _render_zh(sheet: SceneBeatSheet) -> str:
    lines = ["【本场镜头脚本】", "请按以下镜头写连续正文，不要写镜头编号、标题、清单或说明。"]
    for beat in sheet.beats:
        lines.extend(_render_beat_zh(beat))
    lines.append("收尾铁律：最后一拍只写发生了什么，不写它意味着什么。")
    lines.append("正文不得出现章节边界词、设计术语、营销术语或创作说明。")
    return "\n".join(lines)


def _render_beat_zh(beat: SceneBeat) -> list[str]:
    low, high = beat.word_budget
    lines = [f"\n# 镜头 {beat.beat_id}（{low}-{high} 字）"]
    if beat.camera.location or beat.camera.time:
        lines.append(
            "位置/时间："
            + "，".join(v for v in (beat.camera.location, beat.camera.time) if v)
        )
    if beat.characters_present:
        lines.append("人物：" + "、".join(beat.characters_present))
    if beat.external_event:
        lines.append("看得到的事情：")
        lines.extend(f"- {item}" for item in beat.external_event)
    if beat.sensory_anchor:
        lines.append(
            "必须出现的感官："
            + " / ".join(f"{k}:{v}" for k, v in beat.sensory_anchor.items() if v)
        )
    if beat.dialogue_lines.count:
        lines.append(
            f"对白：最多 {beat.dialogue_lines.count} 句；"
            f"{beat.dialogue_lines.speaker or '角色'}的目的：{beat.dialogue_lines.intent}"
        )
    if beat.interior_reaction:
        lines.append("内心反应只作为表演方向，不得直接解释：")
        lines.extend(f"- {item}" for item in beat.interior_reaction)
    if beat.beat_payoff:
        lines.append("镜头结束前要让读者看见：")
        lines.extend(f"- {item}" for item in beat.beat_payoff)
    if beat.beat_type == "cliff":
        lines.append(f"收束形态：{_ending_format_zh(beat.ending_format)}")
    if beat.banned_devices or beat.dialogue_lines.forbidden_explicit:
        banned = list(beat.banned_devices) + list(beat.dialogue_lines.forbidden_explicit)
        lines.append("禁用：" + "；".join(dict.fromkeys(item for item in banned if item)))
    return lines


def _render_en(sheet: SceneBeatSheet) -> str:
    lines = ["[SCENE BEAT SHEET]", "Write continuous prose from these camera beats. Do not output beat labels, headings, lists, or notes."]
    for beat in sheet.beats:
        low, high = beat.word_budget
        lines.append(f"\n# Beat {beat.beat_id} ({low}-{high} words)")
        loc_time = ", ".join(v for v in (beat.camera.location, beat.camera.time) if v)
        if loc_time:
            lines.append(f"Location/time: {loc_time}")
        if beat.characters_present:
            lines.append("Characters: " + ", ".join(beat.characters_present))
        if beat.external_event:
            lines.append("Visible events:")
            lines.extend(f"- {item}" for item in beat.external_event)
        if beat.sensory_anchor:
            lines.append(
                "Sensory anchors: "
                + " / ".join(f"{k}:{v}" for k, v in beat.sensory_anchor.items() if v)
            )
        if beat.dialogue_lines.count:
            lines.append(
                f"Dialogue: at most {beat.dialogue_lines.count} lines; "
                f"{beat.dialogue_lines.speaker or 'speaker'} intent: {beat.dialogue_lines.intent}"
            )
        if beat.beat_type == "cliff":
            lines.append(f"Ending form: {beat.ending_format or 'action'}")
        if beat.banned_devices or beat.dialogue_lines.forbidden_explicit:
            banned = list(beat.banned_devices) + list(beat.dialogue_lines.forbidden_explicit)
            lines.append("Forbidden: " + "; ".join(dict.fromkeys(item for item in banned if item)))
    lines.append("Ending rule: the final beat states only what happens, never what it means.")
    lines.append("The prose must not contain chapter-boundary wording, design terms, marketing terms, or author notes.")
    return "\n".join(lines)


def _ending_format_zh(value: str | None) -> str:
    if value == "image":
        return "画面定格"
    if value == "reveal":
        return "揭示反转"
    return "动作落幕"


__all__ = ["render_scene_beat_sheet_block"]
