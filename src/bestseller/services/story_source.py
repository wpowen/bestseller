from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
import copy
import datetime as dt
import json
import re
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.domain.enums import ArtifactType
from bestseller.services.workflows import get_latest_planning_artifact
from bestseller.settings import AppSettings

_MINIMAL_COST_ACTIVE_SOURCE_KEYS = (
    "premise",
    "world_premise",
    "logline",
    "golden_finger",
    "writing_profile",
    "opening_quality_contract",
    "qimao_opening_contract",
    "public_emotion_kernel",
    "book_spec",
    "world_spec",
    "cast_spec",
    "protagonist",
    "concept_contract",
    "hook_card",
    "story_spine",
    "commercial_brief",
    "seriality_proof",
    "series_engine",
    "conception_artifacts",
)

_CLAUSE_BOUNDARY_RE = re.compile(r"([。！？；!?;\n]+)")


def _split_contract_clauses(text: str) -> list[str]:
    """Split source text without losing a byte of untouched contract text."""

    parts = _CLAUSE_BOUNDARY_RE.split(text)
    clauses: list[str] = []
    for index in range(0, len(parts), 2):
        body = parts[index]
        boundary = parts[index + 1] if index + 1 < len(parts) else ""
        clause = f"{body}{boundary}"
        if clause:
            clauses.append(clause)
    return clauses


def _iter_string_leaves(value: Any, path: tuple[str | int, ...]) -> Iterable[tuple[tuple[str | int, ...], str]]:
    if isinstance(value, str):
        yield path, value
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            yield from _iter_string_leaves(item, (*path, str(key)))
        return
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for index, item in enumerate(value):
            yield from _iter_string_leaves(item, (*path, index))


def _format_source_path(path: tuple[str | int, ...]) -> str:
    rendered = ""
    for item in path:
        if isinstance(item, int):
            rendered += f"[{item}]"
        else:
            rendered += ("." if rendered else "") + item
    return rendered


def _collect_minimal_cost_repairs(
    premise: str,
    metadata: Mapping[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Collect only clauses that violate the accepted minimal-cost contract.

    The returned source tree is a deep copy. Non-violating clauses are never
    handed to the model, so a repair cannot silently rewrite the book around
    the defect it was asked to remove.
    """

    from bestseller.services.anti_default_motif import (  # noqa: PLC0415
        contains_minimal_cost_violation,
    )

    sources: dict[str, Any] = {"canonical_premise": premise}
    for key in _MINIMAL_COST_ACTIVE_SOURCE_KEYS:
        if key in metadata:
            sources[key] = copy.deepcopy(metadata[key])

    repairs: list[dict[str, Any]] = []
    for path, text in _iter_string_leaves(sources, ()):
        clauses = _split_contract_clauses(text)
        for clause_index, clause in enumerate(clauses):
            if not contains_minimal_cost_violation(clause):
                continue
            repairs.append(
                {
                    "id": f"repair_{len(repairs) + 1}",
                    "path": list(path),
                    "path_text": _format_source_path(path),
                    "clause_index": clause_index,
                    "text": clause,
                    "context_before": clauses[clause_index - 1] if clause_index else "",
                    "context_after": (
                        clauses[clause_index + 1]
                        if clause_index + 1 < len(clauses)
                        else ""
                    ),
                }
            )
    return sources, repairs


def minimal_cost_source_contract_violations(
    project: Any,
    premise: str,
) -> list[dict[str, Any]]:
    """Return active source clauses that contradict a minimal-cost selection."""

    from bestseller.services.story_enhancers import resolve_cost_style  # noqa: PLC0415

    metadata = dict(getattr(project, "metadata_json", None) or {})
    if resolve_cost_style(metadata) != "minimal":
        return []
    _, repairs = _collect_minimal_cost_repairs(premise, metadata)
    return repairs


def _extract_json_object(text: str) -> dict[str, Any]:
    raw = str(text or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.IGNORECASE)
        raw = re.sub(r"\s*```$", "", raw)
    start = raw.find("{")
    end = raw.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("creation contract reconciler did not return a JSON object")
    payload = json.loads(raw[start : end + 1])
    if not isinstance(payload, dict):
        raise ValueError("creation contract reconciler returned a non-object payload")
    return payload


def _terminal_boundary(text: str) -> str:
    match = re.search(r"([。！？；!?;\n]+)$", text)
    return match.group(1) if match else ""


def _apply_minimal_cost_repairs(
    sources: dict[str, Any],
    requested_repairs: Sequence[Mapping[str, Any]],
    response_payload: Mapping[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    """Apply an exact, complete repair set and reject partial/model-drift output."""

    from bestseller.services.anti_default_motif import (  # noqa: PLC0415
        contains_minimal_cost_violation,
    )

    raw_repairs = response_payload.get("repairs")
    if not isinstance(raw_repairs, list):
        raise ValueError("creation contract reconciler omitted repairs")
    replacements: dict[str, str] = {}
    for item in raw_repairs:
        if not isinstance(item, Mapping):
            raise ValueError("creation contract reconciler emitted an invalid repair item")
        repair_id = str(item.get("id") or "").strip()
        replacement = str(item.get("replacement") or "").strip()
        if not repair_id or not replacement or repair_id in replacements:
            raise ValueError("creation contract reconciler emitted an incomplete repair item")
        replacements[repair_id] = replacement

    expected_ids = {str(item["id"]) for item in requested_repairs}
    if set(replacements) != expected_ids:
        missing = sorted(expected_ids - set(replacements))
        unexpected = sorted(set(replacements) - expected_ids)
        raise ValueError(
            "creation contract reconciler repair set mismatch: "
            f"missing={missing}, unexpected={unexpected}"
        )

    repaired_sources = copy.deepcopy(sources)
    changed_paths: list[str] = []
    for request in requested_repairs:
        repair_id = str(request["id"])
        original = str(request["text"])
        replacement = replacements[repair_id]
        if contains_minimal_cost_violation(replacement):
            raise ValueError(f"creation contract repair {repair_id} remained non-compliant")
        if _terminal_boundary(original) != _terminal_boundary(replacement):
            raise ValueError(
                f"creation contract repair {repair_id} changed its clause boundary"
            )

        path = tuple(request["path"])
        parent: Any = repaired_sources
        for part in path[:-1]:
            parent = parent[part]
        leaf_key = path[-1]
        leaf_text = str(parent[leaf_key])
        clauses = _split_contract_clauses(leaf_text)
        clause_index = int(request["clause_index"])
        if clauses[clause_index] != original:
            raise ValueError(f"creation contract repair {repair_id} source drifted")
        clauses[clause_index] = replacement
        parent[leaf_key] = "".join(clauses)
        changed_paths.append(str(request["path_text"]))

    return repaired_sources, sorted(set(changed_paths))


async def repair_minimal_cost_source_contract(
    session: AsyncSession,
    settings: AppSettings,
    project: Any,
    premise: str,
    *,
    repair_revision: str,
    workflow_run_id: UUID | None = None,
) -> tuple[str, dict[str, Any]]:
    """Reconcile an accepted source contract, then require foundation rebuild.

    This is a migration of the creation truth, not an outline sanitizer. Only
    violating clauses from active creation sources are editable; all other
    clauses remain byte-identical. The model must return a complete repair set,
    and the result is rejected unless the canonical protagonist and the
    minimal-cost contract both remain valid.
    """

    from bestseller.services.anti_default_motif import (  # noqa: PLC0415
        contains_minimal_cost_violation,
    )
    from bestseller.services.book_design import (  # noqa: PLC0415
        extract_creation_protagonist_name,
    )
    from bestseller.services.llm import (  # noqa: PLC0415
        LLMCompletionRequest,
        complete_text,
    )
    from bestseller.services.story_enhancers import resolve_cost_style  # noqa: PLC0415

    metadata = dict(getattr(project, "metadata_json", None) or {})
    if resolve_cost_style(metadata) != "minimal":
        return premise, {"status": "not_required", "reason": "cost_style_not_minimal"}

    sources, requested_repairs = _collect_minimal_cost_repairs(premise, metadata)
    if not requested_repairs:
        return premise, {"status": "not_required", "reason": "source_already_compliant"}

    repair_items = [
        {
            "id": item["id"],
            "source_path": item["path_text"],
            "violating_clause": item["text"],
            "context_before": item["context_before"],
            "context_after": item["context_after"],
        }
        for item in requested_repairs
    ]
    result = await complete_text(
        session,
        settings,
        LLMCompletionRequest(
            logical_role="editor",
            model_tier="strong",
            system_prompt=(
                # 2026-08-03：这条修复路径随母题警察退役已不可达（检测器恒返回
                # 无违规）。列举式禁令一并删除，避免它日后被重新接线时再次成为注入源。
                "你是书籍创建合同校正器。只修复输入中被标记的违规子句。"
                "用户已选择爽点优先的节奏：主角在两次胜利之间不应长期失能或进度清零。"
                "必须保留人物姓名、专有名词、核心机制、能力用途、上限、节奏和读者承诺；"
                "不得新增人物、身世、能力或世界设定。每个 id 只能返回一个替换子句，"
                "并保留原子句末尾标点。只输出 JSON。"
            ),
            user_prompt=json.dumps(
                {
                    "contract": "minimal_cost_creation_source_reconciliation.v1",
                    "repairs": repair_items,
                    "output_schema": {
                        "repairs": [{"id": "repair_1", "replacement": "替换后的完整子句"}]
                    },
                },
                ensure_ascii=False,
            ),
            fallback_response='{"repairs":[]}',
            prompt_template="creation_source_contract_reconciliation",
            prompt_version="1.0",
            project_id=getattr(project, "id", None),
            workflow_run_id=workflow_run_id,
            max_tokens_override=4096,
            metadata={"repair_revision": str(repair_revision or "")},
        ),
    )
    response_payload = _extract_json_object(result.content)
    repaired_sources, changed_paths = _apply_minimal_cost_repairs(
        sources,
        requested_repairs,
        response_payload,
    )
    repaired_premise = str(repaired_sources["canonical_premise"] or "").strip()
    canonical_name = extract_creation_protagonist_name({"premise": premise})
    if canonical_name and canonical_name not in repaired_premise:
        raise ValueError("creation contract repair removed the canonical protagonist")
    if not repaired_premise or contains_minimal_cost_violation(repaired_premise):
        raise ValueError("creation contract repair did not produce a compliant premise")

    for key in _MINIMAL_COST_ACTIVE_SOURCE_KEYS:
        if key in repaired_sources:
            metadata[key] = repaired_sources[key]
    metadata["premise"] = repaired_premise

    history = [
        dict(item)
        for item in (metadata.get("source_contract_repair_history") or [])
        if isinstance(item, Mapping)
    ]
    audit = {
        "status": "repaired",
        "repair_revision": str(repair_revision or "creation-source-repair-v1"),
        "repaired_at": dt.datetime.now(dt.UTC).isoformat(),
        "reason": "accepted_creation_contract_conflicted_with_selected_cost_style",
        "original_premise": premise,
        "repaired_premise": repaired_premise,
        "changed_active_paths": changed_paths,
        "repair_count": len(requested_repairs),
        "llm_run_id": str(result.llm_run_id) if result.llm_run_id else None,
        "requires_foundation_replan": True,
    }
    history.append(audit)
    metadata["source_contract_repair_history"] = history[-10:]
    metadata["source_contract_repair_revision"] = audit["repair_revision"]
    metadata["source_contract_repair_status"] = "foundation_replan_required"
    project.metadata_json = metadata
    return repaired_premise, audit


def premise_from_artifact(artifact: Any) -> str:
    """Extract a normalized premise from a planning artifact, if present."""

    content = getattr(artifact, "content", None)
    if not isinstance(content, Mapping):
        return ""
    return str(content.get("premise") or "").strip()


def premise_from_locked_design(metadata: Mapping[str, Any]) -> str:
    """Build the narrowest usable fallback from the immutable design snapshot."""

    if str(metadata.get("book_design_snapshot_status") or "") != "locked":
        return ""
    snapshot = metadata.get("book_design_snapshot")
    if not isinstance(snapshot, Mapping):
        return ""
    promise = str(snapshot.get("reader_promise") or "").strip()
    engine = str(snapshot.get("core_story_engine") or "").strip()
    if promise and engine and engine not in promise:
        return f"{promise}\n持续故事引擎：{engine}"
    return promise or engine


async def load_accepted_project_premise(
    session: AsyncSession,
    project: Any,
) -> str:
    """Return the canonical story source for every existing-project resume.

    Authority order is deliberately fixed: latest accepted premise artifact,
    persisted project premise, immutable design snapshot, then title. Runtime
    task envelopes are excluded because they are pre-materialization transport
    and may contain obsolete exploratory text.
    """

    artifact = await get_latest_planning_artifact(
        session,
        project_id=project.id,
        artifact_type=ArtifactType.PREMISE,
    )
    accepted = premise_from_artifact(artifact)
    if accepted:
        return accepted
    metadata = project.metadata_json or {}
    persisted = str(metadata.get("premise") or "").strip()
    if persisted:
        return persisted
    locked_design = premise_from_locked_design(metadata)
    if locked_design:
        return locked_design
    return str(project.title or "").strip()


__all__ = [
    "load_accepted_project_premise",
    "minimal_cost_source_contract_violations",
    "premise_from_artifact",
    "premise_from_locked_design",
    "repair_minimal_cost_source_contract",
]
