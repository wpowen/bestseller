"""DeviceForge — produces device_templates and thematic_motifs."""

from __future__ import annotations

from bestseller.services.material_forge.base import BaseForge


class DeviceForge(BaseForge):
    """Forge for props, artefacts, and symbolic motifs.

    Covers:
    * ``device_templates`` — named artefacts, golden fingers, sacred objects,
      or special abilities with a defined origin, power, cost/limit, and
      narrative role.
    * ``thematic_motifs``  — recurring symbolic elements (moon, scar,
      unfinished letter) that weave through the story, connecting scenes
      and reinforcing themes.

    DeviceForge runs last so it can reference world/power/character
    materials already forged, making the artefacts feel grounded in the
    project's unique setting.
    """

    dimensions = ("device_templates", "thematic_motifs")
    target_per_dimension = 3

    system_instructions = """
    「道具与意象锻造」专项指引：
    - device_templates 包含：origin（来源传说）、power（能力描述）、
      cost_or_limit（使用代价/限制）、narrative_role（叙事功能）、
      bonding_condition（绑定条件，可为空）。
    - thematic_motifs 包含：symbol（象征物）、meanings（象征含义列表）、
      scenes_it_appears（适合出现的场景类型）、emotional_resonance（情感共鸣点）。
    - 道具命名须避免「乾坤/混沌/太极」等已被过度使用的宏大词汇；
      鼓励从日常意象衍生出深邃内涵（如"一枚锈蚀的铜镜"）。
    """

    content_schema_hint = (
        "device_templates: {origin, power, cost_or_limit, narrative_role, bonding_condition}; "
        "thematic_motifs: {symbol, meanings, scenes_it_appears, emotional_resonance}"
    )
