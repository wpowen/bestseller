"""Prepare anonymized source packages for novel distillation."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import hmac
import json
from pathlib import Path
import re
import secrets
import shutil
from typing import Any, Literal

from bestseller.services.distillation_book_parser import (
    ChapterSlice,
    ParsedBook,
    normalize_title_key,
    parse_source_book,
)

PIPELINE_VERSION = "distillation-v1"
TITLE_SALT_FILENAME = "source_title_hash.salt"
REPO_REGISTRY_PATH = Path("data/distillation/source_registry.index.json")
PRIVATE_REGISTRY_FILENAME = "source_registry.private.json"
DUPLICATE_LOG_FILENAME = "duplicate_sources.jsonl"
SOURCE_ID_RE = re.compile(r"^source-[0-9]{4,}$")
DedupePolicy = Literal["skip", "error", "allow"]


@dataclass(frozen=True)
class PrepareSourceResult:
    source_id: str
    skipped: bool
    duplicate_of: str | None
    repo_dir: str | None
    private_dir: str | None
    source_format: str
    encoding: str
    chapter_count: int
    volume_count: int
    source_hash_sha256: str
    title_key_hmac_sha256: str
    parser_warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class DuplicateSourceTitleError(ValueError):
    """Raised when a duplicate title is found and policy is ``error``."""


def prepare_source(
    source_path: Path,
    source_id: str,
    repo_root: Path,
    private_root: Path,
    *,
    dedupe_policy: DedupePolicy = "skip",
    rights_status: str = "user_supplied_for_analysis",
    genre_hint: str | None = None,
) -> PrepareSourceResult:
    if not SOURCE_ID_RE.match(source_id):
        raise ValueError("source_id must match source-NNNN, for example source-0001")
    if dedupe_policy not in {"skip", "error", "allow"}:
        raise ValueError("dedupe_policy must be one of: skip, error, allow")

    source_path = source_path.resolve()
    repo_root = repo_root.resolve()
    private_root = private_root.resolve()
    private_root.mkdir(parents=True, exist_ok=True)

    raw = source_path.read_bytes()
    raw_hash = hashlib.sha256(raw).hexdigest()
    parsed = parse_source_book(source_path)
    source_title = _private_title(parsed, source_path)
    title_key = normalize_title_key(source_title) or normalize_title_key(source_path.stem)
    title_digest = _title_digest(title_key, private_root)

    registry_path = repo_root / REPO_REGISTRY_PATH
    registry = _load_repo_registry(registry_path)
    duplicate_entry = _find_duplicate_by_title(registry, title_digest, source_id)
    if duplicate_entry is not None and dedupe_policy != "allow":
        duplicate_of = str(duplicate_entry.get("canonical_source_id") or "")
        _append_duplicate_log(
            private_root,
            {
                "source_id": source_id,
                "duplicate_of": duplicate_of,
                "source_hash_sha256": raw_hash,
                "source_format": parsed.source_format,
                "title": source_title,
                "title_key": title_key,
                "title_key_hmac_sha256": title_digest,
                "action": dedupe_policy,
            },
        )
        if dedupe_policy == "error":
            raise DuplicateSourceTitleError(
                f"{source_id} duplicates title key already registered as {duplicate_of}"
            )
        return PrepareSourceResult(
            source_id=source_id,
            skipped=True,
            duplicate_of=duplicate_of,
            repo_dir=None,
            private_dir=None,
            source_format=parsed.source_format,
            encoding=parsed.encoding,
            chapter_count=len(parsed.chapters),
            volume_count=len({chapter.volume_no for chapter in parsed.chapters}),
            source_hash_sha256=raw_hash,
            title_key_hmac_sha256=title_digest,
            parser_warnings=parsed.parser_warnings,
        )

    repo_dir = repo_root / "data" / "distillation" / source_id
    private_dir = private_root / source_id
    repo_dir.mkdir(parents=True, exist_ok=True)
    private_dir.mkdir(parents=True, exist_ok=True)

    _write_private_source_files(source_path, private_dir, parsed)
    _write_chapter_chunks_and_payloads(
        parsed,
        source_id=source_id,
        private_dir=private_dir,
        genre_hint=genre_hint,
    )
    _write_repo_package(
        parsed,
        source_id=source_id,
        repo_dir=repo_dir,
        raw_hash=raw_hash,
        title_digest=title_digest,
        rights_status=rights_status,
        duplicate_of=str(duplicate_entry.get("canonical_source_id")) if duplicate_entry else None,
        genre_hint=genre_hint,
    )

    _upsert_repo_registry(
        registry,
        {
            "title_key_hmac_sha256": title_digest,
            "canonical_source_id": str(duplicate_entry.get("canonical_source_id"))
            if duplicate_entry
            else source_id,
            "source_ids": [source_id],
            "source_hashes_sha256": [raw_hash],
            "source_formats": [parsed.source_format],
        },
    )
    _write_json(registry_path, registry)
    _upsert_private_registry(
        private_root,
        {
            "source_id": source_id,
            "title": source_title,
            "title_key": title_key,
            "author": parsed.metadata.author,
            "language": parsed.metadata.language,
            "metadata_source": parsed.metadata.metadata_source,
            "title_key_hmac_sha256": title_digest,
            "source_hash_sha256": raw_hash,
            "source_format": parsed.source_format,
        },
    )

    return PrepareSourceResult(
        source_id=source_id,
        skipped=False,
        duplicate_of=str(duplicate_entry.get("canonical_source_id")) if duplicate_entry else None,
        repo_dir=str(repo_dir),
        private_dir=str(private_dir),
        source_format=parsed.source_format,
        encoding=parsed.encoding,
        chapter_count=len(parsed.chapters),
        volume_count=len({chapter.volume_no for chapter in parsed.chapters}),
        source_hash_sha256=raw_hash,
        title_key_hmac_sha256=title_digest,
        parser_warnings=parsed.parser_warnings,
    )


def _private_title(parsed: ParsedBook, source_path: Path) -> str:
    title = (parsed.metadata.title or "").strip()
    return title or source_path.stem


def _title_digest(title_key: str, private_root: Path) -> str:
    salt_path = private_root / TITLE_SALT_FILENAME
    if not salt_path.exists():
        salt_path.write_text(secrets.token_hex(32) + "\n", encoding="utf-8")
    salt = salt_path.read_text(encoding="utf-8").strip().encode("utf-8")
    return hmac.new(salt, title_key.encode("utf-8"), hashlib.sha256).hexdigest()


def _load_repo_registry(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {
            "version": 1,
            "hash_algorithm": "hmac-sha256",
            "privacy": "title hash uses salt stored outside repository",
            "entries": [],
        }
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected JSON object")
    entries = data.setdefault("entries", [])
    if not isinstance(entries, list):
        raise ValueError(f"{path}: entries must be a list")
    return data


def _find_duplicate_by_title(
    registry: dict[str, Any],
    title_digest: str,
    source_id: str,
) -> dict[str, Any] | None:
    for entry in registry.get("entries") or []:
        if not isinstance(entry, dict):
            continue
        if entry.get("title_key_hmac_sha256") != title_digest:
            continue
        source_ids = entry.get("source_ids")
        if isinstance(source_ids, list) and source_id in source_ids:
            return None
        if entry.get("canonical_source_id") == source_id:
            return None
        return entry
    return None


def _upsert_repo_registry(registry: dict[str, Any], new_entry: dict[str, Any]) -> None:
    entries = registry.setdefault("entries", [])
    if not isinstance(entries, list):
        raise ValueError("registry entries must be a list")
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        if entry.get("title_key_hmac_sha256") != new_entry["title_key_hmac_sha256"]:
            continue
        _unique_extend(entry, "source_ids", new_entry["source_ids"])
        _unique_extend(entry, "source_hashes_sha256", new_entry["source_hashes_sha256"])
        _unique_extend(entry, "source_formats", new_entry["source_formats"])
        entry.setdefault("canonical_source_id", new_entry["canonical_source_id"])
        return
    entries.append(new_entry)


def _upsert_private_registry(private_root: Path, new_entry: dict[str, Any]) -> None:
    path = private_root / PRIVATE_REGISTRY_FILENAME
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError(f"{path}: expected JSON object")
    else:
        data = {"version": 1, "entries": []}
    entries = data.setdefault("entries", [])
    if not isinstance(entries, list):
        raise ValueError(f"{path}: entries must be a list")
    for idx, entry in enumerate(entries):
        if isinstance(entry, dict) and entry.get("source_id") == new_entry["source_id"]:
            entries[idx] = new_entry
            _write_json(path, data)
            return
    entries.append(new_entry)
    _write_json(path, data)


def _unique_extend(entry: dict[str, Any], key: str, values: list[str]) -> None:
    existing = entry.setdefault(key, [])
    if not isinstance(existing, list):
        entry[key] = []
        existing = entry[key]
    for value in values:
        if value not in existing:
            existing.append(value)


def _append_duplicate_log(private_root: Path, payload: dict[str, Any]) -> None:
    path = private_root / DUPLICATE_LOG_FILENAME
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


def _write_private_source_files(source_path: Path, private_dir: Path, parsed: ParsedBook) -> None:
    raw_dir = private_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source_path, raw_dir / f"source.original.{parsed.source_format}")
    (raw_dir / "source.normalized.txt").write_text(parsed.text + "\n", encoding="utf-8")


def _write_chapter_chunks_and_payloads(
    parsed: ParsedBook,
    *,
    source_id: str,
    private_dir: Path,
    genre_hint: str | None,
) -> None:
    chunk_dir = private_dir / "chunks"
    payload_dir = private_dir / "llm_payloads"
    chunk_dir.mkdir(parents=True, exist_ok=True)
    payload_dir.mkdir(parents=True, exist_ok=True)

    for chapter in parsed.chapters:
        (chunk_dir / f"chapter-{chapter.abs_chapter_no:04d}.txt").write_text(
            chapter.body + "\n",
            encoding="utf-8",
        )
        _write_json(
            payload_dir / f"chapter-{chapter.abs_chapter_no:04d}.prompt.json",
            _chapter_prompt_payload(source_id, chapter, parsed, genre_hint=genre_hint),
        )


def _write_repo_package(
    parsed: ParsedBook,
    *,
    source_id: str,
    repo_dir: Path,
    raw_hash: str,
    title_digest: str,
    rights_status: str,
    duplicate_of: str | None,
    genre_hint: str | None,
) -> None:
    chapter_count = len(parsed.chapters)
    volume_count = len({chapter.volume_no for chapter in parsed.chapters})
    average_chapter_chars = round(
        sum(len(chapter.body) for chapter in parsed.chapters) / max(chapter_count, 1)
    )
    boundary_types = sorted({chapter.boundary_type for chapter in parsed.chapters})
    manifest = {
        "source_id": source_id,
        "pipeline_version": PIPELINE_VERSION,
        "source_hash_sha256": raw_hash,
        "source_format": parsed.source_format,
        "encoding": parsed.encoding,
        "title_key_hmac_sha256": title_digest,
        "title_signal_source": parsed.metadata.metadata_source,
        "has_author_metadata": bool(parsed.metadata.author),
        "language": parsed.metadata.language,
        "rights_status": rights_status,
        "redaction_policy": {
            "store_source_title_in_repo": False,
            "store_author_in_repo": False,
            "store_raw_text_in_repo": False,
        },
        "dedupe": {
            "strategy": "private-title-key-hmac",
            "duplicate_of": duplicate_of,
        },
        "parse_profile": {
            "chapter_count": chapter_count,
            "volume_count": volume_count,
            "average_chapter_chars": average_chapter_chars,
            "boundary_types": boundary_types,
            "parser_warnings": list(parsed.parser_warnings),
        },
    }
    if genre_hint:
        manifest["genre_hint"] = genre_hint
    _write_json(repo_dir / "source_manifest.json", manifest)

    chapter_index = {
        "source_id": source_id,
        "pipeline_version": PIPELINE_VERSION,
        "source_format": parsed.source_format,
        "encoding": parsed.encoding,
        "chapter_count": chapter_count,
        "volume_count": volume_count,
        "average_chapter_chars": average_chapter_chars,
        "chapters": [_chapter_index_row(source_id, chapter) for chapter in parsed.chapters],
    }
    _write_json(repo_dir / "chapters.index.json", chapter_index)

    jobs = [
        {
            "job_id": f"{source_id}-chapter-{chapter.abs_chapter_no:04d}",
            "source_id": source_id,
            "abs_chapter_no": chapter.abs_chapter_no,
            "source_format": parsed.source_format,
            "boundary_type": chapter.boundary_type,
            "private_payload_ref": (
                f".distillation_private/{source_id}/llm_payloads/"
                f"chapter-{chapter.abs_chapter_no:04d}.prompt.json"
            ),
            "expected_output_schema": "data/distillation/schemas/chapter_card.schema.json",
            "repo_output_target": f"data/distillation/{source_id}/chapter_cards.jsonl",
            "status": "pending_external_llm",
        }
        for chapter in parsed.chapters
    ]
    _write_jsonl(repo_dir / "llm_jobs" / "chapter_jobs.index.jsonl", jobs)


def _chapter_index_row(source_id: str, chapter: ChapterSlice) -> dict[str, Any]:
    private_ref = (
        f".distillation_private/{source_id}/chunks/chapter-{chapter.abs_chapter_no:04d}.txt"
    )
    return {
        "abs_chapter_no": chapter.abs_chapter_no,
        "volume_no": chapter.volume_no,
        "volume_label_redacted": f"volume-{chapter.volume_no:02d}",
        "chapter_label_redacted": f"chapter-{chapter.abs_chapter_no:04d}",
        "boundary_type": chapter.boundary_type,
        "title_hash_sha256": _sha256_text(chapter.title),
        "title_char_count": len(chapter.title),
        "char_count": len(chapter.body),
        "line_count": len(chapter.body.splitlines()),
        "private_chunk_ref": private_ref,
    }


def _chapter_prompt_payload(
    source_id: str,
    chapter: ChapterSlice,
    parsed: ParsedBook,
    *,
    genre_hint: str | None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "task_type": "chapter_card_extraction",
        "source_id": source_id,
        "source_format": parsed.source_format,
        "abs_chapter_no": chapter.abs_chapter_no,
        "volume_no": chapter.volume_no,
        "boundary_type": chapter.boundary_type,
        "chapter_title_redacted": f"chapter-{chapter.abs_chapter_no:04d}",
        "system": (
            "You extract reusable story-design mechanics from a source novel chapter. "
            "Do not summarize prose. Do not preserve source-specific names. Do not imitate "
            "style. Output exactly one JSON object matching the chapter_card schema. "
            "All names, artifacts, places, organizations, techniques, and unique event chains "
            "must be replaced with role labels."
        ),
        "schema_ref": "data/distillation/schemas/chapter_card.schema.json",
        "chapter_text": chapter.body,
    }
    if genre_hint:
        payload["genre_hint"] = genre_hint
    return payload


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )
