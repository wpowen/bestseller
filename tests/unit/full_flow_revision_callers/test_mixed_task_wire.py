"""Mixed caller wire identity with real SQLite rows and retained-proof parsers.

Reuse the fact caller's context/provider fixtures; stop at the completion request
boundary and inspect the real SDK message renderer. The anchor service is isolated,
not graded. This does not establish paid custody or native reader admission.
"""

from copy import deepcopy
import hashlib
import json
from unittest.mock import AsyncMock
from uuid import uuid4

from full_flow_fact_scope.test_mixed_repair_scope import projectable_mixed_case
import pytest
from test_revision_source_binding_sqlite import seed
from test_rewrite_fact_scope import db as db
from test_rewrite_fact_scope import runtime as runtime
from test_rewrite_fact_scope import stable as stable

from bestseller.infra.db.models import ProjectModel, ReviewReportModel
from bestseller.services import llm, reader_judge, reviews
from bestseller.services.llm_closed_loop import LLMGateFinding
from bestseller.services.rewrite_fact_scope import derive_mixed_repair_scope
from bestseller.services.rewrite_repair_source import build_rewrite_repair_user_prompt
from bestseller.services.source_bound_planning_content import new_reader_first_binding
from bestseller.settings import load_settings

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@pytest.mark.parametrize("stage", ["semantic_repair", "retrofit_repair"])
async def test_mixed_native_task_marker_survives_repair_prompt(
    db, monkeypatch, runtime, stable, stage,
):
    body, report_payload, candidate, _returned = projectable_mixed_case()
    requests = []

    class EditorRequestCapturedError(Exception):
        pass

    async def capture(_session, _settings, request):
        requests.append(request)
        raise EditorRequestCapturedError()

    monkeypatch.setattr(reviews, "complete_text", capture)
    # Native anchor/grade admission has its own SQLite suite. This test covers
    # producer wire binding without manufacturing a successful reader grade.
    freeze = AsyncMock(return_value={"anchors": []})
    monkeypatch.setattr(reader_judge, "freeze_reader_revision_anchors", freeze)
    async with db() as session:
        chapter, parent, task = await seed(session)
        project = await session.get(ProjectModel, chapter.project_id)
        project.metadata_json = {
            "reader_first_planning": new_reader_first_binding("A family choice."),
        }
        chapter.production_state = "pending"
        chapter.target_word_count = 2000
        parent.content_md = body
        parent.word_count = reviews.count_words(body)
        body_sha = hashlib.sha256(body.encode("utf-8")).hexdigest()
        report_payload.update(
            draft_id=str(parent.id),
            reviewed_body_fingerprint=reviews.chapter_review_body_fingerprint(body),
        )
        report = ReviewReportModel(
            id=uuid4(), project_id=project.id, target_type="chapter", target_id=chapter.id,
            reviewer_type="fixture-real-parsers", verdict="rewrite", severity_max="critical",
            structured_output=deepcopy(report_payload),
        )
        session.add(report)
        task.metadata_json = {
            **task.metadata_json,
            "review_report_id": str(report.id),
            "parent_draft_hash": body_sha,
        }
        await session.commit()
        monkeypatch.setattr(
            reviews, "_load_chapter_context",
            AsyncMock(return_value=(project, chapter, None, [], parent)),
        )
        with pytest.raises(EditorRequestCapturedError):
            await reviews.rewrite_chapter_from_task(
                session, project.slug, chapter.chapter_number, rewrite_task_id=task.id,
                settings=load_settings(env={}),
            )

        assert len(requests) == 1
        request = requests[0]
        assert request.logical_role == "editor"
        assert request.prompt_template == "chapter_rewrite"
        assert request.prompt_version == "3.4-verified-mixed-edit-scope"
        assert request.project_id == project.id
        assert request.messages_override is None
        assert task.metadata_json["reader_first_repair_route"]["route"] == "mixed"
        scope = derive_mixed_repair_scope(body, report_payload)
        assert task.metadata_json["verified_mixed_repair_scope"] == scope
        assert "verified_fact_repair_scope" not in task.metadata_json
        expected = {
            "project_id": str(project.id),
            "chapter_id": str(chapter.id),
            "source_draft_id": str(parent.id),
            "source_body_sha256": body_sha,
            "review_report_id": str(report.id),
            "rewrite_task_id": str(task.id),
        }
        repaired = build_rewrite_repair_user_prompt(
            original_user_prompt=request.user_prompt,
            failed_candidate=candidate,
            findings=[LLMGateFinding(
                code="FIXTURE_REPAIR", severity="major", path="candidate",
                message="Inspect the complete failed candidate against its source.",
            )],
            stage=stage,
            language=project.language,
        )
        assert repaired.encode("utf-8").startswith(request.user_prompt.encode("utf-8"))
        for prompt in (request.user_prompt, repaired):
            # The six fields must travel in prompt bytes, independently of the
            # request metadata omitted by the physical retained-request codec.
            wire = llm._build_messages(
                request.model_copy(update={"user_prompt": prompt, "metadata": {}}),
                provider="openai",
            )
            user_messages = [message["content"] for message in wire if message["role"] == "user"]
            assert user_messages == [prompt]
            for marker, expected_value in (
                ("BACKEND_MIXED_REPAIR_SCOPE_JSON:\n", scope),
                ("BACKEND_MIXED_REPAIR_TASK_JSON:\n", expected),
            ):
                assert prompt.count(marker) == 1
                actual, _end = json.JSONDecoder().raw_decode(prompt.split(marker, 1)[1])
                assert actual == expected_value
        repair_payload = json.loads(repaired.split("REWRITE_REPAIR_STAGE_INPUT_JSON:\n", 1)[1])
        assert repair_payload["stage"] == stage
        assert repair_payload["failed_candidate"]["body"] == candidate
        assert report.structured_output == report_payload and report.verdict == "rewrite"
        assert parent.content_md == body and parent.is_current
        assert task.status == "pending" and task.attempts == 1
        freeze.assert_awaited_once()
