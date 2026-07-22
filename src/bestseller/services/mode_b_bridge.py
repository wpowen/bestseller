"""Bridge between the Mode B dialogue orchestrator and the production pipeline.

Mode B (the "帮我写 N 章" conversational orchestrator) historically wrote
chapter markdown directly, bypassing every quality gate. That is the root
cause of "shell" books (template chapters, fake word counts). This bridge
lets the orchestrator drive the *real* ``run_chapter_pipeline`` for each
chapter and keeps ``progress.yaml`` in sync with database truth instead of
LLM self-reported numbers.

Responsibilities:
  * Resolve the Mode B package root ``output/ai-generated/{slug}/``.
  * Verify the project + chapter + scene cards exist in the database.
  * Run a single chapter through ``run_chapter_pipeline`` (gates + scoring).
  * Read back the authoritative word count / scores / verdict and project them
    into ``progress.yaml`` for filesystem orchestration, never trusting dialogue
    self-fill. PostgreSQL remains the canonical runtime source.
  * Map a blocked / requires-human-review outcome to ``REWRITE_CHAPTER``.

This module is intentionally thin: it composes existing services. It does
NOT generate prose itself and does NOT materialize planning artifacts (that
remains an explicit, auditable step via the ``workflow materialize-*``
commands).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

import yaml
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.infra.db.models import ChapterModel, SceneCardModel
from bestseller.domain.workflow import ChapterOutlineBatchInput
from bestseller.services.chapter_word_count_truth import (
    authoritative_zh_word_count,
)
from bestseller.services.pipelines import run_chapter_pipeline
from bestseller.services.projects import get_project_by_slug
from bestseller.settings import AppSettings

MODE_B_SUBDIR = "ai-generated"
MODE_B_FRAMEWORK_PACKAGE = "framework-package.yaml"


class ModeBBridgeError(RuntimeError):
    """Raised when the Mode B package cannot be driven through the pipeline."""


@dataclass(frozen=True)
class ModeBFrameworkPackage:
    """Validated planning payload consumed by the production framework."""

    slug: str
    meta: dict[str, Any]
    story_bible: dict[str, Any]
    outline_batch: ChapterOutlineBatchInput
    root: Path


def _load_yaml_mapping(path: Path, *, label: str) -> dict[str, Any]:
    if not path.is_file():
        raise ModeBBridgeError(f"Missing {label}: {path}")
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ModeBBridgeError(f"Invalid YAML in {label} '{path}': {exc}") from exc
    if not isinstance(payload, dict):
        raise ModeBBridgeError(f"{label} '{path}' must contain a YAML mapping")
    return payload


def _as_text(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if value is None:
        return ""
    return str(value).strip()


def _ordered_event_groups(
    events: Sequence[Mapping[str, Any]],
    *,
    group_count: int,
) -> list[list[dict[str, Any]]]:
    """Split mandatory events into contiguous hidden-node groups."""

    ordered = sorted(
        (dict(event) for event in events),
        key=lambda event: int(event.get("order") or 0),
    )
    if not ordered:
        return []
    count = max(1, min(int(group_count), len(ordered)))
    groups: list[list[dict[str, Any]]] = []
    start = 0
    for index in range(count):
        remaining = len(ordered) - start
        slots = count - index
        take = (remaining + slots - 1) // slots
        groups.append(ordered[start : start + take])
        start += take
    return groups


def _logic_contract_payload(contract: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "entry_state": dict(contract.get("input_state") or {}),
        "causal_chain": list(contract.get("causal_chain") or []),
        "mandatory_events": list(contract.get("mandatory_events") or []),
        "numeric_facts": list(contract.get("numeric_facts") or []),
        "state_transitions": list(contract.get("state_transitions") or []),
        "knowledge_boundaries": dict(contract.get("knowledge_boundaries") or {}),
        "cheap_solutions": dict(contract.get("cheap_solutions") or {}),
        "exit_state": dict(contract.get("exit_state") or {}),
        "seam_requirement": _as_text(contract.get("seam_requirement")),
        "chapter_end_change": _as_text(contract.get("chapter_end_change")),
        "anti_ai_focus": _as_text(contract.get("anti_ai_focus")),
    }


def _chapter_from_contract(
    contract: Mapping[str, Any],
    *,
    target_word_count: int,
    hidden_node_count: int,
    locked_identity_names: set[str] | None = None,
) -> dict[str, Any]:
    number = int(contract.get("chapter") or 0)
    if number <= 0:
        raise ModeBBridgeError("Every contract must declare a positive 'chapter' number")
    events = [
        event
        for event in (contract.get("mandatory_events") or [])
        if isinstance(event, Mapping)
    ]
    groups = _ordered_event_groups(events, group_count=hidden_node_count)
    if not groups:
        raise ModeBBridgeError(f"Chapter {number} has no mandatory_events")

    input_state = dict(contract.get("input_state") or {})
    exit_state = dict(contract.get("exit_state") or {})
    declared_participants = [
        str(item) for item in (input_state.get("participants") or []) if item
    ]
    participants = [
        item
        for item in declared_participants
        if locked_identity_names is None or item in locked_identity_names
    ]
    causal_chain = [str(item) for item in (contract.get("causal_chain") or []) if item]
    cheap_solutions = dict(contract.get("cheap_solutions") or {})
    knowledge_boundaries = dict(contract.get("knowledge_boundaries") or {})
    per_node_words = max(1, target_word_count // len(groups))
    scenes: list[dict[str, Any]] = []
    previous_outcome = ""
    for index, group in enumerate(groups, start=1):
        ids = [_as_text(event.get("id")) for event in group]
        outcomes = [_as_text(event.get("outcome")) for event in group]
        action_sequence = [
            f"{event_id}：{outcome}" if outcome else event_id
            for event_id, outcome in zip(ids, outcomes, strict=False)
            if event_id or outcome
        ]
        node_entry = (
            input_state
            if index == 1
            else {"carry_from_previous_node": previous_outcome}
        )
        node_exit = (
            exit_state
            if index == len(groups)
            else {"completed_outcomes": outcomes}
        )
        previous_outcome = outcomes[-1] if outcomes else previous_outcome
        scenes.append(
            {
                "scene_number": index,
                "scene_type": "development",
                "title": " / ".join(item for item in ids if item),
                "time_label": "·".join(
                    item
                    for item in (
                        _as_text(input_state.get("story_time")),
                        _as_text(input_state.get("location")),
                    )
                    if item
                ),
                "participants": participants,
                "purpose": {
                    "story": "；".join(action_sequence),
                    "emotion": _as_text(contract.get("anti_ai_focus")),
                },
                "entry_state": node_entry,
                "exit_state": node_exit,
                "action_sequence": action_sequence,
                "information_introduced": outcomes,
                "information_held_back": [
                    str(item)
                    for key, value in knowledge_boundaries.items()
                    if "must_not" in str(key)
                    for item in (value if isinstance(value, list) else [value])
                    if item
                ][:8],
                "forbidden_actions": [
                    f"{key}：{value}" for key, value in cheap_solutions.items()
                ],
                "hook_requirement": (
                    _as_text(contract.get("chapter_end_change"))
                    if index == len(groups)
                    else previous_outcome
                ),
                "target_word_count": (
                    target_word_count - per_node_words * (len(groups) - 1)
                    if index == len(groups)
                    else per_node_words
                ),
            }
        )

    first_cause = causal_chain[0] if causal_chain else _as_text(events[0].get("outcome"))
    last_result = causal_chain[-1] if causal_chain else _as_text(events[-1].get("outcome"))
    selected_effects = dict(contract.get("selected_effect_skills") or {})
    return {
        "chapter_number": number,
        "title": _as_text(contract.get("title")) or f"第{number}章",
        "chapter_goal": last_result or "完成本章状态变化",
        "opening_pressure": first_cause,
        "required_payoff": last_result,
        "tail_hook": _as_text(contract.get("chapter_end_change")),
        "opening_situation": "；".join(
            item
            for item in (
                _as_text(input_state.get("story_time")),
                _as_text(input_state.get("location")),
                first_cause,
            )
            if item
        ),
        "main_conflict": f"{first_cause}；人物必须付出可追踪代价才能得到：{last_result}",
        "hook_type": "concrete_state_change",
        "hook_description": _as_text(contract.get("chapter_end_change")),
        "target_emotion": "紧张" if "tension" in str(selected_effects) else "压力",
        "causal_contract": {
            "chapter_function": "chapter_first_state_transition",
            "pressure": first_cause,
            "protagonist_choice": "；".join(causal_chain[1:3]) or first_cause,
            "resistance": "；".join(
                [*causal_chain[3:-1], *[str(value) for value in cheap_solutions.values()]]
            ),
            "cost_or_tradeoff": "；".join(
                str(item) for item in (contract.get("state_transitions") or [])
            ),
            "gain_or_reveal": "；".join(
                _as_text(event.get("outcome")) for event in events
            ),
            "state_change": last_result,
            "next_reader_desire": _as_text(contract.get("chapter_end_change"))
            or last_result,
        },
        "event_cycle_contract": {
            "ordered_event_ids": [_as_text(event.get("id")) for event in events],
            "ordered_outcomes": [_as_text(event.get("outcome")) for event in events],
        },
        "chapter_event_role": "chapter_first_state_transition",
        "information_gap_mode": "reader_tracks_pov",
        "methodology_contract": {
            "selected_effect_skills": selected_effects,
            "single_primary_effect": selected_effects.get("primary"),
            "single_secondary_effect": selected_effects.get("secondary"),
            "anti_ai_focus": _as_text(contract.get("anti_ai_focus")),
        },
        "whole_chapter_logic_contract": _logic_contract_payload(contract),
        "selected_effect_skills": selected_effects,
        "location_refs": [_as_text(input_state.get("location"))],
        "key_reveals": [
            _as_text(event.get("outcome")) for event in events if event.get("outcome")
        ],
        "chapter_concrete_actions": [
            _as_text(event.get("id")) for event in events if event.get("id")
        ],
        "chapter_information_introduced": [
            _as_text(event.get("outcome")) for event in events if event.get("outcome")
        ],
        "chapter_information_held_back": [
            str(item)
            for key, value in knowledge_boundaries.items()
            if "must_not" in str(key)
            for item in (value if isinstance(value, list) else [value])
            if item
        ],
        "volume_number": 1,
        "target_word_count": target_word_count,
        "scenes": scenes,
    }


def load_mode_b_framework_package(
    slug: str,
    *,
    output_base_dir: str | Path = "output",
) -> ModeBFrameworkPackage:
    """Load story-bible inputs and convert all contracts into framework rows."""

    root = resolve_mode_b_root(slug, output_base_dir=output_base_dir)
    meta = _load_yaml_mapping(root / "meta.yaml", label="Mode B meta")
    if _as_text(meta.get("slug")) != slug:
        raise ModeBBridgeError(
            f"meta.yaml slug '{meta.get('slug')}' does not match requested slug '{slug}'"
        )
    package = _load_yaml_mapping(
        root / MODE_B_FRAMEWORK_PACKAGE,
        label="Mode B framework package",
    )
    story_bible = package.get("story_bible")
    if not isinstance(story_bible, dict):
        raise ModeBBridgeError(
            f"{MODE_B_FRAMEWORK_PACKAGE} must define a story_bible mapping"
        )
    contract_paths = sorted((root / "contracts").glob("ch-*.yaml"))
    target_chapters = int(meta.get("target_chapters") or 0)
    if len(contract_paths) != target_chapters:
        raise ModeBBridgeError(
            f"Expected {target_chapters} contracts for '{slug}', found {len(contract_paths)}"
        )
    target_words = int(((meta.get("words_per_chapter") or {}).get("target")) or 2800)
    hidden_nodes = int(meta.get("internal_beats_per_chapter") or 3)
    cast_spec = story_bible.get("cast_spec")
    locked_identity_names: set[str] = set()
    if isinstance(cast_spec, dict):
        for key in ("protagonist", "antagonist"):
            character = cast_spec.get(key)
            if isinstance(character, dict) and character.get("name"):
                locked_identity_names.add(str(character["name"]))
        for character in cast_spec.get("supporting_cast") or []:
            if isinstance(character, dict) and character.get("name"):
                locked_identity_names.add(str(character["name"]))
    chapters = [
        _chapter_from_contract(
            _load_yaml_mapping(path, label=f"chapter contract {path.name}"),
            target_word_count=target_words,
            hidden_node_count=hidden_nodes,
            locked_identity_names=locked_identity_names or None,
        )
        for path in contract_paths
    ]
    actual_numbers = [int(chapter["chapter_number"]) for chapter in chapters]
    expected_numbers = list(range(1, target_chapters + 1))
    if actual_numbers != expected_numbers:
        raise ModeBBridgeError(
            f"Chapter contracts must be contiguous {expected_numbers}; got {actual_numbers}"
        )
    batch = ChapterOutlineBatchInput.model_validate(
        {"batch_name": f"{slug}-chapter-first", "chapters": chapters}
    )
    return ModeBFrameworkPackage(
        slug=slug,
        meta=meta,
        story_bible=dict(story_bible),
        outline_batch=batch,
        root=root,
    )


@dataclass(frozen=True)
class ModeBChapterOutcome:
    """Result of driving one Mode B chapter through the pipeline."""

    chapter_number: int
    passed: bool
    requires_human_review: bool
    word_count: int
    verdict: str | None
    block_codes: tuple[str, ...]
    output_path: str | None
    next_state: str  # COMMIT_CHAPTER | REWRITE_CHAPTER
    workflow_run_id: str | None = None
    repair_items: tuple[dict[str, Any], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "chapter_number": self.chapter_number,
            "passed": self.passed,
            "requires_human_review": self.requires_human_review,
            "word_count": self.word_count,
            "verdict": self.verdict,
            "block_codes": list(self.block_codes),
            "output_path": self.output_path,
            "next_state": self.next_state,
            "workflow_run_id": self.workflow_run_id,
            "repair_items": [dict(item) for item in self.repair_items],
        }


def resolve_mode_b_root(
    slug: str,
    *,
    output_base_dir: str | Path = "output",
) -> Path:
    """Return ``output/ai-generated/{slug}/``."""

    return Path(output_base_dir) / MODE_B_SUBDIR / slug


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def sync_progress_yaml(
    slug: str,
    outcome: ModeBChapterOutcome,
    *,
    output_base_dir: str | Path = "output",
    final_scores: dict[str, float] | None = None,
) -> Path | None:
    """Project PostgreSQL pipeline truth into the Mode B checkpoint YAML.

    Updates the chapter entry with the authoritative word count, scores,
    state and the next orchestrator state. Returns the path written, or
    ``None`` when ``progress.yaml`` is absent (nothing to sync).
    """

    root = resolve_mode_b_root(slug, output_base_dir=output_base_dir)
    path = root / "progress.yaml"
    if not path.is_file():
        return None

    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ModeBBridgeError(f"progress.yaml for '{slug}' is corrupt: {exc}") from exc
    if not isinstance(data, dict):
        raise ModeBBridgeError(f"progress.yaml for '{slug}' is not a mapping")

    chapters = data.setdefault("chapters", {})
    if not isinstance(chapters, dict):
        chapters = {}
        data["chapters"] = chapters

    key = f"{outcome.chapter_number:03d}"
    entry = chapters.get(key) if isinstance(chapters.get(key), dict) else {}
    entry["state"] = "committed" if outcome.passed else "rewriting"
    entry["word_count"] = outcome.word_count
    entry["verdict"] = outcome.verdict
    entry["block_codes"] = list(outcome.block_codes)
    entry["requires_human_review"] = outcome.requires_human_review
    entry["runtime_workflow_run_id"] = outcome.workflow_run_id
    if final_scores:
        entry["final_scores"] = final_scores
    if outcome.passed:
        entry["committed_at"] = _now_iso()
    chapters[key] = entry

    if outcome.repair_items:
        queue = data.get("repair_queue")
        if not isinstance(queue, list):
            queue = []
        existing_keys = {
            (
                str(item.get("runtime_workflow_run_id") or ""),
                int(item.get("affected_chapter") or 0),
                str(item.get("issue_type") or ""),
            )
            for item in queue
            if isinstance(item, dict)
        }
        for repair_item in outcome.repair_items:
            dedupe_key = (
                str(outcome.workflow_run_id or ""),
                int(repair_item.get("affected_chapter") or outcome.chapter_number),
                str(repair_item.get("issue_type") or "consistency_audit"),
            )
            if dedupe_key in existing_keys:
                continue
            queue.append(
                {
                    "id": f"R-{len(queue) + 1:03d}",
                    "created_at": _now_iso(),
                    "runtime_workflow_run_id": outcome.workflow_run_id,
                    "source_audit": repair_item.get("source_audit")
                    or f"milestone-ch-{outcome.chapter_number:03d}",
                    "issue_type": dedupe_key[2],
                    "affected_chapter": dedupe_key[1],
                    "description": str(repair_item.get("description") or ""),
                    "attempts": 0,
                    "status": "pending",
                }
            )
            existing_keys.add(dedupe_key)
        data["repair_queue"] = queue
        data["state"] = "DRAIN_REPAIR_QUEUE"
    else:
        data["state"] = outcome.next_state
    data["current_chapter"] = outcome.chapter_number
    data["last_updated"] = _now_iso()
    data["runtime_projection"] = {
        "schema_version": 1,
        "source": "postgresql",
        "workflow_run_id": outcome.workflow_run_id,
        "projected_at": data["last_updated"],
    }

    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    tmp.replace(path)
    return path


def enqueue_repair_item(
    slug: str,
    *,
    workflow_run_id: str,
    affected_chapter: int,
    issue_type: str,
    description: str,
    output_base_dir: str | Path = "output",
    source_audit: str | None = None,
) -> Path | None:
    """Append a milestone/consistency repair item to ``progress.yaml``.

    Long-book milestone consistency failures must block advancement and be
    tracked until healed. Returns the path written, or ``None`` when
    ``progress.yaml`` is absent.
    """

    root = resolve_mode_b_root(slug, output_base_dir=output_base_dir)
    path = root / "progress.yaml"
    if not path.is_file():
        return None

    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ModeBBridgeError(f"progress.yaml for '{slug}' is corrupt: {exc}") from exc
    if not isinstance(data, dict):
        raise ModeBBridgeError(f"progress.yaml for '{slug}' is not a mapping")

    queue = data.get("repair_queue")
    if not isinstance(queue, list):
        queue = []
    next_id = f"R-{len(queue) + 1:03d}"
    now = _now_iso()
    queue.append(
        {
            "id": next_id,
            "created_at": now,
            "runtime_workflow_run_id": workflow_run_id,
            "source_audit": source_audit or f"milestone-ch-{affected_chapter:03d}",
            "issue_type": issue_type,
            "affected_chapter": affected_chapter,
            "description": description,
            "attempts": 0,
            "status": "pending",
        }
    )
    data["repair_queue"] = queue
    # A pending repair item must stop forward progress.
    data["state"] = "DRAIN_REPAIR_QUEUE"
    data["last_updated"] = now
    data["runtime_projection"] = {
        "schema_version": 1,
        "source": "postgresql",
        "workflow_run_id": workflow_run_id,
        "projected_at": now,
    }

    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    tmp.replace(path)
    return path


async def _load_chapter_block_codes(
    session: AsyncSession,
    *,
    project_id: Any,
    chapter_number: int,
) -> tuple[ChapterModel | None, tuple[str, ...]]:
    chapter = await session.scalar(
        select(ChapterModel).where(
            ChapterModel.project_id == project_id,
            ChapterModel.chapter_number == chapter_number,
        )
    )
    if chapter is None:
        return None, ()
    metadata = dict(getattr(chapter, "metadata_json", None) or {})
    codes = metadata.get("auto_repair_last_block_codes") or []
    if not codes and metadata.get("production_block_code"):
        codes = [metadata["production_block_code"]]
    return chapter, tuple(str(c) for c in codes if c)


async def drive_mode_b_chapter(
    session: AsyncSession,
    settings: AppSettings,
    slug: str,
    chapter_number: int,
    *,
    requested_by: str = "mode-b-orchestrator",
    chapter_first: bool = True,
) -> ModeBChapterOutcome:
    """Run one Mode B chapter through the production pipeline with gates.

    Raises ``ModeBBridgeError`` when the project / chapter / scene cards are
    missing — the orchestrator must materialize planning artifacts first
    (``bestseller workflow materialize-story-bible / materialize-outline``).
    """

    project = await get_project_by_slug(session, slug)
    if project is None:
        raise ModeBBridgeError(
            f"Mode B project '{slug}' has no database record. Run "
            f"`bestseller project create {slug} ...` and "
            f"`bestseller workflow materialize-story-bible/outline {slug}` first."
        )

    chapter = await session.scalar(
        select(ChapterModel).where(
            ChapterModel.project_id == project.id,
            ChapterModel.chapter_number == chapter_number,
        )
    )
    if chapter is None:
        raise ModeBBridgeError(
            f"Chapter {chapter_number} not materialized for '{slug}'. "
            f"Run outline materialization before driving the pipeline."
        )

    scene_count = await session.scalar(
        select(SceneCardModel.id)
        .where(SceneCardModel.chapter_id == chapter.id)
        .limit(1)
    )
    if scene_count is None:
        raise ModeBBridgeError(
            f"Chapter {chapter_number} of '{slug}' has no scene cards. "
            f"Materialize the chapter outline before writing."
        )

    result = await run_chapter_pipeline(
        session,
        settings,
        slug,
        chapter_number,
        requested_by=requested_by,
        export_markdown=True,
        chapter_first=chapter_first,
    )

    chapter_after, block_codes = await _load_chapter_block_codes(
        session,
        project_id=project.id,
        chapter_number=chapter_number,
    )

    word_count = int(getattr(chapter_after, "current_word_count", 0) or 0)
    if word_count <= 0 and result.output_path:
        try:
            body = Path(result.output_path).read_text(encoding="utf-8")
            word_count = authoritative_zh_word_count(
                body, language=str(getattr(project, "language", None) or "zh-CN")
            )
        except OSError:
            word_count = 0

    passed = (
        not result.requires_human_review
        and result.final_verdict == "pass"
        and not block_codes
    )
    next_state = "COMMIT_CHAPTER" if passed else "REWRITE_CHAPTER"

    return ModeBChapterOutcome(
        chapter_number=chapter_number,
        passed=passed,
        requires_human_review=bool(result.requires_human_review),
        word_count=word_count,
        verdict=result.final_verdict,
        block_codes=block_codes,
        output_path=result.output_path,
        next_state=next_state,
        workflow_run_id=str(result.workflow_run_id),
        repair_items=tuple(
            dict(item)
            for item in (
                (getattr(chapter_after, "metadata_json", None) or {}).get(
                    "milestone_repair_items"
                )
                or []
            )
            if isinstance(item, Mapping)
        ),
    )


__all__ = [
    "MODE_B_SUBDIR",
    "MODE_B_FRAMEWORK_PACKAGE",
    "ModeBBridgeError",
    "ModeBChapterOutcome",
    "ModeBFrameworkPackage",
    "drive_mode_b_chapter",
    "enqueue_repair_item",
    "resolve_mode_b_root",
    "load_mode_b_framework_package",
    "sync_progress_yaml",
]
