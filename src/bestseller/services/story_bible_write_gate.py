"""Pre-write story-bible completeness gate (premise / world / characters / outlines)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from bestseller.services.planning_readiness_gate import evaluate_planning_readiness

STORY_BIBLE_INCOMPLETE: str = "STORY_BIBLE_INCOMPLETE"
STORY_BIBLE_MISSING_FILE: str = "STORY_BIBLE_MISSING_FILE"

_REQUIRED_FILES = (
    "premise.md",
    "world.md",
    "characters.md",
    "volume-plan.md",
)


@dataclass(frozen=True)
class StoryBibleWriteFinding:
    code: str
    severity: str
    message: str
    path: str


@dataclass(frozen=True)
class StoryBibleWriteReport:
    passed: bool
    findings: tuple[StoryBibleWriteFinding, ...]

    @property
    def blocking_codes(self) -> tuple[str, ...]:
        return tuple(
            dict.fromkeys(
                f.code
                for f in self.findings
                if f.severity in {"critical", "high"}
            )
        )


def _read_if_exists(path: Path) -> str:
    if path.is_file():
        return path.read_text(encoding="utf-8")
    return ""


def evaluate_story_bible_write_readiness(
    bible_root: Path,
    *,
    chapter_outlines: Sequence[Mapping[str, Any] | Any] = (),
    min_premise_chars: int = 200,
    min_world_chars: int = 300,
    min_characters_chars: int = 220,
    min_volume_plan_chars: int = 200,
) -> StoryBibleWriteReport:
    """Block WRITE when story-bible artifacts are missing or too thin."""

    findings: list[StoryBibleWriteFinding] = []
    root = Path(bible_root)

    for name in _REQUIRED_FILES:
        path = root / name
        if not path.is_file():
            findings.append(
                StoryBibleWriteFinding(
                    code=STORY_BIBLE_MISSING_FILE,
                    severity="critical",
                    message=f"missing {name}",
                    path=str(path),
                )
            )

    premise = _read_if_exists(root / "premise.md")
    world = _read_if_exists(root / "world.md")
    characters = _read_if_exists(root / "characters.md")
    volume_plan_text = _read_if_exists(root / "volume-plan.md")

    if premise and len(premise.strip()) < min_premise_chars:
        findings.append(
            StoryBibleWriteFinding(
                code=STORY_BIBLE_INCOMPLETE,
                severity="critical",
                message=f"premise.md too short ({len(premise.strip())} chars)",
                path=str(root / "premise.md"),
            )
        )
    if world and len(world.strip()) < min_world_chars:
        findings.append(
            StoryBibleWriteFinding(
                code=STORY_BIBLE_INCOMPLETE,
                severity="critical",
                message=f"world.md too short ({len(world.strip())} chars)",
                path=str(root / "world.md"),
            )
        )
    if characters and len(characters.strip()) < min_characters_chars:
        findings.append(
            StoryBibleWriteFinding(
                code=STORY_BIBLE_INCOMPLETE,
                severity="critical",
                message=f"characters.md too short ({len(characters.strip())} chars)",
                path=str(root / "characters.md"),
            )
        )
    if (
        volume_plan_text
        and len(volume_plan_text.strip()) < min_volume_plan_chars
        and not chapter_outlines
    ):
        findings.append(
            StoryBibleWriteFinding(
                code=STORY_BIBLE_INCOMPLETE,
                severity="critical",
                message=(
                    "volume-plan.md is too thin / has no executable per-chapter "
                    f"outlines ({len(volume_plan_text.strip())} chars)"
                ),
                path=str(root / "volume-plan.md"),
            )
        )

    if chapter_outlines:
        readiness = evaluate_planning_readiness(chapter_outlines=chapter_outlines)
        for item in readiness.blocking_findings:
            findings.append(
                StoryBibleWriteFinding(
                    code=item.code,
                    severity=item.severity,
                    message=item.message,
                    path=item.path,
                )
            )

    blocking = [f for f in findings if f.severity in {"critical", "high"}]
    return StoryBibleWriteReport(
        passed=not blocking,
        findings=tuple(findings),
    )


__all__ = [
    "STORY_BIBLE_INCOMPLETE",
    "STORY_BIBLE_MISSING_FILE",
    "StoryBibleWriteFinding",
    "StoryBibleWriteReport",
    "evaluate_story_bible_write_readiness",
]
