"""Pre-write chapter constraints and plan validation.

This module is intentionally pre-generative: it turns scattered bible/canon/
scene context into a small executable contract, then validates the writer's
declared plan before prose generation starts.
"""

from __future__ import annotations

import json
import re
from typing import Any

from pydantic import BaseModel, Field

from bestseller.services.canon_guardrails import CanonGuardrails


class ChapterConstraintManifest(BaseModel):
    chapter_number: int = Field(ge=1)
    scene_number: int | None = Field(default=None, ge=1)
    allowed_characters: list[str] = Field(default_factory=list)
    characters_must_not_appear: list[str] = Field(default_factory=list)
    forbidden_terms: list[str] = Field(default_factory=list)
    allowed_time_anchors: list[str] = Field(default_factory=list)
    forbidden_time_anchors: list[str] = Field(default_factory=list)
    allowed_locations: list[str] = Field(default_factory=list)
    must_echo_hooks_from_prev: list[str] = Field(default_factory=list)
    must_use_protagonist_abilities: list[str] = Field(default_factory=list)
    must_avoid_protagonist_vocabulary: list[str] = Field(default_factory=list)
    chapter_target_state_end: dict[str, Any] = Field(default_factory=dict)


class PrewritePlan(BaseModel):
    characters_to_use: list[str] = Field(default_factory=list)
    time_anchors_to_use: list[str] = Field(default_factory=list)
    locations_to_use: list[str] = Field(default_factory=list)
    hooks_to_echo: list[str] = Field(default_factory=list)
    protagonist_abilities_to_use: list[str] = Field(default_factory=list)
    vocabulary_to_avoid: list[str] = Field(default_factory=list)
    target_state_end: dict[str, Any] = Field(default_factory=dict)


class PlanValidationResult(BaseModel):
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

    return ChapterConstraintManifest(
        chapter_number=chapter_number,
        scene_number=scene_number,
        allowed_characters=_dedupe_strings(participants or []),
        characters_must_not_appear=_dedupe_strings(forbidden_characters),
        forbidden_terms=_dedupe_strings(
            [*forbidden_terms, *_string_list(metadata.get("forbidden_terms"))]
        ),
        allowed_time_anchors=time_anchors,
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

    forbidden_times = set(manifest.forbidden_time_anchors)
    for anchor in plan.time_anchors_to_use:
        if anchor in forbidden_times:
            violations.append(f"time anchor '{anchor}' is forbidden")

    allowed_times = set(manifest.allowed_time_anchors)
    if allowed_times:
        for anchor in plan.time_anchors_to_use:
            if anchor not in allowed_times:
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

    return PlanValidationResult(passed=not violations, violations=violations)


def render_constraint_manifest_block(
    manifest: ChapterConstraintManifest,
    *,
    language: str = "zh-CN",
) -> str:
    payload = json.dumps(manifest.model_dump(mode="json"), ensure_ascii=False, indent=2)
    if language.lower().startswith("zh"):
        return (
            "【写前约束清单 — 优先级最高】\n"
            "以下 JSON 是本场景可执行合同。只能在白名单内选择人物/时间/地点；"
            "不得新增未列出的时间锚、真人出场或同功能设定。\n"
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
) -> str:
    payload = json.dumps(manifest.model_dump(mode="json"), ensure_ascii=False, indent=2)
    if language.lower().startswith("zh"):
        return (
            "根据以下写前约束清单，先声明本场景写作计划。只输出 JSON，不要输出正文。\n"
            "字段必须为：characters_to_use, time_anchors_to_use, locations_to_use, "
            "hooks_to_echo, protagonist_abilities_to_use, vocabulary_to_avoid, target_state_end。\n"
            "所有选择必须来自约束白名单；如某项无白名单，保持空数组。\n"
            f"约束清单：\n```json\n{payload}\n```"
        )
    return (
        "Declare the scene writing plan from this constraint manifest. Output "
        "JSON only, no prose. Required fields: characters_to_use, "
        "time_anchors_to_use, locations_to_use, hooks_to_echo, "
        "protagonist_abilities_to_use, vocabulary_to_avoid, target_state_end.\n"
        f"Manifest:\n```json\n{payload}\n```"
    )


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


__all__ = [
    "ChapterConstraintManifest",
    "PrewritePlan",
    "PlanValidationResult",
    "build_safe_prewrite_plan",
    "compile_chapter_constraint_manifest",
    "parse_prewrite_plan",
    "render_constraint_manifest_block",
    "render_prewrite_plan_block",
    "render_prewrite_plan_prompt",
    "validate_prewrite_plan",
]
