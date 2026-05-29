"""LLM extraction for prepared writing-methodology book sections.

This is the second phase after ``prepare_methodology_book``.  It reads private
section prompt payloads, asks the configured summarizer model to extract
transferable methodology candidates, validates the result, and writes only
repo-safe review JSONL rows.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
import json
from pathlib import Path
import re
from typing import Any, get_args

from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.infra.db.session import session_scope
from bestseller.services.llm import LLMCompletionRequest, complete_text
from bestseller.services.methodology_book_distillation import MethodologyCandidateDeck
from bestseller.services.methodology_cards import (
    MethodologyCategory,
    MethodologyScope,
    MethodologyStage,
)
from bestseller.services.planner import _extract_json_payload
from bestseller.settings import AppSettings

SCHEMA_REL = Path("data/methodology_books/schemas/methodology_candidate.schema.json")
DEFAULT_METHODOLOGY_JOB_TIMEOUT_SECONDS = 180.0
DEFAULT_METHODOLOGY_SUMMARIZER_MAX_TOKENS = 8192
SECTION_TEXT_HARD_CAP = 24000
_CANDIDATE_ID_RE = re.compile(r"^[a-z0-9_.-]+$")


def _methodology_max_tokens(settings: AppSettings) -> int:
    try:
        configured = int(settings.llm.summarizer.max_tokens)
    except (TypeError, ValueError):
        configured = DEFAULT_METHODOLOGY_SUMMARIZER_MAX_TOKENS
    return max(DEFAULT_METHODOLOGY_SUMMARIZER_MAX_TOKENS, configured)


def _read_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected JSON object")
    return data


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for line_no, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        stripped = raw.strip()
        if not stripped:
            continue
        obj = json.loads(stripped)
        if not isinstance(obj, dict):
            raise ValueError(f"{path}:{line_no}: expected JSON object")
        rows.append(obj)
    return rows


def load_methodology_candidate_schema(repo_root: Path) -> dict[str, Any]:
    return _read_json(repo_root / SCHEMA_REL)


def methodology_candidate_required_keys(schema: dict[str, Any]) -> list[str]:
    candidates = schema.get("properties", {}).get("candidates", {})
    item = candidates.get("items", {}) if isinstance(candidates, dict) else {}
    req = item.get("required") if isinstance(item, dict) else None
    if not isinstance(req, list):
        raise ValueError("methodology_candidate schema missing candidates.items.required[]")
    return [str(x) for x in req]


def resolve_methodology_private_payload_path(
    repo_root: Path,
    private_root: Path,
    ref: str,
) -> Path:
    ref_path = Path(ref)
    if ref_path.is_absolute():
        return ref_path
    if str(ref).startswith(".methodology_private/"):
        return (repo_root / ref).resolve()
    return (private_root / ref).resolve()


def sample_long_section_text(text: str, *, max_chars: int = SECTION_TEXT_HARD_CAP) -> str:
    text = text.strip()
    if len(text) <= max_chars:
        return text
    head = max_chars // 3
    middle = max_chars // 3
    tail = max_chars - head - middle
    mid_start = max(0, (len(text) // 2) - (middle // 2))
    mid_end = min(len(text), mid_start + middle)
    return "\n\n[...LONG_SECTION_SAMPLE_BOUNDARY...]\n\n".join(
        (
            text[:head].rstrip(),
            text[mid_start:mid_end].strip(),
            text[-tail:].lstrip(),
        )
    )


def _safe_candidate_id(
    value: object,
    *,
    source_id: str,
    section_id: str,
    index: int,
) -> str:
    raw = str(value or "").strip().lower()
    safe = re.sub(r"[^a-z0-9_.-]+", "-", raw).strip(".-")
    if safe and _CANDIDATE_ID_RE.match(safe):
        return safe[:96]
    return f"{source_id}.{section_id}.{index + 1:02d}"


def _text_tuple(value: object, *, default: tuple[str, ...] = ()) -> tuple[str, ...]:
    if isinstance(value, str):
        items = (value,)
    elif isinstance(value, list | tuple):
        items = tuple(str(item).strip() for item in value if str(item).strip())
    else:
        items = default
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if not item or item in seen:
            continue
        seen.add(item)
        out.append(item)
    return tuple(out)


def _coerce_candidate_payload(
    obj: dict[str, Any],
    *,
    source_id: str,
    section_id: str,
    index: int,
) -> dict[str, Any]:
    row = dict(obj)
    # Identity comes from the private job manifest, not from the model.
    row["source_id"] = source_id
    row["section_id"] = section_id
    row["candidate_id"] = _safe_candidate_id(
        row.get("candidate_id"),
        source_id=source_id,
        section_id=section_id,
        index=index,
    )
    row["scope"] = _text_tuple(row.get("scope"), default=("scene",))
    row["stage"] = _text_tuple(row.get("stage"), default=("planning",))
    row["framework_bindings"] = _text_tuple(
        row.get("framework_bindings"),
        default=("methodology_compiler",),
    )
    for key in (
        "alignment_terms",
        "anti_patterns",
        "conflicts_with",
        "operating_steps",
        "required_contract_fields",
    ):
        row[key] = _text_tuple(row.get(key))

    gates = row.get("gate_bindings")
    if not isinstance(gates, list):
        row["gate_bindings"] = []

    confidence = row.get("confidence")
    try:
        conf = float(confidence) if confidence is not None else 0.0
    except (TypeError, ValueError):
        conf = 0.0
    row["confidence"] = min(1.0, max(0.0, conf))

    if not str(row.get("title") or "").strip():
        row["title"] = f"Methodology candidate {section_id}-{index + 1:02d}"
    if not str(row.get("category") or "").strip():
        row["category"] = "scene_design"
    if not str(row.get("core_claim") or "").strip():
        row["core_claim"] = "Convert the section signal into an explicit craft decision."
    return row


def _candidate_deck_from_obj(
    obj: object,
    *,
    source_id: str,
    section_id: str,
) -> MethodologyCandidateDeck:
    if isinstance(obj, list):
        raw_candidates = obj
    elif isinstance(obj, dict) and isinstance(obj.get("candidates"), list):
        raw_candidates = obj["candidates"]
    elif isinstance(obj, dict) and "core_claim" in obj:
        raw_candidates = [obj]
    else:
        raise ValueError("LLM output must be a JSON object with candidates[]")

    candidate_rows: list[dict[str, Any]] = []
    for idx, item in enumerate(raw_candidates):
        if not isinstance(item, dict):
            raise ValueError(f"candidates[{idx}] must be a JSON object")
        candidate_rows.append(
            _coerce_candidate_payload(
                item,
                source_id=source_id,
                section_id=section_id,
                index=idx,
            )
        )
    return MethodologyCandidateDeck.model_validate({"candidates": candidate_rows})


def validate_methodology_candidate_deck(deck: MethodologyCandidateDeck) -> None:
    seen: set[str] = set()
    for candidate in deck.candidates:
        if candidate.candidate_id in seen:
            raise ValueError(f"duplicate candidate_id: {candidate.candidate_id}")
        seen.add(candidate.candidate_id)


def build_methodology_extraction_prompts(
    payload: dict[str, Any],
    *,
    schema: dict[str, Any],
    max_section_chars: int | None,
) -> tuple[str, str]:
    system = str(payload.get("system") or "").strip()
    if not system:
        raise ValueError("payload missing system prompt")

    section_text = str(payload.get("section_text") or "")
    truncated = False
    hard_cap = SECTION_TEXT_HARD_CAP if max_section_chars is None else min(
        SECTION_TEXT_HARD_CAP,
        int(max_section_chars),
    )
    if len(section_text) > hard_cap:
        section_text = sample_long_section_text(section_text, max_chars=hard_cap)
        truncated = True

    task_obj = {
        "task_type": payload.get("task_type"),
        "source_id": payload.get("source_id"),
        "section_id": payload.get("section_id"),
        "abs_section_no": payload.get("abs_section_no"),
        "boundary_type": payload.get("boundary_type"),
        "section_title_redacted": payload.get("section_title_redacted"),
        "language_hint": payload.get("language_hint"),
        "section_text": section_text,
        "section_text_truncated": truncated,
    }
    user_parts = [
        "Output exactly ONE JSON object: {\"candidates\": [...]}",
        "Do not wrap in markdown fences. Do not add commentary before or after the JSON.",
        "Extract zero to five transferable writing methods from this section.",
        "Do not summarize the section; convert advice into executable rules, gates, "
        "or repair steps.",
        "Never quote source prose or preserve distinctive phrasing from the book.",
        f"Required candidate keys: {', '.join(methodology_candidate_required_keys(schema))}.",
        f"Allowed category values: {', '.join(get_args(MethodologyCategory))}.",
        f"Allowed scope values: {', '.join(get_args(MethodologyScope))}.",
        f"Allowed stage values: {', '.join(get_args(MethodologyStage))}.",
        "Use framework_bindings like outline, scene_card, character_arc, chapter_review, "
        "methodology_compiler, quality_gate, revision_queue, or draft_prompt.",
        "Use gate_bindings only when the method can become a concrete validation or advisory gate.",
        "",
        "=== schema (for reference) ===",
        json.dumps(schema, ensure_ascii=False, indent=2),
        "",
        "=== task payload (JSON) ===",
        json.dumps(task_obj, ensure_ascii=False),
    ]
    return system, "\n".join(user_parts)


async def _complete_methodology_section_llm(
    session: AsyncSession,
    settings: AppSettings,
    *,
    system: str,
    user: str,
    job_id: str,
    source_id: str,
    section_id: str,
) -> MethodologyCandidateDeck:
    summarizer_max_tokens = _methodology_max_tokens(settings)
    fallback = json.dumps({"candidates": []}, ensure_ascii=False)
    result = await complete_text(
        session,
        settings,
        LLMCompletionRequest(
            logical_role="summarizer",
            system_prompt=system,
            user_prompt=user,
            fallback_response=fallback,
            prompt_template="methodology_section_candidates",
            prompt_version="v1",
            project_id=None,
            workflow_run_id=None,
            metadata={
                "methodology_job_id": job_id,
                "methodology_source_id": source_id,
                "section_id": section_id,
            },
            max_tokens_override=summarizer_max_tokens,
        ),
    )
    if result.provider == "fallback" or result.finish_reason == "fallback":
        raise RuntimeError(
            "methodology_section_candidates LLM call used fallback content; "
            "not writing synthetic candidates"
        )

    try:
        if result.finish_reason == "length":
            raise ValueError("LLM output was truncated by max_tokens")
        deck = _candidate_deck_from_obj(
            _extract_json_payload(result.content),
            source_id=source_id,
            section_id=section_id,
        )
        validate_methodology_candidate_deck(deck)
        return deck
    except Exception as exc:
        from bestseller.services.llm_closed_loop import (
            build_repair_user_prompt,
            findings_from_exception,
        )

        repair = await complete_text(
            session,
            settings,
            LLMCompletionRequest(
                logical_role="summarizer",
                system_prompt=system,
                user_prompt=build_repair_user_prompt(
                    original_user_prompt=user,
                    findings=findings_from_exception(exc),
                    language=None,
                ),
                fallback_response=fallback,
                prompt_template="methodology_section_candidates_repair",
                prompt_version="v1",
                project_id=None,
                workflow_run_id=None,
                metadata={
                    "methodology_job_id": job_id,
                    "methodology_source_id": source_id,
                    "section_id": section_id,
                    "semantic_repair_of": str(result.llm_run_id)
                    if result.llm_run_id
                    else None,
                },
                max_tokens_override=summarizer_max_tokens,
            ),
        )
        if repair.provider == "fallback" or repair.finish_reason == "fallback":
            raise
        if repair.finish_reason == "length":
            raise ValueError("repaired LLM output was truncated by max_tokens") from exc
        deck = _candidate_deck_from_obj(
            _extract_json_payload(repair.content),
            source_id=source_id,
            section_id=section_id,
        )
        validate_methodology_candidate_deck(deck)
        return deck


async def extract_methodology_candidates_for_job(
    session: AsyncSession,
    settings: AppSettings,
    *,
    repo_root: Path,
    private_root: Path,
    job: dict[str, Any],
    schema: dict[str, Any],
    max_section_chars: int | None,
) -> MethodologyCandidateDeck:
    ref = job.get("private_payload_ref")
    if not isinstance(ref, str) or not ref.strip():
        raise ValueError("job missing private_payload_ref")

    payload = _read_json(resolve_methodology_private_payload_path(repo_root, private_root, ref))
    source_id = str(job.get("source_id") or payload.get("source_id") or "")
    section_id = str(job.get("section_id") or payload.get("section_id") or "")
    job_id = str(job.get("job_id") or f"{source_id}-{section_id}")
    if not source_id or not section_id:
        raise ValueError("job missing source_id/section_id")

    system, user = build_methodology_extraction_prompts(
        payload,
        schema=schema,
        max_section_chars=max_section_chars,
    )
    return await _complete_methodology_section_llm(
        session,
        settings,
        system=system,
        user=user,
        job_id=job_id,
        source_id=source_id,
        section_id=section_id,
    )


def methodology_section_key(row: dict[str, Any]) -> tuple[str, str] | None:
    sid = row.get("source_id")
    section_id = row.get("section_id")
    if isinstance(sid, str) and isinstance(section_id, str):
        return sid, section_id
    return None


def existing_methodology_section_keys(results_path: Path) -> set[tuple[str, str]]:
    keys: set[tuple[str, str]] = set()
    for row in read_jsonl(results_path):
        key = methodology_section_key(row)
        if key is not None:
            keys.add(key)
            continue
        candidates = row.get("candidates")
        if isinstance(candidates, list):
            for candidate in candidates:
                if isinstance(candidate, dict):
                    key = methodology_section_key(candidate)
                    if key is not None:
                        keys.add(key)
    return keys


def append_methodology_section_result_jsonl(
    path: Path,
    *,
    source_id: str,
    section_id: str,
    deck: MethodologyCandidateDeck,
) -> None:
    row = {
        "source_id": source_id,
        "section_id": section_id,
        "candidate_count": len(deck.candidates),
        "candidates": [candidate.model_dump(mode="json") for candidate in deck.candidates],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def write_methodology_job_error(
    *,
    private_errors_dir: Path,
    job: dict[str, Any],
    error: str,
    exc_type: str | None = None,
) -> Path:
    private_errors_dir.mkdir(parents=True, exist_ok=True)
    job_id = str(job.get("job_id") or "unknown_job")
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in job_id)[:160] or "unknown_job"
    path = private_errors_dir / f"{safe}.json"
    payload = {
        "job_id": job.get("job_id"),
        "source_id": job.get("source_id"),
        "section_id": job.get("section_id"),
        "private_payload_ref": job.get("private_payload_ref"),
        "error": error,
        "exc_type": exc_type,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def iter_pending_section_jobs(
    package_dir: Path,
    *,
    existing_keys: set[tuple[str, str]],
    limit: int | None,
) -> Iterable[dict[str, Any]]:
    jobs_path = package_dir / "llm_jobs" / "section_jobs.index.jsonl"
    if not jobs_path.is_file():
        return
    count = 0
    for job in read_jsonl(jobs_path):
        sid = str(job.get("source_id") or "")
        section_id = str(job.get("section_id") or "")
        if not sid or not section_id:
            continue
        if (sid, section_id) in existing_keys:
            continue
        yield job
        count += 1
        if limit is not None and count >= limit:
            break


async def run_pending_methodology_section_jobs_parallel(
    *,
    package_dir: Path,
    repo_root: Path,
    private_root: Path,
    settings: AppSettings,
    schema: dict[str, Any],
    max_concurrency: int = 2,
    limit: int | None = None,
    max_section_chars: int | None = None,
    private_errors_dir: Path | None = None,
    job_timeout_seconds: float | None = None,
) -> tuple[int, int]:
    out_path = package_dir / "methodology_candidates.review.jsonl"
    done_keys = existing_methodology_section_keys(out_path)
    pending = list(
        iter_pending_section_jobs(package_dir, existing_keys=done_keys, limit=limit)
    )
    if not pending:
        return 0, 0

    sem = asyncio.Semaphore(max(1, int(max_concurrency)))
    write_lock = asyncio.Lock()
    processed = 0
    failures = 0

    async def one(job: dict[str, Any]) -> bool:
        async with sem:
            timeout = (
                DEFAULT_METHODOLOGY_JOB_TIMEOUT_SECONDS
                if job_timeout_seconds is None
                else float(job_timeout_seconds)
            )
            try:
                async def run_job() -> MethodologyCandidateDeck:
                    async with session_scope(settings) as session:
                        return await extract_methodology_candidates_for_job(
                            session,
                            settings,
                            repo_root=repo_root,
                            private_root=private_root,
                            job=job,
                            schema=schema,
                            max_section_chars=max_section_chars,
                        )

                deck = await asyncio.wait_for(run_job(), timeout=timeout)
                source_id = str(job.get("source_id") or "")
                section_id = str(job.get("section_id") or "")
                async with write_lock:
                    append_methodology_section_result_jsonl(
                        out_path,
                        source_id=source_id,
                        section_id=section_id,
                        deck=deck,
                    )
                    done_keys.add((source_id, section_id))
                return True
            except TimeoutError:
                timeout_msg = f"LLM methodology job timed out after {timeout:.1f}s"
                if private_errors_dir is not None:
                    write_methodology_job_error(
                        private_errors_dir=private_errors_dir,
                        job=job,
                        error=timeout_msg,
                        exc_type="TimeoutError",
                    )
                return False
            except Exception as exc:
                if private_errors_dir is not None:
                    write_methodology_job_error(
                        private_errors_dir=private_errors_dir,
                        job=job,
                        error=str(exc),
                        exc_type=type(exc).__name__,
                    )
                return False

    results = await asyncio.gather(*[one(job) for job in pending])
    for ok in results:
        if ok:
            processed += 1
        else:
            failures += 1
    return processed, failures
