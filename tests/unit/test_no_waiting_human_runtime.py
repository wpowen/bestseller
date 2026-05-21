from __future__ import annotations

from pathlib import Path


RUNTIME_ROOTS = (
    Path("src/bestseller"),
    Path("scripts"),
)

FORBIDDEN_WAITING_HUMAN_TOKENS = (
    "WAITING_HUMAN",
    "waiting_human",
    "waiting_human_review",
    "paused_for_human_review",
    "等待人工",
    "等待复核",
)


def test_runtime_code_does_not_emit_waiting_human_states() -> None:
    offenders: list[str] = []
    for root in RUNTIME_ROOTS:
        for path in root.rglob("*"):
            if path.suffix not in {".py", ".html", ".js", ".ts"}:
                continue
            text = path.read_text(encoding="utf-8")
            for token in FORBIDDEN_WAITING_HUMAN_TOKENS:
                if token in text:
                    offenders.append(f"{path}:{token}")

    assert offenders == []
