"""Source-bound v1 length contracts for main's native generation/adoption flow.

Persist the request in ``draft.promotion_metadata['book_length_control']``.
``length_passed`` is a numerical prerequisite only. Every returned receipt has
grade/adoption/credit/dispatch=False. A digest detects drift, not authorization.

The caller owns transaction isolation, flushes, source/CAS fences and existing
lock ordering. These functions acquire no locks, commit nothing, dispatch no
work and never reserve budget. Revalidate immediately before native adoption,
and evaluate the *actual* candidate body separately in that same transaction.

Fixed-suffix support preserves the four public call signatures and v1 schema.
Only replacement requests with an adopted suffix add ``source.adopted_suffix``
and ``budget.mode='fixed_suffix_replacement'``. Its ``targets`` contains only
the requested chapter's temporary aim. Original ``source.plan`` targets and
other adopted bodies stay fixed. Unknown future chapters reserve their original
band capacity. Candidate previews and native refresh count the complete adopted
prefix, including the suffix, exactly once. Prefix-only receipts retain their
existing shape. All source identity changes require a new request, never a
silent migration of an old candidate or paid legacy result.
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import asdict
import hashlib
import json
from typing import Any, TypeVar
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from bestseller.infra.db.models import ChapterDraftVersionModel, ChapterModel, ProjectModel
from bestseller.services.adopted_chapter_source import select_adopted_chapter_source
from bestseller.services.length_stability_gate import (
    CHINESE_CHAPTER_HARD_MAX_WORDS,
    CHINESE_CHAPTER_HARD_MIN_WORDS,
    evaluate_length_control_candidate,
    resolve_chapter_word_window,
)
from bestseller.services.word_targets import (
    LENGTH_CONTROL_COUNT_UNIT_V1,
    LENGTH_CONTROL_V1,
    chapter_length_control_band,
    plan_remaining_word_budget,
    project_word_target_policy,
)
from bestseller.services.writing_profile import is_english_language
from bestseller.settings import AppSettings

__all__ = [
    "LengthControlError",
    "evaluate_candidate",
    "freeze_request",
    "prompt_contract",
    "refresh_after_adoption",
    "require_feasible_request",
    "revalidate_request",
]

REQUEST_SCHEMA = "book_length_request_v1"
_AUTHORITY = dict.fromkeys(("grade", "adoption", "credit", "dispatch"), False)
_Model = TypeVar("_Model", ProjectModel, ChapterModel, ChapterDraftVersionModel)
_PLAN_SOURCE_KEYS = (
    "outline_source_admission",
    "outline_prefix_readiness",
    "fanqie_outline_source_selection",
)


class LengthControlError(ValueError):
    """Fail closed without turning unavailable/stale evidence into legacy."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _json(value: object) -> str:
    try:
        return json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
        )
    except (TypeError, ValueError) as error:
        raise LengthControlError("LENGTH_CONTROL_RECEIPT_INVALID") from error


def _seal(payload: dict[str, Any]) -> dict[str, Any]:
    detached = deepcopy({**payload, **_AUTHORITY})
    detached["digest"] = _hash(_json(detached))
    return detached


def _validate_receipt(receipt: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(receipt, Mapping):
        raise LengthControlError("LENGTH_CONTROL_RECEIPT_INVALID")
    value = deepcopy(dict(receipt))
    if (
        value.get("schema") != REQUEST_SCHEMA
        or value.get("protocol") != LENGTH_CONTROL_V1
        or value.get("counting") != LENGTH_CONTROL_COUNT_UNIT_V1
    ):
        raise LengthControlError("LENGTH_CONTROL_PROTOCOL_INVALID")
    digest = value.pop("digest", None)
    if any(value.get(key) is not False for key in _AUTHORITY) or digest != _hash(_json(value)):
        raise LengthControlError("LENGTH_CONTROL_RECEIPT_DIGEST_MISMATCH")
    value["digest"] = digest
    try:
        if value["stage"] not in {"draft", "rewrite", "deslop"}:
            raise KeyError("stage")
        if not isinstance(value["source"]["plan"], list):
            raise KeyError("plan")
        if not isinstance(value["source"]["adopted_prefix"], list):
            raise KeyError("prefix")
        if value["source"]["project"]["id"] != value["project_id"]:
            raise KeyError("project")
        UUID(value["project_id"])
        UUID(value["chapter_id"])
        source = value["source"]
        count = _integer(source["project"]["target_chapters"], "LENGTH_CONTROL_RECEIPT_INVALID")
        total = _integer(source["project"]["target_word_count"], "LENGTH_CONTROL_RECEIPT_INVALID")
        number = _integer(value["chapter_number"], "LENGTH_CONTROL_RECEIPT_INVALID")
        plan, prefix = source["plan"], source["adopted_prefix"]
        if (
            len(plan) != count
            or number > count
            or len(prefix) != number - 1
            or source["plan_sha256"] != _hash(_json(plan))
        ):
            raise KeyError("plan")
        for index, row in enumerate(plan, 1):
            UUID(row["chapter_id"])
            _integer(row["target_word_count"], "LENGTH_CONTROL_RECEIPT_INVALID")
            if row["chapter_number"] != index:
                raise KeyError("chapter_number")
        if sum(row["target_word_count"] for row in plan) != total:
            raise KeyError("total")
        if plan[number - 1]["chapter_id"] != value["chapter_id"]:
            raise KeyError("chapter_id")
        for index, row in enumerate(prefix):
            _validate_identity(row, value["project_id"], plan[index]["chapter_id"])
            if row["chapter_number"] != index + 1:
                raise KeyError("prefix")
        suffix = source.get("adopted_suffix", [])
        if not isinstance(suffix, list) or number + len(suffix) > count:
            raise KeyError("suffix")
        for index, row in enumerate(suffix, number + 1):
            _validate_identity(row, value["project_id"], plan[index - 1]["chapter_id"])
            if row["chapter_number"] != index:
                raise KeyError("suffix")
        if suffix and source["incumbent"] is None:
            raise KeyError("incumbent")
        if source["incumbent"] is not None:
            _validate_identity(source["incumbent"], value["project_id"], value["chapter_id"])
            if source["incumbent"]["chapter_number"] != number:
                raise KeyError("incumbent")
        if value["parent"] is not None:
            _validate_identity(value["parent"], value["project_id"], value["chapter_id"])
        elif value["stage"] in {"rewrite", "deslop"}:
            raise KeyError("parent")
        budget = _request_budget(source, number)
        window, feasible, code = _request_window(budget, value["stage"], value["parent"])
        if (
            value["budget"] != budget
            or value["window"] != window
            or value["feasible"] is not feasible
            or value["issue_code"] != code
        ):
            raise KeyError("window")
    except (KeyError, TypeError, ValueError) as error:
        raise LengthControlError("LENGTH_CONTROL_RECEIPT_INVALID") from error
    return value


def _integer(value: object, code: str, minimum: int = 1) -> int:
    if type(value) is not int or value < minimum:
        raise LengthControlError(code)
    return value


def _validate_identity(row: Mapping[str, Any], project_id: str, chapter_id: str) -> None:
    UUID(row["draft_id"])
    if row["project_id"] != project_id or row["chapter_id"] != chapter_id:
        raise LengthControlError("LENGTH_CONTROL_RECEIPT_OWNERSHIP")
    _integer(row["word_count"], "LENGTH_CONTROL_RECEIPT_INVALID")
    _integer(row["version_no"], "LENGTH_CONTROL_RECEIPT_INVALID")
    for key in ("body_sha256", "content_sha256", "export_body_sha256"):
        digest = row[key]
        if (
            not isinstance(digest, str)
            or len(digest) != 64
            or any(char not in "0123456789abcdef" for char in digest)
        ):
            raise LengthControlError("LENGTH_CONTROL_RECEIPT_INVALID")


def _body_identity(content_md: str) -> dict[str, Any]:
    # Use the actual P11/export assembler, including its exact title removal.
    # These transient objects carry text only and are never persisted/adopted.
    from bestseller.services.drafts import count_words
    from bestseller.services.fanqie_short_pipeline import assemble_fanqie_whole_story

    if not isinstance(content_md, str):
        raise LengthControlError("LENGTH_CONTROL_BODY_INVALID")
    body = assemble_fanqie_whole_story(
        ProjectModel(),
        [(ChapterModel(), ChapterDraftVersionModel(content_md=content_md))],
    )
    normalized = content_md.replace("\r\n", "\n").replace("\r", "\n")
    return {
        "body_sha256": _hash(normalized),
        "content_sha256": _hash(content_md),
        "export_body_sha256": _hash(body),
        "word_count": count_words(body),
    }


def _draft_identity(draft: ChapterDraftVersionModel) -> dict[str, Any]:
    return {
        "project_id": str(draft.project_id),
        "chapter_id": str(draft.chapter_id),
        "draft_id": str(draft.id),
        "version_no": draft.version_no,
        **_body_identity(draft.content_md),
    }


async def _read_rows(
    session: AsyncSession,
    model: type[_Model],
    *criteria: ColumnElement[bool],
) -> list[_Model]:
    # Column reads bypass ORM identity caches without overwriting pending caller
    # edits. Caller explicitly flushes intended changes under its own locks.
    columns = [getattr(model, prop.key).label(prop.key) for prop in model.__mapper__.column_attrs]
    rows = (
        await session.execute(select(*columns).where(*criteria).execution_options(autoflush=False))
    ).mappings()
    return [model(**dict(row)) for row in rows]


async def _load(
    session: AsyncSession,
    *,
    project: ProjectModel,
    chapter: ChapterModel,
) -> tuple[ProjectModel, ChapterModel, list[ChapterModel], list[ChapterDraftVersionModel]]:
    projects = await _read_rows(session, ProjectModel, ProjectModel.id == project.id)
    if len(projects) != 1:
        raise LengthControlError("LENGTH_CONTROL_PROJECT_UNKNOWN")
    actual = projects[0]
    chapters = await _read_rows(session, ChapterModel, ChapterModel.project_id == actual.id)
    chapters.sort(key=lambda item: item.chapter_number)
    requested = [item for item in chapters if item.id == chapter.id]
    if len(requested) != 1:
        raise LengthControlError("LENGTH_CONTROL_CHAPTER_OWNERSHIP")
    drafts = await _read_rows(
        session, ChapterDraftVersionModel, ChapterDraftVersionModel.project_id == actual.id
    )
    return actual, requested[0], chapters, drafts


def _stored_requests(drafts: list[ChapterDraftVersionModel]) -> list[dict[str, Any]]:
    requests = []
    for draft in drafts:
        metadata = draft.promotion_metadata or {}
        if "book_length_control" in metadata:
            request = _validate_receipt(metadata["book_length_control"])
            if request["project_id"] != str(draft.project_id) or request["chapter_id"] != str(
                draft.chapter_id
            ):
                raise LengthControlError("LENGTH_CONTROL_RECEIPT_OWNERSHIP")
            requests.append(request)
    return requests


def _enabled(project: ProjectModel, *, retained: bool) -> bool:
    metadata = project.metadata_json or {}
    if "book_length_control" not in metadata:
        return retained
    flag = metadata["book_length_control"]
    if (
        not isinstance(flag, Mapping)
        or flag.get("protocol") != LENGTH_CONTROL_V1
        or flag.get("counting") != LENGTH_CONTROL_COUNT_UNIT_V1
    ):
        raise LengthControlError("LENGTH_CONTROL_PROTOCOL_INVALID")
    return True


def _plan(project: ProjectModel, chapters: list[ChapterModel]) -> list[dict[str, Any]]:
    total = _integer(project.target_word_count, "LENGTH_CONTROL_PROJECT_TARGET_INVALID")
    count = _integer(project.target_chapters, "LENGTH_CONTROL_PLAN_CENSUS_INVALID")
    if len(chapters) != count or [item.chapter_number for item in chapters] != list(
        range(1, count + 1)
    ):
        raise LengthControlError("LENGTH_CONTROL_PLAN_CENSUS_INVALID")
    rows = []
    for chapter in chapters:
        metadata = chapter.metadata_json or {}
        rows.append(
            {
                "chapter_id": str(chapter.id),
                "chapter_number": chapter.chapter_number,
                "target_word_count": _integer(
                    chapter.target_word_count, "LENGTH_CONTROL_PLAN_INVALID"
                ),
                "title": chapter.title,
                "chapter_goal": chapter.chapter_goal,
                "original_source": {
                    key: deepcopy(metadata[key]) for key in _PLAN_SOURCE_KEYS if key in metadata
                },
            }
        )
    if sum(row["target_word_count"] for row in rows) != total:
        raise LengthControlError("LENGTH_CONTROL_PLAN_TOTAL_MISMATCH")
    return rows


def _constraints(project: ProjectModel, settings: AppSettings) -> dict[str, Any]:
    policy = project_word_target_policy(project, settings)
    minimum, target, maximum, platform = resolve_chapter_word_window(
        project.metadata_json or {},
        fallback_min=policy.chapter_min,
        fallback_target=policy.chapter_target,
        fallback_max=policy.chapter_max,
        audience=project.audience,
    )
    # Keep both the existing project band and a stricter platform band.
    return {
        "legacy_policy": asdict(policy),
        "platform": platform,
        "platform_window": {"min_words": minimum, "target_words": target, "max_words": maximum},
        "hard_min_words": max(CHINESE_CHAPTER_HARD_MIN_WORDS, minimum, policy.chapter_min),
        "hard_max_words": min(CHINESE_CHAPTER_HARD_MAX_WORDS, maximum, policy.chapter_max),
        "invariants_json": deepcopy(project.invariants_json),
    }


async def _sources(
    session: AsyncSession,
    chapters: list[ChapterModel],
    drafts: list[ChapterDraftVersionModel],
) -> list[dict[str, Any] | None]:
    result = []
    for chapter in chapters:
        adopted, native_receipt = await select_adopted_chapter_source(
            session,
            chapter=chapter,
            lock=False,
        )
        if adopted is None:
            # A native ambiguity/blocked promotion is not a verified empty slot.
            if (
                any(d.chapter_id == chapter.id and d.promotion_state == "promoted" for d in drafts)
                or (chapter.metadata_json or {}).get("numeric_promotion_debt") is not None
            ):
                raise LengthControlError("LENGTH_CONTROL_ADOPTED_SOURCE_UNKNOWN")
            result.append(None)
        else:
            if adopted.project_id != chapter.project_id or adopted.chapter_id != chapter.id:
                raise LengthControlError("LENGTH_CONTROL_ADOPTED_SOURCE_OWNERSHIP")
            result.append(
                {
                    "chapter_number": chapter.chapter_number,
                    **_draft_identity(adopted),
                    "native_receipt_sha256": _hash(_json(native_receipt))
                    if native_receipt
                    else None,
                }
            )
    gap = False
    for source in result:
        if source is None:
            gap = True
        elif gap:
            raise LengthControlError("LENGTH_CONTROL_ADOPTED_PREFIX_GAP")
    return result


def _budget(source: dict[str, Any], prefix: list[dict[str, Any]]) -> dict[str, Any]:
    constraints = source["constraints"]
    try:
        result = plan_remaining_word_budget(
            total_words=source["project"]["target_word_count"],
            total_chapters=source["project"]["target_chapters"],
            planned_targets=tuple(row["target_word_count"] for row in source["plan"]),
            adopted_word_counts=tuple(row["word_count"] for row in prefix),
            protocol=LENGTH_CONTROL_V1,
            hard_min_words=constraints["hard_min_words"],
            hard_max_words=constraints["hard_max_words"],
        )
    except ValueError as error:
        raise LengthControlError("LENGTH_CONTROL_BUDGET_INPUT_INVALID") from error
    if result is None:
        raise LengthControlError("LENGTH_CONTROL_BUDGET_UNKNOWN")
    return json.loads(_json(asdict(result)))


def _fixed_suffix_budget(
    *,
    total_words: int,
    planned_targets: tuple[int, ...],
    chapter_number: int,
    adopted_word_counts: tuple[int | None, ...],
    hard_min_words: int = 1800,
    hard_max_words: int = 3500,
) -> dict[str, Any]:
    """Numerical replacement branch; no input here grants source authority."""
    total = _integer(total_words, "LENGTH_CONTROL_PROJECT_TARGET_INVALID")
    number = _integer(chapter_number, "LENGTH_CONTROL_PLAN_INVALID")
    if (
        not isinstance(planned_targets, (tuple, list))
        or not planned_targets
        or not isinstance(adopted_word_counts, (tuple, list))
        or len(adopted_word_counts) != len(planned_targets)
        or number > len(planned_targets)
    ):
        raise LengthControlError("LENGTH_CONTROL_PLAN_CENSUS_INVALID")
    originals = tuple(_integer(n, "LENGTH_CONTROL_PLAN_INVALID") for n in planned_targets)
    if sum(originals) != total:
        raise LengthControlError("LENGTH_CONTROL_PLAN_TOTAL_MISMATCH")
    gap = False
    for count in adopted_word_counts:
        if count is None:
            gap = True
        else:
            _integer(count, "LENGTH_CONTROL_ADOPTED_SOURCE_UNKNOWN")
            if gap:
                raise LengthControlError("LENGTH_CONTROL_ADOPTED_PREFIX_GAP")
    index = number - 1
    if adopted_word_counts[index] is None:
        raise LengthControlError("LENGTH_CONTROL_CHAPTER_NOT_ADOPTED")
    fixed = [i for i, count in enumerate(adopted_word_counts) if count is not None and i != index]
    future = [i for i, count in enumerate(adopted_word_counts) if count is None]
    fixed_words = sum(adopted_word_counts[i] for i in fixed)
    result = {
        "mode": "fixed_suffix_replacement",
        "protocol": LENGTH_CONTROL_V1,
        "count_unit": LENGTH_CONTROL_COUNT_UNIT_V1,
        "book_min_words": (total * 85 + 99) // 100,
        "book_max_words": total * 115 // 100,
        "fixed_words": fixed_words,
        "adopted_words": fixed_words,
        "fixed_chapter_numbers": [i + 1 for i in fixed],
        "unadopted_chapter_numbers": [i + 1 for i in future],
        "remaining_chapters": len(future) + 1,
        "feasible": False,
        "exact_target_feasible": False,
        "issue_code": None,
        "targets": [],
        "next_band": None,
        **_AUTHORITY,
    }
    if any(
        not CHINESE_CHAPTER_HARD_MIN_WORDS
        <= adopted_word_counts[i]
        <= CHINESE_CHAPTER_HARD_MAX_WORDS
        for i in fixed
    ):
        return {**result, "issue_code": "ADOPTED_CHAPTER_HARD_BOUNDARY"}
    try:
        bands = {
            i: chapter_length_control_band(
                originals[i],
                protocol=LENGTH_CONTROL_V1,
                hard_min_words=hard_min_words,
                hard_max_words=hard_max_words,
            )
            for i in [index, *future]
        }
    except ValueError as error:
        if str(error) != "target has no legal chapter length band":
            raise LengthControlError("LENGTH_CONTROL_BUDGET_INPUT_INVALID") from error
        return {**result, "issue_code": "BOOK_CHAPTER_BAND_INFEASIBLE"}
    current = bands[index]
    later_min = sum(bands[i].min_words for i in future)
    later_max = sum(bands[i].max_words for i in future)
    lower = max(current.min_words, result["book_min_words"] - fixed_words - later_max)
    upper = min(current.max_words, result["book_max_words"] - fixed_words - later_min)
    if lower > upper:
        return {**result, "issue_code": "BOOK_LENGTH_BUDGET_INFEASIBLE"}
    wanted = total - fixed_words - sum(bands[i].target_words for i in future)
    target = min(max(wanted, lower), upper)
    return {
        **result,
        "feasible": True,
        "exact_target_feasible": current.min_words + later_min
        <= total - fixed_words
        <= current.max_words + later_max,
        "targets": [target],
        "next_band": {"min_words": lower, "target_words": target, "max_words": upper},
    }


def _request_budget(source: dict[str, Any], chapter_number: int) -> dict[str, Any]:
    suffix = source.get("adopted_suffix", [])
    if not suffix:
        return _budget(source, source["adopted_prefix"])
    census = [*source["adopted_prefix"], source["incumbent"], *suffix]
    counts = tuple(row["word_count"] for row in census)
    return _fixed_suffix_budget(
        total_words=source["project"]["target_word_count"],
        planned_targets=tuple(row["target_word_count"] for row in source["plan"]),
        chapter_number=chapter_number,
        adopted_word_counts=counts + (None,) * (len(source["plan"]) - len(counts)),
        hard_min_words=source["constraints"]["hard_min_words"],
        hard_max_words=source["constraints"]["hard_max_words"],
    )


def _request_window(
    budget: dict[str, Any],
    stage: str,
    parent: dict[str, Any] | None,
) -> tuple[dict[str, int] | None, bool, str | None]:
    window = deepcopy(budget["next_band"])
    if not budget["feasible"] or window is None:
        return None, False, budget["issue_code"]
    if stage == "deslop":
        if parent is None:
            raise LengthControlError("LENGTH_CONTROL_PARENT_REQUIRED")
        window["max_words"] = min(window["max_words"], parent["word_count"] * 105 // 100)
        if window["max_words"] < window["min_words"]:
            return None, False, "LENGTH_CONTROL_DESLOP_BAND_INFEASIBLE"
        window["target_words"] = min(window["target_words"], window["max_words"])
    return window, True, None


async def _snapshot(
    session: AsyncSession,
    settings: AppSettings,
    *,
    project: ProjectModel,
    chapter: ChapterModel,
    retained: dict[str, Any] | None,
) -> tuple[dict[str, Any], ChapterModel, list[ChapterDraftVersionModel]] | None:
    actual, chapter, chapters, drafts = await _load(session, project=project, chapter=chapter)
    stored = _stored_requests(drafts)
    retained_v1 = retained is not None or bool(stored)
    if not _enabled(actual, retained=retained_v1):
        return None
    if is_english_language(actual.language):
        if retained_v1:
            raise LengthControlError("LENGTH_CONTROL_LANGUAGE_CHANGED")
        return None
    plan = _plan(actual, chapters)
    project_source = {
        "id": str(actual.id),
        "target_word_count": actual.target_word_count,
        "target_chapters": actual.target_chapters,
        "language": actual.language,
        "audience": actual.audience,
    }
    # Once a real candidate carries a frozen plan, changing the rows cannot
    # silently establish a new baseline for subsequent v1 requests.
    for prior in stored:
        if prior["source"]["plan"] != plan or prior["source"]["project"] != project_source:
            raise LengthControlError("LENGTH_CONTROL_ORIGINAL_PLAN_CHANGED")
    sources = await _sources(session, chapters, drafts)
    return (
        {
            "project": project_source,
            "plan": plan,
            "plan_sha256": _hash(_json(plan)),
            "constraints": _constraints(actual, settings),
            "all_adopted": sources,
        },
        chapter,
        drafts,
    )


async def _parent(
    session: AsyncSession,
    *,
    project_id: UUID,
    chapter_id: UUID,
    parent_draft: ChapterDraftVersionModel | None,
    retained: dict[str, Any] | None,
    stage: str,
) -> dict[str, Any] | None:
    previous = retained.get("parent") if retained else None
    parent_id = parent_draft.id if parent_draft is not None else None
    if parent_id is None and previous is not None:
        try:
            parent_id = UUID(previous["draft_id"])
        except (KeyError, TypeError, ValueError) as error:
            raise LengthControlError("LENGTH_CONTROL_PARENT_INVALID") from error
    if parent_id is None:
        if stage in {"rewrite", "deslop"}:
            raise LengthControlError("LENGTH_CONTROL_PARENT_REQUIRED")
        return None
    rows = await _read_rows(
        session,
        ChapterDraftVersionModel,
        ChapterDraftVersionModel.id == parent_id,
        ChapterDraftVersionModel.project_id == project_id,
        ChapterDraftVersionModel.chapter_id == chapter_id,
    )
    if len(rows) != 1:
        raise LengthControlError("LENGTH_CONTROL_PARENT_OWNERSHIP")
    result = _draft_identity(rows[0])
    if result["word_count"] < 1:
        raise LengthControlError("LENGTH_CONTROL_PARENT_EMPTY")
    return result


async def freeze_request(
    session: AsyncSession,
    settings: AppSettings,
    *,
    project: ProjectModel,
    chapter: ChapterModel,
    stage: str = "draft",
    parent_draft: ChapterDraftVersionModel | None = None,
    receipt: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Freeze actual sources before generation; None means legacy, never pass.

    Receipt reuse must match the *entire* newly read contract, or raises a
    LengthControlError. A stale contract is never silently migrated. An adopted
    suffix remains fixed and source-bound during replacement.
    """
    retained = _validate_receipt(receipt) if receipt is not None else None
    if stage not in {"draft", "rewrite", "deslop"}:
        raise LengthControlError("LENGTH_CONTROL_STAGE_INVALID")
    with session.no_autoflush:
        snapshot = await _snapshot(
            session, settings, project=project, chapter=chapter, retained=retained
        )
        if snapshot is None:
            return None
        source, actual_chapter, _drafts = snapshot
        sources = source.pop("all_adopted")
        index = actual_chapter.chapter_number - 1
        if any(item is None for item in sources[:index]):
            raise LengthControlError("LENGTH_CONTROL_REQUEST_NOT_NEXT")
        source["adopted_prefix"] = sources[:index]
        source["incumbent"] = sources[index]
        suffix = [item for item in sources[index + 1 :] if item is not None]
        if suffix:
            source["adopted_suffix"] = suffix
        parent = await _parent(
            session,
            project_id=actual_chapter.project_id,
            chapter_id=actual_chapter.id,
            parent_draft=parent_draft,
            retained=retained,
            stage=stage,
        )
        budget = _request_budget(source, actual_chapter.chapter_number)
        window, feasible, code = _request_window(budget, stage, parent)
        result = _seal(
            {
                "schema": REQUEST_SCHEMA,
                "protocol": LENGTH_CONTROL_V1,
                "counting": LENGTH_CONTROL_COUNT_UNIT_V1,
                "project_id": source["project"]["id"],
                "chapter_id": str(actual_chapter.id),
                "chapter_number": actual_chapter.chapter_number,
                "stage": stage,
                "source": source,
                "parent": parent,
                "budget": budget,
                "window": window,
                "feasible": feasible,
                "issue_code": code,
            }
        )
    if retained is not None and result != retained:
        raise LengthControlError("LENGTH_CONTROL_REQUEST_STALE")
    return result


def require_feasible_request(receipt: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """Reject impossible/stale-shaped v1 inputs before spending or selecting."""
    if receipt is None:
        return None
    request = _validate_receipt(receipt)
    if not request["feasible"] or request["window"] is None:
        raise LengthControlError(request["issue_code"] or "BOOK_LENGTH_BUDGET_INFEASIBLE")
    return request


def prompt_contract(receipt: Mapping[str, Any] | None) -> str:
    """Render the same frozen effective band for writer/editor/deslop prompts."""
    request = require_feasible_request(receipt)
    if request is None:
        return ""
    band = request["window"]
    return (
        f"【冻结正文篇幅合同】{request['protocol']}。计量 {request['counting']}。"
        f"去章标题、按正式导出清理后的正文必须为 {band['min_words']}-{band['max_words']} 字。"
        f"目标约 {band['target_words']} 字。不得新增无关情节凑字。完成本章任务即停止。"
        f"此区间覆盖其他篇幅建议。request_sha256={request['digest']}"
    )


def evaluate_candidate(receipt: Mapping[str, Any], content_md: str) -> dict[str, Any]:
    """Preview one actual body, never append a candidate to durable facts.

    ``length_passed`` is length-only. Main must preserve independent legacy
    counting/quality gates, revalidate sources and use native adoption authority.
    """
    request = _validate_receipt(receipt)
    candidate = _body_identity(content_md)
    window = request["window"]
    decision = None
    preview = None
    code = request["issue_code"]
    if request["feasible"] and window is not None:
        parent = request["parent"]
        try:
            verdict = evaluate_length_control_candidate(
                word_count=candidate["word_count"],
                **request["budget"]["next_band"],
                protocol=LENGTH_CONTROL_V1,
                stage=request["stage"],
                parent_word_count=parent["word_count"] if parent else None,
            )
        except ValueError as error:
            raise LengthControlError("LENGTH_CONTROL_RECEIPT_INVALID") from error
        if verdict is None:
            raise LengthControlError("LENGTH_CONTROL_DECISION_UNKNOWN")
        decision = asdict(verdict)
        code = verdict.issue_code
        if verdict.accepted:
            preview = _budget(
                request["source"],
                [
                    *request["source"]["adopted_prefix"],
                    candidate,
                    *request["source"].get("adopted_suffix", []),
                ],
            )
            if not preview["feasible"]:
                code = preview["issue_code"]
    passed = decision is not None and decision["accepted"] and code is None
    return _seal(
        {
            "schema": "book_length_candidate_v1",
            "protocol": LENGTH_CONTROL_V1,
            "counting": LENGTH_CONTROL_COUNT_UNIT_V1,
            "request_digest": request["digest"],
            "project_id": request["project_id"],
            "chapter_id": request["chapter_id"],
            "candidate": candidate,
            "decision": decision,
            "preview_budget": preview,
            "length_passed": passed,
            "issue_code": code,
        }
    )


async def revalidate_request(
    session: AsyncSession,
    settings: AppSettings,
    *,
    project: ProjectModel,
    chapter: ChapterModel,
    receipt: Mapping[str, Any],
    parent_draft: ChapterDraftVersionModel | None = None,
) -> dict[str, Any]:
    """Rebuild and compare actual sources/old fields; caller supplies CAS/locks.

    ``valid`` only means the request is still the same; it does not mean its
    budget is feasible or that the candidate passes. Operational SQL exceptions
    propagate rather than being mistaken for a proven stale request.
    """
    rebuilt = None
    try:
        retained = _validate_receipt(receipt)
        rebuilt = await freeze_request(
            session,
            settings,
            project=project,
            chapter=chapter,
            stage=retained["stage"],
            parent_draft=parent_draft,
            receipt=retained,
        )
        valid = rebuilt is not None
        code = None if valid else "LENGTH_CONTROL_REQUEST_STALE"
    except LengthControlError as error:
        valid, code = False, error.code
    return _seal(
        {
            "schema": "book_length_revalidation_v1",
            "protocol": LENGTH_CONTROL_V1,
            "counting": LENGTH_CONTROL_COUNT_UNIT_V1,
            "valid": valid,
            "issue_code": code,
            "request_digest": rebuilt["digest"] if rebuilt else None,
        }
    )


async def refresh_after_adoption(
    session: AsyncSession,
    settings: AppSettings,
    *,
    project: ProjectModel,
    chapter: ChapterModel,
    receipt: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Recount only native adoption, returning remaining budget without writes.

    This is a readback, not an adoption action. Main persists any returned
    summary itself. Middle replacements keep the exact frozen suffix and count
    the complete native adopted prefix once after adoption.
    """
    retained = _validate_receipt(receipt) if receipt is not None else None
    with session.no_autoflush:
        snapshot = await _snapshot(
            session, settings, project=project, chapter=chapter, retained=retained
        )
        if snapshot is None:
            return None
        source, actual_chapter, _drafts = snapshot
        sources = source.pop("all_adopted")
        index = actual_chapter.chapter_number - 1
        if sources[index] is None:
            raise LengthControlError("LENGTH_CONTROL_CHAPTER_NOT_ADOPTED")
        prefix = [item for item in sources if item is not None]
        suffix = [item for item in sources[index + 1 :] if item is not None]
        if retained is not None:
            if (
                retained["project_id"] != str(actual_chapter.project_id)
                or retained["chapter_id"] != str(actual_chapter.id)
                or retained["source"]["project"] != source["project"]
                or retained["source"]["plan"] != source["plan"]
                or retained["source"]["constraints"] != source["constraints"]
                or retained["source"]["adopted_prefix"] != sources[:index]
                or retained["source"].get("adopted_suffix", []) != suffix
            ):
                raise LengthControlError("LENGTH_CONTROL_REQUEST_STALE")
        source["adopted_prefix"] = prefix
        if suffix:
            source["adopted_suffix"] = suffix
        budget = _budget(source, prefix)
        next_index = len(prefix)
        return _seal(
            {
                "schema": "book_length_refresh_v1",
                "protocol": LENGTH_CONTROL_V1,
                "counting": LENGTH_CONTROL_COUNT_UNIT_V1,
                "source": source,
                "chapter_id": str(actual_chapter.id),
                "adopted": sources[index],
                "budget": budget,
                "window": budget["next_band"],
                "feasible": budget["feasible"],
                "issue_code": budget["issue_code"],
                "next_chapter_id": (
                    source["plan"][next_index]["chapter_id"]
                    if next_index < len(source["plan"])
                    else None
                ),
            }
        )
