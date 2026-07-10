#!/usr/bin/env python3
"""Initialize and evaluate deterministic novel-quality evidence packages.

Task 1.1 intentionally does not call an LLM.  ``baseline`` creates the exact
artifact layout and returns exit code 2 until real draft, judge and human
evidence has been supplied.  This prevents an empty or degraded run from being
mistaken for a passing quality result.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
import json
from pathlib import Path
import shutil
import subprocess

from bestseller.services.quality_evaluation import (
    build_evaluation_manifest,
    evaluate_release_gate,
    load_quality_evaluation_config,
    write_evaluation_run_skeleton,
)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.command == "baseline":
        return _baseline(args)
    if args.command == "check":
        return _check(args)
    raise RuntimeError(f"unsupported command: {args.command}")


def _baseline(args: argparse.Namespace) -> int:
    config = load_quality_evaluation_config(args.config)
    output = Path(args.output)
    manifest = build_evaluation_manifest(
        config,
        run_id=output.name,
        git_sha=args.git_sha or _git_sha(),
        docker_image_id=args.docker_image_id or "unverified",
        writer_catalog_key=args.writer_catalog_key or config.writer_catalog_key,
        writer_actual_model=args.writer_model or "unresolved",
        judge_catalog_key=args.judge_catalog_key or config.judge_catalog_key,
        judge_actual_model=args.judge_model or "unresolved",
    )
    manifest["execution"] = {
        "require_real_llm": bool(args.require_real_llm),
        "llm_execution_performed": False,
    }
    paths = write_evaluation_run_skeleton(output, manifest)
    gate_path = Path(paths["release_gate"])
    gate = json.loads(gate_path.read_text(encoding="utf-8"))
    if args.require_real_llm:
        gate["reasons"] = list(
            dict.fromkeys([*gate["reasons"], "real_llm_evidence_not_supplied"])
        )
        gate["status"] = "inconclusive"
        gate_path.write_text(
            json.dumps(gate, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
    print(json.dumps({"paths": paths, "release_gate": gate}, ensure_ascii=False))
    return 0 if gate["status"] == "pass" else 2


def _check(args: argparse.Namespace) -> int:
    root = Path(args.run)
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    gate = evaluate_release_gate(manifest, require_human=args.require_human)
    output = Path(args.output) if args.output else root / "release-gate.json"
    output.write_text(
        json.dumps(gate, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    print(json.dumps(gate, ensure_ascii=False))
    return 0 if gate["status"] == "pass" else 2


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    baseline = subparsers.add_parser("baseline")
    baseline.add_argument("--config", required=True)
    baseline.add_argument("--output", required=True)
    baseline.add_argument("--git-sha")
    baseline.add_argument("--docker-image-id")
    baseline.add_argument("--writer-catalog-key")
    baseline.add_argument("--writer-model")
    baseline.add_argument("--judge-catalog-key")
    baseline.add_argument("--judge-model")
    baseline.add_argument("--require-real-llm", action="store_true")

    check = subparsers.add_parser("check")
    check.add_argument("--run", required=True)
    check.add_argument("--output")
    check.add_argument("--require-human", action="store_true")
    return parser.parse_args(argv)


def _git_sha() -> str:
    git = shutil.which("git")
    if git is None:
        raise RuntimeError("git executable is required to record the run identity")
    result = subprocess.run(  # noqa: S603 - executable resolved via shutil.which
        [git, "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


if __name__ == "__main__":
    raise SystemExit(main())
