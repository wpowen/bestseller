"""Effective audit actions preserve evidence and real hard constraints."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace

import pytest
from sqlalchemy import JSON, MetaData
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.schema import CreateTable
from test_first_candidate_working_pointer import db as db

from bestseller.infra.db.models import ChapterModel, SceneDraftVersionModel
from bestseller.services import deterministic_post_write_audit as audit_module
from bestseller.services import drafts
from bestseller.services.deterministic_audit_actions import (
    effective_deterministic_audit_blockers,
    filter_inactive_deterministic_audit_codes,
)
from bestseller.services.deterministic_post_write_audit import (
    DeterministicAuditFinding,
    DeterministicAuditReport,
)
from bestseller.services.quality_gates_config import get_quality_gates_config
from bestseller.services.write_gate import GateConfig
from bestseller.settings import load_settings


def _report(code: str, *, passed: bool = False) -> DeterministicAuditReport:
    return DeterministicAuditReport(
        2,
        (
            DeterministicAuditFinding(
                code,
                "high",
                "literal observed span",
                1,
                1,
                "A proposed repair, not author authority.",
            ),
        ),
        passed,
        {code: 1},
    )


@pytest.mark.parametrize("serialized", [False, True])
@pytest.mark.parametrize(
    "code",
    [
        "OPENING_PRESSURE_THIN",
        "ENDING_HOOK_MISSING",
        "SIGNATURE_IMAGE_MISSING",
        "CALLBACK_OBLIGATION_MISSING",
    ],
)
def test_raw_high_failure_obeys_effective_action_and_preserves_original(serialized, code):
    report = _report(code)
    value = report.to_dict() if serialized else report
    before = deepcopy(value)
    assert (
        effective_deterministic_audit_blockers(
            value,
            settings=GateConfig(mode_by_violation={}, default="audit_only"),
            chapter_number=2,
        )
        == ()
    )
    blocked = effective_deterministic_audit_blockers(
        value,
        settings=GateConfig(mode_by_violation={code: "block"}, default="audit_only"),
        chapter_number=2,
    )
    original = value["findings"][0] if serialized else report.findings[0]
    assert blocked == (original,) and blocked[0] is original
    assert value == before
    assert (value["passed"] if serialized else value.passed) is False


@pytest.mark.parametrize(
    "code",
    [
        "FORBIDDEN_TERM_HIT",
        "CANON_FORBIDDEN_TERM",
        "DEPRECATED_ENTITY_HIT",
        "PARAGRAPH_DUPLICATE_PARAPHRASE",
        "SCENE_CARD_PROSE_COPIED",
        "LENGTH_OUT_OF_BAND",
        "LENGTH_UNDER",
        "LENGTH_OVER",
    ],
)
@pytest.mark.parametrize("serialized", [False, True])
def test_source_identity_duplicate_and_length_remain_hard_under_audit_default(code, serialized):
    report = _report(code)
    value = report.to_dict() if serialized else report
    assert (
        len(
            effective_deterministic_audit_blockers(
                value,
                settings=GateConfig(mode_by_violation={code: "audit_only"}),
                chapter_number=2,
            )
        )
        == 1
    )


def test_actual_registry_and_translated_executor_findings_share_one_policy():
    config = get_quality_gates_config()
    report = _report("OPENING_PRESSURE_THIN")
    assert effective_deterministic_audit_blockers(report, settings=config, chapter_number=2) == ()
    translated = {
        "findings": [
            {"code": "OPENING_PRESSURE_THIN", "severity": "critical", "actual": "same body"},
            {"code": "LENGTH_UNDER", "severity": "critical", "actual": "same body"},
        ]
    }
    result = effective_deterministic_audit_blockers(
        translated, settings=config.l6_gate, chapter_number=2
    )
    assert result == (translated["findings"][1],)


def test_drafts_metadata_uses_action_and_keeps_raw_history_and_effect_block(monkeypatch):
    monkeypatch.setattr(
        drafts,
        "get_quality_gates_config",
        lambda: GateConfig(mode_by_violation={}, default="audit_only"),
    )
    audit = _report("OPENING_PRESSURE_THIN").to_dict()
    audit["findings"].append(_report("DEPRECATED_ENTITY_HIT").to_dict()["findings"][0])
    metadata = {
        "deterministic_audit_latest": audit,
        "historical_audits": [deepcopy(audit)],
        "effect_realization_blocked": True,
        "story_effect_realization_block_codes": ["SELECTED_EFFECT"],
    }
    before = deepcopy(metadata)
    assert drafts._metadata_external_quality_codes(metadata) == (
        "DEPRECATED_ENTITY_HIT",
        "SELECTED_EFFECT",
    )
    assert metadata == before


@pytest.mark.parametrize(
    "code,explicit_block,expected",
    [
        ("OPENING_PRESSURE_THIN", False, "ok"),
        ("OPENING_PRESSURE_THIN", True, "blocked"),
        ("FORBIDDEN_TERM_HIT", False, "blocked"),
    ],
)
@pytest.mark.asyncio
@pytest.mark.parametrize("producer", ["generate", "assemble"])
async def test_actual_chapter_producer_keeps_raw_audit_but_uses_effective_action_sqlite(
    db,
    monkeypatch,
    tmp_path,
    code,
    explicit_block,
    expected,
    producer,
):
    session, project, chapter, _scene, factory = db
    raw = _report(code)
    original_raw = deepcopy(raw.to_dict())
    config = replace(
        get_quality_gates_config(),
        l6_gate=GateConfig(
            default="audit_only",
            mode_by_violation={code: "block"} if explicit_block else {},
        ),
    )
    monkeypatch.setattr(drafts, "get_quality_gates_config", lambda: config)
    monkeypatch.setattr(audit_module, "audit_chapter_prose", lambda **kw: raw)

    async def nothing(*args, **kwargs):
        return None

    async def no_duplicates(*args, **kwargs):
        return []

    async def gate(*args, **kwargs):
        return "ok"

    async def offline_writer(*args, **kwargs):
        return SimpleNamespace(
            content="观测员封好温室的记录交给接班员。",
            llm_run_id=None,
            model_name="offline-fixture",
            provider="fixture",
        )

    for name in (
        "_enrich_chapter_first_context",
        "_render_chapter_first_character_safety_block",
        "_prepare_quality_uplift_prompt_blocks",
        "_materialize_story_effect_evidence",
    ):
        monkeypatch.setattr(drafts, name, nothing)
    monkeypatch.setattr(
        drafts, "build_chapter_first_draft_prompts", lambda *a, **k: ("fixture", "fixture")
    )
    monkeypatch.setattr(drafts, "_maybe_write_chapter_prompt_trace", lambda *a, **k: None)
    monkeypatch.setattr(drafts, "_collect_post_assembly_duplicate_findings", no_duplicates)
    monkeypatch.setattr(drafts, "_evaluate_chapter_quality_gate", gate)
    monkeypatch.setattr(drafts, "complete_text", offline_writer)
    settings = load_settings(env={})
    settings.pipeline.chapter_continuity_critic_enabled = False
    settings.pipeline.commercial_strict_quality_mode = False
    settings.output.base_dir = str(tmp_path / "output")
    if producer == "generate":
        row = await drafts.generate_chapter_draft_once(
            session,
            project.slug,
            chapter.chapter_number,
            settings=settings,
            context_packet=SimpleNamespace(query_text="", assigned_hype_type=None),
        )
    else:

        def create_scene_draft_table(connection):
            table = SceneDraftVersionModel.__table__.to_metadata(MetaData())
            for column in table.c:
                if isinstance(column.type, JSONB):
                    column.type = JSON()
                if column.server_default and "::jsonb" in str(column.server_default.arg):
                    column.server_default = None
            connection.execute(CreateTable(table, include_foreign_key_constraints=[]))

        async with session.bind.begin() as connection:
            await connection.run_sync(create_scene_draft_table)
        session.add(
            SceneDraftVersionModel(
                project_id=project.id,
                scene_card_id=_scene.id,
                version_no=1,
                content_md="观测员封好温室的记录交给接班员。",
                word_count=18,
                is_current=True,
                generation_params={},
            )
        )
        await session.commit()
        row = await drafts.assemble_chapter_draft(
            session,
            project.slug,
            chapter.chapter_number,
            settings=settings,
        )
    assert row.content_md
    await session.commit()
    async with factory() as reader:
        stored = await reader.get(ChapterModel, chapter.id)
        assert stored.production_state == expected
        assert stored.metadata_json["deterministic_audit_latest"] == original_raw
        assert stored.metadata_json["deterministic_audit_latest"]["passed"] is False


def test_unknown_registry_block_stays_blocking_without_source_specific_guess():
    report = {"findings": [{"code": "UNRELATED_POLICY_BLOCK", "severity": "low"}]}
    config = GateConfig(mode_by_violation={"UNRELATED_POLICY_BLOCK": "block"})
    assert effective_deterministic_audit_blockers(
        report, settings=config, chapter_number=9
    ) == tuple(report["findings"])


@pytest.mark.parametrize("severity", ["high", "critical", "block", "blocker"])
def test_unknown_raw_detector_is_conservative_until_explicitly_registered(severity):
    report = {
        "passed": False,
        "findings": [{"code": "NEW_UNREGISTERED_DETECTOR", "severity": severity}],
    }
    before = deepcopy(report)
    default = GateConfig(mode_by_violation={}, default="audit_only")
    assert effective_deterministic_audit_blockers(
        report, settings=default, chapter_number=2
    ) == tuple(report["findings"])
    assert filter_inactive_deterministic_audit_codes(
        ["NEW_UNREGISTERED_DETECTOR"],
        report,
        settings=default,
        chapter_number=2,
    ) == ("NEW_UNREGISTERED_DETECTOR",)
    explicit = GateConfig(mode_by_violation={"NEW_UNREGISTERED_DETECTOR": "audit_only"})
    assert effective_deterministic_audit_blockers(report, settings=explicit, chapter_number=2) == ()
    assert (
        filter_inactive_deterministic_audit_codes(
            ["NEW_UNREGISTERED_DETECTOR"],
            report,
            settings=explicit,
            chapter_number=2,
        )
        == ()
    )
    assert report == before


def test_flat_cache_filter_removes_only_proven_inactive_and_never_mutates_evidence():
    report = _report("OPENING_PRESSURE_THIN").to_dict()
    report["findings"].extend(
        [
            _report("FORBIDDEN_TERM_HIT").to_dict()["findings"][0],
            _report("EXPLICIT_CRAFT_BLOCK").to_dict()["findings"][0],
        ]
    )
    codes = [
        "OPENING_PRESSURE_THIN",
        "UNKNOWN_OLD_CODE",
        "LENGTH_UNDER",
        "FORBIDDEN_TERM_HIT",
        "EXPLICIT_CRAFT_BLOCK",
    ]
    original = deepcopy((codes, report))
    config = GateConfig(mode_by_violation={"EXPLICIT_CRAFT_BLOCK": "block"}, default="audit_only")
    assert filter_inactive_deterministic_audit_codes(
        codes,
        report,
        settings=config,
        chapter_number=2,
    ) == tuple(codes[1:])
    assert (codes, report) == original
    assert filter_inactive_deterministic_audit_codes(
        codes,
        None,
        settings=config,
        chapter_number=2,
    ) == tuple(codes)


def test_tiered_detector_code_stays_active_if_one_exact_finding_blocks():
    report = {
        "findings": [
            {"code": "POV_DRIFT", "severity": "warn"},
            {"code": "POV_DRIFT", "severity": "block"},
        ]
    }
    config = GateConfig(mode_by_violation={"POV_DRIFT": "audit_only"})
    assert filter_inactive_deterministic_audit_codes(
        ["POV_DRIFT"],
        report,
        settings=config,
        chapter_number=2,
    ) == ("POV_DRIFT",)


@pytest.mark.parametrize(
    "cache_key",
    ["auto_repair_last_block_codes", "quality_gate_block_codes", "production_block_code"],
)
@pytest.mark.asyncio
async def test_actual_auto_repair_does_not_resurrect_inactive_flat_cache(monkeypatch, cache_key):
    from test_chapter_auto_repair import FakeChapter, FakeProject, FakeScene, FakeSession

    report = _report("OPENING_PRESSURE_THIN").to_dict()
    cached = (
        "OPENING_PRESSURE_THIN"
        if cache_key == "production_block_code"
        else ["OPENING_PRESSURE_THIN"]
    )
    metadata = {
        cache_key: cached,
        "deterministic_audit_latest": report,
        "auto_repair_total_attempts": 17,
        "historical_audits": [deepcopy(report)],
    }
    chapter = FakeChapter(metadata_json=deepcopy(metadata))
    scene = FakeScene(chapter_id=chapter.id)
    before_scene = deepcopy(scene.metadata_json)
    monkeypatch.setattr(
        drafts,
        "get_quality_gates_config",
        lambda: replace(get_quality_gates_config(),
                        l6_gate=GateConfig(mode_by_violation={}, default="audit_only")),
    )
    monkeypatch.setattr(drafts, "_resolve_chapter_scene_rounds_budget", lambda: 0)
    monkeypatch.setattr(
        drafts, "_pipeline_settings_for_plateau", lambda: SimpleNamespace(rework_plateau_patience=0)
    )
    triggered, codes = await drafts.maybe_prepare_chapter_auto_repair(
        FakeSession(scenes=[scene]),
        project=FakeProject(id=chapter.project_id),
        chapter=chapter,
        repairable_codes=("OPENING_PRESSURE_THIN",),
    )
    assert triggered is False and codes == ()
    assert chapter.metadata_json == metadata
    assert scene.metadata_json == before_scene
