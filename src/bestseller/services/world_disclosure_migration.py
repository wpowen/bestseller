"""Source-bound frontier receipts and conservative, offline legacy repair.

No database/model access. Snapshots use PlanningArtifactVersionModel fields:
id, project_id, artifact_type, version_no, scope_ref_id, created_at, status,
content. Disclosure export envelopes additionally require volume_number resolved
from the approved volume-entry hash or workflow input, never guessed from the
history being repaired. Supply the ordered world history through the current
source version, approved disclosures, and canon/timeline/authored-history exports.
Missing predecessor evidence stays Unknown; only a single exact producer append
between consecutive approved versions is removable. Existing baseline history
is never inferred to be generated from its wording.
Ordinary producer timestamps must satisfy previous_world < disclosure <= world:
PostgreSQL transaction timestamps can be equal for disclosure and new WorldSpec.
Earlier sources require an explicit workflow/artifact/content-hash reuse receipt.

API: audit_world_disclosure_history(**snapshot); propose_world_disclosure_repair
(**snapshot); apply_world_disclosure_repair(proposal, expected_source_hash=...,
actor=..., evidence=..., **fresh_snapshot). Apply re-audits the exact snapshot
and returns an UNAPPROVED candidate for the parent's authorized persistence path.
Do not use import_planning_artifact (which auto-approves) for this candidate.
An old outline grade cannot approve this repaired source or a recovered outline.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import copy
from datetime import UTC, datetime
import hashlib
import json
import re
from typing import Any, TypedDict, Unpack

POLICY = "world-disclosure-frontier-provenance.v1"


class Snapshot(TypedDict):
    project_id: str
    world_versions: Sequence[Mapping[str, Any]]
    disclosures: Sequence[Mapping[str, Any]]
    canon_facts: Sequence[Any]
    timeline_events: Sequence[Any]
    authored_history: Sequence[Any]


def content_hash(value: object) -> str:
    """Canonical UTF-8 JSON hash; inputs must be JSON exports, not ORM objects."""
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def record_planned_frontier(
    world_spec: Mapping[str, Any], disclosure: Mapping[str, Any], *, volume_number: int
) -> dict[str, Any]:
    """Copy the world and record each semantic frontier once, retaining its source.

    Legacy receipts have no body hash: retain them and establish one semantic
    receipt, rather than infer equal facts from a shared summary/window alone.
    """
    result = copy.deepcopy(dict(world_spec))
    summary = disclosure.get("frontier_summary")
    if not isinstance(summary, str) or not summary.strip():
        return result
    meta = result.setdefault("_meta", {})
    if not isinstance(meta, dict):
        raise ValueError("WorldSpec _meta must be a mapping")
    receipts = meta.setdefault("planned_frontiers", {})
    if not isinstance(receipts, dict):
        raise ValueError("planned_frontiers must be a mapping")
    source_meta = disclosure.get("_meta", {})
    entry_hash = source_meta.get("volume_entry_hash") if isinstance(source_meta, Mapping) else None
    # Import/runtime policy metadata is not a new frontier. Keep the entry
    # window explicit, since identical goals can belong to different windows.
    body_hash = content_hash({key: value for key, value in disclosure.items() if key != "_meta"})
    key = f"{volume_number}:{entry_hash or body_hash}:{body_hash}"
    if key in receipts:
        return result
    receipts[key] = {
        "policy": POLICY,
        "epistemic_status": "planned",
        "historical_fact": False,
        "volume_number": volume_number,
        "volume_entry_hash": entry_hash,
        "disclosure_body_hash": body_hash,
        # Preserve the first complete producer/import source hash as evidence;
        # later metadata variants must not replace that original receipt.
        "disclosure_content_hash": content_hash(disclosure),
        "frontier_summary": summary.strip(),
    }
    return result


def _timestamp(row: Mapping[str, Any]) -> datetime | None:
    try:
        value = datetime.fromisoformat(str(row.get("created_at", "")).replace("Z", "+00:00"))
        # DB exports must specify timezone; guessing timezone is not provenance.
        return value.astimezone(UTC) if value.tzinfo else None
    except ValueError:
        return None


def _ref(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: copy.deepcopy(row.get(key))
        for key in (
            "id",
            "project_id",
            "artifact_type",
            "version_no",
            "scope_ref_id",
            "created_at",
            "status",
            "source_run_id",
            "volume_number",
        )
    } | {"content_hash": content_hash(row.get("content"))}


def _history(row: Mapping[str, Any]) -> list[Any] | None:
    content = row.get("content")
    if not isinstance(content, Mapping):
        return None
    history = content.get("history_key_events", [])
    return history if isinstance(history, list) else None


def _legacy_volume(event: object) -> int | None:
    # The old producer emitted exactly these two fields and chose language by
    # ASCII letters in the summary. No substrings, token matching or NLP guesses.
    if not isinstance(event, dict) or set(event) != {"event", "relevance"}:
        return None
    summary = event["event"]
    if not isinstance(summary, str) or not summary or summary != summary.strip():
        return None
    relevance = event["relevance"]
    if not isinstance(relevance, str):
        return None
    pattern = (
        r"Volume ([1-9]\d*) frontier update"
        if re.search(r"[A-Za-z]", summary)
        else r"第([1-9]\d*)卷世界边界更新"
    )
    match = re.fullmatch(pattern, relevance)
    return int(match[1]) if match else None


def _strings(value: object) -> set[str]:
    if isinstance(value, str):
        return {value}
    if isinstance(value, Mapping):
        return set().union(set(), *(_strings(v) for v in value.values()))
    if isinstance(value, (list, tuple)):
        return set().union(set(), *(_strings(v) for v in value))
    return set()


def audit_world_disclosure_history(
    *,
    project_id: str,
    world_versions: Sequence[Mapping[str, Any]],
    disclosures: Sequence[Mapping[str, Any]],
    canon_facts: Sequence[Any],
    timeline_events: Sequence[Any],
    authored_history: Sequence[Any],
) -> dict[str, Any]:
    """Audit exported evidence; never mutate source versions or knowledge.

    All knowledge exports are required, even when empty. Exact independent
    knowledge/authored event text protects a collision. A missing/gapped history
    or an ambiguous producer only supports Unknown, never automatic deletion.
    """
    if not world_versions:
        raise ValueError("At least the current WorldSpec export is required")
    worlds = sorted(world_versions, key=lambda row: row["version_no"])
    target = worlds[-1]
    if len({row["version_no"] for row in worlds}) != len(worlds):
        raise ValueError("Duplicate WorldSpec versions")
    for rows in (worlds, disclosures):
        ids = [row.get("id") for row in rows]
        if len(set(ids)) != len(ids):
            raise ValueError("Duplicate artifact identities in evidence export")
    for row in worlds:
        if (
            row.get("project_id") != project_id
            or row.get("artifact_type") != "world_spec"
            or row.get("scope_ref_id") != target.get("scope_ref_id")
            or not row.get("id")
            or _history(row) is None
        ):
            raise ValueError("WorldSpec exports must identify one project and scope")
    protected = _strings([canon_facts, timeline_events, authored_history])
    previous = worlds[0]
    origins: list[dict[str, Any] | None] = [None] * len(_history(previous) or [])
    for current in worlds[1:]:
        before, after = _history(previous) or [], _history(current) or []
        prev_time, cur_time = _timestamp(previous), _timestamp(current)
        consecutive = (
            current["version_no"] == previous["version_no"] + 1
            and previous.get("status") == current.get("status") == "approved"
            and prev_time is not None
            and cur_time is not None
            and prev_time < cur_time
        )
        if not consecutive or after[: len(before)] != before:
            origins = [None] * len(after)
        else:
            appended = after[len(before) :]
            additions: list[dict[str, Any] | None] = [None] * len(appended)
            if len(appended) == 1:
                event = appended[0]
                volume = _legacy_volume(event)
                matches = []
                if volume is not None and event["event"] not in protected:
                    for source in disclosures:
                        when = _timestamp(source)
                        content = source.get("content", {})
                        if (
                            source.get("project_id") == project_id
                            and source.get("artifact_type") == "volume_world_disclosure"
                            and source.get("status") == "approved"
                            and source.get("id")
                            and source.get("scope_ref_id") == current.get("scope_ref_id")
                            and when is not None
                            and when <= cur_time
                            and isinstance(content, Mapping)
                            and isinstance(content.get("frontier_summary"), str)
                            and content["frontier_summary"].strip() == event["event"]
                            and source.get("volume_number") == volume
                            and not isinstance(source.get("volume_number"), bool)
                            and content.get("volume_number", volume) == volume
                        ):
                            # PostgreSQL now() is transaction-scoped: the disclosure
                            # and its new WorldSpec can have identical timestamps.
                            # Older sources and cross-run reuse need an explicit
                            # artifact/hash reference, not a text-match inference.
                            if when <= prev_time or (
                                source.get("source_run_id")
                                and current.get("source_run_id")
                                and source["source_run_id"] != current["source_run_id"]
                            ):
                                reuse = source.get("reuse_receipts", [])
                                if (
                                    not current.get("source_run_id")
                                    or not isinstance(reuse, list)
                                    or not any(
                                        isinstance(ref, Mapping)
                                        and ref.get("workflow_run_id") == current["source_run_id"]
                                        and ref.get("artifact_id") == source["id"]
                                        and ref.get("content_hash") == content_hash(content)
                                        for ref in reuse
                                    )
                                ):
                                    continue
                            matches.append(source)
                if matches and len({content_hash(s["content"]) for s in matches}) == 1:
                    additions[0] = {
                        "volume_number": volume,
                        "producer": "planner._merge_volume_world_disclosure_into_world_spec",
                        "evidence_strength": "exact_producer_append_with_previous_version_diff",
                        "introduced_in": _ref(current),
                        "previous_version": _ref(previous),
                        "sources": [_ref(s) for s in matches],
                    }
            origins.extend(additions)
        previous = current
    findings = []
    for index, event in enumerate(_history(target) or []):
        if _legacy_volume(event) is None:
            continue
        origin = origins[index]
        if event["event"] in protected:
            origin = None
            reason = "independent_canon_timeline_or_authored_history"
        else:
            reason = "exact_producer_append" if origin else "insufficient_producer_provenance"
        findings.append(
            {
                "history_index": index,
                "event": copy.deepcopy(event),
                "classification": "legacy_generated_planning_note" if origin else "Unknown",
                "reason": reason,
                "provenance": origin,
            }
        )
    # Hash the entire evidence bundle: same source content with a different id,
    # approval status, disclosure or knowledge export must invalidate apply.
    evidence_hash = content_hash(
        {
            "project_id": project_id,
            "world_versions": worlds,
            "disclosures": list(disclosures),
            "canon_facts": list(canon_facts),
            "timeline_events": list(timeline_events),
            "authored_history": list(authored_history),
        }
    )
    return {
        "policy": POLICY,
        "source": _ref(target),
        "source_hash": content_hash(target["content"]),
        "evidence_hash": evidence_hash,
        "findings": findings,
        "removable_count": sum(f["provenance"] is not None for f in findings),
        "unknown_count": sum(f["classification"] == "Unknown" for f in findings),
    }


def propose_world_disclosure_repair(**snapshot: Unpack[Snapshot]) -> dict[str, Any]:
    """Build a deterministic corrected candidate; retain originals and receipts."""
    audit = audit_world_disclosure_history(**snapshot)
    target = max(snapshot["world_versions"], key=lambda row: row["version_no"])
    removed = [f for f in audit["findings"] if f["provenance"] is not None]
    content = copy.deepcopy(target["content"])
    if removed:
        indices = {f["history_index"] for f in removed}
        content["history_key_events"] = [
            e for i, e in enumerate(content["history_key_events"]) if i not in indices
        ]
        sources = {s["id"]: s for s in snapshot["disclosures"]}
        for finding in removed:
            provenance = finding["provenance"]
            for source in provenance["sources"]:
                source_content = sources[source["id"]]["content"]
                if content_hash(source_content) != source["content_hash"]:
                    raise ValueError("Producer source content hash changed")
                content = record_planned_frontier(
                    content,
                    source_content,
                    volume_number=provenance["volume_number"],
                )
        meta = content.setdefault("_meta", {})
        # Prior semantic approval is historical evidence, not approval of this
        # corrected content. Preserve it in the immutable original, plus archive.
        prior_review = {k: meta.pop(k) for k in list(meta) if "review" in k or "approval" in k}
        meta["world_disclosure_migration"] = {
            "policy": POLICY,
            "source": audit["source"],
            "source_hash": audit["source_hash"],
            "evidence_hash": audit["evidence_hash"],
            "archived_generated_entries": removed,
            "historical_review_metadata": prior_review,
            "review_requirement": "fresh_source_bound_semantic_review",
            "recovered_outline_review_requirement": "fresh_review_against_current_approved_world",
            "prior_outline_grades": "historical_only",
        }
        meta["source_bound_expansion"] = "derived_pending_review"
        meta["source_bound_expansion_review"] = "independent_semantic_review_required"
    candidate = {
        "project_id": snapshot["project_id"],
        "artifact_type": "world_spec",
        "scope_ref_id": target.get("scope_ref_id"),
        "status": "pending_review",
        "content": content,
        "supersedes": audit["source"],
        "requires_source_bound_review": True,
    }
    return {
        "audit": audit,
        "changed": bool(removed),
        "candidate": candidate,
        "candidate_hash": content_hash(candidate),
    }


def apply_world_disclosure_repair(
    proposal: Mapping[str, Any],
    *,
    expected_source_hash: str,
    actor: str,
    evidence: Mapping[str, Any],
    **fresh_snapshot: Unpack[Snapshot],
) -> dict[str, Any]:
    """Verify a reviewed proposal and return a candidate, never approve/persist.

    actor/evidence identify the actual operator and technical inspection. They
    are not interpreted as human or semantic approval. The parent must persist
    a NEW pending-review version under its concurrency/transaction controls and
    retain every original approved version; this pure API performs no DB write.
    """
    if not isinstance(actor, str) or not actor.strip() or not evidence:
        raise ValueError("Apply requires an operator and actual inspection evidence")
    fresh = propose_world_disclosure_repair(**fresh_snapshot)
    if expected_source_hash != fresh["audit"]["source_hash"]:
        raise ValueError("Source hash changed; inspect a fresh proposal")
    if content_hash(proposal) != content_hash(fresh):
        raise ValueError("Proposal/evidence changed; inspect a fresh proposal")
    return {
        **fresh,
        "applied": fresh["changed"],
        "persisted": False,
        "operator_receipt": {
            "actor": actor.strip(),
            "inspection_evidence": copy.deepcopy(dict(evidence)),
            "source_hash": expected_source_hash,
            "candidate_hash": fresh["candidate_hash"],
            "semantic_approval": False,
        },
    }
