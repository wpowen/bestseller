"""Project-bound adoption and cache receipts. Stored text never grants itself authority."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from functools import lru_cache
from typing import Any, Mapping

POLICY_VERSION = "prompt-sources.v2"
REFERENCE_KEYS = (
    "distilled_design_reference_blocks", "distilled_design_reference_block",
    "distilled_strategy_blocks", "distilled_strategy_block",
    "distilled_strategy_card", "character_strategy", "distilled_strategy_expected",
    "character_engine_profiles",
)


@lru_cache(maxsize=4)
def _asset_hash(signature: tuple) -> str:
    digest = hashlib.sha256()
    for path, _size, _mtime in signature:
        digest.update(path.encode())
        digest.update(Path(path).read_bytes())
    return digest.hexdigest()


def reference_asset_hash() -> str:
    root = Path(__file__).resolve().parents[3]
    paths = [p for p in (root / "data/distillation/aggregates").rglob("*") if p.is_file()]
    paths.extend(root / "src/bestseller/services" / name for name in (
        "distilled_design_reference.py", "distilled_strategy_compiler.py", "story_design_grammars.py",
    ))
    signature = tuple((str(p), p.stat().st_size, p.stat().st_mtime_ns) for p in sorted(paths) if p.is_file())
    return _asset_hash(signature)


def content_hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":"), default=str).encode()).hexdigest()


def project_input_hash(project: Any) -> str:
    meta = project.metadata_json if isinstance(project.metadata_json, Mapping) else {}
    return content_hash({
        "project_id": str(project.id),
        "genre": project.genre, "sub_genre": project.sub_genre,
        "language": project.language,
        "reader_contract": getattr(project, "reader_contract_json", None),
        "dramatic_question": getattr(project, "dramatic_question", None),
        "theme_statement": getattr(project, "theme_statement", None),
        "audience": getattr(project, "audience", None),
        "title": getattr(project, "title", None),
        "inputs": {key: meta.get(key) for key in (
            "creation_intent_contract", "genre_intent_contract", "book_design_snapshot",
            "premise", "raw_premise", "unique_hook", "story_facets", "commercial_brief",
            "reader_promise", "story_spine", "synopsis",
        )},
    })


def archive_metadata(metadata: Mapping[str, Any], keys: tuple[str, ...], *, reason: str) -> dict:
    """Idempotently supersede projections, preserving their original bytes as JSON values."""
    out = copy.deepcopy(dict(metadata))
    removed = {key: out.pop(key) for key in keys if key in out}
    if not removed:
        return out
    archive = dict(out.get("superseded_prompt_sources") or {})
    digest = content_hash(removed)
    archive.setdefault(digest, {"policy_version": POLICY_VERSION, "reason": reason,
                                "source_values": removed})
    out["superseded_prompt_sources"] = archive
    receipts = dict(out.get("prompt_reference_receipts") or {})
    for key in removed:
        receipts.pop(key, None)
    out["prompt_reference_receipts"] = receipts
    return out


def stamp_reference_cache(project: Any, keys: tuple[str, ...], *, producer: str) -> None:
    if producer not in {"design_reference_renderer", "strategy_compiler", "legacy_bootstrap", "character_profile_materializer"}:
        raise ValueError("unregistered reference producer")
    meta = dict(project.metadata_json or {})
    receipts = dict(meta.get("prompt_reference_receipts") or {})
    for key in keys:
        if key in meta:
            receipts[key] = {"policy_version": POLICY_VERSION, "project_id": str(project.id),
                             "input_hash": project_input_hash(project),
                             "asset_hash": reference_asset_hash(),
                             "content_hash": content_hash(meta[key]), "producer": producer}
    meta["prompt_reference_receipts"] = receipts
    project.metadata_json = meta


def valid_reference_value(project: Any, key: str) -> Any:
    from bestseller.settings import get_settings
    if not get_settings().pipeline.enable_distilled_design_reference:
        return None
    meta = project.metadata_json if isinstance(project.metadata_json, Mapping) else {}
    receipt = (meta.get("prompt_reference_receipts") or {}).get(key)
    if not isinstance(receipt, Mapping):
        return None
    expected = {"policy_version": POLICY_VERSION, "project_id": str(project.id),
                "asset_hash": reference_asset_hash(),
                "input_hash": project_input_hash(project), "content_hash": content_hash(meta.get(key))}
    if any(receipt.get(k) != v for k, v in expected.items()):
        return None
    if receipt.get("producer") not in {"design_reference_renderer", "strategy_compiler", "legacy_bootstrap", "character_profile_materializer"}:
        return None
    return meta.get(key)


def adopt_story_package(project: Any, path: Path, *, requested_by: str) -> dict:
    """Called by an explicit import action, never by auto-discovery or model output."""
    if not requested_by.strip():
        raise ValueError("an adopting actor is required")
    path = path.resolve(strict=True)
    raw = path.read_bytes()
    payload = json.loads(raw)
    if not isinstance(payload, dict) or not isinstance(payload.get("book"), dict):
        raise ValueError("story package requires a book object")
    receipt = {"policy_version": POLICY_VERSION, "project_id": str(project.id),
               "path": str(path), "sha256": hashlib.sha256(raw).hexdigest(),
               "adopted_payload": payload, "adopted_payload_hash": content_hash(payload),
               "adopted_by": requested_by}
    project.metadata_json = {**(project.metadata_json or {}), "story_package_path": str(path),
                             "story_package_adoption": receipt}
    return receipt


def load_adopted_story_package(project: Any) -> dict:
    meta = project.metadata_json if isinstance(project.metadata_json, Mapping) else {}
    receipt = meta.get("story_package_adoption")
    if not isinstance(receipt, Mapping) or receipt.get("policy_version") not in {"prompt-sources.v1", POLICY_VERSION}:
        return {}
    if receipt.get("project_id") != str(project.id) or not receipt.get("adopted_by"):
        return {}
    # Adopted facts are immutable snapshots. Moving/deleting/editing a source
    # file cannot revoke the already adopted cast and constraints.
    snapshot = receipt.get("adopted_payload")
    if isinstance(snapshot, dict) and content_hash(snapshot) == receipt.get("adopted_payload_hash"):
        return {**copy.deepcopy(snapshot), "path": receipt.get("path")}
    if snapshot is not None:
        raise AdoptedSourceUnavailable("Adopted story snapshot failed its integrity check.")
    try:
        path = Path(str(meta.get("story_package_path") or "")).resolve(strict=True)
        raw = path.read_bytes()
        if str(path) != receipt.get("path") or hashlib.sha256(raw).hexdigest() != receipt.get("sha256"):
            raise AdoptedSourceUnavailable("The adopted source changed; explicit readoption is required.")
        payload = json.loads(raw)
    except (OSError, ValueError, TypeError) as exc:
        raise AdoptedSourceUnavailable("Adopted story source is unavailable; retain prior facts or explicitly revise adoption.") from exc
    return {**payload, "path": str(path)} if isinstance(payload, dict) else {}


def outline_dependency_hash(metadata: Mapping[str, Any]) -> str | None:
    inputs = {k: metadata[k] for k in (
        "creation_intent_contract", "book_design_snapshot", "derived_input_revision",
        "story_design_kernel", "world_spec", "cast_spec",
        "book_spec", "volume_plan", "premise", "raw_premise",
        "_truth_artifact_fingerprints", "story_package_adoption",
    ) if metadata.get(k)}
    return content_hash(inputs) if inputs else None


class AdoptedSourceUnavailable(RuntimeError):
    terminal = True
    code = "adopted_source_unavailable"
