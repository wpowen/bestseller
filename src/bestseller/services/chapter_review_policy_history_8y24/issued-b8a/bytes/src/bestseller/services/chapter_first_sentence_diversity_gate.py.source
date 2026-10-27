"""Post-generation gate for chapter first-sentence diversity."""
# ruff: noqa: RUF001

from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
import re


@dataclass(frozen=True)
class FirstSentenceDiversityResult:
    passed: bool
    similarity_max: float
    matched_chapter: int | None
    reason: str


_PUNCT_RE = re.compile(r"[\s，。！？、；：“”‘’（）()【】\[\]《》,.!?;:'\"…·—-]+")
_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+")


def extract_first_sentence(text: str) -> str:
    """Return the first publishable body line from chapter markdown."""

    for raw_line in (text or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if _HEADING_RE.match(line):
            continue
        return line
    return ""


def _normalized(text: str) -> str:
    return _PUNCT_RE.sub("", text or "").lower()


def _char_distance_ratio(left: str, right: str) -> float:
    """Return normalized edit distance using a small dynamic-programming matrix."""

    a = _normalized(left)
    b = _normalized(right)
    if not a and not b:
        return 0.0
    if not a or not b:
        return 1.0
    previous = list(range(len(b) + 1))
    for i, char_a in enumerate(a, start=1):
        current = [i]
        for j, char_b in enumerate(b, start=1):
            current.append(
                min(
                    previous[j] + 1,
                    current[j - 1] + 1,
                    previous[j - 1] + (0 if char_a == char_b else 1),
                )
            )
        previous = current
    return previous[-1] / max(len(a), len(b), 1)


def check_first_sentence_diversity(
    *,
    current_first_sentence: str,
    recent_first_sentences: dict[int, str],
    similarity_threshold: float = 0.70,
    distance_threshold: float = 0.30,
) -> FirstSentenceDiversityResult:
    """Check the current first sentence against recent chapter openings."""

    current = (current_first_sentence or "").strip()
    if not current or not recent_first_sentences:
        return FirstSentenceDiversityResult(True, 0.0, None, "ok")

    max_similarity = 0.0
    matched_chapter: int | None = None
    matched_distance = 1.0
    for chapter_no, sentence in recent_first_sentences.items():
        prior = (sentence or "").strip()
        if not prior:
            continue
        similarity = SequenceMatcher(None, _normalized(current), _normalized(prior)).ratio()
        distance = _char_distance_ratio(current, prior)
        if similarity > max_similarity:
            max_similarity = similarity
            matched_chapter = int(chapter_no)
            matched_distance = distance

    if matched_chapter is not None and (
        max_similarity >= similarity_threshold or matched_distance <= distance_threshold
    ):
        return FirstSentenceDiversityResult(
            passed=False,
            similarity_max=max_similarity,
            matched_chapter=matched_chapter,
            reason=(
                f"too similar to ch{matched_chapter} "
                f"(similarity={max_similarity:.2f}, distance={matched_distance:.2f})"
            ),
        )
    return FirstSentenceDiversityResult(True, max_similarity, matched_chapter, "ok")
