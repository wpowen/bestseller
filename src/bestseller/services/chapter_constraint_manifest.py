"""Pre-write chapter constraints and plan validation.

This module is intentionally pre-generative: it turns scattered bible/canon/
scene context into a small executable contract, then validates the writer's
declared plan before prose generation starts.
"""

# ruff: noqa: ANN401, RUF001

from __future__ import annotations

import json
import re
from typing import Any

from pydantic import BaseModel, Field, field_validator

from bestseller.services.canon_guardrails import CanonGuardrails
from bestseller.services.common_sense_gate import evaluate_common_sense_gate
from bestseller.services.methodology_bridge import get_fragment
from bestseller.services.prompt_packs import PromptPack


class OpeningCausalityContract(BaseModel):
    protagonist_entry_motivation: str = ""
    protagonist_function: str = ""
    visible_failure_cost: str = ""
    required_scene_question: str = ""


class TimeBudgetContract(BaseModel):
    start: str = ""
    deadline: str = ""
    current_remaining: str = ""
    allowed_elapsed_events: list[str] = Field(default_factory=list)
    forbid_untracked_travel: bool = False


class BodyObjectStateContract(BaseModel):
    tracked_objects: dict[str, str] = Field(default_factory=dict)
    tracked_body_states: dict[str, str] = Field(default_factory=dict)
    require_visible_cause_for_bleeding: bool = True


class EndingHookContract(BaseModel):
    allowed_hook_types: list[str] = Field(default_factory=list)
    required_hook_target: str = ""
    forbidden_ending_modes: list[str] = Field(default_factory=list)


class ChapterConstraintManifest(BaseModel):
    chapter_number: int = Field(ge=1)
    scene_number: int | None = Field(default=None, ge=1)
    allowed_characters: list[str] = Field(default_factory=list)
    characters_must_not_appear: list[str] = Field(default_factory=list)
    characters_off_screen_only: list[str] = Field(default_factory=list)
    forbidden_terms: list[str] = Field(default_factory=list)
    required_time_anchors: list[str] = Field(default_factory=list)
    allowed_time_anchors: list[str] = Field(default_factory=list)
    allowed_relative_time_expressions: list[str] = Field(default_factory=list)
    forbidden_time_anchors: list[str] = Field(default_factory=list)
    allowed_locations: list[str] = Field(default_factory=list)
    must_echo_hooks_from_prev: list[str] = Field(default_factory=list)
    must_use_protagonist_abilities: list[str] = Field(default_factory=list)
    must_avoid_protagonist_vocabulary: list[str] = Field(default_factory=list)
    chapter_target_state_end: dict[str, Any] = Field(default_factory=dict)
    opening_causality_contract: OpeningCausalityContract | None = None
    time_budget_contract: TimeBudgetContract | None = None
    body_object_state_contract: BodyObjectStateContract | None = None
    ending_hook_contract: EndingHookContract | None = None


class PrewritePlan(BaseModel):
    characters_to_use: list[str] = Field(default_factory=list)
    time_anchors_to_use: list[str] = Field(default_factory=list)
    locations_to_use: list[str] = Field(default_factory=list)
    hooks_to_echo: list[str] = Field(default_factory=list)
    protagonist_abilities_to_use: list[str] = Field(default_factory=list)
    vocabulary_to_avoid: list[str] = Field(default_factory=list)
    target_state_end: dict[str, Any] = Field(default_factory=dict)
    protagonist_entry_motivation: str = ""
    protagonist_function: str = ""
    visible_failure_cost: str = ""
    time_budget_plan: dict[str, Any] = Field(default_factory=dict)
    body_object_state_plan: dict[str, Any] = Field(default_factory=dict)
    ending_hook_type: str = ""
    ending_hook_target: str = ""
    ending_modes_to_avoid: list[str] = Field(default_factory=list)

    @field_validator(
        "characters_to_use",
        "time_anchors_to_use",
        "locations_to_use",
        "hooks_to_echo",
        "protagonist_abilities_to_use",
        "vocabulary_to_avoid",
        "ending_modes_to_avoid",
        mode="before",
    )
    @classmethod
    def _coerce_string_list_field(cls, value: Any) -> list[str]:
        return _string_list(value)

    @field_validator(
        "time_budget_plan",
        "body_object_state_plan",
        "target_state_end",
        mode="before",
    )
    @classmethod
    def _coerce_mapping_field(cls, value: Any) -> dict[str, Any]:
        if value is None:
            return {}
        if isinstance(value, dict):
            return value
        if isinstance(value, str):
            text = value.strip()
            return {"summary": text} if text else {}
        if isinstance(value, (list, tuple, set)):
            items = _string_list(value)
            return {"elapsed_events": items} if items else {}
        return {}

    @field_validator("ending_hook_type", "ending_hook_target", mode="before")
    @classmethod
    def _coerce_string_field(cls, value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, str):
            return value.strip()
        if isinstance(value, (list, tuple, set)):
            items = _string_list(value)
            return items[0] if items else ""
        return str(value).strip()


class PlanValidationResult(BaseModel):
    passed: bool
    violations: list[str] = Field(default_factory=list)


class ProsePromotionValidationResult(BaseModel):
    passed: bool
    violations: list[str] = Field(default_factory=list)


def compile_chapter_constraint_manifest(
    *,
    chapter_number: int,
    scene_number: int | None = None,
    participants: list[str] | tuple[str, ...] | None = None,
    scene_time_label: str | None = None,
    scene_metadata: dict[str, Any] | None = None,
    scene_exit_state: dict[str, Any] | None = None,
    story_bible_context: dict[str, Any] | None = None,
    hard_fact_snapshot: dict[str, Any] | None = None,
    recent_timeline_events: list[dict[str, Any]] | None = None,
    hook_requirement: str | None = None,
    canon_guardrails: CanonGuardrails | None = None,
    project_metadata: dict[str, Any] | None = None,
) -> ChapterConstraintManifest:
    """Compile the strongest available pre-write constraints.

    The manifest is conservative: scene participants become the character
    allow-list, canon guardrails supply the do-not-appear list, and existing
    time/location/state facts become explicit anchors.
    """

    metadata = project_metadata if isinstance(project_metadata, dict) else {}
    scene_meta = scene_metadata if isinstance(scene_metadata, dict) else {}
    bible = story_bible_context if isinstance(story_bible_context, dict) else {}

    forbidden_characters: list[str] = []
    forbidden_terms: list[str] = []
    if canon_guardrails is not None:
        for rule in canon_guardrails.state_rules:
            if rule.applies_after_chapter is None or chapter_number <= rule.applies_after_chapter:
                forbidden_characters.append(rule.subject)
        forbidden_terms.extend(term.term for term in canon_guardrails.forbidden_terms)

    time_anchors = _dedupe_strings(
        [
            scene_time_label,
            _nested_get(hard_fact_snapshot, "time_anchor"),
            *[
                str(item.get("story_time_label") or "")
                for item in recent_timeline_events or []
                if isinstance(item, dict)
            ],
            *_string_list(metadata.get("allowed_time_anchors")),
            *_string_list(bible.get("allowed_time_anchors")),
        ]
    )
    required_time_anchors = _dedupe_strings(
        [
            *_string_list(metadata.get("required_time_anchors")),
            *_string_list(bible.get("required_time_anchors")),
        ]
    )
    relative_time_expressions = _dedupe_strings(
        [
            *_string_list(metadata.get("allowed_relative_time_expressions")),
            *_string_list(bible.get("allowed_relative_time_expressions")),
            "几秒后",
            "十几秒后",
            "几分钟后",
            "半分钟后",
            "一会儿",
            "片刻后",
            "转眼",
            "没多久",
            "半小时前",
            "二十分钟前",
            "刚才",
        ]
    )
    locations = _dedupe_strings(
        [
            scene_meta.get("location"),
            scene_meta.get("location_name"),
            scene_meta.get("location_tag"),
            *_string_list(_nested_get(bible, "volume", "active_locations")),
            *_string_list(metadata.get("allowed_locations")),
            *_string_list(bible.get("allowed_locations")),
        ]
    )
    opening_contract = _compile_opening_causality_contract(
        chapter_number=chapter_number,
        scene_number=scene_number,
        scene_metadata=scene_meta,
        story_bible_context=bible,
        hook_requirement=hook_requirement,
        project_metadata=metadata,
    )
    time_budget_contract = _compile_time_budget_contract(
        chapter_number=chapter_number,
        scene_number=scene_number,
        scene_metadata=scene_meta,
        story_bible_context=bible,
        project_metadata=metadata,
    )
    body_object_contract = _compile_body_object_state_contract(
        chapter_number=chapter_number,
        scene_number=scene_number,
        scene_metadata=scene_meta,
        story_bible_context=bible,
        project_metadata=metadata,
    )
    ending_hook_contract = _compile_ending_hook_contract(
        chapter_number=chapter_number,
        scene_number=scene_number,
        scene_metadata=scene_meta,
        story_bible_context=bible,
        hook_requirement=hook_requirement,
        project_metadata=metadata,
    )

    return ChapterConstraintManifest(
        chapter_number=chapter_number,
        scene_number=scene_number,
        allowed_characters=_dedupe_strings(participants or []),
        characters_must_not_appear=_dedupe_strings(forbidden_characters),
        characters_off_screen_only=_dedupe_strings(
            [
                *_string_list(metadata.get("characters_off_screen_only")),
                *_string_list(bible.get("characters_off_screen_only")),
            ]
        ),
        forbidden_terms=_dedupe_strings(
            [*forbidden_terms, *_string_list(metadata.get("forbidden_terms"))]
        ),
        required_time_anchors=required_time_anchors,
        allowed_time_anchors=time_anchors,
        allowed_relative_time_expressions=relative_time_expressions,
        forbidden_time_anchors=_dedupe_strings(
            [
                *_string_list(metadata.get("forbidden_time_anchors")),
                *_string_list(bible.get("forbidden_time_anchors")),
            ]
        ),
        allowed_locations=locations,
        must_echo_hooks_from_prev=_dedupe_strings([hook_requirement]),
        must_use_protagonist_abilities=_dedupe_strings(
            [
                *_string_list(metadata.get("protagonist_abilities")),
                *_string_list(bible.get("protagonist_abilities")),
                *_string_list(_nested_get(bible, "protagonist", "abilities")),
            ]
        ),
        must_avoid_protagonist_vocabulary=_dedupe_strings(
            [
                *_string_list(metadata.get("protagonist_forbidden_vocabulary")),
                *_string_list(bible.get("protagonist_forbidden_vocabulary")),
            ]
        ),
        chapter_target_state_end={
            "scene_exit_state": scene_exit_state or {},
        }
        if scene_exit_state
        else {},
        opening_causality_contract=opening_contract,
        time_budget_contract=time_budget_contract,
        body_object_state_contract=body_object_contract,
        ending_hook_contract=ending_hook_contract,
    )


def build_safe_prewrite_plan(manifest: ChapterConstraintManifest) -> PrewritePlan:
    """Create a deterministic plan that is guaranteed to satisfy the manifest."""

    return PrewritePlan(
        characters_to_use=list(manifest.allowed_characters),
        time_anchors_to_use=list(manifest.allowed_time_anchors[:3]),
        locations_to_use=list(manifest.allowed_locations[:3]),
        hooks_to_echo=list(manifest.must_echo_hooks_from_prev),
        protagonist_abilities_to_use=list(manifest.must_use_protagonist_abilities),
        vocabulary_to_avoid=list(manifest.must_avoid_protagonist_vocabulary),
        target_state_end=dict(manifest.chapter_target_state_end),
        protagonist_entry_motivation=(
            manifest.opening_causality_contract.protagonist_entry_motivation
            if manifest.opening_causality_contract
            else ""
        ),
        protagonist_function=(
            manifest.opening_causality_contract.protagonist_function
            if manifest.opening_causality_contract
            else ""
        ),
        visible_failure_cost=(
            manifest.opening_causality_contract.visible_failure_cost
            if manifest.opening_causality_contract
            else ""
        ),
        time_budget_plan=_safe_time_budget_plan(manifest.time_budget_contract),
        body_object_state_plan=_safe_body_object_state_plan(
            manifest.body_object_state_contract
        ),
        ending_hook_type=_safe_ending_hook_type(manifest.ending_hook_contract),
        ending_hook_target=(
            manifest.ending_hook_contract.required_hook_target
            if manifest.ending_hook_contract
            else ""
        ),
        ending_modes_to_avoid=(
            list(manifest.ending_hook_contract.forbidden_ending_modes)
            if manifest.ending_hook_contract
            else []
        ),
    )


def parse_prewrite_plan(raw: str) -> PrewritePlan:
    """Parse a model response into a ``PrewritePlan``.

    Accepts either a plain JSON object or fenced JSON. Raises ``ValueError``
    when no object can be recovered.
    """

    text = (raw or "").strip()
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.S)
    if match:
        text = match.group(1)
    elif "{" in text and "}" in text:
        text = text[text.find("{") : text.rfind("}") + 1]
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError("prewrite plan is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError("prewrite plan must be a JSON object")
    return PrewritePlan.model_validate(payload)


def normalize_prewrite_plan_for_manifest(
    plan: PrewritePlan,
    manifest: ChapterConstraintManifest,
) -> PrewritePlan:
    """Clamp a parsed plan to the manifest before it reaches prose prompts.

    Frontier models sometimes return schema-shaped but contract-invalid helper
    values: list-valued hook types, freeform elapsed-time summaries, or
    off-whitelist participants. The writer prompt should receive a conservative
    executable plan, not those raw declarations. This function keeps useful
    allowed choices and falls back to the deterministic safe contract for
    constrained fields.
    """

    safe = build_safe_prewrite_plan(manifest)

    def _allowed_or_safe(
        values: list[str],
        allowed: list[str],
        fallback: list[str],
    ) -> list[str]:
        if not allowed:
            return list(dict.fromkeys(values))
        kept = [item for item in values if item in set(allowed)]
        return kept or list(fallback)

    ending_hook_type = plan.ending_hook_type
    if (
        manifest.ending_hook_contract is not None
        and manifest.ending_hook_contract.allowed_hook_types
        and ending_hook_type not in manifest.ending_hook_contract.allowed_hook_types
    ):
        ending_hook_type = safe.ending_hook_type

    ending_modes_to_avoid = list(dict.fromkeys(plan.ending_modes_to_avoid))
    if manifest.ending_hook_contract is not None:
        ending_modes_to_avoid = list(
            dict.fromkeys(
                [
                    *ending_modes_to_avoid,
                    *manifest.ending_hook_contract.forbidden_ending_modes,
                ]
            )
        )

    return plan.model_copy(
        update={
            "characters_to_use": _allowed_or_safe(
                plan.characters_to_use,
                manifest.allowed_characters,
                safe.characters_to_use,
            ),
            "time_anchors_to_use": _allowed_or_safe(
                plan.time_anchors_to_use,
                manifest.allowed_time_anchors,
                safe.time_anchors_to_use,
            ),
            "locations_to_use": _allowed_or_safe(
                plan.locations_to_use,
                manifest.allowed_locations,
                safe.locations_to_use,
            ),
            "vocabulary_to_avoid": list(
                dict.fromkeys(
                    [
                        *plan.vocabulary_to_avoid,
                        *manifest.must_avoid_protagonist_vocabulary,
                    ]
                )
            ),
            "target_state_end": (
                dict(manifest.chapter_target_state_end)
                if manifest.chapter_target_state_end
                else dict(plan.target_state_end)
            ),
            "protagonist_entry_motivation": (
                safe.protagonist_entry_motivation
                if manifest.opening_causality_contract is not None
                else plan.protagonist_entry_motivation
            ),
            "protagonist_function": (
                safe.protagonist_function
                if manifest.opening_causality_contract is not None
                else plan.protagonist_function
            ),
            "visible_failure_cost": (
                safe.visible_failure_cost
                if manifest.opening_causality_contract is not None
                else plan.visible_failure_cost
            ),
            "time_budget_plan": (
                dict(safe.time_budget_plan)
                if manifest.time_budget_contract is not None
                else dict(plan.time_budget_plan)
            ),
            "body_object_state_plan": (
                dict(safe.body_object_state_plan)
                if manifest.body_object_state_contract is not None
                else dict(plan.body_object_state_plan)
            ),
            "ending_hook_type": ending_hook_type,
            "ending_hook_target": (
                safe.ending_hook_target
                if manifest.ending_hook_contract is not None
                else plan.ending_hook_target
            ),
            "ending_modes_to_avoid": ending_modes_to_avoid,
        }
    )


def validate_prewrite_plan(
    plan: PrewritePlan,
    manifest: ChapterConstraintManifest,
) -> PlanValidationResult:
    violations: list[str] = []

    allowed_characters = set(manifest.allowed_characters)
    if allowed_characters:
        for name in plan.characters_to_use:
            if name not in allowed_characters:
                violations.append(f"character '{name}' is not in allowed_characters")

    forbidden_characters = set(manifest.characters_must_not_appear)
    for name in plan.characters_to_use:
        if name in forbidden_characters:
            violations.append(f"character '{name}' is forbidden in this chapter")
        if name in set(manifest.characters_off_screen_only):
            violations.append(f"character '{name}' is off-screen only in this chapter")

    forbidden_times = set(manifest.forbidden_time_anchors)
    for anchor in plan.time_anchors_to_use:
        if anchor in forbidden_times:
            violations.append(f"time anchor '{anchor}' is forbidden")

    allowed_times = set(manifest.allowed_time_anchors)
    if allowed_times:
        for anchor in plan.time_anchors_to_use:
            if anchor not in allowed_times and not _is_allowed_relative_time(
                anchor,
                manifest.allowed_relative_time_expressions,
            ):
                violations.append(f"time anchor '{anchor}' is not in allowed_time_anchors")

    allowed_locations = set(manifest.allowed_locations)
    if allowed_locations:
        for location in plan.locations_to_use:
            if location not in allowed_locations:
                violations.append(f"location '{location}' is not in allowed_locations")

    missing_avoidance = [
        word
        for word in manifest.must_avoid_protagonist_vocabulary
        if word not in plan.vocabulary_to_avoid
    ]
    if missing_avoidance:
        violations.append(
            "plan omitted must_avoid_protagonist_vocabulary: "
            + ", ".join(missing_avoidance)
        )

    if manifest.opening_causality_contract is not None:
        _validate_opening_contract(plan, manifest.opening_causality_contract, violations)
    if manifest.time_budget_contract is not None:
        _validate_time_budget_contract(plan, manifest.time_budget_contract, violations)
    if manifest.body_object_state_contract is not None:
        _validate_body_object_contract(
            plan,
            manifest.body_object_state_contract,
            violations,
        )
    if manifest.ending_hook_contract is not None:
        _validate_ending_hook_contract(plan, manifest.ending_hook_contract, violations)

    return PlanValidationResult(passed=not violations, violations=violations)


def validate_chapter_prose_for_promotion(
    text: str,
    manifest: ChapterConstraintManifest,
    *,
    genre: str | None = None,
    sub_genre: str | None = None,
) -> ProsePromotionValidationResult:
    """Validate generated prose before it may be treated as publishable.

    ``validate_prewrite_plan`` only proves that the model declared a safe plan.
    It does not prove that the resulting chapter obeyed its own local causes,
    time arithmetic, or object state. This gate therefore runs deterministic
    prose checks after generation. The checks are genre-aware and only block
    contradictions inside the prose, not legitimate supernatural premises.
    """

    content = text or ""
    violations: list[str] = []
    if not content.strip():
        violations.append("chapter prose is empty")

    for term in [*manifest.forbidden_terms, *manifest.must_avoid_protagonist_vocabulary]:
        if term and term in content:
            violations.append(f"chapter prose contains forbidden term '{term}'")

    for character in manifest.characters_must_not_appear:
        if character and character in content:
            violations.append(f"chapter prose contains forbidden character '{character}'")

    _validate_prose_time_budget(content, manifest.time_budget_contract, violations)
    _validate_prose_body_object_state(
        content,
        manifest.body_object_state_contract,
        violations,
    )
    _validate_prose_ending_hook(content, manifest.ending_hook_contract, violations)
    common_sense = evaluate_common_sense_gate(
        content,
        genre=genre,
        sub_genre=sub_genre,
        chapter_number=manifest.chapter_number,
    )
    for finding in common_sense.findings:
        if finding.severity in {"high", "medium"}:
            marker = str(finding.evidence.get("marker") or "").strip()
            suffix = f" marker={marker}" if marker else ""
            violations.append(f"{finding.code}: {finding.message}{suffix}")

    return ProsePromotionValidationResult(
        passed=not violations,
        violations=violations,
    )


def render_constraint_manifest_block(
    manifest: ChapterConstraintManifest,
    *,
    language: str = "zh-CN",
) -> str:
    payload = json.dumps(manifest.model_dump(mode="json"), ensure_ascii=False, indent=2)
    if language.lower().startswith("zh"):
        return (
            "【写前约束清单 — 优先级最高】\n"
            "以下 JSON 是本场景可执行合同。只能在白名单内选择真人出场/地点；"
            "required_time_anchors 是关键节点，不等于禁用合理相对时间表达；"
            "characters_off_screen_only 只能以门外声音、电话、影子等离场方式出现。\n"
            f"```json\n{payload}\n```"
        )
    return (
        "[PRE-WRITE CONSTRAINT MANIFEST — HIGHEST PRIORITY]\n"
        "This JSON is the executable scene contract. Choose characters, time "
        "anchors, and locations only from allow-lists when present.\n"
        f"```json\n{payload}\n```"
    )


def render_prewrite_plan_block(
    plan: PrewritePlan,
    *,
    language: str = "zh-CN",
) -> str:
    payload = json.dumps(plan.model_dump(mode="json"), ensure_ascii=False, indent=2)
    if language.lower().startswith("zh"):
        return (
            "【已验证写作计划 — 正文必须严格执行】\n"
            "下列计划已通过约束校验。正文只能按此计划展开，禁止临时新增人物、时间锚或地点。\n"
            f"```json\n{payload}\n```"
        )
    return (
        "[VALIDATED PRE-WRITE PLAN]\n"
        "The prose must follow this validated plan; do not add unplanned "
        "characters, time anchors, or locations.\n"
        f"```json\n{payload}\n```"
    )


def render_prewrite_plan_prompt(
    manifest: ChapterConstraintManifest,
    *,
    language: str = "zh-CN",
    pack: PromptPack | None = None,
    chapter_number: int | None = None,
) -> str:
    payload = json.dumps(manifest.model_dump(mode="json"), ensure_ascii=False, indent=2)
    methodology_lines: list[str] = []

    density_rule = get_fragment(pack, phase="prewrite", fragment_key="information_density")
    if density_rule and (chapter_number is None or chapter_number <= 10):
        methodology_lines.append(f"【信息密度规则】\n{density_rule}")

    spring_rule = get_fragment(pack, phase="prewrite", fragment_key="spring_model")
    if spring_rule:
        methodology_lines.append(f"【情绪压缩弹簧法】\n{spring_rule}")

    stakes_rule = get_fragment(pack, phase="prewrite", fragment_key="stakes_design")
    if stakes_rule:
        methodology_lines.append(f"【冲突筹码设计】\n{stakes_rule}")

    methodology_block = "\n\n".join(methodology_lines)
    if language.lower().startswith("zh"):
        methodology_section = (
            "\n\n## 写作方法论参考（用于影响场景计划的字段选择）\n"
            f"{methodology_block}\n"
            if methodology_block
            else ""
        )
        return (
            "根据以下写前约束清单和方法论参考，先声明本场景写作计划。只输出 JSON，不要输出正文。\n"
            "字段必须为：characters_to_use, time_anchors_to_use, locations_to_use, "
            "hooks_to_echo, protagonist_abilities_to_use, vocabulary_to_avoid, target_state_end, "
            "protagonist_entry_motivation, protagonist_function, visible_failure_cost, "
            "time_budget_plan, body_object_state_plan, ending_hook_type, ending_hook_target, "
            "ending_modes_to_avoid。\n"
            "类型要求: time_budget_plan、body_object_state_plan、target_state_end "
            "必须是 JSON 对象; ending_hook_type、ending_hook_target 必须是字符串; "
            "其他复数字段必须是字符串数组。"
            "所有选择必须来自约束白名单; 如某项无白名单, 保持空数组/空对象。"
            "动机、主角作用、失败代价必须照抄合同值; "
            "时间消耗和身体/物件状态必须先登记再写正文。\n"
            f"约束清单：\n```json\n{payload}\n```{methodology_section}"
        )
    methodology_section = (
        "\n\n## Writing methodology reference (use it when choosing plan fields)\n"
        f"{methodology_block}\n"
        if methodology_block
        else ""
    )
    return (
        "Declare the scene writing plan from this constraint manifest. Output "
        "JSON only, no prose. Use the methodology reference when selecting "
        "plan fields. Required fields: characters_to_use, "
        "time_anchors_to_use, locations_to_use, hooks_to_echo, "
        "protagonist_abilities_to_use, vocabulary_to_avoid, target_state_end, "
        "protagonist_entry_motivation, protagonist_function, visible_failure_cost, "
        "time_budget_plan, body_object_state_plan, ending_hook_type, "
        "ending_hook_target, ending_modes_to_avoid.\n"
        "Type requirements: time_budget_plan, body_object_state_plan, and "
        "target_state_end must be JSON objects; ending_hook_type and "
        "ending_hook_target must be strings; plural fields must be arrays of strings.\n"
        f"Manifest:\n```json\n{payload}\n```{methodology_section}"
    )


def _compile_opening_causality_contract(
    *,
    chapter_number: int,
    scene_number: int | None,
    scene_metadata: dict[str, Any],
    story_bible_context: dict[str, Any],
    hook_requirement: str | None,
    project_metadata: dict[str, Any],
) -> OpeningCausalityContract | None:
    explicit = _first_scoped_mapping(
        chapter_number,
        scene_metadata.get("opening_causality_contract"),
        project_metadata.get("opening_causality_contract"),
        story_bible_context.get("opening_causality_contract"),
    )
    opening_quality = _first_mapping(
        scene_metadata.get("opening_quality_contract"),
        project_metadata.get("opening_quality_contract"),
        project_metadata.get("qimao_opening_contract"),
        story_bible_context.get("opening_quality_contract"),
    )
    if chapter_number > 3 and explicit is None:
        return None

    source = explicit or {}
    motivation = _first_text(
        source.get("protagonist_entry_motivation"),
        source.get("entry_motivation"),
        scene_metadata.get("protagonist_entry_motivation"),
        opening_quality.get("protagonist_entry_motivation") if opening_quality else None,
        opening_quality.get("opening_incident") if opening_quality else None,
    )
    function = _first_text(
        source.get("protagonist_function"),
        scene_metadata.get("protagonist_function"),
        opening_quality.get("protagonist_function") if opening_quality else None,
        opening_quality.get("protagonist_edge") if opening_quality else None,
    )
    failure_cost = _first_text(
        source.get("visible_failure_cost"),
        scene_metadata.get("visible_failure_cost"),
        opening_quality.get("visible_failure_cost") if opening_quality else None,
        opening_quality.get("visible_loss_if_fail") if opening_quality else None,
    )
    question = _first_text(
        source.get("required_scene_question"),
        scene_metadata.get("required_scene_question"),
        hook_requirement,
        _opening_quality_chapter_task(opening_quality, chapter_number),
    )

    if not any((motivation, function, failure_cost, question)) and scene_number != 1:
        return None
    return OpeningCausalityContract(
        protagonist_entry_motivation=motivation,
        protagonist_function=function,
        visible_failure_cost=failure_cost,
        required_scene_question=question,
    )


def _compile_time_budget_contract(
    *,
    chapter_number: int,
    scene_number: int | None,
    scene_metadata: dict[str, Any],
    story_bible_context: dict[str, Any],
    project_metadata: dict[str, Any],
) -> TimeBudgetContract | None:
    source = _first_scoped_mapping(
        chapter_number,
        scene_metadata.get("time_budget_contract"),
        scene_metadata.get("time_budget"),
        project_metadata.get("time_budget_contract"),
        story_bible_context.get("time_budget_contract"),
    )
    if source is None and chapter_number > 3:
        return None
    if source is None and scene_number != 1:
        return None
    source = source or {}
    return TimeBudgetContract(
        start=_first_text(source.get("start"), scene_metadata.get("start_time")),
        deadline=_first_text(source.get("deadline"), scene_metadata.get("deadline")),
        current_remaining=_first_text(
            source.get("current_remaining"),
            source.get("remaining"),
            scene_metadata.get("current_remaining"),
        ),
        allowed_elapsed_events=_dedupe_strings(
            [
                *_string_list(source.get("allowed_elapsed_events")),
                *_string_list(scene_metadata.get("allowed_elapsed_events")),
            ]
        ),
        forbid_untracked_travel=bool(source.get("forbid_untracked_travel", True)),
    )


def _compile_body_object_state_contract(
    *,
    chapter_number: int,
    scene_number: int | None,
    scene_metadata: dict[str, Any],
    story_bible_context: dict[str, Any],
    project_metadata: dict[str, Any],
) -> BodyObjectStateContract | None:
    source = _first_scoped_mapping(
        chapter_number,
        scene_metadata.get("body_object_state_contract"),
        project_metadata.get("body_object_state_contract"),
        story_bible_context.get("body_object_state_contract"),
    )
    if source is None and chapter_number > 3:
        return None
    if source is None and scene_number != 1:
        return None
    source = source or {}
    return BodyObjectStateContract(
        tracked_objects=_string_map(source.get("tracked_objects")),
        tracked_body_states=_string_map(source.get("tracked_body_states")),
        require_visible_cause_for_bleeding=bool(
            source.get("require_visible_cause_for_bleeding", True)
        ),
    )


def _compile_ending_hook_contract(
    *,
    chapter_number: int,
    scene_number: int | None,
    scene_metadata: dict[str, Any],
    story_bible_context: dict[str, Any],
    hook_requirement: str | None,
    project_metadata: dict[str, Any],
) -> EndingHookContract | None:
    source = _first_scoped_mapping(
        chapter_number,
        scene_metadata.get("ending_hook_contract"),
        project_metadata.get("ending_hook_contract"),
        story_bible_context.get("ending_hook_contract"),
    )
    if source is None and chapter_number > 3:
        return None
    if source is None and scene_number not in (None, 1):
        return None
    source = source or {}
    hook_types = _dedupe_strings(
        [
            *_string_list(source.get("allowed_hook_types")),
            *_string_list(project_metadata.get("chapter_ending_hook_types")),
        ]
    ) or ["新变量", "未答问题", "危机升级", "身份反转", "强制选择"]
    forbidden_modes = _dedupe_strings(
        [
            *_string_list(source.get("forbidden_ending_modes")),
            *_string_list(project_metadata.get("forbidden_ending_modes")),
        ]
    ) or ["总结主题", "作者式预告", "硬转下一章", "口号式收束"]
    return EndingHookContract(
        allowed_hook_types=hook_types,
        required_hook_target=_first_text(
            source.get("required_hook_target"),
            hook_requirement,
            _opening_quality_chapter_task(
                _first_mapping(
                    project_metadata.get("opening_quality_contract"),
                    project_metadata.get("qimao_opening_contract"),
                ),
                chapter_number,
            ),
        ),
        forbidden_ending_modes=forbidden_modes,
    )


def _validate_opening_contract(
    plan: PrewritePlan,
    contract: OpeningCausalityContract,
    violations: list[str],
) -> None:
    required = {
        "protagonist_entry_motivation": contract.protagonist_entry_motivation,
        "protagonist_function": contract.protagonist_function,
        "visible_failure_cost": contract.visible_failure_cost,
    }
    actual = {
        "protagonist_entry_motivation": plan.protagonist_entry_motivation,
        "protagonist_function": plan.protagonist_function,
        "visible_failure_cost": plan.visible_failure_cost,
    }
    for key, value in required.items():
        if value and not _contract_value_matches(actual.get(key), value):
            violations.append(f"plan omitted or changed {key}: {value}")


def _validate_time_budget_contract(
    plan: PrewritePlan,
    contract: TimeBudgetContract,
    violations: list[str],
) -> None:
    budget = plan.time_budget_plan if isinstance(plan.time_budget_plan, dict) else {}
    if not budget:
        violations.append("plan omitted time_budget_plan")
        return
    if contract.forbid_untracked_travel:
        raw = json.dumps(budget, ensure_ascii=False)
        if _mentions_travel(raw) and not _budget_mentions_allowed_event(
            budget,
            contract.allowed_elapsed_events,
        ):
            violations.append(
                "time_budget_plan includes travel or elapsed time not registered "
                "in allowed_elapsed_events"
            )


def _validate_body_object_contract(
    plan: PrewritePlan,
    contract: BodyObjectStateContract,
    violations: list[str],
) -> None:
    state_plan = (
        plan.body_object_state_plan
        if isinstance(plan.body_object_state_plan, dict)
        else {}
    )
    if (contract.tracked_objects or contract.tracked_body_states) and not state_plan:
        violations.append("plan omitted body_object_state_plan")
        return
    raw = json.dumps(state_plan, ensure_ascii=False)
    for item in contract.tracked_objects:
        if item not in raw:
            violations.append(f"body_object_state_plan does not track object: {item}")
    for item in contract.tracked_body_states:
        if item not in raw:
            violations.append(f"body_object_state_plan does not track body state: {item}")
    if contract.require_visible_cause_for_bleeding and "血" in raw and not any(
        token in raw for token in ("原因", "触发", "伤口", "割破", "咬破", "硌破")
    ):
        violations.append("body_object_state_plan mentions blood without visible cause")


def _validate_ending_hook_contract(
    plan: PrewritePlan,
    contract: EndingHookContract,
    violations: list[str],
) -> None:
    if contract.allowed_hook_types and plan.ending_hook_type not in contract.allowed_hook_types:
        violations.append(
            "ending_hook_type must be one of: "
            + ", ".join(contract.allowed_hook_types)
        )
    if contract.required_hook_target and not _contract_value_matches(
        plan.ending_hook_target,
        contract.required_hook_target,
    ):
        violations.append(
            f"ending_hook_target does not satisfy required target: "
            f"{contract.required_hook_target}"
        )
    missing_modes = [
        mode
        for mode in contract.forbidden_ending_modes
        if mode not in plan.ending_modes_to_avoid
    ]
    if missing_modes:
        violations.append(
            "ending_modes_to_avoid omitted forbidden ending modes: "
            + ", ".join(missing_modes)
        )


def _safe_time_budget_plan(contract: TimeBudgetContract | None) -> dict[str, Any]:
    if contract is None:
        return {}
    return {
        "start": contract.start,
        "deadline": contract.deadline,
        "current_remaining": contract.current_remaining,
        "elapsed_events": list(contract.allowed_elapsed_events),
        "forbid_untracked_travel": contract.forbid_untracked_travel,
    }


def _safe_body_object_state_plan(
    contract: BodyObjectStateContract | None,
) -> dict[str, Any]:
    if contract is None:
        return {}
    result: dict[str, Any] = {
        "tracked_objects": dict(contract.tracked_objects),
        "tracked_body_states": dict(contract.tracked_body_states),
    }
    if contract.require_visible_cause_for_bleeding:
        result["blood_or_injury_requires_visible_cause"] = True
    return result


def _safe_ending_hook_type(contract: EndingHookContract | None) -> str:
    if contract is None or not contract.allowed_hook_types:
        return ""
    return contract.allowed_hook_types[0]


def _dedupe_strings(items: Any) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items or []:
        text = str(item or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _string_list(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        return [raw] if raw.strip() else []
    if isinstance(raw, (list, tuple, set)):
        return [str(item) for item in raw if str(item).strip()]
    return []


def _nested_get(raw: Any, *keys: str) -> Any:
    current = raw
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _first_mapping(*values: Any) -> dict[str, Any] | None:
    for value in values:
        if isinstance(value, dict):
            return value
    return None


def _first_scoped_mapping(chapter_number: int, *values: Any) -> dict[str, Any] | None:
    for value in values:
        scoped = _scoped_mapping(value, chapter_number)
        if scoped is not None:
            return scoped
    return None


def _scoped_mapping(raw: Any, chapter_number: int) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    chapters = raw.get("chapters")
    chapter_payload = None
    if isinstance(chapters, dict):
        chapter_payload = chapters.get(str(chapter_number)) or chapters.get(chapter_number)
    applies = _string_list(raw.get("applies_to_chapters"))
    if applies and str(chapter_number) not in applies and chapter_payload is None:
        return None
    base = {
        key: value
        for key, value in raw.items()
        if key not in {"applies_to_chapters", "chapters"}
    }
    if isinstance(chapter_payload, dict):
        return _merge_dicts(base, chapter_payload)
    return base


def _merge_dicts(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge_dicts(result[key], value)
        else:
            result[key] = value
    return result


def _first_text(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _string_map(raw: Any) -> dict[str, str]:
    if not isinstance(raw, dict):
        return {}
    result: dict[str, str] = {}
    for key, value in raw.items():
        key_text = str(key or "").strip()
        value_text = str(value or "").strip()
        if key_text and value_text:
            result[key_text] = value_text
    return result


def _opening_quality_chapter_task(
    opening_quality: dict[str, Any] | None,
    chapter_number: int,
) -> str:
    if not isinstance(opening_quality, dict):
        return ""
    key = {
        1: "chapter_1_small_turn",
        2: "chapter_2_reveal",
        3: "chapter_3_payoff",
    }.get(chapter_number)
    return _first_text(opening_quality.get(key)) if key else ""


def _contract_value_matches(actual: Any, expected: str) -> bool:
    actual_text = str(actual or "").strip()
    expected_text = str(expected or "").strip()
    if not expected_text:
        return True
    if not actual_text:
        return False
    alternatives = [
        part.strip()
        for part in re.split(r"[、,，/]| or ", expected_text)
        if part.strip()
    ]
    if len(alternatives) > 1:
        return any(_contract_value_matches(actual_text, part) for part in alternatives)
    return (
        actual_text == expected_text
        or expected_text in actual_text
        or actual_text in expected_text
    )


def _validate_prose_time_budget(
    text: str,
    contract: TimeBudgetContract | None,
    violations: list[str],
) -> None:
    if contract is None:
        return
    if contract.forbid_untracked_travel and _mentions_untracked_travel_text(text):
        allowed_raw = " ".join(contract.allowed_elapsed_events)
        if not any(token in allowed_raw for token in ("骑", "车", "路", "赶")):
            violations.append(
                "chapter prose contains travel/time movement not registered in time budget"
            )
    ambiguous_clock_tokens = re.findall(r"\b\d{1,2}:\d{2}:\d{2}\b", text)
    for token in ambiguous_clock_tokens:
        index = text.find(token)
        window = text[max(0, index - 24) : index + len(token) + 24]
        if not any(marker in window for marker in ("倒计时", "距", "剩余", "还剩")):
            violations.append(f"time token '{token}' is not marked as clock or countdown")
    if (
        contract.deadline
        and contract.deadline in ("子时", "午夜", "零点")
        and "子时" in text
        and "距子时" not in text
        and "子时前" not in text
        and "子时后" not in text
    ):
        violations.append(
            "chapter prose mentions 子时 without a clear before/after/countdown relation"
        )


def _validate_prose_body_object_state(
    text: str,
    contract: BodyObjectStateContract | None,
    violations: list[str],
) -> None:
    if contract is None:
        return
    if contract.require_visible_cause_for_bleeding:
        for marker in ("鼻血", "流血", "渗血", "出血"):
            start = text.find(marker)
            if start < 0:
                continue
            window = text[max(0, start - 80) : start + 80]
            if not any(cause in window for cause in ("割", "咬", "硌", "划", "裂", "伤")):
                violations.append(f"body state '{marker}' lacks a visible local cause")
    # Flag ANY contract-tracked object missing from the prose — using THIS book's own
    # tracked_objects, not a detective whitelist (康熙铜钱/青囊秘卷/罗盘). The old whitelist
    # made this check fire only for the one detective book; every other book's missing
    # key object went unchecked.
    for object_name in contract.tracked_objects:
        if object_name and object_name not in text:
            violations.append(f"tracked object '{object_name}' is not visible in chapter prose")


def _validate_prose_ending_hook(
    text: str,
    contract: EndingHookContract | None,
    violations: list[str],
) -> None:
    if contract is None:
        return
    tail = text[-240:]
    for mode in contract.forbidden_ending_modes:
        if mode and mode in tail:
            violations.append(f"ending hook uses forbidden mode '{mode}'")
    if contract.required_hook_target:
        targets = [
            item.strip()
            for item in re.split(r"[、,，/]|或| or ", contract.required_hook_target)
            if item.strip()
        ]
        if targets and not any(target in tail for target in targets):
            violations.append(
                "ending hook does not visibly target required hook: "
                + contract.required_hook_target
            )


def _mentions_untracked_travel_text(text: str) -> bool:
    return any(
        token in text
        for token in (
            "骑电动车",
            "骑车",
            "开车",
            "车程",
            "冲下楼",
            "小巷",
            "半小时",
            "二十分钟",
            "二十分钟后",
        )
    )


def _mentions_travel(text: str) -> bool:
    return any(
        token in text
        for token in ("骑车", "开车", "车程", "路上", "赶路", "travel", "commute")
    )


def _budget_mentions_allowed_event(
    budget: dict[str, Any],
    allowed_elapsed_events: list[str],
) -> bool:
    if not allowed_elapsed_events:
        return False
    raw = json.dumps(budget, ensure_ascii=False)
    return any(event and event in raw for event in allowed_elapsed_events)


def _is_allowed_relative_time(anchor: str, allowed_patterns: list[str]) -> bool:
    text = str(anchor or "").strip()
    if not text:
        return False
    if any(pattern and pattern in text for pattern in allowed_patterns):
        return True
    return bool(
        re.search(
            r"(?:几|十几|半|\d+|一|二|三|四|五|六|七|八|九|十|二十|三十)"
            r"(?:秒|分钟|刻钟|小时)(?:后|前)?",
            text,
        )
    )


__all__ = [
    "BodyObjectStateContract",
    "ChapterConstraintManifest",
    "EndingHookContract",
    "OpeningCausalityContract",
    "PlanValidationResult",
    "PrewritePlan",
    "ProsePromotionValidationResult",
    "TimeBudgetContract",
    "build_safe_prewrite_plan",
    "compile_chapter_constraint_manifest",
    "normalize_prewrite_plan_for_manifest",
    "parse_prewrite_plan",
    "render_constraint_manifest_block",
    "render_prewrite_plan_block",
    "render_prewrite_plan_prompt",
    "validate_chapter_prose_for_promotion",
    "validate_prewrite_plan",
]
