"""Planner-facing catalog for selectable story-effect skills.

This is a coordination layer over existing BestSeller capabilities.  The
catalog is intentionally compact so Planner can choose skills without carrying
every full contract in every prompt; full contracts are expanded only when a
skill is already selected for the current planning pass.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from bestseller.services.brainhole_engine import (
    BRAINHOLE_PROFILE_METADATA_KEY,
    render_brainhole_planner_prompt_block,
    resolve_brainhole_profile,
)

STORY_EFFECT_SKILL_CATALOG_METADATA_KEY = "story_effect_skill_catalog"
STORY_EFFECT_SKILL_SELECTION_METADATA_KEY = "story_effect_skill_selection"
STORY_EFFECT_SKILL_CATALOG_VERSION = "2026-06-15.v2"


class StoryEffectSkillCatalogActivation(BaseModel):
    model_config = ConfigDict(frozen=True)

    scope: Literal["new_project"] = "new_project"
    gate_mode: Literal["audit_only", "warn", "strict"] = "audit_only"
    affects_legacy_projects: bool = False


class StoryEffectSkillEntry(BaseModel):
    model_config = ConfigDict(frozen=True)

    skill_key: str
    effect_type: str
    description: str
    source_modules: tuple[str, ...] = ()
    use_when: tuple[str, ...] = ()
    avoid_when: tuple[str, ...] = ()
    best_stage: tuple[str, ...] = ()
    can_pair_with: tuple[str, ...] = ()
    conflicts_with: tuple[str, ...] = ()
    output_contract: str
    misuse_guardrails: tuple[str, ...] = ()
    expansion_policy: Literal["catalog_only", "expand_when_selected"] = (
        "expand_when_selected"
    )


class StoryEffectSkillCatalogSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)

    version: str = STORY_EFFECT_SKILL_CATALOG_VERSION
    catalog_key: str
    genre: str
    sub_genre: str | None = None
    prompt_pack_key: str | None = None
    activation: StoryEffectSkillCatalogActivation = Field(
        default_factory=StoryEffectSkillCatalogActivation
    )
    skills: tuple[StoryEffectSkillEntry, ...]
    default_stage_preferences: dict[str, tuple[str, ...]] = Field(default_factory=dict)
    router_policy: str = (
        "Planner sees the compact catalog, selects at most one primary and one "
        "secondary effect skill per chapter, then only selected skill contracts "
        "are expanded into the outline contract."
    )

    def to_metadata(self) -> dict[str, Any]:
        payload = self.model_dump(mode="json")
        payload["skills"] = [skill.model_dump(mode="json") for skill in self.skills]
        payload["default_stage_preferences"] = {
            key: list(value) for key, value in self.default_stage_preferences.items()
        }
        return payload


def _base_story_effect_skills() -> tuple[StoryEffectSkillEntry, ...]:
    return (
        StoryEffectSkillEntry(
            skill_key="brainhole_engine",
            effect_type="novelty_contrast",
            description=(
                "Generate high-concept persona-safe contrast by crossing a familiar "
                "character card with a genre-native mechanism/system and protagonist growth stage."
            ),
            source_modules=("brainhole_engine", "concept_lab", "anti_commonsense_hook"),
            use_when=("opening hook", "persona contrast", "mechanism collision"),
            avoid_when=(
                "quiet aftermath",
                "pure grief payoff",
                "chapters needing realism only",
            ),
            best_stage=("opening", "early_middle"),
            can_pair_with=(
                "comedy_engine",
                "world_texture_engine",
                "relationship_chemistry_engine",
            ),
            conflicts_with=("healing_grief_engine",),
            output_contract="brainhole_contract",
            misuse_guardrails=(
                "do not break persona invariants for a cheap gag",
                "do not grant protagonist authority before the growth stage earns it",
            ),
        ),
        StoryEffectSkillEntry(
            skill_key="comedy_engine",
            effect_type="visible_comedy",
            description=(
                "Use identity mismatch, rule mismatch, escalation, callback, and "
                "over-serious treatment of absurdity to create plot-moving comedy."
            ),
            source_modules=("hype_engine", "scene_beat_planner", "character_drama_engine"),
            use_when=("pace needs lift", "absurd situation", "character/system mismatch"),
            avoid_when=("death consequence", "solemn emotional reveal"),
            best_stage=("opening", "early_middle"),
            can_pair_with=(
                "brainhole_engine",
                "dialogue_spark_engine",
                "relationship_chemistry_engine",
            ),
            conflicts_with=("healing_grief_engine",),
            output_contract="comic_effect_contract",
            misuse_guardrails=(
                "comedy must change plot pressure or relationship state",
                "do not use insult comedy or meta commentary as a substitute for action",
            ),
            expansion_policy="catalog_only",
        ),
        StoryEffectSkillEntry(
            skill_key="emotional_payoff_engine",
            effect_type="emotional_payoff",
            description=(
                "Convert seeded emotional debt into a concrete action payoff without "
                "using authorial sentiment labels or forced melodrama."
            ),
            source_modules=("emotion_driven_kernel", "public_emotion_kernel", "payoff_ledger_runtime"),
            use_when=("relationship debt comes due", "misunderstanding repair", "sacrifice reveal"),
            avoid_when=("pure setup", "fast exposition bridge"),
            best_stage=("early_middle", "middle_late", "late"),
            can_pair_with=(
                "relationship_chemistry_engine",
                "callback_motif_engine",
                "healing_grief_engine",
            ),
            conflicts_with=("pure_comedy_only",),
            output_contract="emotional_payoff_contract",
            misuse_guardrails=(
                "payoff must be visible action, not an author emotion label",
                "do not cash out emotional debt before it has been seeded",
            ),
            expansion_policy="catalog_only",
        ),
        StoryEffectSkillEntry(
            skill_key="relationship_chemistry_engine",
            effect_type="relationship_chemistry",
            description=(
                "Shape character pairs through mutual need, friction, misunderstanding, "
                "rescue, debt, and shared secrets."
            ),
            source_modules=("character_drama_engine", "character_arcs", "character_evolution"),
            use_when=("new character entry", "team friction", "trust shift"),
            avoid_when=("solo action chapter",),
            best_stage=("opening", "early_middle", "middle_late"),
            can_pair_with=(
                "brainhole_engine",
                "comedy_engine",
                "dialogue_spark_engine",
                "romance_tenderness_engine",
                "emotional_payoff_engine",
            ),
            conflicts_with=(),
            output_contract="relationship_chemistry_contract",
            misuse_guardrails=(
                "chemistry needs a concrete exchange of need, debt, risk, or trust",
                "do not replace relationship change with generic banter",
            ),
            expansion_policy="catalog_only",
        ),
        StoryEffectSkillEntry(
            skill_key="suspense_reveal_engine",
            effect_type="suspense_reveal",
            description=(
                "Plan information gaps, misdirection, partial reveal, and chapter-end "
                "questions without exposing future truth too early."
            ),
            source_modules=("hook_ledger_runtime", "hook_echo_gate", "revealed_ledger"),
            use_when=("clue turn", "secret pressure", "need read-on question"),
            avoid_when=("full payoff closure",),
            best_stage=("opening", "early_middle", "middle_late"),
            can_pair_with=(
                "tension_pressure_engine",
                "twist_reversal_engine",
                "callback_motif_engine",
            ),
            conflicts_with=("full_exposition_dump",),
            output_contract="suspense_reveal_contract",
            misuse_guardrails=(
                "withhold one actionable gap, not the entire situation",
                "do not reveal future truth before the current chapter earns it",
            ),
            expansion_policy="catalog_only",
        ),
        StoryEffectSkillEntry(
            skill_key="hype_satisfaction_engine",
            effect_type="reader_satisfaction",
            description=(
                "Deliver visible gain, reversal, status shift, upgrade, slapback, or "
                "reward while preserving future pressure."
            ),
            source_modules=("hype_engine", "payoff_ledger_runtime", "setup_payoff_tracker"),
            use_when=("mini climax", "first gain/loss cycle", "promise needs payoff"),
            avoid_when=("slow-burn mystery withholding",),
            best_stage=("opening", "early_middle", "middle_late", "late"),
            can_pair_with=(
                "tension_pressure_engine",
                "emotional_payoff_engine",
                "suspense_reveal_engine",
            ),
            conflicts_with=("payoff_delay_only",),
            output_contract="hype_satisfaction_contract",
            misuse_guardrails=(
                "satisfaction must leave a new pressure or cost behind",
                "do not solve the arc with an unearned reward",
            ),
            expansion_policy="catalog_only",
        ),
        StoryEffectSkillEntry(
            skill_key="moral_dilemma_engine",
            effect_type="value_choice",
            description=(
                "Force protagonist growth through a decision where every option has "
                "cost, relationship fallout, or institutional consequence."
            ),
            source_modules=("story_design_kernel", "ideology_kernel", "character_drama_engine"),
            use_when=("authority grows", "values conflict", "no clean answer"),
            avoid_when=("low-stakes gag chapter",),
            best_stage=("middle_late", "late"),
            can_pair_with=(
                "emotional_payoff_engine",
                "danger_action_engine",
                "system_payoff_engine",
            ),
            conflicts_with=("pure_comedy_only",),
            output_contract="moral_dilemma_contract",
            misuse_guardrails=(
                "every option must carry a concrete cost",
                "do not disguise a solved choice as a dilemma",
            ),
            expansion_policy="catalog_only",
        ),
        StoryEffectSkillEntry(
            skill_key="system_payoff_engine",
            effect_type="system_payoff",
            description=(
                "Turn accumulated rule, institution, or workflow pressure into a "
                "visible policy, role, resource, or power-structure change."
            ),
            source_modules=("story_design_kernel", "payoff_ledger_runtime", "entry_system_kernel"),
            use_when=("arc closure", "institutional consequence", "late-stage protagonist authority"),
            avoid_when=("opening observation", "unearned authority"),
            best_stage=("middle_late", "late"),
            can_pair_with=(
                "moral_dilemma_engine",
                "hype_satisfaction_engine",
                "wonder_awe_engine",
            ),
            conflicts_with=("early_observe_only",),
            output_contract="system_payoff_contract",
            misuse_guardrails=(
                "system change must pay off accumulated pressure",
                "do not redesign institutions before the protagonist has earned authority",
            ),
            expansion_policy="catalog_only",
        ),
        StoryEffectSkillEntry(
            skill_key="tension_pressure_engine",
            effect_type="pressure_escalation",
            description=(
                "Build chapter pressure through deadlines, public consequence, "
                "resource scarcity, relationship risk, and narrowing choices."
            ),
            source_modules=("scene_beat_planner", "hook_ledger_runtime", "payoff_ledger_runtime"),
            use_when=("deadline appears", "stakes need force", "choice space narrows"),
            avoid_when=("rest chapter", "full closure chapter", "pressure already peaked"),
            best_stage=("early_middle", "middle_late", "late"),
            can_pair_with=(
                "hype_satisfaction_engine",
                "suspense_reveal_engine",
                "danger_action_engine",
            ),
            conflicts_with=("false_urgency_only",),
            output_contract="tension_pressure_contract",
            misuse_guardrails=(
                "pressure must change what the protagonist can do next",
                "do not write atmosphere-only tension without a visible constraint",
            ),
        ),
        StoryEffectSkillEntry(
            skill_key="rhythm_pacing_engine",
            effect_type="chapter_rhythm",
            description=(
                "Vary beat size, scene tempo, quiet moments, reversals, and "
                "payoff spacing so adjacent chapters do not feel mechanically identical."
            ),
            source_modules=("scene_beat_planner", "chapter_outline_validator"),
            use_when=("adjacent chapters feel samey", "batch needs tempo variation"),
            avoid_when=("single-scene emergency", "already structurally varied batch"),
            best_stage=("opening", "early_middle", "middle_late", "late"),
            can_pair_with=(
                "tension_pressure_engine",
                "world_texture_engine",
                "emotional_payoff_engine",
            ),
            conflicts_with=("fixed_formula_repeat",),
            output_contract="rhythm_pacing_contract",
            misuse_guardrails=(
                "rhythm changes must serve story state, not decorative alternation",
                "do not force every chapter into the same three-scene shape",
            ),
        ),
        StoryEffectSkillEntry(
            skill_key="twist_reversal_engine",
            effect_type="expectation_reversal",
            description=(
                "Flip an earned assumption through seeded evidence, role reversal, "
                "cost inversion, or a new interpretation of an earlier clue."
            ),
            source_modules=("hook_ledger_runtime", "revealed_ledger", "setup_payoff_tracker"),
            use_when=("reader assumption is ready to flip", "midpoint turn", "clue reframe"),
            avoid_when=("no prior seed", "random shock", "truth would spoil a later arc"),
            best_stage=("early_middle", "middle_late", "late"),
            can_pair_with=("suspense_reveal_engine", "callback_motif_engine"),
            conflicts_with=("unseeded_reversal",),
            output_contract="twist_reversal_contract",
            misuse_guardrails=(
                "reversal must be seeded by a prior observable detail",
                "do not contradict established facts just to surprise the reader",
            ),
            expansion_policy="catalog_only",
        ),
        StoryEffectSkillEntry(
            skill_key="callback_motif_engine",
            effect_type="callback_payoff",
            description=(
                "Reuse an object, phrase, joke, rule, wound, or image with changed "
                "meaning so the chapter feels seeded rather than episodic."
            ),
            source_modules=("payoff_ledger_runtime", "setup_payoff_tracker", "hook_echo_gate"),
            use_when=("seed can echo", "motif needs payoff", "chapter needs continuity"),
            avoid_when=("no prior touchpoint", "callback would stall current action"),
            best_stage=("early_middle", "middle_late", "late"),
            can_pair_with=(
                "emotional_payoff_engine",
                "twist_reversal_engine",
                "system_payoff_engine",
            ),
            conflicts_with=("random_reference_only",),
            output_contract="callback_motif_contract",
            misuse_guardrails=(
                "callback must alter current meaning, status, or choice",
                "do not name-drop a prior item without payoff or pressure",
            ),
        ),
        StoryEffectSkillEntry(
            skill_key="world_texture_engine",
            effect_type="grounded_world_texture",
            description=(
                "Make the world feel lived-in through specific places, objects, "
                "social rules, labor, sensory anchors, and material consequences."
            ),
            source_modules=("worldview_kernel", "entry_system_kernel", "scene_beat_planner"),
            use_when=("setting feels abstract", "new location", "rule needs embodiment"),
            avoid_when=("urgent action cannot pause", "detail would become exposition"),
            best_stage=("opening", "early_middle", "middle_late"),
            can_pair_with=(
                "brainhole_engine",
                "dialogue_spark_engine",
                "rhythm_pacing_engine",
            ),
            conflicts_with=("scenery_dump",),
            output_contract="world_texture_contract",
            misuse_guardrails=(
                "texture must affect action, status, evidence, or cost",
                "do not add decorative scenery that the scene never uses",
            ),
        ),
        StoryEffectSkillEntry(
            skill_key="wonder_awe_engine",
            effect_type="wonder_awe",
            description=(
                "Stage earned scale, beauty, impossibility, sacredness, or system "
                "magnitude through a concrete reveal and visible consequence."
            ),
            source_modules=("worldview_kernel", "story_design_kernel", "payoff_ledger_runtime"),
            use_when=("major reveal", "scale expansion", "late-stage system awe"),
            avoid_when=("routine logistics", "unearned spectacle", "small repair scene"),
            best_stage=("opening", "middle_late", "late"),
            can_pair_with=("system_payoff_engine", "world_texture_engine"),
            conflicts_with=("spectacle_without_cost",),
            output_contract="wonder_awe_contract",
            misuse_guardrails=(
                "awe must reveal rule, cost, or changed possibility",
                "do not use vague grand adjectives without a concrete image",
            ),
            expansion_policy="catalog_only",
        ),
        StoryEffectSkillEntry(
            skill_key="danger_action_engine",
            effect_type="danger_action",
            description=(
                "Turn danger into readable action: threat geometry, forced movement, "
                "injury/resource cost, and tactical choice under pressure."
            ),
            source_modules=("scene_beat_planner", "hype_engine", "character_drama_engine"),
            use_when=("physical danger", "pursuit", "rescue", "public confrontation"),
            avoid_when=("pure debate", "quiet recovery", "threat has no cost"),
            best_stage=("early_middle", "middle_late", "late"),
            can_pair_with=("tension_pressure_engine", "moral_dilemma_engine"),
            conflicts_with=("weightless_action",),
            output_contract="danger_action_contract",
            misuse_guardrails=(
                "action must change position, resource, injury, exposure, or leverage",
                "do not write danger as noise without tactical consequence",
            ),
            expansion_policy="catalog_only",
        ),
        StoryEffectSkillEntry(
            skill_key="dialogue_spark_engine",
            effect_type="dialogue_energy",
            description=(
                "Create sharp dialogue through conflicting agendas, status play, "
                "subtext, callback, interruption, and concrete bargaining."
            ),
            source_modules=("character_drama_engine", "scene_beat_planner"),
            use_when=("first meeting", "banter", "negotiation", "relationship friction"),
            avoid_when=("solo action", "exposition monologue", "grief needs silence"),
            best_stage=("opening", "early_middle", "middle_late"),
            can_pair_with=(
                "comedy_engine",
                "relationship_chemistry_engine",
                "world_texture_engine",
            ),
            conflicts_with=("speech_dump",),
            output_contract="dialogue_spark_contract",
            misuse_guardrails=(
                "dialogue must pursue conflicting goals or alter status",
                "do not let characters explain what the scene can show",
            ),
            expansion_policy="catalog_only",
        ),
        StoryEffectSkillEntry(
            skill_key="healing_grief_engine",
            effect_type="healing_grief",
            description=(
                "Handle loss, repair, regret, forgiveness, and quiet recovery through "
                "specific acts of care rather than sentimental labels."
            ),
            source_modules=("emotion_driven_kernel", "character_drama_engine"),
            use_when=("aftermath", "forgiveness", "loss repair", "late emotional closure"),
            avoid_when=("slapstick beat", "unearned forgiveness", "fast exposition bridge"),
            best_stage=("middle_late", "late"),
            can_pair_with=("emotional_payoff_engine", "callback_motif_engine"),
            conflicts_with=("pure_comedy_only", "fast_payoff_only"),
            output_contract="healing_grief_contract",
            misuse_guardrails=(
                "healing must be shown through a concrete choice, object, or gesture",
                "do not erase grief or forgive betrayal without visible cost",
            ),
            expansion_policy="catalog_only",
        ),
        StoryEffectSkillEntry(
            skill_key="romance_tenderness_engine",
            effect_type="romance_tenderness",
            description=(
                "Add optional tenderness, restrained intimacy, protection, longing, "
                "and mutual recognition when the project metadata explicitly wants romance."
            ),
            source_modules=("character_drama_engine", "emotion_driven_kernel"),
            use_when=("explicit romance lane", "tender trust shift", "mutual protection"),
            avoid_when=("metadata does not request romance", "relationship is platonic only"),
            best_stage=("early_middle", "middle_late", "late"),
            can_pair_with=("relationship_chemistry_engine", "dialogue_spark_engine"),
            conflicts_with=("platonic_only_contract",),
            output_contract="romance_tenderness_contract",
            misuse_guardrails=(
                "use only when metadata or selection explicitly asks for romance",
                "do not convert every relationship beat into romantic subtext",
            ),
            expansion_policy="catalog_only",
        ),
    )


def _catalog_key(
    genre: str,
    sub_genre: str | None = None,
    prompt_pack_key: str | None = None,
) -> str:
    label = f"{genre} {sub_genre or ''} {prompt_pack_key or ''}".lower()
    # mythic-WORKPLACE (招神/神仙 HR) effect-skills need an explicit HR/招聘/职场 signal.
    # Bare 仙/修仙/仙侠 (cultivation) must NOT match — else cultivation books get the
    # workplace/comedy engine stack (brainhole/comedy/HR-contract) and homogenize toward
    # 债契/记账/HR gimmicks. Twin of the brainhole_engine._resolve_profile_key "仙" bug.
    workplace = any(
        t in label
        for t in ("招神", "hr", "人事", "职场", "入职", "面试", "招聘", "打工", "上班", "员工", "上岗")
    )
    mythic = any(t in label for t in ("神仙", "西游", "封神", "天庭", "神庭", "myth"))
    if "招神" in label or (mythic and workplace):
        return "mythic-workplace-effect-skills"
    if any(token in label for token in ("悬疑", "民俗", "怪谈", "suspense", "mystery")):
        return "mystery-effect-skills"
    if any(token in label for token in ("都市", "职场", "现实", "urban")):
        return "urban-effect-skills"
    return "general-serial-effect-skills"


def _stage_preferences(catalog_key: str) -> dict[str, tuple[str, ...]]:
    if catalog_key == "mythic-workplace-effect-skills":
        return {
            "opening": (
                "brainhole_engine",
                "comedy_engine",
                "world_texture_engine",
                "dialogue_spark_engine",
                "relationship_chemistry_engine",
            ),
            "early_middle": (
                "tension_pressure_engine",
                "twist_reversal_engine",
                "callback_motif_engine",
                "hype_satisfaction_engine",
            ),
            "middle_late": (
                "emotional_payoff_engine",
                "moral_dilemma_engine",
                "danger_action_engine",
                "system_payoff_engine",
            ),
            "late": (
                "system_payoff_engine",
                "wonder_awe_engine",
                "healing_grief_engine",
                "callback_motif_engine",
            ),
        }
    return {
        "opening": (
            "brainhole_engine",
            "world_texture_engine",
            "suspense_reveal_engine",
            "hype_satisfaction_engine",
        ),
        "early_middle": (
            "tension_pressure_engine",
            "relationship_chemistry_engine",
            "suspense_reveal_engine",
            "hype_satisfaction_engine",
        ),
        "middle_late": (
            "emotional_payoff_engine",
            "moral_dilemma_engine",
            "danger_action_engine",
            "hype_satisfaction_engine",
        ),
        "late": (
            "system_payoff_engine",
            "emotional_payoff_engine",
            "callback_motif_engine",
            "moral_dilemma_engine",
        ),
    }


def resolve_story_effect_skill_catalog(
    genre: str,
    sub_genre: str | None = None,
    *,
    prompt_pack_key: str | None = None,
) -> StoryEffectSkillCatalogSnapshot:
    catalog_key = _catalog_key(genre, sub_genre, prompt_pack_key)
    return StoryEffectSkillCatalogSnapshot(
        catalog_key=catalog_key,
        genre=genre,
        sub_genre=sub_genre,
        prompt_pack_key=prompt_pack_key,
        skills=_base_story_effect_skills(),
        default_stage_preferences=_stage_preferences(catalog_key),
    )


def attach_story_effect_skill_catalog(
    metadata: Mapping[str, Any] | None,
    catalog: StoryEffectSkillCatalogSnapshot,
) -> dict[str, Any]:
    updated = dict(metadata or {})
    updated[STORY_EFFECT_SKILL_CATALOG_METADATA_KEY] = catalog.to_metadata()
    return updated


def story_effect_skill_catalog_from_metadata(
    metadata: Mapping[str, Any] | None,
) -> StoryEffectSkillCatalogSnapshot | None:
    raw = (metadata or {}).get(STORY_EFFECT_SKILL_CATALOG_METADATA_KEY)
    if not isinstance(raw, Mapping):
        return None
    try:
        return StoryEffectSkillCatalogSnapshot.model_validate(raw)
    except ValueError:
        return None


def selected_story_effect_skill_keys(
    metadata: Mapping[str, Any] | None,
    *,
    stage: str = "chapter_outline",
) -> tuple[str, ...]:
    raw = (metadata or {}).get(STORY_EFFECT_SKILL_SELECTION_METADATA_KEY)
    if isinstance(raw, Mapping):
        stage_value = raw.get(stage) or raw.get("selected") or raw.get("skills")
        if isinstance(stage_value, Mapping):
            additional = stage_value.get("additional") or ()
            if isinstance(additional, str):
                additional_values: tuple[Any, ...] = (additional,)
            elif isinstance(additional, (list, tuple)):
                additional_values = tuple(additional)
            else:
                additional_values = ()
            values = [
                stage_value.get("primary"),
                stage_value.get("secondary"),
                *additional_values,
            ]
        else:
            values = stage_value
    else:
        values = raw
    if isinstance(values, str):
        return (values,)
    if not isinstance(values, (list, tuple)):
        return ()
    keys: list[str] = []
    for value in values:
        if isinstance(value, str) and value and value not in keys:
            keys.append(value)
    return tuple(keys)


def _catalog_or_metadata(
    catalog_or_metadata: StoryEffectSkillCatalogSnapshot | Mapping[str, Any] | None,
) -> StoryEffectSkillCatalogSnapshot | None:
    if isinstance(catalog_or_metadata, StoryEffectSkillCatalogSnapshot):
        return catalog_or_metadata
    if isinstance(catalog_or_metadata, Mapping):
        return story_effect_skill_catalog_from_metadata(catalog_or_metadata)
    return None


def render_story_effect_skill_catalog_prompt_block(
    catalog_or_metadata: StoryEffectSkillCatalogSnapshot | Mapping[str, Any] | None,
    *,
    language: str = "zh-CN",
) -> str:
    catalog = _catalog_or_metadata(catalog_or_metadata)
    if catalog is None:
        return ""
    is_en = language.lower().startswith("en")
    rows: list[str] = []
    for skill in catalog.skills:
        if is_en:
            rows.append(
                "- "
                f"{skill.skill_key}: effect={skill.effect_type}; use_when="
                f"{', '.join(skill.use_when)}; avoid_when={', '.join(skill.avoid_when)}; "
                f"stage={', '.join(skill.best_stage)}; pairs={', '.join(skill.can_pair_with)}; "
                f"output={skill.output_contract}; expansion={skill.expansion_policy}; "
                f"guardrails={', '.join(skill.misuse_guardrails)}."
            )
        else:
            rows.append(
                "- "
                f"{skill.skill_key}：效果={skill.effect_type}；适用="
                f"{'、'.join(skill.use_when)}；避开={'、'.join(skill.avoid_when)}；"
                f"阶段={'、'.join(skill.best_stage)}；可搭配={'、'.join(skill.can_pair_with)}；"
                f"输出={skill.output_contract}；展开={skill.expansion_policy}；"
                f"防误用={'、'.join(skill.misuse_guardrails)}。"
            )

    prefs = {
        key: list(value) for key, value in catalog.default_stage_preferences.items()
    }
    if is_en:
        return (
            "[STORY EFFECT SKILL CATALOG]\n"
            f"Catalog: {catalog.catalog_key} / {catalog.version}. This is a compact "
            "routing catalog, not a request to use every skill.\n"
            "Selection rule: each chapter must output `selected_effect_skills` with "
            "primary, secondary, reason, growth_stage_fit, and expected_contracts. "
            "Choose at most one primary and one secondary skill. Expand and output "
            "only the contracts for selected skills.\n"
            f"Default stage preferences: {prefs}.\n"
            + "\n".join(rows)
        )
    return (
        "【故事效果 Skill 清单】\n"
        f"清单：{catalog.catalog_key} / {catalog.version}。这是短路由清单，不是要求每章全量使用。\n"
        "选择规则：每章必须输出 `selected_effect_skills`，包含 primary、secondary、reason、"
        "growth_stage_fit、expected_contracts。每章最多 1 个 primary + 1 个 secondary；"
        "只展开并输出被选中 skill 的合同，未选中的 skill 不得污染本章。\n"
        f"默认阶段偏好：{prefs}。\n"
        + "\n".join(rows)
    )


def _render_tension_pressure_contract(*, language: str) -> str:
    if language.lower().startswith("en"):
        return (
            "[TENSION PRESSURE CONTRACT]\n"
            "When this contract is present, each selected chapter must include "
            "`tension_pressure_contract`. Required fields: pressure_source, "
            "ticking_constraint, stakes_if_fail, escalation_step, "
            "choice_space_narrowing, relief_or_payoff_window, no_fake_pressure_check.\n"
            "Rules: pressure must come from an existing goal, relationship, "
            "resource, public consequence, or clock; every escalation must change "
            "what the protagonist can do next; never substitute tense atmosphere "
            "for a visible constraint."
        )
    return (
        "【张力压力合同】\n"
        "只要本合同存在，被选中的章节必须输出 `tension_pressure_contract`。必填字段："
        "pressure_source、ticking_constraint、stakes_if_fail、escalation_step、"
        "choice_space_narrowing、relief_or_payoff_window、no_fake_pressure_check。\n"
        "规则：压力必须来自既有目标、关系、资源、公开后果或倒计时；每次加压都要改变"
        "主角下一步能做什么；禁止只写气氛紧张而没有可见约束。"
    )


def _render_rhythm_pacing_contract(*, language: str) -> str:
    if language.lower().startswith("en"):
        return (
            "[RHYTHM PACING CONTRACT]\n"
            "When this contract is present, each selected chapter must include "
            "`rhythm_pacing_contract`. Required fields: beat_mix, tempo_shift, "
            "scene_length_logic, quiet_beat_purpose, turning_point, adjacent_chapter_contrast, "
            "anti_monotony_check.\n"
            "Rules: rhythm changes must follow story state; vary scene count, "
            "pressure level, participant mix, and payoff spacing; do not force "
            "every chapter into the same three-scene shape."
        )
    return (
        "【节奏调度合同】\n"
        "只要本合同存在，被选中的章节必须输出 `rhythm_pacing_contract`。必填字段："
        "beat_mix、tempo_shift、scene_length_logic、quiet_beat_purpose、turning_point、"
        "adjacent_chapter_contrast、anti_monotony_check。\n"
        "规则：节奏变化必须服务当前故事状态；要改变场景数、压力强度、参与角色组合和"
        " payoff 间距；禁止每章机械复刻同一种三场结构。"
    )


def _render_callback_motif_contract(*, language: str) -> str:
    if language.lower().startswith("en"):
        return (
            "[CALLBACK MOTIF CONTRACT]\n"
            "When this contract is present, each selected chapter must include "
            "`callback_motif_contract`. Required fields: seed_or_echo, "
            "motif_object_or_phrase, prior_touchpoint, changed_meaning, current_story_function, "
            "payoff_or_delay, anti_random_echo_check.\n"
            "Rules: a callback must change meaning, status, pressure, or choice in "
            "the current chapter; do not reference an earlier item without payoff, "
            "reversal, or cost."
        )
    return (
        "【回环母题合同】\n"
        "只要本合同存在，被选中的章节必须输出 `callback_motif_contract`。必填字段："
        "seed_or_echo、motif_object_or_phrase、prior_touchpoint、changed_meaning、"
        "current_story_function、payoff_or_delay、anti_random_echo_check。\n"
        "规则：回环必须改变本章的意义、地位、压力或选择；禁止只点名旧物、旧话、旧梗，"
        "却没有 payoff、反转或代价。"
    )


def _render_world_texture_contract(*, language: str) -> str:
    if language.lower().startswith("en"):
        return (
            "[WORLD TEXTURE CONTRACT]\n"
            "When this contract is present, each selected chapter must include "
            "`world_texture_contract`. Required fields: specific_locale_detail, "
            "social_rule_in_use, material_object, sensory_anchor, world_reaction, "
            "plot_function, infodump_check.\n"
            "Rules: texture must be used by action, evidence, status, cost, or "
            "decision; do not add scenery or lore that the scene never touches."
        )
    return (
        "【世界质感合同】\n"
        "只要本合同存在，被选中的章节必须输出 `world_texture_contract`。必填字段："
        "specific_locale_detail、social_rule_in_use、material_object、sensory_anchor、"
        "world_reaction、plot_function、infodump_check。\n"
        "规则：质感必须被行动、证据、身份、代价或选择用到；禁止添加场景从未触碰的"
        "风景说明或设定说明。"
    )


ALL_STORY_EFFECT_SKILL_KEYS: tuple[str, ...] = tuple(
    skill.skill_key for skill in _base_story_effect_skills()
)


def _skill_entry_by_key(skill_key: str) -> StoryEffectSkillEntry | None:
    for skill in _base_story_effect_skills():
        if skill.skill_key == skill_key:
            return skill
    return None


def _render_generic_story_effect_contract(
    entry: StoryEffectSkillEntry, *, language: str
) -> str:
    """Hard per-chapter contract for a selected skill that lacks a bespoke
    renderer — derived from its catalog entry so all 18 skills are usable, not
    just the four with hand-written contracts.

    The contract NAMES the skill's ``output_contract`` field and demands the
    chapter cash the effect (not merely list it), mirroring the bespoke
    renderers' shape so the gate can verify it downstream.
    """

    guardrails = "；".join(entry.misuse_guardrails[:2])
    use_when = "、".join(entry.use_when[:3])
    if language.lower().startswith("en"):
        rails = "; ".join(entry.misuse_guardrails[:2])
        return (
            f"[{entry.skill_key.upper()} CONTRACT — selected at book level, every "
            "chapter must cash it]\n"
            f"Effect: {entry.description}\n"
            f"Each chapter must output `{entry.output_contract}`: a concrete, "
            "on-page beat that delivers this effect through action/choice/reveal "
            "(not a label, not narration). Best used for: "
            f"{', '.join(entry.use_when[:3])}.\n"
            + (f"Guardrails: {rails}." if rails else "")
        )
    return (
        f"【{entry.skill_key} 合同 — 建书时已勾选，每章必须兑现】\n"
        f"效果：{entry.description}\n"
        f"每章必须输出 `{entry.output_contract}`：一个落在页面上的具体 beat，"
        "通过行动/选择/揭示真正兑现这个效果（不是贴标签、不是旁白概述）。"
        f"适合用在：{use_when}。\n"
        + (f"禁忌：{guardrails}。" if guardrails else "")
    )


def _render_selected_story_effect_contract(skill_key: str, *, language: str) -> str:
    renderers = {
        "tension_pressure_engine": _render_tension_pressure_contract,
        "rhythm_pacing_engine": _render_rhythm_pacing_contract,
        "callback_motif_engine": _render_callback_motif_contract,
        "world_texture_engine": _render_world_texture_contract,
    }
    renderer = renderers.get(skill_key)
    if renderer is not None:
        return renderer(language=language)
    entry = _skill_entry_by_key(skill_key)
    if entry is None:
        return ""
    return _render_generic_story_effect_contract(entry, language=language)


def render_selected_story_effect_skill_contracts(
    metadata: Mapping[str, Any] | None,
    *,
    language: str = "zh-CN",
    stage: str = "chapter_outline",
) -> str:
    selected_keys = selected_story_effect_skill_keys(metadata, stage=stage)
    if not selected_keys:
        return ""
    blocks: list[str] = []
    if "brainhole_engine" in selected_keys:
        profile_payload = (metadata or {}).get(BRAINHOLE_PROFILE_METADATA_KEY)
        profile_source: Mapping[str, Any] | None
        if isinstance(profile_payload, Mapping):
            profile_source = {BRAINHOLE_PROFILE_METADATA_KEY: profile_payload}
        else:
            catalog = story_effect_skill_catalog_from_metadata(metadata)
            if catalog is None:
                profile_source = None
            else:
                profile_source = {
                    BRAINHOLE_PROFILE_METADATA_KEY: resolve_brainhole_profile(
                        catalog.genre,
                        catalog.sub_genre,
                        prompt_pack_key=catalog.prompt_pack_key,
                    ).to_metadata()
                }
        block = render_brainhole_planner_prompt_block(profile_source, language=language)
        if block:
            blocks.append(block)
    for skill_key in selected_keys:
        if skill_key == "brainhole_engine":
            continue
        block = _render_selected_story_effect_contract(skill_key, language=language)
        if block:
            blocks.append(block)
    return "\n\n".join(blocks)


__all__ = [
    "STORY_EFFECT_SKILL_CATALOG_METADATA_KEY",
    "STORY_EFFECT_SKILL_CATALOG_VERSION",
    "STORY_EFFECT_SKILL_SELECTION_METADATA_KEY",
    "StoryEffectSkillCatalogActivation",
    "StoryEffectSkillCatalogSnapshot",
    "StoryEffectSkillEntry",
    "attach_story_effect_skill_catalog",
    "render_selected_story_effect_skill_contracts",
    "render_story_effect_skill_catalog_prompt_block",
    "resolve_story_effect_skill_catalog",
    "selected_story_effect_skill_keys",
    "story_effect_skill_catalog_from_metadata",
]
