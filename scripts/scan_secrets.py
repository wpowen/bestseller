#!/usr/bin/env python3
"""Detect likely secrets before they enter the repository.

This scanner is intentionally small and dependency-free so it can run in
pre-commit hooks and local checks before dev dependencies are installed.
"""

from __future__ import annotations

import argparse
import math
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Iterable


MAX_FILE_BYTES = 2_000_000

SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("OpenAI-style API key", re.compile(r"\bsk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{20,}\b")),
    ("Anthropic API key", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}\b")),
    ("NVIDIA API key", re.compile(r"\bnvapi-[A-Za-z0-9_-]{20,}\b")),
    ("JWT-like token", re.compile(r"\beyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{10,}\b")),
)

ASSIGNMENT_RE = re.compile(
    r"""(?x)
    ^\s*(?:export\s+)?
    (
        [A-Z0-9_]*(?:API_KEY|ACCESS_KEY|SECRET|TOKEN|PASSWORD|AUTH_HEADER)
    )
    \s*[:=]\s*
    ["']?([A-Za-z0-9_./+=%:-]{20,})["']?
    """
)

ALLOW_RE = re.compile(
    r"(?i)(your-|your_|example|placeholder|change-?me|dummy|test|fake|xxxx|"
    r"<[^>]+>|\$\{|redacted|none|null)"
)

SKIP_DIRS = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    ".venv-test",
    "__pycache__",
    "artifacts",
    "htmlcov",
    "node_modules",
    "output",
}


def git_root() -> Path:
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        check=True,
        stdout=subprocess.PIPE,
        text=True,
    )
    return Path(result.stdout.strip())


def shannon_entropy(value: str) -> float:
    if not value:
        return 0.0
    counts = {char: value.count(char) for char in set(value)}
    length = len(value)
    return -sum((count / length) * math.log2(count / length) for count in counts.values())


def mask(value: str) -> str:
    value = value.strip().strip("\"'")
    if len(value) <= 12:
        return "<redacted>"
    return f"{value[:4]}...{value[-4:]}"


def should_skip_path(path: Path) -> bool:
    return any(part in SKIP_DIRS for part in path.parts)


def tracked_and_untracked_files() -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "-co", "--exclude-standard"],
        check=True,
        stdout=subprocess.PIPE,
        text=True,
    )
    return [Path(line) for line in result.stdout.splitlines() if line]


def staged_files() -> list[Path]:
    result = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR"],
        check=True,
        stdout=subprocess.PIPE,
        text=True,
    )
    return [Path(line) for line in result.stdout.splitlines() if line]


def read_worktree_file(root: Path, path: Path) -> str | None:
    full_path = root / path
    try:
        if full_path.stat().st_size > MAX_FILE_BYTES:
            return None
        data = full_path.read_bytes()
    except OSError:
        return None
    if b"\0" in data[:4096]:
        return None
    return data.decode("utf-8", errors="replace")


def read_staged_file(path: Path) -> str | None:
    result = subprocess.run(
        ["git", "show", f":{path.as_posix()}"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    if result.returncode != 0 or len(result.stdout) > MAX_FILE_BYTES:
        return None
    if b"\0" in result.stdout[:4096]:
        return None
    return result.stdout.decode("utf-8", errors="replace")


def scan_text(path: Path, text: str) -> list[str]:
    findings: list[str] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        if ALLOW_RE.search(line):
            continue
        for label, pattern in SECRET_PATTERNS:
            for match in pattern.finditer(line):
                value = match.group(0)
                if not ALLOW_RE.search(value):
                    findings.append(f"{path}:{line_number}: {label} ({mask(value)})")
        for match in ASSIGNMENT_RE.finditer(line):
            name, value = match.groups()
            if ALLOW_RE.search(value):
                continue
            if shannon_entropy(value) < 3.4:
                continue
            findings.append(f"{path}:{line_number}: possible secret in {name} ({mask(value)})")
    return findings


def scan_files(root: Path, files: Iterable[Path], *, staged: bool) -> list[str]:
    findings: list[str] = []
    for path in files:
        if should_skip_path(path):
            continue
        text = read_staged_file(path) if staged else read_worktree_file(root, path)
        if text is None:
            continue
        findings.extend(scan_text(path, text))
    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description="Scan repository files for likely secrets.")
    parser.add_argument("files", nargs="*", help="Specific paths to scan.")
    parser.add_argument("--staged", action="store_true", help="Scan staged file contents.")
    parser.add_argument("--all-files", action="store_true", help="Scan tracked and untracked files.")
    args = parser.parse_args()

    root = git_root()
    os.chdir(root)

    if args.staged:
        files = staged_files()
    elif args.all_files or not args.files:
        files = tracked_and_untracked_files()
    else:
        files = [Path(file) for file in args.files]

    findings = scan_files(root, files, staged=args.staged)
    if findings:
        print("Potential secrets detected. Rotate exposed credentials and remove them before commit.")
        for finding in findings:
            print(f"  {finding}")
        return 1

    print("No likely secrets detected.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
