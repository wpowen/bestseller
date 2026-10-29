"""Fresh producer issues a capsule before source-complete prompt rendering.

Real entrypoint and SQLite persistence; unrelated review/audit fixtures are
isolated. Provider IO is replaced. This is not successful paid issuance/E05b.
"""
from copy import deepcopy
from uuid import uuid4
import pytest
from test_chapter_review_commercial_checkpoint import review_db  # noqa: F401
from test_primary_review_submission_entrypoint import primary_db, _run, _tool_completion  # noqa: F401
from bestseller.infra.db.models import LlmRunModel
from bestseller.services import review_source_context, review_source_ordering, reviews
from bestseller.services import primary_review_transport as transport
from bestseller.services import review_issuer_archive as archive

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]

async def test_real_primary_entry_binds_new_issuer_before_render(primary_db, monkeypatch, tmp_path):
    db = primary_db
    db.settings.artifact_store.local_dir = str(tmp_path / "issuer_entry_artifacts")
    source = {"schema": review_source_context.SOURCE_POLICY,
        "adopted_continuity": {"schema": "planning-continuity.v1", "adopted_sources": []},
        "current_source_channels": {"source_selection_receipt": {
            "policy": "typed-world-and-relevant-current-state.v1",
            "source_ordering_policy": review_source_ordering.SOURCE_ORDERING_POLICY}},
        "authority": "source data only"}
    original = deepcopy(source)
    async def load(*args, **kwargs):
        return source
    monkeypatch.setattr(review_source_context, "load_review_source_context", load)
    renders = []
    actual_render = transport.prepare_source_complete_prompt
    def render(**kwargs):
        renders.append(deepcopy(kwargs["source_context"]))
        return actual_render(**kwargs)
    monkeypatch.setattr(transport, "prepare_source_complete_prompt", render)
    sends = []
    async def send(session, settings, request):
        sends.append(request)
        result = _tool_completion()
        run = LlmRunModel(id=uuid4(), project_id=db.project.id, logical_role="critic",
            provider=result.provider, model_name=result.model_name,
            prompt_template=request.prompt_template, prompt_version=request.prompt_version,
            finish_reason=result.finish_reason, metadata_json={"fixture_only": True})
        session.add(run)
        await session.flush()
        result.llm_run_id = run.id
        return result
    monkeypatch.setattr(reviews, "complete_text", send)
    await _run(db)
    assert renders, "PRIMARY_SOURCE_NOT_RENDERED"
    assert "review_issuer" in renders[0], "FRESH_PRIMARY_ISSUER_NOT_BOUND"
    assert source == original
    capsule = renders[0]["review_issuer"]
    verified = archive.verify_current_issuer_archive(db.settings, capsule=capsule)
    assert verified["status"] == "equivalent"
    assert len(sends) == 1
