from __future__ import annotations

import json
from pathlib import Path


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    refs: list[dict[str, object]] = []
    for path in sorted((root / "src").rglob("*.py")):
        text = path.read_text(encoding="utf-8", errors="ignore")
        for line_no, line in enumerate(text.splitlines(), start=1):
            if "KernelComposer" in line or "kernel_composer" in line:
                refs.append(
                    {
                        "path": str(path.relative_to(root)),
                        "line": line_no,
                        "text": line.strip(),
                    }
                )
    print(json.dumps({"references": refs}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
