"""PowerSystemForge — produces power_systems entries."""

from __future__ import annotations

from bestseller.services.material_forge.base import BaseForge


class PowerSystemForge(BaseForge):
    """Forge for cultivation / power system dimensions.

    Covers:
    * ``power_systems`` — cultivation tiers, core mechanics, bottleneck
      logic, upgrade triggers, and societal side-effects of the system.

    The goal is to produce power systems that feel structurally different
    from the typical "灵根/金丹/元婴" ladder — each system should have a
    novel *core mechanic* (what the practitioner actually does / trains),
    not just renamed tiers.
    """

    dimensions = ("power_systems",)
    target_per_dimension = 5

    system_instructions = """
    「功法体系锻造」专项指引：
    - 核心机制必须与"灵气/丹道/五行相生"区分；强调独特的修炼逻辑。
    - content_json 应包含：levels（5 级境界描述）、core_principle（核心修炼原理）、
      bottleneck_logic（瓶颈机制）、upgrade_triggers（晋升触发条件）、
      side_effects_on_society（体系对社会的影响）。
    - 每个体系至少有一个「反常识」设定，令读者耳目一新。
    """

    content_schema_hint = (
        "{levels: [{name, description}×5], core_principle, bottleneck_logic, "
        "upgrade_triggers, side_effects_on_society}"
    )
