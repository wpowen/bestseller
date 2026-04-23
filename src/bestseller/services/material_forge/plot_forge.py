"""PlotForge — produces plot patterns (main/sub line) and scene templates."""

from __future__ import annotations

from bestseller.services.material_forge.base import BaseForge


class PlotForge(BaseForge):
    """Forge for narrative structure dimensions.

    Covers:
    * ``plot_patterns`` — structural templates for main story arcs and
      hidden sub-plots.  Includes act structure, core tension escalation,
      and climax trigger.
    * ``scene_templates`` — reusable scene skeletons (opening hook,
      face-slap reversal, emotional breakthrough, etc.) with emotional
      beats and pacing notes.

    PlotForge specifically avoids "obligatory scene" hard-coding — it
    produces *optional* templates that Planner can draw on, not mandated
    plot beats.  This directly addresses L4 雷同化 in the original
    root-cause analysis.
    """

    dimensions = ("plot_patterns", "scene_templates")
    target_per_dimension = 4

    system_instructions = """
    「情节结构锻造」专项指引：
    - plot_patterns 包含：act_structure（分幕结构）、core_tension（核心张力）、
      escalation_logic（升级逻辑）、climax_trigger（高潮触发器）、
      resolution_pattern（收尾模式）。
    - scene_templates 包含：scene_type（场景类型）、opening_hook（开场钩子）、
      emotional_beats（情绪节拍列表）、pacing_note（节奏说明）、
      optional_twist（可选反转）。
    - 特别注意：情节模板是「可选参考」，不是硬性约束；
      描述中不得出现"必须出现"等指令性语言。
    - 鼓励「反套路」情节模板：让熟悉的场景类型出现意料之外的走向。
    """

    content_schema_hint = (
        "plot_patterns: {act_structure, core_tension, escalation_logic, "
        "climax_trigger, resolution_pattern}; "
        "scene_templates: {scene_type, opening_hook, emotional_beats, "
        "pacing_note, optional_twist}"
    )
