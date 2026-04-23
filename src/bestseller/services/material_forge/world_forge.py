"""WorldForge — produces world_settings, factions, and locale_templates."""

from __future__ import annotations

from bestseller.services.material_forge.base import BaseForge


class WorldForge(BaseForge):
    """Forge for world-building dimensions.

    Covers:
    * ``world_settings`` — overarching world structure, civilisation type,
      cosmology, power ecology.
    * ``factions``        — organisations, sects, empires, companies; their
      goals, internal dynamics, and relationship to each other.
    * ``locale_templates``— landmark locations with atmosphere, hidden
      dangers, and story potential.
    """

    dimensions = ("world_settings", "factions", "locale_templates")
    target_per_dimension = 4

    system_instructions = """
    「世界观锻造」专项指引：
    - world_settings 需包含世界运行规律、能量体系与文明形态；
      避免直接复制"灵气/修真/仙侠传统"标准设定，融入独特的宇宙观元素。
    - factions 需包含组织目标、内部矛盾、与其他势力的关系；
      名称必须独特，不得使用"青云宗/天星阁"等已在库中高频出现的名字。
    - locale_templates 需有场景氛围、潜在危险、叙事用途三个维度。
    """

    content_schema_hint = (
        "world_settings: {cosmology, power_ecology, civilization_type, unique_rules}; "
        "factions: {goal, internal_conflict, external_relations, scale}; "
        "locales: {atmosphere, hidden_dangers, narrative_uses}"
    )
