"""Learn forbidden-term candidates from rejected drafts."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import json
from pathlib import Path
import re

_TOKEN_RE = re.compile(r"[\u4e00-\u9fff]{2,}|[A-Za-z][A-Za-z'-]{2,}")


@dataclass(frozen=True)
class ForbiddenTermCandidate:
    term: str
    count: int
    source_chapters: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {"term": self.term, "count": self.count}
        if self.source_chapters:
            payload["source_chapters"] = list(self.source_chapters)
        return payload


def learn_forbidden_term_candidates(
    rejected_drafts_dir: str | Path,
    *,
    existing_terms: set[str] | None = None,
    whitelist: set[str] | None = None,
    top_n: int = 30,
    min_count: int = 2,
) -> tuple[ForbiddenTermCandidate, ...]:
    """Return high-frequency 1-3 gram candidates absent from existing terms."""

    root = Path(rejected_drafts_dir)
    if not root.exists():
        return ()
    existing = {term.strip() for term in (existing_terms or set()) if term.strip()}
    allowed = {term.strip() for term in (whitelist or set()) if term.strip()}
    counts: Counter[str] = Counter()
    sources: dict[str, set[str]] = {}
    for path in sorted(root.rglob("*.md")):
        source = _source_label(path)
        tokens = _tokens(path.read_text(encoding="utf-8", errors="ignore"))
        for size in (1, 2, 3):
            for index in range(0, max(0, len(tokens) - size + 1)):
                term = "".join(tokens[index:index + size])
                if _candidate_allowed(term, existing=existing, whitelist=allowed):
                    counts[term] += 1
                    sources.setdefault(term, set()).add(source)
    return tuple(
        ForbiddenTermCandidate(
            term=term,
            count=count,
            source_chapters=tuple(sorted(sources.get(term, ()))),
        )
        for term, count in counts.most_common()
        if count >= min_count
    )[:top_n]


def update_guardrails_with_candidates(
    story_bible_dir: str | Path,
    candidates: tuple[ForbiddenTermCandidate, ...],
) -> Path:
    """Write candidates to story-bible/canon-guardrails.json."""

    path = Path(story_bible_dir) / "canon-guardrails.json"
    payload: dict[str, object]
    if path.exists():
        payload = json.loads(path.read_text(encoding="utf-8"))
    else:
        payload = {}
    existing_candidates = {
        str(item.get("term"))
        for item in payload.get("forbidden_terms_candidates", [])
        if isinstance(item, dict)
    }
    additions = [
        candidate.to_dict()
        for candidate in candidates
        if candidate.term not in existing_candidates
    ]
    payload["forbidden_terms_candidates"] = [
        *(payload.get("forbidden_terms_candidates") or []),
        *additions,
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False),
        encoding="utf-8",
    )
    return path


def learn_and_update_guardrails(
    project_output_dir: str | Path,
    story_bible_dir: str | Path,
    *,
    existing_terms: set[str] | None = None,
    whitelist: set[str] | None = None,
    top_n: int = 30,
    min_count: int = 2,
) -> tuple[ForbiddenTermCandidate, ...]:
    """Learn from output/{project}/rejected-drafts and update guardrails."""

    candidates = learn_forbidden_term_candidates(
        Path(project_output_dir) / "rejected-drafts",
        existing_terms=existing_terms,
        whitelist=whitelist,
        top_n=top_n,
        min_count=min_count,
    )
    update_guardrails_with_candidates(story_bible_dir, candidates)
    return candidates


def learn_from_rejected_export(
    project_output_dir: str | Path,
    *,
    top_n: int = 30,
    min_count: int = 2,
) -> tuple[ForbiddenTermCandidate, ...]:
    """Refresh candidate pool after a rejected draft is persisted."""

    story_bible_dir = Path(project_output_dir) / "story-bible"
    payload = load_guardrails_payload(story_bible_dir)
    existing_terms, whitelist = guardrail_term_sets(payload)
    return learn_and_update_guardrails(
        project_output_dir,
        story_bible_dir,
        existing_terms=existing_terms,
        whitelist=whitelist,
        top_n=top_n,
        min_count=min_count,
    )


def load_guardrails_payload(story_bible_dir: str | Path) -> dict[str, object]:
    path = Path(story_bible_dir) / "canon-guardrails.json"
    if not path.exists():
        return {}
    loaded = json.loads(path.read_text(encoding="utf-8"))
    return loaded if isinstance(loaded, dict) else {}


def guardrail_term_sets(
    payload: dict[str, object],
) -> tuple[set[str], set[str]]:
    existing = _string_set(payload.get("forbidden_terms"))
    existing.update(_string_set(payload.get("forbidden_words")))
    whitelist = _string_set(payload.get("forbidden_terms_whitelist"))
    whitelist.update(_string_set(payload.get("forbidden_words_whitelist")))
    return existing, whitelist


def promote_forbidden_term_candidates(
    story_bible_dir: str | Path,
    terms: set[str],
) -> Path:
    """Move reviewed candidates into canon forbidden_terms."""

    path = Path(story_bible_dir) / "canon-guardrails.json"
    payload = load_guardrails_payload(story_bible_dir)
    existing_terms = [
        str(term).strip()
        for term in payload.get("forbidden_terms", [])
        if str(term).strip()
    ]
    promoted = sorted(term.strip() for term in terms if term.strip())
    merged = list(dict.fromkeys([*existing_terms, *promoted]))
    candidates = [
        item
        for item in payload.get("forbidden_terms_candidates", [])
        if not (isinstance(item, dict) and str(item.get("term", "")).strip() in terms)
    ]
    payload["forbidden_terms"] = merged
    payload["forbidden_terms_candidates"] = candidates
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False),
        encoding="utf-8",
    )
    return path


def _tokens(text: str) -> list[str]:
    return [match.group(0).strip() for match in _TOKEN_RE.finditer(text) if match.group(0).strip()]


def _candidate_allowed(term: str, *, existing: set[str], whitelist: set[str]) -> bool:
    if len(term) < 2:
        return False
    return term not in existing and term not in whitelist


def _source_label(path: Path) -> str:
    match = re.search(r"(?:ch|chapter)[-_ ]?(\d+)", path.stem, flags=re.IGNORECASE)
    if match:
        return f"ch{int(match.group(1))}"
    return path.stem


def _string_set(value: object) -> set[str]:
    if not isinstance(value, list):
        return set()
    return {str(item).strip() for item in value if str(item).strip()}
