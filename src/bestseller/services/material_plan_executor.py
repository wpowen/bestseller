from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
import shutil

from bestseller.services.material_self_repair import (
    MaterialRepairAction,
    MaterialSelfRepairPlan,
)


_CONFIDENCE_ORDER = {"low": 0, "medium": 1, "high": 2}


@dataclass(frozen=True)
class MaterialActionResult:
    action: MaterialRepairAction
    applied: bool
    skipped_reason: str | None
    diff_summary: str
    backup_path: Path | None


@dataclass(frozen=True)
class MaterialPlanExecutionReport:
    project_dir: str
    applied: int
    skipped_offline: int
    skipped_unsafe: int
    results: tuple[MaterialActionResult, ...]
    rerun_required: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "project_dir": self.project_dir,
            "applied": self.applied,
            "skipped_offline": self.skipped_offline,
            "skipped_unsafe": self.skipped_unsafe,
            "rerun_required": self.rerun_required,
            "results": [
                {
                    "action": result.action.to_dict(),
                    "applied": result.applied,
                    "skipped_reason": result.skipped_reason,
                    "diff_summary": result.diff_summary,
                    "backup_path": (
                        result.backup_path.as_posix()
                        if result.backup_path is not None
                        else None
                    ),
                }
                for result in self.results
            ],
        }


def execute_material_plan(
    project_dir: Path,
    plan: MaterialSelfRepairPlan,
    *,
    dry_run: bool = False,
    confidence_min: str = "high",
    allow_llm_actions: bool = False,
    backup_root: Path | None = None,
) -> MaterialPlanExecutionReport:
    """Apply deterministic material repair actions."""

    root = project_dir.resolve()
    backup_base = (
        backup_root
        if backup_root is not None
        else root / "audits" / "material-repair-backups"
    )
    backup_run_dir = backup_base / datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    results: list[MaterialActionResult] = []

    for action in plan.actions:
        try:
            result = _execute_action(
                root,
                action,
                dry_run=dry_run,
                confidence_min=confidence_min,
                allow_llm_actions=allow_llm_actions,
                backup_run_dir=backup_run_dir,
            )
        except Exception as exc:  # pragma: no cover - exercised through tests with monkeypatch.
            result = MaterialActionResult(
                action=action,
                applied=False,
                skipped_reason=f"exception:{type(exc).__name__}",
                diff_summary="",
                backup_path=None,
            )
        results.append(result)

    applied = sum(1 for result in results if result.applied)
    skipped_offline = sum(
        1 for result in results if result.skipped_reason == "requires_llm"
    )
    skipped_unsafe = sum(
        1 for result in results if result.skipped_reason == "confidence_below_min"
    )
    return MaterialPlanExecutionReport(
        project_dir=root.as_posix(),
        applied=applied,
        skipped_offline=skipped_offline,
        skipped_unsafe=skipped_unsafe,
        results=tuple(results),
        rerun_required=applied > 0,
    )


def _execute_action(
    root: Path,
    action: MaterialRepairAction,
    *,
    dry_run: bool,
    confidence_min: str,
    allow_llm_actions: bool,
    backup_run_dir: Path,
) -> MaterialActionResult:
    if action.requires_llm and not allow_llm_actions:
        return _skipped(action, "requires_llm")
    if _confidence_rank(action.confidence) < _confidence_rank(confidence_min):
        return _skipped(action, "confidence_below_min")
    if action.action_type == "replace_deprecated_reference":
        return _replace_deprecated_reference(
            root,
            action,
            dry_run=dry_run,
            backup_run_dir=backup_run_dir,
        )
    if action.action_type == "merge_duplicate_entity":
        return _retire_duplicate_sources(
            root,
            action,
            dry_run=dry_run,
            backup_run_dir=backup_run_dir,
        )
    return _skipped(action, "unsupported_action_type")


def _replace_deprecated_reference(
    root: Path,
    action: MaterialRepairAction,
    *,
    dry_run: bool,
    backup_run_dir: Path,
) -> MaterialActionResult:
    replacement = str(action.payload.get("replacement") or "").strip()
    if not replacement:
        return _skipped(action, "no_replacement")
    target = action.target
    source = _source_file_from_action(root, action)
    if source is None or not source.exists():
        return _skipped(action, "file_not_found")
    original = source.read_text(encoding="utf-8")
    if target not in original:
        return _skipped(action, "target_not_found")
    updated = original.replace(target, replacement)
    backup_path = _backup_file(root, source, backup_run_dir, dry_run=dry_run)
    if not dry_run:
        source.write_text(updated, encoding="utf-8")
    rel = _relative(root, source)
    return MaterialActionResult(
        action=action,
        applied=not dry_run,
        skipped_reason="dry_run" if dry_run else None,
        diff_summary=f"{rel}: {target} -> {replacement}",
        backup_path=backup_path,
    )


def _retire_duplicate_sources(
    root: Path,
    action: MaterialRepairAction,
    *,
    dry_run: bool,
    backup_run_dir: Path,
) -> MaterialActionResult:
    sources = action.payload.get("source_files")
    source_paths = sources if isinstance(sources, list) else [action.source_path]
    retired: list[str] = []
    backup_path: Path | None = None
    for raw in source_paths:
        path = _resolve_material_path(root, str(raw))
        if path is None or not path.exists():
            continue
        current_backup = _backup_file(root, path, backup_run_dir, dry_run=dry_run)
        backup_path = backup_path or current_backup
        retired_path = path.with_name(f"{path.name}.retired")
        if not dry_run:
            path.rename(retired_path)
        retired.append(f"{_relative(root, path)} -> {_relative(root, retired_path)}")
    if not retired:
        return _skipped(action, "file_not_found")
    return MaterialActionResult(
        action=action,
        applied=not dry_run,
        skipped_reason="dry_run" if dry_run else None,
        diff_summary="; ".join(retired),
        backup_path=backup_path,
    )


def _source_file_from_action(root: Path, action: MaterialRepairAction) -> Path | None:
    raw = action.source_path.split(":", maxsplit=1)[0]
    return _resolve_material_path(root, raw)


def _resolve_material_path(root: Path, raw_path: str) -> Path | None:
    cleaned = raw_path.strip()
    if not cleaned:
        return None
    path = Path(cleaned)
    if path.is_absolute():
        return path
    return root / cleaned


def _backup_file(
    root: Path,
    source: Path,
    backup_run_dir: Path,
    *,
    dry_run: bool,
) -> Path:
    relative = Path(_relative(root, source))
    backup_path = backup_run_dir / relative
    if not dry_run:
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, backup_path)
    return backup_path


def _skipped(action: MaterialRepairAction, reason: str) -> MaterialActionResult:
    return MaterialActionResult(
        action=action,
        applied=False,
        skipped_reason=reason,
        diff_summary="",
        backup_path=None,
    )


def _confidence_rank(value: str) -> int:
    return _CONFIDENCE_ORDER.get(str(value or "").lower(), -1)


def _relative(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()

