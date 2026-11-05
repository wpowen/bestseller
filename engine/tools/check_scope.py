#!/usr/bin/env python3
"""Report package sizes and check file limits, dependencies and forbidden words."""

import ast
from pathlib import Path
import re
import sys


PACKAGES = ("bookengine", "deai", "webui", "exporter", "ideation", "shared")
BLOCKED = re.compile(
    r"provenance|custody|receipt|sha256|fingerprint|credential_scan|visual_qa|render_v",
    re.I,
)
BLOCKED_IMPORTS = {
    "bookengine": {"deai"},
    "deai": {"bookengine"},
    "ideation": {"bookengine"},
    "shared": {"bookengine", "deai"},
}


def python_paths(root, package):
    if package == "shared":
        return {p for name in ("runtime_support.py", "chapter_outputs.py") if (p := root / "tools" / name).is_file()}

    paths = set((root / package).rglob("*.py"))
    if package == "webui":
        paths.update((root / "tools/webui").rglob("*.py"))
        entrypoint = root / "tools/webui.py"
        if entrypoint.exists():
            paths.add(entrypoint)
    return paths


def imported_modules(node):
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]
    if isinstance(node, ast.ImportFrom):
        return [node.module or ""]
    if not isinstance(node, ast.Call):
        return []

    if isinstance(node.func, ast.Name):
        name = node.func.id
    elif isinstance(node.func, ast.Attribute):
        name = node.func.attr
    else:
        return []
    if name in ("__import__", "import_module") and node.args:
        argument = node.args[0]
        if isinstance(argument, ast.Constant):
            return [str(argument.value)]
    return []


def check_frontend(root, errors):
    assets = sorted(path for path in (root / "tools/webui").rglob("*") if path.suffix in (".js", ".html", ".css"))
    counts = []
    wide = []
    for path in assets:
        lines = path.read_text().splitlines()
        counts.append((len(lines), path.name))
        for number, line in enumerate(lines, 1):
            if len(line) > 160:
                wide.append(f"{path.name}:{number}")

    total = sum(count for count, _ in counts)
    maximum, name = max(counts or [(0, "none")])
    print(f"webui_frontend_lines={total} (max_file={maximum} {name})")
    if maximum > 800:
        errors.append("frontend file exceeds 800")
    if wide:
        errors.append(f"frontend lines over 160 chars: {len(wide)} ({wide[0]})")


def main():
    tool = Path(__file__).resolve()
    root = tool.parents[1]
    errors = []
    cross = []
    imports = []
    words = []
    maximum = (len(tool.read_text().splitlines()), str(tool.relative_to(root)))

    for package in PACKAGES:
        count = 0
        for path in sorted(python_paths(root, package)):
            if {"tests", "eval", "__pycache__"} & set(path.parts):
                continue
            source = path.read_text()
            lines = len(source.splitlines())
            count += lines
            if lines > maximum[0]:
                maximum = (lines, str(path.relative_to(root)))
            words.extend(f"{path.name}:{match[0]}" for match in BLOCKED.finditer(source))

            try:
                tree = ast.parse(source)
            except SyntaxError:
                errors.append(f"{path.name}:syntax")
                continue
            for node in ast.walk(tree):
                for module in imported_modules(node):
                    parts = set(module.split("."))
                    if "bestseller" in parts:
                        imports.append(f"{path.name}:{module}")
                    if BLOCKED_IMPORTS.get(package, set()) & parts:
                        cross.append(f"{package}:{module}")
        print(f"{package}_lines={count}")

    check_frontend(root, errors)
    print(f"max_file_lines={maximum[0]} ({maximum[1]})")
    print("imports_bestseller=" + (", ".join(imports) or "no"))
    print("cross_imports=" + (", ".join(cross) or "none"))
    print("forbidden_keywords=" + (", ".join(words) or "none"))
    if maximum[0] > 500:
        errors.append("file exceeds 500")
    if imports or cross or words:
        errors.append("dependency/scope violation")
    if errors:
        print("FAIL: " + "; ".join(errors))
    else:
        print("PASS: file limits and independence")
    return bool(errors)


if __name__ == "__main__":
    sys.exit(main())
