"""Actual rewrite entry consumes classified scope before any editing request.

SQLite parent/report identity and pure proof parsers are real. This boundary
test stops at length planning and does not claim native paid review admission.
"""
from copy import deepcopy
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from test_optional_fact_scope_preconditions import db as db, runtime as runtime
from test_revision_source_binding_sqlite import seed
from test_rewrite_fact_scope import PARENT, _report, stable as stable
from full_flow_fact_scope.test_reader_first_repair_route import primary

from bestseller.infra.db.models import ProjectModel, ReviewReportModel
from bestseller.services import full_flow_length, reviews, rewrite_fact_scope
from bestseller.services.source_bound_planning_content import new_reader_first_binding
from bestseller.services.rewrite_patch import text_hash
from bestseller.settings import load_settings

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


class ReachedPlanning(Exception):
    pass


@pytest.mark.parametrize("route", ["fact_only", "editorial_only", "mixed", "no_body_edit", "unknown"])
async def test_new_protocol_consumes_route_before_length_planning(db, monkeypatch, runtime, stable, route):
    report = _report(kind="current_fact_conflict" if route in {"fact_only", "mixed"} else "narrative_quality")
    if route in {"editorial_only", "mixed"}:
        primary(report, "rewrite")
    if route == "unknown":
        report["evidence_summary"].pop("llm_window_quality_judge")
    seen = []

    async def stop(*args, **kwargs):
        seen.append(True)
        raise ReachedPlanning()

    monkeypatch.setattr(full_flow_length, "freeze_request", stop)
    async with db() as session:
        chapter, parent, task = await seed(session)
        parent.content_md = PARENT
        parent.word_count = reviews.count_words(PARENT)
        report.update(draft_id=str(parent.id), reviewed_body_fingerprint=reviews.chapter_review_body_fingerprint(PARENT))
        source = ReviewReportModel(id=uuid4(), project_id=chapter.project_id, target_type="chapter",
            target_id=chapter.id, reviewer_type="fixture-real-parsers", verdict="rewrite",
            severity_max="critical", structured_output=deepcopy(report))
        session.add(source)
        task.trigger_type = "chapter_review"
        task.metadata_json = {**task.metadata_json, "review_report_id": str(source.id),
                              "parent_draft_hash": text_hash(PARENT)}
        project = ProjectModel(id=chapter.project_id, slug="reader-route-test", title="Fixture", genre="realistic",
            language="en", target_word_count=8000, target_chapters=4,
            metadata_json={"reader_first_planning": new_reader_first_binding("A family choice.")})
        await session.flush()
        monkeypatch.setattr(reviews, "_load_chapter_context", AsyncMock(return_value=(project, chapter, None, [], parent)))
        original = deepcopy(source.structured_output)
        expected = rewrite_fact_scope.reader_first_repair_route(PARENT, original)
        assert expected["route"] == route
        if route in {"fact_only", "editorial_only"}:
            with pytest.raises(ReachedPlanning):
                await reviews.rewrite_chapter_from_task(session, project.slug, 1, settings=load_settings(env={}))
            assert seen == [True]
        else:
            with pytest.raises(ValueError, match="reader_first_repair_route_requires_resolution"):
                await reviews.rewrite_chapter_from_task(session, project.slug, 1, settings=load_settings(env={}))
            assert seen == []
        assert task.metadata_json["reader_first_repair_route"] == expected
        assert source.structured_output == original
        assert task.status == "pending" and task.attempts == 1
        assert parent.content_md == PARENT
        if route == "fact_only":
            assert task.metadata_json["verified_fact_repair_scope"] == rewrite_fact_scope.require_fact_repair_scope(PARENT, original)
