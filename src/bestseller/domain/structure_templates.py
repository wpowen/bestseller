"""Story structure framework templates.

Provides 5 classic narrative structure templates that anchor beat generation
and pacing curves during the narrative graph materialization phase.

Each template defines a sequence of structural beats with their expected
position (as a fraction of the total chapter count), beat kind, and
tension range.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class StructureBeat(BaseModel, frozen=True):
    beat_name: str = Field(min_length=1)
    position_pct: float = Field(ge=0.0, le=1.0)
    beat_kind: str = Field(min_length=1)
    description: str = Field(min_length=1)
    tension_range: tuple[float, float] = Field(default=(0.3, 0.7))


class StructureTemplate(BaseModel, frozen=True):
    key: str = Field(min_length=1)
    name: str = Field(min_length=1)
    beats: list[StructureBeat] = Field(min_length=1)


# ---------------------------------------------------------------------------
# Factory functions
# ---------------------------------------------------------------------------


def get_three_act_template() -> StructureTemplate:
    return StructureTemplate(
        key="three-act",
        name="Three-Act Structure",
        beats=[
            StructureBeat(
                beat_name="Hook",
                position_pct=0.0,
                beat_kind="hook",
                description="Opening scene that grabs the reader with a compelling question or event.",
                tension_range=(0.20, 0.35),
            ),
            StructureBeat(
                beat_name="Inciting Incident",
                position_pct=0.10,
                beat_kind="inciting_incident",
                description="The event that disrupts the protagonist's ordinary world and sets the story in motion.",
                tension_range=(0.30, 0.50),
            ),
            StructureBeat(
                beat_name="First Plot Point",
                position_pct=0.25,
                beat_kind="plot_point",
                description="The protagonist commits to the central conflict; no turning back.",
                tension_range=(0.45, 0.60),
            ),
            StructureBeat(
                beat_name="Midpoint",
                position_pct=0.50,
                beat_kind="midpoint",
                description="A major revelation or reversal that raises the stakes and shifts the protagonist's approach.",
                tension_range=(0.60, 0.80),
            ),
            StructureBeat(
                beat_name="Second Plot Point",
                position_pct=0.75,
                beat_kind="plot_point",
                description="The darkest moment or major setback that forces the protagonist toward the climax.",
                tension_range=(0.75, 0.90),
            ),
            StructureBeat(
                beat_name="Climax",
                position_pct=0.90,
                beat_kind="climax",
                description="The final confrontation where the central conflict is resolved.",
                tension_range=(0.90, 0.99),
            ),
            StructureBeat(
                beat_name="Resolution",
                position_pct=0.97,
                beat_kind="resolution",
                description="The new equilibrium showing the consequences of the climax.",
                tension_range=(0.15, 0.35),
            ),
        ],
    )


def get_save_the_cat_template() -> StructureTemplate:
    return StructureTemplate(
        key="save-the-cat",
        name="Save the Cat (Blake Snyder)",
        beats=[
            StructureBeat(
                beat_name="Opening Image",
                position_pct=0.0,
                beat_kind="hook",
                description="A visual snapshot of the protagonist's world before the journey begins.",
                tension_range=(0.15, 0.30),
            ),
            StructureBeat(
                beat_name="Theme Stated",
                position_pct=0.05,
                beat_kind="setup",
                description="The thematic premise is hinted at, often in dialogue, before the protagonist understands it.",
                tension_range=(0.15, 0.30),
            ),
            StructureBeat(
                beat_name="Set-Up",
                position_pct=0.08,
                beat_kind="setup",
                description="Establish the protagonist's flaws, relationships, and ordinary world.",
                tension_range=(0.20, 0.35),
            ),
            StructureBeat(
                beat_name="Catalyst",
                position_pct=0.10,
                beat_kind="inciting_incident",
                description="The life-changing event that sets the story in motion.",
                tension_range=(0.30, 0.50),
            ),
            StructureBeat(
                beat_name="Debate",
                position_pct=0.15,
                beat_kind="debate",
                description="The protagonist hesitates — should they accept the call?",
                tension_range=(0.25, 0.45),
            ),
            StructureBeat(
                beat_name="Break into Two",
                position_pct=0.20,
                beat_kind="plot_point",
                description="The protagonist makes a proactive choice and enters Act 2.",
                tension_range=(0.40, 0.55),
            ),
            StructureBeat(
                beat_name="B Story",
                position_pct=0.22,
                beat_kind="subplot_intro",
                description="A new character or relationship arrives, often carrying the theme.",
                tension_range=(0.30, 0.50),
            ),
            StructureBeat(
                beat_name="Fun and Games",
                position_pct=0.30,
                beat_kind="rising_action",
                description="The promise of the premise delivered — the 'trailer moments'.",
                tension_range=(0.45, 0.65),
            ),
            StructureBeat(
                beat_name="Midpoint",
                position_pct=0.50,
                beat_kind="midpoint",
                description="A false victory or false defeat that raises the stakes.",
                tension_range=(0.60, 0.80),
            ),
            StructureBeat(
                beat_name="Bad Guys Close In",
                position_pct=0.60,
                beat_kind="rising_action",
                description="External pressures increase; internal flaws resurface.",
                tension_range=(0.65, 0.85),
            ),
            StructureBeat(
                beat_name="All Is Lost",
                position_pct=0.75,
                beat_kind="crisis",
                description="The lowest point — a whiff of death (literal or metaphorical).",
                tension_range=(0.80, 0.95),
            ),
            StructureBeat(
                beat_name="Dark Night of the Soul",
                position_pct=0.78,
                beat_kind="crisis",
                description="The protagonist processes the loss and finds the strength to continue.",
                tension_range=(0.70, 0.85),
            ),
            StructureBeat(
                beat_name="Break into Three",
                position_pct=0.80,
                beat_kind="plot_point",
                description="Armed with a new insight (often from the B Story), the protagonist hatches a plan.",
                tension_range=(0.75, 0.90),
            ),
            StructureBeat(
                beat_name="Finale",
                position_pct=0.90,
                beat_kind="climax",
                description="The protagonist confronts the antagonist using lessons learned.",
                tension_range=(0.90, 0.99),
            ),
            StructureBeat(
                beat_name="Final Image",
                position_pct=0.98,
                beat_kind="resolution",
                description="A mirror of the Opening Image showing how much the world has changed.",
                tension_range=(0.15, 0.35),
            ),
        ],
    )


def get_hero_journey_template() -> StructureTemplate:
    return StructureTemplate(
        key="hero-journey",
        name="The Hero's Journey (Joseph Campbell / Christopher Vogler)",
        beats=[
            StructureBeat(
                beat_name="Ordinary World",
                position_pct=0.0,
                beat_kind="setup",
                description="The hero's mundane life before the adventure.",
                tension_range=(0.10, 0.25),
            ),
            StructureBeat(
                beat_name="Call to Adventure",
                position_pct=0.08,
                beat_kind="inciting_incident",
                description="Something disrupts the ordinary world, beckoning the hero.",
                tension_range=(0.25, 0.40),
            ),
            StructureBeat(
                beat_name="Refusal of the Call",
                position_pct=0.12,
                beat_kind="debate",
                description="The hero hesitates due to fear or obligation.",
                tension_range=(0.20, 0.40),
            ),
            StructureBeat(
                beat_name="Meeting the Mentor",
                position_pct=0.17,
                beat_kind="setup",
                description="A guide provides wisdom, training, or a talisman.",
                tension_range=(0.25, 0.40),
            ),
            StructureBeat(
                beat_name="Crossing the Threshold",
                position_pct=0.25,
                beat_kind="plot_point",
                description="The hero leaves the known world and enters the special world.",
                tension_range=(0.40, 0.55),
            ),
            StructureBeat(
                beat_name="Tests, Allies, Enemies",
                position_pct=0.35,
                beat_kind="rising_action",
                description="The hero faces challenges, makes friends and foes, learns the rules.",
                tension_range=(0.45, 0.65),
            ),
            StructureBeat(
                beat_name="Approach to the Inmost Cave",
                position_pct=0.50,
                beat_kind="midpoint",
                description="Preparation for the central ordeal; stakes become clear.",
                tension_range=(0.55, 0.75),
            ),
            StructureBeat(
                beat_name="The Ordeal",
                position_pct=0.60,
                beat_kind="crisis",
                description="The hero confronts death or the greatest fear — the central crisis.",
                tension_range=(0.75, 0.95),
            ),
            StructureBeat(
                beat_name="Reward",
                position_pct=0.67,
                beat_kind="rising_action",
                description="The hero seizes the prize (knowledge, object, reconciliation).",
                tension_range=(0.55, 0.70),
            ),
            StructureBeat(
                beat_name="The Road Back",
                position_pct=0.78,
                beat_kind="rising_action",
                description="The hero returns with the reward, but pursuit or consequences follow.",
                tension_range=(0.65, 0.85),
            ),
            StructureBeat(
                beat_name="Resurrection",
                position_pct=0.90,
                beat_kind="climax",
                description="A final test where the hero must apply everything learned.",
                tension_range=(0.85, 0.99),
            ),
            StructureBeat(
                beat_name="Return with the Elixir",
                position_pct=0.97,
                beat_kind="resolution",
                description="The hero returns transformed, bringing benefit to the ordinary world.",
                tension_range=(0.15, 0.35),
            ),
        ],
    )


def get_story_circle_template() -> StructureTemplate:
    return StructureTemplate(
        key="story-circle",
        name="Dan Harmon's Story Circle",
        beats=[
            StructureBeat(
                beat_name="You (Comfort Zone)",
                position_pct=0.0,
                beat_kind="setup",
                description="Establish the character in their zone of comfort.",
                tension_range=(0.10, 0.25),
            ),
            StructureBeat(
                beat_name="Need (Desire)",
                position_pct=0.12,
                beat_kind="inciting_incident",
                description="The character wants something badly enough to act.",
                tension_range=(0.25, 0.45),
            ),
            StructureBeat(
                beat_name="Go (Unfamiliar Situation)",
                position_pct=0.25,
                beat_kind="plot_point",
                description="The character crosses into an unfamiliar situation.",
                tension_range=(0.40, 0.55),
            ),
            StructureBeat(
                beat_name="Search (Adaptation)",
                position_pct=0.38,
                beat_kind="rising_action",
                description="The character struggles to adapt and searches for what they need.",
                tension_range=(0.50, 0.65),
            ),
            StructureBeat(
                beat_name="Find (Meeting the Goal)",
                position_pct=0.50,
                beat_kind="midpoint",
                description="The character gets what they wanted — but at a cost.",
                tension_range=(0.60, 0.80),
            ),
            StructureBeat(
                beat_name="Take (Pay the Price)",
                position_pct=0.65,
                beat_kind="crisis",
                description="The heavy price of getting what they wanted becomes clear.",
                tension_range=(0.75, 0.90),
            ),
            StructureBeat(
                beat_name="Return (Changed)",
                position_pct=0.82,
                beat_kind="climax",
                description="The character returns to the familiar situation, but changed.",
                tension_range=(0.80, 0.95),
            ),
            StructureBeat(
                beat_name="Change (New Equilibrium)",
                position_pct=0.95,
                beat_kind="resolution",
                description="The character has changed and a new status quo is established.",
                tension_range=(0.20, 0.40),
            ),
        ],
    )


def get_kishotenketsu_template() -> StructureTemplate:
    return StructureTemplate(
        key="kishotenketsu",
        name="Kishotenketsu (起承転結)",
        beats=[
            StructureBeat(
                beat_name="Ki (Introduction)",
                position_pct=0.0,
                beat_kind="setup",
                description="Introduce the setting, characters, and situation without conflict.",
                tension_range=(0.10, 0.30),
            ),
            StructureBeat(
                beat_name="Sho (Development)",
                position_pct=0.25,
                beat_kind="rising_action",
                description="Develop the elements introduced; deepen the reader's understanding.",
                tension_range=(0.25, 0.50),
            ),
            StructureBeat(
                beat_name="Ten (Twist)",
                position_pct=0.55,
                beat_kind="midpoint",
                description="A surprising turn or new perspective that recontextualizes everything.",
                tension_range=(0.60, 0.85),
            ),
            StructureBeat(
                beat_name="Ketsu (Conclusion)",
                position_pct=0.85,
                beat_kind="resolution",
                description="Reconcile the twist with the earlier elements; bring harmony.",
                tension_range=(0.30, 0.50),
            ),
        ],
    )


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

_TEMPLATE_FACTORIES = {
    "three-act": get_three_act_template,
    "save-the-cat": get_save_the_cat_template,
    "hero-journey": get_hero_journey_template,
    "story-circle": get_story_circle_template,
    "kishotenketsu": get_kishotenketsu_template,
}


def resolve_structure_template(key: str | None) -> StructureTemplate:
    """Resolve a structure template by key, defaulting to three-act."""
    factory = _TEMPLATE_FACTORIES.get(key or "three-act", get_three_act_template)
    return factory()


def list_structure_template_keys() -> list[str]:
    return list(_TEMPLATE_FACTORIES.keys())
