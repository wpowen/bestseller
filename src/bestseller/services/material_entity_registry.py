"""Build a project-scoped registry of material entities.

The registry is intentionally file-based: lifecycle audits can run against an
exported book package without opening the application database.  It treats
``story-bible`` and the readable Obsidian vault as material sources, then
normalizes active, deprecated, placeholder, and duplicate entities into one
lookup table used by downstream reference gates.
"""
# ruff: noqa: RUF001

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
import json
from pathlib import Path
import re
from typing import Any

import yaml


class EntityType(StrEnum):
    CHARACTER = "character"
    OBJECT = "object"
    LOCATION = "location"
    RULE = "rule"
    CLUE = "clue"
    REVEAL = "reveal"
    HOOK = "hook"
    FACTION = "faction"


class EntityStatus(StrEnum):
    ACTIVE = "active"
    DEPRECATED = "deprecated"
    PLACEHOLDER = "placeholder"
    DUPLICATE = "duplicate"


@dataclass(frozen=True)
class EntityRecord:
    type: EntityType
    canonical_name: str
    aliases: tuple[str, ...]
    status: EntityStatus
    source_files: tuple[str, ...]
    notes: str = ""


@dataclass(frozen=True)
class EntityRegistry:
    records: tuple[EntityRecord, ...]
    by_name: dict[str, EntityRecord]
    by_type: dict[EntityType, tuple[EntityRecord, ...]]


_HEADING_RE = re.compile(r"^##\s+(.+?)\s*$")
_RULE_ID_RE = re.compile(r"\|\s*(R-\d{3,})\s*\|")
_CLUE_ID_RE = re.compile(r"\b(C-\d{3,}|CLUE[-_][A-Za-z0-9_-]+)\b")
_FRONTMATTER_CHARACTER_RE = re.compile(r"^character:\s*['\"]?(.+?)['\"]?\s*$", re.M)
_WIKI_PREFIXES = ("人物/", "地点/", "物件/", "规则/", "线索/")
_PRESERVE_PARENTHETICAL_MARKERS = (
    "心魔",
    "镜中",
    "镜影",
    "残影",
    "真身",
    "自称",
    "出马仙",
    "饿物",
)


def build_entity_registry(project_dir: Path) -> EntityRegistry:
    """Scan material files under ``project_dir`` and build an entity registry."""

    root = project_dir.resolve()
    records: list[EntityRecord] = []
    records.extend(_records_from_cast(root))
    records.extend(_records_from_character_files(root))
    records.extend(_records_from_rule_ledger(root))
    records.extend(_records_from_clue_ledger(root))
    records.extend(_records_from_reveal_schedule(root))
    records.extend(_records_from_forbidden_terms(root))
    records.extend(_records_from_canon_guardrails(root))
    records.extend(_records_from_vault_index(root))

    records = _mark_duplicate_character_files(records)
    by_name = _build_name_index(records)
    by_type: dict[EntityType, tuple[EntityRecord, ...]] = {}
    for entity_type in EntityType:
        by_type[entity_type] = tuple(record for record in records if record.type == entity_type)
    return EntityRegistry(records=tuple(records), by_name=by_name, by_type=by_type)


def material_relative_path(project_dir: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(project_dir.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def canonical_character_name(name: str) -> str:
    """Return the duplicate-comparison key for a character-like name."""

    cleaned = _strip_wiki_target(name).strip()
    cleaned = re.sub(r"\.(md|yaml|yml|json)$", "", cleaned, flags=re.I)
    suffix_match = re.search(r"[（(]([^（）()]*)[）)]$", cleaned)
    if suffix_match and not any(
        marker in suffix_match.group(1) for marker in _PRESERVE_PARENTHETICAL_MARKERS
    ):
        cleaned = re.sub(r"[（(][^（）()]*[）)]$", "", cleaned).strip()
    return cleaned


def _records_from_cast(project_dir: Path) -> list[EntityRecord]:
    path = project_dir / "story-bible" / "cast-and-promises.md"
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8")
    sections = _markdown_sections(text)
    records: list[EntityRecord] = []
    for name, body in sections.items():
        if not _looks_like_material_name(name):
            continue
        aliases = _extract_aliases(body)
        status = EntityStatus.PLACEHOLDER if _is_placeholder_section(body) else EntityStatus.ACTIVE
        records.append(
            EntityRecord(
                type=EntityType.CHARACTER,
                canonical_name=canonical_character_name(name),
                aliases=tuple(sorted(aliases)),
                status=status,
                source_files=(material_relative_path(project_dir, path),),
            )
        )
    return records


def _records_from_character_files(project_dir: Path) -> list[EntityRecord]:
    base = project_dir / "obsidian-vault" / "人物"
    if not base.exists():
        return []
    records: list[EntityRecord] = []
    for path in sorted(base.glob("*.md")):
        if path.name.startswith("_"):
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        name = _frontmatter_character(text) or path.stem
        aliases = _extract_aliases(text)
        if (
            path.stem != canonical_character_name(path.stem)
            or path.stem != canonical_character_name(name)
        ):
            aliases.add(path.stem)
        heading = _first_heading(text)
        if heading and heading != name:
            aliases.add(heading)
        status = EntityStatus.PLACEHOLDER if _is_placeholder_section(text) else EntityStatus.ACTIVE
        records.append(
            EntityRecord(
                type=EntityType.CHARACTER,
                canonical_name=canonical_character_name(name),
                aliases=tuple(sorted(aliases)),
                status=status,
                source_files=(material_relative_path(project_dir, path),),
            )
        )
    return records


def _records_from_rule_ledger(project_dir: Path) -> list[EntityRecord]:
    path = project_dir / "story-bible" / "rule-ledger.md"
    if not path.exists():
        return []
    records: list[EntityRecord] = []
    for match in _RULE_ID_RE.finditer(path.read_text(encoding="utf-8", errors="ignore")):
        rule_id = match.group(1)
        records.append(
            EntityRecord(
                type=EntityType.RULE,
                canonical_name=rule_id,
                aliases=(),
                status=EntityStatus.ACTIVE,
                source_files=(material_relative_path(project_dir, path),),
            )
        )
    return _dedupe_records(records)


def _records_from_clue_ledger(project_dir: Path) -> list[EntityRecord]:
    path = project_dir / "story-bible" / "clue-ledger.md"
    if not path.exists():
        return []
    records = [
        EntityRecord(
            type=EntityType.CLUE,
            canonical_name=match.group(1),
            aliases=(),
            status=EntityStatus.ACTIVE,
            source_files=(material_relative_path(project_dir, path),),
        )
        for match in _CLUE_ID_RE.finditer(path.read_text(encoding="utf-8", errors="ignore"))
    ]
    return _dedupe_records(records)


def _records_from_reveal_schedule(project_dir: Path) -> list[EntityRecord]:
    path = project_dir / "story-bible" / "reveal-schedule.yaml"
    if not path.exists():
        return []
    payload = _safe_yaml(path)
    reveals = payload.get("reveals") if isinstance(payload, dict) else None
    if not isinstance(reveals, list):
        return []
    records: list[EntityRecord] = []
    for reveal in reveals:
        if not isinstance(reveal, dict) or not reveal.get("id"):
            continue
        aliases = tuple(str(token) for token in reveal.get("tokens") or () if str(token).strip())
        records.append(
            EntityRecord(
                type=EntityType.REVEAL,
                canonical_name=str(reveal["id"]),
                aliases=aliases,
                status=EntityStatus.ACTIVE,
                source_files=(material_relative_path(project_dir, path),),
            )
        )
    return records


def _records_from_forbidden_terms(project_dir: Path) -> list[EntityRecord]:
    records: list[EntityRecord] = []
    for path in sorted((project_dir / "story-bible").glob("forbidden*.*")):
        payload = _safe_yaml(path)
        if not isinstance(payload, dict):
            continue
        deprecated = _string_list(payload.get("deprecated_should_remove"))
        deprecated.extend(_string_list(payload.get("deprecated_terms")))
        for term in deprecated:
            records.append(
                EntityRecord(
                    type=EntityType.CHARACTER,
                    canonical_name=term,
                    aliases=(),
                    status=EntityStatus.DEPRECATED,
                    source_files=(material_relative_path(project_dir, path),),
                    notes="Declared deprecated in forbidden policy.",
                )
            )
    return _dedupe_records(records)


def _records_from_canon_guardrails(project_dir: Path) -> list[EntityRecord]:
    path = project_dir / "story-bible" / "canon-guardrails.json"
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    records: list[EntityRecord] = []
    for entry in _guardrail_entries(payload):
        subject = str(entry.get("subject") or entry.get("term") or "").strip()
        reason = str(entry.get("reason") or "")
        if not subject:
            continue
        is_deprecated_guardrail = "旧版" in reason or "当前正典未保留" in reason
        # Generic marker (was hardcoded to one book's character + arc name):
        # a guardrail entry whose reason says the character would hijack the
        # mainline is a planned retirement, whatever the book.
        is_planned_retirement = "抢走" in reason and "主线" in reason
        if is_deprecated_guardrail or is_planned_retirement:
            records.append(
                EntityRecord(
                    type=EntityType.CHARACTER,
                    canonical_name=subject,
                    aliases=(),
                    status=EntityStatus.DEPRECATED,
                    source_files=(material_relative_path(project_dir, path),),
                    notes="Restricted by canon guardrails.",
                )
            )
    return _dedupe_records(records)


def _records_from_vault_index(project_dir: Path) -> list[EntityRecord]:
    base = project_dir / "obsidian-vault"
    if not base.exists():
        return []
    typed_dirs = {
        "地点": EntityType.LOCATION,
        "物件": EntityType.OBJECT,
        "规则": EntityType.RULE,
        "线索": EntityType.CLUE,
        "派系": EntityType.FACTION,
    }
    records: list[EntityRecord] = []
    for dirname, entity_type in typed_dirs.items():
        folder = base / dirname
        if not folder.exists():
            continue
        for path in sorted(folder.glob("*.md")):
            if path.name.startswith("_"):
                continue
            records.append(
                EntityRecord(
                    type=entity_type,
                    canonical_name=canonical_character_name(path.stem),
                    aliases=(),
                    status=EntityStatus.ACTIVE,
                    source_files=(material_relative_path(project_dir, path),),
                )
            )
    return records


def _mark_duplicate_character_files(records: list[EntityRecord]) -> list[EntityRecord]:
    by_canonical: dict[str, list[EntityRecord]] = defaultdict(list)
    for record in records:
        if record.type == EntityType.CHARACTER and record.status != EntityStatus.DEPRECATED:
            by_canonical[record.canonical_name].append(record)

    duplicate_sources: set[str] = set()
    for canonical, group in by_canonical.items():
        file_records = [
            record
            for record in group
            if any("/obsidian-vault/人物/" in f"/{source}" for source in record.source_files)
        ]
        variant_records = [
            record
            for record in file_records
            if any(
                canonical_character_name(Path(source).stem) == canonical
                for source in record.source_files
            )
            and any(Path(source).stem != canonical for source in record.source_files)
        ]
        if len(file_records) > 1 and variant_records:
            duplicate_sources.update(
                source for record in variant_records for source in record.source_files
            )

    next_records: list[EntityRecord] = []
    for record in records:
        if record.status == EntityStatus.DEPRECATED:
            next_records.append(record)
            continue
        if any(source in duplicate_sources for source in record.source_files):
            next_records.append(
                EntityRecord(
                    type=record.type,
                    canonical_name=record.canonical_name,
                    aliases=record.aliases,
                    status=EntityStatus.DUPLICATE,
                    source_files=record.source_files,
                    notes="Duplicate character file for canonical entity.",
                )
            )
        else:
            next_records.append(record)
    return next_records


def _build_name_index(records: Iterable[EntityRecord]) -> dict[str, EntityRecord]:
    priority = {
        EntityStatus.DEPRECATED: 0,
        EntityStatus.ACTIVE: 1,
        EntityStatus.PLACEHOLDER: 2,
        EntityStatus.DUPLICATE: 3,
    }
    by_name: dict[str, EntityRecord] = {}
    for record in sorted(records, key=lambda item: priority[item.status]):
        names = (record.canonical_name, *record.aliases)
        for name in names:
            cleaned = str(name).strip()
            if not cleaned:
                continue
            if cleaned not in by_name:
                by_name[cleaned] = record
    return by_name


def _markdown_sections(text: str) -> dict[str, str]:
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for line in text.splitlines():
        match = _HEADING_RE.match(line)
        if match:
            current = match.group(1).strip()
            sections.setdefault(current, [])
            continue
        if current:
            sections[current].append(line)
    return {name: "\n".join(lines).strip() for name, lines in sections.items()}


def _extract_aliases(text: str) -> set[str]:
    aliases: set[str] = set()
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith(("别名", "aliases", "alias")) and (
            "：" in stripped or ":" in stripped
        ):
            _, value = re.split(r"[:：]", stripped, maxsplit=1)
            aliases.update(_split_alias_values(value))
        if "父亲" in stripped:
            aliases.update({"父亲", "爸爸"})
        if "祖父" in stripped or "爷爷" in stripped:
            aliases.update({"祖父", "爷爷"})
    return {alias for alias in aliases if _looks_like_material_name(alias)}


def _split_alias_values(value: str) -> set[str]:
    return {
        part.strip(" -[]`'\"")
        for part in re.split(r"[,，、/；;]", value)
        if part.strip(" -[]`'\"")
    }


def _is_placeholder_section(text: str) -> bool:
    content_lines = [
        line.strip()
        for line in text.splitlines()
        if line.strip() and not line.strip().startswith(("#", "| ---", "---", "type:", "project:"))
    ]
    if not content_lines:
        return True
    non_empty_cells = 0
    for line in content_lines:
        if "|" not in line:
            non_empty_cells += 1
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        non_empty_cells += sum(1 for cell in cells[1:] if cell)
    return non_empty_cells == 0


def _first_heading(text: str) -> str | None:
    for line in text.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return None


def _frontmatter_character(text: str) -> str | None:
    match = _FRONTMATTER_CHARACTER_RE.search(text)
    return match.group(1).strip().strip("\"'") if match else None


def _safe_yaml(path: Path) -> dict[str, Any]:
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _guardrail_entries(value: object) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    if isinstance(value, dict):
        for key_value in value.values():
            entries.extend(_guardrail_entries(key_value))
        if any(key in value for key in ("subject", "term", "forbidden_patterns")):
            entries.append(dict(value))
    elif isinstance(value, list):
        for item in value:
            entries.extend(_guardrail_entries(item))
    return entries


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _dedupe_records(records: Iterable[EntityRecord]) -> list[EntityRecord]:
    seen: set[tuple[EntityType, str, EntityStatus]] = set()
    deduped: list[EntityRecord] = []
    for record in records:
        key = (record.type, record.canonical_name, record.status)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(record)
    return deduped


def _strip_wiki_target(name: str) -> str:
    cleaned = str(name)
    if "|" in cleaned:
        cleaned = cleaned.split("|", 1)[0]
    for prefix in _WIKI_PREFIXES:
        cleaned = cleaned.removeprefix(prefix)
    return cleaned


def _looks_like_material_name(name: str) -> bool:
    cleaned = str(name).strip()
    if not cleaned or len(cleaned) > 40:
        return False
    return not cleaned.startswith(("用途", "写作规则", "Reader ", "Core ", "Stakes"))


__all__ = [
    "EntityRecord",
    "EntityRegistry",
    "EntityStatus",
    "EntityType",
    "build_entity_registry",
    "canonical_character_name",
    "material_relative_path",
]
