from __future__ import annotations

# ruff: noqa: ANN401
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import re
from typing import Any

STRICT_PREWRITE_PROFILE = "commercial_strict_prewrite"
STRICT_PREWRITE_REPAIR_ATTEMPT_LIMIT = 2


@dataclass(frozen=True)
class PrewriteQualityFinding:
    code: str
    severity: str
    message: str
    path: str
    repair_target_stage: str
    repair_instruction: str
    evidence: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
            "path": self.path,
            "repair_target_stage": self.repair_target_stage,
            "repair_instruction": self.repair_instruction,
            "evidence": self.evidence or {},
        }


@dataclass(frozen=True)
class PrewriteQualityReport:
    stage: str
    passed: bool
    score: float
    blocking_findings: tuple[PrewriteQualityFinding, ...]
    audit_findings: tuple[PrewriteQualityFinding, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "passed": self.passed,
            "score": self.score,
            "blocking_findings": [finding.to_dict() for finding in self.blocking_findings],
            "audit_findings": [finding.to_dict() for finding in self.audit_findings],
        }


_ENGLISH_MECHANISM_RE = re.compile(r"\b[a-zA-Z]{4,}(?:_[a-zA-Z0-9]+)+\b|\b[a-zA-Z]{8,}\b")
# A value that is itself a lowercase hyphenated slug (e.g. "suspense-mystery",
# "rule-mystery-complete") is an internal genre/taxonomy identifier, NOT English
# prose leaking into Chinese text. Mechanism leaks use underscores
# (information_asymmetry, state_loop_engine), so the hyphen-only shape keeps real
# leaks detectable. (2026-06-03 book_spec gate regression: English genre slugs in
# genre/tone/etc. were hard-blocking every autowrite run.)
_BARE_TAXONOMY_SLUG_RE = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)+$")
# Removes leaked English taxonomy/mechanism tokens (hyphen slugs, underscored
# mechanism labels, long bare English words) from Chinese prose during repair.
_EN_TAXONOMY_STRIP_RE = re.compile(
    r"\b[a-z][a-z0-9]*(?:-[a-z0-9]+)+\b"
    r"|\b[a-zA-Z]{4,}(?:_[a-zA-Z0-9]+)+\b"
    r"|\b[a-zA-Z]{8,}\b"
)


def _is_bare_taxonomy_slug(value: str) -> bool:
    return bool(_BARE_TAXONOMY_SLUG_RE.fullmatch(str(value or "").strip()))


def _english_mechanism_leak(value: str) -> bool:
    """True when a string leaks an English mechanism label into Chinese content,
    excluding bare hyphenated taxonomy slugs."""

    text = str(value or "")
    if not _ENGLISH_MECHANISM_RE.search(text):
        return False
    return not _is_bare_taxonomy_slug(text)
_TRUNCATED_FRAGMENT_RE = re.compile(r"^[\u4e00-\u9fffA-Za-z0-9？?！!。.,，、：:；;]{1,2}$")
_FALLBACK_SOURCE_RE = re.compile(
    r"complete-extraction-failure|zero[-_ ]?confidence|fallback(?:[_ -]?progress|[_ -]?source|[_ -]?distillation)?",
    re.IGNORECASE,
)
_OFF_GENRE_STATE_RE = re.compile(
    r"siege_under_pressure|alliance depleted|surrender pathway|advisor council|"
    r"double_reversal_revelation|fallback_progress",
    re.IGNORECASE,
)


def has_kernel_leak(text: str) -> bool:
    """True when ``text`` carries off-genre or fallback-distillation markers
    that the story_design_kernel_gate flags as ``fallback_source_leak``."""
    if not text:
        return False
    return bool(_FALLBACK_SOURCE_RE.search(text) or _OFF_GENRE_STATE_RE.search(text))


def sanitize_distilled_leak(value: Any) -> Any:
    """Recursively strip off-genre / fallback-distillation markers from distilled
    material before it is injected into the StoryDesignKernel (prompt or payload).

    Polluted distilled cards (e.g. ``fallback_progress`` from a low-confidence
    aggregate, or war-strategy tokens leaked from another genre's sources) would
    otherwise trip :func:`evaluate_story_design_kernel_quality`. This keeps the
    structure intact while dropping only the leaves/entries that carry the
    blocked tokens. Single-sourced with the gate regexes so the two never drift.
    """
    if isinstance(value, str):
        if not has_kernel_leak(value):
            return value
        kept = [line for line in value.splitlines() if not has_kernel_leak(line)]
        return "\n".join(kept)
    if isinstance(value, Mapping):
        cleaned: dict[Any, Any] = {}
        for key, item in value.items():
            if isinstance(key, str) and has_kernel_leak(key):
                continue
            sanitized = sanitize_distilled_leak(item)
            if isinstance(item, str) and item.strip() and (
                not isinstance(sanitized, str) or not sanitized.strip()
            ):
                continue  # entry fully emptied by sanitization → drop it
            cleaned[key] = sanitized
        return cleaned
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        cleaned_list: list[Any] = []
        for item in value:
            if isinstance(item, str):
                if has_kernel_leak(item):
                    continue
                cleaned_list.append(item)
                continue
            sanitized = sanitize_distilled_leak(item)
            if sanitized in ({}, []) and item not in ({}, []):
                continue  # nested container emptied by sanitization → drop it
            cleaned_list.append(sanitized)
        return cleaned_list
    return value
_AWKWARD_TITLE_PATTERNS = (
    "构思中",
    "/",
    "\\",
    "不开整改单",
    "会决定两案卷",
)

_REPAIR_TARGETS = {
    "title_uncommercial": "concept_title_repair",
    "english_mechanism_leak": "concept_or_book_spec_language_repair",
    "field_truncated": "artifact_field_regeneration",
    "fallback_source_leak": "world_spec_source_reselection",
    "zero_confidence_source": "world_spec_source_reselection",
    "cast_function_missing": "cast_spec_function_repair",
    "benchmark_alignment_missing": "benchmark_profile_repair",
    "unique_hook_missing": "creative_positioning_repair",
    "series_engine_missing": "series_engine_repair",
    "long_arc_capacity_missing": "volume_strategy_long_arc_repair",
    "volume_differentiation_missing": "volume_strategy_differentiation_repair",
    "volume_primary_force_repeats": "volume_strategy_differentiation_repair",
    "progression_engine_missing": "foundation_progression_engine_repair",
    "rule_engine_missing": "foundation_rule_engine_repair",
    "relationship_engine_missing": "foundation_relationship_engine_repair",
    "story_design_kernel_missing": "story_design_kernel_rebuild",
    "story_design_contract_invalid": "story_design_kernel_contract_repair",
    "story_design_contract_thin": "story_design_kernel_rebuild",
    "distilled_strategy_copy_risk": "story_design_kernel_distilled_binding_rebuild",
    "distilled_strategy_missing": "distilled_strategy_card_compile",
    "distilled_strategy_low_maturity": "distilled_strategy_maturity_repair",
    "distilled_strategy_not_consumed": "previous_artifact_methodology_contract_repair",
    "distilled_strategy_state_variables_missing": "distilled_state_binding_rebuild",
    "distilled_worldview_not_bound": "story_design_kernel_worldview_binding_rebuild",
    "emotion_driven_kernel_missing": "emotion_driven_kernel_rebuild",
    "public_emotion_kernel_missing": "public_emotion_kernel_rebuild",
    "public_emotion_bridge_missing": "public_emotion_bridge_repair",
    "compliance_boundary_kernel_missing": "compliance_boundary_kernel_rebuild",
    "compliance_boundary_high_risk": "compliance_boundary_risk_repair",
    "empathy_contract_missing": "emotion_driven_empathy_contract_repair",
    "bomb_contract_not_consumed": "emotion_driven_bomb_contract_repair",
    "antagonist_moral_contract_thin": "emotion_driven_antagonist_contract_repair",
    "ending_texture_missing": "emotion_driven_ending_texture_repair",
    "state_variable_stalls": "world_spec_or_volume_plan_state_progression_repair",
    "beat_schedule_incomplete": "story_design_kernel_rebuild",
    "methodology_not_consumed": "previous_artifact_methodology_contract_repair",
    "outline_batch_truncated": "chapter_outline_batch_shrink_and_retry",
    "outline_llm_commercial_judge_failed": "chapter_outline_commercial_repair",
    "volume_plan_thin": "volume_plan_repair",
    # Decision policy lives in the cast; regenerate cast rather than aborting.
    "decision_policy_missing": "cast_spec_function_repair",
    # Long-form seriality mapping is repaired by regenerating the volume plan.
    "seriality_volume_mapping_invalid": "volume_plan_repair",
}


def repair_target_for_block_code(code: str) -> str:
    return _REPAIR_TARGETS.get(str(code or "").strip(), "machine_blocked")


def apply_default_prewrite_quality_profile(metadata: Mapping[str, Any] | None) -> dict[str, Any]:
    enriched = dict(metadata or {})
    profile = str(enriched.get("quality_profile") or "").strip()
    if not profile:
        enriched["quality_profile"] = STRICT_PREWRITE_PROFILE
    if enriched.get("quality_profile") == STRICT_PREWRITE_PROFILE:
        enriched.setdefault("methodology_contract_mode", "strict")
        enriched.setdefault("commercial_strict_prewrite", True)
    return enriched


def is_strict_prewrite_project(project: Any) -> bool:
    metadata = getattr(project, "metadata_json", None)
    if not isinstance(metadata, Mapping):
        return False
    return (
        metadata.get("quality_profile") == STRICT_PREWRITE_PROFILE
        or metadata.get("commercial_strict_prewrite") is True
        or metadata.get("prewrite_quality_profile") == STRICT_PREWRITE_PROFILE
    )


def strict_blocks(project: Any, settings: Any, key: str) -> bool:
    # 2026-06 self-harm fix: strict-prewrite mode still RUNS every gate
    # thoroughly and drives auto-repair (see ``strict_methodology_mode`` /
    # ``strict_outline_batch_size`` which remain strict-aware), but whether a
    # gate HARD-ABORTS the whole book is now governed solely by its own
    # ``*_block_on_failure`` config flag — strict mode no longer forces every
    # gate to fail-closed. This removes the documented cascade where a single
    # high-severity planning finding (e.g. ``benchmark_alignment_missing``)
    # aborted generation entirely instead of being reported + repaired.
    # Flip the relevant flag to ``true`` to keep a gate hard.
    return bool(getattr(getattr(settings, "pipeline", settings), key, False))


def strict_methodology_mode(project: Any, settings: Any) -> str:
    if is_strict_prewrite_project(project):
        return "strict"
    return str(getattr(getattr(settings, "pipeline", settings), "methodology_contract_mode", "warn") or "warn")


def strict_outline_batch_size(project: Any, settings: Any) -> int | None:
    if not is_strict_prewrite_project(project):
        return None
    pipeline = getattr(settings, "pipeline", settings)
    # Default 3 (matches settings.py): 5-chapter strict batches exceeded the
    # planner token limit and churned on truncation. Clamp keeps overrides in
    # a safe [3, 5] band.
    return max(
        3,
        min(
            5,
            int(getattr(pipeline, "commercial_strict_prewrite_chapter_outline_batch_size", 3) or 3),
        ),
    )


def strict_outline_shrink_size(project: Any, settings: Any) -> int:
    pipeline = getattr(settings, "pipeline", settings)
    return max(
        2,
        min(
            3,
            int(getattr(pipeline, "commercial_strict_prewrite_outline_batch_shrink_size", 3) or 3),
        ),
    )


def planning_artifact_meta(project: Any, *, gate_reports: Mapping[str, Any] | None = None) -> dict[str, Any]:
    from bestseller.services.genre_skill_profiles import genre_skill_profile_from_metadata
    from bestseller.services.book_design import planning_snapshot_lineage

    metadata = getattr(project, "metadata_json", None)
    metadata = metadata if isinstance(metadata, Mapping) else {}
    quality_profile = metadata.get("quality_profile") or (
        STRICT_PREWRITE_PROFILE if is_strict_prewrite_project(project) else "legacy"
    )
    lineage = {
        "quality_profile": quality_profile,
        "methodology_contract_mode": strict_methodology_mode(project, settings_like(metadata)),
        "prompt_pack_key": metadata.get("prompt_pack_key"),
        "distilled_strategy_card": bool(metadata.get("distilled_strategy_card")),
        "material_reference_block": bool(metadata.get("material_reference_block")),
    }
    genre_skill_profile = genre_skill_profile_from_metadata(metadata)
    if genre_skill_profile is not None:
        lineage["genre_skill_profile_key"] = genre_skill_profile.profile_key
        lineage["genre_skill_profile_version"] = genre_skill_profile.version
    meta: dict[str, Any] = {
        "quality_profile": quality_profile,
        "methodology_lineage": lineage,
        "repair_attempts": [],
        **planning_snapshot_lineage(project),
    }
    if gate_reports:
        meta["gate_reports"] = dict(gate_reports)
    return meta


class settings_like:
    def __init__(self, metadata: Mapping[str, Any]) -> None:
        self.pipeline = self
        self.methodology_contract_mode = metadata.get("methodology_contract_mode") or "warn"


def _is_english_language(language: str | None) -> bool:
    value = str(language or "").strip().lower()
    return value.startswith("en") or value in {"english", "en-us", "en-gb"}


def evaluate_concept_quality(
    *,
    title: str,
    premise: str,
    synopsis: str = "",
    writing_profile: Mapping[str, Any] | None = None,
    language: str | None = None,
) -> PrewriteQualityReport:
    findings: list[PrewriteQualityFinding] = []
    is_en = _is_english_language(language)
    title_text = str(title or "").strip()
    if not title_text:
        findings.append(_finding("title_uncommercial", "critical", "title", "Concept title is empty."))
    if not is_en and any(pattern in title_text for pattern in _AWKWARD_TITLE_PATTERNS):
        findings.append(
            _finding(
                "title_uncommercial",
                "critical",
                "title",
                "Chinese commercial title is placeholder-like or awkward.",
                evidence={"title": title_text},
            )
        )
    combined = "\n".join([title_text, str(premise or ""), str(synopsis or "")])
    if not is_en and _english_mechanism_leak(combined):
        findings.append(
            _finding(
                "english_mechanism_leak",
                "critical",
                "concept",
                "Chinese concept leaks English mechanism labels.",
            )
        )
    market = _mapping(writing_profile).get("market") if isinstance(writing_profile, Mapping) else {}
    reader_promise = _text(_mapping(market).get("reader_promise"))
    selling_points = _string_list(_mapping(market).get("selling_points"))
    if not reader_promise and not selling_points and len(str(premise or "").strip()) < 20:
        findings.append(
            _finding(
                "reader_promise_missing",
                "high",
                "writing_profile.market",
                "Concept lacks a concrete reader promise or selling point.",
            )
        )
    return _report("concept_quality_gate", findings)


def evaluate_book_spec_quality(
    book_spec: Mapping[str, Any] | None,
    *,
    language: str | None = None,
) -> PrewriteQualityReport:
    findings: list[PrewriteQualityFinding] = []
    payload = _mapping(book_spec)
    if not payload:
        findings.append(_finding("book_spec_missing", "critical", "book_spec", "BookSpec is missing."))
        return _report("book_spec_quality_gate", findings)
    if not _is_english_language(language):
        for path, value in _walk_strings(payload):
            if not _is_reader_visible_book_spec_text_path(path):
                continue
            if _english_mechanism_leak(value):
                findings.append(
                    _finding(
                        "english_mechanism_leak",
                        "critical",
                        path,
                        "Chinese BookSpec leaks English mechanism labels.",
                        evidence={"value": value[:200]},
                    )
                )
                break
    for path, value in _walk_strings(payload):
        if path.endswith(("theme", "themes", "reader_promise", "dramatic_question")) and _TRUNCATED_FRAGMENT_RE.match(value):
            findings.append(
                _finding(
                    "field_truncated",
                    "critical",
                    path,
                    "BookSpec contains a truncated field.",
                    evidence={"value": value},
                )
            )
            break
    protagonist = _mapping(payload.get("protagonist"))
    if not any(_text(protagonist.get(key)) for key in ("external_goal", "goal", "desire")):
        findings.append(
            _finding(
                "protagonist_goal_missing",
                "high",
                "book_spec.protagonist",
                "BookSpec protagonist lacks a concrete external goal.",
            )
        )
    return _report("book_spec_quality_gate", findings)


def evaluate_world_spec_source_quality(world_spec: Mapping[str, Any] | None) -> PrewriteQualityReport:
    findings: list[PrewriteQualityFinding] = []
    payload = _mapping(world_spec)
    for path, value in _walk_strings(payload):
        if _FALLBACK_SOURCE_RE.search(value):
            findings.append(
                _finding(
                    "fallback_source_leak",
                    "critical",
                    path,
                    "Fallback or failed distillation source leaked into WorldSpec.",
                    evidence={"value": value[:200]},
                )
            )
            break
    if _contains_zero_confidence(payload):
        findings.append(
            _finding(
                "zero_confidence_source",
                "critical",
                "world_spec",
                "Zero-confidence distillation source is present.",
            )
        )
    rules = _mapping_list(payload.get("rules") or payload.get("world_rules"))
    for index, rule in enumerate(rules[:12]):
        if not any(_text(rule.get(key)) for key in ("story_consequence", "visible_consequence", "chapter_usage_examples")):
            findings.append(
                _finding(
                    "world_rule_consequence_missing",
                    "high",
                    f"world_spec.rules[{index}]",
                    "World rule lacks reader-visible consequence or chapter usage.",
                )
            )
            break
    return _report("world_spec_source_gate", findings)


def evaluate_cast_spec_function_quality(cast_spec: Mapping[str, Any] | None) -> PrewriteQualityReport:
    findings: list[PrewriteQualityFinding] = []
    payload = _mapping(cast_spec)
    protagonist = _mapping(payload.get("protagonist"))
    if not protagonist:
        findings.append(_finding("cast_function_missing", "critical", "cast_spec.protagonist", "Protagonist is missing."))
    elif not any(_text(protagonist.get(key)) for key in ("external_goal", "goal", "core_drive", "desire")):
        findings.append(
            _finding(
                "cast_function_missing",
                "high",
                "cast_spec.protagonist",
                "Protagonist lacks a concrete plot function or drive.",
            )
        )
    cast_text = _stringify(payload)
    if "过去失败并非表面原因" in cast_text or "并非表面原因" in cast_text:
        findings.append(
            _finding(
                "cast_function_missing",
                "high",
                "cast_spec",
                "CastSpec contains generic placeholder motivation.",
            )
        )
    return _report("cast_spec_function_gate", findings)


def evaluate_story_design_kernel_quality(
    kernel: Mapping[str, Any] | None,
    *,
    target_chapters: int,
) -> PrewriteQualityReport:
    findings: list[PrewriteQualityFinding] = []
    payload = _mapping(kernel)
    if not payload:
        findings.append(_finding("story_design_kernel_missing", "critical", "story_design_kernel", "StoryDesignKernel is missing."))
        return _report("story_design_kernel_gate", findings)
    quality_text = _quality_stringify(payload)
    if _OFF_GENRE_STATE_RE.search(quality_text) or _FALLBACK_SOURCE_RE.search(quality_text):
        findings.append(
            _finding(
                "fallback_source_leak",
                "critical",
                "story_design_kernel",
                "Off-genre or fallback distilled variables leaked into StoryDesignKernel.",
            )
        )
    max_beat_chapter = _max_covered_chapter(payload)
    if target_chapters >= 10 and 0 < max_beat_chapter <= 3:
        findings.append(
            _finding(
                "beat_schedule_incomplete",
                "critical",
                "story_design_kernel.beat_schedule",
                "Beat schedule only covers the opening and cannot support the target chapter count.",
                evidence={"target_chapters": target_chapters, "max_covered_chapter": max_beat_chapter},
            )
        )
    return _report("story_design_kernel_gate", findings)


def evaluate_volume_plan_quality(
    volume_plan: Any,
    *,
    target_chapters: int,
) -> PrewriteQualityReport:
    entries = _volume_entries(volume_plan)
    findings: list[PrewriteQualityFinding] = []
    phases = {_text(entry.get("conflict_phase")) for entry in entries if _text(entry.get("conflict_phase"))}
    forces = {_text(entry.get("primary_force_name") or entry.get("primary_force")) for entry in entries if _text(entry.get("primary_force_name") or entry.get("primary_force"))}
    primary_force_count = len(forces)
    phase_count = len(phases)
    # Pressure/phase variety is measured ACROSS volumes. A short book that maps
    # to a single volume (e.g. 20 chapters -> 1 volume via compute_linear_hierarchy)
    # structurally has exactly one volume-level conflict_phase / primary_force, so
    # demanding >=2 of each would be an unsatisfiable contradiction that hard-blocks
    # the framework's own single-volume plan. Only enforce variety for multi-volume
    # plans; single-volume thinness is still caught by the climax/turn/payoff check.
    multi_volume = len(entries) >= 2
    if (
        target_chapters >= 20
        and multi_volume
        and (primary_force_count <= 1 or phase_count <= 1)
    ):
        findings.append(
            _finding(
                "volume_plan_thin",
                "critical",
                "volume_plan",
                "Multi-volume 20+ chapter plan collapses to a single pressure owner or conflict phase.",
                evidence={"unique_primary_force_count": primary_force_count, "unique_conflict_phase_count": phase_count},
            )
        )
    text = _stringify(entries)
    if not re.search(r"mid[-_ ]?turn|中段|转折|climax|高潮|payoff|兑现", text, re.IGNORECASE):
        findings.append(
            _finding(
                "volume_plan_thin",
                "high",
                "volume_plan",
                "VolumePlan lacks an explicit middle turn, climax, or payoff marker.",
            )
        )
    return _report("volume_plan_gate", findings)


def _finding(
    code: str,
    severity: str,
    path: str,
    message: str,
    *,
    evidence: dict[str, Any] | None = None,
) -> PrewriteQualityFinding:
    return PrewriteQualityFinding(
        code=code,
        severity=severity,
        message=message,
        path=path,
        repair_target_stage=repair_target_for_block_code(code),
        repair_instruction=_repair_instruction(code),
        evidence=evidence,
    )


def _report(stage: str, findings: Sequence[PrewriteQualityFinding]) -> PrewriteQualityReport:
    blocking = tuple(f for f in findings if f.severity in {"critical", "high"})
    audit = tuple(f for f in findings if f.severity not in {"critical", "high"})
    penalty = sum(0.25 if f.severity == "critical" else 0.15 if f.severity == "high" else 0.05 for f in findings)
    return PrewriteQualityReport(
        stage=stage,
        passed=not blocking,
        score=max(0.0, round(1.0 - penalty, 3)),
        blocking_findings=blocking,
        audit_findings=audit,
    )


def _repair_instruction(code: str) -> str:
    return {
        "title_uncommercial": "重写为中文商业网文口播自然、主角动作明确、冲突/误解可见的标题。",
        "english_mechanism_leak": "删除英文机制标签，改为自然中文设定表达。",
        "field_truncated": "重生该字段，必须是完整中文短句。",
        "fallback_source_leak": "剔除 fallback/失败蒸馏来源，重新选择同品类高置信素材。",
        "zero_confidence_source": "剔除零置信来源，重新选择可追溯素材。",
        "cast_function_missing": "补齐角色剧情功能、欲望、冲突关系和前三章可见作用。",
        "beat_schedule_incomplete": "重建 StoryDesignKernel 节拍表，覆盖目标章数或完整分段。",
        "volume_plan_thin": "补齐多阶段压力源、中段转折、高潮和 payoff。",
    }.get(code, "停止下游生成，回到对应规划阶段定向修复。")


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _mapping_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    return [dict(item) for item in value if isinstance(item, Mapping)]


def _volume_entries(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, Mapping):
        entries = _mapping_list(value.get("volumes"))
        return entries or [dict(value)]
    return _mapping_list(value)


def _string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if not isinstance(value, Sequence) or isinstance(value, (bytes, bytearray)):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _text(value: Any) -> str:
    return str(value or "").strip() if value is not None else ""


def _stringify(value: Any) -> str:
    if isinstance(value, Mapping):
        return "\n".join(f"{key}: {_stringify(raw)}" for key, raw in value.items())
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return "\n".join(_stringify(item) for item in value)
    return _text(value)


def _quality_stringify(value: Any) -> str:
    if isinstance(value, Mapping):
        parts: list[str] = []
        for key, raw in value.items():
            if str(key) == "_meta":
                continue
            parts.append(str(key))
            parts.append(_quality_stringify(raw))
        return "\n".join(part for part in parts if part)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return "\n".join(_quality_stringify(item) for item in value)
    return _text(value)


def _walk_strings(value: Any, path: str = "") -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    if isinstance(value, Mapping):
        for key, raw in value.items():
            child_path = f"{path}.{key}" if path else str(key)
            rows.extend(_walk_strings(raw, child_path))
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for index, raw in enumerate(value):
            rows.extend(_walk_strings(raw, f"{path}[{index}]"))
    elif isinstance(value, str) and value.strip():
        rows.append((path, value.strip()))
    return rows


# Leaf keys that hold internal identifiers/slugs, NOT reader-visible prose. They
# are exempt from the Chinese-prose English-leak check and from language repair.
# Genre taxonomy slugs ("suspense-mystery", "rule-mystery-complete") live here.
_NON_PROSE_LEAF_KEYS: frozenset[str] = frozenset(
    {
        "id",
        "key",
        "type",
        "kind",
        "name_key",
        "category_key",
        "mechanism_key",
        "hook_type",
        "line_role",
        "opening_frame",
        "expression_style",
        "source_id",
        "source_key",
        "source_type",
        "schema_version",
        "version",
        "genre",
        "sub_genre",
        "subgenre",
        "audience",
        "genre_key",
        "sub_genre_key",
    }
)


def _is_reader_visible_book_spec_text_path(path: str) -> bool:
    normalized = str(path or "").strip()
    if not normalized:
        return False
    if normalized == "_meta" or normalized.startswith("_meta."):
        return False
    leaf = re.sub(r"\[\d+\]$", "", normalized).rsplit(".", 1)[-1].lower()
    return leaf not in _NON_PROSE_LEAF_KEYS


def repair_zh_book_spec_language(
    book_spec: Mapping[str, Any] | None,
    *,
    language: str | None,
) -> dict[str, Any]:
    """Strip leaked English taxonomy/mechanism tokens from a Chinese BookSpec.

    This is the ``concept_or_book_spec_language_repair`` the gate maps but never
    ran — it removes English genre slugs / mechanism labels the model echoed into
    reader-visible prose (tone/themes/dramatic_question/...), keeping the Chinese.
    Identifier fields (genre/sub_genre/keys) are left untouched. Returns a new
    dict; the original is not mutated. No-op for English books.
    """

    payload = _mapping(book_spec)
    if _is_english_language(language) or not payload:
        return dict(payload)
    return _repair_zh_node(payload, leaf="")


def _repair_zh_node(value: Any, *, leaf: str) -> Any:
    if isinstance(value, Mapping):
        return {key: _repair_zh_node(raw, leaf=str(key)) for key, raw in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_repair_zh_node(item, leaf=leaf) for item in value]
    if isinstance(value, str):
        if leaf.lower() in _NON_PROSE_LEAF_KEYS or leaf == "_meta":
            return value
        if not _EN_TAXONOMY_STRIP_RE.search(value):
            return value
        # Strip every gate-matching English token. The strip regex is a superset
        # of the detection regex, so the result is guaranteed leak-free — even for
        # wholly-English values like "Kindle Unlimited" (→ "Kindle"). This is what
        # makes the zh/en separation robust instead of whack-a-mole.
        stripped = _EN_TAXONOMY_STRIP_RE.sub("", value)
        stripped = re.sub(r"\s{2,}", " ", stripped).strip(" ，,、；;:：.。-—·／/|")
        return stripped
    return value


def _contains_zero_confidence(value: Any) -> bool:
    if isinstance(value, Mapping):
        for key, raw in value.items():
            key_text = str(key).lower()
            if key_text in {"confidence", "confidence_score", "source_confidence"} or key_text.endswith("_confidence"):
                try:
                    if float(raw) <= 0:
                        return True
                except (TypeError, ValueError):
                    pass
            if _contains_zero_confidence(raw):
                return True
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return any(_contains_zero_confidence(item) for item in value)
    return False


def _max_covered_chapter(payload: Mapping[str, Any]) -> int:
    max_chapter = 0
    for _path, text in _walk_strings(payload):
        for match in re.finditer(r"(?:chapter|ch|第)\s*(\d{1,4})(?:\s*[-至到]\s*(\d{1,4}))?", text, re.IGNORECASE):
            max_chapter = max(max_chapter, int(match.group(2) or match.group(1)))
    for key in ("beat_schedule", "beats", "beat_sheet"):
        for beat in _mapping_list(payload.get(key)):
            for field in ("chapter", "chapter_number", "end_chapter"):
                try:
                    max_chapter = max(max_chapter, int(beat.get(field) or 0))
                except (TypeError, ValueError):
                    pass
            raw_range = beat.get("chapter_range")
            if isinstance(raw_range, str):
                for match in re.finditer(r"(\d{1,4})(?:\s*[-至到]\s*(\d{1,4}))?", raw_range):
                    max_chapter = max(max_chapter, int(match.group(2) or match.group(1)))
            if isinstance(raw_range, Sequence) and not isinstance(raw_range, (str, bytes, bytearray)):
                for item in raw_range:
                    try:
                        max_chapter = max(max_chapter, int(item))
                    except (TypeError, ValueError):
                        pass
    return max_chapter
