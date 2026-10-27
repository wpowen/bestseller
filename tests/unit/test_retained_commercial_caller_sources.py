"""SQLite caller integration: preserve primary input and pass full commercial sources.

The commercial verifier is observed at its boundary; its physical/projection
checks have separate real-return regressions. No provider or PG is used here.
"""

from copy import deepcopy
import hashlib
import json
from unittest.mock import AsyncMock, Mock

import pytest
from test_commercial_component_cohort import cohort as cohort_fixture
from test_retained_review_input_proof import retained as retained_fixture
from test_retained_review_input_proof import verify

from bestseller.infra.db.models import (
    ExecutionAttemptModel,
    ExecutionOperationModel,
    SceneCardModel,
)
from bestseller.services import chapter_generation_input_builder as builder
from bestseller.services import chapter_llm_quality_judge as judge
from bestseller.services import commercial_source_evidence as source_service
from bestseller.services import llm
from bestseller.services import retained_review_input_proof as proof
from bestseller.services.creation_contract_authority import select_creation_contracts
from bestseller.services.planner import _source_bound_cast_enabled

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]
cohort = cohort_fixture
retained = retained_fixture


async def bind_fixture_primary(case):
    """Extend synthetic primary evidence consistently; preserve budget/call counters."""
    rig = case.rig
    path = case.paths["chapter_review"]
    payload = json.loads(path.read_text())
    payload["messages"]["user"] = llm._bounded_text(
        "Full retained input\n"
        + json.dumps(case.source, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    )
    path.write_text(json.dumps(payload))
    metadata = deepcopy(case.primary.metadata_json)
    metadata["review_source_receipt"]["source_sha256"] = proof._hash(case.source)
    case.primary.metadata_json = metadata
    request = proof._retained_request(rig.settings, case.primary)
    catalog, _ = proof._fresh_catalog()
    role = proof._fresh_role(rig.settings, rig.project, request, catalog)
    effective_hash = llm._effective_input_hash(request, role)
    case.primary.prompt_hash = effective_hash
    metadata["effective_input_receipt"]["hash"] = effective_hash
    case.primary.metadata_json = deepcopy(metadata)
    execution = metadata["execution_receipt"]
    root = await rig.session.get(ExecutionOperationModel, execution["operation_key"])
    aid = execution["attempts"][0]["attempt_id"]
    attempt = await rig.session.get(ExecutionAttemptModel, aid)
    transport_key = hashlib.sha256(
        f"transport-result.v1\n{root.scope_id}\n{aid}".encode()
    ).hexdigest()
    transport = await rig.session.get(ExecutionOperationModel, transport_key)
    paid = deepcopy(root.result_json)
    paid["metadata"]["paid_call_origin"]["prompt_hash"] = effective_hash
    paid["metadata"]["effective_input_receipt"]["hash"] = effective_hash
    root.input_hash = attempt.input_hash = transport.input_hash = effective_hash
    root.result_json = paid
    result = deepcopy(transport.result_json)
    result.update(input_hash=effective_hash, result=deepcopy(paid))
    transport.result_json = result
    await rig.session.commit()


@pytest.mark.parametrize(
    "substitute_primary", [False, True], ids=["normal-bundle", "regressed-subset"]
)
async def test_verify_projected_path_passes_bundle_contracts_and_complete_adopted_history(
    retained, monkeypatch, substitute_primary
):
    case, rig = retained, retained.rig
    history = {
        "schema": "planning-continuity.v1",
        "project_id": str(rig.project.id),
        "before_chapter": 2,
        "adopted_sources": [{"chapter_number": 1, "adoption_state": "accepted"}],
        "recent_adopted_prose": [{"chapter_number": 1, "body": "The lock was opened yesterday."}],
        "source_bound_canon": [],
        "source_bound_timeline": [],
        "authority": "Only adopted prose establishes occurrences.",
    }
    case.source["adopted_continuity"] = deepcopy(history)
    rig.project.metadata_json = {
        **rig.project.metadata_json,
        "creation_intent_contract": {
            "language": "en",
            "pov": "third-limited",
            "field_sources": {"language": "explicit", "pov": "explicit"},
        },
    }
    rig.session.add(
        SceneCardModel(
            project_id=rig.project.id,
            chapter_id=rig.chapter.id,
            scene_number=1,
            scene_type="development",
            participants=["sailor"],
            purpose={"story": "Inspect the key"},
            entry_state={"summary": "Key on table"},
            exit_state={"summary": "Key examined"},
        )
    )
    await bind_fixture_primary(case)
    payload = deepcopy(case.report.structured_output)
    # This marker selects the caller branch only. No projected grade is accepted by this test.
    payload["evidence_summary"]["commercial_action_projection"] = {"status": "projected"}
    case.report.structured_output = payload
    await rig.session.commit()
    before = deepcopy((case.source, case.report.structured_output, rig.project.metadata_json))
    original_build = builder.build_chapter_generation_input_bundle
    build = Mock(wraps=original_build)
    project = Mock(wraps=judge._commercial_source_projection)
    monkeypatch.setattr(builder, "build_chapter_generation_input_bundle", build)
    monkeypatch.setattr(judge, "_commercial_source_projection", project)
    assemble = source_service.assemble_commercial_sources
    observed = []

    async def commercial_boundary(session, settings, **kwargs):
        inputs = build.call_args.kwargs
        assert inputs["project"] is rig.project and inputs["chapter"] is rig.chapter
        assert [s.scene_number for s in inputs["scenes"]] == [1]
        generation = original_build(**inputs).model_dump(mode="json")
        contracts = select_creation_contracts(
            rig.project.metadata_json, source_bound=_source_bound_cast_enabled(rig.project)
        )
        generation["approved_creation_contracts"] = contracts
        generation["adopted_continuity"] = {
            key: value for key, value in history.items() if key != "recent_adopted_prose"
        }
        selected = project.call_args.args[0]
        assert selected == generation
        assert (
            selected["approved_creation_contracts"]["creation_intent_contract"]["pov"]
            == "third-limited"
        )
        assert "recent_adopted_prose" not in selected["adopted_continuity"]
        assert project.call_args.kwargs["previous_chapters"] == history["recent_adopted_prose"]
        expected = assemble(
            chapter_number=2,
            content_md=rig.draft.content_md,
            source=judge._commercial_source_projection(
                generation,
                chapter_number=2,
                content_md=rig.draft.content_md,
                previous_chapters=history["recent_adopted_prose"],
            ),
            previous_chapters=history["recent_adopted_prose"],
        )
        subset = assemble(
            chapter_number=2,
            content_md=rig.draft.content_md,
            source=case.source["current_source_channels"],
            previous_chapters=history["recent_adopted_prose"],
        )
        assert (
            expected.receipt["semantic_source_sha256"] != subset.receipt["semantic_source_sha256"]
        )
        actual = kwargs["commercial_sources"]
        if actual != expected:
            raise ValueError("commercial_current_source_mismatch")
        assert actual.text == expected.text and actual.catalog == expected.catalog
        assert any(row["kind"] == "prior" for row in actual.catalog.values())
        observed.append(actual)
        return {"caller_source_verified": True}

    boundary = AsyncMock(side_effect=commercial_boundary)
    monkeypatch.setattr(proof, "_commercial_proof", boundary)
    if substitute_primary:

        def wrong_assembly(**kwargs):
            return assemble(
                chapter_number=2,
                content_md=rig.draft.content_md,
                source=case.source["current_source_channels"],
                previous_chapters=history["recent_adopted_prose"],
            )

        monkeypatch.setattr(source_service, "assemble_commercial_sources", wrong_assembly)
        with pytest.raises(ValueError, match="commercial_current_source_mismatch"):
            await verify(case)
        assert not observed
    else:
        result = await verify(case)
        assert result["commercial"] == {"caller_source_verified": True}
        assert result["provider_calls"] == result["writes"] == 0
        assert len(observed) == 1
    boundary.assert_awaited_once()
    assert (case.source, case.report.structured_output, rig.project.metadata_json) == before
    assert not rig.session.new and not rig.session.dirty and not rig.session.deleted
