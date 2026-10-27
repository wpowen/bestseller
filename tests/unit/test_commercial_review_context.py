from __future__ import annotations

# ruff: noqa: RUF001
import ast
from copy import deepcopy
import hashlib
import inspect
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from test_adopted_knowledge_lineage import db as db

from bestseller.domain.llm_quality_judge import LLMQualityJudgeResult
from bestseller.infra.db.models import ChapterDraftVersionModel, ChapterModel, ProjectModel
from bestseller.services import chapter_llm_quality_judge as judge
from bestseller.services import reviews
from bestseller.services.commercial_review_context import (
    load_commercial_review_history,
    project_commercial_source,
)
from bestseller.services.commercial_source_evidence import (
    PROMPT_VERSION,
    assemble_commercial_sources,
    resolve_commercial_source_pointer,
)
from bestseller.services.llm import LLMCompletionResult
from bestseller.settings import load_settings


@pytest.mark.asyncio
async def test_adopted_prior_body_excludes_candidates_current_future_and_other_projects(db):
    session, project, first, accepted = db
    accepted.is_current = False
    session.add(
        ChapterDraftVersionModel(
            project_id=project.id,
            chapter_id=first.id,
            version_no=2,
            content_md="未采用版本",
            promotion_state="candidate",
            is_current=True,
        )
    )
    for number in (2, 3):
        chapter = ChapterModel(project_id=project.id, chapter_number=number, chapter_goal="test")
        session.add(chapter)
        await session.flush()
        session.add(
            ChapterDraftVersionModel(
                project_id=project.id,
                chapter_id=chapter.id,
                version_no=1,
                content_md=f"禁止进入前文的第{number}章",
                promotion_state="candidate",
                is_current=True,
            )
        )
    other = ProjectModel(
        slug=uuid4().hex, title="other", genre="test", target_word_count=1000, target_chapters=3
    )
    session.add(other)
    await session.flush()
    session.add(ChapterModel(project_id=other.id, chapter_number=1, chapter_goal="other project"))
    await session.commit()
    before = accepted.content_md.encode()
    history = await load_commercial_review_history(session, project_id=project.id, chapter_number=2)
    assert [item["chapter_number"] for item in history["adopted_sources"]] == [1]
    prior = history["recent_adopted_prose"]
    assert len(prior) == 1 and prior[0]["draft_id"] == str(accepted.id)
    assert prior[0]["body"].encode() == before
    assert prior[0]["body_sha256"] == hashlib.sha256(before).hexdigest()
    assert not session.dirty


def _commercial_consumer():
    """Execute the real bounded reviews consumer, without unrelated review stages."""
    function = ast.parse(inspect.getsource(reviews.review_chapter_draft)).body[0]
    commercial_if = next(
        node
        for node in function.body
        if isinstance(node, ast.If)
        and "enable_chapter_llm_commercial_judge" in ast.unparse(node.test)
    )
    statements = next(node for node in commercial_if.body if isinstance(node, ast.Try)).body
    stop = next(
        index
        for index, node in enumerate(statements)
        if isinstance(node, ast.Assign)
        and any(
            isinstance(t, ast.Name) and t.id == "original_commercial_payload" for t in node.targets
        )
    )
    names = "session settings project chapter scenes chapter_context draft workflow_run_id evidence_retry_context".split()
    wrapper = ast.AsyncFunctionDef(
        name="consume",
        args=ast.arguments(
            posonlyargs=[],
            args=[ast.arg(arg=name) for name in names],
            kwonlyargs=[],
            kw_defaults=[],
            defaults=[ast.Constant(value=None)],
        ),
        body=[
            *deepcopy(statements[:stop]),
            ast.Return(value=ast.Name(id="llm_judge_result", ctx=ast.Load())),
        ],
        decorator_list=[],
    )
    module = ast.fix_missing_locations(ast.Module(body=[wrapper], type_ignores=[]))
    namespace = dict(reviews.__dict__)
    exec(compile(module, "<actual commercial review consumer>", "exec"), namespace)
    return namespace["consume"]


@pytest.mark.asyncio
@pytest.mark.parametrize("missing_history", [False, True])
@pytest.mark.parametrize("recovery_attempt", [False, True])
async def test_actual_reviews_consumer_supplies_adopted_prior_or_stops_before_judge(
    db, monkeypatch, missing_history, recovery_attempt
):
    session, project, _first, accepted = db
    if missing_history:
        accepted.promotion_state = "candidate"
        await session.commit()
    from bestseller.services import chapter_generation_input_builder as builder

    monkeypatch.setattr(
        builder,
        "build_chapter_generation_input_bundle",
        lambda **kw: SimpleNamespace(
            model_dump=lambda **kw: {"schema_version": "chapter-generation-input.v2"}
        ),
    )
    calls = []
    retry = {"attempt": 2, "prior_report_id": str(uuid4())} if recovery_attempt else None
    recovery = {"attempt": 2, "slots": {"test-slot": "bound previous diagnostic"}}
    if recovery_attempt:
        from bestseller.services import commercial_judge_recovery

        async def load_retry(*args, **kwargs):
            assert kwargs["retry_context"] is retry
            assert kwargs["content_md"] == "本章候选未修改"
            return recovery

        monkeypatch.setattr(commercial_judge_recovery, "load_commercial_retry_context", load_retry)

    async def capture(*args, **kwargs):
        calls.append(kwargs)
        return LLMQualityJudgeResult(passed=True, overall_score=0.95)

    async def no_orm_refresh_for_bounded_facade(*args, **kwargs):
        # This bounded consumer uses a SimpleNamespace chapter. ORM metadata
        # refresh is outside the history-selection behavior under test.
        return None

    monkeypatch.setattr(reviews, "_refresh_independent_review_metadata", no_orm_refresh_for_bounded_facade)

    monkeypatch.setattr(judge, "judge_chapter_commercial_quality_stable", capture)
    result = await _commercial_consumer()(
        session,
        load_settings(env={}),
        project,
        SimpleNamespace(id=uuid4(), chapter_number=2, target_word_count=2000),
        [],
        object(),
        SimpleNamespace(id=uuid4(), content_md="本章候选未修改", word_count=2000),
        None,
        retry,
    )
    if missing_history:
        assert not calls and result.infrastructure_failure and result.rewrite_plan.scope == "judge"
    else:
        assert len(calls) == 1
        assert calls[0]["previous_chapters"][0]["body"] == accepted.content_md
        assert calls[0]["generation_input"]["adopted_continuity"]["before_chapter"] == 2
        assert calls[0]["content_md"] == "本章候选未修改"
        assert calls[0]["commercial_retry_context"] == (recovery if recovery_attempt else None)


@pytest.mark.asyncio
async def test_semantic_projection_and_output_reserve_admit_large_plans_without_clipping(
    monkeypatch,
):
    bible = {
        "book_spec": {"planning": "计划" * 23000},
        "cast_spec": {"future": "规划" * 15000},
        "world_rules": [{"name": "潮位规则", "description": "每次满潮封港。"}],
        "participants": [
            {
                "name": "海员",
                "power_tier": "见习",
                "latest_state": "左臂受伤",
                "life_history": "计划" * 15000,
            }
        ],
        "relationships": [{"tension_summary": "海员欠船主一次救命之恩。"}],
        "deceased_characters": [{"name": "前任船主", "death_chapter_number": 1}],
        "protected_characters": [{"name": "船长", "death_chapter_number": 20}],
        "restricted_characters": [{"name": "守塔人", "kind": "missing", "since_chapter": 1}],
    }
    source = {
        "schema_version": "chapter-generation-input.v2",
        "story_bible": bible,
        "scenes": [{"participants": ["海员"]}],
        "acceptance_contract": {
            "source_authority_channels": {
                "established_facts": {"facts": [{"text": "钥匙已经交还。"}]}
            }
        },
    }
    prior_body, body = (
        ("上章海员归还钥匙。" * 10 + "\n\n") * 22,
        ("海员举起受伤的左臂。" * 10 + "\n\n") * 15,
    )
    previous = [
        {
            "chapter_number": 1,
            "body": prior_body,
            "body_sha256": hashlib.sha256(prior_body.encode()).hexdigest(),
        }
    ]
    captured = []

    async def capture(session, settings, request):
        captured.append(request)
        return LLMCompletionResult(
            content=json.dumps(
                {
                    "wire_version": "commercial-review-wire.v1",
                    "rewrite_plan": {},
                    "pass": True,
                    "overall_score": 0.95,
                    "dimension_scores": {"readability": 0.95},
                    "blocking_issues": [],
                    "audit_issues": [],
                }
            ),
            provider="mock",
            model_name="mock",
        )

    monkeypatch.setattr(judge, "complete_text", capture)
    settings = load_settings(env={})
    settings.llm.critic.max_tokens = 32768
    await judge.judge_chapter_commercial_quality(
        object(),
        settings,
        chapter_number=2,
        content_md=body,
        generation_input=source,
        previous_chapters=previous,
    )
    assert len(captured) == 1
    request = captured[0]
    receipt = request.metadata["commercial_source_input_receipt"]
    assert request.prompt_version == PROMPT_VERSION and request.max_tokens_override == 8192
    assert receipt["total_token_budget"] == 65536
    assert (
        receipt["reserved_input_tokens"] + receipt["output_reserve_tokens"]
        <= receipt["total_token_budget"]
    )
    assert receipt["previous"][0]["body_sha256"] == hashlib.sha256(prior_body.encode()).hexdigest()
    payload = json.loads(
        request.user_prompt.split(
            "完整来源包（JSON 数据，含本章全文、已提供前章和类型化引用目录）：\n"
        )[1]
    )
    assert payload["current_body"].encode() == body.encode()
    assert payload["previous_chapters"][0]["body"].encode() == prior_body.encode()
    projected = payload["source_context"]["story_bible"]
    for key in (
        "world_rules",
        "relationships",
        "deceased_characters",
        "protected_characters",
        "restricted_characters",
    ):
        assert projected[key] == bible[key]
    assert "book_spec" not in projected and "cast_spec" not in projected
    assert projected["participants"][0]["latest_state"] == "左臂受伤"
    assert "life_history" not in projected["participants"][0]
    assert len([item for item in payload["citation_catalog"] if item["kind"] == "observation"]) == 1


@pytest.mark.asyncio
async def test_truncated_structured_review_is_unavailable_even_if_prefix_looks_valid(monkeypatch):
    async def truncated(*args):
        return LLMCompletionResult(
            content='{"pass":true,"overall_score":0.99}',
            provider="mock",
            model_name="mock",
            finish_reason="length",
        )

    monkeypatch.setattr(judge, "complete_text", truncated)
    result = await judge.judge_chapter_commercial_quality(
        object(), load_settings(env={}), chapter_number=1, content_md="候选正文保持不变。"
    )
    assert (
        result.infrastructure_failure and not result.passed and result.rewrite_plan.scope == "judge"
    )


def test_catalog_has_atomic_fact_records_not_metadata_leaves():
    source = project_commercial_source(
        {
            "established_facts": {
                "facts": [
                    {
                        "text": "港口已经关闭。",
                        "source_ref": {"chapter": 1, "id": "source"},
                        "temporal_status": "mid_chapter",
                        "metadata": {"unused": "not another fact"},
                    }
                ]
            }
        }
    )
    sources = assemble_commercial_sources(
        chapter_number=2, content_md="他等待开港。", source=source, previous_chapters=[]
    )
    assert len([item for item in sources.catalog.values() if item["kind"] == "observation"]) == 1


def test_shared_sources_are_lossless_and_catalog_resolves_with_navigation_hints():
    fact = {
        "text": "旧港的三号浮标已毁。",
        "source_ref": {"chapter_number": 1},
        "temporal_status": "chapter_end",
    }
    source = {
        "established_facts": {"facts": [fact]},
        "adopted_continuity": {"source_bound_canon": [deepcopy(fact)]},
    }
    body = "海员沿着旧港寻找浮标。" * 8
    prior = "风暴摧毁了三号浮标。" * 8
    sources = assemble_commercial_sources(
        chapter_number=2,
        content_md=body,
        source=source,
        previous_chapters=[{"chapter_number": 1, "body": prior}],
    )
    payload = json.loads(sources.text)
    assert payload["current_body"] == body and payload["previous_chapters"][0]["body"] == prior
    assert sources.receipt["structural_aliases"]
    assert (
        resolve_commercial_source_pointer(
            payload, "#/source_context/adopted_continuity/source_bound_canon/0/text"
        )
        == fact["text"]
    )
    for entry in payload["citation_catalog"]:
        original = sources.catalog[entry["id"]]
        resolved = resolve_commercial_source_pointer(payload, entry["source_ref"])
        assert entry["locator_hint"] == original["text"][:32]
        assert "text" not in entry  # Full source is sent once; this is navigation only.
        if entry["kind"] in {"current", "prior"}:
            assert resolved[entry["start"] : entry["end"]] == original["text"]
        else:
            assert resolved == fact
    changed = deepcopy(source)
    changed["adopted_continuity"]["source_bound_canon"][0]["temporal_status"] = "mid_chapter"
    other = assemble_commercial_sources(
        chapter_number=2, content_md=body, source=changed, previous_chapters=[]
    )
    expanded = json.loads(other.text)
    assert (
        resolve_commercial_source_pointer(
            expanded, "#/source_context/adopted_continuity/source_bound_canon/0/temporal_status"
        )
        == "mid_chapter"
    )


@pytest.mark.parametrize(
    "payload,pointer",
    [
        ({"a": {"$ref": "#/missing"}}, "#/a"),
        ({"a": {"$ref": "#/a"}}, "#/a"),
        ({"a": {"$ref": "#/b"}, "b": {"$ref": "#/a"}}, "#/a"),
    ],
)
def test_invalid_source_pointers_fail_closed(payload, pointer):
    with pytest.raises(ValueError, match="commercial_source_reference"):
        resolve_commercial_source_pointer(payload, pointer)
