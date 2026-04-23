"""CharacterForge — produces character_archetypes and character_templates."""

from __future__ import annotations

from bestseller.services.material_forge.base import BaseForge


class CharacterForge(BaseForge):
    """Forge for character-related dimensions.

    Covers:
    * ``character_archetypes`` — role templates (not specific characters):
      the vengeful prodigy, the reluctant mentor, the corrupt bureaucrat.
      Defined by motivation + wound + growth arc + narrative function.
    * ``character_templates``  — concrete character starters with name,
      background, voice, and relationship potential.  Unlike archetypes,
      these are ready-to-use characters with distinct identities.

    **Anti-cloning rule**: names must be unique across *all* existing
    project characters.  CharacterForge checks the existing_materials
    passed in from WorldForge + PowerForge for any name conflicts and
    refuses to re-use them.
    """

    dimensions = ("character_archetypes", "character_templates")
    target_per_dimension = 4

    system_instructions = """
    「人物锻造」专项指引：
    - character_archetypes 是"人物模板"，不是具体角色；
      包含：motivation（驱动动机）、wound（创伤/弱点）、
      growth_arc（成长轨迹）、narrative_function（叙事功能）。
    - character_templates 是具体角色起点，包含：
      name（中文名，必须独特）、background（50字以内）、
      voice_style（说话风格）、relationship_potential（与主角关系可能性）。
    - 严禁复用库中已出现的高频名字（如"方域/林枫/叶辰"等）；
      所有名字必须有独特的字形/音韵，区别于标准爽文命名套路。
    """

    content_schema_hint = (
        "archetypes: {motivation, wound, growth_arc, narrative_function}; "
        "templates: {name, background, voice_style, relationship_potential}"
    )
