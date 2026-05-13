"""Utilities for promoting distillation packages into system assets.

The distillation pipeline has two separate concerns:

* Per-source extraction packages under ``data/distillation/source-XXXX``.
* System-facing aggregate assets: material-library JSONL, grammar patches,
  anti-copy ledgers, and mechanism registries.

This module keeps the aggregation logic importable and testable. CLI scripts in
``scripts/distillation`` are thin wrappers around these functions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any, Iterable

import yaml


REQUIRED_PACKAGE_FILES: tuple[str, ...] = (
    "source_manifest.json",
    "chapters.index.json",
    "book_design_card.json",
    "anti_copy_ledger.json",
    "volume_cards.jsonl",
    "mechanism_candidates.jsonl",
)

MATERIAL_REVIEW_FILENAMES: tuple[str, ...] = (
    "material_entries.review.jsonl",
    "material_entries.sample.jsonl",
)

SENSITIVE_PATTERNS: tuple[str, ...] = (
    "/Users/",
    "\\Users\\",
    ".pdf",
    ".epub",
    ".mobi",
    ".azw3",
)


@dataclass(frozen=True)
class DistillationPackageReport:
    package_dir: str
    ok: bool
    source_id: str | None = None
    missing_files: tuple[str, ...] = ()
    material_rows: int = 0
    mechanism_rows: int = 0
    volume_rows: int = 0
    chapter_jobs: int = 0
    errors: tuple[str, ...] = ()


@dataclass(frozen=True)
class DistillationAggregateReport:
    aggregate_key: str
    output_dir: str
    source_ids: tuple[str, ...]
    material_rows: int
    mechanism_rows: int
    anti_copy_blocked_combinations: int
    grammar_state_variables: int
    grammar_change_vectors: int
    warnings: tuple[str, ...] = field(default_factory=tuple)


def read_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected JSON object")
    return data


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    for line_no, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        obj = json.loads(stripped)
        if not isinstance(obj, dict):
            raise ValueError(f"{path}:{line_no}: expected JSON object")
        rows.append(obj)
    return rows


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> int:
    materialized = list(rows)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in materialized),
        encoding="utf-8",
    )
    return len(materialized)


def _first_existing(path: Path, names: tuple[str, ...]) -> Path | None:
    for name in names:
        candidate = path / name
        if candidate.exists():
            return candidate
    return None


def _scan_sensitive_values(value: object, *, path: str = "$") -> list[str]:
    findings: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            findings.extend(_scan_sensitive_values(child, path=f"{path}.{key}"))
        return findings
    if isinstance(value, list):
        for idx, child in enumerate(value):
            findings.extend(_scan_sensitive_values(child, path=f"{path}[{idx}]"))
        return findings
    if isinstance(value, str):
        for pattern in SENSITIVE_PATTERNS:
            if pattern in value:
                findings.append(f"{path}: contains sensitive pattern {pattern!r}")
    return findings


def validate_distillation_package(package_dir: Path) -> DistillationPackageReport:
    errors: list[str] = []
    missing = tuple(name for name in REQUIRED_PACKAGE_FILES if not (package_dir / name).exists())
    if missing:
        errors.extend(f"missing required file: {name}" for name in missing)

    source_id: str | None = None
    material_rows = 0
    mechanism_rows = 0
    volume_rows = 0
    chapter_jobs = 0
    try:
        manifest = read_json(package_dir / "source_manifest.json")
        source_id = str(manifest.get("source_id") or "")
        if not source_id.startswith("source-"):
            errors.append("source_manifest.source_id must start with 'source-'")
        redaction = manifest.get("redaction_policy") or {}
        if isinstance(redaction, dict):
            if redaction.get("store_raw_text_in_repo") is not False:
                errors.append("redaction_policy.store_raw_text_in_repo must be false")
            if redaction.get("store_source_title_in_repo") is not False:
                errors.append("redaction_policy.store_source_title_in_repo must be false")
            if redaction.get("store_author_in_repo") is not False:
                errors.append("redaction_policy.store_author_in_repo must be false")
        errors.extend(_scan_sensitive_values(manifest, path="source_manifest"))
    except Exception as exc:  # noqa: BLE001
        errors.append(f"source_manifest invalid: {exc}")

    for filename in ("chapters.index.json", "book_design_card.json", "anti_copy_ledger.json"):
        try:
            errors.extend(_scan_sensitive_values(read_json(package_dir / filename), path=filename))
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{filename} invalid: {exc}")

    try:
        volume_rows = len(read_jsonl(package_dir / "volume_cards.jsonl"))
    except Exception as exc:  # noqa: BLE001
        errors.append(f"volume_cards.jsonl invalid: {exc}")

    try:
        mechanism_rows = len(read_jsonl(package_dir / "mechanism_candidates.jsonl"))
    except Exception as exc:  # noqa: BLE001
        errors.append(f"mechanism_candidates.jsonl invalid: {exc}")

    material_path = _first_existing(package_dir, MATERIAL_REVIEW_FILENAMES)
    if material_path is None:
        errors.append("missing material_entries.review.jsonl or material_entries.sample.jsonl")
    else:
        try:
            material_rows = len(read_jsonl(material_path))
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{material_path.name} invalid: {exc}")

    jobs_path = package_dir / "llm_jobs" / "chapter_jobs.index.jsonl"
    if jobs_path.exists():
        try:
            chapter_jobs = len(read_jsonl(jobs_path))
        except Exception as exc:  # noqa: BLE001
            errors.append(f"llm_jobs/chapter_jobs.index.jsonl invalid: {exc}")

    return DistillationPackageReport(
        package_dir=str(package_dir),
        ok=not errors,
        source_id=source_id,
        missing_files=missing,
        material_rows=material_rows,
        mechanism_rows=mechanism_rows,
        volume_rows=volume_rows,
        chapter_jobs=chapter_jobs,
        errors=tuple(errors),
    )


def _unique_extend(target: list[str], values: Iterable[object]) -> None:
    for value in values:
        text = str(value).strip()
        if text and text not in target:
            target.append(text)


def _material_rows_from_package(package_dir: Path, source_id: str) -> list[dict[str, Any]]:
    material_path = _first_existing(package_dir, MATERIAL_REVIEW_FILENAMES)
    if material_path is None:
        return []
    rows: list[dict[str, Any]] = []
    for row in read_jsonl(material_path):
        material = dict(row)
        content = material.get("content_json")
        if not isinstance(content, dict):
            content = {}
        source_ids = content.get("distillation_source_ids")
        if not isinstance(source_ids, list):
            source_ids = []
        if source_id not in source_ids:
            source_ids.append(source_id)
        content["distillation_source_ids"] = source_ids
        material["content_json"] = content
        material.setdefault("source_type", "user_curated")
        material["status"] = "review"
        rows.append(material)
    return rows


def _aggregate_mechanisms(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    for row in rows:
        mechanism_id = str(row.get("mechanism_id") or row.get("slug") or "").strip()
        if not mechanism_id:
            continue
        item = by_id.setdefault(
            mechanism_id,
            {
                "mechanism_id": mechanism_id,
                "candidate_type": row.get("candidate_type"),
                "summary": row.get("summary"),
                "promotion_target": row.get("promotion_target"),
                "status": "review",
                "source_ids": [],
                "evidence_count": 0,
                "max_confidence": 0.0,
            },
        )
        source_id = str(row.get("source_id") or "").strip()
        if source_id and source_id not in item["source_ids"]:
            item["source_ids"].append(source_id)
        item["evidence_count"] += 1
        try:
            item["max_confidence"] = max(float(row.get("confidence") or 0.0), item["max_confidence"])
        except (TypeError, ValueError):
            pass
    return sorted(by_id.values(), key=lambda item: str(item["mechanism_id"]))


def _aggregate_anti_copy_ledgers(ledgers: list[dict[str, Any]]) -> dict[str, Any]:
    source_ids: list[str] = []
    blocked_categories: list[dict[str, Any]] = []
    blocked_combinations: list[str] = []
    replacement_policy: list[str] = []
    seen_category_policies: set[tuple[str, str]] = set()
    for ledger in ledgers:
        source_id = str(ledger.get("source_id") or "").strip()
        if source_id and source_id not in source_ids:
            source_ids.append(source_id)
        for item in ledger.get("blocked_categories") or []:
            if not isinstance(item, dict):
                continue
            key = (str(item.get("category") or ""), str(item.get("policy") or ""))
            if key in seen_category_policies:
                continue
            seen_category_policies.add(key)
            blocked_categories.append(item)
        _unique_extend(blocked_combinations, ledger.get("blocked_combinations") or [])
        _unique_extend(replacement_policy, ledger.get("replacement_policy") or [])
    return {
        "source_ids": source_ids,
        "blocked_categories": blocked_categories,
        "blocked_combinations": blocked_combinations,
        "replacement_policy": replacement_policy,
    }


def _aggregate_grammar_patches(
    patches: list[dict[str, Any]],
    *,
    aggregate_key: str,
) -> dict[str, Any]:
    output: dict[str, Any] = {
        "key": aggregate_key,
        "name": patches[0].get("name") if patches else aggregate_key,
        "source_ids": [],
        "status": "review",
        "applies_to_categories": [],
        "required_contracts": [],
        "state_variables": [],
        "chapter_change_vectors": [],
        "reader_rewards": [],
        "hook_or_aftereffect_types": [],
        "forbidden_defaults": [],
    }
    for patch in patches:
        _unique_extend(output["source_ids"], patch.get("source_ids") or [])
        _unique_extend(output["applies_to_categories"], patch.get("applies_to_categories") or [])
        _unique_extend(output["required_contracts"], patch.get("required_contracts") or [])
        _unique_extend(output["state_variables"], patch.get("state_variables") or [])
        _unique_extend(output["chapter_change_vectors"], patch.get("chapter_change_vectors") or [])
        _unique_extend(output["reader_rewards"], patch.get("reader_rewards") or [])
        _unique_extend(output["hook_or_aftereffect_types"], patch.get("hook_or_aftereffect_types") or [])
        _unique_extend(output["forbidden_defaults"], patch.get("forbidden_defaults") or [])
    return output


def aggregate_distillation_packages(
    package_dirs: Iterable[Path],
    *,
    output_dir: Path,
    aggregate_key: str,
) -> DistillationAggregateReport:
    packages = list(package_dirs)
    output_dir.mkdir(parents=True, exist_ok=True)
    warnings: list[str] = []
    source_ids: list[str] = []
    material_rows: list[dict[str, Any]] = []
    mechanism_rows: list[dict[str, Any]] = []
    ledgers: list[dict[str, Any]] = []
    grammar_patches: list[dict[str, Any]] = []

    for package_dir in packages:
        report = validate_distillation_package(package_dir)
        if not report.ok:
            warnings.extend(f"{package_dir}: {err}" for err in report.errors)
            continue
        if not report.source_id:
            warnings.append(f"{package_dir}: source_id missing")
            continue
        source_ids.append(report.source_id)
        material_rows.extend(_material_rows_from_package(package_dir, report.source_id))
        mechanism_rows.extend(read_jsonl(package_dir / "mechanism_candidates.jsonl"))
        ledgers.append(read_json(package_dir / "anti_copy_ledger.json"))
        grammar_path = package_dir / "grammar_patch.yaml"
        if grammar_path.exists():
            loaded = yaml.safe_load(grammar_path.read_text(encoding="utf-8")) or {}
            if isinstance(loaded, dict):
                grammar_patches.append(loaded)

    mechanism_registry = _aggregate_mechanisms(mechanism_rows)
    anti_copy = _aggregate_anti_copy_ledgers(ledgers)
    grammar_patch = _aggregate_grammar_patches(grammar_patches, aggregate_key=aggregate_key)

    material_count = write_jsonl(output_dir / "material_entries.review.jsonl", material_rows)
    mechanism_count = write_jsonl(output_dir / "mechanism_registry.jsonl", mechanism_registry)
    write_json(output_dir / "anti_copy_rules.json", anti_copy)
    (output_dir / "grammar_patch.yaml").write_text(
        yaml.safe_dump(grammar_patch, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    manifest = {
        "aggregate_key": aggregate_key,
        "source_ids": source_ids,
        "source_count": len(source_ids),
        "material_rows": material_count,
        "mechanism_rows": mechanism_count,
        "anti_copy_blocked_combinations": len(anti_copy["blocked_combinations"]),
        "grammar_state_variables": len(grammar_patch["state_variables"]),
        "grammar_change_vectors": len(grammar_patch["chapter_change_vectors"]),
        "warnings": warnings,
    }
    write_json(output_dir / "aggregate_manifest.json", manifest)

    return DistillationAggregateReport(
        aggregate_key=aggregate_key,
        output_dir=str(output_dir),
        source_ids=tuple(source_ids),
        material_rows=material_count,
        mechanism_rows=mechanism_count,
        anti_copy_blocked_combinations=len(anti_copy["blocked_combinations"]),
        grammar_state_variables=len(grammar_patch["state_variables"]),
        grammar_change_vectors=len(grammar_patch["chapter_change_vectors"]),
        warnings=tuple(warnings),
    )


def install_story_design_grammar_patch(
    patch_path: Path,
    *,
    grammar_dir: Path,
    dry_run: bool = True,
) -> Path:
    patch = yaml.safe_load(patch_path.read_text(encoding="utf-8")) or {}
    if not isinstance(patch, dict):
        raise ValueError(f"{patch_path}: expected YAML mapping")
    key = str(patch.get("key") or "").strip()
    if not key:
        raise ValueError(f"{patch_path}: missing key")
    target = grammar_dir / f"{key}.yaml"
    if not dry_run:
        grammar_dir.mkdir(parents=True, exist_ok=True)
        target.write_text(
            yaml.safe_dump(patch, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
    return target
