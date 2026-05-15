"""Cursor-only worker: source-0001..source-0240 via allowed CLIs only.

Subprocesses **only**:
  .venv/bin/python scripts/distillation/run_chapter_llm_jobs.py ...
  .venv/bin/python scripts/distillation/aggregate_source_package.py ...

Stops on: chapter CLI stderr hinting invalid JSON, privacy scan hits on
``chapter_cards.jsonl``, or unexpected changes to aggregate
``data/distillation/aggregates/*/material_entries.active.jsonl``.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_REPO = Path(__file__).resolve().parents[2]
_SRC = _REPO / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from bestseller.services.distillation_assets import _scan_sensitive_values  # noqa: E402
from bestseller.services.distillation_book_aggregator import expected_abs_chapters_from_index  # noqa: E402
from bestseller.services.distillation_chapter_llm import (  # noqa: E402
    existing_chapter_card_keys,
    read_jsonl,
)


def _blocked_phrase_hits_in_blob(blob: str, ledger: dict[str, Any]) -> list[str]:
    hits: list[str] = []
    blocked = ledger.get("blocked_combinations") or []
    if not isinstance(blocked, list):
        return hits
    for phrase in blocked:
        p = str(phrase).strip()
        if p and p in blob:
            hits.append(f"blocked_combination:{p!r}")
    return hits


def _privacy_scan_chapter_cards(package_dir: Path) -> list[str]:
    path = package_dir / "chapter_cards.jsonl"
    if not path.is_file():
        return []
    ledger: dict[str, Any] = {}
    ledger_path = package_dir / "anti_copy_ledger.json"
    if ledger_path.is_file():
        try:
            loaded = json.loads(ledger_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                ledger = loaded
        except json.JSONDecodeError:
            ledger = {}
    msgs: list[str] = []
    for row in read_jsonl(path):
        msgs.extend(_scan_sensitive_values(row, path="chapter_card"))
        try:
            blob = json.dumps(row, ensure_ascii=False)
        except (TypeError, ValueError):
            blob = str(row)
        msgs.extend(_blocked_phrase_hits_in_blob(blob, ledger))
    return msgs


CHAPTER_CLI = _REPO / "scripts" / "distillation" / "run_chapter_llm_jobs.py"
AGG_CLI = _REPO / "scripts" / "distillation" / "aggregate_source_package.py"
VENV_PY = _REPO / ".venv" / "bin" / "python"
PROGRESS_PATH = _REPO / ".distillation_private" / "reports" / "cursor_worker_progress.jsonl"
AGG_ROOT = _REPO / "data" / "distillation" / "aggregates"


@dataclass(frozen=True)
class ActiveSnapshot:
    path: str
    mtime_ns: int | None
    size: int | None


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _append_progress(payload: dict[str, Any]) -> None:
    PROGRESS_PATH.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(payload, ensure_ascii=False) + "\n"
    with PROGRESS_PATH.open("a", encoding="utf-8") as handle:
        handle.write(line)
    print(line, end="", flush=True)


def _snapshot_aggregate_actives() -> dict[str, ActiveSnapshot]:
    out: dict[str, ActiveSnapshot] = {}
    if not AGG_ROOT.is_dir():
        return out
    for path in AGG_ROOT.rglob("material_entries.active.jsonl"):
        if not path.is_file():
            continue
        st = path.stat()
        key = str(path.relative_to(_REPO))
        out[key] = ActiveSnapshot(path=key, mtime_ns=st.st_mtime_ns, size=st.st_size)
    return out


def _assert_aggregate_actives_unchanged(baseline: dict[str, ActiveSnapshot], *, where: str) -> None:
    cur = _snapshot_aggregate_actives()
    if set(cur) != set(baseline):
        raise RuntimeError(f"{where}: aggregate active file set changed")
    for key, snap in baseline.items():
        now = cur.get(key)
        if now is None:
            raise RuntimeError(f"{where}: missing aggregate active {key!r}")
        if now.mtime_ns != snap.mtime_ns or now.size != snap.size:
            raise RuntimeError(
                f"{where}: aggregate active updated unexpectedly: {key!r} "
                f"(mtime {snap.mtime_ns}->{now.mtime_ns}, size {snap.size}->{now.size})"
            )


def _chapters_complete(package_dir: Path) -> bool:
    idx_path = package_dir / "chapters.index.json"
    cards_path = package_dir / "chapter_cards.jsonl"
    if not idx_path.is_file():
        return True
    ch = json.loads(idx_path.read_text(encoding="utf-8"))
    expected = expected_abs_chapters_from_index(ch)
    if not expected:
        return True
    present = existing_chapter_card_keys(cards_path)
    missing = expected - {k[1] for k in present}
    return len(missing) == 0


def _run_chapter_batch(
    *,
    package_dir: Path,
    repo_root: Path,
    private_root: Path,
    limit: int,
    max_chapter_chars: int,
    refresh_missing_craft_observations: bool,
) -> tuple[int, str, str]:
    cmd = [
        str(VENV_PY),
        str(CHAPTER_CLI),
        "--repo-root",
        str(repo_root),
        "--private-root",
        str(private_root),
        "--package-dir",
        str(package_dir),
        "--limit",
        str(limit),
        "--max-chapter-chars",
        str(max_chapter_chars),
    ]
    if refresh_missing_craft_observations:
        cmd.append("--refresh-missing-craft-observations")
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            check=False,
            timeout=900,
        )
        return proc.returncode, proc.stdout or "", proc.stderr or ""
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout or ""
        stderr = (exc.stderr or "") + "\nchapter batch timeout"
        return 124, stdout, stderr


def _run_aggregate(*, package_dir: Path, repo_root: Path, private_root: Path) -> tuple[int, str, str]:
    cmd = [
        str(VENV_PY),
        str(AGG_CLI),
        "--repo-root",
        str(repo_root),
        "--private-root",
        str(private_root),
        "--package-dir",
        str(package_dir),
    ]
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            check=False,
            timeout=600,
        )
        return proc.returncode, proc.stdout or "", proc.stderr or ""
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout or ""
        stderr = (exc.stderr or "") + "\naggregate timeout"
        return 124, stdout, stderr


def _stderr_invalid_json(stderr: str) -> bool:
    if not stderr.strip():
        return False
    if re.search(r"JSONDecodeError|not an object|invalid JSON|ValueError: LLM output JSON", stderr, re.I):
        return True
    return False


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-start", type=int, default=1, help="Inclusive source serial.")
    parser.add_argument("--source-end", type=int, default=240, help="Inclusive source serial.")
    parser.add_argument("--chapter-batch-limit", type=int, default=10)
    parser.add_argument("--max-chapter-chars", type=int, default=12000)
    parser.add_argument("--refresh-missing-craft-observations", action="store_true")
    args = parser.parse_args()
    if args.source_start < 1 or args.source_end < args.source_start:
        print("invalid source range", file=sys.stderr)
        raise SystemExit(2)

    if not VENV_PY.is_file():
        print(f"missing venv python: {VENV_PY}", file=sys.stderr)
        raise SystemExit(2)

    _append_progress(
        {
            "owner": "cursor",
            "source_id": None,
            "phase": "chapter",
            "status": f"session_start_source_{args.source_start:04d}_{args.source_end:04d}",
            "updated_at": _utc_iso(),
        }
    )

    repo_root = _REPO.resolve()
    private_root = (repo_root / ".distillation_private").resolve()

    baseline_actives = _snapshot_aggregate_actives()

    for n in range(args.source_start, args.source_end + 1):
        sid = f"source-{n:04d}"
        pkg = repo_root / "data" / "distillation" / sid

        if not pkg.is_dir():
            _append_progress(
                {
                    "owner": "cursor",
                    "source_id": sid,
                    "phase": "error",
                    "status": "missing_package_dir",
                    "updated_at": _utc_iso(),
                }
            )
            continue

        try:
            _append_progress(
                {
                    "owner": "cursor",
                    "source_id": sid,
                    "phase": "chapter",
                    "status": "start",
                    "updated_at": _utc_iso(),
                }
            )

            source_failed = False
            while not _chapters_complete(pkg):
                code, out, err = _run_chapter_batch(
                    package_dir=pkg,
                    repo_root=repo_root,
                    private_root=private_root,
                    limit=max(1, int(args.chapter_batch_limit)),
                    max_chapter_chars=max(1, int(args.max_chapter_chars)),
                    refresh_missing_craft_observations=bool(
                        args.refresh_missing_craft_observations
                    ),
                )
                _assert_aggregate_actives_unchanged(baseline_actives, where=f"{sid} after_chapter_batch")

                if _stderr_invalid_json(err):
                    _append_progress(
                        {
                            "owner": "cursor",
                            "source_id": sid,
                            "phase": "error",
                            "status": f"invalid_json: {err[:2000]!r}",
                            "updated_at": _utc_iso(),
                        }
                    )
                    source_failed = True
                    break
                if code != 0:
                    _append_progress(
                        {
                            "owner": "cursor",
                            "source_id": sid,
                            "phase": "error",
                            "status": f"chapter_cli_exit:{code}",
                            "updated_at": _utc_iso(),
                        }
                    )
                    source_failed = True
                    break

                pv = _privacy_scan_chapter_cards(pkg)
                if pv:
                    _append_progress(
                        {
                            "owner": "cursor",
                            "source_id": sid,
                            "phase": "error",
                            "status": f"privacy_scan:{pv[:12]!r}",
                            "updated_at": _utc_iso(),
                        }
                    )
                    source_failed = True
                    break

                tail = out.strip().splitlines()[-1] if out.strip() else ""
                summary: dict[str, Any] = {}
                if tail:
                    try:
                        summary = json.loads(tail)
                    except json.JSONDecodeError:
                        _append_progress(
                            {
                                "owner": "cursor",
                                "source_id": sid,
                                "phase": "error",
                                "status": f"chapter_summary_not_json:{tail[:800]!r}",
                                "updated_at": _utc_iso(),
                            }
                        )
                        source_failed = True
                        break
                failures = int(summary.get("failures") or 0)
                if failures > 0:
                    _append_progress(
                        {
                            "owner": "cursor",
                            "source_id": sid,
                            "phase": "warn",
                            "status": f"chapter_failures:{failures}",
                            "updated_at": _utc_iso(),
                        }
                    )

                if int(summary.get("processed") or 0) == 0:
                    # no forward progress
                    break

            if source_failed:
                _append_progress(
                    {
                        "owner": "cursor",
                        "source_id": sid,
                        "phase": "error",
                        "status": "skip_after_chapter_failures",
                        "updated_at": _utc_iso(),
                    }
                )
                continue

            if not _chapters_complete(pkg):
                _append_progress(
                    {
                        "owner": "cursor",
                        "source_id": sid,
                        "phase": "error",
                        "status": "incomplete_chapter_cards",
                        "updated_at": _utc_iso(),
                    }
                )
                continue

            _append_progress(
                {
                    "owner": "cursor",
                    "source_id": sid,
                    "phase": "chapter",
                    "status": "done",
                    "updated_at": _utc_iso(),
                }
            )

            _append_progress(
                {
                    "owner": "cursor",
                    "source_id": sid,
                    "phase": "aggregate",
                    "status": "start",
                    "updated_at": _utc_iso(),
                }
            )

            code, out, err = _run_aggregate(
                package_dir=pkg,
                repo_root=repo_root,
                private_root=private_root,
            )
            _assert_aggregate_actives_unchanged(baseline_actives, where=f"{sid} after_aggregate")
            if code != 0:
                _append_progress(
                    {
                        "owner": "cursor",
                        "source_id": sid,
                        "phase": "error",
                        "status": f"aggregate_exit:{code}",
                        "updated_at": _utc_iso(),
                    }
                )
                continue

            if (out or err) and _stderr_invalid_json((out or "") + (err or "")):
                _append_progress(
                    {
                        "owner": "cursor",
                        "source_id": sid,
                        "phase": "warn",
                        "status": f"aggregate_json_signal:{(err or out)[:1000]!r}",
                        "updated_at": _utc_iso(),
                    }
                )
                continue

            _append_progress(
                {
                    "owner": "cursor",
                    "source_id": sid,
                    "phase": "aggregate",
                    "status": "done",
                    "updated_at": _utc_iso(),
                }
            )

            _append_progress(
                {
                    "owner": "cursor",
                    "source_id": sid,
                    "phase": "done",
                    "status": "ok",
                    "updated_at": _utc_iso(),
                }
            )

        except Exception as exc:  # noqa: BLE001
            _append_progress(
                {
                    "owner": "cursor",
                    "source_id": sid,
                    "phase": "error",
                    "status": f"{type(exc).__name__}: {exc}",
                    "updated_at": _utc_iso(),
                }
            )
            print(f"SKIP: {exc}", file=sys.stderr)


if __name__ == "__main__":
    main()
