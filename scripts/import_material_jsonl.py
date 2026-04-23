"""External hook for feeding the multi-dimensional material library.

This CLI lets **any** producer — another LLM, a human curator, a
one-shot scraping script, a batch export from an external knowledge
base — push structured material entries into the shared
``material_library`` table without having to know anything about the
internal ORM, embeddings, or feature flags.

The contract is a single JSONL file: one JSON object per line, each one
matching the :class:`MaterialEntry` dataclass.  See
``docs/material_import_schema.md`` for the full schema + worked examples
you can hand to an external LLM as a prompt instruction.

Why a standalone CLI and not an HTTP endpoint?

* Works for offline batch jobs (nightly Curator sweeps, manual imports).
* No network exposure — the operator keeps full control over what
  reaches the library.
* The same process that ingests the data also computes embeddings,
  upserts idempotently, and (optionally) runs the novelty critic, so
  the data ends up indistinguishable from Curator/Research Agent output.

Safety / invariants
-------------------
* **Idempotent upsert.**  Re-running on the same JSONL updates existing
  rows by ``(dimension, slug)``; ``usage_count`` / ``last_used_at`` are
  preserved (see :func:`material_library.insert_entry`).
* **Dry run is opt-in — and recommended before any bulk import.**
  ``--dry-run`` parses + validates every row, reports summary, and
  never touches the DB.
* **Novelty gate is optional.**  Pass ``--novelty-guard`` to run the
  Batch-3 novelty critic on each row before upsert.  Off by default so
  that an initial seed import doesn't get blocked by an empty library
  gate.
* **Source type defaults to ``web_import``.**  Override per-row via the
  entry's own ``source_type`` field or globally with ``--source-type``.
* **Existing novels are not affected.**  Imports only populate the
  global library — no project material rows are created and no running
  generation pipeline is touched.  Old projects opt in to use the
  library by toggling ``enable_library_soft_reference`` (or the full
  Batch 2 Forge stack).

Usage
-----

    # Preview a file — no DB writes
    python scripts/import_material_jsonl.py data/seed_materials/xianxia_seed.jsonl --dry-run

    # Real import
    python scripts/import_material_jsonl.py data/seed_materials/xianxia_seed.jsonl

    # Force source_type for every row (ignores per-row field)
    python scripts/import_material_jsonl.py data/seed_materials/xianxia_seed.jsonl \\
        --source-type user_curated

    # Import with novelty guard (Batch 3 must be wired up)
    python scripts/import_material_jsonl.py data/seed_materials/xianxia_seed.jsonl \\
        --novelty-guard

    # JSON output for dashboards / pipes
    python scripts/import_material_jsonl.py data/seed_materials/xianxia_seed.jsonl --format json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

# Path shim — scripts/ isn't a package; mirror the pattern used by other scripts.
_THIS = Path(__file__).resolve()
_SRC = _THIS.parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from bestseller.infra.db.session import session_scope  # noqa: E402
from bestseller.services.library_curator import (  # noqa: E402
    _MATERIAL_DIMENSIONS,
)
from bestseller.services.material_library import (  # noqa: E402
    MaterialEntry,
    insert_entry,
)

logger = logging.getLogger("import_material_jsonl")


# ── Allowed constants ─────────────────────────────────────────────────


_ALLOWED_SOURCE_TYPES: frozenset[str] = frozenset(
    {
        "research_agent",
        "llm_synth",
        "user_curated",
        "web_import",
        "mcp_pull",
    }
)

# Mirrors library_curator._MATERIAL_DIMENSIONS — we import the tuple so
# the allow-list is always in sync with the Curator.
_ALLOWED_DIMENSIONS: frozenset[str] = frozenset(_MATERIAL_DIMENSIONS)

_ALLOWED_STATUSES: frozenset[str] = frozenset({"active", "deprecated", "review"})


# ── Validation DTO ────────────────────────────────────────────────────


@dataclass(frozen=True)
class ImportRowResult:
    """Per-row outcome from a single JSONL line."""

    line_no: int
    status: str  # "inserted" | "updated" | "rejected" | "skipped"
    dimension: str | None
    slug: str | None
    reason: str | None = None


@dataclass(frozen=True)
class ImportSummary:
    file: str
    total_rows: int
    inserted_or_updated: int
    rejected: int
    skipped_by_novelty_guard: int
    dry_run: bool
    rows: tuple[ImportRowResult, ...] = field(default_factory=tuple)


# ── JSONL parse + validation ──────────────────────────────────────────


def _parse_jsonl(path: Path) -> list[tuple[int, dict[str, Any]]]:
    """Read the file and yield ``(line_no, parsed_obj)`` pairs.

    Blank lines and ``#`` comments are skipped.  Parse errors raise
    :class:`ValueError` with the offending line number so the operator
    knows what to fix.
    """

    out: list[tuple[int, dict[str, Any]]] = []
    with path.open("r", encoding="utf-8") as fh:
        for line_no, raw in enumerate(fh, start=1):
            stripped = raw.strip()
            if not stripped or stripped.startswith("#"):
                continue
            try:
                obj = json.loads(stripped)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"{path}:{line_no}: invalid JSON — {exc.msg} (column {exc.colno})"
                ) from exc
            if not isinstance(obj, dict):
                raise ValueError(
                    f"{path}:{line_no}: top-level must be an object, got "
                    f"{type(obj).__name__}"
                )
            out.append((line_no, obj))
    return out


def _coerce_entry(
    obj: dict[str, Any],
    *,
    source_type_override: str | None,
    default_status: str,
) -> MaterialEntry:
    """Turn a raw JSON dict into a :class:`MaterialEntry`.

    Raises :class:`ValueError` on any schema violation — the caller is
    responsible for mapping the error to a per-row ``rejected`` result.
    """

    # Required fields ------------------------------------------------------
    required = ("dimension", "slug", "name", "narrative_summary")
    for key in required:
        if not obj.get(key) or not isinstance(obj.get(key), str):
            raise ValueError(f"missing or non-string field '{key}'")

    dimension = obj["dimension"].strip()
    if dimension not in _ALLOWED_DIMENSIONS:
        raise ValueError(
            f"unknown dimension '{dimension}'. "
            f"Allowed: {sorted(_ALLOWED_DIMENSIONS)}"
        )

    slug = obj["slug"].strip()
    if not slug or "/" in slug or " " in slug:
        raise ValueError(
            f"slug must be non-empty and contain no '/' or whitespace "
            f"(got {slug!r})"
        )

    # Optional content_json — default to empty dict ------------------------
    content_json = obj.get("content_json", {})
    if content_json is None:
        content_json = {}
    if not isinstance(content_json, dict):
        raise ValueError(
            f"content_json must be a JSON object, got {type(content_json).__name__}"
        )

    # Source type + citations ---------------------------------------------
    source_type = source_type_override or str(
        obj.get("source_type", "web_import")
    ).strip()
    if source_type not in _ALLOWED_SOURCE_TYPES:
        raise ValueError(
            f"unknown source_type '{source_type}'. "
            f"Allowed: {sorted(_ALLOWED_SOURCE_TYPES)}"
        )

    raw_citations = obj.get("source_citations") or obj.get("source_citations_json") or []
    if not isinstance(raw_citations, list):
        raise ValueError(
            "source_citations must be a list of objects "
            "(e.g., [{'title': 'Wiki', 'url': 'https://...'}])"
        )
    citations: list[dict[str, Any]] = []
    for item in raw_citations:
        if isinstance(item, str):
            citations.append({"url": item})
        elif isinstance(item, dict):
            citations.append(dict(item))
        else:
            raise ValueError(
                "each source_citations entry must be a string URL or an object"
            )

    # Tags / genre / sub_genre --------------------------------------------
    raw_tags = obj.get("tags") or obj.get("tags_json") or []
    if not isinstance(raw_tags, list):
        raise ValueError("tags must be a list of strings")
    tags = [str(t) for t in raw_tags if str(t).strip()]

    genre = obj.get("genre")
    if genre is not None and not isinstance(genre, str):
        raise ValueError("genre must be a string or null")
    sub_genre = obj.get("sub_genre")
    if sub_genre is not None and not isinstance(sub_genre, str):
        raise ValueError("sub_genre must be a string or null")

    # Confidence + coverage -----------------------------------------------
    confidence = float(obj.get("confidence", 0.5) or 0.0)
    if not 0.0 <= confidence <= 1.0:
        raise ValueError(f"confidence must be in [0,1], got {confidence}")
    coverage_score = obj.get("coverage_score")
    if coverage_score is not None:
        coverage_score = float(coverage_score)

    status = str(obj.get("status", default_status) or default_status).strip()
    if status not in _ALLOWED_STATUSES:
        raise ValueError(
            f"unknown status '{status}'. Allowed: {sorted(_ALLOWED_STATUSES)}"
        )

    embedding = obj.get("embedding")
    if embedding is not None:
        if not isinstance(embedding, list) or not all(
            isinstance(v, (int, float)) for v in embedding
        ):
            raise ValueError("embedding must be a list of numbers")
        embedding = [float(v) for v in embedding]

    return MaterialEntry(
        dimension=dimension,
        slug=slug,
        name=obj["name"].strip(),
        narrative_summary=obj["narrative_summary"].strip(),
        content_json=content_json,
        genre=genre,
        sub_genre=sub_genre,
        tags=tags,
        source_type=source_type,
        source_citations=citations,
        confidence=confidence,
        coverage_score=coverage_score,
        status=status,
        embedding=embedding,
    )


# ── Optional novelty-critic hook ──────────────────────────────────────


async def _novelty_guard_ok(entry: MaterialEntry) -> tuple[bool, str | None]:
    """Return ``(is_ok, reason_if_blocked)``.

    The novelty critic is a Batch-3 module; we import lazily so that
    this script works even when Batch 3 hasn't shipped.  If the module
    is missing or raises, we default-allow and emit a warning — the
    import must not silently fail because an orthogonal feature is off.
    """

    try:
        from bestseller.services import novelty_critic  # type: ignore
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "novelty_critic unavailable (%s) — skipping guard for this row",
            exc,
        )
        return True, None

    check = getattr(novelty_critic, "check_library_entry", None)
    if check is None:
        logger.warning(
            "novelty_critic.check_library_entry not implemented — "
            "skipping guard for this row"
        )
        return True, None

    try:
        verdict = await check(entry)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "novelty_critic raised (%s); defaulting to allow for row %s/%s",
            exc,
            entry.dimension,
            entry.slug,
        )
        return True, None

    ok = bool(getattr(verdict, "is_novel", True))
    reason = None if ok else str(getattr(verdict, "reason", "novelty guard blocked"))
    return ok, reason


# ── Main loop ─────────────────────────────────────────────────────────


async def _run_import(
    *,
    path: Path,
    source_type_override: str | None,
    default_status: str,
    dry_run: bool,
    novelty_guard: bool,
) -> ImportSummary:
    parsed = _parse_jsonl(path)
    results: list[ImportRowResult] = []
    inserted_or_updated = 0
    rejected = 0
    skipped_by_novelty = 0

    # Pre-validate everything so we bail out on schema errors before
    # opening a DB session.
    pre_entries: list[tuple[int, MaterialEntry | None, str | None]] = []
    for line_no, obj in parsed:
        try:
            entry = _coerce_entry(
                obj,
                source_type_override=source_type_override,
                default_status=default_status,
            )
            pre_entries.append((line_no, entry, None))
        except ValueError as exc:
            pre_entries.append((line_no, None, str(exc)))

    if dry_run:
        for line_no, entry, err in pre_entries:
            if err is not None:
                results.append(
                    ImportRowResult(
                        line_no=line_no,
                        status="rejected",
                        dimension=None,
                        slug=None,
                        reason=err,
                    )
                )
                rejected += 1
                continue
            assert entry is not None
            results.append(
                ImportRowResult(
                    line_no=line_no,
                    status="inserted",  # dry-run reports intent only
                    dimension=entry.dimension,
                    slug=entry.slug,
                    reason="dry-run (no DB write)",
                )
            )
            inserted_or_updated += 1
        return ImportSummary(
            file=str(path),
            total_rows=len(parsed),
            inserted_or_updated=inserted_or_updated,
            rejected=rejected,
            skipped_by_novelty_guard=0,
            dry_run=True,
            rows=tuple(results),
        )

    # Real import — one session, single commit at the end.
    async with session_scope() as session:
        for line_no, entry, err in pre_entries:
            if err is not None:
                results.append(
                    ImportRowResult(
                        line_no=line_no,
                        status="rejected",
                        dimension=None,
                        slug=None,
                        reason=err,
                    )
                )
                rejected += 1
                continue
            assert entry is not None

            if novelty_guard:
                ok, reason = await _novelty_guard_ok(entry)
                if not ok:
                    results.append(
                        ImportRowResult(
                            line_no=line_no,
                            status="skipped",
                            dimension=entry.dimension,
                            slug=entry.slug,
                            reason=reason or "novelty guard blocked",
                        )
                    )
                    skipped_by_novelty += 1
                    continue

            try:
                await insert_entry(session, entry)
                results.append(
                    ImportRowResult(
                        line_no=line_no,
                        status="inserted",
                        dimension=entry.dimension,
                        slug=entry.slug,
                    )
                )
                inserted_or_updated += 1
            except Exception as exc:  # noqa: BLE001
                logger.exception(
                    "insert_entry failed for %s/%s", entry.dimension, entry.slug
                )
                results.append(
                    ImportRowResult(
                        line_no=line_no,
                        status="rejected",
                        dimension=entry.dimension,
                        slug=entry.slug,
                        reason=f"DB error: {exc}",
                    )
                )
                rejected += 1

        if inserted_or_updated > 0:
            await session.commit()

    return ImportSummary(
        file=str(path),
        total_rows=len(parsed),
        inserted_or_updated=inserted_or_updated,
        rejected=rejected,
        skipped_by_novelty_guard=skipped_by_novelty,
        dry_run=False,
        rows=tuple(results),
    )


# ── Output rendering ──────────────────────────────────────────────────


def _render_text(summary: ImportSummary) -> str:
    header = (
        f"Material library import — {summary.file}\n"
        f"  mode: {'dry-run' if summary.dry_run else 'live'}\n"
        f"  total rows:         {summary.total_rows}\n"
        f"  inserted/updated:   {summary.inserted_or_updated}\n"
        f"  rejected:           {summary.rejected}\n"
        f"  skipped by novelty: {summary.skipped_by_novelty_guard}\n"
    )
    lines = [header]
    for row in summary.rows:
        loc = f"line {row.line_no:4d}"
        ident = (
            f"{row.dimension or '?':<22} {row.slug or '-':<36}"
            if row.dimension or row.slug
            else " " * 60
        )
        reason = f" — {row.reason}" if row.reason else ""
        lines.append(f"  [{row.status:<8}] {loc}  {ident}{reason}")
    return "\n".join(lines)


def _render_json(summary: ImportSummary) -> str:
    payload: dict[str, Any] = {
        "file": summary.file,
        "mode": "dry-run" if summary.dry_run else "live",
        "total_rows": summary.total_rows,
        "inserted_or_updated": summary.inserted_or_updated,
        "rejected": summary.rejected,
        "skipped_by_novelty_guard": summary.skipped_by_novelty_guard,
        "rows": [
            {
                "line_no": row.line_no,
                "status": row.status,
                "dimension": row.dimension,
                "slug": row.slug,
                "reason": row.reason,
            }
            for row in summary.rows
        ],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


# ── CLI entry point ───────────────────────────────────────────────────


def _parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Import JSONL material entries into the global material "
            "library.  Safe to re-run: idempotent by (dimension, slug)."
        ),
    )
    parser.add_argument(
        "path",
        type=Path,
        help="Path to a .jsonl file; one MaterialEntry per line.",
    )
    parser.add_argument(
        "--source-type",
        choices=sorted(_ALLOWED_SOURCE_TYPES),
        default=None,
        help=(
            "Override the source_type for every row (otherwise each row "
            "uses its own source_type field, falling back to web_import)."
        ),
    )
    parser.add_argument(
        "--default-status",
        choices=sorted(_ALLOWED_STATUSES),
        default="active",
        help="Status to assign to rows that don't set one (default: active).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse + validate only; do not touch the database.",
    )
    parser.add_argument(
        "--novelty-guard",
        action="store_true",
        help=(
            "Run the Batch-3 novelty critic on each row before upsert. "
            "Silently no-ops if the critic module is not installed."
        ),
    )
    parser.add_argument(
        "--format",
        choices=["text", "json"],
        default="text",
        help="Output format for the summary.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable DEBUG logging.",
    )
    return parser.parse_args(list(argv) if argv is not None else None)


def main(argv: Iterable[str] | None = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )

    path: Path = args.path
    if not path.exists():
        logger.error("input file not found: %s", path)
        return 2
    if path.is_dir():
        logger.error("input must be a file, got directory: %s", path)
        return 2

    try:
        summary = asyncio.run(
            _run_import(
                path=path,
                source_type_override=args.source_type,
                default_status=args.default_status,
                dry_run=args.dry_run,
                novelty_guard=args.novelty_guard,
            )
        )
    except ValueError as exc:
        logger.error("validation failed: %s", exc)
        return 3

    if args.format == "json":
        sys.stdout.write(_render_json(summary) + "\n")
    else:
        sys.stdout.write(_render_text(summary) + "\n")

    if summary.rejected > 0 and summary.inserted_or_updated == 0:
        return 4
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
