"""Missing-only initialization on real isolated SQLite and actual body gates."""
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
from functools import lru_cache
import hashlib
import json
from uuid import uuid4

import pytest
from sqlalchemy import event, select, update
from test_chapter_review_admission_sqlite import admission_db, BODY  # noqa: F401

from bestseller.infra.db import models as db
from bestseller.services import reviews
from bestseller.services.invariants import invariants_from_dict, invariants_to_dict, seed_invariants
from bestseller.services.project_invariant_initialization import ensure_missing_project_invariants
from bestseller.settings import LLM_RUNTIME_PROFILES, apply_runtime_llm_profile

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


async def initialize(d, **kwargs):
    return await ensure_missing_project_invariants(d.session, d.settings,
        project_id=d.project.id, workflow_id=kwargs.get("workflow_id", d.workflow.id))


async def test_missing_only_real_gate_readback_idempotence_and_rollback(admission_db, monkeypatch):
    d = admission_db
    d.settings.generation.words_per_chapter = d.settings.generation.words_per_chapter.model_copy(
        update={"min": 20, "target": 100, "max": 200})
    from bestseller import settings as settings_module
    monkeypatch.setattr(settings_module, "get_settings", lru_cache(maxsize=1)(lambda: d.settings))
    connection = await d.session.connection()
    await connection.run_sync(lambda conn: conn.connection.dbapi_connection.create_function(
        "NOW", 0, lambda: datetime.now(timezone.utc).isoformat()))
    d.cfg = replace(d.cfg, l6_enabled=True, l5=replace(d.cfg.l5, enabled=False))
    before_report, before_meta = deepcopy(d.report.structured_output), deepcopy(d.project.metadata_json)
    skipped = await reviews._evaluate_rewrite_quality_gate(session=d.session, project=d.project,
        chapter_number=1, content=BODY)
    assert skipped.outcome is None and skipped.report is None
    receipt = await initialize(d)
    assert receipt["status"] == "initialized" and receipt["changed"]
    assert receipt["requires_current_body_gate_recheck"] is True
    assert receipt["requires_review_freshness_rebinding"] is True
    assert receipt["body_gate_pass_asserted"] is False
    await d.session.commit()
    async with d.factory() as reader:
        project = await reader.get(db.ProjectModel, d.project.id)
        parsed = invariants_from_dict(project.invariants_json)
        assert parsed.length_envelope.min_chars == 110 and parsed.length_envelope.max_chars == 1100
        assert parsed.naming_scheme is None and parsed.opening_archetype_pool == ()
        assert parsed.cliffhanger_policy.allowed_types == () and parsed.hype_scheme.is_empty
        assert parsed.protagonist_name is None and parsed.forced_methodology_fragments == ()
        assert all(project.metadata_json[k] == v for k, v in before_meta.items())
        assert (await reader.get(db.ReviewReportModel, d.report.id)).structured_output == before_report
        ledger = await reader.get(db.ExecutionBudgetModel, d.budget.scope_id)
        assert (ledger.calls_used, ledger.tokens_charged) == (1, 30)
    actual = await reviews._evaluate_rewrite_quality_gate(session=d.session, project=d.project,
        chapter_number=1, content=BODY)
    assert actual.outcome == "ok" and actual.report is not None
    assert actual.report.report_json["rewrite_evaluation_id"] == actual.evaluation_id
    blocked = await reviews._evaluate_rewrite_quality_gate(session=d.session, project=d.project,
        chapter_number=1, content="Short.")
    assert blocked.outcome == "blocked" and blocked.report is not None
    await d.session.commit()
    writes = []
    def capture(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith(("UPDATE", "INSERT", "DELETE")):
            writes.append(statement)
    event.listen(d.session.bind.sync_engine, "before_cursor_execute", capture)
    try:
        repeat = await initialize(d)
        assert repeat["status"] == "existing_valid" and repeat["changed"] is False
        assert writes == []
    finally:
        event.remove(d.session.bind.sync_engine, "before_cursor_execute", capture)


@pytest.mark.parametrize("raw", [{}, False, [], {"project_id": "broken", "language": "en"}])
async def test_invalid_existing_payload_never_reseeds_or_mutates_metadata(admission_db, raw):
    d = admission_db
    d.project.invariants_json = deepcopy(raw)
    d.project.metadata_json = {**d.project.metadata_json, "author_data": {"names": ["Aster", "Mira"]}}
    await d.session.commit()
    before = deepcopy(d.project.metadata_json)
    receipt = await initialize(d)
    assert receipt["status"] == "existing_invalid" and not receipt["changed"]
    await d.session.commit()
    async with d.factory() as reader:
        project = await reader.get(db.ProjectModel, d.project.id)
        assert project.invariants_json == raw and project.metadata_json == before


async def test_existing_valid_author_contract_and_unknown_extensions_preserved(admission_db):
    d = admission_db
    raw = invariants_to_dict(seed_invariants(project_id=d.project.id, language="en",
        words_per_chapter=d.settings.generation.words_per_chapter,
        overrides={"protagonist_name": "Maribel"}))
    raw["author_extension"] = {"unchanged": "literal writer data"}
    d.project.invariants_json = raw
    await d.session.commit()
    receipt = await initialize(d)
    assert receipt["status"] == "existing_valid" and not receipt["changed"]
    assert d.project.invariants_json == raw


@pytest.mark.parametrize("genre", ["cozy-fantasy", "orbital documentary"])
async def test_no_genre_preset_or_naming_pool_injection(admission_db, genre):
    d = admission_db
    d.project.genre = genre
    d.project.metadata_json = {**d.project.metadata_json, "prompt_pack_key": "cozy-fantasy",
        "writing_profile_overrides": {"forced_methodology_fragments": ["unselected formula"]},
        "story_enhancers": {"source": "user", "comedy": True},
        "locked_identity_manifest": {"names": ["Tamsin", "Luca"]}}
    await d.session.commit()
    before = deepcopy(d.project.metadata_json)
    await initialize(d)
    inv = invariants_from_dict(d.project.invariants_json)
    assert inv.naming_scheme is None and inv.protagonist_name is None
    assert not inv.opening_archetype_pool and not inv.cliffhanger_policy.allowed_types
    assert inv.hype_scheme.is_empty and not inv.forced_methodology_fragments
    assert all(d.project.metadata_json[k] == v for k, v in before.items())


async def test_actual_style_and_canonical_explicit_pov_are_respected(admission_db):
    d = admission_db
    d.session.add(db.StyleGuideModel(project_id=d.project.id, pov_type="first-person", tense="present"))
    await d.session.commit()
    receipt = await initialize(d)
    assert d.project.invariants_json["pov"] == "first"
    assert d.project.invariants_json["tense"] == "present"
    assert receipt["inputs"]["pov_source"] == "persisted_style_guide"


async def test_canonical_per_field_pov_precedes_style_and_does_not_edit_it(admission_db):
    d = admission_db
    style = db.StyleGuideModel(project_id=d.project.id, pov_type="third-limited", tense="present")
    d.session.add(style)
    d.project.metadata_json = {**d.project.metadata_json, "creation_intent_contract": {
        "field_sources": {"pov": "explicit"}, "pov": "first-person"}}
    await d.session.commit()
    receipt = await initialize(d)
    assert receipt["inputs"]["pov_source"] == "canonical_explicit"
    assert d.project.invariants_json["pov"] == "first"
    assert style.pov_type == "third-limited"


async def test_runtime_profile_and_supplied_technical_length_are_audit_only(admission_db, tmp_path, monkeypatch):
    d = admission_db
    profile = tmp_path / "runtime-profile.json"
    key = next(iter(LLM_RUNTIME_PROFILES))
    data = json.dumps({"active_profile": key}).encode()
    profile.write_bytes(data)
    # Use the actual runtime's environment key, without changing live profiles.
    from bestseller.settings import LLM_RUNTIME_PROFILE_ENV
    monkeypatch.setenv(LLM_RUNTIME_PROFILE_ENV, str(profile))
    expected = apply_runtime_llm_profile(d.settings)
    receipt = await initialize(d)
    assert receipt["inputs"]["runtime_profile_sha256"] == hashlib.sha256(data).hexdigest()
    assert receipt["inputs"]["words_per_chapter"] == d.settings.generation.words_per_chapter.model_dump(mode="json")
    assert receipt["inputs"]["effective_llm_configuration_sha256"]
    encoded = json.dumps(d.project.invariants_json)
    assert expected.llm.writer.model not in encoded
    assert "api_key" not in json.dumps(receipt)


async def test_caller_rollback_restores_missing_contract(admission_db):
    d = admission_db
    pid = d.project.id
    await initialize(d)
    await d.session.rollback()
    async with d.factory() as reader:
        project = await reader.get(db.ProjectModel, pid)
        assert project.invariants_json is None
        assert "invariant_content_origins" not in project.metadata_json


@pytest.mark.parametrize("case", ["foreign_workflow", "foreign_chapter", "dirty_project", "old_origins", "unsupported_style", "adopted_effects", "explicit_empty_pov"])
async def test_refuse_ambiguous_or_uncommitted_sources(admission_db, case):
    d = admission_db
    if case == "foreign_workflow":
        d.workflow.project_id = uuid4()
    elif case == "foreign_chapter":
        d.workflow.scope_id = uuid4()
    elif case == "dirty_project":
        d.project.title = "Unsaved author title"
    elif case == "old_origins":
        d.project.metadata_json = {**d.project.metadata_json,
            "invariant_content_origins": {"hype_scheme": {"kind": "explicit"}}}
    elif case == "adopted_effects":
        d.project.metadata_json = {**d.project.metadata_json, "adopted_effect_configuration": {
            "reader_promise": "A legitimate adopted effect must not disappear."}}
    elif case == "explicit_empty_pov":
        d.project.metadata_json = {**d.project.metadata_json, "creation_intent_contract": {
            "field_sources": {"pov": "explicit"}, "pov": None}}
    else:
        d.session.add(db.StyleGuideModel(project_id=d.project.id, pov_type="second-person", tense="present"))
    if case != "dirty_project":
        await d.session.commit()
    with pytest.raises(ValueError):
        await initialize(d)
    if case == "dirty_project":
        assert d.project.title == "Unsaved author title"
    assert d.project.invariants_json is None
