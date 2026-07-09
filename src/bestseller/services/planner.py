from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
import copy
from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import json
import logging
import math
from pathlib import Path
import re
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.domain.enums import ArtifactType, ChapterStatus, WorkflowStatus
from bestseller.domain.planning import (
    NovelPlanningResult,
    PlanningArtifactCreate,
    PlanningArtifactRecord,
    VolumePlanningResult,
)
from bestseller.domain.story_bible import (
    is_safe_character_role_label,
    normalize_character_age,
    normalize_character_role_label,
)
from bestseller.infra.db.models import (
    ChapterModel,
    PlanningArtifactVersionModel,
    ProjectModel,
    VolumeModel,
    WorkflowRunModel,
    WorkflowStepRunModel,
)
from bestseller.services.character_drama_engine import (
    build_character_drama_map,
    character_drama_map_to_dict,
    render_character_drama_prompt_block,
)
from bestseller.services.character_identity_resolver import (
    collect_entry_aliases,
    merge_character_with_aliases,
    resolve_character_match,
)
from bestseller.services.emotion_driven_kernel import (
    emotion_driven_kernel_from_dict,
    emotion_driven_kernel_to_dict,
    evaluate_emotion_contracts,
    render_emotion_driven_kernel_prompt_block,
)
from bestseller.services.compliance_boundary_kernel import (
    build_compliance_boundary_kernel_seed,
    compliance_boundary_kernel_from_dict,
    compliance_boundary_kernel_to_dict,
    render_compliance_boundary_prompt_block,
)
from bestseller.services.concept_lab import (
    concept_lab_from_source,
    render_concept_lab_prompt_block,
)
from bestseller.services.entry_registry import (
    build_entry_coverage_matrix,
    build_fallback_entry_registry,
    entry_registry_from_dict,
    entry_registry_to_dict,
    render_entry_registry_prompt_block,
)
from bestseller.services.entry_system_kernel import (
    build_fallback_entry_system_kernel,
    entry_system_kernel_from_dict,
    entry_system_kernel_to_dict,
    render_entry_system_kernel_prompt_block,
)
from bestseller.services.hook_ledger_runtime import render_hook_ledger_planner_contract
from bestseller.services.payoff_ledger_runtime import render_payoff_ledger_planner_contract
from bestseller.services.llm import LLMCompletionRequest, complete_text
from bestseller.services.methodology_compiler import MethodologyStage
from bestseller.services.planner_prompt_helpers import attach_planner_methodology
from bestseller.services.novel_categories import (
    NovelCategoryResearch,
    get_novel_category,
    resolve_novel_category,
)
from bestseller.services.planning_context import (
    summarize_book_spec,
    summarize_cast_spec,
    summarize_volume_plan_context,
    summarize_world_spec,
)
from bestseller.services.public_emotion_kernel import (
    build_public_emotion_kernel_seed,
    public_emotion_kernel_from_dict,
    public_emotion_kernel_to_dict,
    render_public_emotion_prompt_block,
)
from bestseller.services.projects import get_project_by_slug, import_planning_artifact
from bestseller.services.prewrite_quality_profile import (
    evaluate_book_spec_quality,
    evaluate_cast_spec_function_quality,
    evaluate_concept_quality,
    evaluate_story_design_kernel_quality,
    evaluate_volume_plan_quality,
    evaluate_world_spec_source_quality,
    is_strict_prewrite_project,
    planning_artifact_meta,
    repair_target_for_block_code,
    repair_zh_book_spec_language,
    sanitize_distilled_leak,
    strict_blocks,
    strict_outline_batch_size,
    strict_outline_shrink_size,
)
from bestseller.services.methodology_bridge import (
    render_phase_block as render_methodology_phase_block,
)
from bestseller.services.prompt_packs import (
    render_prompt_pack_fragment,
    render_prompt_pack_prompt_block,
    resolve_prompt_pack,
)
from bestseller.services.story_bible import (
    enforce_volume_plan_contract,
    parse_cast_spec_input,
    parse_volume_plan_input,
    parse_world_spec_input,
)
from bestseller.services.narrative_contracts import (
    IDENTITY_POLICY_VERSION,
    repair_commercial_zh_identity_policy,
    validate_commercial_zh_identity_policy,
)
from bestseller.services.story_design_grammars import (
    render_story_design_grammar_prompt_block,
    resolve_story_design_grammar,
)
from bestseller.services.simulation_oracle_planner import (
    augment_story_design_kernel_with_oracle,
)
from bestseller.services.story_design_kernel import (
    render_story_design_kernel_prompt_block,
    story_design_kernel_from_dict,
)
from bestseller.services.story_effect_skills import (
    STORY_EFFECT_SKILL_SELECTION_METADATA_KEY,
    render_selected_story_effect_skill_contracts,
    render_story_effect_skill_catalog_prompt_block,
)
from bestseller.services.story_enhancers import (
    audit_story_enhancer_coverage,
    render_story_enhancer_contract_block,
    resolve_story_enhancers,
    story_enhancer_repair_directives,
)
from bestseller.services.brainhole_engine import BRAINHOLE_PROFILE_METADATA_KEY
from bestseller.domain.ideology import (
    ideology_kernel_to_dict,
    render_ideology_kernel_prompt_block,
)
from bestseller.services.ideology_coherence_gate import (
    evaluate_ideology_kernel_coherence,
)
from bestseller.services.ideology_kernel import derive_ideology_kernel
from bestseller.domain.world_model import (
    render_world_model_prompt_block,
    world_model_from_dict,
    world_model_to_dict,
)
from bestseller.services.world_model_deriver import derive_world_model
from bestseller.services.story_shape_router import derive_story_shape
from bestseller.services.title_dedup import (
    DEFAULT_NEAR_DUP_THRESHOLD,
    TitleCollisionError,
    derive_title_from_content,
    find_title_collisions,
    jaccard_similarity,
)
from bestseller.services.platform_title_workflow import (
    build_platform_title_workflow,
    build_story_grounded_title_revision_messages,
    finalize_revised_title,
    is_bare_taxonomy_title,
    title_readability_issue,
)
from bestseller.services.workflows import create_workflow_run, create_workflow_step_run
from bestseller.services.writing_profile import (
    is_english_language,
    render_serial_fiction_guardrails,
    render_writing_profile_prompt_block,
    resolve_writing_profile,
)
from bestseller.settings import AppSettings, get_settings

from bestseller.services.progress_context import emit_activity, emit_milestone

logger = logging.getLogger(__name__)


def _methodology_stage_for_planner_artifact(logical_name: str) -> MethodologyStage | None:
    name = logical_name.lower()
    if "chapter_outline" in name or "prewrite" in name:
        return MethodologyStage.OUTLINE_CHAPTER
    if any(token in name for token in ("volume", "act", "world_disclosure")):
        return MethodologyStage.OUTLINE_VOLUME
    if any(token in name for token in ("book", "world", "cast", "character")):
        return MethodologyStage.OUTLINE_BOOK
    return None

WORKFLOW_TYPE_GENERATE_NOVEL_PLAN = "generate_novel_plan"
PlanningProgressCallback = Callable[[str, dict[str, Any] | None], None]


class PlannerFallbackError(RuntimeError):
    """Raised when a planner artifact degrades to fallback content and the
    caller opted in to fail-fast instead of silently using fallback.

    Prevents downstream corruption such as partial chapter outlines
    producing gaps like "missing chapters [151..350]".
    """


class OutlineBatchShrinkRequired(PlannerFallbackError):
    """Raised when a strict outline batch must be split before retrying."""


class OutlineEventDuplicationError(PlannerFallbackError):
    """Raised when a freshly generated outline batch re-narrates events that
    earlier batches already consumed (R5: cross-batch narrative amnesia).

    ``findings`` carries one entry per offending chapter:
    ``{"chapter_number", "goal", "ledger_chapter_number", "ledger_goal",
    "similarity"}`` so the repair loop can issue targeted per-chapter
    directives instead of regenerating the whole batch blindly.
    """

    def __init__(self, message: str, *, findings: list[dict[str, Any]]) -> None:
        super().__init__(message)
        self.findings = findings


class StoryEnhancerCoverageError(PlannerFallbackError):
    """Raised when a book-level selected story-enhancer effect (脑洞/喜剧/反转/…)
    is systemically uncashed across an outline batch — the zhaoshen-hr-v13 failure
    (logically rigorous but bland). ``directives`` carries ready repair lines so
    the loop can demand the missing effects without regenerating blindly. Treated
    as soft on the final attempt: a quality gap must never abort the book.
    """

    def __init__(self, message: str, *, directives: list[str]) -> None:
        super().__init__(message)
        self.directives = directives


class OutlineFieldDegeneracyError(PlannerFallbackError):
    """Raised when a chapter's goal/opening_situation/main_conflict have
    collapsed into near-duplicates of each other (真机 tracked-rulehorror-v1:
    ch1 的三字段字面相同，existence-only 闸门照样全部通过——闸门查"非空"，
    模型把三个不同语义的字段填成同一句话就骗过了检查). ``findings`` carries
    one entry per degenerate field pair so the repair loop can issue directives
    that explain each field's distinct semantics instead of asking for a blind
    full rewrite. Treated as soft on the final attempt: a quality gap must
    never abort the book.
    """

    def __init__(self, message: str, *, findings: list[dict[str, Any]]) -> None:
        super().__init__(message)
        self.findings = findings


# Default acceptance threshold for the R5 consumed-event dedup check
# (character 2-gram Jaccard between a new chapter goal and ledger goals).
# Defined here because it is a default argument of the outline repair loop.
OUTLINE_EVENT_DEDUP_THRESHOLD_DEFAULT = 0.55


def _emit_planner_progress(
    progress: PlanningProgressCallback | None,
    stage: str,
    *,
    project: ProjectModel,
    workflow_run_id: UUID,
    current_step: str,
    **payload: Any,
) -> None:
    if progress is None:
        return
    progress(
        stage,
        {
            "project_slug": project.slug,
            "workflow_run_id": str(workflow_run_id),
            "current_step": current_step,
            **payload,
        },
    )


@dataclass(frozen=True)
class PlannerContext:
    """Centralized category-aware context built once per pipeline run.

    Carries resolved category data so downstream functions do not need to
    re-resolve.  All fields are optional for backward compatibility — when
    ``category_key`` is None, every downstream function falls back to the
    legacy behavior.
    """

    category_key: str | None = None
    category_research: NovelCategoryResearch | None = None
    challenge_phases: tuple[str, ...] = ()
    anti_pattern_block_zh: str = ""
    anti_pattern_block_en: str = ""
    reader_promise_zh: str = ""
    reader_promise_en: str = ""
    challenge_evolution_summary_zh: str = ""
    challenge_evolution_summary_en: str = ""
    category_context_summary: str = ""


def _build_planner_context(
    project: ProjectModel,
    volume_count: int,
) -> PlannerContext:
    """Build a PlannerContext once at the entry of a planning pipeline."""
    from bestseller.services.novel_categories import (
        render_category_anti_patterns,
        render_category_challenge_evolution_summary,
        render_category_reader_promise,
    )
    from bestseller.services.planning_context import summarize_category_context

    cat = resolve_novel_category(project.genre, project.sub_genre)
    key = cat.key if cat else None
    phases = tuple(_assign_conflict_phases(volume_count, category_key=key))

    return PlannerContext(
        category_key=key,
        category_research=cat,
        challenge_phases=phases,
        anti_pattern_block_zh=render_category_anti_patterns(cat, is_en=False) if cat else "",
        anti_pattern_block_en=render_category_anti_patterns(cat, is_en=True) if cat else "",
        reader_promise_zh=render_category_reader_promise(cat, is_en=False) if cat else "",
        reader_promise_en=render_category_reader_promise(cat, is_en=True) if cat else "",
        challenge_evolution_summary_zh=render_category_challenge_evolution_summary(cat, is_en=False)
        if cat
        else "",
        challenge_evolution_summary_en=render_category_challenge_evolution_summary(cat, is_en=True)
        if cat
        else "",
        category_context_summary=summarize_category_context(
            key, language=str(project.language or "zh-CN")
        ),
    )


def _json_dumps(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _story_package_candidate_paths(project: ProjectModel) -> list[Path]:
    metadata = project.metadata_json if isinstance(project.metadata_json, dict) else {}
    candidates: list[Path] = []

    explicit = metadata.get("story_package_path")
    if isinstance(explicit, str) and explicit:
        candidates.append(Path(explicit))

    slug_underscored = project.slug.replace("-", "_")
    cwd = Path.cwd()
    for anchor in (cwd, *cwd.parents):
        candidates.append(
            anchor / "story-factory" / "projects" / slug_underscored / "story_package.json"
        )

    unique_candidates: list[Path] = []
    seen: set[Path] = set()
    for path in candidates:
        try:
            resolved = path.resolve()
        except OSError:
            continue
        if resolved in seen:
            continue
        seen.add(resolved)
        unique_candidates.append(resolved)
    return unique_candidates


def _load_story_package_seed(project: ProjectModel) -> dict[str, Any]:
    for path in _story_package_candidate_paths(project):
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        return {
            "path": str(path),
            "book": data.get("book") if isinstance(data.get("book"), dict) else {},
            "reader_desire_map": data.get("reader_desire_map")
            if isinstance(data.get("reader_desire_map"), dict)
            else {},
            "story_bible": data.get("story_bible")
            if isinstance(data.get("story_bible"), dict)
            else {},
            "route_graph": data.get("route_graph")
            if isinstance(data.get("route_graph"), dict)
            else {},
        }
    return {}


def _story_package_prompt_block(project: ProjectModel, *, language: str | None = None) -> str:
    seed = _load_story_package_seed(project)
    if not seed:
        return ""

    is_en = is_english_language(language)
    book = _mapping(seed.get("book"))
    reader_desire = _mapping(seed.get("reader_desire_map"))
    story_bible = _mapping(seed.get("story_bible"))
    route_graph = _mapping(seed.get("route_graph"))
    characters = [
        item.get("name") for item in _mapping_list(book.get("characters")) if item.get("name")
    ]
    milestones = [
        item.get("title")
        for item in _mapping_list(route_graph.get("milestones"))
        if item.get("title")
    ]
    summary = {
        "canonical_characters": characters[:8],
        "reader_promise": reader_desire.get("core_fantasy"),
        "reward_promises": _string_list(reader_desire.get("reward_promises"))[:3],
        "story_bible_mainline": story_bible.get("mainline_goal"),
        "milestones": milestones[:6],
        "source_path": seed.get("path"),
    }
    label = "[Story package seed]" if is_en else "【story_package 既有商业设定】"
    return (
        f"\n\n{label}\n"
        "Treat this as pre-existing commercial canon. Reuse its characters, reader promise, and milestones whenever compatible.\n"
        if is_en
        else f"\n\n{label}\n请把它视为已有商业化 canon，在不冲突时优先复用其中的人物、读者承诺与里程碑。\n"
    ) + _json_dumps(summary)


_DISTILLED_REFERENCE_PHASE_CHAR_BUDGETS: dict[str, int] = {
    "architecture": 4200,
    "world": 3200,
    "cast": 2800,
    "story_design": 3200,
    "volume_plan": 2800,
    "chapter_outline": 2400,
    "craft": 1800,
}


def _trim_distilled_reference_part(text: str, *, max_chars: int) -> str:
    stripped = str(text or "").strip()
    if max_chars <= 0 or len(stripped) <= max_chars:
        return stripped
    marker = "\n...[distilled reference trimmed for prompt budget]"
    keep = max(max_chars - len(marker), 0)
    return stripped[:keep].rstrip() + marker


def _distilled_design_reference_block(project: ProjectModel, phase: str) -> str:
    """Return a pre-rendered distilled mature-fiction design block for a phase."""

    metadata = project.metadata_json if isinstance(project.metadata_json, dict) else {}
    parts: list[str] = []
    max_chars = _DISTILLED_REFERENCE_PHASE_CHAR_BUDGETS.get(phase, 2400)
    strategy_blocks = metadata.get("distilled_strategy_blocks")
    if isinstance(strategy_blocks, dict):
        strategy_block = strategy_blocks.get(phase)
        if isinstance(strategy_block, str) and strategy_block.strip():
            parts.append(strategy_block.strip())
    blocks = metadata.get("distilled_design_reference_blocks")
    if isinstance(blocks, dict):
        block = blocks.get(phase)
        if isinstance(block, str) and block.strip():
            parts.append(block.strip())
    block = metadata.get("distilled_design_reference_block")
    if phase == "architecture" and isinstance(block, str) and block.strip():
        parts.append(block.strip())
    if parts:
        per_part_budget = max(max_chars // len(parts), 800)
        trimmed_parts = [
            _trim_distilled_reference_part(part, max_chars=per_part_budget)
            for part in parts
        ]
        # Strip off-genre / fallback markers so they neither leak into the
        # kernel via LLM echo nor trip the story_design_kernel_gate.
        block = sanitize_distilled_leak("\n\n".join(trimmed_parts))
        if not block.strip():
            return ""
        return "\n\n" + block + "\n"
    return ""


def _render_template(template: str, variables: dict[str, str]) -> str:
    """Safe str.format_map that ignores missing keys and returns '' for empty templates."""
    if not template:
        return ""
    try:
        return template.format_map(variables)
    except (KeyError, IndexError, ValueError):
        return template


def _repair_truncated_json(raw: str) -> str | None:
    """Best-effort repair of a JSON string truncated mid-array.

    Pattern seen in production (2026-04-17): MiniMax-M2.7 hits its
    ``max_tokens`` ceiling while emitting a large ``"chapters": [ … ]``
    array, leaving an incomplete trailing object that breaks parsing.

    Strategy:
    1. Find the outermost opening brace/bracket.
    2. Walk the string, tracking brace/bracket depth while respecting
       string literals and escape sequences.
    3. Remember the position of the last character where the parser
       was at a "clean" boundary — i.e. right after a closing ``}`` or
       ``]`` at the same depth as the array we're filling.
    4. Truncate after that boundary and close any still-open containers.

    Returns the repaired JSON string, or ``None`` if the input is
    malformed beyond easy repair.
    """
    stripped = raw.strip()
    if not stripped or stripped[0] not in "{[":
        return None
    stack: list[str] = []
    in_string = False
    escape = False
    last_clean = -1
    for i, ch in enumerate(stripped):
        if escape:
            escape = False
            continue
        if ch == "\\" and in_string:
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch in "{[":
            stack.append(ch)
        elif ch in "}]":
            if not stack:
                return None
            opener = stack.pop()
            if (opener == "{" and ch != "}") or (opener == "[" and ch != "]"):
                return None
            # A clean boundary inside an array is right after a child object
            # closes while the array itself is still open on the stack.
            if stack and stack[-1] == "[" and opener == "{":
                last_clean = i
    if last_clean == -1:
        return None
    # Slice up to and including the last complete child object, then
    # walk the stack state *at that position* to close whatever is still
    # open outside it.  Re-scan to compute the stack at ``last_clean``.
    stack2: list[str] = []
    in_string = False
    escape = False
    for ch in stripped[: last_clean + 1]:
        if escape:
            escape = False
            continue
        if ch == "\\" and in_string:
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch in "{[":
            stack2.append(ch)
        elif ch in "}]" and stack2:
            stack2.pop()
    closing = "".join("}" if opener == "{" else "]" for opener in reversed(stack2))
    return stripped[: last_clean + 1] + closing


def _find_balanced_json_substring(text: str, opening: str, closing: str) -> str | None:
    """Find the first balanced ``opening...closing`` substring in ``text``.

    Walks from the first occurrence of ``opening``, tracking depth while
    respecting JSON string literals and their escapes.  Returns the
    minimal balanced substring, or ``None`` if no balanced pair exists.

    Rationale — 2026-04-21 production failure:
    MiniMax-M2.7 occasionally wraps the real JSON in prose that also
    contains stray braces (e.g. an inline "{X}" example).  The naïve
    ``str.rfind(closing)`` strategy grabs the trailing stray and returns
    an unparseable substring.  Brace-matching is O(n) and immune to
    trailing noise.
    """
    start = text.find(opening)
    if start == -1:
        return None
    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if escape:
            escape = False
            continue
        if in_string:
            if ch == "\\":
                escape = True
                continue
            if ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
            continue
        if ch == opening:
            depth += 1
        elif ch == closing:
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


def _strip_markdown_fences(text: str) -> str:
    """Strip leading/trailing ``` fences (with or without ``json`` tag).

    Handles the common MiniMax-M2.7 output shape:
        ``` (or ```json) + newline + JSON + newline + ```
    Does not attempt to strip intra-content fences — balanced-brace
    extraction handles those cases naturally.
    """
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    # Drop opening fence line (everything up to the first newline after ```)
    newline_idx = stripped.find("\n")
    if newline_idx == -1:
        return stripped
    body = stripped[newline_idx + 1 :]
    # Drop trailing ``` (possibly with surrounding whitespace)
    body = body.rstrip()
    if body.endswith("```"):
        body = body[: -len("```")].rstrip()
    return body


def _extract_json_payload(text: str) -> Any:
    stripped = text.strip()
    if not stripped:
        raise ValueError("Planner returned empty content.")

    # Strip surrounding markdown fences so the direct parse works on the
    # common happy-path MiniMax output.
    unfenced = _strip_markdown_fences(stripped)
    try:
        return json.loads(unfenced)
    except json.JSONDecodeError:
        pass

    # Strategy 1: balanced brace/bracket matching from the first opener.
    # This is robust against prose prefixes *and* prose suffixes that
    # happen to contain stray closers — unlike ``rfind(closing)`` which
    # is fooled by trailing example text.
    for opening, closing in (("{", "}"), ("[", "]")):
        candidate = _find_balanced_json_substring(unfenced, opening, closing)
        if candidate is not None:
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                continue

    # Strategy 2 (legacy): rfind-based widest-span scan.  Kept as a
    # fallback in case brace-matching missed an edge case (e.g. JSON
    # containing literal unescaped control characters that make the
    # balanced scan's string-literal tracking over-consume).
    for opening, closing in (("{", "}"), ("[", "]")):
        start = unfenced.find(opening)
        end = unfenced.rfind(closing)
        if start != -1 and end != -1 and end > start:
            try:
                return json.loads(unfenced[start : end + 1])
            except json.JSONDecodeError:
                continue

    # Strategy 3: tolerate truncation at max_tokens — drop the incomplete
    # trailing object and close any still-open containers.
    repaired = _repair_truncated_json(unfenced)
    if repaired is not None:
        try:
            result = json.loads(repaired)
        except json.JSONDecodeError:
            pass
        else:
            logger.warning(
                "Planner output repaired after truncation (orig=%d bytes, repaired=%d bytes).",
                len(unfenced),
                len(repaired),
            )
            return result

    # Strategy 4: json-repair fallback for structurally malformed LLM
    # output.  Handles cases the prior strategies cannot: extra openers
    # (e.g. ``{ {`` before an object inside an array), trailing commas,
    # unquoted keys, single-quoted strings — common MiniMax-M2.7 glitches
    # observed in production 2026-04-21 (superhero-fiction-1776147970,
    # volume_8_chapter_outline attempt 1).  ``json_repair`` is a project
    # dependency (pyproject.toml: ``json-repair>=0.39.1,<1.0``).
    try:
        from json_repair import repair_json

        repaired_str = repair_json(unfenced)
        if repaired_str:
            result = json.loads(repaired_str)
            logger.warning(
                "Planner output repaired via json-repair (orig=%d bytes, repaired=%d bytes).",
                len(unfenced),
                len(repaired_str),
            )
            return result
    except Exception:
        pass

    raise ValueError("Planner output does not contain valid JSON.")


def _persist_failing_planner_output(
    *,
    project: ProjectModel,
    logical_name: str,
    attempt: int,
    content: str,
    error: Exception,
) -> None:
    """Persist the raw LLM response that failed ``_extract_json_payload``.

    Rationale — 2026-04-21 production incident: ``response_payload_ref``
    on ``LlmRunModel`` is declared but never populated, so the actual
    MiniMax-M2.7 output that defeats the parser is lost on retry. This
    helper writes each failing attempt to ``artifacts/planner_failures/``
    so we can root-cause format regressions offline without waiting for
    the failure to recur in production.

    Best-effort: any I/O error is swallowed so diagnostic logging can
    never crash the planner pipeline.
    """
    try:
        from datetime import datetime

        base_dir = Path("/app/artifacts") / "planner_failures"
        if not base_dir.exists():
            # Host-dev fallback when not running inside Docker.
            base_dir = Path("artifacts") / "planner_failures"
        base_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ")
        slug = getattr(project, "slug", "unknown")
        filename = f"{timestamp}_{slug}_{logical_name}_attempt{attempt}.txt"
        target = base_dir / filename
        header = (
            f"# Planner artifact parse failure\n"
            f"# project_slug: {slug}\n"
            f"# logical_name: {logical_name}\n"
            f"# attempt: {attempt}\n"
            f"# error: {type(error).__name__}: {error}\n"
            f"# content_len: {len(content)}\n"
            f"# ---- RAW CONTENT BELOW ----\n\n"
        )
        target.write_text(header + content, encoding="utf-8")
        logger.warning(
            "Persisted failing planner output to %s (artifact=%s, attempt=%d, len=%d).",
            target,
            logical_name,
            attempt,
            len(content),
        )
    except Exception as persist_exc:  # pragma: no cover - diagnostic best-effort
        logger.warning(
            "Failed to persist planner failure for artifact=%s attempt=%d: %s",
            logical_name,
            attempt,
            persist_exc,
        )


def _planner_length_retry_prompt(user_prompt: str, logical_name: str) -> str:
    compact_contract = (
        "\n\n[STRICT RETRY AFTER TRUNCATED OUTPUT]\n"
        "The previous response hit the output token limit. Regenerate a compact, complete JSON object only. "
        "Do not include optional empty/null-heavy subobjects. Keep every free-text field to one concise sentence. "
        "Prefer fewer, sharper entries over exhaustive lists. The JSON must close cleanly."
    )
    if logical_name == "cast_spec":
        compact_contract += (
            "\nFor CastSpec specifically: output protagonist, one primary antagonist, "
            "2 antagonist_forces, 3-4 supporting_cast entries, and a compact conflict_map. "
            "For each character include only plot function, goal/desire, flaw, voice_profile, "
            "moral_framework, relationships, gender, pronoun_set_zh, pronoun_set_en, and name_reasoning. "
            "Do not emit large psych_profile/life_history/social_network/family_imprint objects unless they contain concrete story-useful values."
        )
    return f"{user_prompt.rstrip()}{compact_contract}"


def _merge_planning_payload(fallback_payload: Any, generated_payload: Any) -> Any:
    """Merge LLM output with fallback — **LLM-primary** strategy.

    The LLM output is used as the base.  Fallback values only fill in
    fields that the LLM omitted or left empty, rather than the other way
    around.  This ensures the LLM's creative choices dominate the final
    output and the fallback only acts as a safety net for missing fields.
    """
    if generated_payload is None:
        return copy.deepcopy(fallback_payload)

    if isinstance(fallback_payload, dict):
        if not isinstance(generated_payload, dict):
            return copy.deepcopy(fallback_payload)
        # Start from LLM output, fill gaps from fallback
        merged = copy.deepcopy(generated_payload)
        for key, fb_value in fallback_payload.items():
            if key not in merged:
                # LLM omitted this field entirely — fill from fallback
                merged[key] = copy.deepcopy(fb_value)
            elif merged[key] is None or (isinstance(merged[key], str) and not merged[key].strip()):
                # LLM left this field empty — fill from fallback
                merged[key] = copy.deepcopy(fb_value)
            elif isinstance(fb_value, dict) and isinstance(merged[key], dict):
                # Recursively fill sub-fields
                merged[key] = _merge_planning_payload(fb_value, merged[key])
            elif isinstance(fb_value, list) and isinstance(merged[key], list):
                merged[key] = _merge_planning_payload(fb_value, merged[key])
        return merged

    if isinstance(fallback_payload, list):
        if not isinstance(generated_payload, list):
            return copy.deepcopy(fallback_payload)
        if not generated_payload:
            return copy.deepcopy(fallback_payload)
        # If both lists have matching dicts, recursively fill per-element
        if len(fallback_payload) == len(generated_payload) and all(
            isinstance(fallback_item, dict) and isinstance(generated_item, dict)
            for fallback_item, generated_item in zip(
                fallback_payload, generated_payload, strict=False
            )
        ):
            return [
                _merge_planning_payload(fallback_item, generated_item)
                for fallback_item, generated_item in zip(
                    fallback_payload, generated_payload, strict=False
                )
            ]
        # LLM provided a different-length list — trust the LLM's structure
        return copy.deepcopy(generated_payload)

    if isinstance(generated_payload, str) and not generated_payload.strip():
        return copy.deepcopy(fallback_payload)

    return copy.deepcopy(generated_payload)


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _mapping_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]


def _planner_title_text(value: Any, *, max_chars: int = 1200) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        text = value
    else:
        try:
            text = json.dumps(value, ensure_ascii=False, sort_keys=True)
        except Exception:
            text = str(value)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:max_chars]


def _extract_planning_title_terms(text: str, markers: tuple[str, ...]) -> list[str]:
    found: list[str] = []
    for marker in markers:
        if marker in text and marker not in found:
            found.append(marker)
    return found[:8]


def _extract_title_identity_from_planning(
    premise: str,
    cast_spec_payload: Any,
    story_text: str,
) -> str:
    cast = _mapping(cast_spec_payload)
    protagonist = _mapping(cast.get("protagonist"))
    for key in ("identity", "occupation", "profession", "role_label", "function"):
        text = _non_empty_string(protagonist.get(key), "")
        if text and text != "protagonist":
            return text
    for pattern in (
        r"「([^」]{2,12}(?:验房师|师|官|员|士|客|者))」",
        r"([\u4e00-\u9fff]{2,12}(?:验房师|风水师|炼丹师|剑修|法医|捕头|秘书|神医|道士|执照))",
    ):
        match = re.search(pattern, premise)
        if match:
            return match.group(1).strip()
    cast_text = _planner_title_text(cast_spec_payload, max_chars=1600)
    for pattern in (
        r'"identity"\s*:\s*"([^"]{2,24})"',
        r'"role"\s*:\s*"([^"]{2,24})"',
    ):
        match = re.search(pattern, cast_text)
        if match:
            return match.group(1).strip()
    match = re.search(r"([\u4e00-\u9fff]{2,12}(?:师|官|员|士|客|者))", story_text)
    return match.group(1).strip() if match else ""


def _extract_title_protagonist(cast_spec_payload: Any, premise: str) -> str:
    cast = _mapping(cast_spec_payload)
    protagonist = _mapping(cast.get("protagonist"))
    name = _non_empty_string(protagonist.get("name"), "")
    if name and name not in {"主角", "读者", "平台"}:
        return name
    cast_text = _planner_title_text(cast_spec_payload, max_chars=1600)
    for pattern in (r'"name"\s*:\s*"([^"]{2,6})"', r"主角[：:]\s*([\u4e00-\u9fff]{2,6})"):
        match = re.search(pattern, cast_text)
        if match:
            name = match.group(1).strip()
            if name not in {"主角", "读者", "平台"}:
                return name
    match = re.search(r"([\u4e00-\u9fff]{2,4})挂靠", premise)
    return match.group(1).strip() if match else ""


def _build_story_grounded_title_profile(
    project: ProjectModel,
    *,
    premise: str,
    book_spec_payload: Any,
    cast_spec_payload: Any,
    story_design_payload: Any,
    volume_plan_payload: Any,
    prewrite_payload: Any | None = None,
) -> dict[str, Any]:
    metadata = project.metadata_json or {}
    writing_profile = _mapping(metadata.get("writing_profile"))
    market = _mapping(writing_profile.get("market"))
    volume_plan = _mapping_list(volume_plan_payload)
    first_volume = volume_plan[0] if volume_plan else {}
    story_text = " ".join(
        filter(
            None,
            [
                premise,
                _planner_title_text(book_spec_payload, max_chars=700),
                _planner_title_text(story_design_payload, max_chars=900),
                _planner_title_text(first_volume, max_chars=900),
                _planner_title_text(prewrite_payload, max_chars=500),
            ],
        )
    )
    protagonist = _extract_title_protagonist(cast_spec_payload, premise)
    identity = _extract_title_identity_from_planning(
        premise, cast_spec_payload, story_text
    )
    action_terms = _extract_planning_title_terms(
        story_text,
        (
            "签出",
            "签发",
            "强制复检",
            "双人交叉验房",
            "指出违规",
            "钉死",
            "倒查",
            "触发复检",
            "写进现场笔记",
            "条款反杀",
        ),
    )
    object_terms = _extract_planning_title_terms(
        story_text,
        (
            "验房报告",
            "终版报告",
            "现场笔记",
            "岗位说明书",
            "豁免清单",
            "合规台账",
            "执照扣分",
            "48小时",
            "写字楼",
        ),
    )
    stakes_terms = _extract_planning_title_terms(
        story_text,
        (
            "执照扣分",
            "吊销线",
            "冷门岗位",
            "职业边界",
            "越权",
            "曝光度",
            "存续审查",
            "签约前",
        ),
    )
    return {
        "language": project.language or "zh-CN",
        "primary_title": project.title,
        "primary_category": project.genre or "",
        "secondary_category": project.sub_genre or "",
        "target_platform": market.get("platform_target") or "",
        "tags": [
            tag
            for tag in _string_list(metadata.get("tags"))
            if tag and tag not in {project.genre, project.sub_genre}
        ][:8],
        "logline": premise,
        "reader_promise": market.get("reader_promise") or "",
        "story_title_dna": {
            "protagonist": protagonist,
            "identity": identity,
            "opening": premise,
            "central_action": first_volume.get("volume_goal") or "",
            "conflict": first_volume.get("volume_obstacle") or "",
            "stakes": _mapping(first_volume.get("volume_resolution")).get("cost_paid")
            or "",
            "payoff": first_volume.get("volume_climax")
            or first_volume.get("volume_goal")
            or "",
        },
        "title_anchor_groups": {
            "identity": [term for term in [identity, protagonist] if term],
            "action": action_terms,
            "object": object_terms,
            "stakes": stakes_terms,
        },
    }


def _apply_project_title_workflow_metadata(
    project: ProjectModel,
    *,
    metadata: Mapping[str, Any],
    title_record: Mapping[str, Any],
    title_workflow: Mapping[str, Any],
) -> None:
    workflow_candidates = (
        title_workflow.get("candidates")
        if isinstance(title_workflow.get("candidates"), list)
        else []
    )
    next_metadata = dict(metadata)
    next_metadata["title_workflow_primary"] = dict(title_record)
    next_metadata["title_workflow"] = dict(title_workflow)
    next_metadata["title_candidates"] = workflow_candidates
    writing_profile = dict(_mapping(next_metadata.get("writing_profile")))
    market = dict(_mapping(writing_profile.get("market")))
    market["title_workflow_primary"] = dict(title_record)
    market["title_workflow_summary"] = {
        "schema_version": title_workflow.get("schema_version"),
        "candidate_policy": title_workflow.get("candidate_policy"),
        "candidate_count": len(workflow_candidates),
        "platform_count": title_workflow.get("platform_count"),
        "recommended_primary_title": dict(title_record),
    }
    writing_profile["market"] = market
    next_metadata["writing_profile"] = writing_profile
    project.metadata_json = next_metadata


async def _refresh_story_grounded_project_title(
    session: AsyncSession,
    settings: AppSettings,
    *,
    project: ProjectModel,
    premise: str,
    book_spec_payload: Any,
    cast_spec_payload: Any,
    story_design_payload: Any,
    volume_plan_payload: Any,
    prewrite_payload: Any | None,
    workflow_run_id: UUID,
) -> tuple[str, bool, UUID | None, dict[str, Any]]:
    if is_english_language(project.language):
        return project.title, False, None, {"skipped": "english_title_path"}
    if not getattr(settings.generation, "title_llm_revision_enabled", True):
        return project.title, False, None, {"skipped": "title_llm_revision_disabled"}

    profile = _build_story_grounded_title_profile(
        project,
        premise=premise,
        book_spec_payload=book_spec_payload,
        cast_spec_payload=cast_spec_payload,
        story_design_payload=story_design_payload,
        volume_plan_payload=volume_plan_payload,
        prewrite_payload=prewrite_payload,
    )
    current_title = project.title
    metadata = project.metadata_json or {}
    writing_profile = _mapping(metadata.get("writing_profile"))
    market = _mapping(writing_profile.get("market"))
    title_meta = _mapping(market.get("title_workflow_primary")) or _mapping(
        metadata.get("title_workflow_primary")
    )
    issue = title_readability_issue(current_title)
    has_full_title_workflow = isinstance(metadata.get("title_workflow"), Mapping) and isinstance(
        metadata.get("title_candidates"), list
    )
    should_refresh = bool(issue or title_meta.get("llm_revised") or not has_full_title_workflow)
    if not should_refresh:
        return current_title, False, None, {
            "skipped": "current_title_not_marked_weak",
            "title_anchor_groups": profile.get("title_anchor_groups"),
        }

    target_platform = str(profile.get("target_platform") or "")
    current_workflow_profile = dict(profile)
    current_workflow_profile["primary_title"] = current_title
    current_title_workflow = build_platform_title_workflow(
        current_workflow_profile,
        target_platform=target_platform,
    )
    current_primary = _mapping(current_title_workflow.get("recommended_primary_title"))
    current_evaluation = _mapping(current_primary.get("title_evaluation"))
    # Do NOT short-circuit on a *provisional* primary (a story-DNA/template
    # stand-in produced when the model title was rejected). It carries
    # requires_llm_revision=True and must be regenerated by the revision step
    # below — otherwise a mechanical genre-label template can ship as the final
    # project title (the 2026-06-03 regression). Only a non-provisional, passing
    # title may skip revision.
    _current_is_provisional = bool(
        current_primary.get("provisional") or current_primary.get("requires_llm_revision")
    )
    if (
        _non_empty_string(current_primary.get("title"), "") == current_title
        and current_evaluation.get("decision") == "pass"
        and not _current_is_provisional
    ):
        workflow_candidates = (
            current_title_workflow.get("candidates")
            if isinstance(current_title_workflow.get("candidates"), list)
            else []
        )
        title_record = {
            "title": current_title,
            "pre_revision_title": title_meta.get("pre_revision_title"),
            "llm_revised": bool(title_meta.get("llm_revised")),
            "llm_run_id": title_meta.get("llm_run_id"),
            "source": title_meta.get("source") or "story_grounded_planning_revision",
            "platform_label": current_primary.get("platform_label")
            or current_title_workflow.get("platform_label"),
            "scope_label": current_primary.get("scope_label"),
            "pattern": current_primary.get("pattern"),
            "title_evaluation": current_evaluation,
            "anchor_groups": profile.get("title_anchor_groups"),
            "candidate_count": len(workflow_candidates),
            "platform_count": current_title_workflow.get("platform_count"),
            "candidate_policy": current_title_workflow.get("candidate_policy"),
        }
        _apply_project_title_workflow_metadata(
            project,
            metadata=metadata,
            title_record=title_record,
            title_workflow=current_title_workflow,
        )
        return current_title, False, None, {
            "skipped": "current_title_passed_full_title_workflow",
            "title_evaluation": current_evaluation,
            "title_anchor_groups": profile.get("title_anchor_groups"),
            "candidate_count": len(workflow_candidates),
        }
    system_prompt, user_prompt = build_story_grounded_title_revision_messages(
        profile,
        current_title=current_title,
        target_platform=target_platform,
        reason=issue or "conception_title_needs_planning_grounding",
    )
    completion = await complete_text(
        session,
        settings,
        LLMCompletionRequest(
            logical_role="editor",
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            fallback_response=current_title,
            prompt_template="title_story_grounded_revision",
            prompt_version="1.0",
            project_id=project.id,
            workflow_run_id=workflow_run_id,
            max_tokens_override=512,
            metadata={
                "project_slug": project.slug,
                "current_title": current_title,
                "title_anchor_groups": profile.get("title_anchor_groups"),
                "reason": issue or "planning_grounding",
            },
        ),
    )
    adopted, was_revised = finalize_revised_title(
        profile,
        current_title,
        completion.content,
        target_platform=target_platform,
    )
    evidence = {
        "previous_title": current_title,
        "adopted_title": adopted,
        "was_revised": was_revised,
        "llm_run_id": str(completion.llm_run_id) if completion.llm_run_id else None,
        "reason": issue or "planning_grounding",
        "title_anchor_groups": profile.get("title_anchor_groups"),
    }
    if was_revised:
        workflow_profile = dict(profile)
        workflow_profile["primary_title"] = adopted
        title_workflow = build_platform_title_workflow(
            workflow_profile,
            target_platform=target_platform,
        )
        primary_candidate = _mapping(title_workflow.get("recommended_primary_title"))
        primary_evaluation = _mapping(primary_candidate.get("title_evaluation"))
        if (
            _non_empty_string(primary_candidate.get("title"), "")
            and primary_evaluation.get("decision") == "pass"
        ):
            adopted = _non_empty_string(primary_candidate.get("title"), adopted)
        else:
            adopted_evaluation = _mapping(
                title_workflow.get("candidate_evaluations", {}).get(adopted)
                if isinstance(title_workflow.get("candidate_evaluations"), Mapping)
                else {}
            )
            if adopted_evaluation.get("decision") != "pass":
                evidence["blocked"] = "title_workflow_primary_not_pass"
                evidence["title_evaluation"] = adopted_evaluation
                return current_title, False, completion.llm_run_id, evidence
            primary_candidate = {
                "title": adopted,
                "title_evaluation": adopted_evaluation,
                "platform_label": title_workflow.get("platform_label"),
                "scope_label": "目标平台",
                "pattern": "story_grounded_planning_revision",
            }
        project.title = adopted
        metadata = dict(project.metadata_json or {})
        workflow_candidates = (
            title_workflow.get("candidates")
            if isinstance(title_workflow.get("candidates"), list)
            else []
        )
        title_record = {
            "title": adopted,
            "pre_revision_title": current_title,
            "llm_revised": True,
            "llm_run_id": str(completion.llm_run_id) if completion.llm_run_id else None,
            "source": "story_grounded_planning_revision",
            "platform_label": primary_candidate.get("platform_label")
            or title_workflow.get("platform_label"),
            "scope_label": primary_candidate.get("scope_label"),
            "pattern": primary_candidate.get("pattern"),
            "title_evaluation": primary_candidate.get("title_evaluation"),
            "anchor_groups": profile.get("title_anchor_groups"),
            "candidate_count": len(workflow_candidates),
            "platform_count": title_workflow.get("platform_count"),
            "candidate_policy": title_workflow.get("candidate_policy"),
        }
        _apply_project_title_workflow_metadata(
            project,
            metadata=metadata,
            title_record=title_record,
            title_workflow=title_workflow,
        )
    return adopted, was_revised, completion.llm_run_id, evidence


def _non_empty_string(value: Any, default: str) -> str:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return default


def _first_non_empty_text(*values: Any, default: str = "") -> str:
    for value in values:
        text = _non_empty_string(value, "")
        if text:
            return text
    return default


def _require_complete_volume_outline(
    *,
    logical_name: str,
    volume_number: int,
    expected_count: int,
    chapters: list[dict[str, Any]],
) -> None:
    """Fail closed when a generated per-volume outline has the wrong length."""

    if expected_count <= 0:
        return
    actual_count = len(chapters)
    if actual_count == expected_count:
        return
    raise PlannerFallbackError(
        f"Planner artifact '{logical_name}' returned {actual_count}/{expected_count} "
        f"chapters for volume {volume_number}. Refusing to pad or trim with fallback "
        "chapters because that would turn a broken story architecture into valid-looking "
        "downstream input."
    )


def _is_outline_count_contract_error(error: Exception) -> bool:
    message = str(error)
    return "returned " in message and "/" in message and "chapters for volume" in message


def _normalize_generated_outline_titles_or_fail(
    chapters: list[dict[str, Any]],
    *,
    logical_name: str,
    existing_titles: list[tuple[int | None, str]] | None = None,
    near_dup_threshold: float = DEFAULT_NEAR_DUP_THRESHOLD,
) -> None:
    """Validate that every generated chapter has a concrete, unique title.

    Two passes:

    1. **Existence pass** (legacy behavior): map ``chapter_title`` /
       ``subtitle`` aliases onto the canonical ``title`` field. If any
       chapter ends up without a non-empty title, raise
       ``PlannerFallbackError`` listing the affected chapter numbers so
       the repair loop can re-prompt the LLM.

    2. **Uniqueness pass** (new): check every accepted title for exact
       duplicates and near-duplicates (Jaccard similarity over character
       2-grams ``>= near_dup_threshold``) against:
         * other chapters in the same batch,
         * titles in ``existing_titles`` (i.e. earlier volumes already
           persisted for this project).

       Any collision raises ``TitleCollisionError`` with the conflicting
       chapter pairs attached. The repair loop in
       :func:`_outline_repair_directives_from_error` turns those into
       targeted regeneration instructions for the planner.

    Parameters
    ----------
    chapters:
        Mutable list of chapter dicts from the LLM payload.
    logical_name:
        Artifact name used in error messages so the operator can pinpoint
        the failing pipeline step.
    existing_titles:
        Optional sequence of ``(chapter_number_or_None, title)`` pairs
        for chapters already persisted in this project. When provided,
        cross-volume collisions become hard errors instead of silent
        duplicates.
    near_dup_threshold:
        Two titles whose 2-gram Jaccard similarity is ``>= threshold``
        are considered colliding. ``1.0`` disables near-dup detection
        (only catches exact matches). Default 0.7 catches templated
        variants like "苏瑶之名" vs "苏瑶之心".
    """

    # ── Pass 1: existence ────────────────────────────────────────────
    missing: list[Any] = []
    for chapter in chapters:
        alias_title = _first_non_empty_text(
            chapter.get("chapter_title"),
            chapter.get("subtitle"),
        )
        if alias_title and not _non_empty_string(chapter.get("title"), ""):
            chapter["title"] = alias_title
        if not _non_empty_string(chapter.get("title"), ""):
            missing.append(chapter.get("chapter_number") or "?")
    if missing:
        sample = ", ".join(str(item) for item in missing[:10])
        if len(missing) > 10:
            sample += ", ..."
        raise PlannerFallbackError(
            f"Planner artifact '{logical_name}' omitted concrete chapter titles "
            f"for chapters [{sample}]. Refusing fallback title synthesis."
        )

    # ── Pass 2: uniqueness ──────────────────────────────────────────
    # The planner has historically produced template-shaped collisions
    # such as ``铁壁破壁`` reappearing every ~30 chapters even when the
    # prompt explicitly bans the suffix tokens. We catch them here, in
    # code, rather than trusting the LLM to obey the negative list.
    candidates = [
        (
            int(chapter.get("chapter_number") or 0),
            str(chapter.get("title") or "").strip(),
        )
        for chapter in chapters
    ]
    report = find_title_collisions(
        candidates,
        existing_titles=existing_titles or (),
        near_dup_threshold=near_dup_threshold,
    )
    if not report.ok:
        # Build a concise message; the structured ``collisions`` field
        # is what the repair loop actually consumes.
        sample_lines = [
            (
                f"ch{c.chapter_number}: '{c.candidate_title}' "
                f"collides with '{c.conflict_title}'"
                + (
                    f" (ch{c.conflict_chapter_number})"
                    if c.conflict_chapter_number is not None
                    else ""
                )
                + (" [exact]" if c.similarity >= 1.0 else f" [Jaccard {c.similarity:.2f}]")
            )
            for c in report.collisions[:10]
        ]
        suffix = "" if len(report.collisions) <= 10 else f" (+{len(report.collisions) - 10} more)"
        raise TitleCollisionError(
            f"Planner artifact '{logical_name}' produced duplicate chapter titles: "
            + "; ".join(sample_lines)
            + suffix,
            collisions=report.collisions,
        )


def _chapter_outline_identity_manifest(cast_spec: dict[str, Any]) -> list[dict[str, Any]]:
    try:
        from bestseller.services.narrative_contracts import build_identity_manifest

        return build_identity_manifest(cast_spec)
    except Exception:
        logger.debug(
            "Unable to build identity manifest for chapter-outline validation", exc_info=True
        )
        return []


def _outline_identity_token(value: Any) -> str:
    return "".join(_non_empty_string(value, "").lower().split())


def _outline_identity_index(
    identity_manifest: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for identity in identity_manifest:
        if not isinstance(identity, dict):
            continue
        tokens = [
            _non_empty_string(identity.get("name"), ""),
            *_string_list(identity.get("aliases")),
        ]
        for token in tokens:
            normalized = _outline_identity_token(token)
            if normalized:
                index[normalized] = identity
    return index


def _outline_fuzzy_resolve_participant(
    unknown_token: str,
    identity_index: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    """Resolve a LLM-hallucinated participant name to its likely canonical form.

    Production failure mode: ``identity_index`` has ``苏澄`` but the chapter
    outline LLM writes ``姜澄`` (one character swapped). The contract gate
    flags ``PLAN_SCENE_UNKNOWN_PARTICIPANT`` and the project blocks forever.

    Heuristic: accept a match only when **exactly one** cast member shares
    ≥ ``min_overlap_ratio`` of characters with ``unknown_token`` *and* has the
    same length. The same-length constraint plus the "exactly one" rule
    keeps the resolver from silently swallowing genuinely new character
    introductions; ambiguous or partial matches fall through to the
    standard UNKNOWN block so the LLM (or a human) can resolve them.
    """

    if not unknown_token or not identity_index:
        return None
    unknown_chars = set(unknown_token)
    if not unknown_chars:
        return None

    candidates: list[dict[str, Any]] = []
    for known_token, identity in identity_index.items():
        if len(known_token) != len(unknown_token):
            continue
        known_chars = set(known_token)
        if not known_chars:
            continue
        # Sørensen-Dice coefficient on character sets. With same length L
        # the formula reduces to ``shared_chars / L``. For 2-character
        # Chinese names this is 0.5 when exactly one character matches —
        # the typical LLM transcription error (姜澄 → 苏澄).
        shared = len(unknown_chars & known_chars)
        dice = (2 * shared) / (len(unknown_chars) + len(known_chars))
        if dice >= 0.5:
            candidates.append(identity)
    if len(candidates) == 1:
        return candidates[0]
    return None


def _outline_default_protagonist(identity_manifest: list[dict[str, Any]]) -> str:
    for identity in identity_manifest:
        if not isinstance(identity, dict):
            continue
        role = _non_empty_string(identity.get("role"), "").lower()
        name = _non_empty_string(identity.get("name"), "")
        if name and "protagonist" in role:
            return name
    for identity in identity_manifest:
        if isinstance(identity, dict):
            name = _non_empty_string(identity.get("name"), "")
            if name:
                return name
    return "主角"


def _outline_scene_time_missing_or_generic(value: Any) -> bool:
    label = _non_empty_string(value, "")
    if not label:
        return True
    if label in {"章节开场", "章节中段", "章节结尾", "章节补充钩子"}:
        return True
    return label.startswith("章节场景")


def _outline_chapter_label(chapter: Any) -> str:
    return _first_non_empty_text(
        getattr(chapter, "title", None),
        getattr(chapter, "chapter_goal", None),
        getattr(chapter, "main_conflict", None),
        getattr(chapter, "hook_description", None),
        default=f"第{getattr(chapter, 'chapter_number', '?')}章",
    )


def _outline_scene_time_repair(chapter: Any, scene: Any) -> str:
    return (
        f"第{getattr(chapter, 'chapter_number', '?')}章"
        f"「{_outline_chapter_label(chapter)}」场景{getattr(scene, 'scene_number', '?')}"
    )


def _outline_scene_story_repair(chapter: Any, scene: Any) -> str:
    participants = [
        item for item in getattr(scene, "participants", []) if _non_empty_string(item, "")
    ]
    actors = "、".join(participants[:3]) if participants else "主角"
    base = _first_non_empty_text(
        getattr(chapter, "main_conflict", None),
        getattr(chapter, "hook_description", None),
        getattr(chapter, "chapter_goal", None),
        getattr(chapter, "title", None),
        default=f"第{getattr(chapter, 'chapter_number', '?')}章核心压力",
    )
    return (
        f"第{getattr(chapter, 'chapter_number', '?')}章场景{getattr(scene, 'scene_number', '?')}"
        f"让{actors}围绕「{base}」完成一次可见行动、信息交换或代价承担。"
    )


def _outline_visible_story_candidate(*values: Any) -> str:
    try:
        from bestseller.services.narrative_contracts import _has_meta_planning_language
    except Exception:
        _has_meta_planning_language = lambda _value: False  # type: ignore[assignment]

    for value in values:
        for raw_line in str(value or "").splitlines():
            line = _non_empty_string(raw_line, "")
            if not line:
                continue
            if re.match(r"^(开篇合同|30%解锁合同|能力代价合同|爽点合同|收束合同|连续性合同)[:：]", line):
                continue
            if re.match(r"^(第\d+段)?前\d+字", line):
                continue
            if "合同" in line or "读者" in line:
                continue
            if _has_meta_planning_language(line):
                continue
            return line
    return ""


def _outline_chapter_goal_repair(chapter: Any, *, protagonist_name: str) -> str:
    candidate = _outline_visible_story_candidate(
        getattr(chapter, "main_conflict", None),
        getattr(chapter, "opening_situation", None),
        getattr(chapter, "hook_description", None),
    )
    if candidate:
        return candidate
    label = _outline_chapter_label(chapter)
    return f"{protagonist_name}在「{label}」中面对当前压力并做出可见选择。"


def _outline_chapter_conflict_repair(chapter: Any, *, protagonist_name: str) -> str:
    candidate = _outline_visible_story_candidate(
        getattr(chapter, "opening_situation", None),
        getattr(chapter, "chapter_goal", None),
        getattr(chapter, "hook_description", None),
    )
    if candidate:
        return candidate
    label = _outline_chapter_label(chapter)
    return f"{protagonist_name}必须在「{label}」里处理当前阻力，否则失去关键线索。"


def _outline_chapter_hook_repair(chapter: Any, *, protagonist_name: str) -> str:
    candidate = _outline_visible_story_candidate(
        getattr(chapter, "hook_description", None),
        getattr(chapter, "main_conflict", None),
    )
    if candidate:
        return candidate
    return f"{protagonist_name}拿到新的证据，但这条证据把压力推向下一步行动。"


def _outline_purpose_character_names(
    story_purpose: str,
    identity_index: dict[str, dict[str, Any]],
) -> tuple[str, ...]:
    if not story_purpose or not identity_index:
        return ()
    try:
        from bestseller.services.narrative_contracts import _extract_purpose_character_names

        return _extract_purpose_character_names(story_purpose, identity_index)
    except Exception:
        logger.debug(
            "Unable to extract purpose character names during outline repair", exc_info=True
        )
        return ()


def _repair_generated_volume_outline_contract_inputs(
    batch: Any,
    *,
    identity_manifest: list[dict[str, Any]],
    protagonist_flaw_default: str | None = None,
) -> int:
    """Repair deterministic scene-card fields before planner contract validation.

    This keeps the identity manifest locked: unknown ad-hoc participants are
    removed instead of being silently promoted into canonical cast.

    It also backfills required-but-frequently-empty fields the model leaves
    blank (scene ``entry_state``/``exit_state`` dicts, chapter
    ``protagonist_flaw``) so the downstream planning-readiness gate sees a
    complete outline instead of hard-blocking the framework's own output.
    """

    protagonist_name = _outline_default_protagonist(identity_manifest)
    identity_index = _outline_identity_index(identity_manifest)
    flaw_default = _non_empty_string(
        protagonist_flaw_default,
        "在压力下习惯独自硬扛、用旧策略应对，给本章留下可被利用的破绽。",
    )
    repaired = 0

    def _mc(scene_obj: Any, *keys: str) -> str | None:
        """Pull the first non-empty value from the scene's methodology_contract.

        The planner LLM frequently nests the concrete scene data (cut_point,
        conflict_stakes, signature_image, action_sequence, …) inside
        ``methodology_contract`` rather than on the top-level scene fields the
        backfill historically read. Reading both salvages real, specific anchors
        instead of falling through to the generic placeholder that starved the
        writer of a concrete scene contract.
        """
        contract = getattr(scene_obj, "methodology_contract", None)
        if not isinstance(contract, Mapping):
            return None
        for key in keys:
            value = contract.get(key)
            text = _non_empty_string(value, "") if not isinstance(value, (list, tuple)) else " ".join(
                str(v) for v in value if v
            )
            if text and text.strip():
                return text.strip()
        return None

    for chapter in getattr(batch, "chapters", []) or []:
        previous_exit_summary: str | None = None
        for scene in getattr(chapter, "scenes", []) or []:
            # Backfill scene entry/exit state (gate requires non-empty dicts).
            # Prefer concrete anchors — top-level fields first, then the rich
            # methodology_contract — and only fall back to the generic
            # placeholder when the scene truly carries nothing specific.
            if not getattr(scene, "entry_state", None):
                scene.entry_state = {
                    "summary": _first_non_empty_text(
                        previous_exit_summary,
                        getattr(scene, "protagonist_state", None),
                        getattr(scene, "concrete_goal", None),
                        _mc(scene, "conflict_stakes", "information_held_back", "spotlight_character"),
                        default="承接上一拍未决的压力与选择进入本拍。",
                    )
                }
                repaired += 1
            exit_summary = _first_non_empty_text(
                getattr(scene, "cut_point", None),
                _mc(scene, "cut_point", "reveal_mode", "signature_image", "action_sequence"),
                getattr(scene, "concrete_goal", None),
                getattr(scene, "protagonist_state", None),
                default="本拍结束时主角状态发生可见变化，并挂出新的压力或选择。",
            )
            if not getattr(scene, "exit_state", None):
                scene.exit_state = {"summary": exit_summary}
                repaired += 1
            # Derive a tail hook from the scene's own cut_point / hook_type when
            # the planner left hook_requirement blank, so the writer gets a
            # concrete ending target instead of nothing.
            if not getattr(scene, "hook_requirement", None):
                _hook = _first_non_empty_text(
                    getattr(scene, "cut_point", None),
                    _mc(scene, "cut_point", "hook_type"),
                    default="",
                )
                if _hook:
                    scene.hook_requirement = _hook
                    repaired += 1
            # Derive key_dialogue_beats from the scene's action_sequence when the
            # planner left the beat list empty — concrete action beats give the
            # writer scene structure instead of nothing to render.
            if not getattr(scene, "key_dialogue_beats", None):
                _seq = getattr(scene, "action_sequence", None)
                _beats: list[str] = []
                if isinstance(_seq, (list, tuple)):
                    _beats = [str(x).strip() for x in _seq if str(x).strip()][:4]
                if not _beats:
                    _mc_seq = _mc(scene, "action_sequence")
                    if _mc_seq:
                        _beats = [_mc_seq]
                if _beats:
                    scene.key_dialogue_beats = _beats
                    repaired += 1
            # Derive a sensory anchor from the scene's signature_image.
            if not getattr(scene, "sensory_anchors", None):
                _sig = _first_non_empty_text(
                    getattr(scene, "signature_image", None),
                    _mc(scene, "signature_image"),
                    default="",
                )
                if _sig:
                    scene.sensory_anchors = {"signature_image": _sig}
                    repaired += 1
            previous_exit_summary = (
                (getattr(scene, "exit_state", None) or {}).get("summary") or exit_summary
            )

            if _outline_scene_time_missing_or_generic(getattr(scene, "time_label", None)):
                scene.time_label = _outline_scene_time_repair(chapter, scene)
                repaired += 1

            participant_tokens: set[str] = set()
            repaired_participants: list[str] = []
            for raw_participant in getattr(scene, "participants", []) or []:
                token = _outline_identity_token(raw_participant)
                if not token:
                    continue
                if identity_index:
                    identity = identity_index.get(token)
                    if identity is None:
                        repaired += 1
                        continue
                    participant = _non_empty_string(identity.get("name"), str(raw_participant))
                else:
                    participant = _non_empty_string(raw_participant, "")
                participant_token = _outline_identity_token(participant)
                if participant and participant_token not in participant_tokens:
                    participant_tokens.add(participant_token)
                    repaired_participants.append(participant)

            if not repaired_participants:
                repaired_participants = [protagonist_name]
                participant_tokens = {_outline_identity_token(protagonist_name)}
                repaired += 1
            if repaired_participants != list(getattr(scene, "participants", []) or []):
                scene.participants = repaired_participants

            purpose = dict(getattr(scene, "purpose", None) or {})
            story_purpose = _non_empty_string(purpose.get("story"), "")
            if story_purpose and identity_index:
                for referenced_name in _outline_purpose_character_names(
                    story_purpose, identity_index
                ):
                    token = _outline_identity_token(referenced_name)
                    identity = identity_index.get(token)
                    canonical = _non_empty_string(
                        identity.get("name") if identity else referenced_name,
                        referenced_name,
                    )
                    canonical_token = _outline_identity_token(canonical)
                    if canonical and canonical_token not in participant_tokens:
                        scene.participants.append(canonical)
                        participant_tokens.add(canonical_token)
                        repaired += 1
            elif not story_purpose:
                purpose["story"] = _outline_scene_story_repair(chapter, scene)
                repaired += 1

            if not _non_empty_string(purpose.get("emotion"), ""):
                # Derive a scene-specific emotion task from the story purpose
                # instead of a fixed placeholder — the old constant sentence
                # produced identical low-signal emotion tasks on every thin
                # card, and the writer got zero guidance on WHOSE fear/want
                # this scene turns on.
                _story_hint = _non_empty_string(purpose.get("story"), "")[:40]
                purpose["emotion"] = (
                    f"围绕「{_story_hint}」写出当事人一个具体的怕、要或误判，"
                    "让读者看见情绪来源，并把选择或代价推到下一拍。"
                    if _story_hint
                    else "写出当事人此刻一个具体的怕、要或误判，让读者看见情绪来源。"
                )
                repaired += 1
            if purpose != getattr(scene, "purpose", None):
                scene.purpose = purpose

        # Backfill chapter protagonist_flaw (front-ten gate requires it). The
        # protagonist's flaw is a stable trait, so the canonical cast flaw is
        # the correct value rather than the chapter-specific inner state.
        if not _non_empty_string(getattr(chapter, "protagonist_flaw", None), ""):
            chapter.protagonist_flaw = flaw_default
            repaired += 1

    return repaired


_OUTLINE_CONTRACT_VALUE_MAX_CHARS = 400
_OUTLINE_SCENE_PARTICIPANT_CAP = 5


def _outline_first_clauses(text: Any, count: int = 1) -> str:
    parts = [
        part.strip()
        for part in re.split(r"[；;。]", _non_empty_string(text, ""))
        if part.strip()
    ]
    return "；".join(parts[:count])


def _outline_state_dict_text(value: Any) -> str:
    if isinstance(value, Mapping):
        for item in value.values():
            text = _non_empty_string(item, "")
            if text:
                return text
    return ""


def _outline_scene_match_text(scene: Any) -> str:
    pieces: list[str] = []
    purpose = getattr(scene, "purpose", None)
    if isinstance(purpose, Mapping):
        pieces.extend(_non_empty_string(value, "") for value in purpose.values())
    pieces.extend(
        _non_empty_string(getattr(scene, attr, None), "")
        for attr in ("title", "concrete_goal", "protagonist_state", "cut_point")
    )
    return " ".join(piece for piece in pieces if piece)


def _outline_chapter_match_text(chapter: Any) -> str:
    pieces = [
        _non_empty_string(getattr(chapter, attr, None), "")
        for attr in (
            "chapter_goal",
            "main_conflict",
            "hook_description",
            "opening_situation",
            "opening_pressure",
            "tail_hook",
        )
    ]
    return " ".join(piece for piece in pieces if piece)


def _outline_identity_match_candidates(
    identity_manifest: list[dict[str, Any]],
) -> list[tuple[str, tuple[str, ...]]]:
    """Return ``(canonical_name, search_tokens)`` pairs for text matching.

    Tokens shorter than 2 characters are dropped: single-character matches
    against free chapter text are overwhelmingly false positives.
    """

    candidates: list[tuple[str, tuple[str, ...]]] = []
    for identity in identity_manifest:
        if not isinstance(identity, dict):
            continue
        name = _non_empty_string(identity.get("name"), "")
        if not name:
            continue
        tokens = tuple(
            token.strip()
            for token in (name, *_string_list(identity.get("aliases")))
            if len(token.strip()) >= 2
        )
        if tokens:
            candidates.append((name, tokens))
    return candidates


def _tag_enriched_outline_field(chapter: Any, field_name: str) -> None:
    """Append ``field_name`` to a chapter's ``enriched_fields`` side-channel.

    Deterministic backfill (T5, 2026-07-09) must leave a trace of what it
    filled in, so quality audits can tell "字段非空" apart from "字段是模型
    真写的" — ``ChapterOutlineInput`` has ``extra="allow"`` precisely so this
    round-trips through ``model_dump()`` without a dedicated schema field.
    """

    existing = getattr(chapter, "enriched_fields", None)
    tags = list(existing) if isinstance(existing, list) else []
    if field_name not in tags:
        tags.append(field_name)
    chapter.enriched_fields = tags


def _tag_degenerate_outline_fields(batch: Any, findings: list[dict[str, Any]]) -> None:
    """T5 末轮软接受：在退化仍未修复时给受影响章节打 ``degenerate_fields`` 标，
    不只是打日志——日志不可查询，标记随 ``model_dump()`` 落库供审计/分段验收核验。
    """

    by_chapter: dict[Any, list[str]] = {}
    for finding in findings:
        by_chapter.setdefault(finding.get("chapter_number"), []).append(
            f"{finding.get('field_a')}≈{finding.get('field_b')}"
        )
    if not by_chapter:
        return
    for chapter in getattr(batch, "chapters", None) or []:
        pairs = by_chapter.get(getattr(chapter, "chapter_number", None))
        if not pairs:
            continue
        existing = getattr(chapter, "degenerate_fields", None)
        tags = list(existing) if isinstance(existing, list) else []
        for pair in pairs:
            if pair not in tags:
                tags.append(pair)
        chapter.degenerate_fields = tags


def _enrich_generated_volume_outline_systemic_fields(
    batch: Any,
    *,
    identity_manifest: list[dict[str, Any]],
    language: str | None = None,
) -> int:
    """Deterministically backfill systemically-missing outline batch fields.

    Production 500-chapter runs showed three batch-level gaps that downstream
    hard gates treat as fatal only much later: empty ``opening_situation``
    (commercial_planning_readiness ``missing_opening_situation``),
    protagonist-only scene ``participants`` on every scene
    (``golden_three_solo_scene_chain``), and whole batches without
    ``causal_contract`` (chapter_causality_contract at materialization).
    All three are derivable from fields the model did emit, so they are
    repaired here — before batch validation — instead of killing the book
    hundreds of steps downstream.
    """

    is_en = is_english_language(language)
    match_candidates = _outline_identity_match_candidates(identity_manifest)
    repaired = 0

    try:
        from bestseller.services.quality_levers.webnovel_method_cards import (
            chapter_end_hook_keys as _chapter_end_hook_keys,
            match_hook_type_key as _match_hook_type_key,
        )

        _HOOK_KEYS = _chapter_end_hook_keys()
    except Exception:  # method-card config missing → skip hook backfill (soft)
        _HOOK_KEYS = ()

        def _match_hook_type_key(_value: str) -> str | None:
            return None

    for chapter in getattr(batch, "chapters", []) or []:
        chapter_text = _outline_chapter_match_text(chapter)
        scenes = list(getattr(chapter, "scenes", []) or [])
        main_conflict = getattr(chapter, "main_conflict", None)
        chapter_goal = getattr(chapter, "chapter_goal", None)
        hook_description = getattr(chapter, "hook_description", None)

        # ── 1) participants: text-match cast names into under-populated scenes ──
        participants_backfilled = False
        for scene in scenes:
            participants = [
                item
                for item in getattr(scene, "participants", []) or []
                if _non_empty_string(item, "")
            ]
            tokens = {_outline_identity_token(item) for item in participants}
            if len(tokens) < 2 and match_candidates:
                haystack = f"{_outline_scene_match_text(scene)} {chapter_text}"
                for canonical, search_tokens in match_candidates:
                    canonical_token = _outline_identity_token(canonical)
                    if canonical_token in tokens:
                        continue
                    if any(token in haystack for token in search_tokens):
                        participants.append(canonical)
                        tokens.add(canonical_token)
                    if len(tokens) >= 2:
                        break
            participants = participants[:_OUTLINE_SCENE_PARTICIPANT_CAP]
            if participants != list(getattr(scene, "participants", []) or []):
                scene.participants = participants
                repaired += 1
                participants_backfilled = True
        if participants_backfilled:
            _tag_enriched_outline_field(chapter, "participants")

        # ── 2) opening_situation: synthesize in-medias-res opening pressure ──
        if not _non_empty_string(getattr(chapter, "opening_situation", None), ""):
            first_scene = scenes[0] if scenes else None
            first_purpose = getattr(first_scene, "purpose", None) if first_scene is not None else None
            story = (
                _non_empty_string(first_purpose.get("story"), "")
                if isinstance(first_purpose, Mapping)
                else ""
            )
            conflict_clause = _outline_first_clauses(main_conflict, 1)
            goal_text = _non_empty_string(chapter_goal, "")
            # 防止退化(T5)：opening_situation="开场时空处境"，chapter_goal="本章
            # 意图"——语义不同，不该是同一句话。story 缺失时先试首场景的差异化
            # 素材(time_label/entry_state)，只有真的没有差异化素材才退回复制
            # chapter_goal，且必须打标供审计。
            seed = story
            used_goal_as_seed = False
            if not seed:
                time_label = (
                    _non_empty_string(getattr(first_scene, "time_label", None), "")
                    if first_scene is not None else ""
                )
                entry_state_text = (
                    _outline_state_dict_text(getattr(first_scene, "entry_state", None))
                    if first_scene is not None else ""
                )
                alt_seed = _first_non_empty_text(time_label, entry_state_text, default="")
                if alt_seed and (
                    _normalize_outline_field_text(alt_seed)
                    != _normalize_outline_field_text(goal_text)
                ):
                    seed = alt_seed
                elif goal_text:
                    seed = goal_text
                    used_goal_as_seed = True
                else:
                    seed = conflict_clause
            pressure = _first_non_empty_text(conflict_clause, hook_description, default="")
            if seed:
                if is_en:
                    opening = f"In medias res: {seed}"
                    if pressure and pressure != seed:
                        opening += f"; immediate pressure — {pressure}"
                else:
                    opening = f"开章即事中：{seed}"
                    if pressure and pressure != seed:
                        opening += f"；当场压力——{pressure}"
                chapter.opening_situation = opening
                repaired += 1
                _tag_enriched_outline_field(chapter, "opening_situation")
                if used_goal_as_seed:
                    _tag_enriched_outline_field(
                        chapter, "opening_situation_copied_from_goal_no_alt_material"
                    )

        # ── 3) causal_contract: map missing axes from chapter fields ──
        existing = getattr(chapter, "causal_contract", None)
        filled = dict(existing) if isinstance(existing, Mapping) else {}
        last_scene = scenes[-1] if scenes else None
        last_scene_text = (
            _outline_scene_match_text(last_scene) if last_scene is not None else ""
        )
        exit_text = (
            _outline_state_dict_text(getattr(last_scene, "exit_state", None))
            if last_scene is not None
            else ""
        )
        key_reveals = "；".join(_string_list(getattr(chapter, "key_reveals", None)))
        volume_label = getattr(chapter, "volume_number", None) or "?"
        derived = {
            "pressure": _first_non_empty_text(
                getattr(chapter, "opening_pressure", None),
                _outline_first_clauses(main_conflict, 1),
                default="",
            ),
            "resistance": _first_non_empty_text(
                _outline_first_clauses(main_conflict, 2),
                main_conflict,
                default="",
            ),
            "protagonist_desire": _non_empty_string(chapter_goal, ""),
            "protagonist_choice": _non_empty_string(chapter_goal, ""),
            "visible_action_or_reaction": _first_non_empty_text(
                last_scene_text, chapter_goal, default=""
            ),
            "state_change": _first_non_empty_text(exit_text, hook_description, default=""),
            "gain_or_reveal": _first_non_empty_text(key_reveals, hook_description, default=""),
            "cost_or_tradeoff": _outline_first_clauses(main_conflict, 1),
            "chapter_function": _first_non_empty_text(
                getattr(chapter, "chapter_event_role", None),
                default=(
                    f"Advance volume {volume_label} main line"
                    if is_en
                    else f"推进第{volume_label}卷主线"
                ),
            ),
            "next_reader_desire": _first_non_empty_text(
                hook_description,
                getattr(chapter, "tail_hook", None),
                default="",
            ),
        }
        added = 0
        for key, value in derived.items():
            if filled.get(key):
                continue
            text = _non_empty_string(value, "")
            if text:
                filled[key] = text[:_OUTLINE_CONTRACT_VALUE_MAX_CHARS]
                added += 1
        if added:
            chapter.causal_contract = filled
            repaired += 1
            _tag_enriched_outline_field(chapter, "causal_contract")

        # ── 4) hook_type: derive a canonical taxonomy label when the planner
        # left it blank (observed empty on whole batches, e.g. ch1-5 / ch21-30).
        # The chapter already carries a concrete hook_description / tail_hook; we
        # map it to the nearest canonical hook key, falling back to the first
        # canonical key so the writability gate sees a non-empty label.
        if not _non_empty_string(getattr(chapter, "hook_type", None), "") and _HOOK_KEYS:
            hook_source = _first_non_empty_text(
                hook_description, getattr(chapter, "tail_hook", None), default=""
            )
            derived_hook = _match_hook_type_key(hook_source) if hook_source else None
            if not derived_hook:
                derived_hook = _HOOK_KEYS[0]
            chapter.hook_type = derived_hook
            repaired += 1
            _tag_enriched_outline_field(chapter, "hook_type")

    return repaired


def _rescue_golden_three_solo_scenes(
    batch: Any, *, identity_manifest: list[dict[str, Any]]
) -> int:
    """Final guard before the golden-three solo-chain gate (G8).

    MUST run AFTER :func:`_repair_generated_volume_outline_contract_inputs`: that
    identity-lock repair strips ad-hoc (non-roster) participants down to the
    protagonist, so a scene the LLM wrote as a duo can collapse back to solo
    *after* enrichment already judged it fine. A 凡人流 solo-discovery opening
    then trips ``golden_three_solo_scene_chain`` and the whole volume outline is
    hard-killed. For chapters 1-3 only, this promotes a canonical roster name
    (which survives the identity lock) into the highest-tension scene so the
    chapter is no longer a solo chain. Deterministic; never touches ch4+.
    """

    match_candidates = _outline_identity_match_candidates(identity_manifest)
    if not match_candidates:
        return 0
    rescued = 0
    for chapter in getattr(batch, "chapters", []) or []:
        number = getattr(chapter, "chapter_number", None)
        if not (isinstance(number, int) and 1 <= number <= 3):
            continue
        scenes = list(getattr(chapter, "scenes", []) or [])
        if not scenes:
            continue
        token_sets = [
            {
                _outline_identity_token(p)
                for p in getattr(sc, "participants", []) or []
                if _non_empty_string(p, "")
            }
            for sc in scenes
        ]
        if any(len(tokens) >= 2 for tokens in token_sets):
            continue
        present = set().union(*token_sets) if token_sets else set()
        chapter_text = _outline_chapter_match_text(chapter)
        pick = next(
            (
                canonical
                for canonical, search_tokens in match_candidates
                if _outline_identity_token(canonical) not in present
                and any(token in chapter_text for token in search_tokens)
            ),
            None,
        )
        if pick is None:
            pick = next(
                (
                    canonical
                    for canonical, _ in match_candidates
                    if _outline_identity_token(canonical) not in present
                ),
                None,
            )
        if pick is None:
            continue
        idx = max(
            range(len(scenes)),
            key=lambda i: (len(token_sets[i]), len(_outline_scene_match_text(scenes[i]))),
        )
        target = scenes[idx]
        merged = [
            p for p in getattr(target, "participants", []) or [] if _non_empty_string(p, "")
        ]
        merged.append(pick)
        distinct = {_outline_identity_token(m) for m in merged}
        for canonical, _ in match_candidates:
            if len(distinct) >= 2:
                break
            token = _outline_identity_token(canonical)
            if token not in distinct:
                merged.append(canonical)
                distinct.add(token)
        target.participants = merged[:_OUTLINE_SCENE_PARTICIPANT_CAP]
        rescued += 1
    return rescued


def _require_outline_systemic_fields_or_raise(batch: Any, *, logical_name: str) -> None:
    """Hard-require systemically-missing fields at batch validation time.

    Runs AFTER :func:`_enrich_generated_volume_outline_systemic_fields`, so it
    only fires when even the deterministic backfill could not derive a value —
    exactly the cases the downstream hard gates (chapter_causality_contract,
    commercial_planning_readiness golden-three checks) would otherwise kill the
    book on hundreds of steps later. The semicolon-separated message chunks are
    consumed by :func:`_outline_repair_directives_from_error` to build targeted
    regeneration directives.
    """

    missing_opening: list[Any] = []
    missing_causal: list[Any] = []
    golden_solo: list[Any] = []
    for chapter in getattr(batch, "chapters", []) or []:
        number = getattr(chapter, "chapter_number", None)
        if not _non_empty_string(getattr(chapter, "opening_situation", None), ""):
            missing_opening.append(number)
        contract = getattr(chapter, "causal_contract", None)
        if not (isinstance(contract, Mapping) and any(bool(value) for value in contract.values())):
            missing_causal.append(number)
        if isinstance(number, int) and 1 <= number <= 3:
            # Reuse the downstream golden-three solo-chain judgment verbatim
            # (production-acceptance contract: only hard-fail here what the
            # commercial readiness gate would hard-fail later anyway).
            try:
                from bestseller.services.commercial_planning_readiness import (
                    _chapter_is_solo_chain,
                    chapter_plan_probe_from_mapping,
                )

                probe = chapter_plan_probe_from_mapping(
                    chapter.model_dump(mode="json", by_alias=True)
                )
                if _chapter_is_solo_chain(probe):
                    golden_solo.append(number)
            except Exception:
                logger.debug(
                    "Golden-three solo-chain probe failed during outline validation",
                    exc_info=True,
                )

    problems: list[str] = []
    if missing_opening:
        problems.append(
            f"chapters {missing_opening} missing opening_situation — every chapter must open "
            "in medias res with the concrete pressure the protagonist is already facing"
        )
    if missing_causal:
        problems.append(
            f"chapters {missing_causal} missing causal_contract — every chapter must include "
            "the reader-visible causal axes (pressure, protagonist_choice, resistance, "
            "cost_or_tradeoff, gain_or_reveal, state_change, next_reader_desire)"
        )
    if golden_solo:
        problems.append(
            f"golden-three chapters {golden_solo} contain only protagonist-solo scenes — "
            "at least one scene per chapter must list a second named cast participant "
            "(opponent, ally, or pressure source) in participants"
        )
    if problems:
        raise PlannerFallbackError(
            f"Planner artifact '{logical_name}' systemic-field contract failed: "
            + "; ".join(problems)
        )


def _outline_find_chapter_scene(
    batch: Any,
    *,
    chapter_number: Any,
    scene_number: Any,
) -> tuple[Any, Any] | None:
    for chapter in getattr(batch, "chapters", []) or []:
        if getattr(chapter, "chapter_number", None) != chapter_number:
            continue
        for scene in getattr(chapter, "scenes", []) or []:
            if getattr(scene, "scene_number", None) == scene_number:
                return chapter, scene
    return None


def _outline_find_chapter(batch: Any, *, chapter_number: Any) -> Any | None:
    for chapter in getattr(batch, "chapters", []) or []:
        if getattr(chapter, "chapter_number", None) == chapter_number:
            return chapter
    return None


def _repair_generated_volume_outline_contract_blocks(
    batch: Any,
    report: Any,
    *,
    identity_manifest: list[dict[str, Any]],
) -> int:
    """Apply deterministic repairs for known-safe chapter-plan violations."""

    identity_index = _outline_identity_index(identity_manifest)
    protagonist_name = _outline_default_protagonist(identity_manifest)
    repaired = 0

    def _set_participants(scene: Any, values: list[str]) -> None:
        nonlocal repaired
        cleaned: list[str] = []
        seen: set[str] = set()
        for value in values:
            token = _outline_identity_token(value)
            if not token or token in seen:
                continue
            identity: dict[str, Any] | None = identity_index.get(token) if identity_index else None
            if identity_index and identity is None:
                # Try to alias the LLM-hallucinated name onto its likely
                # canonical cast member (e.g. 姜澄 → 苏澄). Only fires when
                # there is *exactly one* cast entry sharing ≥ 50% of the
                # characters and an identical length, so ambiguous cases
                # still fall through to the UNKNOWN gate.
                identity = _outline_fuzzy_resolve_participant(token, identity_index)
                if identity is None:
                    repaired += 1
                    continue
            canonical = _non_empty_string(identity.get("name") if identity else value, value)
            canonical_token = _outline_identity_token(canonical)
            if canonical_token and canonical_token not in seen:
                cleaned.append(canonical)
                seen.add(canonical_token)
        if not cleaned:
            cleaned = [protagonist_name]
        if cleaned != list(getattr(scene, "participants", []) or []):
            scene.participants = cleaned
            repaired += 1

    for violation in getattr(report, "blocking_violations", ()) or ():
        code = getattr(violation, "code", "")
        metadata = getattr(violation, "metadata", {}) or {}
        if code in {
            "PLAN_CHAPTER_GOAL_GENERIC",
            "PLAN_CHAPTER_GOAL_META",
        }:
            chapter = _outline_find_chapter(
                batch,
                chapter_number=metadata.get("chapter_number"),
            )
            if chapter is not None:
                chapter.chapter_goal = _outline_chapter_goal_repair(
                    chapter,
                    protagonist_name=protagonist_name,
                )
                repaired += 1
            continue

        if code in {
            "PLAN_CHAPTER_CONFLICT_GENERIC",
            "PLAN_CHAPTER_CONFLICT_META",
        }:
            chapter = _outline_find_chapter(
                batch,
                chapter_number=metadata.get("chapter_number"),
            )
            if chapter is not None:
                chapter.main_conflict = _outline_chapter_conflict_repair(
                    chapter,
                    protagonist_name=protagonist_name,
                )
                repaired += 1
            continue

        if code in {
            "PLAN_CHAPTER_HOOK_GENERIC",
            "PLAN_CHAPTER_HOOK_META",
        }:
            chapter = _outline_find_chapter(
                batch,
                chapter_number=metadata.get("chapter_number"),
            )
            if chapter is not None:
                chapter.hook_description = _outline_chapter_hook_repair(
                    chapter,
                    protagonist_name=protagonist_name,
                )
                repaired += 1
            continue

        found = _outline_find_chapter_scene(
            batch,
            chapter_number=metadata.get("chapter_number"),
            scene_number=metadata.get("scene_number"),
        )
        if found is None:
            continue
        chapter, scene = found

        if code in {
            "PLAN_SCENE_TIME_MISSING",
            "PLAN_SCENE_TIME_GENERIC",
        }:
            scene.time_label = _outline_scene_time_repair(chapter, scene)
            repaired += 1
            continue

        if code in {
            "PLAN_SCENE_STORY_PURPOSE_MISSING",
            "PLAN_SCENE_STORY_PURPOSE_GENERIC",
            "PLAN_SCENE_STORY_PURPOSE_META",
        }:
            purpose = dict(getattr(scene, "purpose", None) or {})
            purpose["story"] = _outline_scene_story_repair(chapter, scene)
            if not _non_empty_string(purpose.get("emotion"), ""):
                purpose["emotion"] = "压力递进，并把选择、代价或线索推到下一拍。"
            scene.purpose = purpose
            repaired += 1
            continue

        if code in {
            "PLAN_SCENE_PARTICIPANTS_MISSING",
            "PLAN_SCENE_UNKNOWN_PARTICIPANT",
        }:
            _set_participants(
                scene,
                [
                    item
                    for item in getattr(scene, "participants", []) or []
                    if _non_empty_string(item, "")
                ],
            )
            continue

        if code == "PLAN_SCENE_PURPOSE_CHARACTER_NOT_IN_PARTICIPANTS":
            character = _non_empty_string(metadata.get("character"), "")
            participants = [
                item
                for item in getattr(scene, "participants", []) or []
                if _non_empty_string(item, "")
            ]
            if character:
                participants.append(character)
                _set_participants(scene, participants)
                repaired += 1
            continue

    return repaired


def _selected_story_effect_skill_keys(
    project: ProjectModel,
    *,
    stage: str,
) -> tuple[str, ...]:
    metadata = project.metadata_json if isinstance(project.metadata_json, dict) else {}
    selection = _mapping(metadata.get(STORY_EFFECT_SKILL_SELECTION_METADATA_KEY))
    stage_selection = _mapping(selection.get(stage))
    keys: list[str] = []
    for field_name in ("primary", "secondary"):
        skill_key = _non_empty_string(stage_selection.get(field_name), "")
        if skill_key and skill_key not in keys:
            keys.append(skill_key)
    return tuple(keys)


def _brainhole_required_contract_fields(project: ProjectModel) -> tuple[str, ...]:
    metadata = project.metadata_json if isinstance(project.metadata_json, dict) else {}
    profile = _mapping(metadata.get(BRAINHOLE_PROFILE_METADATA_KEY))
    fields = _string_list(profile.get("required_contract_fields"))
    if fields:
        return tuple(fields)
    return (
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


def _story_effect_skill_key(value: Any) -> str:
    key = _non_empty_string(value, "")
    if key:
        return key
    value_map = _mapping(value)
    for field_name in ("name", "skill_key", "key", "id", "engine"):
        key = _non_empty_string(value_map.get(field_name), "")
        if key:
            return key
    return ""


def _missing_brainhole_contract_fields(
    brainhole_contract: Mapping[str, Any],
    required_contract_fields: Iterable[str],
) -> list[str]:
    return [
        field_name
        for field_name in required_contract_fields
        if not _non_empty_string(brainhole_contract.get(field_name), "")
    ]


def _normalize_brainhole_effect_selection(
    chapter: Any,
    selected_effects: Mapping[str, Any],
    *,
    brainhole_contract: Mapping[str, Any],
    required_contract_fields: Iterable[str],
) -> dict[str, Any]:
    if not brainhole_contract:
        return dict(selected_effects)
    if _missing_brainhole_contract_fields(brainhole_contract, required_contract_fields):
        return dict(selected_effects)

    normalized = dict(selected_effects)
    primary_raw = normalized.get("primary")
    secondary_raw = normalized.get("secondary")
    primary_key = _story_effect_skill_key(primary_raw)
    secondary_key = _story_effect_skill_key(secondary_raw)
    if primary_key == "brainhole_engine":
        if not isinstance(primary_raw, str):
            primary_map = _mapping(primary_raw)
            normalized["primary"] = "brainhole_engine"
            if "reason" not in normalized and _non_empty_string(primary_map.get("reason"), ""):
                normalized["reason"] = primary_map["reason"]
        setattr(chapter, "selected_effect_skills", normalized)
        return normalized

    if secondary_key == "brainhole_engine":
        normalized["primary"] = "brainhole_engine"
        normalized["secondary"] = primary_key or secondary_key
        secondary_map = _mapping(secondary_raw)
        if "reason" not in normalized and _non_empty_string(secondary_map.get("reason"), ""):
            normalized["reason"] = secondary_map["reason"]
        setattr(chapter, "selected_effect_skills", normalized)
        return normalized

    if primary_key or secondary_key:
        normalized["_normalized_from_selected_effect_skills"] = {
            "primary": primary_key or primary_raw,
            "secondary": secondary_key or secondary_raw,
            "reason": (
                "project-selected brainhole_engine restored because this chapter "
                "already contains a complete brainhole_contract"
            ),
        }
    normalized["primary"] = "brainhole_engine"
    normalized.setdefault(
        "reason",
        "Project-selected brainhole_engine restored from complete brainhole_contract.",
    )
    normalized.setdefault("expected_contracts", ["brainhole_contract"])
    setattr(chapter, "selected_effect_skills", normalized)
    return normalized


def _require_selected_story_effect_contracts_or_raise(
    batch: Any,
    *,
    project: ProjectModel,
    logical_name: str,
) -> None:
    selected_keys = _selected_story_effect_skill_keys(project, stage="chapter_outline")
    if not selected_keys:
        return

    required_effect_keys = (
        ("brainhole_engine",)
        if "brainhole_engine" in selected_keys
        else selected_keys[:1]
    )
    required_contract_fields = _brainhole_required_contract_fields(project)
    findings: list[str] = []
    for chapter in getattr(batch, "chapters", []) or []:
        chapter_number = getattr(chapter, "chapter_number", "?")
        selected_effects = _mapping(getattr(chapter, "selected_effect_skills", None))
        brainhole_contract = _mapping(getattr(chapter, "brainhole_contract", None))
        if "brainhole_engine" in selected_keys:
            selected_effects = _normalize_brainhole_effect_selection(
                chapter,
                selected_effects,
                brainhole_contract=brainhole_contract,
                required_contract_fields=required_contract_fields,
            )
        effect_values = {
            _story_effect_skill_key(selected_effects.get(field_name))
            for field_name in ("primary", "secondary")
        }
        effect_values.discard("")
        missing_effect_keys = [
            skill_key for skill_key in required_effect_keys if skill_key not in effect_values
        ]
        if not selected_effects:
            findings.append(
                f"STORY_EFFECT_CONTRACT_MISSING ch{chapter_number}: "
                "missing selected_effect_skills"
            )
        elif missing_effect_keys:
            findings.append(
                f"STORY_EFFECT_SELECTION_MISMATCH ch{chapter_number}: "
                f"selected_effect_skills must include {', '.join(missing_effect_keys)}"
            )

        if "brainhole_engine" not in selected_keys:
            continue
        if not brainhole_contract:
            findings.append(
                f"BRAINHOLE_CONTRACT_MISSING ch{chapter_number}: "
                "missing brainhole_contract required by selected brainhole_engine"
            )
            continue
        missing_fields = _missing_brainhole_contract_fields(
            brainhole_contract,
            required_contract_fields,
        )
        if missing_fields:
            findings.append(
                f"BRAINHOLE_CONTRACT_INCOMPLETE ch{chapter_number}: "
                f"brainhole_contract missing fields {', '.join(missing_fields)}"
            )

    if findings:
        raise PlannerFallbackError(
            f"Planner artifact '{logical_name}' failed selected story-effect contract: "
            + "; ".join(findings[:20])
        )


_OUTLINE_FIELD_DEGENERACY_PAIRS: tuple[tuple[str, str], ...] = (
    ("chapter_goal", "opening_situation"),
    ("chapter_goal", "main_conflict"),
    ("opening_situation", "main_conflict"),
)
_OUTLINE_DEGENERACY_CONTAINMENT_RATIO = 0.8
_OUTLINE_DEGENERACY_JACCARD_THRESHOLD = 0.85
_OUTLINE_FIELD_PUNCT_RE = re.compile(
    r"[\s，,。.！!？?；;：:、\"'“”‘’「」『』（）()\-—…]+"
)


def _normalize_outline_field_text(text: str) -> str:
    return _OUTLINE_FIELD_PUNCT_RE.sub("", text or "")


def _detect_degenerate_outline_fields(batch: Any) -> list[dict[str, Any]]:
    """Detect chapters where goal/opening_situation/main_conflict have
    collapsed into near-duplicates of each other.

    Production 500-chapter runs showed the model can satisfy every
    existence-only gate downstream (``missing_opening_situation``,
    ``chapter_causality_contract``) by writing the SAME sentence into all
    three fields — they carry distinct semantics (goal=intent,
    opening_situation=scene+pressure, main_conflict=obstacle+stakes) but
    nothing checks that they actually differ. Real case (tracked-
    rulehorror-v1 ch1): all three fields were byte-identical.
    """

    findings: list[dict[str, Any]] = []
    for chapter in getattr(batch, "chapters", None) or []:
        chapter_number = getattr(chapter, "chapter_number", None)
        for field_a, field_b in _OUTLINE_FIELD_DEGENERACY_PAIRS:
            text_a = _non_empty_string(getattr(chapter, field_a, None), "")
            text_b = _non_empty_string(getattr(chapter, field_b, None), "")
            if not text_a or not text_b:
                continue
            norm_a = _normalize_outline_field_text(text_a)
            norm_b = _normalize_outline_field_text(text_b)
            if not norm_a or not norm_b:
                continue
            similarity = 0.0
            degenerate = False
            if norm_a == norm_b:
                similarity, degenerate = 1.0, True
            else:
                shorter, longer = (
                    (norm_a, norm_b) if len(norm_a) <= len(norm_b) else (norm_b, norm_a)
                )
                if (
                    shorter in longer
                    and len(shorter) / len(longer) > _OUTLINE_DEGENERACY_CONTAINMENT_RATIO
                ):
                    similarity, degenerate = round(len(shorter) / len(longer), 2), True
                else:
                    jac = jaccard_similarity(norm_a, norm_b)
                    if jac >= _OUTLINE_DEGENERACY_JACCARD_THRESHOLD:
                        similarity, degenerate = round(jac, 2), True
            if degenerate:
                findings.append(
                    {
                        "chapter_number": chapter_number,
                        "field_a": field_a,
                        "field_b": field_b,
                        "text_a": text_a[:120],
                        "text_b": text_b[:120],
                        "similarity": similarity,
                    }
                )
    return findings


def _validate_generated_volume_outline_or_raise(
    payload: Any,
    *,
    project: ProjectModel,
    logical_name: str,
    volume_number: int,
    expected_count: int,
    chapter_number_offset: int,
    cast_spec: dict[str, Any],
    existing_titles: list[tuple[int | None, str]] | None = None,
    strict_story_effects: bool = True,
) -> dict[str, Any]:
    """Normalize and validate a generated volume outline before persistence.

    ``existing_titles`` is forwarded to
    :func:`_normalize_generated_outline_titles_or_fail` so cross-volume
    title duplicates become a hard validation error and the repair loop
    can re-prompt the planner with a targeted directive.
    """

    from bestseller.domain.workflow import ChapterOutlineBatchInput
    from bestseller.services.narrative_contracts import validate_chapter_plan_contract

    if isinstance(payload, list):
        payload = {
            "batch_name": f"volume-{volume_number}-outline",
            "chapters": payload,
        }
    if not isinstance(payload, dict):
        raise PlannerFallbackError(
            f"Planner artifact '{logical_name}' returned {type(payload).__name__}, expected object."
        )
    vol_chapters = _mapping_list(payload.get("chapters"))
    _require_complete_volume_outline(
        logical_name=logical_name,
        volume_number=volume_number,
        expected_count=expected_count,
        chapters=vol_chapters,
    )
    for idx, chapter in enumerate(vol_chapters):
        chapter["volume_number"] = volume_number
        chapter["chapter_number"] = chapter_number_offset + idx
    _normalize_generated_outline_titles_or_fail(
        vol_chapters,
        logical_name=logical_name,
        existing_titles=existing_titles,
    )

    batch = ChapterOutlineBatchInput.model_validate(
        {
            "batch_name": payload.get("batch_name") or f"volume-{volume_number}-outline",
            "chapters": vol_chapters,
        }
    )
    if strict_story_effects:
        _require_selected_story_effect_contracts_or_raise(
            batch,
            project=project,
            logical_name=logical_name,
        )
    else:
        # Final repair attempt: a selected story-effect contract (e.g. a missing
        # structured brainhole_contract the model never emits as a field) must not
        # abort the whole book — soft-accept. The effect content is still pushed by
        # the prompt contract and audited softly by the story-enhancer gate (#28).
        try:
            _require_selected_story_effect_contracts_or_raise(
                batch, project=project, logical_name=logical_name
            )
        except PlannerFallbackError as exc:
            logger.warning(
                "Soft-accepting %s with unmet story-effect contract after final "
                "attempt: %s",
                logical_name,
                str(exc)[:600],
            )

    # ── 章纲字段退化查重(T5, 2026-07-09) ────────────────────────────────
    # 真机案例(tracked-rulehorror-v1 ch1)：chapter_goal/opening_situation/
    # main_conflict 三字段字面相同，下游 existence-only 闸门("非空即过")照样
    # 全通过。在 enrichment 之前检测——enrichment 只填空字段，不该让它把已经
    # 退化的模型输出洗白成"看起来正常"；退化必须回炉重写，而不是被复制掩盖。
    _degeneracy_findings = _detect_degenerate_outline_fields(batch)
    if _degeneracy_findings:
        _degeneracy_summary = "; ".join(
            f"ch{f['chapter_number']} {f['field_a']}≈{f['field_b']}(sim={f['similarity']})"
            for f in _degeneracy_findings[:20]
        )
        if strict_story_effects:
            raise OutlineFieldDegeneracyError(
                f"Planner artifact '{logical_name}' has degenerate outline fields "
                "(chapter_goal/opening_situation/main_conflict collapsed into "
                f"near-duplicates): {_degeneracy_summary}",
                findings=_degeneracy_findings,
            )
        logger.warning(
            "Soft-accepting %s with %d degenerate outline field pair(s) after "
            "final attempt: %s",
            logical_name,
            len(_degeneracy_findings),
            _degeneracy_summary,
        )
        _tag_degenerate_outline_fields(batch, _degeneracy_findings)

    identity_manifest = _chapter_outline_identity_manifest(cast_spec)
    _cast_protagonist = _mapping(cast_spec.get("protagonist")) if isinstance(cast_spec, dict) else {}
    # Systemic enrichment runs BEFORE the identity-lock repair: a scene whose
    # ad-hoc participants get stripped down to the protagonist there was not
    # planned as a solo scene, so text-match backfill must only target scenes
    # the model itself left under-populated.
    enrich_count = _enrich_generated_volume_outline_systemic_fields(
        batch,
        identity_manifest=identity_manifest,
        language=_planner_language(project),
    )
    if enrich_count:
        logger.info(
            "Backfilled %d systemic outline field(s) (opening_situation/participants/"
            "causal_contract) before validating %s for project '%s'.",
            enrich_count,
            logical_name,
            project.slug,
        )
    repair_count = _repair_generated_volume_outline_contract_inputs(
        batch,
        identity_manifest=identity_manifest,
        protagonist_flaw_default=_first_non_empty_text(
            _cast_protagonist.get("flaw"),
            _cast_protagonist.get("fatal_flaw"),
            _cast_protagonist.get("core_wound"),
            default="",
        )
        or None,
    )
    if repair_count:
        logger.info(
            "Repaired %d deterministic scene field(s) before validating %s for project '%s'.",
            repair_count,
            logical_name,
            project.slug,
        )
    report = validate_chapter_plan_contract(
        batch,
        identity_manifest=identity_manifest,
        require_identity_registry=bool(identity_manifest),
    )
    report_repair_count = _repair_generated_volume_outline_contract_blocks(
        batch,
        report,
        identity_manifest=identity_manifest,
    )
    if report_repair_count:
        logger.info(
            "Repaired %d report-driven scene field(s) before accepting %s for project '%s'.",
            report_repair_count,
            logical_name,
            project.slug,
        )
        report = validate_chapter_plan_contract(
            batch,
            identity_manifest=identity_manifest,
            require_identity_registry=bool(identity_manifest),
        )
    if report.blocks:
        raise PlannerFallbackError(
            report.error_message(
                project_slug=project.slug,
                artifact=f"{logical_name}/chapter_plan_contract",
            )
        )
    # Final golden-three solo-rescue, AFTER the identity-lock repair has stripped
    # any ad-hoc participants — so a 凡人流 solo opening can no longer collapse
    # back to solo and hard-kill the whole volume outline (G8).
    golden_rescued = _rescue_golden_three_solo_scenes(
        batch, identity_manifest=identity_manifest
    )
    if golden_rescued:
        logger.info(
            "Rescued %d golden-three solo chapter(s) with a named participant before "
            "validating %s for project '%s'.",
            golden_rescued,
            logical_name,
            project.slug,
        )
    _require_outline_systemic_fields_or_raise(batch, logical_name=logical_name)
    return batch.model_dump(mode="json", by_alias=True)


def _outline_repair_directives_from_error(
    error: Exception,
    *,
    language: str | None,
    volume_number: int | None = None,
    chapter_number_offset: int | None = None,
    expected_count: int | None = None,
) -> list[str]:
    """Convert a failed outline attempt into concrete replanning directives.

    Special-cases :class:`TitleCollisionError`: instead of asking the LLM
    to rewrite the whole volume, we issue per-chapter directives naming
    the specific colliding title and the conflicting prior chapter so
    the planner can fix only what's broken.
    """

    is_en = is_english_language(language)

    # Retry runs historically fixed the named problem but dropped fields the
    # previous attempt already got right (causal_contract vanishing on retry
    # batches was a systemic production failure). Every repair prompt therefore
    # carries an explicit keep-what-passed directive.
    preserve_directive = (
        (
            "Carry over every field that already passed in the previous version: "
            "keep opening_situation, causal_contract, scene participants, "
            "methodology_contract, selected_effect_skills, brainhole_contract, "
            "and all scene cards intact for everything not named above. Never drop "
            "or blank a previously-valid field while repairing."
        )
        if is_en
        else (
            "修复时必须保留上一版已通过的全部字段：未被点名的章节及其 opening_situation、"
            "causal_contract、scene participants、methodology_contract、selected_effect_skills、"
            "brainhole_contract、场景卡必须原样保留，不得因为修复其他问题而丢弃或留空。"
        )
    )

    # ── Consumed-event duplication branch (R5) ──────────────────────
    # Surfaced from the cross-batch ledger check when a fresh batch
    # re-narrates an arc an earlier batch already consumed. Issue one
    # targeted directive per offending chapter.
    if isinstance(error, StoryEnhancerCoverageError) and error.directives:
        return [*error.directives, preserve_directive]
    if isinstance(error, OutlineEventDuplicationError) and error.findings:
        directives = []
        for finding in error.findings[:20]:
            chapter_number = finding.get("chapter_number")
            ledger_chapter = finding.get("ledger_chapter_number")
            ledger_goal = str(finding.get("ledger_goal") or "")[:120]
            similarity = float(finding.get("similarity") or 0.0)
            if is_en:
                directives.append(
                    f"Chapter {chapter_number}: its goal is a near-rewrite "
                    f"(similarity {similarity:.2f}) of the already-consumed event from "
                    f"chapter {ledger_chapter}: \"{ledger_goal}\". That event already "
                    f"happened and is spent. Rewrite ONLY chapter {chapter_number} with a "
                    "NEW concrete event that advances the main plot — different action, "
                    "different obstacle, different state change. Do not produce a variant "
                    "of the consumed event."
                )
            else:
                directives.append(
                    f"第{chapter_number}章的 chapter_goal 与已消耗事件清单中"
                    f"第{ledger_chapter}章的事件高度重复（相似度 {similarity:.2f}）："
                    f"「{ledger_goal}」。该事件已经发生并消耗完毕。"
                    f"只需重写第{chapter_number}章——必须是推进主线的全新具体事件，"
                    "行动、阻力、状态变化都要与清单中的事件不同，禁止生成其变体。"
                )
        if is_en:
            directives.append(
                "Keep all other chapters unchanged. Only rewrite the chapters named "
                "above; do not regenerate the whole batch."
            )
        else:
            directives.append("其他章节保持不变。仅按上述指令重写指定章节，不要重写整批。")
        directives.append(preserve_directive)
        return directives

    # ── Outline field degeneracy branch (T5, 2026-07-09) ─────────────
    # Surfaced when chapter_goal/opening_situation/main_conflict collapsed
    # into near-duplicates of each other. Each field has a distinct job;
    # the directive spells that out per chapter instead of asking for a
    # blind full rewrite (which tends to just re-collapse the same way).
    if isinstance(error, OutlineFieldDegeneracyError) and error.findings:
        directives = []
        field_semantics_en = {
            "chapter_goal": "the protagonist's INTENT this chapter (what they're "
            "trying to accomplish) — not a scene description",
            "opening_situation": "the OPENING scene/time/place and the immediate "
            "pressure the protagonist walks into — not a restatement of intent",
            "main_conflict": "WHO or WHAT is blocking the protagonist and WHY — "
            "the obstacle, not the goal or the opening scene",
        }
        field_semantics_zh = {
            "chapter_goal": "主角本章想达成什么（意图），不是处境描述",
            "opening_situation": "开场时空处境+当场压力，不是意图的重复",
            "main_conflict": "谁/什么在挡路、为什么挡，不是目标或开场处境的重复",
        }
        for finding in error.findings[:20]:
            chapter_number = finding.get("chapter_number")
            field_a = str(finding.get("field_a") or "")
            field_b = str(finding.get("field_b") or "")
            similarity = float(finding.get("similarity") or 0.0)
            if is_en:
                directives.append(
                    f"Chapter {chapter_number}: '{field_a}' and '{field_b}' are "
                    f"near-identical (similarity {similarity:.2f}) — they collapsed "
                    "into the same sentence. Rewrite BOTH so each carries its own "
                    f"distinct meaning: {field_a} = {field_semantics_en.get(field_a, field_a)}; "
                    f"{field_b} = {field_semantics_en.get(field_b, field_b)}."
                )
            else:
                directives.append(
                    f"第{chapter_number}章：「{field_a}」与「{field_b}」几乎相同"
                    f"（相似度 {similarity:.2f}），退化成了同一句话。请分别重写，"
                    f"让两个字段各自承担不同语义：{field_a} = "
                    f"{field_semantics_zh.get(field_a, field_a)}；{field_b} = "
                    f"{field_semantics_zh.get(field_b, field_b)}。"
                )
        if is_en:
            directives.append(
                "Keep all other chapters and fields unchanged. Only rewrite the "
                "named fields on the named chapters."
            )
        else:
            directives.append("其他章节和字段保持不变。仅重写上述指定章节的指定字段。")
        directives.append(preserve_directive)
        return directives

    # ── Title-collision branch ──────────────────────────────────────
    # Surfaced from `_normalize_generated_outline_titles_or_fail` when
    # the new dedup pass catches exact or near-duplicate titles.
    # Producing focused per-chapter directives is much cheaper than
    # asking the LLM to redo the entire volume.
    if isinstance(error, TitleCollisionError) and error.collisions:
        directives: list[str] = []
        for col in error.collisions[:20]:  # cap directives to keep prompt size bounded
            if is_en:
                conflict_loc = (
                    f"chapter {col.conflict_chapter_number}"
                    if col.conflict_chapter_number is not None
                    else "an earlier chapter"
                )
                sim_note = (
                    "identical to"
                    if col.similarity >= 1.0
                    else f"near-duplicate of (Jaccard {col.similarity:.2f})"
                )
                directives.append(
                    f"Chapter {col.chapter_number}: the proposed title "
                    f"'{col.candidate_title}' is {sim_note} '{col.conflict_title}' "
                    f"used in {conflict_loc}. Rewrite ONLY chapter "
                    f"{col.chapter_number}'s title — derive a unique 2-6-word "
                    "phrase from THIS chapter's own main_conflict/hook_description/scenes "
                    "(named object, named person, specific place, specific action result). "
                    "Do not reuse any title from any earlier chapter, do not produce a "
                    "'noun + function-suffix' template, and do not share 2 or more "
                    "characters with the conflicting title."
                )
            else:
                conflict_loc = (
                    f"第{col.conflict_chapter_number}章"
                    if col.conflict_chapter_number is not None
                    else "前面某章"
                )
                sim_note = (
                    "完全相同"
                    if col.similarity >= 1.0
                    else f"高度近似（Jaccard {col.similarity:.2f}）"
                )
                directives.append(
                    f"第{col.chapter_number}章的标题「{col.candidate_title}」与"
                    f"{conflict_loc}的「{col.conflict_title}」{sim_note}。"
                    f"只需重写第{col.chapter_number}章的 title —— 必须从本章的 "
                    "main_conflict/hook_description/scenes 中提取独有元素"
                    "（命名器物、人名、地点、独有动作结果）；2-6 字；"
                    "不得复用任何前章标题，不得使用「名词+功能尾词」模板，"
                    "不得与冲突标题共享 ≥2 个字。"
                )
        # Brief overall reminder at the end.
        if is_en:
            directives.append(
                "Keep all other chapters unchanged. Only rewrite the chapter "
                "titles named above; do not regenerate the whole volume."
            )
        else:
            directives.append("其他章节保持不变。仅按上述指令重写指定章节的 title，不要重写整卷。")
        directives.append(preserve_directive)
        return directives

    message = str(error).strip()
    if not message:
        message = type(error).__name__
    count_mismatch = re.search(r"returned\s+(\d+)\s*/\s*(\d+)\s+chapters", message, re.IGNORECASE)
    chunks = [chunk.strip() for chunk in message.split(";") if chunk.strip()]
    directives: list[str] = []
    if count_mismatch:
        actual_raw, expected_raw = count_mismatch.groups()
        actual_count = int(actual_raw)
        expected_from_error = int(expected_raw)
        target_count = (
            int(expected_count)
            if isinstance(expected_count, int) and expected_count > 0
            else expected_from_error
        )
        range_clause_en = ""
        range_clause_zh = ""
        if (
            isinstance(chapter_number_offset, int)
            and chapter_number_offset > 0
            and target_count > 0
        ):
            range_start = chapter_number_offset
            range_end = chapter_number_offset + target_count - 1
            next_chapter = range_end + 1
            range_clause_en = (
                f" Global chapter_number values must stay within {range_start}-{range_end}; "
                f"do not extend into chapter {next_chapter} or later."
            )
            range_clause_zh = (
                f"全局章节号必须限定在第{range_start}-{range_end}章，"
                f"不得延伸到第{next_chapter}章及以后；"
            )
        volume_clause_en = (
            f"Volume {volume_number} only; "
            if isinstance(volume_number, int) and volume_number > 0
            else ""
        )
        volume_clause_zh = (
            f"只允许规划第{volume_number}卷；"
            if isinstance(volume_number, int) and volume_number > 0
            else ""
        )
        if actual_count > target_count:
            if is_en:
                directives.append(
                    f"The previous outline over-generated {actual_count}/{target_count} chapters. "
                    f"Regenerate the whole volume from scratch. {volume_clause_en}"
                    f"The chapters array must contain exactly {target_count} items;{range_clause_en} "
                    "Do not carry future-volume material into this volume, summarize, merge, omit, pad, or split chapters."
                )
            else:
                directives.append(
                    f"上一版多生成了 {actual_count}/{target_count} 章。本次必须从头重写整卷，"
                    f"{volume_clause_zh}chapters 数组必须恰好包含 {target_count} 项；"
                    f"{range_clause_zh}不得把后续卷内容提前，不得概括、合并、遗漏、补白或拆卷。"
                )
        elif actual_count < target_count:
            if is_en:
                directives.append(
                    f"The previous outline emitted only {actual_count}/{target_count} chapters. "
                    f"Regenerate the whole volume from scratch. {volume_clause_en}"
                    f"The chapters array must contain exactly {target_count} items;{range_clause_en} "
                    "Do not summarize, merge, omit, pad, or split chapters."
                )
            else:
                directives.append(
                    f"上一版只生成了 {actual_count}/{target_count} 章。本次必须从头重写整卷，"
                    f"{volume_clause_zh}chapters 数组必须恰好包含 {target_count} 项；"
                    f"{range_clause_zh}不得概括、合并、遗漏、补白或拆卷。"
                )
        else:
            if is_en:
                directives.append(
                    f"The previous outline had an invalid chapter-count contract. "
                    f"Regenerate the whole volume from scratch. {volume_clause_en}"
                    f"The chapters array must contain exactly {target_count} items;{range_clause_en} "
                    "Do not summarize, merge, omit, pad, or split chapters."
                )
            else:
                directives.append(
                    "上一版章数合同异常。本次必须从头重写整卷，"
                    f"{volume_clause_zh}chapters 数组必须恰好包含 {target_count} 项；"
                    f"{range_clause_zh}不得概括、合并、遗漏、补白或拆卷。"
                )
    if (
        "STORY_EFFECT_CONTRACT_MISSING" in message
        or "STORY_EFFECT_SELECTION_MISMATCH" in message
        or "BRAINHOLE_CONTRACT_MISSING" in message
        or "BRAINHOLE_CONTRACT_INCOMPLETE" in message
    ):
        if is_en:
            directives.append(
                "Repair selected story-effect contracts: every chapter in this batch "
                "must include selected_effect_skills containing the project-selected "
                "brainhole_engine, and a complete brainhole_contract with one_sentence_sell, "
                "character_core_used, modern_system, contrast_mechanism, visible_comedy, "
                "serious_underbelly, plot_consequence, protagonist_decision, "
                "growth_stage_fit, and risk_check."
            )
        else:
            directives.append(
                "修复故事效果合同：本批每章必须输出 selected_effect_skills，且必须包含项目选定的 "
                "brainhole_engine；每章还必须输出完整 brainhole_contract，字段包含 "
                "one_sentence_sell、character_core_used、modern_system、contrast_mechanism、"
                "visible_comedy、serious_underbelly、plot_consequence、protagonist_decision、"
                "growth_stage_fit、risk_check。"
            )
    for chunk in chunks[:10]:
        if is_en:
            directives.append(
                "Repair the previous outline failure: "
                f"{chunk}. Rewrite the affected chapters as reader-visible events with "
                "specific action, obstacle, state change, cost, and next pressure."
            )
        else:
            directives.append(
                "修复上一版章纲失败项："
                f"{chunk}。相关章节必须重写成读者可见的具体事件，写清行动、阻力、状态变化、代价和下一步压力。"
            )
    if not directives:
        directives.append(
            "Regenerate the affected volume outline from scratch with concrete events."
            if is_en
            else "从头重写受影响卷章纲，必须全部落到具体事件。"
        )
    directives.append(preserve_directive)
    return directives


async def _generate_volume_outline_with_repair_loop(
    session: AsyncSession,
    settings: AppSettings,
    *,
    project: ProjectModel,
    workflow_run_id: UUID,
    logical_name: str,
    book_spec: dict[str, Any],
    cast_spec: dict[str, Any],
    volume_plan: list[dict[str, Any]],
    volume_entry: dict[str, Any],
    fallback_payload: dict[str, Any],
    volume_number: int,
    expected_count: int,
    chapter_number_offset: int,
    revealed_ledger_block: str | None,
    base_constraints: list[str],
    progress: PlanningProgressCallback | None = None,
    consumed_event_entries: list[dict[str, Any]] | None = None,
    event_dedup_threshold: float = OUTLINE_EVENT_DEDUP_THRESHOLD_DEFAULT,
) -> tuple[dict[str, Any], UUID | None, list[dict[str, Any]]]:
    """Generate a volume outline, then regenerate with diagnostics until valid.

    When ``consumed_event_entries`` is provided (R5 cross-batch ledger),
    each accepted payload is additionally checked for chapters whose goal
    re-narrates an already-consumed event; offenders trigger the same
    repair-directive retry path as validation failures. The check is
    deliberately soft on the final attempt — a residual near-duplicate is
    flagged in ``_meta`` rather than killing the whole book.
    """

    max_repair_attempts = max(
        1,
        int(getattr(settings.pipeline, "chapter_outline_repair_attempts", 3)),
    )
    repair_constraints = list(base_constraints)
    repair_history: list[dict[str, Any]] = []
    last_error: Exception | None = None
    last_llm_run_id: UUID | None = None

    # Cross-volume title dedup: fetch every title already persisted for
    # this project, EXCLUDING the volume we are about to (re)generate.
    # That set is passed to the prompt and to the validator so the
    # planner can neither suggest a duplicate nor smuggle one through.
    existing_titles = await _fetch_existing_chapter_titles(
        session,
        project.id,
        exclude_volume_number=volume_number,
    )

    for attempt in range(1, max_repair_attempts + 1):
        attempt_finish_reason: str | None = None
        _emit_planner_progress(
            progress,
            "planning_outline_attempt_started",
            project=project,
            workflow_run_id=workflow_run_id,
            current_step=f"{logical_name}_attempt_{attempt}",
            logical_name=logical_name,
            volume_number=volume_number,
            attempt=attempt,
            max_attempts=max_repair_attempts,
            expected_chapters=expected_count,
            repair_directives_count=max(0, len(repair_constraints) - len(base_constraints)),
        )
        vol_outline_system, vol_outline_user = _volume_outline_prompts(
            project,
            book_spec,
            cast_spec,
            volume_plan,
            volume_entry,
            revealed_ledger_block=revealed_ledger_block,
            extra_constraints=repair_constraints or None,
            existing_titles=existing_titles,
        )
        vol_outline_user = attach_planner_methodology(
            vol_outline_user,
            stage=MethodologyStage.OUTLINE_VOLUME,
            project_ctx=project,
            token_budget=600,
        )
        try:
            raw_payload, llm_run_id = await _generate_structured_artifact(
                session,
                settings,
                project=project,
                logical_name=logical_name,
                system_prompt=vol_outline_system,
                user_prompt=vol_outline_user,
                fallback_payload=fallback_payload,
                workflow_run_id=workflow_run_id,
                abort_on_fallback=True,
                merge_fallback=False,
            )
            last_llm_run_id = llm_run_id
            raw_meta = (
                _mapping(raw_payload.get("_meta")) if isinstance(raw_payload, dict) else {}
            )
            attempt_finish_reason = _non_empty_string(raw_meta.get("finish_reason"), "")
            payload = _validate_generated_volume_outline_or_raise(
                raw_payload,
                project=project,
                logical_name=logical_name,
                volume_number=volume_number,
                expected_count=expected_count,
                chapter_number_offset=chapter_number_offset,
                cast_spec=cast_spec,
                existing_titles=existing_titles,
                strict_story_effects=attempt < max_repair_attempts,
            )
            if raw_meta:
                payload["_meta"] = raw_meta
            if consumed_event_entries:
                duplicate_findings = _outline_duplicate_event_findings(
                    _mapping_list(payload.get("chapters")),
                    consumed_event_entries,
                    threshold=event_dedup_threshold,
                )
                if duplicate_findings:
                    summary = "; ".join(
                        (
                            f"ch{f['chapter_number']} goal duplicates consumed event "
                            f"ch{f['ledger_chapter_number']} "
                            f"(similarity {f['similarity']:.2f})"
                        )
                        for f in duplicate_findings[:10]
                    )
                    if attempt >= max_repair_attempts:
                        # Soft-accept: a residual near-duplicate event must not
                        # abort the book. Surface it operationally via the WARN
                        # log below and the repair_history entry; the _meta key
                        # carries the structured findings for any consumer.
                        logger.warning(
                            "Volume %d outline batch '%s': accepting with %d "
                            "unresolved consumed-event duplicate(s) after %d "
                            "attempt(s): %s",
                            volume_number,
                            logical_name,
                            len(duplicate_findings),
                            attempt,
                            summary,
                        )
                        meta = _mapping(payload.get("_meta"))
                        meta["event_dedup_unresolved"] = duplicate_findings
                        payload["_meta"] = meta
                        repair_history.append(
                            {
                                "attempt": attempt,
                                "status": "passed_with_event_duplicates",
                                "event_duplicates": duplicate_findings[:10],
                            }
                        )
                    else:
                        raise OutlineEventDuplicationError(
                            f"Planner artifact '{logical_name}' re-narrated "
                            f"already-consumed events: {summary}",
                            findings=duplicate_findings,
                        )
            # Story-enhancer coverage gate (#28): if the book checked enhancer
            # effects at creation, every batch must actually cash them. Systemic
            # absence (the zhaoshen-hr-v13 bland-outline failure) triggers repair;
            # soft-accept on the final attempt so a quality gap never aborts.
            _enhancer_selection = resolve_story_enhancers(
                project.metadata_json if isinstance(project.metadata_json, dict) else {}
            )
            _enhancer_gaps = audit_story_enhancer_coverage(
                _mapping_list(payload.get("chapters")), _enhancer_selection
            )
            if _enhancer_gaps:
                _enhancer_directives = story_enhancer_repair_directives(
                    _mapping_list(payload.get("chapters")), _enhancer_selection
                )
                _gap_summary = ", ".join(
                    f"{g['effect']}={int(g['coverage'] * 100)}%" for g in _enhancer_gaps
                )
                if attempt >= max_repair_attempts:
                    logger.warning(
                        "Volume %d outline batch '%s': accepting with %d "
                        "under-delivered story enhancer(s) after %d attempt(s): %s",
                        volume_number,
                        logical_name,
                        len(_enhancer_gaps),
                        attempt,
                        _gap_summary,
                    )
                    meta = _mapping(payload.get("_meta"))
                    meta["story_enhancer_gaps"] = _enhancer_gaps
                    payload["_meta"] = meta
                    repair_history.append(
                        {
                            "attempt": attempt,
                            "status": "passed_with_enhancer_gaps",
                            "enhancer_gaps": _enhancer_gaps,
                        }
                    )
                else:
                    raise StoryEnhancerCoverageError(
                        f"Planner artifact '{logical_name}' left selected story "
                        f"enhancers uncashed: {_gap_summary}",
                        directives=_enhancer_directives,
                    )
            if repair_history and repair_history[-1].get("status") not in (
                "passed",
                "passed_with_enhancer_gaps",
                "passed_with_event_duplicates",
            ):
                repair_history.append({"attempt": attempt, "status": "passed"})
            _emit_planner_progress(
                progress,
                "planning_outline_attempt_completed",
                project=project,
                workflow_run_id=workflow_run_id,
                current_step=f"{logical_name}_attempt_{attempt}",
                logical_name=logical_name,
                volume_number=volume_number,
                attempt=attempt,
                max_attempts=max_repair_attempts,
                expected_chapters=expected_count,
                generated_chapters=len(payload.get("chapters", [])),
                llm_run_id=str(llm_run_id) if llm_run_id is not None else None,
            )
            return payload, llm_run_id, repair_history
        except Exception as exc:
            last_error = exc
            if (
                is_strict_prewrite_project(project)
                and expected_count > strict_outline_shrink_size(project, settings)
                and (
                    attempt_finish_reason == "length"
                    or _is_outline_count_contract_error(exc)
                )
            ):
                raise OutlineBatchShrinkRequired(
                    f"Planner artifact '{logical_name}' requires smaller outline batches "
                    f"after attempt {attempt}: finish_reason={attempt_finish_reason or 'unknown'}, "
                    f"error={exc}"
                ) from exc
            directives = _outline_repair_directives_from_error(
                exc,
                language=project.language,
                volume_number=volume_number,
                chapter_number_offset=chapter_number_offset,
                expected_count=expected_count,
            )
            repair_history.append(
                {
                    "attempt": attempt,
                    "status": "failed",
                    "error": str(exc)[:2000],
                    "next_directives": directives[:5],
                }
            )
            _emit_planner_progress(
                progress,
                "planning_outline_attempt_failed",
                project=project,
                workflow_run_id=workflow_run_id,
                current_step=f"{logical_name}_attempt_{attempt}",
                logical_name=logical_name,
                volume_number=volume_number,
                attempt=attempt,
                max_attempts=max_repair_attempts,
                expected_chapters=expected_count,
                error=str(exc)[:1200],
                next_directives=directives[:5],
            )
            if attempt >= max_repair_attempts:
                break
            logger.warning(
                "Volume %d outline attempt %d/%d failed validation; regenerating with %d repair directives.",
                volume_number,
                attempt,
                max_repair_attempts,
                len(directives),
            )
            repair_constraints = [*base_constraints, *directives]

    raise PlannerFallbackError(
        f"Planner artifact '{logical_name}' failed chapter-outline repair loop after "
        f"{max_repair_attempts} attempt(s). Last error: {last_error}. "
        f"Last LLM run id: {last_llm_run_id}"
    )


def _chapter_outline_batch_size(settings: AppSettings, project: ProjectModel) -> int:
    strict_size = strict_outline_batch_size(project, settings)
    if strict_size is not None:
        return min(strict_size, max(1, int(getattr(project, "target_chapters", 0) or strict_size)))
    configured = int(getattr(settings.pipeline, "chapter_outline_batch_size", 10) or 10)
    target_chapters = int(getattr(project, "target_chapters", 0) or 0)
    if target_chapters <= 12:
        return max(2, min(configured, 3))
    if target_chapters > 120:
        return max(8, min(configured, 10))
    return max(8, min(configured, 12))


def _chapter_outline_batch_ranges(
    *,
    chapter_number_offset: int,
    expected_count: int,
    batch_size: int,
) -> list[tuple[int, int, int]]:
    """Return ``(chapter_start, chapter_end, count)`` ranges for outline batches."""

    if expected_count <= 0:
        return []
    size = max(1, batch_size)
    ranges: list[tuple[int, int, int]] = []
    next_start = max(1, chapter_number_offset)
    remaining = expected_count
    while remaining > 0:
        count = min(size, remaining)
        chapter_end = next_start + count - 1
        ranges.append((next_start, chapter_end, count))
        next_start = chapter_end + 1
        remaining -= count
    return ranges


def _volume_entry_for_outline_batch(
    volume_entry: dict[str, Any],
    *,
    chapter_start: int,
    chapter_end: int,
    count: int,
) -> dict[str, Any]:
    batch_entry = copy.deepcopy(_mapping(volume_entry))
    batch_entry["chapter_count_target"] = count
    batch_entry["chapter_range"] = [chapter_start, chapter_end]
    batch_entry["outline_batch_range"] = [chapter_start, chapter_end]
    return batch_entry


def _fallback_payload_for_outline_batch(
    fallback_payload: dict[str, Any],
    *,
    volume_number: int,
    chapter_start: int,
    chapter_end: int,
) -> dict[str, Any]:
    chapters = []
    for chapter in _mapping_list(_mapping(fallback_payload).get("chapters")):
        chapter_number = _int_or_none(chapter.get("chapter_number"))
        if chapter_number is None:
            continue
        if chapter_start <= chapter_number <= chapter_end:
            chapters.append(copy.deepcopy(chapter))
    return {
        "batch_name": f"volume-{volume_number}-outline-{chapter_start}-{chapter_end}",
        "chapters": chapters,
    }


def _outline_batch_constraints(
    project: ProjectModel,
    *,
    volume_number: int,
    chapter_start: int,
    chapter_end: int,
    count: int,
    previous_exit_state: str | None,
    consumed_event_ledger: list[str] | None = None,
) -> list[str]:
    language = _planner_language(project)
    is_en = is_english_language(language)
    if is_en:
        constraints = [
            (
                f"Generate ONLY volume {volume_number} chapters {chapter_start}-{chapter_end}: "
                f"exactly {count} chapter objects. Do not generate earlier or later chapters."
            ),
            (
                "Treat this as a resumable outline batch. Preserve the book/volume goal, but keep "
                "the JSON output limited to this batch's chapter cards."
            ),
        ]
        if previous_exit_state:
            constraints.append(
                "Continue from the previous batch exit state; the first chapter must visibly react "
                f"to this state: {previous_exit_state[:800]}"
            )
        if consumed_event_ledger:
            constraints.append(
                _outline_event_ledger_constraint(consumed_event_ledger, language=language)
            )
        return constraints

    constraints = [
        (
            f"只生成第{volume_number}卷第{chapter_start}-{chapter_end}章："
            f"chapters 数组必须恰好 {count} 个章节对象，不得生成更早或更晚章节。"
        ),
        "这是可断点续跑的章纲批次；保留全书/本卷目标，但 JSON 输出只能包含本批章节卡。",
    ]
    if previous_exit_state:
        constraints.append(
            f"必须承接上一批 exit state；本批第一章要读者可见地回应该状态：{previous_exit_state[:800]}"
        )
    if consumed_event_ledger:
        constraints.append(
            _outline_event_ledger_constraint(consumed_event_ledger, language=language)
        )
    return constraints


def _chapter_exit_state_summary(chapter: dict[str, Any]) -> str | None:
    for key in (
        "exit_state",
        "state_change",
        "hook_description",
        "next_reader_desire",
        "main_conflict",
    ):
        value = chapter.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    causal = _mapping(chapter.get("causal_contract"))
    for key in ("state_change", "next_reader_desire", "gain_or_reveal", "cost_or_tradeoff"):
        value = causal.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


# ── R5: cross-batch consumed-event ledger ──────────────────────────────
# Progressive outline batches historically had no shared event state, so
# the same plot arc was regenerated by multiple batches (production
# evidence: one signing-scene arc rewritten ~6 times across 50 chapters;
# cross-batch title collisions were the symptom, not the disease). The
# ledger below is extracted deterministically (no LLM) from every accepted
# batch, injected into the next batch's prompt, and enforced at acceptance
# time with character n-gram similarity against each new chapter's goal.

_OUTLINE_EVENT_LEDGER_GOAL_CHARS = 90
_OUTLINE_EVENT_LEDGER_HOOK_CHARS = 60
_OUTLINE_EVENT_LEDGER_MAX_PROMPT_LINES = 80


def _outline_chapter_goal_text(chapter: dict[str, Any]) -> str:
    return _first_non_empty_text(
        chapter.get("chapter_goal"),
        chapter.get("goal"),
        chapter.get("main_conflict"),
    ).strip()


def _outline_chapter_hook_text(chapter: dict[str, Any]) -> str:
    return _first_non_empty_text(
        chapter.get("hook_description"),
        chapter.get("hook"),
    ).strip()


def _outline_consumed_event_entries(
    chapters: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Deterministically compress accepted chapters into ledger entries.

    Each entry is ``{"chapter_number", "goal", "hook", "line"}`` where
    ``line`` is the compact prompt-facing form ``ch<N> | goal | hook``.
    Chapters without any goal/hook text yield no entry.
    """

    entries: list[dict[str, Any]] = []
    for chapter in chapters:
        if not isinstance(chapter, dict):
            continue
        goal = _outline_chapter_goal_text(chapter)[:_OUTLINE_EVENT_LEDGER_GOAL_CHARS]
        hook = _outline_chapter_hook_text(chapter)[:_OUTLINE_EVENT_LEDGER_HOOK_CHARS]
        if not goal and not hook:
            continue
        chapter_number = _int_or_none(chapter.get("chapter_number"))
        parts = [f"ch{chapter_number if chapter_number is not None else '?'}"]
        if goal:
            parts.append(goal)
        if hook:
            parts.append(hook)
        entries.append(
            {
                "chapter_number": chapter_number,
                "goal": goal,
                "hook": hook,
                "line": " | ".join(parts),
            }
        )
    return entries


def _outline_event_ledger_lines(entries: list[dict[str, Any]]) -> list[str]:
    lines = [str(entry.get("line") or "") for entry in entries if entry.get("line")]
    # Keep prompt size bounded: most recent lines matter most for the
    # next batch, but earlier arcs are exactly what re-bloom — keep both
    # ends when over budget (drop the middle).
    cap = _OUTLINE_EVENT_LEDGER_MAX_PROMPT_LINES
    if len(lines) <= cap:
        return lines
    head = cap // 2
    tail = cap - head
    return [*lines[:head], *lines[-tail:]]


def _outline_duplicate_event_findings(
    chapters: list[dict[str, Any]],
    consumed_event_entries: list[dict[str, Any]],
    *,
    threshold: float = OUTLINE_EVENT_DEDUP_THRESHOLD_DEFAULT,
) -> list[dict[str, Any]]:
    """Deterministic acceptance check: flag new chapters whose goal is a
    near-rewrite of an already-consumed ledger event.

    Similarity is character 2-gram Jaccard (same primitive the title
    dedup uses), computed goal-vs-goal. One finding per chapter (the
    highest-similarity ledger match).
    """

    if threshold <= 0 or not consumed_event_entries:
        return []
    findings: list[dict[str, Any]] = []
    for chapter in chapters:
        if not isinstance(chapter, dict):
            continue
        goal = _outline_chapter_goal_text(chapter)[:_OUTLINE_EVENT_LEDGER_GOAL_CHARS]
        if not goal:
            continue
        best: dict[str, Any] | None = None
        for entry in consumed_event_entries:
            ledger_goal = str(entry.get("goal") or "")
            if not ledger_goal:
                continue
            similarity = jaccard_similarity(goal, ledger_goal)
            if similarity >= threshold and (best is None or similarity > best["similarity"]):
                best = {
                    "chapter_number": _int_or_none(chapter.get("chapter_number")),
                    "goal": goal,
                    "ledger_chapter_number": entry.get("chapter_number"),
                    "ledger_goal": ledger_goal,
                    "similarity": round(similarity, 4),
                }
        if best is not None:
            findings.append(best)
    return findings


def _outline_event_ledger_constraint(
    ledger_lines: list[str],
    *,
    language: str | None,
) -> str:
    if is_english_language(language):
        return (
            "CONSUMED-EVENT LEDGER — the events below already happened in "
            "earlier batches and are spent. Do NOT re-narrate them or write "
            "variants of them (same characters doing the same kind of action, "
            "or the same conflict re-occurring, counts as a variant). Every "
            "chapter in this batch must advance the story with NEW events:\n"
            + "\n".join(ledger_lines)
        )
    return (
        "【已消耗事件台账】以下事件已在前序批次发生并消耗完毕，"
        "禁止重写、复述或生成其变体（同一人物做同一类动作、同一冲突再次发生均算变体）。"
        "本批每一章都必须推进全新事件：\n" + "\n".join(ledger_lines)
    )


def _outline_event_dedup_threshold(settings: AppSettings) -> float:
    try:
        value = float(
            getattr(
                getattr(settings, "pipeline", None),
                "outline_event_dedup_threshold",
                OUTLINE_EVENT_DEDUP_THRESHOLD_DEFAULT,
            )
        )
    except (TypeError, ValueError):
        return OUTLINE_EVENT_DEDUP_THRESHOLD_DEFAULT
    if not (0.0 <= value <= 1.0):
        return OUTLINE_EVENT_DEDUP_THRESHOLD_DEFAULT
    return value


# ── R16: clipped-fragment guard for deterministic title dedupe ─────────
# The deterministic dedupe fallback historically emitted sentence
# fragments clipped from goal text as titles (production evidence:
# 「霍听澜把一份」「试图让陈屿的」). Every check below is at the grammar
# layer (particles, copulas, common auxiliary verbs) — NO genre words.

_TITLE_FRAGMENT_LEADING_CHARS: frozenset[str] = frozenset(
    "的了在是和与或但因所而从这那他她你我它之其也都已被把让叫将对向于就还又当若想要去来再没不"
)
_TITLE_FRAGMENT_LEADING_BIGRAMS: tuple[str, ...] = (
    "试图", "打算", "开始", "继续", "决定", "想要", "准备", "必须", "已经", "正在",
)
_TITLE_FRAGMENT_TRAILING_CHARS: frozenset[str] = frozenset(
    "的了着把被让将和与或对向从而之于在是就还也又个为"
)
_TITLE_FRAGMENT_INTERIOR_CHARS: frozenset[str] = frozenset("的了把被让将是")

_TITLE_BRACKET_NOUN_RE = re.compile(r"[「『《【]([^」』》】]{2,4})[」』》】]")
_TITLE_BOUNDED_CJK_RUN_RE = re.compile(r"(?:^|[^一-鿿])([一-鿿]{2,4})(?=[^一-鿿]|$)")
_TITLE_CJK_CHAR_RE = re.compile(r"[一-鿿]")
_TITLE_NUMERAL_SUFFIXES = "二三四五六七八九十"


def _is_clipped_title_fragment(title: str) -> bool:
    """True when ``title`` reads as a clipped sentence fragment rather
    than a noun phrase — i.e. it starts with a particle/auxiliary verb,
    ends dangling on a particle, or carries sentence-structure characters
    (的/了/把/被/让/将/是) in its interior."""

    s = (title or "").strip()
    if not s:
        return True
    if not _TITLE_CJK_CHAR_RE.search(s):
        # Latin titles are out of scope for this CJK grammar check.
        return False
    if s[0] in _TITLE_FRAGMENT_LEADING_CHARS:
        return True
    if any(s.startswith(bigram) for bigram in _TITLE_FRAGMENT_LEADING_BIGRAMS):
        return True
    if s[-1] in _TITLE_FRAGMENT_TRAILING_CHARS:
        return True
    if any(ch in _TITLE_FRAGMENT_INTERIOR_CHARS for ch in s):
        return True
    return False


def _dedup_title_candidate_ok(candidate: str, used_titles: list[str]) -> bool:
    if not candidate or _is_clipped_title_fragment(candidate):
        return False
    for used in used_titles:
        if candidate == used:
            return False
        if jaccard_similarity(candidate, used) >= DEFAULT_NEAR_DUP_THRESHOLD:
            return False
    return True


def _extract_concrete_title_phrase(
    chapter: dict[str, Any],
    used_titles: list[str],
) -> str | None:
    """Extract a 2-4 character noun phrase from THIS chapter's hook/goal.

    Bracketed proper nouns win; otherwise boundary-delimited CJK runs
    (longest first). Candidates that read as clipped fragments or collide
    with ``used_titles`` (exact or near-dup) are skipped."""

    sources = (
        _outline_chapter_hook_text(chapter),
        _outline_chapter_goal_text(chapter),
    )
    for text in sources:
        if not text:
            continue
        for match in _TITLE_BRACKET_NOUN_RE.finditer(text):
            candidate = match.group(1).strip()
            if _dedup_title_candidate_ok(candidate, used_titles):
                return candidate
        runs = [match.group(1) for match in _TITLE_BOUNDED_CJK_RUN_RE.finditer(text)]
        for desired_len in (4, 3, 2):
            for candidate in runs:
                if len(candidate) != desired_len:
                    continue
                if _dedup_title_candidate_ok(candidate, used_titles):
                    return candidate
    return None


def _numbered_title_fallback(original_title: str, used_titles: list[str]) -> str:
    """「原标题·二」-style numbered suffix; never a clipped goal fragment.

    The base is shortened from the right until the suffixed candidate
    clears both exact and near-dup checks against ``used_titles``, so the
    result always survives the downstream Jaccard title validation."""

    base = (original_title or "").strip() or "本章"
    while len(base) >= 2:
        for numeral in _TITLE_NUMERAL_SUFFIXES:
            candidate = f"{base}·{numeral}"
            if all(
                candidate != used
                and jaccard_similarity(candidate, used) < DEFAULT_NEAR_DUP_THRESHOLD
                for used in used_titles
            ):
                return candidate
        base = base[:-1]
    # Pathological corner: ascending integer suffix is unique by construction.
    index = 11
    while True:
        candidate = f"{base or '本章'}·{index}"
        if candidate not in used_titles:
            return candidate
        index += 1


def _repair_clipped_dedup_titles(
    chapters: list[dict[str, Any]],
    title_changes: list[tuple[int, str, str]],
    *,
    existing_titles: list[tuple[int | None, str]],
    language: str | None = None,
) -> tuple[list[dict[str, Any]], list[tuple[int, str, str]]]:
    """Post-process deterministic dedupe rewrites (R16).

    Any rewritten title that reads as a clipped sentence fragment is
    replaced by (in order of preference) a concrete 2-4 character noun
    phrase from the chapter's own hook/goal, else a numbered
    「原标题·二」 suffix. Non-fragment rewrites pass through untouched.
    Returns new lists; inputs are not mutated."""

    if not title_changes:
        return chapters, title_changes

    fragment_changes = {
        chapter_number: (old_title, new_title)
        for chapter_number, old_title, new_title in title_changes
        if _is_clipped_title_fragment(new_title)
    }
    if not fragment_changes:
        return chapters, title_changes

    used_titles: list[str] = [
        title for _, title in existing_titles if (title or "").strip()
    ]
    used_titles.extend(
        str(chapter.get("title") or "").strip()
        for chapter in chapters
        if isinstance(chapter, dict)
        and _int_or_none(chapter.get("chapter_number")) not in fragment_changes
        and str(chapter.get("title") or "").strip()
    )

    repaired_chapters: list[dict[str, Any]] = []
    replacement_by_chapter: dict[int, str] = {}
    for chapter in chapters:
        chapter_number = (
            _int_or_none(chapter.get("chapter_number")) if isinstance(chapter, dict) else None
        )
        if chapter_number is None or chapter_number not in fragment_changes:
            repaired_chapters.append(chapter)
            continue
        old_title, _fragment = fragment_changes[chapter_number]
        replacement = _extract_concrete_title_phrase(chapter, used_titles)
        if replacement is None:
            replacement = _numbered_title_fallback(old_title, used_titles)
        used_titles.append(replacement)
        replacement_by_chapter[chapter_number] = replacement
        repaired_chapters.append({**chapter, "title": replacement})

    repaired_changes = [
        (
            chapter_number,
            old_title,
            replacement_by_chapter.get(chapter_number, new_title),
        )
        for chapter_number, old_title, new_title in title_changes
    ]
    return repaired_chapters, repaired_changes


async def _generate_volume_outline_batched(
    session: AsyncSession,
    settings: AppSettings,
    *,
    project: ProjectModel,
    workflow_run_id: UUID,
    logical_name: str,
    book_spec: dict[str, Any],
    cast_spec: dict[str, Any],
    volume_plan: list[dict[str, Any]],
    volume_entry: dict[str, Any],
    fallback_payload: dict[str, Any],
    volume_number: int,
    expected_count: int,
    chapter_number_offset: int,
    revealed_ledger_block: str | None,
    base_constraints: list[str],
    progress: PlanningProgressCallback | None = None,
) -> tuple[dict[str, Any], list[UUID], list[dict[str, Any]]]:
    batch_size = _chapter_outline_batch_size(settings, project)
    batch_ranges = _chapter_outline_batch_ranges(
        chapter_number_offset=chapter_number_offset,
        expected_count=expected_count,
        batch_size=batch_size,
    )
    if len(batch_ranges) <= 1 and not is_strict_prewrite_project(project):
        payload, llm_run_id, repair_history = await _generate_volume_outline_with_repair_loop(
            session,
            settings,
            project=project,
            workflow_run_id=workflow_run_id,
            logical_name=logical_name,
            book_spec=book_spec,
            cast_spec=cast_spec,
            volume_plan=volume_plan,
            volume_entry=volume_entry,
            fallback_payload=fallback_payload,
            volume_number=volume_number,
            expected_count=expected_count,
            chapter_number_offset=chapter_number_offset,
            revealed_ledger_block=revealed_ledger_block,
            base_constraints=base_constraints,
            progress=progress,
        )
        return payload, [llm_run_id] if llm_run_id is not None else [], repair_history

    all_chapters: list[dict[str, Any]] = []
    llm_run_ids: list[UUID] = []
    repair_history: list[dict[str, Any]] = []
    previous_exit_state: str | None = None
    reused_batch_count = 0
    # R5: consumed-event ledger accumulated across batches so later batches
    # cannot re-narrate arcs earlier batches already spent.
    consumed_event_entries: list[dict[str, Any]] = []
    event_dedup_threshold = _outline_event_dedup_threshold(settings)

    for chapter_start, chapter_end, count in batch_ranges:
        batch_logical_name = f"{logical_name}_batch_{chapter_start}_{chapter_end}"
        batch_constraints = [
            *base_constraints,
            *_outline_batch_constraints(
                project,
                volume_number=volume_number,
                chapter_start=chapter_start,
                chapter_end=chapter_end,
                count=count,
                previous_exit_state=previous_exit_state,
                consumed_event_ledger=_outline_event_ledger_lines(consumed_event_entries),
            ),
        ]
        try:
            generated_batches = [
                await _generate_volume_outline_with_repair_loop(
                    session,
                    settings,
                    project=project,
                    workflow_run_id=workflow_run_id,
                    logical_name=batch_logical_name,
                    book_spec=book_spec,
                    cast_spec=cast_spec,
                    volume_plan=volume_plan,
                    volume_entry=_volume_entry_for_outline_batch(
                        volume_entry,
                        chapter_start=chapter_start,
                        chapter_end=chapter_end,
                        count=count,
                    ),
                    fallback_payload=_fallback_payload_for_outline_batch(
                        fallback_payload,
                        volume_number=volume_number,
                        chapter_start=chapter_start,
                        chapter_end=chapter_end,
                    ),
                    volume_number=volume_number,
                    expected_count=count,
                    chapter_number_offset=chapter_start,
                    revealed_ledger_block=revealed_ledger_block,
                    base_constraints=batch_constraints,
                    progress=progress,
                    consumed_event_entries=consumed_event_entries,
                    event_dedup_threshold=event_dedup_threshold,
                )
            ]
        except OutlineBatchShrinkRequired as exc:
            shrink_size = strict_outline_shrink_size(project, settings)
            shrink_ranges = _chapter_outline_batch_ranges(
                chapter_number_offset=chapter_start,
                expected_count=count,
                batch_size=shrink_size,
            )
            repair_history.append(
                {
                    "batch": [chapter_start, chapter_end],
                    "logical_name": batch_logical_name,
                    "status": "shrunk",
                    "reason": str(exc)[:1200],
                    "shrink_size": shrink_size,
                    "shrink_ranges": [[start, end] for start, end, _count in shrink_ranges],
                }
            )
            generated_batches = []
            for sub_start, sub_end, sub_count in shrink_ranges:
                sub_constraints = [
                    *base_constraints,
                    *_outline_batch_constraints(
                        project,
                        volume_number=volume_number,
                        chapter_start=sub_start,
                        chapter_end=sub_end,
                        count=sub_count,
                        previous_exit_state=previous_exit_state,
                        consumed_event_ledger=_outline_event_ledger_lines(
                            consumed_event_entries
                        ),
                    ),
                    "本批次是上一轮长输出/章数失败后的缩小批次；只输出当前缩小范围，不得补写其他章节。",
                ]
                generated_batches.append(
                    await _generate_volume_outline_with_repair_loop(
                        session,
                        settings,
                        project=project,
                        workflow_run_id=workflow_run_id,
                        logical_name=f"{logical_name}_batch_{sub_start}_{sub_end}",
                        book_spec=book_spec,
                        cast_spec=cast_spec,
                        volume_plan=volume_plan,
                        volume_entry=_volume_entry_for_outline_batch(
                            volume_entry,
                            chapter_start=sub_start,
                            chapter_end=sub_end,
                            count=sub_count,
                        ),
                        fallback_payload=_fallback_payload_for_outline_batch(
                            fallback_payload,
                            volume_number=volume_number,
                            chapter_start=sub_start,
                            chapter_end=sub_end,
                        ),
                        volume_number=volume_number,
                        expected_count=sub_count,
                        chapter_number_offset=sub_start,
                        revealed_ledger_block=revealed_ledger_block,
                        base_constraints=sub_constraints,
                        progress=progress,
                        consumed_event_entries=consumed_event_entries,
                        event_dedup_threshold=event_dedup_threshold,
                    )
                )

        for batch_payload, llm_run_id, batch_repair_history in generated_batches:
            if llm_run_id is not None:
                llm_run_ids.append(llm_run_id)
            batch_meta = _mapping(_mapping(batch_payload).get("_meta"))
            if batch_meta.get("reused"):
                reused_batch_count += 1
            await import_planning_artifact(
                session,
                project.slug,
                PlanningArtifactCreate(
                    artifact_type=ArtifactType.VOLUME_CHAPTER_OUTLINE,
                    content=batch_payload,
                    source_run_id=llm_run_id,
                ),
            )
            if batch_repair_history:
                repair_history.append(
                    {
                        "batch": [
                            _mapping_list(batch_payload.get("chapters"))[0].get("chapter_number")
                            if _mapping_list(batch_payload.get("chapters"))
                            else chapter_start,
                            _mapping_list(batch_payload.get("chapters"))[-1].get("chapter_number")
                            if _mapping_list(batch_payload.get("chapters"))
                            else chapter_end,
                        ],
                        "logical_name": _mapping(batch_meta).get("source_step") or batch_logical_name,
                        "attempts": batch_repair_history,
                    }
                )
            batch_chapters = _mapping_list(batch_payload.get("chapters"))
            all_chapters.extend(copy.deepcopy(batch_chapters))
            if batch_chapters:
                previous_exit_state = _chapter_exit_state_summary(batch_chapters[-1])
                # R5: register this batch's events as consumed so the next
                # batch is prompted with — and checked against — them.
                consumed_event_entries.extend(
                    _outline_consumed_event_entries(batch_chapters)
                )

    combined = {
        "batch_name": f"volume-{volume_number}-outline",
        "chapters": all_chapters,
        "_meta": {
            "batch_size": batch_size,
            "batch_count": len(batch_ranges),
            "batch_ranges": [[start, end] for start, end, _count in batch_ranges],
            "reused_batch_count": reused_batch_count,
            "reused": reused_batch_count == len(batch_ranges),
        },
    }
    existing_titles = await _fetch_existing_chapter_titles(
        session,
        project.id,
        exclude_volume_number=volume_number,
    )
    # Deterministic last-resort title dedupe. The per-batch LLM repair loop
    # cannot see other batches' titles, so a cross-batch duplicate (e.g. ch1 and
    # ch6 both "后视镜") only surfaces here, where the validation below would
    # otherwise hard-raise TitleCollisionError and kill the whole book. A
    # duplicate chapter title is cosmetic — rewrite it deterministically (prefer
    # a content-derived phrase, fall back to a conventional 「（二）」numeral)
    # rather than aborting.
    from bestseller.services.title_dedup import make_titles_unique

    _deduped_chapters, _title_changes = make_titles_unique(
        _mapping_list(combined.get("chapters")),
        existing_titles=existing_titles,
        language=getattr(project, "language", None),
    )
    if _title_changes:
        # R16: never let the deterministic dedupe ship a clipped goal
        # fragment (「霍听澜把一份」-style) as a chapter title — prefer a
        # 2-4 char noun phrase from the chapter's own hook/goal, else a
        # numbered 「原标题·二」 suffix.
        _deduped_chapters, _title_changes = _repair_clipped_dedup_titles(
            _deduped_chapters,
            _title_changes,
            existing_titles=existing_titles,
            language=getattr(project, "language", None),
        )
        combined["chapters"] = _deduped_chapters
        logger.warning(
            "Volume %d outline: deterministically deduped %d colliding chapter "
            "title(s) the LLM repair did not converge on: %s",
            volume_number,
            len(_title_changes),
            "; ".join(
                f"ch{cn} '{old}'->'{new}'" for cn, old, new in _title_changes
            ),
        )
    validated = _validate_generated_volume_outline_or_raise(
        combined,
        project=project,
        logical_name=logical_name,
        volume_number=volume_number,
        expected_count=expected_count,
        chapter_number_offset=chapter_number_offset,
        cast_spec=cast_spec,
        existing_titles=existing_titles,
    )
    validated["_meta"] = combined["_meta"]
    return validated, llm_run_ids, repair_history


_SIGNING_PLATFORM_KEYWORDS: dict[str, tuple[str, ...]] = {
    "qimao": ("七猫", "qimao"),
    "qidian": ("起点", "qidian", "阅文"),
    "tomato": ("番茄", "tomato", "fanqie"),
}


def _text_mentions_platform(value: Any, keywords: tuple[str, ...]) -> bool:
    if not isinstance(value, str):
        return False
    text = value.strip().lower()
    return any(keyword.lower() in text for keyword in keywords)


def _text_mentions_qimao(value: Any) -> bool:
    return _text_mentions_platform(value, _SIGNING_PLATFORM_KEYWORDS["qimao"])


def _project_platform_candidates(project: ProjectModel) -> list[Any]:
    metadata = _mapping(project.metadata_json)
    profile = _mapping(metadata.get("writing_profile"))
    market = _mapping(profile.get("market"))
    serialization = _mapping(profile.get("serialization"))
    return [
        metadata.get("platform_target"),
        metadata.get("target_platform"),
        metadata.get("platform"),
        metadata.get("content_mode"),
        market.get("platform_target"),
        market.get("target_platform"),
        market.get("content_mode"),
        market.get("reader_promise"),
        serialization.get("opening_mandate"),
        project.audience,
    ]


def project_targets_signing_platform(project: ProjectModel) -> str | None:
    """Return the matched signing-platform key (qimao / qidian / tomato), or None."""
    candidates = _project_platform_candidates(project)
    for platform_key, keywords in _SIGNING_PLATFORM_KEYWORDS.items():
        if any(_text_mentions_platform(item, keywords) for item in candidates):
            return platform_key
    return None


def project_targets_qimao(project: ProjectModel) -> bool:
    return project_targets_signing_platform(project) == "qimao"


def project_uses_signing_quality_gate(project: ProjectModel) -> bool:
    metadata = _mapping(project.metadata_json)
    if metadata.get("opening_quality_gate_disabled") is True:
        return False
    return bool(
        metadata.get("opening_quality_contract")
        or metadata.get("qimao_opening_contract")
        or project_targets_signing_platform(project)
    )


def _project_platform_label(project: ProjectModel, market: dict[str, Any]) -> str:
    metadata = _mapping(project.metadata_json)
    return _first_non_empty_text(
        market.get("platform_target"),
        market.get("target_platform"),
        metadata.get("platform_target"),
        metadata.get("target_platform"),
        metadata.get("platform"),
        default="商业网文签约口径",
    )


def _int_or_none(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _chapter_count_from_range(value: Any) -> int | None:
    if not isinstance(value, (list, tuple)) or len(value) < 2:
        return None
    start = _int_or_none(value[0])
    end = _int_or_none(value[1])
    if start is None or end is None or start <= 0 or end < start:
        return None
    return end - start + 1


def _chapter_bounds_from_range(value: Any) -> tuple[int, int] | None:
    if not isinstance(value, (list, tuple)) or len(value) < 2:
        return None
    start = _int_or_none(value[0])
    end = _int_or_none(value[1])
    if start is None or end is None or start <= 0 or end < start:
        return None
    return start, end


def _derive_volume_chapter_bounds(entry: dict[str, Any]) -> tuple[int, int] | None:
    direct = _chapter_bounds_from_range(entry.get("chapter_range"))
    if direct is not None:
        return direct

    arc_ranges = entry.get("arc_ranges")
    if not isinstance(arc_ranges, list):
        return None
    bounds: list[tuple[int, int]] = []
    for arc_range in arc_ranges:
        parsed = _chapter_bounds_from_range(arc_range)
        if parsed is None:
            return None
        bounds.append(parsed)
    if not bounds:
        return None
    return bounds[0][0], bounds[-1][1]


def _derive_volume_chapter_count(entry: dict[str, Any]) -> dict[str, Any]:
    existing = _int_or_none(entry.get("chapter_count_target"))
    if existing is not None and existing > 0:
        return entry

    derived = _chapter_count_from_range(entry.get("chapter_range"))
    if derived is None:
        arc_ranges = entry.get("arc_ranges")
        if isinstance(arc_ranges, list):
            derived = 0
            for arc_range in arc_ranges:
                count = _chapter_count_from_range(arc_range)
                if count is None:
                    derived = 0
                    break
                derived += count
            if derived <= 0:
                derived = None

    if derived is None:
        return entry
    return {**entry, "chapter_count_target": derived}


def _normalize_volume_plan_payload(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        volumes = _mapping_list(value.get("volumes"))
    else:
        volumes = _mapping_list(value)
    return [_derive_volume_chapter_count(volume) for volume in volumes]


def build_qimao_opening_contract(
    project: ProjectModel,
    *,
    premise: str,
    book_spec: dict[str, Any],
    cast_spec: dict[str, Any],
    volume_plan: Any,
) -> dict[str, Any]:
    is_en = is_english_language(getattr(project, "language", None))
    metadata = _mapping(project.metadata_json)
    profile = _mapping(metadata.get("writing_profile"))
    market = _mapping(profile.get("market"))
    series_engine = _mapping(_mapping(book_spec).get("series_engine"))
    book_protagonist = _mapping(_mapping(book_spec).get("protagonist"))
    cast_protagonist = _mapping(_mapping(cast_spec).get("protagonist"))
    stakes = _mapping(_mapping(book_spec).get("stakes"))
    volumes = _normalize_volume_plan_payload(volume_plan)
    first_volume = volumes[0] if volumes else {}
    opening_state = _mapping(first_volume.get("opening_state"))
    volume_resolution = _mapping(first_volume.get("volume_resolution"))

    protagonist_name = _first_non_empty_text(
        book_protagonist.get("name"),
        cast_protagonist.get("name"),
        default="protagonist" if is_en else "主角",
    )
    opening_strategy = _first_non_empty_text(
        market.get("opening_contract"),
        market.get("opening_strategy"),
        metadata.get("opening_strategy"),
        series_engine.get("opening_strategy"),
        default=(
            "Open from an anomaly, crisis, misunderstanding, humiliation, loss, conflict of interest, or forced choice."
            if is_en
            else "从异常、危机、误会、侮辱、损失、利益冲突或被迫选择切入。"
        ),
    )
    first_page_pressure = _first_non_empty_text(
        opening_state.get("world_situation"),
        first_volume.get("volume_obstacle"),
        stakes.get("personal"),
        default=(
            f"{protagonist_name} faces visible pressure on the first page; the story cannot sit in normal daily setup."
            if is_en
            else f"{protagonist_name}在第一页遭遇可见压力，不能停在普通日常里。"
        ),
    )
    immediate_goal = _first_non_empty_text(
        book_protagonist.get("external_goal"),
        cast_protagonist.get("goal"),
        first_volume.get("volume_goal"),
        default=(
            f"{protagonist_name} must act immediately to secure the main-story entry point."
            if is_en
            else f"{protagonist_name}必须立刻做出行动，先保住主线入口。"
        ),
    )
    visible_loss = _first_non_empty_text(
        stakes.get("personal"),
        first_volume.get("volume_obstacle"),
        volume_resolution.get("cost_paid"),
        default=(
            f"If they fail, {protagonist_name} loses a key opportunity, relationship, or survival space."
            if is_en
            else f"如果失败，{protagonist_name}会失去关键机会、关系或生存空间。"
        ),
    )
    protagonist_edge = _first_non_empty_text(
        book_protagonist.get("golden_finger"),
        book_protagonist.get("core_strength"),
        cast_protagonist.get("golden_finger"),
        cast_protagonist.get("core_strength"),
        # 2026-06-25 去同质化：原默认一律是"抓住别人忽略的漏洞并制造反转"，
        # 叠加品类模板的钻漏洞偏置→每本玄幻/修仙书主角都成"卡规则/天道漏洞"型。
        # 改为不预设打法的中性优势（具体形态由本书 golden_finger/archetype 决定）。
        default=(
            f"{protagonist_name} can make, under pressure, the one decisive move others can't — and turn the situation."
            if is_en
            else f"{protagonist_name}能在高压下做出别人做不到的那一步关键判断或行动，扭转局面。"
        ),
    )
    edge_limit = _first_non_empty_text(
        book_protagonist.get("weakness"),
        cast_protagonist.get("weakness"),
        cast_protagonist.get("fatal_flaw"),
        default=(
            "The edge can solve only the first layer of pressure; it cannot bypass the main cost."
            if is_en
            else "优势只能解决第一轮压力，不能直接跳过主线代价。"
        ),
    )
    first_three_goal = _first_non_empty_text(
        series_engine.get("first_three_chapter_goal"),
        market.get("first_three_chapter_goal"),
        default=(
            "The first three chapters must land conflict, protagonist edge, a small payoff, and the next hook."
            if is_en
            else "前三章完成冲突、优势、小爽点、下一轮钩子。"
        ),
    )
    payoff_rhythm = _first_non_empty_text(
        series_engine.get("payoff_rhythm"),
        market.get("payoff_rhythm"),
        default=(
            "Dense short payoffs; even transition chapters need conflict, gain, or information gap."
            if is_en
            else "短回报密集，过渡章也要有小冲突、小收益或小信息差。"
        ),
    )
    core_loop = _first_non_empty_text(
        series_engine.get("core_loop"),
        first_volume.get("reader_hook_to_next"),
        default="trigger conflict -> protagonist action -> reward/cost -> next hook"
        if is_en
        else "触发冲突 -> 主角行动 -> 收益/代价 -> 新钩子",
    )

    return {
        "platform_target": _project_platform_label(project, market),
        "source": "commercial_fiction_opening_quality_framework",
        "protagonist_name": protagonist_name,
        "rejection_causes_addressed": [
            "weak prose / 文笔还有待提升",
            "weak immersion / 代入感较弱",
            "ordinary entry point / 开篇的切入点比较普通",
            "weak attraction / 缺乏足够的吸引力",
            "flat narration / 故事的叙述较为平淡",
        ],
        "opening_incident": opening_strategy,
        "first_page_conflict": (
            f"{first_page_pressure} The first page must form visible conflict."
            if is_en
            else f"{first_page_pressure} 必须在前600字内形成可感冲突。"
        ),
        "protagonist_immediate_goal": immediate_goal,
        "visible_loss_if_fail": visible_loss,
        "protagonist_edge": protagonist_edge,
        "edge_limit": edge_limit,
        "chapter_1_small_turn": (
            f"{protagonist_name} takes active action and creates a local reversal or information edge."
            if is_en
            else f"{protagonist_name}主动行动，至少完成一次局部反制或信息差建立。"
        ),
        "chapter_2_reveal": (
            "Chapter 2 reveals new information, an expanded misunderstanding, or a hidden rule that changes the situation."
            if is_en
            else "第二章放出会改变局势判断的新信息、误会扩大或隐藏规则。"
        ),
        "chapter_3_payoff": (
            f"{first_three_goal} Payoff rhythm: {payoff_rhythm}"
            if is_en
            else f"{first_three_goal} 节奏口径：{payoff_rhythm}"
        ),
        "first_10000_loop": core_loop,
        "forbidden_opening_modes": [
            "background_exposition",
            "normal_day",
            "scenery_first",
            "worldbuilding_first",
            "slow_relationship_setup",
        ],
        "premise_anchor": _first_non_empty_text(
            _mapping(book_spec).get("logline"),
            premise,
            default=project.title,
        ),
    }


def persist_qimao_opening_contract(
    project: ProjectModel,
    *,
    premise: str,
    book_spec: dict[str, Any],
    cast_spec: dict[str, Any],
    volume_plan: Any,
) -> dict[str, Any] | None:
    contract = build_qimao_opening_contract(
        project,
        premise=premise,
        book_spec=book_spec,
        cast_spec=cast_spec,
        volume_plan=volume_plan,
    )
    metadata = dict(_mapping(project.metadata_json))
    metadata["opening_quality_contract"] = contract
    metadata["opening_quality_contract_status"] = "planned"
    metadata["qimao_opening_contract"] = contract
    metadata["qimao_opening_contract_status"] = "planned"
    project.metadata_json = metadata
    return contract


def _role_metadata_with_evolution_note(
    current_metadata: Any,
    *,
    raw_role: str,
    normalized_role: str,
) -> dict[str, Any]:
    metadata = copy.deepcopy(_mapping(current_metadata))
    metadata["role_evolution"] = raw_role
    if normalized_role and normalized_role != raw_role:
        metadata["role_evolution_normalized_label"] = normalized_role
    return metadata


def _age_metadata_with_note(
    current_metadata: Any,
    *,
    raw_age: Any,
    normalized_age: int | None,
) -> dict[str, Any]:
    metadata = copy.deepcopy(_mapping(current_metadata))
    raw_text = str(raw_age).strip()
    if raw_text:
        metadata["age_note"] = raw_text
    if normalized_age is not None and raw_text != str(normalized_age):
        metadata["age_normalized"] = normalized_age
    return metadata


def _sanitize_character_age_field(candidate: dict[str, Any]) -> dict[str, Any]:
    raw_age = candidate.get("age")
    if raw_age is None or raw_age == "":
        candidate.pop("age", None)
        return candidate

    normalized_age = normalize_character_age(raw_age)
    if normalized_age is None:
        candidate["metadata"] = _age_metadata_with_note(
            candidate.get("metadata"),
            raw_age=raw_age,
            normalized_age=None,
        )
        candidate.pop("age", None)
        return candidate

    if isinstance(raw_age, str) and raw_age.strip() != str(normalized_age):
        candidate["metadata"] = _age_metadata_with_note(
            candidate.get("metadata"),
            raw_age=raw_age,
            normalized_age=normalized_age,
        )
    candidate["age"] = normalized_age
    return candidate


def _store_character_evolution_notes(
    current_metadata: Any,
    notes: list[str],
) -> dict[str, Any]:
    metadata = copy.deepcopy(_mapping(current_metadata))
    existing = _string_list(metadata.get("evolution_notes"))
    metadata["evolution_notes"] = existing + [note for note in notes if note not in existing]
    return metadata


def _sanitize_new_character_candidate(
    raw_value: Any,
    *,
    default_role: str = "supporting",
) -> dict[str, Any]:
    if isinstance(raw_value, str):
        candidate = {"name": raw_value, "role": default_role}
    else:
        candidate = copy.deepcopy(_mapping(raw_value))

    raw_role = candidate.get("role")
    if not isinstance(raw_role, str) or not raw_role.strip():
        candidate["role"] = default_role
        return _sanitize_character_age_field(candidate)

    normalized_role = normalize_character_role_label(raw_role, fallback=default_role)
    if is_safe_character_role_label(raw_role):
        candidate["role"] = normalized_role
        return _sanitize_character_age_field(candidate)

    candidate["metadata"] = _role_metadata_with_evolution_note(
        candidate.get("metadata"),
        raw_role=raw_role.strip(),
        normalized_role=normalized_role,
    )
    candidate["role"] = default_role
    return _sanitize_character_age_field(candidate)


def _sanitize_character_evolution_changes(
    target: dict[str, Any],
    changes: dict[str, Any],
    *,
    allow_role_change: bool,
) -> dict[str, Any]:
    sanitized = copy.deepcopy(changes)
    raw_evolution_notes = _string_list(sanitized.get("changes"))
    raw_evolution_notes.extend(
        note
        for note in _string_list(sanitized.get("evolution_notes"))
        if note not in raw_evolution_notes
    )
    if raw_evolution_notes:
        sanitized["metadata"] = _store_character_evolution_notes(
            sanitized.get("metadata"),
            raw_evolution_notes,
        )
        sanitized.pop("changes", None)
        sanitized.pop("evolution_notes", None)

    raw_age = sanitized.get("age")
    if raw_age is not None and raw_age != "":
        normalized_age = normalize_character_age(raw_age)
        if normalized_age is None:
            sanitized["metadata"] = _age_metadata_with_note(
                sanitized.get("metadata"),
                raw_age=raw_age,
                normalized_age=None,
            )
            sanitized.pop("age", None)
        else:
            if isinstance(raw_age, str) and raw_age.strip() != str(normalized_age):
                sanitized["metadata"] = _age_metadata_with_note(
                    sanitized.get("metadata"),
                    raw_age=raw_age,
                    normalized_age=normalized_age,
                )
            sanitized["age"] = normalized_age

    raw_role = sanitized.get("role")
    if not isinstance(raw_role, str) or not raw_role.strip():
        return sanitized

    fallback_role = _non_empty_string(target.get("role"), "supporting")
    normalized_role = normalize_character_role_label(raw_role, fallback=fallback_role)
    if allow_role_change and is_safe_character_role_label(raw_role):
        sanitized["role"] = normalized_role
        return sanitized

    sanitized.pop("role", None)
    sanitized["metadata"] = _role_metadata_with_evolution_note(
        sanitized.get("metadata"),
        raw_role=raw_role.strip(),
        normalized_role=normalized_role,
    )
    return sanitized


def _named_item(items: list[dict[str, Any]], index: int, default_name: str) -> dict[str, Any]:
    if 0 <= index < len(items):
        item = items[index]
        return {
            **item,
            "name": _non_empty_string(item.get("name"), default_name),
        }
    return {"name": default_name}


def _protagonist_name_from_book_spec(
    book_spec: dict[str, Any],
    premise: str,
    genre: str = "",
    language: str | None = None,
    seed_text: str | None = None,
) -> str:
    protagonist = _mapping(_mapping(book_spec).get("protagonist"))
    return _non_empty_string(
        protagonist.get("name"),
        _derive_protagonist_name(premise, genre, language=language, seed_text=seed_text),
    )


def _derive_protagonist_name(
    premise: str,
    genre: str = "",
    language: str | None = None,
    seed_text: str | None = None,
) -> str:
    """Return a safe placeholder protagonist name.

    Premise text is NEVER regex-mined for names — that historically produced
    garbage fragments like ``基于末`` (from ``基于末日…``). The authoritative
    source for character names is the LLM call ``_generate_character_names()``
    which is invoked at the start of ``run_planning_pipeline``. This helper
    only exists as a last-resort placeholder when no LLM/book_spec name is
    available, and returns a curated genre-appropriate name from the pool.
    """
    pool = _genre_name_pool(
        genre,
        language=language,
        seed_text=seed_text or _seed_material(premise[:160], genre, language or ""),
    )
    name = _mapping(pool.get("protagonist")).get("name")
    _fallback = "Protagonist" if is_english_language(language) else "主角"
    return name if isinstance(name, str) and name else _fallback


# ── R4: premise explicit-roster passthrough ────────────────────────────
# When a premise explicitly names its cast (e.g. a "人物名册（正文必须沿用
# 以下姓名）" section), those names are authoritative. Production evidence:
# the LLM name pool + cast planner renamed an entire explicit roster. The
# extraction below is generic (structural markers + frequency + a
# language-level surname check) — deliberately NO genre vocabulary.

_PREMISE_ROSTER_MARKERS: tuple[str, ...] = (
    "人物名册",
    "角色名册",
    "人物表",
    "角色表",
    "必须沿用",
    "沿用以下姓名",
    "名册",
    # G2 (xianxia benchmark): natural-language roster intros. Premises often
    # list the supporting cast as "关键配角：…孤女宋拾、…执事关铎、…" rather
    # than a formal name table; without these markers the embedded names were
    # dropped and the cast planner invented replacements (P-4).
    "关键配角",
    "主要配角",
    "核心配角",
    "重要配角",
    "主要人物",
    "核心人物",
    "cast roster",
    "character roster",
    "must use the following names",
    "key supporting cast",
    "main characters",
)

# Grammatical / structural / role-label tokens only — these describe the
# document or narrative function, never a specific genre's content.
_PREMISE_ROSTER_STOPWORDS: frozenset[str] = frozenset(
    {
        "人物", "角色", "名册", "姓名", "名单", "名字", "本名", "正文",
        "必须", "沿用", "以下", "如下", "使用", "禁止", "改名", "主角",
        "反派", "配角", "盟友", "对手", "男主", "女主", "主线", "支线",
        "故事", "前提", "设定", "世界", "背景", "章节", "大纲", "自己",
        "他们", "她们", "我们", "时候", "现在", "已经", "开始", "最后",
        "突然", "所有", "知道", "发现", "没有", "可以", "什么", "这个",
        "那个", "一个", "事情", "问题", "方法", "东西",
    }
)

# Characters that never appear inside a Chinese person name in practice —
# particles, copulas, pronouns, common function words. Pure grammar layer.
_PREMISE_NAME_FUNCTION_CHARS: frozenset[str] = frozenset(
    "的了在是和与或但因而从就都也很还又被把让将对向于之其这那他她你我它们个一不没有为以及等"
)

# Common Chinese surnames (language-level resource, not a genre word-list).
# Used ONLY by the frequency fallback path to tell person names apart from
# frequent common nouns; the explicit-marker path does not require it.
_COMMON_ZH_SURNAMES: frozenset[str] = frozenset(
    "王李张刘陈杨黄赵吴周徐孙马朱胡郭何林罗高郑梁谢宋唐许韩冯邓曹彭曾肖田董袁潘"
    "蒋蔡余杜叶程苏魏吕丁任沈姚卢姜崔钟谭陆汪范金石廖贾夏韦付方白邹孟熊秦邱江"
    "尹薛闫段雷侯龙史陶黎贺顾毛郝龚邵万钱严覃武戴莫孔向汤楚洛纪温庄晏柳霍盛"
)

_PREMISE_CJK_RUN_RE = re.compile(r"[一-鿿]+")


def _premise_roster_segments(premise: str) -> list[str]:
    """Return the text segments that follow explicit roster markers.

    The window is generous (3000 chars) and ends at the first section break
    — a blank line OR the next ``【…】`` section header — because hand-authored
    rosters can list 20+ characters and run well past a few hundred chars
    (the old 400-char cap truncated such rosters to their first few names).
    """

    segments: list[str] = []
    lowered = premise.lower()
    for marker in _PREMISE_ROSTER_MARKERS:
        start = 0
        marker_lower = marker.lower()
        while True:
            idx = lowered.find(marker_lower, start)
            if idx < 0:
                break
            tail = premise[idx + len(marker) : idx + len(marker) + 3000]
            # A roster section ends at the first blank line or next 【…】 header.
            cuts = [c for c in (tail.find("\n\n"), tail.find("【")) if c >= 0]
            cut = min(cuts) if cuts else -1
            segments.append(tail if cut < 0 else tail[:cut])
            start = idx + len(marker)
    return segments


def _premise_name_candidate_ok(token: str) -> bool:
    if not (2 <= len(token) <= 3):
        return False
    if token in _PREMISE_ROSTER_STOPWORDS:
        return False
    if any(ch in _PREMISE_NAME_FUNCTION_CHARS for ch in token):
        return False
    return True


_PREMISE_PARENTHETICAL_RE = re.compile(r"[（(][^）)]*[）)]")
_PREMISE_LIST_SEP_RE = re.compile(r"[、,，;；]")
_PREMISE_LEADING_PAREN_RE = re.compile(r"^[^一-鿿]*[（(][^）)]*[）)]")
_PREMISE_LEADING_NONCJK_RE = re.compile(r"^[^一-鿿]+")


def _name_from_roster_item(item: str) -> str | None:
    """Pull one person name from a single roster list item (G2/G2b).

    Roster items name a character either at the HEAD followed by a
    descriptive parenthetical (``陈屿（口头禅…）`` — common in hand-authored
    rosters) or after a role/origin prefix at the TAIL (``…孤女宋拾`` —
    natural-language rosters). Both reduce to: strip leading marker
    brackets / punctuation, keep the text *before the first parenthesis*,
    then a 2-3 char head IS the name while a longer head yields its
    surname-anchored 2-3 char tail.

    Crucially this takes ONE boundary name per item rather than harvesting
    every 2-3 char run in the marker segment — the latter drowns real names
    under description noise (``口头禅``/``测谎``/``差评``) and even seeds bogus
    cast members.
    """

    s = item.strip()
    # Drop any leading parenthetical block(s) (e.g. the marker aside
    # "（正文必须沿用以下姓名与设定）") and surrounding non-CJK punctuation.
    prev = None
    while s and s != prev:
        prev = s
        s = _PREMISE_LEADING_PAREN_RE.sub("", s)
        s = _PREMISE_LEADING_NONCJK_RE.sub("", s)
    if not s:
        return None
    # The name sits before the first descriptive parenthesis.
    head_segment = re.split(r"[（(]", s, maxsplit=1)[0]
    head = "".join(ch for ch in head_segment if "一" <= ch <= "鿿")
    if len(head) < 2:
        return None
    if len(head) <= 3:
        candidate = head
    else:
        last3 = head[-3:]
        candidate = last3 if last3[0] in _COMMON_ZH_SURNAMES else head[-2:]
    if candidate in _PREMISE_ROSTER_STOPWORDS:
        return None
    if any(ch in _PREMISE_NAME_FUNCTION_CHARS for ch in candidate):
        return None
    return candidate


def _extract_premise_locked_names(
    premise: str,
    *,
    language: str | None = None,
    max_names: int = 12,
) -> list[str]:
    """Extract the explicit character roster from a premise, generically.

    Two passes, both deterministic and genre-free:

    1. **Marker pass** — names listed after structural roster markers
       (「人物名册」/「必须沿用」…); authoritative when present.
    2. **Frequency pass** — 2-3 character CJK tokens that occur >= 3
       times in the premise AND start with a common surname (a
       language-level check, not a genre word-list). Longer candidates
       suppress their substrings ("赵小磊" suppresses "赵小"/"小磊").

    English premises currently return only marker-pass names (Latin
    rosters are rare and the cast prompt already receives them verbatim).
    """

    text = (premise or "").strip()
    if not text:
        return []

    ordered: list[str] = []
    seen: set[str] = set()

    def _add(name: str) -> None:
        if name not in seen:
            seen.add(name)
            ordered.append(name)

    # Marker pass (G2/G2b): split each roster segment into list items and
    # take ONE boundary name per item. Handles formal tables
    # ("名册：周澈、九嶷、林晚"), tail-embedded natural rosters
    # ("…孤女宋拾、…执事关铎") and head-position hand-authored rosters
    # ("陈屿（口头禅…）；老金（…）") in one pass — without harvesting every CJK
    # run, which would drown names under description noise.
    for segment in _premise_roster_segments(text):
        # Remove descriptive parentheticals BEFORE splitting so their inner
        # commas ("陈屿（主角，口头禅…）") don't fragment a single roster item
        # into description-word "items" (口头禅/测谎/差评).
        cleaned_segment = _PREMISE_PARENTHETICAL_RE.sub("、", segment)
        for item in _PREMISE_LIST_SEP_RE.split(cleaned_segment):
            candidate = _name_from_roster_item(item)
            if candidate is not None:
                _add(candidate)

    if is_english_language(language):
        return ordered[:max_names]

    # When an explicit roster marker already yielded a substantial cast,
    # treat it as authoritative and skip the frequency fallback — on long
    # hand-authored premises that fallback harvests common nouns whose first
    # char happens to be a surname (龙王/武职/百年). The fallback still runs
    # for sparse/marker-less premises (e.g. a lone protagonist named inline).
    if len(ordered) >= 6:
        return ordered[:max_names]

    # Pass 2: high-frequency name-shaped tokens across the whole premise.
    counts: dict[str, int] = {}
    runs = _PREMISE_CJK_RUN_RE.findall(text)
    for run in runs:
        for n in (2, 3):
            for i in range(len(run) - n + 1):
                token = run[i : i + n]
                counts[token] = counts.get(token, 0) + 1
    frequent = [
        (token, count)
        for token, count in counts.items()
        if count >= 3
        and _premise_name_candidate_ok(token)
        and token[0] in _COMMON_ZH_SURNAMES
    ]
    # Prefer longer tokens; suppress substrings of an already-kept token.
    frequent.sort(key=lambda item: (-len(item[0]), -item[1], item[0]))
    kept: list[str] = []
    for token, _count in frequent:
        if any(token in longer for longer in kept):
            continue
        kept.append(token)
    for token in kept:
        if any(token in name for name in ordered):
            continue
        _add(token)

    return ordered[:max_names]


def _premise_character_descriptor(premise: str, name: str) -> str:
    """Pull the premise clause that introduces ``name`` (role + parenthetical).

    e.g. for "栖梧宗药圃监工裴萤（发现谢迟丹草异常的第一人）" returns
    "栖梧宗药圃监工裴萤，发现谢迟丹草异常的第一人". Falls back to empty string.
    """

    text = premise or ""
    idx = text.find(name)
    if idx < 0:
        return ""
    # role prefix: the run of non-separator chars immediately before the name
    start = idx
    seps = "，。；、：（）()【】\n 　"
    while start > 0 and text[start - 1] not in seps:
        start -= 1
    role = text[start:idx].strip()
    # parenthetical right after the name
    desc = ""
    tail = text[idx + len(name):]
    for open_p, close_p in (("（", "）"), ("(", ")")):
        if tail.startswith(open_p):
            end = tail.find(close_p)
            if end > 0:
                desc = tail[1:end].strip()
            break
    parts = [p for p in (f"{role}{name}" if role else name, desc) if p]
    return "，".join(parts)


_FEMALE_DESC_CUES = (
    "孤女", "师姐", "少女", "女子", "妇人", "娘", "妃", "母", "姑", "婆",
    "嬷", "姐", "妹", "夫人", "小姐", "婢", "尼姑", "女修",
)
_MALE_DESC_CUES = (
    "师兄", "少年", "翁", "公子", "兄", "父", "爷", "郎", "叟", "汉子",
    "先生", "和尚", "道长", "执事", "男子", "男修",
)
# Weak fallback only: given-name characters that skew strongly feminine.
_FEMALE_NAME_CHARS = set("萤雪月兰婉莲香琴瑶玉珠媛娟妍蓉茵芷莹黛颜霜柔嫣")


def _infer_premise_character_gender(
    premise: str, name: str, descriptor: str = ""
) -> str:
    """Best-effort deterministic gender for a premise-locked character.

    The foundation_identity_contract requires a non-unknown gender at
    materialization. Premise-locked entries are appended after the LLM cast
    gates that normally fill this, so they arrive blank. Infer from explicit
    cues in the descriptor / premise window (孤女/师姐/翁/执事…), then a weak
    feminine-name-character fallback, and finally default to ``male`` (the most
    common xianxia supporting default) so materialization is never blocked.
    The value is correctable later — a defaulted gender beats a dropped name.
    """

    window = descriptor or ""
    idx = premise.find(name) if premise else -1
    if idx != -1:
        window += " " + premise[max(0, idx - 8):idx + 48]
    for cue in _FEMALE_DESC_CUES:
        if cue in window:
            return "female"
    for cue in _MALE_DESC_CUES:
        if cue in window:
            return "male"
    if any(ch in _FEMALE_NAME_CHARS for ch in name[1:]):
        return "female"
    return "male"


def _premise_locked_identity_fields(
    premise: str, name: str, descriptor: str = ""
) -> dict[str, str]:
    """gender + zh/en pronoun set for a premise-locked character (contract-required)."""

    gender = _infer_premise_character_gender(premise, name, descriptor)
    if gender == "female":
        return {"gender": "female", "pronoun_set_zh": "她", "pronoun_set_en": "she/her"}
    return {"gender": "male", "pronoun_set_zh": "他", "pronoun_set_en": "he/him"}


def _enforce_premise_locked_cast(
    cast_spec: dict[str, Any],
    premise: str,
    *,
    language: str | None = None,
) -> dict[str, Any]:
    """Guarantee every premise-locked character survives into the cast roster.

    The cast prompt receives the locked names, but the model routinely renames
    or drops premise supporting cast (蚀漏砚: 裴萤/关铎/宋拾/白杪 were all dropped
    and replaced with invented names). The outline then faithfully references the
    premise names, the identity lock strips them as non-roster ad-hoc mentions,
    and their scenes collapse to false solo chains — breaking both 章级可写性 and
    人物关系演化. This deterministic post-generation contract appends any missing
    locked name as a named supporting_cast member with a premise-derived role and
    arc, so the locked relationships propagate through the whole pipeline.
    """

    if not isinstance(cast_spec, dict):
        return cast_spec
    locked = _extract_premise_locked_names(premise, language=language)
    if not locked:
        return cast_spec

    def _names_in(value: Any) -> set[str]:
        found: set[str] = set()
        for member in _mapping_list(value):
            for key in ("name", "canonical_name", "title"):
                n = _non_empty_string(member.get(key), "")
                if n:
                    found.add(n)
            for alias in member.get("aliases") or []:
                if _non_empty_string(alias, ""):
                    found.add(str(alias).strip())
        return found

    protagonist = _mapping(cast_spec.get("protagonist"))
    present = (
        _names_in(cast_spec.get("supporting_cast"))
        | _names_in(cast_spec.get("antagonist_forces"))
        | {_non_empty_string(protagonist.get("name"), "")}
    )
    present.discard("")

    missing = [n for n in locked if n not in present]
    if not missing:
        return cast_spec

    updated = dict(cast_spec)
    roster = list(_mapping_list(cast_spec.get("supporting_cast")))
    for name in missing:
        descriptor = _premise_character_descriptor(premise, name)
        goal = descriptor or f"{name}在设定中既定的立场与目标"
        roster.append(
            {
                "name": name,
                "role": "supporting",
                "goal": goal,
                "motivation": goal,
                "evolution_arc": (
                    f"{name}随主线推进，与主角的关系/立场逐卷演化（设定锚定的关键配角）。"
                ),
                "arc_trajectory": (
                    f"{name}从初遇到与主角阵营分合，立场随冲突升级而转变。"
                ),
                "relationship_to_protagonist": descriptor or "设定锚定的关键关系人",
                "name_reasoning": "premise-locked 关键配角（设定锚定，禁止改名或丢弃）",
                # Foundation identity contract requires a locked gender + zh/en
                # pronouns at materialization; appended entries skip the LLM
                # identity gates, so populate them deterministically here.
                **_premise_locked_identity_fields(premise, name, descriptor),
                "metadata": {"premise_locked_enforced": True},
            }
        )
    updated["supporting_cast"] = roster
    return updated


def _merge_locked_names_into_pool(
    pool: dict[str, Any],
    locked_names: list[str],
) -> dict[str, Any]:
    """Return a new name pool with premise roster names merged in as locked.

    The original pool is not mutated. Locked names are surfaced both as a
    dedicated ``locked_names`` list (consumed by the cast-spec prompt) and
    as ally entries so every deterministic fallback consumer sees them.
    """

    if not locked_names:
        return pool
    merged = copy.deepcopy(pool) if isinstance(pool, dict) else {}
    merged["locked_names"] = list(locked_names)
    existing = {
        _non_empty_string(_mapping(merged.get("protagonist")).get("name"), ""),
        *(
            _non_empty_string(entry.get("name"), "")
            for entry in _mapping_list(merged.get("allies"))
        ),
        *(
            _non_empty_string(entry.get("name"), "")
            for entry in _mapping_list(merged.get("antagonists"))
        ),
    }
    allies = list(_mapping_list(merged.get("allies")))
    for name in locked_names:
        if name in existing:
            continue
        allies.append(
            {
                "name": name,
                "locked": True,
                "name_reasoning": "premise 名册既定人物，必须沿用",
            }
        )
    merged["allies"] = allies
    return merged


async def _generate_character_names(
    session: AsyncSession,
    settings: AppSettings,
    *,
    genre: str,
    sub_genre: str,
    language: str | None,
    premise: str,
    book_spec: dict[str, Any],
    character_count: int = 5,
    workflow_run_id: UUID | None = None,
    project_id: UUID | None = None,
) -> dict[str, Any]:
    """Generate contextually appropriate character names via LLM.

    Considers genre, era, character archetypes, and cultural context to produce
    natural, memorable names. Returns a dict with protagonist, allies, and
    antagonists name entries.
    """
    protagonist = _mapping(_mapping(book_spec).get("protagonist"))
    archetype = protagonist.get("archetype", "")
    era_hints = _detect_era_from_genre(genre)
    is_en = is_english_language(language)
    # R4: premise explicit roster names survive every return path below.
    locked_names = _extract_premise_locked_names(premise, language=language)

    if is_en:
        user_prompt = (
            f"Generate {character_count} character names for the following novel.\n\n"
            f"Genre: {genre} ({sub_genre})\n"
            f"Era / setting hint: {era_hints}\n"
            f"Premise: {premise[:300]}\n"
            f"Protagonist archetype: {archetype}\n\n"
            "Requirements:\n"
            "1. Names must feel natural for English-language commercial fiction in this genre.\n"
            "2. The protagonist name should be memorable and easy to pronounce.\n"
            "3. Avoid confusingly similar initials or sounds across the core cast.\n"
            "4. Antagonist names may imply personality, but stay subtle.\n\n"
            "Output JSON:\n"
            '{"protagonist": {"name": "protagonist name", "name_reasoning": "why it fits"},\n'
            '  "allies": [{"name": "ally name", "name_reasoning": "why it fits"}],\n'
            '  "antagonists": [{"name": "antagonist name", "name_reasoning": "why it fits"}]\n'
            "}"
        )
    else:
        user_prompt = (
            f"为以下小说生成 {character_count} 个角色名字。\n\n"
            f"题材：{genre}（{sub_genre}）\n"
            f"时代背景：{era_hints}\n"
            f"故事前提：{premise[:300]}\n"
            f"主角原型：{archetype}\n\n"
            f"要求：\n"
            f"1. 根据题材和时代选择合适的姓名风格：\n"
            f"   - 古代/仙侠/玄幻：古典、利落、带意象感\n"
            f"   - 现代/都市：现代自然、易读、口语化场景适配\n"
            f"   - 末日/科幻/未来：硬朗清晰、带生存压力感\n"
            f"2. 主角名 2-3 字，音调和谐，有记忆点\n"
            f"3. 所有角色姓氏不能重复\n"
            f"4. 避免谐音不雅、过于生僻或网文烂大街的名字\n"
            f"5. 反派名可暗示性格（但不要太刻意）\n\n"
            f"输出 JSON：\n"
            f'{{"protagonist": {{"name": "主角名", "name_reasoning": "命名理由"}},\n'
            f'  "allies": [{{"name": "盟友名", "name_reasoning": "命名理由"}}],\n'
            f'  "antagonists": [{{"name": "反派名", "name_reasoning": "命名理由"}}]\n'
            f"}}"
        )

    request = LLMCompletionRequest(
        logical_role="critic",
        system_prompt=(
            (
                "# ROLE\n"
                "You are a naming specialist for English-language commercial fiction.\n"
                "You have named cast for 30+ signed novels across thriller, fantasy, sci-fi, romance.\n"
                "You know the difference between names readers REMEMBER and names that fade.\n"
                "\n"
                "# CONTEXT · The five naming axes (apply silently)\n"
                "1. **Phonetic memorability** — short, distinct syllables\n"
                "2. **Genre fit** — Hawthorne feels Gothic, Ryder feels modern thriller\n"
                "3. **Cross-cast distinction** — no two characters share initial / rhyme / length\n"
                "4. **Subtle personality hint** — names suggest without telegraphing\n"
                "5. **Readability** — pronounceable on first sight\n"
                "\n"
                "# TASK\n"
                "Generate names + reasoning per the user prompt requirements.\n"
                "\n"
                "# CONSTRAINTS\n"
                "- Strict JSON output, no markdown fences, no commentary\n"
                "- Each reasoning ≤ 30 words\n"
                "- No clichéd fantasy names (no Aragorn-knockoffs, no Twilight-knockoffs)\n"
                "- No two main-cast names sharing initial letter\n"
                "\n"
                "# OUTPUT FORMAT (strict JSON)\n"
                '{"protagonist": {"name", "name_reasoning"}, '
                '"allies": [{"name", "name_reasoning"}], '
                '"antagonists": [{"name", "name_reasoning"}]}'
            )
            if is_en
            else (
                "# ROLE\n"
                "你是一位中文小说命名专家——签约编辑都问你「这角色叫什么好」的那种。\n"
                "你为 30+ 部签约长篇起过名，深谙各题材的命名风格（古典 / 现代 / 玄幻 / 末日 / 都市）。\n"
                "你能一眼看穿网文烂大街的名字（如「叶辰」「林轩」「楚天」），并主动规避。\n"
                "\n"
                "# CONTEXT · 五大命名维度（默默应用，不要写进 reasoning）\n"
                "1. **音韵记忆点**：2-3 字，音调和谐，读出来有顿挫感\n"
                "2. **题材气质适配**：古风用古典字、现代用日常字、玄幻用意象字\n"
                "3. **跨角色区分度**：主要角色姓氏不重复，首字音也不撞\n"
                "4. **暗藏性格**：反派名可暗示性格但不能太刻意\n"
                "5. **避雷**：避免谐音不雅、过于生僻、网文烂大街\n"
                "\n"
                "# CONTEXT · 中文网文烂名黑名单（绝对避免）\n"
                "- 「叶辰」「林轩」「楚天」「叶凡」「萧炎」「韩立」（爆款重名灾区）\n"
                "- 任何「+雨 / +寒 / +轻 / +默」结尾的女角色名\n"
                "- 反派叫「张无忌」「李莫愁」（金庸碰瓷）\n"
                "\n"
                "# TASK\n"
                "按 user prompt 的要求生成角色名 + 命名理由。\n"
                "\n"
                "# CONSTRAINTS\n"
                "- 严格 JSON 输出，无 markdown 围栏，无解释文字\n"
                "- 每个 name_reasoning ≤ 30 字\n"
                "- 主要角色姓氏不能重复\n"
                "- 主要角色首字音不能撞（同姓且首字同音的两个主要角色不可并存）\n"
                "\n"
                "# THINKING（输出 JSON 前在脑内 3 步）\n"
                "1. 看 era_hints 决定基调（古典 / 现代 / 末日 / 玄幻）\n"
                "2. 先定主角，再围绕主角设计盟友、反派的对比 / 呼应\n"
                "3. 自检：每个名字念出声 → 顺口吗？跟主角撞音吗？是不是黑名单里的？\n"
                "\n"
                "# OUTPUT FORMAT（严格 JSON）\n"
                '{"protagonist": {"name", "name_reasoning"}, '
                '"allies": [{"name", "name_reasoning"}], '
                '"antagonists": [{"name", "name_reasoning"}]}'
            )
        ),
        user_prompt=user_prompt,
        fallback_response=json.dumps(
            _genre_name_pool(
                genre,
                language=language,
                seed_text=_seed_material(
                    genre,
                    sub_genre,
                    language or "",
                    premise[:300],
                    archetype,
                    project_id or "",
                    workflow_run_id or "",
                ),
            ),
            ensure_ascii=False,
        ),
        prompt_template="generate_character_names",
        project_id=project_id,
        workflow_run_id=workflow_run_id,
    )
    result = await complete_text(session, settings, request)

    try:
        parsed = _extract_json_payload(result.content)
        if isinstance(parsed, dict) and parsed.get("protagonist", {}).get("name"):
            return _merge_locked_names_into_pool(parsed, locked_names)
        raise ValueError("character_names_schema_invalid: missing protagonist.name")
    except (ValueError, KeyError, TypeError) as exc:
        from bestseller.services.llm_closed_loop import build_repair_user_prompt, findings_from_exception

        findings = findings_from_exception(exc, default_path="character_names")
        repair = await complete_text(
            session,
            settings,
            request.model_copy(
                update={
                    "user_prompt": build_repair_user_prompt(
                        original_user_prompt=user_prompt,
                        findings=findings,
                        language=language,
                    ),
                    "prompt_template": "generate_character_names_repair",
                    "metadata": {
                        "semantic_repair_of": str(result.llm_run_id) if result.llm_run_id else None,
                        "repair_findings": [finding.to_dict() for finding in findings],
                    },
                }
            ),
        )
        try:
            parsed = _extract_json_payload(repair.content)
            if isinstance(parsed, dict) and parsed.get("protagonist", {}).get("name"):
                return _merge_locked_names_into_pool(parsed, locked_names)
        except (ValueError, KeyError, TypeError):
            logger.warning(
                "Character name JSON repair failed; falling back to deterministic name pool.",
                exc_info=True,
            )

    return _merge_locked_names_into_pool(
        _genre_name_pool(
            genre,
            language=language,
            seed_text=_seed_material(
                genre,
                sub_genre,
                language or "",
                premise[:300],
                archetype,
                project_id or "",
                workflow_run_id or "",
            ),
        ),
        locked_names,
    )


def _detect_era_from_genre(genre: str) -> str:
    """Infer era/setting from genre for name style selection."""
    normalized = genre.lower()
    if any(tok in normalized for tok in ("仙", "玄幻", "修真", "古代", "武侠", "历史")):
        return "古代/架空古风"
    if any(tok in normalized for tok in ("都市", "现代", "校园", "职场")):
        return "现代都市"
    if any(tok in normalized for tok in ("科幻", "末日", "未来", "赛博", "星际")):
        return "未来/末日"
    return "架空（可自由选择风格）"


def _seed_material(*parts: Any) -> str:
    return "|".join(str(part).strip() for part in parts if str(part).strip())


def _stable_order(items: list[str], *, seed_text: str, salt: str) -> list[str]:
    decorated: list[tuple[str, str]] = []
    for idx, item in enumerate(items):
        digest = hashlib.sha256(f"{seed_text}|{salt}|{idx}|{item}".encode()).hexdigest()
        decorated.append((digest, item))
    decorated.sort(key=lambda pair: pair[0])
    return [item for _, item in decorated]


def _stable_index(seed_text: str, *, salt: str, size: int) -> int:
    if size <= 0:
        return 0
    digest = hashlib.sha256(f"{seed_text}|{salt}".encode()).hexdigest()
    return int(digest[:12], 16) % size


def _project_name_seed(project: ProjectModel, premise: str = "") -> str:
    return _seed_material(
        getattr(project, "slug", ""),
        getattr(project, "title", ""),
        getattr(project, "genre", ""),
        getattr(project, "sub_genre", ""),
        getattr(project, "language", ""),
        getattr(project, "id", ""),
        premise[:160],
    )


def _role_label(role: str, *, language: str | None = None, index: int = 0) -> str:
    """Return a generic role *placeholder* for the given role.

    Emergency fallback for when the LLM fails to produce character names. These
    are intentionally NOT plausible proper names: baking real-looking names here
    made the same handful of names (沈远/顾铭/Nora Chen/Victor Hale …) recur
    across unrelated books. A neutral placeholder both avoids cross-book
    homogenisation and signals that the naming pipeline fell through and needs
    attention rather than silently shipping a baked name.
    """
    if is_english_language(language):
        if role == "protagonist":
            return "the protagonist"
        if role == "ally":
            return f"Ally {index + 1}"
        if role == "antagonist":
            return "the antagonist"
        if role == "local_threat":
            return "the local threat"
        if role == "betrayer":
            return "the betrayer"
        return "the character"
    if role == "protagonist":
        return "主角"
    if role == "ally":
        return f"盟友{index + 1}"
    if role == "antagonist":
        return "反派"
    if role == "local_threat":
        return "地方势力"
    if role == "betrayer":
        return "背叛者"
    return "角色"


def _generic_name_bundle(language: str | None = None) -> dict[str, Any]:
    protagonist_name = _role_label("protagonist", language=language)
    ally_names = [
        _role_label("ally", language=language, index=0),
        _role_label("ally", language=language, index=1),
    ]
    antagonist_name = _role_label("antagonist", language=language)
    if is_english_language(language):
        return {
            "protagonist": {
                "name": protagonist_name,
                "name_reasoning": "Leave the concrete naming decision to the model unless it explicitly fails to provide one.",
            },
            "allies": [
                {
                    "name": ally_names[0],
                    "name_reasoning": "Neutral role label used only when no proper cast name is generated.",
                },
                {
                    "name": ally_names[1],
                    "name_reasoning": "Neutral role label used only when no proper cast name is generated.",
                },
            ],
            "antagonists": [
                {
                    "name": antagonist_name,
                    "name_reasoning": "Neutral role label used only when no proper cast name is generated.",
                },
            ],
        }
    return {
        "protagonist": {
            "name": protagonist_name,
            "name_reasoning": "仅在模型没有给出有效姓名时使用的中性角色标签。",
        },
        "allies": [
            {
                "name": ally_names[0],
                "name_reasoning": "仅在模型没有给出有效姓名时使用的中性角色标签。",
            },
            {
                "name": ally_names[1],
                "name_reasoning": "仅在模型没有给出有效姓名时使用的中性角色标签。",
            },
        ],
        "antagonists": [
            {
                "name": antagonist_name,
                "name_reasoning": "仅在模型没有给出有效姓名时使用的中性角色标签。",
            },
        ],
    }


def _genre_name_pool(
    genre: str,
    language: str | None = None,
    *,
    seed_text: str | None = None,
) -> dict[str, Any]:
    """Neutral emergency fallback when the model fails to produce names."""
    return _generic_name_bundle(language=language)


def _genre_profile(
    genre: str, *, category_key: str | None = None, language: str | None = None
) -> dict[str, Any]:
    # Try category-specific profile derivation first
    if category_key:
        cat = get_novel_category(category_key)
        if cat and cat.challenge_evolution_pathway:
            return _derive_genre_profile_from_category(cat)

    # Legacy 3-bucket fallback
    normalized = genre.lower()
    if any(token in normalized for token in ("sci", "space", "科幻", "science")):
        if is_english_language(language):
            return {
                "tones": ["tense", "bleak", "suspenseful"],
                "themes": ["truth vs. control", "order vs. freedom", "the cost of sacrifice"],
                "world_name": "Frontier Corridor Network",
                "world_premise": "Routes, records, and clearances determine who controls the narrative of reality.",
                "power_system_name": "Authority Sigil",
                "locations": ["Border Station", "Silent Corridor", "Deep Archive"],
                "factions": ["Imperial Archive Bureau", "Free Pilots Guild"],
            }
        return {
            "tones": ["紧张", "冷峻", "悬疑"],
            "themes": ["真相与控制", "秩序与自由", "牺牲的代价"],
            "world_name": "边境航道网络",
            "world_premise": "航线、记录与权限共同决定谁拥有对现实的解释权。",
            "power_system_name": "权限印记",
            "locations": ["边境星港", "静默航道", "底层日志库"],
            "factions": ["帝国档案局", "边境自由引航团"],
        }
    if any(token in normalized for token in ("fantasy", "玄幻", "仙", "magic")):
        if is_english_language(language):
            return {
                "tones": ["high-stakes", "wondrous", "intense"],
                "themes": ["power and its cost", "order vs. rebellion", "growth and betrayal"],
                "world_name": "The Fractured Realm",
                "world_premise": "World resources and the power hierarchy are monopolized by a handful of sects.",
                "power_system_name": "Spirit Sigil System",
                "locations": ["Borderlands City", "Rift Valley", "Ancestral Grounds"],
                "factions": ["Ward-Keeper Sect", "Black Market Alliance"],
            }
        return {
            "tones": ["高压", "奇诡", "燃"],
            "themes": ["力量与代价", "秩序与反抗", "成长与背叛"],
            "world_name": "裂界王朝",
            "world_premise": "世界资源和力量阶梯由少数宗门垄断。",
            "power_system_name": "灵印体系",
            "locations": ["边荒城", "秘境裂谷", "王朝祖地"],
            "factions": ["镇界宗", "黑市同盟"],
        }
    if is_english_language(language):
        return {
            "tones": ["tense", "restrained", "driven"],
            "themes": ["identity and choice", "trust and its price", "truth and lies"],
            "world_name": "Stormgate City",
            "world_premise": "Power and secrets together shape everyone's fate.",
            "power_system_name": "Momentum Ladder",
            "locations": ["Central City", "Restricted Zone", "Old Archive"],
            "factions": ["Governing Authority", "Underground Alliance"],
        }
    return {
        "tones": ["紧张", "克制", "推进感"],
        "themes": ["身份与选择", "信任与代价", "真相与谎言"],
        "world_name": "风暴边城",
        "world_premise": "权力与秘密共同塑造每个人的命运。",
        "power_system_name": "势能阶梯",
        "locations": ["主城", "禁区", "旧档案馆"],
        "factions": ["统治机关", "地下同盟"],
    }


def _profile_seed_list(
    value: Any,
    defaults: list[str],
    *,
    min_items: int,
) -> list[str]:
    values = list(dict.fromkeys(_string_list(value)))
    for default in defaults:
        if default and default not in values:
            values.append(default)
        if len(values) >= min_items:
            break
    while len(values) < min_items:
        stem = defaults[-1] if defaults else "场域"
        values.append(f"{stem}{len(values) + 1}")
    return values


def _derive_genre_profile_from_category(cat: NovelCategoryResearch) -> dict[str, Any]:
    """Derive a genre profile dict from category research data.

    Extracts tones/themes from the reader promise and phase descriptions,
    and world building seeds from world rule templates.
    """
    # Extract themes from challenge evolution phase descriptions
    phases = cat.challenge_evolution_pathway
    themes: list[str] = []
    for phase in phases[:3]:
        desc = phase.description_zh
        if desc and len(desc) > 4:
            # Trim to a pithy theme-like phrase
            themes.append(desc[:20].rstrip("。，、"))
    if not themes:
        themes = ["身份与选择", "信任与代价", "真相与谎言"]

    # Extract world seeds from world rule templates
    rules = cat.world_rule_templates
    world_name = rules[0].name_zh if rules else "未命名世界"
    world_premise = rules[0].description_zh if rules else "世界运行规则尚未确定。"
    power_system_name = rules[1].name_zh if len(rules) > 1 else "核心体系"
    locations = _profile_seed_list(
        [r.name_zh for r in rules[:3]],
        ["主城", "禁区", "旧档案馆"],
        min_items=3,
    )
    factions = ["统治方", "挑战方"]  # Generic — LLM will override

    # Derive tones from category signal keywords
    signal_kws = cat.signal_keywords.get("narrative_zh", [])
    tones = signal_kws[:3] if signal_kws else ["紧张", "克制", "推进感"]

    return {
        "tones": tones,
        "themes": themes,
        "world_name": world_name,
        "world_premise": world_premise,
        "power_system_name": power_system_name,
        "locations": locations,
        "factions": factions,
    }


def _supplemental_world_rule(
    rule_index: int,
    *,
    is_en: bool,
    protagonist_name: str,
) -> dict[str, Any]:
    if is_en:
        templates = {
            2: {
                "name": "Access Threshold Rule",
                "description": "Important spaces, people, and resources sit behind permissions, status, or gatekeepers.",
                "story_consequence": "The protagonist must cross a visible threshold before any real progress becomes possible.",
                "exploitation_potential": "Thresholds create bottlenecks, and bottlenecks create routines that can be studied or broken.",
            },
            3: {
                "name": "Isolation Zone Rule",
                "description": "Once the story moves into the key danger zone, outside support becomes unreliable.",
                "story_consequence": "Breakthroughs and reversals have to happen under pressure, without guaranteed rescue.",
                "exploitation_potential": "The same isolation that traps the protagonist also weakens the opponent's direct control.",
            },
        }
        fallback = {
            "name": f"Constraint Rule {rule_index}",
            "description": "A visible constraint shapes access, risk, and consequence.",
            "story_consequence": f"{protagonist_name} must turn the constraint into leverage instead of ignoring it.",
            "exploitation_potential": "Every constraint creates a repeatable pressure point.",
        }
    else:
        templates = {
            2: {
                "name": "门槛通行规则",
                "description": "关键地点、关键人物与关键资源，都被权限、身份或中间人把守。",
                "story_consequence": "主角必须跨过一个明确门槛，主线才可能真正推进。",
                "exploitation_potential": "门槛会形成固定流程，是冲突与博弈最容易聚焦的地方。",
            },
            3: {
                "name": "禁区隔绝规则",
                "description": "一旦进入关键危险区域，外部支援会变得不可靠。",
                "story_consequence": "突破和反转必须在压力下完成，不能依赖稳定救援。",
                "exploitation_potential": "隔绝既困住主角，也削弱对手的直接控制。",
            },
        }
        # 2026-06-25 去通用性污染：去掉"撬开/反向利用"作为默认约束打法的措辞。
        fallback = {
            "name": f"约束规则{rule_index}",
            "description": "一条明确约束持续改变进入、风险与后果。",
            "story_consequence": f"{protagonist_name}必须把约束转化为筹码，而不是绕开它。",
            "exploitation_potential": "约束越稳定，越容易成为情节聚焦的支点。",
        }
    return {
        "rule_id": f"R{rule_index:03d}",
        **templates.get(rule_index, fallback),
    }


def _world_template(
    genre: str,
    *,
    language: str | None,
    protagonist_name: str,
    seed_text: str,
    category_key: str | None = None,
) -> dict[str, Any]:
    is_en = is_english_language(language)

    # Try category-specific world rules first
    if category_key:
        cat = get_novel_category(category_key)
        if cat and cat.world_rule_templates:
            rules = []
            for idx, wrt in enumerate(cat.world_rule_templates, start=1):
                rules.append(
                    {
                        "rule_id": f"R{idx:03d}",
                        "name": wrt.name_en if is_en else wrt.name_zh,
                        "description": wrt.description_en if is_en else wrt.description_zh,
                        "story_consequence": (
                            (wrt.story_consequence_en or wrt.story_consequence_zh)
                            if is_en
                            else wrt.story_consequence_zh
                        ).replace("{protagonist}", protagonist_name),
                        "exploitation_potential": (
                            (wrt.exploitation_potential_en or wrt.exploitation_potential_zh)
                            if is_en
                            else wrt.exploitation_potential_zh
                        ),
                    }
                )
            while len(rules) < 3:
                rules.append(
                    _supplemental_world_rule(
                        len(rules) + 1,
                        is_en=is_en,
                        protagonist_name=protagonist_name,
                    )
                )
            return {
                "rules": rules,
                "power_structure": (
                    "Power concentrates in the hands of whoever controls access, narrative framing, and the distribution of risk."
                    if is_en
                    else "解释权、进入权和分配权握在少数人手里，其他人只能在缝隙里争取主动。"
                ),
                "forbidden_zones": (
                    "Any sealed archive, core facility, ancestral site, or hidden meeting place becomes a pressure point once the protagonist gets close."
                    if is_en
                    else "任何被封存、被隔绝、被严密看守的地点，一旦靠近就会立刻放大冲突。"
                ),
                "history_event": (
                    f"{protagonist_name} once paid the price for a key incident whose official version never matched the truth."
                    if is_en
                    else f"{protagonist_name}曾在一场关键事件里承担过与真相并不匹配的代价。"
                ),
            }

    if is_en:
        return {
            "rules": [
                {
                    "rule_id": "R001",
                    "name": "Core Order Rule",
                    "description": "An official order determines who gets access to truth, protection, and resources.",
                    "story_consequence": f"{protagonist_name} cannot rely on emotion alone and must secure evidence or leverage strong enough to move the system.",
                    "exploitation_potential": "Every official order leaves traces, gaps, or witnesses that can be turned back against it.",
                },
                {
                    "rule_id": "R002",
                    "name": "Access Threshold Rule",
                    "description": "Important spaces, people, and resources sit behind permissions, status, or gatekeepers.",
                    "story_consequence": "The protagonist must cross a visible threshold before any real progress becomes possible.",
                    "exploitation_potential": "Thresholds create bottlenecks, and bottlenecks create routines that can be studied or broken.",
                },
                {
                    "rule_id": "R003",
                    "name": "Isolation Zone Rule",
                    "description": "Once the story moves into the key danger zone, outside support becomes unreliable.",
                    "story_consequence": "Breakthroughs and reversals have to happen under pressure, without guaranteed rescue.",
                    "exploitation_potential": "The same isolation that traps the protagonist also weakens the opponent's direct control.",
                },
            ],
            "power_structure": "Power concentrates in the hands of whoever controls access, narrative framing, and the distribution of risk.",
            "forbidden_zones": "Any sealed archive, core facility, ancestral site, or hidden meeting place becomes a pressure point once the protagonist gets close.",
            "history_event": f"{protagonist_name} once paid the price for a key incident whose official version never matched the truth.",
        }
    # 2026-06-25 去通用性污染：无品类模板时的兜底世界规则原来一律是"秩序-证据-漏洞-
    # 反向利用-撬开"的智斗/规则-exploitation框架(钻漏洞偏置从主角层躲到了世界规则层)，
    # 套给每个题材。改为题材中性的结构约束(秩序/门槛/隔绝)，exploitation_potential 只
    # 描述"叙事支点"，不预设"找漏洞/反向利用/撬开"作为默认打法。
    return {
        "rules": [
            {
                "rule_id": "R001",
                "name": "核心秩序规则",
                "description": "某套被广泛承认的秩序决定谁能拿到力量、资源与保护。",
                "story_consequence": f"{protagonist_name}不能只靠情绪或直觉推进，必须拿到足以撬动局面的实力、筹码或资格。",
                "exploitation_potential": "秩序越明确，它的边界、代价与例外就越清晰，越能成为推动情节的支点（具体怎么用由本书主角决定，不限于钻漏洞）。",
            },
            {
                "rule_id": "R002",
                "name": "门槛通行规则",
                "description": "关键地点、关键人物与关键资源，都被权限、身份或中间人把守。",
                "story_consequence": "主角必须跨过一个明确门槛，主线才可能真正推进。",
                "exploitation_potential": "门槛会形成固定流程，是冲突与博弈最容易聚焦的地方。",
            },
            {
                "rule_id": "R003",
                "name": "禁区隔绝规则",
                "description": "一旦进入关键危险区，外部支援、常规沟通和安全退路都会快速变得不可靠。",
                "story_consequence": "真正的突破与反转只能在高压、孤立的环境里完成。",
                "exploitation_potential": "隔绝不只困住主角，也会削弱对手的即时控制与调度能力。",
            },
        ],
        "power_structure": "解释权、进入权和分配权握在少数人手里，其他人只能在缝隙里争取主动。",
        "forbidden_zones": "任何被封存、被隔绝、被严密看守的地点，一旦靠近就会立刻放大冲突。",
        "history_event": f"{protagonist_name}曾在一场关键事件里承担过与代价并不匹配的损失。",
    }


def _planner_writing_profile(project: ProjectModel) -> Any:
    raw = (
        project.metadata_json.get("writing_profile")
        if isinstance(project.metadata_json, dict)
        else None
    )
    return resolve_writing_profile(
        raw,
        genre=project.genre,
        sub_genre=project.sub_genre,
        audience=project.audience,
        language=project.language,
    )


def _planner_language(project: ProjectModel) -> str:
    return str(project.language or "zh-CN")


async def _build_revealed_ledger_block(
    session: AsyncSession,
    project_id: UUID,
    *,
    language: str = "zh-CN",
) -> str | None:
    """Wrap :func:`build_revealed_ledger` for use inside planner pipelines.

    Returns ``None`` when the ledger is empty or the build raises — the
    planner then falls back to running with no ledger block rather than
    aborting. This keeps the ledger a best-effort augmentation and never
    a hard dependency of the planning flow.
    """
    try:
        from bestseller.services.revealed_ledger import build_revealed_ledger

        ledger = await build_revealed_ledger(session, project_id)
        if ledger.is_empty:
            return None
        block = ledger.to_prompt_block(language=language)
        return block or None
    except Exception:
        logger.debug(
            "Revealed-ledger build failed for project %s (non-fatal)",
            project_id,
            exc_info=True,
        )
        return None


async def _repair_volume_plan_convergence_if_needed(
    *,
    session: AsyncSession,
    settings: Any,
    project: ProjectModel,
    book_spec_payload: dict[str, Any],
    world_spec_payload: dict[str, Any],
    cast_spec_payload: dict[str, Any],
    act_plan_payload: Any,
    volume_plan_payload: Any,
    workflow_run_id: UUID,
) -> tuple[Any, UUID | None]:
    """Scan a VolumePlan for cross-volume convergence and auto-repair once
    if criticals are found.

    Returns ``(possibly-repaired payload, repair LLM run id or None)``.
    Failures (validation, LLM) fall back to the original payload. This is
    a best-effort guardrail, not a blocking gate — we do not want a
    convergence scanner misfire to abort the whole planning pipeline.
    """
    if not isinstance(volume_plan_payload, list) or len(volume_plan_payload) < 2:
        return volume_plan_payload, None
    try:
        from bestseller.services.volume_fingerprint import (
            scan_volume_plan_for_convergence,
        )

        report = scan_volume_plan_for_convergence(volume_plan_payload)
    except Exception:
        logger.debug("Volume convergence scan failed (non-fatal)", exc_info=True)
        return volume_plan_payload, None

    # Attach findings to workflow metadata for observability.
    try:
        from bestseller.infra.db.models import WorkflowRunModel as _WR

        wr = await session.scalar(select(_WR).where(_WR.id == workflow_run_id))
        if wr is not None:
            findings_summary = [
                {
                    "volume_a": f.volume_a,
                    "volume_b": f.volume_b,
                    "similarity": round(f.similarity, 3),
                    "severity": f.severity,
                    "reason": f.reason,
                }
                for f in report.findings[:20]
            ]
            wr.metadata_json = {
                **(wr.metadata_json or {}),
                "volume_convergence_findings": findings_summary,
                "volume_convergence_has_critical": report.has_critical,
                "volume_conflict_phase_counts": dict(report.conflict_phase_counts),
                "volume_force_name_counts": dict(report.force_name_counts),
            }
    except Exception:
        logger.debug("Workflow-metadata attachment failed (non-fatal)", exc_info=True)

    if not report.has_critical:
        if report.findings:
            logger.info(
                "Volume convergence: %d warning-level finding(s); continuing without repair.",
                len(report.findings),
            )
        return volume_plan_payload, None

    logger.warning(
        "Volume convergence critical: %d pair(s); attempting single repair pass.",
        len(report.critical_findings),
    )

    try:
        language = _planner_language(project)
        repair_block = report.to_prompt_block(language=language)
        repair_system, repair_user = _volume_plan_prompts(
            project,
            book_spec_payload,
            world_spec_payload,
            cast_spec_payload,
            act_plan=act_plan_payload,
        )
        is_en = is_english_language(language)
        header = (
            "\n\n[Volume convergence — repair the converged volumes]"
            if is_en
            else "\n\n【卷间趋同 — 请重新生成以消除以下问题】"
        )
        repair_user += f"{header}\n{repair_block}\n"
        if is_en:
            repair_user += (
                "Regenerate the entire VolumePlan JSON array. Each flagged pair of "
                "volumes must diverge on conflict_phase, primary_force_name, climax "
                "shape, and core payoff class. Preserve volume_number ordering."
            )
        else:
            repair_user += (
                "请重新生成整份 VolumePlan JSON 数组："
                "每一对被标记的卷都必须在 conflict_phase、primary_force_name、"
                "climax 形态、core payoff 类别 四个维度上都形成差异；"
                "保持 volume_number 的顺序。"
            )

        repaired_payload, repair_llm_run_id = await _generate_structured_artifact(
            session,
            settings,
            project=project,
            logical_name="volume_plan_convergence_repair",
            system_prompt=repair_system,
            user_prompt=repair_user,
            fallback_payload=volume_plan_payload,
            workflow_run_id=workflow_run_id,
            validator=parse_volume_plan_input,
        )
        if not isinstance(repaired_payload, list) or len(repaired_payload) < 2:
            return volume_plan_payload, None
        return repaired_payload, repair_llm_run_id
    except Exception:
        logger.warning("Volume convergence repair failed; keeping original plan.", exc_info=True)
        return volume_plan_payload, None


async def _repair_cast_foundation_if_needed(
    *,
    session: AsyncSession,
    settings: Any,
    project: ProjectModel,
    book_spec_payload: dict[str, Any],
    world_spec_payload: dict[str, Any],
    cast_spec_payload: dict[str, Any],
    volume_count: int,
    workflow_run_id: UUID,
) -> tuple[dict[str, Any], UUID | None]:
    """Scan cast spec foundational richness; auto-repair once if critical.

    The pre-volume gate that prevents the xianxia failure mode: when the
    cast spec ships only one antagonist or a force roster whose
    ``active_volumes`` union doesn't cover the planned volumes, every
    downstream volume collapses onto the same primary_force_name. This
    helper detects that up front and asks the LLM to regenerate just the
    ``antagonist_forces`` + ``supporting_cast`` fields with concrete
    coverage requirements attached to the prompt.

    Returns ``(possibly-repaired cast_spec_payload, repair LLM run id or
    None)``. Any failure falls back to the original cast spec; this is a
    best-effort guardrail, not a blocking gate.
    """
    try:
        from bestseller.services.foundation_richness import (
            scan_cast_foundation_richness,
        )

        report = scan_cast_foundation_richness(
            cast_spec_payload,
            volume_count=volume_count,
            language=_planner_language(project),
        )
    except Exception:
        logger.debug("Foundation-richness scan failed (non-fatal)", exc_info=True)
        return cast_spec_payload, None

    # Attach findings to workflow metadata for observability.
    try:
        from bestseller.infra.db.models import WorkflowRunModel as _WR

        wr = await session.scalar(select(_WR).where(_WR.id == workflow_run_id))
        if wr is not None:
            wr.metadata_json = {
                **(wr.metadata_json or {}),
                "foundation_richness_findings": [
                    {
                        "code": f.code,
                        "severity": f.severity,
                        "message": f.message,
                    }
                    for f in report.findings[:20]
                ],
                "foundation_richness_critical": report.is_critical,
                "foundation_richness_force_count": report.force_count,
                "foundation_richness_forces_required": report.forces_required,
                "foundation_richness_coverage_ratio": round(report.coverage_ratio, 3),
            }
    except Exception:
        logger.debug("Workflow-metadata attachment failed (non-fatal)", exc_info=True)

    if not report.is_critical:
        if report.findings:
            logger.info(
                "Foundation richness: %d warning-level finding(s); continuing without repair.",
                len(report.findings),
            )
        return cast_spec_payload, None

    logger.warning(
        "Foundation richness critical (%d critical, %d warning); attempting single repair.",
        report.critical_count,
        report.warning_count,
    )

    try:
        language = _planner_language(project)
        is_en = is_english_language(language)
        repair_block = report.to_prompt_block(language=language)
        repair_system, repair_user = _cast_spec_prompts(
            project, book_spec_payload, world_spec_payload
        )
        header = (
            "\n\n[Foundation richness — repair the thin antagonist roster]"
            if is_en
            else "\n\n【基础素材丰富度 — 请修复过于单薄的反派势力与配角池】"
        )
        repair_user += f"{header}\n{repair_block}\n"
        if is_en:
            repair_user += (
                "\nRegenerate the ENTIRE CastSpec JSON. Keep the protagonist, "
                "antagonist, world tie-ins, and name_reasoning fields intact; "
                "rework antagonist_forces and supporting_cast to satisfy every "
                "constraint above. Do not narrow any existing field."
            )
        else:
            repair_user += (
                "\n请重新生成整份 CastSpec JSON：保留 protagonist、antagonist、"
                "世界观锚点、各角色的 name_reasoning 字段不变；"
                "重构 antagonist_forces 与 supporting_cast 两个字段，"
                "使之满足上面列出的所有约束；不要缩减任何已有字段。"
            )

        repaired_payload, repair_llm_run_id = await _generate_structured_artifact(
            session,
            settings,
            project=project,
            logical_name="cast_spec_foundation_repair",
            system_prompt=repair_system,
            user_prompt=repair_user,
            fallback_payload=cast_spec_payload,
            workflow_run_id=workflow_run_id,
            validator=parse_cast_spec_input,
        )
        if not isinstance(repaired_payload, dict):
            return cast_spec_payload, None

        # Sanity-check the repair before accepting it — reject if the
        # repair regressed on force count (LLM sometimes misinterprets
        # the instruction and returns a smaller list).
        try:
            repaired_report = scan_cast_foundation_richness(
                repaired_payload,
                volume_count=volume_count,
                language=language,
            )
            if repaired_report.force_count < report.force_count:
                logger.warning(
                    "Cast repair regressed force count (%d → %d); keeping original.",
                    report.force_count,
                    repaired_report.force_count,
                )
                return cast_spec_payload, None
        except Exception:
            pass

        return repaired_payload, repair_llm_run_id
    except Exception:
        logger.warning("Cast foundation repair failed; keeping original cast spec.", exc_info=True)
        return cast_spec_payload, None


def _repair_cast_identity_locks_for_planner(
    project: ProjectModel,
    cast_spec_payload: dict[str, Any],
) -> dict[str, Any]:
    """Ensure planner-produced CastSpec satisfies the foundation identity contract."""

    if not getattr(get_settings().pipeline, "require_foundation_identity_lock", True):
        return cast_spec_payload
    from bestseller.services.narrative_contracts import (
        repair_legacy_foundation_identity_locks,
        validate_foundation_identity_contract,
    )

    repaired_payload, repair_count = repair_legacy_foundation_identity_locks(
        cast_spec_payload,
        allow_unreliable_defaults=True,
    )
    if not isinstance(repaired_payload, dict):
        return cast_spec_payload
    repaired_payload, commercial_repair_count = repair_commercial_zh_identity_policy(
        repaired_payload,
        language=getattr(project, "language", None) or "zh-CN",
        premise_text=_cast_identity_policy_premise(project),
    )
    repair_count += int(commercial_repair_count or 0)
    if not isinstance(repaired_payload, dict):
        return cast_spec_payload
    report = validate_foundation_identity_contract(repaired_payload)
    report.raise_for_blocks(project_slug=project.slug, artifact="cast_spec")
    commercial_report = validate_commercial_zh_identity_policy(
        repaired_payload,
        language=getattr(project, "language", None) or "zh-CN",
        premise_text=_cast_identity_policy_premise(project),
    )
    commercial_report.raise_for_blocks(project_slug=project.slug, artifact="cast_spec")
    if repair_count:
        logger.warning(
            "Planner CastSpec identity locks auto-repaired for project '%s' (%d field(s)).",
            project.slug,
            repair_count,
        )
    normalized_payload = parse_cast_spec_input(repaired_payload).model_dump(mode="json")
    prior_meta = _mapping(repaired_payload.get("_meta"))
    prior_policy_versions = _mapping(prior_meta.get("policy_versions"))
    normalized_payload["_meta"] = {
        **prior_meta,
        "policy_versions": {
            **prior_policy_versions,
            "identity_policy": IDENTITY_POLICY_VERSION,
        },
    }
    return normalized_payload


def _protagonist_name_from_payload(payload: dict[str, Any]) -> str:
    protagonist = _mapping(payload.get("protagonist"))
    return _first_non_empty_text(protagonist.get("name"), default="")


def _planner_forbidden_protagonist_names(
    project: ProjectModel,
    *,
    protagonist_name: str,
) -> set[str]:
    metadata = _mapping(getattr(project, "metadata_json", None))
    forbidden: set[str] = {
        _role_label("protagonist", language=getattr(project, "language", None)),
        _role_label("protagonist", language="zh-CN"),
        _role_label("protagonist", language="en"),
    }
    raw_forbidden = metadata.get("protagonist_forbidden_names")
    if isinstance(raw_forbidden, (list, tuple, set)):
        forbidden.update(str(item).strip() for item in raw_forbidden if str(item).strip())
    forbidden.discard("")
    forbidden.discard(protagonist_name)
    # Allowlisted public-domain mythological names must never be drift-repaired.
    forbidden.difference_update(_public_domain_mythology_allowlist(project))
    return forbidden


def _replace_name_drift_in_value(value: Any, *, forbidden_names: set[str], replacement: str) -> Any:
    if not forbidden_names or not replacement:
        return value
    if isinstance(value, str):
        repaired = value
        for forbidden in sorted(forbidden_names, key=len, reverse=True):
            repaired = repaired.replace(forbidden, replacement)
        return repaired
    if isinstance(value, list):
        return [
            _replace_name_drift_in_value(
                item,
                forbidden_names=forbidden_names,
                replacement=replacement,
            )
            for item in value
        ]
    if isinstance(value, dict):
        return {
            key: _replace_name_drift_in_value(
                item,
                forbidden_names=forbidden_names,
                replacement=replacement,
            )
            for key, item in value.items()
        }
    return value


def _count_name_drift_occurrences(value: Any, *, forbidden_names: set[str]) -> int:
    if not forbidden_names:
        return 0
    if isinstance(value, str):
        return sum(value.count(forbidden) for forbidden in forbidden_names)
    if isinstance(value, list):
        return sum(_count_name_drift_occurrences(item, forbidden_names=forbidden_names) for item in value)
    if isinstance(value, dict):
        return sum(_count_name_drift_occurrences(item, forbidden_names=forbidden_names) for item in value.values())
    return 0


def _repair_protagonist_name_drift_for_planner(
    project: ProjectModel,
    payload: dict[str, Any],
    *,
    protagonist_name: str | None = None,
    artifact_type: str,
) -> dict[str, Any]:
    """Remove default protagonist-name drift before planning artifacts feed prompts."""

    if not isinstance(payload, dict):
        return payload
    locked_name = _first_non_empty_text(
        protagonist_name,
        _protagonist_name_from_payload(payload),
        _mapping(getattr(project, "metadata_json", None)).get("protagonist_name"),
        default="",
    )
    if not locked_name:
        return payload
    forbidden_names = _planner_forbidden_protagonist_names(
        project,
        protagonist_name=locked_name,
    )
    drift_count = _count_name_drift_occurrences(payload, forbidden_names=forbidden_names)
    if drift_count <= 0:
        return payload
    repaired = _replace_name_drift_in_value(
        copy.deepcopy(payload),
        forbidden_names=forbidden_names,
        replacement=locked_name,
    )
    meta = _mapping(repaired.get("_meta"))
    repairs = list(meta.get("name_drift_repair") or [])
    repairs.append(
        {
            "artifact_type": artifact_type,
            "protagonist_name": locked_name,
            "forbidden_name_count": len(forbidden_names),
            "replacement_count": drift_count,
        }
    )
    repaired["_meta"] = {**meta, "name_drift_repair": repairs}
    logger.warning(
        "Planner %s protagonist name drift auto-repaired for project '%s' (%d occurrence(s)).",
        artifact_type,
        project.slug,
        drift_count,
    )
    return repaired


def _looks_like_rule_survival_project(project: ProjectModel, premise: str = "") -> bool:
    haystack = " ".join(
        str(part or "")
        for part in (
            getattr(project, "genre", ""),
            getattr(project, "sub_genre", ""),
            getattr(project, "title", ""),
            premise,
        )
    ).lower()
    return any(
        marker in haystack
        for marker in (
            "规则生存",
            "规则怪谈",
            "规则审查",
            "meta博弈",
            "元叙事",
            "免责剧本",
            "副本规则",
        )
    )


def _generic_power_progression_text(value: Any) -> bool:
    text = str(value or "")
    return any(
        marker in text
        for marker in (
            "不断变强",
            "获取足够的力量",
            "力量不足",
            "保护想保护",
            "强大不等于正确",
            "求力者",
        )
    )


def _rule_survival_goal_contract(protagonist_name: str) -> dict[str, str]:
    subject = protagonist_name or "主角"
    return {
        "archetype": "规则破局者",
        "core_wound": f"{subject}曾因相信官方规则流程而背负停职与同伴受害的代价。",
        "external_goal": (
            f"{subject}必须在六章内利用规则之间的矛盾破局，"
            "证明通关提示是幕后审判者写给自己的免责剧本。"
        ),
        "internal_need": f"{subject}需要从服从规则审查程序，转向判断规则背后的责任与牺牲。",
        "golden_finger": "能捕捉规则改写前后的矛盾痕迹。",
    }


def _repair_rule_survival_goal_drift_for_planner(
    project: ProjectModel,
    payload: dict[str, Any],
    *,
    premise: str,
    artifact_type: str,
) -> dict[str, Any]:
    if not isinstance(payload, dict) or not _looks_like_rule_survival_project(project, premise):
        return payload
    protagonist_name = _protagonist_name_from_payload(payload) or _first_non_empty_text(
        _mapping(getattr(project, "metadata_json", None)).get("protagonist_name"),
        default="",
    )
    if not protagonist_name:
        return payload
    contract = _rule_survival_goal_contract(protagonist_name)
    repaired = copy.deepcopy(payload)
    target = repaired.setdefault("protagonist", {})
    if not isinstance(target, dict):
        return payload
    changed_fields: list[str] = []
    for field in ("archetype", "core_wound", "external_goal", "internal_need"):
        current = target.get(field)
        if not current or _generic_power_progression_text(current):
            target[field] = contract[field]
            changed_fields.append(field)
    goal = target.get("goal")
    if not goal or _generic_power_progression_text(goal):
        target["goal"] = contract["external_goal"]
        changed_fields.append("goal")
    if not target.get("golden_finger"):
        target["golden_finger"] = contract["golden_finger"]
        changed_fields.append("golden_finger")
    if not changed_fields:
        return payload
    meta = _mapping(repaired.get("_meta"))
    repairs = list(meta.get("rule_survival_goal_repair") or [])
    repairs.append(
        {
            "artifact_type": artifact_type,
            "protagonist_name": protagonist_name,
            "fields": sorted(set(changed_fields)),
        }
    )
    repaired["_meta"] = {**meta, "rule_survival_goal_repair": repairs}
    logger.warning(
        "Planner %s rule-survival goal drift auto-repaired for project '%s' (%s).",
        artifact_type,
        project.slug,
        ", ".join(sorted(set(changed_fields))),
    )
    return repaired


_CAST_PERSONHOOD_REPAIR_CODES: frozenset[str] = frozenset(
    {
        "CHARACTER_IP_ANCHOR_MISSING",
        "CORE_WOUND_MISSING",
        "TAG_MEMORY_MISSING",
        "INDEPENDENT_LIFE_MISSING",
        "CHARACTER_CONTRAST_MISSING",
        "CHARACTER_PERSONHOOD_INCOMPLETE",
        "VILLAIN_CHARISMA_MISSING",
        "ANTAGONIST_MOTIVE_OVERLAP",
        "ABILITY_ORIGIN_CONTRACT_MISSING",
    }
)


_ABILITY_ORIGIN_CONTRACT_FIELDS: tuple[str, ...] = (
    "source",
    "visible_signature",
    "limit",
    "cost",
    "growth_trigger",
    "plot_use",
)

_POWER_PROGRESSION_TOKENS: tuple[str, ...] = (
    "xianxia",
    "cultivation",
    "progression",
    "litrpg",
    "system",
    "power",
    "ability",
    "skill",
    "level",
    "upgrade",
    "玄幻",
    "修仙",
    "仙侠",
    "升级",
    "异能",
    "系统",
    "金手指",
    "修炼",
    "技能",
    "境界",
    "灵气",
    "血脉",
)


def _truthy_values(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, dict):
        return any(_truthy_values(item) for item in value.values())
    if isinstance(value, (list, tuple, set)):
        return any(_truthy_values(item) for item in value)
    return True


def _role_requires_ip_anchor(role: str) -> int:
    role_lower = role.lower()
    if "protagonist" in role_lower:
        return 3
    if "antagonist" in role_lower:
        return 2
    return 0


def _short_character_basis(character: dict[str, Any], *, is_en: bool) -> str:
    for key in ("fear", "secret", "goal", "background", "flaw"):
        value = character.get(key)
        if isinstance(value, str) and value.strip():
            text = value.strip()
            return text[:120]
    return "the central wound behind the story" if is_en else "故事核心伤口"


def _ensure_min_strings(items: Any, additions: list[str], minimum: int) -> list[str]:
    current = _string_list(items)
    for addition in additions:
        if len(current) >= minimum:
            break
        if addition and addition not in current:
            current.append(addition)
    return current


def _synthesized_quirks(
    character: dict[str, Any],
    *,
    required: int,
    is_en: bool,
) -> list[str]:
    name = _non_empty_string(character.get("name"), "the character" if is_en else "角色")
    basis = _short_character_basis(character, is_en=is_en)
    if is_en:
        candidates = [
            f"{name} checks exits before answering difficult questions.",
            f"{name} touches a worn personal object when {basis} is mentioned.",
            f"{name} repeats the last factual detail aloud before making a risky choice.",
            f"{name} keeps their voice controlled until someone threatens an innocent.",
        ]
    else:
        candidates = [
            f"{name}进入陌生空间会先确认退路和窗位。",
            f"提到「{basis}」相关线索时，{name}会下意识停顿半拍。",
            f"{name}做危险决定前会把最后一个事实低声复述一遍。",
            f"一旦有人威胁无辜者，{name}说话会突然压低到近乎无声。",
        ]
    return _ensure_min_strings(
        _mapping(character.get("ip_anchor")).get("quirks"),
        candidates,
        required,
    )


def _synthesized_tag_memory(name: str, basis: str, *, is_en: bool) -> str:
    if is_en:
        return f"When {basis} comes up, {name} taps one knuckle twice before speaking."
    return f"一提到「{basis}」，{name}会先用指节轻敲两下再开口。"


def _synthesized_independent_life(name: str, basis: str, *, is_en: bool) -> str:
    if is_en:
        return (
            f"Before being pulled into the main plot, {name} kept a separate obligation "
            f"that still interrupts them whenever {basis} escalates."
        )
    return f"被卷入主线前，{name}还有一桩自己的日常牵挂；每当「{basis}」升级，这件事都会打断ta。"


def _project_and_character_power_text(
    project: ProjectModel | None,
    character: dict[str, Any],
) -> str:
    parts: list[str] = []
    if project is not None:
        for attr in ("genre", "sub_genre", "audience", "title"):
            value = getattr(project, attr, None)
            if isinstance(value, str) and value.strip():
                parts.append(value.strip())
        metadata = getattr(project, "metadata_json", None)
        if isinstance(metadata, dict):
            for key in (
                "category_key",
                "content_mode",
                "platform_key",
                "genre",
                "sub_genre",
                "trope",
                "writing_preset",
            ):
                value = metadata.get(key)
                if isinstance(value, str) and value.strip():
                    parts.append(value.strip())
    for key in (
        "golden_finger",
        "strength",
        "power_tier",
        "differentiated_advantage",
        "arc_trajectory",
        "background",
        "goal",
    ):
        value = character.get(key)
        if isinstance(value, str) and value.strip():
            parts.append(value.strip())
    return " ".join(parts)


def _requires_ability_origin_contract(
    project: ProjectModel | None,
    character: dict[str, Any],
) -> bool:
    text = _project_and_character_power_text(project, character).lower()
    return any(token in text for token in _POWER_PROGRESSION_TOKENS)


def _synthesize_ability_origin_contract(
    character: dict[str, Any],
    *,
    is_en: bool,
) -> dict[str, str]:
    name = _non_empty_string(character.get("name"), "the protagonist" if is_en else "主角")
    ability = _first_non_empty_text(
        character.get("golden_finger"),
        character.get("strength"),
        character.get("power_tier"),
        character.get("differentiated_advantage"),
        default="pressure-conversion ability" if is_en else "压力转化型能力",
    )
    if is_en:
        return {
            "source": (
                f"{ability} awakens when {name} is forced through sustained public "
                "pressure; "
                "it converts real emotional impact into limited usable leverage."
            ),
            "visible_signature": (
                f"When the ability activates, {name} goes still, touches an old "
                "personal mark, "
                "and reads the room's anger and fear as a low physical vibration."
            ),
            "limit": (
                "It cannot create power from nothing; it needs present, genuine "
                "emotional pressure, "
                "weakens against calm or isolated opponents, and becomes unstable under overload."
            ),
            "cost": (
                "Every use feeds other people's pain back into the protagonist, drains stamina, "
                "and tempts them to solve problems by provoking more suffering."
            ),
            "growth_trigger": (
                "Growth only unlocks when the protagonist fights back under real consequence "
                "while keeping a chosen moral line intact."
            ),
            "plot_use": (
                "The ability enables reversals but leaves a traceable emotional signature, "
                "drawing enemies closer and forcing choices between power, exposure, and restraint."
            ),
        }
    return {
        "source": (
            f"{ability}在{name}遭遇持续现实压迫时觉醒,"
            "把现场真实情绪冲击转化为有限的异能燃料。"
        ),
        "visible_signature": (
            f"能力启动时,{name}会瞬间安静下来,下意识触碰旧伤或随身旧物,"
            "并把周围愤怒与恐惧感知成低频震动。"
        ),
        "limit": (
            "它不能凭空制造力量,只能吸收现场真实且强烈的情绪;"
            "面对冷静、麻木或被隔离的人群效率大幅下降,连续过载会失控。"
        ),
        "cost": (
            f"吸收越多,{name}越会被他人的痛苦反噬,体力迅速消耗,"
            "愤怒和攻击性被放大,并不断侵蚀\"不伤无辜\"的底线。"
        ),
        "growth_trigger": (
            f"只有当{name}在现实压迫中主动反击、承担代价并守住底线时,"
            "能力才会解锁新用法或提高转化率。"
        ),
        "plot_use": (
            f"能力既让{name}完成打脸反击,也会留下可追踪的情绪波纹,"
            "引来权势方、警方或地下势力追查,迫使他在变强与暴露之间选择。"
        ),
    }


def _ensure_character_ability_origin_contract(
    repaired: dict[str, Any],
    *,
    project: ProjectModel | None,
    is_en: bool,
) -> None:
    if not _requires_ability_origin_contract(project, repaired):
        return

    metadata = copy.deepcopy(_mapping(repaired.get("metadata")))
    top_overlay = copy.deepcopy(_mapping(repaired.get("methodology_overlay")))
    metadata_overlay = copy.deepcopy(_mapping(metadata.get("methodology_overlay")))
    overlay = {**top_overlay, **metadata_overlay}
    top_ability = _mapping(top_overlay.get("ability_origin_contract"))
    metadata_ability = _mapping(metadata_overlay.get("ability_origin_contract"))
    ability = {**top_ability, **metadata_ability}
    synthesized = _synthesize_ability_origin_contract(repaired, is_en=is_en)
    for field in _ABILITY_ORIGIN_CONTRACT_FIELDS:
        if not _truthy_values(ability.get(field)):
            ability[field] = synthesized[field]
    overlay["ability_origin_contract"] = ability
    metadata["methodology_overlay"] = overlay
    repaired["metadata"] = metadata


_MOTIVE_STOPWORDS_ZH_EN = {
    "的",
    "了",
    "是",
    "在",
    "和",
    "与",
    "及",
    "对",
    "被",
    "因",
    "为",
    "要",
    "让",
    "the",
    "and",
    "to",
    "of",
    "a",
    "an",
    "for",
    "by",
    "in",
    "on",
    "with",
}


def _motive_keyword_bag(*values: Any) -> set[str]:
    text = " ".join(str(value) for value in values if value)
    tokens = re.findall(r"[\u4e00-\u9fffA-Za-z]+", text)
    return {
        token.lower()
        for token in tokens
        if token.lower() not in _MOTIVE_STOPWORDS_ZH_EN and len(token) > 1
    }


def _motive_jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 0.0
    return len(a & b) / len(a | b)


def _antagonist_motive_profiles(*, is_en: bool) -> list[dict[str, str]]:
    if is_en:
        return [
            {
                "axis": "order through containment",
                "goal": "Lock every dangerous threshold behind law, quarantine, and sworn custody.",
                "background": "Once watched a generous exception turn into a public disaster.",
                "secret": "Keeps proof that mercy, not malice, caused the first collapse.",
            },
            {
                "axis": "private atonement",
                "goal": "Recover one lost life even if the ritual bankrupts everyone else's future.",
                "background": "Built their authority on a death they still cannot confess.",
                "secret": "The promised resurrection will erase an innocent witness.",
            },
            {
                "axis": "institutional dominion",
                "goal": "Capture the council seats, archives, and enforcement offices that decide truth.",
                "background": "Learned that evidence only matters after power agrees to preserve it.",
                "secret": "Funds both sides so every verdict eventually depends on their signature.",
            },
            {
                "axis": "forbidden revelation",
                "goal": "Prove that taboo knowledge matters more than inherited moral limits.",
                "background": "Was exiled for publishing a correct conclusion at an unforgivable cost.",
                "secret": "Needs the protagonist alive as the final witness to the theory.",
            },
        ]
    return [
        {
            "axis": "铁律封禁",
            "goal": "以戒律、封印和巡查权锁死所有危险边界。",
            "background": "曾亲眼看见一次宽恕变成公开灾难，从此只相信隔离。",
            "secret": "保存着证据：最初的惨祸并非恶意造成，而是仁慈失控。",
        },
        {
            "axis": "以命换命的执念",
            "goal": "不惜献祭自身名声和资源，也要换回一个早该死去的人。",
            "background": "如今的地位建立在一场不能承认的死亡之上。",
            "secret": "所谓复生会抹掉一名无辜见证者的存在。",
        },
        {
            "axis": "权柄吞并",
            "goal": "夺取议事席、档案库和执法口径，让真相只能按自己的印章生效。",
            "background": "早年明白证据本身没有力量，保管证据的人才有力量。",
            "secret": "同时资助敌我两边，确保任何裁决最终都要经过自己签字。",
        },
        {
            "axis": "禁知狂热",
            "goal": "证明禁忌知识高于祖训伦理，哪怕要让旧秩序当众破产。",
            "background": "曾因发表正确却残酷的结论被逐出师门。",
            "secret": "需要主角活到最后，成为那套理论无法否认的见证人。",
        },
        {
            "axis": "血脉清算",
            "goal": "清除被视为污染源的血脉分支，重建祖训定义的纯净秩序。",
            "background": "少年时因家族混血丑闻失去继承权，从此把血统当作战场。",
            "secret": "自己的继承资格同样来自一桩被掩盖的禁忌联姻。",
        },
        {
            "axis": "证据垄断",
            "goal": "控制卷宗、证词和验尸结论，让真相只能按既定叙述流通。",
            "background": "曾因一份公开证据失去全部盟友，学会先夺记录再夺人心。",
            "secret": "最关键的原始证据一直藏在自己亲手封存的假案里。",
        },
        {
            "axis": "名望献祭",
            "goal": "用一场足够轰动的牺牲换取门派、家族或租界的公开臣服。",
            "background": "多年被当作边缘人利用，终于明白恐惧比尊敬更可靠。",
            "secret": "真正想献祭的不是敌人，而是当年羞辱过自己的同盟。",
        },
        {
            "axis": "旧案翻盘",
            "goal": "推翻一桩被定性的旧案，让所有既得利益者重新付出代价。",
            "background": "亲眼看着无辜者被当成结案工具，从此只相信反向清算。",
            "secret": "旧案若完全公开，自己也会成为共犯之一。",
        },
    ]


def _extra_antagonist_motive_profile(
    *,
    index: int,
    name: str,
    is_en: bool,
) -> dict[str, str]:
    ordinal = index + 1
    if is_en:
        return {
            "axis": f"bespoke pressure axis {ordinal}",
            "goal": f"Force the plot through {name}'s own pressure channel {ordinal}, not a copied resurrection or revenge template.",
            "background": f"{name} carries a private institutional debt keyed to marker {ordinal}.",
            "secret": f"The hidden leverage around {name} turns on marker {ordinal}, making their motive non-interchangeable.",
        }
    return {
        "axis": f"专属压力轴{ordinal}",
        "goal": f"沿{name}独有的第{ordinal}号压力通道推进局势，而不是复用复仇、复活或夺权模板。",
        "background": f"{name}背负一桩只属于自己的第{ordinal}号旧怨，这桩宿怨决定其行动边界。",
        "secret": f"{name}真正隐瞒的是第{ordinal}号把柄；一旦曝光，ta会失去当前阵营的庇护。",
    }


def _separate_overlapping_antagonist_motives(
    project: ProjectModel,
    cast_spec_payload: dict[str, Any],
) -> dict[str, Any]:
    """Deterministically split duplicated antagonist motive templates."""

    repaired = copy.deepcopy(cast_spec_payload)
    entries: list[dict[str, Any]] = []
    antagonist = _mapping(repaired.get("antagonist"))
    if antagonist:
        antagonist["role"] = _non_empty_string(antagonist.get("role"), "antagonist")
        repaired["antagonist"] = antagonist
        entries.append(antagonist)
    for member in _mapping_list(repaired.get("supporting_cast")):
        role_lower = _non_empty_string(member.get("role"), "").lower()
        if "antagonist" in role_lower:
            entries.append(member)

    if len(entries) < 2:
        return repaired

    bags = [
        _motive_keyword_bag(
            entry.get("goal"),
            entry.get("background"),
            entry.get("secret"),
        )
        for entry in entries
    ]
    has_overlap = any(
        _motive_jaccard(bags[i], bags[j]) > 0.4
        for i in range(len(bags))
        for j in range(i + 1, len(bags))
    )
    if not has_overlap:
        return repaired

    profiles = _antagonist_motive_profiles(
        is_en=is_english_language(getattr(project, "language", None))
    )
    is_en = is_english_language(getattr(project, "language", None))
    for idx, entry in enumerate(entries):
        profile = (
            profiles[idx]
            if idx < len(profiles)
            else _extra_antagonist_motive_profile(
                index=idx,
                name=_non_empty_string(entry.get("name"), f"antagonist_{idx + 1}"),
                is_en=is_en,
            )
        )
        entry["motive_axis"] = profile["axis"]
        entry["goal"] = profile["goal"]
        entry["background"] = profile["background"]
        entry["secret"] = profile["secret"]
    return repaired


def _ensure_character_contrast_fields(
    repaired: dict[str, Any],
    *,
    name: str,
    basis: str,
    role_lower: str,
    is_en: bool,
) -> None:
    surface_fields = [
        key
        for key in ("background", "goal", "strength")
        if _non_empty_string(repaired.get(key), "")
    ]
    if len(surface_fields) < 2:
        if not _non_empty_string(repaired.get("background"), ""):
            repaired["background"] = (
                f"{name} is publicly shaped by the unresolved cost of {basis}."
                if is_en
                else f"{name}的外在身份一直被「{basis}」留下的代价塑形。"
            )
            surface_fields.append("background")
        if len(surface_fields) < 2 and not _non_empty_string(repaired.get("goal"), ""):
            repaired["goal"] = (
                f"Resolve {basis} before it destroys the people still tied to it."
                if is_en
                else f"在「{basis}」毁掉仍被牵连的人之前，把它彻底解决。"
            )
            surface_fields.append("goal")
        if len(surface_fields) < 2 and not _non_empty_string(repaired.get("strength"), ""):
            repaired["strength"] = (
                "Can stay precise under pressure and turn small contradictions into leverage."
                if is_en
                else "能在压力下保持精确，把细小矛盾变成反击支点。"
            )

    hidden_fields = [
        key for key in ("secret", "fear", "flaw") if _non_empty_string(repaired.get(key), "")
    ]
    if hidden_fields:
        return
    if "antagonist" in role_lower:
        repaired["secret"] = (
            f"{name} knows their solution to {basis} repeats the original harm."
            if is_en
            else f"{name}知道自己解决「{basis}」的方法正在重演最初的伤害。"
        )
    else:
        repaired["fear"] = (
            f"{name} fears that proving the truth about {basis} will expose "
            "their own earlier misjudgment."
            if is_en
            else f"{name}害怕证明「{basis}」真相的同时，也暴露自己曾经判断失误。"
        )


def _synthesize_character_bible_fields(
    character: dict[str, Any],
    *,
    is_en: bool,
    project: ProjectModel | None = None,
) -> dict[str, Any]:
    repaired = copy.deepcopy(character)
    name = _non_empty_string(repaired.get("name"), "the character" if is_en else "角色")
    role = _non_empty_string(repaired.get("role"), "supporting")
    role_lower = role.lower()
    required_quirks = _role_requires_ip_anchor(role)
    basis = _short_character_basis(repaired, is_en=is_en)
    anchor = copy.deepcopy(_mapping(repaired.get("ip_anchor")))

    if not _non_empty_string(anchor.get("tag_memory"), ""):
        anchor["tag_memory"] = _synthesized_tag_memory(name, basis, is_en=is_en)
    if (
        "protagonist" not in role_lower
        and "antagonist" not in role_lower
        and not _non_empty_string(anchor.get("independent_life"), "")
    ):
        anchor["independent_life"] = _synthesized_independent_life(
            name,
            basis,
            is_en=is_en,
        )

    if required_quirks:
        anchor["quirks"] = _synthesized_quirks(
            repaired,
            required=required_quirks,
            is_en=is_en,
        )
        anchor["sensory_signatures"] = _ensure_min_strings(
            anchor.get("sensory_signatures"),
            (
                [
                    f"a restrained pause before {name} speaks",
                    "the dry smell of paper, metal, and rain",
                ]
                if is_en
                else [
                    f"{name}开口前那一瞬克制的停顿",
                    "纸张、金属与雨水混在一起的冷味",
                ]
            ),
            1,
        )
        anchor["signature_objects"] = _ensure_min_strings(
            anchor.get("signature_objects"),
            (
                [f"{name}'s worn notebook", "a marked token kept out of sight"]
                if is_en
                else [f"{name}随身带着的旧册", "一枚总被藏在袖中的旧物"]
            ),
            1,
        )
        if not _non_empty_string(anchor.get("core_wound"), ""):
            anchor["core_wound"] = (
                f"{name} once trusted the wrong version of events around {basis}, and someone else paid the price."
                if is_en
                else f"{name}曾在「{basis}」上相信过错误叙事，结果让一个无法补偿的人替自己付出代价。"
            )
    repaired["ip_anchor"] = anchor

    if "protagonist" in role_lower or "antagonist" in role_lower:
        _ensure_character_contrast_fields(
            repaired,
            name=name,
            basis=basis,
            role_lower=role_lower,
            is_en=is_en,
        )

    if "protagonist" in role_lower:
        _ensure_character_ability_origin_contract(
            repaired,
            project=project,
            is_en=is_en,
        )

        psych = copy.deepcopy(_mapping(repaired.get("psych_profile")))
        if not _truthy_values(psych):
            psych = (
                {
                    "mbti": "INTJ",
                    "enneagram": "6w5",
                    "attachment_style": "avoidant-secure under earned trust",
                    "big_five": {
                        "openness": 68,
                        "conscientiousness": 84,
                        "extraversion": 34,
                        "agreeableness": 46,
                        "neuroticism": 62,
                    },
                    "cognitive_biases": ["threat scanning", "responsibility overreach"],
                    "temperament": "guarded analytical",
                }
                if is_en
                else {
                    "mbti": "INTJ",
                    "enneagram": "6w5",
                    "attachment_style": "回避型，但在被反复证明可信后转向稳定依恋",
                    "big_five": {
                        "openness": 68,
                        "conscientiousness": 84,
                        "extraversion": 34,
                        "agreeableness": 46,
                        "neuroticism": 62,
                    },
                    "cognitive_biases": ["威胁扫描", "责任过度归因"],
                    "temperament": "克制的分析型",
                }
            )
        repaired["psych_profile"] = psych

        history = copy.deepcopy(_mapping(repaired.get("life_history")))
        if not _truthy_values(history):
            history = (
                {
                    "formative_events": [
                        {
                            "title": f"The cost of {basis}",
                            "summary": f"{name} learned that a wrong conclusion can ruin someone else's life.",
                            "impact": "Turns every later choice into an attempt to verify one more fact.",
                        }
                    ],
                    "education": "self-trained through pressure, investigation, and repeated loss",
                    "defining_moments": ["Chose the harder truth over the safer official story."],
                    "regrets": [f"Did not question {basis} early enough."],
                }
                if is_en
                else {
                    "formative_events": [
                        {
                            "title": f"围绕「{basis}」付出的代价",
                            "summary": f"{name}第一次明白，错误判断会让别人替自己承受后果。",
                            "impact": "从此每个重大选择都要多核验一个事实。",
                        }
                    ],
                    "education": "在压力、调查和反复失去中形成的自学路径",
                    "defining_moments": ["选择更痛的真相，而不是更安全的官方说法。"],
                    "regrets": [f"没能更早质疑「{basis}」。"],
                }
            )
        repaired["life_history"] = history

        family = copy.deepcopy(_mapping(repaired.get("family_imprint")))
        if not _truthy_values(family):
            family = (
                {
                    "parenting_style": "love expressed through demands and silence",
                    "family_socioeconomic": "unstable respectability",
                    "sibling_dynamics": "learned to become the responsible one before being ready",
                    "inherited_values": ["protect first, explain later", "debts must be repaid"],
                }
                if is_en
                else {
                    "parenting_style": "以要求和沉默表达爱的家庭模式",
                    "family_socioeconomic": "体面但随时可能坠落的不稳定阶层",
                    "sibling_dynamics": "过早学会承担那个必须负责的人",
                    "inherited_values": ["先保护，再解释", "答应的事必须做到"],
                }
            )
        repaired["family_imprint"] = family

        beliefs = copy.deepcopy(_mapping(repaired.get("beliefs")))
        if not _truthy_values(beliefs):
            beliefs = (
                {
                    "philosophical_stance": "truth is a duty, not a comfort",
                    "ideology": "systems only deserve loyalty when they protect the vulnerable",
                    "crisis_of_faith": f"Whether exposing {basis} will destroy the people {name} wants to save.",
                }
                if is_en
                else {
                    "philosophical_stance": "真相不是安慰，而是一种责任",
                    "ideology": "制度只有在保护弱者时才值得忠诚",
                    "crisis_of_faith": f"揭开「{basis}」是否会反而毁掉{name}想保护的人。",
                }
            )
        repaired["beliefs"] = beliefs

    if "antagonist" in role_lower:
        charisma = copy.deepcopy(_mapping(repaired.get("villain_charisma")))
        if not _non_empty_string(charisma.get("noble_motivation"), ""):
            charisma["noble_motivation"] = (
                f"{name} believes harsh control can prevent a larger collapse."
                if is_en
                else f"{name}相信残酷控制可以阻止更大范围的崩塌。"
            )
        if not _non_empty_string(charisma.get("pain_origin"), ""):
            charisma["pain_origin"] = (
                f"A past failure around {basis} convinced {name} that mercy only creates future victims."
                if is_en
                else f"围绕「{basis}」的一次失败让{name}相信，仁慈只会制造更多未来受害者。"
            )
        if not _truthy_values(charisma.get("redeeming_qualities")):
            charisma["redeeming_qualities"] = (
                [
                    "keeps promises to dependents",
                    "never forgets the people lost in the first disaster",
                ]
                if is_en
                else ["会兑现对依附者的承诺", "始终记得第一场灾难里失去的人"]
            )
        if not _non_empty_string(charisma.get("philosophical_appeal"), ""):
            charisma["philosophical_appeal"] = (
                "Order can look merciful when everyone remembers chaos."
                if is_en
                else "当所有人都记得混乱的代价时，秩序看起来也会像一种仁慈。"
            )
        if not _truthy_values(charisma.get("personal_code")):
            charisma["personal_code"] = (
                ["does not betray written bargains", "does not waste sacrifice for vanity"]
                if is_en
                else ["不会背弃明文交易", "不会为了虚荣浪费牺牲"]
            )
        if not _non_empty_string(charisma.get("tragic_irony"), ""):
            charisma["tragic_irony"] = (
                f"To prevent another {basis}, {name} becomes the reason others repeat the same wound."
                if is_en
                else f"为了阻止「{basis}」重演，{name}反而成为让更多人承受同类伤口的人。"
            )
        if not _non_empty_string(charisma.get("protagonist_mirror"), ""):
            charisma["protagonist_mirror"] = (
                f"Both {name} and the protagonist want to stop loss; they differ on who may be sacrificed."
                if is_en
                else f"{name}和主角都想阻止失去，只是对谁可以被牺牲给出了相反答案。"
            )
        repaired["villain_charisma"] = charisma

    return repaired


def _synthesize_missing_cast_bible_fields(
    project: ProjectModel,
    cast_spec_payload: dict[str, Any],
) -> dict[str, Any]:
    """Fill remaining L2 character-bible fields from existing cast facts.

    This is a hard fallback after LLM repair. It writes actual character
    anchors/personhood data derived from the role, goal, fear, secret, and
    background already present in the cast; it does not mark deficiencies as
    ignored.
    """

    cast_spec = parse_cast_spec_input(cast_spec_payload)
    normalized = cast_spec.model_dump(mode="json")
    is_en = is_english_language(getattr(project, "language", None))
    repaired = copy.deepcopy(_mapping(cast_spec_payload))
    if normalized.get("protagonist"):
        repaired["protagonist"] = _synthesize_character_bible_fields(
            normalized["protagonist"],
            is_en=is_en,
            project=project,
        )
    if normalized.get("antagonist"):
        repaired["antagonist"] = _synthesize_character_bible_fields(
            normalized["antagonist"],
            is_en=is_en,
            project=project,
        )
    repaired["supporting_cast"] = [
        _synthesize_character_bible_fields(character, is_en=is_en, project=project)
        for character in _mapping_list(normalized.get("supporting_cast"))
    ]
    repaired["antagonist_forces"] = normalized.get("antagonist_forces") or []
    repaired["conflict_map"] = normalized.get("conflict_map") or []
    repaired = _separate_overlapping_antagonist_motives(project, repaired)
    return repaired


async def _repair_cast_personhood_if_needed(
    *,
    session: AsyncSession,
    settings: Any,
    project: ProjectModel,
    book_spec_payload: dict[str, Any],
    world_spec_payload: dict[str, Any],
    cast_spec_payload: dict[str, Any],
    workflow_run_id: UUID,
) -> tuple[dict[str, Any], UUID | None]:
    """Regenerate CastSpec once when the character bible is structurally thin.

    This wires the L2 Bible Gate feedback into the generation path for the
    fields that CastSpec can actually fix: IP anchors, tag memories,
    supporting-cast independent lives, protagonist/antagonist contrast,
    protagonist personhood, primary-antagonist charisma, and antagonist motive
    separation. Non-cast deficiencies remain the materialization gate's
    responsibility.
    """

    try:
        from bestseller.services.bible_gate import (
            BibleCompletenessReport,
            build_draft_from_materialization_content,
            validate_bible_completeness,
        )
        from bestseller.services.invariants import (
            invariants_from_dict,
            seed_invariants,
        )

        if getattr(project, "invariants_json", None):
            invariants = invariants_from_dict(project.invariants_json)
        else:
            _planner_meta = getattr(project, "metadata_json", None)
            _planner_pack_key = (
                _planner_meta.get("prompt_pack_key")
                if isinstance(_planner_meta, Mapping)
                else None
            )
            invariants = seed_invariants(
                project_id=project.id,
                language=getattr(project, "language", None),
                words_per_chapter=getattr(
                    settings.generation,
                    "words_per_chapter",
                    None,
                ),
                genre=getattr(project, "genre", None),
                sub_genre=getattr(project, "sub_genre", None),
                prompt_pack_key=_planner_pack_key,
            )
        draft = build_draft_from_materialization_content(
            book_spec_content=book_spec_payload,
            world_spec_content=world_spec_payload,
            cast_spec_content=cast_spec_payload,
        )
        report = validate_bible_completeness(draft, invariants)
    except Exception:
        logger.debug("Cast personhood bible scan failed (non-fatal)", exc_info=True)
        return cast_spec_payload, None

    actionable = tuple(d for d in report.deficiencies if d.code in _CAST_PERSONHOOD_REPAIR_CODES)
    if not actionable:
        return cast_spec_payload, None

    logger.warning(
        "Cast personhood gate found %d actionable deficiency(ies); attempting repair.",
        len(actionable),
    )

    try:
        synthesized_payload = _synthesize_missing_cast_bible_fields(
            project,
            cast_spec_payload,
        )
        synthesized_draft = build_draft_from_materialization_content(
            book_spec_content=book_spec_payload,
            world_spec_content=world_spec_payload,
            cast_spec_content=synthesized_payload,
        )
        synthesized_report = validate_bible_completeness(synthesized_draft, invariants)
        synthesized_actionable = [
            d
            for d in synthesized_report.deficiencies
            if d.code in _CAST_PERSONHOOD_REPAIR_CODES
        ]
        if len(synthesized_actionable) < len(actionable):
            logger.info(
                "Cast personhood deterministic repair reduced deficiencies "
                "(%d -> %d); skipping LLM repair.",
                len(actionable),
                len(synthesized_actionable),
            )
            return synthesized_payload, None
    except Exception:
        logger.debug(
            "Cast personhood deterministic repair failed; falling back to LLM repair.",
            exc_info=True,
        )

    try:
        language = _planner_language(project)
        is_en = is_english_language(language)
        repair_report = BibleCompletenessReport(deficiencies=actionable)
        repair_system, repair_user = _cast_spec_prompts(
            project, book_spec_payload, world_spec_payload
        )
        repair_user += (
            "\n\n[Character bible repair - regenerate CastSpec to satisfy "
            "these hard requirements]\n"
            if is_en
            else "\n\n【人物圣经修复 — 请重生 CastSpec 以满足以下硬性要求】\n"
        )
        repair_user += repair_report.feedback_for_regen()
        repair_user += (
            "\nRegenerate the ENTIRE CastSpec JSON. Preserve the core premise, names, "
            "relationships, antagonist_forces, and conflict_map when possible, but "
            "fill every missing IP anchor, ability origin contract, personhood layer, "
            "and villain charisma field; "
            "also separate any overlapping antagonist motives. "
            "Do not return a partial patch."
            if is_en
            else "\n请重新生成整份 CastSpec JSON。尽量保留核心设定、角色姓名、关系、"
            "antagonist_forces 和 conflict_map，但必须补齐所有缺失的 IP 锚点、"
            "能力来源合同、人格底层和反派魅力字段，并拆分任何过度重合的反派动机。不要输出局部补丁。"
        )

        repaired_payload, repair_llm_run_id = await _generate_structured_artifact(
            session,
            settings,
            project=project,
            logical_name="cast_spec_personhood_repair",
            system_prompt=repair_system,
            user_prompt=repair_user,
            fallback_payload=cast_spec_payload,
            workflow_run_id=workflow_run_id,
            validator=parse_cast_spec_input,
        )
        if not isinstance(repaired_payload, dict):
            return cast_spec_payload, None
        repaired_payload = _synthesize_missing_cast_bible_fields(
            project,
            repaired_payload,
        )

        try:
            repaired_draft = build_draft_from_materialization_content(
                book_spec_content=book_spec_payload,
                world_spec_content=world_spec_payload,
                cast_spec_content=repaired_payload,
            )
            repaired_report = validate_bible_completeness(repaired_draft, invariants)
            repaired_actionable = [
                d for d in repaired_report.deficiencies if d.code in _CAST_PERSONHOOD_REPAIR_CODES
            ]
            if len(repaired_actionable) >= len(actionable):
                logger.warning(
                    "Cast personhood repair did not reduce deficiencies "
                    "(%d -> %d); keeping original.",
                    len(actionable),
                    len(repaired_actionable),
                )
                return cast_spec_payload, None
        except Exception:
            logger.debug("Cast personhood repaired payload validation failed", exc_info=True)
            return cast_spec_payload, None

        return repaired_payload, repair_llm_run_id
    except Exception:
        logger.warning("Cast personhood repair failed; keeping original cast spec.", exc_info=True)
        return cast_spec_payload, None


async def _repair_world_spec_richness_if_needed(
    *,
    session: AsyncSession,
    settings: Any,
    project: ProjectModel,
    premise: str,
    book_spec_payload: dict[str, Any],
    world_spec_payload: dict[str, Any],
    workflow_run_id: UUID,
) -> tuple[dict[str, Any], UUID | None]:
    """Scan world_spec richness (rules/locations/factions vs chapter count)
    and auto-repair once if critical.

    This is the world-level peer of :func:`_repair_cast_foundation_if_needed`
    and runs immediately after world_spec generation — before the cast
    spec prompt consumes the world summary. Detects the two failure modes
    from the 6-book audit:

    * Starved world (道种破虚): too few rules/locations/factions for the
      planned chapter count, causing chapter-level material exhaustion
      and volume-plan collapse.
    * Bloated world: too many rules for chapters to ever ground,
      producing shallow worldbuilding.

    Best-effort guardrail: any failure falls back to the original spec.
    """

    try:
        from bestseller.services.world_richness import (
            scan_world_spec_richness,
        )

        report = scan_world_spec_richness(
            world_spec_payload,
            total_chapters=max(project.target_chapters, 1),
            language=_planner_language(project),
        )
    except Exception:
        logger.debug("World-richness scan failed (non-fatal)", exc_info=True)
        return world_spec_payload, None

    # Attach findings to workflow metadata for observability.
    try:
        from bestseller.infra.db.models import WorkflowRunModel as _WR

        wr = await session.scalar(select(_WR).where(_WR.id == workflow_run_id))
        if wr is not None:
            wr.metadata_json = {
                **(wr.metadata_json or {}),
                "world_richness_findings": [
                    {
                        "code": f.code,
                        "severity": f.severity,
                        "message": f.message,
                    }
                    for f in report.findings[:20]
                ],
                "world_richness_critical": report.is_critical,
                "world_richness_rule_count": report.rule_count,
                "world_richness_rule_floor": report.rule_bounds.floor,
                "world_richness_rule_ceiling": report.rule_bounds.ceiling,
                "world_richness_location_count": report.location_count,
                "world_richness_faction_count": report.faction_count,
            }
    except Exception:
        logger.debug("Workflow-metadata attachment failed (non-fatal)", exc_info=True)

    if not report.is_critical:
        if report.findings:
            logger.info(
                "World richness: %d warning-level finding(s); continuing without repair.",
                len(report.findings),
            )
        return world_spec_payload, None

    logger.warning(
        "World richness critical (%d critical, %d warning); attempting single repair.",
        report.critical_count,
        report.warning_count,
    )
    emit_activity(
        "world_richness_repair_started",
        {
            "count": report.critical_count,
            "severity": "high",
            "reasons": [f.message for f in report.findings[:3]],
        },
    )

    try:
        language = _planner_language(project)
        is_en = is_english_language(language)
        repair_block = report.to_prompt_block(language=language)
        repair_system, repair_user = _world_spec_prompts(project, premise, book_spec_payload)
        header = (
            "\n\n[World richness — repair the under/over-scaled world foundation]"
            if is_en
            else "\n\n【世界观丰富度 — 请修复与章节规模不匹配的世界设定】"
        )
        repair_user += f"{header}\n{repair_block}\n"
        if is_en:
            repair_user += (
                "\nRegenerate the ENTIRE WorldSpec JSON. Keep world_name, "
                "world_premise, power_system, power_structure, history_key_events, "
                "and forbidden_zones intact; rework `rules`, `locations`, and "
                "`factions` to satisfy every constraint above. Rule names must be "
                "pairwise distinct. Every rule must carry a non-empty "
                "description AND story_consequence. Do not narrow any existing "
                "field."
            )
        else:
            repair_user += (
                "\n请重新生成整份 WorldSpec JSON：保留 world_name、world_premise、"
                "power_system、power_structure、history_key_events、forbidden_zones "
                "字段不变；重构 rules、locations、factions 三个字段，"
                "使之满足上面列出的所有约束。rule 名称必须两两不同；"
                "每条 rule 都必须同时包含非空的 description 与 story_consequence。"
                "不要缩减任何已有字段。"
            )

        repaired_payload, repair_llm_run_id = await _generate_structured_artifact(
            session,
            settings,
            project=project,
            logical_name="world_spec_richness_repair",
            system_prompt=repair_system,
            user_prompt=repair_user,
            fallback_payload=world_spec_payload,
            workflow_run_id=workflow_run_id,
            validator=parse_world_spec_input,
        )
        if not isinstance(repaired_payload, dict):
            return world_spec_payload, None

        # Sanity-check the repair: reject if it regressed on rule count
        # (LLM sometimes misinterprets the instruction and shrinks the
        # list; the starvation branch should never go *lower* than the
        # original, the bloat branch should never go *higher*).
        try:
            from bestseller.services.world_richness import (
                scan_world_spec_richness as _rescan,
            )

            repaired_report = _rescan(
                repaired_payload,
                total_chapters=max(project.target_chapters, 1),
                language=language,
            )
            was_starved = any(f.code.startswith("starved_") for f in report.findings)
            was_bloated = any(f.code.startswith("bloated_") for f in report.findings)
            if was_starved and repaired_report.rule_count < report.rule_count:
                logger.warning(
                    "World repair regressed rule count on starvation branch "
                    "(%d → %d); keeping original.",
                    report.rule_count,
                    repaired_report.rule_count,
                )
                return world_spec_payload, None
            if was_bloated and repaired_report.rule_count > report.rule_count:
                logger.warning(
                    "World repair regressed rule count on bloat branch "
                    "(%d → %d); keeping original.",
                    report.rule_count,
                    repaired_report.rule_count,
                )
                return world_spec_payload, None
        except Exception:
            pass

        emit_milestone(
            "world_richness_repair_completed",
            {"status": "repaired", "rule_count": getattr(report, "rule_count", None)},
        )
        return repaired_payload, repair_llm_run_id
    except Exception:
        logger.warning(
            "World richness repair failed; keeping original world spec.",
            exc_info=True,
        )
        return world_spec_payload, None


async def _repair_volume_plan_foreshadowing_if_needed(
    *,
    session: AsyncSession,
    settings: Any,
    project: ProjectModel,
    book_spec_payload: dict[str, Any],
    world_spec_payload: dict[str, Any],
    cast_spec_payload: dict[str, Any],
    act_plan_payload: Any,
    volume_plan_payload: Any,
    workflow_run_id: UUID,
) -> tuple[Any, UUID | None]:
    """Scan volume plan's foreshadowing density and auto-repair once if
    critical.

    This addresses the B3 starvation pattern observed across all 6
    production books: 800-1200 chapter novels were producing only 5-8
    clues total (one clue every 100-170 chapters), leaving most
    chapters with no active foreshadow thread.

    Best-effort: failures fall back to the original plan.
    """

    if not isinstance(volume_plan_payload, list) or len(volume_plan_payload) < 1:
        return volume_plan_payload, None
    try:
        from bestseller.services.foreshadowing_scaling import (
            scan_volume_plan_foreshadowing,
        )

        report = scan_volume_plan_foreshadowing(
            volume_plan_payload,
            total_chapters=max(project.target_chapters, 1),
            language=_planner_language(project),
        )
    except Exception:
        logger.debug("Foreshadowing-scaling scan failed (non-fatal)", exc_info=True)
        return volume_plan_payload, None

    # Attach findings to workflow metadata for observability.
    try:
        from bestseller.infra.db.models import WorkflowRunModel as _WR

        wr = await session.scalar(select(_WR).where(_WR.id == workflow_run_id))
        if wr is not None:
            wr.metadata_json = {
                **(wr.metadata_json or {}),
                "foreshadowing_scaling_findings": [
                    {
                        "code": f.code,
                        "severity": f.severity,
                        "message": f.message,
                    }
                    for f in report.findings[:20]
                ],
                "foreshadowing_scaling_critical": report.is_critical,
                "foreshadowing_planted_count": report.planted_count,
                "foreshadowing_planted_floor": report.planted_bounds.floor,
                "foreshadowing_paid_off_count": report.paid_off_count,
                "foreshadowing_paid_off_floor": report.paid_off_bounds.floor,
            }
    except Exception:
        logger.debug("Workflow-metadata attachment failed (non-fatal)", exc_info=True)

    if not report.is_critical:
        if report.findings:
            logger.info(
                "Foreshadowing scaling: %d warning-level finding(s); continuing without repair.",
                len(report.findings),
            )
        return volume_plan_payload, None

    logger.warning(
        "Foreshadowing scaling critical (%d critical, %d warning); attempting single repair.",
        report.critical_count,
        report.warning_count,
    )

    try:
        language = _planner_language(project)
        is_en = is_english_language(language)
        repair_block = report.to_prompt_block(language=language)
        repair_system, repair_user = _volume_plan_prompts(
            project,
            book_spec_payload,
            world_spec_payload,
            cast_spec_payload,
            act_plan=act_plan_payload,
        )
        header = (
            "\n\n[Foreshadowing scaling — repair thin clue/payoff density]"
            if is_en
            else "\n\n【伏笔密度 — 请修复密度不足的伏笔设计】"
        )
        repair_user += f"{header}\n{repair_block}\n"
        if is_en:
            repair_user += (
                "\nRegenerate the ENTIRE VolumePlan JSON array. Keep every "
                "volume_number, volume_title, volume_theme, goal, obstacle, "
                "climax, resolution, conflict_phase, and primary_force_name "
                "intact; only enrich the `foreshadowing_planted` and "
                "`foreshadowing_paid_off` arrays to satisfy every constraint "
                "above. Every plant must be a CONCRETE, nameable item — "
                "object, person, date, place, or event — never a vague omen."
            )
        else:
            repair_user += (
                "\n请重新生成整份 VolumePlan JSON 数组："
                "保留每卷的 volume_number、volume_title、volume_theme、"
                "goal、obstacle、climax、resolution、conflict_phase、"
                "primary_force_name 字段不变；仅充实 foreshadowing_planted 与 "
                "foreshadowing_paid_off 两个数组，使之满足上面列出的所有约束。"
                "每条伏笔必须是具体可名的物件/人物/日期/地点/事件，"
                "不能是『一个征兆』这类空泛占位。"
            )

        repaired_payload, repair_llm_run_id = await _generate_structured_artifact(
            session,
            settings,
            project=project,
            logical_name="volume_plan_foreshadowing_repair",
            system_prompt=repair_system,
            user_prompt=repair_user,
            fallback_payload=volume_plan_payload,
            workflow_run_id=workflow_run_id,
            validator=parse_volume_plan_input,
        )
        if not isinstance(repaired_payload, list) or len(repaired_payload) < 1:
            return volume_plan_payload, None

        # Sanity-check: the repair must not regress planted_count on
        # a starvation branch.
        try:
            from bestseller.services.foreshadowing_scaling import (
                scan_volume_plan_foreshadowing as _rescan,
            )

            repaired_report = _rescan(
                repaired_payload,
                total_chapters=max(project.target_chapters, 1),
                language=language,
            )
            was_starved = any(f.code.startswith("starved_") for f in report.findings)
            if was_starved and repaired_report.planted_count < report.planted_count:
                logger.warning(
                    "Foreshadowing repair regressed plant count (%d → %d); keeping original.",
                    report.planted_count,
                    repaired_report.planted_count,
                )
                return volume_plan_payload, None
        except Exception:
            pass

        return repaired_payload, repair_llm_run_id
    except Exception:
        logger.warning(
            "Foreshadowing repair failed; keeping original volume plan.",
            exc_info=True,
        )
        return volume_plan_payload, None


def _fill_missing_book_spec_narrative_lines(
    *,
    project: ProjectModel,
    premise: str,
    book_spec_payload: dict[str, Any],
    volume_count: int,
) -> dict[str, Any]:
    payload = copy.deepcopy(_mapping(book_spec_payload))
    existing = _mapping(payload.get("narrative_lines"))
    narrative_lines = copy.deepcopy(existing)

    volume_count = max(1, int(volume_count or 1))
    title = _non_empty_string(project.title, "本书")
    protagonist = _mapping(payload.get("protagonist"))
    protagonist_name = _non_empty_string(protagonist.get("name"), "主角")
    premise_text = _non_empty_string(premise, _non_empty_string(payload.get("logline"), title))

    if not _mapping_list(narrative_lines.get("overt_line")):
        arc_count = max(1, min(max(3, volume_count), 6))
        arcs: list[dict[str, Any]] = []
        for index in range(arc_count):
            start = 1 + math.floor(index * volume_count / arc_count)
            end = min(volume_count, math.floor((index + 1) * volume_count / arc_count))
            if end < start:
                end = start
            arcs.append(
                {
                    "name": f"{title}明线弧{index + 1}",
                    "start_volume": start,
                    "end_volume": end,
                    "surface_goal": f"{protagonist_name}在第{start}-{end}卷解决阶段性外部压力。",
                    "reader_payoff": "每卷给出可见推进、代价和新的下一步欲望。",
                }
            )
        narrative_lines["overt_line"] = arcs

    if not _mapping_list(narrative_lines.get("undercurrent_line")):
        narrative_lines["undercurrent_line"] = [
            {
                "name": f"{title}暗线压力",
                "start_volume": 1,
                "end_volume": volume_count,
                "shadow_conflict": f"{premise_text[:120]}背后的长期压力持续改写每卷选择。",
                "connection_rule": "每条明线弧至少暴露一次暗线代价或误导。",
            }
        ]

    hidden = _mapping(narrative_lines.get("hidden_thread"))
    if not _non_empty_string(hidden.get("statement"), ""):
        narrative_lines["hidden_thread"] = {
            **hidden,
            "statement": f"{protagonist_name}面对的关键真相从第一卷已被埋入，但完整含义直到终局才揭开。",
            "seed_volumes": [1],
            "payoff_volumes": [volume_count],
            "reader_experience": "早期像线索，中段像代价，末段变成重新解释全书的真相。",
        }

    core_axis = _mapping(narrative_lines.get("core_axis"))
    if not _non_empty_string(core_axis.get("statement"), ""):
        narrative_lines["core_axis"] = {
            **core_axis,
            "statement": f"{title}的核心轴：{protagonist_name}如何在外部压力与自身代价之间夺回主动权。",
            "phrasing_tokens": [protagonist_name, "主动权", "代价", "真相"],
        }

    if narrative_lines == existing:
        return book_spec_payload
    payload["narrative_lines"] = narrative_lines
    return payload


async def _repair_book_spec_narrative_lines_if_needed(
    *,
    session: AsyncSession,
    settings: Any,
    project: ProjectModel,
    premise: str,
    book_spec_payload: dict[str, Any],
    workflow_run_id: UUID,
) -> tuple[dict[str, Any], UUID | None]:
    """Scan BookSpec.narrative_lines (四线贯穿 contract) and auto-repair
    once if critical.

    Addresses the B9 root-cause observed in 道种破虚 (24 volumes × single
    '元婴老者' overt arc): without an explicit four-layer macro structure,
    the LLM defaults to a single overt antagonist rotating through volumes.
    The gate validates that the BookSpec defines overt / undercurrent /
    hidden / core_axis lines at scale-appropriate spans and triggers a
    focused regeneration when critical.

    Best-effort: any failure falls back to the original spec.
    """

    try:
        from bestseller.services.narrative_lines import (
            scan_narrative_lines,
        )

        _hierarchy = compute_linear_hierarchy(max(project.target_chapters, 1))
        volume_count = int(_hierarchy.get("volume_count") or 1)
        narrative_lines_raw = _mapping(book_spec_payload).get("narrative_lines")
        report = scan_narrative_lines(
            narrative_lines_raw if narrative_lines_raw is not None else {},
            total_chapters=max(project.target_chapters, 1),
            volume_count=volume_count,
            language=_planner_language(project),
        )
    except Exception:
        logger.debug("Narrative-lines scan failed (non-fatal)", exc_info=True)
        return book_spec_payload, None

    # Attach findings to workflow metadata for observability.
    try:
        from bestseller.infra.db.models import WorkflowRunModel as _WR

        wr = await session.scalar(select(_WR).where(_WR.id == workflow_run_id))
        if wr is not None:
            wr.metadata_json = {
                **(wr.metadata_json or {}),
                "narrative_lines_findings": [
                    {
                        "code": f.code,
                        "severity": f.severity,
                        "message": f.message,
                    }
                    for f in report.findings[:20]
                ],
                "narrative_lines_critical": report.is_critical,
                "narrative_lines_has_overt": report.has_overt,
                "narrative_lines_has_undercurrent": report.has_undercurrent,
                "narrative_lines_has_hidden": report.has_hidden_thread,
                "narrative_lines_has_core_axis": report.has_core_axis,
            }
    except Exception:
        logger.debug("Workflow-metadata attachment failed (non-fatal)", exc_info=True)

    if not report.is_critical:
        if report.findings:
            logger.info(
                "Narrative lines: %d warning-level finding(s); continuing without repair.",
                len(report.findings),
            )
        return book_spec_payload, None

    emit_activity(
        "narrative_lines_repair_started",
        {
            "count": report.critical_count,
            "severity": "high",
            "reasons": [f.message for f in report.findings[:3]],
        },
    )
    deterministic_payload = _fill_missing_book_spec_narrative_lines(
        project=project,
        premise=premise,
        book_spec_payload=book_spec_payload,
        volume_count=volume_count,
    )
    if deterministic_payload != book_spec_payload:
        try:
            deterministic_report = scan_narrative_lines(
                _mapping(deterministic_payload).get("narrative_lines") or {},
                total_chapters=max(project.target_chapters, 1),
                volume_count=volume_count,
                language=_planner_language(project),
            )
            if not deterministic_report.is_critical:
                logger.info(
                    "Narrative lines critical findings repaired deterministically; "
                    "continuing without LLM repair."
                )
                emit_milestone(
                    "narrative_lines_repair_completed",
                    {"status": "repaired", "method": "deterministic"},
                )
                return deterministic_payload, None
        except Exception:
            logger.debug("Deterministic narrative-lines repair scan failed", exc_info=True)

    logger.warning(
        "Narrative lines critical (%d critical, %d warning); attempting single repair.",
        report.critical_count,
        report.warning_count,
    )

    try:
        language = _planner_language(project)
        is_en = is_english_language(language)
        repair_block = report.to_prompt_block(language=language)
        repair_system, repair_user = _book_spec_prompts(project, premise, book_spec_payload)
        header = (
            "\n\n[Narrative lines — repair the missing four-layer macro structure]"
            if is_en
            else "\n\n【叙事四线 — 请修复缺失的四层宏观叙事结构】"
        )
        repair_user += f"{header}\n{repair_block}\n"
        if is_en:
            repair_user += (
                "\nRegenerate the ENTIRE BookSpec JSON. Keep title, logline, "
                "genre, target_audience, tone, themes, protagonist, stakes, "
                "and series_engine intact; add or rework the top-level "
                "`narrative_lines` field so every constraint above is met. "
                "overt_line, undercurrent_line, hidden_thread, core_axis are "
                "ALL required. Do not narrow any existing field."
            )
        else:
            repair_user += (
                "\n请重新生成整份 BookSpec JSON："
                "保留 title、logline、genre、target_audience、tone、themes、"
                "protagonist、stakes、series_engine 字段不变；"
                "补全或重构顶层 `narrative_lines` 字段，"
                "使之满足上面列出的所有约束。"
                "overt_line、undercurrent_line、hidden_thread、core_axis "
                "四者缺一不可，不要缩减任何已有字段。"
            )

        repaired_payload, repair_llm_run_id = await _generate_structured_artifact(
            session,
            settings,
            project=project,
            logical_name="book_spec_narrative_lines_repair",
            system_prompt=repair_system,
            user_prompt=repair_user,
            fallback_payload=book_spec_payload,
            workflow_run_id=workflow_run_id,
        )
        if not isinstance(repaired_payload, dict):
            return book_spec_payload, None

        # Sanity-check: the repaired book_spec must at least provide the
        # four layers. If the re-run still has missing_* findings, keep
        # the original to avoid churning.
        try:
            from bestseller.services.narrative_lines import (
                scan_narrative_lines as _rescan,
            )

            repaired_report = _rescan(
                _mapping(repaired_payload).get("narrative_lines") or {},
                total_chapters=max(project.target_chapters, 1),
                volume_count=volume_count,
                language=language,
            )
            if (
                repaired_report.is_critical
                and repaired_report.critical_count >= report.critical_count
            ):
                logger.warning(
                    "Narrative-lines repair did not reduce critical count "
                    "(%d → %d); keeping original book spec.",
                    report.critical_count,
                    repaired_report.critical_count,
                )
                return book_spec_payload, None
        except Exception:
            pass

        emit_milestone(
            "narrative_lines_repair_completed",
            {"status": "repaired", "method": "llm"},
        )
        return repaired_payload, repair_llm_run_id
    except Exception:
        logger.warning(
            "Narrative-lines repair failed; keeping original book spec.",
            exc_info=True,
        )
        return book_spec_payload, None


async def _repair_cast_spec_antagonist_lifecycle_if_needed(
    *,
    session: AsyncSession,
    settings: Any,
    project: ProjectModel,
    book_spec_payload: dict[str, Any],
    world_spec_payload: dict[str, Any],
    cast_spec_payload: dict[str, Any],
    volume_count: int,
    workflow_run_id: UUID,
) -> tuple[dict[str, Any], UUID | None]:
    """Scan CastSpec.antagonists lifecycle and auto-repair once if critical.

    Addresses the post-fix regression observed after the first antagonist
    gate: even when each volume has a distinct named enemy, if every enemy
    is a single-volume kill-and-move-on boss the story still reads as a
    rotating template. This gate validates that the antagonist roster
    models evolution (line_role separation, stage spans, varied
    resolution_type palette) and triggers a focused repair otherwise.

    Best-effort: any failure falls back to the original cast spec.
    """

    try:
        from bestseller.services.antagonist_lifecycle import (
            scan_antagonist_lifecycle,
        )

        antagonists_raw = _mapping(cast_spec_payload).get("antagonists")
        report = scan_antagonist_lifecycle(
            antagonists_raw if antagonists_raw is not None else [],
            total_chapters=max(project.target_chapters, 1),
            volume_count=max(int(volume_count or 0), 1),
            language=_planner_language(project),
        )
    except Exception:
        logger.debug("Antagonist-lifecycle scan failed (non-fatal)", exc_info=True)
        return cast_spec_payload, None

    # Attach findings to workflow metadata for observability.
    try:
        from bestseller.infra.db.models import WorkflowRunModel as _WR

        wr = await session.scalar(select(_WR).where(_WR.id == workflow_run_id))
        if wr is not None:
            wr.metadata_json = {
                **(wr.metadata_json or {}),
                "antagonist_lifecycle_findings": [
                    {
                        "code": f.code,
                        "severity": f.severity,
                        "message": f.message,
                    }
                    for f in report.findings[:20]
                ],
                "antagonist_lifecycle_critical": report.is_critical,
                "antagonist_lifecycle_count": report.antagonist_count,
                "antagonist_lifecycle_resolution_distribution": dict(
                    report.resolution_distribution
                ),
            }
    except Exception:
        logger.debug("Workflow-metadata attachment failed (non-fatal)", exc_info=True)

    if not report.is_critical:
        if report.findings:
            logger.info(
                "Antagonist lifecycle: %d warning-level finding(s); continuing without repair.",
                len(report.findings),
            )
        return cast_spec_payload, None

    logger.warning(
        "Antagonist lifecycle critical (%d critical, %d warning); attempting single repair.",
        report.critical_count,
        report.warning_count,
    )

    try:
        language = _planner_language(project)
        is_en = is_english_language(language)
        repair_block = report.to_prompt_block(language=language)
        repair_system, repair_user = _cast_spec_prompts(
            project, book_spec_payload, world_spec_payload
        )
        header = (
            "\n\n[Antagonist lifecycle — repair the rotating-template roster]"
            if is_en
            else "\n\n【敌人生命周期 — 请修复轮换模板式的反派名单】"
        )
        repair_user += f"{header}\n{repair_block}\n"
        if is_en:
            repair_user += (
                "\nRegenerate the ENTIRE CastSpec JSON. Keep protagonist, "
                "world tie-ins, and existing name_reasoning fields intact; "
                "produce a top-level `antagonists` array where every entry "
                "has {name, archetype, line_role, stages_of_relevance, "
                "resolution_type, transition_volume, transition_mechanism}. "
                "Spread overt antagonists across volumes (distinct names), "
                "give the undercurrent one a multi-volume span, seed the "
                "hidden one early with payoff in the last quarter, and do "
                "NOT let every antagonist end as 'defeated_and_killed'."
            )
        else:
            repair_user += (
                "\n请重新生成整份 CastSpec JSON："
                "保留 protagonist、世界观锚点、已有 name_reasoning 字段不变；"
                "在顶层生成 `antagonists` 数组，每条记录都包含 "
                "{name、archetype、line_role、stages_of_relevance、"
                "resolution_type、transition_volume、transition_mechanism}。"
                "明线敌人要覆盖不同卷（名字两两不同），"
                "暗线敌人要跨多卷活跃，隐藏线敌人需要前期埋线、末 1/4 揭示，"
                "并且禁止所有敌人都是『defeated_and_killed』。"
            )

        repaired_payload, repair_llm_run_id = await _generate_structured_artifact(
            session,
            settings,
            project=project,
            logical_name="cast_spec_antagonist_lifecycle_repair",
            system_prompt=repair_system,
            user_prompt=repair_user,
            fallback_payload=cast_spec_payload,
            workflow_run_id=workflow_run_id,
            validator=parse_cast_spec_input,
        )
        if not isinstance(repaired_payload, dict):
            return cast_spec_payload, None

        # Sanity-check: the repaired cast_spec must at least reduce the
        # critical count; otherwise keep the original.
        try:
            from bestseller.services.antagonist_lifecycle import (
                scan_antagonist_lifecycle as _rescan,
            )

            repaired_report = _rescan(
                _mapping(repaired_payload).get("antagonists") or [],
                total_chapters=max(project.target_chapters, 1),
                volume_count=max(int(volume_count or 0), 1),
                language=language,
            )
            if (
                repaired_report.is_critical
                and repaired_report.critical_count >= report.critical_count
            ):
                logger.warning(
                    "Antagonist-lifecycle repair did not reduce critical count "
                    "(%d → %d); keeping original cast spec.",
                    report.critical_count,
                    repaired_report.critical_count,
                )
                return cast_spec_payload, None
        except Exception:
            pass

        return repaired_payload, repair_llm_run_id
    except Exception:
        logger.warning(
            "Antagonist-lifecycle repair failed; keeping original cast spec.",
            exc_info=True,
        )
        return cast_spec_payload, None


async def _repair_cast_spec_relationship_scaling_if_needed(
    *,
    session: AsyncSession,
    settings: Any,
    project: ProjectModel,
    book_spec_payload: dict[str, Any],
    world_spec_payload: dict[str, Any],
    cast_spec_payload: dict[str, Any],
    volume_count: int,
    workflow_run_id: UUID,
) -> tuple[dict[str, Any], UUID | None]:
    """Scan CastSpec.supporting_cast scaling and auto-repair once if critical.

    Addresses the social-fabric analogue of the world-richness and
    foundation-richness starvation patterns: long novels shipping with
    only 3-5 supporting_cast entries force scenes across many volumes to
    recycle the same small cluster of faces, giving the whole book a
    "cast of six" feel regardless of plot scale.

    Best-effort: any failure falls back to the original cast spec.
    """

    try:
        from bestseller.services.relationship_scaling import (
            scan_relationship_scaling,
        )

        supporting_raw = _mapping(cast_spec_payload).get("supporting_cast")
        report = scan_relationship_scaling(
            supporting_raw if supporting_raw is not None else [],
            total_chapters=max(project.target_chapters, 1),
            volume_count=max(int(volume_count or 0), 1),
            language=_planner_language(project),
        )
    except Exception:
        logger.debug("Relationship-scaling scan failed (non-fatal)", exc_info=True)
        return cast_spec_payload, None

    # Attach findings to workflow metadata for observability.
    try:
        from bestseller.infra.db.models import WorkflowRunModel as _WR

        wr = await session.scalar(select(_WR).where(_WR.id == workflow_run_id))
        if wr is not None:
            wr.metadata_json = {
                **(wr.metadata_json or {}),
                "relationship_scaling_findings": [
                    {
                        "code": f.code,
                        "severity": f.severity,
                        "message": f.message,
                    }
                    for f in report.findings[:20]
                ],
                "relationship_scaling_critical": report.is_critical,
                "relationship_scaling_count": report.supporting_cast_count,
                "relationship_scaling_floor": report.supporting_bounds.floor,
                "relationship_scaling_ceiling": report.supporting_bounds.ceiling,
                "relationship_scaling_distinct_buckets": report.distinct_role_buckets,
                "relationship_scaling_role_distribution": dict(report.role_distribution),
            }
    except Exception:
        logger.debug("Workflow-metadata attachment failed (non-fatal)", exc_info=True)

    if not report.is_critical:
        if report.findings:
            logger.info(
                "Relationship scaling: %d warning-level finding(s); continuing without repair.",
                len(report.findings),
            )
        return cast_spec_payload, None

    logger.warning(
        "Relationship scaling critical (%d critical, %d warning); attempting single repair.",
        report.critical_count,
        report.warning_count,
    )

    try:
        language = _planner_language(project)
        is_en = is_english_language(language)
        repair_block = report.to_prompt_block(language=language)
        repair_system, repair_user = _cast_spec_prompts(
            project, book_spec_payload, world_spec_payload
        )
        header = (
            "\n\n[Relationship scaling — repair the starved supporting cast]"
            if is_en
            else "\n\n【关系网规模 — 请修复过于单薄的 supporting_cast】"
        )
        repair_user += f"{header}\n{repair_block}\n"
        if is_en:
            repair_user += (
                "\nRegenerate the ENTIRE CastSpec JSON. Keep protagonist, "
                "antagonist, antagonists, world tie-ins, and existing "
                "name_reasoning fields intact; expand `supporting_cast` so "
                "every entry has {name, role, active_volumes, "
                "relationship_to_protagonist, evolution_arc}. Cover every "
                "volume with at least one active non-antagonist. Spread "
                "roles across at least 3 distinct categories "
                "(mentor/ally/rival/family/romantic/subordinate/confidant/"
                "broker) with no category exceeding 40% of the roster."
            )
        else:
            repair_user += (
                "\n请重新生成整份 CastSpec JSON："
                "保留 protagonist、antagonist、antagonists、世界观锚点、"
                "已有 name_reasoning 字段不变；"
                "扩充 `supporting_cast` 字段，使每个条目包含 "
                "{name、role、active_volumes、relationship_to_protagonist、"
                "evolution_arc}。每一卷至少 1 名活跃的非敌人类配角，"
                "role 要覆盖 ≥3 种类别"
                "（mentor/ally/rival/family/romantic/subordinate/"
                "confidant/broker），单一类别不得超过 40%。"
            )

        repaired_payload, repair_llm_run_id = await _generate_structured_artifact(
            session,
            settings,
            project=project,
            logical_name="cast_spec_relationship_scaling_repair",
            system_prompt=repair_system,
            user_prompt=repair_user,
            fallback_payload=cast_spec_payload,
            workflow_run_id=workflow_run_id,
            validator=parse_cast_spec_input,
        )
        if not isinstance(repaired_payload, dict):
            return cast_spec_payload, None

        # Sanity-check: the repaired cast_spec must at least reduce the
        # critical count; otherwise keep the original. Guards against the
        # LLM misinterpreting the repair instruction and returning a
        # smaller or equally-starved supporting_cast.
        try:
            from bestseller.services.relationship_scaling import (
                scan_relationship_scaling as _rescan,
            )

            repaired_report = _rescan(
                _mapping(repaired_payload).get("supporting_cast") or [],
                total_chapters=max(project.target_chapters, 1),
                volume_count=max(int(volume_count or 0), 1),
                language=language,
            )
            if (
                repaired_report.is_critical
                and repaired_report.critical_count >= report.critical_count
            ):
                logger.warning(
                    "Relationship-scaling repair did not reduce critical count "
                    "(%d → %d); keeping original cast spec.",
                    report.critical_count,
                    repaired_report.critical_count,
                )
                return cast_spec_payload, None
        except Exception:
            pass

        return repaired_payload, repair_llm_run_id
    except Exception:
        logger.warning(
            "Relationship-scaling repair failed; keeping original cast spec.",
            exc_info=True,
        )
        return cast_spec_payload, None


def _planner_prompt_pack(project: ProjectModel):
    writing_profile = _planner_writing_profile(project)
    return resolve_prompt_pack(
        writing_profile.market.prompt_pack_key,
        genre=project.genre,
        sub_genre=project.sub_genre,
    )


def _planner_fragment_or_ref(
    prompt_pack: Any,
    project: ProjectModel,
    fragment_name: str,
) -> str:
    """Return a ``planner_*`` pack fragment unless reference-style generation is active.

    When the project has a non-empty ``metadata_json["material_reference_block"]``
    (i.e. Forge has already run and §slug URNs are injected into prompts),
    returns ``""`` so the reference block fully replaces the B-class script
    injection that causes theme homogenisation across same-genre books.

    When no reference block is present (cold-start, flag off, or Forge
    skipped because the library had no seeds), falls back to the legacy
    pack fragment so baseline quality is preserved.

    Parameters
    ----------
    prompt_pack:
        Resolved prompt pack from :func:`_planner_prompt_pack`; ``None`` is
        treated as "no pack available" and returns ``""``.
    project:
        The project model — used to check whether the reference block was
        stashed during Batch-2 pre-fetch in ``generate_novel_plan``.
    fragment_name:
        The pack fragment key (e.g. ``"planner_book_spec"``).

    Returns
    -------
    str
        Either ``""`` (reference-style active) or ``f"{fragment}\\n"`` (legacy path).
    """
    if not prompt_pack:
        return ""
    # Honour the feature flag defensively — if the flag was flipped after
    # the block was stashed, respect the current setting rather than stale
    # metadata. ``get_settings()`` is cached so this is cheap.
    try:
        if not get_settings().pipeline.enable_reference_style_generation:
            return f"{render_prompt_pack_fragment(prompt_pack, fragment_name)}\n"
    except Exception:
        pass
    ref_block = ""
    try:
        md = project.metadata_json or {}
        if isinstance(md, dict):
            ref_block = md.get("material_reference_block", "") or ""
    except Exception:
        ref_block = ""
    if ref_block:
        # Reference-style active: suppress B-class fragment to avoid
        # prescribing pre-baked beats that collide across same-genre books.
        return ""
    return f"{render_prompt_pack_fragment(prompt_pack, fragment_name)}\n"


def _build_protagonist_from_category(
    protagonist_name: str,
    *,
    writing_profile: Any,
    category_key: str | None = None,
    is_en: bool = False,
    low_pressure: bool = False,
) -> dict[str, Any]:
    """Build protagonist dict using category archetype if available, else legacy defaults."""
    archetype = None
    if category_key:
        cat = get_novel_category(category_key)
        if cat and cat.protagonist_archetypes:
            archetype = cat.protagonist_archetypes[0]

    if archetype:
        core_wound = (archetype.core_wound_en if is_en else archetype.core_wound_zh) or ""
        ext_goal_tpl = (
            archetype.external_goal_template_en if is_en else archetype.external_goal_template_zh
        ) or ""
        int_need_tpl = (
            archetype.internal_need_template_en if is_en else archetype.internal_need_template_zh
        ) or ""
        if low_pressure and not is_en:
            # The urban-contemporary realist career-ladder templates ("{name}要在
            # 现实规则中拿到足以改变命运的位置") misframe a comedy/healing book. Swap
            # in low-pressure goals/needs so theme/DQ/contract derivation stays on
            # tone (genre-misroute fix — only the fallback templates change; an
            # explicit protagonist_core_drive from conception still wins below).
            ext_goal_tpl = "{name}只想守住自己的清静日子，把缠上门的麻烦一件件甩利索。"
            int_need_tpl = "{name}得学会与甩不掉的好运和麻烦和解，顺手接住身边人的善意。"
        ext_goal = (
            writing_profile.character.protagonist_core_drive
            or ext_goal_tpl.replace("{name}", protagonist_name)
            or (
                f"{protagonist_name} must resolve the core conflict."
                if is_en
                else f"{protagonist_name}必须解决核心冲突。"
            )
        )
        int_need = int_need_tpl.replace("{name}", protagonist_name) or (
            f"{protagonist_name} must grow beyond current limitations."
            if is_en
            else f"{protagonist_name}需要突破当前局限。"
        )
        return {
            "name": protagonist_name,
            "core_wound": core_wound.replace("{name}", protagonist_name),
            "external_goal": ext_goal,
            "internal_need": int_need,
            "archetype": writing_profile.character.protagonist_archetype
            or (archetype.name_en if is_en else archetype.name_zh),
            "golden_finger": writing_profile.character.golden_finger,
        }

    # Legacy default
    return {
        "name": protagonist_name,
        "core_wound": (
            f"{protagonist_name} once paid a heavy price for a critical misjudgment."
            if is_en
            else f"{protagonist_name}曾因一次关键判断失误付出沉重代价。"
        ),
        "external_goal": (
            writing_profile.character.protagonist_core_drive
            or (
                f"{protagonist_name} must track down and expose the orchestrator behind the current crisis."
                if is_en
                else f"{protagonist_name}必须主动追查并破解当前危机背后的操盘者。"
            )
        ),
        "internal_need": (
            f"{protagonist_name} must shift from shouldering everything alone to building a sustainable alliance."
            if is_en
            else f"{protagonist_name}需要从只靠个人硬撑，转向建立真正可持续的同盟。"
        ),
        "archetype": writing_profile.character.protagonist_archetype,
        "golden_finger": writing_profile.character.golden_finger,
    }


def _fallback_expected_character_count(project: ProjectModel) -> int:
    """Return a bounded expected named-cast count for Bible gate sizing."""

    target_chapters = max(int(getattr(project, "target_chapters", 0) or 0), 1)
    return max(12, min(60, math.ceil(target_chapters / 15) + 8))


_ZH_LEADING_SUBJECT_RE = re.compile(
    r"^(?:主角|主人公|男主|女主|他|她|它|该角色|本角色)"
    r"(?:要|想|想要|需要|必须|得|会|能|将|去|应|应该|准备|打算)?"
)


def _strip_leading_subject_zh(clause: str | None, names: list[str] | None = None) -> str:
    """Strip a leading subject so a clause can be embedded *after* a subject.

    Category goal/need templates and genre themes are full sentences that
    already carry their own subject (e.g. ``主角要在现实规则中拿到位置``,
    ``主角需要学会…``). Jamming them verbatim into a ``{name}能否在{X}的同时，
    仍然{Y}？`` template produced the double-subject garble observed in
    《福星甩不掉》(``主角能否在主角要在…的同时，仍然主角需要…``). This removes a
    leading explicit name and/or a generic ``主角/他/她`` (+ optional modal verb)
    so the embedded clause reads grammatically. Trailing zh punctuation is
    trimmed by the caller.
    """

    text = (clause or "").strip()
    if not text:
        return ""
    for name in names or []:
        name = (name or "").strip()
        if name and name not in {"主角", "主人公"} and text.startswith(name):
            text = text[len(name) :].lstrip("，,、 　")
            break
    text = _ZH_LEADING_SUBJECT_RE.sub("", text).lstrip("，,、 　")
    return text.strip()


def _planner_tone_is_low_pressure(project: ProjectModel) -> bool:
    """Whether the project is a low-pressure (comedy/healing/cozy) tone.

    Reuses the canonical classifier so the planner fallbacks frame comedy books
    with comedy goals/needs/themes instead of the realist career-drama templates
    that the ``urban-contemporary`` category supplies (genre misroute fix).
    """

    try:
        from bestseller.services.invariants import is_low_pressure_tone

        meta = project.metadata_json if isinstance(project.metadata_json, dict) else {}
        pack_key = meta.get("prompt_pack_key") or meta.get("prompt_pack_name")
        return bool(
            is_low_pressure_tone(
                pack_key,
                getattr(project, "genre", None),
                getattr(project, "sub_genre", None),
            )
        )
    except Exception:
        return False


def _fallback_theme_statement(
    project: ProjectModel,
    premise: str,
    book_spec: dict[str, Any],
) -> str:
    low_pressure = _planner_tone_is_low_pressure(project)
    if low_pressure and not is_english_language(project.language):
        # Comedy/healing: a clean, tone-appropriate theme. Never mash a realist
        # career-goal sentence into a "真正的力量不是逃避X" frame.
        return "真正的从容，不是逃开所有麻烦，而是被麻烦缠上时还能笑着把日子过顺。"

    themes = _string_list(book_spec.get("themes"))
    if themes:
        first_theme = _strip_leading_subject_zh(
            themes[0], [str(_mapping(book_spec.get("protagonist")).get("name") or "")]
        ) or themes[0]
        if is_english_language(project.language):
            return f"True power is proven by what a person refuses to sacrifice when {first_theme.lower()} is tested."
        return f"真正的力量不是逃避{first_theme}，而是在代价逼近时仍守住自己不愿牺牲的东西。"

    profile = _genre_profile(project.genre, language=project.language)
    theme = _string_list(profile.get("themes"))[0] if _string_list(profile.get("themes")) else ""
    if is_english_language(project.language):
        return (
            f"Survival becomes meaningful only when the protagonist can choose truth over control"
            f" in a world shaped by {theme or 'fear'}."
        )
    return f"真正的胜利不是摆脱{theme or '恐惧'}，而是在真相与牺牲之间仍选择保护值得保护的人。"


def _fallback_dramatic_question(
    project: ProjectModel,
    premise: str,
    book_spec: dict[str, Any],
) -> str:
    protagonist = _mapping(book_spec.get("protagonist"))
    protagonist_name = _non_empty_string(
        protagonist.get("name"),
        _derive_protagonist_name(
            premise,
            project.genre,
            language=project.language,
            seed_text=_project_name_seed(project, premise),
        ),
    )
    external_goal = _non_empty_string(protagonist.get("external_goal"), "")
    internal_need = _non_empty_string(protagonist.get("internal_need"), "")
    low_pressure = _planner_tone_is_low_pressure(project)
    if is_english_language(project.language):
        goal_clause = (
            external_goal.rstrip(".?!")
            if external_goal
            else "expose the truth behind the central crisis"
        )
        need_clause = (
            internal_need.rstrip(".?!") if internal_need else "become someone who can trust others"
        )
        return f"Can {protagonist_name} {goal_clause} without losing the chance to {need_clause}?"
    # Guard against the literal "主角" placeholder leaking into the question:
    # derive a real name, and if that also fails, drop the leading subject
    # entirely (a subject-less rhetorical question is grammatical) rather than
    # emitting "主角能否…".
    if protagonist_name in {"主角", "主人公", ""}:
        derived = _derive_protagonist_name(
            premise,
            project.genre,
            language=project.language,
            seed_text=_project_name_seed(project, premise),
        )
        protagonist_name = derived if derived and derived not in {"主角", "主人公"} else ""
    # Strip the embedded clauses' own subject so the template doesn't produce
    # the double-subject mash (主角能否在主角要在…的同时，仍然主角需要…). The
    # template already supplies "能否在…", so also drop a redundant leading 在.
    goal_clause = re.sub(
        r"^在", "", _strip_leading_subject_zh(external_goal.rstrip("。？！"), [protagonist_name])
    ).strip()
    need_clause = _strip_leading_subject_zh(
        internal_need.rstrip("。？！"), [protagonist_name]
    )
    if not goal_clause:
        goal_clause = "把缠上门的麻烦一件件理顺" if low_pressure else "查清核心危机背后的真相"
    if not need_clause:
        need_clause = "守住自己想要的清静日子" if low_pressure else "完成真正的自我转变"
    return f"{protagonist_name}能否在{goal_clause}的同时，仍然{need_clause}？"


def _fallback_naming_pool(
    project: ProjectModel,
    *,
    premise: str,
    desired_count: int,
    reserved_names: list[str] | None = None,
) -> list[str]:
    seed_text = _project_name_seed(project, premise)
    reserved = [
        name.strip() for name in (reserved_names or []) if isinstance(name, str) and name.strip()
    ]
    names = list(dict.fromkeys(reserved))

    if is_english_language(project.language):
        first_names = _stable_order(
            [
                "Mara",
                "Theo",
                "Nora",
                "Elias",
                "Rowan",
                "Iris",
                "Caleb",
                "Vera",
                "Julian",
                "Mira",
                "Cassian",
                "Leah",
                "Silas",
                "Anika",
                "Dorian",
                "Selene",
                "Jonah",
                "Rhea",
                "Adrian",
                "Lyra",
            ],
            seed_text=seed_text,
            salt="naming-first",
        )
        last_names = _stable_order(
            [
                "Vale",
                "Cross",
                "Reed",
                "Hale",
                "Morrow",
                "Voss",
                "Ashford",
                "Kade",
                "Lennox",
                "Stone",
                "Marsh",
                "Black",
                "Quinn",
                "Ward",
                "Keene",
                "Rook",
                "Frost",
                "Sloane",
                "Wren",
                "Locke",
            ],
            seed_text=seed_text,
            salt="naming-last",
        )
        for first in first_names:
            for last in last_names:
                candidate = f"{first} {last}"
                if candidate not in names:
                    names.append(candidate)
                if len(names) >= desired_count:
                    return names
        return names

    surnames = _stable_order(
        [
            "林",
            "沈",
            "陆",
            "顾",
            "谢",
            "苏",
            "秦",
            "叶",
            "周",
            "许",
            "韩",
            "楚",
            "江",
            "白",
            "程",
            "姜",
            "洛",
            "方",
            "纪",
            "宋",
        ],
        seed_text=seed_text,
        salt="naming-surname",
    )
    given_a = _stable_order(
        [
            "青",
            "玄",
            "昭",
            "怀",
            "知",
            "临",
            "远",
            "澜",
            "星",
            "砚",
            "云",
            "照",
            "无",
            "清",
            "明",
            "若",
            "承",
            "景",
            "寒",
            "予",
        ],
        seed_text=seed_text,
        salt="naming-given-a",
    )
    given_b = _stable_order(
        [
            "川",
            "衡",
            "辞",
            "微",
            "舟",
            "宁",
            "晏",
            "真",
            "珩",
            "野",
            "棠",
            "声",
            "行",
            "尘",
            "阙",
            "安",
            "渊",
            "庭",
            "殊",
            "曜",
        ],
        seed_text=seed_text,
        salt="naming-given-b",
    )
    for surname in surnames:
        for first in given_a:
            for second in given_b:
                candidate = f"{surname}{first}{second}"
                if candidate not in names:
                    names.append(candidate)
                if len(names) >= desired_count:
                    return names
    return names


def _ensure_book_spec_bible_fields(
    project: ProjectModel,
    premise: str,
    book_spec: dict[str, Any],
) -> dict[str, Any]:
    """Ensure BookSpec carries the project-level L2 Bible Gate fields."""

    normalized = copy.deepcopy(_mapping(book_spec))
    expected = normalized.get("expected_character_count")
    try:
        expected_count = int(expected)
    except (TypeError, ValueError):
        expected_count = 0
    if expected_count <= 0:
        expected_count = _fallback_expected_character_count(project)
        normalized["expected_character_count"] = expected_count

    if not _non_empty_string(normalized.get("theme_statement"), ""):
        normalized["theme_statement"] = _fallback_theme_statement(project, premise, normalized)

    if not _non_empty_string(normalized.get("dramatic_question"), ""):
        normalized["dramatic_question"] = _fallback_dramatic_question(project, premise, normalized)

    protagonist = _mapping(normalized.get("protagonist"))
    reserved_names = [str(protagonist.get("name") or "").strip()]
    existing_pool = _string_list(normalized.get("naming_pool"))
    required_pool_size = max(1, expected_count * 2)
    merged_pool = list(dict.fromkeys([*reserved_names, *existing_pool]))
    if len([name for name in merged_pool if name]) < required_pool_size:
        merged_pool = _fallback_naming_pool(
            project,
            premise=premise,
            desired_count=required_pool_size,
            reserved_names=merged_pool,
        )
    normalized["naming_pool"] = [name for name in merged_pool if name][:required_pool_size]
    return normalized


_EN_TAXONOMY_TOKEN_RE = re.compile(
    r"\b[a-z][a-z0-9]*(?:-[a-z0-9]+)+\b"  # hyphenated genre slugs
    r"|\b[a-zA-Z]{4,}(?:_[a-zA-Z0-9]+)+\b"  # underscored mechanism labels
    r"|\b[a-zA-Z]{8,}\b"  # long bare English words
)


def _sanitize_zh_tone(
    raw_tone: Any,
    fallback_tones: Any,
    *,
    language: str | None,
) -> list[str]:
    """Strip English taxonomy/mechanism tokens from tone for Chinese books.

    A zh-CN book must never carry English genre slugs ("suspense-mystery") or
    mechanism labels in reader-visible prose. The model sometimes echoes the
    genre string it was given; this removes those tokens and falls back to the
    clean Chinese genre-profile tones when nothing usable remains.
    (2026-06-03 zh/en separation — book_spec gate regression.)
    """

    items = raw_tone if isinstance(raw_tone, list) else ([raw_tone] if raw_tone else [])
    if is_english_language(language):
        return [str(item).strip() for item in items if str(item).strip()]
    cleaned: list[str] = []
    for item in items:
        text = _EN_TAXONOMY_TOKEN_RE.sub("", str(item)).strip(" ，,、；;:：.。-")
        text = re.sub(r"\s{2,}", " ", text)
        if text and re.search(r"[一-鿿]", text):
            cleaned.append(text)
    if cleaned:
        return cleaned
    fb = fallback_tones if isinstance(fallback_tones, list) else ([fallback_tones] if fallback_tones else [])
    return [str(item).strip() for item in fb if str(item).strip()]


def _fallback_book_spec(
    project: ProjectModel, premise: str, *, category_key: str | None = None
) -> dict[str, Any]:
    profile = _genre_profile(project.genre, category_key=category_key, language=project.language)
    writing_profile = _planner_writing_profile(project)
    story_package = _load_story_package_seed(project)
    book_seed = _mapping(story_package.get("book"))
    reader_desire = _mapping(story_package.get("reader_desire_map"))
    story_bible = _mapping(story_package.get("story_bible"))
    route_graph = _mapping(story_package.get("route_graph"))
    name_seed = _project_name_seed(project, premise)
    protagonist_name = _derive_protagonist_name(
        premise,
        project.genre,
        language=project.language,
        seed_text=name_seed,
    )
    milestone_titles = [
        item.get("title")
        for item in _mapping_list(route_graph.get("milestones"))
        if item.get("title")
    ]
    story_tags = _string_list(book_seed.get("tags")) + _string_list(
        book_seed.get("interaction_tags")
    )
    story_themes = _string_list(story_bible.get("side_threads"))
    book_spec = {
        "title": project.title,
        "logline": (
            _non_empty_string(story_bible.get("premise"), "")
            or _non_empty_string(book_seed.get("synopsis"), "")
            or premise.strip()
        ),
        "genre": project.genre,
        "target_audience": project.audience or "web-serial",
        "tone": _sanitize_zh_tone(
            writing_profile.style.tone_keywords or profile["tones"],
            profile["tones"],
            language=project.language,
        ),
        "themes": [
            *profile["themes"],
            *[item for item in story_themes[:2] if item not in profile["themes"]],
            *[
                item
                for item in writing_profile.market.selling_points[:2]
                if item not in profile["themes"] and item not in story_themes[:2]
            ],
        ],
        "protagonist": _build_protagonist_from_category(
            protagonist_name,
            writing_profile=writing_profile,
            category_key=category_key,
            is_en=is_english_language(project.language),
            low_pressure=_planner_tone_is_low_pressure(project),
        ),
        "stakes": {
            "personal": f"{protagonist_name}会失去自己仍在意的人。",
            "social": "更大范围的秩序会因此崩坏，更多无辜者将被牵连。",
            "existential": "如果幕后计划成功，整个世界的基本运行秩序都会被改写。",
        },
        "series_engine": {
            "core_loop": (
                _non_empty_string(route_graph.get("mainline"), "")
                or "主角利用差异化优势抢先一步 -> 得到短回报 -> 引来更大反压 -> 被迫升级手段 -> 揭开更深真相"
            ),
            "hook_style": writing_profile.market.chapter_hook_strategy,
            "reader_promise": (
                _non_empty_string(reader_desire.get("core_fantasy"), "")
                or writing_profile.market.reader_promise
            ),
            "selling_points": list(
                dict.fromkeys(
                    _string_list(reader_desire.get("reward_promises"))[:3]
                    or writing_profile.market.selling_points
                )
            ),
            "trope_keywords": list(
                dict.fromkeys(story_tags[:4] or writing_profile.market.trope_keywords)
            ),
            "opening_strategy": writing_profile.market.opening_strategy,
            "payoff_rhythm": writing_profile.market.payoff_rhythm,
            "first_three_chapter_goal": writing_profile.serialization.first_three_chapter_goal,
            "control_promises": _string_list(reader_desire.get("control_promises"))[:3],
            "suspense_questions": _string_list(reader_desire.get("suspense_questions"))[:3],
            "mainline_milestones": milestone_titles[:6],
        },
    }
    return _ensure_book_spec_bible_fields(project, premise, book_spec)


def _fallback_world_spec(
    project: ProjectModel,
    premise: str,
    book_spec: dict[str, Any],
    *,
    category_key: str | None = None,
) -> dict[str, Any]:
    profile = _genre_profile(project.genre, category_key=category_key, language=project.language)
    name_seed = _project_name_seed(project, premise)
    protagonist_name = _protagonist_name_from_book_spec(
        book_spec,
        premise,
        project.genre,
        language=project.language,
        seed_text=name_seed,
    )
    template = _world_template(
        project.genre,
        language=project.language,
        protagonist_name=protagonist_name,
        seed_text=name_seed,
        category_key=category_key,
    )
    _is_en = is_english_language(project.language)
    location_names = _profile_seed_list(
        profile.get("locations"),
        ["Central City", "Restricted Zone", "Old Archive"]
        if _is_en
        else ["主城", "禁区", "旧档案馆"],
        min_items=3,
    )
    faction_names = _profile_seed_list(
        profile.get("factions"),
        ["Governing Authority", "Underground Alliance"]
        if _is_en
        else ["统治机关", "地下同盟"],
        min_items=2,
    )
    return {
        "world_name": profile["world_name"],
        "world_premise": profile["world_premise"],
        "rules": template["rules"],
        "power_system": {
            "name": profile["power_system_name"],
            "tiers": ["Novice", "Intermediate", "Advanced", "Apex"]
            if _is_en
            else ["低阶", "中阶", "高阶", "顶层"],
            "acquisition_method": "Advance through real adventure, resource competition, and high-pressure trials."
            if _is_en
            else "通过真实冒险、资源争夺和高压试炼提升。",
            "hard_limits": "Each tier leap exacts an irreversible cost — loss, sacrifice, or permanent trade-off."
            if _is_en
            else "每次跃迁都会伴随代价、损耗或不可逆牺牲。",
            "protagonist_starting_tier": "Novice" if _is_en else "低阶",
        },
        "locations": [
            {
                "name": location_names[0],
                "type": "Core Stronghold" if _is_en else "核心据点",
                "atmosphere": "High-pressure, regimented, conflict can erupt at any moment"
                if _is_en
                else "高压、秩序化、随时可能爆发冲突",
                "key_rules": ["R001", "R002"],
                "story_role": "Opening stage and source of oppressive order"
                if _is_en
                else "开局主舞台与秩序压迫的来源",
            },
            {
                "name": location_names[1],
                "type": "Danger Zone" if _is_en else "危险区域",
                "atmosphere": "Distorted, oppressive, forces characters into hard choices"
                if _is_en
                else "失真、压迫、逼迫人物做出选择",
                "key_rules": ["R003"],
                "story_role": "Site of investigation and climactic confrontation"
                if _is_en
                else "调查推进和高潮冲突发生地",
            },
            {
                "name": location_names[2],
                "type": "Ultimate Destination" if _is_en else "终极目标地",
                "atmosphere": "Mysterious, sealed, comes at a great cost"
                if _is_en
                else "神秘、封闭、伴随巨大代价",
                "key_rules": ["R001", "R002", "R003"],
                "story_role": "Repository of the final truth and critical evidence"
                if _is_en
                else "最终真相与关键证据的藏身处",
            },
        ],
        "factions": [
            {
                "name": faction_names[0],
                "goal": "Maintain the existing order and control."
                if _is_en
                else "维持既有秩序与控制力。",
                "method": "Suppress dissent through rules, resources, and coercive force."
                if _is_en
                else "通过规则、资源和强制力量压制异议。",
                "relationship_to_protagonist": "hostile" if _is_en else "敌对",
                "internal_conflict": "Some insiders know the truth but dare not take a public stand."
                if _is_en
                else "内部有人知道真相，但不敢公开站队。",
            },
            {
                "name": faction_names[1],
                "goal": "Hold on to survival space and win greater autonomy."
                if _is_en
                else "在夹缝中保住生存空间并获取更多自主权。",
                "method": "Back-channel deals, informal alliances, and grey-area operations."
                if _is_en
                else "私下交易、非正式合作与灰色行动。",
                "relationship_to_protagonist": "complicated" if _is_en else "复杂",
                "internal_conflict": "They want to use the protagonist but fear being dragged down with them."
                if _is_en
                else "既想利用主角，又担心被主角拖下水。",
            },
        ],
        "power_structure": template["power_structure"],
        "history_key_events": [
            {
                "event": template["history_event"],
                "relevance": "This is both the protagonist's open wound and the entry point to the current crisis."
                if _is_en
                else "这既是主角心结，也是当前主线危机的前史入口。",
            }
        ],
        "forbidden_zones": template["forbidden_zones"],
    }


def _build_default_conflict_forces(
    *,
    protagonist_name: str,
    antagonist_name: str,
    local_threat_name: str,
    betrayer_name: str,
    ally_name: str,
    volume_count: int,
    is_en: bool = False,
    category_key: str | None = None,
) -> list[dict[str, Any]]:
    """Generate neutral fallback conflict forces.

    These are intentionally structural instead of plot-specific so the
    genre preset does not smuggle in a prewritten storyline when the model
    omits the field.
    """
    phases = _assign_conflict_phases(volume_count, category_key=category_key)
    forces: list[dict[str, Any]] = []
    if is_en:
        phase_to_force: dict[str, dict[str, Any]] = {
            "survival": {
                "name": "Immediate Survival Pressure",
                "force_type": "faction",
                "threat_description": f"{local_threat_name} becomes the nearest obstacle to {protagonist_name}'s short-term survival and movement.",
                "relationship_to_protagonist": "Direct resistance during the opening stage.",
                "escalation_path": "Pressure starts local, then exposes a wider chain of control.",
                "character_ref": local_threat_name,
            },
            "political_intrigue": {
                "name": "Power Friction",
                "force_type": "systemic",
                "threat_description": "Rules, institutions, and gatekeepers begin resisting the protagonist's next move.",
                "relationship_to_protagonist": "The protagonist is forced to navigate systems instead of a single enemy.",
                "escalation_path": "Soft resistance hardens into visible restrictions and forced choices.",
            },
            "betrayal": {
                "name": "Trust Collapse",
                "force_type": "character",
                "threat_description": f"{betrayer_name} introduces a rupture inside the protagonist's trust network.",
                "relationship_to_protagonist": "An internal fracture becomes harder to ignore.",
                "escalation_path": "Doubt becomes exposure, then forces a painful reset of alliances.",
                "character_ref": betrayer_name,
            },
            "faction_war": {
                "name": "Multi-Side Collision",
                "force_type": "faction",
                "threat_description": "Multiple groups compete at once, making the protagonist a movable but costly variable.",
                "relationship_to_protagonist": "No side is fully safe; every move shifts the balance.",
                "escalation_path": "Competing agendas converge into open collision.",
            },
            "existential_threat": {
                "name": "Endgame Threat",
                "force_type": "character",
                "threat_description": f"{antagonist_name} or the structure behind them becomes impossible to ignore and raises the cost of failure for everyone.",
                "relationship_to_protagonist": "The main line can no longer be delayed or sidestepped.",
                "escalation_path": "The story shifts from containment to irreversible confrontation.",
                "character_ref": antagonist_name,
            },
            "internal_reckoning": {
                "name": "Internal Reckoning",
                "force_type": "internal",
                "threat_description": "Accumulated cost, guilt, and desire turn inward and reshape the protagonist's choices.",
                "relationship_to_protagonist": "The final resistance is now partly internal.",
                "escalation_path": "Self-doubt becomes decision pressure and then demands transformation.",
            },
        }
    else:
        phase_to_force: dict[str, dict[str, Any]] = {
            "survival": {
                "name": "生存压力",
                "force_type": "faction",
                "threat_description": f"{local_threat_name}成为{protagonist_name}当前阶段最直接的近身阻力。",
                "relationship_to_protagonist": "主角必须先处理眼前压力，主线才能继续推进。",
                "escalation_path": "局部阻力逐步暴露出更大的控制链条。",
                "character_ref": local_threat_name,
            },
            "political_intrigue": {
                "name": "权力摩擦",
                "force_type": "systemic",
                "threat_description": "规则、机构和把关者开始对主角形成成体系的反制。",
                "relationship_to_protagonist": "主角不再只面对单一敌人，而是面对一整套门槛。",
                "escalation_path": "从软性限制变成公开封锁与强制站队。",
            },
            "betrayal": {
                "name": "信任危机",
                "force_type": "character",
                "threat_description": f"{betrayer_name}让主角内部关系开始失稳，信任不再可靠。",
                "relationship_to_protagonist": "主角必须重新定义谁能并肩、谁只能利用。",
                "escalation_path": "从怀疑、试探，升级为暴露与割裂。",
                "character_ref": betrayer_name,
            },
            "faction_war": {
                "name": "多方冲突",
                "force_type": "faction",
                "threat_description": "不止一方在争夺主动权，主角每一次选择都会改变局势平衡。",
                "relationship_to_protagonist": "没有绝对安全边，所有阵营都可能同时施压。",
                "escalation_path": "多条冲突线相互撞击，形成更大场面。",
            },
            "existential_threat": {
                "name": "终局威胁",
                "force_type": "character",
                "threat_description": f"{antagonist_name}或其背后的结构终于抬到台前，失败代价开始覆盖更大范围。",
                "relationship_to_protagonist": "主角已经无法绕开主线核心矛盾。",
                "escalation_path": "故事从局部止损转入不可逆转的正面冲突。",
                "character_ref": antagonist_name,
            },
            "internal_reckoning": {
                "name": "内在拷问",
                "force_type": "internal",
                "threat_description": "主角一路积累的代价、欲望与伤口反过来影响最终选择。",
                "relationship_to_protagonist": "最后阶段的阻力已经部分来自主角内部。",
                "escalation_path": "从隐约不安发展为必须完成的自我重组。",
            },
        }
    for vol_idx, phase in enumerate(phases, start=1):
        base = phase_to_force.get(phase, phase_to_force["survival"])
        forces.append(
            {
                **base,
                "active_volumes": [vol_idx],
            }
        )
    return forces


def _fallback_cast_spec(
    project: ProjectModel,
    premise: str,
    book_spec: dict[str, Any],
    world_spec: dict[str, Any],
    *,
    category_key: str | None = None,
    character_name_pool: dict[str, Any] | None = None,
) -> dict[str, Any]:
    profile = _genre_profile(project.genre, category_key=category_key, language=project.language)
    writing_profile = _planner_writing_profile(project)
    is_en = is_english_language(project.language)
    story_package = _load_story_package_seed(project)
    story_characters = _mapping_list(_mapping(story_package.get("book")).get("characters"))
    protagonist = _mapping(_mapping(book_spec).get("protagonist"))
    name_seed = _project_name_seed(project, premise)
    protagonist_name = _protagonist_name_from_book_spec(
        book_spec,
        premise,
        project.genre,
        language=project.language,
        seed_text=name_seed,
    )
    external_goal = _non_empty_string(
        protagonist.get("external_goal"),
        (
            f"{protagonist_name} must track down and expose the orchestrator behind the current crisis."
            if is_en
            else f"{protagonist_name}必须主动追查并破解当前危机背后的操盘者。"
        ),
    )
    locations = _mapping_list(_mapping(world_spec).get("locations"))
    factions = _mapping_list(_mapping(world_spec).get("factions"))
    power_system = _mapping(_mapping(world_spec).get("power_system"))
    profile_locations = _profile_seed_list(
        profile.get("locations"),
        ["Central City"] if is_en else ["主城"],
        min_items=1,
    )
    profile_factions = _profile_seed_list(
        profile.get("factions"),
        ["Governing Authority"] if is_en else ["统治机关"],
        min_items=1,
    )
    home_location = _named_item(locations, 0, profile_locations[0])
    ruling_faction = _named_item(factions, 0, profile_factions[0])
    protagonist_tier = _non_empty_string(
        power_system.get("protagonist_starting_tier"),
        "low" if is_en else "低阶",
    )
    # Use LLM-generated name pool when available; fall back to static pool
    name_pool = (
        character_name_pool
        if character_name_pool
        else _genre_name_pool(project.genre, language=project.language, seed_text=name_seed)
    )
    pool_allies = [a["name"] for a in _mapping_list(name_pool.get("allies")) if a.get("name")]
    pool_antagonists = [
        a["name"] for a in _mapping_list(name_pool.get("antagonists")) if a.get("name")
    ]
    story_antagonists = [
        item.get("name")
        for item in story_characters
        if item.get("name")
        and str(item.get("role") or "").lower() in {"反派", "宿敌", "antagonist", "rival", "enemy"}
    ]
    story_supporters = [
        item.get("name")
        for item in story_characters
        if item.get("name")
        and item.get("name") != protagonist_name
        and item.get("name") not in story_antagonists
    ]
    ally_name = next(
        (n for n in pool_allies if n != protagonist_name),
        _role_label("ally", language=project.language, index=0),
    )
    if story_supporters:
        ally_name = story_supporters[0]
    antagonist_name = next((n for n in story_antagonists if n != protagonist_name), "")
    if not antagonist_name:
        antagonist_name = next(
            (n for n in pool_antagonists if n != protagonist_name),
            _role_label("antagonist", language=project.language),
        )
    # Extra names for multi-force conflict characters
    _used = {protagonist_name, ally_name, antagonist_name}
    _extra_allies = [n for n in pool_allies if n not in _used]
    _story_remaining = [n for n in story_supporters[1:] if n not in _used]
    local_threat_name = (
        _story_remaining[0]
        if _story_remaining
        else (
            _extra_allies[0]
            if _extra_allies
            else _role_label("local_threat", language=project.language)
        )
    )
    _used.add(local_threat_name)
    betrayer_name = next(
        (n for n in _story_remaining[1:] if n not in _used),
        next(
            (n for n in _extra_allies[1:] if n not in _used),
            _role_label("betrayer", language=project.language),
        ),
    )
    _used.add(betrayer_name)
    # Determine volume count for conflict force assignment
    total_chapters = max(project.target_chapters, 1)
    hierarchy = compute_linear_hierarchy(total_chapters)
    volume_count = hierarchy["volume_count"]
    extra_story_supporting_cast: list[dict[str, Any]] = []
    for item in story_characters:
        name = _non_empty_string(item.get("name"), "")
        if not name or name in {
            ally_name,
            local_threat_name,
            betrayer_name,
            antagonist_name,
            protagonist_name,
        }:
            continue
        role_text = str(item.get("role") or "").strip()
        normalized_role = (
            "antagonist"
            if role_text in {"反派", "宿敌", "antagonist", "rival", "enemy"}
            else "ally"
        )
        extra_story_supporting_cast.append(
            {
                "name": name,
                "role": normalized_role,
                "gender": "unknown",
                "pronoun_set_zh": "",
                "pronoun_set_en": "",
                "background": _non_empty_string(item.get("description"), item.get("title")),
                "goal": item.get("title"),
                "value_to_story": (
                    f"Retains the canonical story-package function of {name}."
                    if is_en
                    else f"保留 story_package 中 {name} 的既有叙事功能。"
                ),
                "arc_state": "opening" if is_en else "开场",
                "knowledge_state": {},
                "voice_profile": {},
                "moral_framework": {},
                "metadata": {
                    "story_package_role": role_text,
                    "story_package_title": item.get("title"),
                },
            }
        )
    return {
        "protagonist": {
            "name": protagonist_name,
            "age": 28,
            "role": "protagonist",
            "gender": "unknown",
            "pronoun_set_zh": "",
            "pronoun_set_en": "",
            "background": (
                f"Formerly worked within the system tied to {home_location['name']}, later pushed to the margins."
                if is_en
                else f"曾在{home_location['name']}所属体系中工作，后被边缘化。"
            ),
            "goal": external_goal,
            "fear": (
                "Causing the death of someone important through their own decisions — again."
                if is_en
                else "再次因为自己的决定害死重要的人。"
            ),
            "flaw": (
                "Shoulders every burden alone, refuses to delegate risk."
                if is_en
                else "习惯把压力全部扛在自己身上。"
            ),
            "strength": (
                "Hyper-alert to anomalies and shifts in risk."
                if is_en
                else "对异常细节和风险变化高度敏感。"
            ),
            "secret": (
                f"Suspects the losses around {home_location['name']} were engineered by record tampering inside {ruling_faction['name']}."
                if is_en
                else f"他怀疑{home_location['name']}的异常不是自然损耗，而是{ruling_faction['name']}内部改写记录造成。"
            ),
            "arc_trajectory": (
                "From lone wolf to building a sustainable alliance."
                if is_en
                else "从单打独斗到建立可持续同盟。"
            ),
            "arc_state": "opening" if is_en else "开场",
            "archetype": writing_profile.character.protagonist_archetype,
            "golden_finger": writing_profile.character.golden_finger,
            "knowledge_state": {
                "knows": (
                    ["Anomalies exist in the current crisis", "The official narrative has gaps"]
                    if is_en
                    else ["当前危机存在异常迹象", "官方叙事有漏洞"]
                ),
                "falsely_believes": (
                    [f"{ally_name} betrayed them during the original incident"]
                    if is_en
                    else [f"{ally_name}当年做出了背离自己的选择"]
                ),
                "unaware_of": (
                    [f"{antagonist_name} is directly linked to the past disaster"]
                    if is_en
                    else [f"{antagonist_name}与过去事故存在直接关联"]
                ),
            },
            "power_tier": protagonist_tier,
            "voice_profile": (
                {
                    "speech_register": "clipped and direct",
                    "verbal_tics": ["Forget it.", "I'll figure it out."],
                    "sentence_style": "short, punchy sentences",
                    "emotional_expression": "guarded",
                    "mannerisms": [
                        "rubs the bridge of their nose when thinking",
                        "drops voice at key moments",
                    ],
                    "internal_monologue_style": "fragmented self-interrogation",
                    "vocabulary_level": "mid",
                }
                if is_en
                else {
                    "speech_register": "口语偏利落",
                    "verbal_tics": ["……算了", "我来想办法"],
                    "sentence_style": "短句利落型",
                    "emotional_expression": "内敛",
                    "mannerisms": ["下意识揉眉心", "说到关键处压低声音"],
                    "internal_monologue_style": "碎片式自问自答",
                    "vocabulary_level": "中",
                }
            ),
            "moral_framework": (
                {
                    "core_values": ["Protect those nearby", "Truth matters more than order"],
                    "lines_never_crossed": ["Will not sacrifice innocents for intel"],
                    "willing_to_sacrifice": "Personal safety and social standing",
                }
                if is_en
                else {
                    "core_values": ["保护身边的人", "真相比秩序重要"],
                    "lines_never_crossed": ["不会牺牲无辜者换取情报"],
                    "willing_to_sacrifice": "个人安全和社会地位",
                }
            ),
            "relationships": [
                {
                    "character": ally_name,
                    "type": "former partner" if is_en else "旧搭档",
                    "tension": (
                        "Mutual respect for each other's skills, but old debts remain unsettled."
                        if is_en
                        else "彼此仍认可对方能力，但都有未说开的旧账。"
                    ),
                },
                {
                    "character": antagonist_name,
                    "type": "enemy" if is_en else "敌人",
                    "tension": (
                        "Both know the other is the wildcard in their plans."
                        if is_en
                        else "双方都知道对方会成为自己计划里最大的变量。"
                    ),
                },
            ],
        },
        "antagonist": {
            "name": antagonist_name,
            "role": "antagonist",
            "gender": "unknown",
            "pronoun_set_zh": "",
            "pronoun_set_en": "",
            "background": (
                f"A high-ranking power broker within {ruling_faction['name']}."
                if is_en
                else f"{ruling_faction['name']}中的高位操盘者。"
            ),
            "goal": (
                "Complete the restructuring plan and destroy all evidence before the truth surfaces."
                if is_en
                else "在真相曝光前完成既定重构计划并清除证据。"
            ),
            "fear": (
                "Being abandoned by those above once the core truth leaks."
                if is_en
                else "一旦核心真相外泄，自己会被更高层抛弃。"
            ),
            "flaw": (
                "Believes order will always matter more than people."
                if is_en
                else "相信秩序永远比人更重要。"
            ),
            "strength": (
                "Commands rules, resources, and enforcement power."
                if is_en
                else "掌握规则、资源与执行力量。"
            ),
            "secret": (
                "Was personally involved in the chain of decisions behind the protagonist's past failure."
                if is_en
                else "其本人直接参与了主角过去那场失败背后的决策链。"
            ),
            "arc_trajectory": (
                "From behind-the-scenes puppeteer to open pursuit of the protagonist."
                if is_en
                else "从幕后操盘到公开下场追杀主角。"
            ),
            "arc_state": "opening" if is_en else "开场",
            "knowledge_state": {
                "knows": (
                    [
                        "The protagonist has begun questioning the old case",
                        "Someone inside the system may defect",
                    ]
                    if is_en
                    else ["主角已经开始怀疑旧案", "体系里有人可能倒向主角"]
                ),
                "falsely_believes": (
                    [f"{ally_name} is still fully under control"]
                    if is_en
                    else [f"{ally_name}仍然完全可控"]
                ),
                "unaware_of": (
                    ["How quickly the protagonist will find the real evidence chain"]
                    if is_en
                    else ["主角会这么快找到真正证据链"]
                ),
            },
            "power_tier": "high" if is_en else "高阶",
            "voice_profile": (
                {
                    "speech_register": "polished and bureaucratic",
                    "verbal_tics": ["A necessary cost of order.", "You really think so?"],
                    "sentence_style": "long, analytical sentences",
                    "emotional_expression": "ice-calm with occasional contempt",
                    "mannerisms": ["avoids eye contact while speaking", "adjusts cuffs habitually"],
                    "internal_monologue_style": "cold strategic calculus",
                    "vocabulary_level": "high",
                }
                if is_en
                else {
                    "speech_register": "文雅官腔",
                    "verbal_tics": ["不过是秩序的代价", "你以为呢"],
                    "sentence_style": "长句思辨型",
                    "emotional_expression": "冷静克制、偶尔流露轻蔑",
                    "mannerisms": ["说话时不看对方眼睛", "习惯性整理袖口"],
                    "internal_monologue_style": "冷酷推演式",
                    "vocabulary_level": "高",
                }
            ),
            "moral_framework": (
                {
                    "core_values": ["Order above the individual", "Ends justify the means"],
                    "lines_never_crossed": [
                        "Never gets hands dirty — always lets the rules do the killing"
                    ],
                    "willing_to_sacrifice": "Anyone who obstructs the larger plan, including allies",
                }
                if is_en
                else {
                    "core_values": ["秩序高于个体", "结果证明手段"],
                    "lines_never_crossed": ["不会亲手动手——总让规则替自己执行"],
                    "willing_to_sacrifice": "任何妨碍大局的人，包括自己的盟友",
                }
            ),
            "relationships": [
                {
                    "character": protagonist_name,
                    "type": "target" if is_en else "追捕对象",
                    "tension": (
                        "Must suppress the protagonist without killing them too soon and losing control of the evidence trail."
                        if is_en
                        else "必须压制对方，但又不能让其过早死去以免线索失控。"
                    ),
                }
            ],
            "justification": (
                "So long as order holds, sacrificing the few is a necessary cost."
                if is_en
                else "只要秩序不崩，牺牲少数人就是必要成本。"
            ),
            "method": (
                "Falsify records, weaponize regulations, coordinate pursuit and resource blockades."
                if is_en
                else "删改记录、借规则压制、操控追捕和资源封锁。"
            ),
            "weakness": (
                "Over-reliance on institutional power structures."
                if is_en
                else "过度依赖体制和既有权力结构。"
            ),
            "relationship_to_protagonist": (
                "One of the key architects behind the protagonist's past defeat."
                if is_en
                else "主角过去那场惨败背后的关键责任人之一。"
            ),
            "reveal_timing": "end of volume 1" if is_en else "第一卷末",
        },
        "supporting_cast": [
            {
                "name": ally_name,
                "role": "ally",
                "gender": "unknown",
                "pronoun_set_zh": "",
                "pronoun_set_en": "",
                "background": (
                    "A former partner still operating inside the system."
                    if is_en
                    else "仍在体系内部活动的旧搭档。"
                ),
                "goal": (
                    "Confirm the truth of the old case while protecting those still caught in the crossfire."
                    if is_en
                    else "确认旧案真相并尽量保护仍在局中的人。"
                ),
                "value_to_story": (
                    "Provides operational capability, insider perspective, and emotional tension."
                    if is_en
                    else "提供行动力、体制内视角和情感张力。"
                ),
                "potential_betrayal": "medium" if is_en else "中",
                "arc_state": "cautious observer" if is_en else "谨慎观望",
                "knowledge_state": {
                    "knows": (
                        ["There is an unreleased record from the past incident"]
                        if is_en
                        else ["过去那场事故还有未公开的一段记录"]
                    ),
                    "falsely_believes": (
                        ["A low-profile investigation can avoid larger conflict"]
                        if is_en
                        else ["只要低调调查就能避免更大冲突"]
                    ),
                    "unaware_of": (
                        [f"{antagonist_name} has already flagged them as a potential liability"]
                        if is_en
                        else [f"{antagonist_name}已经将自己视为潜在隐患"]
                    ),
                },
                "voice_profile": (
                    {
                        "speech_register": "formal institutional tone with private sarcasm",
                        "verbal_tics": ["Listen to me.", "It's not that simple."],
                        "sentence_style": "mid-length, logical",
                        "emotional_expression": "calm surface, privately anxious",
                        "mannerisms": ["nervously touches an old ID badge in pocket"],
                        "internal_monologue_style": "constant pros-and-cons weighing",
                        "vocabulary_level": "mid-high",
                    }
                    if is_en
                    else {
                        "speech_register": "体制内正式用语夹杂私下吐槽",
                        "verbal_tics": ["你听我说", "这事儿没那么简单"],
                        "sentence_style": "中等长度、逻辑清晰",
                        "emotional_expression": "表面沉稳、私下焦虑",
                        "mannerisms": ["紧张时反复摸口袋里的旧证件"],
                        "internal_monologue_style": "反复权衡利弊",
                        "vocabulary_level": "中高",
                    }
                ),
                "moral_framework": (
                    {
                        "core_values": [
                            "Protect those still in the game",
                            "Loyalty with conditions",
                        ],
                        "lines_never_crossed": ["Will never sell out a former partner"],
                        "willing_to_sacrifice": "Their career inside the system",
                    }
                    if is_en
                    else {
                        "core_values": ["保护还在局中的人", "忠诚但有条件"],
                        "lines_never_crossed": ["不会出卖曾经的搭档"],
                        "willing_to_sacrifice": "自己在体系内的前途",
                    }
                ),
            },
            {
                "name": local_threat_name,
                "role": "antagonist",
                "gender": "unknown",
                "pronoun_set_zh": "",
                "pronoun_set_en": "",
                "background": (
                    f"A local power figure and {ruling_faction['name']}'s ground-level enforcer in the protagonist's area."
                    if is_en
                    else f"主角所在地区的实权人物，{ruling_faction['name']}在基层的执行者。"
                ),
                "goal": (
                    "Protect territory and vested interests; eliminate all destabilising elements."
                    if is_en
                    else "维护自己的地盘和既得利益，清除一切不稳定因素。"
                ),
                "flaw": (
                    "Short-sighted; only cares about immediate control."
                    if is_en
                    else "目光短浅，只关心眼前的控制权。"
                ),
                "strength": (
                    "Absolute grip on local resources and connections."
                    if is_en
                    else "对本地资源和人脉有绝对掌控力。"
                ),
                "secret": (
                    f"Has a hidden quid-pro-quo arrangement with {antagonist_name}."
                    if is_en
                    else f"私下与{antagonist_name}有利益输送关系。"
                ),
                "arc_trajectory": (
                    "From local bully to discarded pawn of the higher-ups."
                    if is_en
                    else "从地方小霸到被更高层抛弃的弃子。"
                ),
                "arc_state": "opening" if is_en else "开场",
                "power_tier": "mid" if is_en else "中阶",
                "voice_profile": (
                    {
                        "speech_register": "blunt and aggressive",
                        "verbal_tics": ["On my turf.", "Who the hell are you?"],
                        "sentence_style": "short barked commands",
                        "emotional_expression": "openly volatile",
                        "mannerisms": ["slams the table when making a point"],
                        "internal_monologue_style": "crude, profit-first calculus",
                        "vocabulary_level": "low",
                    }
                    if is_en
                    else {
                        "speech_register": "粗犷直接",
                        "verbal_tics": ["在我地盘上", "你算什么东西"],
                        "sentence_style": "短句命令型",
                        "emotional_expression": "外放暴躁",
                        "mannerisms": ["说话时习惯拍桌子"],
                        "internal_monologue_style": "简单粗暴的利益算计",
                        "vocabulary_level": "低",
                    }
                ),
                "moral_framework": (
                    {
                        "core_values": ["Territory is everything", "Might makes right"],
                        "lines_never_crossed": [],
                        "willing_to_sacrifice": "Anyone standing between them and profit",
                    }
                    if is_en
                    else {
                        "core_values": ["地盘就是一切", "拳头大的说了算"],
                        "lines_never_crossed": [],
                        "willing_to_sacrifice": "任何挡在利益面前的人",
                    }
                ),
            },
            {
                "name": betrayer_name,
                "role": "ally",
                "gender": "unknown",
                "pronoun_set_zh": "",
                "pronoun_set_en": "",
                "background": (
                    "One of the protagonist's trusted companions who helped at a critical moment."
                    if is_en
                    else "主角信任的同伴之一，曾在关键时刻提供过帮助。"
                ),
                "goal": (
                    "Ostensibly helps the protagonist while secretly advancing a hidden agenda."
                    if is_en
                    else "表面上协助主角，实际上在为自己的秘密目标铺路。"
                ),
                "flaw": (
                    "Cannot let go of personal ambition." if is_en else "无法割舍自己的野心。"
                ),
                "strength": (
                    "Expert at masking true intentions; exceptional social skills."
                    if is_en
                    else "善于隐藏真实意图，社交能力极强。"
                ),
                "secret": (
                    "Has already struck a deal with a higher power behind the scenes."
                    if is_en
                    else "早已在暗中与更高层势力达成交易。"
                ),
                "arc_trajectory": (
                    "From trusted companion to betrayer, ultimately consumed by their own choices."
                    if is_en
                    else "从可靠同伴到背叛者，最终被自己的选择反噬。"
                ),
                "arc_state": "disguise phase" if is_en else "伪装期",
                "power_tier": protagonist_tier,
                "voice_profile": (
                    {
                        "speech_register": "warm and reassuring",
                        "verbal_tics": ["Don't worry.", "Leave it to me."],
                        "sentence_style": "smooth mid-length sentences",
                        "emotional_expression": "surface warmth and care",
                        "mannerisms": ["always the first to volunteer help"],
                        "internal_monologue_style": "precision-engineered insincerity",
                        "vocabulary_level": "mid-high",
                    }
                    if is_en
                    else {
                        "speech_register": "温和亲切",
                        "verbal_tics": ["放心", "交给我"],
                        "sentence_style": "柔和中等长度",
                        "emotional_expression": "表面温暖体贴",
                        "mannerisms": ["总是第一个主动帮忙"],
                        "internal_monologue_style": "精密计算的伪善",
                        "vocabulary_level": "中高",
                    }
                ),
                "moral_framework": (
                    {
                        "core_values": ["Self-interest above all"],
                        "lines_never_crossed": [],
                        "willing_to_sacrifice": "Anyone's trust",
                    }
                    if is_en
                    else {
                        "core_values": ["自己的利益高于一切"],
                        "lines_never_crossed": [],
                        "willing_to_sacrifice": "任何人的信任",
                    }
                ),
            },
        ]
        + extra_story_supporting_cast,
        "antagonist_forces": _build_default_conflict_forces(
            protagonist_name=protagonist_name,
            antagonist_name=antagonist_name,
            local_threat_name=local_threat_name,
            betrayer_name=betrayer_name,
            ally_name=ally_name,
            volume_count=volume_count,
            is_en=is_en,
            category_key=category_key,
        ),
        "conflict_map": [
            {
                "character_a": protagonist_name,
                "character_b": ally_name,
                "conflict_type": "emotional entanglement" if is_en else "情感纠葛",
                "trigger_condition": (
                    "Whenever the past failure is mentioned, old misunderstandings between the two reignite."
                    if is_en
                    else "一旦谈到过去那场失败，两人的旧误会就会被重新点燃。"
                ),
            },
            {
                "character_a": protagonist_name,
                "character_b": antagonist_name,
                "conflict_type": "goal opposition" if is_en else "目标冲突",
                "trigger_condition": (
                    "Once the protagonist gets close to the core evidence, the antagonist must escalate openly."
                    if is_en
                    else "主角一旦接近核心证据链，反派就必须公开加压。"
                ),
            },
            {
                "character_a": protagonist_name,
                "character_b": local_threat_name,
                "conflict_type": "survival" if is_en else "生存对抗",
                "trigger_condition": (
                    "When the protagonist operates on the local power figure's territory."
                    if is_en
                    else "主角在地方势力的地盘上展开行动时。"
                ),
            },
            {
                "character_a": protagonist_name,
                "character_b": betrayer_name,
                "conflict_type": "hidden betrayal" if is_en else "隐性背叛",
                "trigger_condition": (
                    "The closer the protagonist gets to the truth, the faster the betrayer must advance their own plan."
                    if is_en
                    else "主角越接近真相，背叛者越需要加速自己的计划。"
                ),
            },
        ],
    }


def compute_linear_hierarchy(total_chapters: int) -> dict[str, int]:
    """Compute act/volume/arc counts for a LINEAR novel based on total chapter count.

    Returns a dict with keys: act_count, volume_count, arc_batch_size.

    The hierarchy scales naturally with novel length:
    - arc_batch_size is fixed at 12 (the narrative rhythm atom)
    - volume_count grows with chapters (~30-50 chapters per volume)
    - act_count grows slowly (macro narrative arcs, max 6)

    Backward compatible: novels ≤50 chapters get act_count=1, volume_count=1,
    behaving identically to the old system.
    """
    arc_batch_size = 12

    # Volume count: ~30-50 chapters per volume
    if total_chapters <= 50:
        volume_count = 1
    elif total_chapters <= 120:
        volume_count = max(2, round(total_chapters / 30))
    else:
        # Ensure monotonicity at the 120→121 boundary: never fewer volumes
        # than at 120 chapters (which yields 4 via round(120/30)).
        volume_count = max(4, math.ceil(total_chapters / 50))

    # Act count: macro narrative structure (1-6 acts)
    if total_chapters <= 50:
        act_count = 1
    elif total_chapters <= 120:
        act_count = 3
    elif total_chapters <= 300:
        act_count = 4
    elif total_chapters <= 1500:
        act_count = 5
    else:
        act_count = 6

    return {
        "act_count": act_count,
        "volume_count": volume_count,
        "arc_batch_size": arc_batch_size,
    }


def _build_volume_ranges(total_chapters: int, volume_count: int) -> list[tuple[int, int]]:
    base = total_chapters // volume_count
    remainder = total_chapters % volume_count
    ranges: list[tuple[int, int]] = []
    cursor = 1
    for index in range(volume_count):
        count = base + (1 if index < remainder else 0)
        ranges.append((cursor, cursor + count - 1))
        cursor += count
    return ranges


_VOLUME_TITLE_BY_PHASE: dict[str, str] = {
    "survival": "绝境",
    "political_intrigue": "暗棋",
    "betrayal": "裂痕",
    "faction_war": "乱局",
    "existential_threat": "终焉",
    "internal_reckoning": "蜕变",
}

_VOLUME_TITLE_BY_PHASE_EN: dict[str, str] = {
    "survival": "Crucible",
    "political_intrigue": "Shadows",
    "betrayal": "Fractures",
    "faction_war": "Chaos",
    "existential_threat": "Endgame",
    "internal_reckoning": "Reckoning",
}

_VOLUME_GOAL_TEMPLATES: dict[str, str] = {
    "survival": "{protagonist}必须在{force_name}的直接威胁下争取到生存空间和初步的反击资本。",
    "political_intrigue": "{protagonist}需要看穿{force_name}背后的权力博弈，找到可以利用的裂缝。",
    "betrayal": "{protagonist}必须在{force_name}造成的信任崩塌中重新确认谁是真正的盟友。",
    "faction_war": "{protagonist}需要在{force_name}引发的多方混战中找到自己的立足之地。",
    "existential_threat": "{protagonist}必须倾尽所有力量去阻止{force_name}带来的终极灾难。",
    "internal_reckoning": "{protagonist}必须直面自己内心最深处的矛盾，完成真正的蜕变。",
}

_VOLUME_GOAL_TEMPLATES_EN: dict[str, str] = {
    "survival": "{protagonist} must carve out survival space and initial leverage against {force_name}'s direct threat.",
    "political_intrigue": "{protagonist} must see through {force_name}'s power games and find a crack to exploit.",
    "betrayal": "{protagonist} must determine who is truly loyal after {force_name} shatters the circle of trust.",
    "faction_war": "{protagonist} must find solid ground amid the all-out conflict sparked by {force_name}.",
    "existential_threat": "{protagonist} must commit everything to stop the catastrophe {force_name} is about to unleash.",
    "internal_reckoning": "{protagonist} must confront the deepest contradictions inside themselves and emerge transformed.",
}

_VOLUME_RESOLUTION_TEMPLATES: dict[str, str] = {
    "survival": "主角暂时挣脱了生存危机，但代价是暴露在更大势力的视野中。",
    "political_intrigue": "主角撕开了权力网络的一角，却发现更深层的阴谋才刚刚浮出水面。",
    "betrayal": "主角在信任废墟上重建了新的同盟，但伤痕不会轻易愈合。",
    "faction_war": "大战暂时平息，但格局已经彻底改变，主角的位置也随之转变。",
    "existential_threat": "主角以巨大的代价阻止了最坏的结果，世界暂时稳住但永远改变了。",
    "internal_reckoning": "主角完成了蜕变，以全新的姿态面对之后的道路。",
}

_VOLUME_RESOLUTION_TEMPLATES_EN: dict[str, str] = {
    "survival": "The protagonist escapes the immediate crisis, but the cost is exposure to far larger forces.",
    "political_intrigue": "The protagonist tears open one corner of the power web, only to find the deeper conspiracy just surfacing.",
    "betrayal": "The protagonist rebuilds alliances on the ruins of trust, but the scars won't heal easily.",
    "faction_war": "The battle pauses, but the landscape is permanently altered and the protagonist's role with it.",
    "existential_threat": "The protagonist stops the worst outcome at enormous cost; the world holds but is forever changed.",
    "internal_reckoning": "The protagonist completes the transformation and faces the road ahead as a new person.",
}


def _volume_goal_achieved(volume_number: int, volume_count: int) -> bool:
    """Determine whether the protagonist achieves the volume goal.

    Creates a realistic wave of victories and setbacks:
    - Volume 1: True (initial hook victory)
    - Final volume: True (resolution)
    - Penultimate volume: False (major setback before the finale)
    - Middle volumes: alternate, leaning toward failure in the
      middle third of the book (the "crisis zone").
    """
    if volume_count <= 2:
        return True
    if volume_number == 1 or volume_number == volume_count:
        return True
    if volume_number == volume_count - 1:
        return False  # penultimate setback
    # Middle volumes: fail in the crisis zone (40%-70% of the book)
    progress = volume_number / volume_count
    if 0.4 <= progress <= 0.7:
        return False
    # Outside crisis zone: alternate (even=True, odd=False)
    return volume_number % 2 == 0


def _volume_plan_validator_for(project: ProjectModel) -> Callable[[Any], Any]:
    """Validator for freshly generated volume plans (P-6 contract).

    Parses the payload, then rejects plans that under-plan the book
    (volume count mismatch / chapter coverage shortfall / missing
    next-volume hooks) so the structured-artifact retry loop feeds the
    diagnostic codes back to the model. Repair paths keep using the plain
    parser — they operate on already-accepted plans.
    """

    target_chapters = max(int(project.target_chapters or 0), 1)
    expected_volume_count = int(
        compute_linear_hierarchy(target_chapters).get("volume_count") or 1
    )

    def _validate(content: Any) -> Any:
        parsed = parse_volume_plan_input(content)
        enforce_volume_plan_contract(
            parsed,
            expected_volume_count=expected_volume_count,
            target_chapters=target_chapters,
        )
        return parsed

    return _validate


def _fallback_volume_plan(
    project: ProjectModel,
    book_spec: dict[str, Any],
    cast_spec: dict[str, Any],
    world_spec: dict[str, Any],
    *,
    category_key: str | None = None,
) -> list[dict[str, Any]]:
    profile = _genre_profile(project.genre, category_key=category_key, language=project.language)
    total_chapters = max(project.target_chapters, 1)
    hierarchy = compute_linear_hierarchy(total_chapters)
    volume_count = hierarchy["volume_count"]
    chapter_ranges = _build_volume_ranges(total_chapters, volume_count)
    cast_payload = _mapping(cast_spec)
    is_en = is_english_language(project.language)
    protagonist_name = _non_empty_string(
        _mapping(cast_payload.get("protagonist")).get("name"),
        "protagonist" if is_en else "主角",
    )
    protagonist_goal = _non_empty_string(
        _mapping(_mapping(book_spec).get("protagonist")).get("external_goal")
        or _mapping(book_spec).get("logline"),
        "Advance the main story." if is_en else "推进主线目标。",
    )
    story_package = _load_story_package_seed(project)
    route_graph = _mapping(story_package.get("route_graph"))
    milestone_entries = _mapping_list(route_graph.get("milestones"))
    hidden_routes_raw = route_graph.get("hidden_routes")
    hidden_routes: list[str] = []
    if isinstance(hidden_routes_raw, list):
        for item in hidden_routes_raw:
            if isinstance(item, dict):
                route_text = _non_empty_string(
                    item.get("reveal"),
                    _non_empty_string(
                        item.get("summary"), _non_empty_string(item.get("title"), "")
                    ),
                )
                if route_text:
                    hidden_routes.append(route_text)
            elif isinstance(item, str) and item.strip():
                hidden_routes.append(item.strip())
    antagonist_name = _non_empty_string(
        _mapping(cast_payload.get("antagonist")).get("name"),
        "the antagonist" if is_en else "敌对操盘者",
    )
    themes = _string_list(_mapping(book_spec).get("themes")) or profile["themes"]
    power_system = _mapping(_mapping(world_spec).get("power_system"))
    protagonist_tier = _non_empty_string(
        power_system.get("protagonist_starting_tier"),
        "low" if is_en else "低阶",
    )
    worldview_volume_contract = _fallback_worldview_volume_contract(project)

    # Use conflict forces if available, otherwise fall back to single-antagonist
    antagonist_forces = _mapping_list(cast_payload.get("antagonist_forces"))
    conflict_phases = _assign_conflict_phases(volume_count, category_key=category_key)
    # Build volume→force mapping
    force_by_volume: dict[int, dict[str, Any]] = {}
    for force_raw in antagonist_forces:
        force = _mapping(force_raw)
        for vol in force.get("active_volumes") or []:
            if isinstance(vol, int):
                force_by_volume[vol] = force

    plan: list[dict[str, Any]] = []
    phase_occurrence_counter: dict[str, int] = {}
    used_titles: set[str] = set()
    for volume_number, (chapter_start, chapter_end) in enumerate(chapter_ranges, start=1):
        phase = conflict_phases[min(volume_number - 1, len(conflict_phases) - 1)]
        force = force_by_volume.get(volume_number, {})
        force_name = _non_empty_string(force.get("name"), antagonist_name)
        milestone = (
            milestone_entries[volume_number - 1]
            if volume_number - 1 < len(milestone_entries)
            else {}
        )
        milestone_title = _non_empty_string(_mapping(milestone).get("title"), "")
        phase_occurrence = phase_occurrence_counter.get(phase, 0)
        phase_occurrence_counter[phase] = phase_occurrence + 1
        if milestone_title:
            volume_title = milestone_title
        else:
            volume_title = _resolve_fallback_volume_title(
                phase, phase_occurrence, volume_number, is_en=is_en
            )
            # Disambiguate if an LLM-provided milestone happens to collide
            # with a fallback-composed title. We escalate via additional
            # cycle composition rather than emitting a "·二"/"·第N卷"
            # sequel tag, which reads as a clumsy suffix.
            _extra_cycle = 1
            while volume_title in used_titles and _extra_cycle <= 12:
                volume_title = _compose_cycle_title(
                    _resolve_fallback_volume_title(
                        phase, phase_occurrence, volume_number, is_en=is_en
                    ),
                    _extra_cycle,
                    is_en=is_en,
                )
                _extra_cycle += 1
        used_titles.add(volume_title)
        # Try category-specific phase templates; fall back to generic text
        phase_tpl = _resolve_phase_templates(phase, category_key=category_key, is_en=is_en)
        tpl_vars = {"protagonist": protagonist_name, "force_name": force_name}
        vol_goal = _render_template(phase_tpl.get("goal", ""), tpl_vars) or (
            f"{protagonist_name} continues pushing the main objective while forcing a new stage of movement around {force_name}."
            if is_en
            else f"{protagonist_name}围绕主线目标继续推进，并迫使与「{force_name}」相关的局势进入新阶段。"
        )
        vol_obstacle = _render_template(phase_tpl.get("obstacle", ""), tpl_vars) or (
            f"{force_name} becomes the key resistance of this volume and turns progress into a cost-bearing choice."
            if is_en
            else f"{force_name}成为本卷关键阻力，让推进主线的每一步都必须付出更明确的代价。"
        )
        vol_climax = _render_template(phase_tpl.get("climax", ""), tpl_vars) or (
            f"In the volume climax, {protagonist_name} must make a high-cost decision that determines whether the main line can continue."
            if is_en
            else f"在本卷高潮里，{protagonist_name}必须做出一次高代价抉择，决定主线能否继续推进。"
        )
        vol_resolution_text = _render_template(phase_tpl.get("resolution", ""), tpl_vars) or (
            "A stage is resolved, but the resulting cost, fracture, or imbalance cannot be taken back."
            if is_en
            else "阶段问题暂时落地，但由此产生的新代价、裂缝或失衡无法撤回。"
        )

        # Compute arc ranges within this volume
        arc_batch_size = hierarchy["arc_batch_size"]
        arcs: list[list[int]] = []
        cursor = chapter_start
        while cursor <= chapter_end:
            arc_end = min(cursor + arc_batch_size - 1, chapter_end)
            arcs.append([cursor, arc_end])
            cursor = arc_end + 1

        volume_payload = {
            "volume_number": volume_number,
                "volume_title": volume_title,
                "volume_theme": themes[(volume_number - 1) % len(themes)],
                "word_count_target": int(project.target_word_count / volume_count),
                "chapter_count_target": chapter_end - chapter_start + 1,
                "conflict_phase": phase,
                "primary_force_name": force_name,
                "opening_state": {
                    "protagonist_status": (
                        (
                            "The protagonist is already under pressure and cannot stay still."
                            if volume_number == 1
                            else f"The protagonist enters Volume {volume_number} from the aftereffects of the previous stage."
                        )
                        if is_en
                        else (
                            "主角已经被推入高压局面，无法停在原地。"
                            if volume_number == 1
                            else f"主角带着上一卷的后果进入第{volume_number}卷。"
                        )
                    ),
                    "protagonist_power_tier": protagonist_tier
                    if volume_number == 1
                    else ("Changed by the previous stage" if is_en else "经历上一阶段后的新状态"),
                    "world_situation": (
                        f"A new layer of pressure forms around {force_name}."
                        if is_en
                        else f"围绕「{force_name}」的新一层压力开始成形。"
                    ),
                },
                "volume_goal": vol_goal,
                "volume_obstacle": vol_obstacle,
                "volume_climax": vol_climax,
                "volume_resolution": {
                    "protagonist_power_tier": (
                        ("mid" if is_en else "中阶") if volume_number >= 2 else protagonist_tier
                    ),
                    "goal_achieved": _volume_goal_achieved(volume_number, volume_count),
                    "cost_paid": vol_resolution_text,
                    "new_threat_introduced": (
                        (
                            f"A new pressure source for Volume {volume_number + 1} is now unavoidable."
                            if volume_number < volume_count
                            else "All active lines now converge."
                        )
                        if is_en
                        else (
                            f"第{volume_number + 1}卷的新压力来源已经无法回避。"
                            if volume_number < volume_count
                            else "所有主线开始汇聚。"
                        )
                    ),
                },
                "key_reveals": [
                    (
                        f"Volume {volume_number} reveals information that changes how the protagonist understands the main objective: {protagonist_goal}"
                        if is_en
                        else f"第{volume_number}卷揭示一条会改变主角理解主线目标的新信息：{protagonist_goal}"
                    ),
                    *(
                        [
                            (
                                f"Hidden route in play: {hidden_routes[volume_number - 1]}"
                                if is_en
                                else f"暗线开始发酵：{hidden_routes[volume_number - 1]}"
                            )
                        ]
                        if volume_number - 1 < len(hidden_routes)
                        else []
                    ),
                ],
                # P-3 (xianxia benchmark): these used to carry INSTRUCTION
                # sentences / boilerplate hooks. Because the structured-artifact
                # merge backfills fallback values into missing LLM fields, the
                # scaffold text shipped inside production artifacts as if it
                # were plan content (zhaoshen-hr-v4 vol1 hook verbatim). Honest
                # emptiness instead: empty fields trip
                # enforce_volume_plan_contract / foreshadowing gates, while
                # boilerplate sails through everything.
                "foreshadowing_planted": [],
                "foreshadowing_paid_off": [],
                "reader_hook_to_next": (
                    (
                        f"Milestone '{milestone_title}' lands, but the next commercial escalation is already visible."
                        if is_en
                        else f"「{milestone_title}」这个里程碑落地后，更大的商业钩子已经抬头。"
                    )
                    if milestone_title and volume_number < volume_count
                    else ""
                ),
                "arc_ranges": arcs,
            "is_final_volume": volume_number == volume_count,
        }
        if worldview_volume_contract:
            volume_payload.update(
                {
                    "world_state_targets": [
                        f"{key} +{min(volume_number, 3)}"
                        for key in _string_list(
                            worldview_volume_contract.get("world_state_target_keys")
                        )[:2]
                    ],
                    "active_authority_claims": list(
                        _string_list(worldview_volume_contract.get("active_authority_claims"))
                    ),
                    "map_function": _render_worldview_map_function(
                        worldview_volume_contract,
                        volume_number=volume_number,
                        is_en=is_en,
                    ),
                    "world_asset_refs": list(
                        _string_list(worldview_volume_contract.get("world_asset_refs"))
                    ),
                    "asset_risk_escalation": _render_worldview_asset_risk_escalation(
                        worldview_volume_contract,
                        volume_number=volume_number,
                        is_en=is_en,
                    ),
                    "reveal_budget": 1,
                }
            )
        plan.append(volume_payload)
    return plan


def _fallback_worldview_volume_contract(project: ProjectModel) -> dict[str, Any]:
    metadata = _mapping(getattr(project, "metadata_json", None))
    kernel = _mapping(metadata.get("story_design_kernel") or metadata.get("story_design"))
    worldview = _mapping(kernel.get("worldview_kernel"))
    if not worldview:
        return {}

    state_variables = _mapping_list(worldview.get("state_variables"))
    asset_ledger = _mapping_list(worldview.get("asset_ledger"))
    authority_claims = _mapping_list(worldview.get("authority_claims"))
    scene_templates = _mapping_list(worldview.get("scene_templates"))
    locations = _mapping_list(worldview.get("locations"))
    invariants = _mapping_list(worldview.get("invariants"))
    systems = _mapping_list(worldview.get("systems"))
    factions = _mapping_list(worldview.get("factions"))

    first_asset = asset_ledger[0] if asset_ledger else {}
    asset_risk_parts = [
        _non_empty_string(first_asset.get("cost"), ""),
        _non_empty_string(first_asset.get("exposure_risk"), ""),
    ]
    first_claim = authority_claims[0] if authority_claims else {}
    claim_ref = _non_empty_string(
        first_claim.get("target") or first_claim.get("claimant"),
        "",
    )
    return {
        "world_state_target_keys": [
            _non_empty_string(item.get("key"), "")
            for item in state_variables
            if _non_empty_string(item.get("key"), "")
        ],
        "active_authority_claims": [claim_ref] if claim_ref else [],
        "map_location": _non_empty_string(
            (locations[0] if locations else {}).get("name"),
            "",
        ),
        "map_rule": _non_empty_string(
            (invariants[0] if invariants else {}).get("rule")
            or (systems[0] if systems else {}).get("operating_logic"),
            "",
        ),
        "map_pressure": _non_empty_string(
            (factions[0] if factions else {}).get("name") or first_claim.get("claimant"),
            "",
        ),
        "world_asset_refs": [
            _non_empty_string(first_asset.get("key"), "")
        ]
        if _non_empty_string(first_asset.get("key"), "")
        else [],
        "asset_risk_base": " ".join(item for item in asset_risk_parts if item),
        "scene_template_refs": [
            _non_empty_string(item.get("key"), "")
            for item in scene_templates
            if _non_empty_string(item.get("key"), "")
        ],
    }


def _render_worldview_map_function(
    contract: Mapping[str, Any],
    *,
    volume_number: int,
    is_en: bool,
) -> str:
    location = _non_empty_string(contract.get("map_location"), "")
    rule = _non_empty_string(contract.get("map_rule"), "")
    pressure = _non_empty_string(contract.get("map_pressure"), "")
    if is_en:
        return (
            f"Volume {volume_number} uses {location or 'the primary map'} to demonstrate "
            f"{rule or 'a worldview rule'} and create faction/authority pressure from "
            f"{pressure or 'the active authority'}."
        )
    return (
        f"第{volume_number}卷用「{location or '核心地图'}」展示"
        f"「{rule or '世界规则'}」，并制造来自「{pressure or '活跃权威'}」的势力/权威压力。"
    )


def _render_worldview_asset_risk_escalation(
    contract: Mapping[str, Any],
    *,
    volume_number: int,
    is_en: bool,
) -> str:
    base = _non_empty_string(contract.get("asset_risk_base"), "")
    if is_en:
        return (
            f"Stage {volume_number} asset use increases exposure: {base}"
            if base
            else f"Stage {volume_number} asset use must increase cost, exposure, or attention."
        )
    return (
        f"第{volume_number}层资产使用提高暴露：{base}"
        if base
        else f"第{volume_number}层资产使用必须提高代价、暴露或注意力。"
    )


def _fallback_act_plan(
    project: ProjectModel,
    book_spec: dict[str, Any],
    cast_spec: dict[str, Any],
    world_spec: dict[str, Any],
) -> list[dict[str, Any]]:
    """Generate a neutral act skeleton for long novels."""
    total_chapters = max(project.target_chapters, 1)
    hierarchy = compute_linear_hierarchy(total_chapters)
    act_count = hierarchy["act_count"]
    arc_batch_size = hierarchy["arc_batch_size"]

    is_en = is_english_language(project.language)
    protagonist_name = _non_empty_string(
        _mapping(_mapping(cast_spec).get("protagonist")).get("name"),
        "Protagonist" if is_en else "主角",
    )
    themes = _string_list(_mapping(book_spec).get("themes"))
    default_emotions = (
        ["focused", "tense", "strained", "sharp", "resolute", "cathartic"]
        if is_en
        else ["专注", "紧张", "拉扯", "尖锐", "决断", "释放"]
    )

    act_size = total_chapters // act_count
    acts: list[dict[str, Any]] = []

    for i in range(act_count):
        start = i * act_size + 1
        end = (i + 1) * act_size if i < act_count - 1 else total_chapters
        theme = themes[i % len(themes)] if themes else ("Core progression" if is_en else "主线推进")
        emotion = default_emotions[min(i, len(default_emotions) - 1)]
        goal = (
            f"Move the main story from stage {i + 1} into stage {i + 2 if i + 1 < act_count else i + 1}."
            if is_en
            else f"把主线从第{i + 1}阶段推进到下一阶段。"
        )

        # Build arc breakdown within this act
        arcs: list[dict[str, Any]] = []
        arc_start = start
        arc_idx = 0
        while arc_start <= end:
            arc_end = min(arc_start + arc_batch_size - 1, end)
            arc_goal = (
                f"Advance the core conflict of the {theme} phase"
                if is_en
                else f"推进{theme}阶段的核心冲突"
            )
            arcs.append(
                {
                    "arc_index": arc_idx,
                    "chapter_start": arc_start,
                    "chapter_end": arc_end,
                    "arc_goal": arc_goal,
                }
            )
            arc_start = arc_end + 1
            arc_idx += 1

        climax_chapter = start + (end - start) * 4 // 5
        is_final = i == act_count - 1

        act_dict: dict[str, Any] = {
            "act_id": f"act_{i + 1:02d}",
            "act_index": i,
            "title": f"Act {i + 1}" if is_en else f"第{i + 1}幕",
            "chapter_start": start,
            "chapter_end": end,
            "act_goal": goal,
            "core_theme": theme,
            "dominant_emotion": emotion,
            "climax_chapter": climax_chapter,
            "entry_state": (
                f"{protagonist_name} enters the story with unresolved pressure."
                if is_en
                else f"{protagonist_name}带着未解决的压力进入故事。"
            )
            if i == 0
            else (
                f"{protagonist_name} enters a new stage after the last act."
                if is_en
                else f"{protagonist_name}在上一幕后进入新的阶段。"
            ),
            "exit_state": (
                f"{protagonist_name} completes the core dramatic movement."
                if is_en
                else f"{protagonist_name}完成主线的核心情感与行动闭环。"
            )
            if is_final
            else (
                f"{protagonist_name} leaves this act with a changed position and new cost."
                if is_en
                else f"{protagonist_name}带着新的位置与代价进入下一幕。"
            ),
            "payoff_promises": [
                f"Act {i + 1} should deliver at least one concrete step-change in the main line."
                if is_en
                else f"第{i + 1}幕必须兑现至少一次能改变主线局面的阶段性突破。"
            ],
            "arc_breakdown": arcs,
            "is_final_act": is_final,
        }
        if is_final:
            act_dict["resolution_contract"] = {
                "all_threads_resolved": True,
                "emotional_closure": True,
                "protagonist_arc_complete": True,
            }
        acts.append(act_dict)

    return acts


def _act_plan_prompts(
    project: ProjectModel,
    book_spec: dict[str, Any],
    world_spec: dict[str, Any],
    cast_spec: dict[str, Any],
) -> tuple[str, str]:
    """Generate LLM prompts for act-level planning."""
    language = _planner_language(project)
    is_en = is_english_language(language)
    hierarchy = compute_linear_hierarchy(max(project.target_chapters, 1))
    act_count = hierarchy["act_count"]
    arc_batch_size = hierarchy["arc_batch_size"]

    system_prompt = (
        "You are a senior story architect for long-form commercial fiction. "
        "Plan the macro narrative structure (Acts) for the full novel. Output ONLY valid JSON, no markdown."
        if is_en
        else "你是长篇商业小说的高级故事架构师。规划全书的宏观叙事结构（幕）。输出必须是合法 JSON，不要解释。"
    )

    user_prompt = (
        (
            f"Project title: {project.title}\n"
            f"Target chapters: {project.target_chapters}\n"
            f"BookSpec summary:\n{summarize_book_spec(book_spec, language='en')}\n"
            f"WorldSpec summary:\n{summarize_world_spec(world_spec, language='en')}\n"
            f"CastSpec summary:\n{summarize_cast_spec(cast_spec, language='en')}\n\n"
            f"Divide the full {project.target_chapters}-chapter story into exactly {act_count} Acts (幕).\n"
            "Each act must have a clear emotional arc from entry_state to exit_state.\n\n"
            "Output ONLY valid JSON with this structure:\n"
            '{"acts": [\n'
            "  {\n"
            '    "act_id": "act_01",\n'
            '    "act_index": 0,\n'
            '    "title": "<Act title>",\n'
            '    "chapter_start": 1,\n'
            '    "chapter_end": <end chapter>,\n'
            '    "act_goal": "<what must be accomplished>",\n'
            '    "core_theme": "<one theme word>",\n'
            '    "dominant_emotion": "<dominant emotion>",\n'
            '    "climax_chapter": <chapter number>,\n'
            '    "entry_state": "<protagonist state at start>",\n'
            '    "exit_state": "<protagonist state at end>",\n'
            '    "payoff_promises": ["<specific payoff>"],\n'
            '    "arc_breakdown": [{"arc_index": 0, "chapter_start": 1, "chapter_end": 12, "arc_goal": "..."}],\n'
            '    "is_final_act": false\n'
            "  }\n"
            "]}\n\n"
            "CRITICAL rules:\n"
            "- Acts must be contiguous: act_01 ends where act_02 begins\n"
            f"- Total chapters across all acts must equal exactly {project.target_chapters}\n"
            f"- Each act: ~{project.target_chapters // act_count} chapters on average (can vary ±30%)\n"
            "- payoff_promises: 2-4 per act, specific and emotionally satisfying\n"
            f"- arc_breakdown: each act should have ~{arc_batch_size}-chapter arcs\n"
            "- Last act must have is_final_act: true and include resolution_contract"
        )
        if is_en
        else (
            f"项目标题：{project.title}\n"
            f"目标章节：{project.target_chapters}\n"
            f"BookSpec 摘要：\n{summarize_book_spec(book_spec, language='zh')}\n"
            f"WorldSpec 摘要：\n{summarize_world_spec(world_spec, language='zh')}\n"
            f"CastSpec 摘要：\n{summarize_cast_spec(cast_spec, language='zh')}\n\n"
            f"将全书 {project.target_chapters} 章分为恰好 {act_count} 幕（Act）。\n"
            "每幕必须有从 entry_state 到 exit_state 的清晰情感弧。\n\n"
            "输出格式（纯 JSON，无 markdown）：\n"
            '{"acts": [\n'
            "  {\n"
            '    "act_id": "act_01",\n'
            '    "act_index": 0,\n'
            '    "title": "<幕标题>",\n'
            '    "chapter_start": 1,\n'
            '    "chapter_end": <结束章号>,\n'
            '    "act_goal": "<本幕必须完成的叙事目标>",\n'
            '    "core_theme": "<一个主题词，如 觉醒|崛起|危机|蜕变|决战>",\n'
            '    "dominant_emotion": "<主导情绪：热血|紧张|压抑|震撼|爽快|满足>",\n'
            '    "climax_chapter": <章号>,\n'
            '    "entry_state": "<主角在幕初的状态>",\n'
            '    "exit_state": "<主角在幕末的状态>",\n'
            '    "payoff_promises": ["<具体爽点承诺>"],\n'
            '    "arc_breakdown": [{"arc_index": 0, "chapter_start": 1, "chapter_end": 12, "arc_goal": "..."}],\n'
            '    "is_final_act": false\n'
            "  }\n"
            "]}\n\n"
            "【硬性要求】\n"
            "- 各幕章节范围必须首尾相接，不允许间隙或重叠\n"
            f"- 所有幕的章节总数必须恰好等于 {project.target_chapters}\n"
            f"- 每幕平均约 {project.target_chapters // act_count} 章（允许 ±30%）\n"
            "- payoff_promises：每幕 2-4 个，必须具体到读者能感受到的爽点\n"
            f"- arc_breakdown：每幕按 ~{arc_batch_size} 章一弧细分\n"
            "- 最后一幕必须标记 is_final_act: true 并包含 resolution_contract"
        )
    )

    return system_prompt, user_prompt


# ── Multi-Force Conflict Taxonomy ──────────────────────────────────
# Each volume should present a *different type* of challenge rather
# than endlessly repeating "antagonist keeps pressuring."

_CONFLICT_PHASE_TYPES: list[str] = [
    "survival",  # 直接生存威胁
    "political_intrigue",  # 权力博弈与暗中布局
    "betrayal",  # 信任崩塌与背刺
    "faction_war",  # 多方势力全面对抗
    "existential_threat",  # 终极威胁与最大牺牲
    "internal_reckoning",  # 内心拷问与自我面对
]

# Phase-based volume title pools. Used as a fallback when no milestone title
# is provided, so volumes get distinct, meaningful names instead of
# generic "第N卷" / "Volume N" placeholders. Each list is cycled by the
# phase's occurrence index across the volume plan.
#
# Pools are intentionally ≥ 12 entries each so a 24-volume novel rarely
# cycles past the pool. When cycles do occur, :func:`_resolve_fallback_volume_title`
# composes a fresh title via phase-agnostic prefix/suffix mixing — it
# NEVER appends "·二" / "·III" ordinals, which read as clumsy sequel tags.
_PHASE_TITLE_VARIATIONS_ZH: dict[str, list[str]] = {
    # Category: action-progression phases
    "individual_survival": [
        "血路初开",
        "绝境求生",
        "悬崖立命",
        "险中续命",
        "刀锋自渡",
        "孤身破围",
        "寒夜独行",
        "九死一生",
        "荆棘破春",
        "绝壁残照",
        "微光之路",
        "风雪孤魂",
    ],
    "faction_friction": [
        "夹缝立足",
        "势力角逐",
        "风云暗涌",
        "群雄倾轧",
        "众宗交织",
        "明争暗斗",
        "纵横捭阖",
        "风波潜流",
        "各怀心思",
        "棋局初展",
        "弈海沉浮",
        "刀俎之间",
    ],
    "power_system_test": [
        "体系破障",
        "规则叩问",
        "质变之门",
        "力道重铸",
        "大道辨识",
        "破而后立",
        "试剑苍穹",
        "奇经百脉",
        "铸魂炼体",
        "淬火问道",
        "九转玄关",
        "通元证法",
    ],
    "world_threat": [
        "天下危局",
        "苍生倾覆",
        "乾坤失衡",
        "众生浩劫",
        "九州同泣",
        "星河陨落",
        "浩荡末路",
        "山河破碎",
        "苍茫覆灭",
        "日月无光",
        "人间风雷",
        "浩劫当头",
    ],
    "transcendence": [
        "破执证道",
        "大道归一",
        "心魔照影",
        "超凡入圣",
        "登临彼岸",
        "红尘俱忘",
        "玄元入定",
        "证道飞升",
        "孤舟渡苦",
        "心法通玄",
        "无我同尘",
        "道行圆融",
    ],
    # Legacy _CONFLICT_PHASE_TYPES
    "survival": [
        "绝地求生",
        "险中立身",
        "生死博弈",
        "残局求存",
        "刀尖踏步",
        "孤城不破",
        "夹道幽光",
        "烽烟独守",
        "血色黎明",
        "残阳立誓",
        "死里挣脱",
        "荆棘踏霜",
    ],
    "political_intrigue": [
        "暗流权谋",
        "棋盘迷影",
        "庙堂风云",
        "权谋迭起",
        "朝堂裂影",
        "密议深夜",
        "玉阶迷雾",
        "棋局暗张",
        "云谲波诡",
        "私宴疑踪",
        "百官默契",
        "朱笔如刀",
    ],
    "betrayal": [
        "信任崩裂",
        "背刺寒霜",
        "裂痕成渊",
        "故人反目",
        "旧盟破镜",
        "同袍离心",
        "暗箭难防",
        "回身寒刃",
        "誓言散尽",
        "兄弟陌路",
        "深情反目",
        "别后无情",
    ],
    "faction_war": [
        "群雄逐鹿",
        "百宗乱战",
        "势力倾轧",
        "风云对决",
        "旌旗对峙",
        "烽烟四起",
        "铁骑纵横",
        "万宗争鸣",
        "诸侯离合",
        "各据山河",
        "雄据一方",
        "刀兵并举",
    ],
    "existential_threat": [
        "天倾之危",
        "末世将至",
        "终极决断",
        "万象归零",
        "苍穹倒悬",
        "星坠人寰",
        "大劫临头",
        "天门崩开",
        "寰宇将倾",
        "终焉之钟",
        "弥天劫起",
        "古今同覆",
    ],
    "internal_reckoning": [
        "归心照影",
        "内心审判",
        "破执立我",
        "自我重铸",
        "照见前尘",
        "心海生波",
        "独白深夜",
        "孤灯问我",
        "照影审心",
        "前尘回眸",
        "心音寂寂",
        "自渡无人",
    ],
}

_PHASE_TITLE_VARIATIONS_EN: dict[str, list[str]] = {
    "individual_survival": [
        "First Blood",
        "Edge of Survival",
        "Cliff of Fate",
        "Breath by Breath",
        "A Thin Line",
        "Knife's Edge Run",
        "Lone Thread",
        "Hard Rain",
        "The Last Breath",
        "Through the Briar",
        "Midnight Vigil",
        "Embers Alive",
    ],
    "faction_friction": [
        "Cracks Between Powers",
        "Shifting Alliances",
        "Undercurrents Rise",
        "Caught in the Fray",
        "Rival Tides",
        "Double-Edged Pact",
        "Currents Collide",
        "Broken Accord",
        "Divided Courts",
        "Fault Lines",
        "Hidden Hands",
        "Shared Knives",
    ],
    "power_system_test": [
        "Rules Unbound",
        "Crossing the Threshold",
        "Trial of Ascent",
        "Forging Anew",
        "Breaking the Doctrine",
        "New Laws of the Flesh",
        "Ashes and Forge",
        "Beyond the Canon",
        "Heat of Refining",
        "Unwritten Code",
        "Second Awakening",
        "A Harder Vow",
    ],
    "world_threat": [
        "World in Peril",
        "Heaven Tilts",
        "The Great Reckoning",
        "A Fracturing Sky",
        "All Lands Weep",
        "Nightfall Continent",
        "Fall of the Age",
        "Ashes of Empire",
        "Storm Over the Realm",
        "The Grand Collapse",
        "End of the Balance",
        "Silent Heavens",
    ],
    "transcendence": [
        "Beyond the Path",
        "Unity of the Way",
        "Shadow of the Mind",
        "Stepping Beyond Mortality",
        "Last Shore",
        "Forgetting the World",
        "Ninefold Serene",
        "Dissolving Self",
        "Into the Cloud",
        "Truth Without Form",
        "A Final Echo",
        "The Whispering Way",
    ],
    "survival": [
        "Bare Survival",
        "Stand Your Ground",
        "Life on a Knife's Edge",
        "The Last Ember",
        "A Blade's Breadth",
        "Unbroken Wall",
        "Dim Lantern Vigil",
        "Lonesome Garrison",
        "First Dawn After",
        "Oath Beneath Ashes",
        "Tearing Free",
        "Thorn and Frost",
    ],
    "political_intrigue": [
        "Whispers of Power",
        "The Shifting Board",
        "Court of Shadows",
        "A Web of Schemes",
        "Cracks at Court",
        "Midnight Councils",
        "Mists of the Throne",
        "Hidden Moves",
        "Rolling Tides",
        "Secret Banquets",
        "A Silent Accord",
        "The Red Pen",
    ],
    "betrayal": [
        "Broken Trust",
        "A Cold Blade",
        "Fault Lines Open",
        "Friends Turned Foes",
        "A Shattered Bond",
        "Estranged Comrades",
        "Arrows from Shadow",
        "Cold Steel Reversed",
        "Vows Ashes",
        "Brothers No More",
        "A Beloved's Turn",
        "Afterward Silence",
    ],
    "faction_war": [
        "Rival Banners",
        "Open Warfare",
        "The Grand Clash",
        "Age of Contention",
        "Standard Against Standard",
        "Smoke on the Horizon",
        "Iron Horsemen",
        "A Thousand Schools",
        "Vassals Choose",
        "Rule by Territory",
        "Dominion's Edge",
        "Swords and Spears",
    ],
    "existential_threat": [
        "On the Brink",
        "Twilight of an Age",
        "The Final Choice",
        "Reduction to Zero",
        "The Firmament Inverted",
        "Stars Fall on Men",
        "Calamity Overhead",
        "The Gates Break",
        "A World Tilts",
        "The Final Bell",
        "End of All Sky",
        "Past Becomes Dust",
    ],
    "internal_reckoning": [
        "Into the Self",
        "Inner Trial",
        "Breaking the Chain",
        "Reforging the Heart",
        "Seeing Past Selves",
        "The Tides of Self",
        "A Long Vigil",
        "The Lone Lamp",
        "Mirror of the Heart",
        "Looking Back",
        "Silent Echoes",
        "No One to Carry You",
    ],
}


# Cycle composers — used when a phase's occurrence exceeds its pool size.
# Instead of appending ordinals ("·二", "·III") we compose a fresh title
# from a neutral prefix + original base + neutral suffix pool, giving the
# reader a genuinely new-sounding name without the sequel-tag aesthetic.
_TITLE_CYCLE_PREFIX_ZH: tuple[str, ...] = (
    "重帷",
    "再临",
    "新弦",
    "岁迹",
    "回响",
    "余烬",
    "深谷",
    "续章",
    "暗面",
    "静流",
    "远岸",
    "余韵",
)
_TITLE_CYCLE_SUFFIX_ZH: tuple[str, ...] = (
    "重演",
    "续声",
    "回声",
    "再起",
    "余响",
    "新篇",
    "再临",
    "新象",
    "重启",
    "深处",
    "之后",
    "暗影",
)
_TITLE_CYCLE_PREFIX_EN: tuple[str, ...] = (
    "Echoes of",
    "Return to",
    "Beneath",
    "After",
    "Shadows of",
    "Remnants of",
    "Deep",
    "Beyond",
    "Into",
    "Still",
    "Past",
    "Long After",
)
_TITLE_CYCLE_SUFFIX_EN: tuple[str, ...] = (
    "Revisited",
    "Rekindled",
    "Continued",
    "Reechoed",
    "Rewritten",
    "Anew",
    "Returning",
    "Reformed",
    "Reshaped",
    "Enduring",
    "Resurgent",
    "Redrawn",
)


def _compose_cycle_title(base: str, cycle: int, *, is_en: bool) -> str:
    """Compose a fresh title from neutral prefix/suffix mixing.

    Used when the phase pool has been exhausted and we would otherwise
    emit a "·二" / "· II" ordinal suffix. Instead of ordinal numbering
    we produce a variant like "重帷血路初开" (cycle=1) or
    "余烬血路初开" (cycle=2). Deterministic: same (base, cycle) always
    produces the same composed title so volume plans regenerate
    stably.
    """

    if cycle <= 0:
        return base
    if is_en:
        prefix = _TITLE_CYCLE_PREFIX_EN[(cycle - 1) % len(_TITLE_CYCLE_PREFIX_EN)]
        # Alternate between prefix and suffix forms on successive cycles
        # for additional variety. Odd cycles → prefix; even cycles → suffix.
        if cycle % 2 == 1:
            return f"{prefix} {base}"
        suffix = _TITLE_CYCLE_SUFFIX_EN[(cycle - 1) % len(_TITLE_CYCLE_SUFFIX_EN)]
        return f"{base} {suffix}"
    prefix = _TITLE_CYCLE_PREFIX_ZH[(cycle - 1) % len(_TITLE_CYCLE_PREFIX_ZH)]
    if cycle % 2 == 1:
        return f"{prefix}{base}"
    suffix = _TITLE_CYCLE_SUFFIX_ZH[(cycle - 1) % len(_TITLE_CYCLE_SUFFIX_ZH)]
    return f"{base}{suffix}"


def _resolve_fallback_volume_title(
    phase_key: str,
    phase_occurrence: int,
    volume_number: int,
    *,
    is_en: bool,
) -> str:
    """Pick a distinct phase-based title for a fallback volume plan entry.

    ``phase_occurrence`` is the 0-based index of how many volumes with the
    same phase have already been named. Cycling the pool by occurrence keeps
    titles distinct across repeated phases.

    When the pool is exhausted (a single phase repeats more than ``len(pool)``
    times across the plan — rare with 12-entry pools) the helper composes a
    fresh title via :func:`_compose_cycle_title` rather than appending a
    clumsy "·二" / "· II" sequel tag.
    """
    pool = (_PHASE_TITLE_VARIATIONS_EN if is_en else _PHASE_TITLE_VARIATIONS_ZH).get(
        phase_key
    ) or []
    if pool:
        base = pool[phase_occurrence % len(pool)]
        cycle = phase_occurrence // len(pool)
        return _compose_cycle_title(base, cycle, is_en=is_en)
    return f"Volume {volume_number}" if is_en else f"第{volume_number}卷"


_CHINESE_NUMERALS = "零一二三四五六七八九十"


def _chinese_ordinal(n: int) -> str:
    """Return a compact Chinese numeral for 1-99 (enough for volume counts)."""
    if n < 0:
        return str(n)
    if n <= 10:
        return _CHINESE_NUMERALS[n]
    if n < 20:
        return "十" + (_CHINESE_NUMERALS[n - 10] if n > 10 else "")
    tens, ones = divmod(n, 10)
    return f"{_CHINESE_NUMERALS[tens]}十" + (_CHINESE_NUMERALS[ones] if ones else "")


def _roman(n: int) -> str:
    """Return a Roman numeral for small positive integers (suffix use)."""
    vals = [(10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I")]
    out = ""
    remaining = max(n, 0)
    for v, s in vals:
        while remaining >= v:
            out += s
            remaining -= v
    return out or "I"


_VOLUME_OBSTACLE_TEMPLATES: dict[str, str] = {
    "survival": "{force_name}带来直接生存压力——{protagonist}必须先活下来才能图更远的事。",
    "political_intrigue": "{force_name}在暗中布局，{protagonist}必须看穿复杂的权力交易才能找到出路。",
    "betrayal": "来自{force_name}的背刺让{protagonist}失去了最信任的依靠，必须在废墟中重建。",
    "faction_war": "{force_name}发动了全面攻势，{protagonist}被卷入多方博弈的泥潭。",
    "existential_threat": "{force_name}的终极计划威胁着整个世界的根基，{protagonist}必须做出最大的牺牲。",
    "internal_reckoning": "{protagonist}面对来自内心深处的拷问——{force_name}逼迫他直面自己一直在逃避的真相。",
}

_VOLUME_CLIMAX_TEMPLATES: dict[str, str] = {
    "survival": "{protagonist}在生死边缘完成一次绝地反击，暂时挣脱{force_name}的控制。",
    "political_intrigue": "{protagonist}揭开了{force_name}布下的一层权力陷阱，但发现背后还有更深的棋局。",
    "betrayal": "{protagonist}在信任崩塌后做出一个痛苦的抉择，切断了与{force_name}的最后牵绊。",
    "faction_war": "多方势力在决战中重新洗牌，{protagonist}凭借关键情报扭转了自己的位置。",
    "existential_threat": "{protagonist}以极大的个人代价阻止了{force_name}的终极计划的第一阶段。",
    "internal_reckoning": "{protagonist}直面内心最深处的恐惧，完成了真正意义上的蜕变。",
}

_VOLUME_OBSTACLE_TEMPLATES_EN: dict[str, str] = {
    "survival": "{force_name} poses a direct survival threat — {protagonist} must stay alive before anything else.",
    "political_intrigue": "{force_name} is maneuvering in the shadows; {protagonist} must see through the power plays to find a way out.",
    "betrayal": "A devastating betrayal by {force_name} strips {protagonist} of their most trusted support — they must rebuild from the wreckage.",
    "faction_war": "{force_name} launches an all-out offensive, dragging {protagonist} into a multi-faction quagmire.",
    "existential_threat": "{force_name}'s endgame threatens the very foundation of the world; {protagonist} must make the ultimate sacrifice.",
    "internal_reckoning": "{protagonist} confronts a deep inner crisis — {force_name} forces them to face the truth they've been running from.",
}

_VOLUME_CLIMAX_TEMPLATES_EN: dict[str, str] = {
    "survival": "{protagonist} pulls off a desperate counter-strike at death's door, temporarily breaking free of {force_name}'s grip.",
    "political_intrigue": "{protagonist} exposes one layer of {force_name}'s power trap — only to discover an even deeper game beneath.",
    "betrayal": "In the wreckage of broken trust, {protagonist} makes an agonizing choice that severs their last tie to {force_name}.",
    "faction_war": "The battlefield reshuffles as {protagonist} leverages critical intelligence to shift the balance against {force_name}.",
    "existential_threat": "{protagonist} pays a devastating personal price to halt the first phase of {force_name}'s ultimate plan.",
    "internal_reckoning": "{protagonist} faces their deepest fear head-on, emerging truly transformed.",
}

_CHAPTER_CONFLICT_TEMPLATES: dict[str, dict[str, str]] = {
    "survival": {
        "setup": "{protagonist}发现自身处境远比预想的危险，{force_name}的威胁已经逼到眼前。",
        "investigation": "{protagonist}在{force_name}的压力下搜寻生存资源和潜在的逃生路线。",
        "pressure": "{force_name}收紧了包围圈，{protagonist}必须在有限时间内做出取舍。",
        "reversal": "局势突然逆转——{protagonist}找到了反击{force_name}的意外切入口。",
        "climax": "{protagonist}和{force_name}正面交锋，这场生存之战到了最后关头。",
    },
    "political_intrigue": {
        "setup": "{protagonist}开始察觉{force_name}在暗处布下的权力网络。",
        "investigation": "{protagonist}深入{force_name}的势力版图，试图找到关键弱点。",
        "pressure": "{force_name}的政治手段让{protagonist}的盟友开始动摇。",
        "reversal": "{protagonist}发现了一个可以反制{force_name}的隐秘情报。",
        "climax": "权力博弈的棋盘上，{protagonist}和{force_name}的角力到了决定性时刻。",
    },
    "betrayal": {
        "setup": "{protagonist}身边出现了令人不安的信号——{force_name}的真面目开始显露。",
        "investigation": "{protagonist}在不确定中追查{force_name}背叛的线索。",
        "pressure": "背叛的证据越来越多，{protagonist}的信任体系正在崩塌。",
        "reversal": "真相大白——{force_name}的背叛比想象中更深远，但也暴露了新的机会。",
        "climax": "{protagonist}必须在被背叛的痛苦中做出最艰难的决定。",
    },
    "faction_war": {
        "setup": "多方势力的矛盾激化，{protagonist}被卷入{force_name}引发的冲突漩涡。",
        "investigation": "{protagonist}试图在{force_name}主导的混战中找到自己的立足点。",
        "pressure": "{force_name}的攻势让{protagonist}的阵地岌岌可危。",
        "reversal": "战局中出现意外变量，{protagonist}抓住了扭转与{force_name}对抗的机会。",
        "climax": "全面对抗的最终战场上，{protagonist}必须在{force_name}的围攻中打开突破口。",
    },
    "existential_threat": {
        "setup": "{force_name}的终极威胁浮出水面，{protagonist}意识到这远超之前所有挑战。",
        "investigation": "{protagonist}拼命搜寻能对抗{force_name}的终极手段。",
        "pressure": "{force_name}的计划正在不可逆转地推进，留给{protagonist}的时间越来越少。",
        "reversal": "一个意想不到的发现让{protagonist}看到了对抗{force_name}的最后希望。",
        "climax": "终极对决——{protagonist}以最大的牺牲和{force_name}展开命运之战。",
    },
    "internal_reckoning": {
        "setup": "{protagonist}开始被内心深处的矛盾所困扰——{force_name}触及了他最脆弱的地方。",
        "investigation": "{protagonist}在{force_name}的逼迫下回溯自己一路走来的选择。",
        "pressure": "{force_name}让{protagonist}无法再逃避——必须直面最不愿面对的真相。",
        "reversal": "{protagonist}在崩溃的边缘找到了内心深处真正的答案。",
        "climax": "{protagonist}完成了精神上的蜕变，以全新的姿态回应{force_name}的终极拷问。",
    },
}

_CHAPTER_CONFLICT_TEMPLATES_EN: dict[str, dict[str, str]] = {
    "survival": {
        "setup": "{protagonist} discovers their situation is far more dangerous than expected — {force_name}'s threat is closing in.",
        "investigation": "Under {force_name}'s pressure, {protagonist} searches for survival resources and escape routes.",
        "pressure": "{force_name} tightens the noose; {protagonist} must make impossible trade-offs under a shrinking deadline.",
        "reversal": "The tables turn — {protagonist} finds an unexpected opening to strike back at {force_name}.",
        "climax": "{protagonist} and {force_name} clash head-on in a fight for survival that has reached its breaking point.",
    },
    "political_intrigue": {
        "setup": "{protagonist} begins to sense the power network {force_name} has been building in the shadows.",
        "investigation": "{protagonist} probes deep into {force_name}'s sphere of influence, searching for a critical weakness.",
        "pressure": "{force_name}'s political maneuvers start to shake {protagonist}'s allies loose.",
        "reversal": "{protagonist} uncovers a hidden piece of intelligence that could turn the tables on {force_name}.",
        "climax": "On the chessboard of power, {protagonist} and {force_name} reach the decisive moment of their struggle.",
    },
    "betrayal": {
        "setup": "Disturbing signals emerge around {protagonist} — {force_name}'s true nature begins to surface.",
        "investigation": "{protagonist} tracks the trail of {force_name}'s betrayal through a fog of uncertainty.",
        "pressure": "Evidence of the betrayal mounts; {protagonist}'s trust is crumbling.",
        "reversal": "The truth is out — {force_name}'s betrayal runs far deeper than imagined, but it also reveals a new opportunity.",
        "climax": "{protagonist} must make the hardest decision of their life in the shadow of betrayal.",
    },
    "faction_war": {
        "setup": "Tensions between factions boil over; {protagonist} is drawn into the conflict vortex unleashed by {force_name}.",
        "investigation": "{protagonist} scrambles to find a foothold in the chaos dominated by {force_name}.",
        "pressure": "{force_name}'s assault pushes {protagonist}'s position to the brink.",
        "reversal": "An unexpected variable emerges in the battle, giving {protagonist} a chance to turn the tide against {force_name}.",
        "climax": "On the final battlefield, {protagonist} must punch through {force_name}'s siege to survive.",
    },
    "existential_threat": {
        "setup": "{force_name}'s ultimate threat surfaces; {protagonist} realizes this dwarfs every previous challenge.",
        "investigation": "{protagonist} desperately searches for a weapon that can counter {force_name}'s endgame.",
        "pressure": "{force_name}'s plan advances irreversibly; {protagonist}'s window is closing fast.",
        "reversal": "An unexpected discovery gives {protagonist} a last glimmer of hope against {force_name}.",
        "climax": "The final showdown — {protagonist} stakes everything in a battle of destiny against {force_name}.",
    },
    "internal_reckoning": {
        "setup": "{protagonist} is haunted by a deepening inner conflict — {force_name} has struck at their most vulnerable point.",
        "investigation": "Under {force_name}'s pressure, {protagonist} retraces every choice that led them here.",
        "pressure": "{force_name} leaves {protagonist} no room to run — they must face the truth they've been avoiding.",
        "reversal": "On the edge of collapse, {protagonist} finds the real answer buried deep within.",
        "climax": "{protagonist} completes a spiritual metamorphosis and faces {force_name}'s ultimate challenge as a changed person.",
    },
}


def _assign_conflict_phases(volume_count: int, *, category_key: str | None = None) -> list[str]:
    """Assign a conflict phase type to each volume based on total volume count.

    When *category_key* is given and the corresponding category has a
    ``challenge_evolution_pathway``, the phase keys from that pathway are
    used instead of the hardcoded ``_CONFLICT_PHASE_TYPES``.
    """
    # Try category-specific phases first
    if category_key:
        cat = get_novel_category(category_key)
        if cat and cat.challenge_evolution_pathway:
            cat_phases = [p.phase_key for p in cat.challenge_evolution_pathway]
            return _distribute_phases(volume_count, cat_phases)

    # Legacy fallback — preserve original hardcoded distributions exactly
    phases = _CONFLICT_PHASE_TYPES
    if volume_count <= 1:
        return ["survival"]
    if volume_count == 2:
        return ["survival", "existential_threat"]
    if volume_count == 3:
        return ["survival", "political_intrigue", "existential_threat"]
    if volume_count == 4:
        return ["survival", "political_intrigue", "betrayal", "existential_threat"]
    if volume_count == 5:
        return ["survival", "political_intrigue", "betrayal", "faction_war", "existential_threat"]
    # 6+ volumes: keep first and last fixed, cycle middle phases for extras
    first = phases[0]  # always survival
    last = phases[-1]  # always internal_reckoning
    middle = phases[1:-1]  # intrigue, betrayal, faction_war, existential_threat
    result: list[str] = [first]
    extra = volume_count - 2  # slots between first and last
    for i in range(extra):
        result.append(middle[i % len(middle)])
    result.append(last)
    return result


def _distribute_phases(volume_count: int, phases: list[str]) -> list[str]:
    """Distribute *phases* across *volume_count* volumes.

    The first and last phase are pinned; middle phases are selected or cycled.
    """
    if not phases:
        return ["survival"] * max(volume_count, 1)
    if volume_count <= 1:
        return [phases[0]]
    if volume_count == 2:
        return [phases[0], phases[-1]]
    if volume_count == len(phases):
        return list(phases)
    if volume_count < len(phases):
        # Fewer volumes than phases — pin first/last, pick evenly from middle
        first, last = phases[0], phases[-1]
        middle = phases[1:-1]
        need = volume_count - 2
        step = len(middle) / need if need > 0 else 1
        picked = [middle[min(int(i * step), len(middle) - 1)] for i in range(need)]
        return [first] + picked + [last]
    # More volumes than phases — pin first/last, cycle middle
    first = phases[0]
    last = phases[-1]
    middle = phases[1:-1] if len(phases) > 2 else phases
    result: list[str] = [first]
    extra = volume_count - 2
    for i in range(extra):
        result.append(middle[i % len(middle)] if middle else first)
    result.append(last)
    return result


def _resolve_phase_templates(
    phase_key: str,
    *,
    category_key: str | None = None,
    is_en: bool = False,
) -> dict[str, str]:
    """Return volume-level templates (goal, climax, obstacle, resolution) for *phase_key*.

    Looks up the category's ``challenge_evolution_pathway`` first.
    Falls back to the legacy ``_VOLUME_*_TEMPLATES`` dicts.
    """
    if category_key:
        cat = get_novel_category(category_key)
        if cat:
            for phase in cat.challenge_evolution_pathway:
                if phase.phase_key == phase_key:
                    return {
                        "goal": (
                            phase.volume_goal_template_en
                            if is_en
                            else phase.volume_goal_template_zh
                        )
                        or "",
                        "climax": (
                            phase.volume_climax_template_en
                            if is_en
                            else phase.volume_climax_template_zh
                        )
                        or "",
                        "obstacle": (
                            phase.volume_obstacle_template_en
                            if is_en
                            else phase.volume_obstacle_template_zh
                        )
                        or "",
                        "resolution": (
                            phase.volume_resolution_template_en
                            if is_en
                            else phase.volume_resolution_template_zh
                        )
                        or "",
                    }
    # Legacy fallback
    goal_map = _VOLUME_GOAL_TEMPLATES_EN if is_en else _VOLUME_GOAL_TEMPLATES
    climax_map = _VOLUME_CLIMAX_TEMPLATES_EN if is_en else _VOLUME_CLIMAX_TEMPLATES
    obstacle_map = _VOLUME_OBSTACLE_TEMPLATES_EN if is_en else _VOLUME_OBSTACLE_TEMPLATES
    resolution_map = _VOLUME_RESOLUTION_TEMPLATES_EN if is_en else _VOLUME_RESOLUTION_TEMPLATES
    return {
        "goal": goal_map.get(phase_key, ""),
        "climax": climax_map.get(phase_key, ""),
        "obstacle": obstacle_map.get(phase_key, ""),
        "resolution": resolution_map.get(phase_key, ""),
    }


def _resolve_chapter_conflict_templates(
    phase_key: str,
    *,
    category_key: str | None = None,
    is_en: bool = False,
) -> dict[str, str]:
    """Return chapter-level conflict templates for *phase_key*.

    Looks up the category's ``challenge_evolution_pathway`` first.
    Falls back to legacy ``_CHAPTER_CONFLICT_TEMPLATES``.
    """
    if category_key:
        cat = get_novel_category(category_key)
        if cat:
            for phase in cat.challenge_evolution_pathway:
                if phase.phase_key == phase_key:
                    tpl = phase.chapter_conflict_templates
                    suffix = "_en" if is_en else "_zh"
                    return {
                        "setup": getattr(tpl, f"setup{suffix}", "") or "",
                        "investigation": getattr(tpl, f"investigation{suffix}", "") or "",
                        "pressure": getattr(tpl, f"pressure{suffix}", "") or "",
                        "reversal": getattr(tpl, f"reversal{suffix}", "") or "",
                        "climax": getattr(tpl, f"climax{suffix}", "") or "",
                    }
    # Legacy fallback
    _tpl_map = _CHAPTER_CONFLICT_TEMPLATES_EN if is_en else _CHAPTER_CONFLICT_TEMPLATES
    return _tpl_map.get(phase_key, _tpl_map.get("survival", {}))


def _phase_name(index_within_volume: int, total_in_volume: int) -> str:
    ratio = index_within_volume / max(total_in_volume, 1)
    if ratio <= 0.2:
        return "setup"
    if ratio <= 0.5:
        return "investigation"
    if ratio <= 0.75:
        return "pressure"
    if ratio <= 0.9:
        return "reversal"
    return "climax"


def _hook_type(
    index_within_volume: int, total_in_volume: int, *, language: str | None = None
) -> str:
    phase = _phase_name(index_within_volume, total_in_volume)
    if is_english_language(language):
        mapping = {
            "setup": "information reveal",
            "investigation": "conflict escalation",
            "pressure": "crisis suspense",
            "reversal": "reversal",
            "climax": "action cliffhanger",
        }
    else:
        mapping = {
            "setup": "信息揭示",
            "investigation": "冲突升级",
            "pressure": "危机悬念",
            "reversal": "反转",
            "climax": "行动截断",
        }
    return mapping[phase]


# Extended scene type taxonomy for pacing diversity.
def _pick_by_seed(options: list[str], slug: str, chapter: int, label: str) -> str:
    """Pick from *options* using a deterministic-but-project-unique hash seed.

    Different novels (slugs) get different selections for the same chapter
    position, breaking the visible repetition pattern across projects.

    ``options`` must be non-empty.
    """
    if not options:
        return ""
    _h = int(
        hashlib.md5(f"{slug}:{chapter}:{label}".encode(), usedforsecurity=False).hexdigest()[:8], 16
    )
    return options[_h % len(options)]


def _fallback_unique_chapter_beat(
    *,
    project_slug: str,
    chapter_number: int,
    phase: str,
    protagonist: str,
    force_name: str,
    language: str | None = None,
) -> str:
    is_en = is_english_language(language)
    if is_en:
        beat_pool = [
            "a hidden witness changes the direction of the investigation",
            "a false clue is exposed as deliberate bait",
            "an ally withholds one decisive detail",
            "a hostile rule of the setting changes without warning",
            "a private weakness becomes tactically relevant",
            "a recovered object contradicts the accepted timeline",
            "a minor debt comes due at the worst possible moment",
            "a protective choice creates a new vulnerability",
            "a familiar place reveals an impossible second layer",
            "a promise made earlier becomes impossible to keep",
            "a defeated opponent leaves behind a dangerous instruction",
            "a supposed advantage proves to be a trap",
        ]
        beat = _pick_by_seed(beat_pool, project_slug, chapter_number, f"unique_beat:{phase}")
        return (
            f"chapter {chapter_number}: {protagonist} faces {beat} under pressure from {force_name}"
        )

    beat_pool_zh = [
        "旧物显出一处前文未见的刻痕",
        "证人的一句话推翻原先时间线",
        "盟友隐瞒的半句真相终于露出破绽",
        "熟悉地点出现不该存在的第二层空间",
        "敌方故意留下的假线索反向暴露弱点",
        "主角保护他人的选择制造新的软肋",
        "一个被拖延的旧隐患在最坏时刻爆发",
        "已经解决的旧案突然牵出新受害者",
        "被忽视的器物规则发生异常偏移",
        "一次短暂胜利附带看不见的代价",
        "败退者留下的指令指向更深陷阱",
        "主角的私人执念被敌方精准利用",
        "旁观者的记忆补上关键空白",
        "看似安全的退路被提前封死",
        "一次试探逼出敌方真正目的",
        "卷内主线出现不可逆的阶段性损失",
    ]
    beat = _pick_by_seed(beat_pool_zh, project_slug, chapter_number, f"unique_beat:{phase}")
    return beat


def _fallback_hook_description(
    *,
    project_slug: str,
    chapter_number: int,
    phase: str,
    unique_beat: str,
    protagonist: str,
    language: str | None = None,
) -> str:
    is_en = is_english_language(language)
    if is_en:
        pressure = _pick_by_seed(
            [
                "a new deadline",
                "a personal cost",
                "a reversed clue",
                "a visible betrayal",
                "a narrowing escape route",
            ],
            project_slug,
            chapter_number,
            f"hook_pressure:{phase}",
        )
        hook_event = _pick_by_seed(
            [
                "the only witness vanishes before naming the buyer",
                "the sealed exit locks from the outside",
                "the recovered object answers to someone else's blood",
                "an ally's token appears in the enemy's hand",
                "the deadline is cut in half by a public accusation",
            ],
            project_slug,
            chapter_number,
            f"hook_event:{phase}",
        )
        return f"{protagonist} resolves {unique_beat}, but {pressure} lands: {hook_event}."

    hook_event = _pick_by_seed(
        [
            "唯一证人还没说出买主便被人带走",
            "来时的退路从外面落了锁",
            "刚取回的旧物忽然认了别人的血",
            "盟友的信物出现在敌人手里",
            "对方当众点名要他交出手中证据",
            "原本安全的器物规则当场反噬",
        ],
        project_slug,
        chapter_number,
        f"hook_event:{phase}",
    )
    return f"{protagonist}刚处理完「{unique_beat}」，却发现{hook_event}。"


# After high-tension phases, insert low-tension scene types to create rhythm.
_SCENE_TYPE_AFTER_CLIMAX = [
    "aftermath",
    "introspection",
    "relationship_building",
    "quiet_revelation",
    "emotional_recovery",
    "alliance_shift",
]
_SCENE_TYPE_AFTER_PRESSURE = [
    "preparation",
    "worldbuilding_discovery",
    "strategic_planning",
    "resource_gathering",
    "mentor_moment",
]
_SCENE_TYPE_COMIC_INTERVAL = 7  # Insert comic relief every N chapters

_FALLBACK_TITLE_PREFIXES = [
    "暗潮",
    "盲区",
    "裂痕",
    "回声",
    "风眼",
    "余烬",
    "伏线",
    "变局",
    "断点",
    "逆流",
    "边界",
    "悬灯",
    "浮标",
    "锈迹",
    "夜隙",
    "残局",
    "沉渊",
    "灰幕",
    "雾锁",
    "棱线",
    "铁壁",
    "荒火",
    "冷锋",
    "碎影",
]
_FALLBACK_TITLE_SUFFIXES = {
    "setup": ["初现", "入局", "投石", "试探", "铺火", "露锋", "破冰", "起手", "掀幕", "落子"],
    "investigation": [
        "追索",
        "摸底",
        "拆解",
        "寻隙",
        "探针",
        "回查",
        "溯源",
        "揭层",
        "织网",
        "破壁",
    ],
    "pressure": ["加压", "围拢", "失衡", "封锁", "死线", "逼近", "绞杀", "窒息", "崩弦", "缩网"],
    "reversal": ["反咬", "逆转", "偏航", "脱钩", "换轨", "回火", "翻盘", "倒戈", "破局", "重铸"],
    "climax": ["爆裂", "截断", "崩口", "闯线", "归零", "掀牌", "决堤", "焚天", "碎锁", "终幕"],
}
_FALLBACK_TITLE_PREFIXES_EN = [
    "Storm",
    "Ash",
    "Iron",
    "Glass",
    "Night",
    "Ember",
    "Shadow",
    "Signal",
    "Hollow",
    "Rift",
    "Cinder",
    "Cipher",
    "Frost",
    "Veil",
    "Thorn",
    "Drift",
    "Shard",
    "Crimson",
    "Wraith",
    "Beacon",
    "Chasm",
    "Ruin",
    "Onyx",
    "Haze",
]
_FALLBACK_TITLE_SUFFIXES_EN = {
    "setup": [
        "Wake",
        "Threshold",
        "First Light",
        "Opening Move",
        "Spark",
        "Edge",
        "Kindling",
        "Harbinger",
        "Genesis",
        "Prelude",
    ],
    "investigation": [
        "Trace",
        "Crossing",
        "Faultline",
        "Search",
        "Probe",
        "Ledger",
        "Cipher",
        "Thread",
        "Excavation",
        "Inquiry",
    ],
    "pressure": [
        "Lockdown",
        "Deadline",
        "Pressure",
        "Siege",
        "Choke Point",
        "Breaking Point",
        "Stranglehold",
        "Crucible",
        "Gauntlet",
        "Cascade",
    ],
    "reversal": [
        "Countermove",
        "Turn",
        "Slip",
        "Backfire",
        "Pivot",
        "Undoing",
        "Gambit",
        "Shift",
        "Resurgence",
        "Overthrow",
    ],
    "climax": [
        "Rupture",
        "Burn",
        "Cutline",
        "Zero Hour",
        "Collapse",
        "Last Gate",
        "Reckoning",
        "Inferno",
        "Convergence",
        "Endgame",
    ],
}

# NOTE: The legacy `_FALLBACK_EVENT_TITLES_ZH` / `_FALLBACK_EVENT_TITLES_EN`
# pools were removed in the 2026-05 title rewrite. Those 32-entry pools
# indexed by ``chapter_number % len(pool)`` guaranteed a periodic collision
# every ~32 chapters and were the root cause of cross-book title clones
# (e.g. "Cipher Crossing" appearing in 4 different books). Fallback titles
# now flow through ``_chapter_fallback_subtitle`` which derives them from
# the chapter's own ``main_conflict`` / ``unique_beat`` / ``chapter_goal``
# via ``bestseller.services.title_dedup.derive_title_from_content``.


def _fallback_title_from_chapter_number(
    chapter_number: int,
    *,
    language: str | None = None,
    salt: int = 0,
) -> str:
    """Last-resort title that is guaranteed unique per chapter number.

    Used by :func:`_fallback_chapter_outline_batch` ONLY when content
    extraction is fully exhausted (all of unique_beat / main_conflict /
    chapter_goal are templated cycles and every concrete extract is
    already taken). Returns ``节<N>`` for Chinese / ``Beat <N>`` for
    English — obviously a placeholder rather than a real story title,
    so an operator scanning the output can immediately tell that the
    fallback degraded mode was hit and the LLM path needs fixing.

    ``salt`` lets the caller request a different placeholder when the
    primary one collides with an unrelated already-used title.
    """

    is_en = is_english_language(language)
    if salt > 0:
        return f"Beat {chapter_number}-{salt}" if is_en else f"节{chapter_number}-{salt}"
    return f"Beat {chapter_number}" if is_en else f"节{chapter_number}"


def _chapter_fallback_subtitle(
    chapter_number: int,
    phase: str,
    index_within_volume: int,
    volume_number: int,
    *,
    language: str | None = None,
    is_opening: bool,
    project_slug: str = "",
    unique_beat: str | None = None,
    chapter_goal: str | None = None,
    main_conflict: str | None = None,
    used_titles: set[str] | None = None,
) -> str:
    """Derive a fallback subtitle from THIS chapter's own content.

    History: this function used to index a 32-entry fixed pool by
    ``chapter_number % len(pool)``, which guaranteed a periodic collision
    every ~32 chapters and produced the cross-book title clones audited
    in 2026-05. The pool approach is now retired.

    New contract: extract a 2-6 character concrete noun phrase from
    ``unique_beat`` / ``main_conflict`` / ``chapter_goal`` via
    :func:`bestseller.services.title_dedup.derive_title_from_content`.
    If no phrase can be extracted, raise :class:`PlannerFallbackError`
    rather than emitting a guaranteed-collision pool word — failing
    loudly here surfaces the underlying outline problem (no concrete
    main_conflict) instead of silently producing duplicates.

    Parameters
    ----------
    used_titles:
        Optional set of titles already emitted earlier in the same
        fallback batch. When provided, the extractor will skip any
        candidate already in the set so the batch as a whole stays
        unique even if the templated content_repeats a phrase across
        chapters. The caller is responsible for adding the returned
        title to this set after the call.

    Note: callers that previously *relied* on this function always
    returning a string need to update their error handling. The repair
    loop in :func:`_generate_volume_outline_with_repair_loop` already
    catches ``PlannerFallbackError`` and re-prompts the LLM, so this is
    the right place to surface the problem.
    """

    derived = derive_title_from_content(
        main_conflict=main_conflict,
        unique_beat=unique_beat,
        chapter_goal=chapter_goal,
        language=language,
        exclude=used_titles or (),
    )
    if derived:
        return derived

    raise PlannerFallbackError(
        f"_chapter_fallback_subtitle: chapter {chapter_number} (volume {volume_number}) "
        f"has no extractable concrete phrase. "
        f"main_conflict={(main_conflict or '')[:80]!r} "
        f"unique_beat={(unique_beat or '')[:80]!r} "
        f"chapter_goal={(chapter_goal or '')[:80]!r}. "
        "Refusing to substitute a fixed-pool word — the LLM outline path "
        "must produce a concrete title."
    )


def _varied_scene_type(
    base_type: str,
    chapter_number: int,
    scene_number: int,
    phase: str,
    prev_phase: str | None,
    *,
    project_slug: str = "",
) -> str:
    """Choose a richer scene type based on pacing context.

    Uses a hash-based seed derived from ``project_slug``, ``chapter_number``
    and ``scene_number`` so that different novels produce different scene-type
    sequences even when the chapter layout is identical.
    """
    # Build a per-position seed that varies across novels
    _seed_input = f"{project_slug}:{chapter_number}:{scene_number}:{phase}"
    _hash_val = int(hashlib.md5(_seed_input.encode(), usedforsecurity=False).hexdigest()[:8], 16)

    # After a climax or reversal chapter, first scene should be aftermath/introspection
    if (
        scene_number == 1
        and prev_phase in ("climax", "reversal")
        and phase in ("setup", "investigation")
    ):
        return _SCENE_TYPE_AFTER_CLIMAX[_hash_val % len(_SCENE_TYPE_AFTER_CLIMAX)]
    # Middle scenes in investigation phase can be relationship or worldbuilding
    if scene_number == 2 and phase == "investigation":
        return _SCENE_TYPE_AFTER_PRESSURE[_hash_val % len(_SCENE_TYPE_AFTER_PRESSURE)]
    # Periodic comic relief — vary the interval per novel (5–9 chapters);
    # use a chapter-only seed so the interval is stable regardless of scene_number.
    _ch_hash = int(
        hashlib.md5(f"{project_slug}:{chapter_number}".encode(), usedforsecurity=False).hexdigest()[
            :8
        ],
        16,
    )
    _comic_interval = 5 + (_ch_hash % 5)
    if (
        chapter_number % _comic_interval == 0
        and scene_number == 1
        and phase not in ("climax", "reversal")
    ):
        return "comic_relief"
    return base_type


def _compute_scene_count(
    chapter_number: int,
    phase: str,
    prev_phase: str | None,
    chapters_from_end: int,
) -> int:
    """Vary scene count per chapter to break the mechanical 3-scene rhythm.

    Returns 2, 3, or 4:
    - 2 scenes: aftermath chapters (post-climax cool-down), resolution chapters
    - 4 scenes: climax chapters, major confrontation chapters
    - 3 scenes: everything else (the baseline)
    """
    # Post-climax aftermath: compact 2-scene chapter
    if prev_phase in ("climax", "reversal") and phase in ("setup", "investigation"):
        return 2
    # Climax and confrontation chapters get more breathing room
    if phase in ("climax", "reversal"):
        return 4
    # Final resolution chapter: compact
    if chapters_from_end == 0:
        return 2
    # Every ~10th chapter: a tighter 2-scene chapter for pacing variety
    if chapter_number > 5 and chapter_number % 10 == 0 and phase not in ("pressure",):
        return 2
    return 3


def _normalize_conflict_phase_key(conflict_phase: str) -> str:
    """Normalize LLM/fallback conflict labels into the internal phase enum."""

    raw = (conflict_phase or "").strip()
    key = raw.lower()
    direct = {
        "survival": "survival",
        "political_intrigue": "political_intrigue",
        "betrayal": "betrayal",
        "faction_war": "faction_war",
        "existential_threat": "existential_threat",
        "internal_reckoning": "internal_reckoning",
    }
    if key in direct:
        return direct[key]
    zh_aliases = (
        ("生存", "survival"),
        ("求生", "survival"),
        ("权力", "political_intrigue"),
        ("政治", "political_intrigue"),
        ("背叛", "betrayal"),
        ("信任", "betrayal"),
        ("派系", "faction_war"),
        ("阵营", "faction_war"),
        ("多方", "faction_war"),
        ("终局", "existential_threat"),
        ("存在", "existential_threat"),
        ("灭绝", "existential_threat"),
        ("内心", "internal_reckoning"),
        ("自我", "internal_reckoning"),
        ("精神", "internal_reckoning"),
    )
    for marker, phase in zh_aliases:
        if marker in raw:
            return phase
    return key or "survival"


def _reader_visible_force_name(force_name: str, phase_key: str, *, is_en: bool) -> str:
    """Replace structural force labels with concrete obstacle names for fallback outlines."""

    value = (force_name or "").strip()
    generic_markers = (
        "生存压力",
        "权力摩擦",
        "信任危机",
        "多方冲突",
        "终局威胁",
        "内在清算",
        "代表的势力角力",
        "Immediate Survival Pressure",
        "Power Friction",
        "Trust Collapse",
        "Multi-Side Collision",
        "Endgame Threat",
        "Internal Reckoning",
    )
    if value and not any(marker in value for marker in generic_markers):
        return value
    if is_en:
        fallback = {
            "survival": "the checkpoint patrol",
            "political_intrigue": "the permit bureau",
            "betrayal": "the compromised ally",
            "faction_war": "the rival blockade fleet",
            "existential_threat": "the sealed command core",
            "internal_reckoning": "the protagonist's buried guilt",
        }
        return fallback.get(phase_key, "the active opposition")
    fallback = {
        "survival": "封锁巡检队",
        "political_intrigue": "通行许可局",
        "betrayal": "被收买的盟友",
            "faction_war": "对峙封锁舰队",
        "existential_threat": "封存指挥核心",
        "internal_reckoning": "主角埋藏的愧疚",
    }
    return fallback.get(phase_key, "眼前阻力")


def _render_chapter_conflict(
    conflict_phase: str,
    chapter_phase: str,
    protagonist: str,
    force_name: str,
    *,
    project_slug: str = "",
    chapter_number: int = 1,
) -> str:
    """Generate a chapter-level conflict summary with per-novel diversity.

    First attempts to use the rich per-conflict-phase templates from
    ``_CHAPTER_CONFLICT_TEMPLATES``; falls back to a generic description
    when the conflict_phase or chapter_phase is unrecognized.
    """
    is_en = bool(re.search(r"[A-Za-z]", protagonist or force_name or ""))
    phase_key = _normalize_conflict_phase_key(conflict_phase)
    visible_force_name = _reader_visible_force_name(force_name, phase_key, is_en=is_en)

    # Try rich templates first (keyed by conflict_phase × chapter_phase)
    _templates = _CHAPTER_CONFLICT_TEMPLATES_EN if is_en else _CHAPTER_CONFLICT_TEMPLATES
    phase_dict = _templates.get(phase_key, {})
    rich_template = phase_dict.get(chapter_phase)
    if rich_template:
        return rich_template.format(protagonist=protagonist, force_name=visible_force_name)

    # Fallback: varied generic descriptions
    phase_labels = {
        "setup": "识别问题",
        "investigation": "推进调查",
        "pressure": "承受加压",
        "reversal": "迎来转折",
        "climax": "直面冲突",
    }
    phase_labels_en = {
        "setup": "establish the pressure",
        "investigation": "push the investigation",
        "pressure": "absorb the counter-pressure",
        "reversal": "handle a destabilising turn",
        "climax": "face the direct conflict",
    }
    label = (
        phase_labels_en.get(chapter_phase, "keep the plot moving")
        if is_en
        else phase_labels.get(chapter_phase, "继续推进")
    )
    _templates_generic_en = [
        f"{protagonist} must {label} while dealing with the active resistance around {visible_force_name}.",
        f"{protagonist} navigates escalating pressure from {visible_force_name} as the situation demands they {label}.",
        f"Caught between {visible_force_name}'s maneuvers and their own goals, {protagonist} fights to {label}.",
    ]
    _templates_generic_zh = [
        f"{protagonist}必须绕开「{visible_force_name}」设置的阻碍，拿到能改变局势的证据或筹码。",
        f"面对「{visible_force_name}」不断升级的施压，{protagonist}必须用一次具体行动夺回主动权。",
        f"{protagonist}被「{visible_force_name}」的布局和自身目标夹在中间，只能用高风险选择换取突破口。",
    ]
    return _pick_by_seed(
        _templates_generic_en if is_en else _templates_generic_zh,
        project_slug,
        chapter_number,
        "conflict_render",
    )


def _phase_name_within_arc(index: int, total: int) -> str:
    """Determine the narrative phase of a chapter within its arc.

    More granular than the per-volume 5-phase system, providing finer
    narrative rhythm control within each 12-chapter arc.
    """
    ratio = index / max(total, 1)
    if ratio <= 0.13:
        return "hook"
    if ratio <= 0.33:
        return "setup"
    if ratio <= 0.53:
        return "escalation"
    if ratio <= 0.73:
        return "twist"
    if ratio <= 0.87:
        return "climax"
    return "resolution_hook"


def _compute_chapter_arc_info(
    chapter_number: int,
    volume_plan: list[dict[str, Any]],
) -> tuple[int, str]:
    """Find which arc a chapter belongs to and its phase within that arc.

    Returns (arc_index, arc_phase). arc_index is global across the whole book.
    """
    global_arc_index = 0
    for vol in volume_plan:
        vol_map = _mapping(vol)
        arc_ranges = vol_map.get("arc_ranges")
        if not isinstance(arc_ranges, list):
            # Volume has no arc_ranges — treat entire volume as one arc
            ch_count = max(int(vol_map.get("chapter_count_target") or 1), 1)
            vol_start = _compute_volume_start(vol_map, volume_plan)
            vol_end = vol_start + ch_count - 1
            if vol_start <= chapter_number <= vol_end:
                idx_in_arc = chapter_number - vol_start
                total_in_arc = ch_count
                return global_arc_index, _phase_name_within_arc(idx_in_arc, total_in_arc)
            global_arc_index += 1
            continue
        for arc_range in arc_ranges:
            if isinstance(arc_range, list) and len(arc_range) == 2:
                arc_start, arc_end = arc_range
                if arc_start <= chapter_number <= arc_end:
                    idx_in_arc = chapter_number - arc_start
                    total_in_arc = arc_end - arc_start + 1
                    return global_arc_index, _phase_name_within_arc(idx_in_arc, total_in_arc)
                global_arc_index += 1
    return 0, "setup"


def _compute_volume_start(vol_map: dict[str, Any], volume_plan: list[dict[str, Any]]) -> int:
    """Compute the start chapter of a volume from the plan."""
    vol_num = vol_map.get("volume_number", 1)
    cursor = 1
    for v in volume_plan:
        v_map = _mapping(v)
        if v_map.get("volume_number") == vol_num:
            return cursor
        cursor += max(int(v_map.get("chapter_count_target") or 1), 1)
    return cursor


_WRITTEN_CHAPTER_STATUSES_FOR_OFFSET: tuple[str, ...] = (
    ChapterStatus.DRAFTING.value,
    ChapterStatus.REVIEW.value,
    ChapterStatus.REVISION.value,
    ChapterStatus.COMPLETE.value,
)


async def _max_written_chapter_number(session: AsyncSession, project_id: UUID) -> int:
    """Return the highest ``chapter_number`` already written for a project.

    Used as the authoritative offset when re-planning a tail volume — the
    fallback must not renumber chapters past this frontier. Returns 0 when
    nothing is written yet (fresh project).
    """
    stmt = select(func.max(ChapterModel.chapter_number)).where(
        ChapterModel.project_id == project_id,
        ChapterModel.status.in_(_WRITTEN_CHAPTER_STATUSES_FOR_OFFSET),
    )
    result = await session.scalar(stmt)
    return int(result or 0)


async def _fetch_existing_chapter_titles(
    session: AsyncSession,
    project_id: UUID,
    *,
    exclude_volume_number: int | None = None,
) -> list[tuple[int | None, str]]:
    """Fetch ``(chapter_number, title)`` pairs already persisted for a project.

    Used by the volume-outline validator to detect cross-volume title
    collisions: when generating volume N, the planner must not reuse any
    title from volumes ``1..N-1``. Passing the result through to
    :func:`_normalize_generated_outline_titles_or_fail` turns repeats into
    a hard validation failure that the repair loop will then re-prompt
    with a targeted directive.

    Parameters
    ----------
    session:
        Open async session bound to the project's database.
    project_id:
        The project whose titles should be returned.
    exclude_volume_number:
        When provided, chapters belonging to that volume are skipped via
        a JOIN on ``volumes.volume_number``. This matters when
        *re-planning* an existing volume — the chapters currently
        attached to that volume are about to be replaced, so they must
        not be treated as prior art.

    Returns
    -------
    list of ``(chapter_number, title)`` tuples for chapters with a
    non-empty title. Order is by ``chapter_number`` ascending so the
    repair-loop error message lists conflicts in book order. Chapters
    with empty or NULL titles are silently filtered out.

    Schema note: ``ChapterModel`` has no direct ``volume_number``
    column — chapters reference volumes via ``volume_id`` FK. To filter
    out a specific volume number we OUTER JOIN against ``VolumeModel``
    and exclude matching rows. Chapters with NULL ``volume_id`` (orphans
    or freshly-created rows pre-volume-assignment) are always included
    because they cannot logically belong to the excluded volume.
    """

    if exclude_volume_number is None:
        stmt = (
            select(
                ChapterModel.chapter_number,
                ChapterModel.title,
            )
            .where(ChapterModel.project_id == project_id)
            .order_by(ChapterModel.chapter_number.asc())
        )
    else:
        stmt = (
            select(
                ChapterModel.chapter_number,
                ChapterModel.title,
                VolumeModel.volume_number,
            )
            .where(ChapterModel.project_id == project_id)
            .join(
                VolumeModel,
                ChapterModel.volume_id == VolumeModel.id,
                isouter=True,
            )
            .where(
                (VolumeModel.volume_number == None)  # noqa: E711 — SQL NULL match
                | (VolumeModel.volume_number != exclude_volume_number)
            )
            .order_by(ChapterModel.chapter_number.asc())
        )
    rows = (await session.execute(stmt)).all()
    out: list[tuple[int | None, str]] = []
    for row in rows:
        cn = row[0]
        title = (row[1] or "").strip()
        if not title:
            continue
        out.append((int(cn) if cn is not None else None, title))
    return out


async def _next_chapter_number_for_volume(
    session: AsyncSession,
    project_id: UUID,
    volume_number: int,
) -> int:
    """Return the first ``chapter_number`` that a fresh replan of ``volume_number`` should use.

    Authority chain (strongest → weakest):
      1. ``min(chapter_number)`` across chapters already belonging to this
         volume. Replanning an existing volume must replace/update its current
         chapter range, never append a duplicate copy after the book frontier.
      2. ``max(chapter_number) + 1`` across ALL chapters belonging to *earlier*
         volumes (volume_number < N). This binds the start of volume N to the
         real DB layout regardless of what VOLUME_PLAN targets claim.
      3. ``max(chapter_number) + 1`` across all chapters in the project when
         no earlier-volume chapter exists (e.g. volume 1 or an empty project).

    Never trusts VOLUME_PLAN targets — those are exactly what drifted during
    the 200-chapter gap incident. Always ≥ 1.
    """
    current_stmt = (
        select(func.min(ChapterModel.chapter_number))
        .join(VolumeModel, ChapterModel.volume_id == VolumeModel.id)
        .where(
            ChapterModel.project_id == project_id,
            VolumeModel.volume_number == int(volume_number),
        )
    )
    current_min = int(await session.scalar(current_stmt) or 0)
    if current_min > 0:
        return current_min

    prior_stmt = (
        select(func.max(ChapterModel.chapter_number))
        .join(VolumeModel, ChapterModel.volume_id == VolumeModel.id)
        .where(
            ChapterModel.project_id == project_id,
            VolumeModel.volume_number < int(volume_number),
        )
    )
    prior_max = int(await session.scalar(prior_stmt) or 0)
    if prior_max > 0:
        return prior_max + 1
    any_stmt = select(func.max(ChapterModel.chapter_number)).where(
        ChapterModel.project_id == project_id,
    )
    any_max = int(await session.scalar(any_stmt) or 0)
    return max(any_max + 1, 1)


_EVENT_CYCLE_ROLE_SEQUENCE: tuple[str, ...] = (
    "trigger",
    "desire_lock",
    "obstacle_escalation",
    "method_search",
    "execution_turn",
    "payoff_feedback",
)

_INFORMATION_GAP_SEQUENCE: tuple[str, ...] = (
    "reader_knows_equal",
    "protagonist_knows_less",
    "reader_knows_less",
    "reader_knows_more",
    "others_hide_truth",
)


def _fallback_event_cycle_role(
    *,
    chapter_function: str,
    phase: str,
    index_within_volume: int,
    chapters_from_end: int,
) -> str:
    if chapters_from_end <= 2:
        return "payoff_feedback"
    if phase in {"reversal", "climax", "confrontation"}:
        return "execution_turn"
    if chapter_function == "reveal":
        return "execution_turn"
    if chapter_function == "action" and phase in {"pressure", "escalation"}:
        return "obstacle_escalation"
    return _EVENT_CYCLE_ROLE_SEQUENCE[(index_within_volume - 1) % len(_EVENT_CYCLE_ROLE_SEQUENCE)]


def _fallback_information_gap_mode(
    *,
    project_slug: str,
    chapter_number: int,
    role: str,
) -> str:
    if role == "trigger":
        return "reader_knows_equal"
    if role in {"method_search", "execution_turn"}:
        return "reader_knows_less"
    if role == "payoff_feedback":
        return "reader_knows_more"
    return _pick_by_seed(
        list(_INFORMATION_GAP_SEQUENCE),
        project_slug,
        chapter_number,
        f"information_gap:{role}",
    )


def _fallback_event_cycle_contract(
    *,
    is_en: bool,
    volume_number: int,
    index_within_volume: int,
    role: str,
    protagonist_name: str,
    force_name: str,
    volume_goal: str,
    chapter_unique_beat: str,
    chapter_hook_description: str,
    chapter_function: str,
    information_gap_mode: str,
) -> dict[str, Any]:
    event_unit_id = f"v{volume_number}-event-{((index_within_volume - 1) // 6) + 1}"
    common = (
        {
            "event_unit_id": event_unit_id,
            "chapter_event_role": role,
            "information_gap_mode": information_gap_mode,
            "reader_desire": (
                f"Readers want to see how {protagonist_name} turns "
                f"{chapter_unique_beat} into movement on {volume_goal}."
            ),
            "event_pressure": (
                f"{force_name} uses {chapter_unique_beat} to close the easy path."
            ),
            "step_focus": (
                f"This chapter serves the `{role}` role inside the event unit, "
                "not a full six-step event by itself."
            ),
            "expected_state_delta": (
                f"The chapter must visibly change {chapter_function}, pressure, "
                "relationship, resource, clue, status, or exposure state."
            ),
            "handoff_to_next": chapter_hook_description,
        }
        if is_en
        else {
            "event_unit_id": event_unit_id,
            "chapter_event_role": role,
            "information_gap_mode": information_gap_mode,
            "reader_desire": f"读者想看到{protagonist_name}如何把「{chapter_unique_beat}」转化为「{volume_goal}」的推进。",
            "event_pressure": f"{force_name}借「{chapter_unique_beat}」关闭轻松路径。",
            "step_focus": f"本章只承担事件单元中的「{role}」职责，不在单章内复刻完整六步。",
            "expected_state_delta": "本章必须让剧情、压力、关系、资源、线索、身份或暴露风险发生可见变化。",
            "handoff_to_next": chapter_hook_description,
        }
    )
    role_specific_en = {
        "trigger": {
            "emotion_event": f"{chapter_unique_beat} disrupts the current balance."
        },
        "desire_lock": {
            "desire_goal": f"{protagonist_name} commits to advancing {volume_goal} despite the narrowing options."
        },
        "obstacle_escalation": {
            "obstacle": f"{force_name} turns {chapter_unique_beat} into visible resistance or cost."
        },
        "method_search": {
            "solution_method": f"{protagonist_name} searches for a concrete method that can answer {chapter_unique_beat}."
        },
        "execution_turn": {
            "action_resolution": f"{protagonist_name} executes a turn around {chapter_unique_beat} and changes the local balance."
        },
        "payoff_feedback": {
            "resolution_feedback": f"The chapter pays off one part of {chapter_unique_beat} while exposing the next consequence."
        },
        "reaction_reset": {
            "next_reader_waiting": chapter_hook_description,
        },
        "bridge_hook": {
            "next_reader_waiting": chapter_hook_description,
        },
    }
    role_specific_zh = {
        "trigger": {"emotion_event": f"「{chapter_unique_beat}」打破当前平衡。"},
        "desire_lock": {
            "desire_goal": f"{protagonist_name}决定在选择收窄前继续推进「{volume_goal}」。"
        },
        "obstacle_escalation": {
            "obstacle": f"{force_name}把「{chapter_unique_beat}」转化为可见阻力或代价。"
        },
        "method_search": {
            "solution_method": f"{protagonist_name}寻找能回应「{chapter_unique_beat}」的具体方法。"
        },
        "execution_turn": {
            "action_resolution": f"{protagonist_name}围绕「{chapter_unique_beat}」执行转折行动并改变局部局势。"
        },
        "payoff_feedback": {
            "resolution_feedback": f"本章兑现「{chapter_unique_beat}」的一部分，同时暴露下一层后果。"
        },
        "reaction_reset": {"next_reader_waiting": chapter_hook_description},
        "bridge_hook": {"next_reader_waiting": chapter_hook_description},
    }
    common.update((role_specific_en if is_en else role_specific_zh).get(role, {}))
    return common


def _fallback_chapter_outline_batch(
    project: ProjectModel,
    book_spec: dict[str, Any],
    cast_spec: dict[str, Any],
    volume_plan: list[dict[str, Any]],
    *,
    category_key: str | None = None,
    chapter_number_offset: int = 1,
) -> dict[str, Any]:
    """Synthesize a best-effort chapter outline batch.

    ``chapter_number_offset`` is the first global chapter_number to assign
    (default 1). Pass the existing volume frontier when building the
    fallback prompt payload for a replan so any explicit fail-fast diagnostics
    report chapter numbers anchored to the DB layout.
    """
    writing_profile = _planner_writing_profile(project)
    cast_payload = _mapping(cast_spec)
    protagonist_name = _non_empty_string(
        _mapping(cast_payload.get("protagonist")).get("name"), "主角"
    )
    supporting_cast = _mapping_list(cast_payload.get("supporting_cast"))
    ally_name = _non_empty_string(
        _named_item(supporting_cast, 0, protagonist_name).get("name"), protagonist_name
    )
    antagonist_name = _non_empty_string(
        _mapping(cast_payload.get("antagonist")).get("name"), "敌人"
    )
    normalized_volume_plan = _mapping_list(volume_plan)
    if not normalized_volume_plan:
        normalized_volume_plan = [
            {
                "volume_number": 1,
                "chapter_count_target": max(project.target_chapters, 1),
                "volume_goal": "推动主线调查取得关键进展",
            }
        ]
    # Build antagonist-character lookup from supporting_cast for scene participants
    _antag_chars: dict[str, str] = {}
    for sc in supporting_cast:
        sc_map = _mapping(sc)
        if _non_empty_string(sc_map.get("role"), "") == "antagonist":
            sc_name = _non_empty_string(sc_map.get("name"), "")
            if sc_name:
                _antag_chars[sc_name] = sc_name

    # Normalize antagonist_forces once — handle both Pydantic models and raw dicts
    raw_forces = cast_payload.get("antagonist_forces") or []
    if not isinstance(raw_forces, list):
        raw_forces = []
    normalized_forces: list[dict[str, Any]] = []
    for f in raw_forces:
        if isinstance(f, dict):
            normalized_forces.append(f)
        elif hasattr(f, "model_dump"):
            normalized_forces.append(f.model_dump())
        else:
            normalized_forces.append(_mapping(f))
    worldview_outline_contract = _fallback_worldview_outline_contract(project)

    chapters: list[dict[str, Any]] = []
    chapter_number = max(int(chapter_number_offset), 1)
    # Track titles emitted so far in this fallback batch so
    # `_chapter_fallback_subtitle` can skip duplicates and produce a
    # batch-unique result even when the templated content recycles
    # phrases across chapters. The set is mutated below as each
    # chapter's title is produced.
    fallback_used_titles: set[str] = set()
    _gen = get_settings().generation
    chapter_target_words = max(
        _gen.words_per_chapter.min,
        min(
            _gen.words_per_chapter.target,
            int(project.target_word_count / max(project.target_chapters, 1)),
        ),
    )
    _scene_count_target = _gen.scenes_per_chapter.target or 5
    scene_target_words = max(
        _gen.words_per_scene.min,
        min(_gen.words_per_scene.target, int(chapter_target_words / max(_scene_count_target, 1))),
    )
    prev_phase: str | None = None
    for raw_volume_index, volume in enumerate(normalized_volume_plan, start=1):
        volume_payload = _mapping(volume)
        total_in_volume = max(int(volume_payload.get("chapter_count_target") or 1), 1)
        volume_goal = _non_empty_string(
            volume_payload.get("volume_goal"), "推动主线调查取得关键进展"
        )
        volume_number = int(volume_payload.get("volume_number") or raw_volume_index)
        # Extract per-volume conflict phase and force name
        conflict_phase = _non_empty_string(volume_payload.get("conflict_phase"), "survival")
        volume_force_name = _non_empty_string(
            volume_payload.get("primary_force_name"), antagonist_name
        )
        # Determine the primary antagonist character for this volume's scenes
        volume_antag_participant = antagonist_name  # default
        for af in normalized_forces:
            active_vols = af.get("active_volumes") or []
            if isinstance(active_vols, list) and volume_number in active_vols:
                char_ref = _non_empty_string(af.get("character_ref"), "")
                if char_ref:
                    volume_antag_participant = char_ref
                break

        for index_within_volume in range(1, total_in_volume + 1):
            phase = _phase_name(index_within_volume, total_in_volume)
            is_opening_chapter = chapter_number <= 3

            # Ending contract: graduated wind-down that scales with novel length.
            # - Last 3 chapters: hard contract (convergence → confrontation → resolution)
            # - Wind-down zone (5% of total, min 5, max 20): begin resolving threads
            # - Before wind-down: normal chapter goals
            total_ch = max(project.target_chapters, 1)
            is_en = is_english_language(project.language)
            chapters_from_end = total_ch - chapter_number
            wind_down_size = max(5, min(20, round(total_ch * 0.05)))

            if chapters_from_end == 2:
                chapter_goal = (
                    "Final preparations before the ultimate confrontation — all foreshadowing threads converge."
                    if is_en
                    else f"决战前最后准备——{protagonist_name}的所有伏笔汇聚，各方力量到位。"
                )
            elif chapters_from_end == 1:
                chapter_goal = (
                    "The ultimate confrontation or core mystery revealed — the story's central conflict reaches its peak."
                    if is_en
                    else f"终极对决或核心悬念揭晓——{protagonist_name}与命运正面交锋。"
                )
            elif chapters_from_end == 0:
                chapter_goal = (
                    "Resolution landing — emotional closure, lingering resonance, and the final image."
                    if is_en
                    else f"结局着陆——{protagonist_name}的情感收束，余韵留白，最终画面。"
                )
            elif chapters_from_end < wind_down_size:
                # Graduated wind-down zone: start resolving open threads
                wind_progress = 1.0 - (chapters_from_end / wind_down_size)
                if wind_progress < 0.33:
                    chapter_goal = (
                        f"Begin tying off secondary subplots — {protagonist_name} confronts lingering loose ends while the final threat crystallizes."
                        if is_en
                        else f"开始收束次要支线——{protagonist_name}处理遗留问题，终极威胁逐渐明朗。"
                    )
                elif wind_progress < 0.66:
                    chapter_goal = (
                        f"Resolve major relationship arcs and pay off key planted clues — {protagonist_name} faces difficult personal choices."
                        if is_en
                        else f"收束主要人物关系线、回收关键伏笔——{protagonist_name}面临艰难的个人抉择。"
                    )
                else:
                    chapter_goal = (
                        "Final alliances formed, last secrets revealed — all forces converge toward the climactic confrontation."
                        if is_en
                        else "最终联盟结成、最后的秘密揭露——所有力量向高潮对决汇聚。"
                    )
            else:
                # Expanded template pool (20+ variants) + position-aware seeding
                # to minimize cross-chapter collisions. Previously only 7 templates
                # were seeded with just (slug, chapter) → adjacent chapters often
                # landed on the same template, causing identical scene purposes
                # across chapters 6/7, 13/14 etc. Now seeded with volume+index too.
                _goal_templates_en = [
                    f"{protagonist_name} advances the volume goal in chapter {chapter_number}, forcing the situation into a new high-pressure phase.",
                    f"{protagonist_name} uncovers a critical clue that reframes the entire conflict, but the discovery comes at a steep personal cost.",
                    f"An unexpected alliance shifts the power balance — {protagonist_name} must decide whether to trust a former adversary.",
                    f"{protagonist_name} is forced into a desperate gambit when the existing plan collapses, revealing deeper layers of the conspiracy.",
                    f"The stakes escalate as {protagonist_name} discovers the true scope of the threat, and a ticking clock forces immediate action.",
                    f"{protagonist_name} confronts an internal contradiction that mirrors the external conflict, and must reconcile both to move forward.",
                    f"A secondary character's hidden agenda surfaces, complicating {protagonist_name}'s path and forcing a painful re-evaluation.",
                    f"{protagonist_name} must infiltrate hostile territory where a single mistake exposes the entire operation.",
                    f"A long-buried secret from {protagonist_name}'s past resurfaces, threatening to unravel present alliances.",
                    f"The antagonist makes a calculated move that forces {protagonist_name} onto unfamiliar ground with diminished resources.",
                    f"{protagonist_name} gains a temporary advantage through an unexpected resource, but its use carries a hidden cost.",
                    f"A trusted ally takes an action {protagonist_name} cannot yet understand — is it betrayal, strategy, or something else?",
                    f"{protagonist_name} must choose between two imperfect paths, each closing off a future possibility permanently.",
                    f"The established rules of the world are broken in a small but telling way — {protagonist_name} is the first to notice.",
                    f"A bystander is drawn into the conflict by accident, and {protagonist_name} must protect them while pursuing the real objective.",
                    f"{protagonist_name} attempts a breakthrough in cultivation or skill that requires reconciling two opposing principles.",
                    f"A rival's public humiliation of {protagonist_name} masks a more dangerous private maneuver happening in parallel.",
                    f"{protagonist_name} receives incomplete information and must decide whether acting now or waiting is the greater risk.",
                    f"An environmental or systemic threat emerges that cannot be defeated by force alone — {protagonist_name} must find another way.",
                    f"{protagonist_name} is offered a deal that would solve the immediate problem but compromise a core principle.",
                ]
                _goal_templates_zh = [
                    f"{protagonist_name}在第{chapter_number}章推进{volume_goal}，并迫使局势进入新的高压阶段。",
                    f"{protagonist_name}发现了一条改写整个冲突格局的关键线索，但代价是一次沉重的个人损失。",
                    f"一个出乎意料的合作打破了力量平衡——{protagonist_name}必须决定是否信任曾经的对手。",
                    f"原有计划彻底崩盘，{protagonist_name}被迫孤注一掷，同时揭开了更深层的阴谋。",
                    f"威胁的真实规模浮出水面，倒计时开始——{protagonist_name}必须立即行动。",
                    f"{protagonist_name}面对一个与外部冲突互为镜像的内心矛盾，必须同时解决才能前进。",
                    f"一个次要角色的隐藏目的浮出水面，打乱了{protagonist_name}的部署，迫使他重新评估局势。",
                    f"{protagonist_name}必须潜入敌方腹地，一个失误就会暴露整个行动。",
                    f"{protagonist_name}过去埋下的秘密突然浮现，威胁当下所有盟友关系。",
                    f"反派落下一步精心布置的棋，{protagonist_name}被迫在陌生地界以更少资源应对。",
                    f"{protagonist_name}借助意外资源取得短暂优势，但这份资源带着隐性代价。",
                    f"一位被信任的盟友做出{protagonist_name}一时难以理解的举动——是背叛、策略，还是另有隐情？",
                    f"{protagonist_name}必须在两条不完美的路之间选择，每一条都会永久关闭某种未来可能。",
                    f"世界既定规则出现一个细小却关键的裂缝——{protagonist_name}是第一个察觉的人。",
                    f"一个路人意外卷入冲突，{protagonist_name}必须在保护对方的同时继续推进真正目标。",
                    f"{protagonist_name}尝试一次突破，需要调和两种对立的修炼/技巧原则。",
                    f"对手在公开场合羞辱{protagonist_name}，以此掩护一场更危险的暗中布置。",
                    f"{protagonist_name}拿到不完整的情报，必须判断：立即行动与继续等待，哪一个更冒险？",
                    f"一种环境或体系层面的威胁出现，无法用力量硬碰硬——{protagonist_name}必须另辟蹊径。",
                    f"{protagonist_name}被提出一个能解决眼前难题的交易，但代价是动摇一条核心原则。",
                ]
                # Position-aware seed: include volume + in-volume index so
                # adjacent chapters reliably draw different templates.
                chapter_goal = _pick_by_seed(
                    _goal_templates_en if is_en else _goal_templates_zh,
                    project.slug,
                    chapter_number,
                    f"chapter_goal:v{volume_number}:i{index_within_volume}:p{phase}",
                )
            chapter_unique_beat = _fallback_unique_chapter_beat(
                project_slug=project.slug,
                chapter_number=chapter_number,
                phase=phase,
                protagonist=protagonist_name,
                force_name=volume_force_name,
                language=project.language,
            )
            chapter_goal = (
                f"{chapter_goal} {protagonist_name} must handle {chapter_unique_beat} and leave with a usable result or a clear loss."
                if is_en
                else f"{chapter_goal} {protagonist_name}必须处理「{chapter_unique_beat}」，并获得可用结果或付出明确损失。"
            )
            chapter_hook_description = _fallback_hook_description(
                project_slug=project.slug,
                chapter_number=chapter_number,
                phase=phase,
                unique_beat=chapter_unique_beat,
                protagonist=protagonist_name,
                language=project.language,
            )
            chapter_conflict = _render_chapter_conflict(
                conflict_phase,
                phase,
                protagonist_name,
                volume_force_name,
                project_slug=project.slug,
                chapter_number=chapter_number,
            )
            chapter_conflict = (
                f"{chapter_conflict} Chapter-specific pressure: {chapter_unique_beat}."
                if is_en
                else f"{chapter_conflict} 本章独有压力：{chapter_unique_beat}。"
            )
            num_scenes = _compute_scene_count(chapter_number, phase, prev_phase, chapters_from_end)
            # Build scenes dynamically: opening + N middle + closing hook
            scenes: list[dict[str, Any]] = []

            # Scene 1: Opening. Keep fallback scene cards reader-visible; do
            # not embed chapter numbers or author-note goal tags because those
            # can leak into draft prose when a repair path inspects scene cards.
            opening_story = (
                f"The opening turns on {chapter_unique_beat}; {protagonist_name} must choose an immediate direction under near-term pressure."
                if is_en and is_opening_chapter
                else f"The prior consequence closes in through {chapter_unique_beat}; {protagonist_name} must act before the pressure traps them."
                if is_en
                else f"开场以「{chapter_unique_beat}」切入，{protagonist_name}必须马上判断行动方向。"
                if is_opening_chapter
                else f"上一章后果通过「{chapter_unique_beat}」压到{protagonist_name}面前，迫使他立刻应对。"
            )
            opening_emotion = (
                "Give the reader a clear point of engagement, then increase instability."
                if is_en and is_opening_chapter
                else "Sustain pressure and uncertainty."
                if is_en
                else "先建立明确吸引点，再持续抬高不确定性"
                if is_opening_chapter
                else "持续拉高压力和不确定性"
            )
            scenes.append(
                {
                    "scene_number": 1,
                    "scene_type": "hook"
                    if is_opening_chapter
                    else _varied_scene_type(
                        "setup" if phase == "setup" else "transition",
                        chapter_number,
                        1,
                        phase,
                        prev_phase,
                        project_slug=project.slug,
                    ),
                    "title": "Opening Beat" if is_en else "开场",
                    "time_label": (
                        f"Opening pressure: {chapter_unique_beat}"
                        if is_en
                        else f"开场压力：{chapter_unique_beat}"
                    ),
                    "participants": [protagonist_name, ally_name],
                    "purpose": {
                        "story": opening_story,
                        "emotion": opening_emotion,
                    },
                    "entry_state": {
                        protagonist_name: {
                            "arc_state": _pick_by_seed(
                                ["承压推进", "犹豫不决", "暗中筹谋", "被迫应对", "重振旗鼓"],
                                project.slug,
                                chapter_number,
                                "entry_arc",
                            ),
                            "emotion": _pick_by_seed(
                                ["紧绷", "焦虑", "冷静克制", "愤怒压抑", "期待中带着不安"],
                                project.slug,
                                chapter_number,
                                "entry_emo",
                            ),
                        },
                        ally_name: {
                            "arc_state": _pick_by_seed(
                                ["谨慎协作", "主动支援", "心存疑虑", "独立行动", "勉强配合"],
                                project.slug,
                                chapter_number,
                                "ally_entry",
                            ),
                            "emotion": _pick_by_seed(
                                ["戒备", "忧虑", "冷静", "不安", "坚定"],
                                project.slug,
                                chapter_number,
                                "ally_emo",
                            ),
                        },
                    },
                    "exit_state": {
                        protagonist_name: {
                            "arc_state": _pick_by_seed(
                                ["主动出击", "获得线索", "陷入困境", "做出抉择", "暂时脱险"],
                                project.slug,
                                chapter_number,
                                "exit_arc",
                            ),
                            "emotion": _pick_by_seed(
                                ["更坚定", "沉重", "释然", "紧迫感", "复杂交织"],
                                project.slug,
                                chapter_number,
                                "exit_emo",
                            ),
                        },
                        ally_name: {
                            "arc_state": _pick_by_seed(
                                ["被迫跟进", "选择信任", "产生分歧", "承担更多", "暗自打算"],
                                project.slug,
                                chapter_number,
                                "ally_exit",
                            ),
                            "emotion": _pick_by_seed(
                                ["压力上升", "决心", "动摇", "疲惫", "隐忍"],
                                project.slug,
                                chapter_number,
                                "ally_exit_emo",
                            ),
                        },
                    },
                    "target_word_count": scene_target_words,
                }
            )

            # Middle scenes (0 for 2-scene, 1 for 3-scene, 2 for 4-scene)
            middle_count = num_scenes - 2
            # Seed-based scene type selection for diversity across novels
            _ch_seed = int(
                hashlib.md5(
                    f"{project.slug}:mid:{chapter_number}".encode(), usedforsecurity=False
                ).hexdigest()[:8],
                16,
            )
            _HIGH_TENSION_TYPES = [
                "conflict",
                "confrontation",
                "desperate_gambit",
                "tactical_clash",
            ]
            _LOW_TENSION_TYPES = ["reveal", "discovery", "negotiation", "deduction"]
            _REFLECTION_TYPES = [
                "introspection",
                "relationship_building",
                "moral_dilemma",
                "quiet_revelation",
            ]
            _middle_types = [
                _HIGH_TENSION_TYPES[_ch_seed % len(_HIGH_TENSION_TYPES)]
                if phase in {"pressure", "reversal", "climax"}
                else _LOW_TENSION_TYPES[_ch_seed % len(_LOW_TENSION_TYPES)],
                _REFLECTION_TYPES[(_ch_seed >> 4) % len(_REFLECTION_TYPES)]
                if phase not in ("climax",)
                else _HIGH_TENSION_TYPES[(_ch_seed >> 4) % len(_HIGH_TENSION_TYPES)],
            ]
            for mi in range(middle_count):
                base_type = _middle_types[mi % len(_middle_types)]
                scenes.append(
                    {
                        "scene_number": len(scenes) + 1,
                        "scene_type": _varied_scene_type(
                            base_type,
                            chapter_number,
                            len(scenes) + 1,
                            phase,
                            prev_phase,
                            project_slug=project.slug,
                        ),
                        "title": ("Primary Move" if mi == 0 else "Shift")
                        if is_en
                        else ("推进" if mi == 0 else "变化"),
                        "time_label": (
                            f"Middle pressure {mi + 1}: {chapter_unique_beat}"
                            if is_en
                            else f"中段压力{mi + 1}：{chapter_unique_beat}"
                        ),
                        "participants": [protagonist_name, volume_antag_participant]
                        if index_within_volume % 2 == 0
                        else [protagonist_name, ally_name],
                        "purpose": {
                            "story": (
                                f"{chapter_unique_beat} produces a fresh cost or new information."
                                if mi == 0
                                else f"{chapter_unique_beat} is complicated by a deeper cost, truth, or shift."
                            )
                            if is_en
                            else (
                                f"「{chapter_unique_beat}」制造新的代价或信息交换。"
                                if mi == 0
                                else f"「{chapter_unique_beat}」暴露更深一层的代价、真相或变化。"
                            ),
                            "emotion": "Raise friction without flattening the chapter rhythm."
                            if is_en
                            else "继续抬高摩擦感，但不把章节写成单一节奏。",
                        },
                        "entry_state": {
                            protagonist_name: {
                                "arc_state": _pick_by_seed(
                                    [
                                        "带着怀疑推进",
                                        "谨慎试探",
                                        "果断介入",
                                        "被动应战",
                                        "暗中观察",
                                    ],
                                    project.slug,
                                    chapter_number + mi,
                                    "mid_entry",
                                ),
                                "emotion": _pick_by_seed(
                                    ["警觉", "冷静", "焦躁", "隐忍", "好奇"],
                                    project.slug,
                                    chapter_number + mi,
                                    "mid_entry_emo",
                                ),
                            },
                        },
                        "exit_state": {
                            protagonist_name: {
                                "arc_state": _pick_by_seed(
                                    [
                                        "掌握更多真相",
                                        "付出代价",
                                        "发现矛盾",
                                        "暂时得利",
                                        "陷入两难",
                                    ],
                                    project.slug,
                                    chapter_number + mi,
                                    "mid_exit",
                                ),
                                "emotion": _pick_by_seed(
                                    ["不安", "震惊", "隐隐兴奋", "沉重", "决绝"],
                                    project.slug,
                                    chapter_number + mi,
                                    "mid_exit_emo",
                                ),
                            },
                            antagonist_name: {
                                "arc_state": _pick_by_seed(
                                    [
                                        "开始主动压制",
                                        "暗中调整策略",
                                        "示弱引诱",
                                        "全面出击",
                                        "布下新局",
                                    ],
                                    project.slug,
                                    chapter_number + mi,
                                    "antag_mid",
                                ),
                                "emotion": _pick_by_seed(
                                    ["冷静施压", "得意", "谨慎", "愤怒", "隐忍待发"],
                                    project.slug,
                                    chapter_number + mi,
                                    "antag_mid_emo",
                                ),
                            },
                        },
                        "target_word_count": scene_target_words,
                    }
                )

            # Final scene: closing hook
            scenes.append(
                {
                    "scene_number": len(scenes) + 1,
                    "scene_type": "hook",
                    "title": "Closing Hook" if is_en else "尾钩",
                    "time_label": (
                        f"Closing pressure: {chapter_unique_beat}"
                        if is_en
                        else f"尾声压力：{chapter_unique_beat}"
                    ),
                    "participants": [protagonist_name, ally_name]
                    if index_within_volume % 3 != 0
                    else [protagonist_name, volume_antag_participant],
                    "purpose": {
                        "story": chapter_hook_description,
                        "emotion": "Make the reader unable to stop — they MUST read the next chapter."
                        if is_en
                        else "让读者必须继续追下一章",
                    },
                    "entry_state": {
                        protagonist_name: {
                            "arc_state": _pick_by_seed(
                                ["准备收束", "短暂喘息", "整理线索", "面临抉择", "孤注一掷"],
                                project.slug,
                                chapter_number,
                                "hook_entry",
                            ),
                            "emotion": _pick_by_seed(
                                [
                                    "短暂控制局势",
                                    "紧绷到极点",
                                    "表面平静内心翻涌",
                                    "疲惫但不甘",
                                    "冷静中带着决绝",
                                ],
                                project.slug,
                                chapter_number,
                                "hook_entry_emo",
                            ),
                        },
                    },
                    "exit_state": {
                        protagonist_name: {
                            "arc_state": _pick_by_seed(
                                [
                                    "被迫进入更难局面",
                                    "发现更大的真相",
                                    "失去重要倚仗",
                                    "打开新的可能",
                                    "站在命运岔路口",
                                ],
                                project.slug,
                                chapter_number,
                                "hook_exit",
                            ),
                            "emotion": _pick_by_seed(
                                [
                                    "强压下前进",
                                    "震惊无措",
                                    "悲愤交加",
                                    "危机感拉满",
                                    "痛苦但更清醒",
                                ],
                                project.slug,
                                chapter_number,
                                "hook_exit_emo",
                            ),
                        },
                    },
                    "target_word_count": scene_target_words,
                }
            )
            # Compute arc-level info for this chapter
            arc_index, arc_phase = _compute_chapter_arc_info(chapter_number, normalized_volume_plan)
            _high_tension = phase in {"pressure", "reversal", "climax", "confrontation"}
            chapter_function = (
                "payoff"
                if chapters_from_end <= 2
                else "reveal"
                if phase in {"reversal", "climax"}
                else "action"
                if _high_tension
                else "transition"
            )
            chapter_event_role = _fallback_event_cycle_role(
                chapter_function=chapter_function,
                phase=phase,
                index_within_volume=index_within_volume,
                chapters_from_end=chapters_from_end,
            )
            information_gap_mode = _fallback_information_gap_mode(
                project_slug=project.slug,
                chapter_number=chapter_number,
                role=chapter_event_role,
            )
            event_cycle_contract = _fallback_event_cycle_contract(
                is_en=is_en,
                volume_number=volume_number,
                index_within_volume=index_within_volume,
                role=chapter_event_role,
                protagonist_name=protagonist_name,
                force_name=volume_force_name,
                volume_goal=volume_goal,
                chapter_unique_beat=chapter_unique_beat,
                chapter_hook_description=chapter_hook_description,
                chapter_function=chapter_function,
                information_gap_mode=information_gap_mode,
            )
            causal_contract = (
                {
                    "chapter_function": chapter_function,
                    "pressure": f"{volume_force_name} uses {chapter_unique_beat} to force an immediate response from {protagonist_name}.",
                    "protagonist_desire": f"{protagonist_name} wants to advance {volume_goal} through {chapter_unique_beat}.",
                    "protagonist_choice": f"{protagonist_name} chooses a concrete response before the pressure closes the current option.",
                    "visible_action_or_reaction": f"{protagonist_name} acts around {chapter_unique_beat} and changes the local balance.",
                    "resistance": f"{volume_force_name} blocks the move through direct opposition, limited time, or a hidden rule.",
                    "cost_or_tradeoff": f"The move creates exposure risk, resource loss, or a relationship cost for {protagonist_name}.",
                    "gain_or_reveal": f"{protagonist_name} gains usable information, leverage, or a painful reveal from this chapter.",
                    "state_change": f"{protagonist_name} moves from pressured uncertainty to a changed state with a usable result or a clear loss.",
                    "next_reader_desire": chapter_hook_description,
                }
                if is_en
                else {
                    "chapter_function": chapter_function,
                    "pressure": f"{volume_force_name}借「{chapter_unique_beat}」逼{protagonist_name}立刻应对。",
                    "protagonist_desire": f"{protagonist_name}想通过「{chapter_unique_beat}」推进「{volume_goal}」。",
                    "protagonist_choice": f"{protagonist_name}选择在当前机会关闭前做出具体应对。",
                    "visible_action_or_reaction": f"{protagonist_name}围绕「{chapter_unique_beat}」行动，并改变局部力量平衡。",
                    "resistance": f"{volume_force_name}通过正面阻拦、时限或隐藏规则压制这次行动。",
                    "cost_or_tradeoff": f"这次行动让{protagonist_name}承担暴露风险、资源损失或关系代价。",
                    "gain_or_reveal": f"{protagonist_name}获得可用信息、局势杠杆，或痛苦但有效的揭露。",
                    "state_change": f"{protagonist_name}从承压不确定，变成握有可用结果或明确损失。",
                    "next_reader_desire": chapter_hook_description,
                }
            )

            # ── Phase-3: assign Swain scene/sequel pattern ──
            for si, sc_dict in enumerate(scenes):
                if _high_tension:
                    sc_dict["swain_pattern"] = "action"
                elif si % 2 == 0:
                    sc_dict["swain_pattern"] = "action"
                else:
                    sc_dict["swain_pattern"] = "sequel"

            # Compute the chapter title before building the chapter dict so
            # we can: (a) feed `fallback_used_titles` into the extractor
            # to skip already-taken phrases, and (b) gracefully skip the
            # chapter if extraction has nothing left to pick from rather
            # than crashing the whole fallback batch. The LLM path should
            # be doing the real work; the fallback is degraded mode.
            try:
                fallback_title = _chapter_fallback_subtitle(
                    chapter_number,
                    phase,
                    index_within_volume,
                    volume_number,
                    language=project.language,
                    is_opening=(chapter_number == 1),
                    project_slug=project.slug,
                    unique_beat=chapter_unique_beat,
                    chapter_goal=chapter_goal,
                    main_conflict=chapter_conflict,
                    used_titles=fallback_used_titles,
                )
            except PlannerFallbackError:
                # Templated content exhausted; synthesize a
                # batch-unique placeholder anchored to the chapter number
                # so downstream identity stays valid. This is the LAST
                # resort — the LLM path's repair loop should catch this
                # case long before persistence.
                fallback_title = _fallback_title_from_chapter_number(
                    chapter_number, language=project.language
                )
                # Ensure uniqueness even against this placeholder format.
                while fallback_title in fallback_used_titles:
                    fallback_title = _fallback_title_from_chapter_number(
                        chapter_number, language=project.language, salt=len(fallback_used_titles)
                    )
            fallback_used_titles.add(fallback_title)
            chapter_payload = {
                "chapter_number": chapter_number,
                # NOTE: title intentionally left as a SHORT subtitle without
                # any "第N章" prefix. The chapter header renderer
                # (``drafts._format_chapter_heading``) is responsible for
                # re-attaching the canonical "第N章：" prefix exactly once,
                # which prevents the "# 第1章 第1章：…" double-prefix bug.
                "title": fallback_title,
                "goal": chapter_goal,
                "opening_situation": (
                    writing_profile.serialization.opening_mandate
                    if chapter_number == 1
                    else (
                        f"Chapter {chapter_number} opens after {chapter_unique_beat}; {protagonist_name} must respond before the pressure escalates."
                        if is_en
                        else f"第{chapter_number}章开场承接「{chapter_unique_beat}」，{protagonist_name}必须在压力升级前做出反应。"
                    )
                ),
                "main_conflict": chapter_conflict,
                "hook_type": _hook_type(
                    index_within_volume, total_in_volume, language=project.language
                ),
                "hook_description": chapter_hook_description,
                "causal_contract": causal_contract,
                "event_cycle_contract": event_cycle_contract,
                "chapter_event_role": chapter_event_role,
                "information_gap_mode": information_gap_mode,
                "volume_number": volume_number,
                "arc_index": arc_index,
                "arc_phase": arc_phase,
                "target_word_count": chapter_target_words,
                "scenes": scenes,
                # Position tags for the quality-levers pipeline.
                # Computed once at outline time so the writer + critic can
                # pick up the same labels without re-deriving them.
                "positions": list(
                    _outline_chapter_positions(
                        chapter_number=chapter_number,
                        volume_number=volume_number,
                        index_within_volume=index_within_volume,
                        total_in_volume=total_in_volume,
                    )
                ),
            }
            if worldview_outline_contract:
                landing_seed = _non_empty_string(
                    worldview_outline_contract.get("world_rule_landing"), ""
                )
                asset_cost_note = _non_empty_string(
                    worldview_outline_contract.get("world_asset_cost_note"), ""
                )
                world_rule_landing = (
                    f"{landing_seed} This chapter lands it through {chapter_unique_beat}."
                    if is_en and landing_seed
                    else f"{landing_seed} 本章通过「{chapter_unique_beat}」落地。"
                    if landing_seed
                    else chapter_unique_beat
                )
                if asset_cost_note:
                    world_rule_landing = (
                        f"{world_rule_landing} Visible asset cost/exposure: {asset_cost_note}."
                        if is_en
                        else f"{world_rule_landing} 资产代价/暴露：{asset_cost_note}。"
                    )
                chapter_payload.update(
                    {
                        "world_rule_refs": list(
                            _string_list(worldview_outline_contract.get("world_rule_refs"))
                        ),
                        "world_rule_landing": world_rule_landing,
                        "world_state_deltas": _mapping_list(
                            worldview_outline_contract.get("world_state_deltas")
                        ),
                        "world_asset_refs": list(
                            _string_list(worldview_outline_contract.get("world_asset_refs"))
                        ),
                        "authority_claim_refs": list(
                            _string_list(worldview_outline_contract.get("authority_claim_refs"))
                        ),
                        "world_scene_template_ref": _non_empty_string(
                            worldview_outline_contract.get("world_scene_template_ref"),
                            "",
                        )
                        or None,
                        "reveal_weight": int(worldview_outline_contract.get("reveal_weight") or 0),
                        "anti_copy_boundary_notes": list(
                            _string_list(
                                worldview_outline_contract.get("anti_copy_boundary_notes")
                            )
                        ),
                        "location_refs": list(
                            _string_list(worldview_outline_contract.get("location_refs"))
                        ),
                        "faction_refs": list(
                            _string_list(worldview_outline_contract.get("faction_refs"))
                        ),
                        "key_reveals": [],
                    }
                )
            chapters.append(chapter_payload)
            prev_phase = phase
            chapter_number += 1
    return {"batch_name": "auto-generated-full-outline", "chapters": chapters}


def _fallback_worldview_outline_contract(project: ProjectModel) -> dict[str, Any]:
    metadata = _mapping(getattr(project, "metadata_json", None))
    kernel = _mapping(metadata.get("story_design_kernel") or metadata.get("story_design"))
    worldview = _mapping(kernel.get("worldview_kernel"))
    if not worldview:
        return {}

    invariants = _mapping_list(worldview.get("invariants"))
    systems = _mapping_list(worldview.get("systems"))
    locations = _mapping_list(worldview.get("locations"))
    factions = _mapping_list(worldview.get("factions"))
    integration = _mapping(worldview.get("integration_contract"))
    state_variables = _mapping_list(worldview.get("state_variables"))
    asset_ledger = _mapping_list(worldview.get("asset_ledger"))
    authority_claims = _mapping_list(worldview.get("authority_claims"))
    scene_templates = _mapping_list(worldview.get("scene_templates"))

    rule_refs: list[str] = []
    landing_candidates: list[str] = []
    for invariant in invariants:
        key = _non_empty_string(invariant.get("key"), "")
        rule = _non_empty_string(invariant.get("rule"), "")
        if key:
            rule_refs.append(key)
        elif rule:
            rule_refs.append(rule)
        if rule:
            landing_candidates.append(rule)
    for system in systems:
        name = _non_empty_string(system.get("name"), "")
        if name:
            rule_refs.append(name)
        logic = _non_empty_string(system.get("operating_logic"), "")
        if logic:
            landing_candidates.append(logic)

    location_refs = [
        _non_empty_string(item.get("name"), "")
        for item in locations
        if _non_empty_string(item.get("name"), "")
    ]
    faction_refs = [
        _non_empty_string(item.get("name"), "")
        for item in factions
        if _non_empty_string(item.get("name"), "")
    ]
    landing = _non_empty_string(integration.get("chapter_rule"), "") or (
        landing_candidates[0] if landing_candidates else ""
    )
    first_state = state_variables[0] if state_variables else {}
    state_key = _non_empty_string(first_state.get("key"), "")
    first_asset = asset_ledger[0] if asset_ledger else {}
    asset_key = _non_empty_string(first_asset.get("key"), "")
    asset_cost_note = " ".join(
        item
        for item in (
            _non_empty_string(first_asset.get("cost"), ""),
            _non_empty_string(first_asset.get("exposure_risk"), ""),
        )
        if item
    )
    first_claim = authority_claims[0] if authority_claims else {}
    claim_ref = _non_empty_string(
        first_claim.get("target") or first_claim.get("claimant"),
        "",
    )
    first_template = scene_templates[0] if scene_templates else {}
    return {
        "world_rule_refs": _dedupe_text_values([item for item in rule_refs if item])[:2],
        "world_rule_landing": landing,
        "world_state_deltas": [
            {
                "key": state_key,
                "delta": "+1",
                "evidence": landing or "chapter executes the worldview rule",
            }
        ]
        if state_key
        else [],
        "world_asset_refs": [asset_key] if asset_key else [],
        "world_asset_cost_note": asset_cost_note,
        "authority_claim_refs": [claim_ref] if claim_ref else [],
        "world_scene_template_ref": _non_empty_string(first_template.get("key"), ""),
        "reveal_weight": 1 if state_key else 0,
        "anti_copy_boundary_notes": _string_list(worldview.get("anti_copy_boundaries")),
        "location_refs": _dedupe_text_values(location_refs)[:1],
        "faction_refs": _dedupe_text_values(faction_refs)[:1],
    }


def _dedupe_text_values(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = _non_empty_string(value, "")
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return result


def _outline_chapter_positions(
    *,
    chapter_number: int,
    volume_number: int,
    index_within_volume: int,
    total_in_volume: int,
) -> tuple[str, ...]:
    """Compute ``chapter_positions`` tags for one outline entry.

    Delegates to :func:`quality_levers.detect_chapter_positions` so the
    rules stay defined in a single place. Wrapped in try/except so any
    config breakage falls back to an empty list rather than killing
    the entire outline batch.
    """

    try:
        from bestseller.services.quality_levers import detect_chapter_positions

        return detect_chapter_positions(
            chapter_number=chapter_number,
            volume_number=volume_number,
            is_first_chapter_of_volume=(index_within_volume == 1),
            is_last_chapter_of_volume=(index_within_volume == total_in_volume),
        )
    except Exception:
        return ()


def _append_category_context(
    user_prompt: str,
    project: ProjectModel,
    *,
    category_key: str | None = None,
    is_en: bool = False,
) -> str:
    """Append category reader promise, evolution summary, and anti-patterns to a prompt."""
    from bestseller.services.novel_categories import (
        render_category_anti_patterns,
        render_category_challenge_evolution_summary,
        render_category_reader_promise,
    )

    cat = get_novel_category(category_key) if category_key else None
    if cat is None:
        cat = resolve_novel_category(project.genre, project.sub_genre)
    # Skip if it's just the default with no meaningful content
    if not cat or (not cat.quality_traps and not cat.reader_promise_zh):
        return user_prompt

    promise = render_category_reader_promise(cat, is_en=is_en)
    evolution = render_category_challenge_evolution_summary(cat, is_en=is_en)
    anti_patterns = render_category_anti_patterns(cat, is_en=is_en)

    blocks = [b for b in [promise, evolution, anti_patterns] if b]
    if blocks:
        user_prompt += "\n\n" + "\n\n".join(blocks)
    return user_prompt


def _stash_distilled_design_reference_blocks(
    project: ProjectModel,
    *,
    category_key: str | None,
    settings: AppSettings,
) -> None:
    """Pre-render distilled mature-fiction reference blocks into project metadata."""

    try:
        if not getattr(settings.pipeline, "enable_distilled_design_reference", True):
            return
        from bestseller.services.distilled_design_reference import (
            render_all_distilled_design_reference_blocks,
        )

        blocks = render_all_distilled_design_reference_blocks(
            category_key=category_key,
            genre=project.genre,
            sub_genre=project.sub_genre,
            language=_planner_language(project),
        )
        if not blocks:
            return
        metadata = dict(project.metadata_json) if isinstance(project.metadata_json, dict) else {}
        metadata["distilled_design_reference_blocks"] = blocks
        # Keep a single architecture block for older prompt helpers/tests that
        # inspect one metadata value instead of the phase-indexed map.
        if blocks.get("architecture"):
            metadata["distilled_design_reference_block"] = blocks["architecture"]
        project.metadata_json = metadata
    except Exception:
        logger.exception("Planner distilled design reference block failed — continuing without it")


def _distilled_strategy_project_context(project: ProjectModel) -> dict[str, object]:
    metadata = project.metadata_json if isinstance(project.metadata_json, dict) else {}
    story_facets = metadata.get("story_facets") if isinstance(metadata, dict) else {}
    if not isinstance(story_facets, dict):
        story_facets = {}
    commercial_brief = metadata.get("commercial_brief") if isinstance(metadata, dict) else {}
    if not isinstance(commercial_brief, dict):
        commercial_brief = {}
    reader_contract = (
        project.reader_contract_json if isinstance(project.reader_contract_json, dict) else {}
    )
    return {
        "premise": metadata.get("premise") or metadata.get("raw_premise"),
        "unique_hook": (
            story_facets.get("unique_hook")
            or commercial_brief.get("unique_hook")
            or metadata.get("unique_hook")
        ),
        "reader_promise": (
            reader_contract.get("core_fantasy")
            or reader_contract.get("reader_promise")
            or commercial_brief.get("reader_promise")
            or metadata.get("reader_promise")
        ),
        "dramatic_question": project.dramatic_question,
        "theme_statement": project.theme_statement,
        "audience": project.audience,
        "title": project.title,
    }


def _concept_lab_contract_block(project: ProjectModel, *, language: str) -> str:
    metadata = project.metadata_json if isinstance(project.metadata_json, dict) else {}
    block = render_concept_lab_prompt_block(metadata, language=language)
    return f"{block}\n" if block else ""


def _story_enhancer_contract_line(project: ProjectModel, language: str) -> str:
    """Book-level story-enhancer hard contract (empty unless the user checked
    capabilities at creation). Injected into every volume/chapter outline prompt
    alongside the concept-lab contract."""

    metadata = project.metadata_json if isinstance(project.metadata_json, dict) else {}
    block = render_story_enhancer_contract_block(
        resolve_story_enhancers(metadata), language=language
    )
    return f"\n{block}\n" if block else ""


def _concept_methodology_block(project: ProjectModel, *, language: str) -> str:
    """Agent ②/③: soft 脑洞/爽点 methodology framework from Agent ①.

    Read from the persisted writing_profile so the planner can fuse the
    methodology into book_spec / world / cast and grow genre-fitting settings.
    Never a hard contract — the model transforms it into original mechanisms.
    """

    metadata = project.metadata_json if isinstance(project.metadata_json, dict) else {}
    profile = metadata.get("writing_profile")
    methodology_payload = (
        profile.get("concept_methodology") if isinstance(profile, dict) else None
    )
    if not isinstance(methodology_payload, dict) or not methodology_payload:
        return ""
    try:
        from bestseller.services.concept_methodology_agent import (
            ConceptMethodology,
            render_concept_methodology_block,
        )

        methodology = ConceptMethodology.model_validate(methodology_payload)
    except Exception:  # pragma: no cover - defensive validation guard
        return ""
    block = render_concept_methodology_block(methodology, language=language)
    return f"{block}\n" if block else ""


def _compact_concept_lab_contract_block(project: ProjectModel, *, language: str) -> str:
    metadata = project.metadata_json if isinstance(project.metadata_json, dict) else {}
    bundle = concept_lab_from_source(metadata)
    if bundle is None:
        return ""
    is_en = is_english_language(language)
    loop = bundle.story_loop
    payload = {
        "reader_promise": bundle.reader_promise,
        "one_liner": bundle.one_liner,
        "story_loop": {
            "opening_question": loop.opening_question,
            "recurring_pressure": loop.recurring_pressure,
            "payoff_window_chapters": loop.payoff_window_chapters,
            "per_chapter_contract": list(loop.per_chapter_contract[:6]),
        },
        "methodology_targets": list(bundle.methodology_targets[:6]),
        "guardrails": list(bundle.guardrails[:6]),
    }
    label = "[Selected Concept Lab contract]" if is_en else "【已选脑洞组合合同】"
    directive = (
        "Keep this reader-promise lineage visible in every chapter outline."
        if is_en
        else "短书章纲必须保留这条读者承诺链路，每章循环不得退化成普通题材模板。"
    )
    return f"\n\n{label}\n{directive}\n{_json_dumps(payload)}\n"


def _stash_distilled_strategy_card(
    project: ProjectModel,
    *,
    category_key: str | None,
    settings: AppSettings,
) -> None:
    """Compile and stash project-specific distilled strategy metadata."""

    try:
        if not getattr(settings.pipeline, "enable_distilled_design_reference", True):
            return
        from bestseller.services.distilled_strategy_compiler import (
            compile_distilled_strategy_card,
            distilled_strategy_card_to_dict,
            render_all_distilled_strategy_blocks,
        )
        from bestseller.services.character_intelligence.strategy import (
            build_character_strategy_from_distillation,
        )

        card = compile_distilled_strategy_card(
            category_key=category_key,
            genre=project.genre,
            sub_genre=project.sub_genre,
            project_context=_distilled_strategy_project_context(project),
        )
        if card is None:
            return
        language = _planner_language(project)
        metadata = dict(project.metadata_json) if isinstance(project.metadata_json, dict) else {}
        card_payload = distilled_strategy_card_to_dict(card)
        metadata["distilled_strategy_card"] = card_payload
        metadata["character_strategy"] = build_character_strategy_from_distillation(
            distilled_strategy_card=card_payload,
        )
        blocks = render_all_distilled_strategy_blocks(card, language=language)
        if blocks:
            metadata["distilled_strategy_blocks"] = blocks
            if blocks.get("architecture"):
                metadata["distilled_strategy_block"] = blocks["architecture"]
        project.metadata_json = metadata
    except Exception:
        logger.exception("Planner distilled strategy card failed — continuing without it")


def _book_spec_prompts(
    project: ProjectModel, premise: str, fallback: dict[str, Any]
) -> tuple[str, str]:
    from bestseller.services.genre_review_profiles import resolve_genre_review_profile

    language = _planner_language(project)
    is_en = is_english_language(language)
    _lang_key = "en" if is_en else "zh"
    writing_profile = _planner_writing_profile(project)
    prompt_pack = _planner_prompt_pack(project)
    _genre_profile = resolve_genre_review_profile(project.genre, project.sub_genre)
    _genre_system = getattr(_genre_profile.planner_prompts, f"book_spec_system_{_lang_key}", "")
    system_prompt = (
        "You are an English-language commercial fiction planner. "
        "Output valid JSON only. Build a marketable serial-fiction story engine, not literary commentary."
        if is_en
        else (
            "你是长篇中文小说的故事策划师。"
            "输出必须是合法 JSON，不要解释。"
            "你要产出的是适合中文网文平台连载的商业小说骨架，而不是文学评论。"
        )
    )
    if _genre_system:
        system_prompt += f"\n{_genre_system}"
    _pp_block = (
        f"Prompt Pack：\n{render_prompt_pack_prompt_block(prompt_pack)}\n" if prompt_pack else ""
    )
    _pp_book_spec = _planner_fragment_or_ref(prompt_pack, project, "planner_book_spec")
    _methodology_planner_block = render_methodology_phase_block(
        prompt_pack,
        phase="planner",
    )
    _methodology_line = f"\n{_methodology_planner_block}\n" if _methodology_planner_block else ""
    _story_package_block = _story_package_prompt_block(project, language=language)
    _distilled_architecture_block = _distilled_design_reference_block(project, "architecture")
    from bestseller.services.hook_propagation import (
        hook_spec_from_metadata,
        render_hook_spec_prompt_block,
    )

    _hook_contract_block = render_hook_spec_prompt_block(
        hook_spec_from_metadata(project.metadata_json if isinstance(project.metadata_json, dict) else {}),
        language=language,
    )
    _hook_contract_line = f"{_hook_contract_block}\n" if _hook_contract_block else ""
    _concept_lab_contract_line = _concept_lab_contract_block(project, language=language)
    _story_enhancer_line = _story_enhancer_contract_line(project, language)
    _concept_methodology_line = _concept_methodology_block(project, language=language)
    from bestseller.services.story_spine import render_story_spine_block as _rssb
    _spine_meta = getattr(project, "metadata_json", None) or {}
    _story_spine_block = (
        _rssb(_spine_meta.get("story_spine"), is_en=is_en)
        if isinstance(_spine_meta, dict)
        else ""
    )
    if is_en:
        user_prompt = (
            f"Project title: {project.title}\n"
            f"Genre: {project.genre}\n"
            f"Target words: {project.target_word_count}\n"
            f"Target chapters: {project.target_chapters}\n"
            f"Audience: {project.audience or 'web-serial'}\n"
            f"Premise: {premise}\n"
            f"{_story_spine_block}"
            "Write all planning artifacts in English.\n"
            f"Writing profile:\n{render_writing_profile_prompt_block(writing_profile, language=language)}\n"
            f"{_pp_block}"
            f"Serial fiction guardrails:\n{render_serial_fiction_guardrails(writing_profile, language=language)}\n"
            f"{_story_package_block}\n"
            f"{_distilled_architecture_block}"
            f"{_hook_contract_line}"
            f"{_concept_lab_contract_line}"
            f"{_story_enhancer_line}"
            f"{_concept_methodology_line}"
            f"{_pp_book_spec}"
            f"{_methodology_line}"
            "Generate a BookSpec JSON with title, logline, genre, target_audience, tone, themes, "
            "theme_statement, dramatic_question, expected_character_count, naming_pool, protagonist, stakes, and series_engine. "
            "theme_statement must be a single falsifiable sentence. dramatic_question must be a yes/no question answered only in the finale. "
            "naming_pool must contain at least 2x expected_character_count style-consistent names. "
            "Inside series_engine, explicitly define the core serial engine, reader promise, first-three-chapter hook, chapter-ending hook strategy, and the rhythm of short and long payoffs."
        )
    else:
        user_prompt = (
            f"项目标题：{project.title}\n"
            f"类型：{project.genre}\n"
            f"目标字数：{project.target_word_count}\n"
            f"目标章节：{project.target_chapters}\n"
            f"受众：{project.audience or 'web-serial'}\n"
            f"Premise：{premise}\n"
            f"{_story_spine_block}"
            f"写作画像：\n{render_writing_profile_prompt_block(writing_profile, language=language)}\n"
            f"{_pp_block}"
            f"商业网文硬约束：\n{render_serial_fiction_guardrails(writing_profile, language=language)}\n"
            f"{_story_package_block}\n"
            f"{_distilled_architecture_block}"
            f"{_hook_contract_line}"
            f"{_concept_lab_contract_line}"
            f"{_story_enhancer_line}"
            f"{_concept_methodology_line}"
            f"{_pp_book_spec}"
            f"{_methodology_line}"
            "请生成一个 BookSpec JSON，包含 title、logline、genre、target_audience、tone、themes、"
            "theme_statement、dramatic_question、expected_character_count、naming_pool、"
            "protagonist、stakes、series_engine、unique_hook、benchmark_works。"
            "theme_statement 必须是一句可被全书证明/反证的核心命题；dramatic_question 必须是结尾才能回答的 yes/no 问题；"
            "naming_pool 至少包含 expected_character_count 两倍数量、风格一致的候选姓名。"
            "其中 series_engine 必须清楚写出：核心连载引擎、读者承诺、前三章抓手、章节尾钩策略、"
            "短回报与长回报的节奏安排。"
            "unique_hook 必须用一句话写出本书区别于同题材作品的唯一卖点/反套路设定（具体到机制或选择，不能是泛词）。"
            "benchmark_works 必须列出 3-5 部与本书题材和卖点真实可比的已发表作品名（数组），作为结构与能力对标。"
        )
    _genre_instruction = getattr(
        _genre_profile.planner_prompts, f"book_spec_instruction_{_lang_key}", ""
    )
    if _genre_instruction:
        user_prompt += f"\n\n{'[Genre planning requirements]' if is_en else '【品类规划要求】'}\n{_genre_instruction}"
    user_prompt = _append_category_context(user_prompt, project, is_en=is_en)

    # Inject four-layer narrative-lines contract (明线/暗线/隐藏线/核心轴) so
    # the BookSpec defines the macro-narrative architecture up front. Without
    # this, the book collapses to a single overt arc that rotates across
    # volumes — the canonical 道种破虚 failure mode where every volume reads
    # like the same "stage boss" template.
    try:
        from bestseller.services.narrative_lines import (
            render_narrative_lines_constraints_block,
        )

        _hierarchy = compute_linear_hierarchy(max(project.target_chapters, 1))
        _volume_count = int(_hierarchy.get("volume_count") or 1)
        user_prompt += "\n\n" + render_narrative_lines_constraints_block(
            total_chapters=max(project.target_chapters, 1),
            volume_count=_volume_count,
            language=language,
        )
        if is_en:
            user_prompt += (
                "\nEMIT `narrative_lines` inside the BookSpec JSON with this shape:\n"
                "  narrative_lines: {\n"
                "    overt_line: [{name, volumes: [int,...], antagonist_ref}],\n"
                "    undercurrent_line: [{name, start_volume, end_volume, antagonist_ref}],\n"
                "    hidden_thread: {statement, seed_volumes: [int,...], payoff_volumes: [int,...]},\n"
                "    core_axis: {statement, phrasing_tokens: [str,...]}\n"
                "  }\n"
                "Every volume plan and chapter outline downstream will reference these layers."
            )
        else:
            user_prompt += (
                "\n请在 BookSpec JSON 中输出 `narrative_lines` 字段，结构如下：\n"
                "  narrative_lines: {\n"
                "    overt_line: [{name, volumes: [int,...], antagonist_ref}],\n"
                "    undercurrent_line: [{name, start_volume, end_volume, antagonist_ref}],\n"
                "    hidden_thread: {statement, seed_volumes: [int,...], payoff_volumes: [int,...]},\n"
                "    core_axis: {statement, phrasing_tokens: [str,...]}\n"
                "  }\n"
                "后续所有卷规划和章纲都会引用这四条线。"
            )
    except Exception:
        logger.debug(
            "Narrative-lines constraints block injection failed (non-fatal)",
            exc_info=True,
        )

    return system_prompt, user_prompt


_WORLD_SPEC_COUNTER_EXAMPLES_ZH: str = (
    "【严禁输出以下错误结构】\n"
    '  "power_structure": {"overview": "...", "factions": [...]}   ← 错误：必须是字符串\n'
    '  "forbidden_zones": [{"name": "...", "rules": "..."}]        ← 错误：必须是字符串\n'
    '  "history_key_events": [{"name": "...", "relevance": "..."}] ← 错误：字段名必须是 event 不是 name\n'
    '  "rules": [{"rule_name": "...", "description": {"summary": "..."}}] ← 错误：description 必须是字符串\n'
    "\n【正确写法示例】\n"
    '  "power_structure": "青萝镇以王李两家世代联盟为权力核心，外加祖庭监督。王家负责血契，李家负责器灵契约，祖庭仲裁。"\n'
    '  "forbidden_zones": "封印禁地（镇东古井之下）、器宫核心（百年未启）、魂池（仅三年一开）。"\n'
    '  "history_key_events": [{"event": "器灵初现", "relevance": "奠定血契传统"}, {"event": "妖族之战", "relevance": "建立祖庭秩序"}]\n'
    '  "rules": [{"rule_name": "血契", "description": "人与器灵须以血立约，违约代价为血脉永封"}]\n'
)

_WORLD_SPEC_COUNTER_EXAMPLES_EN: str = (
    "[Forbidden output shapes]\n"
    '  "power_structure": {"overview": "...", "factions": [...]}   WRONG — must be a string\n'
    '  "forbidden_zones": [{"name": "...", "rules": "..."}]        WRONG — must be a string\n'
    '  "history_key_events": [{"name": "...", "relevance": "..."}] WRONG — key must be \'event\', not \'name\'\n'
    '  "rules": [{"rule_name": "...", "description": {"summary": "..."}}] WRONG — description must be string\n'
    "\n[Correct shapes]\n"
    '  "power_structure": "The Crown holds legitimacy, the Guild holds access, the Spire holds risk — each balanced against the others."\n'
    '  "forbidden_zones": "The Deep Archive (sealed since the Second Fracture), the Old Well below East Gate, the Blooming Grove after solstice."\n'
    '  "history_key_events": [{"event": "The Second Fracture", "relevance": "Triggered the current oath bindings"}]\n'
    '  "rules": [{"rule_name": "Blood Covenant", "description": "Every bonded pair shares wounds across the bond — breaking the bond kills both"}]\n'
)

_CAST_SPEC_COUNTER_EXAMPLES_ZH: str = (
    "【严禁输出以下错误结构】\n"
    '  "protagonist": {"王青峰": {"role": "protagonist", "age": 20}} ← 错误：不要以角色名做外层 key 包住角色对象\n'
    '  "supporting_cast": {"师父": {...}, "青儿": {...}}              ← 错误：必须是数组，不是 dict\n'
    '  "conflict_map": {"王青峰 vs 李墨白": {"conflict_type": "..."}} ← 错误：必须是数组\n'
    "\n【正确写法示例】\n"
    '  "protagonist": {"name": "王青峰", "role": "protagonist", "age": 20, ...}\n'
    '  "supporting_cast": [{"name": "师父", "role": "mentor", ...}, {"name": "青儿", "role": "sister", ...}]\n'
    '  "conflict_map": [{"character_a": "王青峰", "character_b": "李墨白", "conflict_type": "血脉之争", "trigger_condition": "初遇之际"}]\n'
)

_CAST_SPEC_COUNTER_EXAMPLES_EN: str = (
    "[Forbidden output shapes]\n"
    '  "protagonist": {"Elena": {"role": "protagonist", ...}}  WRONG — do not wrap the object with the character name as outer key\n'
    '  "supporting_cast": {"Mentor": {...}, "Rival": {...}}    WRONG — must be an array, not a dict\n'
    '  "conflict_map": {"Elena vs Kell": {"conflict_type": "..."}} WRONG — must be an array\n'
    "\n[Correct shapes]\n"
    '  "protagonist": {"name": "Elena", "role": "protagonist", ...}\n'
    '  "supporting_cast": [{"name": "Marin", "role": "mentor", ...}, {"name": "Kell", "role": "rival", ...}]\n'
    '  "conflict_map": [{"character_a": "Elena", "character_b": "Kell", "conflict_type": "bloodline rivalry", "trigger_condition": "first meeting"}]\n'
)


def _world_spec_prompts(
    project: ProjectModel, premise: str, book_spec: dict[str, Any]
) -> tuple[str, str]:
    from bestseller.services.genre_review_profiles import resolve_genre_review_profile

    language = _planner_language(project)
    is_en = is_english_language(language)
    _lang_key = "en" if is_en else "zh"
    writing_profile = _planner_writing_profile(project)
    prompt_pack = _planner_prompt_pack(project)
    _genre_profile = resolve_genre_review_profile(project.genre, project.sub_genre)
    _genre_system = getattr(_genre_profile.planner_prompts, f"world_spec_system_{_lang_key}", "")
    system_prompt = (
        "You are a world-building designer for long-form commercial fiction. Output valid JSON only."
        if is_en
        else "你是长篇中文小说世界观设计师。输出必须是合法 JSON，不要解释。"
    )
    if _genre_system:
        system_prompt += f"\n{_genre_system}"
    _pp_block = (
        f"Prompt Pack：\n{render_prompt_pack_prompt_block(prompt_pack)}\n" if prompt_pack else ""
    )
    _pp_world_spec = _planner_fragment_or_ref(prompt_pack, project, "planner_world_spec")
    # Batch 2: inject §slug material references when enable_reference_style_generation is on
    _mat_ref = (project.metadata_json or {}).get("material_reference_block", "")
    _mat_ref_block = f"\n{_mat_ref}\n" if _mat_ref else ""
    _story_package_block = _story_package_prompt_block(project, language=language)
    _distilled_world_block = _distilled_design_reference_block(project, "world")
    from bestseller.services.hook_propagation import (
        hook_spec_from_metadata,
        render_hook_spec_prompt_block,
    )

    _hook_contract_block = render_hook_spec_prompt_block(
        hook_spec_from_metadata(project.metadata_json if isinstance(project.metadata_json, dict) else {}),
        language=language,
    )
    _hook_contract_line = f"{_hook_contract_block}\n" if _hook_contract_block else ""
    _concept_lab_contract_line = _concept_lab_contract_block(project, language=language)
    _story_enhancer_line = _story_enhancer_contract_line(project, language)
    _concept_methodology_line = _concept_methodology_block(project, language=language)
    user_prompt = (
        (
            f"Project title: {project.title}\n"
            f"Genre: {project.genre}\n"
            f"Premise: {premise}\n"
            "Write all planning artifacts in English.\n"
            f"Writing profile:\n{render_writing_profile_prompt_block(writing_profile, language=language)}\n"
            f"{_pp_block}"
            f"BookSpec summary:\n{summarize_book_spec(book_spec, language='en')}\n"
            f"{_story_package_block}\n"
            f"{_distilled_world_block}"
            f"{_hook_contract_line}"
            f"{_concept_lab_contract_line}"
            f"{_story_enhancer_line}"
            f"{_concept_methodology_line}"
            f"{_pp_world_spec}"
            f"{_mat_ref_block}"
            "Generate a WorldSpec JSON with world_name, world_premise, rules, power_system, locations, factions, power_structure, history_key_events, and forbidden_zones. "
            "World rules must create conflict, cost, upgrade space, and conspiracy leverage rather than empty lore.\n"
            "power_system MUST be a structured object, never a prose paragraph: "
            '{"name": str, "tiers": [ordered tier names, low to high — for cultivation/progression genres list every major tier], '
            '"tier_progression": [{"tier": name, "breakthrough_cost": what advancing INTO this tier costs (resource/lifespan/risk — concrete, not "hard work"), '
            '"bottleneck": the gate/wall that stalls most people at this tier}], '
            '"acquisition_method": str, "hard_limits": str, "protagonist_starting_tier": str}. '
            "For cultivation/progression genres tier_progression MUST cover every tier in tiers — each tier needs an explicit cost AND bottleneck, "
            "so the ladder is mechanically real (a 凡人修仙传-class ladder gates every realm with cost + wall), not a bare name list.\n"
            "Downstream progression tracking and tier-consistency gates consume these fields; free text starves them.\n\n"
            f"{_WORLD_SPEC_COUNTER_EXAMPLES_EN}"
        )
        if is_en
        else (
            f"项目标题：{project.title}\n"
            f"类型：{project.genre}\n"
            f"Premise：{premise}\n"
            f"写作画像：\n{render_writing_profile_prompt_block(writing_profile, language=language)}\n"
            f"{_pp_block}"
            f"BookSpec 摘要：\n{summarize_book_spec(book_spec, language='zh')}\n"
            f"{_story_package_block}\n"
            f"{_distilled_world_block}"
            f"{_hook_contract_line}"
            f"{_concept_lab_contract_line}"
            f"{_story_enhancer_line}"
            f"{_concept_methodology_line}"
            f"{_pp_world_spec}"
            f"{_mat_ref_block}"
            "请生成一个 WorldSpec JSON，包含 world_name、world_premise、rules、power_system、locations、"
            "factions、power_structure、history_key_events、forbidden_zones。"
            "要求世界规则能直接制造冲突、爽点成本、升级空间和阴谋推进空间，不要只写空背景。\n"
            "power_system 必须是结构化对象，禁止写成一段散文："
            '{"name": 体系名, "tiers": [境界/层级名列表，由低到高有序——修炼/升级类题材必须列全每个大境界], '
            '"tier_progression": [{"tier": 境界名, "breakthrough_cost": 突破进入该境界要付的代价(资源/寿数/风险——写具体，不许写"刻苦"这种空话), '
            '"bottleneck": 卡住多数人无法跨过该境界的天槛/瓶颈}], '
            '"acquisition_method": 晋升机制, "hard_limits": 硬上限/天花板, "protagonist_starting_tier": 主角起始层级}。'
            "修炼/升级类题材，tier_progression 必须覆盖 tiers 里每一个境界——每个境界都要有明确的突破代价 + 瓶颈/天槛，"
            "让阶梯在机制上是真的(凡人修仙传级别的阶梯，每一境都用代价+天槛卡住)，而不是一串光秃秃的名字。\n"
            "下游的境界追踪与一致性闸门直接消费这些字段，写成自由文本会让它们全部失效。\n\n"
            f"{_WORLD_SPEC_COUNTER_EXAMPLES_ZH}"
        )
    )
    _genre_instruction = getattr(
        _genre_profile.planner_prompts, f"world_spec_instruction_{_lang_key}", ""
    )
    if _genre_instruction:
        user_prompt += f"\n\n{'[Genre planning requirements]' if is_en else '【品类规划要求】'}\n{_genre_instruction}"
    user_prompt = _append_category_context(user_prompt, project, is_en=is_en)

    # Inject world-richness floor/ceiling constraints so the FIRST
    # world_spec pass scales rules / locations / factions to chapter
    # count. Without this the LLM either starves the foundation (道种破虚:
    # 18 rules × 316 chapters → chapter-level material exhaustion) or
    # bloats it (EN projects: 574 rules that never ground). Both failure
    # modes push chapters onto a small exploitable pool and force volume
    # convergence.
    try:
        from bestseller.services.world_richness import (
            render_world_constraints_block,
        )

        user_prompt += "\n\n" + render_world_constraints_block(
            total_chapters=max(project.target_chapters, 1),
            language=language,
        )
    except Exception:
        logger.debug(
            "World-richness constraints block injection failed (non-fatal)",
            exc_info=True,
        )

    return system_prompt, user_prompt


def _locked_roster_prompt_block(locked_names: list[str], *, is_en: bool) -> str:
    """Render the R4 premise-roster lock instruction for the cast prompt."""

    names = "、".join(locked_names) if not is_en else ", ".join(locked_names)
    count = len(locked_names)
    if is_en:
        return (
            "\n\n[LOCKED CAST ROSTER — premise passthrough]\n"
            f"The premise explicitly fixes these {count} character names: {names}.\n"
            "Every one of these names MUST appear as a NAMED character in exactly "
            "one of protagonist / antagonist / antagonist_forces / supporting_cast — "
            "none may be dropped. Do NOT rename, replace, or alias any of them.\n"
            "For villains, the antagonist_forces[].name MUST be the person's own "
            "name (e.g. 'Mr White'), NOT an abstract faction label — a faction may "
            "be noted as that named villain's affiliation, but the name field is a "
            "person. You may only ADD new characters beyond this roster."
        )
    return (
        "\n\n【既定人物名册——premise 直通，最高优先级】\n"
        f"premise 已明确给定以下 {count} 个人物姓名：{names}。\n"
        "这 N 个名字**每一个**都必须作为具名角色，出现在 protagonist / antagonist / "
        "antagonist_forces / supporting_cast 之一中，禁止遗漏、禁止改名换名或同义替换。\n"
        "反派人物放入 antagonist_forces 时，其 name 字段必须是该人物的**具名**"
        "（如「白先生」「姜暮」），不得用抽象阵营名（如「某某集团·某分公司」）替代具名反派"
        "——阵营只能作为该具名反派的归属描述写在其它字段，name 必须是人。"
        "只允许在此名册之外补充新角色名。"
    )


_PUBLIC_DOMAIN_MYTHOLOGY_ALLOWLIST_KEY = "public_domain_mythology_allowlist"


def _public_domain_mythology_allowlist(project: ProjectModel) -> list[str]:
    """Public-domain mythological names (西游/封神 etc.) the book may use as-is.

    The base cast naming rules ("姓氏不能重复"、"避免网文烂大街名字"、"符合时代背景")
    implicitly steer the model away from recognizable mythology, so it silently
    humanizes a roster like 哮天犬/孙悟空 into original modern names. When a book
    explicitly opts into public-domain mythology via this metadata key, those
    names are allowlisted: encouraged as-is and exempt from the naming rules and
    name-drift repair.
    """

    metadata = _mapping(getattr(project, "metadata_json", None))
    raw = metadata.get(_PUBLIC_DOMAIN_MYTHOLOGY_ALLOWLIST_KEY)
    if not isinstance(raw, (list, tuple, set)):
        return []
    seen: set[str] = set()
    names: list[str] = []
    for item in raw:
        name = str(item).strip()
        if name and name not in seen:
            seen.add(name)
            names.append(name)
    return names


def _public_domain_mythology_block(names: list[str], *, is_en: bool) -> str:
    """Override naming rules for explicitly-allowlisted public-domain names."""

    joined = ", ".join(names) if is_en else "、".join(names)
    if is_en:
        return (
            "\n\n[PUBLIC-DOMAIN MYTHOLOGY ALLOWLIST — overrides naming rules]\n"
            f"These names are public-domain mythology and MUST be used verbatim: {joined}.\n"
            "For these specific characters, IGNORE the originality/“avoid famous names”/"
            "“no repeated surname”/era-fit naming rules — keep the classic name exactly. "
            "Build each as a full character (motive, voice, relationships) under that name; "
            "do NOT humanize, modernize, alias, or replace it with an original name."
        )
    return (
        "\n\n【公有领域神话人物名单——优先级高于命名规则】\n"
        f"以下名字属于公有领域神话（《西游记》《封神演义》等），必须原样使用：{joined}。\n"
        "对这些特定角色，请**忽略**上面的「原创/避免烂大街名字/姓氏不重复/符合时代背景」命名规则，"
        "保留经典本名一字不改；并以该本名为其设计完整人物（动机/声口/关系），"
        "**禁止**人形化、现代化、改名、同义替换或换成原创名。"
    )


def _cast_spec_prompts(
    project: ProjectModel,
    book_spec: dict[str, Any],
    world_spec: dict[str, Any],
    *,
    locked_names: list[str] | None = None,
) -> tuple[str, str]:
    from bestseller.services.genre_review_profiles import resolve_genre_review_profile

    language = _planner_language(project)
    is_en = is_english_language(language)
    _lang_key = "en" if is_en else "zh"
    prompt_pack = _planner_prompt_pack(project)
    era_hint = _detect_era_from_genre(project.genre)
    _genre_profile = resolve_genre_review_profile(project.genre, project.sub_genre)
    _genre_system = getattr(_genre_profile.planner_prompts, f"cast_spec_system_{_lang_key}", "")
    system_prompt = (
        "You are a cast architect for long-form commercial fiction. Output valid JSON only."
        if is_en
        else "你是长篇中文小说角色架构师。输出必须是合法 JSON，不要解释。"
    )
    if _genre_system:
        system_prompt += f"\n{_genre_system}"
    _pp_block = (
        f"Prompt Pack：\n{render_prompt_pack_prompt_block(prompt_pack)}\n" if prompt_pack else ""
    )
    _pp_cast_spec = _planner_fragment_or_ref(prompt_pack, project, "planner_cast_spec")
    _story_package_block = _story_package_prompt_block(project, language=language)
    _distilled_cast_block = _distilled_design_reference_block(project, "cast")
    _concept_lab_contract_line = _concept_lab_contract_block(project, language=language)
    _story_enhancer_line = _story_enhancer_contract_line(project, language)
    user_prompt = (
        (
            f"BookSpec summary:\n{summarize_book_spec(book_spec, language='en')}\n"
            f"WorldSpec summary:\n{summarize_world_spec(world_spec, language='en')}\n"
            f"Era / setting hint: {era_hint}\n"
            "Write all planning artifacts in English.\n"
            f"{_pp_block}"
            f"{_story_package_block}\n"
            f"{_distilled_cast_block}"
            f"{_concept_lab_contract_line}"
            f"{_story_enhancer_line}"
            f"{_pp_cast_spec}"
            "Generate a CastSpec JSON with protagonist, antagonist, antagonist_forces, supporting_cast, and conflict_map. "
            "The protagonist needs a vivid desire, a real weakness, visible growth space, and a memorable edge; the antagonist must actively counter the protagonist and keep escalating. "
            "Every major character must include a voice_profile object and a moral_framework object so their speech patterns stay distinct.\n\n"
            "IDENTITY LOCK — every protagonist, antagonist, and supporting_cast character must include "
            "gender (male/female/nonbinary/unknown), pronoun_set_en, and pronoun_set_zh. "
            "Do not omit these fields. For named person characters, gender must be male/female/nonbinary; "
            "unknown is only allowed for explicitly non-person entities marked with entity_type. "
            "For Chinese-language (zh) web fiction the PROTAGONIST gender must be male or female — "
            "do NOT use nonbinary for the protagonist unless the premise explicitly requires a "
            "genderless/AI/entity lead. pronoun_set_zh must be 他 or 她; never the pinyin 'ta'.\n\n"
            "PERSONHOOD LAYER — every protagonist must read as a real person, not a plot function. "
            "Populate ALL of:\n"
            "  - psych_profile: {mbti (e.g. 'INTJ'), big_five (OCEAN scores 0-100), enneagram (e.g. '5w4'), "
            "attachment_style (secure/anxious/avoidant/disorganized), cognitive_biases (list), temperament}\n"
            "  - life_history: {formative_events [{age, title, summary, impact}], education, career_history, "
            "defining_moments, trauma, achievements, regrets}\n"
            "  - social_network: {family/mentors/peers/superiors/subordinates/enemies as lists of "
            "{name, bond, emotional_weight, influence}; community and dependencies as string lists}\n"
            "  - beliefs: {religion, devotion_level, philosophical_stance, political_view, superstitions, "
            "ideology, crisis_of_faith}\n"
            "  - family_imprint: {parenting_style, family_socioeconomic, sibling_dynamics, inherited_values, "
            "family_secrets, breaking_points}\n"
            "Pull from real psychometric data — a character whose MBTI, OCEAN scores, attachment style, "
            "and family imprint all align is dramatically more consistent across chapters.\n\n"
            "VILLAIN CHARISMA — primary antagonists must NOT be pure evil. Populate villain_charisma with at "
            "least 4 of: noble_motivation, pain_origin, redeeming_qualities, philosophical_appeal, "
            "personal_code, tragic_irony, protagonist_mirror. The reader should briefly think 'maybe the "
            "villain is right' and feel loss when the villain falls.\n\n"
            "IMPORTANT — antagonist_forces:\n"
            "- Include an 'antagonist_forces' array with 2-4 conflict forces\n"
            "- Each force: {name, force_type (character/faction/environment/internal/systemic), active_volumes, threat_description, escalation_path}\n"
            "- Each volume should face a DIFFERENT type of challenge — don't repeat the same antagonist pressure\n"
            "- Mix visible and hidden threats for rich plotline interweaving\n\n"
            "Naming rules:\n"
            f"- Names must fit the {project.genre} genre and the {era_hint} setting\n"
            "- Core cast names should be memorable, readable, and easy to distinguish in dialogue\n"
            "- Avoid confusingly similar names or generic placeholder naming\n"
            "- Antagonist names may imply personality, but stay subtle\n"
            "- Every character must include a name_reasoning field\n\n"
            f"{_CAST_SPEC_COUNTER_EXAMPLES_EN}"
        )
        if is_en
        else (
            f"BookSpec 摘要：\n{summarize_book_spec(book_spec, language='zh')}\n"
            f"WorldSpec 摘要：\n{summarize_world_spec(world_spec, language='zh')}\n"
            f"题材时代：{era_hint}\n"
            f"{_pp_block}"
            f"{_story_package_block}\n"
            f"{_distilled_cast_block}"
            f"{_concept_lab_contract_line}"
            f"{_story_enhancer_line}"
            f"{_pp_cast_spec}"
            "请生成一个 CastSpec JSON，包含 protagonist、antagonist、antagonist_forces、supporting_cast、conflict_map。"
            "主角必须有鲜明欲望、明显短板、可持续升级点和可被读者快速记住的差异化优势；"
            "反派必须能持续升级并主动反制主角；配角要形成明确功能位和关系张力。\n"
            "\n【重要——多力量冲突设计】\n"
            "必须包含 antagonist_forces 数组（2-4个冲突力量），每个包含：\n"
            "name, force_type(character/faction/environment/internal/systemic), active_volumes, threat_description, escalation_path\n"
            "每卷应面对不同类型的挑战——不要全书只有一个反派在施压\n"
            "要有明线冲突和暗线伏笔的交织\n\n"
            "每个角色必须包含 voice_profile 对象（speech_register、verbal_tics、sentence_style、"
            "emotional_expression、mannerisms）和 moral_framework 对象（core_values、"
            "lines_never_crossed、willing_to_sacrifice），确保不同角色的说话方式有明显区分度。\n\n"
            "【身份锁定】protagonist、antagonist、supporting_cast 中每个角色都必须包含 "
            "gender（male/female/nonbinary/unknown）、pronoun_set_zh、pronoun_set_en。"
            "不要省略。具名人物角色的 gender 必须是 male/female/nonbinary；"
            "unknown 只允许用于明确标记 entity_type 的非人物实体。"
            "中文网文【主角】的 gender 必须是 male 或 female——除非命题明确要求无性别/AI/实体主角，"
            "否则不得给主角用 nonbinary；pronoun_set_zh 必须是 他 或 她，禁止使用拼音 'ta'。\n\n"
            "【人格底层 — 让角色像真人，不是剧情齿轮】\n"
            "主角必须完整填写以下五块（参考真实心理学数据，不要写抽象类型）：\n"
            "  - psych_profile：{mbti（如 'INTJ'）、big_five（OCEAN 五维 0-100 分）、enneagram（如 '5w4'）、"
            "attachment_style（安全/焦虑/回避/混乱）、cognitive_biases（认知偏差列表）、temperament（气质）}\n"
            "  - life_history：{formative_events 列表 [{age, title, summary, impact}]、education、"
            "career_history、defining_moments、trauma、achievements、regrets}\n"
            "  - social_network：{family/mentors/peers/superiors/subordinates/enemies 均为对象列表 "
            "[{name, bond, emotional_weight, influence}]；community 与 dependencies 为字符串列表}\n"
            "  - beliefs：{religion、devotion_level（虔诚程度）、philosophical_stance（儒/道/佛/虚无/实用）、"
            "political_view、superstitions、ideology（终极信念）、crisis_of_faith（信仰动摇触发点）}\n"
            "  - family_imprint：{parenting_style（教养方式）、family_socioeconomic（出身阶层）、"
            "sibling_dynamics（兄弟姐妹角色）、inherited_values、family_secrets、breaking_points}\n"
            "MBTI/九型/Big Five/依恋类型 互相一致的角色，在每一章决策时会更稳定。\n\n"
            "【反派必须有魅力 — 不要纯坏】\n"
            "primary 反派必须填写 villain_charisma 至少 4 项："
            "noble_motivation（高尚出发点）、pain_origin（黑化伤痛）、redeeming_qualities（让读者心软的瞬间）、"
            "philosophical_appeal（理念中合理的部分）、personal_code（绝不做的事）、tragic_irony（悲剧反讽）、"
            "protagonist_mirror（与主角相似处）。读者应该在反派落败时也感到失落，而不是只想看 ta 死。\n\n"
            "【角色命名硬性要求】\n"
            f"- 角色名字必须符合「{project.genre}」题材和「{era_hint}」时代背景\n"
            "- 主角名 2-3 字，音调优美朗朗上口，有记忆点\n"
            "- 所有角色的姓氏不能重复\n"
            "- 避免过于生僻的字、谐音不雅的组合、或网文中已经烂大街的名字\n"
            "- 反派名可暗示性格特质但不要太刻意\n"
            "- 每个角色附 name_reasoning 字段说明命名理由\n\n"
            f"{_CAST_SPEC_COUNTER_EXAMPLES_ZH}"
        )
    )
    _mythology_allowlist = _public_domain_mythology_allowlist(project)
    _locked = [
        name.strip()
        for name in (locked_names or [])
        if isinstance(name, str) and name.strip()
    ]
    # Allowlisted public-domain names are also force-locked into the roster so the
    # model can't silently humanize them out of existence.
    for _myth_name in _mythology_allowlist:
        if _myth_name not in _locked:
            _locked.append(_myth_name)
    if _locked:
        user_prompt += _locked_roster_prompt_block(_locked, is_en=is_en)
    if _mythology_allowlist:
        user_prompt += _public_domain_mythology_block(_mythology_allowlist, is_en=is_en)
    _genre_instruction = getattr(
        _genre_profile.planner_prompts, f"cast_spec_instruction_{_lang_key}", ""
    )
    if _genre_instruction:
        user_prompt += f"\n\n{'[Genre planning requirements]' if is_en else '【品类规划要求】'}\n{_genre_instruction}"
    # G1 (xianxia benchmark): this contract used to hard-code "3-5 named
    # characters", directly contradicting the relationship-scaling block
    # (10-volume floor = 15). Models obey the smaller number: a 500-chapter
    # epic shipped 3 supporting characters and the scaling repair could not
    # recover. Scale the roster target with the project's volume count.
    from bestseller.services.relationship_scaling import compute_supporting_bounds

    _roster_volume_count = int(
        compute_linear_hierarchy(max(project.target_chapters, 1)).get("volume_count") or 1
    )
    _roster_bounds = compute_supporting_bounds(_roster_volume_count)
    if is_en:
        user_prompt += (
            "\n\n[Compact output contract]\n"
            "- Keep character free-text fields to one concise sentence.\n"
            "- Do not emit large empty/null-heavy psych_profile, life_history, social_network, beliefs, or family_imprint objects.\n"
            f"- Supporting cast target: {_roster_bounds.floor}-{_roster_bounds.ceiling} named characters "
            f"(floor scaled to {_roster_volume_count} volume(s); never fewer than {_roster_bounds.floor}).\n"
            "- The JSON must be complete and must not hit the output token limit."
        )
    else:
        user_prompt += (
            "\n\n【紧凑输出合同】\n"
            "- 每个角色的自由文本字段控制在一句具体中文内。\n"
            "- 不要输出大块空值/null 的 psych_profile、life_history、social_network、beliefs、family_imprint。\n"
            f"- supporting_cast 需 {_roster_bounds.floor}-{_roster_bounds.ceiling} 名具名角色"
            f"（下限按全书 {_roster_volume_count} 卷伸缩，不得少于 {_roster_bounds.floor} 名）。\n"
            "- JSON 必须完整闭合，禁止打满输出 token 上限。"
        )
    user_prompt = _append_category_context(user_prompt, project, is_en=is_en)

    # Inject foundation-richness floor constraints so the FIRST cast-spec
    # pass produces enough distinct antagonist_forces with active_volumes
    # coverage. Without this, the downstream volume plan falls through to
    # the single-antagonist fallback and every volume collapses onto one
    # pressure — the canonical xianxia (道种破虚) failure mode.
    try:
        from bestseller.services.foundation_richness import (
            render_foundation_constraints_block,
        )

        _hierarchy = compute_linear_hierarchy(max(project.target_chapters, 1))
        _volume_count = int(_hierarchy.get("volume_count") or 1)
        if _volume_count > 1:
            user_prompt += "\n\n" + render_foundation_constraints_block(
                volume_count=_volume_count,
                language=language,
            )
    except Exception:
        logger.debug(
            "Foundation-richness constraints block injection failed (non-fatal)", exc_info=True
        )

    # Inject antagonist-lifecycle constraints: demand a lifecycle-rich
    # `antagonists` roster so each antagonist has a line_role, stages of
    # relevance, and a resolution_type. Without this, the post-fix
    # regression observed in production kicks in: each volume gets a
    # distinct enemy, but every enemy is a one-volume kill-and-move-on
    # boss, so the story still reads as a rotating template.
    try:
        from bestseller.services.antagonist_lifecycle import (
            render_antagonist_lifecycle_constraints_block,
        )

        _hierarchy = compute_linear_hierarchy(max(project.target_chapters, 1))
        _volume_count = int(_hierarchy.get("volume_count") or 1)
        if _volume_count > 1:
            user_prompt += "\n\n" + render_antagonist_lifecycle_constraints_block(
                total_chapters=max(project.target_chapters, 1),
                volume_count=_volume_count,
                language=language,
            )
            if is_en:
                user_prompt += (
                    "\nEMIT `antagonists` at the CastSpec top level with entries "
                    "shaped like:\n"
                    "  {name, archetype, line_role (overt|undercurrent|hidden), "
                    "stages_of_relevance [{start_volume, end_volume}, ...], "
                    "resolution_type, transition_volume, transition_mechanism}\n"
                    "Every overt antagonist MUST have a distinct `name`; past "
                    "enemies that survive should transition to allies, neutrals, "
                    "or fade away — do NOT kill every antagonist."
                )
            else:
                user_prompt += (
                    "\n请在 CastSpec 顶层输出 `antagonists` 字段，每个元素结构：\n"
                    "  {name, archetype, line_role（overt|undercurrent|hidden）, "
                    "stages_of_relevance [{start_volume, end_volume}, ...], "
                    "resolution_type, transition_volume, transition_mechanism}\n"
                    "每个明线敌人的 name 必须两两不同；未被杀的前期敌人应转化为"
                    "盟友/中立/消失，不要把所有敌人都设为『被主角击杀』。"
                )
    except Exception:
        logger.debug(
            "Antagonist-lifecycle constraints block injection failed (non-fatal)",
            exc_info=True,
        )

    # Inject relationship-scaling constraints so the FIRST cast-spec pass
    # produces a supporting_cast roster large enough and diverse enough to
    # populate a book of the planned length. Without this, long novels
    # (≥ 300 chapters) ship with only 3-5 supporting characters and every
    # volume recycles the same faces — the social-fabric analogue of the
    # world-richness / foreshadowing-scaling starvation patterns.
    try:
        from bestseller.services.relationship_scaling import (
            render_relationship_constraints_block,
        )

        _hierarchy = compute_linear_hierarchy(max(project.target_chapters, 1))
        _volume_count = int(_hierarchy.get("volume_count") or 1)
        if _volume_count > 1:
            user_prompt += "\n\n" + render_relationship_constraints_block(
                total_chapters=max(project.target_chapters, 1),
                volume_count=_volume_count,
                language=language,
            )
            if is_en:
                user_prompt += (
                    "\nEMIT `supporting_cast` at the CastSpec top level with "
                    "entries shaped like:\n"
                    "  {name, role, active_volumes (list of volume numbers "
                    "or [{start_volume, end_volume}, ...]), "
                    "relationship_to_protagonist, evolution_arc, "
                    "goal (the character's OWN agenda, in tension with the protagonist's), "
                    "flaw (an exploitable weakness the plot can use)}\n"
                    "Spread roles across mentor / ally / rival / family / "
                    "romantic / subordinate / confidant / broker — no single "
                    "category may exceed 40% of the roster. Every volume "
                    "MUST have at least one active non-antagonist "
                    "supporting-cast member."
                )
            else:
                user_prompt += (
                    "\n请在 CastSpec 顶层输出 `supporting_cast` 字段，每个元素结构：\n"
                    "  {name、role、active_volumes（卷号列表或 "
                    "[{start_volume, end_volume}, ...]）、"
                    "relationship_to_protagonist、evolution_arc（关系随全书演化）、"
                    "goal（该角色自己的独立动机，需与主角目标存在张力）、"
                    "flaw（可被剧情利用的缺陷）}\n"
                    "role 要在 mentor/ally/rival/family/romantic/subordinate/"
                    "confidant/broker 之间分布——单一类别不得超过 40%。"
                    "每一卷至少要有 1 名活跃的非敌人类 supporting_cast 成员。"
                )
    except Exception:
        logger.debug(
            "Relationship-scaling constraints block injection failed (non-fatal)",
            exc_info=True,
        )

    return system_prompt, user_prompt


def _volume_plan_prompts(
    project: ProjectModel,
    book_spec: dict[str, Any],
    world_spec: dict[str, Any],
    cast_spec: dict[str, Any],
    *,
    act_plan: list[dict[str, Any]] | None = None,
) -> tuple[str, str]:
    from bestseller.services.genre_review_profiles import resolve_genre_review_profile

    language = _planner_language(project)
    is_en = is_english_language(language)
    _lang_key = "en" if is_en else "zh"
    writing_profile = _planner_writing_profile(project)
    prompt_pack = _planner_prompt_pack(project)
    _genre_profile = resolve_genre_review_profile(project.genre, project.sub_genre)
    _genre_system = getattr(_genre_profile.planner_prompts, f"volume_plan_system_{_lang_key}", "")
    system_prompt = (
        "You are a structural editor for long-form commercial fiction. Output a valid JSON array only."
        if is_en
        else "你是长篇中文小说结构编辑。输出必须是合法 JSON 数组，不要解释。"
    )
    if _genre_system:
        system_prompt += f"\n{_genre_system}"
    _pp_block = (
        f"Prompt Pack：\n{render_prompt_pack_prompt_block(prompt_pack)}\n" if prompt_pack else ""
    )
    _pp_volume_plan = _planner_fragment_or_ref(prompt_pack, project, "planner_volume_plan")
    _story_package_block = _story_package_prompt_block(project, language=language)
    _story_design_block = _story_design_kernel_prompt_block(project)
    _emotion_driven_block = _emotion_driven_kernel_prompt_block(project)
    _public_emotion_block = _public_emotion_kernel_prompt_block(project)
    _compliance_boundary_block = _compliance_boundary_prompt_block(project)
    _entry_system_block = _entry_system_kernel_prompt_block(project)
    _entry_registry_block = _entry_registry_prompt_block(project)
    _character_drama_block = _character_drama_prompt_block(project, cast_spec=cast_spec)
    _distilled_volume_block = _distilled_design_reference_block(project, "volume_plan")
    from bestseller.services.hook_propagation import (
        hook_spec_from_metadata,
        render_hook_spec_prompt_block,
    )

    _hook_contract_block = render_hook_spec_prompt_block(
        hook_spec_from_metadata(project.metadata_json if isinstance(project.metadata_json, dict) else {}),
        language=language,
    )
    _hook_contract_line = f"{_hook_contract_block}\n" if _hook_contract_block else ""
    _concept_lab_contract_line = _concept_lab_contract_block(project, language=language)
    _story_enhancer_line = _story_enhancer_contract_line(project, language)
    # P-6 (xianxia benchmark): the prompt previously never stated how many
    # volumes to plan or how many chapters they must cover, so a 500-chapter
    # project shipped a 5-volume × 50-chapter plan (half the book unplanned).
    # State the contract up front; enforce_volume_plan_contract() rejects
    # violations at the validation boundary.
    _contract_chapters = max(int(project.target_chapters or 0), 1)
    _contract_volume_count = int(
        compute_linear_hierarchy(_contract_chapters).get("volume_count") or 1
    )
    _coverage_contract_line = (
        (
            f"HARD CONTRACT: output EXACTLY {_contract_volume_count} volume(s); the sum of all "
            f"chapter_count_target values MUST equal {_contract_chapters} (the full book — never plan only part of it); "
            "every non-final volume MUST carry a concrete, volume-specific reader_hook_to_next "
            "(empty or boilerplate hooks are rejected).\n"
        )
        if is_en
        else (
            f"【硬性契约】必须恰好输出 {_contract_volume_count} 卷；所有卷的 chapter_count_target 之和"
            f"必须等于 {_contract_chapters} 章（覆盖全书，禁止只规划前半部）；"
            "除最后一卷外，每卷的 reader_hook_to_next 必须是该卷专属的具体钩子，空值或套话会被拒收。\n"
        )
    )
    user_prompt = (
        (
            f"Project title: {project.title}\n"
            f"Target words: {project.target_word_count}\n"
            f"Target chapters: {project.target_chapters}\n"
            "Write all planning artifacts in English.\n"
            f"Writing profile:\n{render_writing_profile_prompt_block(writing_profile, language=language)}\n"
            f"{_pp_block}"
            f"BookSpec summary:\n{summarize_book_spec(book_spec, language='en')}\n"
            f"WorldSpec summary:\n{summarize_world_spec(world_spec, language='en')}\n"
            f"CastSpec summary:\n{summarize_cast_spec(cast_spec, language='en')}\n"
            f"{_story_package_block}\n"
            f"{_distilled_volume_block}"
            f"{_hook_contract_line}"
            f"{_concept_lab_contract_line}"
            f"{_story_enhancer_line}"
            f"{_story_design_block}\n"
            f"{_emotion_driven_block}\n"
            f"{_public_emotion_block}\n"
            f"{_compliance_boundary_block}\n"
            f"{_entry_system_block}\n"
            f"{_entry_registry_block}\n"
            f"{_character_drama_block}\n"
            f"{_pp_volume_plan}"
            f"{_coverage_contract_line}"
            "Generate a VolumePlan JSON array. Each entry must include volume_number, volume_title, volume_theme, chapter_count_target, volume_goal, volume_obstacle, volume_climax, volume_resolution, conflict_phase, and primary_force_name. "
            "Each entry must also include worldview progression fields: `world_state_targets`, `active_authority_claims`, `map_function`, `world_asset_refs`, `asset_risk_escalation`, and `reveal_budget`. "
            "`world_state_targets` must name WorldviewKernel state variables and their intended movement; `map_function` must explain resource anomaly, faction/authority pressure, or rule demonstration; `asset_risk_escalation` must increase cost/exposure/attention when an asset repeats. "
            "`volume_resolution` MUST be an object shaped like {protagonist_power_tier, goal_achieved, cost_paid, new_threat_introduced}; never emit it as a plain string. "
            "Inside `volume_resolution`, `goal_achieved` MUST be a JSON boolean (`true` or `false`), not a prose summary; put prose in `cost_paid` or `new_threat_introduced`. "
            "CRITICAL: `volume_title` must be a concrete, evocative 2-6 word name (e.g. 'Ashes of the Old Court'). Placeholder names like 'Volume 3', 'Vol. 4', or empty strings are forbidden and will be rejected. "
            "CRITICAL: Each volume must face a DIFFERENT primary conflict force from the CastSpec's antagonist_forces. Don't repeat the same antagonist pressure — vary between survival, political intrigue, betrayal, faction warfare, existential threat, etc. "
            "Every volume needs a concrete payoff, escalation, key reveal, volume-end hook, and anticipation for the next volume.\n\n"
            "[VOLUME-LEVEL DIFFERENTIATION — HARD CONSTRAINT]\n"
            "1. No single `conflict_phase` value may be used in more than 2 volumes across the entire plan.\n"
            "2. `primary_force_name` MUST be different for every consecutive pair of volumes — the same faction/villain cannot dominate two volumes in a row.\n"
            "3. Vary the CLIMAX SHAPE across volumes: duel, melee, psychological showdown, reveal-twist, sacrificial choice, negotiation, trap-spring, comeback-win. The same climax shape may not appear in consecutive volumes.\n"
            "4. Each volume's CORE PAYOFF must belong to a different reader-satisfaction class: power-tier jump / identity reversal / information decode / emotional fulfillment / resource windfall / political ascent / score-settling / world-scope expansion.\n"
            "5. Vary the RHYTHM ARC: some volumes build steadily then shock, others open loud then tighten into a tense payoff, others stage a false-win → reversal → true-win. No two volumes may share the same rhythm arc.\n"
            "6. `volume_goal` must be a concrete, visualizable objective or event — NEVER 'advance the main plot' / 'consolidate power' / 'level up'.\n"
            "7. `volume_obstacle` may not simply be 'the prior volume's antagonist continuing to press' — if volume N-1's primary pressure was faction A, volume N must shift to faction B or to a non-faction obstacle (environment / inner reckoning / systemic crisis)."
        )
        if is_en
        else (
            f"项目标题：{project.title}\n"
            f"目标字数：{project.target_word_count}\n"
            f"目标章节：{project.target_chapters}\n"
            f"写作画像：\n{render_writing_profile_prompt_block(writing_profile, language=language)}\n"
            f"{_pp_block}"
            f"BookSpec 摘要：\n{summarize_book_spec(book_spec, language='zh')}\n"
            f"WorldSpec 摘要：\n{summarize_world_spec(world_spec, language='zh')}\n"
            f"CastSpec 摘要：\n{summarize_cast_spec(cast_spec, language='zh')}\n"
            f"{_story_package_block}\n"
            f"{_distilled_volume_block}"
            f"{_hook_contract_line}"
            f"{_concept_lab_contract_line}"
            f"{_story_enhancer_line}"
            f"{_story_design_block}\n"
            f"{_emotion_driven_block}\n"
            f"{_public_emotion_block}\n"
            f"{_compliance_boundary_block}\n"
            f"{_entry_system_block}\n"
            f"{_entry_registry_block}\n"
            f"{_character_drama_block}\n"
            f"{_pp_volume_plan}"
            f"{_coverage_contract_line}"
            "请生成 VolumePlan JSON 数组，每个元素包含 volume_number、volume_title、volume_theme、"
            "chapter_count_target、volume_goal、volume_obstacle、volume_climax、volume_resolution、"
            "conflict_phase（冲突类型：survival/political_intrigue/betrayal/faction_war/existential_threat/internal_reckoning）、"
            "primary_force_name（本卷主要冲突力量名称）。"
            "每个元素还必须包含世界观推进字段：`world_state_targets`、`active_authority_claims`、"
            "`map_function`、`world_asset_refs`、`asset_risk_escalation`、`reveal_budget`。"
            "`world_state_targets` 必须写明 WorldviewKernel 状态变量及目标变化；`map_function` 必须说明资源异常、"
            "势力/权威压力或规则展示；当 asset 重复出现时，`asset_risk_escalation` 必须提高代价、暴露或注意力。"
            "volume_resolution 必须是对象，结构为 {protagonist_power_tier, goal_achieved, cost_paid, new_threat_introduced}，严禁输出成普通字符串。"
            "其中 goal_achieved 必须是 JSON 布尔值 true/false，不能写成中文剧情总结；剧情文字放到 cost_paid 或 new_threat_introduced。"
            "【关键】volume_title 必须是 2-6 字的具体意象化卷名（例如『逆命入局』『灰楼开门』），"
            "严禁使用『第N卷』『Volume N』『未命名』等占位名或空字符串，否则会被拒绝。"
            "【关键】每卷必须面对不同的冲突力量和冲突类型——不要所有卷都是同一个反派在施压！"
            "每卷都要有清晰的爽点兑现、局势升级、关键揭示、卷尾钩子和下一卷期待。\n\n"
            "【卷间差异化硬约束——读者最怕每卷都长得一样】\n"
            "1. conflict_phase 不得重复使用超过 2 次（6 卷 ≥ 4 种不同类型，10 卷 ≥ 6 种不同类型）。\n"
            "2. primary_force_name 每卷都必须不同——不能让同一个反派/势力连续两卷都是本卷主要压力。\n"
            "3. 每卷的 climax 形态要轮换：独斗、大混战、心理对决、"
            "揭示反转、牺牲抉择、谈判博弈、制造陷阱、反败为胜等，同一种 climax 形态不得连续两卷使用。\n"
            "4. 每卷的 core payoff（读者预期兑现点）必须属于不同的爽点类别："
            "实力跃迁 / 身份翻身 / 信息解谜 / 情感兑现 / 资源爆发 / 权力获取 / 旧怨了结 / 世界观扩张。\n"
            "5. 每卷的节奏弧要不同：有的卷是『稳扎稳打—骤变—慢热收束』，"
            "有的是『开局炸场—困局僵持—爆点收尾』，有的是『伪胜利—反转—真胜利』，不得全书用同一条节奏弧。\n"
            "6. 每卷 volume_goal 必须是具体的、可视化的目标物或目标事件——"
            "禁止『继续推进主线』『巩固势力』『提升实力』这类抽象功能描述。\n"
            "7. 每卷 volume_obstacle 不得是『前卷反派的延续』——如果前一卷主要压力是A势力，本卷必须换成 B 势力或完全不同类型（环境/内心/系统性危机等）。"
        )
    )
    # Inject act plan context when available (multi-act novels)
    if act_plan:
        act_context = _json_dumps(act_plan)
        if is_en:
            user_prompt += (
                f"\n\nActPlan (macro narrative structure):\n{act_context}\n"
                "Each volume must belong to one act. Volume themes and goals must align with "
                "the parent act's core_theme and act_goal. Volumes within the same act should "
                "form a coherent narrative progression."
            )
        else:
            user_prompt += (
                f"\n\n幕计划（全书宏观叙事结构）：\n{act_context}\n"
                "每卷必须隶属于一个幕，主题和目标需与所属幕的 core_theme 和 act_goal 一致。"
                "同一幕内的卷应形成连贯的叙事推进。"
            )

    _genre_instruction = getattr(
        _genre_profile.planner_prompts, f"volume_plan_instruction_{_lang_key}", ""
    )
    if _genre_instruction:
        user_prompt += f"\n\n{'[Genre planning requirements]' if is_en else '【品类规划要求】'}\n{_genre_instruction}"
    user_prompt = _append_category_context(user_prompt, project, is_en=is_en)

    # Inject foreshadowing scaling constraints so the FIRST volume-plan
    # pass produces enough plants/payoffs for the novel length. Without
    # this, production data shows systemic starvation (1 clue per 100-170
    # chapters across all 6 production books, regardless of genre).
    try:
        from bestseller.services.foreshadowing_scaling import (
            render_foreshadowing_constraints_block,
        )

        _hierarchy = compute_linear_hierarchy(max(project.target_chapters, 1))
        _volume_count = int(_hierarchy.get("volume_count") or 1)
        user_prompt += "\n\n" + render_foreshadowing_constraints_block(
            total_chapters=max(project.target_chapters, 1),
            volume_count=_volume_count,
            language=language,
        )
    except Exception:
        logger.debug(
            "Foreshadowing-scaling constraints block injection failed (non-fatal)",
            exc_info=True,
        )

    # Thread narrative-lines core_axis into every volume theme so the
    # four-layer contract propagates from book_spec down to volumes. The
    # BookSpec defines `narrative_lines.core_axis` — each volume's
    # `volume_theme` should echo a core_axis phrasing_token or include an
    # explicit `core_axis_reference` field.
    try:
        _narrative_lines = _mapping(book_spec).get("narrative_lines")
        if isinstance(_narrative_lines, dict) and _narrative_lines:
            _core_axis = _mapping(_narrative_lines.get("core_axis"))
            _axis_statement = str(_core_axis.get("statement") or "").strip()
            _axis_tokens = _core_axis.get("phrasing_tokens") or []
            _axis_tokens_str = ", ".join(str(t).strip() for t in _axis_tokens if str(t).strip())
            if _axis_statement or _axis_tokens_str:
                if is_en:
                    user_prompt += (
                        "\n\n[NARRATIVE-LINES THREADING — hard constraint]\n"
                        f"Core axis: {_axis_statement or '(see BookSpec)'}\n"
                        f"Phrasing tokens: [{_axis_tokens_str or 'see BookSpec'}]\n"
                        "Every `volume_theme` MUST reference the core_axis "
                        "(either by using one of the phrasing tokens verbatim "
                        "or by emitting a `core_axis_reference` field whose "
                        "value is one of the tokens). Without this, the book "
                        "collapses into a single overt arc rotating across "
                        "volumes."
                    )
                else:
                    user_prompt += (
                        "\n\n【叙事四线贯穿 — 硬性要求】\n"
                        f"核心轴：{_axis_statement or '（参见 BookSpec）'}\n"
                        f"关键词：[{_axis_tokens_str or '参见 BookSpec'}]\n"
                        "每卷的 volume_theme 必须引用核心轴"
                        "（要么逐字包含其中一个 phrasing_token，"
                        "要么输出 `core_axis_reference` 字段，值为其中一个 token）。"
                        "否则全书会塌陷为一条单线剧情在各卷间轮换。"
                    )
    except Exception:
        logger.debug(
            "Narrative-lines core_axis threading injection failed (non-fatal)",
            exc_info=True,
        )

    return system_prompt, user_prompt


def _outline_prompts(
    project: ProjectModel,
    book_spec: dict[str, Any],
    cast_spec: dict[str, Any],
    volume_plan: list[dict[str, Any]],
) -> tuple[str, str]:
    from bestseller.services.genre_review_profiles import resolve_genre_review_profile

    language = _planner_language(project)
    is_en = is_english_language(language)
    _lang_key = "en" if is_en else "zh"
    writing_profile = _planner_writing_profile(project)
    prompt_pack = _planner_prompt_pack(project)
    from bestseller.services.invariants import is_low_pressure_tone as _is_low_pressure_tone

    _proj_meta = project.metadata_json if isinstance(project.metadata_json, dict) else {}
    _outline_low_pressure = _is_low_pressure_tone(
        getattr(prompt_pack, "key", None) or _proj_meta.get("prompt_pack_key"),
        project.genre,
        project.sub_genre,
    )
    _genre_profile = resolve_genre_review_profile(project.genre, project.sub_genre)
    _genre_system = getattr(_genre_profile.planner_prompts, f"outline_system_{_lang_key}", "")
    system_prompt = (
        "You are a chapter-outline planner for long-form commercial fiction. Output valid JSON only."
        if is_en
        else "你是长篇中文小说章纲规划师。输出必须是合法 JSON，不要解释。"
    )
    if _genre_system:
        system_prompt += f"\n{_genre_system}"
    _pp_block = (
        f"Prompt Pack：\n{render_prompt_pack_prompt_block(prompt_pack)}\n" if prompt_pack else ""
    )
    _pp_outline = _planner_fragment_or_ref(prompt_pack, project, "planner_outline")
    _methodology_planner_block = render_methodology_phase_block(
        prompt_pack,
        phase="planner",
    )
    _methodology_line = f"\n{_methodology_planner_block}\n" if _methodology_planner_block else ""
    _story_design_block = _story_design_kernel_prompt_block(project)
    _emotion_driven_block = _emotion_driven_kernel_prompt_block(project)
    _entry_system_block = _entry_system_kernel_prompt_block(project)
    _entry_registry_block = _entry_registry_prompt_block(project)
    _character_drama_block = _character_drama_prompt_block(project, cast_spec=cast_spec)
    _story_effect_catalog_block = render_story_effect_skill_catalog_prompt_block(
        project.metadata_json if isinstance(project.metadata_json, dict) else {},
        language=language,
    )
    _story_effect_catalog_line = (
        f"\n{_story_effect_catalog_block}\n" if _story_effect_catalog_block else ""
    )
    _selected_story_effect_contracts = render_selected_story_effect_skill_contracts(
        project.metadata_json if isinstance(project.metadata_json, dict) else {},
        language=language,
        stage="chapter_outline",
    )
    _selected_story_effect_contract_line = (
        f"\n{_selected_story_effect_contracts}\n"
        if _selected_story_effect_contracts
        else ""
    )
    _distilled_outline_block = _distilled_design_reference_block(project, "chapter_outline")
    _hook_ledger_v2_block = render_hook_ledger_planner_contract(language=language)
    _hook_ledger_v2_line = f"\n{_hook_ledger_v2_block}\n" if _hook_ledger_v2_block else ""
    _payoff_ledger_v2_block = render_payoff_ledger_planner_contract(language=language)
    _payoff_ledger_v2_line = f"\n{_payoff_ledger_v2_block}\n" if _payoff_ledger_v2_block else ""
    from bestseller.services.hook_propagation import (
        hook_spec_from_metadata,
        render_hook_spec_prompt_block,
    )

    _hook_spec = hook_spec_from_metadata(
        project.metadata_json if isinstance(project.metadata_json, dict) else {}
    )
    _anti_commonsense_hook_block = render_hook_spec_prompt_block(
        _hook_spec,
        language=language,
    )
    _anti_commonsense_hook_line = (
        f"\n{_anti_commonsense_hook_block}\n" if _anti_commonsense_hook_block else ""
    )
    _concept_lab_contract_line = _concept_lab_contract_block(project, language=language)
    _story_enhancer_line = _story_enhancer_contract_line(project, language)
    # ── Webnovel method cards (design-layer methodology, baked upstream) ──
    # target_emotion discipline + hook_type taxonomy + golden-opening rules.
    # Soft: any config/load failure degrades to empty fragments.
    _method_cards_hook_line = ""
    _golden_opening_line = ""
    _tone_contract_line = ""
    _logic_coherence_line = ""
    _appeal_line = ""
    _emotion_vocab_csv = "爽、燃、暖、虐、悬疑、紧张、轻松、甜、震撼"
    _hook_keys_csv = ""
    try:
        from bestseller.services.quality_levers.webnovel_method_cards import (
            chapter_end_hook_keys,
            render_golden_opening_rules_block,
            render_outline_hook_taxonomy_block,
            render_logic_coherence_contract_block,
            render_opening_pull_contract_block,
            render_front_ten_retention_contract_block,
            render_tone_emotion_contract_block,
            target_emotion_vocabulary,
        )

        _vocab = target_emotion_vocabulary()
        if _vocab:
            _emotion_vocab_csv = "、".join(_vocab)
        _hook_keys_csv = ", ".join(chapter_end_hook_keys())
        _tone_block = render_tone_emotion_contract_block(
            _mapping(book_spec).get("tone"), language="en" if is_en else "zh"
        )
        _tone_contract_line = f"\n{_tone_block}\n" if _tone_block else ""
        _logic_coherence_line = "\n" + render_logic_coherence_contract_block(
            language="en" if is_en else "zh"
        ) + "\n"
        _appeal_line = (
            "\n"
            + render_opening_pull_contract_block(language="en" if is_en else "zh")
            + "\n"
            + render_front_ten_retention_contract_block(
                language="en" if is_en else "zh"
            )
            + "\n"
        )
        if not is_en:
            _method_block = render_outline_hook_taxonomy_block("opening")
            _method_cards_hook_line = f"\n{_method_block}\n" if _method_block else ""
            _golden_block = render_golden_opening_rules_block(
                low_pressure=_outline_low_pressure
            )
            _golden_opening_line = f"\n{_golden_block}\n" if _golden_block else ""
    except Exception:
        logger.debug(
            "Webnovel method-cards block injection failed (non-fatal)",
            exc_info=True,
        )
    user_prompt = (
        (
            f"Project title: {project.title}\n"
            f"Target chapters: {project.target_chapters}\n"
            "Write all planning artifacts in English.\n"
            f"Writing profile:\n{render_writing_profile_prompt_block(writing_profile, language=language)}\n"
            f"{_pp_block}"
            f"Serial fiction guardrails:\n{render_serial_fiction_guardrails(writing_profile, language=language)}\n"
            f"BookSpec summary:\n{summarize_book_spec(book_spec, language='en')}\n"
            f"CastSpec summary:\n{summarize_cast_spec(cast_spec, language='en')}\n"
            f"VolumePlan summary:\n{summarize_volume_plan_context(volume_plan, current_volume=1, language='en')}\n"
            f"{_distilled_outline_block}"
            f"{_story_design_block}\n"
            f"{_emotion_driven_block}\n"
            f"{_entry_system_block}\n"
            f"{_entry_registry_block}\n"
            f"{_character_drama_block}\n"
            f"{_story_effect_catalog_line}"
            f"{_selected_story_effect_contract_line}"
            f"{_pp_outline}"
            f"{_methodology_line}"
            f"{_concept_lab_contract_line}"
            f"{_story_enhancer_line}"
            f"{_anti_commonsense_hook_line}"
            f"{_hook_ledger_v2_line}"
            f"{_payoff_ledger_v2_line}"
            "Generate a full ChapterOutlineBatch JSON with batch_name and chapters. Each chapter needs at least 3 scenes. "
            "The first 3 chapters must rapidly establish the protagonist edge, the core anomaly, the first gain/loss cycle, and a strong read-on hook. "
            + (
                "[GOLDEN OPENING — chapters 1-3 hard rules, low-pressure comedy/healing] Open on a warm, concrete present-day moment (an ordinary day disrupted / an enviable small joy / dropped into a homey unfamiliar setting); NEVER open on a crisis, countdown, or threat. "
                "The golden finger / system MUST be SHOWN through a concrete, ideally funny event — the reader infers the rules from action and consequences; never have a system / customer-service voice / AI / narrator recite its own rules as a page-one infodump. "
                "The protagonist must appear within the first 300 characters and the first laugh / warm beat / clear anticipation (not a crisis) within 1000 characters; no prologue, no flashback/POV-switch opening, no worldview lecture; "
                "introduce at most 6 new proper nouns in chapter 1 and at most 5 in each of chapters 2-3 (names, factions, systems, jargon all count); "
                "information release priority: warmth/comedy > golden-finger SHOWN > goal > worldview; no high-intensity conflict before the 2/3 mark; chapter 1 lightly states the protagonist's goal and the book's selling point.\n"
                if _outline_low_pressure
                else
                "[GOLDEN OPENING — chapters 1-3 hard rules] Start at the highest-conflict point; the protagonist must appear within the first 300 characters and the first payoff/expectation beat within 1000 characters; "
                "no prologue, no flashback/POV-switch opening, no weather/scenery opening, no worldview lecture; "
                "introduce at most 6 new proper nouns in chapter 1 and at most 5 in each of chapters 2-3 (names, factions, systems, jargon all count); "
                "information release priority: crisis > characterization > golden-finger hint > worldview; chapter 1 must state the protagonist's goal and the book's selling point.\n"
            )
            + "Each chapter must define title, goal, main_conflict, and hook_description; each scene must define story and emotion tasks. "
            f"Each chapter must also output `target_emotion` — exactly one of [{_emotion_vocab_csv}] — and goal/main_conflict must show how this chapter's conflict delivers that emotion. "
            f"{_tone_contract_line}"
            f"{_logic_coherence_line}"
            f"{_appeal_line}"
            + (
                f"`hook_type` must be chosen from these canonical keys: [{_hook_keys_csv}]; never use the same hook_type in two consecutive chapters (hook_description stays a concrete next event). "
                if _hook_keys_csv
                else ""
            )
            +
            "Each chapter should include `event_cycle_contract`, `chapter_event_role`, and `information_gap_mode`. "
            + (
                "Because the Story Effect Skill Catalog is present, every chapter must include `selected_effect_skills` "
                "with primary, secondary, reason, growth_stage_fit, and expected_contracts; output only the effect "
                "contracts for the selected skills. "
                if _story_effect_catalog_block
                else ""
            )
            +
            "`chapter_event_role` is the chapter's role inside a larger event unit, not a requirement to repeat all six event steps per chapter. "
            "Distribute roles such as trigger, desire_lock, obstacle_escalation, method_search, execution_turn, payoff_feedback, reaction_reset, and bridge_hook across the batch. "
            "Each chapter must include worldview compliance fields: `world_rule_refs` (WorldviewKernel invariant keys or system names used), "
            "`world_rule_landing` (the concrete choice, evidence, cost, or state change that lands the rule), `location_refs`, `faction_refs`, and `key_reveals`. "
            "When the WorldviewKernel includes enhanced contracts, every chapter must also include `world_state_deltas`, "
            "`world_asset_refs`, `authority_claim_refs`, `world_scene_template_ref`, `reveal_weight`, and `anti_copy_boundary_notes`; "
            "asset refs must show visible cost or exposure in `world_rule_landing` or state-delta evidence. "
            "`key_reveals` must obey the WorldviewKernel reveal_ladder and must not expose future reveals early. "
            "The chapter title must be a concrete story image/event, not an internal phase label. "
            "Never use titles like Wake, Threshold, Opening Move, Trace, Pressure, Countermove, Endgame. "
            "Never use placeholder hooks such as 'new evidence, deadline, or cost appears'.\n\n"
            "[DIVERSITY CONSTRAINTS — CRITICAL]\n"
            "1. Each chapter's scene_type combination MUST differ from adjacent chapters — vary scene count, type arrangement, and participant mix.\n"
            "2. Each chapter's main_conflict must be a specific, concrete event — never use vague summaries like 'push the investigation' or 'advance the goal'.\n"
            "3. Character entry_state/exit_state must be tied to the chapter's specific events — never reuse the same arc_state or emotion across multiple chapters.\n"
            "4. Break the narrative rhythm: some chapters end in failure, some focus on quiet character moments, some open with a twist.\n"
            "5. Chapter goals must be concrete, visualizable events — not abstract narrative functions.\n"
            "6. Each chapter must advance at least two concrete state dimensions: plot, relationship, clue, power, resource, status, or exposure risk. "
            "Write the visible change into goal/main_conflict/scenes; never write author notes like 'build worldbuilding', 'introduce a faction', or 'deepen theme'.\n\n"
            "[OUTLINE V2 EXECUTABLE SCRIPT FIELDS — MANDATORY FOR GOLDEN THREE CHAPTERS]\n"
            "Chapters 1-3 MUST include these additional fields (chapters 4+ are encouraged):\n"
            "- protagonist_inner_state: The protagonist's specific inner state at chapter START, "
            "bound to a concrete object or event — NOT generic emotions like 'calm' or 'focused'.\n"
            "- chapter_concrete_actions: 3-5 observable physical actions the protagonist takes "
            "(camera-recordable). NEVER write unobservable actions like 'he thought about it' or 'he felt the threat'.\n"
            "- chapter_object_uses: How each of THIS book's key objects/abilities/devices (per its own "
            "genre & setting — could be a cultivation技, a system panel, a clue object, a social lever, etc.) is used. "
            "Format: 'object: action → result_or_signal'. Must include result (failure counts).\n"
            "- chapter_information_introduced: Specific facts the reader learns by end of chapter "
            "— concrete enough to track on a planning board.\n"
            "- chapter_information_held_back: Facts the author knows but deliberately withholds "
            "— each is a tension gap pulling readers to the next chapter.\n"
            "Scenes should also include concrete_goal, protagonist_state, information_introduced, "
            "information_held_back, object_signal.\n"
            "Each scene MUST ALSO carry concrete, scene-specific (non-generic, non-duplicated) card fields "
            "— these become the writer's contract, so generic placeholders here produce flat prose:\n"
            "- entry_state: {\"summary\": ...} — the protagonist's concrete situation / knowledge / emotional baseline ENTERING the scene.\n"
            "- exit_state: {\"summary\": ...} — the visible state change by the scene's END (MUST differ from entry_state to show progression).\n"
            "- key_dialogue_beats: 2-4 concrete dialogue/action beats for THIS scene (specific intent, not a summary).\n"
            "- sensory_anchors: concrete sensory anchors grounded in image / sound / touch.\n"
            "- hook_requirement: the scene's concrete tail hook (a question / threat / appointment / unfinished action; required especially for a chapter's LAST scene).\n"
            "These fields are evaluated by the commercial quality judge: the judge checks whether "
            "the drafted prose delivers on the capability demonstration and information pacing promised in the outline."
        )
        if is_en
        else (
            f"项目标题：{project.title}\n"
            f"目标章节：{project.target_chapters}\n"
            f"写作画像：\n{render_writing_profile_prompt_block(writing_profile, language=language)}\n"
            f"{_pp_block}"
            f"商业网文硬约束：\n{render_serial_fiction_guardrails(writing_profile, language=language)}\n"
            f"BookSpec 摘要：\n{summarize_book_spec(book_spec, language='zh')}\n"
            f"CastSpec 摘要：\n{summarize_cast_spec(cast_spec, language='zh')}\n"
            f"VolumePlan 摘要：\n{summarize_volume_plan_context(volume_plan, current_volume=1, language='zh')}\n"
            f"{_distilled_outline_block}"
            f"{_story_design_block}\n"
            f"{_emotion_driven_block}\n"
            f"{_entry_system_block}\n"
            f"{_entry_registry_block}\n"
            f"{_character_drama_block}\n"
            f"{_story_effect_catalog_line}"
            f"{_selected_story_effect_contract_line}"
            f"{_pp_outline}"
            f"{_methodology_line}"
            f"{_anti_commonsense_hook_line}"
            f"{_hook_ledger_v2_line}"
            f"{_payoff_ledger_v2_line}"
            f"{_method_cards_hook_line}"
            f"{_golden_opening_line}"
            "请生成完整 ChapterOutlineBatch JSON，包含 batch_name 和 chapters。每章至少 3 个 scenes。"
            "要求：前 3 章必须快速完成主角卖点亮相、核心异常亮相、第一轮得失与追读钩子，"
            "并严格执行上方【黄金三章硬约束】：主角300字内登场、1000字内出现第一个爽点或期待点、"
            "禁止序章/插叙/大段世界观解说、新专有名词不超上限、第1章点明主角目标与本书卖点；"
            "每章都要写明 title、goal、main_conflict、hook_description；每场都要有 story/emotion 任务。"
            f"每章必须写明 target_emotion：从受控词表【{_emotion_vocab_csv}】中只选一个，"
            "并在 goal/main_conflict 中写清该情绪如何通过本章冲突交付（先定情绪再定故事）；"
            f"{_tone_contract_line}"
            f"{_logic_coherence_line}"
            f"{_appeal_line}"
            "hook_type 必须从上方「章尾钩子13式」的 key 中选型，连续两章不得重复同一 hook_type，"
            "hook_description 仍写具体下一步事件。"
            "每章建议包含 `event_cycle_contract`、`chapter_event_role`、`information_gap_mode`。"
            + (
                "因【故事效果 Skill 清单】已存在，每章必须输出 `selected_effect_skills`，包含 "
                "primary、secondary、reason、growth_stage_fit、expected_contracts；只输出被选中 skill 的效果合同。"
                if _story_effect_catalog_block
                else ""
            )
            +
            "`chapter_event_role` 表示本章在更大事件单元中的职责，不是要求每章完整复刻六步。"
            "请在批次中分配 trigger、desire_lock、obstacle_escalation、method_search、execution_turn、payoff_feedback、reaction_reset、bridge_hook 等角色。"
            "每章必须包含世界观合规字段：`world_rule_refs`（使用到的 WorldviewKernel invariant key 或 system name）、"
            "`world_rule_landing`（让规则落地的具体选择、证据、代价或状态变化）、`location_refs`、`faction_refs`、`key_reveals`。"
            "当 WorldviewKernel 含有增强契约时，每章还必须输出 `world_state_deltas`、`world_asset_refs`、"
            "`authority_claim_refs`、`world_scene_template_ref`、`reveal_weight`、`anti_copy_boundary_notes`；"
            "凡引用 asset，必须在 `world_rule_landing` 或状态变量 evidence 中写出可见 cost/exposure。"
            "`key_reveals` 必须遵守 WorldviewKernel reveal_ladder，禁止提前揭露未来真相。"
            "title 必须是 2-6 字的具体故事意象/事件名，优先使用器物、地点、人名、规则或异常现象；"
            "严禁把内部功能词当标题，禁止使用「初现、入局、投石、试探、铺火、露锋、破冰、起手、掀幕、落子、追索、摸底、拆解、寻隙、探针、回查、溯源、揭层、织网、破壁」这类模板尾词。"
            "【标题格式硬禁止】title 禁止写成「功能词·具体事」两段式（例如『取证·某地某物登记』『反证·某人某事签名』这类用办案功能词作前缀），"
            "禁止使用「·」「・」「•」等分隔符把办案/结构功能标签接到事件上；"
            "禁止用「取证、举证、质证、反证、勘验、验尸、立案、结案、破案、追凶、对峙、审讯、布局、收网、复盘、推演、开局、终局、楔子、引子、伏笔、转折、高潮、反转」这类节拍/办案功能词当标题或标题前缀。"
            "title 只写这一章发生的那件具体的事本身（器物/地点/人名/异象），不写它在办案流程里的职责。"
            "hook_description 必须是具体下一步事件，禁止写「围绕某冲突出现新的证据、时限或代价」这类占位句。\n\n"
            "【多样性硬约束——极其重要】\n"
            "1. 每章的 scene_type 组合不得与前后两章雷同，必须有结构差异（场景数、类型排列、参与角色组合都要变化）。\n"
            "2. 每章的 main_conflict 必须是独立的、具体的事件描述，禁止使用「推进调查」「承压推进」等泛化概括。\n"
            "3. 角色的 entry_state/exit_state 必须紧扣本章具体事件，禁止多章复用相同的 arc_state 或 emotion。\n"
            "4. 避免所有章节都遵循相同的叙事模式（如每章都是「发现线索→遭遇阻碍→获得突破」），"
            "要主动打破节奏：有的章以失败结尾，有的章以安静的人物关系推进为主，有的章以反转开场。\n"
            "5. chapter goal 必须是具体的、可视化的事件，不能是抽象的叙事功能描述。\n"
            "6. 每章必须至少推进两个真实状态维度：剧情、关系、线索、能力、资源、身份/地位、暴露风险；"
            "这种变化必须写进 goal/main_conflict/scenes，禁止写「建立世界观」「引入势力」「完善体系」「深化主题」这类作者笔记。\n\n"
            "【大纲 v2 可执行脚本字段——黄金三章强制输出】\n"
            "前 3 章的每章必须额外输出以下字段（第 4 章起建议输出）：\n"
            "- protagonist_inner_state: 主角在本章开头的具体内心状态，必须绑定一个具体物件或事件，"
            "不能是「镇定」「冷静」等通用情绪词。\n"
            "- chapter_concrete_actions: 主角在本章实际完成的 3-5 个可观察动作（摄像机能拍到的），"
            "每条用动词开头。禁止写「主角思考了一下」「他感受到了威胁」等不可观察的行为。\n"
            "- chapter_object_uses: 每个超自然/职业物件的使用方式，格式：「物件: 动作 → 结果或信号」。"
            "必须包含结果（失败也算），不能只写「使用了某件关键物」。\n"
            "- chapter_information_introduced: 读者在本章结束后知道的具体事实，每条可以写进侦探白板的线索。\n"
            "- chapter_information_held_back: 作者知道但本章故意不告诉读者的事实，每条是下一章节的悬念来源。\n"
            "每个场景也应包含 concrete_goal、protagonist_state、information_introduced、information_held_back、object_signal。\n"
            "每个场景还必须给出具体的场景卡字段（这些字段就是写手的写作契约，写套话/与其它场景雷同会直接导致正文变流水账）：\n"
            "- entry_state: {\"summary\": ...}：进入本场景时主角的具体处境 / 掌握的信息 / 情绪底色。\n"
            "- exit_state: {\"summary\": ...}：本场景结束时发生的可见状态变化（必须与 entry_state 不同，体现推进）。\n"
            "- key_dialogue_beats: 2-4 条本场景的关键对白或动作拍子（具体台词意图，不要概述）。\n"
            "- sensory_anchors: 本场景的具体感官锚点（落到画面 / 声音 / 触感）。\n"
            "- hook_requirement: 本场景结尾的具体钩子（问题 / 威胁 / 约定 / 未完成动作之一；章末场景尤其必须有）。\n"
            "这些字段是商业质量评测的核心依据：裁判模型会检查正文是否兑现了大纲里承诺的能力实证和信息节奏。"
        )
    )
    _genre_instruction = getattr(
        _genre_profile.planner_prompts, f"outline_instruction_{_lang_key}", ""
    )
    if _genre_instruction:
        user_prompt += f"\n\n{'[Genre planning requirements]' if is_en else '【品类规划要求】'}\n{_genre_instruction}"
    user_prompt = _append_category_context(user_prompt, project, is_en=is_en)
    return system_prompt, user_prompt


def _build_deceased_character_constraints(
    cast_spec: dict[str, Any],
    volume_entry: dict[str, Any],
    language: str = "zh-CN",
) -> list[str]:
    """Extract characters who are already dead at the start of this volume.

    Returns constraint strings preventing the planner from assigning
    deceased characters as active scene participants in chapters that
    precede their death chapter.

    This prevents ``character_resurrection`` safety-block errors at the
    scene-contract check by embedding the timeline constraint directly
    in the outline-generation prompt rather than patching them after
    the fact.

    ``cast_spec`` follows the ``CastSpecInput`` schema:
    ``{protagonist: <character> | None, antagonist: <character> | None,
    supporting_cast: [<character>, ...]}``. Each character is a dict
    (post ``model_dump``) and may carry ``death_chapter_number`` either
    as a top-level key (via ``CharacterInput.model_config = extra="allow"``)
    or inside ``metadata``.
    """
    constraints: list[str] = []
    cast_data = _mapping(cast_spec)

    # Collect every named character in the cast spec — the schema stores
    # protagonist / antagonist as single objects (not list-with-``characters``)
    # and supporting_cast as a flat list of character dicts.
    characters: list[dict[str, Any]] = []
    protag = cast_data.get("protagonist")
    if isinstance(protag, dict):
        characters.append(protag)
    antag = cast_data.get("antagonist")
    if isinstance(antag, dict):
        characters.append(antag)
    supporting = cast_data.get("supporting_cast") or []
    if isinstance(supporting, list):
        for sc in supporting:
            if isinstance(sc, dict):
                characters.append(sc)

    volume_number = int(volume_entry.get("volume_number", 1))
    vol_start_ch = int(volume_entry.get("start_chapter_number", 1))

    for char in characters:
        meta = char.get("metadata") if isinstance(char.get("metadata"), dict) else {}
        death_ch_raw = char.get("death_chapter_number")
        if death_ch_raw is None and isinstance(meta, dict):
            death_ch_raw = meta.get("death_chapter_number")
        if death_ch_raw is None:
            continue
        try:
            death_ch = int(death_ch_raw)
        except (TypeError, ValueError):
            continue
        if death_ch < vol_start_ch:
            name = str(char.get("name") or "Unknown")
            if language == "zh-CN":
                constraints.append(
                    f"角色「{name}」已在第{death_ch}章死亡，不能作为活跃角色出现在第{volume_number}卷的场景/章节中（禁止复活）"
                )
            else:
                constraints.append(
                    f"Character「{name}」died in chapter {death_ch} and cannot appear as an active participant in volume {volume_number} scenes/chapters (no resurrection)."
                )
    return constraints


# Soft cap on how many prior titles to surface in the outline prompt.
# 200 covers ~10 volumes of a 20-chapter cadence; well above the empirical
# n-gram-repeat distance we saw in audits (collisions clustered around
# 30-50 chapters apart). Prompts stay bounded regardless of book length.
_OUTLINE_TITLE_CONTEXT_MAX = 200


def _render_existing_titles_block(
    existing_titles: list[tuple[int | None, str]] | None,
) -> tuple[str, str]:
    """Render a ``(english, chinese)`` "DO NOT REPEAT" prompt block.

    Returns ``("", "")`` when ``existing_titles`` is empty/None so the
    calling prompt can splice it in unconditionally.

    Only the most recent :data:`_OUTLINE_TITLE_CONTEXT_MAX` titles are
    surfaced. The planner is more likely to re-emit a recently-used
    pattern than something from 500 chapters ago; capping the list keeps
    prompt size bounded.

    The list is rendered with chapter numbers so the planner can refer
    back to specific volumes when re-prompted by the repair loop.
    """

    if not existing_titles:
        return "", ""

    tail = existing_titles[-_OUTLINE_TITLE_CONTEXT_MAX:]
    if not tail:
        return "", ""

    en_lines: list[str] = []
    zh_lines: list[str] = []
    for chapter_number, title in tail:
        cn_label = f"ch{chapter_number}" if chapter_number is not None else "ch?"
        en_lines.append(f"  {cn_label}: {title}")
        zh_lines.append(
            f"  第{chapter_number}章：{title}" if chapter_number is not None else f"  ?: {title}"
        )

    en_block = (
        "[DO NOT REPEAT — titles already used in earlier volumes of this novel]\n"
        f"(showing most recent {len(tail)})\n" + "\n".join(en_lines) + "\n\n"
    )
    zh_block = (
        "【请勿重复 —— 本书前几卷已用的章节标题】\n"
        f"（仅展示最近 {len(tail)} 条）\n" + "\n".join(zh_lines) + "\n\n"
    )
    return en_block, zh_block


def _volume_outline_prompts(
    project: ProjectModel,
    book_spec: dict[str, Any],
    cast_spec: dict[str, Any],
    volume_plan: list[dict[str, Any]],
    volume_entry: dict[str, Any],
    *,
    revealed_ledger_block: str | None = None,
    extra_constraints: list[str] | None = None,
    existing_titles: list[tuple[int | None, str]] | None = None,
) -> tuple[str, str]:
    """Prompts for generating chapter outlines for a single volume.

    ``revealed_ledger_block``: optional pre-rendered block from
    :func:`bestseller.services.revealed_ledger.build_revealed_ledger`. When
    provided, the planner sees an up-to-date summary of already-revealed
    facts, overused hook_types, and recurring beat phrases so it does not
    re-reveal or replay them in the new volume's outline.

    Prior-volume summary: computed from ``volume_plan`` using
    :func:`bestseller.services.volume_fingerprint.render_prior_volumes_summary_block`
    so the model sees explicitly what earlier volumes already claimed —
    goal, obstacle, conflict_phase, primary_force_name — and is forced to
    diverge on the axis of differentiation.

    ``existing_titles``: optional sequence of ``(chapter_number, title)``
    pairs already persisted for this project. When provided we attach a
    "do-not-repeat" block to the prompt listing the most recent titles so
    the planner stops cycling through ``铁壁破壁`` / ``回声追索`` /
    ``Cipher Crossing``-style templates volume after volume. The list is
    capped (most recent ~200 entries) to keep prompt size bounded
    regardless of how many chapters the book already has.
    """
    from bestseller.services.genre_review_profiles import resolve_genre_review_profile
    from bestseller.services.volume_fingerprint import (
        render_prior_volumes_summary_block,
    )

    language = _planner_language(project)
    is_en = is_english_language(language)
    _lang_key = "en" if is_en else "zh"
    writing_profile = _planner_writing_profile(project)
    prompt_pack = _planner_prompt_pack(project)
    from bestseller.services.invariants import is_low_pressure_tone as _is_low_pressure_tone

    _proj_meta = project.metadata_json if isinstance(project.metadata_json, dict) else {}
    _outline_low_pressure = _is_low_pressure_tone(
        getattr(prompt_pack, "key", None) or _proj_meta.get("prompt_pack_key"),
        project.genre,
        project.sub_genre,
    )
    _genre_profile = resolve_genre_review_profile(project.genre, project.sub_genre)
    _genre_system = getattr(_genre_profile.planner_prompts, f"outline_system_{_lang_key}", "")
    volume_number = int(volume_entry.get("volume_number", 1))
    chapter_count = int(volume_entry.get("chapter_count_target", 10))
    compact_outline_mode = project.target_chapters <= 12 or chapter_count <= 12
    short_complete_outline_mode = compact_outline_mode or (
        project.target_chapters <= 60 and chapter_count >= 12
    )
    if short_complete_outline_mode:
        scene_count_contract_en = (
            "Each chapter needs 2 compact scenes by default; use 3 only for climax, reversal, "
            "or finale chapters. Keep each scene purpose to one short sentence. "
        )
        scene_count_contract_zh = (
            "每章默认只需 2 个紧凑 scenes；只有高潮、反转或终章可用 3 个。"
            "每个 scene 的 purpose 控制在一句短句内。"
        )
        count_safety_en = (
            "[COUNT SAFETY — NON-NEGOTIABLE]\n"
            f"- Emit exactly {chapter_count} chapter objects in `chapters`.\n"
            "- Include every required chapter_number for this volume; the last object must be the final chapter in the requested range.\n"
            "- If the response is getting long, shorten strings and scene details; never reduce, merge, summarize, group, or omit chapters.\n"
            "- Do not write overview sections instead of chapter objects.\n"
        )
        count_safety_zh = (
            "【章节数量安全线 —— 不可让步】\n"
            f"- `chapters` 必须恰好输出 {chapter_count} 个章节对象。\n"
            "- 必须包含本卷要求范围内的每一个 chapter_number；最后一个对象必须是本卷范围内的最终章。\n"
            "- 如果输出变长，只能压缩字段文字和 scene 细节，绝不能减少、合并、概括、分组或遗漏章节。\n"
            "- 禁止用总览段落替代逐章对象。\n"
        )
    else:
        scene_count_contract_en = "Each chapter needs at least 3 scenes. "
        scene_count_contract_zh = "每章至少 3 个 scenes。"
        count_safety_en = ""
        count_safety_zh = ""
    chapter_bounds = _derive_volume_chapter_bounds(_mapping(volume_entry))
    if chapter_bounds is not None:
        chapter_start, chapter_end = chapter_bounds
        chapter_bounds_line_en = (
            f"Use global chapter_number values {chapter_start}-{chapter_end} only; "
            f"do not generate chapter {chapter_end + 1} or later. "
        )
        chapter_bounds_line_zh = (
            f"全局章节号必须落在第{chapter_start}-{chapter_end}章；"
            f"不能生成第{chapter_end + 1}章及以后的内容。"
        )
    else:
        chapter_bounds_line_en = ""
        chapter_bounds_line_zh = ""
    # ── Webnovel method cards (design-layer methodology, baked upstream) ──
    # Stage-aware hook taxonomy + target_emotion discipline; golden-opening
    # rules only for batches covering chapters 1-3. Soft-degrades to "".
    _method_cards_hook_line = ""
    _golden_opening_line = ""
    _tone_contract_line = ""
    _logic_coherence_line = ""
    _appeal_line = ""
    _emotion_vocab_csv = "爽、燃、暖、虐、悬疑、紧张、轻松、甜、震撼"
    _hook_keys_csv = ""
    _covers_golden_opening = (
        chapter_bounds[0] <= 3 if chapter_bounds is not None else volume_number == 1
    )
    try:
        from bestseller.services.quality_levers.webnovel_method_cards import (
            chapter_end_hook_keys,
            render_golden_opening_rules_block,
            render_outline_hook_taxonomy_block,
            render_logic_coherence_contract_block,
            render_opening_pull_contract_block,
            render_front_ten_retention_contract_block,
            render_tone_emotion_contract_block,
            target_emotion_vocabulary,
        )

        _vocab = target_emotion_vocabulary()
        if _vocab:
            _emotion_vocab_csv = "、".join(_vocab)
        _hook_keys_csv = ", ".join(chapter_end_hook_keys())
        _tone_block = render_tone_emotion_contract_block(
            _mapping(book_spec).get("tone"), language="en" if is_en else "zh"
        )
        _tone_contract_line = f"\n{_tone_block}\n" if _tone_block else ""
        _logic_coherence_line = "\n" + render_logic_coherence_contract_block(
            language="en" if is_en else "zh"
        ) + "\n"
        _appeal_line = (
            "\n"
            + render_opening_pull_contract_block(language="en" if is_en else "zh")
            + "\n"
            + render_front_ten_retention_contract_block(
                language="en" if is_en else "zh"
            )
            + "\n"
        )
        _total_volumes = max(
            [
                int(_mapping(v).get("volume_number") or 0)
                for v in (volume_plan or [])
                if isinstance(v, dict)
            ]
            or [volume_number]
        )
        if volume_number <= 1:
            _method_stage = "opening"
        elif volume_number >= _total_volumes:
            _method_stage = "finale"
        elif volume_number == _total_volumes - 1:
            _method_stage = "pre_climax"
        else:
            _method_stage = "middle"
        if not is_en:
            _method_block = render_outline_hook_taxonomy_block(_method_stage)
            _method_cards_hook_line = f"\n{_method_block}\n" if _method_block else ""
            if _covers_golden_opening:
                _golden_block = render_golden_opening_rules_block(
                    low_pressure=_outline_low_pressure
                )
                _golden_opening_line = f"\n{_golden_block}\n" if _golden_block else ""
    except Exception:
        logger.debug(
            "Webnovel method-cards block injection failed (non-fatal)",
            exc_info=True,
        )
    system_prompt = (
        "You are a chapter-outline planner for long-form commercial fiction. Output valid JSON only."
        if is_en
        else "你是长篇中文小说章纲规划师。输出必须是合法 JSON，不要解释。"
    )
    if _genre_system:
        system_prompt += f"\n{_genre_system}"
    _pp_block = (
        ""
        if compact_outline_mode
        else f"Prompt Pack：\n{render_prompt_pack_prompt_block(prompt_pack)}\n"
        if prompt_pack
        else ""
    )
    _pp_outline = "" if compact_outline_mode else _planner_fragment_or_ref(
        prompt_pack,
        project,
        "planner_outline",
    )
    _methodology_planner_block = render_methodology_phase_block(
        prompt_pack,
        phase="planner",
    )
    _methodology_line = (
        ""
        if compact_outline_mode
        else f"\n{_methodology_planner_block}\n"
        if _methodology_planner_block
        else ""
    )
    _story_package_block = "" if compact_outline_mode else _story_package_prompt_block(
        project,
        language=language,
    )
    _story_design_block = (
        _compact_outline_context_block(project)
        if compact_outline_mode
        else _story_design_kernel_prompt_block(project)
    )
    _emotion_driven_block = (
        _compact_prompt_block_text(_emotion_driven_kernel_prompt_block(project), max_chars=1000)
        if compact_outline_mode
        else _emotion_driven_kernel_prompt_block(project)
    )
    _public_emotion_block = (
        _compact_prompt_block_text(_public_emotion_kernel_prompt_block(project), max_chars=800)
        if compact_outline_mode
        else _public_emotion_kernel_prompt_block(project)
    )
    _compliance_boundary_block = "" if compact_outline_mode else _compliance_boundary_prompt_block(project)
    _entry_system_block = "" if compact_outline_mode else _entry_system_kernel_prompt_block(project)
    _entry_registry_block = "" if compact_outline_mode else _entry_registry_prompt_block(project)
    _character_drama_block = (
        _compact_prompt_block_text(
            _character_drama_prompt_block(project, cast_spec=cast_spec),
            max_chars=1200,
        )
        if compact_outline_mode
        else _character_drama_prompt_block(project, cast_spec=cast_spec)
    )
    _story_effect_catalog_block = render_story_effect_skill_catalog_prompt_block(
        project.metadata_json if isinstance(project.metadata_json, dict) else {},
        language=language,
    )
    _story_effect_catalog_line = (
        f"\n{_story_effect_catalog_block}\n" if _story_effect_catalog_block else ""
    )
    _selected_story_effect_contracts = render_selected_story_effect_skill_contracts(
        project.metadata_json if isinstance(project.metadata_json, dict) else {},
        language=language,
        stage="chapter_outline",
    )
    _selected_story_effect_contract_line = (
        f"\n{_selected_story_effect_contracts}\n"
        if _selected_story_effect_contracts
        else ""
    )
    _distilled_outline_block = (
        _compact_prompt_block_text(
            _distilled_design_reference_block(project, "chapter_outline"),
            max_chars=1200,
        )
        if compact_outline_mode
        else _distilled_design_reference_block(project, "chapter_outline")
    )
    _ledger_line = f"{revealed_ledger_block}\n\n" if revealed_ledger_block else ""
    _hook_ledger_v2_block = render_hook_ledger_planner_contract(language=language)
    _hook_ledger_v2_line = (
        ""
        if compact_outline_mode
        else f"\n{_hook_ledger_v2_block}\n"
        if _hook_ledger_v2_block
        else ""
    )
    _payoff_ledger_v2_block = render_payoff_ledger_planner_contract(language=language)
    _payoff_ledger_v2_line = (
        ""
        if compact_outline_mode
        else f"\n{_payoff_ledger_v2_block}\n"
        if _payoff_ledger_v2_block
        else ""
    )
    from bestseller.services.hook_propagation import (
        hook_outline_extra_constraints,
        hook_spec_from_metadata,
        render_hook_spec_prompt_block,
    )

    _hook_spec = hook_spec_from_metadata(
        project.metadata_json if isinstance(project.metadata_json, dict) else {}
    )
    _anti_commonsense_hook_block = render_hook_spec_prompt_block(
        _hook_spec,
        language=language,
    )
    _anti_commonsense_hook_line = (
        ""
        if compact_outline_mode
        else f"\n{_anti_commonsense_hook_block}\n"
        if _anti_commonsense_hook_block
        else ""
    )
    _concept_lab_contract_line = (
        _compact_concept_lab_contract_block(project, language=language)
        if compact_outline_mode
        else _concept_lab_contract_block(project, language=language)
    )
    _story_enhancer_line = _story_enhancer_contract_line(project, language)
    _prior_vols_block = render_prior_volumes_summary_block(
        volume_plan,
        current_volume_number=volume_number,
        language=language,
    )
    _prior_vols_line = f"{_prior_vols_block}\n\n" if _prior_vols_block else ""
    _existing_titles_block_en, _existing_titles_block_zh = _render_existing_titles_block(
        existing_titles
    )
    _existing_titles_line = _existing_titles_block_en if is_en else _existing_titles_block_zh
    vol_plan_summary = summarize_volume_plan_context(
        volume_plan, current_volume=volume_number, language=language
    )
    if compact_outline_mode:
        _writing_profile_text = (
            "Writing profile: compact commercial opening validation; keep chapters executable.\n"
            if is_en
            else "写作画像：短篇开局验证；章纲只保留可执行剧情动作。\n"
        )
        _serial_guardrails_text = ""
    else:
        _writing_profile_text = (
            f"Writing profile:\n{render_writing_profile_prompt_block(writing_profile, language=language)}\n"
            if is_en
            else f"写作画像：\n{render_writing_profile_prompt_block(writing_profile, language=language)}\n"
        )
        _serial_guardrails_text = (
            f"Serial fiction guardrails:\n{render_serial_fiction_guardrails(writing_profile, language=language)}\n"
            if is_en
            else f"商业网文硬约束：\n{render_serial_fiction_guardrails(writing_profile, language=language)}\n"
        )
    user_prompt = (
        (
            f"Project title: {project.title}\n"
            f"Volume {volume_number} — {chapter_count} chapters\n"
            "Write all planning artifacts in English.\n"
            f"{_writing_profile_text}"
            f"{_pp_block}"
            f"{_serial_guardrails_text}"
            f"BookSpec summary:\n{summarize_book_spec(book_spec, language='en')}\n"
            f"CastSpec summary:\n{summarize_cast_spec(cast_spec, language='en', volume_number=volume_number)}\n"
            f"VolumePlan context:\n{vol_plan_summary}\n"
            f"{_story_package_block}\n"
            f"{_distilled_outline_block}"
            f"{_story_design_block}\n"
            f"{_emotion_driven_block}\n"
            f"{_public_emotion_block}\n"
            f"{_compliance_boundary_block}\n"
            f"{_entry_system_block}\n"
            f"{_entry_registry_block}\n"
            f"{_character_drama_block}\n"
            f"{_story_effect_catalog_line}"
            f"{_selected_story_effect_contract_line}"
            f"{_prior_vols_line}"
            f"{_ledger_line}"
            f"{_existing_titles_line}"
            f"{_pp_outline}"
            f"{_methodology_line}"
            f"{_concept_lab_contract_line}"
            f"{_story_enhancer_line}"
            f"{_anti_commonsense_hook_line}"
            f"{_hook_ledger_v2_line}"
            f"{_payoff_ledger_v2_line}"
            f"Generate a ChapterOutlineBatch JSON for volume {volume_number} ONLY ({chapter_count} chapters). "
            f"The chapters array must contain exactly {chapter_count} objects, one per chapter, with no summaries or grouped chapters. "
            f"{chapter_bounds_line_en}"
            f"{count_safety_en}"
            f"Include batch_name and chapters. {scene_count_contract_en}"
            "Each chapter must define title, goal, main_conflict, and hook_description; each scene must define story and emotion tasks. "
            f"Each chapter must also output `target_emotion` — exactly one of [{_emotion_vocab_csv}] — and goal/main_conflict must show how this chapter's conflict delivers that emotion. "
            f"{_tone_contract_line}"
            f"{_logic_coherence_line}"
            f"{_appeal_line}"
            + (
                f"`hook_type` must be chosen from these canonical keys: [{_hook_keys_csv}]; never use the same hook_type in two consecutive chapters. "
                if _hook_keys_csv
                else ""
            )
            + (
                (
                    "[GOLDEN OPENING — chapters 1-3 hard rules, low-pressure comedy/healing] Open on a warm, concrete present-day moment (an ordinary day disrupted / an enviable small joy / dropped into a homey unfamiliar setting); NEVER open on a crisis, countdown, or threat. "
                    "The golden finger / system MUST be SHOWN through a concrete, ideally funny event — never have a system / customer-service voice / AI / narrator recite its own rules as a page-one infodump. "
                    "The protagonist must appear within the first 300 characters and the first laugh / warm beat / clear anticipation (not a crisis) within 1000 characters; no prologue, no flashback/POV-switch opening, no worldview lecture; "
                    "introduce at most 6 new proper nouns in chapter 1 and at most 5 in each of chapters 2-3 (names, factions, systems, jargon all count); "
                    "information release priority: warmth/comedy > golden-finger SHOWN > goal > worldview; no high-intensity conflict before the 2/3 mark; chapter 1 lightly states the protagonist's goal and the book's selling point. "
                    if _outline_low_pressure
                    else
                    "[GOLDEN OPENING — chapters 1-3 hard rules] Start at the highest-conflict point; the protagonist must appear within the first 300 characters and the first payoff/expectation beat within 1000 characters; "
                    "no prologue, no flashback/POV-switch opening, no weather/scenery opening, no worldview lecture; "
                    "introduce at most 6 new proper nouns in chapter 1 and at most 5 in each of chapters 2-3 (names, factions, systems, jargon all count); "
                    "information release priority: crisis > characterization > golden-finger hint > worldview; chapter 1 must state the protagonist's goal and the book's selling point. "
                )
                if _covers_golden_opening
                else ""
            )
            +
            "Each chapter must include causal_contract with flexible reader-visible axes: chapter_function, pressure, protagonist_desire, protagonist_choice, visible_action_or_reaction, resistance, cost_or_tradeoff, gain_or_reveal, state_change, next_reader_desire. "
            "Each chapter must include a non-empty `opening_situation`: the concrete in-scene pressure the protagonist is already facing as the chapter opens — no recap, no scenery warm-up. "
            "[CHAPTER 1 OPENING — HARD RULE] opening_situation must land on ONE specific person in ONE instantly-legible visceral jeopardy or burning want (bodily danger / public humiliation / a loved one in peril / a want that hurts). Do NOT make the chapter-1 opening pressure an abstract institutional/accounting/property/rule/transaction dispute (account deadlines, seals, disputed ownership, bylaws may be background only, never the chapter-1 hook). "
            "[COLD-READER ANCHORS — HARD RULE] Assume the reader has NOT seen the blurb. Within the first 800 characters of chapter 1 the reader must learn, through action/dialogue: (1) who the protagonist is and their situation; (2) what this place/predicament is; (3) the FULL content of whatever rule governs this chapter's life-or-death stakes — that rule must be completely visible to the reader BEFORE it first fires (never name-drop a rule without stating it); (4) when the cheat/golden-finger first activates, the reader must be able to state in one sentence what it is and does; (5) by chapter end the reader can tally what the protagonist won and what it cost. Weave anchors into action and dialogue, never narrator lecture. Everything beyond these required anchors — worldbuilding and secondary private jargon — is deferred; any private term's first appearance carries a plain-language gloss in the same sentence; at most 3 named characters on the first screen. "
            "Each scene's `participants` must name every on-page character from the cast list. A chapter must not consist solely of protagonist-only scenes: whenever the cast allows, give at least one scene a second named participant (opponent, ally, or pressure source). "
            "Each chapter must include chapter-level `methodology_contract`: conflict_stakes, conflict_buffs, hooks_to_resolve, hooks_to_plant, relationship_debts (unresolved interpersonal promises/tensions to advance — NOT financial or ledger debt), pacing_mode, emotion_phase, is_climax, loop_position. "
            "Do not put scene camera/reveal/cut fields in chapter methodology_contract, and do not put story-level ability_origin_contract or recognition_anchors in chapters. "
            "Each scene must include scene-level `methodology_contract`: conflict_stakes, conflict_buffs, hook_type, spotlight_character, information_control_mode, camera_distance, reveal_mode, signature_image, cut_point, action_sequence, relationship_debts (interpersonal promises/tensions, never ledger framing). "
            "Do not put chapter-level pacing/climax/loop fields or story-level ability/recognition fields in scene methodology_contract. "
            "Each chapter should also include `event_cycle_contract`, `chapter_event_role`, and `information_gap_mode`; the role is this chapter's contribution to a larger event unit, not a full six-step template repeated in every chapter. "
            "Across the volume, distribute roles such as trigger, desire_lock, obstacle_escalation, method_search, execution_turn, payoff_feedback, reaction_reset, and bridge_hook to prevent homogeneous chapters. "
            "Each chapter must also include worldview compliance fields: `world_rule_refs` (WorldviewKernel invariant keys or system names used), "
            "`world_rule_landing` (the concrete choice, evidence, cost, or state change that lands the rule), `location_refs`, `faction_refs`, and `key_reveals`. "
            "When the WorldviewKernel includes enhanced contracts, each chapter must also include `world_state_deltas`, "
            "`world_asset_refs`, `authority_claim_refs`, `world_scene_template_ref`, `reveal_weight`, and `anti_copy_boundary_notes`; "
            "asset refs must show visible cost or exposure in `world_rule_landing` or state-delta evidence. "
            "`key_reveals` must obey the WorldviewKernel reveal_ladder and must not expose future reveals early. "
            "Do not force these axes into a fixed prose order; use them to prove the chapter has causal movement and a next-chapter desire. "
            # ── TITLE CONTRACT (rewritten 2026-05) ────────────────────
            # Old prompt only said "title must be concrete" + a small
            # negative list. The model still drifted into "noun + function
            # suffix" templates (Cipher Crossing, Storm Probe). We now
            # require positive extraction from the chapter's own content.
            "[CHAPTER TITLE CONTRACT — STRICT]\n"
            "1. Each `title` MUST be 2-6 words extracted from THIS chapter's own concrete elements: "
            "a named object, a named person, a specific place, a specific action result, a quoted phrase, or a unique image present in `main_conflict` / `hook_description` / scenes.\n"
            "2. A title MUST NOT be a 'noun + function-suffix' template "
            "(e.g. anything ending in 'Probe', 'Trace', 'Drop', 'Snap', 'Test', 'Crossing', 'Faultline', 'Cipher', 'Beacon', 'Reveal' when used as a generic suffix).\n"
            "3. A title MUST be unique within this entire novel — see the "
            "DO-NOT-REPEAT block above for titles already used in earlier volumes; "
            "do not propose any title equal to or near-equivalent of those.\n"
            "4. If a chapter's content does not yield a unique concrete title, "
            "rewrite the chapter's `main_conflict` / `hook_description` to be more specific first, then derive the title from that rewrite.\n"
            "Never use placeholder hooks such as 'new evidence, deadline, or cost appears'. "
            "Each chapter must advance at least two concrete state dimensions: plot, relationship, clue, power, resource, status, or exposure risk. "
            "Write the visible change into goal/main_conflict/scenes; never write author notes like 'build worldbuilding', 'introduce a faction', or 'deepen theme'."
        )
        if is_en
        else (
            f"项目标题：{project.title}\n"
            f"第{volume_number}卷 — 共{chapter_count}章\n"
            f"{_writing_profile_text}"
            f"{_pp_block}"
            f"{_serial_guardrails_text}"
            f"BookSpec 摘要：\n{summarize_book_spec(book_spec, language='zh')}\n"
            f"CastSpec 摘要：\n{summarize_cast_spec(cast_spec, language='zh', volume_number=volume_number)}\n"
            f"VolumePlan 上下文：\n{vol_plan_summary}\n"
            f"{_story_package_block}\n"
            f"{_distilled_outline_block}"
            f"{_story_design_block}\n"
            f"{_emotion_driven_block}\n"
            f"{_public_emotion_block}\n"
            f"{_compliance_boundary_block}\n"
            f"{_entry_system_block}\n"
            f"{_entry_registry_block}\n"
            f"{_character_drama_block}\n"
            f"{_story_effect_catalog_line}"
            f"{_selected_story_effect_contract_line}"
            f"{_prior_vols_line}"
            f"{_ledger_line}"
            f"{_existing_titles_line}"
            f"{_pp_outline}"
            f"{_methodology_line}"
            f"{_concept_lab_contract_line}"
            f"{_story_enhancer_line}"
            f"{_anti_commonsense_hook_line}"
            f"{_hook_ledger_v2_line}"
            f"{_payoff_ledger_v2_line}"
            f"{_method_cards_hook_line}"
            f"{_golden_opening_line}"
            f"请仅生成第{volume_number}卷的 ChapterOutlineBatch JSON（共{chapter_count}章），"
            f"chapters 数组必须恰好包含 {chapter_count} 个章节对象，一章一个对象，不能概括、合并或分组，"
            f"{chapter_bounds_line_zh}"
            f"{count_safety_zh}"
            f"包含 batch_name 和 chapters。{scene_count_contract_zh}"
            "每章都要写明 title、goal、main_conflict、hook_description；每场都要有 story/emotion 任务。"
            f"每章必须写明 target_emotion：从受控词表【{_emotion_vocab_csv}】中只选一个，"
            "并在 goal/main_conflict 中写清该情绪如何通过本章冲突交付（先定情绪再定故事）；"
            f"{_tone_contract_line}"
            f"{_logic_coherence_line}"
            f"{_appeal_line}"
            "hook_type 必须从上方「章尾钩子13式」的 key 中选型，连续两章不得重复同一 hook_type，"
            "hook_description 仍写具体下一步事件。"
            + (
                "本批次覆盖第1-3章，必须严格执行上方【黄金三章硬约束】："
                "主角300字内登场、1000字内出现第一个爽点或期待点、"
                "禁止序章/插叙/大段世界观解说、新专有名词不超上限、第1章点明主角目标与本书卖点。"
                if _covers_golden_opening
                else ""
            )
            +
            "每章必须包含 causal_contract：chapter_function、pressure、protagonist_desire、protagonist_choice、visible_action_or_reaction、resistance、cost_or_tradeoff、gain_or_reveal、state_change、next_reader_desire。"
            "每章必须写明非空的 opening_situation：开章即事中，写清主角此刻正面对的现场压力，不得用回顾或氛围铺垫开场。"
            "【第一章开篇·硬要求】opening_situation 必须落在一个具体的人 + 一个一眼看懂的切肤危机或强烈渴望上"
            "（身体危险 / 被当众羞辱 / 亲近之人有难 / 一个烧心的想要）；严禁把第一章开篇压力写成抽象的"
            "制度·账目·产权·规则·交易纠纷（账期、封条、产权存疑、条例这类官僚设定只能作后景，不得当第一章钩子）。"
            "【新读者锚点·硬要求】默认读者没看过简介。第一章前 800 字内，读者必须从动作/对话中自然得知："
            "①主角是谁、什么身份处境；②这个地方/这局是什么；③主宰本章生死的核心规则的【完整内容】——"
            "该规则必须在第一次生效之前对读者完整可见，禁止只提规则名不给内容、禁止让读者猜；"
            "④金手指第一次生效时，读者要能用一句话说出它是什么、有什么用；"
            "⑤章末读者能明确盘点：主角这一章赢了什么、付出了什么。"
            "锚点必须织进动作与对白，不许旁白说明书。除上述必需锚点外的世界观与次要私设一律延后；"
            "任何私设术语首次出现必须当句给一个人话级解释；第一章首屏具名出场人物≤3。"
            "每个 scene 的 participants 必须列出全部在场人物：具名人物从 CastSpec 名单中选取；"
            "无名配角也必须列出，用带身份+性别的描述性称谓（如『家属(男,五十多岁)』『夜班保安(女)』），"
            "禁止留空让写手自行发明——同一无名配角跨场景必须沿用同一称谓，身份/性别不得漂移。"
            "整章不得全是主角单人场景——只要卡司允许，至少一个场景要有第二个具名在场者（对手、盟友或施压方）。"
            "【场景卡事实一致性】同一章各场景卡之间、以及卡内各字段之间不得有事实矛盾"
            "（如某场景把患者设为'无名无主'，另一字段又给她安排守床家属；门已锁死又有人推门而入）——"
            "生成后逐卡自查一遍事实冲突再输出。"
            "每章必须包含章节级 `methodology_contract`：conflict_stakes、conflict_buffs、hooks_to_resolve、hooks_to_plant、relationship_debts（本章要推进的未了人际承诺或张力，不是金钱账务，剧情内容里禁止因此出现欠账/记账设定）、pacing_mode、emotion_phase、is_climax、loop_position。"
            "章节级 methodology_contract 不得放镜头、揭示、断点等场景字段，也不得放 ability_origin_contract、recognition_anchors 等故事级字段。"
            "每个 scene 必须包含场景级 `methodology_contract`：conflict_stakes、conflict_buffs、hook_type、spotlight_character、information_control_mode、camera_distance、reveal_mode、signature_image、cut_point、action_sequence、relationship_debts（人际承诺/张力，不得写成账务）。"
            "场景级 methodology_contract 不得放 pacing/climax/loop 等章节字段，也不得放能力来源/人物识别等故事级字段。"
            "每章还建议包含 `event_cycle_contract`、`chapter_event_role`、`information_gap_mode`；角色表示本章在更大事件单元中的职责，不是把完整六步模板重复塞进每一章。"
            "请在整卷中分配 trigger、desire_lock、obstacle_escalation、method_search、execution_turn、payoff_feedback、reaction_reset、bridge_hook 等角色，以避免章节同质化。"
            "每章还必须包含世界观合规字段：`world_rule_refs`（使用到的 WorldviewKernel invariant key 或 system name）、"
            "`world_rule_landing`（让规则落地的具体选择、证据、代价或状态变化）、`location_refs`、`faction_refs`、`key_reveals`。"
            "当 WorldviewKernel 含有增强契约时，每章还必须输出 `world_state_deltas`、`world_asset_refs`、"
            "`authority_claim_refs`、`world_scene_template_ref`、`reveal_weight`、`anti_copy_boundary_notes`；"
            "凡引用 asset，必须在 `world_rule_landing` 或状态变量 evidence 中写出可见 cost/exposure。"
            "`key_reveals` 必须遵守 WorldviewKernel reveal_ladder，禁止提前揭露未来真相。"
            "这些是读者可见的因果轴，不是固定写作模板；不要把正文写成固定套路，只用它证明本章有压力、有选择/行动/反应、有阻力/代价/收益/状态变化，并制造下一章阅读欲望。"
            # ── 章节标题硬合同（2026-05 重写）────────────────────────
            # 旧版只说"必须具体" + 小型黑名单。模型仍然滑回"名词+功能尾词"
            # 模板（铁壁破壁、回声追索）。新版强制从本章内容提取，并显式
            # 给出全书已用标题。
            "【章节标题硬合同 —— 严格执行】\n"
            "1. 每个 title 必须是 2-6 字，且必须从本章 main_conflict / hook_description / scenes 中已经出现的具体元素提取："
            "命名器物、人物名字、具体地点、独有动作的结果、独有意象、一句被引用的台词。\n"
            "2. 严禁出现「名词+功能尾词」模板，禁止的尾词包括但不限于："
            "初现、入局、投石、试探、铺火、露锋、破冰、起手、掀幕、落子、追索、摸底、拆解、寻隙、探针、回查、溯源、揭层、织网、破壁、加压、失衡、绞杀、窒息、封锁、逼近、崩弦、死线、契机、锁链、加注、试招、试压、逆鳞、炸场、再起、收网、下注、开局。\n"
            "3. 必须在全书范围内唯一 —— 参见上方「请勿重复」标题列表（已经在前几卷用过的标题），"
            "本卷生成的标题不得与之相同或近似（共享 ≥2 个字也算近似）。\n"
            "4. 如果本章内容里抽不出独有的具体标题，先把 main_conflict/hook_description 改得更具体，再从改写后的内容里提取标题。\n"
            "hook_description 必须是具体下一步事件，禁止写「围绕某冲突出现新的证据、时限或代价」这类占位句。"
            "每章必须至少推进两个真实状态维度：剧情、关系、线索、能力、资源、身份/地位、暴露风险；"
            "这种变化必须写进 goal/main_conflict/scenes，禁止写「建立世界观」「引入势力」「完善体系」「深化主题」这类作者笔记。"
        )
    )
    _genre_instruction = getattr(
        _genre_profile.planner_prompts, f"outline_instruction_{_lang_key}", ""
    )
    if _genre_instruction and not compact_outline_mode:
        user_prompt += f"\n\n{'[Genre planning requirements]' if is_en else '【品类规划要求】'}\n{_genre_instruction}"
    if not compact_outline_mode:
        user_prompt = _append_category_context(user_prompt, project, is_en=is_en)
    hook_constraints = hook_outline_extra_constraints(_hook_spec, language=language)
    merged_extra_constraints = [*(extra_constraints or []), *hook_constraints]
    if merged_extra_constraints:
        header = (
            "[Hard constraints — MUST be reflected in the outline]"
            if is_en
            else "【硬约束 — 必须体现在章纲中】"
        )
        constraint_lines = "\n".join(f"- {c}" for c in merged_extra_constraints)
        user_prompt += f"\n\n{header}\n{constraint_lines}"
    return system_prompt, user_prompt


def _volume_cast_expansion_prompts(
    project: ProjectModel,
    book_spec: dict[str, Any],
    world_spec: dict[str, Any],
    cast_spec: dict[str, Any],
    volume_entry: dict[str, Any],
    prior_feedback_summary: str | None = None,
    extra_constraints: list[str] | None = None,
) -> tuple[str, str]:
    """Prompts for expanding/evolving the cast for a specific volume (Phase 3)."""
    language = _planner_language(project)
    is_en = is_english_language(language)
    volume_number = int(volume_entry.get("volume_number", 1))
    system_prompt = (
        "You are a character architect evolving a cast for the next volume of a long-form novel. Output valid JSON only."
        if is_en
        else "你是长篇小说角色进化架构师，负责为下一卷扩展和演化角色。输出必须是合法 JSON，不要解释。"
    )
    feedback_block = ""
    if prior_feedback_summary:
        feedback_block = f"\n{'Previous volume writing feedback:' if is_en else '上一卷写作反馈：'}\n{prior_feedback_summary}\n"
    user_prompt = (
        (
            f"BookSpec summary:\n{summarize_book_spec(book_spec, language='en')}\n"
            f"WorldSpec summary:\n{summarize_world_spec(world_spec, language='en')}\n"
            f"Current CastSpec summary:\n{summarize_cast_spec(cast_spec, language='en', volume_number=volume_number)}\n"
            f"Volume {volume_number} plan: {_json_dumps(volume_entry)}\n"
            f"{feedback_block}"
            f"For volume {volume_number}, generate a JSON with:\n"
            "1. 'new_characters': array of new supporting characters needed for this volume\n"
            "2. 'character_evolutions': array of {name, changes} for existing characters that should evolve based on prior events\n"
            "3. 'relationship_updates': array of relationship changes entering this volume\n"
            "The 'changes' field must be an object, never an array. If you need prose notes, put them under "
            "'evolution_notes' or 'arc_shift'.\n"
            "Inside 'changes', never put a full sentence into 'role'. Use 'role' only for a short structural label "
            "(<=64 chars, e.g. ally, rival, antagonist_lieutenant). Put arc/function changes into fields like "
            "'role_evolution', 'alliance_status', 'arc_shift', or 'function_in_volume' instead.\n"
            "If you include 'age', it must be a bare integer (e.g. 47). Do not use prose like 'late 40s'; "
            "use 'age_note' for fuzzy age text.\n"
            "Keep new characters minimal — only introduce who the volume absolutely needs."
        )
        if is_en
        else (
            f"BookSpec 摘要：\n{summarize_book_spec(book_spec, language='zh')}\n"
            f"WorldSpec 摘要：\n{summarize_world_spec(world_spec, language='zh')}\n"
            f"当前角色摘要：\n{summarize_cast_spec(cast_spec, language='zh', volume_number=volume_number)}\n"
            f"第{volume_number}卷计划：{_json_dumps(volume_entry)}\n"
            f"{feedback_block}"
            f"请为第{volume_number}卷生成 JSON，包含：\n"
            "1. 'new_characters'：本卷需要的新配角数组\n"
            "2. 'character_evolutions'：需要根据前卷事件演化的现有角色 {name, changes} 数组\n"
            "3. 'relationship_updates'：进入本卷时的关系变化数组\n"
            "'changes' 必须是对象，不能是数组；如果要写描述性说明，请放到 'evolution_notes' 或 'arc_shift'。\n"
            "在 'changes' 里，不要把完整句子写进 'role'。'role' 只能是简短结构标签（<=64 字符，例如 ally、rival、"
            "antagonist_lieutenant）；角色功能变化请写到 'role_evolution'、'alliance_status'、'arc_shift'、"
            "'function_in_volume' 这类字段。\n"
            "如果填写 'age'，必须是纯整数（例如 47），不要写 'late 40s' 这类自然语言；模糊年龄请写到 'age_note'。\n"
            "新角色应最少化——只引入本卷绝对必要的角色。"
        )
    )
    if extra_constraints:
        is_en = is_english_language(_planner_language(project))
        header = (
            "[Hard constraints — MUST shape cast decisions]"
            if is_en
            else "【硬约束 — 必须影响角色决策】"
        )
        constraint_lines = "\n".join(f"- {c}" for c in extra_constraints)
        user_prompt += f"\n\n{header}\n{constraint_lines}"
    return system_prompt, user_prompt


def _volume_world_disclosure_prompts(
    project: ProjectModel,
    world_spec: dict[str, Any],
    volume_entry: dict[str, Any],
    prior_world_snapshot: str | None = None,
) -> tuple[str, str]:
    """Prompts for revealing world details for a specific volume (Phase 3)."""
    language = _planner_language(project)
    is_en = is_english_language(language)
    volume_number = int(volume_entry.get("volume_number", 1))
    system_prompt = (
        "You are a world-building editor managing progressive world disclosure. Output valid JSON only."
        if is_en
        else "你是负责渐进式世界观揭示的编辑。输出必须是合法 JSON，不要解释。"
    )
    snapshot_block = ""
    if prior_world_snapshot:
        snapshot_block = f"\n{'World state after previous volume:' if is_en else '上一卷结束时的世界状态：'}\n{prior_world_snapshot}\n"
    user_prompt = (
        (
            f"WorldSpec summary:\n{summarize_world_spec(world_spec, language='en')}\n"
            f"Volume {volume_number} plan: {_json_dumps(volume_entry)}\n"
            f"{snapshot_block}"
            f"For volume {volume_number}, generate a JSON with:\n"
            "1. 'new_locations': locations revealed or first visited in this volume\n"
            "2. 'new_rules_revealed': world rules the reader learns in this volume\n"
            "3. 'faction_movements': how factions shift in this volume\n"
            "4. 'frontier_summary': one-paragraph summary of what the reader now knows about the world\n"
            "Only reveal what the plot needs — keep mysteries for later volumes."
        )
        if is_en
        else (
            f"WorldSpec 摘要：\n{summarize_world_spec(world_spec, language='zh')}\n"
            f"第{volume_number}卷计划：{_json_dumps(volume_entry)}\n"
            f"{snapshot_block}"
            f"请为第{volume_number}卷生成 JSON，包含：\n"
            "1. 'new_locations'：本卷揭示或首次到访的地点\n"
            "2. 'new_rules_revealed'：读者在本卷中了解到的世界规则\n"
            "3. 'faction_movements'：本卷中各势力的变化\n"
            "4. 'frontier_summary'：一段话总结读者此时对世界的了解\n"
            "只揭示情节需要的——为后续卷保留悬念。"
        )
    )
    return system_prompt, user_prompt


def _merge_mapping_non_empty(base: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    merged = copy.deepcopy(base)
    for key, value in incoming.items():
        if value in (None, "", [], {}):
            continue
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge_mapping_non_empty(_mapping(merged.get(key)), value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def _merge_volume_cast_expansion_into_cast_spec(
    cast_spec: dict[str, Any],
    cast_expansion: dict[str, Any],
) -> dict[str, Any]:
    merged = copy.deepcopy(_mapping(cast_spec))
    if not merged:
        return merged

    supporting_cast = [
        copy.deepcopy(_mapping(item)) for item in _mapping_list(merged.get("supporting_cast"))
    ]
    supporting_by_name = {
        _non_empty_string(item.get("name"), ""): item
        for item in supporting_cast
        if _non_empty_string(item.get("name"), "")
    }

    # Primary characters are hoisted up so ``_upsert_character`` can
    # cross-check before creating a supporting-cast duplicate of the
    # protagonist / antagonist under a variant name.
    _protagonist_entry = _mapping(merged.get("protagonist"))
    _antagonist_entry = _mapping(merged.get("antagonist"))
    primary_characters: dict[str, dict[str, Any]] = {}
    _protagonist_name = _non_empty_string(_protagonist_entry.get("name"), "")
    if _protagonist_name:
        primary_characters[_protagonist_name] = _protagonist_entry
    _antagonist_name = _non_empty_string(_antagonist_entry.get("name"), "")
    if _antagonist_name:
        primary_characters[_antagonist_name] = _antagonist_entry

    def _assign_primary(key: str, value: dict[str, Any]) -> None:
        primary_characters[key] = value
        if merged.get("protagonist", {}).get("name") == key:
            merged["protagonist"] = value
        elif merged.get("antagonist", {}).get("name") == key:
            merged["antagonist"] = value

    def _upsert_character(raw_value: Any) -> None:
        candidate = _sanitize_new_character_candidate(raw_value)
        name = _non_empty_string(candidate.get("name"), "")
        if not name:
            return
        # Cross-check primary characters — an LLM-generated "new character"
        # that canonicalizes to the protagonist / antagonist should fold back
        # into the primary entry rather than creating a duplicate row.
        primary_hit = resolve_character_match(candidate, primary_characters)
        if primary_hit is not None:
            _assign_primary(
                primary_hit,
                merge_character_with_aliases(primary_characters[primary_hit], candidate),
            )
            return
        matched_key = resolve_character_match(candidate, supporting_by_name)
        if matched_key is None:
            supporting_by_name[name] = candidate
            supporting_cast.append(candidate)
            return
        existing = supporting_by_name[matched_key]
        updated = merge_character_with_aliases(existing, candidate)
        supporting_by_name[matched_key] = updated
        idx = supporting_cast.index(existing)
        supporting_cast[idx] = updated

    for raw_character in _mapping_list(cast_expansion.get("new_characters")):
        _upsert_character(raw_character)

    for evolution in _mapping_list(cast_expansion.get("character_evolutions")):
        evo_map = _mapping(evolution)
        name = _non_empty_string(evo_map.get("name") or evo_map.get("character"), "")
        if not name:
            continue
        changes = _mapping(evo_map.get("changes"))
        if not changes:
            changes = {
                key: value for key, value in evo_map.items() if key not in {"name", "character"}
            }
        # Alias-aware lookup so an evolution targeting "三叔" still finds
        # the "王守真" entry when the two have been linked via aliases.
        search_probe: dict[str, Any] = {"name": name}
        if isinstance(evo_map.get("aliases"), (list, str)):
            search_probe["aliases"] = evo_map["aliases"]
        primary_hit = resolve_character_match(search_probe, primary_characters)
        supporting_hit = (
            None
            if primary_hit is not None
            else resolve_character_match(search_probe, supporting_by_name)
        )
        if primary_hit is not None:
            target_key = primary_hit
            target = primary_characters[primary_hit]
        elif supporting_hit is not None:
            target_key = supporting_hit
            target = supporting_by_name[supporting_hit]
        else:
            _upsert_character({"name": name, **changes})
            continue
        allow_role_change = target_key not in primary_characters
        sanitized_changes = _sanitize_character_evolution_changes(
            target,
            changes,
            allow_role_change=allow_role_change,
        )
        merged_target = _merge_mapping_non_empty(target, sanitized_changes)
        # If the evolution arrived under an alias (name != target_key), also
        # fold the alias into the merged entry's alias list.
        if name and name != target_key:
            _aliases = list(collect_entry_aliases(merged_target))
            if name not in _aliases:
                _aliases.append(name)
                merged_target["aliases"] = _aliases
        if target_key in primary_characters:
            _assign_primary(target_key, merged_target)
        else:
            idx = supporting_cast.index(target)
            supporting_by_name[target_key] = merged_target
            supporting_cast[idx] = merged_target

    merged["supporting_cast"] = supporting_cast
    relationship_updates = _mapping_list(cast_expansion.get("relationship_updates"))
    if relationship_updates:
        conflict_map = list(_mapping_list(merged.get("conflict_map")))
        for update in relationship_updates:
            update_map = _mapping(update)
            left = _non_empty_string(update_map.get("character_a") or update_map.get("name"), "")
            right = _non_empty_string(
                update_map.get("character_b") or update_map.get("counterpart"), ""
            )
            relation_type = _non_empty_string(
                update_map.get("type") or update_map.get("relationship_type"), ""
            )
            if left and right and relation_type:
                conflict_map.append(
                    {
                        "character_a": left,
                        "character_b": right,
                        "conflict_type": relation_type,
                        "trigger_condition": update_map.get("tension") or update_map.get("summary"),
                    }
                )
        if conflict_map:
            merged["conflict_map"] = conflict_map
    return merged


def _merge_volume_world_disclosure_into_world_spec(
    world_spec: dict[str, Any],
    world_disclosure: dict[str, Any],
    *,
    volume_number: int,
) -> dict[str, Any]:
    merged = copy.deepcopy(_mapping(world_spec))
    if not merged:
        return merged

    locations = [copy.deepcopy(_mapping(item)) for item in _mapping_list(merged.get("locations"))]
    locations_by_name = {
        _non_empty_string(item.get("name"), ""): item
        for item in locations
        if _non_empty_string(item.get("name"), "")
    }
    for raw_location in _mapping_list(world_disclosure.get("new_locations")):
        if isinstance(raw_location, str):
            candidate = {"name": raw_location, "type": "location"}
        else:
            candidate = copy.deepcopy(_mapping(raw_location))
        name = _non_empty_string(candidate.get("name"), "")
        if not name:
            continue
        existing = locations_by_name.get(name)
        if existing is None:
            locations.append(candidate)
            locations_by_name[name] = candidate
        else:
            merged_location = _merge_mapping_non_empty(existing, candidate)
            idx = locations.index(existing)
            locations[idx] = merged_location
            locations_by_name[name] = merged_location
    if locations:
        merged["locations"] = locations

    rules = [copy.deepcopy(_mapping(item)) for item in _mapping_list(merged.get("rules"))]
    existing_rule_names = {_non_empty_string(item.get("name"), "") for item in rules}
    for index, raw_rule in enumerate(
        _mapping_list(world_disclosure.get("new_rules_revealed")), start=1
    ):
        if isinstance(raw_rule, str):
            candidate = {
                "rule_id": f"VR{volume_number:02d}-{index:02d}",
                "name": raw_rule,
                "description": raw_rule,
            }
        else:
            candidate = copy.deepcopy(_mapping(raw_rule))
            if not candidate.get("rule_id"):
                candidate["rule_id"] = f"VR{volume_number:02d}-{index:02d}"
        name = _non_empty_string(candidate.get("name"), "")
        if name and name not in existing_rule_names:
            rules.append(candidate)
            existing_rule_names.add(name)
    if rules:
        merged["rules"] = rules

    factions = [copy.deepcopy(_mapping(item)) for item in _mapping_list(merged.get("factions"))]
    factions_by_name = {
        _non_empty_string(item.get("name"), ""): item
        for item in factions
        if _non_empty_string(item.get("name"), "")
    }
    for raw_movement in _mapping_list(world_disclosure.get("faction_movements")):
        movement = copy.deepcopy(_mapping(raw_movement))
        name = _non_empty_string(movement.get("name") or movement.get("faction"), "")
        if not name:
            continue
        existing = factions_by_name.get(name)
        if existing is None:
            candidate = {
                "name": name,
                "goal": movement.get("goal") or movement.get("movement"),
                "method": movement.get("method"),
                "relationship_to_protagonist": movement.get("relationship_to_protagonist"),
                "internal_conflict": movement.get("internal_conflict"),
            }
            factions.append(candidate)
            factions_by_name[name] = candidate
        else:
            merged_faction = _merge_mapping_non_empty(existing, movement)
            idx = factions.index(existing)
            factions[idx] = merged_faction
            factions_by_name[name] = merged_faction
    if factions:
        merged["factions"] = factions

    frontier_summary = _non_empty_string(world_disclosure.get("frontier_summary"), "")
    if frontier_summary:
        history_key_events = list(_mapping_list(merged.get("history_key_events")))
        history_key_events.append(
            {
                "event": frontier_summary,
                "relevance": (
                    f"Volume {volume_number} frontier update"
                    if re.search(r"[A-Za-z]", frontier_summary)
                    else f"第{volume_number}卷世界边界更新"
                ),
            }
        )
        merged["history_key_events"] = history_key_events
    return merged


async def _generate_structured_artifact(
    session: AsyncSession,
    settings: AppSettings,
    *,
    project: ProjectModel,
    logical_name: str,
    system_prompt: str,
    user_prompt: str,
    fallback_payload: Any,
    workflow_run_id: UUID,
    step_run_id: UUID | None = None,
    validator: Callable[[Any], Any] | None = None,
    abort_on_fallback: bool = False,
    merge_fallback: bool = True,
) -> tuple[Any, UUID | None]:
    # Retry budget for planner artifacts.  Bumped to 4 on 2026-04-21 after
    # a production incident (romantasy-1776330993 volume_5_chapter_outline)
    # where two back-to-back MiniMax-M2.7 formatting glitches killed an
    # entire heal job.  Four attempts give non-deterministic LLM output a
    # reasonable chance to self-heal before the whole job bails.
    _max_attempts = 4
    fail_closed_artifacts = {
        "book_spec",
        "world_spec",
        "cast_spec",
        "volume_plan",
        "volume_plan_repair",
        "story_design_kernel",
        "emotion_driven_kernel",
    }
    effective_abort_on_fallback = bool(
        abort_on_fallback
        or logical_name in fail_closed_artifacts
        or logical_name.endswith("_kernel")
    )

    last_llm_run_id: UUID | None = None
    stage = _methodology_stage_for_planner_artifact(logical_name)
    effective_user_prompt = (
        user_prompt
        if "【题材方法论·" in user_prompt or stage is None
        else attach_planner_methodology(
            user_prompt,
            stage=stage,
            project_ctx=project,
            token_budget=400 if "repair" in logical_name else 800,
        )
    )
    input_hash = _planner_artifact_input_hash(
        logical_name=logical_name,
        system_prompt=system_prompt,
        user_prompt=effective_user_prompt,
        fallback_payload=fallback_payload,
    )
    artifact_type = _planner_artifact_type_for_logical_name(logical_name)
    if (
        bool(getattr(settings.pipeline, "planning_artifact_reuse_enabled", True))
        and artifact_type is not None
    ):
        reusable = await _latest_reusable_planning_artifact(
            session,
            project=project,
            artifact_type=artifact_type,
            input_hash=input_hash,
            validator=validator,
            allow_legacy_latest=bool(
                getattr(settings.pipeline, "planning_artifact_reuse_allow_legacy", True)
            )
            and artifact_type != ArtifactType.VOLUME_CHAPTER_OUTLINE,
        )
        if reusable is not None:
            await _heartbeat_planner_workflow(
                session,
                workflow_run_id=workflow_run_id,
                logical_name=logical_name,
                phase="reused",
            )
            logger.info(
                "Reusing %s planning artifact %s v%s for project '%s'.",
                artifact_type.value,
                reusable.id,
                reusable.version_no,
                project.slug,
            )
            return (
                _with_planning_meta(
                    reusable.content,
                    logical_name=logical_name,
                    input_hash=input_hash,
                    workflow_run_id=workflow_run_id,
                    reused_artifact_id=reusable.id,
                    project=project,
                ),
                None,
            )

    semantic_repair_history: list[dict[str, Any]] = []
    for attempt in range(_max_attempts):
        await _heartbeat_planner_workflow(
            session,
            workflow_run_id=workflow_run_id,
            logical_name=logical_name,
            phase="llm_started",
            attempt=attempt + 1,
        )
        max_tokens_override = _planner_stage_max_tokens(logical_name, project=project)
        completion = await complete_text(
            session,
            settings,
            LLMCompletionRequest(
                logical_role="planner",
                system_prompt=system_prompt,
                user_prompt=effective_user_prompt,
                fallback_response=_json_dumps(fallback_payload),
                prompt_template=f"planner_{logical_name}",
                prompt_version="1.0",
                project_id=project.id,
                workflow_run_id=workflow_run_id,
                step_run_id=step_run_id,
                max_tokens_override=max_tokens_override,
                metadata={
                    "project_slug": project.slug,
                    "artifact": logical_name,
                    "attempt": attempt + 1,
                    "fail_closed": effective_abort_on_fallback,
                    "input_hash": input_hash,
                    "max_tokens_override": max_tokens_override,
                    "semantic_repair_history": semantic_repair_history[-3:],
                },
            ),
        )
        await _heartbeat_planner_workflow(
            session,
            workflow_run_id=workflow_run_id,
            logical_name=logical_name,
            phase="llm_completed",
            attempt=attempt + 1,
        )
        last_llm_run_id = completion.llm_run_id
        finish_reason = _non_empty_string(getattr(completion, "finish_reason", None), "")
        # If the LLM call itself exhausted retries, complete_text flips
        # ``provider`` to "fallback".  For structural artifacts where the
        # fallback would silently corrupt downstream (e.g. per-volume
        # chapter outlines), abort immediately with a clear signal.
        if effective_abort_on_fallback and getattr(completion, "provider", None) == "fallback":
            raise PlannerFallbackError(
                f"Planner artifact '{logical_name}' had to fall back after LLM retries "
                f"exhausted. Refusing to continue because downstream requires a real validated output."
            )
        if effective_abort_on_fallback and finish_reason == "length":
            error = PlannerFallbackError(
                f"Planner artifact '{logical_name}' returned finish_reason=length on "
                f"attempt {attempt + 1}; refusing to approve a truncated planning artifact."
            )
            semantic_repair_history.append(
                {
                    "attempt": attempt + 1,
                    "error_type": "finish_reason_length",
                    "findings": [
                        {
                            "code": "outline_batch_truncated"
                            if "outline" in logical_name
                            else "planner_artifact_truncated",
                            "severity": "critical",
                            "message": str(error),
                            "path": logical_name,
                        }
                    ],
                }
            )
            _persist_failing_planner_output(
                project=project,
                logical_name=logical_name,
                attempt=attempt + 1,
                content=completion.content,
                error=error,
            )
            if attempt < _max_attempts - 1:
                effective_user_prompt = _planner_length_retry_prompt(
                    effective_user_prompt,
                    logical_name,
                )
                continue
            raise error
        try:
            generated = _extract_json_payload(completion.content)
            payload = (
                _merge_planning_payload(fallback_payload, generated)
                if merge_fallback
                else copy.deepcopy(generated)
            )
            if validator is not None:
                validator(_without_planning_meta(payload))
            return (
                _with_planning_meta(
                    payload,
                    logical_name=logical_name,
                    input_hash=input_hash,
                    workflow_run_id=workflow_run_id,
                    project=project,
                    finish_reason=finish_reason,
                ),
                last_llm_run_id,
            )
        except Exception as exc:
            from bestseller.services.llm_closed_loop import (
                build_repair_user_prompt,
                findings_from_exception,
            )

            repair_findings = findings_from_exception(exc)
            semantic_repair_history.append(
                {
                    "attempt": attempt + 1,
                    "error_type": type(exc).__name__,
                    "findings": [finding.to_dict() for finding in repair_findings[:12]],
                }
            )
            # Persist the raw LLM response to disk so we can root-cause
            # parse/validation failures offline. Critical because
            # ``response_payload_ref`` on LlmRunModel is currently unused
            # and the content would otherwise be lost on retry.
            _persist_failing_planner_output(
                project=project,
                logical_name=logical_name,
                attempt=attempt + 1,
                content=completion.content,
                error=exc,
            )
            if attempt < _max_attempts - 1:
                effective_user_prompt = build_repair_user_prompt(
                    original_user_prompt=user_prompt,
                    findings=repair_findings,
                    language=getattr(project, "language", None),
                )
                logger.warning(
                    "Planner artifact %s attempt %d failed parse/validation (%s: %s), retrying with diagnostics=%s …",
                    logical_name,
                    attempt + 1,
                    type(exc).__name__,
                    exc,
                    [finding.code for finding in repair_findings[:8]],
                )
                continue
            if effective_abort_on_fallback:
                raise PlannerFallbackError(
                    f"Planner artifact '{logical_name}' failed parse/validation after "
                    f"{_max_attempts} attempts ({type(exc).__name__}: {exc})."
                ) from exc
            logger.warning(
                "Planner artifact %s failed after %d attempts (%s: %s), using fallback.",
                logical_name,
                _max_attempts,
                type(exc).__name__,
                exc,
            )
            return copy.deepcopy(fallback_payload), last_llm_run_id

    # Should not reach here, but satisfy type checker
    return copy.deepcopy(fallback_payload), last_llm_run_id


# ---------------------------------------------------------------------------
# Story design kernel
# ---------------------------------------------------------------------------


def _validate_story_design_kernel_payload(payload: Any) -> None:
    if not isinstance(payload, dict):
        raise ValueError("StoryDesignKernel payload must be a JSON object.")
    story_design_kernel_from_dict(payload)


def _validate_emotion_driven_kernel_payload(payload: Any) -> None:
    if not isinstance(payload, dict):
        raise ValueError("EmotionDrivenKernel payload must be a JSON object.")
    kernel = emotion_driven_kernel_from_dict(payload)
    report = evaluate_emotion_contracts(kernel)
    if not report.passed:
        codes = ", ".join(issue.code for issue in report.issues)
        raise ValueError(f"EmotionDrivenKernel contract is incomplete: {codes}")


def _validate_entry_system_kernel_payload(payload: Any) -> None:
    if not isinstance(payload, dict):
        raise ValueError("EntrySystemKernel payload must be a JSON object.")
    entry_system_kernel_from_dict(payload)


_PLANNER_REUSABLE_ARTIFACTS: dict[str, ArtifactType] = {
    "book_spec": ArtifactType.BOOK_SPEC,
    "world_spec": ArtifactType.WORLD_SPEC,
    "cast_spec": ArtifactType.CAST_SPEC,
    "volume_plan": ArtifactType.VOLUME_PLAN,
    "story_design_kernel": ArtifactType.STORY_DESIGN_KERNEL,
    "public_emotion_kernel": ArtifactType.PUBLIC_EMOTION_KERNEL,
    "compliance_boundary_kernel": ArtifactType.COMPLIANCE_BOUNDARY_KERNEL,
    "entry_system_kernel": ArtifactType.ENTRY_SYSTEM_KERNEL,
    "emotion_driven_kernel": ArtifactType.EMOTION_DRIVEN_KERNEL,
    "prewrite_readiness": ArtifactType.PREWRITE_READINESS,
}


def _planner_artifact_type_for_logical_name(logical_name: str) -> ArtifactType | None:
    if logical_name in _PLANNER_REUSABLE_ARTIFACTS:
        return _PLANNER_REUSABLE_ARTIFACTS[logical_name]
    if re.fullmatch(r"volume_\d+_world_disclosure", logical_name):
        return ArtifactType.VOLUME_WORLD_DISCLOSURE
    if re.fullmatch(r"volume_\d+_chapter_outline(_batch_\d+_\d+)?", logical_name):
        return ArtifactType.VOLUME_CHAPTER_OUTLINE
    return None


# The planner role's configured completion budget (config/default.yaml
# `models.planner.max_tokens`). Heavy structured stages (chapter-outline
# batches, story-design / emotion kernels) emit large JSON and were being
# clamped BELOW this budget, so the model truncated mid-object
# (finish_reason="length") -> invalid JSON -> validation failure -> slow retry
# loop / fallback. Use the full budget for those stages so the real output can
# complete. See memory: story-design-kernel-gate-blocker / model-strategy-minimax-m3.
_PLANNER_MAX_OUTPUT_TOKENS = 16384


def _planner_stage_max_tokens(logical_name: str, *, project: Any | None = None) -> int | None:
    if re.fullmatch(r"volume_\d+_chapter_outline(_batch_\d+_\d+)?", logical_name):
        return _PLANNER_MAX_OUTPUT_TOKENS
    if logical_name in {"story_design_kernel", "emotion_driven_kernel"}:
        return _PLANNER_MAX_OUTPUT_TOKENS
    if logical_name in {
        "volume_plan",
        "volume_plan_repair",
        "volume_plan_foreshadowing_repair",
        "volume_plan_convergence_repair",
    } and project is not None:
        # P-6 enabler (xianxia benchmark): a full 10-volume plan measures
        # ~18k chars in production — physically impossible inside the legacy
        # 8192-token cap, which is how a 500-chapter project shipped a
        # truncation-shrunk 5-volume plan. Scale with the project's volume
        # count; the coverage contract is unsatisfiable otherwise.
        target_chapters = max(int(getattr(project, "target_chapters", 0) or 0), 1)
        volume_count = int(compute_linear_hierarchy(target_chapters).get("volume_count") or 1)
        return min(32768, max(8192, volume_count * 2560))
    if logical_name == "cast_spec" and project is not None:
        # G1 enabler (xianxia benchmark): once the roster floor scales with
        # volume count (3-5 → 15-30 for a 10-volume book), a full cast with
        # goal/flaw/evolution_arc no longer fits in 8192 tokens (a single
        # verification run emitted 12615 tokens for 23 characters). Scale the
        # cap with the roster ceiling, or the new motivation fields get
        # truncated away exactly like the P-6 volume-plan shrink.
        from bestseller.services.relationship_scaling import compute_supporting_bounds

        target_chapters = max(int(getattr(project, "target_chapters", 0) or 0), 1)
        volume_count = int(compute_linear_hierarchy(target_chapters).get("volume_count") or 1)
        ceiling = compute_supporting_bounds(volume_count).ceiling
        # ~600 tokens/character + book-spec overhead; short books (ceiling ~7)
        # fall back to the 8192 floor, a 10-volume roster (ceiling 30) gets 20k.
        return min(32768, max(8192, ceiling * 600 + 2000))
    if logical_name in {
        "book_spec",
        "book_spec_narrative_lines_repair",
        "world_spec",
        "cast_spec",
        "volume_plan",
        "volume_plan_repair",
        "volume_plan_foreshadowing_repair",
        "volume_plan_convergence_repair",
    }:
        return 8192
    if logical_name.endswith("_world_disclosure") or logical_name.endswith("_cast_expansion"):
        return 4096
    return None


def _planner_artifact_input_hash(
    *,
    logical_name: str,
    system_prompt: str,
    user_prompt: str,
    fallback_payload: Any,
) -> str:
    payload = {
        "logical_name": logical_name,
        "system_prompt": system_prompt,
        "user_prompt": user_prompt,
        "fallback_payload": fallback_payload,
    }
    return hashlib.sha256(_json_dumps(payload).encode("utf-8")).hexdigest()


def _without_planning_meta(payload: Any) -> Any:
    if not isinstance(payload, dict) or "_meta" not in payload:
        return payload
    clean = copy.deepcopy(payload)
    clean.pop("_meta", None)
    return clean


def _with_planning_meta(
    payload: Any,
    *,
    logical_name: str,
    input_hash: str,
    workflow_run_id: UUID,
    reused_artifact_id: UUID | None = None,
    project: ProjectModel | None = None,
    finish_reason: str | None = None,
) -> Any:
    if not isinstance(payload, dict):
        return payload
    enriched = copy.deepcopy(payload)
    prior_meta = enriched.get("_meta") if isinstance(enriched.get("_meta"), dict) else {}
    strict_meta = planning_artifact_meta(project) if project is not None else {}
    enriched["_meta"] = {
        **strict_meta,
        **prior_meta,
        "input_hash": input_hash,
        "source_step": logical_name,
        "workflow_run_id": str(workflow_run_id),
        "reused": reused_artifact_id is not None,
        "reused_artifact_id": str(reused_artifact_id) if reused_artifact_id else None,
    }
    if finish_reason:
        enriched["_meta"]["finish_reason"] = finish_reason
    if logical_name == "cast_spec":
        policy_versions = enriched["_meta"].get("policy_versions")
        if not isinstance(policy_versions, dict):
            policy_versions = {}
        policy_versions["identity_policy"] = IDENTITY_POLICY_VERSION
        enriched["_meta"]["policy_versions"] = policy_versions
    return enriched


def _cast_identity_policy_premise(project: ProjectModel) -> str:
    metadata = project.metadata_json if isinstance(project.metadata_json, dict) else {}
    parts = [
        getattr(project, "title", "") or "",
        getattr(project, "genre", "") or "",
        getattr(project, "sub_genre", "") or "",
        str(metadata.get("premise") or ""),
        str(metadata.get("concept") or ""),
        str(metadata.get("description") or ""),
    ]
    return "\n".join(part for part in parts if part)


def _reusable_artifact_policy_report(
    project: ProjectModel,
    *,
    artifact_type: ArtifactType,
    content: Any,
) -> Any | None:
    if artifact_type != ArtifactType.CAST_SPEC or not isinstance(content, dict):
        return None
    return validate_commercial_zh_identity_policy(
        content,
        language=getattr(project, "language", None) or "zh-CN",
        premise_text=_cast_identity_policy_premise(project),
        require_policy_version=True,
    )


async def _heartbeat_planner_workflow(
    session: AsyncSession,
    *,
    workflow_run_id: UUID,
    logical_name: str,
    phase: str,
    attempt: int | None = None,
) -> None:
    metadata_patch = {
        "planner_last_heartbeat_at": datetime.now(UTC).isoformat(),
        "planner_last_artifact": logical_name,
        "planner_last_phase": phase,
    }
    if attempt is not None:
        metadata_patch["planner_last_attempt"] = attempt
    session_get = getattr(session, "get", None)
    if session_get is None:
        return
    workflow_run = await session_get(WorkflowRunModel, workflow_run_id)
    if workflow_run is None:
        return
    workflow_run.updated_at = datetime.now(UTC)
    workflow_run.metadata_json = {
        **(workflow_run.metadata_json or {}),
        **metadata_patch,
    }
    await session.flush()


async def _latest_reusable_planning_artifact(
    session: AsyncSession,
    *,
    project: ProjectModel,
    artifact_type: ArtifactType,
    input_hash: str,
    validator: Callable[[Any], Any] | None,
    allow_legacy_latest: bool,
) -> PlanningArtifactVersionModel | None:
    hash_stmt = (
        select(PlanningArtifactVersionModel)
        .where(
            PlanningArtifactVersionModel.project_id == project.id,
            PlanningArtifactVersionModel.artifact_type == artifact_type.value,
            PlanningArtifactVersionModel.status == "approved",
            PlanningArtifactVersionModel.content["_meta"]["input_hash"].astext == input_hash,
        )
        .order_by(
            PlanningArtifactVersionModel.version_no.desc(),
            PlanningArtifactVersionModel.created_at.desc(),
        )
        .limit(1)
    )
    artifact = await session.scalar(hash_stmt)
    if artifact is None and allow_legacy_latest:
        legacy_stmt = (
            select(PlanningArtifactVersionModel)
            .where(
                PlanningArtifactVersionModel.project_id == project.id,
                PlanningArtifactVersionModel.artifact_type == artifact_type.value,
                PlanningArtifactVersionModel.status == "approved",
            )
            .order_by(
                PlanningArtifactVersionModel.version_no.desc(),
                PlanningArtifactVersionModel.created_at.desc(),
            )
            .limit(1)
        )
        artifact = await session.scalar(legacy_stmt)
    if artifact is None:
        return None
    policy_report = _reusable_artifact_policy_report(
        project,
        artifact_type=artifact_type,
        content=artifact.content,
    )
    if policy_report is not None and not getattr(policy_report, "passed", False):
        logger.debug(
            "Skipping reusable %s artifact %s for project '%s': policy validation failed: %s",
            artifact_type.value,
            artifact.id,
            project.slug,
            getattr(policy_report, "to_dict", lambda: {})(),
        )
        return None
    if isinstance(artifact.content, dict):
        meta = artifact.content.get("_meta")
        if isinstance(meta, dict):
            artifact_hash = meta.get("input_hash")
            if isinstance(artifact_hash, str) and artifact_hash and artifact_hash != input_hash:
                return None
    if validator is not None:
        try:
            validator(_without_planning_meta(artifact.content))
        except Exception:
            logger.debug(
                "Skipping reusable %s artifact %s for project '%s': validation failed",
                artifact_type.value,
                artifact.id,
                project.slug,
                exc_info=True,
            )
            return None
    return artifact


def _story_design_kernel_prompt_block(project: ProjectModel) -> str:
    metadata = project.metadata_json if isinstance(project.metadata_json, dict) else {}
    raw = _mapping(metadata.get("story_design_kernel"))
    if not raw:
        return ""
    try:
        return "\n\n" + render_story_design_kernel_prompt_block(raw)
    except Exception:
        logger.debug("Failed to render StoryDesignKernel prompt block", exc_info=True)
        return ""


def _emotion_driven_kernel_prompt_block(project: ProjectModel) -> str:
    metadata = project.metadata_json if isinstance(project.metadata_json, dict) else {}
    raw = _mapping(metadata.get("emotion_driven_kernel"))
    if not raw:
        return ""
    try:
        return "\n\n" + render_emotion_driven_kernel_prompt_block(
            raw,
            language=_planner_language(project),
        )
    except Exception:
        logger.debug("Failed to render EmotionDrivenKernel prompt block", exc_info=True)
        return ""


def _truncate_for_prompt(value: Any, max_chars: int) -> str:
    text = str(value or "").strip()
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    return text[: max(max_chars - 24, 0)].rstrip() + "\n...[truncated]"


def _compact_prompt_block_text(text: str, *, max_chars: int) -> str:
    stripped = str(text or "").strip()
    if not stripped:
        return ""
    return "\n\n" + _truncate_for_prompt(stripped, max_chars) + "\n"


def _compact_outline_context_block(project: ProjectModel, *, max_chars: int = 1600) -> str:
    metadata = project.metadata_json if isinstance(project.metadata_json, dict) else {}
    # Always lead with a compact ideology block so even token-tight (compact-mode)
    # 细纲 prompts reference the book's core ideology — a raw truncated JSON dump
    # would drop the deeply-nested ideology_kernel and lose the thematic spine.
    ideology_block = ""
    sdk = _mapping(metadata.get("story_design_kernel"))
    ideology_raw = _mapping(sdk.get("ideology_kernel"))
    if ideology_raw:
        try:
            from bestseller.domain.ideology import render_ideology_compact_block

            ideology_block = render_ideology_compact_block(ideology_raw)
        except Exception:
            ideology_block = ""
    picked: dict[str, Any] = {}
    for key in (
        "story_design_kernel",
        "emotion_driven_kernel",
        "public_emotion_kernel",
        "compliance_boundary_kernel",
        "entry_system_kernel",
    ):
        raw = _mapping(metadata.get(key))
        if raw:
            picked[key] = _truncate_for_prompt(_json_dumps(raw), max_chars // 7)
    if not picked and not ideology_block:
        return ""
    head = f"\n\n{ideology_block}\n" if ideology_block else ""
    if not picked:
        return head
    return (
        f"{head}"
        "\n\n【压缩核心执行上下文】\n"
        f"{_truncate_for_prompt(_json_dumps(picked), max_chars)}\n"
    )


def _public_emotion_kernel_prompt_block(project: ProjectModel) -> str:
    metadata = project.metadata_json if isinstance(project.metadata_json, dict) else {}
    raw = _mapping(metadata.get("public_emotion_kernel"))
    if not raw:
        return ""
    try:
        return "\n\n" + render_public_emotion_prompt_block(
            raw,
            language=_planner_language(project),
        )
    except Exception:
        logger.debug("Failed to render PublicEmotionKernel prompt block", exc_info=True)
        return ""


def _compliance_boundary_prompt_block(project: ProjectModel) -> str:
    metadata = project.metadata_json if isinstance(project.metadata_json, dict) else {}
    raw = _mapping(metadata.get("compliance_boundary_kernel"))
    if not raw:
        return ""
    try:
        return "\n\n" + render_compliance_boundary_prompt_block(
            raw,
            language=_planner_language(project),
        )
    except Exception:
        logger.debug("Failed to render ComplianceBoundaryKernel prompt block", exc_info=True)
        return ""


def _entry_system_kernel_prompt_block(project: ProjectModel) -> str:
    metadata = project.metadata_json if isinstance(project.metadata_json, dict) else {}
    raw = _mapping(metadata.get("entry_system_kernel"))
    if not raw:
        return ""
    try:
        return "\n\n" + render_entry_system_kernel_prompt_block(raw)
    except Exception:
        logger.debug("Failed to render EntrySystemKernel prompt block", exc_info=True)
        return ""


def _entry_registry_prompt_block(project: ProjectModel) -> str:
    metadata = project.metadata_json if isinstance(project.metadata_json, dict) else {}
    raw = _mapping(metadata.get("entry_registry"))
    if not raw:
        return ""
    try:
        return "\n\n" + render_entry_registry_prompt_block(raw)
    except Exception:
        logger.debug("Failed to render EntryRegistry prompt block", exc_info=True)
        return ""


def _distilled_worldview_bindings_for_project(project: ProjectModel) -> dict[str, Any]:
    metadata = project.metadata_json if isinstance(project.metadata_json, dict) else {}
    distilled_card = _mapping(metadata.get("distilled_strategy_card"))
    if not distilled_card:
        return {}
    world_bindings = _mapping(distilled_card.get("worldview_bindings"))
    if world_bindings:
        return _mapping(sanitize_distilled_leak(world_bindings))
    try:
        from bestseller.services.distilled_worldview_bridge import (
            build_distilled_worldview_bindings,
        )

        return _mapping(sanitize_distilled_leak(build_distilled_worldview_bindings(distilled_card)))
    except Exception:
        logger.debug("Failed to rebuild distilled worldview bindings", exc_info=True)
        return {}


def _distilled_worldview_binding_list(
    world_bindings: Mapping[str, Any],
    key: str,
) -> list[dict[str, Any]]:
    return copy.deepcopy(_mapping_list(world_bindings.get(key)))


def _fallback_entry_system_kernel(
    project: ProjectModel,
    *,
    story_design_kernel: dict[str, Any] | None = None,
) -> dict[str, Any]:
    metadata = project.metadata_json if isinstance(project.metadata_json, dict) else {}
    project_like = {
        "genre": project.genre,
        "sub_genre": project.sub_genre,
        "category_key": metadata.get("category_key") or metadata.get("category"),
        "target_chapters": project.target_chapters,
    }
    kernel = build_fallback_entry_system_kernel(
        project_like,
        story_design_kernel=story_design_kernel,
    )
    return entry_system_kernel_to_dict(kernel)


def _persist_entry_system_kernel_metadata(
    project: ProjectModel,
    *,
    story_design_kernel: dict[str, Any] | None = None,
) -> dict[str, Any]:
    metadata = project.metadata_json if isinstance(project.metadata_json, dict) else {}
    existing = _mapping(metadata.get("entry_system_kernel"))
    if existing:
        try:
            payload = entry_system_kernel_to_dict(entry_system_kernel_from_dict(existing))
        except Exception:
            payload = _fallback_entry_system_kernel(
                project,
                story_design_kernel=story_design_kernel,
            )
    else:
        payload = _fallback_entry_system_kernel(
            project,
            story_design_kernel=story_design_kernel,
        )
    project.metadata_json = {
        **metadata,
        "entry_system_kernel": payload,
    }
    return payload


def _persist_entry_registry_metadata(
    project: ProjectModel,
    *,
    entry_system_kernel: dict[str, Any] | None = None,
) -> dict[str, Any]:
    metadata = project.metadata_json if isinstance(project.metadata_json, dict) else {}
    kernel_payload = entry_system_kernel or _mapping(metadata.get("entry_system_kernel"))
    if not kernel_payload:
        kernel_payload = _persist_entry_system_kernel_metadata(project)
        metadata = project.metadata_json if isinstance(project.metadata_json, dict) else {}
    kernel = entry_system_kernel_from_dict(kernel_payload)
    existing = _mapping(metadata.get("entry_registry"))
    if existing:
        try:
            registry_payload = entry_registry_to_dict(entry_registry_from_dict(existing))
        except Exception:
            registry_payload = {}
    else:
        registry_payload = {}
    if not registry_payload:
        coverage = build_entry_coverage_matrix(
            kernel,
            target_chapters=project.target_chapters,
            genre=project.genre,
        )
        registry = build_fallback_entry_registry(
            kernel,
            coverage,
            project_metadata=metadata,
        )
        registry_payload = entry_registry_to_dict(registry)
    project.metadata_json = {
        **metadata,
        "entry_system_kernel": entry_system_kernel_to_dict(kernel),
        "entry_registry": registry_payload,
    }
    return registry_payload


def _character_drama_prompt_block(
    project: ProjectModel,
    *,
    cast_spec: dict[str, Any] | None = None,
) -> str:
    metadata = project.metadata_json if isinstance(project.metadata_json, dict) else {}
    raw = _mapping(metadata.get("character_drama_map"))
    if raw:
        try:
            return "\n\n" + render_character_drama_prompt_block(raw)
        except Exception:
            logger.debug("Failed to render CharacterDramaMap prompt block", exc_info=True)
    if cast_spec:
        try:
            drama_map = build_character_drama_map(cast_spec, language=_planner_language(project))
            return "\n\n" + render_character_drama_prompt_block(drama_map)
        except Exception:
            logger.debug("Failed to build CharacterDramaMap prompt block", exc_info=True)
    return ""


def _persist_character_drama_map(project: ProjectModel, cast_spec: dict[str, Any]) -> None:
    try:
        character_drama_payload = character_drama_map_to_dict(
            build_character_drama_map(cast_spec, language=_planner_language(project))
        )
    except Exception:
        logger.debug("Failed to persist CharacterDramaMap metadata", exc_info=True)
        return
    project.metadata_json = {
        **(project.metadata_json or {}),
        "character_drama_map": character_drama_payload,
    }


def _fallback_beat_schedule(target_chapters: int, opening_hook: str) -> list[dict[str, Any]]:
    """Build a beat schedule whose ranges span the full target chapter count.

    The story_design_kernel_gate flags ``beat_schedule_incomplete`` when the
    schedule only covers the opening (max covered chapter <= 3) for a 10+ chapter
    book. The previous single ``1-3`` fallback entry tripped that gate against the
    framework's own fallback. This distributes structural phases across 1..N so
    the fallback satisfies its own gate and the embedded example stops teaching
    the LLM to emit opening-only beats.
    """
    n = max(1, int(target_chapters or 0))
    phases = [
        ("建立读者承诺、主线目标和第一轮状态变化。", "主角从被动处境转入带代价的主动选择。", "读者看到本书独有机制第一次产生结果。"),
        ("压力源升级，误解或对手抬升一档。", "主角的普通身份出现新裂缝，代价显形。", "一次中等兑现，同时挂出更高倒计时。"),
        ("中段转折，信息差反转，旧策略失效。", "主角被迫改变核心选择模式。", "关键真相或势力浮出，赌注跳档。"),
        ("逼近高潮，多线压力收束到单一抉择。", "代价全面兑现，退路被自己的胜利吃掉。", "倒数第二轮大兑现，锁死最终对决。"),
        ("高潮与闭环，完成或颠覆最终抉择。", "身份与权力关系被永久重写。", "全书核心承诺兑现并埋下续作钩子。"),
    ]
    phases = phases[: n] if n < len(phases) else phases
    count = len(phases)
    base, rem = divmod(n, count)
    beats: list[dict[str, Any]] = []
    start = 1
    for i, (duty, state_change, payoff) in enumerate(phases):
        size = max(1, base + (1 if i < rem else 0))
        end = min(n, start + size - 1)
        beats.append(
            {
                "chapter_range": str(start) if start == end else f"{start}-{end}",
                "duty": duty,
                "state_change": state_change,
                "payoff": payoff,
                "hook_or_aftereffect": opening_hook
                if i == 0
                else "上一阶段的代价转为下一阶段的新压力源。",
            }
        )
        start = end + 1
        if start > n:
            break
    return beats


def _fallback_story_design_kernel(
    project: ProjectModel,
    premise: str,
    book_spec: dict[str, Any],
    world_spec: dict[str, Any],
    cast_spec: dict[str, Any],
    *,
    category_key: str | None = None,
) -> dict[str, Any]:
    metadata = project.metadata_json if isinstance(project.metadata_json, dict) else {}
    concept_bundle = concept_lab_from_source(metadata)
    shape = derive_story_shape(
        project,
        genre=project.genre,
        sub_genre=project.sub_genre,
        target_chapters=project.target_chapters,
        target_word_count=project.target_word_count,
        audience=project.audience,
        metadata={**metadata, "category_key": category_key or ""},
    )
    grammar = resolve_story_design_grammar(
        category_key=category_key,
        genre=project.genre,
        sub_genre=project.sub_genre,
        metadata=metadata,
    )
    series_engine = _mapping(book_spec.get("series_engine"))
    book_protagonist = _mapping(book_spec.get("protagonist"))
    cast_protagonist = _mapping(cast_spec.get("protagonist"))
    protagonist_name = _first_non_empty_text(
        cast_protagonist.get("name"),
        book_protagonist.get("name"),
        default="主角" if not is_english_language(project.language) else "Protagonist",
    )
    protagonist_goal = _first_non_empty_text(
        cast_protagonist.get("goal"),
        book_protagonist.get("external_goal"),
        book_protagonist.get("goal"),
        default="完成核心目标"
        if not is_english_language(project.language)
        else "pursue the core goal",
    )
    world_rules = _mapping_list(world_spec.get("rules"))
    world_locations = _mapping_list(world_spec.get("locations"))
    world_factions = _mapping_list(world_spec.get("factions"))
    power_system = _mapping(world_spec.get("power_system"))
    distilled_world_bindings = _distilled_worldview_bindings_for_project(project)
    first_rule = world_rules[0] if world_rules else {}
    rule_name = _first_non_empty_text(
        first_rule.get("rule_name"),
        first_rule.get("name"),
        first_rule.get("description"),
        default="世界规则" if not is_english_language(project.language) else "world rule",
    )
    first_location = world_locations[0] if world_locations else {}
    first_faction = world_factions[0] if world_factions else {}
    power_system_name = _first_non_empty_text(
        power_system.get("name"),
        world_spec.get("power_structure"),
        default="核心体系" if not is_english_language(project.language) else "core system",
    )
    reader_promise = _first_non_empty_text(
        concept_bundle.reader_promise if concept_bundle else "",
        series_engine.get("reader_promise"),
        book_spec.get("reader_promise"),
        ", ".join(grammar.reader_rewards[:3]),
        default="每章都必须产生可见状态变化。",
    )
    unique_hook = _first_non_empty_text(
        concept_bundle.one_liner if concept_bundle else "",
        metadata.get("unique_hook"),
        book_spec.get("unique_hook"),
        book_spec.get("creative_hook"),
        book_spec.get("logline"),
        premise,
        default=project.title,
    )
    commercial_pull = _first_non_empty_text(
        concept_bundle.one_liner if concept_bundle else "",
        series_engine.get("core_serial_engine"),
        series_engine.get("core_engine"),
        book_spec.get("commercial_pull"),
        reader_promise,
    )
    change_vectors = (
        list(concept_bundle.story_loop.escalation_axis[:5] if concept_bundle else ())
        or list(grammar.chapter_change_vectors[:5])
        or list(shape.primary_duties[:5])
        or ["目标变化", "压力变化", "关系变化"]
    )
    concept_material_basis = (
        list(concept_bundle.material_brief.query_terms[:4]) if concept_bundle else []
    )
    concept_proof_criteria = (
        list(concept_bundle.story_loop.per_chapter_contract[:4]) if concept_bundle else []
    )
    macro_options = list(grammar.macro_structure_options)
    macro_structure_type = (
        "progressive_staircase"
        if "progressive_staircase" in macro_options
        else (macro_options[0] if macro_options else "progressive_staircase")
    )
    reader_desire_types = list(grammar.reader_desire_types[:3]) or [
        "curiosity",
        "respect_value",
        "control",
    ]
    event_pattern_types = list(grammar.conflict_event_types[:4]) or [
        "emotion_event",
        "obstacle_escalation",
        "method_search",
        "payoff_feedback",
    ]

    return {
        "version": 1,
        "shape": shape.model_dump(mode="json"),
        "reader_promise": reader_promise,
        "premise_contract": {
            "unique_hook": unique_hook,
            "core_question": _first_non_empty_text(
                book_spec.get("core_question"),
                default=f"{protagonist_name}能否在代价升级中完成目标？",
            ),
            "commercial_pull": commercial_pull,
            "forbidden_defaults": list(grammar.forbidden_defaults[:8]),
        },
        "character_conflict_contracts": [
            {
                "character_key": "protagonist",
                "external_goal": protagonist_goal,
                "internal_need": _first_non_empty_text(
                    cast_protagonist.get("internal_need"),
                    book_protagonist.get("internal_need"),
                    default="在选择中改变自身局限。",
                ),
                "pressure_source": _first_non_empty_text(
                    book_spec.get("central_conflict"),
                    series_engine.get("core_engine"),
                    default="外部目标和内部代价同时施压。",
                ),
                "choice_axis": _first_non_empty_text(
                    cast_protagonist.get("choice_axis"),
                    default="短期收益还是长期代价。",
                ),
                "change_vector": change_vectors[0],
            }
        ],
        "world_conflict_contracts": [
            {
                "axis": rule_name,
                "rule": _first_non_empty_text(
                    first_rule.get("description"),
                    first_rule.get("rule"),
                    default=f"{rule_name}会改变角色选择的成本。",
                ),
                "visible_cost": _first_non_empty_text(
                    first_rule.get("story_consequence"),
                    first_rule.get("visible_cost"),
                    default="违反或利用规则都会留下可见后果。",
                ),
                "escalation_path": "从局部问题扩大到全书主线压力。",
            }
        ],
        "four_causes_contract": {
            "purpose_result": _first_non_empty_text(
                book_spec.get("theme"),
                book_spec.get("theme_statement"),
                book_spec.get("core_question"),
                default="把本书主题目的转化为读者能感到的阶段结果。",
            ),
            "material_basis": [
                unique_hook,
                protagonist_goal,
                rule_name,
                power_system_name,
                *concept_material_basis,
            ],
            "formal_pattern": macro_structure_type,
            "driving_forces": [
                reader_promise,
                _first_non_empty_text(
                    book_spec.get("central_conflict"),
                    series_engine.get("core_engine"),
                    default="外部压力和内部选择持续推动事件单元。",
                ),
            ],
            "proof_criteria": [
                *concept_proof_criteria,
                "每个事件单元必须产生可见状态变化。",
                "事件六步跨章节分布，不要求每章完整重复。",
            ],
        },
        "macro_structure_contract": {
            "structure_type": macro_structure_type,
            "mainline_rule": "主线按事件单元递进，每个单元完成一次欲望、阻碍、行动和反馈的状态改变。",
            "subline_rule": "副线必须依附主线选择的成本、资源、关系或信息变化。",
            "rhythm_rule": "短兑现、阻碍升级、行动转折和余波交替出现。",
            "anti_homogeneity_rule": "每章只承担事件单元中的一个主要角色，禁止把完整六步作为固定章模板。",
        },
        "reader_desire_matrix": [
            {
                "desire_type": desire_type,
                "reader_expectation": f"读者期待看到{protagonist_name}通过{protagonist_goal}获得新的局势控制或情绪兑现。",
                "payoff_mode": reward,
                "risk_control": "连续章节不得复用同一种阻碍、同一种信息差或同一种尾钩。",
            }
            for desire_type, reward in zip(
                reader_desire_types,
                (list(grammar.reader_rewards[:3]) or ["短兑现", "新悬念", "状态变化"]),
                strict=False,
            )
        ],
        "event_pattern_inventory": [
            {
                "pattern_type": pattern_type,
                "use_case": "作为跨章节事件单元的一环使用。",
                "reader_effect": "制造期待、阻碍、解决欲或反馈余波。",
                "anti_repetition_rule": "同一事件角色不能连续主导过多章节，必须变换压力来源和信息差。",
            }
            for pattern_type in event_pattern_types
        ],
        "worldview_kernel": {
            "premise": _first_non_empty_text(
                world_spec.get("world_premise"),
                book_spec.get("world_premise"),
                premise,
                default=f"{project.title}的世界规则必须持续制造选择、代价和升级。",
            ),
            "uniqueness_principle": _first_non_empty_text(
                metadata.get("worldview_uniqueness_principle"),
                series_engine.get("worldview_uniqueness_principle"),
                default=(
                    "每个设定都必须转化为角色选择、资源约束、势力压力或章节后果。"
                    if not is_english_language(project.language)
                    else "Every world detail must become a choice, resource constraint, faction pressure, or chapter consequence."
                ),
            ),
            "invariants": [
                {
                    "key": "primary_world_rule",
                    "rule": _first_non_empty_text(
                        first_rule.get("description"),
                        first_rule.get("rule"),
                        default=f"{rule_name}决定角色推进主线时必须付出的代价。",
                    ),
                    "violation_cost": _first_non_empty_text(
                        first_rule.get("story_consequence"),
                        first_rule.get("visible_cost"),
                        default="绕过世界规则会产生可追踪的反噬、灵机损耗或身份暴露。",
                    ),
                    "narrative_use": "把世界观从背景说明转化为每章的障碍、工具和后果。",
                }
            ],
            "systems": [
                {
                    "name": power_system_name,
                    "operating_logic": _first_non_empty_text(
                        power_system.get("acquisition_method"),
                        world_spec.get("power_structure"),
                        default="角色必须通过明确规则取得资格、资源或力量。",
                    ),
                    "resources_or_authority": _first_non_empty_text(
                        power_system.get("resources_or_authority"),
                        first_rule.get("exploitation_potential"),
                        default="资格、资源、情报、关系和公开身份。",
                    ),
                    "limits": _first_non_empty_text(
                        power_system.get("hard_limits"),
                        default="任何突破都必须受限于门槛、代价、风险或他人反制。",
                    ),
                    "costs": _first_non_empty_text(
                        first_rule.get("story_consequence"),
                        default="每次使用核心体系都会改变角色状态或局势压力。",
                    ),
                    "failure_modes": ["规则失效", "资源透支", "敌方反制"],
                }
            ],
            "factions": [
                {
                    "name": _first_non_empty_text(
                        first_faction.get("name"), default="主要对抗势力"
                    ),
                    "public_role": _first_non_empty_text(
                        first_faction.get("goal"),
                        default="维护或争夺当前世界秩序。",
                    ),
                    "hidden_agenda": _first_non_empty_text(
                        first_faction.get("internal_conflict"),
                        default="利用世界规则压迫主角或改变主线走向。",
                    ),
                    "resources": _first_non_empty_text(
                        first_faction.get("method"),
                        default="人手、制度权限、资源渠道和情报。",
                    ),
                    "pressure_on_protagonist": _first_non_empty_text(
                        first_faction.get("relationship_to_protagonist"),
                        default="迫使主角在短期收益和长期代价之间选择。",
                    ),
                }
            ],
            "locations": [
                {
                    "name": _first_non_empty_text(first_location.get("name"), default="核心地点"),
                    "surface_function": _first_non_empty_text(
                        first_location.get("location_type"),
                        default="承载主线事件的公开空间。",
                    ),
                    "hidden_function": _first_non_empty_text(
                        first_location.get("story_role"),
                        default="暴露世界规则、势力利益或隐藏真相。",
                    ),
                    "conflict_sources": _string_list(first_location.get("key_rules"))[:3]
                    or [rule_name],
                    "evidence_or_resource_types": ["线索", "资源", "身份凭证"],
                }
            ],
            "reveal_ladder": [
                {
                    "stage": "opening_volume",
                    "reveal": "核心世界规则第一次被证明会改变角色命运。",
                    "earliest_volume": 1,
                    "earliest_chapter": 1,
                    "unlock_condition": "必须通过具体事件、证据或代价揭示，不能用设定说明替代。",
                }
            ],
            "integration_contract": {
                "chapter_rule": "每章至少让一个世界规则通过选择、证据、资源或代价落地。",
                "volume_rule": "每卷关闭一个局部世界规则冲突，并打开更高层级的秩序压力。",
                "reveal_rule": "未到揭示点的世界真相只能通过异常、物件、传闻或后果暗示。",
                "continuity_rule": "新增规则、地点、势力和代价必须回写到世界观设定档并被后续章节继承。",
            },
            "distilled_mechanism_bindings": _distilled_worldview_binding_list(
                distilled_world_bindings,
                "distilled_mechanism_bindings",
            ),
            "state_variables": _distilled_worldview_binding_list(
                distilled_world_bindings,
                "state_variables",
            ),
            "asset_ledger": _distilled_worldview_binding_list(
                distilled_world_bindings,
                "asset_ledger",
            ),
            "authority_claims": _distilled_worldview_binding_list(
                distilled_world_bindings,
                "authority_claims",
            ),
            "scene_templates": _distilled_worldview_binding_list(
                distilled_world_bindings,
                "scene_templates",
            ),
            "anti_copy_boundaries": _string_list(
                distilled_world_bindings.get("anti_copy_boundaries")
            ),
        },
        "structure_strategy": {
            "macro_strategy": _first_non_empty_text(
                series_engine.get("macro_strategy"),
                default="主线目标、角色选择和世界规则交替推进。",
            ),
            "chapter_engine": _first_non_empty_text(
                series_engine.get("chapter_hook_strategy"),
                default="每章推进至少两个状态维度。",
            ),
            "pacing_rule": _first_non_empty_text(
                series_engine.get("payoff_rhythm"),
                default="短兑现与长线伏笔交替。",
            ),
            "freshness_rule": "连续章节不得重复同一压力源、同一选择轴或同一回报类型。",
        },
        "plot_tree": [
            {
                "key": "mainline",
                "line_type": "main",
                "label": _first_non_empty_text(project.title, book_spec.get("title")),
                "role": "驱动全书外部目标。",
                "current_state": "主角尚未完成核心目标。",
                "target_state": "主角完成阶段目标并暴露下一层代价。",
                "failure_if_removed": "故事会失去主线推进和读者追读承诺。",
            },
            {
                "key": "protagonist-change",
                "line_type": "character",
                "label": f"{protagonist_name}的选择变化",
                "role": "把外部事件转化为人物状态变化。",
                "current_state": "旧策略仍在主导选择。",
                "target_state": "新选择模式开始形成。",
                "dependency_on_mainline": "主角每次选择都必须改变主线目标的成本或路径。",
                "failure_if_removed": "主线会退化为事件流水账。",
            },
        ],
        "beat_schedule": _fallback_beat_schedule(
            int(getattr(project, "target_chapters", 0) or 0),
            _first_non_empty_text(
                concept_bundle.story_loop.opening_question if concept_bundle else "",
                default="第一次选择留下未还清的代价或新压力源。",
            ),
        ),
        "change_vectors": change_vectors[:5],
        "uniqueness_constraints": [
            *list(concept_bundle.guardrails[:4] if concept_bundle else ()),
            "不得把亲属失踪/死亡、身世旧案、神秘信物、退婚羞辱作为默认驱动。",
            "每章必须写出具体状态变化，而不是只推进作者笔记。",
        ],
        "reverse_outline_status": "not_started",
    }


def _story_design_kernel_prompts(
    project: ProjectModel,
    premise: str,
    book_spec: dict[str, Any],
    world_spec: dict[str, Any],
    cast_spec: dict[str, Any],
    fallback_payload: dict[str, Any],
    *,
    category_key: str | None = None,
    ideology_block: str = "",
    world_model_block: str = "",
) -> tuple[str, str]:
    language = _planner_language(project)
    is_en = is_english_language(language)
    grammar = resolve_story_design_grammar(
        category_key=category_key,
        genre=project.genre,
        sub_genre=project.sub_genre,
        metadata=project.metadata_json if isinstance(project.metadata_json, dict) else {},
    )
    grammar_block = render_story_design_grammar_prompt_block(grammar)
    shape = derive_story_shape(project, metadata={"category_key": category_key or ""})
    try:
        character_drama_block = render_character_drama_prompt_block(
            build_character_drama_map(cast_spec, language=language)
        )
    except Exception:
        logger.debug("Failed to build CharacterDramaMap for story-design prompt", exc_info=True)
        character_drama_block = ""
    distilled_story_design_block = _distilled_design_reference_block(project, "story_design")
    public_emotion_block = _public_emotion_kernel_prompt_block(project)
    compliance_boundary_block = _compliance_boundary_prompt_block(project)
    concept_lab_contract_line = _concept_lab_contract_block(project, language=language)
    system_prompt = (
        "You are a story architect. Output one valid, fully-closed JSON object only. "
        "HARD COMPACTNESS CONSTRAINT: the JSON MUST be emitted complete in a single "
        "response and must never be truncated. Keep every explanatory string field "
        "terse (≤ 60 chars each), cap plot_tree at ≤ 10 nodes, keep beat_schedule beats "
        "≤ the target chapter count, and keep every list short. Do not expand any field "
        "verbosely. Keep the entire JSON under ~9000 characters — prefer terse over "
        "long-and-truncated. Include all required fields, but concisely."
        if is_en
        else "你是长篇小说剧情架构师。只输出一个合法且完整闭合的 JSON 对象，不要解释。"
        "【硬性紧凑约束】整个 JSON 必须一次性完整输出、绝不能被截断："
        "所有说明性字符串字段保持精炼（单条 ≤ 60 字），plot_tree 节点 ≤ 10 个、"
        "beat_schedule 节拍数 ≤ 目标章节数，各列表项从简；禁止任何字段长篇展开；"
        "确保整个 JSON 总长 ≤ 9000 字符。必填字段都要有，但宁可精炼也不可超长被截断。"
    )
    user_prompt = (
        (
            f"Project title: {project.title}\n"
            f"Premise:\n{premise}\n\n"
            f"Story shape: {shape.model_dump(mode='json')}\n"
            f"{grammar_block}\n\n"
            f"{concept_lab_contract_line}"
            f"BookSpec summary:\n{summarize_book_spec(book_spec, language='en')}\n"
            f"WorldSpec summary:\n{summarize_world_spec(world_spec, language='en')}\n"
            f"CastSpec summary:\n{summarize_cast_spec(cast_spec, language='en')}\n\n"
            f"{distilled_story_design_block}"
            f"{public_emotion_block}"
            f"{compliance_boundary_block}"
            f"{character_drama_block}\n\n"
            "Generate a StoryDesignKernel JSON object. It must contain reader_promise, "
            "premise_contract, character_conflict_contracts, world_conflict_contracts, "
            "worldview_kernel, structure_strategy, plot_tree, beat_schedule, change_vectors, and "
            "uniqueness_constraints. It should also include the optional writing-principle fields "
            "four_causes_contract, macro_structure_contract, reader_desire_matrix, and "
            "event_pattern_inventory. Treat event patterns as multi-chapter event-unit roles; "
            "do not require every chapter to carry all six event steps. Every non-main plot_tree "
            "node must depend on the mainline. "
            "The worldview_kernel is the book-specific operating system: define invariants, "
            "systems, factions, locations, reveal_ladder, and integration_contract so volume "
            "and chapter planning must obey the world. Also preserve data-driven execution "
            "fields when present: distilled_mechanism_bindings, state_variables, asset_ledger, "
            "authority_claims, scene_templates, and anti_copy_boundaries. State variables must "
            "be book-specific; assets must include visible cost/exposure; authority claims must "
            "explain legitimacy; anti-copy boundaries must remain. "
            "Do not use family disappearance/death, hidden lineage cases, magic objects, or generic revenge as default motivation.\n\n"
            f"Use this schema-compatible fallback as the minimum structure:\n{_json_dumps(fallback_payload)}"
        )
        if is_en
        else (
            f"项目标题：{project.title}\n"
            f"前提：\n{premise}\n\n"
            f"故事形态：{shape.model_dump(mode='json')}\n"
            f"{grammar_block}\n\n"
            f"{concept_lab_contract_line}"
            f"BookSpec 摘要：\n{summarize_book_spec(book_spec, language='zh')}\n"
            f"WorldSpec 摘要：\n{summarize_world_spec(world_spec, language='zh')}\n"
            f"CastSpec 摘要：\n{summarize_cast_spec(cast_spec, language='zh')}\n\n"
            f"{distilled_story_design_block}"
            f"{public_emotion_block}"
            f"{compliance_boundary_block}"
            f"{character_drama_block}\n\n"
            "请生成 StoryDesignKernel JSON 对象，必须包含 reader_promise、premise_contract、"
            "character_conflict_contracts、world_conflict_contracts、worldview_kernel、structure_strategy、plot_tree、"
            "beat_schedule、change_vectors、uniqueness_constraints。plot_tree 中每条非主线都必须说明"
            " dependency_on_mainline；每条线都必须说明 failure_if_removed。"
            "同时建议包含写作原理扩展字段：four_causes_contract、macro_structure_contract、"
            "reader_desire_matrix、event_pattern_inventory。事件模式是跨章节的事件单元角色，"
            "不能要求每一章都完整承载六步，否则会造成章节同质化。"
            "worldview_kernel 是本书专属世界观操作系统，必须定义 invariants、systems、factions、"
            "locations、reveal_ladder、integration_contract，让卷纲和章纲必须遵循这个世界。"
            "同时必须保留可数据化执行字段：distilled_mechanism_bindings、state_variables、"
            "asset_ledger、authority_claims、scene_templates、anti_copy_boundaries。"
            "state_variables 必须是本书专属状态变量；asset 必须包含可见 cost/exposure；"
            "authority_claims 必须说明合法性；anti_copy_boundaries 必须保留并约束后续生成。"
            "禁止把亲属失踪/死亡、身世旧案、神秘信物、退婚羞辱、通用复仇当作默认驱动。\n\n"
            f"以下 fallback 是最低结构要求，请在此基础上做出本书独有设计：\n{_json_dumps(fallback_payload)}"
        )
    )
    # Inject the ideology (母题) kernel right after the premise so the worldview /
    # plot_tree / beat_schedule are read as serving the book's thematic spine,
    # not just its genre. Insert once, after the first occurrence of the premise.
    if ideology_block and premise and premise in user_prompt:
        header = (
            "\n\n## Ideology Kernel — the book's thematic spine "
            "(worldview/plot_tree/beat_schedule MUST serve it)\n"
            if is_en
            else "\n\n## 核心理念内核 — 本书思想脊柱"
            "（worldview_kernel / plot_tree / beat_schedule 都必须服务它）\n"
        )
        user_prompt = user_prompt.replace(
            premise, premise + header + ideology_block, 1
        )
    # Inject the world model (世界宪法) so worldview_kernel / invariants / systems
    # are derived to OBEY the differential world laws, not bolted on. Additive and
    # fail-safe: an empty block leaves the prompt unchanged.
    if world_model_block:
        wm_header = (
            "\n\n## World Model — the book's world constitution "
            "(worldview_kernel/invariants/systems MUST obey these derived laws)\n"
            if is_en
            else "\n\n## 世界模型 — 本书世界宪法"
            "（worldview_kernel / invariants / systems 必须遵守并从这些推演规律生长）\n"
        )
        user_prompt = f"{user_prompt}{wm_header}{world_model_block}"
    return system_prompt, user_prompt


async def _generate_story_design_kernel(
    session: AsyncSession,
    settings: AppSettings,
    *,
    project: ProjectModel,
    project_slug: str,
    premise: str,
    book_spec_payload: dict[str, Any],
    world_spec_payload: dict[str, Any],
    cast_spec_payload: dict[str, Any],
    category_key: str | None,
    workflow_run_id: UUID,
    step_order: int,
    llm_run_ids: list[UUID],
    artifact_records: list[PlanningArtifactRecord],
) -> dict[str, Any]:
    fallback = _fallback_story_design_kernel(
        project,
        premise,
        book_spec_payload,
        world_spec_payload,
        cast_spec_payload,
        category_key=category_key,
    )
    # Derive the core-ideology (母题) kernel BEFORE the story-design kernel so the
    # worldview / plot_tree / beat_schedule grow from the book's thematic spine
    # instead of genre tropes alone. Fully fail-safe: any failure degrades to a
    # no-ideology prompt rather than breaking planning.
    ideology_payload: dict[str, Any] | None = None
    ideology_block = ""
    target_chapters = int(getattr(project, "target_chapters", 0) or 0)
    est_volumes = max(1, round(target_chapters / 50)) if target_chapters else 1
    try:
        ideology_kernel = await derive_ideology_kernel(
            session,
            settings,
            premise=premise,
            genre=getattr(project, "genre", None),
            book_spec=book_spec_payload,
            volumes=est_volumes,
            title=str(getattr(project, "title", "") or ""),
            language=_planner_language(project),
            project_id=getattr(project, "id", None),
            workflow_run_id=workflow_run_id,
        )
        ideology_payload = ideology_kernel_to_dict(ideology_kernel)
        ideology_block = render_ideology_kernel_prompt_block(ideology_kernel)
    except Exception:  # noqa: BLE001 - ideology is additive; never block planning
        logger.debug("Ideology kernel derivation failed; planning without it", exc_info=True)
    # Derive the WorldModel (世界宪法) by differential mapping so the worldview_kernel
    # GROWS FROM the world's derived laws (currency/class/transport/...) instead of
    # being bolted on. Genre is context-only; laws are anchored to THIS book's
    # axioms. Fully fail-safe: any failure degrades to a no-world-model prompt.
    world_model_payload: dict[str, Any] | None = None
    world_model_block = ""
    try:
        # 复用构思阶段已派生的世界模型(2026-07-08 设定/逻辑框架层)——构思终稿的
        # 机制因果账审计已经用它约束了金手指/代价,这里若重新派生会用"已终稿的
        # premise"生成一份不同的世界模型,造成两阶段各信各的漂移。只有构思阶段
        # 未产出(旧书/失败降级)时才在这里补派生。
        _conception_wm = getattr(project, "metadata_json", None)
        _conception_wm = (
            _conception_wm.get("world_model")
            if isinstance(_conception_wm, dict)
            else None
        )
        if isinstance(_conception_wm, dict) and _conception_wm.get("world_laws"):
            world_model = world_model_from_dict(_conception_wm)
            world_model_payload = _conception_wm
        else:
            world_model = await derive_world_model(
                session,
                settings,
                premise=premise,
                genre=getattr(project, "genre", None),
                language=_planner_language(project),
                project_id=getattr(project, "id", None),
                workflow_run_id=workflow_run_id,
            )
            world_model_payload = world_model_to_dict(world_model)
        world_model_block = render_world_model_prompt_block(world_model)
    except Exception:  # noqa: BLE001 - world model is additive; never block planning
        logger.debug("World model derivation failed; planning without it", exc_info=True)
    system_prompt, user_prompt = _story_design_kernel_prompts(
        project,
        premise,
        book_spec_payload,
        world_spec_payload,
        cast_spec_payload,
        fallback,
        category_key=category_key,
        ideology_block=ideology_block,
        world_model_block=world_model_block,
    )
    payload, llm_run_id = await _generate_structured_artifact(
        session,
        settings,
        project=project,
        logical_name="story_design_kernel",
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        fallback_payload=fallback,
        workflow_run_id=workflow_run_id,
        validator=_validate_story_design_kernel_payload,
    )
    if llm_run_id is not None:
        llm_run_ids.append(llm_run_id)
    if not isinstance(payload, dict):
        payload = fallback

    # MiroFish Simulation Oracle 增强(默认关闭 / 失败安全降级 / 见 simulation_oracle_planner)。
    payload = await augment_story_design_kernel_with_oracle(
        session,
        settings,
        payload=payload,
        project=project,
        premise=premise,
        cast_spec_payload=cast_spec_payload,
    )

    # Carry the derived WorldModel (世界宪法) on the kernel payload so downstream
    # stages (prose injection, world-law consistency gate) read the SAME single
    # source of truth. Stored on the raw payload (persisted in metadata_json),
    # additive to the validated StoryDesignKernel schema.
    if isinstance(payload, dict) and world_model_payload and not payload.get("world_model"):
        payload["world_model"] = world_model_payload

    # Carry the ideology (母题) kernel inside the StoryDesignKernel so it propagates
    # to every downstream prompt via render_story_design_kernel_prompt_block. Run
    # the structural coherence gate as an ADVISORY check (never hard-aborts; per
    # the 2026-05/06 regression lesson the theme levers report+repair, not block).
    if isinstance(payload, dict) and ideology_payload and not payload.get("ideology_kernel"):
        payload["ideology_kernel"] = ideology_payload
        try:
            ideology_gate = evaluate_ideology_kernel_coherence(
                ideology_payload, volumes=est_volumes, total_chapters=target_chapters
            )
            logger.info(
                "Ideology kernel attached: thesis=%r gate=%s coverage=%.2f",
                ideology_payload.get("thesis_statement", ""),
                ideology_gate.verdict,
                ideology_gate.coverage,
            )
        except Exception:  # noqa: BLE001 - advisory gate must never break planning
            logger.debug("Ideology coherence gate failed", exc_info=True)

    story_design_kernel_from_dict(payload)
    quality_report = evaluate_story_design_kernel_quality(
        payload,
        target_chapters=int(getattr(project, "target_chapters", 0) or 0),
    )
    await _record_prewrite_quality_gate(
        session,
        workflow_run_id=workflow_run_id,
        step_name="story_design_kernel_gate",
        step_order=step_order,
        report=quality_report,
        strict=is_strict_prewrite_project(project),
    )
    payload = _attach_prewrite_quality_report(
        payload,
        step_name="story_design_kernel_gate",
        report=quality_report,
        project=project,
    )
    try:
        character_drama_payload = character_drama_map_to_dict(
            build_character_drama_map(cast_spec_payload, language=_planner_language(project))
        )
    except Exception:
        logger.debug("Failed to build CharacterDramaMap metadata", exc_info=True)
        character_drama_payload = {}
    artifact = await import_planning_artifact(
        session,
        project_slug,
        PlanningArtifactCreate(
            artifact_type=ArtifactType.STORY_DESIGN_KERNEL,
            content=payload,
        ),
    )
    artifact_records.append(
        PlanningArtifactRecord(
            artifact_type=ArtifactType.STORY_DESIGN_KERNEL,
            artifact_id=artifact.id,
            version_no=artifact.version_no,
        )
    )
    metadata = {
        **(project.metadata_json or {}),
        "story_design_kernel": payload,
    }
    if character_drama_payload:
        metadata["character_drama_map"] = character_drama_payload
    project.metadata_json = metadata
    await create_workflow_step_run(
        session,
        workflow_run_id=workflow_run_id,
        step_name="generate_story_design_kernel",
        step_order=step_order + 1,
        status=WorkflowStatus.COMPLETED,
        output_ref={
            "artifact_id": str(artifact.id),
            "llm_run_id": str(llm_run_id) if llm_run_id else None,
        },
    )
    return payload


async def _generate_entry_system_kernel_artifacts(
    session: AsyncSession,
    *,
    project: ProjectModel,
    project_slug: str,
    story_design_kernel: dict[str, Any] | None,
    workflow_run_id: UUID,
    step_order: int,
    artifact_records: list[PlanningArtifactRecord],
) -> dict[str, Any]:
    payload = _persist_entry_system_kernel_metadata(
        project,
        story_design_kernel=story_design_kernel,
    )
    registry_payload = _persist_entry_registry_metadata(
        project,
        entry_system_kernel=payload,
    )
    artifact = await import_planning_artifact(
        session,
        project_slug,
        PlanningArtifactCreate(
            artifact_type=ArtifactType.ENTRY_SYSTEM_KERNEL,
            content=payload,
        ),
    )
    artifact_records.append(
        PlanningArtifactRecord(
            artifact_type=ArtifactType.ENTRY_SYSTEM_KERNEL,
            artifact_id=artifact.id,
            version_no=artifact.version_no,
        )
    )
    await create_workflow_step_run(
        session,
        workflow_run_id=workflow_run_id,
        step_name="generate_entry_system_kernel",
        step_order=step_order,
        status=WorkflowStatus.COMPLETED,
        output_ref={
            "artifact_id": str(artifact.id),
            "registry_entry_count": len(registry_payload.get("entries") or []),
        },
    )
    return payload


def _fallback_emotion_driven_kernel(
    project: ProjectModel,
    premise: str,
    book_spec: dict[str, Any],
    world_spec: dict[str, Any],
    cast_spec: dict[str, Any],
    *,
    story_design_kernel: dict[str, Any] | None = None,
    category_key: str | None = None,
) -> dict[str, Any]:
    is_en = is_english_language(project.language)
    metadata = project.metadata_json if isinstance(project.metadata_json, dict) else {}
    story_design = _mapping(story_design_kernel) or _mapping(metadata.get("story_design_kernel"))
    premise_contract = _mapping(story_design.get("premise_contract"))
    beat_schedule = _mapping_list(story_design.get("beat_schedule"))
    first_beat = beat_schedule[0] if beat_schedule else {}

    series_engine = _mapping(book_spec.get("series_engine"))
    book_protagonist = _mapping(book_spec.get("protagonist"))
    cast_protagonist = _mapping(cast_spec.get("protagonist"))
    antagonist = _mapping(cast_spec.get("antagonist"))
    antagonists = _mapping_list(cast_spec.get("antagonists"))
    if not antagonist and antagonists:
        antagonist = antagonists[0]

    protagonist_name = _first_non_empty_text(
        cast_protagonist.get("name"),
        book_protagonist.get("name"),
        default="Protagonist" if is_en else "主角",
    )
    protagonist_goal = _first_non_empty_text(
        cast_protagonist.get("goal"),
        book_protagonist.get("external_goal"),
        book_protagonist.get("goal"),
        default="resolve the central crisis" if is_en else "解决核心危机",
    )
    protagonist_need = _first_non_empty_text(
        cast_protagonist.get("internal_need"),
        book_protagonist.get("internal_need"),
        default="grow beyond an old survival strategy" if is_en else "突破旧有生存策略",
    )
    protagonist_flaw = _first_non_empty_text(
        cast_protagonist.get("flaw"),
        cast_protagonist.get("core_wound"),
        book_protagonist.get("core_wound"),
        default="tries to control the situation alone" if is_en else "习惯独自掌控局面",
    )
    antagonist_name = _first_non_empty_text(
        antagonist.get("name"),
        antagonist.get("force_name"),
        default="opposing force" if is_en else "对立力量",
    )
    antagonist_goal = _first_non_empty_text(
        antagonist.get("goal"),
        antagonist.get("agenda"),
        antagonist.get("motivation"),
        default="keep control of the existing order" if is_en else "维持既有秩序的控制权",
    )
    hidden_desire = _first_non_empty_text(
        antagonist.get("hidden_desire"),
        antagonist.get("motivation"),
        antagonist_goal,
    )

    world_rules = _mapping_list(world_spec.get("rules"))
    first_rule = world_rules[0] if world_rules else {}
    rule_name = _first_non_empty_text(
        first_rule.get("rule_name"),
        first_rule.get("name"),
        first_rule.get("axis"),
        default="world rule" if is_en else "世界规则",
    )
    rule_cost = _first_non_empty_text(
        first_rule.get("visible_cost"),
        first_rule.get("story_consequence"),
        first_rule.get("description"),
        default="every shortcut leaves a visible cost" if is_en else "每次捷径都会留下可见代价",
    )
    reader_promise = _first_non_empty_text(
        story_design.get("reader_promise"),
        book_spec.get("reader_promise"),
        series_engine.get("reader_promise"),
        default=(
            f"Readers will track how {protagonist_name}'s choices convert pressure into irreversible change."
            if is_en
            else f"读者会追看{protagonist_name}如何在压力中做选择，并承担不可逆变化。"
        ),
    )
    unique_hook = _first_non_empty_text(
        premise_contract.get("unique_hook"),
        metadata.get("unique_hook"),
        book_spec.get("unique_hook"),
        book_spec.get("creative_hook"),
        premise,
        default=project.title,
    )
    central_conflict = _first_non_empty_text(
        book_spec.get("central_conflict"),
        premise_contract.get("core_question"),
        premise,
        default=f"{protagonist_name} versus {antagonist_name}"
        if is_en
        else f"{protagonist_name}与{antagonist_name}的正面冲突",
    )
    target_chapters = max(int(project.target_chapters or 0), 1)
    opening_end = min(target_chapters, 3)
    opening_range = f"1-{opening_end}" if opening_end > 1 else "1"
    whole_range = f"1-{target_chapters}"
    category_hint = category_key or _first_non_empty_text(project.genre, project.sub_genre)
    callback_motif = _first_non_empty_text(
        first_beat.get("hook_or_aftereffect"),
        first_beat.get("payoff"),
        rule_name,
        default="first visible cost" if is_en else "第一次可见代价",
    )

    ending_probe = " ".join(
        _first_non_empty_text(value)
        for value in (
            book_spec.get("ending"),
            book_spec.get("ending_type"),
            _mapping(story_design.get("shape")).get("ending_contract"),
        )
        if _first_non_empty_text(value)
    ).lower()
    ending_type = (
        "BE" if any(token in ending_probe for token in ("be", "bad", "tragic", "悲")) else "HE"
    )
    if ending_type == "BE":
        ending_contract = {
            "ending_type": "BE",
            "core_wish_fulfilled": (
                f"{protagonist_name} reaches the truth or love that once looked possible."
                if is_en
                else f"{protagonist_name}真正触碰到曾经可能拥有的真相或幸福。"
            ),
            "irreversible_cost_retained": (
                "The cost paid on the way cannot be restored."
                if is_en
                else "一路付出的代价不能被结尾清零。"
            ),
            "tragic_causality": (
                f"{central_conflict} makes the desired happiness incompatible with the value being protected."
                if is_en
                else f"{central_conflict}让圆满与必须守住的价值发生不可调和的冲突。"
            ),
            "active_value_choice": (
                f"{protagonist_name} actively chooses the value that matters more than personal completion."
                if is_en
                else f"{protagonist_name}主动选择比个人圆满更重要的价值。"
            ),
            "aesthetic_callback": callback_motif,
        }
    else:
        ending_contract = {
            "ending_type": "HE",
            "core_wish_fulfilled": (
                f"{protagonist_name} wins a real future, not a reset."
                if is_en
                else f"{protagonist_name}获得真实未来，而不是一键清零。"
            ),
            "relationship_settlement": (
                "The key relationship receives an explicit answer and new position."
                if is_en
                else "关键关系得到明确回应，并进入新的位置。"
            ),
            "irreversible_cost_retained": (
                "The wound, lost time, or paid price remains acknowledged."
                if is_en
                else "伤痕、错过的时间或已付代价仍被承认。"
            ),
            "theme_answer": (
                f"{protagonist_name} proves that the core victory is a chosen value, not only an event result."
                if is_en
                else f"{protagonist_name}证明真正的胜利是价值选择，而不只是事件结果。"
            ),
            "future_open": (
                "The next life is possible while the past remains unrepaired."
                if is_en
                else "未来仍然打开，但过去不会被强行复原。"
            ),
        }

    return {
        "version": 1,
        "reader_emotion_promise": reader_promise,
        "primary_reader_waiting": [
            (
                f"whether {protagonist_name} can achieve {protagonist_goal} without losing {protagonist_need}"
                if is_en
                else f"{protagonist_name}能否完成{protagonist_goal}，又不失去{protagonist_need}"
            ),
            (
                f"when {antagonist_name}'s public mask breaks"
                if is_en
                else f"{antagonist_name}的体面何时露出裂缝"
            ),
            (
                f"how {rule_name} turns into an irreversible cost"
                if is_en
                else f"{rule_name}如何转化成不可逆代价"
            ),
        ],
        "empathy_contracts": [
            {
                "contract_id": "protagonist-opening-empathy",
                "character_key": "protagonist",
                "chapter_range": opening_range,
                "situation": (
                    f"{protagonist_name} is forced into the first visible pressure of {unique_hook}."
                    if is_en
                    else f"{protagonist_name}被推入『{unique_hook}』的第一轮可见压力。"
                ),
                "current_desire": protagonist_goal,
                "fear_or_loss": (
                    f"Failure means losing the path to {protagonist_need}."
                    if is_en
                    else f"失败意味着失去{protagonist_need}的可能。"
                ),
                "flaw_pressure": protagonist_flaw,
                "sensory_entry": (
                    "a concrete sound, smell, pain point, or spatial limit that narrows judgment"
                    if is_en
                    else "用声音、气味、疼痛或空间限制把读者压进现场判断"
                ),
                "judgment_logic": (
                    f"{protagonist_name} must judge through limited information and the old flaw."
                    if is_en
                    else f"{protagonist_name}必须在信息不足和旧缺点压力下判断。"
                ),
                "emotional_reaction": (
                    "fear, shame, anger, or desire appears before the competent move."
                    if is_en
                    else "先出现恐惧、羞耻、愤怒或欲望，再进入能力行动。"
                ),
                "reasonable_action": (
                    "make a bounded, risky choice rather than a clean omniscient solution"
                    if is_en
                    else "做出有边界、有风险的选择，而不是全知式解法"
                ),
                "consequence": (
                    "the action wins one step while opening the next unresolved threat."
                    if is_en
                    else "行动赢下一步，同时制造下一个未解的隐患。"
                ),
            }
        ],
        "bomb_contracts": [
            {
                "bomb_id": "opening-information-bomb",
                "bomb_type": "danger",
                "chapter_range": opening_range,
                "reader_knows": (
                    f"{antagonist_name} or {rule_name} is already creating a trap behind the visible goal."
                    if is_en
                    else f"读者先知道{antagonist_name}或{rule_name}已经在可见目标背后设局。"
                ),
                "character_blindspot": (
                    f"{protagonist_name} is focused on {protagonist_goal} and lacks the full context."
                    if is_en
                    else f"{protagonist_name}正专注于{protagonist_goal}，还不知道完整危险。"
                ),
                "danger": central_conflict,
                "trigger_condition": (
                    "the protagonist accepts the first apparent shortcut"
                    if is_en
                    else "主角接受第一个看似可行的捷径"
                ),
                "countdown": (
                    "within the opening three chapters" if is_en else "前三章内必须触发或升级"
                ),
                "consequence": rule_cost,
                "payoff_window": (
                    "the opening hook must explode or visibly transform into a larger long-arc threat"
                    if is_en
                    else "开篇钩子必须爆开，或明确转化为更大的长线威胁/代价"
                ),
                "rational_ignorance": (
                    f"{protagonist_name}'s blindspot is credible because the clue is hidden inside a trusted rule or urgent pressure."
                    if is_en
                    else f"{protagonist_name}不知道危险是合理的：线索藏在可信规则或紧急压力里。"
                ),
                "escalation_steps": [
                    "danger approaches" if is_en else "危险逼近",
                    "near discovery fails" if is_en else "差点发现但错过",
                    "payoff creates a new open loop" if is_en else "兑现后留下新的悬念缺口",
                ],
            }
        ],
        "antagonist_moral_contracts": [
            {
                "antagonist_key": antagonist_name,
                "chapter_range": whole_range,
                "public_mask": (
                    f"{antagonist_name} appears to protect order, safety, or fairness."
                    if is_en
                    else f"{antagonist_name}表面上在维护秩序、安全或公道。"
                ),
                "real_good_deeds": [
                    (
                        "has genuinely solved a visible problem for others"
                        if is_en
                        else "确实为他人解决过一个可见问题"
                    )
                ],
                "hidden_desire": hidden_desire,
                "fear_of_loss": (
                    "losing status, control, or the story used to justify past sacrifices"
                    if is_en
                    else "害怕失去地位、控制权，或曾经牺牲的正当性"
                ),
                "cracks": [
                    (
                        "speaks of the greater good only when the result benefits them"
                        if is_en
                        else "总在结果有利于自己时强调大局"
                    )
                ],
                "first_boundary_crossing": (
                    "sacrifices a weaker person while calling it necessary"
                    if is_en
                    else "第一次以必要之名牺牲弱者"
                ),
                "self_justification": (
                    "the world would collapse without this control"
                    if is_en
                    else "没有这种控制，世界只会更糟"
                ),
                "collapse_wound": (
                    "cannot accept that the protected order was also self-protection"
                    if is_en
                    else "无法承认所谓维护秩序也在保护自己的欲望"
                ),
                "target_reader_response": (
                    "hate, understand the cause, and remember the wound"
                    if is_en
                    else "恨他、理解成因、记住伤口"
                ),
            }
        ],
        "ending_texture_contract": ending_contract,
        "emotion_chain": [
            {
                "chapter_range": opening_range,
                "target_reader_emotion": "anxiety + anticipation" if is_en else "焦虑 + 期待",
                "reader_waiting_for": (
                    f"the first irreversible result of {unique_hook}"
                    if is_en
                    else f"『{unique_hook}』第一次产生不可逆结果"
                ),
                "reader_worry": (
                    f"{protagonist_name} will choose too late or choose the wrong cost."
                    if is_en
                    else f"{protagonist_name}会太晚选择，或选错代价。"
                ),
                "pressure_source": central_conflict,
                "payoff_or_aftereffect": _first_non_empty_text(
                    first_beat.get("payoff"),
                    first_beat.get("hook_or_aftereffect"),
                    default="first payoff opens a larger unresolved stake"
                    if is_en
                    else "第一次兑现牵出更大的未解之局",
                ),
                "callback": callback_motif,
            }
        ],
        "callback_motifs": [
            callback_motif,
            category_hint,
        ],
    }


def build_emotion_driven_kernel_backfill_payload(
    project: ProjectModel,
    *,
    premise: str | None = None,
    book_spec: dict[str, Any] | None = None,
    world_spec: dict[str, Any] | None = None,
    cast_spec: dict[str, Any] | None = None,
    story_design_kernel: dict[str, Any] | None = None,
    category_key: str | None = None,
) -> dict[str, Any]:
    """Build a conservative emotion kernel for legacy projects.

    This is intentionally deterministic and LLM-free.  Old projects often
    have only partial planning metadata, so each input falls back to the same
    local planning defaults used by normal plan generation.
    """

    metadata = project.metadata_json if isinstance(project.metadata_json, dict) else {}
    resolved_category = category_key or _first_non_empty_text(
        metadata.get("category_key"),
        default="",
    )
    if not resolved_category:
        category = resolve_novel_category(project.genre, project.sub_genre)
        resolved_category = category.key if category else None

    resolved_premise = _first_non_empty_text(
        premise,
        metadata.get("premise"),
        metadata.get("logline"),
        metadata.get("unique_hook"),
        project.dramatic_question,
        project.theme_statement,
        project.title,
        default=project.title,
    )
    resolved_book_spec = _mapping(book_spec) or _mapping(metadata.get("book_spec"))
    if not resolved_book_spec:
        resolved_book_spec = _fallback_book_spec(
            project,
            resolved_premise,
            category_key=resolved_category,
        )

    resolved_world_spec = _mapping(world_spec) or _mapping(metadata.get("world_spec"))
    if not resolved_world_spec:
        resolved_world_spec = _fallback_world_spec(
            project,
            resolved_premise,
            resolved_book_spec,
            category_key=resolved_category,
        )

    resolved_cast_spec = _mapping(cast_spec) or _mapping(metadata.get("cast_spec"))
    if not resolved_cast_spec:
        resolved_cast_spec = _fallback_cast_spec(
            project,
            resolved_premise,
            resolved_book_spec,
            resolved_world_spec,
            category_key=resolved_category,
        )

    resolved_story_design = _mapping(story_design_kernel) or _mapping(
        metadata.get("story_design_kernel")
    )
    payload = _fallback_emotion_driven_kernel(
        project,
        resolved_premise,
        resolved_book_spec,
        resolved_world_spec,
        resolved_cast_spec,
        story_design_kernel=resolved_story_design,
        category_key=resolved_category,
    )
    return emotion_driven_kernel_to_dict(emotion_driven_kernel_from_dict(payload))


def _emotion_driven_kernel_prompts(
    project: ProjectModel,
    premise: str,
    book_spec: dict[str, Any],
    world_spec: dict[str, Any],
    cast_spec: dict[str, Any],
    fallback_payload: dict[str, Any],
    *,
    story_design_kernel: dict[str, Any] | None = None,
) -> tuple[str, str]:
    language = _planner_language(project)
    is_en = is_english_language(language)
    story_design_block = ""
    if story_design_kernel:
        try:
            story_design_block = "\n\n" + render_story_design_kernel_prompt_block(
                story_design_kernel
            )
        except Exception:
            logger.debug(
                "Failed to render StoryDesignKernel for emotion-driven prompt",
                exc_info=True,
            )
    public_emotion_block = _public_emotion_kernel_prompt_block(project)
    compliance_boundary_block = _compliance_boundary_prompt_block(project)

    system_prompt = (
        "You are a fiction emotion-architecture designer. Output one valid JSON object only."
        if is_en
        else "你是长篇小说情绪架构师。只输出一个合法 JSON 对象，不要解释。"
    )
    user_prompt = (
        (
            f"Project title: {project.title}\n"
            f"Premise:\n{premise}\n\n"
            f"BookSpec summary:\n{summarize_book_spec(book_spec, language='en')}\n"
            f"WorldSpec summary:\n{summarize_world_spec(world_spec, language='en')}\n"
            f"CastSpec summary:\n{summarize_cast_spec(cast_spec, language='en')}\n"
            f"{story_design_block}\n\n"
            f"{public_emotion_block}\n"
            f"{compliance_boundary_block}\n"
            "Generate an EmotionDrivenKernel JSON object. It must turn plot into reader emotion contracts, not generic advice.\n"
            "Required top-level fields: version, reader_emotion_promise, primary_reader_waiting, empathy_contracts, bomb_contracts, antagonist_moral_contracts, ending_texture_contract, emotion_chain, callback_motifs.\n"
            "Empathy contracts must include situation, current_desire, fear_or_loss, sensory_entry, judgment_logic, reasonable_action, and consequence.\n"
            "Bomb contracts must include reader_knows, character_blindspot, danger, trigger_condition, countdown, consequence, payoff_window, and rational_ignorance.\n"
            "Antagonist moral contracts must include public_mask, real_good_deeds, hidden_desire, fear_of_loss, cracks, first_boundary_crossing, self_justification, and collapse_wound.\n"
            "HE must fulfill happiness while retaining irreversible cost. BE must have unavoidable causality, active value choice, and aesthetic callback.\n\n"
            f"Use this schema-compatible fallback as the minimum structure:\n{_json_dumps(fallback_payload)}"
        )
        if is_en
        else (
            f"项目标题：{project.title}\n"
            f"前提：\n{premise}\n\n"
            f"BookSpec 摘要：\n{summarize_book_spec(book_spec, language='zh')}\n"
            f"WorldSpec 摘要：\n{summarize_world_spec(world_spec, language='zh')}\n"
            f"CastSpec 摘要：\n{summarize_cast_spec(cast_spec, language='zh')}\n"
            f"{story_design_block}\n\n"
            f"{public_emotion_block}\n"
            f"{compliance_boundary_block}\n"
            "请生成 EmotionDrivenKernel JSON 对象。它要把剧情转成读者情绪合同，而不是泛泛写作建议。\n"
            "顶层必须包含：version、reader_emotion_promise、primary_reader_waiting、empathy_contracts、bomb_contracts、antagonist_moral_contracts、ending_texture_contract、emotion_chain、callback_motifs。\n"
            "代入合同必须包含 situation、current_desire、fear_or_loss、sensory_entry、judgment_logic、reasonable_action、consequence。\n"
            "炸弹合同必须包含 reader_knows、character_blindspot、danger、trigger_condition、countdown、consequence、payoff_window、rational_ignorance。\n"
            "反派道德面具必须包含 public_mask、real_good_deeds、hidden_desire、fear_of_loss、cracks、first_boundary_crossing、self_justification、collapse_wound。\n"
            "HE 必须兑现幸福但保留不可逆代价；BE 必须有无法逃开的因果、主动价值选择和美感回收。\n\n"
            f"以下 fallback 是最低结构要求，请在此基础上做出本书独有情绪设计：\n{_json_dumps(fallback_payload)}"
        )
    )
    return system_prompt, user_prompt


async def _generate_emotion_driven_kernel(
    session: AsyncSession,
    settings: AppSettings,
    *,
    project: ProjectModel,
    project_slug: str,
    premise: str,
    book_spec_payload: dict[str, Any],
    world_spec_payload: dict[str, Any],
    cast_spec_payload: dict[str, Any],
    category_key: str | None,
    workflow_run_id: UUID,
    step_order: int,
    llm_run_ids: list[UUID],
    artifact_records: list[PlanningArtifactRecord],
    story_design_kernel: dict[str, Any] | None = None,
) -> dict[str, Any]:
    fallback = _fallback_emotion_driven_kernel(
        project,
        premise,
        book_spec_payload,
        world_spec_payload,
        cast_spec_payload,
        story_design_kernel=story_design_kernel,
        category_key=category_key,
    )
    system_prompt, user_prompt = _emotion_driven_kernel_prompts(
        project,
        premise,
        book_spec_payload,
        world_spec_payload,
        cast_spec_payload,
        fallback,
        story_design_kernel=story_design_kernel,
    )
    payload, llm_run_id = await _generate_structured_artifact(
        session,
        settings,
        project=project,
        logical_name="emotion_driven_kernel",
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        fallback_payload=fallback,
        workflow_run_id=workflow_run_id,
        validator=_validate_emotion_driven_kernel_payload,
    )
    if llm_run_id is not None:
        llm_run_ids.append(llm_run_id)
    if not isinstance(payload, dict):
        payload = fallback

    kernel = emotion_driven_kernel_from_dict(payload)
    payload = emotion_driven_kernel_to_dict(kernel)
    artifact = await import_planning_artifact(
        session,
        project_slug,
        PlanningArtifactCreate(
            artifact_type=ArtifactType.EMOTION_DRIVEN_KERNEL,
            content=payload,
        ),
    )
    artifact_records.append(
        PlanningArtifactRecord(
            artifact_type=ArtifactType.EMOTION_DRIVEN_KERNEL,
            artifact_id=artifact.id,
            version_no=artifact.version_no,
        )
    )
    project.metadata_json = {
        **(project.metadata_json or {}),
        "emotion_driven_kernel": payload,
    }
    await create_workflow_step_run(
        session,
        workflow_run_id=workflow_run_id,
        step_name="generate_emotion_driven_kernel",
        step_order=step_order,
        status=WorkflowStatus.COMPLETED,
        output_ref={
            "artifact_id": str(artifact.id),
            "llm_run_id": str(llm_run_id) if llm_run_id else None,
        },
    )
    return payload


def _project_target_audiences(
    project: ProjectModel,
    book_spec: Mapping[str, Any],
    metadata: Mapping[str, Any],
) -> list[str]:
    audiences = _string_list(metadata.get("target_audiences"))
    if audiences:
        return audiences
    audiences = _string_list(book_spec.get("target_audiences"))
    if audiences:
        return audiences
    audience = _non_empty_string(getattr(project, "audience", ""), "")
    return [audience] if audience else []


def _project_target_platform(project: ProjectModel, metadata: Mapping[str, Any]) -> str:
    writing_profile = _mapping(metadata.get("writing_profile"))
    market = _mapping(writing_profile.get("market"))
    return _first_non_empty_text(
        metadata.get("target_platform"),
        metadata.get("platform_target"),
        market.get("platform_target"),
        getattr(project, "platform_target", None),
        default="general",
    )


def _project_commercial_brief(
    project: ProjectModel,
    *,
    premise: str,
    book_spec: Mapping[str, Any],
    metadata: Mapping[str, Any],
) -> dict[str, Any]:
    commercial = dict(_mapping(metadata.get("commercial_brief")))
    target_audiences = _project_target_audiences(project, book_spec, metadata)
    if target_audiences and not commercial.get("target_audiences"):
        commercial["target_audiences"] = target_audiences
    for source_key, target_key in (
        ("reader_promise", "reader_promise"),
        ("logline", "reader_promise"),
        ("unique_hook", "unique_hook"),
    ):
        if not commercial.get(target_key) and book_spec.get(source_key):
            commercial[target_key] = book_spec.get(source_key)
    if not commercial.get("premise"):
        commercial["premise"] = premise
    return commercial


async def _generate_public_emotion_kernel_artifact(
    session: AsyncSession,
    *,
    project: ProjectModel,
    project_slug: str,
    premise: str,
    book_spec_payload: dict[str, Any],
    workflow_run_id: UUID,
    step_order: int,
    artifact_records: list[PlanningArtifactRecord],
) -> dict[str, Any]:
    metadata = dict(project.metadata_json or {})
    existing = _mapping(metadata.get("public_emotion_kernel"))
    seed = build_public_emotion_kernel_seed(
        book_spec={
            **book_spec_payload,
            "title": book_spec_payload.get("title") or project.title,
            "genre": book_spec_payload.get("genre") or project.genre,
            "premise": premise,
        },
        commercial_brief=_project_commercial_brief(
            project,
            premise=premise,
            book_spec=book_spec_payload,
            metadata=metadata,
        ),
        project_metadata=metadata,
    )
    raw_payload = existing or seed
    try:
        payload = public_emotion_kernel_to_dict(
            public_emotion_kernel_from_dict(dict(raw_payload))
        )
    except Exception:
        payload = public_emotion_kernel_to_dict(public_emotion_kernel_from_dict(seed))

    artifact = await import_planning_artifact(
        session,
        project_slug,
        PlanningArtifactCreate(
            artifact_type=ArtifactType.PUBLIC_EMOTION_KERNEL,
            content=payload,
        ),
    )
    artifact_records.append(
        PlanningArtifactRecord(
            artifact_type=ArtifactType.PUBLIC_EMOTION_KERNEL,
            artifact_id=artifact.id,
            version_no=artifact.version_no,
        )
    )
    project.metadata_json = {
        **(project.metadata_json or {}),
        "public_emotion_kernel": payload,
    }
    await create_workflow_step_run(
        session,
        workflow_run_id=workflow_run_id,
        step_name="generate_public_emotion_kernel",
        step_order=step_order,
        status=WorkflowStatus.COMPLETED,
        output_ref={"artifact_id": str(artifact.id)},
    )
    return payload


async def _generate_compliance_boundary_kernel_artifact(
    session: AsyncSession,
    *,
    project: ProjectModel,
    project_slug: str,
    workflow_run_id: UUID,
    step_order: int,
    artifact_records: list[PlanningArtifactRecord],
) -> dict[str, Any]:
    metadata = dict(project.metadata_json or {})
    existing = _mapping(metadata.get("compliance_boundary_kernel"))
    seed = build_compliance_boundary_kernel_seed(
        platform=_project_target_platform(project, metadata)
    )
    raw_payload = existing or seed
    try:
        payload = compliance_boundary_kernel_to_dict(
            compliance_boundary_kernel_from_dict(dict(raw_payload))
        )
    except Exception:
        payload = compliance_boundary_kernel_to_dict(
            compliance_boundary_kernel_from_dict(seed)
        )

    artifact = await import_planning_artifact(
        session,
        project_slug,
        PlanningArtifactCreate(
            artifact_type=ArtifactType.COMPLIANCE_BOUNDARY_KERNEL,
            content=payload,
        ),
    )
    artifact_records.append(
        PlanningArtifactRecord(
            artifact_type=ArtifactType.COMPLIANCE_BOUNDARY_KERNEL,
            artifact_id=artifact.id,
            version_no=artifact.version_no,
        )
    )
    project.metadata_json = {
        **(project.metadata_json or {}),
        "compliance_boundary_kernel": payload,
    }
    await create_workflow_step_run(
        session,
        workflow_run_id=workflow_run_id,
        step_name="generate_compliance_boundary_kernel",
        step_order=step_order,
        status=WorkflowStatus.COMPLETED,
        output_ref={"artifact_id": str(artifact.id)},
    )
    return payload


# ---------------------------------------------------------------------------
# Promotional brief: title refinement + tags + protagonist intro + blurb
# ---------------------------------------------------------------------------


def _promotional_brief_fallback(
    project: ProjectModel,
    book_spec: dict[str, Any],
    cast_spec: dict[str, Any],
) -> dict[str, Any]:
    """Build a static fallback promotional brief from existing artifacts."""
    bs = _mapping(book_spec)
    protag = _mapping(bs.get("protagonist"))
    cast = _mapping(cast_spec)
    cast_protag = _mapping(cast.get("protagonist"))
    is_en = is_english_language(project.language)

    # Merge protagonist info from both specs
    protag_name = (
        protag.get("name") or cast_protag.get("name") or ("Protagonist" if is_en else "主角")
    )
    protag_archetype = protag.get("archetype") or cast_protag.get("archetype") or ""
    protag_golden_finger = protag.get("golden_finger") or ""
    protag_goal = protag.get("external_goal") or cast_protag.get("goal") or ""

    return {
        "title": bs.get("title") or project.title,
        "tags": _string_list(bs.get("themes"))[:8] or ([project.genre] if project.genre else []),
        "protagonist": {
            "name": protag_name,
            "archetype": protag_archetype,
            "golden_finger": protag_golden_finger,
            "goal": protag_goal,
        },
        "blurb": _non_empty_string(
            bs.get("logline"),
            project.title,
        ),
    }


def _promotional_brief_prompts(
    project: ProjectModel,
    book_spec: dict[str, Any],
    cast_spec: dict[str, Any],
    volume_plan: list[dict[str, Any]],
) -> tuple[str, str]:
    """Build system + user prompt for generating the promotional brief."""
    is_en = is_english_language(project.language)
    book_summary = summarize_book_spec(book_spec, language="en" if is_en else "zh")
    cast_summary = summarize_cast_spec(cast_spec, language="en" if is_en else "zh")
    vol_count = len(volume_plan) if isinstance(volume_plan, list) else 0
    ch_count = sum(
        int(v.get("chapter_count_target", 0))
        for v in (volume_plan if isinstance(volume_plan, list) else [])
    )

    if is_en:
        system_prompt = (
            "You are a bestselling fiction marketing copywriter. "
            "You produce compelling blurbs, tags, and one-liners for novel promotion. "
            "Output valid JSON only."
        )
        user_prompt = (
            f"Based on the following novel plan, generate a promotional brief.\n\n"
            f"BookSpec summary:\n{book_summary}\n\n"
            f"CastSpec summary:\n{cast_summary}\n\n"
            f"Scale: {vol_count} volume(s), {ch_count} chapters\n\n"
            "Generate a JSON object with:\n"
            '1. "title": Refined novel title (catchy, memorable, 2-6 words)\n'
            '2. "tags": Array of 5-8 genre/theme tags for discoverability (e.g. ["post-apocalyptic", "survival", "power fantasy"])\n'
            '3. "protagonist": {{"name": "...", "tagline": "one-line character pitch"}}\n'
            '4. "blurb": Promotional synopsis under 500 words. Must:\n'
            "   - Open with a hook that grabs attention in the first sentence\n"
            "   - Introduce the protagonist's situation and unique edge\n"
            "   - Reveal the central conflict without major spoilers\n"
            "   - End with a question or cliffhanger that compels the reader to start reading\n"
            "   - Use vivid, punchy language — this is marketing copy, not a summary\n"
        )
    else:
        system_prompt = (
            "你是顶级网络小说营销文案专家。"
            "你擅长写出让人忍不住点开的书名、标签和宣传简介。"
            "输出必须是合法 JSON，不要解释。"
        )
        user_prompt = (
            f"根据以下小说规划，生成一份宣传简介。\n\n"
            f"BookSpec 摘要：\n{book_summary}\n\n"
            f"CastSpec 摘要：\n{cast_summary}\n\n"
            f"规模：{vol_count} 卷，{ch_count} 章\n\n"
            "生成一个 JSON 对象，包含：\n"
            '1. "title"：优化后的小说标题（吸睛、好记、2-10字）\n'
            '2. "tags"：5-8 个作品标签，用于分类和推荐（如 ["末日生存", "异能觉醒", "废土霸主"]）\n'
            '3. "protagonist"：{"name": "主角名", "tagline": "一句话角色卖点"}\n'
            '4. "blurb"：宣传简介，500字以内。要求：\n'
            "   - 第一句话就要有钩子，让人想读下去\n"
            "   - 交代主角处境和独特优势\n"
            "   - 点出核心冲突，但不剧透关键转折\n"
            "   - 结尾用悬念或反问收束，让读者迫不及待想翻开第一章\n"
            "   - 语言要有画面感、有节奏——这是营销文案，不是情节摘要\n"
        )
    return system_prompt, user_prompt


async def _generate_promotional_brief(
    session: AsyncSession,
    settings: AppSettings,
    *,
    project: ProjectModel,
    book_spec: dict[str, Any],
    cast_spec: dict[str, Any],
    volume_plan: list[dict[str, Any]],
    workflow_run_id: UUID,
    step_order: int,
    llm_run_ids: list[UUID],
    artifact_records: list[PlanningArtifactRecord],
) -> dict[str, Any]:
    """Generate promotional brief (title + tags + protagonist + blurb) and persist as artifact."""
    fallback = _promotional_brief_fallback(project, book_spec, cast_spec)
    system_prompt, user_prompt = _promotional_brief_prompts(
        project,
        book_spec,
        cast_spec,
        volume_plan,
    )
    brief_payload, llm_run_id = await _generate_structured_artifact(
        session,
        settings,
        project=project,
        logical_name="promotional_brief",
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        fallback_payload=fallback,
        workflow_run_id=workflow_run_id,
    )
    if llm_run_id is not None:
        llm_run_ids.append(llm_run_id)

    # Persist as artifact
    brief_artifact = await import_planning_artifact(
        session,
        project.slug,
        PlanningArtifactCreate(
            artifact_type=ArtifactType.PROMOTIONAL_BRIEF,
            content=brief_payload,
        ),
    )
    artifact_records.append(
        PlanningArtifactRecord(
            artifact_type=ArtifactType.PROMOTIONAL_BRIEF,
            artifact_id=brief_artifact.id,
            version_no=brief_artifact.version_no,
        )
    )
    await create_workflow_step_run(
        session,
        workflow_run_id=workflow_run_id,
        step_name="generate_promotional_brief",
        step_order=step_order,
        status=WorkflowStatus.COMPLETED,
        output_ref={
            "artifact_id": str(brief_artifact.id),
            "llm_run_id": str(llm_run_id) if llm_run_id else None,
        },
    )

    # Update project title if the LLM produced a better one — but never let a
    # bare taxonomy/category name (题材名 ≠ 书名) overwrite a real title. Beyond
    # that (T7, 2026-07-09): a candidate must actually be BETTER by the same
    # click-power ruler the conception title already passed, or it must be
    # replacing a bare-taxonomy name — otherwise this second, ungated title
    # producer silently overwrites a title that already cleared the appeal
    # gate. On any evaluation failure we conservatively keep the existing
    # title (an unverified overwrite is exactly the bug this closes).
    new_title = brief_payload.get("title")
    if (
        isinstance(new_title, str)
        and new_title.strip()
        and new_title.strip() != project.title
        and not is_bare_taxonomy_title(new_title.strip())
    ):
        _candidate_title = new_title.strip()
        try:
            from bestseller.services.story_appeal import load_story_appeal_config
            from bestseller.services.title_appeal_gate import evaluate_title_appeal

            _appeal_cfg = load_story_appeal_config()
            _title_min = float((_appeal_cfg.get("meets_bar", {}) or {}).get("title_min", 0))
            _new_verdict = evaluate_title_appeal(
                _candidate_title, genre=project.genre, sub_genre=project.sub_genre,
                config=_appeal_cfg,
            )
            _old_is_bare = is_bare_taxonomy_title(project.title)
            _old_verdict = None if _old_is_bare else evaluate_title_appeal(
                project.title, genre=project.genre, sub_genre=project.sub_genre,
                config=_appeal_cfg,
            )
            _old_total = _old_verdict.total if _old_verdict is not None else None
            if _should_overwrite_promotional_title(
                candidate_total=_new_verdict.total, title_min=_title_min,
                old_title_is_bare=_old_is_bare, old_total=_old_total,
            ):
                project.title = _candidate_title
            else:
                logger.info(
                    "promotional_brief: keeping existing title %r over candidate %r "
                    "(candidate_gate=%.1f title_min=%.1f old_bare=%s)",
                    project.title, _candidate_title, _new_verdict.total, _title_min, _old_is_bare,
                )
        except Exception:
            logger.warning(
                "promotional_brief title comparison failed; keeping existing title",
                exc_info=True,
            )

    # 简介单一真源(T7, 2026-07-09)：promotional_brief 曾独立再生一份 blurb，
    # 与构思终稿 synopsis(已过病理检测器+文案工序淘汰赛，见 blurb_pathology.py/
    # blurb_copywriter.py) 互不校验、各写各的。此处消费终版 synopsis，只做平台
    # 字数裁剪，不再另起 LLM 产线——两份"故事身份"的分裂到此收敛为一份。
    _metadata = project.metadata_json or {}
    blurb = _resolve_promotional_brief_blurb(
        converged_synopsis=str(_metadata.get("synopsis") or ""),
        llm_blurb=str(brief_payload.get("blurb") or ""),
        premise_fallback=str(
            _metadata.get("premise") or _mapping(book_spec).get("logline") or ""
        ),
    )

    # Store tags + blurb in project metadata for easy access
    project.metadata_json = {
        **_metadata,
        "promotional_brief": {
            "tags": brief_payload.get("tags", []),
            "protagonist": brief_payload.get("protagonist", {}),
            "blurb": blurb,
        },
    }

    return brief_payload


def _should_overwrite_promotional_title(
    *,
    candidate_total: float,
    title_min: float,
    old_title_is_bare: bool,
    old_total: float | None,
) -> bool:
    """T7 书名覆盖收紧：候选必须达标 AND (旧名是题材裸名 OR 候选分数更高)。

    两条件同时成立才允许覆盖——不再是"非空且不是裸题材名"就无条件覆盖。
    """

    candidate_clears_bar = title_min <= 0 or candidate_total >= title_min
    old_beaten = old_title_is_bare or (old_total is not None and old_total < candidate_total)
    return candidate_clears_bar and old_beaten


def _resolve_promotional_brief_blurb(
    *, converged_synopsis: str, llm_blurb: str, premise_fallback: str
) -> str:
    """T7 简介单一真源：converged_synopsis 非空时直接消费(只裁剪不重生)；
    否则用 LLM 产的 blurb，命中致命病理才退到 premise 兜底。"""

    from bestseller.services.blurb_pathology import detect_blurb_pathology, truncate_at_sentence

    converged_synopsis = converged_synopsis.strip()
    if converged_synopsis:
        return truncate_at_sentence(converged_synopsis, 500)

    llm_blurb = llm_blurb.strip()
    fatal = [f for f in detect_blurb_pathology(llm_blurb) if f.severity == "fatal"]
    if fatal:
        return premise_fallback.strip()
    return llm_blurb


async def _run_prewrite_readiness_gate(
    session: AsyncSession,
    settings: AppSettings,
    *,
    project: ProjectModel,
    project_slug: str,
    book_spec_payload: dict[str, Any],
    world_spec_payload: dict[str, Any],
    cast_spec_payload: dict[str, Any],
    volume_plan_payload: object,
    workflow_run_id: UUID,
    step_order: int,
    artifact_records: list[PlanningArtifactRecord],
    story_design_kernel: dict[str, Any] | None = None,
    emotion_driven_kernel: dict[str, Any] | None = None,
    public_emotion_kernel: dict[str, Any] | None = None,
    compliance_boundary_kernel: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Persist the planning kernel and readiness report before drafting entry."""

    from bestseller.services.planning_kernel import (
        decide_prewrite_readiness_gate,
        persist_project_planning_kernel,
    )

    payload = persist_project_planning_kernel(
        project,
        book_spec=book_spec_payload,
        world_spec=world_spec_payload,
        cast_spec=cast_spec_payload,
        volume_plan=volume_plan_payload,
        story_design_kernel=story_design_kernel,
        emotion_driven_kernel=emotion_driven_kernel,
        public_emotion_kernel=public_emotion_kernel,
        compliance_boundary_kernel=compliance_boundary_kernel,
        output_base_dir=settings.output.base_dir,
    )
    report = _mapping(payload.get("prewrite_readiness_report"))
    readiness_artifact = await import_planning_artifact(
        session,
        project_slug,
        PlanningArtifactCreate(
            artifact_type=ArtifactType.PREWRITE_READINESS,
            content={
                "validation_type": "prewrite_readiness",
                **payload,
            },
        ),
    )
    artifact_records.append(
        PlanningArtifactRecord(
            artifact_type=ArtifactType.PREWRITE_READINESS,
            artifact_id=readiness_artifact.id,
            version_no=readiness_artifact.version_no,
        )
    )
    decision = decide_prewrite_readiness_gate(
        report,
        mode=settings.pipeline.prewrite_readiness_gate_mode,
        block_on_failure=settings.pipeline.prewrite_readiness_block_on_failure,
    )
    should_block = decision.should_block
    await create_workflow_step_run(
        session,
        workflow_run_id=workflow_run_id,
        step_name="prewrite_readiness_gate",
        step_order=step_order,
        status=WorkflowStatus.FAILED if should_block else WorkflowStatus.COMPLETED,
        output_ref={
            "artifact_id": str(readiness_artifact.id),
            "passed": decision.passed,
            "score": decision.score,
            "blocking_codes": list(decision.blocking_codes),
            "critical_codes": list(decision.critical_codes),
            "warning_codes": list(decision.warning_codes),
            "gate_decision": decision.to_dict(),
        },
        error_message=(
            "Prewrite readiness gate failed: " + ", ".join(decision.blocking_codes)
            if should_block
            else None
        ),
    )
    if should_block:
        raise PlannerFallbackError(
            "Prewrite readiness gate failed: " + ", ".join(decision.blocking_codes)
        )
    return payload


async def _record_prewrite_quality_gate(
    session: AsyncSession,
    *,
    workflow_run_id: UUID,
    step_name: str,
    step_order: int,
    report: Any,
    strict: bool,
) -> None:
    payload = report.to_dict() if hasattr(report, "to_dict") else dict(report or {})
    blocking_codes = [
        str(item.get("code"))
        for item in _mapping_list(payload.get("blocking_findings"))
        if item.get("code")
    ]
    output_ref = {
        **payload,
        "blocking_codes": blocking_codes,
        "repair_targets": {
            code: repair_target_for_block_code(code) for code in blocking_codes
        },
    }
    should_block = strict and not bool(payload.get("passed"))
    await create_workflow_step_run(
        session,
        workflow_run_id=workflow_run_id,
        step_name=step_name,
        step_order=step_order,
        status=WorkflowStatus.FAILED if should_block else WorkflowStatus.COMPLETED,
        output_ref=output_ref,
        error_message=(
            f"{step_name} failed: " + ", ".join(blocking_codes) if should_block else None
        ),
    )
    if should_block:
        raise PlannerFallbackError(f"{step_name} failed: " + ", ".join(blocking_codes))


async def _workflow_step_run_exists(
    session: AsyncSession,
    *,
    workflow_run_id: UUID,
    step_order: int,
) -> bool:
    existing_id = await session.scalar(
        select(WorkflowStepRunModel.id).where(
            WorkflowStepRunModel.workflow_run_id == workflow_run_id,
            WorkflowStepRunModel.step_order == step_order,
        )
    )
    return existing_id is not None


async def _record_planner_failure_step_once(
    session: AsyncSession,
    *,
    workflow_run_id: UUID,
    step_name: str,
    step_order: int,
    error_message: str,
) -> None:
    if await _workflow_step_run_exists(
        session,
        workflow_run_id=workflow_run_id,
        step_order=step_order,
    ):
        return
    await create_workflow_step_run(
        session,
        workflow_run_id=workflow_run_id,
        step_name=step_name,
        step_order=step_order,
        status=WorkflowStatus.FAILED,
        error_message=error_message,
    )


def _outline_judge_repair_directives(
    judge_payload: Mapping[str, Any] | None,
    *,
    round_idx: int,
    max_rounds: int,
) -> list[str]:
    """Decide whether to run another in-loop outline repair round (G4).

    Returns the repair directives to feed the next regeneration when the
    commercial judge failed AND the round budget is not yet spent; otherwise
    ``[]`` (stop). The bound prevents the generate→reject→regenerate death
    loop that the strict-prewrite cascade was prone to.
    """

    if judge_payload is None:
        return []
    if round_idx >= max_rounds:
        return []
    if judge_payload.get("passed"):
        return []
    return _string_list(judge_payload.get("repair_directives"))


async def _run_planner_outline_commercial_judge(
    session: AsyncSession,
    settings: AppSettings,
    *,
    project: ProjectModel,
    workflow_run_id: UUID,
    step_name: str,
    step_order: int,
    outline_payload: Mapping[str, Any],
) -> dict[str, Any] | None:
    if not is_strict_prewrite_project(project):
        return None
    from bestseller.services.outline_llm_judge import judge_outline_commercial_readiness
    from bestseller.services.prompt_packs import resolve_prompt_pack

    metadata = project.metadata_json if isinstance(project.metadata_json, dict) else {}
    pack = resolve_prompt_pack(
        metadata.get("prompt_pack_name") or metadata.get("prompt_pack_key"),
        genre=str(getattr(project, "genre", "general-fiction") or "general-fiction"),
        sub_genre=getattr(project, "sub_genre", None),
    )
    result = await judge_outline_commercial_readiness(
        session,
        settings,
        outline_payload=outline_payload,
        project_brief={
            "slug": project.slug,
            "title": project.title,
            "genre": project.genre,
            "sub_genre": project.sub_genre,
            "target_chapters": project.target_chapters,
            "reader_contract": project.reader_contract_json or {},
            "hype_scheme": project.hype_scheme_json or {},
            "metadata": metadata,
        },
        threshold=float(
            getattr(
                settings.pipeline,
                "commercial_strict_prewrite_planning_judge_threshold",
                0.82,
            )
            or 0.82
        ),
        workflow_run_id=workflow_run_id,
        pack=pack,
    )
    payload = result.model_dump(mode="json", by_alias=True)
    blocking_codes = [
        str(issue.get("code"))
        for issue in _mapping_list(payload.get("blocking_issues"))
        if issue.get("code")
    ]
    if not blocking_codes and not result.passed:
        blocking_codes = ["outline_llm_commercial_judge_failed"]
    # G4: surface concrete regeneration directives so the caller can repair the
    # outline IN THE SAME RUN instead of shipping a failing outline verbatim.
    from bestseller.services.outline_llm_judge import build_outline_repair_directives

    payload["repair_directives"] = (
        build_outline_repair_directives(result) if not result.passed else []
    )
    payload["passed"] = bool(result.passed)
    output_ref = {
        **payload,
        "blocking_codes": blocking_codes,
        "repair_targets": {
            code: repair_target_for_block_code(code) for code in blocking_codes
        },
    }
    await create_workflow_step_run(
        session,
        workflow_run_id=workflow_run_id,
        step_name=step_name,
        step_order=step_order,
        status=WorkflowStatus.MACHINE_BLOCKED if not result.passed else WorkflowStatus.COMPLETED,
        output_ref=output_ref,
        error_message=(
            f"{step_name} failed: score={result.overall_score:.3f}, "
            + ", ".join(blocking_codes)
            if not result.passed
            else None
        ),
    )
    if not result.passed:
        # Honor the soft-block flag, consistent with the workflows.py outline
        # judge path and the sibling planner gates (reverse_outline,
        # worldview_compliance). In strict mode this raises to drive outline
        # repair; in "run + report + repair, no hard abort" mode it records the
        # MACHINE_BLOCKED step (above) and returns the report so the pipeline
        # continues instead of killing the whole book.
        if strict_blocks(
            project, settings, "outline_llm_commercial_judge_block_on_failure"
        ):
            raise PlannerFallbackError(
                f"{step_name} failed: score={result.overall_score:.3f}, "
                + ", ".join(blocking_codes)
            )
        logger.warning(
            "%s failed (score=%.3f, codes=%s) — soft mode "
            "(outline_llm_commercial_judge_block_on_failure=false): recording "
            "report and continuing without hard abort.",
            step_name,
            result.overall_score,
            ", ".join(blocking_codes),
        )
    return payload


def _attach_prewrite_quality_report(
    payload: Any,
    *,
    step_name: str,
    report: Any,
    project: ProjectModel,
) -> Any:
    if not isinstance(payload, dict):
        return payload
    enriched = copy.deepcopy(payload)
    meta = enriched.get("_meta") if isinstance(enriched.get("_meta"), dict) else {}
    gate_reports = meta.get("gate_reports") if isinstance(meta.get("gate_reports"), dict) else {}
    report_payload = report.to_dict() if hasattr(report, "to_dict") else dict(report or {})
    enriched["_meta"] = {
        **planning_artifact_meta(project),
        **meta,
        "gate_reports": {
            **gate_reports,
            step_name: report_payload,
        },
    }
    return enriched


async def _run_hook_strength_gate(
    settings: AppSettings,
    *,
    project: ProjectModel,
    premise: str,
) -> tuple[Any | None, dict[str, Any] | None]:
    if not getattr(settings.hook_engine, "enabled", True):
        return None, None
    from bestseller.services.anti_commonsense_hook import (
        build_hook_duplicate_risk_fn,
        generate_hook_candidates,
    )
    from bestseller.services.hook_propagation import (
        coerce_hook_spec,
        stash_hook_spec_on_project,
    )
    from bestseller.services.hook_strength_gate import (
        evaluate_hook_strength_gate,
        hook_strength_report_to_dict,
        repair_hook_spec_once,
    )

    metadata = project.metadata_json if isinstance(project.metadata_json, dict) else {}
    hook_spec = coerce_hook_spec(metadata.get("hook_spec"))
    provided_spec = hook_spec is not None
    min_h_norm = float(getattr(settings.hook_engine, "min_h_norm", 30.0))
    reference_texts: list[str] = [premise, str(getattr(project, "title", "") or "")]
    for key in ("hook_history", "hook_candidates", "previous_hooks"):
        raw = metadata.get(key)
        if isinstance(raw, list):
            for item in raw:
                if isinstance(item, dict):
                    spec_payload = item.get("spec") if isinstance(item.get("spec"), dict) else item
                    reference_texts.extend(
                        str(spec_payload.get(field) or "")
                        for field in ("one_liner", "core_rule", "reversal")
                        if isinstance(spec_payload, dict)
                    )
                else:
                    reference_texts.append(str(item))
    duplicate_risk_fn = build_hook_duplicate_risk_fn(reference_texts)
    if hook_spec is None:
        rank_weights = {
            "h_norm": float(getattr(settings.hook_engine, "rank_weight_h_norm", 0.62)),
            "novelty": float(getattr(settings.hook_engine, "rank_weight_novelty", 0.28)),
            "duplicate_risk": float(
                getattr(settings.hook_engine, "rank_weight_duplicate_risk", 0.10)
            ),
        }
        candidates = generate_hook_candidates(
            genre=project.genre,
            locale=project.language,
            role=None,
            base_desire=None,
            count=max(1, int(getattr(settings.hook_engine, "candidate_count", 6))),
            seed=int(
                hashlib.sha256(f"{project.slug}:{premise}".encode("utf-8")).hexdigest()[:8],
                16,
            ),
            min_h_norm=min_h_norm,
            duplicate_risk_fn=duplicate_risk_fn,
            rank_weights=rank_weights,
        )
        if candidates:
            hook_spec = candidates[0].spec
            metadata = dict(metadata)
            metadata["hook_candidates"] = [item.model_dump(mode="json") for item in candidates]
            project.metadata_json = metadata
    if hook_spec is None:
        return None, None
    premise_context: dict[str, Any] = {
        "premise": premise,
        "title": getattr(project, "title", None),
        "genre": getattr(project, "genre", None),
        "sub_genre": getattr(project, "sub_genre", None),
        "tags": metadata.get("tags"),
    }
    # Structured anchor channel: when the title workflow already derived
    # story-grounded anchor groups, feed them to the gate so alignment does
    # not rely on premise-text auto-extraction alone.
    _title_workflow_primary = metadata.get("title_workflow_primary")
    if isinstance(_title_workflow_primary, dict):
        _structured_anchor_groups = _title_workflow_primary.get("anchor_groups")
        if isinstance(_structured_anchor_groups, dict) and _structured_anchor_groups:
            premise_context["title_anchor_groups"] = _structured_anchor_groups
    report = evaluate_hook_strength_gate(
        hook_spec,
        min_h_norm=min_h_norm,
        premise_context=premise_context,
    )
    rewrite_attempted = False
    rewrite_applied = False
    if not report.passed:
        rewrite_attempted = True
        repaired = repair_hook_spec_once(hook_spec, report, premise_context=premise_context)
        repaired_report = evaluate_hook_strength_gate(
            repaired,
            min_h_norm=min_h_norm,
            premise_context=premise_context,
        )
        if repaired_report.h_norm > report.h_norm:
            hook_spec = repaired
            report = repaired_report
            rewrite_applied = True
    if report.verdict == "reject" or report.score.verdict == "reject":
        # A rejected hook — whether auto-generated from the template library or
        # auto-stored on the project by the quickstart/conception pipeline — must
        # never hard-fail the whole autonomous run. Dropping the hook and
        # continuing yields a usable book; raising would abort a 500-chapter plan
        # over an optional opening enhancement. Degrade to the advisory path and
        # record the demote (the hook engine is soft by design).
        payload = hook_strength_report_to_dict(report)
        payload["rewrite_attempted"] = rewrite_attempted
        payload["rewrite_applied"] = rewrite_applied
        payload["auto_demoted"] = True
        payload["demote_reason"] = (
            "auto_generated_hook_rejected"
            if not provided_spec
            else "provided_hook_rejected"
        )
        logger.warning(
            "hook_strength_gate auto-demoted for project=%s: hook rejected "
            "(verdict=%s, h_norm=%.2f, provided=%s); continuing without hook_spec",
            project.slug,
            report.verdict,
            report.h_norm,
            provided_spec,
        )
        return None, payload
    payload = hook_strength_report_to_dict(report)
    payload["rewrite_attempted"] = rewrite_attempted
    payload["rewrite_applied"] = rewrite_applied
    stash_hook_spec_on_project(project, hook_spec, score_payload=payload)
    return hook_spec, payload


async def _run_reverse_outline_gate(
    session: AsyncSession,
    settings: AppSettings,
    *,
    project: ProjectModel,
    story_design_kernel: dict[str, Any] | None,
    outline_payload: dict[str, Any],
    workflow_run_id: UUID,
    step_order: int,
) -> dict[str, Any]:
    """Verify generated chapter outlines against the StoryDesignKernel."""

    from bestseller.services.reverse_outline_gate import (
        evaluate_reverse_outline_gate,
        reverse_outline_report_to_dict,
    )

    report = evaluate_reverse_outline_gate(story_design_kernel, outline_payload)
    report_payload = reverse_outline_report_to_dict(report)
    should_block = strict_blocks(project, settings, "reverse_outline_gate_block_on_failure") and not report.passed
    metadata = dict(project.metadata_json or {})
    kernel = _mapping(metadata.get("story_design_kernel"))
    if kernel:
        kernel["reverse_outline_status"] = "verified" if report.passed else "needs_repair"
        metadata["story_design_kernel"] = kernel
    metadata["reverse_outline_gate_report"] = report_payload
    project.metadata_json = metadata

    blocking_codes = [
        str(item.get("code"))
        for item in _mapping_list(report_payload.get("blocking_findings"))
        if item.get("code")
    ]
    await create_workflow_step_run(
        session,
        workflow_run_id=workflow_run_id,
        step_name="reverse_outline_gate",
        step_order=step_order,
        status=WorkflowStatus.FAILED if should_block else WorkflowStatus.COMPLETED,
        output_ref={
            "passed": report.passed,
            "score": report.score,
            "blocking_codes": blocking_codes,
        },
        error_message=(
            "Reverse outline gate failed: " + ", ".join(blocking_codes) if should_block else None
        ),
    )
    if should_block:
        raise PlannerFallbackError("Reverse outline gate failed: " + ", ".join(blocking_codes))
    return report_payload


async def _run_worldview_compliance_gate(
    session: AsyncSession,
    settings: AppSettings,
    *,
    project: ProjectModel,
    story_design_kernel: dict[str, Any] | None,
    outline_payload: dict[str, Any],
    workflow_run_id: UUID,
    step_order: int,
) -> dict[str, Any]:
    """Verify generated chapter outlines against the WorldviewKernel."""

    from bestseller.services.worldview_compliance_gate import (
        evaluate_worldview_compliance_gate,
        worldview_compliance_report_to_dict,
    )

    report = evaluate_worldview_compliance_gate(story_design_kernel, outline_payload)
    report_payload = worldview_compliance_report_to_dict(report)
    should_block = (
        strict_blocks(project, settings, "worldview_compliance_gate_block_on_failure")
        and not report.passed
    )
    metadata = dict(project.metadata_json or {})
    kernel = _mapping(metadata.get("story_design_kernel"))
    if kernel:
        kernel["worldview_compliance_status"] = "verified" if report.passed else "needs_repair"
        metadata["story_design_kernel"] = kernel
    metadata["worldview_compliance_gate_report"] = report_payload
    project.metadata_json = metadata

    blocking_codes = [
        str(item.get("code"))
        for item in _mapping_list(report_payload.get("blocking_findings"))
        if item.get("code")
    ]
    await create_workflow_step_run(
        session,
        workflow_run_id=workflow_run_id,
        step_name="worldview_compliance_gate",
        step_order=step_order,
        status=WorkflowStatus.FAILED if should_block else WorkflowStatus.COMPLETED,
        output_ref={
            "passed": report.passed,
            "score": report.score,
            "blocking_codes": blocking_codes,
        },
        error_message=(
            "Worldview compliance gate failed: " + ", ".join(blocking_codes)
            if should_block
            else None
        ),
    )
    if should_block:
        raise PlannerFallbackError("Worldview compliance gate failed: " + ", ".join(blocking_codes))
    return report_payload


async def _run_story_principle_gate(
    session: AsyncSession,
    *,
    project: ProjectModel,
    outline_payload: dict[str, Any],
    workflow_run_id: UUID,
    step_order: int,
) -> dict[str, Any]:
    """Audit event-unit writing-principle coverage across chapter outlines."""

    from bestseller.services.quality_gates_config import get_quality_gates_config
    from bestseller.services.story_principle_gate import (
        evaluate_story_principle_contract,
        story_principle_report_to_dict,
    )

    gate_cfg = get_quality_gates_config().story_principle
    if not gate_cfg.enabled:
        return {}

    report = evaluate_story_principle_contract(
        outline_payload,
        min_roles_per_batch=gate_cfg.min_event_cycle_roles_per_batch,
        max_same_role_streak=gate_cfg.max_same_role_streak,
    )
    report_payload = story_principle_report_to_dict(report)
    should_block = gate_cfg.block_on_failure and not report.passed

    metadata = dict(project.metadata_json or {})
    kernel = _mapping(metadata.get("story_design_kernel"))
    if kernel:
        kernel["story_principle_status"] = "verified" if report.passed else "needs_repair"
        metadata["story_design_kernel"] = kernel
    metadata["story_principle_gate_report"] = report_payload
    project.metadata_json = metadata

    finding_codes = [
        str(item.get("code"))
        for item in _mapping_list(report_payload.get("findings"))
        if item.get("code")
    ]
    await create_workflow_step_run(
        session,
        workflow_run_id=workflow_run_id,
        step_name="story_principle_gate",
        step_order=step_order,
        status=WorkflowStatus.FAILED if should_block else WorkflowStatus.COMPLETED,
        output_ref={
            "passed": report.passed,
            "finding_codes": finding_codes,
            "present_roles": sorted(report.present_roles),
        },
        error_message=(
            "Story principle gate failed: " + ", ".join(finding_codes)
            if should_block
            else None
        ),
    )
    if should_block:
        raise PlannerFallbackError("Story principle gate failed: " + ", ".join(finding_codes))
    return report_payload


async def _run_worldview_progression_gate(
    session: AsyncSession,
    settings: AppSettings,
    *,
    project: ProjectModel,
    story_design_kernel: dict[str, Any] | None,
    volume_plan_payload: object,
    workflow_run_id: UUID,
    step_order: int,
) -> dict[str, Any]:
    """Verify the volume plan progresses the WorldviewKernel across arcs."""

    from bestseller.services.worldview_progression_gate import (
        evaluate_worldview_progression_gate,
        worldview_progression_report_to_dict,
    )

    report = evaluate_worldview_progression_gate(
        story_design_kernel,
        volume_plan_payload,
    )
    report_payload = worldview_progression_report_to_dict(report)
    should_block = (
        strict_blocks(project, settings, "worldview_progression_gate_block_on_failure")
        and not report.passed
    )
    metadata = dict(project.metadata_json or {})
    kernel = _mapping(metadata.get("story_design_kernel"))
    if kernel:
        kernel["worldview_progression_status"] = (
            "verified" if report.passed else "needs_repair"
        )
        metadata["story_design_kernel"] = kernel
    metadata["worldview_progression_gate_report"] = report_payload
    project.metadata_json = metadata

    blocking_codes = [
        str(item.get("code"))
        for item in _mapping_list(report_payload.get("blocking_findings"))
        if item.get("code")
    ]
    await create_workflow_step_run(
        session,
        workflow_run_id=workflow_run_id,
        step_name="worldview_progression_gate",
        step_order=step_order,
        status=WorkflowStatus.FAILED if should_block else WorkflowStatus.COMPLETED,
        output_ref={
            "passed": report.passed,
            "score": report.score,
            "blocking_codes": blocking_codes,
        },
        error_message=(
            "Worldview progression gate failed: " + ", ".join(blocking_codes)
            if should_block
            else None
        ),
    )
    if should_block:
        raise PlannerFallbackError(
            "Worldview progression gate failed: " + ", ".join(blocking_codes)
        )
    return report_payload


async def generate_novel_plan(
    session: AsyncSession,
    settings: AppSettings,
    project_slug: str,
    premise: str,
    *,
    requested_by: str = "system",
    progress: PlanningProgressCallback | None = None,
) -> NovelPlanningResult:
    project = await _assert_plan_writer_not_locked(session, project_slug)

    workflow_run = await create_workflow_run(
        session,
        project_id=project.id,
        workflow_type=WORKFLOW_TYPE_GENERATE_NOVEL_PLAN,
        status=WorkflowStatus.RUNNING,
        scope_type="project",
        scope_id=project.id,
        requested_by=requested_by,
        current_step="store_premise",
        metadata={"project_slug": project.slug, "premise": premise},
    )
    step_order = 1
    current_step_name = "store_premise"
    llm_run_ids: list[UUID] = []
    artifact_records: list[PlanningArtifactRecord] = []

    # Resolve category once for all downstream fallback functions
    _category = resolve_novel_category(project.genre, project.sub_genre)
    _category_key: str | None = _category.key if _category else None

    # Store category_key in project metadata for downstream reuse
    if _category_key and isinstance(project.metadata_json, dict):
        project.metadata_json["category_key"] = _category_key

    # ── Batch 2: Reference-style material injection ───────────────────────
    # When ``enable_reference_style_generation`` is on, pre-fetch the
    # material reference block (§slug URNs) and stash it in
    # ``project.metadata_json["material_reference_block"]`` so that every
    # downstream sync prompt function can read it without needing async.
    # Empty when no project_materials exist or flag is off — treated as no-op.
    if settings.pipeline.enable_reference_style_generation:
        try:
            from bestseller.services.material_reference import (
                render_material_reference_block,
            )

            _mat_ref_block = await render_material_reference_block(session, project.id)
            if _mat_ref_block and isinstance(project.metadata_json, dict):
                project.metadata_json["material_reference_block"] = _mat_ref_block
        except Exception:
            logger.exception(
                "generate_novel_plan: material reference block failed — continuing without"
            )

    _stash_distilled_strategy_card(
        project,
        category_key=_category_key,
        settings=settings,
    )
    _stash_distilled_design_reference_blocks(
        project,
        category_key=_category_key,
        settings=settings,
    )
    hook_spec: Any | None = None

    try:
        hook_gate_payload: dict[str, Any] | None = None
        if getattr(settings.hook_engine, "enabled", True):
            current_step_name = "hook_strength_gate"
            workflow_run.current_step = current_step_name
            hook_spec, hook_gate_payload = await _run_hook_strength_gate(
                settings,
                project=project,
                premise=premise,
            )
            if hook_gate_payload is not None:
                await create_workflow_step_run(
                    session,
                    workflow_run_id=workflow_run.id,
                    step_name=current_step_name,
                    step_order=step_order,
                    status=WorkflowStatus.COMPLETED,
                    output_ref=hook_gate_payload,
                )
                step_order += 1
            current_step_name = "store_premise"
            workflow_run.current_step = current_step_name

        premise_artifact = await import_planning_artifact(
            session,
            project_slug,
            PlanningArtifactCreate(
                artifact_type=ArtifactType.PREMISE,
                content={
                    "premise": premise,
                    "_meta": planning_artifact_meta(project),
                },
            ),
        )
        artifact_records.append(
            PlanningArtifactRecord(
                artifact_type=ArtifactType.PREMISE,
                artifact_id=premise_artifact.id,
                version_no=premise_artifact.version_no,
            )
        )
        await create_workflow_step_run(
            session,
            workflow_run_id=workflow_run.id,
            step_name=current_step_name,
            step_order=step_order,
            status=WorkflowStatus.COMPLETED,
            output_ref={"artifact_id": str(premise_artifact.id)},
        )
        step_order += 1

        current_step_name = "concept_quality_gate"
        workflow_run.current_step = current_step_name
        concept_report = evaluate_concept_quality(
            title=project.title,
            premise=premise,
            writing_profile=_mapping(_mapping(project.metadata_json or {}).get("writing_profile")),
            language=project.language,
        )
        await _record_prewrite_quality_gate(
            session,
            workflow_run_id=workflow_run.id,
            step_name=current_step_name,
            step_order=step_order,
            report=concept_report,
            strict=is_strict_prewrite_project(project),
        )
        step_order += 1

        # Generate character names via LLM up-front so every downstream
        # fallback (book_spec / world_spec / cast_spec) sees real, contextual
        # names instead of regex-extracted fragments or generic pool defaults.
        # If the LLM call fails, _generate_character_names falls back to the
        # curated genre pool — never to regex on premise.
        current_step_name = "generate_character_names"
        workflow_run.current_step = current_step_name
        _emit_planner_progress(
            progress,
            "planning_step_started",
            project=project,
            workflow_run_id=workflow_run.id,
            current_step=current_step_name,
        )
        character_name_pool = await _generate_character_names(
            session,
            settings,
            genre=project.genre,
            sub_genre=project.sub_genre or "",
            language=project.language,
            premise=premise,
            book_spec={},
            workflow_run_id=workflow_run.id,
            project_id=project.id,
        )
        llm_protagonist_name = (
            _mapping(character_name_pool.get("protagonist")).get("name")
            or _genre_name_pool(
                project.genre,
                language=project.language,
                seed_text=_project_name_seed(project, premise),
            )["protagonist"]["name"]
        )
        await create_workflow_step_run(
            session,
            workflow_run_id=workflow_run.id,
            step_name=current_step_name,
            step_order=step_order,
            status=WorkflowStatus.COMPLETED,
            output_ref={
                "protagonist_name": llm_protagonist_name,
                "ally_count": len(_mapping_list(character_name_pool.get("allies"))),
                "antagonist_count": len(_mapping_list(character_name_pool.get("antagonists"))),
            },
        )
        step_order += 1
        _emit_planner_progress(
            progress,
            "planning_step_completed",
            project=project,
            workflow_run_id=workflow_run.id,
            current_step=current_step_name,
        )

        book_spec_fallback = _fallback_book_spec(project, premise, category_key=_category_key)
        # Override placeholder name with LLM-designed one so the LLM book_spec
        # call sees the same protagonist name in its fallback context.
        if isinstance(book_spec_fallback.get("protagonist"), dict):
            book_spec_fallback["protagonist"]["name"] = llm_protagonist_name
        current_step_name = "generate_book_spec"
        workflow_run.current_step = current_step_name
        _emit_planner_progress(
            progress,
            "planning_step_started",
            project=project,
            workflow_run_id=workflow_run.id,
            current_step=current_step_name,
            artifact_type=ArtifactType.BOOK_SPEC.value,
        )
        book_system, book_user = _book_spec_prompts(project, premise, book_spec_fallback)
        book_user = attach_planner_methodology(
            book_user,
            stage=MethodologyStage.OUTLINE_BOOK,
            project_ctx=project,
            token_budget=800,
        )
        book_spec_payload, llm_run_id = await _generate_structured_artifact(
            session,
            settings,
            project=project,
            logical_name="book_spec",
            system_prompt=book_system,
            user_prompt=book_user,
            fallback_payload=book_spec_fallback,
            workflow_run_id=workflow_run.id,
        )
        if llm_run_id is not None:
            llm_run_ids.append(llm_run_id)
        book_spec_payload = _ensure_book_spec_bible_fields(
            project,
            premise,
            book_spec_payload,
        )
        book_spec_payload = _repair_protagonist_name_drift_for_planner(
            project,
            book_spec_payload,
            protagonist_name=llm_protagonist_name,
            artifact_type=ArtifactType.BOOK_SPEC.value,
        )
        book_spec_payload = _repair_rule_survival_goal_drift_for_planner(
            project,
            book_spec_payload,
            premise=premise,
            artifact_type=ArtifactType.BOOK_SPEC.value,
        )
        if hook_spec is not None:
            from bestseller.services.hook_propagation import apply_hook_to_book_spec

            book_spec_payload = apply_hook_to_book_spec(
                book_spec_payload,
                hook_spec,
                premise_context={
                    "premise": premise,
                    "title": getattr(project, "title", None),
                    "genre": getattr(project, "genre", None),
                    "sub_genre": getattr(project, "sub_genre", None),
                    "tags": _mapping(project.metadata_json or {}).get("tags"),
                },
            )

        # ── Narrative-lines gate: validate the four-layer macro contract
        # (明线/暗线/隐藏线/核心轴) is present in the BookSpec before
        # downstream artifacts consume it. Critical gaps trigger a single
        # focused repair of the BookSpec.
        (
            repaired_book_spec,
            narrative_lines_repair_llm_run_id,
        ) = await _repair_book_spec_narrative_lines_if_needed(
            session=session,
            settings=settings,
            project=project,
            premise=premise,
            book_spec_payload=book_spec_payload,
            workflow_run_id=workflow_run.id,
        )
        if narrative_lines_repair_llm_run_id is not None:
            llm_run_ids.append(narrative_lines_repair_llm_run_id)
            book_spec_payload = repaired_book_spec
        book_spec_payload = _ensure_book_spec_bible_fields(
            project,
            premise,
            book_spec_payload,
        )
        book_spec_payload = _repair_protagonist_name_drift_for_planner(
            project,
            book_spec_payload,
            protagonist_name=llm_protagonist_name,
            artifact_type=ArtifactType.BOOK_SPEC.value,
        )
        book_spec_payload = _repair_rule_survival_goal_drift_for_planner(
            project,
            book_spec_payload,
            premise=premise,
            artifact_type=ArtifactType.BOOK_SPEC.value,
        )
        if hook_spec is not None:
            from bestseller.services.hook_propagation import apply_hook_to_book_spec

            book_spec_payload = apply_hook_to_book_spec(
                book_spec_payload,
                hook_spec,
                premise_context={
                    "premise": premise,
                    "title": getattr(project, "title", None),
                    "genre": getattr(project, "genre", None),
                    "sub_genre": getattr(project, "sub_genre", None),
                    "tags": _mapping(project.metadata_json or {}).get("tags"),
                },
            )
        book_spec_payload = _repair_protagonist_name_drift_for_planner(
            project,
            book_spec_payload,
            protagonist_name=llm_protagonist_name,
            artifact_type=ArtifactType.BOOK_SPEC.value,
        )
        book_spec_payload = _repair_rule_survival_goal_drift_for_planner(
            project,
            book_spec_payload,
            premise=premise,
            artifact_type=ArtifactType.BOOK_SPEC.value,
        )

        current_step_name = "book_spec_quality_gate"
        workflow_run.current_step = current_step_name
        # zh/en separation: run the mapped language repair so English taxonomy
        # tokens the model echoed into Chinese prose are stripped before the gate
        # blocks (2026-06-03). Identifier fields keep their slugs.
        book_spec_payload = repair_zh_book_spec_language(
            book_spec_payload,
            language=project.language,
        )
        book_spec_quality_report = evaluate_book_spec_quality(
            book_spec_payload,
            language=project.language,
        )
        await _record_prewrite_quality_gate(
            session,
            workflow_run_id=workflow_run.id,
            step_name=current_step_name,
            step_order=step_order,
            report=book_spec_quality_report,
            strict=is_strict_prewrite_project(project),
        )
        book_spec_payload = _attach_prewrite_quality_report(
            book_spec_payload,
            step_name=current_step_name,
            report=book_spec_quality_report,
            project=project,
        )
        step_order += 1

        current_step_name = "generate_book_spec"
        workflow_run.current_step = current_step_name
        book_artifact = await import_planning_artifact(
            session,
            project_slug,
            PlanningArtifactCreate(artifact_type=ArtifactType.BOOK_SPEC, content=book_spec_payload),
        )
        artifact_records.append(
            PlanningArtifactRecord(
                artifact_type=ArtifactType.BOOK_SPEC,
                artifact_id=book_artifact.id,
                version_no=book_artifact.version_no,
            )
        )
        await create_workflow_step_run(
            session,
            workflow_run_id=workflow_run.id,
            step_name=current_step_name,
            step_order=step_order,
            status=WorkflowStatus.COMPLETED,
            output_ref={
                "artifact_id": str(book_artifact.id),
                "llm_run_id": str(llm_run_id) if llm_run_id else None,
            },
        )
        _emit_planner_progress(
            progress,
            "planning_step_completed",
            project=project,
            workflow_run_id=workflow_run.id,
            current_step=current_step_name,
            artifact_type=ArtifactType.BOOK_SPEC.value,
            artifact_id=str(book_artifact.id),
            llm_run_id=str(llm_run_id) if llm_run_id else None,
        )
        step_order += 1

        world_spec_fallback = _fallback_world_spec(
            project, premise, book_spec_payload, category_key=_category_key
        )
        current_step_name = "generate_world_spec"
        workflow_run.current_step = current_step_name
        _emit_planner_progress(
            progress,
            "planning_step_started",
            project=project,
            workflow_run_id=workflow_run.id,
            current_step=current_step_name,
            artifact_type=ArtifactType.WORLD_SPEC.value,
        )
        world_system, world_user = _world_spec_prompts(project, premise, book_spec_payload)
        world_user = attach_planner_methodology(
            world_user,
            stage=MethodologyStage.OUTLINE_BOOK,
            project_ctx=project,
            token_budget=800,
        )
        world_spec_payload, llm_run_id = await _generate_structured_artifact(
            session,
            settings,
            project=project,
            logical_name="world_spec",
            system_prompt=world_system,
            user_prompt=world_user,
            fallback_payload=world_spec_fallback,
            workflow_run_id=workflow_run.id,
            validator=parse_world_spec_input,
        )
        if llm_run_id is not None:
            llm_run_ids.append(llm_run_id)

        # ── World richness gate: scan rules/locations/factions for
        # scale-appropriate breadth before cast spec and volume plan
        # consume the world summary. Critical starvation (道种破虚) or
        # bloat triggers a single focused repair pass. Best-effort — a
        # failing repair keeps the original spec.
        (
            repaired_world_spec,
            world_richness_repair_llm_run_id,
        ) = await _repair_world_spec_richness_if_needed(
            session=session,
            settings=settings,
            project=project,
            premise=premise,
            book_spec_payload=book_spec_payload,
            world_spec_payload=world_spec_payload,
            workflow_run_id=workflow_run.id,
        )
        if world_richness_repair_llm_run_id is not None:
            llm_run_ids.append(world_richness_repair_llm_run_id)
            world_spec_payload = repaired_world_spec
        if hook_spec is not None:
            from bestseller.services.hook_propagation import apply_hook_to_world_spec

            world_spec_payload = apply_hook_to_world_spec(world_spec_payload, hook_spec)

        current_step_name = "world_spec_source_gate"
        workflow_run.current_step = current_step_name
        world_spec_quality_report = evaluate_world_spec_source_quality(world_spec_payload)
        await _record_prewrite_quality_gate(
            session,
            workflow_run_id=workflow_run.id,
            step_name=current_step_name,
            step_order=step_order,
            report=world_spec_quality_report,
            strict=is_strict_prewrite_project(project),
        )
        world_spec_payload = _attach_prewrite_quality_report(
            world_spec_payload,
            step_name=current_step_name,
            report=world_spec_quality_report,
            project=project,
        )
        step_order += 1

        current_step_name = "generate_world_spec"
        workflow_run.current_step = current_step_name
        world_artifact = await import_planning_artifact(
            session,
            project_slug,
            PlanningArtifactCreate(
                artifact_type=ArtifactType.WORLD_SPEC, content=world_spec_payload
            ),
        )
        artifact_records.append(
            PlanningArtifactRecord(
                artifact_type=ArtifactType.WORLD_SPEC,
                artifact_id=world_artifact.id,
                version_no=world_artifact.version_no,
            )
        )
        await create_workflow_step_run(
            session,
            workflow_run_id=workflow_run.id,
            step_name=current_step_name,
            step_order=step_order,
            status=WorkflowStatus.COMPLETED,
            output_ref={
                "artifact_id": str(world_artifact.id),
                "llm_run_id": str(llm_run_id) if llm_run_id else None,
            },
        )
        _emit_planner_progress(
            progress,
            "planning_step_completed",
            project=project,
            workflow_run_id=workflow_run.id,
            current_step=current_step_name,
            artifact_type=ArtifactType.WORLD_SPEC.value,
            artifact_id=str(world_artifact.id),
            llm_run_id=str(llm_run_id) if llm_run_id else None,
        )
        step_order += 1

        cast_spec_fallback = _fallback_cast_spec(
            project,
            premise,
            book_spec_payload,
            world_spec_payload,
            category_key=_category_key,
            character_name_pool=character_name_pool,
        )
        current_step_name = "generate_cast_spec"
        workflow_run.current_step = current_step_name
        _emit_planner_progress(
            progress,
            "planning_step_started",
            project=project,
            workflow_run_id=workflow_run.id,
            current_step=current_step_name,
            artifact_type=ArtifactType.CAST_SPEC.value,
        )
        cast_system, cast_user = _cast_spec_prompts(
            project,
            book_spec_payload,
            world_spec_payload,
            locked_names=_string_list(character_name_pool.get("locked_names")),
        )
        cast_user = attach_planner_methodology(
            cast_user,
            stage=MethodologyStage.OUTLINE_BOOK,
            project_ctx=project,
            token_budget=800,
        )
        cast_spec_payload, llm_run_id = await _generate_structured_artifact(
            session,
            settings,
            project=project,
            logical_name="cast_spec",
            system_prompt=cast_system,
            user_prompt=cast_user,
            fallback_payload=cast_spec_fallback,
            workflow_run_id=workflow_run.id,
            validator=parse_cast_spec_input,
        )
        cast_spec_payload = _repair_cast_identity_locks_for_planner(project, cast_spec_payload)
        cast_spec_payload = _repair_protagonist_name_drift_for_planner(
            project,
            cast_spec_payload,
            protagonist_name=_protagonist_name_from_payload(book_spec_payload)
            or llm_protagonist_name,
            artifact_type=ArtifactType.CAST_SPEC.value,
        )
        cast_spec_payload = _repair_rule_survival_goal_drift_for_planner(
            project,
            cast_spec_payload,
            premise=premise,
            artifact_type=ArtifactType.CAST_SPEC.value,
        )
        cast_spec_payload = _enforce_premise_locked_cast(
            cast_spec_payload, premise, language=_planner_language(project)
        )
        if llm_run_id is not None:
            llm_run_ids.append(llm_run_id)
        cast_artifact = await import_planning_artifact(
            session,
            project_slug,
            PlanningArtifactCreate(artifact_type=ArtifactType.CAST_SPEC, content=cast_spec_payload),
        )
        artifact_records.append(
            PlanningArtifactRecord(
                artifact_type=ArtifactType.CAST_SPEC,
                artifact_id=cast_artifact.id,
                version_no=cast_artifact.version_no,
            )
        )
        await create_workflow_step_run(
            session,
            workflow_run_id=workflow_run.id,
            step_name=current_step_name,
            step_order=step_order,
            status=WorkflowStatus.COMPLETED,
            output_ref={
                "artifact_id": str(cast_artifact.id),
                "llm_run_id": str(llm_run_id) if llm_run_id else None,
            },
        )
        _emit_planner_progress(
            progress,
            "planning_step_completed",
            project=project,
            workflow_run_id=workflow_run.id,
            current_step=current_step_name,
            artifact_type=ArtifactType.CAST_SPEC.value,
            artifact_id=str(cast_artifact.id),
            llm_run_id=str(llm_run_id) if llm_run_id else None,
        )
        step_order += 1

        (
            repaired_cast_spec,
            personhood_repair_llm_run_id,
        ) = await _repair_cast_personhood_if_needed(
            session=session,
            settings=settings,
            project=project,
            book_spec_payload=book_spec_payload,
            world_spec_payload=world_spec_payload,
            cast_spec_payload=cast_spec_payload,
            workflow_run_id=workflow_run.id,
        )
        if personhood_repair_llm_run_id is not None:
            llm_run_ids.append(personhood_repair_llm_run_id)
            cast_spec_payload = _repair_cast_identity_locks_for_planner(
                project, repaired_cast_spec
            )
            cast_spec_payload = _repair_protagonist_name_drift_for_planner(
                project,
                cast_spec_payload,
                protagonist_name=_protagonist_name_from_payload(book_spec_payload)
                or llm_protagonist_name,
                artifact_type=ArtifactType.CAST_SPEC.value,
            )
            cast_spec_payload = _repair_rule_survival_goal_drift_for_planner(
                project,
                cast_spec_payload,
                premise=premise,
                artifact_type=ArtifactType.CAST_SPEC.value,
            )
            cast_artifact = await import_planning_artifact(
                session,
                project_slug,
                PlanningArtifactCreate(
                    artifact_type=ArtifactType.CAST_SPEC,
                    content=cast_spec_payload,
                ),
            )
            artifact_records.append(
                PlanningArtifactRecord(
                    artifact_type=ArtifactType.CAST_SPEC,
                    artifact_id=cast_artifact.id,
                    version_no=cast_artifact.version_no,
                )
            )

        # ── Foundation richness gate: scan the cast spec for thin
        # antagonist roster / insufficient active_volumes coverage before
        # anything downstream consumes it. If critical, repair once with a
        # focused LLM pass and persist a new cast-spec artifact version so
        # the fix is visible in project history. Best-effort: repair
        # failures fall back to the original spec.
        _foundation_hierarchy = compute_linear_hierarchy(max(project.target_chapters, 1))
        _foundation_volume_count = int(_foundation_hierarchy.get("volume_count") or 1)
        if _foundation_volume_count > 1:
            (
                repaired_cast_spec,
                foundation_repair_llm_run_id,
            ) = await _repair_cast_foundation_if_needed(
                session=session,
                settings=settings,
                project=project,
                book_spec_payload=book_spec_payload,
                world_spec_payload=world_spec_payload,
                cast_spec_payload=cast_spec_payload,
                volume_count=_foundation_volume_count,
                workflow_run_id=workflow_run.id,
            )
            if foundation_repair_llm_run_id is not None:
                llm_run_ids.append(foundation_repair_llm_run_id)
                cast_spec_payload = _repair_cast_identity_locks_for_planner(
                    project, repaired_cast_spec
                )
                cast_spec_payload = _repair_protagonist_name_drift_for_planner(
                    project,
                    cast_spec_payload,
                    protagonist_name=_protagonist_name_from_payload(book_spec_payload)
                    or llm_protagonist_name,
                    artifact_type=ArtifactType.CAST_SPEC.value,
                )
                cast_spec_payload = _repair_rule_survival_goal_drift_for_planner(
                    project,
                    cast_spec_payload,
                    premise=premise,
                    artifact_type=ArtifactType.CAST_SPEC.value,
                )
                # Persist repaired cast spec as a new artifact version so
                # downstream readers (retrieval, plan judge, review stages)
                # see the enriched roster.
                cast_artifact = await import_planning_artifact(
                    session,
                    project_slug,
                    PlanningArtifactCreate(
                        artifact_type=ArtifactType.CAST_SPEC,
                        content=cast_spec_payload,
                    ),
                )
                artifact_records.append(
                    PlanningArtifactRecord(
                        artifact_type=ArtifactType.CAST_SPEC,
                        artifact_id=cast_artifact.id,
                        version_no=cast_artifact.version_no,
                    )
                )

        # ── Antagonist lifecycle gate: after foundation_richness fills in
        # the antagonist_forces roster, validate that the `antagonists`
        # array (with line_role, stages_of_relevance, resolution_type, and
        # transitions) is lifecycle-rich. Catches the post-fix regression
        # where each volume has a distinct enemy but every enemy is a
        # one-volume kill-and-move-on boss.
        if _foundation_volume_count > 1:
            (
                repaired_cast_spec,
                lifecycle_repair_llm_run_id,
            ) = await _repair_cast_spec_antagonist_lifecycle_if_needed(
                session=session,
                settings=settings,
                project=project,
                book_spec_payload=book_spec_payload,
                world_spec_payload=world_spec_payload,
                cast_spec_payload=cast_spec_payload,
                volume_count=_foundation_volume_count,
                workflow_run_id=workflow_run.id,
            )
            if lifecycle_repair_llm_run_id is not None:
                llm_run_ids.append(lifecycle_repair_llm_run_id)
                cast_spec_payload = _repair_cast_identity_locks_for_planner(
                    project, repaired_cast_spec
                )
                cast_spec_payload = _repair_protagonist_name_drift_for_planner(
                    project,
                    cast_spec_payload,
                    protagonist_name=_protagonist_name_from_payload(book_spec_payload)
                    or llm_protagonist_name,
                    artifact_type=ArtifactType.CAST_SPEC.value,
                )
                cast_spec_payload = _repair_rule_survival_goal_drift_for_planner(
                    project,
                    cast_spec_payload,
                    premise=premise,
                    artifact_type=ArtifactType.CAST_SPEC.value,
                )
                cast_artifact = await import_planning_artifact(
                    session,
                    project_slug,
                    PlanningArtifactCreate(
                        artifact_type=ArtifactType.CAST_SPEC,
                        content=cast_spec_payload,
                    ),
                )
                artifact_records.append(
                    PlanningArtifactRecord(
                        artifact_type=ArtifactType.CAST_SPEC,
                        artifact_id=cast_artifact.id,
                        version_no=cast_artifact.version_no,
                    )
                )

        # ── Relationship scaling gate: the social-fabric peer of the
        # world-richness / foundation-richness gates. Validates that the
        # supporting_cast roster is large enough (≥ 1.5 × volume_count),
        # spans ≥ 3 distinct role categories, and covers every volume with
        # at least one active non-antagonist. Without this gate, long
        # novels ship with only 3-5 named side characters and every
        # volume recycles the same faces.
        if _foundation_volume_count > 1:
            (
                repaired_cast_spec,
                relationship_repair_llm_run_id,
            ) = await _repair_cast_spec_relationship_scaling_if_needed(
                session=session,
                settings=settings,
                project=project,
                book_spec_payload=book_spec_payload,
                world_spec_payload=world_spec_payload,
                cast_spec_payload=cast_spec_payload,
                volume_count=_foundation_volume_count,
                workflow_run_id=workflow_run.id,
            )
            if relationship_repair_llm_run_id is not None:
                llm_run_ids.append(relationship_repair_llm_run_id)
                cast_spec_payload = _repair_cast_identity_locks_for_planner(
                    project, repaired_cast_spec
                )
                cast_spec_payload = _repair_protagonist_name_drift_for_planner(
                    project,
                    cast_spec_payload,
                    protagonist_name=_protagonist_name_from_payload(book_spec_payload)
                    or llm_protagonist_name,
                    artifact_type=ArtifactType.CAST_SPEC.value,
                )
                cast_spec_payload = _repair_rule_survival_goal_drift_for_planner(
                    project,
                    cast_spec_payload,
                    premise=premise,
                    artifact_type=ArtifactType.CAST_SPEC.value,
                )
                cast_artifact = await import_planning_artifact(
                    session,
                    project_slug,
                    PlanningArtifactCreate(
                        artifact_type=ArtifactType.CAST_SPEC,
                        content=cast_spec_payload,
                    ),
                )
                artifact_records.append(
                    PlanningArtifactRecord(
                        artifact_type=ArtifactType.CAST_SPEC,
                        artifact_id=cast_artifact.id,
                        version_no=cast_artifact.version_no,
                    )
                )

        current_step_name = "cast_spec_function_gate"
        workflow_run.current_step = current_step_name
        cast_spec_quality_report = evaluate_cast_spec_function_quality(cast_spec_payload)
        await _record_prewrite_quality_gate(
            session,
            workflow_run_id=workflow_run.id,
            step_name=current_step_name,
            step_order=step_order,
            report=cast_spec_quality_report,
            strict=is_strict_prewrite_project(project),
        )
        cast_spec_payload = _attach_prewrite_quality_report(
            cast_spec_payload,
            step_name=current_step_name,
            report=cast_spec_quality_report,
            project=project,
        )
        cast_artifact = await import_planning_artifact(
            session,
            project_slug,
            PlanningArtifactCreate(
                artifact_type=ArtifactType.CAST_SPEC,
                content=cast_spec_payload,
            ),
        )
        artifact_records.append(
            PlanningArtifactRecord(
                artifact_type=ArtifactType.CAST_SPEC,
                artifact_id=cast_artifact.id,
                version_no=cast_artifact.version_no,
            )
        )
        step_order += 1

        current_step_name = "generate_public_emotion_kernel"
        workflow_run.current_step = current_step_name
        _emit_planner_progress(
            progress,
            "planning_step_started",
            project=project,
            workflow_run_id=workflow_run.id,
            current_step=current_step_name,
            artifact_type=ArtifactType.PUBLIC_EMOTION_KERNEL.value,
        )
        public_emotion_payload = await _generate_public_emotion_kernel_artifact(
            session,
            project=project,
            project_slug=project_slug,
            premise=premise,
            book_spec_payload=book_spec_payload,
            workflow_run_id=workflow_run.id,
            step_order=step_order,
            artifact_records=artifact_records,
        )
        _emit_planner_progress(
            progress,
            "planning_step_completed",
            project=project,
            workflow_run_id=workflow_run.id,
            current_step=current_step_name,
            artifact_type=ArtifactType.PUBLIC_EMOTION_KERNEL.value,
        )
        step_order += 1

        current_step_name = "generate_compliance_boundary_kernel"
        workflow_run.current_step = current_step_name
        _emit_planner_progress(
            progress,
            "planning_step_started",
            project=project,
            workflow_run_id=workflow_run.id,
            current_step=current_step_name,
            artifact_type=ArtifactType.COMPLIANCE_BOUNDARY_KERNEL.value,
        )
        compliance_boundary_payload = await _generate_compliance_boundary_kernel_artifact(
            session,
            project=project,
            project_slug=project_slug,
            workflow_run_id=workflow_run.id,
            step_order=step_order,
            artifact_records=artifact_records,
        )
        _emit_planner_progress(
            progress,
            "planning_step_completed",
            project=project,
            workflow_run_id=workflow_run.id,
            current_step=current_step_name,
            artifact_type=ArtifactType.COMPLIANCE_BOUNDARY_KERNEL.value,
        )
        step_order += 1

        story_design_payload: dict[str, Any] | None = None
        if settings.pipeline.enable_story_design_kernel:
            current_step_name = "generate_story_design_kernel"
            workflow_run.current_step = current_step_name
            _emit_planner_progress(
                progress,
                "planning_step_started",
                project=project,
                workflow_run_id=workflow_run.id,
                current_step=current_step_name,
                artifact_type=ArtifactType.STORY_DESIGN_KERNEL.value,
            )
            story_design_payload = await _generate_story_design_kernel(
                session,
                settings,
                project=project,
                project_slug=project_slug,
                premise=premise,
                book_spec_payload=book_spec_payload,
                world_spec_payload=world_spec_payload,
                cast_spec_payload=cast_spec_payload,
                category_key=_category_key,
                workflow_run_id=workflow_run.id,
                step_order=step_order,
                llm_run_ids=llm_run_ids,
                artifact_records=artifact_records,
            )
            _emit_planner_progress(
                progress,
                "planning_step_completed",
                project=project,
                workflow_run_id=workflow_run.id,
                current_step=current_step_name,
                artifact_type=ArtifactType.STORY_DESIGN_KERNEL.value,
            )
            step_order += 2

        current_step_name = "generate_entry_system_kernel"
        workflow_run.current_step = current_step_name
        _emit_planner_progress(
            progress,
            "planning_step_started",
            project=project,
            workflow_run_id=workflow_run.id,
            current_step=current_step_name,
            artifact_type=ArtifactType.ENTRY_SYSTEM_KERNEL.value,
        )
        await _generate_entry_system_kernel_artifacts(
            session,
            project=project,
            project_slug=project_slug,
            story_design_kernel=story_design_payload,
            workflow_run_id=workflow_run.id,
            step_order=step_order,
            artifact_records=artifact_records,
        )
        _emit_planner_progress(
            progress,
            "planning_step_completed",
            project=project,
            workflow_run_id=workflow_run.id,
            current_step=current_step_name,
            artifact_type=ArtifactType.ENTRY_SYSTEM_KERNEL.value,
        )
        step_order += 1

        emotion_driven_payload: dict[str, Any] | None = None
        if settings.pipeline.enable_emotion_driven_kernel:
            current_step_name = "generate_emotion_driven_kernel"
            workflow_run.current_step = current_step_name
            _emit_planner_progress(
                progress,
                "planning_step_started",
                project=project,
                workflow_run_id=workflow_run.id,
                current_step=current_step_name,
                artifact_type=ArtifactType.EMOTION_DRIVEN_KERNEL.value,
            )
            emotion_driven_payload = await _generate_emotion_driven_kernel(
                session,
                settings,
                project=project,
                project_slug=project_slug,
                premise=premise,
                book_spec_payload=book_spec_payload,
                world_spec_payload=world_spec_payload,
                cast_spec_payload=cast_spec_payload,
                category_key=_category_key,
                workflow_run_id=workflow_run.id,
                step_order=step_order,
                llm_run_ids=llm_run_ids,
                artifact_records=artifact_records,
                story_design_kernel=story_design_payload,
            )
            _emit_planner_progress(
                progress,
                "planning_step_completed",
                project=project,
                workflow_run_id=workflow_run.id,
                current_step=current_step_name,
                artifact_type=ArtifactType.EMOTION_DRIVEN_KERNEL.value,
            )
            step_order += 1

        # ── Act Plan: macro narrative structure for long novels ──
        hierarchy = compute_linear_hierarchy(max(project.target_chapters, 1))
        act_plan_payload: list[dict[str, Any]] | None = None
        if (
            hierarchy["act_count"] > 1
            and project.target_chapters > settings.pipeline.act_plan_threshold
        ):
            act_plan_fallback = _fallback_act_plan(
                project, book_spec_payload, cast_spec_payload, world_spec_payload
            )
            current_step_name = "generate_act_plan"
            workflow_run.current_step = current_step_name
            _emit_planner_progress(
                progress,
                "planning_step_started",
                project=project,
                workflow_run_id=workflow_run.id,
                current_step=current_step_name,
                artifact_type=ArtifactType.ACT_PLAN.value,
            )
            act_system, act_user = _act_plan_prompts(
                project, book_spec_payload, world_spec_payload, cast_spec_payload
            )
            act_user = attach_planner_methodology(
                act_user,
                stage=MethodologyStage.OUTLINE_VOLUME,
                project_ctx=project,
                token_budget=700,
            )
            act_plan_payload_raw, llm_run_id = await _generate_structured_artifact(
                session,
                settings,
                project=project,
                logical_name="act_plan",
                system_prompt=act_system,
                user_prompt=act_user,
                fallback_payload={"acts": act_plan_fallback},
                workflow_run_id=workflow_run.id,
            )
            if llm_run_id is not None:
                llm_run_ids.append(llm_run_id)
            # Extract acts list from payload (may be {"acts": [...]} or [...])
            if isinstance(act_plan_payload_raw, dict) and "acts" in act_plan_payload_raw:
                act_plan_payload = act_plan_payload_raw["acts"]
            elif isinstance(act_plan_payload_raw, list):
                act_plan_payload = act_plan_payload_raw
            else:
                act_plan_payload = act_plan_fallback

            act_artifact = await import_planning_artifact(
                session,
                project_slug,
                PlanningArtifactCreate(
                    artifact_type=ArtifactType.ACT_PLAN, content={"acts": act_plan_payload}
                ),
            )
            artifact_records.append(
                PlanningArtifactRecord(
                    artifact_type=ArtifactType.ACT_PLAN,
                    artifact_id=act_artifact.id,
                    version_no=act_artifact.version_no,
                )
            )
            # Persist act plan to project metadata
            from bestseller.services.story_bible import upsert_act_plan

            await upsert_act_plan(session, project, act_plan_payload)

            await create_workflow_step_run(
                session,
                workflow_run_id=workflow_run.id,
                step_name=current_step_name,
                step_order=step_order,
                status=WorkflowStatus.COMPLETED,
                output_ref={
                    "artifact_id": str(act_artifact.id),
                    "llm_run_id": str(llm_run_id) if llm_run_id else None,
                },
            )
            _emit_planner_progress(
                progress,
                "planning_step_completed",
                project=project,
                workflow_run_id=workflow_run.id,
                current_step=current_step_name,
                artifact_type=ArtifactType.ACT_PLAN.value,
                artifact_id=str(act_artifact.id),
                llm_run_id=str(llm_run_id) if llm_run_id else None,
            )
            step_order += 1

        volume_plan_fallback = _fallback_volume_plan(
            project,
            book_spec_payload,
            cast_spec_payload,
            world_spec_payload,
            category_key=_category_key,
        )
        current_step_name = "generate_volume_plan"
        workflow_run.current_step = current_step_name
        _emit_planner_progress(
            progress,
            "planning_step_started",
            project=project,
            workflow_run_id=workflow_run.id,
            current_step=current_step_name,
            artifact_type=ArtifactType.VOLUME_PLAN.value,
        )
        volume_system, volume_user = _volume_plan_prompts(
            project,
            book_spec_payload,
            world_spec_payload,
            cast_spec_payload,
            act_plan=act_plan_payload,
        )
        volume_user = attach_planner_methodology(
            volume_user,
            stage=MethodologyStage.OUTLINE_VOLUME,
            project_ctx=project,
            token_budget=700,
        )
        volume_plan_payload, llm_run_id = await _generate_structured_artifact(
            session,
            settings,
            project=project,
            logical_name="volume_plan",
            system_prompt=volume_system,
            user_prompt=volume_user,
            fallback_payload=volume_plan_fallback,
            workflow_run_id=workflow_run.id,
            validator=_volume_plan_validator_for(project),
        )
        if llm_run_id is not None:
            llm_run_ids.append(llm_run_id)

        # ── Volume convergence gate: scan the freshly-generated plan for
        # cross-volume plot repetition. On critical findings, auto-repair
        # with the convergence block fed back into the prompt so the LLM
        # can diverge the offending volumes. The repair is best-effort:
        # failures fall through to the original plan rather than aborting.
        (
            volume_plan_payload,
            conv_repair_llm_run_id,
        ) = await _repair_volume_plan_convergence_if_needed(
            session=session,
            settings=settings,
            project=project,
            book_spec_payload=book_spec_payload,
            world_spec_payload=world_spec_payload,
            cast_spec_payload=cast_spec_payload,
            act_plan_payload=act_plan_payload,
            volume_plan_payload=volume_plan_payload,
            workflow_run_id=workflow_run.id,
        )
        if conv_repair_llm_run_id is not None:
            llm_run_ids.append(conv_repair_llm_run_id)

        # ── Foreshadowing scaling gate: production data shows every book
        # (even 1200-chapter novels) was producing only 5-8 clues total.
        # Scan the volume plan's aggregate plant/payoff counts and repair
        # once if below the chapter-scaled floor.
        (
            volume_plan_payload,
            foresh_repair_llm_run_id,
        ) = await _repair_volume_plan_foreshadowing_if_needed(
            session=session,
            settings=settings,
            project=project,
            book_spec_payload=book_spec_payload,
            world_spec_payload=world_spec_payload,
            cast_spec_payload=cast_spec_payload,
            act_plan_payload=act_plan_payload,
            volume_plan_payload=volume_plan_payload,
            workflow_run_id=workflow_run.id,
        )
        if foresh_repair_llm_run_id is not None:
            llm_run_ids.append(foresh_repair_llm_run_id)
        if hook_spec is not None:
            from bestseller.services.hook_propagation import apply_hook_to_volume_plan

            volume_plan_payload = apply_hook_to_volume_plan(volume_plan_payload, hook_spec)

        current_step_name = "volume_plan_gate"
        workflow_run.current_step = current_step_name
        volume_plan_quality_report = evaluate_volume_plan_quality(
            volume_plan_payload if isinstance(volume_plan_payload, list) else [],
            target_chapters=int(getattr(project, "target_chapters", 0) or 0),
        )
        await _record_prewrite_quality_gate(
            session,
            workflow_run_id=workflow_run.id,
            step_name=current_step_name,
            step_order=step_order,
            report=volume_plan_quality_report,
            strict=is_strict_prewrite_project(project),
        )
        if isinstance(volume_plan_payload, dict):
            volume_plan_payload = _attach_prewrite_quality_report(
                volume_plan_payload,
                step_name=current_step_name,
                report=volume_plan_quality_report,
                project=project,
            )
        elif isinstance(volume_plan_payload, list):
            volume_plan_payload = [
                _attach_prewrite_quality_report(
                    entry,
                    step_name=current_step_name,
                    report=volume_plan_quality_report,
                    project=project,
                )
                if isinstance(entry, dict)
                else entry
                for entry in volume_plan_payload
            ]
        step_order += 1

        current_step_name = "generate_volume_plan"
        workflow_run.current_step = current_step_name
        volume_artifact = await import_planning_artifact(
            session,
            project_slug,
            PlanningArtifactCreate(
                artifact_type=ArtifactType.VOLUME_PLAN, content=volume_plan_payload
            ),
        )
        artifact_records.append(
            PlanningArtifactRecord(
                artifact_type=ArtifactType.VOLUME_PLAN,
                artifact_id=volume_artifact.id,
                version_no=volume_artifact.version_no,
            )
        )
        await create_workflow_step_run(
            session,
            workflow_run_id=workflow_run.id,
            step_name=current_step_name,
            step_order=step_order,
            status=WorkflowStatus.COMPLETED,
            output_ref={
                "artifact_id": str(volume_artifact.id),
                "llm_run_id": str(llm_run_id) if llm_run_id else None,
            },
        )
        _emit_planner_progress(
            progress,
            "planning_step_completed",
            project=project,
            workflow_run_id=workflow_run.id,
            current_step=current_step_name,
            artifact_type=ArtifactType.VOLUME_PLAN.value,
            artifact_id=str(volume_artifact.id),
            llm_run_id=str(llm_run_id) if llm_run_id else None,
        )
        step_order += 1

        if settings.pipeline.enable_worldview_progression_gate:
            current_step_name = "worldview_progression_gate"
            workflow_run.current_step = current_step_name
            _emit_planner_progress(
                progress,
                "planning_step_started",
                project=project,
                workflow_run_id=workflow_run.id,
                current_step=current_step_name,
            )
            await _run_worldview_progression_gate(
                session,
                settings,
                project=project,
                story_design_kernel=story_design_payload,
                volume_plan_payload=volume_plan_payload,
                workflow_run_id=workflow_run.id,
                step_order=step_order,
            )
            _emit_planner_progress(
                progress,
                "planning_step_completed",
                project=project,
                workflow_run_id=workflow_run.id,
                current_step=current_step_name,
            )
            step_order += 1

        # ── Plan Judge: validate plan against genre-specific rubrics ──
        if settings.quality.enable_plan_judge:
            from bestseller.services.plan_judge import validate_plan as _validate_plan

            plan_validation = _validate_plan(
                genre=project.genre,
                sub_genre=project.sub_genre,
                book_spec=book_spec_payload,
                world_spec=world_spec_payload,
                cast_spec=cast_spec_payload,
                volume_plan=volume_plan_payload if isinstance(volume_plan_payload, list) else [],
                language=project.language,
            )
            validation_artifact = await import_planning_artifact(
                session,
                project_slug,
                PlanningArtifactCreate(
                    artifact_type=ArtifactType.PLAN_VALIDATION,
                    content=plan_validation.model_dump(mode="json"),
                ),
            )
            artifact_records.append(
                PlanningArtifactRecord(
                    artifact_type=ArtifactType.PLAN_VALIDATION,
                    artifact_id=validation_artifact.id,
                    version_no=validation_artifact.version_no,
                )
            )

            # ── Auto-repair: re-generate volume plan if critical findings ──
            if not plan_validation.overall_pass:
                critical_findings = [
                    f for f in plan_validation.findings if f.severity == "critical"
                ]
                if critical_findings and isinstance(volume_plan_payload, list):
                    try:
                        repair_notes = "\n".join(
                            f"- {f.message}" + (f" ({f.suggestion})" if f.suggestion else "")
                            for f in critical_findings
                        )
                        repair_system, repair_user = _volume_plan_prompts(
                            project,
                            book_spec_payload,
                            world_spec_payload,
                            cast_spec_payload,
                            act_plan=act_plan_payload,
                        )
                        is_en = is_english_language(project.language)
                        repair_user += (
                            f"\n\n{'[Plan repair — fix these critical issues]' if is_en else '【规划修复 — 必须修正以下关键问题】'}"
                            f"\n{repair_notes}"
                            f"\n{'Regenerate the volume plan addressing all issues above.' if is_en else '请重新生成卷计划，确保修正以上所有问题。'}"
                        )

                        repaired_payload, repair_llm_run_id = await _generate_structured_artifact(
                            session,
                            settings,
                            project=project,
                            logical_name="volume_plan_repair",
                            system_prompt=repair_system,
                            user_prompt=repair_user,
                            fallback_payload=volume_plan_payload,
                            workflow_run_id=workflow_run.id,
                            validator=parse_volume_plan_input,
                        )
                        if repair_llm_run_id is not None:
                            llm_run_ids.append(repair_llm_run_id)
                        volume_plan_payload = repaired_payload
                        volume_artifact = await import_planning_artifact(
                            session,
                            project_slug,
                            PlanningArtifactCreate(
                                artifact_type=ArtifactType.VOLUME_PLAN, content=volume_plan_payload
                            ),
                        )
                        repaired_validation = _validate_plan(
                            genre=project.genre,
                            sub_genre=project.sub_genre,
                            book_spec=book_spec_payload,
                            world_spec=world_spec_payload,
                            cast_spec=cast_spec_payload,
                            volume_plan=(
                                volume_plan_payload
                                if isinstance(volume_plan_payload, list)
                                else []
                            ),
                            language=project.language,
                        )
                        repair_validation_artifact = await import_planning_artifact(
                            session,
                            project_slug,
                            PlanningArtifactCreate(
                                artifact_type=ArtifactType.PLAN_VALIDATION,
                                content=repaired_validation.model_dump(mode="json"),
                            ),
                        )
                        artifact_records.append(
                            PlanningArtifactRecord(
                                artifact_type=ArtifactType.PLAN_VALIDATION,
                                artifact_id=repair_validation_artifact.id,
                                version_no=repair_validation_artifact.version_no,
                            )
                        )
                        workflow_run.metadata_json = {
                            **(workflow_run.metadata_json or {}),
                            "plan_judge_auto_repair": {
                                "attempted": True,
                                "llm_run_id": str(repair_llm_run_id)
                                if repair_llm_run_id
                                else None,
                                "validation_artifact_id": str(repair_validation_artifact.id),
                                "passed": repaired_validation.overall_pass,
                                "remaining_findings": [
                                    finding.model_dump(mode="json")
                                    for finding in repaired_validation.findings[:12]
                                ],
                            },
                        }
                        if not repaired_validation.overall_pass:
                            remaining = [
                                f"{finding.category}: {finding.message}"
                                for finding in repaired_validation.findings
                                if finding.severity == "critical"
                            ][:5]
                            raise PlannerFallbackError(
                                "Plan auto-repair failed validation after regeneration: "
                                + ("; ".join(remaining) if remaining else "critical findings remain")
                            )
                        plan_validation = repaired_validation
                    except PlannerFallbackError:
                        raise
                    except Exception as exc:
                        logger.warning(
                            "Plan auto-repair failed; refusing to continue with original plan",
                            exc_info=True,
                        )
                        raise PlannerFallbackError(
                            "Plan auto-repair failed; refusing to continue with an invalid plan."
                        ) from exc

        qimao_opening_contract = persist_qimao_opening_contract(
            project,
            premise=premise,
            book_spec=book_spec_payload,
            cast_spec=cast_spec_payload,
            volume_plan=volume_plan_payload,
        )
        if qimao_opening_contract:
            workflow_run.metadata_json = {
                **(workflow_run.metadata_json or {}),
                "opening_quality_contract": qimao_opening_contract,
                "qimao_opening_contract": qimao_opening_contract,
            }

        prewrite_repair_directives: list[str] = []
        prewrite_payload: dict[str, Any] | None = None
        if settings.pipeline.enable_prewrite_readiness_gate:
            current_step_name = "prewrite_readiness_gate"
            workflow_run.current_step = current_step_name
            _emit_planner_progress(
                progress,
                "planning_step_started",
                project=project,
                workflow_run_id=workflow_run.id,
                current_step=current_step_name,
                artifact_type=ArtifactType.PREWRITE_READINESS.value,
            )
            prewrite_payload = await _run_prewrite_readiness_gate(
                session,
                settings,
                project=project,
                project_slug=project_slug,
                book_spec_payload=book_spec_payload,
                world_spec_payload=world_spec_payload,
                cast_spec_payload=cast_spec_payload,
                volume_plan_payload=volume_plan_payload,
                story_design_kernel=story_design_payload,
                emotion_driven_kernel=emotion_driven_payload,
                public_emotion_kernel=public_emotion_payload,
                compliance_boundary_kernel=compliance_boundary_payload,
                workflow_run_id=workflow_run.id,
                step_order=step_order,
                artifact_records=artifact_records,
            )
            prewrite_repair_directives = _string_list(
                prewrite_payload.get("prewrite_repair_directives")
            )
            _emit_planner_progress(
                progress,
                "planning_step_completed",
                project=project,
                workflow_run_id=workflow_run.id,
                current_step=current_step_name,
                artifact_type=ArtifactType.PREWRITE_READINESS.value,
            )
            step_order += 1

        current_step_name = "story_grounded_title_revision"
        workflow_run.current_step = current_step_name
        _emit_planner_progress(
            progress,
            "planning_step_started",
            project=project,
            workflow_run_id=workflow_run.id,
            current_step=current_step_name,
        )
        (
            refreshed_title,
            title_was_revised,
            title_llm_run_id,
            title_refresh_evidence,
        ) = await _refresh_story_grounded_project_title(
            session,
            settings,
            project=project,
            premise=premise,
            book_spec_payload=book_spec_payload,
            cast_spec_payload=cast_spec_payload,
            story_design_payload=story_design_payload,
            volume_plan_payload=volume_plan_payload,
            prewrite_payload=prewrite_payload,
            workflow_run_id=workflow_run.id,
        )
        if title_llm_run_id is not None:
            llm_run_ids.append(title_llm_run_id)
        await create_workflow_step_run(
            session,
            workflow_run_id=workflow_run.id,
            step_name=current_step_name,
            step_order=step_order,
            status=WorkflowStatus.COMPLETED,
            output_ref={
                **title_refresh_evidence,
                "title": refreshed_title,
                "llm_run_id": str(title_llm_run_id) if title_llm_run_id else None,
                "reused": not title_was_revised,
            },
        )
        _emit_planner_progress(
            progress,
            "planning_step_completed",
            project=project,
            workflow_run_id=workflow_run.id,
            current_step=current_step_name,
            title=refreshed_title,
            revised=title_was_revised,
            llm_run_id=str(title_llm_run_id) if title_llm_run_id else None,
        )
        step_order += 1

        # ── Per-volume chapter outline generation ──
        normalized_vp = _mapping_list(volume_plan_payload)
        outline_fallback = _fallback_chapter_outline_batch(
            project,
            book_spec_payload,
            cast_spec_payload,
            normalized_vp,
            category_key=_category_key,
        )
        all_outline_chapters: list[dict[str, Any]] = []
        chapter_offset = 1

        _project_total_chapters = max(int(getattr(project, "target_chapters", 0) or 0), 1)
        for vol_entry in normalized_vp:
            vol_num = int(vol_entry.get("volume_number", 1))
            vol_ch_count = int(vol_entry.get("chapter_count_target", 10))
            # Clamp: a single volume's chapter count can never exceed the
            # project's total target. The LLM sometimes echoes target_word_count
            # (e.g. 2200) into chapter_count_target, which makes the
            # completeness guard demand thousands of chapters and abort. A
            # volume cannot contain more chapters than the whole book.
            if vol_ch_count > _project_total_chapters:
                logger.warning(
                    "volume %s chapter_count_target=%d exceeds project total %d; "
                    "clamping (likely an LLM word-count/chapter-count confusion)",
                    vol_num,
                    vol_ch_count,
                    _project_total_chapters,
                )
                vol_ch_count = _project_total_chapters
            vol_ch_count = max(1, vol_ch_count)

            # Extract this volume's fallback chapters
            vol_fallback_chapters = [
                ch
                for ch in outline_fallback.get("chapters", [])
                if ch.get("volume_number") == vol_num
            ]
            vol_fallback = {
                "batch_name": f"volume-{vol_num}-outline",
                "chapters": vol_fallback_chapters,
            }

            current_step_name = f"generate_volume_{vol_num}_outline"
            workflow_run.current_step = current_step_name
            _emit_planner_progress(
                progress,
                "planning_step_started",
                project=project,
                workflow_run_id=workflow_run.id,
                current_step=current_step_name,
                artifact_type=ArtifactType.VOLUME_CHAPTER_OUTLINE.value,
                volume_number=vol_num,
                expected_chapters=vol_ch_count,
            )

            # Build the revealed-facts/beats ledger so the LLM sees what
            # has already been revealed across prior volumes and can avoid
            # re-revealing or replaying the same beats.
            _ledger_block = await _build_revealed_ledger_block(
                session, project.id, language=_planner_language(project)
            )

            _deceased_constraints = _build_deceased_character_constraints(
                cast_spec_payload, vol_entry, language=project.language or "zh-CN"
            )
            if _deceased_constraints:
                logger.info(
                    "Adding %d deceased-character constraints for volume %d: %s",
                    len(_deceased_constraints),
                    vol_num,
                    _deceased_constraints,
                )
            # Outline commercial-judge feedback from a prior blocked attempt:
            # injecting these directives both instructs the model to fix the
            # flagged scenes and changes the planner input-hash so the outline
            # is regenerated (not reused). Bounded by the round counter the
            # materialize gate maintains.
            _outline_meta = project.metadata_json if isinstance(project.metadata_json, dict) else {}
            _commercial_repair_directives = _string_list(
                _outline_meta.get("outline_commercial_repair_directives")
            )
            _outline_constraints = [
                *prewrite_repair_directives,
                *_deceased_constraints,
                *_commercial_repair_directives,
            ]
            # G4: generate → commercial judge → bounded in-run repair. A failing
            # judge now regenerates the outline WITH its directives appended,
            # instead of shipping a sub-threshold outline (shilouyan-bench-v1
            # vol-1 0.340 shipped verbatim because directives only flowed via an
            # outer heal run's metadata). The round counter prevents a loop.
            _max_judge_repair_rounds = int(
                getattr(settings.pipeline, "outline_commercial_judge_repair_rounds", 1) or 0
            )
            outline_llm_run_ids: list[UUID] = []
            outline_repair_history: list[Any] = []
            outline_commercial_report: dict[str, Any] | None = None
            _judge_directives: list[str] = []
            for _judge_round in range(_max_judge_repair_rounds + 1):
                _round_constraints = [*_outline_constraints, *_judge_directives]
                (
                    vol_outline_payload,
                    _gen_run_ids,
                    _gen_repair_history,
                ) = await _generate_volume_outline_batched(
                    session,
                    settings,
                    project=project,
                    workflow_run_id=workflow_run.id,
                    logical_name=f"volume_{vol_num}_chapter_outline",
                    book_spec=book_spec_payload,
                    cast_spec=cast_spec_payload,
                    volume_plan=normalized_vp,
                    volume_entry=vol_entry,
                    fallback_payload=vol_fallback,
                    volume_number=vol_num,
                    expected_count=vol_ch_count,
                    chapter_number_offset=chapter_offset,
                    revealed_ledger_block=_ledger_block,
                    base_constraints=_round_constraints,
                    progress=progress,
                )
                outline_llm_run_ids.extend(_gen_run_ids)
                if _gen_repair_history:
                    outline_repair_history.extend(_gen_repair_history)

                current_step_name = f"volume_{vol_num}_outline_commercial_judge"
                workflow_run.current_step = current_step_name
                outline_commercial_report = await _run_planner_outline_commercial_judge(
                    session,
                    settings,
                    project=project,
                    workflow_run_id=workflow_run.id,
                    step_name=current_step_name,
                    step_order=step_order,
                    outline_payload=vol_outline_payload if isinstance(vol_outline_payload, dict) else {},
                )
                step_order += 1
                _judge_directives = _outline_judge_repair_directives(
                    outline_commercial_report,
                    round_idx=_judge_round,
                    max_rounds=_max_judge_repair_rounds,
                )
                if not _judge_directives:
                    break
                logger.warning(
                    "volume_%d outline commercial judge failed (score=%.3f); "
                    "in-run repair round %d/%d with %d directive(s).",
                    vol_num,
                    float((outline_commercial_report or {}).get("overall_score") or 0.0),
                    _judge_round + 1,
                    _max_judge_repair_rounds,
                    len(_judge_directives),
                )

            llm_run_ids.extend(outline_llm_run_ids)
            if outline_repair_history:
                workflow_run.metadata_json = {
                    **(workflow_run.metadata_json or {}),
                    "chapter_outline_repair_history": [
                        *(
                            (workflow_run.metadata_json or {}).get("chapter_outline_repair_history")
                            or []
                        ),
                        {
                            "volume_number": vol_num,
                            "logical_name": f"volume_{vol_num}_chapter_outline",
                            "attempts": outline_repair_history,
                        },
                    ],
                }

            vol_chapters = (
                vol_outline_payload.get("chapters", [])
                if isinstance(vol_outline_payload, dict)
                else []
            )
            all_outline_chapters.extend(vol_chapters)

            if outline_commercial_report is not None:
                vol_outline_payload = _attach_prewrite_quality_report(
                    vol_outline_payload,
                    step_name=current_step_name,
                    report=outline_commercial_report,
                    project=project,
                )
                step_order += 1

            current_step_name = f"generate_volume_{vol_num}_outline"
            workflow_run.current_step = current_step_name
            # Save per-volume artifact
            vol_outline_artifact = await import_planning_artifact(
                session,
                project_slug,
                PlanningArtifactCreate(
                    artifact_type=ArtifactType.VOLUME_CHAPTER_OUTLINE,
                    content=vol_outline_payload,
                    source_run_id=outline_llm_run_ids[-1] if outline_llm_run_ids else None,
                ),
            )
            artifact_records.append(
                PlanningArtifactRecord(
                    artifact_type=ArtifactType.VOLUME_CHAPTER_OUTLINE,
                    artifact_id=vol_outline_artifact.id,
                    version_no=vol_outline_artifact.version_no,
                )
            )
            await create_workflow_step_run(
                session,
                workflow_run_id=workflow_run.id,
                step_name=current_step_name,
                step_order=step_order,
                status=WorkflowStatus.COMPLETED,
                output_ref={
                    "artifact_id": str(vol_outline_artifact.id),
                    "llm_run_ids": [str(item) for item in outline_llm_run_ids],
                    "reused": bool(
                        _mapping(_mapping(vol_outline_payload).get("_meta")).get("reused")
                    ),
                    "batch_count": _mapping(_mapping(vol_outline_payload).get("_meta")).get(
                        "batch_count"
                    ),
                },
            )
            _emit_planner_progress(
                progress,
                "planning_step_completed",
                project=project,
                workflow_run_id=workflow_run.id,
                current_step=current_step_name,
                artifact_type=ArtifactType.VOLUME_CHAPTER_OUTLINE.value,
                volume_number=vol_num,
                generated_chapters=len(vol_chapters),
                artifact_id=str(vol_outline_artifact.id),
                llm_run_ids=[str(item) for item in outline_llm_run_ids],
            )
            step_order += 1
            chapter_offset += vol_ch_count

        # Merge into combined CHAPTER_OUTLINE_BATCH for backward compatibility
        outline_payload = {
            "batch_name": "auto-generated-full-outline",
            "chapters": all_outline_chapters,
            "_meta": planning_artifact_meta(project),
        }
        outline_artifact = await import_planning_artifact(
            session,
            project_slug,
            PlanningArtifactCreate(
                artifact_type=ArtifactType.CHAPTER_OUTLINE_BATCH, content=outline_payload
            ),
        )
        artifact_records.append(
            PlanningArtifactRecord(
                artifact_type=ArtifactType.CHAPTER_OUTLINE_BATCH,
                artifact_id=outline_artifact.id,
                version_no=outline_artifact.version_no,
            )
        )

        if settings.pipeline.enable_story_principle_gate:
            current_step_name = "story_principle_gate"
            workflow_run.current_step = current_step_name
            _emit_planner_progress(
                progress,
                "planning_step_started",
                project=project,
                workflow_run_id=workflow_run.id,
                current_step=current_step_name,
            )
            await _run_story_principle_gate(
                session,
                project=project,
                outline_payload=outline_payload,
                workflow_run_id=workflow_run.id,
                step_order=step_order,
            )
            _emit_planner_progress(
                progress,
                "planning_step_completed",
                project=project,
                workflow_run_id=workflow_run.id,
                current_step=current_step_name,
            )
            step_order += 1

        if settings.pipeline.enable_reverse_outline_gate:
            current_step_name = "reverse_outline_gate"
            workflow_run.current_step = current_step_name
            _emit_planner_progress(
                progress,
                "planning_step_started",
                project=project,
                workflow_run_id=workflow_run.id,
                current_step=current_step_name,
            )
            await _run_reverse_outline_gate(
                session,
                settings,
                project=project,
                story_design_kernel=story_design_payload,
                outline_payload=outline_payload,
                workflow_run_id=workflow_run.id,
                step_order=step_order,
            )
            _emit_planner_progress(
                progress,
                "planning_step_completed",
                project=project,
                workflow_run_id=workflow_run.id,
                current_step=current_step_name,
            )
            step_order += 1

        if settings.pipeline.enable_worldview_compliance_gate:
            current_step_name = "worldview_compliance_gate"
            workflow_run.current_step = current_step_name
            _emit_planner_progress(
                progress,
                "planning_step_started",
                project=project,
                workflow_run_id=workflow_run.id,
                current_step=current_step_name,
            )
            await _run_worldview_compliance_gate(
                session,
                settings,
                project=project,
                story_design_kernel=story_design_payload,
                outline_payload=outline_payload,
                workflow_run_id=workflow_run.id,
                step_order=step_order,
            )
            _emit_planner_progress(
                progress,
                "planning_step_completed",
                project=project,
                workflow_run_id=workflow_run.id,
                current_step=current_step_name,
            )
            step_order += 1

        # ── Promotional brief: title refinement + tags + protagonist intro + blurb ──
        current_step_name = "generate_promotional_brief"
        workflow_run.current_step = current_step_name
        _emit_planner_progress(
            progress,
            "planning_step_started",
            project=project,
            workflow_run_id=workflow_run.id,
            current_step=current_step_name,
        )
        step_order += 1
        _normalized_vp = (
            volume_plan_payload
            if isinstance(volume_plan_payload, list)
            else _mapping_list(volume_plan_payload.get("volumes"))
        )
        await _generate_promotional_brief(
            session,
            settings,
            project=project,
            book_spec=book_spec_payload,
            cast_spec=cast_spec_payload,
            volume_plan=_normalized_vp,
            workflow_run_id=workflow_run.id,
            step_order=step_order,
            llm_run_ids=llm_run_ids,
            artifact_records=artifact_records,
        )
        _emit_planner_progress(
            progress,
            "planning_step_completed",
            project=project,
            workflow_run_id=workflow_run.id,
            current_step=current_step_name,
        )

        workflow_run.current_step = "completed"
        workflow_run.status = WorkflowStatus.COMPLETED.value
        _emit_planner_progress(
            progress,
            "planning_workflow_completed",
            project=project,
            workflow_run_id=workflow_run.id,
            current_step="completed",
        )
        workflow_run.metadata_json = {
            **workflow_run.metadata_json,
            "artifact_ids": {
                record.artifact_type.value: str(record.artifact_id) for record in artifact_records
            },
            "llm_run_ids": [str(item) for item in llm_run_ids],
        }
        await session.flush()

        outline_chapters = (
            outline_payload.get("chapters", []) if isinstance(outline_payload, dict) else []
        )
        volume_count = (
            len(volume_plan_payload)
            if isinstance(volume_plan_payload, list)
            else len(volume_plan_payload.get("volumes", []))
        )
        return NovelPlanningResult(
            workflow_run_id=workflow_run.id,
            project_id=project.id,
            premise=premise,
            artifacts=artifact_records,
            volume_count=volume_count,
            chapter_count=len(outline_chapters),
            llm_run_ids=llm_run_ids,
        )
    except Exception as exc:
        workflow_run.status = WorkflowStatus.FAILED.value
        workflow_run.current_step = current_step_name
        workflow_run.error_message = str(exc)
        await _record_planner_failure_step_once(
            session,
            workflow_run_id=workflow_run.id,
            step_name=current_step_name,
            step_order=step_order,
            error_message=str(exc),
        )
        await session.flush()
        try:
            await session.commit()
        except Exception:
            logger.exception(
                "Failed to persist failed workflow state for project '%s'",
                project_slug,
            )
        raise


# ---------------------------------------------------------------------------
# Progressive Planning: Phase 3 — Foundation + Volume Loop
# ---------------------------------------------------------------------------

WORKFLOW_TYPE_FOUNDATION_PLAN = "generate_foundation_plan"
WORKFLOW_TYPE_VOLUME_PLAN = "generate_volume_plan"


async def _assert_plan_writer_not_locked(session: AsyncSession, project_slug: str) -> ProjectModel:
    """Guard shared by generate_novel_plan and generate_foundation_plan.

    Refuses to re-run top-level planning on a project that has committed
    drafted chapters. Prevents the drifted-VOLUME_PLAN root cause that
    triggered the 200-chapter gap on xianxia-upgrade-1776137730.
    """
    project = await get_project_by_slug(session, project_slug)
    if project is None:
        raise ValueError(f"Project '{project_slug}' was not found.")
    existing_max_written = await _max_written_chapter_number(session, project.id)
    if existing_max_written > 0:
        raise RuntimeError(
            f"Project '{project_slug}' already has {existing_max_written} written "
            "chapters — top-level planning is locked. Resume via "
            "generate_volume_plan per remaining volume instead."
        )
    return project


async def generate_foundation_plan(
    session: AsyncSession,
    settings: AppSettings,
    project_slug: str,
    premise: str,
    *,
    requested_by: str = "system",
    progress: PlanningProgressCallback | None = None,
) -> NovelPlanningResult:
    """Phase A of progressive planning: generate BookSpec → WorldSpec → CastSpec → VolumePlan.

    Identical to ``generate_novel_plan`` but **stops before chapter outlines**.
    Outlines are generated per-volume via ``generate_volume_plan()``.
    """
    project = await _assert_plan_writer_not_locked(session, project_slug)

    workflow_run = await create_workflow_run(
        session,
        project_id=project.id,
        workflow_type=WORKFLOW_TYPE_FOUNDATION_PLAN,
        status=WorkflowStatus.RUNNING,
        scope_type="project",
        scope_id=project.id,
        requested_by=requested_by,
        current_step="store_premise",
        metadata={"project_slug": project.slug, "premise": premise, "progressive": True},
    )
    step_order = 1
    current_step_name = "store_premise"
    llm_run_ids: list[UUID] = []
    artifact_records: list[PlanningArtifactRecord] = []

    # Resolve category once for all downstream fallback functions
    _category = resolve_novel_category(project.genre, project.sub_genre)
    _category_key: str | None = _category.key if _category else None

    # Store category_key in project metadata for downstream reuse
    if _category_key and isinstance(project.metadata_json, dict):
        project.metadata_json["category_key"] = _category_key

    # ── Batch 2: Reference-style material injection ───────────────────────
    if settings.pipeline.enable_reference_style_generation:
        try:
            from bestseller.services.material_reference import (
                render_material_reference_block,
            )

            _mat_ref_block_f = await render_material_reference_block(session, project.id)
            if _mat_ref_block_f and isinstance(project.metadata_json, dict):
                project.metadata_json["material_reference_block"] = _mat_ref_block_f
        except Exception:
            logger.exception(
                "generate_foundation_plan: material reference block failed — continuing without"
            )

    _stash_distilled_strategy_card(
        project,
        category_key=_category_key,
        settings=settings,
    )
    _stash_distilled_design_reference_blocks(
        project,
        category_key=_category_key,
        settings=settings,
    )
    hook_spec: Any | None = None

    try:
        hook_gate_payload: dict[str, Any] | None = None
        if getattr(settings.hook_engine, "enabled", True):
            current_step_name = "hook_strength_gate"
            workflow_run.current_step = current_step_name
            hook_spec, hook_gate_payload = await _run_hook_strength_gate(
                settings,
                project=project,
                premise=premise,
            )
            if hook_gate_payload is not None:
                await create_workflow_step_run(
                    session,
                    workflow_run_id=workflow_run.id,
                    step_name=current_step_name,
                    step_order=step_order,
                    status=WorkflowStatus.COMPLETED,
                    output_ref=hook_gate_payload,
                )
                step_order += 1
            current_step_name = "store_premise"
            workflow_run.current_step = current_step_name

        # ── Premise ──
        premise_artifact = await import_planning_artifact(
            session,
            project_slug,
            PlanningArtifactCreate(
                artifact_type=ArtifactType.PREMISE, content={"premise": premise}
            ),
        )
        artifact_records.append(
            PlanningArtifactRecord(
                artifact_type=ArtifactType.PREMISE,
                artifact_id=premise_artifact.id,
                version_no=premise_artifact.version_no,
            )
        )
        await create_workflow_step_run(
            session,
            workflow_run_id=workflow_run.id,
            step_name=current_step_name,
            step_order=step_order,
            status=WorkflowStatus.COMPLETED,
            output_ref={"artifact_id": str(premise_artifact.id)},
        )
        step_order += 1

        # ── Character names ──
        character_name_pool = await _generate_character_names(
            session,
            settings,
            genre=project.genre,
            sub_genre=project.sub_genre or "",
            language=project.language,
            premise=premise,
            book_spec={},
            workflow_run_id=workflow_run.id,
            project_id=project.id,
        )
        llm_protagonist_name = (
            _mapping(character_name_pool.get("protagonist")).get("name")
            or _genre_name_pool(
                project.genre,
                language=project.language,
                seed_text=_project_name_seed(project, premise),
            )["protagonist"]["name"]
        )
        await create_workflow_step_run(
            session,
            workflow_run_id=workflow_run.id,
            step_name="generate_character_names",
            step_order=step_order,
            status=WorkflowStatus.COMPLETED,
            output_ref={
                "protagonist_name": llm_protagonist_name,
                "ally_count": len(_mapping_list(character_name_pool.get("allies"))),
                "antagonist_count": len(_mapping_list(character_name_pool.get("antagonists"))),
            },
        )
        step_order += 1

        # ── BookSpec ──
        book_spec_fallback = _fallback_book_spec(project, premise, category_key=_category_key)
        if isinstance(book_spec_fallback.get("protagonist"), dict):
            book_spec_fallback["protagonist"]["name"] = llm_protagonist_name
        current_step_name = "generate_book_spec"
        workflow_run.current_step = current_step_name
        book_system, book_user = _book_spec_prompts(project, premise, book_spec_fallback)
        book_user = attach_planner_methodology(
            book_user,
            stage=MethodologyStage.OUTLINE_BOOK,
            project_ctx=project,
            token_budget=800,
        )
        book_spec_payload, llm_run_id = await _generate_structured_artifact(
            session,
            settings,
            project=project,
            logical_name="book_spec",
            system_prompt=book_system,
            user_prompt=book_user,
            fallback_payload=book_spec_fallback,
            workflow_run_id=workflow_run.id,
        )
        if llm_run_id is not None:
            llm_run_ids.append(llm_run_id)
        book_spec_payload = _ensure_book_spec_bible_fields(
            project,
            premise,
            book_spec_payload,
        )

        # ── Narrative-lines gate (see long-form generator for rationale) ──
        (
            repaired_book_spec,
            narrative_lines_repair_llm_run_id,
        ) = await _repair_book_spec_narrative_lines_if_needed(
            session=session,
            settings=settings,
            project=project,
            premise=premise,
            book_spec_payload=book_spec_payload,
            workflow_run_id=workflow_run.id,
        )
        if narrative_lines_repair_llm_run_id is not None:
            llm_run_ids.append(narrative_lines_repair_llm_run_id)
            book_spec_payload = repaired_book_spec
        if hook_spec is not None:
            from bestseller.services.hook_propagation import apply_hook_to_book_spec

            book_spec_payload = apply_hook_to_book_spec(
                book_spec_payload,
                hook_spec,
                premise_context={
                    "premise": premise,
                    "title": getattr(project, "title", None),
                    "genre": getattr(project, "genre", None),
                    "sub_genre": getattr(project, "sub_genre", None),
                    "tags": _mapping(project.metadata_json or {}).get("tags"),
                },
            )

        book_artifact = await import_planning_artifact(
            session,
            project_slug,
            PlanningArtifactCreate(artifact_type=ArtifactType.BOOK_SPEC, content=book_spec_payload),
        )
        artifact_records.append(
            PlanningArtifactRecord(
                artifact_type=ArtifactType.BOOK_SPEC,
                artifact_id=book_artifact.id,
                version_no=book_artifact.version_no,
            )
        )
        await create_workflow_step_run(
            session,
            workflow_run_id=workflow_run.id,
            step_name=current_step_name,
            step_order=step_order,
            status=WorkflowStatus.COMPLETED,
            output_ref={"artifact_id": str(book_artifact.id)},
        )
        step_order += 1

        # ── WorldSpec ──
        world_spec_fallback = _fallback_world_spec(
            project, premise, book_spec_payload, category_key=_category_key
        )
        current_step_name = "generate_world_spec"
        workflow_run.current_step = current_step_name
        world_system, world_user = _world_spec_prompts(project, premise, book_spec_payload)
        world_user = attach_planner_methodology(
            world_user,
            stage=MethodologyStage.OUTLINE_BOOK,
            project_ctx=project,
            token_budget=800,
        )
        world_spec_payload, llm_run_id = await _generate_structured_artifact(
            session,
            settings,
            project=project,
            logical_name="world_spec",
            system_prompt=world_system,
            user_prompt=world_user,
            fallback_payload=world_spec_fallback,
            workflow_run_id=workflow_run.id,
            validator=parse_world_spec_input,
        )
        if llm_run_id is not None:
            llm_run_ids.append(llm_run_id)

        # ── World richness gate (see long-form generator for rationale) ──
        (
            repaired_world_spec,
            world_richness_repair_llm_run_id,
        ) = await _repair_world_spec_richness_if_needed(
            session=session,
            settings=settings,
            project=project,
            premise=premise,
            book_spec_payload=book_spec_payload,
            world_spec_payload=world_spec_payload,
            workflow_run_id=workflow_run.id,
        )
        if world_richness_repair_llm_run_id is not None:
            llm_run_ids.append(world_richness_repair_llm_run_id)
            world_spec_payload = repaired_world_spec
        if hook_spec is not None:
            from bestseller.services.hook_propagation import apply_hook_to_world_spec

            world_spec_payload = apply_hook_to_world_spec(world_spec_payload, hook_spec)

        world_artifact = await import_planning_artifact(
            session,
            project_slug,
            PlanningArtifactCreate(
                artifact_type=ArtifactType.WORLD_SPEC, content=world_spec_payload
            ),
        )
        artifact_records.append(
            PlanningArtifactRecord(
                artifact_type=ArtifactType.WORLD_SPEC,
                artifact_id=world_artifact.id,
                version_no=world_artifact.version_no,
            )
        )
        await create_workflow_step_run(
            session,
            workflow_run_id=workflow_run.id,
            step_name=current_step_name,
            step_order=step_order,
            status=WorkflowStatus.COMPLETED,
            output_ref={"artifact_id": str(world_artifact.id)},
        )
        step_order += 1

        # ── CastSpec ──
        cast_spec_fallback = _fallback_cast_spec(
            project,
            premise,
            book_spec_payload,
            world_spec_payload,
            category_key=_category_key,
            character_name_pool=character_name_pool,
        )
        current_step_name = "generate_cast_spec"
        workflow_run.current_step = current_step_name
        cast_system, cast_user = _cast_spec_prompts(
            project,
            book_spec_payload,
            world_spec_payload,
            locked_names=_string_list(character_name_pool.get("locked_names")),
        )
        cast_user = attach_planner_methodology(
            cast_user,
            stage=MethodologyStage.OUTLINE_BOOK,
            project_ctx=project,
            token_budget=800,
        )
        cast_spec_payload, llm_run_id = await _generate_structured_artifact(
            session,
            settings,
            project=project,
            logical_name="cast_spec",
            system_prompt=cast_system,
            user_prompt=cast_user,
            fallback_payload=cast_spec_fallback,
            workflow_run_id=workflow_run.id,
            validator=parse_cast_spec_input,
        )
        cast_spec_payload = _repair_cast_identity_locks_for_planner(project, cast_spec_payload)
        cast_spec_payload = _repair_protagonist_name_drift_for_planner(
            project,
            cast_spec_payload,
            protagonist_name=_protagonist_name_from_payload(book_spec_payload)
            or llm_protagonist_name,
            artifact_type=ArtifactType.CAST_SPEC.value,
        )
        cast_spec_payload = _repair_rule_survival_goal_drift_for_planner(
            project,
            cast_spec_payload,
            premise=premise,
            artifact_type=ArtifactType.CAST_SPEC.value,
        )
        cast_spec_payload = _enforce_premise_locked_cast(
            cast_spec_payload, premise, language=_planner_language(project)
        )
        if llm_run_id is not None:
            llm_run_ids.append(llm_run_id)

        (
            repaired_cast_spec,
            personhood_repair_llm_run_id,
        ) = await _repair_cast_personhood_if_needed(
            session=session,
            settings=settings,
            project=project,
            book_spec_payload=book_spec_payload,
            world_spec_payload=world_spec_payload,
            cast_spec_payload=cast_spec_payload,
            workflow_run_id=workflow_run.id,
        )
        if personhood_repair_llm_run_id is not None:
            llm_run_ids.append(personhood_repair_llm_run_id)
            cast_spec_payload = _repair_cast_identity_locks_for_planner(
                project, repaired_cast_spec
            )
            cast_spec_payload = _repair_protagonist_name_drift_for_planner(
                project,
                cast_spec_payload,
                protagonist_name=_protagonist_name_from_payload(book_spec_payload)
                or llm_protagonist_name,
                artifact_type=ArtifactType.CAST_SPEC.value,
            )
            cast_spec_payload = _repair_rule_survival_goal_drift_for_planner(
                project,
                cast_spec_payload,
                premise=premise,
                artifact_type=ArtifactType.CAST_SPEC.value,
            )

        # ── Foundation richness gate (see long-form generator for rationale) ──
        _foundation_hierarchy = compute_linear_hierarchy(max(project.target_chapters, 1))
        _foundation_volume_count = int(_foundation_hierarchy.get("volume_count") or 1)
        if _foundation_volume_count > 1:
            (
                repaired_cast_spec,
                foundation_repair_llm_run_id,
            ) = await _repair_cast_foundation_if_needed(
                session=session,
                settings=settings,
                project=project,
                book_spec_payload=book_spec_payload,
                world_spec_payload=world_spec_payload,
                cast_spec_payload=cast_spec_payload,
                volume_count=_foundation_volume_count,
                workflow_run_id=workflow_run.id,
            )
            if foundation_repair_llm_run_id is not None:
                llm_run_ids.append(foundation_repair_llm_run_id)
                cast_spec_payload = _repair_cast_identity_locks_for_planner(
                    project, repaired_cast_spec
                )
                cast_spec_payload = _repair_protagonist_name_drift_for_planner(
                    project,
                    cast_spec_payload,
                    protagonist_name=_protagonist_name_from_payload(book_spec_payload)
                    or llm_protagonist_name,
                    artifact_type=ArtifactType.CAST_SPEC.value,
                )
                cast_spec_payload = _repair_rule_survival_goal_drift_for_planner(
                    project,
                    cast_spec_payload,
                    premise=premise,
                    artifact_type=ArtifactType.CAST_SPEC.value,
                )

            # ── Antagonist lifecycle gate ──
            (
                repaired_cast_spec,
                lifecycle_repair_llm_run_id,
            ) = await _repair_cast_spec_antagonist_lifecycle_if_needed(
                session=session,
                settings=settings,
                project=project,
                book_spec_payload=book_spec_payload,
                world_spec_payload=world_spec_payload,
                cast_spec_payload=cast_spec_payload,
                volume_count=_foundation_volume_count,
                workflow_run_id=workflow_run.id,
            )
            if lifecycle_repair_llm_run_id is not None:
                llm_run_ids.append(lifecycle_repair_llm_run_id)
                cast_spec_payload = _repair_cast_identity_locks_for_planner(
                    project, repaired_cast_spec
                )
                cast_spec_payload = _repair_protagonist_name_drift_for_planner(
                    project,
                    cast_spec_payload,
                    protagonist_name=_protagonist_name_from_payload(book_spec_payload)
                    or llm_protagonist_name,
                    artifact_type=ArtifactType.CAST_SPEC.value,
                )
                cast_spec_payload = _repair_rule_survival_goal_drift_for_planner(
                    project,
                    cast_spec_payload,
                    premise=premise,
                    artifact_type=ArtifactType.CAST_SPEC.value,
                )

            # ── Relationship scaling gate ──
            (
                repaired_cast_spec,
                relationship_repair_llm_run_id,
            ) = await _repair_cast_spec_relationship_scaling_if_needed(
                session=session,
                settings=settings,
                project=project,
                book_spec_payload=book_spec_payload,
                world_spec_payload=world_spec_payload,
                cast_spec_payload=cast_spec_payload,
                volume_count=_foundation_volume_count,
                workflow_run_id=workflow_run.id,
            )
            if relationship_repair_llm_run_id is not None:
                llm_run_ids.append(relationship_repair_llm_run_id)
                cast_spec_payload = _repair_cast_identity_locks_for_planner(
                    project, repaired_cast_spec
                )
                cast_spec_payload = _repair_protagonist_name_drift_for_planner(
                    project,
                    cast_spec_payload,
                    protagonist_name=_protagonist_name_from_payload(book_spec_payload)
                    or llm_protagonist_name,
                    artifact_type=ArtifactType.CAST_SPEC.value,
                )
                cast_spec_payload = _repair_rule_survival_goal_drift_for_planner(
                    project,
                    cast_spec_payload,
                    premise=premise,
                    artifact_type=ArtifactType.CAST_SPEC.value,
                )

        # Final premise-locked-cast enforcement, AFTER every LLM repair gate
        # (personhood / foundation richness / antagonist lifecycle / relationship
        # scaling). Those gates regenerate the roster and can re-drop the
        # premise's named key cast (蚀漏砚: 裴萤/关铎/宋拾/白杪), which the outline
        # then references and the identity lock strips to false solo chains. The
        # earlier enforcement at cast-generation time does not survive those
        # regenerations, so it must run once more as the last step before store.
        cast_spec_payload = _enforce_premise_locked_cast(
            cast_spec_payload, premise, language=_planner_language(project)
        )

        cast_artifact = await import_planning_artifact(
            session,
            project_slug,
            PlanningArtifactCreate(artifact_type=ArtifactType.CAST_SPEC, content=cast_spec_payload),
        )
        artifact_records.append(
            PlanningArtifactRecord(
                artifact_type=ArtifactType.CAST_SPEC,
                artifact_id=cast_artifact.id,
                version_no=cast_artifact.version_no,
            )
        )
        await create_workflow_step_run(
            session,
            workflow_run_id=workflow_run.id,
            step_name=current_step_name,
            step_order=step_order,
            status=WorkflowStatus.COMPLETED,
            output_ref={"artifact_id": str(cast_artifact.id)},
        )
        step_order += 1

        current_step_name = "generate_public_emotion_kernel"
        workflow_run.current_step = current_step_name
        public_emotion_payload = await _generate_public_emotion_kernel_artifact(
            session,
            project=project,
            project_slug=project_slug,
            premise=premise,
            book_spec_payload=book_spec_payload,
            workflow_run_id=workflow_run.id,
            step_order=step_order,
            artifact_records=artifact_records,
        )
        step_order += 1

        current_step_name = "generate_compliance_boundary_kernel"
        workflow_run.current_step = current_step_name
        compliance_boundary_payload = await _generate_compliance_boundary_kernel_artifact(
            session,
            project=project,
            project_slug=project_slug,
            workflow_run_id=workflow_run.id,
            step_order=step_order,
            artifact_records=artifact_records,
        )
        step_order += 1

        emotion_driven_payload: dict[str, Any] | None = None
        if settings.pipeline.enable_emotion_driven_kernel:
            current_step_name = "generate_emotion_driven_kernel"
            workflow_run.current_step = current_step_name
            emotion_driven_payload = await _generate_emotion_driven_kernel(
                session,
                settings,
                project=project,
                project_slug=project_slug,
                premise=premise,
                book_spec_payload=book_spec_payload,
                world_spec_payload=world_spec_payload,
                cast_spec_payload=cast_spec_payload,
                category_key=_category_key,
                workflow_run_id=workflow_run.id,
                step_order=step_order,
                llm_run_ids=llm_run_ids,
                artifact_records=artifact_records,
                story_design_kernel=None,
            )
            step_order += 1

        current_step_name = "generate_entry_system_kernel"
        workflow_run.current_step = current_step_name
        await _generate_entry_system_kernel_artifacts(
            session,
            project=project,
            project_slug=project_slug,
            story_design_kernel=None,
            workflow_run_id=workflow_run.id,
            step_order=step_order,
            artifact_records=artifact_records,
        )
        step_order += 1

        # ── VolumePlan ──
        volume_plan_fallback = _fallback_volume_plan(
            project,
            book_spec_payload,
            cast_spec_payload,
            world_spec_payload,
            category_key=_category_key,
        )
        current_step_name = "generate_volume_plan"
        workflow_run.current_step = current_step_name
        vp_system, vp_user = _volume_plan_prompts(
            project, book_spec_payload, world_spec_payload, cast_spec_payload
        )
        volume_plan_payload, llm_run_id = await _generate_structured_artifact(
            session,
            settings,
            project=project,
            logical_name="volume_plan",
            system_prompt=vp_system,
            user_prompt=vp_user,
            fallback_payload=volume_plan_fallback,
            workflow_run_id=workflow_run.id,
            validator=_volume_plan_validator_for(project),
        )
        if llm_run_id is not None:
            llm_run_ids.append(llm_run_id)

        # ── Foreshadowing scaling gate (see long-form path for rationale) ──
        (
            volume_plan_payload,
            foresh_repair_llm_run_id,
        ) = await _repair_volume_plan_foreshadowing_if_needed(
            session=session,
            settings=settings,
            project=project,
            book_spec_payload=book_spec_payload,
            world_spec_payload=world_spec_payload,
            cast_spec_payload=cast_spec_payload,
            act_plan_payload=None,
            volume_plan_payload=volume_plan_payload,
            workflow_run_id=workflow_run.id,
        )
        if foresh_repair_llm_run_id is not None:
            llm_run_ids.append(foresh_repair_llm_run_id)
        if hook_spec is not None:
            from bestseller.services.hook_propagation import apply_hook_to_volume_plan

            volume_plan_payload = apply_hook_to_volume_plan(volume_plan_payload, hook_spec)

        volume_artifact = await import_planning_artifact(
            session,
            project_slug,
            PlanningArtifactCreate(
                artifact_type=ArtifactType.VOLUME_PLAN, content=volume_plan_payload
            ),
        )
        artifact_records.append(
            PlanningArtifactRecord(
                artifact_type=ArtifactType.VOLUME_PLAN,
                artifact_id=volume_artifact.id,
                version_no=volume_artifact.version_no,
            )
        )
        await create_workflow_step_run(
            session,
            workflow_run_id=workflow_run.id,
            step_name=current_step_name,
            step_order=step_order,
            status=WorkflowStatus.COMPLETED,
            output_ref={"artifact_id": str(volume_artifact.id)},
        )
        step_order += 1

        qimao_opening_contract = persist_qimao_opening_contract(
            project,
            premise=premise,
            book_spec=book_spec_payload,
            cast_spec=cast_spec_payload,
            volume_plan=volume_plan_payload,
        )
        if qimao_opening_contract:
            workflow_run.metadata_json = {
                **(workflow_run.metadata_json or {}),
                "opening_quality_contract": qimao_opening_contract,
                "qimao_opening_contract": qimao_opening_contract,
            }

        if settings.pipeline.enable_prewrite_readiness_gate:
            current_step_name = "prewrite_readiness_gate"
            workflow_run.current_step = current_step_name
            await _run_prewrite_readiness_gate(
                session,
                settings,
                project=project,
                project_slug=project_slug,
                book_spec_payload=book_spec_payload,
                world_spec_payload=world_spec_payload,
                cast_spec_payload=cast_spec_payload,
                volume_plan_payload=volume_plan_payload,
                emotion_driven_kernel=emotion_driven_payload,
                public_emotion_kernel=public_emotion_payload,
                compliance_boundary_kernel=compliance_boundary_payload,
                workflow_run_id=workflow_run.id,
                step_order=step_order,
                artifact_records=artifact_records,
            )
            step_order += 1

        # ── Promotional brief: title + tags + protagonist + blurb ──
        current_step_name = "generate_promotional_brief"
        workflow_run.current_step = current_step_name
        step_order += 1
        _norm_vp = (
            volume_plan_payload
            if isinstance(volume_plan_payload, list)
            else _mapping_list(volume_plan_payload.get("volumes"))
        )
        await _generate_promotional_brief(
            session,
            settings,
            project=project,
            book_spec=book_spec_payload,
            cast_spec=cast_spec_payload,
            volume_plan=_norm_vp,
            workflow_run_id=workflow_run.id,
            step_order=step_order,
            llm_run_ids=llm_run_ids,
            artifact_records=artifact_records,
        )

        # ── Done — no outline step; volumes handle their own outlines ──
        workflow_run.current_step = "completed"
        workflow_run.status = WorkflowStatus.COMPLETED.value
        workflow_run.metadata_json = {
            **workflow_run.metadata_json,
            "artifact_ids": {r.artifact_type.value: str(r.artifact_id) for r in artifact_records},
            "llm_run_ids": [str(i) for i in llm_run_ids],
        }
        await session.flush()

        volume_count = (
            len(volume_plan_payload)
            if isinstance(volume_plan_payload, list)
            else len(volume_plan_payload.get("volumes", []))
        )
        return NovelPlanningResult(
            workflow_run_id=workflow_run.id,
            project_id=project.id,
            premise=premise,
            artifacts=artifact_records,
            volume_count=volume_count,
            chapter_count=0,
            llm_run_ids=llm_run_ids,
        )
    except Exception as exc:
        workflow_run.status = WorkflowStatus.FAILED.value
        workflow_run.current_step = current_step_name
        workflow_run.error_message = str(exc)
        await _record_planner_failure_step_once(
            session,
            workflow_run_id=workflow_run.id,
            step_name=current_step_name,
            step_order=step_order,
            error_message=str(exc),
        )
        await session.flush()
        try:
            await session.commit()
        except Exception:
            logger.exception(
                "Failed to persist failed foundation workflow state for project '%s'",
                project_slug,
            )
        raise


async def generate_volume_plan(
    session: AsyncSession,
    settings: AppSettings,
    project_slug: str,
    volume_number: int,
    *,
    book_spec: dict[str, Any],
    world_spec: dict[str, Any],
    cast_spec: dict[str, Any],
    volume_plan: list[dict[str, Any]],
    prior_feedback_summary: str | None = None,
    prior_world_snapshot: str | None = None,
    extra_constraints: list[str] | None = None,
    requested_by: str = "system",
    progress: PlanningProgressCallback | None = None,
) -> VolumePlanningResult:
    """Phase B of progressive planning: plan a single volume.

    Steps: cast expansion → world disclosure → volume outline.
    Uses prior volume's writing feedback to evolve characters and world.

    ``extra_constraints`` — caller-supplied hard constraints injected verbatim
    at the end of the cast-expansion and volume-outline prompts (before the
    closing instruction line). Also merged with any directives stored in
    ``project.metadata_json["mid_flight_directives"]`` so both in-flight rescue
    operations and ad-hoc per-call overrides apply together.
    """
    project = await get_project_by_slug(session, project_slug)
    if project is None:
        raise ValueError(f"Project '{project_slug}' was not found.")

    # Resolve category once for downstream fallback functions
    _category = resolve_novel_category(project.genre, project.sub_genre)
    _category_key: str | None = _category.key if _category else None

    volume_plan = _normalize_volume_plan_payload(volume_plan)

    # Find this volume's entry
    vol_entry: dict[str, Any] | None = None
    for v in volume_plan:
        if int(v.get("volume_number", 0)) == volume_number:
            vol_entry = v
            break
    if vol_entry is None:
        raise ValueError(f"Volume {volume_number} not found in volume plan")

    # Merge caller-supplied constraints with project-level mid-flight directives.
    # Stored directives apply to ALL future volumes (rescue operation), while
    # extra_constraints are per-call overrides. Both are appended to cast-expansion
    # and volume-outline prompts so the LLM treats them as hard constraints.
    _stored_directives: list[str] = []
    if isinstance(project.metadata_json, dict):
        _raw = project.metadata_json.get("mid_flight_directives") or []
        if isinstance(_raw, list):
            _stored_directives = [str(d) for d in _raw if d]
        _prewrite_raw = project.metadata_json.get("prewrite_repair_directives") or []
        if isinstance(_prewrite_raw, list):
            for directive in _prewrite_raw:
                text = str(directive).strip()
                if text and text not in _stored_directives:
                    _stored_directives.append(text)
    _all_constraints: list[str] = list(_stored_directives) + list(extra_constraints or [])

    workflow_run = await create_workflow_run(
        session,
        project_id=project.id,
        workflow_type=WORKFLOW_TYPE_VOLUME_PLAN,
        status=WorkflowStatus.RUNNING,
        scope_type="volume",
        scope_id=project.id,
        requested_by=requested_by,
        current_step="volume_cast_expansion",
        metadata={"project_slug": project.slug, "volume_number": volume_number},
    )
    step_order = 1
    current_step_name = "volume_cast_expansion" if volume_number > 1 else "volume_world_disclosure"
    llm_run_ids: list[UUID] = []
    artifact_records: list[PlanningArtifactRecord] = []
    new_characters_introduced = 0

    try:
        effective_cast_spec = copy.deepcopy(_mapping(cast_spec))
        effective_world_spec = copy.deepcopy(_mapping(world_spec))
        # ── Cast Expansion (skip for volume 1 — initial cast is from foundation) ──
        if volume_number > 1:
            cast_exp_system, cast_exp_user = _volume_cast_expansion_prompts(
                project,
                book_spec,
                world_spec,
                cast_spec,
                vol_entry,
                prior_feedback_summary=prior_feedback_summary,
                extra_constraints=_all_constraints or None,
            )
            cast_exp_user = attach_planner_methodology(
                cast_exp_user,
                stage=MethodologyStage.OUTLINE_BOOK,
                project_ctx=project,
                token_budget=700,
            )
            cast_exp_payload, llm_run_id = await _generate_structured_artifact(
                session,
                settings,
                project=project,
                logical_name=f"volume_{volume_number}_cast_expansion",
                system_prompt=cast_exp_system,
                user_prompt=cast_exp_user,
                fallback_payload={
                    "new_characters": [],
                    "character_evolutions": [],
                    "relationship_updates": [],
                },
                workflow_run_id=workflow_run.id,
            )
            if llm_run_id is not None:
                llm_run_ids.append(llm_run_id)
            new_characters_introduced = len(cast_exp_payload.get("new_characters", []))
            cast_exp_artifact = await import_planning_artifact(
                session,
                project_slug,
                PlanningArtifactCreate(
                    artifact_type=ArtifactType.VOLUME_CAST_EXPANSION, content=cast_exp_payload
                ),
            )
            artifact_records.append(
                PlanningArtifactRecord(
                    artifact_type=ArtifactType.VOLUME_CAST_EXPANSION,
                    artifact_id=cast_exp_artifact.id,
                    version_no=cast_exp_artifact.version_no,
                )
            )
            effective_cast_spec = _merge_volume_cast_expansion_into_cast_spec(
                effective_cast_spec,
                cast_exp_payload,
            )
            # Validate and normalize the merged CastSpec immediately so
            # malformed role updates fail close to the merge point instead of
            # surfacing later during story-bible materialization.
            effective_cast_spec = parse_cast_spec_input(effective_cast_spec).model_dump(mode="json")
            repaired_cast_spec, repair_llm_run_id = await _repair_cast_personhood_if_needed(
                session=session,
                settings=settings,
                project=project,
                book_spec_payload=book_spec,
                world_spec_payload=effective_world_spec,
                cast_spec_payload=effective_cast_spec,
                workflow_run_id=workflow_run.id,
            )
            if repair_llm_run_id is not None:
                llm_run_ids.append(repair_llm_run_id)
            if repaired_cast_spec != effective_cast_spec:
                effective_cast_spec = parse_cast_spec_input(repaired_cast_spec).model_dump(
                    mode="json"
                )
                workflow_run.metadata_json = {
                    **(workflow_run.metadata_json or {}),
                    "volume_cast_personhood_auto_repaired": True,
                    "volume_cast_personhood_auto_repaired_for": volume_number,
                }
            if effective_cast_spec and effective_cast_spec != _mapping(cast_spec):
                merged_cast_artifact = await import_planning_artifact(
                    session,
                    project_slug,
                    PlanningArtifactCreate(
                        artifact_type=ArtifactType.CAST_SPEC, content=effective_cast_spec
                    ),
                )
                artifact_records.append(
                    PlanningArtifactRecord(
                        artifact_type=ArtifactType.CAST_SPEC,
                        artifact_id=merged_cast_artifact.id,
                        version_no=merged_cast_artifact.version_no,
                    )
                )
            await create_workflow_step_run(
                session,
                workflow_run_id=workflow_run.id,
                step_name=current_step_name,
                step_order=step_order,
                status=WorkflowStatus.COMPLETED,
                output_ref={"artifact_id": str(cast_exp_artifact.id)},
            )
            step_order += 1

        # ── World Disclosure ──
        current_step_name = "volume_world_disclosure"
        workflow_run.current_step = current_step_name
        world_disc_system, world_disc_user = _volume_world_disclosure_prompts(
            project,
            world_spec,
            vol_entry,
            prior_world_snapshot=prior_world_snapshot,
        )
        world_disc_user = attach_planner_methodology(
            world_disc_user,
            stage=MethodologyStage.OUTLINE_BOOK,
            project_ctx=project,
            token_budget=700,
        )
        world_disc_payload, llm_run_id = await _generate_structured_artifact(
            session,
            settings,
            project=project,
            logical_name=f"volume_{volume_number}_world_disclosure",
            system_prompt=world_disc_system,
            user_prompt=world_disc_user,
            fallback_payload={
                "new_locations": [],
                "new_rules_revealed": [],
                "faction_movements": [],
                "frontier_summary": "",
            },
            workflow_run_id=workflow_run.id,
        )
        if llm_run_id is not None:
            llm_run_ids.append(llm_run_id)
        world_disc_artifact = await import_planning_artifact(
            session,
            project_slug,
            PlanningArtifactCreate(
                artifact_type=ArtifactType.VOLUME_WORLD_DISCLOSURE, content=world_disc_payload
            ),
        )
        artifact_records.append(
            PlanningArtifactRecord(
                artifact_type=ArtifactType.VOLUME_WORLD_DISCLOSURE,
                artifact_id=world_disc_artifact.id,
                version_no=world_disc_artifact.version_no,
            )
        )
        effective_world_spec = _merge_volume_world_disclosure_into_world_spec(
            effective_world_spec,
            world_disc_payload,
            volume_number=volume_number,
        )
        if effective_world_spec and effective_world_spec != _mapping(world_spec):
            merged_world_artifact = await import_planning_artifact(
                session,
                project_slug,
                PlanningArtifactCreate(
                    artifact_type=ArtifactType.WORLD_SPEC, content=effective_world_spec
                ),
            )
            artifact_records.append(
                PlanningArtifactRecord(
                    artifact_type=ArtifactType.WORLD_SPEC,
                    artifact_id=merged_world_artifact.id,
                    version_no=merged_world_artifact.version_no,
                )
            )
        await create_workflow_step_run(
            session,
            workflow_run_id=workflow_run.id,
            step_name=current_step_name,
            step_order=step_order,
            status=WorkflowStatus.COMPLETED,
            output_ref={"artifact_id": str(world_disc_artifact.id)},
        )
        step_order += 1

        _persist_character_drama_map(project, effective_cast_spec)

        if settings.pipeline.enable_prewrite_readiness_gate:
            current_step_name = "prewrite_readiness_gate"
            workflow_run.current_step = current_step_name
            await _run_prewrite_readiness_gate(
                session,
                settings,
                project=project,
                project_slug=project_slug,
                book_spec_payload=_mapping(book_spec),
                world_spec_payload=effective_world_spec,
                cast_spec_payload=effective_cast_spec,
                volume_plan_payload=volume_plan,
                workflow_run_id=workflow_run.id,
                step_order=step_order,
                artifact_records=artifact_records,
            )
            step_order += 1

        # ── Volume Outline ──
        current_step_name = f"generate_volume_{volume_number}_outline"
        workflow_run.current_step = current_step_name

        # Build per-volume fallback using the authoritative chapter-number
        # frontier from the ``chapters`` table. The offset comes exclusively
        # from DB evidence (max chapter_number in prior volumes, then
        # project-wide fallback) — never from VOLUME_PLAN targets, since those
        # are exactly what drifted during the 200-chapter gap on
        # xianxia-upgrade-1776137730 (drifted targets pushed vol 4 to ch 351
        # after only 150 had been written).
        chapter_number_offset = await _next_chapter_number_for_volume(
            session,
            project.id,
            volume_number,
        )
        # Restrict the fallback to the single volume being replanned — the
        # fallback numbers chapters globally across whatever volume_plan it
        # receives, so passing only the target volume entry keeps numbering
        # anchored at ``chapter_number_offset``.
        single_volume_plan = [
            v
            for v in _mapping_list(volume_plan)
            if int(v.get("volume_number", 0) or 0) == volume_number
        ]
        full_fallback = _fallback_chapter_outline_batch(
            project,
            book_spec,
            effective_cast_spec,
            single_volume_plan,
            category_key=_category_key,
            chapter_number_offset=chapter_number_offset,
        )
        vol_fallback_chapters = [
            ch
            for ch in full_fallback.get("chapters", [])
            if ch.get("volume_number") == volume_number
        ]
        vol_fallback = {
            "batch_name": f"volume-{volume_number}-outline",
            "chapters": vol_fallback_chapters,
        }

        # Build the revealed-facts/beats ledger so the LLM sees what
        # has already been revealed across prior volumes and can avoid
        # re-revealing or replaying the same beats.
        _ledger_block = await _build_revealed_ledger_block(
            session, project.id, language=_planner_language(project)
        )

        _deceased_constraints = _build_deceased_character_constraints(
            effective_cast_spec, vol_entry, language=project.language or "zh-CN"
        )
        _all_constraints = (_all_constraints or []) + _deceased_constraints
        (
            vol_outline_payload,
            outline_llm_run_ids,
            outline_repair_history,
        ) = await _generate_volume_outline_batched(
            session,
            settings,
            project=project,
            workflow_run_id=workflow_run.id,
            logical_name=f"volume_{volume_number}_chapter_outline",
            book_spec=book_spec,
            cast_spec=effective_cast_spec,
            volume_plan=_mapping_list(volume_plan),
            volume_entry=vol_entry,
            fallback_payload=vol_fallback,
            volume_number=volume_number,
            expected_count=int(
                vol_entry.get("chapter_count_target", len(vol_fallback_chapters) or 1)
            ),
            chapter_number_offset=chapter_number_offset,
            revealed_ledger_block=_ledger_block,
            base_constraints=_all_constraints or [],
            progress=progress,
        )
        llm_run_ids.extend(outline_llm_run_ids)
        if outline_repair_history:
            workflow_run.metadata_json = {
                **(workflow_run.metadata_json or {}),
                "chapter_outline_repair_history": [
                    *(
                        (workflow_run.metadata_json or {}).get("chapter_outline_repair_history")
                        or []
                    ),
                    {
                        "volume_number": volume_number,
                        "logical_name": f"volume_{volume_number}_chapter_outline",
                        "attempts": outline_repair_history,
                    },
                ],
            }

        vol_chapters = (
            vol_outline_payload.get("chapters", []) if isinstance(vol_outline_payload, dict) else []
        )

        vol_outline_artifact = await import_planning_artifact(
            session,
            project_slug,
            PlanningArtifactCreate(
                artifact_type=ArtifactType.VOLUME_CHAPTER_OUTLINE,
                content=vol_outline_payload,
                source_run_id=outline_llm_run_ids[-1] if outline_llm_run_ids else None,
            ),
        )
        artifact_records.append(
            PlanningArtifactRecord(
                artifact_type=ArtifactType.VOLUME_CHAPTER_OUTLINE,
                artifact_id=vol_outline_artifact.id,
                version_no=vol_outline_artifact.version_no,
            )
        )
        await create_workflow_step_run(
            session,
            workflow_run_id=workflow_run.id,
            step_name=current_step_name,
            step_order=step_order,
            status=WorkflowStatus.COMPLETED,
            output_ref={
                "artifact_id": str(vol_outline_artifact.id),
                "llm_run_ids": [str(item) for item in outline_llm_run_ids],
                "reused": bool(_mapping(_mapping(vol_outline_payload).get("_meta")).get("reused")),
                "batch_count": _mapping(_mapping(vol_outline_payload).get("_meta")).get(
                    "batch_count"
                ),
            },
        )
        step_order += 1

        # ── Done ──
        workflow_run.current_step = "completed"
        workflow_run.status = WorkflowStatus.COMPLETED.value
        workflow_run.metadata_json = {
            **workflow_run.metadata_json,
            "artifact_ids": {r.artifact_type.value: str(r.artifact_id) for r in artifact_records},
            "llm_run_ids": [str(i) for i in llm_run_ids],
        }
        await session.flush()

        return VolumePlanningResult(
            workflow_run_id=workflow_run.id,
            volume_number=volume_number,
            chapter_count=len(vol_chapters),
            new_characters_introduced=new_characters_introduced,
            artifacts=artifact_records,
            llm_run_ids=llm_run_ids,
        )
    except Exception as exc:
        workflow_run.status = WorkflowStatus.FAILED.value
        workflow_run.current_step = current_step_name
        workflow_run.error_message = str(exc)
        await _record_planner_failure_step_once(
            session,
            workflow_run_id=workflow_run.id,
            step_name=current_step_name,
            step_order=step_order,
            error_message=str(exc),
        )
        await session.flush()
        try:
            await session.commit()
        except Exception:
            logger.exception(
                "Failed to persist failed volume workflow state for project '%s'",
                project_slug,
            )
        raise
