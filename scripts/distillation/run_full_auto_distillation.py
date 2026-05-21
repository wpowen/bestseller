"""Unattended distillation driver: optional corpus prepare → chapter LLM → per-book aggregate →
cross-book aggregate → privacy gate → import (dry-run by default).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import subprocess
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_THIS = Path(__file__).resolve()
_SRC = _THIS.parents[2] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from bestseller.infra.db.session import session_scope  # noqa: E402
from bestseller.services.distillation_assets import (  # noqa: E402
    aggregate_distillation_packages,
    install_story_design_grammar_patch,
    read_json,
    validate_distillation_package,
)
from bestseller.services.distillation_book_aggregator import (  # noqa: E402
    aggregate_source_package_async,
    expected_abs_chapters_from_index,
    infer_aggregate_key,
    package_book_phase_complete,
    write_aggregate_active_materials,
)
from bestseller.services.distillation_chapter_llm import (  # noqa: E402
    chapter_card_keys_missing_craft,
    existing_chapter_card_keys,
    load_chapter_card_schema,
    run_pending_chapter_jobs_parallel,
)
from bestseller.services.distillation_genre_classifier import (  # noqa: E402
    classify_genre_bucket_for_package,
)
from bestseller.services.distillation_privacy_gate import (  # noqa: E402
    privacy_violation_count_for_material_row,
)

from bestseller.settings import get_settings  # noqa: E402


_ACTIVE_PROMOTION_MODES = {"dry-run", "live"}
_SOURCE_DIR_RE = re.compile(r"^source-(\d+)$")


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def _discover_sources(dist_root: Path) -> list[Path]:
    paths = [p for p in dist_root.glob("source-*") if p.is_dir()]
    return sorted(paths, key=lambda p: p.name)


def _source_serial(path: Path) -> int | None:
    match = _SOURCE_DIR_RE.fullmatch(path.name)
    if not match:
        return None
    return int(match.group(1))


def _parse_source_bound(value: str | int | None, *, flag_name: str) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, int):
        if value < 1:
            raise ValueError(f"{flag_name} must be >= 1")
        return value
    text = str(value).strip()
    if text.isdigit():
        parsed = int(text)
    else:
        match = _SOURCE_DIR_RE.fullmatch(text)
        if not match:
            raise ValueError(f"{flag_name} must be an integer or source-NNNN")
        parsed = int(match.group(1))
    if parsed < 1:
        raise ValueError(f"{flag_name} must be >= 1")
    return parsed


def _filter_sources(
    sources: list[Path],
    *,
    source_start: str | int | None = None,
    source_end: str | int | None = None,
) -> list[Path]:
    start = _parse_source_bound(source_start, flag_name="--source-start")
    end = _parse_source_bound(source_end, flag_name="--source-end")
    if start is not None and end is not None and start > end:
        raise ValueError("--source-start must be <= --source-end")
    if start is None and end is None:
        return list(sources)

    selected: list[Path] = []
    for path in sources:
        serial = _source_serial(path)
        if serial is None:
            continue
        if start is not None and serial < start:
            continue
        if end is not None and serial > end:
            continue
        selected.append(path)
    return selected


def _count_incomplete_packages(source_paths: list[Path]) -> tuple[int, list[str]]:
    """Packages with manifest that do not yet pass :func:`validate_distillation_package`."""

    bad: list[str] = []
    for pkg in source_paths:
        if not (pkg / "source_manifest.json").is_file():
            continue
        rep = validate_distillation_package(pkg)
        if not rep.ok:
            bad.append(pkg.name)
    return len(bad), bad


def _load_state(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"version": 1, "book_complete_sources": [], "notes": []}
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {"version": 1, "book_complete_sources": []}


@dataclass
class RunReport:
    started_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    sources_scanned: int = 0
    sources_attempted: list[str] = field(default_factory=list)
    sources_succeeded: list[str] = field(default_factory=list)
    sources_skipped: list[str] = field(default_factory=list)
    sources_failed: dict[str, str] = field(default_factory=dict)
    chapter_cards_generated: int = 0
    material_entries_review_generated: int = 0
    material_entries_active_imported: int = 0
    aggregate_keys: list[str] = field(default_factory=list)
    grammar_install_targets: list[str] = field(default_factory=list)
    redaction_violations: list[str] = field(default_factory=list)
    chapter_anomaly_sources: list[str] = field(default_factory=list)
    chapter_job_failures: int = 0


async def _book_phase(
    *,
    package_dir: Path,
    repo_root: Path,
    private_errors: Path,
    settings: Any,
    write_active_artifacts: bool,
) -> None:
    async with session_scope(settings) as session:
        await aggregate_source_package_async(
            session,
            settings,
            package_dir=package_dir,
            repo_root=repo_root,
            private_errors_dir=private_errors,
            chapter_batch_size=None,
            write_active_artifacts=write_active_artifacts,
        )


def _import_active_jsonl(repo_root: Path, active_path: Path, *, dry_run: bool) -> tuple[int, int]:
    """Return (exit_code, inserted_or_updated from JSON if parseable)."""

    cmd = [
        sys.executable,
        str(repo_root / "scripts/import_material_jsonl.py"),
        str(active_path),
        "--source-type",
        "user_curated",
        "--default-status",
        "active",
        "--format",
        "json",
    ]
    if dry_run:
        cmd.append("--dry-run")
    proc = subprocess.run(
        cmd,
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        check=False,
    )
    inserted = 0
    if proc.stdout:
        try:
            summary = json.loads(proc.stdout)
            inserted = int(summary.get("inserted_or_updated") or 0)
        except json.JSONDecodeError:
            pass
    return proc.returncode, inserted


async def _run_one_pass(
    args: argparse.Namespace,
    *,
    sources: list[Path],
    repo_root: Path,
    private_root: Path,
    dist_root: Path,
    reports_dir: Path,
    errors_dir: Path,
    state_path: Path,
    report_path: Path,
    iteration: int,
) -> tuple[RunReport, int]:
    print(
        json.dumps(
            {
                "event": "pass_start",
                "iteration": iteration,
                "repo_root": str(repo_root),
                "dist_root": str(dist_root),
                "source_packages": len(sources),
            },
            ensure_ascii=False,
        ),
        flush=True,
    )

    report = RunReport()
    report.sources_scanned = len(sources)
    if args.resume:
        state = _load_state(state_path)
    else:
        state = {"version": 1, "book_complete_sources": []}
    book_done: set[str] = set(
        str(x) for x in state.get("book_complete_sources") or [] if isinstance(x, str)
    )

    print(
        json.dumps(
            {
                "event": "sources_selected",
                "iteration": iteration,
                "count": len(sources),
                "first": sources[0].name if sources else None,
            },
            ensure_ascii=False,
        ),
        flush=True,
    )

    settings = get_settings()
    schema = load_chapter_card_schema(repo_root)
    chapter_limit = args.chapter_job_limit if args.chapter_job_limit else None
    if chapter_limit is not None and chapter_limit < 1:
        chapter_limit = None

    for pkg in sources:
        sid = pkg.name
        manifest_path = pkg / "source_manifest.json"
        if not manifest_path.is_file():
            report.sources_skipped.append(sid)
            continue

        report.sources_attempted.append(sid)

        # --- Phase 2: chapter cards ---
        max_cc = int(args.max_chapter_chars) if getattr(args, "max_chapter_chars", 0) else 0
        max_chapter_chars_arg = max_cc if max_cc > 0 else None

        try:
            refresh_craft = bool(getattr(args, "refresh_missing_craft_observations", False))
            processed, failures = await run_pending_chapter_jobs_parallel(
                package_dir=pkg,
                repo_root=repo_root,
                private_root=private_root,
                settings=settings,
                schema=schema,
                max_concurrency=max(1, int(args.chapter_workers)),
                limit=chapter_limit,
                max_chapter_chars=max_chapter_chars_arg,
                private_errors_dir=errors_dir,
                job_timeout_seconds=float(getattr(args, "chapter_job_timeout_seconds", 120.0)),
                refresh_missing_craft_observations=refresh_craft,
            )
            report.chapter_cards_generated += processed
            report.chapter_job_failures += failures
            if refresh_craft:
                for _retry_round in range(max(0, int(getattr(args, "chapter_retry_rounds", 0)))):
                    missing_craft_now = chapter_card_keys_missing_craft(pkg / "chapter_cards.jsonl")
                    if not missing_craft_now:
                        break
                    retry_processed, retry_failures = await run_pending_chapter_jobs_parallel(
                        package_dir=pkg,
                        repo_root=repo_root,
                        private_root=private_root,
                        settings=settings,
                        schema=schema,
                        max_concurrency=max(1, int(args.chapter_workers)),
                        limit=chapter_limit,
                        max_chapter_chars=max_chapter_chars_arg,
                        private_errors_dir=errors_dir,
                        job_timeout_seconds=float(getattr(args, "chapter_job_timeout_seconds", 120.0)),
                        refresh_missing_craft_observations=True,
                    )
                    report.chapter_cards_generated += retry_processed
                    report.chapter_job_failures += retry_failures
                    if retry_processed == 0 and retry_failures == 0:
                        break
        except Exception as exc:  # noqa: BLE001
            report.sources_failed[sid] = f"chapter_phase:{type(exc).__name__}:{exc}"
            _atomic_write_json(
                errors_dir / f"{sid}_full_auto_chapter.json",
                {"source": sid, "error": str(exc)},
            )
            continue

        try:
            ch_index = read_json(pkg / "chapters.index.json")
            expected = expected_abs_chapters_from_index(ch_index)
            present = existing_chapter_card_keys(pkg / "chapter_cards.jsonl")
            missing_nos = sorted(expected - {k[1] for k in present}) if expected else []
            if missing_nos:
                report.sources_failed[sid] = (
                    f"incomplete_chapter_cards missing_count={len(missing_nos)} sample={missing_nos[:12]!r}"
                )
                report.chapter_anomaly_sources.append(sid)
                continue
            if refresh_craft:
                missing_craft = chapter_card_keys_missing_craft(pkg / "chapter_cards.jsonl")
                if missing_craft:
                    sample = sorted(no for _sid, no in missing_craft)[:12]
                    report.sources_failed[sid] = (
                        "incomplete_craft_observations "
                        f"missing_count={len(missing_craft)} sample={sample!r}"
                    )
                    continue
        except Exception as exc:  # noqa: BLE001
            report.sources_failed[sid] = f"chapter_card_index_check:{type(exc).__name__}:{exc}"
            continue

        # --- Phase 3: book aggregation ---
        if package_book_phase_complete(pkg) and not (
            refresh_craft and processed > 0
        ):
            book_done.add(sid)
            report.sources_succeeded.append(sid)
            continue

        try:
            await _book_phase(
                package_dir=pkg,
                repo_root=repo_root,
                private_errors=errors_dir,
                settings=settings,
                write_active_artifacts=args.import_mode in _ACTIVE_PROMOTION_MODES,
            )
            book_done.add(sid)
            report.sources_succeeded.append(sid)
        except Exception as exc:  # noqa: BLE001
            report.sources_failed[sid] = f"book_phase:{type(exc).__name__}:{exc}"
            _atomic_write_json(
                errors_dir / f"{sid}_full_auto_book.json",
                {"source": sid, "error": str(exc)},
            )
            continue

    state["book_complete_sources"] = sorted(book_done)
    state["updated_at"] = datetime.now(timezone.utc).isoformat()
    state["last_pass_iteration"] = iteration
    _atomic_write_json(state_path, state)

    # --- Cross-book aggregation: every package that already validates.
    #
    # `sources` may be a narrow incremental range. Aggregates are shared assets,
    # so rebuilding them from only that range would erase the already-distilled
    # corpus from the aggregate files.
    ready_packages: list[Path] = []
    for pkg in _discover_sources(dist_root):
        rep = validate_distillation_package(pkg)
        if rep.ok:
            ready_packages.append(pkg)

    if ready_packages and not getattr(args, "skip_genre_classify", False):
        async with session_scope(settings) as session:
            for pkg in ready_packages:
                try:
                    await classify_genre_bucket_for_package(
                        session,
                        settings,
                        package_dir=pkg,
                        repo_root=repo_root,
                        private_root=private_root,
                        force=bool(getattr(args, "force_genre_reclassify", False)),
                    )
                except Exception as exc:  # noqa: BLE001
                    print(
                        json.dumps(
                            {
                                "event": "genre_classify_failed",
                                "package": pkg.name,
                                "error": f"{type(exc).__name__}: {exc}",
                            },
                            ensure_ascii=False,
                        ),
                        flush=True,
                    )

    groups: dict[str, list[Path]] = defaultdict(list)
    for pkg in ready_packages:
        manifest = read_json(pkg / "source_manifest.json")
        if str(args.aggregate_key) != "auto":
            key = str(args.aggregate_key)
        else:
            key = infer_aggregate_key(manifest)
        groups[key].append(pkg)

    aggregates_root = dist_root / "aggregates"
    import_jobs: list[tuple[str, Path]] = []
    for agg_key, dirs in sorted(groups.items(), key=lambda kv: kv[0]):
        if len(dirs) == 0:
            continue
        out_dir = aggregates_root / agg_key
        agg_report = aggregate_distillation_packages(
            dirs,
            output_dir=out_dir,
            aggregate_key=agg_key,
        )
        report.aggregate_keys.append(agg_key)
        report.material_entries_review_generated += agg_report.material_rows

        active_path = out_dir / "material_entries.active.jsonl"
        if getattr(args, "import_mode", "none") in _ACTIVE_PROMOTION_MODES:
            ac_path = out_dir / "anti_copy_rules.json"
            write_aggregate_active_materials(
                out_dir,
                anti_copy_rules_path=ac_path,
                private_reports_dir=reports_dir,
            )

            import_jobs.append((agg_key, active_path))

            ledger: dict[str, Any] = {}
            if ac_path.is_file():
                try:
                    loaded = read_json(ac_path)
                    if isinstance(loaded, dict):
                        ledger = loaded
                except Exception:
                    ledger = {}

            if active_path.is_file():
                for line in active_path.read_text(encoding="utf-8").splitlines():
                    if not line.strip():
                        continue
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(row, dict):
                        continue
                    _n, msgs = privacy_violation_count_for_material_row(row, anti_copy_ledger=ledger)
                    for msg in msgs:
                        report.redaction_violations.append(f"{agg_key}:{msg}")
        else:
            print(
                json.dumps(
                    {
                        "event": "active_material_generation_skipped",
                        "aggregate_key": agg_key,
                        "reason": "import_mode_none",
                        "existing_active_artifact": active_path.is_file(),
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )

    privacy_blocked = bool(report.redaction_violations)

    if not privacy_blocked and getattr(args, "import_mode", "none") in ("dry-run", "live"):
        dry = args.import_mode == "dry-run"
        for agg_key, active_path in import_jobs:
            if not active_path.is_file():
                continue
            code, ins = _import_active_jsonl(repo_root, active_path, dry_run=dry)
            if code != 0:
                report.sources_failed[f"import:{agg_key}"] = f"exit={code}"
            else:
                report.material_entries_active_imported += ins

    if not privacy_blocked and args.auto_install_grammar:
        for agg_key, dirs in sorted(groups.items(), key=lambda kv: kv[0]):
            if not dirs:
                continue
            out_dir = aggregates_root / agg_key
            gpath = out_dir / "grammar_patch.yaml"
            if gpath.is_file():
                target = install_story_design_grammar_patch(
                    gpath,
                    grammar_dir=repo_root / "config" / "story_design_grammars",
                    dry_run=False,
                )
                report.grammar_install_targets.append(str(target))

    _atomic_write_json(report_path, report.__dict__)
    print(json.dumps({"event": "pass_report", "iteration": iteration, **report.__dict__}, ensure_ascii=False), flush=True)

    if privacy_blocked:
        print(
            json.dumps(
                {
                    "event": "privacy_hard_fail",
                    "violations_sample": report.redaction_violations[:24],
                    "import_skipped": True,
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        return report, 3

    return report, 1 if report.sources_failed else 0


async def _async_main(args: argparse.Namespace) -> int:
    repo_root: Path = args.repo_root.resolve()
    private_root = (
        (repo_root / args.private_root).resolve()
        if not args.private_root.is_absolute()
        else args.private_root.resolve()
    )
    dist_root = repo_root / "data" / "distillation"
    reports_dir = private_root / "reports"
    errors_dir = private_root / "errors"
    state_path = reports_dir / "full_auto_distillation_state.json"
    report_path = reports_dir / "full_auto_distillation_report.json"

    if args.import_mode in _ACTIVE_PROMOTION_MODES and not args.allow_reviewed_promotion:
        print(
            json.dumps(
                {
                    "event": "promotion_blocked",
                    "message": (
                        "--import-mode dry-run/live requires --allow-reviewed-promotion "
                        "because it writes material_entries.active.jsonl promotion artifacts."
                    ),
                },
                ensure_ascii=False,
            ),
            file=sys.stderr,
            flush=True,
        )
        return 2
    if args.import_mode in _ACTIVE_PROMOTION_MODES and not getattr(args, "single_pass", False):
        print(
            json.dumps(
                {
                    "event": "promotion_blocked",
                    "message": (
                        "--allow-reviewed-promotion may only be used with --single-pass; "
                        "long-running unattended daemons must keep --import-mode none."
                    ),
                },
                ensure_ascii=False,
            ),
            file=sys.stderr,
            flush=True,
        )
        return 2
    if args.auto_install_grammar and not args.allow_reviewed_promotion:
        print(
            json.dumps(
                {
                    "event": "promotion_blocked",
                    "message": "--auto-install-grammar requires --allow-reviewed-promotion.",
                },
                ensure_ascii=False,
            ),
            file=sys.stderr,
            flush=True,
        )
        return 2

    if args.corpus_dir:
        corpus_path = Path(args.corpus_dir).expanduser()
        corpus_path = corpus_path if corpus_path.is_absolute() else (Path.cwd() / corpus_path).resolve()
        if not corpus_path.is_dir():
            print(
                json.dumps(
                    {"event": "corpus_dir_missing", "path": str(corpus_path)},
                    ensure_ascii=False,
                ),
                file=sys.stderr,
                flush=True,
            )
            return 2
        cmd = [
            sys.executable,
            str(repo_root / "scripts/distillation/batch_prepare_corpus.py"),
            str(corpus_path),
            "--repo-root",
            str(repo_root),
            "--private-root",
            str(args.private_root),
            "--dedupe-policy",
            args.corpus_dedupe_policy,
            "--workers",
            str(max(1, int(args.corpus_workers))),
        ]
        if args.corpus_genre_hint:
            cmd.extend(["--genre-hint", args.corpus_genre_hint])
        proc = subprocess.run(cmd, cwd=str(repo_root), check=False)
        if proc.returncode != 0:
            print(
                json.dumps(
                    {
                        "event": "corpus_prepare_failed",
                        "exit_code": proc.returncode,
                        "stderr_tail": (proc.stderr or "")[-4000:],
                    },
                    ensure_ascii=False,
                ),
                file=sys.stderr,
                flush=True,
            )
            return 1

    print(
        json.dumps(
            {
                "event": "full_auto_start",
                "until_complete": not args.single_pass,
                "repo_root": str(repo_root),
                "dist_root": str(dist_root),
                "import_mode": args.import_mode,
            },
            ensure_ascii=False,
        ),
        flush=True,
    )

    sources = _discover_sources(dist_root)
    try:
        sources = _filter_sources(
            sources,
            source_start=getattr(args, "source_start", None),
            source_end=getattr(args, "source_end", None),
        )
    except ValueError as exc:
        print(
            json.dumps({"event": "source_range_invalid", "error": str(exc)}, ensure_ascii=False),
            file=sys.stderr,
            flush=True,
        )
        return 2
    if args.source_limit and args.source_limit > 0:
        sources = sources[: int(args.source_limit)]

    iteration = 0
    prev_incomplete: int | None = None
    stall_rounds = 0

    while True:
        iteration += 1
        report, code = await _run_one_pass(
            args,
            sources=sources,
            repo_root=repo_root,
            private_root=private_root,
            dist_root=dist_root,
            reports_dir=reports_dir,
            errors_dir=errors_dir,
            state_path=state_path,
            report_path=report_path,
            iteration=iteration,
        )
        if code == 3:
            return 3
        n_incomplete, incomplete_sample = _count_incomplete_packages(sources)
        print(
            json.dumps(
                {
                    "event": "pass_status",
                    "iteration": iteration,
                    "incomplete_packages": n_incomplete,
                    "incomplete_sample": incomplete_sample[:12],
                    "chapter_cards_this_pass": report.chapter_cards_generated,
                    "sources_succeeded_this_pass": len(report.sources_succeeded),
                },
                ensure_ascii=False,
            ),
            flush=True,
        )

        if args.single_pass:
            return code

        if n_incomplete == 0 and code == 0:
            print(
                json.dumps({"event": "all_distillation_complete", "iterations": iteration}, ensure_ascii=False),
                flush=True,
            )
            return 0

        if n_incomplete == 0:
            print(
                json.dumps(
                    {
                        "event": "distillation_validation_complete_but_failures_pending",
                        "iterations": iteration,
                        "failures": len(report.sources_failed),
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )

        # No forward progress: same incomplete count and no new cards / successes → likely hard failures.
        if (
            prev_incomplete is not None
            and n_incomplete == prev_incomplete
            and report.chapter_cards_generated == 0
            and len(report.sources_succeeded) == 0
        ):
            stall_rounds += 1
        else:
            stall_rounds = 0
        prev_incomplete = n_incomplete

        if stall_rounds >= int(args.stall_exit_rounds):
            print(
                json.dumps(
                    {
                        "event": "stall_exit",
                        "message": "No progress for consecutive passes; fix errors in .distillation_private/errors/ then re-run with --resume.",
                        "incomplete_packages": n_incomplete,
                        "stall_rounds": stall_rounds,
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
            return 2

        await asyncio.sleep(max(0.0, float(args.loop_sleep_seconds)))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--private-root", type=Path, default=Path(".distillation_private"))
    parser.add_argument(
        "--aggregate-key",
        default="auto",
        help=(
            "Use 'auto' to bucket by source_manifest distillation_genre_bucket (if set) "
            "then genre_hint heuristics, or a fixed key for all ready packages."
        ),
    )
    parser.add_argument("--chapter-workers", type=int, default=4)
    parser.add_argument(
        "--chapter-job-timeout-seconds",
        type=float,
        default=120.0,
        help=(
            "Per-chapter LLM job timeout in seconds before it is marked failed "
            "(default: 120.0)."
        ),
    )
    parser.add_argument("--source-limit", type=int, default=0, help="0 = no limit.")
    parser.add_argument(
        "--source-start",
        default=None,
        help="Optional inclusive lower bound, e.g. 241 or source-0241.",
    )
    parser.add_argument(
        "--source-end",
        default=None,
        help="Optional inclusive upper bound, e.g. 480 or source-0480.",
    )
    parser.add_argument(
        "--chapter-job-limit",
        type=int,
        default=None,
        help="Max pending chapter LLM jobs per source (default: all pending).",
    )
    parser.add_argument(
        "--chapter-retry-rounds",
        type=int,
        default=2,
        help=(
            "Extra per-source retry rounds for failed chapter/craft jobs before "
            "moving to the next source (default: 2)."
        ),
    )
    parser.add_argument(
        "--import-mode",
        choices=("none", "dry-run", "live"),
        default="none",
        help="Promote review material to material_entries.active.jsonl and optionally import it (default: none).",
    )
    parser.add_argument(
        "--auto-import",
        action="store_true",
        help="Deprecated: equivalent to --import-mode live.",
    )
    parser.add_argument("--auto-install-grammar", action="store_true")
    parser.add_argument(
        "--corpus-dir",
        type=Path,
        default=None,
        help="Optional: scan raw book library, dedupe, and run batch prepare before distillation phases.",
    )
    parser.add_argument(
        "--corpus-genre-hint",
        default=None,
        help="Optional genre hint forwarded to batch_prepare_corpus.",
    )
    parser.add_argument(
        "--corpus-dedupe-policy",
        choices=("skip", "error", "allow"),
        default="skip",
        help="Dedupe policy for batch_prepare_corpus (default: skip).",
    )
    parser.add_argument(
        "--corpus-workers",
        type=int,
        default=4,
        help="Worker processes for batch_prepare_corpus.",
    )
    parser.add_argument(
        "--skip-genre-classify",
        action="store_true",
        help="Skip LLM distillation_genre_bucket step before cross-book aggregate.",
    )
    parser.add_argument(
        "--force-genre-reclassify",
        action="store_true",
        help="Re-run genre bucket LLM even when manifest already has an allowed bucket.",
    )
    parser.add_argument(
        "--allow-reviewed-promotion",
        action="store_true",
        help=(
            "Permit --import-mode live and --auto-install-grammar after operational review. "
            "Also required for dry-run promotion because it writes active artifacts."
        ),
    )
    parser.add_argument(
        "--max-chapter-chars",
        type=int,
        default=0,
        help="Optional cap on raw chapter_text length before sub-chunking (0 = no cap).",
    )
    parser.add_argument(
        "--refresh-missing-craft-observations",
        action="store_true",
        help=(
            "Re-run existing chapter_cards rows that lack craft_observations before book aggregation. "
            "Use when backfilling the anonymous author-craft layer."
        ),
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Load prior state book_complete_sources; chapter resume uses chapter_cards.jsonl keys.",
    )
    parser.add_argument(
        "--single-pass",
        action="store_true",
        help="Process the corpus once then exit (default: loop until all packages validate).",
    )
    parser.add_argument(
        "--loop-sleep-seconds",
        type=float,
        default=5.0,
        help="Pause between passes when --until-complete (default: 5).",
    )
    parser.add_argument(
        "--stall-exit-rounds",
        type=int,
        default=8,
        help="Exit with code 2 after this many consecutive no-progress passes (default: 8).",
    )
    args = parser.parse_args()
    if args.auto_import:
        args.import_mode = "live"
    code = asyncio.run(_async_main(args))
    raise SystemExit(code)


if __name__ == "__main__":
    main()
