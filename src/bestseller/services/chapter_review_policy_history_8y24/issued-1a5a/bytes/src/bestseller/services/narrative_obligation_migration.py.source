"""Source-bound graph obligations and pure legacy projection repair.

Only Clue/Payoff exports are accepted. This module never reads/writes a database
or changes Canon/Timeline. Applying a proposal archives every original row.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

SCHEMA = "narrative-obligation.v1"


def graph_content_hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def is_reference_obligation(value: Mapping[str, Any]) -> bool:
    metadata = value.get("metadata_json") or value.get("metadata") or value
    marker = metadata.get("narrative_obligation") if isinstance(metadata, Mapping) else None
    return value.get("status") == "reference" or (
        isinstance(marker, Mapping) and marker.get("kind") == "reference")


def reference_metadata(source: Any, *, path: str, volume_number: int) -> dict[str, Any]:
    return {"narrative_obligation": {"schema": SCHEMA, "kind": "reference",
        "producer": "rebuild_narrative_graph", "reason": "planning_text_has_no_explicit_event_or_due",
        "source": {"content_hash": graph_content_hash(source), "hash_scope": "normalized_volume_entry",
                   "path": path, "volume_number": volume_number}}}


def bind_explicit_obligation(source: Mapping[str, Any], path: Sequence[str | int]) -> dict[str, Any]:
    """Bind a structured, explicitly dated promise in a loaded approved artifact.

    Caller supplies the real artifact export, not an inferred approval. Source
    names/description alone never authorize a deadline or actual occurrence.
    """
    if (source.get("status") != "approved" or not source.get("id")
        or not source.get("project_id") or source.get("artifact_type") not in {
            "volume_plan", "chapter_outline_batch", "volume_chapter_outline"}):
        raise ValueError("An approved planning artifact is required.")
    record = source["content"]
    for key in path:
        record = record[key]
    if not isinstance(record, Mapping) or record.get("obligation_type") == "reference":
        raise ValueError("An explicit structured commitment is required.")
    due = next((record.get(key) for key in ("expected_use_by_chapter", "expected_payoff_by_chapter",
        "payoff_by_chapter", "due_chapter") if type(record.get(key)) is int), None)
    if due is None or due < 1 or not (record.get("item") or record.get("label")):
        raise ValueError("An explicit named promise and chapter deadline are required.")
    return {"schema": SCHEMA, "kind": "commitment", "due_chapter": due,
        "source": {"artifact_id": str(source["id"]), "project_id": str(source["project_id"]),
                   "status": "approved", "content_hash": graph_content_hash(source["content"]),
                   "path": list(path), "record": copy.deepcopy(dict(record)),
                   "record_hash": graph_content_hash(record)}}


def has_explicit_due(item: Mapping[str, Any], due: int | None) -> bool:
    metadata = item.get("metadata_json") or item.get("metadata") or item
    marker = metadata.get("narrative_obligation") if isinstance(metadata, Mapping) else None
    if not isinstance(marker, Mapping) or marker.get("schema") != SCHEMA or marker.get("kind") != "commitment":
        return False
    source = marker.get("source") or {}
    record = source.get("record")
    label = item.get("item") or item.get("name") or item.get("label")
    return (type(due) is int and marker.get("due_chapter") == due
        and source.get("status") == "approved" and bool(source.get("artifact_id"))
        and bool(source.get("project_id")) and bool(source.get("content_hash"))
        and isinstance(record, Mapping) and source.get("record_hash") == graph_content_hash(record)
        and label == (record.get("item") or record.get("label")))


def _when(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo is not None else None
    except (TypeError, ValueError):
        return None


def _seed(value: str) -> tuple[str | None, str]:
    match = re.match(r"^\s*\[\s*(S[\w-]{0,23})\s*\]\s*", value)
    return (match.group(1), value[match.end():].strip()) if match else (None, value.strip())


def _legacy_specs(content: Any, windows: Sequence[Mapping[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    """Fingerprint the old producer's exact projection, never linguistic overlap."""
    volumes = content if isinstance(content, list) else content.get("volumes", [])
    entries = {v.get("volume_number"): v for v in volumes if isinstance(v, Mapping)}
    clues, payoffs, seeds = [], [], {}
    for window in sorted(windows, key=lambda w: w["volume_number"]):
        number, first, last = window["volume_number"], window["first_chapter"], window["last_chapter"]
        entry = entries.get(number, {})
        planted = entry.get("foreshadowing_planted") or []
        # No approved volume entry: chapter-hook fallback has insufficient source
        # evidence in this audit and remains Unknown.
        if not isinstance(planted, list) or any(not isinstance(v, str) for v in planted):
            continue
        for raw in planted:
            seed, label = _seed(raw)
            code = f"clue-{len(clues) + 1:03d}"
            if seed:
                seeds[seed] = code
            clues.append({"clue_code": code, "label": label[:200], "description": label,
                "clue_type": "foreshadow", "planted_in_volume_number": number,
                "planted_in_chapter_number": first, "planted_in_scene_number": window.get("first_scene", 1),
                "expected_payoff_by_volume_number": number, "expected_payoff_by_chapter_number": last,
                "expected_payoff_by_scene_number": window.get("last_scene"),
                "actual_paid_off_chapter_number": None, "actual_paid_off_scene_number": None,
                "reveal_guard": "在对应 payoff 前不得完整揭露。", "status": "planted"})
        paid = entry.get("foreshadowing_paid_off") or []
        if not isinstance(paid, list) or any(not isinstance(v, str) for v in paid):
            continue
        for raw in paid:
            seed, label = _seed(raw)
            token = re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]+", "", label).lower()
            matched = seeds.get(seed)
            if not matched:
                matched = next((c["clue_code"] for c in clues
                    if (ct := re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]+", "", c["label"]).lower())
                    and token and (ct in token or token in ct)), None)
            payoffs.append({"payoff_code": f"payoff-{len(payoffs) + 1:03d}", "label": label[:200],
                "description": label, "target_volume_number": number, "target_chapter_number": last,
                "target_scene_number": window.get("last_scene"), "actual_chapter_number": last,
                "actual_scene_number": window.get("last_scene"), "status": "paid_off",
                "_source_clue_code": matched})
            for clue in clues:
                if clue["clue_code"] == matched:
                    clue.update(status="resolved", actual_paid_off_chapter_number=last,
                                actual_paid_off_scene_number=window.get("last_scene"))
    return {**{("clues", c["clue_code"]): c for c in clues},
            **{("payoffs", p["payoff_code"]): p for p in payoffs}}


def propose_legacy_graph_repair(*, project_id: str, rows: Sequence[Mapping[str, Any]],
    approved_sources: Sequence[Mapping[str, Any]], windows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Dry run. Exact old producer matches become candidates; all else Unknown.

    Rows are full JSON exports: {table: clues|payoffs, row: {...all columns...}}.
    Windows describe the chapters/scenes actually materialized at old build time,
    not the full planned volume. Approval/timestamp/project evidence is mandatory.
    """
    originals = copy.deepcopy(list(rows))
    by_code = {str(item["row"].get("clue_code")): item["row"].get("id")
               for item in rows if item.get("table") == "clues"}
    sources = [s for s in approved_sources if str(s.get("project_id")) == str(project_id)
        and s.get("artifact_type") == "volume_plan" and s.get("status") == "approved"
        and s.get("id") and _when(s.get("created_at"))]
    prepared = [(s, _legacy_specs(s["content"], windows)) for s in sources]
    results, candidates = [], copy.deepcopy(originals)
    for index, item in enumerate(originals):
        table, row = item.get("table"), item.get("row", {})
        code = row.get("clue_code" if table == "clues" else "payoff_code")
        outcome = {"table": table, "row_id": str(row.get("id")), "classification": "unknown"}
        if table not in {"clues", "payoffs"} or str(row.get("project_id")) != str(project_id):
            raise ValueError("Only this project's Clue/Payoff rows can be migrated.")
        if is_reference_obligation(row):
            outcome["classification"] = "already_reference"
        elif not (row.get("metadata_json") or row.get("metadata")) and (created := _when(row.get("created_at"))):
            matches = []
            for source, specs in prepared:
                expected = specs.get((table, code))
                if not expected or _when(source["created_at"]) > created:
                    continue
                expected = dict(expected)
                source_code = expected.pop("_source_clue_code", None)
                if table == "payoffs":
                    expected["source_clue_id"] = by_code.get(source_code) if source_code else None
                if all(row.get(k) == v for k, v in expected.items()):
                    matches.append(source)
            if matches:
                latest = max(_when(s["created_at"]) for s in matches)
                matches = [s for s in matches if _when(s["created_at"]) == latest]
            if len(matches) == 1:
                source = matches[0]
                receipt = {"schema": SCHEMA, "kind": "reference", "producer": "rebuild_narrative_graph",
                    "reason": "exact_legacy_producer_inferred_event_and_due",
                    "original_row": row, "original_row_hash": graph_content_hash(row),
                    "source": {"artifact_id": str(source["id"]), "content_hash": graph_content_hash(source["content"]),
                               "hash_scope": "full_artifact_content"}}
                candidates[index]["row"]["status"] = "reference"
                metadata_key = "metadata" if "metadata" in row and "metadata_json" not in row else "metadata_json"
                candidates[index]["row"][metadata_key] = {"narrative_obligation": receipt}
                outcome.update(classification="repair_candidate", source=receipt["source"],
                               original_row_hash=receipt["original_row_hash"])
        results.append(outcome)
    return {"schema": SCHEMA, "project_id": str(project_id), "requires_operator_review": True,
        "original_rows": originals, "candidate_rows": candidates, "audit": results,
        "expected_rows_hash": graph_content_hash(originals),
        "source_hashes": {str(s["id"]): graph_content_hash(s["content"]) for s in sources},
        "source_records_hash": graph_content_hash(sources),
        "windows": copy.deepcopy(list(windows))}


def apply_legacy_graph_repair(rows: Sequence[Mapping[str, Any]], proposal: Mapping[str, Any], *,
    approved_sources: Sequence[Mapping[str, Any]], expected_source_hashes: Mapping[str, str],
    actor: str, evidence: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Pure operator apply; parent persists only status/metadata and reads back."""
    if not actor.strip() or not evidence:
        raise ValueError("Operator identity and source inspection evidence are required.")
    verified = propose_legacy_graph_repair(project_id=proposal["project_id"],
        rows=proposal["original_rows"], approved_sources=approved_sources, windows=proposal["windows"])
    if verified != dict(proposal) or dict(expected_source_hashes) != verified["source_hashes"]:
        raise ValueError("Source/proposal changed; audit again before applying.")
    # Only already-applied rows with an exact migration receipt may tolerate
    # the database's on-update timestamp. Never relax the first-apply hash.
    current = copy.deepcopy(list(rows))
    for item, candidate, original in zip(current, verified["candidate_rows"],
        verified["original_rows"]):
        if candidate == original:
            continue
        meta = item["row"].get("metadata_json") or item["row"].get("metadata") or {}
        if not isinstance(meta, Mapping):
            continue
        marker = meta.get("narrative_obligation") or {}
        if not isinstance(marker, Mapping):
            continue
        operator = marker.get("operator")
        if (not isinstance(operator, Mapping) or set(operator) != {"actor", "evidence"}
            or not isinstance(operator["actor"], str) or not operator["actor"].strip()
            or not isinstance(operator["evidence"], Mapping) or not operator["evidence"]):
            continue
        marker.pop("operator")
        row, expected = item["row"], candidate["row"]
        if "updated_at" in row and "updated_at" in expected:
            try:
                # SQLite may return naive UTC; PG returns an explicit offset.
                datetime.fromisoformat(str(row["updated_at"]).replace("Z", "+00:00"))
            except (TypeError, ValueError):
                continue
            row["updated_at"] = expected["updated_at"]
    if current == verified["candidate_rows"]:
        return copy.deepcopy(list(rows))
    if graph_content_hash(rows) != verified["expected_rows_hash"]:
        raise ValueError("Graph rows changed; audit again before applying.")
    result = copy.deepcopy(verified["candidate_rows"])
    for item, original in zip(result, verified["original_rows"], strict=True):
        if item != original:
            meta = item["row"].get("metadata_json") or item["row"].get("metadata")
            meta["narrative_obligation"]["operator"] = {
                "actor": actor, "evidence": copy.deepcopy(dict(evidence))}
    return result


def propose_graph_snapshot_repair(snapshot: Mapping[str, Any], *,
    approved_sources: Sequence[Mapping[str, Any]], windows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Offline full-export wrapper; unrelated arcs/beats remain byte-for-byte data."""
    rows = [{"table": table, "row": copy.deepcopy(row)}
            for table in ("clues", "payoffs") for row in snapshot.get(table, [])]
    projects = {str(item["row"].get("project_id")) for item in rows}
    if len(projects) != 1:
        raise ValueError("Snapshot must contain graph rows for exactly one project.")
    proposal = propose_legacy_graph_repair(project_id=projects.pop(), rows=rows,
        approved_sources=approved_sources, windows=windows)
    return {"original_snapshot": copy.deepcopy(dict(snapshot)),
        "expected_snapshot_hash": graph_content_hash(snapshot), "proposal": proposal}


def apply_graph_snapshot_repair(snapshot: Mapping[str, Any], review: Mapping[str, Any], *,
    approved_sources: Sequence[Mapping[str, Any]], expected_snapshot_hash: str,
    expected_source_hashes: Mapping[str, str], actor: str, evidence: Mapping[str, Any]) -> dict[str, Any]:
    """Return a full corrected export; never rename identities or erase graph rows."""
    if (graph_content_hash(review["original_snapshot"]) != expected_snapshot_hash
        or review["expected_snapshot_hash"] != expected_snapshot_hash):
        raise ValueError("Snapshot/proposal source hash changed.")
    original = review["original_snapshot"]
    for key, value in original.items():
        if key not in {"clues", "payoffs"} and snapshot.get(key) != value:
            raise ValueError("Unrelated graph content changed.")
    rows = [{"table": table, "row": copy.deepcopy(row)}
            for table in ("clues", "payoffs") for row in snapshot.get(table, [])]
    corrected = apply_legacy_graph_repair(rows, review["proposal"], approved_sources=approved_sources,
        expected_source_hashes=expected_source_hashes, actor=actor, evidence=evidence)
    result = copy.deepcopy(dict(snapshot))
    for table in ("clues", "payoffs"):
        result[table] = [item["row"] for item in corrected if item["table"] == table]
    return result
