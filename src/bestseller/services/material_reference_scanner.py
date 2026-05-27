"""Scan project material files for invalid entity references."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re

from bestseller.services.material_entity_registry import (
    EntityRegistry,
    EntityStatus,
    material_relative_path,
)


@dataclass(frozen=True)
class ReferenceProblem:
    file: str
    line_no: int
    referenced_name: str
    problem: str
    context: str


_WIKILINK_RE = re.compile(r"\[\[([^]|#]+)(?:#[^]|]+)?(?:\|[^]]+)?\]\]")
_RULE_RE = re.compile(r"\bR-\d{3,}\b")


def scan_material_references(
    project_dir: Path,
    registry: EntityRegistry,
) -> list[ReferenceProblem]:
    """Return deprecated, unknown, and duplicate references in material files."""

    root = project_dir.resolve()
    names = _scan_names(registry)
    problems: list[ReferenceProblem] = []
    for path in _material_files(root):
        rel = material_relative_path(root, path)
        text = path.read_text(encoding="utf-8", errors="ignore")
        for line_no, line in enumerate(text.splitlines(), 1):
            if _is_declaration_line(rel, line):
                continue
            problems.extend(_scan_known_names(rel, line_no, line, names, registry))
            problems.extend(_scan_unknown_wikilinks(rel, line_no, line, registry))
            problems.extend(_scan_unknown_rules(rel, line_no, line, registry))
    return _dedupe_problems(problems)


def _material_files(project_dir: Path) -> list[Path]:
    roots = [project_dir / "story-bible", project_dir / "obsidian-vault"]
    files: list[Path] = []
    for root in roots:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in {".md", ".yaml", ".yml", ".json"}:
                continue
            parts = set(path.parts)
            if "Canon" in parts or "_archive" in parts or path.name.startswith("."):
                continue
            if "raw" in parts or path.name in {"_manifest.json", "00-主页.md"}:
                continue
            files.append(path)
    return sorted(files)


def _scan_names(registry: EntityRegistry) -> tuple[str, ...]:
    return tuple(sorted(registry.by_name, key=len, reverse=True))


def _scan_known_names(
    file: str,
    line_no: int,
    line: str,
    names: tuple[str, ...],
    registry: EntityRegistry,
) -> list[ReferenceProblem]:
    problems: list[ReferenceProblem] = []
    for name in names:
        if not name or name not in line:
            continue
        record = registry.by_name.get(name)
        if record is None:
            continue
        if _is_self_reference(file, record.source_files):
            continue
        if record.status == EntityStatus.DEPRECATED:
            problems.append(
                ReferenceProblem(file, line_no, name, "deprecated", line.strip())
            )
        elif record.status == EntityStatus.DUPLICATE:
            problems.append(
                ReferenceProblem(file, line_no, name, "duplicate_canonical", line.strip())
            )
    return problems


def _scan_unknown_wikilinks(
    file: str,
    line_no: int,
    line: str,
    registry: EntityRegistry,
) -> list[ReferenceProblem]:
    problems: list[ReferenceProblem] = []
    for match in _WIKILINK_RE.finditer(line):
        target = match.group(1).strip()
        name = target.split("/")[-1]
        if target.startswith(("Inbox/", "../", "#")):
            continue
        if name and name not in registry.by_name:
            problems.append(ReferenceProblem(file, line_no, name, "unknown", line.strip()))
    return problems


def _scan_unknown_rules(
    file: str,
    line_no: int,
    line: str,
    registry: EntityRegistry,
) -> list[ReferenceProblem]:
    problems: list[ReferenceProblem] = []
    for match in _RULE_RE.finditer(line):
        rule_id = match.group(0)
        if rule_id not in registry.by_name:
            problems.append(ReferenceProblem(file, line_no, rule_id, "unknown", line.strip()))
    return problems


def _is_declaration_line(file: str, line: str) -> bool:
    if "forbidden" in file:
        return True
    stripped = line.strip()
    return stripped.startswith(("| ID |", "| --- |", "schema_version:"))


def _is_self_reference(file: str, source_files: tuple[str, ...]) -> bool:
    return file in source_files


def _dedupe_problems(problems: list[ReferenceProblem]) -> list[ReferenceProblem]:
    seen: set[tuple[str, int, str, str]] = set()
    deduped: list[ReferenceProblem] = []
    for problem in problems:
        key = (problem.file, problem.line_no, problem.referenced_name, problem.problem)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(problem)
    return deduped


__all__ = ["ReferenceProblem", "scan_material_references"]
