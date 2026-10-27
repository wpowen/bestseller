"""A short paid rewrite remains the edit target; the incumbent is not its substitute."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
from test_revision_source_binding_sqlite import db as db
from test_revision_source_binding_sqlite import seed
from test_rewrite_atomic_evidence_sqlite import _audit
from test_rewrite_atomic_evidence_sqlite import runtime as runtime

from bestseller.infra.db.models import (
    ChapterDraftVersionModel,
    ChapterQualityReportModel,
    ProjectModel,
    RewriteTaskModel,
)
from bestseller.services import reviews
from bestseller.services.llm import LLMCompletionResult
from bestseller.services.quality_report_claim import graded_text_fingerprint
from bestseller.services.rewrite_patch import text_hash
from bestseller.settings import load_settings

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]
INCUMBENT = ("温室管理员核对气象观测记录随后封存仪器等待下一班接手" * 110)[:2516]
SHORT = ("观测员沿着玻璃温室检查仪器把新测量的气温写入记录" * 70)[:1263]
STILL_SHORT = ("接班员读完观测记录沿走廊寻找温差出现的原因" * 90)[:1450]
REPAIRED = ("交班员带着封好的记录走向值班室发现窗边温度继续降低" * 100)[:2200]


@pytest.mark.parametrize("repair", ["viable", "short", "exception"])
async def test_short_finish_stop_candidate_is_evaluated_repaired_and_read_back(
    db,
    monkeypatch,
    runtime,
    repair: str,
) -> None:
    band = runtime
    from bestseller.services.ai_flavor import detector

    # These repeated measurement fixtures test body identity and length, not style.
    monkeypatch.setattr(detector, "detect", lambda *a, **k: SimpleNamespace(overall_score=0))
    band.hard_min, band.hard_max, band.safe_min, band.safe_max = 1800, 3000, 2000, 2800
    assert reviews.count_words(SHORT) == 1263
    requests, evaluated = [], []
    async with db() as session:
        chapter, current, task = await seed(session)
        current.content_md, current.word_count = INCUMBENT, reviews.count_words(INCUMBENT)
        chapter.production_state = "ok"
        chapter.target_word_count = 2200
        task.metadata_json = {**task.metadata_json, "parent_draft_hash": text_hash(INCUMBENT)}
        original_attempts = task.attempts
        project = ProjectModel(
            id=chapter.project_id,
            slug="isolated-weather-station",
            title="The weather station",
            genre="science-fiction",
            language="zh-CN",
            target_chapters=4,
            target_word_count=8800,
            metadata_json={},
            invariants_json=None,
        )
        await session.flush()
        monkeypatch.setattr(
            reviews,
            "_load_chapter_context",
            AsyncMock(return_value=(project, chapter, None, [], current)),
        )
        monkeypatch.setattr(
            reviews, "_collect_post_assembly_duplicate_findings", AsyncMock(return_value=())
        )
        monkeypatch.setattr(reviews, "_quality_retrofit_candidate_findings", lambda *a, **k: [])
        monkeypatch.setattr(
            reviews,
            "_audit_chapter_rewrite_candidate",
            lambda **kw: (
                _audit(
                    "LENGTH_OUT_OF_BAND" if reviews.count_words(kw["content_md"]) < 1800 else None
                ),
                band,
            ),
        )

        async def gate(**kwargs):
            text = kwargs["content"]
            evaluated.append(text)
            extra = kwargs.get("extra_report_payload")
            if extra:
                session.add(
                    ChapterQualityReportModel(
                        chapter_id=chapter.id,
                        blocks_write=False,
                        report_json={
                            **extra,
                            "graded": graded_text_fingerprint(text),
                            "violations": [],
                            "blocking_codes": [],
                        },
                    )
                )
                await session.flush()
            # The deterministic publication floor must still block the actual
            # short body even if this independent gate reports OK.
            return "ok"

        async def complete(_session, _settings, request):
            requests.append(request)
            if len(requests) == 1:
                content = SHORT
            elif repair == "exception":
                raise RuntimeError("fixture repair unavailable")
            else:
                content = {"viable": REPAIRED, "short": STILL_SHORT}[repair]
            return LLMCompletionResult(
                content=content,
                provider="fixture",
                model_name="fixture-editor",
                finish_reason="stop",
                llm_run_id=None,
            )

        monkeypatch.setattr(reviews, "_evaluate_chapter_quality_gate", gate)
        monkeypatch.setattr(reviews, "complete_text", complete)
        returned, _ = await reviews.rewrite_chapter_from_task(
            session,
            project.slug,
            chapter.chapter_number,
            rewrite_task_id=task.id,
            settings=load_settings(env={}),
        )
        await session.commit()
        returned_id, current_id, task_id = returned.id, current.id, task.id
    assert len(requests) == 2
    assert requests[0].prompt_template == "chapter_rewrite"
    # This was already the selected protocol in immutable 8R source.
    assert requests[0].prompt_version == "3.2-source-selected-voice-editor"
    assert requests[1].prompt_template == "chapter_rewrite_repair"
    assert requests[1].prompt_version == "2.0-candidate-source"
    assert requests[1].system_prompt == requests[0].system_prompt
    assert requests[1].user_prompt.startswith(requests[0].user_prompt)
    repair_input = json.loads(
        requests[1].user_prompt.split("REWRITE_REPAIR_STAGE_INPUT_JSON:\n", 1)[1]
    )
    assert repair_input["failed_candidate"]["body"] == SHORT
    assert repair_input["failed_candidate"]["body_sha256"] == text_hash(SHORT)
    assert all(f["candidate_body_sha256"] == text_hash(SHORT) for f in repair_input["findings"])
    assert "LENGTH_UNDER" in {f["diagnostic"]["code"] for f in repair_input["findings"]}
    assert evaluated[0] == SHORT
    async with db() as reader:
        stored_task = await reader.get(RewriteTaskModel, task_id)
        incumbent = await reader.get(ChapterDraftVersionModel, current_id)
        drafts = list(
            await reader.scalars(
                select(ChapterDraftVersionModel).order_by(ChapterDraftVersionModel.version_no)
            )
        )
        assert len(drafts) == 2
        candidate = drafts[-1]
        metadata = stored_task.metadata_json
        initial = next(e for e in metadata["candidate_evidence_history"] if e["stage"] == "initial")
        assert initial["content_md"] == SHORT
        assert initial["word_count"] == 1263
        assert initial["body_sha256"] == text_hash(SHORT)
        assert initial["gate_outcome"] == "blocked"
        assert "LENGTH_UNDER" in {v["code"] for v in initial["quality_gate_violations"]}
        assert incumbent.content_md == INCUMBENT
        assert candidate.content_md != INCUMBENT
        assert metadata["candidate_body_sha256"] == text_hash(candidate.content_md)
        assert metadata["candidate_gate_graded"] == graded_text_fingerprint(candidate.content_md)
        assert stored_task.attempts == original_attempts + 1
        if repair == "viable":
            assert candidate.content_md == REPAIRED
            assert candidate.is_current and not incumbent.is_current
            assert returned_id == candidate.id
            assert stored_task.status == "completed"
        else:
            assert candidate.content_md == (STILL_SHORT if repair == "short" else SHORT)
            assert incumbent.is_current and not candidate.is_current
            assert returned_id == incumbent.id
            assert stored_task.status == "failed"
            assert metadata["preserved_current_chapter_draft_id"] == str(current_id)
            assert metadata["candidate_quality_gate_outcome"] == "blocked"
