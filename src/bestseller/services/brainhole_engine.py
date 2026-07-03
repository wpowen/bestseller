"""Versioned brainhole-generation profile for planner-facing story novelty.

The profile is deliberately metadata-driven.  New projects can carry a stable
snapshot into planning, while legacy projects without the metadata keep the
previous outline prompts unchanged.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

BRAINHOLE_PROFILE_METADATA_KEY = "brainhole_profile"
BRAINHOLE_PROFILE_VERSION = "2026-06-25.v2"


class BrainholeProfileActivation(BaseModel):
    model_config = ConfigDict(frozen=True)

    scope: Literal["new_project"] = "new_project"
    gate_mode: Literal["audit_only", "warn", "strict"] = "audit_only"
    affects_legacy_projects: bool = False


class BrainholePersonaCardSchema(BaseModel):
    model_config = ConfigDict(frozen=True)

    required_fields: tuple[str, ...] = (
        "name",
        "public_memory",
        "core_invariants",
        "elastic_zones",
        "forbidden_moves",
        "safe_contrast_moves",
        "modern_system_matches",
        "audience_risk",
    )
    invariant_policy: str = (
        "Contrasts may pressure a character's known core, but must not rewrite "
        "the core without an explicit story cause and visible repair path."
    )


class BrainholeModernSystemSchema(BaseModel):
    model_config = ConfigDict(frozen=True)

    required_fields: tuple[str, ...] = (
        "system",
        "core_logic",
        "pressure_points",
        "comic_forms",
        "risk_notes",
    )
    selection_policy: str = (
        "Use current life systems only when they create a concrete process, "
        "metric, rule, queue, complaint, KPI, platform, contract, or workflow "
        "that can collide with the persona card."
    )


class BrainholeGrowthStage(BaseModel):
    model_config = ConfigDict(frozen=True)

    stage_key: str
    chapter_range: str
    protagonist_capability: str
    decision_scope: str
    allowed_hr_actions: tuple[str, ...]
    forbidden_shortcuts: tuple[str, ...]


class BrainholeStageBindings(BaseModel):
    model_config = ConfigDict(frozen=True)

    book_spec: tuple[str, ...] = ("brainhole_market_promise",)
    cast_spec: tuple[str, ...] = ("persona_cards",)
    volume_plan: tuple[str, ...] = ("growth_stage_ladder", "modern_system_pool")
    chapter_outline: tuple[str, ...] = ("brainhole_contract",)
    review: tuple[str, ...] = ("persona_invariant_gate", "growth_stage_fit")


# ── Profile families ─────────────────────────────────────────────────────────
# The brainhole engine is a UNIVERSAL high-concept / 反差 / 名场面 effect skill that
# any genre can select.  Earlier the snapshot baked a single HR-recruiting-comedy
# contract (interview → dismiss → redesign recruiting system) as the default for
# every genre, so a 玄幻 / 悬疑 book that turned the 脑洞 effect on was told to write
# "面试 / 调岗 / 辞退" set-pieces.  We now keep that HR ladder ONLY for the
# mythic-workplace concept (招神 / 神仙 HR), and give every other genre a neutral
# "high-concept mechanism authority" ladder that manifests the 脑洞 in the book's
# own domain.

_WORKPLACE_GROWTH_STAGES: tuple[BrainholeGrowthStage, ...] = (
    BrainholeGrowthStage(
        stage_key="observe",
        chapter_range="opening",
        protagonist_capability="Can notice contradiction and make one low-risk offer.",
        decision_scope="observation, interview, recommendation",
        allowed_hr_actions=("interview", "recommend", "temporary_trial"),
        forbidden_shortcuts=("final dismissal", "system redesign", "god-level arbitration"),
    ),
    BrainholeGrowthStage(
        stage_key="assign",
        chapter_range="early-middle",
        protagonist_capability="Can match role, resource, and pressure after reading persona risk.",
        decision_scope="role assignment, probation, conflict mediation",
        allowed_hr_actions=("assign_role", "probation", "mediate_conflict"),
        forbidden_shortcuts=("erase character flaw", "solve by authority reveal"),
    ),
    BrainholeGrowthStage(
        stage_key="reshape",
        chapter_range="middle-late",
        protagonist_capability="Can use institutional leverage and accept counter-cost.",
        decision_scope="transfer, dismissal, new role creation, policy change",
        allowed_hr_actions=("transfer", "dismiss_with_cause", "create_role", "change_policy"),
        forbidden_shortcuts=("change mythic core for convenience", "free victory without backlash"),
    ),
    BrainholeGrowthStage(
        stage_key="system",
        chapter_range="late",
        protagonist_capability="Can redesign the recruitment system under public consequence.",
        decision_scope="system redesign, alliance restructuring, value judgment",
        allowed_hr_actions=("redesign_system", "public_arbitration", "institutional_bargain"),
        forbidden_shortcuts=("ignore accumulated costs", "reset relationships"),
    ),
)

_GENERIC_GROWTH_STAGES: tuple[BrainholeGrowthStage, ...] = (
    BrainholeGrowthStage(
        stage_key="observe",
        chapter_range="opening",
        protagonist_capability="Can notice the core contradiction and make one low-risk move.",
        decision_scope="observation, probing, a small reversible trial",
        allowed_hr_actions=("observe", "probe", "small_trial"),
        forbidden_shortcuts=("world-level arbitration", "irreversible escalation", "god-level shortcut"),
    ),
    BrainholeGrowthStage(
        stage_key="apply",
        chapter_range="early-middle",
        protagonist_capability="Can deploy the high-concept mechanism deliberately after reading the situation.",
        decision_scope="apply mechanism, leverage a constraint, mediate conflict",
        allowed_hr_actions=("apply_mechanism", "leverage_constraint", "mediate_conflict"),
        forbidden_shortcuts=("erase the core flaw", "solve by authority reveal"),
    ),
    BrainholeGrowthStage(
        stage_key="reshape",
        chapter_range="middle-late",
        protagonist_capability="Can bend the mechanism or local order and accept counter-cost.",
        decision_scope="escalate, reconfigure, create a new option, change a local rule",
        allowed_hr_actions=("escalate", "reconfigure", "create_new_option", "change_local_rule"),
        forbidden_shortcuts=("rewrite the core for convenience", "free victory without backlash"),
    ),
    BrainholeGrowthStage(
        stage_key="redefine",
        chapter_range="late",
        protagonist_capability="Can redefine the mechanism's rules under public consequence.",
        decision_scope="redefine rules, public reckoning, structural bargain",
        allowed_hr_actions=("redefine_rules", "public_reckoning", "structural_bargain"),
        forbidden_shortcuts=("ignore accumulated costs", "reset relationships"),
    ),
)

_WORKPLACE_CONTRAST_AXES = (
    "mythic_vs_workplace",
    "sacred_duty_vs_private_desire",
    "petty_process_vs_world_consequence",
    "persona_core_under_pressure",
    "growth_stage_unlock",
)
_GENERIC_CONTRAST_AXES = (
    "high_concept_vs_grounded",
    "duty_vs_private_desire",
    "small_mechanism_vs_world_consequence",
    "persona_core_under_pressure",
    "growth_stage_unlock",
)

_WORKPLACE_CONTRACT_FIELDS = (
    "one_sentence_sell",
    "character_core_used",
    "modern_system",
    "contrast_mechanism",
    "visible_comedy",
    "serious_underbelly",
    "plot_consequence",
    "protagonist_decision",
    "growth_stage_fit",
    "risk_check",
)
_GENERIC_CONTRACT_FIELDS = (
    "one_sentence_sell",
    "character_core_used",
    "novel_mechanism",
    "contrast_mechanism",
    "high_concept_setpiece",
    "tonal_consistency",
    "plot_consequence",
    "protagonist_decision",
    "growth_stage_fit",
    "risk_check",
)

_WORKPLACE_GROWTH_AXIS = (
    "Brainhole difficulty and HR authority must track protagonist growth: "
    "observe -> diagnose -> assign -> transfer -> dismiss -> create role "
    "-> redesign the recruiting system."
)
_GENERIC_GROWTH_AXIS = (
    "Brainhole difficulty and the protagonist's authority over the high-concept "
    "mechanism must track growth: observe -> apply -> leverage -> reshape -> "
    "redefine the mechanism's rules."
)

_WORKPLACE_GROWTH_GATE_EN = (
    "Growth gate: the protagonist's current capability decides which HR move is "
    "legal. Early chapters can observe, recommend, or offer a trial; later "
    "chapters may transfer, dismiss, create roles, or redesign policy only after "
    "earned authority and visible cost."
)
_GENERIC_GROWTH_GATE_EN = (
    "Growth gate: the protagonist's current capability decides how far the "
    "high-concept mechanism can be pushed. Early chapters can observe, probe, or "
    "run a small reversible trial; later chapters may escalate, reconfigure, "
    "create new options, or redefine rules only after earned authority and "
    "visible cost."
)
_WORKPLACE_GROWTH_GATE_ZH = (
    "成长安全门：主角当前能力决定本章能做什么 HR 动作。前期只能观察、推荐、试用；"
    "中后期必须在权威、代价、关系后果已经铺垫后，才能调岗、辞退、造岗或改制度。"
)
_GENERIC_GROWTH_GATE_ZH = (
    "成长安全门：主角当前能力决定本章能动用多大的脑洞机制。前期只能观察、试探、小幅可逆尝试；"
    "中后期必须在权威、代价、关系后果已经铺垫后，才能升级、重构、造出新解或改写规则。"
)

_WORKPLACE_RISK_TAIL_ZH = "笑点与主角当前成长阶段冲突"
_GENERIC_RISK_TAIL_ZH = "反差或情绪落点与主角当前成长阶段冲突"
_WORKPLACE_RISK_TAIL_EN = (
    "or comedy that contradicts the protagonist's current growth stage"
)
_GENERIC_RISK_TAIL_EN = (
    "or a contrast/emotional beat that contradicts the protagonist's current "
    "growth stage"
)


class BrainholeProfileSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)

    version: str = BRAINHOLE_PROFILE_VERSION
    profile_key: str
    genre: str
    sub_genre: str | None = None
    prompt_pack_key: str | None = None
    activation: BrainholeProfileActivation = Field(
        default_factory=BrainholeProfileActivation
    )
    persona_schema: BrainholePersonaCardSchema = Field(
        default_factory=BrainholePersonaCardSchema
    )
    modern_system_schema: BrainholeModernSystemSchema = Field(
        default_factory=BrainholeModernSystemSchema
    )
    stage_bindings: BrainholeStageBindings = Field(default_factory=BrainholeStageBindings)
    # Defaults are the GENRE-NEUTRAL family; resolve_brainhole_profile() swaps in
    # the workplace/HR family only for the mythic-workplace (招神/神仙 HR) concept.
    contrast_axes: tuple[str, ...] = _GENERIC_CONTRAST_AXES
    required_contract_fields: tuple[str, ...] = _GENERIC_CONTRACT_FIELDS
    protagonist_growth_axis: str = _GENERIC_GROWTH_AXIS
    growth_stages: tuple[BrainholeGrowthStage, ...] = _GENERIC_GROWTH_STAGES
    # Render-driving copy (so the prompt block carries no hardcoded HR prose).
    growth_gate_zh: str = _GENERIC_GROWTH_GATE_ZH
    growth_gate_en: str = _GENERIC_GROWTH_GATE_EN
    risk_tail_zh: str = _GENERIC_RISK_TAIL_ZH
    risk_tail_en: str = _GENERIC_RISK_TAIL_EN

    def to_metadata(self) -> dict[str, Any]:
        payload = self.model_dump(mode="json")
        payload["contrast_axes"] = list(self.contrast_axes)
        payload["required_contract_fields"] = list(self.required_contract_fields)
        return payload


def _resolve_profile_key(
    genre: str,
    sub_genre: str | None = None,
    prompt_pack_key: str | None = None,
) -> str:
    label = f"{genre} {sub_genre or ''} {prompt_pack_key or ''}".lower()
    # mythic-WORKPLACE (招神/神仙 HR) is a workplace comedy in a divine frame, so it needs an
    # explicit HR/招聘/职场 signal. Bare 仙/修仙/仙侠 (cultivation-progression) must NOT match —
    # else every 修仙 book is force-framed into an HR/契约/制度 ladder and the model keeps growing
    # 债契/宗债/记账/账册 金手指 (cross-book homogenization; see xianxia-upgrade 真机 books).
    workplace = any(
        t in label
        for t in ("招神", "hr", "人事", "职场", "入职", "面试", "招聘", "打工", "上班", "员工", "上岗")
    )
    mythic = any(t in label for t in ("神仙", "西游", "封神", "天庭", "神庭", "myth"))
    if "招神" in label or (mythic and workplace):
        return "mythic-workplace-brainhole"
    if any(token in label for token in ("都市", "职场", "现实", "urban")):
        return "urban-system-brainhole"
    if any(token in label for token in ("悬疑", "民俗", "怪谈", "suspense", "mystery")):
        return "mystery-rule-brainhole"
    return "general-serial-brainhole"


def resolve_brainhole_profile(
    genre: str,
    sub_genre: str | None = None,
    *,
    prompt_pack_key: str | None = None,
) -> BrainholeProfileSnapshot:
    """Resolve a deterministic novelty-generation profile snapshot.

    The workplace/HR ladder (面试→调岗→辞退→改制度) is reserved for the
    mythic-workplace concept (招神 / 神仙 HR comedy), where it is the actual
    premise.  Every other genre receives a genre-neutral high-concept-mechanism
    ladder so the 脑洞 manifests in that book's own domain instead of being
    forced into an HR-recruiting frame.
    """

    profile_key = _resolve_profile_key(genre, sub_genre, prompt_pack_key)
    if profile_key == "mythic-workplace-brainhole":
        return BrainholeProfileSnapshot(
            profile_key=profile_key,
            genre=genre,
            sub_genre=sub_genre,
            prompt_pack_key=prompt_pack_key,
            contrast_axes=_WORKPLACE_CONTRAST_AXES,
            required_contract_fields=_WORKPLACE_CONTRACT_FIELDS,
            protagonist_growth_axis=_WORKPLACE_GROWTH_AXIS,
            growth_stages=_WORKPLACE_GROWTH_STAGES,
            growth_gate_zh=_WORKPLACE_GROWTH_GATE_ZH,
            growth_gate_en=_WORKPLACE_GROWTH_GATE_EN,
            risk_tail_zh=_WORKPLACE_RISK_TAIL_ZH,
            risk_tail_en=_WORKPLACE_RISK_TAIL_EN,
        )
    return BrainholeProfileSnapshot(
        profile_key=profile_key,
        genre=genre,
        sub_genre=sub_genre,
        prompt_pack_key=prompt_pack_key,
    )


def attach_brainhole_profile(
    metadata: Mapping[str, Any] | None,
    profile: BrainholeProfileSnapshot,
) -> dict[str, Any]:
    """Return metadata with a serialized profile attached, without mutating input."""

    updated = dict(metadata or {})
    updated[BRAINHOLE_PROFILE_METADATA_KEY] = profile.to_metadata()
    return updated


def brainhole_profile_from_metadata(
    metadata: Mapping[str, Any] | None,
) -> BrainholeProfileSnapshot | None:
    raw = (metadata or {}).get(BRAINHOLE_PROFILE_METADATA_KEY)
    if not isinstance(raw, Mapping):
        return None
    try:
        return BrainholeProfileSnapshot.model_validate(raw)
    except ValueError:
        return None


def _profile_or_metadata(
    profile_or_metadata: BrainholeProfileSnapshot | Mapping[str, Any] | None,
) -> BrainholeProfileSnapshot | None:
    if isinstance(profile_or_metadata, BrainholeProfileSnapshot):
        return profile_or_metadata
    if isinstance(profile_or_metadata, Mapping):
        return brainhole_profile_from_metadata(profile_or_metadata)
    return None


def _render_stage_rows(profile: BrainholeProfileSnapshot, *, is_en: bool) -> str:
    rows: list[str] = []
    for stage in profile.growth_stages:
        if is_en:
            rows.append(
                "- "
                f"{stage.stage_key} ({stage.chapter_range}): capability="
                f"{stage.protagonist_capability}; decision_scope="
                f"{stage.decision_scope}; allowed_moves="
                f"{', '.join(stage.allowed_hr_actions)}; forbidden_shortcuts="
                f"{', '.join(stage.forbidden_shortcuts)}."
            )
        else:
            rows.append(
                "- "
                f"{stage.stage_key}（{stage.chapter_range}）：主角能力="
                f"{stage.protagonist_capability}；决策边界="
                f"{stage.decision_scope}；允许动作="
                f"{'、'.join(stage.allowed_hr_actions)}；禁止捷径="
                f"{'、'.join(stage.forbidden_shortcuts)}。"
            )
    return "\n".join(rows)


def render_brainhole_planner_prompt_block(
    profile_or_metadata: BrainholeProfileSnapshot | Mapping[str, Any] | None,
    *,
    language: str = "zh-CN",
) -> str:
    """Render the chapter-outline contract consumed by Planner prompts.

    Returns an empty string when the project does not carry the metadata.  This
    keeps legacy projects unchanged and lets new projects opt in through their
    project metadata snapshot.
    """

    profile = _profile_or_metadata(profile_or_metadata)
    if profile is None:
        return ""

    is_en = language.lower().startswith("en")
    fields = ", ".join(profile.required_contract_fields)
    axes = ", ".join(profile.contrast_axes)
    stages = _render_stage_rows(profile, is_en=is_en)

    if is_en:
        return (
            "[BRAINHOLE PLANNER CONTRACT]\n"
            f"Profile: {profile.profile_key} / {profile.version}. This is an "
            "audit-only novelty-generation contract for chapter outlines.\n"
            "Generate novelty by crossing: familiar persona card x modern "
            "system card x persona-safe contrast x protagonist growth stage x "
            "plot consequence.\n"
            f"Contrast axes: {axes}.\n"
            "Persona invariant gate: pressure the known core, do not casually "
            "rewrite it. If a character appears to betray a core value, the "
            "outline must include the external cause, internal cost, and repair "
            "path.\n"
            f"{profile.growth_gate_en}\n"
            f"Growth ladder:\n{stages}\n"
            "Each chapter must include `brainhole_contract` when this contract "
            "is present. Required fields: "
            f"{fields}.\n"
            "Risk check must reject: hard personality break, cheap insult, "
            "unearned protagonist shortcut, trend reference without plot "
            f"consequence, {profile.risk_tail_en}."
        )

    return (
        "【脑洞生成合同】\n"
        f"快照：{profile.profile_key} / {profile.version}。这是章节大纲阶段的"
        "审计型脑洞生成能力。\n"
        "脑洞公式：大众熟悉的人物常识卡 × 现代系统卡 × 不伤核心人设的反差 × "
        "主角成长阶段 × 剧情后果。\n"
        f"反差轴：{axes}。\n"
        "人设安全门：可以压迫角色核心，不能随手改写角色核心。若角色看似背叛核心价值，"
        "大纲必须写清外部诱因、内部代价、以及后续圆回路径。\n"
        f"{profile.growth_gate_zh}\n"
        f"成长阶梯：\n{stages}\n"
        "只要本合同存在，每章必须输出 `brainhole_contract`。必填字段："
        f"{fields}。\n"
        "risk_check 必须排除：硬改人设、低级冒犯、主角未成长就越权解决、热点只贴标签无剧情后果、"
        f"{profile.risk_tail_zh}。"
    )


__all__ = [
    "BRAINHOLE_PROFILE_METADATA_KEY",
    "BRAINHOLE_PROFILE_VERSION",
    "BrainholeGrowthStage",
    "BrainholeModernSystemSchema",
    "BrainholePersonaCardSchema",
    "BrainholeProfileActivation",
    "BrainholeProfileSnapshot",
    "BrainholeStageBindings",
    "attach_brainhole_profile",
    "brainhole_profile_from_metadata",
    "render_brainhole_planner_prompt_block",
    "resolve_brainhole_profile",
]
