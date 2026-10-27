"""Primary tool protocol through the real review entrypoint and SQLite rows.

Provider responses are controlled fixtures. No provider, execution-credit, or
literary quality acceptance is claimed by these structural protocol tests.
"""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.schema import CreateTable
from test_chapter_review_commercial_checkpoint import review_db  # noqa: F401

from bestseller.domain.review import ChapterReviewResult, ChapterReviewScores
from bestseller.infra.db.models import LlmRunModel, ReviewReportModel, RewriteTaskModel
from bestseller.services import review_source_context, reviews

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@pytest.fixture
async def primary_db(review_db, monkeypatch):  # noqa: F811 - imported pytest fixture
    db = review_db
    db.settings.quality.enable_llm_chapter_commentary = True
    db.settings.pipeline.enable_chapter_llm_commercial_judge = False
    db.draft.content_md = (
        "她核对名册后归还钥匙。\n\n管理员把归还时间记入登记簿。\n\n她带着盖章的凭条离开档案室。"
    )
    await db.session.commit()
    async with db.session.bind.begin() as connection:
        ddl = str(CreateTable(LlmRunModel.__table__).compile(dialect=connection.dialect))
        await connection.exec_driver_sql(ddl.replace("::jsonb", ""))
    scores = ChapterReviewScores(**dict.fromkeys(ChapterReviewScores.model_fields, 0.9))
    monkeypatch.setattr(
        reviews,
        "evaluate_chapter_draft",
        lambda **kwargs: ChapterReviewResult(
            verdict="pass",
            severity_max="major",
            scores=scores,
            rewrite_instructions="Deterministic fixture direction.",
        ),
    )

    async def source_context(*args, **kwargs):
        return {"schema": "isolated-review-source", "source": "fixture"}

    monkeypatch.setattr(review_source_context, "load_review_source_context", source_context)
    return db


async def _run(db):
    return await reviews.review_chapter_draft(db.session, db.settings, db.project.slug, 2)


def _tool_completion(*, raw_message_present=True, reuse_paragraph=False):
    payload = {
        "verdict": "rewrite",
        "scores": dict.fromkeys(ChapterReviewScores.model_fields, 0.9),
        "rewrite_direction": "Show the visible consequence of the recorded return.",
        "commentary": "The key return is explicit; clarify its immediate consequence.",
        "citations": [
            {
                "paragraph_number": 1,
                "dimension": "continuity",
                "reason": "The selected current paragraph explicitly completes the key return.",
            }
        ],
    }
    if reuse_paragraph:
        payload["citations"].append({
            "paragraph_number": 1,
            "dimension": "continuity",
            "reason": "The same paragraph also identifies the person returning the key.",
        })
    # Preserve physical whitespace/key ordering; the report must not replace
    # this with a regenerated argument string or canonical commentary.
    arguments = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    calls = [
        {
            "id": "recorded-primary-submission",
            "type": "function",
            "function": {
                "name": "submit_chapter_critique",
                "arguments": arguments,
            },
        }
    ]
    content = "Auxiliary provider text, not the canonical grade."
    return SimpleNamespace(
        content=content,
        provider="fixture-recorded-provider",
        finish_reason="tool_calls",
        fallback_used=False,
        model_name="fixture-reviewer",
        llm_run_id=None,
        tool_calls=calls,
        raw_message={"role": "assistant", "content": content, "tool_calls": deepcopy(calls)}
        if raw_message_present
        else None,
    )


def _objects(value):
    """Find retained structures without depending on a report-key spelling."""
    if isinstance(value, dict):
        yield value
        for item in value.values():
            yield from _objects(item)
    elif isinstance(value, list):
        for item in value:
            yield from _objects(item)


@pytest.mark.parametrize("raw_message_present", [False, True])
@pytest.mark.parametrize("reuse_paragraph", [False, True])
async def test_real_entry_uses_current_numeric_tools_and_retains_full_physical_response(
    primary_db,
    monkeypatch,
    raw_message_present,
    reuse_paragraph,
):
    from bestseller.services.primary_review_submission import (
        parse_primary_review_submission,
        primary_review_submission_tools,
    )

    db = primary_db
    completion = _tool_completion(raw_message_present=raw_message_present, reuse_paragraph=reuse_paragraph)
    physical = deepcopy(vars(completion))
    calls = []
    run_id = None

    async def retained_provider(session, settings, request):
        nonlocal run_id
        calls.append(request)
        assert request.prompt_template == "chapter_review"
        assert request.prompt_version.startswith("7.0")
        assert request.tool_choice == "required"
        assert request.tools == primary_review_submission_tools(db.draft.content_md)
        assert request.project_id == db.project.id
        assert request.metadata["review_source_receipt"]["source_complete"] is True
        assert (
            request.metadata["primary_review_submission_policy"] == "primary-review-submission.v2"
        )
        assert request.metadata["primary_review_body_sha256"] == hashlib.sha256(
            db.draft.content_md.encode()
        ).hexdigest()
        parameters = request.tools[0]["function"]["parameters"]
        selectors = parameters["properties"]["citations"]["items"]["properties"]
        assert selectors["paragraph_number"]["enum"] == [1, 2, 3]
        assert selectors["paragraph_number"]["type"] == "integer"
        assert set(parameters["properties"]["scores"]["required"]) == set(
            ChapterReviewScores.model_fields
        )
        run = LlmRunModel(
            project_id=db.project.id,
            logical_role="critic",
            provider=completion.provider,
            model_name=completion.model_name,
            prompt_template=request.prompt_template,
            prompt_version=request.prompt_version,
            finish_reason=completion.finish_reason,
            metadata_json={"fixture_physical_completion": physical},
        )
        session.add(run)
        await session.flush()
        run_id = run.id
        completion.llm_run_id = run.id
        return completion

    monkeypatch.setattr(reviews, "complete_text", retained_provider)
    body = db.draft.content_md
    canonical, receipt, errors = parse_primary_review_submission(completion, body)
    assert errors == ()
    # The legacy generic real-completion check must not gain tool-finish
    # acceptance for unrelated scene/window/free-text callers.
    assert reviews._is_real_chapter_review_completion(completion) is False
    result, report, quality, task = await _run(db)
    await db.session.commit()
    assert len(calls) == 1 and result.verdict == "rewrite" and task is not None
    async with db.factory() as reader:
        stored = await reader.get(ReviewReportModel, report.id)
        stored_task = await reader.get(RewriteTaskModel, task.id)
        run = await reader.get(LlmRunModel, run_id)
        data = stored.structured_output
        assert stored.llm_run_id == run_id
        assert data["critic_evidence"]["passed"] is True
        assert data["critic_evidence"]["status"] == "verified"
        assert data["critic_response"] == canonical
        assert data["primary_submission_receipt"] == receipt
        assert data["primary_submission_original"] == {
            "content": physical["content"],
            "tool_calls": physical["tool_calls"],
            "raw_message": physical["raw_message"],
            "finish_reason": physical["finish_reason"],
            "llm_run_id": str(run_id),
        }
        objects = list(_objects(data))
        assert receipt in objects
        assert any(
            item.get("content") == physical["content"]
            and item.get("tool_calls") == physical["tool_calls"]
            and "raw_message" in item
            and item["raw_message"] == physical["raw_message"]
            for item in objects
        )
        assert run.metadata_json["fixture_physical_completion"] == physical
        assert run.finish_reason == "tool_calls"
        assert quality.review_report_id == report.id
        assert "visible consequence" in stored_task.instructions
        selected = data["critic_evidence"]["citations"][0]
        assert selected["body_sha256"] == hashlib.sha256(body.encode()).hexdigest()
        assert len(data["critic_evidence"]["citations"]) == (2 if reuse_paragraph else 1)
        if reuse_paragraph:
            other = data["critic_evidence"]["citations"][1]
            assert other["id"] == selected["id"] and other["text"] == selected["text"]
            assert other["reason"] != selected["reason"]
            assert receipt["source_coverage"]["distinct_anchor_count"] == 1
    assert db.draft.content_md == body


@pytest.mark.parametrize(
    "failure",
    [
        "mock",
        "fallback",
        "fallback_flag",
        "length",
        "content_filter",
        "missing_tool",
        "paragraph_999",
        "raw_message_conflict",
    ],
)
async def test_entry_rejects_nonprovider_or_invalid_tool_without_rewrite_authority(
    primary_db,
    monkeypatch,
    failure,
):
    db = primary_db
    completion = _tool_completion()
    if failure in {"mock", "fallback"}:
        completion.provider = failure + "-fixture"
    elif failure == "fallback_flag":
        completion.fallback_used = True
    elif failure in {"length", "content_filter"}:
        completion.finish_reason = failure
    elif failure == "missing_tool":
        completion.tool_calls = None
        completion.raw_message = None
    elif failure == "paragraph_999":
        payload = json.loads(completion.tool_calls[0]["function"]["arguments"])
        payload["citations"][0]["paragraph_number"] = 999
        completion.tool_calls[0]["function"]["arguments"] = json.dumps(payload)
        completion.raw_message["tool_calls"] = deepcopy(completion.tool_calls)
    else:
        completion.raw_message["tool_calls"][0]["function"]["arguments"] = "{}"
    physical = deepcopy(vars(completion))
    calls = []

    async def response(*args, **kwargs):
        calls.append(True)
        return completion

    monkeypatch.setattr(reviews, "complete_text", response)
    result, report, _, task = await _run(db)
    await db.session.commit()
    assert calls == [True] and task is None and result.verdict == "unknown"
    async with db.factory() as reader:
        stored = await reader.get(ReviewReportModel, report.id)
        assert stored.structured_output["critic_evidence"]["passed"] is False
        assert stored.structured_output["primary_submission_original"] == {
            "content": physical["content"],
            "tool_calls": physical["tool_calls"],
            "raw_message": physical["raw_message"],
            "finish_reason": physical["finish_reason"],
            "llm_run_id": None,
        }
        assert list(await reader.scalars(select(RewriteTaskModel))) == []
        objects = list(_objects(stored.structured_output))
        assert any(
            item.get("tool_calls") == physical["tool_calls"]
            and item.get("content") == physical["content"]
            for item in objects
        )
    assert vars(completion) == physical


async def test_required_source_failure_stops_before_provider_and_authorizes_no_rewrite(
    primary_db,
    monkeypatch,
):
    db = primary_db
    calls = []

    async def unavailable_source(*args, **kwargs):
        raise RuntimeError("source_context_unavailable")

    async def forbidden_provider(*args, **kwargs):
        calls.append(True)
        pytest.fail("required-source failure must stop before the provider boundary")

    monkeypatch.setattr(review_source_context, "load_review_source_context", unavailable_source)
    monkeypatch.setattr(reviews, "complete_text", forbidden_provider)
    body = db.draft.content_md
    result, report, _, task = await _run(db)
    await db.session.commit()
    assert calls == [] and task is None and result.verdict == "unknown"
    async with db.factory() as reader:
        stored = await reader.get(ReviewReportModel, report.id)
        assert stored.structured_output["critic_evidence"]["passed"] is False
        assert list(await reader.scalars(select(RewriteTaskModel))) == []
        assert list(await reader.scalars(select(LlmRunModel))) == []
    assert db.draft.content_md == body
