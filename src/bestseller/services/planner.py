from __future__ import annotations

import copy
import hashlib
import json
import logging
import math
from pathlib import Path
import re
from typing import Any, Callable
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.domain.enums import ArtifactType, ChapterStatus, WorkflowStatus
from bestseller.domain.planning import NovelPlanningResult, PlanningArtifactCreate, PlanningArtifactRecord, VolumePlanningResult
from bestseller.domain.story_bible import (
    is_safe_character_role_label,
    normalize_character_age,
    normalize_character_role_label,
)
from bestseller.infra.db.models import ChapterModel, ProjectModel, VolumeModel
from bestseller.services.llm import LLMCompletionRequest, complete_text
from bestseller.services.planning_context import (
    summarize_book_spec,
    summarize_cast_spec,
    summarize_volume_plan_context,
    summarize_world_spec,
)
from bestseller.services.novel_categories import (
    NovelCategoryResearch,
    get_novel_category,
    resolve_novel_category,
)
from bestseller.services.prompt_packs import (
    render_methodology_block,
    render_prompt_pack_fragment,
    render_prompt_pack_prompt_block,
    resolve_prompt_pack,
)
from bestseller.services.projects import get_project_by_slug, import_planning_artifact
from bestseller.services.story_bible import parse_cast_spec_input, parse_volume_plan_input, parse_world_spec_input
from bestseller.services.writing_profile import (
    is_english_language,
    render_serial_fiction_guardrails,
    render_writing_profile_prompt_block,
    resolve_writing_profile,
)
from bestseller.services.workflows import create_workflow_run, create_workflow_step_run
from bestseller.settings import AppSettings, get_settings


from dataclasses import dataclass

logger = logging.getLogger(__name__)

WORKFLOW_TYPE_GENERATE_NOVEL_PLAN = "generate_novel_plan"


class PlannerFallbackError(RuntimeError):
    """Raised when a planner artifact degrades to fallback content and the
    caller opted in to fail-fast instead of silently using fallback.

    Prevents downstream corruption such as partial chapter outlines
    producing gaps like "missing chapters [151..350]".
    """


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
    project: "ProjectModel",
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
        challenge_evolution_summary_zh=render_category_challenge_evolution_summary(cat, is_en=False) if cat else "",
        challenge_evolution_summary_en=render_category_challenge_evolution_summary(cat, is_en=True) if cat else "",
        category_context_summary=summarize_category_context(key, language=str(project.language or "zh-CN")),
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
        candidates.append(anchor / "story-factory" / "projects" / slug_underscored / "story_package.json")

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
            "reader_desire_map": data.get("reader_desire_map") if isinstance(data.get("reader_desire_map"), dict) else {},
            "story_bible": data.get("story_bible") if isinstance(data.get("story_bible"), dict) else {},
            "route_graph": data.get("route_graph") if isinstance(data.get("route_graph"), dict) else {},
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
    characters = [item.get("name") for item in _mapping_list(book.get("characters")) if item.get("name")]
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
        if is_en else
        f"\n\n{label}\n请把它视为已有商业化 canon，在不冲突时优先复用其中的人物、读者承诺与里程碑。\n"
    ) + _json_dumps(summary)


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


def _extract_json_payload(text: str) -> Any:
    stripped = text.strip()
    if not stripped:
        raise ValueError("Planner returned empty content.")
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        pass

    for opening, closing in (("{", "}"), ("[", "]")):
        start = stripped.find(opening)
        end = stripped.rfind(closing)
        if start != -1 and end != -1 and end > start:
            try:
                return json.loads(stripped[start : end + 1])
            except json.JSONDecodeError:
                continue

    # Tolerate truncation at max_tokens: drop the incomplete trailing
    # object and close any still-open containers.
    repaired = _repair_truncated_json(stripped)
    if repaired is not None:
        try:
            result = json.loads(repaired)
        except json.JSONDecodeError:
            pass
        else:
            logger.warning(
                "Planner output repaired after truncation (orig=%d bytes, repaired=%d bytes).",
                len(stripped),
                len(repaired),
            )
            return result
    raise ValueError("Planner output does not contain valid JSON.")


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
            for fallback_item, generated_item in zip(fallback_payload, generated_payload, strict=False)
        ):
            return [
                _merge_planning_payload(fallback_item, generated_item)
                for fallback_item, generated_item in zip(fallback_payload, generated_payload, strict=False)
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


def _non_empty_string(value: Any, default: str) -> str:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return default


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

    result = await complete_text(
        session,
        settings,
        LLMCompletionRequest(
            logical_role="critic",
            system_prompt=(
                "You are a naming specialist for English-language commercial fiction. "
                "Generate natural, memorable, genre-appropriate names. Output valid JSON only."
                if is_en
                else (
                    "你是一位中文小说命名专家。你精通各种题材的命名风格，能生成自然、"
                    "有记忆点、符合文化语境的角色名字。输出必须是合法 JSON，不要解释。"
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
        ),
    )

    try:
        parsed = _extract_json_payload(result.content)
        if isinstance(parsed, dict) and parsed.get("protagonist", {}).get("name"):
            return parsed
    except (ValueError, KeyError):
        pass

    return _genre_name_pool(
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
        digest = hashlib.sha256(f"{seed_text}|{salt}|{idx}|{item}".encode("utf-8")).hexdigest()
        decorated.append((digest, item))
    decorated.sort(key=lambda pair: pair[0])
    return [item for _, item in decorated]


def _stable_index(seed_text: str, *, salt: str, size: int) -> int:
    if size <= 0:
        return 0
    digest = hashlib.sha256(f"{seed_text}|{salt}".encode("utf-8")).hexdigest()
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
    """Return a plausible placeholder *name* for the given role.

    These are used as emergency fallback when the LLM fails to produce character
    names. They must look like real character names — NOT role descriptions such
    as ``盟友甲`` — so that even in fallback mode the novel reads naturally.
    """
    if is_english_language(language):
        if role == "protagonist":
            return "Alex Reed"
        if role == "ally":
            return ["Sam Blake", "Nora Chen", "Jake Cross"][min(index, 2)]
        if role == "antagonist":
            return "Victor Hale"
        if role == "local_threat":
            return "Marcus Webb"
        if role == "betrayer":
            return "Dominic Pryce"
        return "Jordan"
    if role == "protagonist":
        return "林逸"
    if role == "ally":
        return ["沈远", "陆昭", "秦晗"][min(index, 2)]
    if role == "antagonist":
        return "顾铭"
    if role == "local_threat":
        return "周庆"
    if role == "betrayer":
        return "方域"
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


def _genre_profile(genre: str, *, category_key: str | None = None, language: str | None = None) -> dict[str, Any]:
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
    locations = [r.name_zh for r in rules[:3]] if rules else ["主城", "禁区", "旧档案馆"]
    factions = ["统治方", "挑战方"]  # Generic — LLM will override

    # Derive tones from the reader promise keywords
    promise = cat.reader_promise_zh or ""
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
                rules.append({
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
                })
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
    return {
        "rules": [
            {
                "rule_id": "R001",
                "name": "核心秩序规则",
                "description": "某套被广泛承认的秩序决定谁能拿到真相、资源与保护。",
                "story_consequence": f"{protagonist_name}不能只靠情绪或直觉推进，必须拿到足以撬动秩序的证据、筹码或资格。",
                "exploitation_potential": "秩序越明确，留下的痕迹和漏洞也越清晰，能够被反向利用。",
            },
            {
                "rule_id": "R002",
                "name": "门槛通行规则",
                "description": "关键地点、关键人物与关键资源，都被权限、身份或中间人把守。",
                "story_consequence": "主角必须跨过一个明确门槛，主线才可能真正推进。",
                "exploitation_potential": "门槛会形成固定流程，而固定流程就是最容易被观察和撬开的地方。",
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
        "history_event": f"{protagonist_name}曾在一场关键事件里承担过与真相并不匹配的代价。",
    }


def _planner_writing_profile(project: ProjectModel) -> Any:
    raw = project.metadata_json.get("writing_profile") if isinstance(project.metadata_json, dict) else None
    return resolve_writing_profile(
        raw,
        genre=project.genre,
        sub_genre=project.sub_genre,
        audience=project.audience,
        language=project.language,
    )


def _planner_language(project: ProjectModel) -> str:
    return str(project.language or "zh-CN")


def _planner_prompt_pack(project: ProjectModel):
    writing_profile = _planner_writing_profile(project)
    return resolve_prompt_pack(
        writing_profile.market.prompt_pack_key,
        genre=project.genre,
        sub_genre=project.sub_genre,
    )


def _build_protagonist_from_category(
    protagonist_name: str,
    *,
    writing_profile: Any,
    category_key: str | None = None,
    is_en: bool = False,
) -> dict[str, Any]:
    """Build protagonist dict using category archetype if available, else legacy defaults."""
    archetype = None
    if category_key:
        cat = get_novel_category(category_key)
        if cat and cat.protagonist_archetypes:
            archetype = cat.protagonist_archetypes[0]

    if archetype:
        core_wound = (archetype.core_wound_en if is_en else archetype.core_wound_zh) or ""
        ext_goal_tpl = (archetype.external_goal_template_en if is_en else archetype.external_goal_template_zh) or ""
        int_need_tpl = (archetype.internal_need_template_en if is_en else archetype.internal_need_template_zh) or ""
        ext_goal = (
            writing_profile.character.protagonist_core_drive
            or ext_goal_tpl.replace("{name}", protagonist_name)
            or (f"{protagonist_name} must resolve the core conflict." if is_en else f"{protagonist_name}必须解决核心冲突。")
        )
        int_need = int_need_tpl.replace("{name}", protagonist_name) or (
            f"{protagonist_name} must grow beyond current limitations." if is_en
            else f"{protagonist_name}需要突破当前局限。"
        )
        return {
            "name": protagonist_name,
            "core_wound": core_wound.replace("{name}", protagonist_name),
            "external_goal": ext_goal,
            "internal_need": int_need,
            "archetype": writing_profile.character.protagonist_archetype or (archetype.name_en if is_en else archetype.name_zh),
            "golden_finger": writing_profile.character.golden_finger,
        }

    # Legacy default
    return {
        "name": protagonist_name,
        "core_wound": (
            f"{protagonist_name} once paid a heavy price for a critical misjudgment."
            if is_en else f"{protagonist_name}曾因一次关键判断失误付出沉重代价。"
        ),
        "external_goal": (
            writing_profile.character.protagonist_core_drive
            or (f"{protagonist_name} must track down and expose the orchestrator behind the current crisis." if is_en
                else f"{protagonist_name}必须主动追查并破解当前危机背后的操盘者。")
        ),
        "internal_need": (
            f"{protagonist_name} must shift from shouldering everything alone to building a sustainable alliance."
            if is_en else f"{protagonist_name}需要从只靠个人硬撑，转向建立真正可持续的同盟。"
        ),
        "archetype": writing_profile.character.protagonist_archetype,
        "golden_finger": writing_profile.character.golden_finger,
    }


def _fallback_book_spec(project: ProjectModel, premise: str, *, category_key: str | None = None) -> dict[str, Any]:
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
    story_tags = _string_list(book_seed.get("tags")) + _string_list(book_seed.get("interaction_tags"))
    story_themes = _string_list(story_bible.get("side_threads"))
    return {
        "title": project.title,
        "logline": (
            _non_empty_string(story_bible.get("premise"), "")
            or _non_empty_string(book_seed.get("synopsis"), "")
            or premise.strip()
        ),
        "genre": project.genre,
        "target_audience": project.audience or "web-serial",
        "tone": writing_profile.style.tone_keywords or profile["tones"],
        "themes": [
            *profile["themes"],
            *[item for item in story_themes[:2] if item not in profile["themes"]],
            *[
                item for item in writing_profile.market.selling_points[:2]
                if item not in profile["themes"] and item not in story_themes[:2]
            ],
        ],
        "protagonist": _build_protagonist_from_category(
            protagonist_name,
            writing_profile=writing_profile,
            category_key=category_key,
            is_en=is_english_language(project.language),
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
            "selling_points": list(dict.fromkeys(
                _string_list(reader_desire.get("reward_promises"))[:3] or writing_profile.market.selling_points
            )),
            "trope_keywords": list(dict.fromkeys(story_tags[:4] or writing_profile.market.trope_keywords)),
            "opening_strategy": writing_profile.market.opening_strategy,
            "payoff_rhythm": writing_profile.market.payoff_rhythm,
            "first_three_chapter_goal": writing_profile.serialization.first_three_chapter_goal,
            "control_promises": _string_list(reader_desire.get("control_promises"))[:3],
            "suspense_questions": _string_list(reader_desire.get("suspense_questions"))[:3],
            "mainline_milestones": milestone_titles[:6],
        },
    }


def _fallback_world_spec(project: ProjectModel, premise: str, book_spec: dict[str, Any], *, category_key: str | None = None) -> dict[str, Any]:
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
    return {
        "world_name": profile["world_name"],
        "world_premise": profile["world_premise"],
        "rules": template["rules"],
        "power_system": {
            "name": profile["power_system_name"],
            "tiers": ["Novice", "Intermediate", "Advanced", "Apex"] if _is_en else ["低阶", "中阶", "高阶", "顶层"],
            "acquisition_method": "Advance through real adventure, resource competition, and high-pressure trials." if _is_en else "通过真实冒险、资源争夺和高压试炼提升。",
            "hard_limits": "Each tier leap exacts an irreversible cost — loss, sacrifice, or permanent trade-off." if _is_en else "每次跃迁都会伴随代价、损耗或不可逆牺牲。",
            "protagonist_starting_tier": "Novice" if _is_en else "低阶",
        },
        "locations": [
            {
                "name": profile["locations"][0],
                "type": "Core Stronghold" if _is_en else "核心据点",
                "atmosphere": "High-pressure, regimented, conflict can erupt at any moment" if _is_en else "高压、秩序化、随时可能爆发冲突",
                "key_rules": ["R001", "R002"],
                "story_role": "Opening stage and source of oppressive order" if _is_en else "开局主舞台与秩序压迫的来源",
            },
            {
                "name": profile["locations"][1],
                "type": "Danger Zone" if _is_en else "危险区域",
                "atmosphere": "Distorted, oppressive, forces characters into hard choices" if _is_en else "失真、压迫、逼迫人物做出选择",
                "key_rules": ["R003"],
                "story_role": "Site of investigation and climactic confrontation" if _is_en else "调查推进和高潮冲突发生地",
            },
            {
                "name": profile["locations"][2],
                "type": "Ultimate Destination" if _is_en else "终极目标地",
                "atmosphere": "Mysterious, sealed, comes at a great cost" if _is_en else "神秘、封闭、伴随巨大代价",
                "key_rules": ["R001", "R002", "R003"],
                "story_role": "Repository of the final truth and critical evidence" if _is_en else "最终真相与关键证据的藏身处",
            },
        ],
        "factions": [
            {
                "name": profile["factions"][0],
                "goal": "Maintain the existing order and control." if _is_en else "维持既有秩序与控制力。",
                "method": "Suppress dissent through rules, resources, and coercive force." if _is_en else "通过规则、资源和强制力量压制异议。",
                "relationship_to_protagonist": "hostile" if _is_en else "敌对",
                "internal_conflict": "Some insiders know the truth but dare not take a public stand." if _is_en else "内部有人知道真相，但不敢公开站队。",
            },
            {
                "name": profile["factions"][1],
                "goal": "Hold on to survival space and win greater autonomy." if _is_en else "在夹缝中保住生存空间并获取更多自主权。",
                "method": "Back-channel deals, informal alliances, and grey-area operations." if _is_en else "私下交易、非正式合作与灰色行动。",
                "relationship_to_protagonist": "complicated" if _is_en else "复杂",
                "internal_conflict": "They want to use the protagonist but fear being dragged down with them." if _is_en else "既想利用主角，又担心被主角拖下水。",
            },
        ],
        "power_structure": template["power_structure"],
        "history_key_events": [
            {
                "event": template["history_event"],
                "relevance": "This is both the protagonist's open wound and the entry point to the current crisis." if _is_en else "这既是主角心结，也是当前主线危机的前史入口。",
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
        forces.append({
            **base,
            "active_volumes": [vol_idx],
        })
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
    home_location = _named_item(locations, 0, profile["locations"][0])
    ruling_faction = _named_item(factions, 0, profile["factions"][0])
    protagonist_tier = _non_empty_string(
        power_system.get("protagonist_starting_tier"),
        "low" if is_en else "低阶",
    )
    # Use LLM-generated name pool when available; fall back to static pool
    name_pool = character_name_pool if character_name_pool else _genre_name_pool(project.genre, language=project.language, seed_text=name_seed)
    pool_allies = [a["name"] for a in _mapping_list(name_pool.get("allies")) if a.get("name")]
    pool_antagonists = [a["name"] for a in _mapping_list(name_pool.get("antagonists")) if a.get("name")]
    story_antagonists = [
        item.get("name")
        for item in story_characters
        if item.get("name") and str(item.get("role") or "").lower() in {"反派", "宿敌", "antagonist", "rival", "enemy"}
    ]
    story_supporters = [
        item.get("name")
        for item in story_characters
        if item.get("name") and item.get("name") != protagonist_name and item.get("name") not in story_antagonists
    ]
    ally_name = next((n for n in pool_allies if n != protagonist_name), _role_label("ally", language=project.language, index=0))
    if story_supporters:
        ally_name = story_supporters[0]
    antagonist_name = next((n for n in story_antagonists if n != protagonist_name), "")
    if not antagonist_name:
        antagonist_name = next((n for n in pool_antagonists if n != protagonist_name), _role_label("antagonist", language=project.language))
    # Extra names for multi-force conflict characters
    _used = {protagonist_name, ally_name, antagonist_name}
    _extra_allies = [n for n in pool_allies if n not in _used]
    _story_remaining = [n for n in story_supporters[1:] if n not in _used]
    local_threat_name = _story_remaining[0] if _story_remaining else (_extra_allies[0] if _extra_allies else _role_label("local_threat", language=project.language))
    _used.add(local_threat_name)
    betrayer_name = next(
        (n for n in _story_remaining[1:] if n not in _used),
        next((n for n in _extra_allies[1:] if n not in _used), _role_label("betrayer", language=project.language)),
    )
    _used.add(betrayer_name)
    # Determine volume count for conflict force assignment
    total_chapters = max(project.target_chapters, 1)
    hierarchy = compute_linear_hierarchy(total_chapters)
    volume_count = hierarchy["volume_count"]
    extra_story_supporting_cast: list[dict[str, Any]] = []
    for item in story_characters:
        name = _non_empty_string(item.get("name"), "")
        if not name or name in {ally_name, local_threat_name, betrayer_name, antagonist_name, protagonist_name}:
            continue
        role_text = str(item.get("role") or "").strip()
        normalized_role = "antagonist" if role_text in {"反派", "宿敌", "antagonist", "rival", "enemy"} else "ally"
        extra_story_supporting_cast.append(
            {
                "name": name,
                "role": normalized_role,
                "background": _non_empty_string(item.get("description"), item.get("title")),
                "goal": item.get("title"),
                "value_to_story": (
                    f"Retains the canonical story-package function of {name}."
                    if is_en else f"保留 story_package 中 {name} 的既有叙事功能。"
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
                "Has always suspected the past failure was not what it appeared."
                if is_en
                else "主角一直怀疑过去的失败并非表面原因。"
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
                    "mannerisms": ["rubs the bridge of their nose when thinking", "drops voice at key moments"],
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
                    ["The protagonist has begun questioning the old case", "Someone inside the system may defect"]
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
                    "lines_never_crossed": ["Never gets hands dirty — always lets the rules do the killing"],
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
                        "core_values": ["Protect those still in the game", "Loyalty with conditions"],
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
                "background": (
                    "One of the protagonist's trusted companions who helped at a critical moment."
                    if is_en
                    else f"主角信任的同伴之一，曾在关键时刻提供过帮助。"
                ),
                "goal": (
                    "Ostensibly helps the protagonist while secretly advancing a hidden agenda."
                    if is_en
                    else "表面上协助主角，实际上在为自己的秘密目标铺路。"
                ),
                "flaw": (
                    "Cannot let go of personal ambition."
                    if is_en
                    else "无法割舍自己的野心。"
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
        ] + extra_story_supporting_cast,
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


def _fallback_volume_plan(project: ProjectModel, book_spec: dict[str, Any], cast_spec: dict[str, Any], world_spec: dict[str, Any], *, category_key: str | None = None) -> list[dict[str, Any]]:
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
                    _non_empty_string(item.get("summary"), _non_empty_string(item.get("title"), "")),
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

    # Use conflict forces if available, otherwise fall back to single-antagonist
    antagonist_forces = _mapping_list(cast_payload.get("antagonist_forces"))
    conflict_phases = _assign_conflict_phases(volume_count, category_key=category_key)
    # Build volume→force mapping
    force_by_volume: dict[int, dict[str, Any]] = {}
    for force_raw in antagonist_forces:
        force = _mapping(force_raw)
        for vol in (force.get("active_volumes") or []):
            if isinstance(vol, int):
                force_by_volume[vol] = force

    plan: list[dict[str, Any]] = []
    phase_occurrence_counter: dict[str, int] = {}
    used_titles: set[str] = set()
    for volume_number, (chapter_start, chapter_end) in enumerate(chapter_ranges, start=1):
        phase = conflict_phases[min(volume_number - 1, len(conflict_phases) - 1)]
        force = force_by_volume.get(volume_number, {})
        force_name = _non_empty_string(force.get("name"), antagonist_name)
        milestone = milestone_entries[volume_number - 1] if volume_number - 1 < len(milestone_entries) else {}
        milestone_title = _non_empty_string(_mapping(milestone).get("title"), "")
        phase_occurrence = phase_occurrence_counter.get(phase, 0)
        phase_occurrence_counter[phase] = phase_occurrence + 1
        if milestone_title:
            volume_title = milestone_title
        else:
            volume_title = _resolve_fallback_volume_title(
                phase, phase_occurrence, volume_number, is_en=is_en
            )
            # Disambiguate if a later phase repeat exhausted the pool and
            # produced a duplicate against an earlier milestone/phase title.
            if volume_title in used_titles:
                volume_title = (
                    f"{volume_title} · Volume {volume_number}" if is_en
                    else f"{volume_title}·第{volume_number}卷"
                )
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

        plan.append(
            {
                "volume_number": volume_number,
                "volume_title": volume_title,
                "volume_theme": themes[(volume_number - 1) % len(themes)],
                "word_count_target": int(project.target_word_count / volume_count),
                "chapter_count_target": chapter_end - chapter_start + 1,
                "conflict_phase": phase,
                "primary_force_name": force_name,
                "opening_state": {
                    "protagonist_status": (
                        ("The protagonist is already under pressure and cannot stay still." if volume_number == 1 else f"The protagonist enters Volume {volume_number} from the aftereffects of the previous stage.")
                        if is_en
                        else ("主角已经被推入高压局面，无法停在原地。" if volume_number == 1 else f"主角带着上一卷的后果进入第{volume_number}卷。")
                    ),
                    "protagonist_power_tier": protagonist_tier
                    if volume_number == 1
                    else (
                        f"Changed by the previous stage"
                        if is_en
                        else f"经历上一阶段后的新状态"
                    ),
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
                        (f"A new pressure source for Volume {volume_number + 1} is now unavoidable." if volume_number < volume_count else "All active lines now converge.")
                        if is_en
                        else (f"第{volume_number + 1}卷的新压力来源已经无法回避。" if volume_number < volume_count else "所有主线开始汇聚。")
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
                                if is_en else f"暗线开始发酵：{hidden_routes[volume_number - 1]}"
                            )
                        ]
                        if volume_number - 1 < len(hidden_routes) else []
                    ),
                ],
                "foreshadowing_planted": (
                    [(f"Plant one unresolved variable that must mature in Volume {volume_number + 1}." if is_en else f"埋下一条必须在第{volume_number + 1}卷继续发酵的未解变量。")]
                    if volume_number < volume_count
                    else []
                ),
                "foreshadowing_paid_off": (
                    [(f"Pay off at least one earlier setup in a way that changes the next stage." if is_en else f"回收至少一条前序铺垫，并让它改变下一阶段。")]
                    if volume_number > 1
                    else []
                ),
                "reader_hook_to_next": (
                    (
                        f"Milestone '{milestone_title}' lands, but the next commercial escalation is already visible."
                        if milestone_title and volume_number < volume_count
                        else (f"The immediate pressure changes shape, but the story cannot settle yet." if volume_number < volume_count else "The story is ready for its final landing.")
                    )
                    if is_en
                    else (
                        f"「{milestone_title}」这个里程碑落地后，更大的商业钩子已经抬头。"
                        if milestone_title and volume_number < volume_count
                        else ("眼前压力虽然变形或后撤，但故事还不能停下来。" if volume_number < volume_count else "故事已经进入终局着陆阶段。")
                    )
                ),
                "arc_ranges": arcs,
                "is_final_volume": volume_number == volume_count,
            }
        )
    return plan


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
            arcs.append({
                "arc_index": arc_idx,
                "chapter_start": arc_start,
                "chapter_end": arc_end,
                "arc_goal": arc_goal,
            })
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
            ) if i == 0 else (
                f"{protagonist_name} enters a new stage after the last act."
                if is_en
                else f"{protagonist_name}在上一幕后进入新的阶段。"
            ),
            "exit_state": (
                f"{protagonist_name} completes the core dramatic movement."
                if is_en
                else f"{protagonist_name}完成主线的核心情感与行动闭环。"
            ) if is_final else (
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
    "survival",           # 直接生存威胁
    "political_intrigue",  # 权力博弈与暗中布局
    "betrayal",           # 信任崩塌与背刺
    "faction_war",        # 多方势力全面对抗
    "existential_threat",  # 终极威胁与最大牺牲
    "internal_reckoning",  # 内心拷问与自我面对
]

# Phase-based volume title pools. Used as a fallback when no milestone title
# is provided, so volumes get distinct, meaningful names instead of
# generic "第N卷" / "Volume N" placeholders. Each list is cycled by the
# phase's occurrence index across the volume plan.
_PHASE_TITLE_VARIATIONS_ZH: dict[str, list[str]] = {
    # Category: action-progression phases
    "individual_survival": ["血路初开", "绝境求生", "悬崖立命", "险中续命"],
    "faction_friction": ["夹缝立足", "势力角逐", "风云暗涌", "群雄倾轧"],
    "power_system_test": ["体系破障", "规则叩问", "质变之门", "力道重铸"],
    "world_threat": ["天下危局", "苍生倾覆", "乾坤失衡", "众生浩劫"],
    "transcendence": ["破执证道", "大道归一", "心魔照影", "超凡入圣"],
    # Legacy _CONFLICT_PHASE_TYPES
    "survival": ["绝地求生", "险中立身", "生死博弈", "残局求存"],
    "political_intrigue": ["暗流权谋", "棋盘迷影", "庙堂风云", "权谋迭起"],
    "betrayal": ["信任崩裂", "背刺寒霜", "裂痕成渊", "故人反目"],
    "faction_war": ["群雄逐鹿", "百宗乱战", "势力倾轧", "风云对决"],
    "existential_threat": ["天倾之危", "末世将至", "终极决断", "万象归零"],
    "internal_reckoning": ["归心照影", "内心审判", "破执立我", "自我重铸"],
}

_PHASE_TITLE_VARIATIONS_EN: dict[str, list[str]] = {
    "individual_survival": ["First Blood", "Edge of Survival", "Cliff of Fate", "Breath by Breath"],
    "faction_friction": ["Cracks Between Powers", "Shifting Alliances", "Undercurrents Rise", "Caught in the Fray"],
    "power_system_test": ["Rules Unbound", "Crossing the Threshold", "Trial of Ascent", "Forging Anew"],
    "world_threat": ["World in Peril", "Heaven Tilts", "The Great Reckoning", "A Fracturing Sky"],
    "transcendence": ["Beyond the Path", "Unity of the Way", "Shadow of the Mind", "Stepping Beyond Mortality"],
    "survival": ["Bare Survival", "Stand Your Ground", "Life on a Knife's Edge", "The Last Ember"],
    "political_intrigue": ["Whispers of Power", "The Shifting Board", "Court of Shadows", "A Web of Schemes"],
    "betrayal": ["Broken Trust", "A Cold Blade", "Fault Lines Open", "Friends Turned Foes"],
    "faction_war": ["Rival Banners", "Open Warfare", "The Grand Clash", "Age of Contention"],
    "existential_threat": ["On the Brink", "Twilight of an Age", "The Final Choice", "Reduction to Zero"],
    "internal_reckoning": ["Into the Self", "Inner Trial", "Breaking the Chain", "Reforging the Heart"],
}


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
    """
    pool = (_PHASE_TITLE_VARIATIONS_EN if is_en else _PHASE_TITLE_VARIATIONS_ZH).get(phase_key) or []
    if pool:
        base = pool[phase_occurrence % len(pool)]
        # When a phase repeats beyond the pool size, append an occurrence
        # suffix so titles stay distinct without resorting to "第N卷".
        cycle = phase_occurrence // len(pool)
        if cycle == 0:
            return base
        return f"{base} · II" if is_en and cycle == 1 else (
            f"{base} · {_roman(cycle + 1)}" if is_en else f"{base}·{_chinese_ordinal(cycle + 1)}"
        )
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
    first = phases[0]       # always survival
    last = phases[-1]       # always internal_reckoning
    middle = phases[1:-1]   # intrigue, betrayal, faction_war, existential_threat
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
                        "goal": (phase.volume_goal_template_en if is_en else phase.volume_goal_template_zh) or "",
                        "climax": (phase.volume_climax_template_en if is_en else phase.volume_climax_template_zh) or "",
                        "obstacle": (phase.volume_obstacle_template_en if is_en else phase.volume_obstacle_template_zh) or "",
                        "resolution": (phase.volume_resolution_template_en if is_en else phase.volume_resolution_template_zh) or "",
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


def _hook_type(index_within_volume: int, total_in_volume: int, *, language: str | None = None) -> str:
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
    _h = int(hashlib.md5(f"{slug}:{chapter}:{label}".encode(), usedforsecurity=False).hexdigest()[:8], 16)
    return options[_h % len(options)]


# After high-tension phases, insert low-tension scene types to create rhythm.
_SCENE_TYPE_AFTER_CLIMAX = [
    "aftermath", "introspection", "relationship_building",
    "quiet_revelation", "emotional_recovery", "alliance_shift",
]
_SCENE_TYPE_AFTER_PRESSURE = [
    "preparation", "worldbuilding_discovery",
    "strategic_planning", "resource_gathering", "mentor_moment",
]
_SCENE_TYPE_COMIC_INTERVAL = 7  # Insert comic relief every N chapters

_FALLBACK_TITLE_PREFIXES = [
    "暗潮", "盲区", "裂痕", "回声", "风眼", "余烬",
    "伏线", "变局", "断点", "逆流", "边界", "悬灯",
    "浮标", "锈迹", "夜隙", "残局", "沉渊", "灰幕",
    "雾锁", "棱线", "铁壁", "荒火", "冷锋", "碎影",
]
_FALLBACK_TITLE_SUFFIXES = {
    "setup": ["初现", "入局", "投石", "试探", "铺火", "露锋", "破冰", "起手", "掀幕", "落子"],
    "investigation": ["追索", "摸底", "拆解", "寻隙", "探针", "回查", "溯源", "揭层", "织网", "破壁"],
    "pressure": ["加压", "围拢", "失衡", "封锁", "死线", "逼近", "绞杀", "窒息", "崩弦", "缩网"],
    "reversal": ["反咬", "逆转", "偏航", "脱钩", "换轨", "回火", "翻盘", "倒戈", "破局", "重铸"],
    "climax": ["爆裂", "截断", "崩口", "闯线", "归零", "掀牌", "决堤", "焚天", "碎锁", "终幕"],
}
_FALLBACK_TITLE_PREFIXES_EN = [
    "Storm", "Ash", "Iron", "Glass", "Night", "Ember",
    "Shadow", "Signal", "Hollow", "Rift", "Cinder", "Cipher",
    "Frost", "Veil", "Thorn", "Drift", "Shard", "Crimson",
    "Wraith", "Beacon", "Chasm", "Ruin", "Onyx", "Haze",
]
_FALLBACK_TITLE_SUFFIXES_EN = {
    "setup": ["Wake", "Threshold", "First Light", "Opening Move", "Spark", "Edge", "Kindling", "Harbinger", "Genesis", "Prelude"],
    "investigation": ["Trace", "Crossing", "Faultline", "Search", "Probe", "Ledger", "Cipher", "Thread", "Excavation", "Inquiry"],
    "pressure": ["Lockdown", "Deadline", "Pressure", "Siege", "Choke Point", "Breaking Point", "Stranglehold", "Crucible", "Gauntlet", "Cascade"],
    "reversal": ["Countermove", "Turn", "Slip", "Backfire", "Pivot", "Undoing", "Gambit", "Shift", "Resurgence", "Overthrow"],
    "climax": ["Rupture", "Burn", "Cutline", "Zero Hour", "Collapse", "Last Gate", "Reckoning", "Inferno", "Convergence", "Endgame"],
}


def _chapter_fallback_subtitle(
    chapter_number: int,
    phase: str,
    index_within_volume: int,
    volume_number: int,
    *,
    language: str | None = None,
    is_opening: bool,
    project_slug: str = "",
) -> str:
    """Build a concise, genre-neutral fallback subtitle.

    Uses a project-slug-seeded shuffle so different novels get different title
    sequences even when using the same genre preset. The expanded vocabulary
    (24 prefixes x 10 suffixes per phase) minimizes visible repetition across
    30+ chapters.
    """
    import hashlib  # noqa: PLC0415
    import random as _rng  # noqa: PLC0415

    is_en = is_english_language(language)
    suffix_map = _FALLBACK_TITLE_SUFFIXES_EN if is_en else _FALLBACK_TITLE_SUFFIXES
    phase_key = phase if phase in suffix_map else "investigation"
    prefixes = list(_FALLBACK_TITLE_PREFIXES_EN if is_en else _FALLBACK_TITLE_PREFIXES)
    suffixes = list(suffix_map[phase_key])

    # Seed a deterministic shuffle from project_slug so each novel gets a
    # unique title sequence, but the same novel is reproducible.
    seed = int(hashlib.md5(project_slug.encode(), usedforsecurity=False).hexdigest()[:8], 16)
    _rng.Random(seed).shuffle(prefixes)
    _rng.Random(seed + 1).shuffle(suffixes)

    prefix_index = (chapter_number - 1) % len(prefixes)
    suffix_index = (chapter_number - 1) % len(suffixes)
    if is_en:
        return f"{prefixes[prefix_index]} {suffixes[suffix_index]}".strip()
    return f"{prefixes[prefix_index]}{suffixes[suffix_index]}"


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
    if scene_number == 1 and prev_phase in ("climax", "reversal") and phase in ("setup", "investigation"):
        return _SCENE_TYPE_AFTER_CLIMAX[_hash_val % len(_SCENE_TYPE_AFTER_CLIMAX)]
    # Middle scenes in investigation phase can be relationship or worldbuilding
    if scene_number == 2 and phase == "investigation":
        return _SCENE_TYPE_AFTER_PRESSURE[_hash_val % len(_SCENE_TYPE_AFTER_PRESSURE)]
    # Periodic comic relief — vary the interval per novel (5–9 chapters);
    # use a chapter-only seed so the interval is stable regardless of scene_number.
    _ch_hash = int(hashlib.md5(f"{project_slug}:{chapter_number}".encode(), usedforsecurity=False).hexdigest()[:8], 16)
    _comic_interval = 5 + (_ch_hash % 5)
    if chapter_number % _comic_interval == 0 and scene_number == 1 and phase not in ("climax", "reversal"):
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

    # Try rich templates first (keyed by conflict_phase × chapter_phase)
    _templates = _CHAPTER_CONFLICT_TEMPLATES_EN if is_en else _CHAPTER_CONFLICT_TEMPLATES
    phase_dict = _templates.get(conflict_phase, {})
    rich_template = phase_dict.get(chapter_phase)
    if rich_template:
        return rich_template.format(protagonist=protagonist, force_name=force_name)

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
    label = phase_labels_en.get(chapter_phase, "keep the plot moving") if is_en else phase_labels.get(chapter_phase, "继续推进")
    _templates_generic_en = [
        f"{protagonist} must {label} while dealing with the active resistance around {force_name}.",
        f"{protagonist} navigates escalating pressure from {force_name} as the situation demands they {label}.",
        f"Caught between {force_name}'s maneuvers and their own goals, {protagonist} fights to {label}.",
    ]
    _templates_generic_zh = [
        f"{protagonist}必须在处理「{force_name}」带来的当前阻力时完成本章的「{label}」。",
        f"面对「{force_name}」不断升级的施压，{protagonist}艰难推进——{label}。",
        f"{protagonist}被「{force_name}」的布局和自身目标夹在中间，必须在夹缝中{label}。",
    ]
    return _pick_by_seed(
        _templates_generic_en if is_en else _templates_generic_zh,
        project_slug, chapter_number, "conflict_render",
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


async def _next_chapter_number_for_volume(
    session: AsyncSession,
    project_id: UUID,
    volume_number: int,
) -> int:
    """Return the first ``chapter_number`` that a fresh replan of ``volume_number`` should use.

    Authority chain (strongest → weakest):
      1. ``max(chapter_number) + 1`` across ALL chapters belonging to *earlier*
         volumes (volume_number < N). This binds the start of volume N to the
         real DB layout regardless of what VOLUME_PLAN targets claim.
      2. ``max(chapter_number) + 1`` across all chapters in the project when
         no earlier-volume chapter exists (e.g. volume 1 or an empty project).

    Never trusts VOLUME_PLAN targets — those are exactly what drifted during
    the 200-chapter gap incident. Always ≥ 1.
    """
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
    (default 1). Pass ``max(existing_written_chapter_number) + 1`` when
    padding a volume replan so the fallback doesn't collide with already-
    written chapters — this was the root cause of the 200-chapter gap on
    xianxia-upgrade-1776137730.
    """
    writing_profile = _planner_writing_profile(project)
    cast_payload = _mapping(cast_spec)
    protagonist_name = _non_empty_string(_mapping(cast_payload.get("protagonist")).get("name"), "主角")
    supporting_cast = _mapping_list(cast_payload.get("supporting_cast"))
    ally_name = _non_empty_string(_named_item(supporting_cast, 0, protagonist_name).get("name"), protagonist_name)
    antagonist_name = _non_empty_string(_mapping(cast_payload.get("antagonist")).get("name"), "敌人")
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

    chapters: list[dict[str, Any]] = []
    chapter_number = max(int(chapter_number_offset), 1)
    _gen = get_settings().generation
    chapter_target_words = max(
        _gen.words_per_chapter.min,
        min(_gen.words_per_chapter.target, int(project.target_word_count / max(project.target_chapters, 1))),
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
        volume_goal = _non_empty_string(volume_payload.get("volume_goal"), "推动主线调查取得关键进展")
        volume_number = int(volume_payload.get("volume_number") or raw_volume_index)
        # Extract per-volume conflict phase and force name
        conflict_phase = _non_empty_string(volume_payload.get("conflict_phase"), "survival")
        volume_force_name = _non_empty_string(volume_payload.get("primary_force_name"), antagonist_name)
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
                        f"Final alliances formed, last secrets revealed — all forces converge toward the climactic confrontation."
                        if is_en
                        else f"最终联盟结成、最后的秘密揭露——所有力量向高潮对决汇聚。"
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
            num_scenes = _compute_scene_count(chapter_number, phase, prev_phase, chapters_from_end)
            # Build scenes dynamically: opening + N middle + closing hook
            scenes: list[dict[str, Any]] = []

            # Scene 1: Opening — inject chapter_goal for per-chapter uniqueness
            _ch_goal_tag = f" [chapter goal: {chapter_goal}]" if is_en else f"（本章目标：{chapter_goal}）"
            opening_story = (
                f"Establish the immediate state, the current direction, and the near-term pressure.{_ch_goal_tag}"
                if is_en and is_opening_chapter
                else f"Carry forward the previous result and restate the immediate action target.{_ch_goal_tag}"
                if is_en
                else f"建立当前局面、行动方向与眼前压力{_ch_goal_tag}"
                if is_opening_chapter
                else f"承接上章后果并明确本章行动目标{_ch_goal_tag}"
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
            scenes.append({
                "scene_number": 1,
                "scene_type": "hook" if is_opening_chapter else _varied_scene_type(
                    "setup" if phase == "setup" else "transition",
                    chapter_number, 1, phase, prev_phase,
                    project_slug=project.slug,
                ),
                "title": "Opening Beat" if is_en else "开场",
                "time_label": "章节开场",
                "participants": [protagonist_name, ally_name],
                "purpose": {
                    "story": opening_story,
                    "emotion": opening_emotion,
                },
                "entry_state": {
                    protagonist_name: {"arc_state": _pick_by_seed(
                        ["承压推进", "犹豫不决", "暗中筹谋", "被迫应对", "重振旗鼓"], project.slug, chapter_number, "entry_arc"
                    ), "emotion": _pick_by_seed(
                        ["紧绷", "焦虑", "冷静克制", "愤怒压抑", "期待中带着不安"], project.slug, chapter_number, "entry_emo"
                    )},
                    ally_name: {"arc_state": _pick_by_seed(
                        ["谨慎协作", "主动支援", "心存疑虑", "独立行动", "勉强配合"], project.slug, chapter_number, "ally_entry"
                    ), "emotion": _pick_by_seed(
                        ["戒备", "忧虑", "冷静", "不安", "坚定"], project.slug, chapter_number, "ally_emo"
                    )},
                },
                "exit_state": {
                    protagonist_name: {"arc_state": _pick_by_seed(
                        ["主动出击", "获得线索", "陷入困境", "做出抉择", "暂时脱险"], project.slug, chapter_number, "exit_arc"
                    ), "emotion": _pick_by_seed(
                        ["更坚定", "沉重", "释然", "紧迫感", "复杂交织"], project.slug, chapter_number, "exit_emo"
                    )},
                    ally_name: {"arc_state": _pick_by_seed(
                        ["被迫跟进", "选择信任", "产生分歧", "承担更多", "暗自打算"], project.slug, chapter_number, "ally_exit"
                    ), "emotion": _pick_by_seed(
                        ["压力上升", "决心", "动摇", "疲惫", "隐忍"], project.slug, chapter_number, "ally_exit_emo"
                    )},
                },
                "target_word_count": scene_target_words,
            })

            # Middle scenes (0 for 2-scene, 1 for 3-scene, 2 for 4-scene)
            middle_count = num_scenes - 2
            # Seed-based scene type selection for diversity across novels
            _ch_seed = int(hashlib.md5(f"{project.slug}:mid:{chapter_number}".encode(), usedforsecurity=False).hexdigest()[:8], 16)
            _HIGH_TENSION_TYPES = ["conflict", "confrontation", "desperate_gambit", "tactical_clash"]
            _LOW_TENSION_TYPES = ["reveal", "discovery", "negotiation", "deduction"]
            _REFLECTION_TYPES = ["introspection", "relationship_building", "moral_dilemma", "quiet_revelation"]
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
                scenes.append({
                    "scene_number": len(scenes) + 1,
                    "scene_type": _varied_scene_type(
                        base_type, chapter_number, len(scenes) + 1, phase, prev_phase,
                        project_slug=project.slug,
                    ),
                    "title": ("Primary Move" if mi == 0 else "Shift") if is_en else ("推进" if mi == 0 else "变化"),
                    "time_label": "章节中段",
                    "participants": [protagonist_name, volume_antag_participant]
                    if index_within_volume % 2 == 0
                    else [protagonist_name],
                    "purpose": {
                        "story": (
                            f"Move the chapter forward and force a fresh cost or new information. [chapter goal: {chapter_goal}]"
                            if mi == 0
                            else f"Complicate the situation with a deeper cost, truth, or shift. [chapter goal: {chapter_goal}]"
                        ) if is_en else (
                            f"推动本章局势前进，并换来新的代价或信息。（本章目标：{chapter_goal}）"
                            if mi == 0
                            else f"用更深一层的代价、真相或变化把局势再往前推。（本章目标：{chapter_goal}）"
                        ),
                        "emotion": "Raise friction without flattening the chapter rhythm." if is_en else "继续抬高摩擦感，但不把章节写成单一节奏。",
                    },
                    "entry_state": {
                        protagonist_name: {"arc_state": _pick_by_seed(
                            ["带着怀疑推进", "谨慎试探", "果断介入", "被动应战", "暗中观察"],
                            project.slug, chapter_number + mi, "mid_entry"
                        ), "emotion": _pick_by_seed(
                            ["警觉", "冷静", "焦躁", "隐忍", "好奇"],
                            project.slug, chapter_number + mi, "mid_entry_emo"
                        )},
                    },
                    "exit_state": {
                        protagonist_name: {"arc_state": _pick_by_seed(
                            ["掌握更多真相", "付出代价", "发现矛盾", "暂时得利", "陷入两难"],
                            project.slug, chapter_number + mi, "mid_exit"
                        ), "emotion": _pick_by_seed(
                            ["不安", "震惊", "隐隐兴奋", "沉重", "决绝"],
                            project.slug, chapter_number + mi, "mid_exit_emo"
                        )},
                        antagonist_name: {"arc_state": _pick_by_seed(
                            ["开始主动压制", "暗中调整策略", "示弱引诱", "全面出击", "布下新局"],
                            project.slug, chapter_number + mi, "antag_mid"
                        ), "emotion": _pick_by_seed(
                            ["冷静施压", "得意", "谨慎", "愤怒", "隐忍待发"],
                            project.slug, chapter_number + mi, "antag_mid_emo"
                        )},
                    },
                    "target_word_count": scene_target_words,
                })

            # Final scene: closing hook
            scenes.append({
                "scene_number": len(scenes) + 1,
                "scene_type": "hook",
                "title": "Closing Hook" if is_en else "尾钩",
                "time_label": "章节结尾",
                "participants": [protagonist_name, ally_name]
                if index_within_volume % 3 != 0
                else [protagonist_name, volume_antag_participant],
                "purpose": {
                    "story": (
                        f"{writing_profile.market.chapter_hook_strategy}"
                        f"{' [chapter goal: ' + chapter_goal + ']' if is_en else '（本章目标：' + chapter_goal + '）'}"
                    ),
                    "emotion": "Make the reader unable to stop — they MUST read the next chapter." if is_en else "让读者必须继续追下一章",
                },
                "entry_state": {
                    protagonist_name: {"arc_state": _pick_by_seed(
                        ["准备收束", "短暂喘息", "整理线索", "面临抉择", "孤注一掷"],
                        project.slug, chapter_number, "hook_entry"
                    ), "emotion": _pick_by_seed(
                        ["短暂控制局势", "紧绷到极点", "表面平静内心翻涌", "疲惫但不甘", "冷静中带着决绝"],
                        project.slug, chapter_number, "hook_entry_emo"
                    )},
                },
                "exit_state": {
                    protagonist_name: {"arc_state": _pick_by_seed(
                        ["被迫进入更难局面", "发现更大的真相", "失去重要倚仗", "打开新的可能", "站在命运岔路口"],
                        project.slug, chapter_number, "hook_exit"
                    ), "emotion": _pick_by_seed(
                        ["强压下前进", "震惊无措", "悲愤交加", "危机感拉满", "痛苦但更清醒"],
                        project.slug, chapter_number, "hook_exit_emo"
                    )},
                },
                "target_word_count": scene_target_words,
            })
            # Compute arc-level info for this chapter
            arc_index, arc_phase = _compute_chapter_arc_info(chapter_number, normalized_volume_plan)

            # ── Phase-3: assign Swain scene/sequel pattern ──
            _high_tension = phase in {"pressure", "reversal", "climax", "confrontation"}
            for si, sc_dict in enumerate(scenes):
                if _high_tension:
                    sc_dict["swain_pattern"] = "action"
                elif si % 2 == 0:
                    sc_dict["swain_pattern"] = "action"
                else:
                    sc_dict["swain_pattern"] = "sequel"

            chapters.append(
                {
                    "chapter_number": chapter_number,
                    # NOTE: title intentionally left as a SHORT subtitle without
                    # any "第N章" prefix. The chapter header renderer
                    # (``drafts._format_chapter_heading``) is responsible for
                    # re-attaching the canonical "第N章：" prefix exactly once,
                    # which prevents the "# 第1章 第1章：…" double-prefix bug.
                    #
                    # Previously this fell back to an 8-word hard-coded cycle
                    # (``封锁/碰撞/反咬/闯关/断局/逼近/裂缝/追线``) indexed by
                    # ``chapter_number % 8``, which produced visibly repeating
                    # titles every 8 chapters in the output. We now either
                    # derive the subtitle from the chapter phase / position
                    # so the result still reads like a chapter title rather
                    # than a clipped planning note.
                    "title": _chapter_fallback_subtitle(
                        chapter_number,
                        phase,
                        index_within_volume,
                        volume_number,
                        language=project.language,
                        is_opening=(chapter_number == 1),
                        project_slug=project.slug,
                    ),
                    "goal": chapter_goal,
                    "opening_situation": (
                        writing_profile.serialization.opening_mandate
                        if chapter_number == 1
                        else "承接上一章尾钩，主角没有空档去长篇解释设定。"
                    ),
                    "main_conflict": _render_chapter_conflict(
                        conflict_phase, phase, protagonist_name, volume_force_name,
                        project_slug=project.slug, chapter_number=chapter_number,
                    ),
                    "hook_type": _hook_type(index_within_volume, total_in_volume, language=project.language),
                    "hook_description": writing_profile.market.chapter_hook_strategy,
                    "volume_number": volume_number,
                    "arc_index": arc_index,
                    "arc_phase": arc_phase,
                    "target_word_count": chapter_target_words,
                    "scenes": scenes,
                }
            )
            prev_phase = phase
            chapter_number += 1
    return {"batch_name": "auto-generated-full-outline", "chapters": chapters}


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


def _book_spec_prompts(project: ProjectModel, premise: str, fallback: dict[str, Any]) -> tuple[str, str]:
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
    _pp_block = f"Prompt Pack：\n{render_prompt_pack_prompt_block(prompt_pack)}\n" if prompt_pack else ""
    _pp_book_spec = f"{render_prompt_pack_fragment(prompt_pack, 'planner_book_spec')}\n" if prompt_pack else ""
    _methodology_planner_block = render_methodology_block(prompt_pack, phase="planner")
    _methodology_line = f"\n{_methodology_planner_block}\n" if _methodology_planner_block else ""
    _story_package_block = _story_package_prompt_block(project, language=language)
    if is_en:
        user_prompt = (
            f"Project title: {project.title}\n"
            f"Genre: {project.genre}\n"
            f"Target words: {project.target_word_count}\n"
            f"Target chapters: {project.target_chapters}\n"
            f"Audience: {project.audience or 'web-serial'}\n"
            f"Premise: {premise}\n"
            "Write all planning artifacts in English.\n"
            f"Writing profile:\n{render_writing_profile_prompt_block(writing_profile, language=language)}\n"
            f"{_pp_block}"
            f"Serial fiction guardrails:\n{render_serial_fiction_guardrails(writing_profile, language=language)}\n"
            f"{_story_package_block}\n"
            f"{_pp_book_spec}"
            f"{_methodology_line}"
            "Generate a BookSpec JSON with title, logline, genre, target_audience, tone, themes, protagonist, stakes, and series_engine. "
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
            f"写作画像：\n{render_writing_profile_prompt_block(writing_profile, language=language)}\n"
            f"{_pp_block}"
            f"商业网文硬约束：\n{render_serial_fiction_guardrails(writing_profile, language=language)}\n"
            f"{_story_package_block}\n"
            f"{_pp_book_spec}"
            f"{_methodology_line}"
            "请生成一个 BookSpec JSON，包含 title、logline、genre、target_audience、tone、themes、"
            "protagonist、stakes、series_engine。"
            "其中 series_engine 必须清楚写出：核心连载引擎、读者承诺、前三章抓手、章节尾钩策略、"
            "短回报与长回报的节奏安排。"
        )
    _genre_instruction = getattr(_genre_profile.planner_prompts, f"book_spec_instruction_{_lang_key}", "")
    if _genre_instruction:
        user_prompt += f"\n\n{'[Genre planning requirements]' if is_en else '【品类规划要求】'}\n{_genre_instruction}"
    user_prompt = _append_category_context(user_prompt, project, is_en=is_en)
    return system_prompt, user_prompt


def _world_spec_prompts(project: ProjectModel, premise: str, book_spec: dict[str, Any]) -> tuple[str, str]:
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
    _pp_block = f"Prompt Pack：\n{render_prompt_pack_prompt_block(prompt_pack)}\n" if prompt_pack else ""
    _pp_world_spec = f"{render_prompt_pack_fragment(prompt_pack, 'planner_world_spec')}\n" if prompt_pack else ""
    _story_package_block = _story_package_prompt_block(project, language=language)
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
            f"{_pp_world_spec}"
            "Generate a WorldSpec JSON with world_name, world_premise, rules, power_system, locations, factions, power_structure, history_key_events, and forbidden_zones. "
            "World rules must create conflict, cost, upgrade space, and conspiracy leverage rather than empty lore."
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
            f"{_pp_world_spec}"
            "请生成一个 WorldSpec JSON，包含 world_name、world_premise、rules、power_system、locations、"
            "factions、power_structure、history_key_events、forbidden_zones。"
            "要求世界规则能直接制造冲突、爽点成本、升级空间和阴谋推进空间，不要只写空背景。"
        )
    )
    _genre_instruction = getattr(_genre_profile.planner_prompts, f"world_spec_instruction_{_lang_key}", "")
    if _genre_instruction:
        user_prompt += f"\n\n{'[Genre planning requirements]' if is_en else '【品类规划要求】'}\n{_genre_instruction}"
    user_prompt = _append_category_context(user_prompt, project, is_en=is_en)
    return system_prompt, user_prompt


def _cast_spec_prompts(project: ProjectModel, book_spec: dict[str, Any], world_spec: dict[str, Any]) -> tuple[str, str]:
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
    _pp_block = f"Prompt Pack：\n{render_prompt_pack_prompt_block(prompt_pack)}\n" if prompt_pack else ""
    _pp_cast_spec = f"{render_prompt_pack_fragment(prompt_pack, 'planner_cast_spec')}\n" if prompt_pack else ""
    _story_package_block = _story_package_prompt_block(project, language=language)
    user_prompt = (
        (
            f"BookSpec summary:\n{summarize_book_spec(book_spec, language='en')}\n"
            f"WorldSpec summary:\n{summarize_world_spec(world_spec, language='en')}\n"
            f"Era / setting hint: {era_hint}\n"
            "Write all planning artifacts in English.\n"
            f"{_pp_block}"
            f"{_story_package_block}\n"
            f"{_pp_cast_spec}"
            "Generate a CastSpec JSON with protagonist, antagonist, antagonist_forces, supporting_cast, and conflict_map. "
            "The protagonist needs a vivid desire, a real weakness, visible growth space, and a memorable edge; the antagonist must actively counter the protagonist and keep escalating. "
            "Every major character must include a voice_profile object and a moral_framework object so their speech patterns stay distinct.\n\n"
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
            "- Every character must include a name_reasoning field"
        )
        if is_en
        else (
            f"BookSpec 摘要：\n{summarize_book_spec(book_spec, language='zh')}\n"
            f"WorldSpec 摘要：\n{summarize_world_spec(world_spec, language='zh')}\n"
            f"题材时代：{era_hint}\n"
            f"{_pp_block}"
            f"{_story_package_block}\n"
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
            "【角色命名硬性要求】\n"
            f"- 角色名字必须符合「{project.genre}」题材和「{era_hint}」时代背景\n"
            "- 主角名 2-3 字，音调优美朗朗上口，有记忆点\n"
            "- 所有角色的姓氏不能重复\n"
            "- 避免过于生僻的字、谐音不雅的组合、或网文中已经烂大街的名字\n"
            "- 反派名可暗示性格特质但不要太刻意\n"
            "- 每个角色附 name_reasoning 字段说明命名理由"
        )
    )
    _genre_instruction = getattr(_genre_profile.planner_prompts, f"cast_spec_instruction_{_lang_key}", "")
    if _genre_instruction:
        user_prompt += f"\n\n{'[Genre planning requirements]' if is_en else '【品类规划要求】'}\n{_genre_instruction}"
    user_prompt = _append_category_context(user_prompt, project, is_en=is_en)
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
    _pp_block = f"Prompt Pack：\n{render_prompt_pack_prompt_block(prompt_pack)}\n" if prompt_pack else ""
    _pp_volume_plan = f"{render_prompt_pack_fragment(prompt_pack, 'planner_volume_plan')}\n" if prompt_pack else ""
    _story_package_block = _story_package_prompt_block(project, language=language)
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
            f"{_pp_volume_plan}"
            "Generate a VolumePlan JSON array. Each entry must include volume_number, volume_title, volume_theme, chapter_count_target, volume_goal, volume_obstacle, volume_climax, volume_resolution, conflict_phase, and primary_force_name. "
            "CRITICAL: `volume_title` must be a concrete, evocative 2-6 word name (e.g. 'Ashes of the Old Court'). Placeholder names like 'Volume 3', 'Vol. 4', or empty strings are forbidden and will be rejected. "
            "CRITICAL: Each volume must face a DIFFERENT primary conflict force from the CastSpec's antagonist_forces. Don't repeat the same antagonist pressure — vary between survival, political intrigue, betrayal, faction warfare, existential threat, etc. "
            "Every volume needs a concrete payoff, escalation, key reveal, volume-end hook, and anticipation for the next volume."
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
            f"{_pp_volume_plan}"
            "请生成 VolumePlan JSON 数组，每个元素包含 volume_number、volume_title、volume_theme、"
            "chapter_count_target、volume_goal、volume_obstacle、volume_climax、volume_resolution、"
            "conflict_phase（冲突类型：survival/political_intrigue/betrayal/faction_war/existential_threat/internal_reckoning）、"
            "primary_force_name（本卷主要冲突力量名称）。"
            "【关键】volume_title 必须是 2-6 字的具体意象化卷名（例如『逆命入局』『灰楼开门』），"
            "严禁使用『第N卷』『Volume N』『未命名』等占位名或空字符串，否则会被拒绝。"
            "【关键】每卷必须面对不同的冲突力量和冲突类型——不要所有卷都是同一个反派在施压！"
            "每卷都要有清晰的爽点兑现、局势升级、关键揭示、卷尾钩子和下一卷期待。"
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

    _genre_instruction = getattr(_genre_profile.planner_prompts, f"volume_plan_instruction_{_lang_key}", "")
    if _genre_instruction:
        user_prompt += f"\n\n{'[Genre planning requirements]' if is_en else '【品类规划要求】'}\n{_genre_instruction}"
    user_prompt = _append_category_context(user_prompt, project, is_en=is_en)
    return system_prompt, user_prompt


def _outline_prompts(project: ProjectModel, book_spec: dict[str, Any], cast_spec: dict[str, Any], volume_plan: list[dict[str, Any]]) -> tuple[str, str]:
    from bestseller.services.genre_review_profiles import resolve_genre_review_profile

    language = _planner_language(project)
    is_en = is_english_language(language)
    _lang_key = "en" if is_en else "zh"
    writing_profile = _planner_writing_profile(project)
    prompt_pack = _planner_prompt_pack(project)
    _genre_profile = resolve_genre_review_profile(project.genre, project.sub_genre)
    _genre_system = getattr(_genre_profile.planner_prompts, f"outline_system_{_lang_key}", "")
    system_prompt = (
        "You are a chapter-outline planner for long-form commercial fiction. Output valid JSON only."
        if is_en
        else "你是长篇中文小说章纲规划师。输出必须是合法 JSON，不要解释。"
    )
    if _genre_system:
        system_prompt += f"\n{_genre_system}"
    _pp_block = f"Prompt Pack：\n{render_prompt_pack_prompt_block(prompt_pack)}\n" if prompt_pack else ""
    _pp_outline = f"{render_prompt_pack_fragment(prompt_pack, 'planner_outline')}\n" if prompt_pack else ""
    _methodology_planner_block = render_methodology_block(prompt_pack, phase="planner")
    _methodology_line = f"\n{_methodology_planner_block}\n" if _methodology_planner_block else ""
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
            f"{_pp_outline}"
            f"{_methodology_line}"
            "Generate a full ChapterOutlineBatch JSON with batch_name and chapters. Each chapter needs at least 3 scenes. "
            "The first 3 chapters must rapidly establish the protagonist edge, the core anomaly, the first gain/loss cycle, and a strong read-on hook. "
            "Each chapter must define goal, main_conflict, and hook_description; each scene must define story and emotion tasks.\n\n"
            "[DIVERSITY CONSTRAINTS — CRITICAL]\n"
            "1. Each chapter's scene_type combination MUST differ from adjacent chapters — vary scene count, type arrangement, and participant mix.\n"
            "2. Each chapter's main_conflict must be a specific, concrete event — never use vague summaries like 'push the investigation' or 'advance the goal'.\n"
            "3. Character entry_state/exit_state must be tied to the chapter's specific events — never reuse the same arc_state or emotion across multiple chapters.\n"
            "4. Break the narrative rhythm: some chapters end in failure, some focus on quiet character moments, some open with a twist.\n"
            "5. Chapter goals must be concrete, visualizable events — not abstract narrative functions."
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
            f"{_pp_outline}"
            f"{_methodology_line}"
            "请生成完整 ChapterOutlineBatch JSON，包含 batch_name 和 chapters。每章至少 3 个 scenes。"
            "要求：前 3 章必须快速完成主角卖点亮相、核心异常亮相、第一轮得失与追读钩子；"
            "每章都要写明 goal、main_conflict、hook_description；每场都要有 story/emotion 任务。\n\n"
            "【多样性硬约束——极其重要】\n"
            "1. 每章的 scene_type 组合不得与前后两章雷同，必须有结构差异（场景数、类型排列、参与角色组合都要变化）。\n"
            "2. 每章的 main_conflict 必须是独立的、具体的事件描述，禁止使用「推进调查」「承压推进」等泛化概括。\n"
            "3. 角色的 entry_state/exit_state 必须紧扣本章具体事件，禁止多章复用相同的 arc_state 或 emotion。\n"
            "4. 避免所有章节都遵循相同的叙事模式（如每章都是「发现线索→遭遇阻碍→获得突破」），"
            "要主动打破节奏：有的章以失败结尾，有的章以安静的人物关系推进为主，有的章以反转开场。\n"
            "5. chapter goal 必须是具体的、可视化的事件，不能是抽象的叙事功能描述。"
        )
    )
    _genre_instruction = getattr(_genre_profile.planner_prompts, f"outline_instruction_{_lang_key}", "")
    if _genre_instruction:
        user_prompt += f"\n\n{'[Genre planning requirements]' if is_en else '【品类规划要求】'}\n{_genre_instruction}"
    user_prompt = _append_category_context(user_prompt, project, is_en=is_en)
    return system_prompt, user_prompt


def _volume_outline_prompts(
    project: ProjectModel,
    book_spec: dict[str, Any],
    cast_spec: dict[str, Any],
    volume_plan: list[dict[str, Any]],
    volume_entry: dict[str, Any],
) -> tuple[str, str]:
    """Prompts for generating chapter outlines for a single volume."""
    from bestseller.services.genre_review_profiles import resolve_genre_review_profile

    language = _planner_language(project)
    is_en = is_english_language(language)
    _lang_key = "en" if is_en else "zh"
    writing_profile = _planner_writing_profile(project)
    prompt_pack = _planner_prompt_pack(project)
    _genre_profile = resolve_genre_review_profile(project.genre, project.sub_genre)
    _genre_system = getattr(_genre_profile.planner_prompts, f"outline_system_{_lang_key}", "")
    volume_number = int(volume_entry.get("volume_number", 1))
    chapter_count = int(volume_entry.get("chapter_count_target", 10))
    system_prompt = (
        "You are a chapter-outline planner for long-form commercial fiction. Output valid JSON only."
        if is_en
        else "你是长篇中文小说章纲规划师。输出必须是合法 JSON，不要解释。"
    )
    if _genre_system:
        system_prompt += f"\n{_genre_system}"
    _pp_block = f"Prompt Pack：\n{render_prompt_pack_prompt_block(prompt_pack)}\n" if prompt_pack else ""
    _pp_outline = f"{render_prompt_pack_fragment(prompt_pack, 'planner_outline')}\n" if prompt_pack else ""
    _methodology_planner_block = render_methodology_block(prompt_pack, phase="planner")
    _methodology_line = f"\n{_methodology_planner_block}\n" if _methodology_planner_block else ""
    _story_package_block = _story_package_prompt_block(project, language=language)
    vol_plan_summary = summarize_volume_plan_context(volume_plan, current_volume=volume_number, language=language)
    user_prompt = (
        (
            f"Project title: {project.title}\n"
            f"Volume {volume_number} — {chapter_count} chapters\n"
            "Write all planning artifacts in English.\n"
            f"Writing profile:\n{render_writing_profile_prompt_block(writing_profile, language=language)}\n"
            f"{_pp_block}"
            f"Serial fiction guardrails:\n{render_serial_fiction_guardrails(writing_profile, language=language)}\n"
            f"BookSpec summary:\n{summarize_book_spec(book_spec, language='en')}\n"
            f"CastSpec summary:\n{summarize_cast_spec(cast_spec, language='en', volume_number=volume_number)}\n"
            f"VolumePlan context:\n{vol_plan_summary}\n"
            f"{_story_package_block}\n"
            f"{_pp_outline}"
            f"{_methodology_line}"
            f"Generate a ChapterOutlineBatch JSON for volume {volume_number} ONLY ({chapter_count} chapters). "
            "Include batch_name and chapters. Each chapter needs at least 3 scenes. "
            "Each chapter must define goal, main_conflict, and hook_description; each scene must define story and emotion tasks."
        )
        if is_en
        else (
            f"项目标题：{project.title}\n"
            f"第{volume_number}卷 — 共{chapter_count}章\n"
            f"写作画像：\n{render_writing_profile_prompt_block(writing_profile, language=language)}\n"
            f"{_pp_block}"
            f"商业网文硬约束：\n{render_serial_fiction_guardrails(writing_profile, language=language)}\n"
            f"BookSpec 摘要：\n{summarize_book_spec(book_spec, language='zh')}\n"
            f"CastSpec 摘要：\n{summarize_cast_spec(cast_spec, language='zh', volume_number=volume_number)}\n"
            f"VolumePlan 上下文：\n{vol_plan_summary}\n"
            f"{_story_package_block}\n"
            f"{_pp_outline}"
            f"{_methodology_line}"
            f"请仅生成第{volume_number}卷的 ChapterOutlineBatch JSON（共{chapter_count}章），"
            "包含 batch_name 和 chapters。每章至少 3 个 scenes。"
            "每章都要写明 goal、main_conflict、hook_description；每场都要有 story/emotion 任务。"
        )
    )
    _genre_instruction = getattr(_genre_profile.planner_prompts, f"outline_instruction_{_lang_key}", "")
    if _genre_instruction:
        user_prompt += f"\n\n{'[Genre planning requirements]' if is_en else '【品类规划要求】'}\n{_genre_instruction}"
    user_prompt = _append_category_context(user_prompt, project, is_en=is_en)
    return system_prompt, user_prompt


def _volume_cast_expansion_prompts(
    project: ProjectModel,
    book_spec: dict[str, Any],
    world_spec: dict[str, Any],
    cast_spec: dict[str, Any],
    volume_entry: dict[str, Any],
    prior_feedback_summary: str | None = None,
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
        feedback_block = (
            f"\n{'Previous volume writing feedback:' if is_en else '上一卷写作反馈：'}\n{prior_feedback_summary}\n"
        )
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
        snapshot_block = (
            f"\n{'World state after previous volume:' if is_en else '上一卷结束时的世界状态：'}\n{prior_world_snapshot}\n"
        )
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

    supporting_cast = [copy.deepcopy(_mapping(item)) for item in _mapping_list(merged.get("supporting_cast"))]
    supporting_by_name = {
        _non_empty_string(item.get("name"), ""): item
        for item in supporting_cast
        if _non_empty_string(item.get("name"), "")
    }

    def _upsert_character(raw_value: Any) -> None:
        candidate = _sanitize_new_character_candidate(raw_value)
        name = _non_empty_string(candidate.get("name"), "")
        if not name:
            return
        existing = supporting_by_name.get(name)
        if existing is None:
            supporting_by_name[name] = candidate
            supporting_cast.append(candidate)
            return
        supporting_by_name[name] = _merge_mapping_non_empty(existing, candidate)
        idx = supporting_cast.index(existing)
        supporting_cast[idx] = supporting_by_name[name]

    for raw_character in _mapping_list(cast_expansion.get("new_characters")):
        _upsert_character(raw_character)

    primary_characters = {
        _non_empty_string(_mapping(merged.get("protagonist")).get("name"), ""): _mapping(merged.get("protagonist")),
        _non_empty_string(_mapping(merged.get("antagonist")).get("name"), ""): _mapping(merged.get("antagonist")),
    }
    for evolution in _mapping_list(cast_expansion.get("character_evolutions")):
        evo_map = _mapping(evolution)
        name = _non_empty_string(evo_map.get("name") or evo_map.get("character"), "")
        if not name:
            continue
        changes = _mapping(evo_map.get("changes"))
        if not changes:
            changes = {
                key: value
                for key, value in evo_map.items()
                if key not in {"name", "character"}
            }
        target = primary_characters.get(name) or supporting_by_name.get(name)
        if target is None:
            _upsert_character({"name": name, **changes})
            continue
        allow_role_change = name not in primary_characters
        sanitized_changes = _sanitize_character_evolution_changes(
            target,
            changes,
            allow_role_change=allow_role_change,
        )
        merged_target = _merge_mapping_non_empty(target, sanitized_changes)
        if name == primary_characters.get(name, {}).get("name"):
            if merged.get("protagonist", {}).get("name") == name:
                merged["protagonist"] = merged_target
            elif merged.get("antagonist", {}).get("name") == name:
                merged["antagonist"] = merged_target
        elif name in supporting_by_name:
            idx = supporting_cast.index(supporting_by_name[name])
            supporting_by_name[name] = merged_target
            supporting_cast[idx] = merged_target

    merged["supporting_cast"] = supporting_cast
    relationship_updates = _mapping_list(cast_expansion.get("relationship_updates"))
    if relationship_updates:
        conflict_map = list(_mapping_list(merged.get("conflict_map")))
        for update in relationship_updates:
            update_map = _mapping(update)
            left = _non_empty_string(update_map.get("character_a") or update_map.get("name"), "")
            right = _non_empty_string(update_map.get("character_b") or update_map.get("counterpart"), "")
            relation_type = _non_empty_string(update_map.get("type") or update_map.get("relationship_type"), "")
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
    for index, raw_rule in enumerate(_mapping_list(world_disclosure.get("new_rules_revealed")), start=1):
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
                    if re.search(r"[A-Za-z]", frontier_summary) else f"第{volume_number}卷世界边界更新"
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
) -> tuple[Any, UUID | None]:
    _max_attempts = 2  # try once, retry once on parse/validation failure

    last_llm_run_id: UUID | None = None
    for attempt in range(_max_attempts):
        completion = await complete_text(
            session,
            settings,
            LLMCompletionRequest(
                logical_role="planner",
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                fallback_response=_json_dumps(fallback_payload),
                prompt_template=f"planner_{logical_name}",
                prompt_version="1.0",
                project_id=project.id,
                workflow_run_id=workflow_run_id,
                step_run_id=step_run_id,
                metadata={
                    "project_slug": project.slug,
                    "artifact": logical_name,
                    "attempt": attempt + 1,
                },
            ),
        )
        last_llm_run_id = completion.llm_run_id
        # If the LLM call itself exhausted retries, complete_text flips
        # ``provider`` to "fallback".  For structural artifacts where the
        # fallback would silently corrupt downstream (e.g. per-volume
        # chapter outlines), abort immediately with a clear signal.
        if abort_on_fallback and completion.provider == "fallback":
            raise PlannerFallbackError(
                f"Planner artifact '{logical_name}' had to fall back after LLM retries "
                f"exhausted. Refusing to continue because downstream requires a real outline."
            )
        try:
            generated = _extract_json_payload(completion.content)
            payload = _merge_planning_payload(fallback_payload, generated)
            if validator is not None:
                validator(payload)
            return payload, last_llm_run_id
        except Exception as exc:
            if attempt < _max_attempts - 1:
                logger.warning(
                    "Planner artifact %s attempt %d failed parse/validation (%s: %s), retrying …",
                    logical_name,
                    attempt + 1,
                    type(exc).__name__,
                    exc,
                )
                continue
            if abort_on_fallback:
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
    protag_name = protag.get("name") or cast_protag.get("name") or (
        "Protagonist" if is_en else "主角"
    )
    protag_archetype = protag.get("archetype") or cast_protag.get("archetype") or ""
    protag_golden_finger = protag.get("golden_finger") or ""
    protag_goal = protag.get("external_goal") or cast_protag.get("goal") or ""

    return {
        "title": bs.get("title") or project.title,
        "tags": _string_list(bs.get("themes"))[:8] or (
            [project.genre] if project.genre else []
        ),
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
        project, book_spec, cast_spec, volume_plan,
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

    # Update project title if the LLM produced a better one
    new_title = brief_payload.get("title")
    if isinstance(new_title, str) and new_title.strip() and new_title.strip() != project.title:
        project.title = new_title.strip()

    # Store tags + blurb in project metadata for easy access
    project.metadata_json = {
        **(project.metadata_json or {}),
        "promotional_brief": {
            "tags": brief_payload.get("tags", []),
            "protagonist": brief_payload.get("protagonist", {}),
            "blurb": brief_payload.get("blurb", ""),
        },
    }

    return brief_payload


async def generate_novel_plan(
    session: AsyncSession,
    settings: AppSettings,
    project_slug: str,
    premise: str,
    *,
    requested_by: str = "system",
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

    try:
        premise_artifact = await import_planning_artifact(
            session,
            project_slug,
            PlanningArtifactCreate(
                artifact_type=ArtifactType.PREMISE,
                content={"premise": premise},
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

        # Generate character names via LLM up-front so every downstream
        # fallback (book_spec / world_spec / cast_spec) sees real, contextual
        # names instead of regex-extracted fragments or generic pool defaults.
        # If the LLM call fails, _generate_character_names falls back to the
        # curated genre pool — never to regex on premise.
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

        book_spec_fallback = _fallback_book_spec(project, premise, category_key=_category_key)
        # Override placeholder name with LLM-designed one so the LLM book_spec
        # call sees the same protagonist name in its fallback context.
        if isinstance(book_spec_fallback.get("protagonist"), dict):
            book_spec_fallback["protagonist"]["name"] = llm_protagonist_name
        current_step_name = "generate_book_spec"
        workflow_run.current_step = current_step_name
        book_system, book_user = _book_spec_prompts(project, premise, book_spec_fallback)
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
            output_ref={"artifact_id": str(book_artifact.id), "llm_run_id": str(llm_run_id) if llm_run_id else None},
        )
        step_order += 1

        world_spec_fallback = _fallback_world_spec(project, premise, book_spec_payload, category_key=_category_key)
        current_step_name = "generate_world_spec"
        workflow_run.current_step = current_step_name
        world_system, world_user = _world_spec_prompts(project, premise, book_spec_payload)
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
        world_artifact = await import_planning_artifact(
            session,
            project_slug,
            PlanningArtifactCreate(artifact_type=ArtifactType.WORLD_SPEC, content=world_spec_payload),
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
            output_ref={"artifact_id": str(world_artifact.id), "llm_run_id": str(llm_run_id) if llm_run_id else None},
        )
        step_order += 1

        cast_spec_fallback = _fallback_cast_spec(project, premise, book_spec_payload, world_spec_payload, category_key=_category_key, character_name_pool=character_name_pool)
        current_step_name = "generate_cast_spec"
        workflow_run.current_step = current_step_name
        cast_system, cast_user = _cast_spec_prompts(project, book_spec_payload, world_spec_payload)
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
            output_ref={"artifact_id": str(cast_artifact.id), "llm_run_id": str(llm_run_id) if llm_run_id else None},
        )
        step_order += 1

        # ── Act Plan: macro narrative structure for long novels ──
        hierarchy = compute_linear_hierarchy(max(project.target_chapters, 1))
        act_plan_payload: list[dict[str, Any]] | None = None
        if hierarchy["act_count"] > 1 and project.target_chapters > settings.pipeline.act_plan_threshold:
            act_plan_fallback = _fallback_act_plan(project, book_spec_payload, cast_spec_payload, world_spec_payload)
            current_step_name = "generate_act_plan"
            workflow_run.current_step = current_step_name
            act_system, act_user = _act_plan_prompts(project, book_spec_payload, world_spec_payload, cast_spec_payload)
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
                PlanningArtifactCreate(artifact_type=ArtifactType.ACT_PLAN, content={"acts": act_plan_payload}),
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
                output_ref={"artifact_id": str(act_artifact.id), "llm_run_id": str(llm_run_id) if llm_run_id else None},
            )
            step_order += 1

        volume_plan_fallback = _fallback_volume_plan(project, book_spec_payload, cast_spec_payload, world_spec_payload, category_key=_category_key)
        current_step_name = "generate_volume_plan"
        workflow_run.current_step = current_step_name
        volume_system, volume_user = _volume_plan_prompts(
            project,
            book_spec_payload,
            world_spec_payload,
            cast_spec_payload,
            act_plan=act_plan_payload,
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
            validator=parse_volume_plan_input,
        )
        if llm_run_id is not None:
            llm_run_ids.append(llm_run_id)
        volume_artifact = await import_planning_artifact(
            session,
            project_slug,
            PlanningArtifactCreate(artifact_type=ArtifactType.VOLUME_PLAN, content=volume_plan_payload),
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
            output_ref={"artifact_id": str(volume_artifact.id), "llm_run_id": str(llm_run_id) if llm_run_id else None},
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
                critical_findings = [f for f in plan_validation.findings if f.severity == "critical"]
                if critical_findings and isinstance(volume_plan_payload, list):
                    try:
                        repair_notes = "\n".join(
                            f"- {f.message}" + (f" ({f.suggestion})" if f.suggestion else "")
                            for f in critical_findings
                        )
                        repair_system, repair_user = _volume_plan_prompts(
                            project, book_spec_payload, world_spec_payload, cast_spec_payload,
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
                            PlanningArtifactCreate(artifact_type=ArtifactType.VOLUME_PLAN, content=volume_plan_payload),
                        )
                    except Exception:
                        logger.warning("Plan auto-repair failed; continuing with original plan", exc_info=True)

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

        for vol_entry in normalized_vp:
            vol_num = int(vol_entry.get("volume_number", 1))
            vol_ch_count = int(vol_entry.get("chapter_count_target", 10))

            # Extract this volume's fallback chapters
            vol_fallback_chapters = [
                ch for ch in outline_fallback.get("chapters", [])
                if ch.get("volume_number") == vol_num
            ]
            vol_fallback = {"batch_name": f"volume-{vol_num}-outline", "chapters": vol_fallback_chapters}

            current_step_name = f"generate_volume_{vol_num}_outline"
            workflow_run.current_step = current_step_name

            vol_outline_system, vol_outline_user = _volume_outline_prompts(
                project,
                book_spec_payload,
                cast_spec_payload,
                normalized_vp,
                vol_entry,
            )
            vol_outline_payload, llm_run_id = await _generate_structured_artifact(
                session,
                settings,
                project=project,
                logical_name=f"volume_{vol_num}_chapter_outline",
                system_prompt=vol_outline_system,
                user_prompt=vol_outline_user,
                fallback_payload=vol_fallback,
                workflow_run_id=workflow_run.id,
                # Partial/missing volume outlines corrupt the global chapter
                # sequence (observed 2026-04-17: "missing chapters [151..350]").
                # Fail fast so the pipeline surfaces the real LLM failure
                # rather than materialising a silent fallback.
                abort_on_fallback=True,
            )
            if llm_run_id is not None:
                llm_run_ids.append(llm_run_id)

            # Normalize chapter numbers to global sequence.
            # If the LLM response was truncated mid-array (MiniMax hitting
            # max_tokens), _extract_json_payload will have salvaged the
            # complete chapters but we might be short of vol_ch_count.
            # Pad from the fallback outline so the global sequence has no
            # gaps (otherwise pipelines.py detect_chapter_sequence_gaps
            # aborts materialization downstream).
            vol_chapters = vol_outline_payload.get("chapters", []) if isinstance(vol_outline_payload, dict) else []
            if len(vol_chapters) < vol_ch_count:
                missing = vol_ch_count - len(vol_chapters)
                logger.warning(
                    "Volume %d outline returned %d/%d chapters — padding last %d from fallback.",
                    vol_num,
                    len(vol_chapters),
                    vol_ch_count,
                    missing,
                )
                pad = copy.deepcopy(vol_fallback_chapters[-missing:])
                vol_chapters = list(vol_chapters) + pad
                if isinstance(vol_outline_payload, dict):
                    vol_outline_payload["chapters"] = vol_chapters
            for idx, ch in enumerate(vol_chapters):
                ch["volume_number"] = vol_num
                ch["chapter_number"] = chapter_offset + idx
            all_outline_chapters.extend(vol_chapters)

            # Save per-volume artifact
            vol_outline_artifact = await import_planning_artifact(
                session,
                project_slug,
                PlanningArtifactCreate(
                    artifact_type=ArtifactType.VOLUME_CHAPTER_OUTLINE,
                    content=vol_outline_payload,
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
                output_ref={"artifact_id": str(vol_outline_artifact.id), "llm_run_id": str(llm_run_id) if llm_run_id else None},
            )
            step_order += 1
            chapter_offset += vol_ch_count

        # Merge into combined CHAPTER_OUTLINE_BATCH for backward compatibility
        outline_payload = {"batch_name": "auto-generated-full-outline", "chapters": all_outline_chapters}
        outline_artifact = await import_planning_artifact(
            session,
            project_slug,
            PlanningArtifactCreate(artifact_type=ArtifactType.CHAPTER_OUTLINE_BATCH, content=outline_payload),
        )
        artifact_records.append(
            PlanningArtifactRecord(
                artifact_type=ArtifactType.CHAPTER_OUTLINE_BATCH,
                artifact_id=outline_artifact.id,
                version_no=outline_artifact.version_no,
            )
        )

        # ── Promotional brief: title refinement + tags + protagonist intro + blurb ──
        current_step_name = "generate_promotional_brief"
        workflow_run.current_step = current_step_name
        step_order += 1
        _normalized_vp = volume_plan_payload if isinstance(volume_plan_payload, list) else _mapping_list(volume_plan_payload.get("volumes"))
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

        workflow_run.current_step = "completed"
        workflow_run.status = WorkflowStatus.COMPLETED.value
        workflow_run.metadata_json = {
            **workflow_run.metadata_json,
            "artifact_ids": {record.artifact_type.value: str(record.artifact_id) for record in artifact_records},
            "llm_run_ids": [str(item) for item in llm_run_ids],
        }
        await session.flush()

        outline_chapters = outline_payload.get("chapters", []) if isinstance(outline_payload, dict) else []
        volume_count = len(volume_plan_payload) if isinstance(volume_plan_payload, list) else len(volume_plan_payload.get("volumes", []))
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
        await create_workflow_step_run(
            session,
            workflow_run_id=workflow_run.id,
            step_name=current_step_name,
            step_order=step_order,
            status=WorkflowStatus.FAILED,
            error_message=str(exc),
        )
        await session.flush()
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

    try:
        # ── Premise ──
        premise_artifact = await import_planning_artifact(
            session, project_slug,
            PlanningArtifactCreate(artifact_type=ArtifactType.PREMISE, content={"premise": premise}),
        )
        artifact_records.append(PlanningArtifactRecord(
            artifact_type=ArtifactType.PREMISE, artifact_id=premise_artifact.id, version_no=premise_artifact.version_no,
        ))
        await create_workflow_step_run(session, workflow_run_id=workflow_run.id, step_name=current_step_name, step_order=step_order, status=WorkflowStatus.COMPLETED, output_ref={"artifact_id": str(premise_artifact.id)})
        step_order += 1

        # ── Character names ──
        character_name_pool = await _generate_character_names(
            session, settings, genre=project.genre, sub_genre=project.sub_genre or "",
            language=project.language, premise=premise, book_spec={},
            workflow_run_id=workflow_run.id, project_id=project.id,
        )
        llm_protagonist_name = (
            _mapping(character_name_pool.get("protagonist")).get("name")
            or _genre_name_pool(
                project.genre,
                language=project.language,
                seed_text=_project_name_seed(project, premise),
            )["protagonist"]["name"]
        )

        # ── BookSpec ──
        book_spec_fallback = _fallback_book_spec(project, premise, category_key=_category_key)
        if isinstance(book_spec_fallback.get("protagonist"), dict):
            book_spec_fallback["protagonist"]["name"] = llm_protagonist_name
        current_step_name = "generate_book_spec"
        workflow_run.current_step = current_step_name
        book_system, book_user = _book_spec_prompts(project, premise, book_spec_fallback)
        book_spec_payload, llm_run_id = await _generate_structured_artifact(
            session, settings, project=project, logical_name="book_spec",
            system_prompt=book_system, user_prompt=book_user,
            fallback_payload=book_spec_fallback, workflow_run_id=workflow_run.id,
        )
        if llm_run_id is not None:
            llm_run_ids.append(llm_run_id)
        book_artifact = await import_planning_artifact(session, project_slug, PlanningArtifactCreate(artifact_type=ArtifactType.BOOK_SPEC, content=book_spec_payload))
        artifact_records.append(PlanningArtifactRecord(artifact_type=ArtifactType.BOOK_SPEC, artifact_id=book_artifact.id, version_no=book_artifact.version_no))
        await create_workflow_step_run(session, workflow_run_id=workflow_run.id, step_name=current_step_name, step_order=step_order, status=WorkflowStatus.COMPLETED, output_ref={"artifact_id": str(book_artifact.id)})
        step_order += 1

        # ── WorldSpec ──
        world_spec_fallback = _fallback_world_spec(project, premise, book_spec_payload, category_key=_category_key)
        current_step_name = "generate_world_spec"
        workflow_run.current_step = current_step_name
        world_system, world_user = _world_spec_prompts(project, premise, book_spec_payload)
        world_spec_payload, llm_run_id = await _generate_structured_artifact(
            session, settings, project=project, logical_name="world_spec",
            system_prompt=world_system, user_prompt=world_user,
            fallback_payload=world_spec_fallback, workflow_run_id=workflow_run.id,
            validator=parse_world_spec_input,
        )
        if llm_run_id is not None:
            llm_run_ids.append(llm_run_id)
        world_artifact = await import_planning_artifact(session, project_slug, PlanningArtifactCreate(artifact_type=ArtifactType.WORLD_SPEC, content=world_spec_payload))
        artifact_records.append(PlanningArtifactRecord(artifact_type=ArtifactType.WORLD_SPEC, artifact_id=world_artifact.id, version_no=world_artifact.version_no))
        await create_workflow_step_run(session, workflow_run_id=workflow_run.id, step_name=current_step_name, step_order=step_order, status=WorkflowStatus.COMPLETED, output_ref={"artifact_id": str(world_artifact.id)})
        step_order += 1

        # ── CastSpec ──
        cast_spec_fallback = _fallback_cast_spec(project, premise, book_spec_payload, world_spec_payload, category_key=_category_key, character_name_pool=character_name_pool)
        current_step_name = "generate_cast_spec"
        workflow_run.current_step = current_step_name
        cast_system, cast_user = _cast_spec_prompts(project, book_spec_payload, world_spec_payload)
        cast_spec_payload, llm_run_id = await _generate_structured_artifact(
            session, settings, project=project, logical_name="cast_spec",
            system_prompt=cast_system, user_prompt=cast_user,
            fallback_payload=cast_spec_fallback, workflow_run_id=workflow_run.id,
            validator=parse_cast_spec_input,
        )
        if llm_run_id is not None:
            llm_run_ids.append(llm_run_id)
        cast_artifact = await import_planning_artifact(session, project_slug, PlanningArtifactCreate(artifact_type=ArtifactType.CAST_SPEC, content=cast_spec_payload))
        artifact_records.append(PlanningArtifactRecord(artifact_type=ArtifactType.CAST_SPEC, artifact_id=cast_artifact.id, version_no=cast_artifact.version_no))
        await create_workflow_step_run(session, workflow_run_id=workflow_run.id, step_name=current_step_name, step_order=step_order, status=WorkflowStatus.COMPLETED, output_ref={"artifact_id": str(cast_artifact.id)})
        step_order += 1

        # ── VolumePlan ──
        volume_plan_fallback = _fallback_volume_plan(project, book_spec_payload, cast_spec_payload, world_spec_payload, category_key=_category_key)
        current_step_name = "generate_volume_plan"
        workflow_run.current_step = current_step_name
        vp_system, vp_user = _volume_plan_prompts(project, book_spec_payload, world_spec_payload, cast_spec_payload)
        volume_plan_payload, llm_run_id = await _generate_structured_artifact(
            session, settings, project=project, logical_name="volume_plan",
            system_prompt=vp_system, user_prompt=vp_user,
            fallback_payload=volume_plan_fallback, workflow_run_id=workflow_run.id,
            validator=parse_volume_plan_input,
        )
        if llm_run_id is not None:
            llm_run_ids.append(llm_run_id)
        volume_artifact = await import_planning_artifact(session, project_slug, PlanningArtifactCreate(artifact_type=ArtifactType.VOLUME_PLAN, content=volume_plan_payload))
        artifact_records.append(PlanningArtifactRecord(artifact_type=ArtifactType.VOLUME_PLAN, artifact_id=volume_artifact.id, version_no=volume_artifact.version_no))
        await create_workflow_step_run(session, workflow_run_id=workflow_run.id, step_name=current_step_name, step_order=step_order, status=WorkflowStatus.COMPLETED, output_ref={"artifact_id": str(volume_artifact.id)})
        step_order += 1

        # ── Promotional brief: title + tags + protagonist + blurb ──
        current_step_name = "generate_promotional_brief"
        workflow_run.current_step = current_step_name
        step_order += 1
        _norm_vp = volume_plan_payload if isinstance(volume_plan_payload, list) else _mapping_list(volume_plan_payload.get("volumes"))
        await _generate_promotional_brief(
            session, settings, project=project,
            book_spec=book_spec_payload, cast_spec=cast_spec_payload,
            volume_plan=_norm_vp, workflow_run_id=workflow_run.id,
            step_order=step_order, llm_run_ids=llm_run_ids,
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

        volume_count = len(volume_plan_payload) if isinstance(volume_plan_payload, list) else len(volume_plan_payload.get("volumes", []))
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
        await create_workflow_step_run(session, workflow_run_id=workflow_run.id, step_name=current_step_name, step_order=step_order, status=WorkflowStatus.FAILED, error_message=str(exc))
        await session.flush()
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
    requested_by: str = "system",
) -> VolumePlanningResult:
    """Phase B of progressive planning: plan a single volume.

    Steps: cast expansion → world disclosure → volume outline.
    Uses prior volume's writing feedback to evolve characters and world.
    """
    project = await get_project_by_slug(session, project_slug)
    if project is None:
        raise ValueError(f"Project '{project_slug}' was not found.")

    # Resolve category once for downstream fallback functions
    _category = resolve_novel_category(project.genre, project.sub_genre)
    _category_key: str | None = _category.key if _category else None

    # Find this volume's entry
    vol_entry: dict[str, Any] | None = None
    for v in _mapping_list(volume_plan):
        if int(v.get("volume_number", 0)) == volume_number:
            vol_entry = v
            break
    if vol_entry is None:
        raise ValueError(f"Volume {volume_number} not found in volume plan")

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
                project, book_spec, world_spec, cast_spec, vol_entry,
                prior_feedback_summary=prior_feedback_summary,
            )
            cast_exp_payload, llm_run_id = await _generate_structured_artifact(
                session, settings, project=project,
                logical_name=f"volume_{volume_number}_cast_expansion",
                system_prompt=cast_exp_system, user_prompt=cast_exp_user,
                fallback_payload={"new_characters": [], "character_evolutions": [], "relationship_updates": []},
                workflow_run_id=workflow_run.id,
            )
            if llm_run_id is not None:
                llm_run_ids.append(llm_run_id)
            new_characters_introduced = len(cast_exp_payload.get("new_characters", []))
            cast_exp_artifact = await import_planning_artifact(
                session, project_slug,
                PlanningArtifactCreate(artifact_type=ArtifactType.VOLUME_CAST_EXPANSION, content=cast_exp_payload),
            )
            artifact_records.append(PlanningArtifactRecord(
                artifact_type=ArtifactType.VOLUME_CAST_EXPANSION,
                artifact_id=cast_exp_artifact.id, version_no=cast_exp_artifact.version_no,
            ))
            effective_cast_spec = _merge_volume_cast_expansion_into_cast_spec(
                effective_cast_spec,
                cast_exp_payload,
            )
            # Validate and normalize the merged CastSpec immediately so
            # malformed role updates fail close to the merge point instead of
            # surfacing later during story-bible materialization.
            effective_cast_spec = parse_cast_spec_input(effective_cast_spec).model_dump(mode="json")
            if effective_cast_spec and effective_cast_spec != _mapping(cast_spec):
                merged_cast_artifact = await import_planning_artifact(
                    session,
                    project_slug,
                    PlanningArtifactCreate(artifact_type=ArtifactType.CAST_SPEC, content=effective_cast_spec),
                )
                artifact_records.append(PlanningArtifactRecord(
                    artifact_type=ArtifactType.CAST_SPEC,
                    artifact_id=merged_cast_artifact.id, version_no=merged_cast_artifact.version_no,
                ))
            await create_workflow_step_run(session, workflow_run_id=workflow_run.id, step_name=current_step_name, step_order=step_order, status=WorkflowStatus.COMPLETED, output_ref={"artifact_id": str(cast_exp_artifact.id)})
            step_order += 1

        # ── World Disclosure ──
        current_step_name = "volume_world_disclosure"
        workflow_run.current_step = current_step_name
        world_disc_system, world_disc_user = _volume_world_disclosure_prompts(
            project, world_spec, vol_entry,
            prior_world_snapshot=prior_world_snapshot,
        )
        world_disc_payload, llm_run_id = await _generate_structured_artifact(
            session, settings, project=project,
            logical_name=f"volume_{volume_number}_world_disclosure",
            system_prompt=world_disc_system, user_prompt=world_disc_user,
            fallback_payload={"new_locations": [], "new_rules_revealed": [], "faction_movements": [], "frontier_summary": ""},
            workflow_run_id=workflow_run.id,
        )
        if llm_run_id is not None:
            llm_run_ids.append(llm_run_id)
        world_disc_artifact = await import_planning_artifact(
            session, project_slug,
            PlanningArtifactCreate(artifact_type=ArtifactType.VOLUME_WORLD_DISCLOSURE, content=world_disc_payload),
        )
        artifact_records.append(PlanningArtifactRecord(
            artifact_type=ArtifactType.VOLUME_WORLD_DISCLOSURE,
            artifact_id=world_disc_artifact.id, version_no=world_disc_artifact.version_no,
        ))
        effective_world_spec = _merge_volume_world_disclosure_into_world_spec(
            effective_world_spec,
            world_disc_payload,
            volume_number=volume_number,
        )
        if effective_world_spec and effective_world_spec != _mapping(world_spec):
            merged_world_artifact = await import_planning_artifact(
                session,
                project_slug,
                PlanningArtifactCreate(artifact_type=ArtifactType.WORLD_SPEC, content=effective_world_spec),
            )
            artifact_records.append(PlanningArtifactRecord(
                artifact_type=ArtifactType.WORLD_SPEC,
                artifact_id=merged_world_artifact.id, version_no=merged_world_artifact.version_no,
            ))
        await create_workflow_step_run(session, workflow_run_id=workflow_run.id, step_name=current_step_name, step_order=step_order, status=WorkflowStatus.COMPLETED, output_ref={"artifact_id": str(world_disc_artifact.id)})
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
            session, project.id, volume_number,
        )
        # Restrict the fallback to the single volume being replanned — the
        # fallback numbers chapters globally across whatever volume_plan it
        # receives, so passing only the target volume entry keeps numbering
        # anchored at ``chapter_number_offset``.
        single_volume_plan = [v for v in _mapping_list(volume_plan) if int(v.get("volume_number", 0) or 0) == volume_number]
        full_fallback = _fallback_chapter_outline_batch(
            project,
            book_spec,
            effective_cast_spec,
            single_volume_plan,
            category_key=_category_key,
            chapter_number_offset=chapter_number_offset,
        )
        vol_fallback_chapters = [ch for ch in full_fallback.get("chapters", []) if ch.get("volume_number") == volume_number]
        vol_fallback = {"batch_name": f"volume-{volume_number}-outline", "chapters": vol_fallback_chapters}

        vol_outline_system, vol_outline_user = _volume_outline_prompts(
            project, book_spec, effective_cast_spec, _mapping_list(volume_plan), vol_entry,
        )
        vol_outline_payload, llm_run_id = await _generate_structured_artifact(
            session, settings, project=project,
            logical_name=f"volume_{volume_number}_chapter_outline",
            system_prompt=vol_outline_system, user_prompt=vol_outline_user,
            fallback_payload=vol_fallback, workflow_run_id=workflow_run.id,
            abort_on_fallback=True,
        )
        if llm_run_id is not None:
            llm_run_ids.append(llm_run_id)

        vol_chapters = vol_outline_payload.get("chapters", []) if isinstance(vol_outline_payload, dict) else []
        # Pad from fallback if LLM truncation reduced the chapter count
        # (see sibling loop at line ~4900 for rationale).
        vol_ch_count = int(vol_entry.get("chapter_count_target", len(vol_chapters) or 1))
        if len(vol_chapters) < vol_ch_count and vol_fallback_chapters:
            missing = vol_ch_count - len(vol_chapters)
            logger.warning(
                "Volume %d outline returned %d/%d chapters — padding last %d from fallback.",
                volume_number,
                len(vol_chapters),
                vol_ch_count,
                missing,
            )
            pad = copy.deepcopy(vol_fallback_chapters[-missing:])
            vol_chapters = list(vol_chapters) + pad
            if isinstance(vol_outline_payload, dict):
                vol_outline_payload["chapters"] = vol_chapters
        # Force a contiguous, offset-safe chapter_number sequence. The LLM
        # output cannot be trusted to stay above ``chapter_number_offset``,
        # and fallback padding used to silently leak stale global numbers.
        for idx, ch in enumerate(vol_chapters):
            ch["volume_number"] = volume_number
            ch["chapter_number"] = chapter_number_offset + idx

        vol_outline_artifact = await import_planning_artifact(
            session, project_slug,
            PlanningArtifactCreate(artifact_type=ArtifactType.VOLUME_CHAPTER_OUTLINE, content=vol_outline_payload),
        )
        artifact_records.append(PlanningArtifactRecord(
            artifact_type=ArtifactType.VOLUME_CHAPTER_OUTLINE,
            artifact_id=vol_outline_artifact.id, version_no=vol_outline_artifact.version_no,
        ))
        await create_workflow_step_run(session, workflow_run_id=workflow_run.id, step_name=current_step_name, step_order=step_order, status=WorkflowStatus.COMPLETED, output_ref={"artifact_id": str(vol_outline_artifact.id)})
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
        await create_workflow_step_run(session, workflow_run_id=workflow_run.id, step_name=current_step_name, step_order=step_order, status=WorkflowStatus.FAILED, error_message=str(exc))
        await session.flush()
        raise
