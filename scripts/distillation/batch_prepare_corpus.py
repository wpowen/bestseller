"""Scan a corpus directory and run ``prepare_source`` on new book files in parallel.

Typical usage (from repository root):

    python3 scripts/distillation/batch_prepare_corpus.py /Volumes/书籍 \\
      --genre-hint 玄幻 \\
      --workers 4 \\
      --dedupe-policy skip

Same normalized title (e.g. ``Foo.epub`` + ``Foo.mobi``) is prepared **once**; the
best available format is chosen (**TXT** before EPUB, then MD/HTML, then MOBI/AZW3).
Other formats are recorded in state as ``skipped_sibling_format`` so they are not retried.

MOBI/AZW3: uses Calibre ``ebook-convert`` when present; otherwise the optional ``mobi``
PyPI package (``uv sync --extra distillation``) unpacks via KindleUnpack.

Re-runs skip paths already recorded in the state file (``ok``, ``skipped_duplicate``,
``skipped_sibling_format``). Assigns ``source-NNNN`` from existing packages + registry.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Any

_THIS = Path(__file__).resolve()
_SRC = _THIS.parents[2] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from bestseller.services.distillation_book_parser import SUPPORTED_FORMATS  # noqa: E402
from bestseller.services.distillation_corpus import dedupe_corpus_paths_by_title  # noqa: E402


def _fingerprint(path: Path) -> str:
    st = path.stat()
    key = f"{path.resolve()}|{st.st_size}|{st.st_mtime_ns}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def _is_ignored_path(path: Path) -> bool:
    return any(part.startswith(".") for part in path.parts)


def iter_corpus_book_files(corpus_root: Path) -> list[Path]:
    corpus_root = corpus_root.resolve()
    found: list[Path] = []
    for path in corpus_root.rglob("*"):
        if not path.is_file() or _is_ignored_path(path):
            continue
        ext = path.suffix.lower().removeprefix(".")
        if ext == "markdown":
            ext = "md"
        if ext in SUPPORTED_FORMATS:
            found.append(path)
    return sorted(found, key=lambda p: str(p).lower())


def _max_source_index_from_registry(repo_root: Path) -> int:
    reg_path = repo_root / "data" / "distillation" / "source_registry.index.json"
    if not reg_path.is_file():
        return 0
    try:
        data = json.loads(reg_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 0
    highest = 0
    for entry in data.get("entries") or []:
        if not isinstance(entry, dict):
            continue
        for sid in entry.get("source_ids") or []:
            if isinstance(sid, str):
                match = re.fullmatch(r"source-(\d+)", sid)
                if match:
                    highest = max(highest, int(match.group(1)))
    return highest


def next_source_serial(repo_root: Path) -> int:
    root = repo_root / "data" / "distillation"
    highest = _max_source_index_from_registry(repo_root)
    if root.is_dir():
        for child in root.iterdir():
            match = re.fullmatch(r"source-(\d+)", child.name)
            if match and child.is_dir():
                highest = max(highest, int(match.group(1)))
    return highest + 1


def load_completed_fingerprints(state_path: Path) -> set[str]:
    if not state_path.exists():
        return set()
    done: set[str] = set()
    for line in state_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("status") in ("ok", "skipped_duplicate", "skipped_sibling_format"):
            fp = row.get("fingerprint")
            if isinstance(fp, str):
                done.add(fp)
    return done


def _append_state(state_path: Path, row: dict[str, Any]) -> None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    with state_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def _init_worker(src_dir: str) -> None:
    if src_dir not in sys.path:
        sys.path.insert(0, src_dir)


def _worker_prepare(job: dict[str, Any]) -> dict[str, Any]:
    from bestseller.services.distillation_source_preparer import prepare_source

    book = Path(job["book_path"]).expanduser()
    result = prepare_source(
        book,
        job["source_id"],
        Path(job["repo_root"]),
        Path(job["private_root"]),
        dedupe_policy=job["dedupe_policy"],
        rights_status=job["rights_status"],
        genre_hint=job.get("genre_hint"),
    )
    return {
        "fingerprint": job["fingerprint"],
        "book_path": job["book_path"],
        "source_id": job["source_id"],
        "result": result.to_dict(),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "corpus_dir",
        type=Path,
        help="Root directory to scan recursively (e.g. /Volumes/书籍).",
    )
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--private-root", type=Path, default=Path(".distillation_private"))
    parser.add_argument("--genre-hint", default=None)
    parser.add_argument(
        "--dedupe-policy",
        choices=("skip", "error", "allow"),
        default="skip",
    )
    parser.add_argument(
        "--rights-status",
        default="user_supplied_for_analysis",
        choices=(
            "unknown",
            "user_supplied_for_analysis",
            "licensed",
            "public_domain",
            "do_not_process",
        ),
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=4,
        help="Process pool size; each task still uses short file locks on the shared registry.",
    )
    parser.add_argument(
        "--state-file",
        type=Path,
        default=Path(".distillation_private/corpus_prepare_state.jsonl"),
        help="Append-only log of processed fingerprints for incremental runs.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List work items without calling prepare_source.",
    )
    parser.add_argument(
        "--no-corpus-title-dedupe",
        action="store_true",
        help="Process every supported file; do not collapse same-title siblings.",
    )
    args = parser.parse_args()

    corpus_dir = args.corpus_dir.expanduser()
    if not corpus_dir.is_dir():
        print(f"error: corpus directory not found: {corpus_dir}", file=sys.stderr)
        raise SystemExit(2)

    repo_root = args.repo_root.resolve()
    private_root = (
        (repo_root / args.private_root).resolve()
        if not args.private_root.is_absolute()
        else args.private_root.resolve()
    )

    state_path = (
        (repo_root / args.state_file).resolve()
        if not args.state_file.is_absolute()
        else args.state_file.resolve()
    )
    done_fps = load_completed_fingerprints(state_path)

    all_files = iter_corpus_book_files(corpus_dir)
    if args.no_corpus_title_dedupe:
        canonical_files = all_files
        sibling_records: list[dict[str, str]] = []
    else:
        canonical_files, sibling_records = dedupe_corpus_paths_by_title(all_files)

    for rec in sibling_records:
        skipped_path = Path(rec["skipped_path"])
        fp = _fingerprint(skipped_path)
        if fp in done_fps:
            continue
        _append_state(
            state_path,
            {
                "fingerprint": fp,
                "book_path": rec["skipped_path"],
                "status": "skipped_sibling_format",
                "title_key": rec["title_key"],
                "canonical_book_path": rec["chosen_path"],
            },
        )
        done_fps.add(fp)

    pending: list[tuple[Path, str, str]] = []
    serial = next_source_serial(repo_root)
    for book in canonical_files:
        fp = _fingerprint(book)
        if fp in done_fps:
            continue
        source_id = f"source-{serial:04d}"
        serial += 1
        pending.append((book, fp, source_id))

    print(
        json.dumps(
            {
                "corpus_dir": str(corpus_dir),
                "total_supported_files": len(all_files),
                "unique_books_after_title_dedupe": len(canonical_files),
                "sibling_paths_skipped": len(sibling_records),
                "pending_jobs": len(pending),
                "next_source_serial_start": next_source_serial(repo_root)
                if not pending
                else pending[0][2],
                "workers": args.workers,
            },
            ensure_ascii=False,
            indent=2,
        )
    )

    if args.dry_run:
        for book, fp, sid in pending:
            print(f"{sid}\t{fp[:12]}…\t{book}")
        return

    if not pending:
        return

    jobs = [
        {
            "book_path": str(book),
            "fingerprint": fp,
            "source_id": sid,
            "repo_root": str(repo_root),
            "private_root": str(private_root),
            "dedupe_policy": args.dedupe_policy,
            "rights_status": args.rights_status,
            "genre_hint": args.genre_hint,
        }
        for book, fp, sid in pending
    ]

    workers = max(1, int(args.workers))
    failures = 0
    with ProcessPoolExecutor(
        max_workers=workers,
        initializer=_init_worker,
        initargs=(str(_SRC),),
    ) as pool:
        futures = {pool.submit(_worker_prepare, job): job for job in jobs}
        for fut in as_completed(futures):
            job = futures[fut]
            try:
                payload = fut.result()
            except Exception as exc:  # noqa: BLE001 — surface worker crash
                failures += 1
                _append_state(
                    state_path,
                    {
                        "fingerprint": job["fingerprint"],
                        "book_path": job["book_path"],
                        "source_id": job["source_id"],
                        "status": "error",
                        "error": repr(exc),
                    },
                )
                print(f"error\t{job['book_path']}\t{exc}", file=sys.stderr)
                continue

            res = payload["result"]
            if res.get("skipped"):
                _append_state(
                    state_path,
                    {
                        "fingerprint": payload["fingerprint"],
                        "book_path": payload["book_path"],
                        "source_id": payload["source_id"],
                        "status": "skipped_duplicate",
                        "duplicate_of": res.get("duplicate_of"),
                    },
                )
            else:
                _append_state(
                    state_path,
                    {
                        "fingerprint": payload["fingerprint"],
                        "book_path": payload["book_path"],
                        "source_id": payload["source_id"],
                        "status": "ok",
                        "chapter_count": res.get("chapter_count"),
                    },
                )
            print(
                json.dumps(
                    {"book_path": payload["book_path"], "result": res},
                    ensure_ascii=False,
                )
            )

    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
