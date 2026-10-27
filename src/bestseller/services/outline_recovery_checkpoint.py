"""Lossless outline recovery evidence; never an outline approval mechanism."""

from __future__ import annotations

from collections.abc import Callable, Collection, Mapping, Sequence
import copy
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.domain.workflow import ChapterOutlineBatchInput
from bestseller.infra.db.models import ProjectModel
from bestseller.services.prompt_source_policy import content_hash, outline_dependency_hash


def recovery_binding(metadata: Mapping, volume_number: int, window: list[int]) -> dict:
    snapshot = metadata.get("book_design_snapshot") or {}
    revision = metadata.get("derived_input_revision") or {}
    return {
        "volume_number": volume_number,
        "window": window,
        "dependency_hash": outline_dependency_hash(metadata) or content_hash({}),
        "source_snapshot_hash": snapshot.get("source_hash")
        or metadata.get("book_design_snapshot_hash"),
        "revision_key": revision.get("candidate_hash") or "",
    }


def outline_window(payload: Mapping) -> list[int] | None:
    chapters = payload.get("chapters")
    if not isinstance(chapters, list) or not chapters:
        return None
    numbers = [c.get("chapter_number") if isinstance(c, Mapping) else None for c in chapters]
    if any(type(n) is not int or n < 1 for n in numbers):
        return None
    if numbers != list(range(numbers[0], numbers[-1] + 1)):
        return None
    return [numbers[0], numbers[-1]]


def matching_checkpoint(record: Mapping, metadata: Mapping, volume_number: int) -> bool:
    payload = record.get("candidate")
    if not isinstance(payload, Mapping) or record.get("candidate_hash") != content_hash(payload):
        return False
    window = outline_window(payload)
    if not window or record.get("binding") != recovery_binding(metadata, volume_number, window):
        return False
    active = metadata.get("rolling_outline_plan") or {}
    if active and window != [active.get("window_start"), active.get("window_end")]:
        return False
    report = record.get("report")
    return (
        isinstance(report, Mapping)
        and report.get("graded_outline_hash") == content_hash(payload)
        and report.get("outline_repair_dependency_hash") == record["binding"]["dependency_hash"]
        and report.get(
            "outline_repair_source_snapshot_hash", record["binding"]["source_snapshot_hash"]
        )
        == record["binding"]["source_snapshot_hash"]
    )


def select_active_surgical_repair_source(
    *,
    best_payload: Mapping | None,
    best_report: Mapping | None,
    candidate_payload: Mapping | None,
    candidate_report: Mapping | None,
) -> tuple[dict | None, dict | None]:
    """Select a strictly paired, unapproved one-hop edit as working state only.

    A lower judge score cannot undo validated fixes on an active repair branch.
    This does not change the score-best checkpoint or approve either source.
    Missing/conflicting proof returns independent copies of the supplied best.
    """
    fallback = (
        copy.deepcopy(dict(best_payload)) if isinstance(best_payload, Mapping) else None,
        copy.deepcopy(dict(best_report)) if isinstance(best_report, Mapping) else None,
    )
    if not all(
        isinstance(value, Mapping)
        for value in (best_payload, best_report, candidate_payload, candidate_report)
    ):
        return fallback
    if any(
        report.get(key) is True
        for report in (best_report, candidate_report)
        for key in ("passed", "pass")
    ):
        return fallback
    if not any(candidate_report.get(key) is False for key in ("passed", "pass")):
        return fallback  # Unknown judgement is not an active rejected branch.
    window = outline_window(best_payload)
    if window is None or outline_window(candidate_payload) != window:
        return fallback
    if [chapter.get("volume_number", 1) for chapter in best_payload["chapters"]] != [
        chapter.get("volume_number", 1) for chapter in candidate_payload["chapters"]
    ]:
        return fallback
    metadata = candidate_payload.get("_meta")
    receipt = metadata.get("surgical_repair_receipt") if isinstance(metadata, Mapping) else None
    if not isinstance(receipt, Mapping):
        return fallback
    before, source_report = receipt.get("before_candidate"), receipt.get("source_report")
    run_id = receipt.get("llm_run_id")
    if (
        not isinstance(before, Mapping)
        or not isinstance(source_report, Mapping)
        or not isinstance(run_id, (str, UUID))
        or not str(run_id).strip()
    ):
        return fallback
    best_body = {key: value for key, value in best_payload.items() if key != "_meta"}
    before_body = {key: value for key, value in before.items() if key != "_meta"}
    candidate_body = {key: value for key, value in candidate_payload.items() if key != "_meta"}
    if content_hash(before_body) != content_hash(best_body):
        return fallback
    if receipt.get("after_candidate_hash") != content_hash(candidate_body):
        return fallback
    if candidate_report.get("graded_outline_hash") != content_hash(candidate_payload):
        return fallback
    # Known historical source_hash is accepted only when the exact-grade key
    # is absent and it hashes this supplied full parent. It cannot override a
    # conflicting grade or stand in for a world/snapshot dependency hash.
    parent_grade_hash = source_report.get("graded_outline_hash")
    if parent_grade_hash is None:
        parent_grade_hash = source_report.get("source_hash")
    if parent_grade_hash != content_hash(best_payload):
        return fallback
    return copy.deepcopy(dict(candidate_payload)), copy.deepcopy(dict(candidate_report))


def restore_active_surgical_repair_source(
    *,
    metadata: Mapping,
    volume_number: int,
    best_payload: Mapping | None,
    best_report: Mapping | None,
) -> tuple[dict | None, dict | None]:
    """Restore a matching producer branch, or a strict legacy one-hop edit."""
    fallback = (
        copy.deepcopy(dict(best_payload)) if isinstance(best_payload, Mapping) else None,
        copy.deepcopy(dict(best_report)) if isinstance(best_report, Mapping) else None,
    )
    if isinstance(best_report, Mapping) and any(
        best_report.get(key) is True for key in ("passed", "pass")
    ):
        return fallback
    buckets = metadata.get("outline_commercial_judge_checkpoints")
    if not isinstance(buckets, Mapping):
        return fallback
    active_pairs = []
    for bucket in buckets.values():
        branch = bucket.get("active_repair_branch") if isinstance(bucket, Mapping) else None
        if _matching_active_repair_branch(branch, metadata, volume_number, best_payload):
            active_pairs.append(
                (copy.deepcopy(branch["candidate"]), copy.deepcopy(branch["report"]))
            )
            continue
        latest = bucket.get("latest_candidate") if isinstance(bucket, Mapping) else None
        if not isinstance(latest, Mapping) or not matching_checkpoint(
            latest, metadata, volume_number
        ):
            continue
        pair = select_active_surgical_repair_source(
            best_payload=best_payload,
            best_report=best_report,
            candidate_payload=latest["candidate"],
            candidate_report=latest["report"],
        )
        if pair[0] == latest["candidate"] and pair[1] == latest["report"]:
            active_pairs.append(pair)
    if len(active_pairs) == 1:
        return active_pairs[0]
    # Ambiguous matching buckets have no proven temporal ordering; do not
    # choose by score, dict order or a made-up "latest" timestamp.
    return fallback


def _matching_active_repair_branch(
    record: Mapping | None,
    metadata: Mapping,
    volume_number: int,
    best_payload: Mapping | None,
) -> bool:
    """Validate the persisted producer marker, never infer approval from it."""
    if not isinstance(record, Mapping) or not isinstance(best_payload, Mapping):
        return False
    if not matching_checkpoint(record, metadata, volume_number):
        return False
    if record.get("producer") != "surgical-active-branch-v1":
        return False
    if record.get("root_best_candidate_hash") != content_hash(best_payload):
        return False
    report = record["report"]
    if any(report.get(key) is True for key in ("passed", "pass")) or not any(
        report.get(key) is False for key in ("passed", "pass")
    ):
        return False
    edges = record.get("lineage_edges")
    depth = record.get("lineage_depth")
    if (
        not isinstance(edges, list)
        or not edges
        or len(edges) > 8
        or type(depth) is not int
        or depth < len(edges)
    ):
        return False
    previous = None
    for edge in edges:
        if not isinstance(edge, Mapping) or not all(
            isinstance(edge.get(key), str) and edge[key].strip()
            for key in (
                "parent_candidate_hash",
                "candidate_hash",
                "parent_body_hash",
                "after_body_hash",
                "source_report_hash",
                "llm_run_id",
                "workflow_run_id",
            )
        ):
            return False
        if previous is not None and edge["parent_candidate_hash"] != previous:
            return False
        previous = edge["candidate_hash"]
    if (
        depth == len(edges)
        and edges[0]["parent_candidate_hash"] != record["root_best_candidate_hash"]
    ):
        return False
    edge = edges[-1]
    payload = record["candidate"]
    meta = payload.get("_meta")
    receipt = meta.get("surgical_repair_receipt") if isinstance(meta, Mapping) else None
    if not isinstance(receipt, Mapping) or not isinstance(receipt.get("before_candidate"), Mapping):
        return False
    source_report = receipt.get("source_report")
    if not isinstance(source_report, Mapping):
        return False
    source_hash = source_report.get("graded_outline_hash")
    if source_hash is None:
        source_hash = source_report.get("source_hash")
    before_body = {k: v for k, v in receipt["before_candidate"].items() if k != "_meta"}
    after_body = {k: v for k, v in payload.items() if k != "_meta"}
    return (
        edge["candidate_hash"] == record["candidate_hash"]
        and edge["parent_candidate_hash"] == source_hash
        and edge["parent_body_hash"] == content_hash(before_body)
        and edge["after_body_hash"]
        == content_hash(after_body)
        == receipt.get("after_candidate_hash")
        and edge["source_report_hash"] == content_hash(source_report)
        and edge["llm_run_id"] == str(receipt.get("llm_run_id"))
        and edge["workflow_run_id"] == record.get("workflow_run_id")
        and record.get("parent_candidate_hash") == edge["parent_candidate_hash"]
    )


def _produce_active_repair_branch(
    *,
    best_record: Mapping | None,
    previous_branch: Mapping | None,
    candidate_record: Mapping,
    metadata: Mapping,
    volume_number: int,
) -> dict | None:
    """Issue a marker only for a verified child of this binding's active parent."""
    if not isinstance(best_record, Mapping) or not matching_checkpoint(
        best_record, metadata, volume_number
    ):
        return None
    if not matching_checkpoint(candidate_record, metadata, volume_number):
        return None
    root = best_record["candidate"]
    previous_valid = _matching_active_repair_branch(previous_branch, metadata, volume_number, root)
    parent = previous_branch if previous_valid else best_record
    chosen, report = select_active_surgical_repair_source(
        best_payload=parent["candidate"],
        best_report=parent["report"],
        candidate_payload=candidate_record["candidate"],
        candidate_report=candidate_record["report"],
    )
    if (
        chosen != candidate_record["candidate"]
        or report != candidate_record["report"]
        or candidate_record["candidate_hash"] == parent["candidate_hash"]
        or not str(candidate_record.get("workflow_run_id") or "").strip()
    ):
        return None
    receipt = chosen["_meta"]["surgical_repair_receipt"]
    edge = {
        "parent_candidate_hash": parent["candidate_hash"],
        "candidate_hash": candidate_record["candidate_hash"],
        "parent_body_hash": content_hash(
            {k: v for k, v in parent["candidate"].items() if k != "_meta"}
        ),
        "after_body_hash": receipt["after_candidate_hash"],
        "source_report_hash": content_hash(receipt["source_report"]),
        "llm_run_id": str(receipt["llm_run_id"]),
        "workflow_run_id": str(candidate_record["workflow_run_id"]),
    }
    edges = list(previous_branch["lineage_edges"]) if previous_valid else []
    return {
        **copy.deepcopy(dict(candidate_record)),
        "producer": "surgical-active-branch-v1",
        "root_best_candidate_hash": best_record["candidate_hash"],
        "parent_candidate_hash": parent["candidate_hash"],
        "lineage_depth": previous_branch["lineage_depth"] + 1 if previous_valid else 1,
        "lineage_edges": copy.deepcopy([*edges, edge][-8:]),
    }


async def checkpoint_outline_candidate(
    session: AsyncSession,
    *,
    project: ProjectModel,
    workflow_run_id: UUID,
    volume_number: int,
    candidate: Mapping,
    report: Mapping | None = None,
) -> None:
    from sqlalchemy import select

    from bestseller.infra.db.models import WorkflowRunModel
    from bestseller.services.planner import (
        _prefer_outline_repair_baseline,
        _restore_persisted_outline_repair_baseline,
    )

    nested = getattr(session, "in_nested_transaction", None)
    if callable(nested) and nested():
        from bestseller.services.budget import ExecutionBudgetError

        raise ExecutionBudgetError("execution_transaction_not_releasable")
    window = outline_window(candidate)
    if window is None:
        raise ValueError("outline recovery checkpoint requires an exact contiguous window")
    metadata = copy.deepcopy(dict(project.metadata_json or {}))
    binding = recovery_binding(metadata, volume_number, window)
    digest = content_hash(candidate)
    if report is not None and (
        report.get("graded_outline_hash") != digest
        or report.get("outline_repair_dependency_hash") != binding["dependency_hash"]
    ):
        raise ValueError("outline recovery verdict does not match candidate/source")
    records = dict(metadata.get("outline_commercial_judge_checkpoints") or {})
    key = content_hash(binding)
    bucket = dict(records.get(key) or {})
    # Seed a legacy one-hop latest before a pending child overwrites latest.
    # Otherwise a restart after the child's judge would lose its proven parent.
    previous_latest = bucket.get("latest_candidate")
    if isinstance(previous_latest, Mapping) and not _matching_active_repair_branch(
        bucket.get("active_repair_branch"), metadata, volume_number,
        (bucket.get("best") or {}).get("candidate"),
    ):
        seeded = _produce_active_repair_branch(
            best_record=bucket.get("best"), previous_branch=None,
            candidate_record=previous_latest, metadata=metadata, volume_number=volume_number,
        )
        if seeded is not None:
            bucket["active_repair_branch"] = seeded
    record = {
        "candidate": copy.deepcopy(dict(candidate)),
        "candidate_hash": digest,
        "binding": binding,
        "workflow_run_id": str(workflow_run_id),
        "report": copy.deepcopy(dict(report)) if report is not None else None,
        "judgement_status": "judged" if report is not None else "pending",
    }
    bucket["latest_candidate"] = record
    if report is not None:
        best = bucket.get("best") or {}
        if not matching_checkpoint(best, metadata, volume_number):
            best = {}
        best_payload, best_report = best.get("candidate"), best.get("report")
        if best_payload is None:
            prior_payload, prior_report = _restore_persisted_outline_repair_baseline(metadata)
            if prior_payload and outline_window(prior_payload) == window and prior_report:
                best_payload, best_report = prior_payload, prior_report
        chosen, verdict = _prefer_outline_repair_baseline(
            best_payload=best_payload,
            best_report=best_report,
            candidate_payload=candidate,
            candidate_report=report,
        )
        # Legacy summaries have no exact grade digest. Do not invent one.
        if verdict.get("graded_outline_hash") == content_hash(chosen):
            origin = (
                best if best and content_hash(best["candidate"]) == content_hash(chosen) else record
            )
            best_record = {
                **origin,
                "candidate": chosen,
                "candidate_hash": content_hash(chosen),
                "report": verdict,
            }
            bucket["best"] = best_record
            metadata.update(
                {
                    "outline_commercial_repair_baseline": chosen,
                    "outline_commercial_repair_baseline_kind": "lossless-v1",
                    "outline_repair_dependency_hash": binding["dependency_hash"],
                    "outline_commercial_repair_baseline_revision_key": binding["revision_key"],
                    "outline_commercial_repair_directives": list(
                        verdict.get("repair_directives") or []
                    )[:8],
                    "outline_commercial_last_failure": {
                        "workflow_run_id": best_record["workflow_run_id"],
                        "volume_number": volume_number,
                        "baseline_revision_key": binding["revision_key"],
                        "recovery_baseline_score": verdict.get("overall_score"),
                        "recovery_blocking_codes": [
                            i.get("code") for i in verdict.get("blocking_issues", [])
                        ],
                    },
                }
            )
    if report is not None:
        previous_branch = bucket.get("active_repair_branch")
        active_branch = _produce_active_repair_branch(
            best_record=bucket.get("best"), previous_branch=previous_branch,
            candidate_record=record, metadata=metadata, volume_number=volume_number,
        )
        if active_branch is not None:
            bucket["active_repair_branch"] = active_branch
        elif not _matching_active_repair_branch(
            previous_branch, metadata, volume_number, (bucket.get("best") or {}).get("candidate")
        ):
            bucket.pop("active_repair_branch", None)
    records[key] = bucket
    metadata["outline_commercial_judge_checkpoints"] = records
    project.metadata_json = metadata
    workflow = await session.scalar(
        select(WorkflowRunModel).where(WorkflowRunModel.id == workflow_run_id)
    )
    if workflow is not None:
        workflow.metadata_json = {
            **(workflow.metadata_json or {}),
            "outline_repair_dependency_hash": binding["dependency_hash"],
            "outline_repair_source_snapshot_hash": binding["source_snapshot_hash"],
            "outline_repair_window": window,
        }
    # Commit independently of subsequent judge transport, patch parsing or pauses.
    await session.flush()
    await session.commit()


def reconstruct_judged_batches(
    *,
    artifacts: Sequence[Any],
    allowed_source_ids: Collection,
    report: Mapping,
    metadata: Mapping,
    workflow_metadata: Mapping,
    volume_number: int,
    producer_validator: Callable[[dict[str, Any]], Mapping[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """Rebuild only retained same-run batches; missing bytes produce no recovery."""
    active = metadata.get("rolling_outline_plan") or {}
    window = [active.get("window_start"), active.get("window_end")]
    if any(type(n) is not int for n in window) or window[0] < 1 or window[1] < window[0]:
        return None
    binding = recovery_binding(metadata, volume_number, window)
    dependency = binding["dependency_hash"]
    if (
        workflow_metadata.get("outline_repair_dependency_hash") != dependency
        or report.get("outline_repair_dependency_hash") != dependency
    ):
        return None
    if binding["source_snapshot_hash"] and not metadata.get("book_design_snapshot"):
        # A legacy opaque dependency cannot prove a snapshot absent from its inputs.
        return None
    explicit_snapshot = workflow_metadata.get("outline_repair_source_snapshot_hash")
    if explicit_snapshot is not None and explicit_snapshot != binding["source_snapshot_hash"]:
        return None
    report_snapshot = report.get("outline_repair_source_snapshot_hash")
    if report_snapshot is not None and report_snapshot != binding["source_snapshot_hash"]:
        return None
    if binding["revision_key"] and any(
        workflow_metadata.get(key) != binding["revision_key"]
        for key in ("failure_baseline_revision_key", "derived_source_candidate_hash")
    ):
        return None
    latest = {}
    for artifact in artifacts:  # caller orders newest first
        payload = artifact.content
        if artifact.source_run_id not in allowed_source_ids or not isinstance(payload, Mapping):
            continue
        span = outline_window(payload)
        if span is None or span[0] < window[0] or span[1] > window[1]:
            continue
        if any(c.get("volume_number") != volume_number for c in payload["chapters"]):
            continue
        if any(
            c.get("source_snapshot_hash", binding["source_snapshot_hash"])
            != binding["source_snapshot_hash"]
            for c in payload["chapters"]
        ):
            continue
        latest.setdefault(tuple(span), payload)
    parts = [latest[key] for key in sorted(latest)]
    chapters = [copy.deepcopy(c) for p in parts for c in p["chapters"]]
    if not parts or outline_window({"chapters": chapters}) != window:
        return None
    try:
        combined = ChapterOutlineBatchInput.model_validate(
            {
                "batch_name": f"volume-{volume_number}-outline",
                "chapters": chapters,
            }
        ).model_dump(mode="json", by_alias=True)
    except ValueError:
        return None
    spans = [outline_window(p) for p in parts]
    reused = sum(bool((p.get("_meta") or {}).get("reused")) for p in parts)
    combined["_meta"] = {
        "batch_size": max(end - start + 1 for start, end in spans),
        "batch_count": len(parts),
        "batch_ranges": spans,
        "reused_batch_count": reused,
        "reused": reused == len(parts),
        "cross_batch_event_similarity_candidates": [
            c
            for p in parts
            for c in (p.get("_meta") or {}).get("cross_batch_event_similarity_candidates", [])
        ],
        "story_enhancer_distribution_candidates": [
            c
            for p in parts
            for c in (p.get("_meta") or {}).get("story_enhancer_distribution_candidates", [])
        ],
    }
    if content_hash(combined) == report.get("graded_outline_hash"):
        return combined
    if producer_validator is None:
        return None
    # Replay the original pure producer boundary, not a new generation or repair.
    # Validators may enrich contracts. Preserve the producer's exact batch metadata.
    batch_metadata = copy.deepcopy(combined["_meta"])
    raw = {"batch_name": combined["batch_name"], "chapters": chapters, "_meta": batch_metadata}
    from bestseller.services.planner import PlannerFallbackError

    try:
        validated = copy.deepcopy(dict(producer_validator(copy.deepcopy(raw))))
    except (ValueError, TypeError, PlannerFallbackError):
        return None
    validated["_meta"] = batch_metadata
    if outline_window(validated) != window:
        return None
    return validated if content_hash(validated) == report.get("graded_outline_hash") else None


async def reconstruct_with_historical_producer(
    session: AsyncSession,
    *,
    project: ProjectModel,
    artifacts: Sequence[Any],
    allowed_source_ids: Collection,
    report: Mapping,
    workflow_metadata: Mapping,
    volume_number: int,
) -> tuple[dict[str, Any] | None, dict]:
    """Read source versions effective before these batches; accept only exact replay."""
    from sqlalchemy import select

    from bestseller.domain.enums import ArtifactType
    from bestseller.infra.db.models import PlanningArtifactVersionModel
    from bestseller.services import planner
    from bestseller.services.pipelines import _expand_volume_plan_into_rolling_windows

    dated = [
        a.created_at
        for a in artifacts
        if (a.source_run_id in allowed_source_ids and getattr(a, "created_at", None) is not None)
    ]
    if not dated:
        return None, {}
    cutoff = min(dated)
    sources = {}
    for kind in (ArtifactType.CAST_SPEC, ArtifactType.VOLUME_PLAN):
        source = await session.scalar(
            select(PlanningArtifactVersionModel)
            .where(
                PlanningArtifactVersionModel.project_id == project.id,
                PlanningArtifactVersionModel.artifact_type == kind.value,
                PlanningArtifactVersionModel.scope_ref_id.is_(None),
                PlanningArtifactVersionModel.status == "approved",
                PlanningArtifactVersionModel.created_at <= cutoff,
            )
            .order_by(
                PlanningArtifactVersionModel.created_at.desc(),
                PlanningArtifactVersionModel.version_no.desc(),
            )
            .limit(1)
        )
        if source is None or not isinstance(source.content, Mapping):
            return None, {}
        sources[kind.value] = source
    plan = sources[ArtifactType.VOLUME_PLAN.value].content
    cast = sources[ArtifactType.CAST_SPEC.value].content
    schedule = project.metadata_json.get("rolling_outline_windows")
    if not isinstance(schedule, list) or not schedule:
        return None, {}
    from bestseller.services.rolling_outline import rolling_window_schedule_hash

    if project.metadata_json.get("rolling_outline_windows_hash") != rolling_window_schedule_hash(
        schedule
    ):
        return None, {}
    # Window partitioning can round 8 to 9 chapters. Infer only from existing
    # helper outputs and the exact persisted schedule, never from the target hash.
    entries = None
    for size in range(6, 11):
        try:
            windows = _expand_volume_plan_into_rolling_windows(
                planner._normalize_volume_plan_payload(copy.deepcopy(plan)),
                window_size=size,
            )
        except (TypeError, ValueError):
            return None, {}
        reconstructed_schedule = [
            {
                "volume_number": entry["volume_number"],
                "window_index": entry["rolling_window_index"],
                "window_start": entry["start_chapter_number"],
                "window_end": entry["end_chapter_number"],
            }
            for entry in windows
        ]
        if reconstructed_schedule == schedule:
            entries = windows
            break
    active = project.metadata_json.get("rolling_outline_plan") or {}
    entry = next(
        (
            item
            for item in entries or []
            if (
                item["volume_number"] == volume_number
                and item["start_chapter_number"] == active.get("window_start")
                and item["end_chapter_number"] == active.get("window_end")
            )
        ),
        None,
    )
    if entry is None:
        return None, {}
    existing_titles = await planner._fetch_existing_chapter_titles(
        session,
        project.id,
        exclude_volume_number=volume_number,
    )

    def validate(raw: dict[str, Any]) -> Mapping[str, Any]:
        return planner._validate_generated_volume_outline_or_raise(
            raw,
            project=project,
            logical_name=f"volume_{volume_number}_chapter_outline",
            volume_number=volume_number,
            expected_count=entry["chapter_count_target"],
            chapter_number_offset=entry["start_chapter_number"],
            cast_spec=copy.deepcopy(cast),
            volume_entry=copy.deepcopy(entry),
            existing_titles=existing_titles,
            strict_field_degeneracy=True,
        )

    recovered = reconstruct_judged_batches(
        artifacts=artifacts,
        allowed_source_ids=allowed_source_ids,
        report=report,
        metadata=project.metadata_json,
        workflow_metadata=workflow_metadata,
        volume_number=volume_number,
        producer_validator=validate,
    )
    receipt = {
        "producer_validation_replayed": True,
        "source_cutoff": cutoff.isoformat(),
        "source_artifacts": {
            kind: {
                "id": str(row.id),
                "version_no": row.version_no,
                "content_hash": content_hash(row.content),
            }
            for kind, row in sources.items()
        },
    }
    return recovered, receipt if recovered is not None else {}
