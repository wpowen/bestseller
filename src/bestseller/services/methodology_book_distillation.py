"""Prepare writing-methodology books for safe distillation.

This module is the theory-book sibling of the existing source-novel
distillation preparer.  It keeps raw book text in a private directory and only
writes repo-safe manifests, section indexes, and LLM job manifests.  Later LLM
steps can turn those section payloads into ``MethodologyCard`` assets without
ever committing source prose or source titles.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
import hashlib
import hmac
import json
from pathlib import Path
import re
import secrets
import shutil
from typing import Any, Literal, cast, get_args

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows
    fcntl = None  # type: ignore[assignment]

from pydantic import BaseModel, ConfigDict, Field, field_validator
import yaml

from bestseller.services.distillation_book_parser import (
    ChapterSlice,
    ParsedBook,
    normalize_title_key,
    parse_source_book,
)
from bestseller.services.distillation_source_preparer import DuplicateSourceTitleError
from bestseller.services.methodology_cards import (
    MethodologyCard,
    MethodologyCardDeck,
    MethodologyCategory,
    MethodologyGateBinding,
    MethodologyGateMode,
    MethodologyScope,
    MethodologyStage,
)

PIPELINE_VERSION = "methodology-book-distillation-v1"
TITLE_SALT_FILENAME = "methodology_book_title_hash.salt"
REPO_REGISTRY_PATH = Path("data/methodology_books/source_registry.index.json")
PRIVATE_REGISTRY_FILENAME = "source_registry.private.json"
DUPLICATE_LOG_FILENAME = "duplicate_sources.jsonl"
SOURCE_ID_RE = re.compile(r"^source-[0-9]{4,}$")
DedupePolicy = Literal["skip", "error", "allow"]


class MethodologyBookGateBinding(BaseModel, frozen=True):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    gate: str = Field(min_length=1)
    default_mode: str = "warn"


class MethodologyCandidate(BaseModel, frozen=True):
    """One extracted transferable writing method from a theory-book section."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    candidate_id: str = Field(pattern=r"^[a-z0-9_.-]+$")
    source_id: str = Field(pattern=r"^source-[0-9]{4,}$")
    section_id: str = Field(pattern=r"^sec-[0-9]{4}$")
    title: str = Field(min_length=1)
    category: str = Field(min_length=1)
    scope: tuple[str, ...] = Field(min_length=1)
    stage: tuple[str, ...] = Field(min_length=1)
    core_claim: str = Field(min_length=1)
    operating_steps: tuple[str, ...] = Field(default_factory=tuple)
    anti_patterns: tuple[str, ...] = Field(default_factory=tuple)
    required_contract_fields: tuple[str, ...] = Field(default_factory=tuple)
    framework_bindings: tuple[str, ...] = Field(min_length=1)
    gate_bindings: tuple[MethodologyBookGateBinding, ...] = Field(default_factory=tuple)
    alignment_terms: tuple[str, ...] = Field(default_factory=tuple)
    conflicts_with: tuple[str, ...] = Field(default_factory=tuple)
    confidence: float = Field(ge=0.0, le=1.0)

    @field_validator(
        "alignment_terms",
        "anti_patterns",
        "conflicts_with",
        "framework_bindings",
        "operating_steps",
        "required_contract_fields",
        "scope",
        "stage",
    )
    @classmethod
    def _validate_unique_text_tuple(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(item.strip() for item in value if item and item.strip())
        if len(normalized) != len(value):
            raise ValueError("items must be non-empty strings")
        if len(set(normalized)) != len(normalized):
            raise ValueError("items must be unique")
        return normalized


class MethodologyCandidateDeck(BaseModel, frozen=True):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    candidates: tuple[MethodologyCandidate, ...] = Field(default_factory=tuple)


@dataclass(frozen=True)
class PrepareMethodologyBookResult:
    source_id: str
    skipped: bool
    duplicate_of: str | None
    repo_dir: str | None
    private_dir: str | None
    source_format: str
    encoding: str
    section_count: int
    source_hash_sha256: str
    title_key_hmac_sha256: str
    parser_warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def prepare_methodology_book(
    source_path: Path,
    source_id: str,
    repo_root: Path,
    private_root: Path,
    *,
    dedupe_policy: DedupePolicy = "skip",
    rights_status: str = "user_supplied_for_analysis",
    source_family: str = "writing_methodology_books",
    language_hint: str | None = None,
) -> PrepareMethodologyBookResult:
    """Prepare a source theory book without storing raw text in the repository."""

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

    registry_path = repo_root / REPO_REGISTRY_PATH
    with _methodology_book_registry_lock(repo_root):
        title_digest = _title_digest(title_key, private_root)
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
            return PrepareMethodologyBookResult(
                source_id=source_id,
                skipped=True,
                duplicate_of=duplicate_of,
                repo_dir=None,
                private_dir=None,
                source_format=parsed.source_format,
                encoding=parsed.encoding,
                section_count=len(parsed.chapters),
                source_hash_sha256=raw_hash,
                title_key_hmac_sha256=title_digest,
                parser_warnings=parsed.parser_warnings,
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
                "source_family": source_family,
            },
        )
        _write_json(registry_path, registry)

    repo_dir = repo_root / "data" / "methodology_books" / source_id
    private_dir = private_root / source_id
    repo_dir.mkdir(parents=True, exist_ok=True)
    private_dir.mkdir(parents=True, exist_ok=True)

    _write_private_source_files(source_path, private_dir, parsed)
    _write_section_chunks_and_payloads(
        parsed,
        source_id=source_id,
        private_dir=private_dir,
        language_hint=language_hint,
    )
    _write_repo_package(
        parsed,
        source_id=source_id,
        repo_dir=repo_dir,
        raw_hash=raw_hash,
        title_digest=title_digest,
        rights_status=rights_status,
        source_family=source_family,
        language_hint=language_hint,
    )

    with _methodology_book_registry_lock(repo_root):
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

    return PrepareMethodologyBookResult(
        source_id=source_id,
        skipped=False,
        duplicate_of=None,
        repo_dir=str(repo_dir),
        private_dir=str(private_dir),
        source_format=parsed.source_format,
        encoding=parsed.encoding,
        section_count=len(parsed.chapters),
        source_hash_sha256=raw_hash,
        title_key_hmac_sha256=title_digest,
        parser_warnings=parsed.parser_warnings,
    )


def validate_methodology_book_package(package_dir: Path) -> tuple[str, ...]:
    """Return repo-safety and completeness errors for a prepared package."""

    errors: list[str] = []
    for name in ("source_manifest.json", "sections.index.json"):
        if not (package_dir / name).is_file():
            errors.append(f"missing required file: {name}")
    jobs_path = package_dir / "llm_jobs" / "section_jobs.index.jsonl"
    if not jobs_path.is_file():
        errors.append("missing required file: llm_jobs/section_jobs.index.jsonl")

    for path in (package_dir / "source_manifest.json", package_dir / "sections.index.json"):
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            errors.append(f"{path.name} invalid JSON: {exc}")
            continue
        errors.extend(_scan_sensitive_values(data, path=path.name))

    if jobs_path.is_file():
        for idx, row in enumerate(_read_jsonl(jobs_path), start=1):
            private_ref = str(row.get("private_payload_ref") or "")
            if not private_ref.startswith(".methodology_private/"):
                errors.append(f"section job {idx}: private_payload_ref must stay private")
            errors.extend(_scan_sensitive_values(row, path=f"section_jobs[{idx}]"))

    return tuple(errors)


def load_methodology_candidates(path: Path) -> MethodologyCandidateDeck:
    rows = _read_jsonl(path)
    candidate_rows: list[dict[str, Any]] = []
    for row in rows:
        nested = row.get("candidates")
        if isinstance(nested, list):
            candidate_rows.extend(item for item in nested if isinstance(item, dict))
        else:
            candidate_rows.append(row)
    return MethodologyCandidateDeck(
        candidates=tuple(MethodologyCandidate.model_validate(row) for row in candidate_rows)
    )


def candidates_to_methodology_cards(
    deck: MethodologyCandidateDeck,
    *,
    id_prefix: str = "writing_books",
    min_confidence: float = 0.65,
) -> MethodologyCardDeck:
    """Convert reviewed candidates into framework-native MethodologyCard rows."""

    cards: list[MethodologyCard] = []
    for candidate in deck.candidates:
        if candidate.confidence < min_confidence:
            continue
        source_ref = f"{candidate.source_id}.{candidate.section_id}"
        card_id = f"{id_prefix}.{source_ref}.{candidate.candidate_id}"
        cards.append(
            MethodologyCard(
                id=card_id,
                source_ids=(source_ref,),
                title=candidate.title,
                category=_methodology_category(candidate.category),
                scope=_methodology_scope(candidate.scope),
                stage=_methodology_stage(candidate.stage),
                core_claim=candidate.core_claim,
                anti_patterns=candidate.anti_patterns,
                required_contract_fields=candidate.required_contract_fields,
                framework_bindings=candidate.framework_bindings,
                gate_bindings=tuple(
                    MethodologyGateBinding(
                        gate=binding.gate,
                        default_mode=_methodology_gate_mode(binding.default_mode),
                    )
                    for binding in candidate.gate_bindings
                ),
                maturity="draft",
            )
        )
    return MethodologyCardDeck(cards=tuple(cards))


def _methodology_category(value: str) -> str:
    allowed = set(get_args(MethodologyCategory))
    return value if value in allowed else "scene_design"


def _methodology_scope(values: tuple[str, ...]) -> tuple[str, ...]:
    allowed = set(get_args(MethodologyScope))
    normalized = tuple(value for value in values if value in allowed)
    return normalized or ("scene",)


def _methodology_stage(values: tuple[str, ...]) -> tuple[str, ...]:
    allowed = set(get_args(MethodologyStage))
    normalized = tuple(value for value in values if value in allowed)
    return normalized or ("planning",)


def _methodology_gate_mode(value: str) -> MethodologyGateMode:
    allowed = set(get_args(MethodologyGateMode))
    if value in allowed:
        return cast(MethodologyGateMode, value)
    return "advisory"


def write_methodology_cards_yaml(path: Path, deck: MethodologyCardDeck) -> None:
    payload = {"cards": [card.model_dump(mode="json") for card in deck.cards]}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")


@contextmanager
def _methodology_book_registry_lock(repo_root: Path) -> Iterator[None]:
    lock_path = repo_root / "data" / "methodology_books" / ".prepare_source.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    if fcntl is None:
        yield
        return
    with lock_path.open("a+b") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _private_title(parsed: ParsedBook, source_path: Path) -> str:
    return (parsed.metadata.title or "").strip() or source_path.stem


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


def _write_section_chunks_and_payloads(
    parsed: ParsedBook,
    *,
    source_id: str,
    private_dir: Path,
    language_hint: str | None,
) -> None:
    chunk_dir = private_dir / "sections"
    payload_dir = private_dir / "llm_payloads"
    chunk_dir.mkdir(parents=True, exist_ok=True)
    payload_dir.mkdir(parents=True, exist_ok=True)

    for section in parsed.chapters:
        section_id = _section_id(section)
        (chunk_dir / f"{section_id}.txt").write_text(section.body + "\n", encoding="utf-8")
        _write_json(
            payload_dir / f"{section_id}.prompt.json",
            _section_prompt_payload(source_id, section, parsed, language_hint=language_hint),
        )


def _write_repo_package(
    parsed: ParsedBook,
    *,
    source_id: str,
    repo_dir: Path,
    raw_hash: str,
    title_digest: str,
    rights_status: str,
    source_family: str,
    language_hint: str | None,
) -> None:
    section_count = len(parsed.chapters)
    average_section_chars = round(
        sum(len(section.body) for section in parsed.chapters) / max(section_count, 1)
    )
    boundary_types = sorted({section.boundary_type for section in parsed.chapters})
    manifest = {
        "source_id": source_id,
        "pipeline_version": PIPELINE_VERSION,
        "source_family": source_family,
        "source_hash_sha256": raw_hash,
        "source_format": parsed.source_format,
        "encoding": parsed.encoding,
        "title_key_hmac_sha256": title_digest,
        "title_signal_source": parsed.metadata.metadata_source,
        "has_author_metadata": bool(parsed.metadata.author),
        "language": parsed.metadata.language or language_hint,
        "rights_status": rights_status,
        "redaction_policy": {
            "store_source_title_in_repo": False,
            "store_author_in_repo": False,
            "store_raw_text_in_repo": False,
        },
        "parse_profile": {
            "section_count": section_count,
            "average_section_chars": average_section_chars,
            "boundary_types": boundary_types,
            "parser_warnings": list(parsed.parser_warnings),
        },
        "outputs": {
            "candidate_schema": "data/methodology_books/schemas/methodology_candidate.schema.json",
            "review_candidates": (
                f"data/methodology_books/{source_id}/methodology_candidates.review.jsonl"
            ),
            "review_cards": f"data/methodology_books/{source_id}/methodology_cards.review.yaml",
        },
    }
    _write_json(repo_dir / "source_manifest.json", manifest)

    sections = {
        "source_id": source_id,
        "pipeline_version": PIPELINE_VERSION,
        "source_format": parsed.source_format,
        "encoding": parsed.encoding,
        "section_count": section_count,
        "average_section_chars": average_section_chars,
        "sections": [_section_index_row(source_id, section) for section in parsed.chapters],
    }
    _write_json(repo_dir / "sections.index.json", sections)

    jobs = [
        {
            "job_id": f"{source_id}-{_section_id(section)}",
            "source_id": source_id,
            "section_id": _section_id(section),
            "source_format": parsed.source_format,
            "boundary_type": section.boundary_type,
            "private_payload_ref": (
                f".methodology_private/{source_id}/llm_payloads/"
                f"{_section_id(section)}.prompt.json"
            ),
            "expected_output_schema": (
                "data/methodology_books/schemas/methodology_candidate.schema.json"
            ),
            "repo_output_target": (
                f"data/methodology_books/{source_id}/methodology_candidates.review.jsonl"
            ),
            "status": "pending_external_llm",
        }
        for section in parsed.chapters
    ]
    _write_jsonl(repo_dir / "llm_jobs" / "section_jobs.index.jsonl", jobs)


def _section_id(section: ChapterSlice) -> str:
    return f"sec-{section.abs_chapter_no:04d}"


def _section_index_row(source_id: str, section: ChapterSlice) -> dict[str, Any]:
    section_id = _section_id(section)
    return {
        "section_id": section_id,
        "source_id": source_id,
        "abs_section_no": section.abs_chapter_no,
        "volume_no": section.volume_no,
        "volume_label_redacted": f"volume-{section.volume_no:02d}",
        "section_label_redacted": section_id,
        "boundary_type": section.boundary_type,
        "heading_hash_sha256": _sha256_text(section.title),
        "heading_char_count": len(section.title),
        "char_count": len(section.body),
        "line_count": len(section.body.splitlines()),
        "private_chunk_ref": f".methodology_private/{source_id}/sections/{section_id}.txt",
    }


def _section_prompt_payload(
    source_id: str,
    section: ChapterSlice,
    parsed: ParsedBook,
    *,
    language_hint: str | None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "task_type": "writing_methodology_section_extraction",
        "source_id": source_id,
        "source_format": parsed.source_format,
        "section_id": _section_id(section),
        "abs_section_no": section.abs_chapter_no,
        "boundary_type": section.boundary_type,
        "section_title_redacted": _section_id(section),
        "system": (
            "# ROLE\n"
            "You extract reusable writing methodology from ONE section of a craft book.\n"
            "You are not a summarizer. Convert advice into executable framework rules.\n"
            "\n"
            "# COPYRIGHT AND PRIVACY CONTRACT\n"
            "- Do not quote long passages. Do not preserve distinctive source phrasing.\n"
            "- Never output the book title, author name, file path, or source-specific examples.\n"
            "- Keep only abstract, transferable methods, checks, and repair actions.\n"
            "\n"
            "# METHOD CARD REQUIREMENTS\n"
            "For each candidate method, provide: title, category, scope, stage, core_claim,\n"
            "operating_steps, anti_patterns, required_contract_fields, framework_bindings,\n"
            "gate_bindings, alignment_terms, conflicts_with, and confidence.\n"
            "\n"
            "# ALIGNMENT TASK\n"
            "Map each method to common craft concepts when possible: scene/sequel,\n"
            "goal-obstacle-result, want-vs-need, POV distance, setup/payoff,\n"
            "show-don't-tell, snowflake expansion, action-reaction, revision pass.\n"
            "\n"
            "# OUTPUT\n"
            "Output exactly one JSON object: {\"candidates\": [...]}. No markdown fences."
        ),
        "schema_ref": "data/methodology_books/schemas/methodology_candidate.schema.json",
        "section_text": section.body,
    }
    if language_hint or parsed.metadata.language:
        payload["language_hint"] = language_hint or parsed.metadata.language
    return payload


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
        for pattern in ("/Users/", "\\Users\\", ".epub", ".mobi", ".azw3", "z-library", "z-lib"):
            if pattern in value:
                findings.append(f"{path}: contains sensitive pattern {pattern!r}")
    return findings


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.is_file():
        return rows
    for line_no, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        stripped = raw.strip()
        if not stripped:
            continue
        obj = json.loads(stripped)
        if not isinstance(obj, dict):
            raise ValueError(f"{path}:{line_no}: expected JSON object")
        rows.append(obj)
    return rows


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


__all__ = [
    "MethodologyCandidate",
    "MethodologyCandidateDeck",
    "PrepareMethodologyBookResult",
    "candidates_to_methodology_cards",
    "load_methodology_candidates",
    "prepare_methodology_book",
    "validate_methodology_book_package",
    "write_methodology_cards_yaml",
]
