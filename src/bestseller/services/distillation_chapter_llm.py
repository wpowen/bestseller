"""Run in-repo LLM extraction for distillation chapter jobs (Phase 2).

Reads per-chapter payloads under ``.distillation_private/``, calls
:func:`bestseller.services.llm.complete_text`, validates against
``chapter_card.schema.json``, and appends rows to ``chapter_cards.jsonl``.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Iterable

from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.infra.db.session import session_scope
from bestseller.services.llm import LLMCompletionRequest, complete_text
from bestseller.services.planner import _extract_json_payload
from bestseller.settings import AppSettings

SCHEMA_REL = Path("data/distillation/schemas/chapter_card.schema.json")

# Production guard: never send a single user prompt chunk larger than this window.
CHAPTER_TEXT_CHUNK_SOFT = 8000
CHAPTER_TEXT_CHUNK_HARD = 12000
DEFAULT_CHAPTER_JOB_TIMEOUT_SECONDS = 120.0
DEFAULT_DISTILLATION_SUMMARIZER_MAX_TOKENS = 6144


def _distillation_max_tokens(settings: AppSettings) -> int:
    """Resolve distillation summary token budget.

    This keeps compatibility with previous fixed 6144-token behavior but allows
    a higher per-role limit (for example, NVIDIA DeepSeek distillation) to
    flow through when configured.
    """

    try:
        configured = int(settings.llm.summarizer.max_tokens)
    except (TypeError, ValueError):
        configured = DEFAULT_DISTILLATION_SUMMARIZER_MAX_TOKENS
    return max(DEFAULT_DISTILLATION_SUMMARIZER_MAX_TOKENS, configured)


def _coerce_chapter_card_payload(
    obj: dict[str, Any],
    *,
    source_id: str,
    abs_no: int,
    volume_no: int,
) -> dict[str, Any]:
    row = dict(obj)
    # These identity fields come from the private job manifest, not the model.
    # Providers can echo stale examples or nearby source ids; trusting that
    # output corrupts resume keys and aggregate lineage.
    row["source_id"] = source_id
    row["abs_chapter_no"] = int(abs_no)
    row["volume_no"] = int(volume_no)

    chapter_function = str(row.get("chapter_function") or "").strip()
    if not chapter_function:
        chapter_function = "LLM chapter extraction fallback."
    row["chapter_function"] = chapter_function

    for key in (
        "state_changes",
        "reader_rewards",
        "open_hooks",
        "reusable_mechanisms",
        "non_reusable_specifics",
        "risk_flags",
    ):
        value = row.get(key)
        if not isinstance(value, list):
            row[key] = []

    craft = row.get("craft_observations")
    if not isinstance(craft, dict):
        row["craft_observations"] = {}

    confidence = row.get("confidence")
    try:
        conf = float(confidence) if confidence is not None else 0.0
    except (TypeError, ValueError):
        conf = 0.0
    if not (0.0 <= conf <= 1.0):
        conf = 0.0
    row["confidence"] = conf

    return row


def _read_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected JSON object")
    return data


def load_chapter_card_schema(repo_root: Path) -> dict[str, Any]:
    path = repo_root / SCHEMA_REL
    return _read_json(path)


def chapter_card_required_keys(schema: dict[str, Any]) -> list[str]:
    req = schema.get("required")
    if not isinstance(req, list):
        raise ValueError("chapter_card schema missing required[]")
    return [str(x) for x in req]


def validate_chapter_card(obj: dict[str, Any], *, schema: dict[str, Any]) -> None:
    for key in chapter_card_required_keys(schema):
        if key not in obj:
            raise ValueError(f"chapter_card missing required field: {key}")
    conf = obj.get("confidence")
    if not isinstance(conf, (int, float)) or not (0.0 <= float(conf) <= 1.0):
        raise ValueError("chapter_card.confidence must be a number in [0, 1]")
    acn = obj.get("abs_chapter_no")
    if not isinstance(acn, int) or acn < 1:
        raise ValueError("chapter_card.abs_chapter_no must be int >= 1")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        rows.append(json.loads(line))
    return rows


def existing_chapter_card_keys(chapter_cards_path: Path) -> set[tuple[str, int]]:
    keys: set[tuple[str, int]] = set()
    for row in read_jsonl(chapter_cards_path):
        sid = row.get("source_id")
        acn = row.get("abs_chapter_no")
        if isinstance(sid, str) and isinstance(acn, int):
            keys.add((sid, acn))
    return keys


def resolve_private_payload_path(repo_root: Path, private_root: Path, ref: str) -> Path:
    """Resolve ``private_payload_ref`` (repo-relative) to an absolute path."""
    ref_path = Path(ref)
    if ref_path.is_absolute():
        return ref_path
    if str(ref).startswith(".distillation_private/"):
        return (repo_root / ref).resolve()
    return (private_root / ref).resolve()


def split_chapter_text_for_llm(
    text: str,
    *,
    soft: int = CHAPTER_TEXT_CHUNK_SOFT,
    hard: int = CHAPTER_TEXT_CHUNK_HARD,
) -> list[str]:
    """Split chapter prose so every segment is ≤ ``soft`` chars (never exceeds ``hard``)."""

    text = text.strip()
    if not text:
        return []
    if len(text) <= soft:
        return [text]
    chunks: list[str] = []
    rest = text
    while rest:
        if len(rest) <= soft:
            chunks.append(rest)
            break
        window_end = min(len(rest), hard)
        window = rest[:window_end]
        split_at = min(soft, len(window))
        para = window.rfind("\n\n", 1, soft)
        if para > 0:
            split_at = para + 2
        else:
            nl = window.rfind("\n", 1, soft)
            if nl > 0:
                split_at = nl + 1
        piece = rest[:split_at].strip()
        if not piece:
            split_at = min(hard, len(rest))
            piece = rest[:split_at]
            if not piece:
                break
        chunks.append(piece)
        rest = rest[split_at:].lstrip()
    return [c for c in chunks if c]


def _dedupe_list(items: list[Any]) -> list[Any]:
    seen: set[str] = set()
    out: list[Any] = []
    for it in items:
        key = json.dumps(it, sort_keys=True, ensure_ascii=False) if isinstance(it, dict) else str(it)
        if key in seen:
            continue
        seen.add(key)
        out.append(it)
    return out


def _dedupe_strings(items: list[Any]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        text = str(item).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


def merge_craft_observations(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Merge abstract prose-craft observations from chapter segments."""

    buckets: dict[str, list[Any]] = {}
    for row in rows:
        craft = row.get("craft_observations")
        if not isinstance(craft, dict):
            continue
        for key, value in craft.items():
            if value in (None, "", [], {}):
                continue
            bucket = buckets.setdefault(str(key), [])
            if isinstance(value, list):
                bucket.extend(value)
            else:
                bucket.append(value)

    merged: dict[str, Any] = {}
    for key, values in buckets.items():
        deduped = _dedupe_strings(values)
        if not deduped:
            continue
        if key == "do_not_copy":
            merged[key] = deduped[:12]
        else:
            merged[key] = " / ".join(deduped[:4])
    return merged


def merge_chapter_card_segments(rows: list[dict[str, Any]], *, schema: dict[str, Any]) -> dict[str, Any]:
    """Merge per-subchunk chapter_card JSON into one schema-valid object."""

    if not rows:
        raise ValueError("merge_chapter_card_segments: empty rows")
    base = dict(rows[0])
    sid = str(base.get("source_id") or "")
    acn = int(base.get("abs_chapter_no") or 0)
    vol = int(base.get("volume_no") or 1)
    for r in rows[1:]:
        if str(r.get("source_id") or "") != sid or int(r.get("abs_chapter_no") or 0) != acn:
            raise ValueError("segment chapter_card source_id/abs_chapter_no mismatch")

    functions: list[str] = []
    state_changes: list[Any] = []
    reader_rewards: list[Any] = []
    open_hooks: list[Any] = []
    reusable_mechanisms: list[Any] = []
    non_reusable_specifics: list[Any] = []
    risk_flags: list[Any] = []
    craft_rows: list[dict[str, Any]] = []
    confidences: list[float] = []

    n = len(rows)
    for i, r in enumerate(rows):
        fn = str(r.get("chapter_function") or "").strip()
        if fn:
            functions.append(f"[分段{i + 1}/{n}]\n{fn}")
        for key, bucket in (
            ("state_changes", state_changes),
            ("reader_rewards", reader_rewards),
            ("open_hooks", open_hooks),
            ("reusable_mechanisms", reusable_mechanisms),
            ("non_reusable_specifics", non_reusable_specifics),
            ("risk_flags", risk_flags),
        ):
            v = r.get(key)
            if isinstance(v, list):
                bucket.extend(v)
        craft = r.get("craft_observations")
        if isinstance(craft, dict) and craft:
            craft_rows.append({"craft_observations": craft})
        c = r.get("confidence")
        if isinstance(c, (int, float)):
            confidences.append(float(c))

    merged: dict[str, Any] = {
        "source_id": sid,
        "abs_chapter_no": acn,
        "volume_no": vol,
        "chapter_function": "\n\n".join(functions) if functions else str(base.get("chapter_function") or ""),
        "state_changes": _dedupe_list(state_changes),
        "reader_rewards": _dedupe_list(reader_rewards),
        "open_hooks": _dedupe_list(open_hooks),
        "reusable_mechanisms": _dedupe_list(reusable_mechanisms),
        "craft_observations": merge_craft_observations(craft_rows),
        "non_reusable_specifics": _dedupe_list(non_reusable_specifics),
        "risk_flags": _dedupe_list(risk_flags),
        "confidence": min(confidences) if confidences else float(base.get("confidence") or 0.0),
    }
    validate_chapter_card(merged, schema=schema)
    return merged


def write_chapter_job_error(
    *,
    private_errors_dir: Path,
    job: dict[str, Any],
    error: str,
    exc_type: str | None = None,
) -> Path:
    """Persist a failed chapter job under ``.distillation_private/errors`` for resume triage."""

    private_errors_dir.mkdir(parents=True, exist_ok=True)
    job_id = str(job.get("job_id") or "unknown_job")
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in job_id)[:160] or "unknown_job"
    path = private_errors_dir / f"{safe}.json"
    payload = {
        "job_id": job.get("job_id"),
        "source_id": job.get("source_id"),
        "abs_chapter_no": job.get("abs_chapter_no"),
        "private_payload_ref": job.get("private_payload_ref"),
        "error": error,
        "exc_type": exc_type,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def build_extraction_prompts(
    payload: dict[str, Any],
    *,
    schema: dict[str, Any],
    max_chapter_chars: int | None,
    segment: tuple[int, int] | None = None,
) -> tuple[str, str]:
    system = str(payload.get("system") or "").strip()
    if not system:
        raise ValueError("payload missing system prompt")

    chapter_text = str(payload.get("chapter_text") or "")
    truncated = False
    if max_chapter_chars is not None and len(chapter_text) > max_chapter_chars:
        chapter_text = chapter_text[:max_chapter_chars]
        truncated = True

    task_obj: dict[str, Any] = {
        "task_type": payload.get("task_type"),
        "source_id": payload.get("source_id"),
        "abs_chapter_no": payload.get("abs_chapter_no"),
        "volume_no": payload.get("volume_no"),
        "boundary_type": payload.get("boundary_type"),
        "chapter_title_redacted": payload.get("chapter_title_redacted"),
        "genre_hint": payload.get("genre_hint"),
        "chapter_text": chapter_text,
        "chapter_text_truncated": truncated,
    }
    if segment is not None:
        task_obj["segment_index"] = segment[0]
        task_obj["segment_total"] = segment[1]
        task_obj["segment_note"] = (
            "You are processing ONE contiguous segment of the chapter. "
            "Extract mechanisms/rewards/hooks visible in THIS segment only; "
            "use empty arrays where a field has no signal in this slice. "
            "Keep source_id/abs_chapter_no/volume_no identical to the payload."
        )

    schema_hint = json.dumps(schema, ensure_ascii=False, indent=2)
    user_parts = [
        "Output exactly ONE JSON object matching the chapter_card schema.",
        "Do not wrap in markdown fences. Do not add commentary before or after the JSON.",
        f"Required top-level keys: {', '.join(chapter_card_required_keys(schema))}.",
        "Treat craft_observations as mandatory even if the schema marks it optional.",
        "craft_observations must contain non-empty anonymous craft controls only, not author imitation.",
        (
            "Include at least four concrete craft keys when signal exists: pov_distance, "
            "sentence_rhythm, paragraphing, dialogue_method, description_method, "
            "exposition_method, hook_delivery, transition_method, do_not_copy."
        ),
        "Do not quote source phrases, preserve distinctive word choices, or create a copyable style fingerprint.",
        "Do not set risk_flags to llm_fallback unless the payload explicitly says the LLM was unavailable.",
        "",
        "=== schema (for reference) ===",
        schema_hint,
        "",
        "=== task payload (JSON) ===",
        json.dumps(task_obj, ensure_ascii=False),
    ]
    return system, "\n".join(user_parts)


async def _complete_distillation_chapter_llm(
    session: AsyncSession,
    settings: AppSettings,
    *,
    system: str,
    user: str,
    job_id: str,
    source_id: str,
    abs_no: int,
    volume_no: int,
) -> dict[str, Any]:
    summarizer_max_tokens = _distillation_max_tokens(settings)

    result = await complete_text(
        session,
        settings,
        LLMCompletionRequest(
            logical_role="summarizer",
            system_prompt=system,
            user_prompt=user,
            fallback_response=json.dumps(
                {
                    "source_id": source_id,
                    "abs_chapter_no": abs_no,
                    "volume_no": 1,
                    "chapter_function": "LLM unavailable — distillation fallback stub.",
                    "state_changes": [],
                    "reader_rewards": [],
                    "open_hooks": [],
                    "reusable_mechanisms": [],
                    "non_reusable_specifics": [],
                    "risk_flags": ["llm_fallback"],
                    "confidence": 0.0,
                },
                ensure_ascii=False,
            ),
            prompt_template="distillation_chapter_card",
            prompt_version="v1",
            project_id=None,
            workflow_run_id=None,
            metadata={
                "distillation_job_id": job_id,
                "distillation_source_id": source_id,
                "abs_chapter_no": abs_no,
            },
            max_tokens_override=summarizer_max_tokens,
        ),
    )
    if result.provider == "fallback" or result.finish_reason == "fallback":
        raise RuntimeError(
            "distillation_chapter_card LLM call used fallback content; "
            "not writing a synthetic chapter_card"
        )

    try:
        if result.finish_reason == "length":
            raise ValueError("LLM output was truncated by max_tokens")
        obj = _extract_json_payload(result.content)
        if isinstance(obj, list):
            if len(obj) == 1 and isinstance(obj[0], dict):
                obj = obj[0]
        if not isinstance(obj, dict):
            raise ValueError("LLM output was not a JSON object")
        obj = _coerce_chapter_card_payload(
            obj,
            source_id=source_id,
            abs_no=abs_no,
            volume_no=volume_no,
        )
        if not _has_craft_observations(obj):
            raise ValueError("chapter_card craft_observations is empty")
        return obj
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
                fallback_response=json.dumps(
                    {
                        "source_id": source_id,
                        "abs_chapter_no": abs_no,
                        "volume_no": volume_no,
                        "chapter_function": "LLM unavailable — distillation fallback stub.",
                        "state_changes": [],
                        "reader_rewards": [],
                        "open_hooks": [],
                        "reusable_mechanisms": [],
                        "non_reusable_specifics": [],
                        "risk_flags": ["llm_fallback"],
                        "confidence": 0.0,
                    },
                    ensure_ascii=False,
                ),
                prompt_template="distillation_chapter_card_repair",
                prompt_version="v1",
                project_id=None,
                workflow_run_id=None,
                metadata={
                    "distillation_job_id": job_id,
                    "distillation_source_id": source_id,
                    "abs_chapter_no": abs_no,
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
        obj = _extract_json_payload(repair.content)
        if isinstance(obj, list) and len(obj) == 1 and isinstance(obj[0], dict):
            obj = obj[0]
        if not isinstance(obj, dict):
            raise ValueError("repaired LLM output was not a JSON object") from exc
        obj = _coerce_chapter_card_payload(
            obj,
            source_id=source_id,
            abs_no=abs_no,
            volume_no=volume_no,
        )
        if not _has_craft_observations(obj):
            raise ValueError("repaired chapter_card craft_observations is empty") from exc
        return obj


async def extract_chapter_card_for_job(
    session: AsyncSession,
    settings: AppSettings,
    *,
    repo_root: Path,
    private_root: Path,
    job: dict[str, Any],
    schema: dict[str, Any],
    max_chapter_chars: int | None,
) -> dict[str, Any]:
    ref = job.get("private_payload_ref")
    if not isinstance(ref, str) or not ref.strip():
        raise ValueError("job missing private_payload_ref")

    payload_path = resolve_private_payload_path(repo_root, private_root, ref)
    payload = _read_json(payload_path)

    source_id = str(job.get("source_id") or payload.get("source_id") or "")
    job_id = str(job.get("job_id") or "")
    abs_no = int(job.get("abs_chapter_no") or payload.get("abs_chapter_no") or 0)

    full_text = str(payload.get("chapter_text") or "")
    if max_chapter_chars is not None and len(full_text) > max_chapter_chars:
        full_text = full_text[: int(max_chapter_chars)]

    chunks = split_chapter_text_for_llm(full_text)
    if not chunks:
        raise ValueError("empty chapter_text after redaction load")

    segment_rows: list[dict[str, Any]] = []
    for idx, chunk in enumerate(chunks):
        seg_payload = dict(payload)
        seg_payload["chapter_text"] = chunk
        system, user = build_extraction_prompts(
            seg_payload,
            schema=schema,
            max_chapter_chars=None,
            segment=(idx + 1, len(chunks)) if len(chunks) > 1 else None,
        )
        if len(user) > CHAPTER_TEXT_CHUNK_HARD + 50000:
            raise ValueError("built user prompt exceeds safety budget (check chunking)")

        obj = await _complete_distillation_chapter_llm(
            session,
            settings,
            system=system,
            user=user,
            job_id=job_id,
            source_id=source_id,
            abs_no=abs_no,
            volume_no=int(payload.get("volume_no") or 1),
        )
        validate_chapter_card(obj, schema=schema)
        segment_rows.append(obj)

    if len(segment_rows) == 1:
        return segment_rows[0]
    return merge_chapter_card_segments(segment_rows, schema=schema)


def append_chapter_card_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def _chapter_card_key(row: dict[str, Any]) -> tuple[str, int] | None:
    sid = row.get("source_id")
    acn = row.get("abs_chapter_no")
    if isinstance(sid, str) and isinstance(acn, int):
        return sid, acn
    return None


def _has_craft_observations(row: dict[str, Any]) -> bool:
    craft = row.get("craft_observations")
    return isinstance(craft, dict) and any(v not in (None, "", [], {}) for v in craft.values())


def chapter_card_keys_missing_craft(chapter_cards_path: Path) -> set[tuple[str, int]]:
    """Return existing chapter-card keys that need prose-craft backfill."""

    missing: set[tuple[str, int]] = set()
    for row in read_jsonl(chapter_cards_path):
        key = _chapter_card_key(row)
        if key is not None and not _has_craft_observations(row):
            missing.add(key)
    return missing


def upsert_chapter_card_jsonl(path: Path, row: dict[str, Any]) -> None:
    """Replace an existing chapter card with the same key; append if absent."""

    key = _chapter_card_key(row)
    if key is None:
        append_chapter_card_jsonl(path, row)
        return
    rows = read_jsonl(path)
    replaced = False
    out: list[dict[str, Any]] = []
    for existing in rows:
        if _chapter_card_key(existing) == key:
            if not replaced:
                out.append(row)
                replaced = True
            continue
        out.append(existing)
    if not replaced:
        out.append(row)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in out),
        encoding="utf-8",
    )


def iter_pending_jobs(
    package_dir: Path,
    *,
    existing_keys: set[tuple[str, int]],
    limit: int | None,
    refresh_keys: set[tuple[str, int]] | None = None,
) -> Iterable[dict[str, Any]]:
    jobs_path = package_dir / "llm_jobs" / "chapter_jobs.index.jsonl"
    if not jobs_path.is_file():
        return
    count = 0
    for job in read_jsonl(jobs_path):
        sid = str(job.get("source_id") or "")
        acn = job.get("abs_chapter_no")
        if not sid or not isinstance(acn, int):
            continue
        key = (sid, acn)
        if key in existing_keys and key not in (refresh_keys or set()):
            continue
        yield job
        count += 1
        if limit is not None and count >= limit:
            break


async def run_pending_chapter_jobs_parallel(
    *,
    package_dir: Path,
    repo_root: Path,
    private_root: Path,
    settings: AppSettings,
    schema: dict[str, Any],
    max_concurrency: int = 4,
    limit: int | None = None,
    max_chapter_chars: int | None = None,
    private_errors_dir: Path | None = None,
    job_timeout_seconds: float | None = None,
    refresh_missing_craft_observations: bool = False,
) -> tuple[int, int]:
    """Run pending chapter-card extractions with bounded concurrency.

    Each job uses its own DB session (same contract as the sequential CLI).
    Appends to ``chapter_cards.jsonl`` are serialized with a lock so lines
    stay well-formed for resume.
    """

    out_path = package_dir / "chapter_cards.jsonl"
    done_keys = existing_chapter_card_keys(out_path)
    refresh_keys = (
        chapter_card_keys_missing_craft(out_path) if refresh_missing_craft_observations else set()
    )
    pending = list(
        iter_pending_jobs(
            package_dir,
            existing_keys=done_keys,
            refresh_keys=refresh_keys,
            limit=limit,
        )
    )
    if not pending:
        return 0, 0

    sem = asyncio.Semaphore(max(1, int(max_concurrency)))
    write_lock = asyncio.Lock()
    processed = 0
    failures = 0

    async def one(job: dict[str, Any]) -> tuple[bool, str | None]:
        async with sem:
            try:
                async def run_job() -> dict[str, Any]:
                    async with session_scope(settings) as session:
                        return await extract_chapter_card_for_job(
                            session,
                            settings,
                            repo_root=repo_root,
                            private_root=private_root,
                            job=job,
                            schema=schema,
                            max_chapter_chars=max_chapter_chars,
                        )

                timeout = DEFAULT_CHAPTER_JOB_TIMEOUT_SECONDS if job_timeout_seconds is None else float(job_timeout_seconds)
                row = await asyncio.wait_for(run_job(), timeout=timeout)
                async with write_lock:
                    key = _chapter_card_key(row)
                    if key in refresh_keys:
                        upsert_chapter_card_jsonl(out_path, row)
                        refresh_keys.discard(key)
                    else:
                        append_chapter_card_jsonl(out_path, row)
                    if key is not None:
                        done_keys.add(key)
                return True, None
            except asyncio.TimeoutError:
                timeout_msg = f"LLM chapter job timed out after {timeout:.1f}s"
                if private_errors_dir is not None:
                    write_chapter_job_error(
                        private_errors_dir=private_errors_dir,
                        job=job,
                        error=timeout_msg,
                        exc_type="TimeoutError",
                    )
                return False, timeout_msg
            except Exception as exc:  # noqa: BLE001
                if private_errors_dir is not None:
                    write_chapter_job_error(
                        private_errors_dir=private_errors_dir,
                        job=job,
                        error=str(exc),
                        exc_type=type(exc).__name__,
                    )
                return False, f"{type(exc).__name__}: {exc}"

    results = await asyncio.gather(*[one(job) for job in pending])
    for ok, _err in results:
        if ok:
            processed += 1
        else:
            failures += 1
    return processed, failures
