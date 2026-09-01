from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import replace
from uuid import UUID, uuid4

import pytest

from bestseller.domain.story_engine import canonical_json_hash
from bestseller.infra.db.models import (
    StoryEngineCanaryCampaignModel,
    StoryEngineReaderResponseModel,
    StoryEngineReaderStudyModel,
)
from bestseller.services.story_engine_reader_study import (
    ReaderEvidenceSource,
    StoryEngineReaderResponse,
    StoryEngineReaderStudyCellSpec,
    StoryEngineReaderStudyManifest,
    create_story_engine_reader_study,
    evaluate_story_engine_reader_study,
    finalize_story_engine_reader_study,
    record_story_engine_reader_response,
)


def _cell(
    *,
    cell_key: str,
    reading_scope: str,
    baseline_project_id: UUID | None = None,
    engine_project_id: UUID | None = None,
) -> StoryEngineReaderStudyCellSpec:
    return StoryEngineReaderStudyCellSpec(
        cell_key=cell_key,
        experiment="E1",
        genre="悬疑",
        seed="seed-1",
        reading_scope=reading_scope,
        baseline_project_id=baseline_project_id or uuid4(),
        engine_project_id=engine_project_id or uuid4(),
    )


def _manifest(*, canary_campaign_id: UUID | None = None) -> StoryEngineReaderStudyManifest:
    baseline_project_id = uuid4()
    engine_project_id = uuid4()
    return StoryEngineReaderStudyManifest(
        study_key="story-engine-reader-20260831",
        canary_campaign_id=canary_campaign_id or uuid4(),
        target_cohort="中文网文悬疑核心读者",
        blind_protocol="随机交换 A/B 位置, 受试者不可见生成方式",
        cells=(
            _cell(
                cell_key="mystery-seed-1-first-3",
                reading_scope="first_3",
                baseline_project_id=baseline_project_id,
                engine_project_id=engine_project_id,
            ),
            _cell(
                cell_key="mystery-seed-1-first-10",
                reading_scope="first_10",
                baseline_project_id=baseline_project_id,
                engine_project_id=engine_project_id,
            ),
        ),
    )


def _responses(
    manifest: StoryEngineReaderStudyManifest,
    *,
    sample_size: int = 30,
    evidence_source: ReaderEvidenceSource = ReaderEvidenceSource.LIVE,
    engine_preferences: int | None = None,
) -> tuple[StoryEngineReaderResponse, ...]:
    preferred = sample_size if engine_preferences is None else engine_preferences
    rows: list[StoryEngineReaderResponse] = []
    for cell in manifest.cells:
        for index in range(sample_size):
            rows.append(
                StoryEngineReaderResponse(
                    response_key=f"{cell.cell_key}-{index}",
                    participant_hash=canonical_json_hash(
                        {"cell": cell.cell_key, "participant": index}
                    ),
                    cell_key=cell.cell_key,
                    assigned_order=(
                        "baseline_first" if index % 2 == 0 else "engine_first"
                    ),
                    preferred_variant="engine" if index < preferred else "baseline",
                    engine_recall_accurate=index < 24,
                    baseline_recall_accurate=index < 20,
                    engine_severe_abandonment=index < 2,
                    baseline_severe_abandonment=index < 2,
                    evidence_source=evidence_source,
                )
            )
    return tuple(rows)


def _live_campaign(
    manifest: StoryEngineReaderStudyManifest,
) -> StoryEngineCanaryCampaignModel:
    canary_manifest = {
        "experiment": "E1",
        "cells": [
            {
                "genre": cell.genre,
                "seed": cell.seed,
                "legacy_project_id": str(cell.baseline_project_id),
                "engine_project_id": str(cell.engine_project_id),
            }
            for cell in manifest.cells[:1]
        ],
    }
    manifest_hash = canonical_json_hash(canary_manifest)
    return StoryEngineCanaryCampaignModel(
        id=manifest.canary_campaign_id,
        campaign_key="e1-source",
        experiment="E1",
        status="canary_validated",
        evidence_source="live",
        manifest_hash=manifest_hash,
        manifest_json=canary_manifest,
        report_json={
            "manifest_hash": manifest_hash,
            "cohort": {"canary_ready": True},
        },
    )


def test_reader_manifest_requires_first_three_and_first_ten_blind_cells() -> None:
    manifest = _manifest()

    with pytest.raises(ValueError, match="first_3 and first_10"):
        StoryEngineReaderStudyManifest(
            study_key=manifest.study_key,
            canary_campaign_id=manifest.canary_campaign_id,
            target_cohort=manifest.target_cohort,
            blind_protocol=manifest.blind_protocol,
            cells=manifest.cells[:1],
        )


def test_complete_live_reader_study_reaches_reader_validated() -> None:
    manifest = _manifest()

    report = evaluate_story_engine_reader_study(
        manifest,
        _responses(manifest),
    )

    assert report.reader_ready is True
    assert report.release_status == "PASS_LIVE_READER"
    assert report.sample_size == 60
    assert len(report.cells) == 2
    assert all(cell.sample_size == 30 for cell in report.cells)
    assert all(cell.engine_preference_wilson_lower > 0.5 for cell in report.cells)
    assert all(cell.engine_recall_rate == 0.8 for cell in report.cells)


def test_reader_study_is_fail_closed_for_low_sample_or_fixture_evidence() -> None:
    manifest = _manifest()

    undersized = evaluate_story_engine_reader_study(
        manifest,
        _responses(manifest, sample_size=29),
    )
    fixture = evaluate_story_engine_reader_study(
        manifest,
        _responses(manifest, evidence_source=ReaderEvidenceSource.FIXTURE),
    )

    assert undersized.reader_ready is False
    assert "READER_SAMPLE_SIZE_INSUFFICIENT" in undersized.blocking_codes
    assert fixture.reader_ready is False
    assert "LIVE_READER_EVIDENCE_REQUIRED" in fixture.blocking_codes
    assert fixture.release_status == "PASS_FIXTURE_BLOCKED_LIVE_READER"


def test_reader_study_blocks_when_preference_confidence_does_not_clear_half() -> None:
    manifest = _manifest()

    report = evaluate_story_engine_reader_study(
        manifest,
        _responses(manifest, engine_preferences=20),
    )

    assert report.reader_ready is False
    assert "READER_PREFERENCE_CONFIDENCE_INSUFFICIENT" in report.blocking_codes


def test_reader_study_blocks_low_recall_and_increased_abandonment() -> None:
    manifest = _manifest()
    responses = tuple(
        replace(
            response,
            engine_recall_accurate=False,
            engine_severe_abandonment=True,
            baseline_severe_abandonment=False,
        )
        for response in _responses(manifest)
    )

    report = evaluate_story_engine_reader_study(manifest, responses)

    assert "READER_RECALL_ACCURACY_INSUFFICIENT" in report.blocking_codes
    assert "READER_SEVERE_ABANDONMENT_INCREASED" in report.blocking_codes


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"response_key": ""}, "response and cell keys"),
        ({"participant_hash": "short"}, "SHA-256"),
        ({"participant_hash": "z" * 64}, "SHA-256"),
        ({"assigned_order": "unknown"}, "assigned order"),
        ({"preferred_variant": "unknown"}, "preferred variant"),
    ],
)
def test_reader_response_rejects_unusable_evidence_identity(
    changes: dict[str, object],
    message: str,
) -> None:
    manifest = _manifest()
    response = _responses(manifest)[:1][0]

    with pytest.raises(ValueError, match=message):
        replace(response, **changes)


@pytest.mark.asyncio
async def test_reader_study_creation_requires_matching_live_canary_campaign() -> None:
    manifest = _manifest()
    campaign = StoryEngineCanaryCampaignModel(
        id=manifest.canary_campaign_id,
        campaign_key="e1-source",
        experiment="E1",
        status="fixture_validated",
        evidence_source="fixture",
        manifest_hash="a" * 64,
        manifest_json={"campaign_key": "e1-source"},
        report_json={"cohort": {"canary_ready": False}},
    )

    class _Session:
        async def get(self, _model: object, _identity: object) -> object:
            return campaign

    with pytest.raises(ValueError, match="live canary"):
        await create_story_engine_reader_study(
            _Session(),  # type: ignore[arg-type]
            manifest=manifest,
        )


@pytest.mark.asyncio
async def test_reader_study_creation_freezes_cells_from_live_canary() -> None:
    manifest = _manifest()
    campaign = _live_campaign(manifest)

    class _Session:
        def __init__(self) -> None:
            self.added: list[object] = []

        async def get(self, _model: object, _identity: object) -> object:
            return campaign

        async def scalar(self, _statement: object) -> None:
            return None

        def add(self, value: object) -> None:
            self.added.append(value)

        async def flush(self) -> None:
            for value in self.added:
                if getattr(value, "id", None) is None:
                    value.id = uuid4()

        @asynccontextmanager
        async def begin_nested(self):
            yield

    session = _Session()
    study = await create_story_engine_reader_study(
        session,  # type: ignore[arg-type]
        manifest=manifest,
    )

    assert study.canary_campaign_id == campaign.id
    assert study.manifest_hash == manifest.stable_hash()
    assert study.status == "planned"


@pytest.mark.asyncio
async def test_reader_study_creation_rejects_projects_not_in_source_canary() -> None:
    manifest = _manifest()
    campaign = _live_campaign(manifest)
    campaign.manifest_json["cells"][0]["engine_project_id"] = str(uuid4())
    campaign.manifest_hash = canonical_json_hash(campaign.manifest_json)
    campaign.report_json["manifest_hash"] = campaign.manifest_hash

    class _Session:
        async def get(self, _model: object, _identity: object) -> object:
            return campaign

    with pytest.raises(ValueError, match="do not match"):
        await create_story_engine_reader_study(
            _Session(),  # type: ignore[arg-type]
            manifest=manifest,
        )


@pytest.mark.asyncio
async def test_reader_response_record_is_idempotent_and_marks_collecting() -> None:
    manifest = _manifest()
    study = StoryEngineReaderStudyModel(
        id=uuid4(),
        study_key=manifest.study_key,
        canary_campaign_id=manifest.canary_campaign_id,
        status="planned",
        evidence_source="pending",
        manifest_hash=manifest.stable_hash(),
        manifest_json=manifest.to_mapping(),
        report_json={},
    )
    response = _responses(manifest)[:1][0]

    class _Session:
        def __init__(self) -> None:
            self.existing: StoryEngineReaderResponseModel | None = None

        async def scalar(self, _statement: object) -> object | None:
            return self.existing

        def add(self, value: StoryEngineReaderResponseModel) -> None:
            self.existing = value

        async def flush(self) -> None:
            if self.existing is not None and self.existing.id is None:
                self.existing.id = uuid4()

    session = _Session()
    created = await record_story_engine_reader_response(
        session,  # type: ignore[arg-type]
        study=study,
        manifest=manifest,
        response=response,
    )
    reused = await record_story_engine_reader_response(
        session,  # type: ignore[arg-type]
        study=study,
        manifest=manifest,
        response=response,
    )

    assert reused is created
    assert created.cell_key == response.cell_key
    assert study.status == "collecting"


@pytest.mark.asyncio
async def test_reader_study_finalization_persists_evidence_hash_and_status() -> None:
    manifest = _manifest()
    study = StoryEngineReaderStudyModel(
        id=uuid4(),
        study_key=manifest.study_key,
        canary_campaign_id=manifest.canary_campaign_id,
        status="collecting",
        evidence_source="pending",
        manifest_hash=manifest.stable_hash(),
        manifest_json=manifest.to_mapping(),
        report_json={},
    )
    rows = [
        StoryEngineReaderResponseModel(
            id=uuid4(),
            study_id=study.id,
            **response.to_mapping(),
        )
        for response in _responses(manifest)
    ]

    class _Rows:
        def all(self) -> list[StoryEngineReaderResponseModel]:
            return rows

    class _Session:
        async def scalars(self, _statement: object) -> _Rows:
            return _Rows()

        async def flush(self) -> None:
            return None

    result = await finalize_story_engine_reader_study(
        _Session(),  # type: ignore[arg-type]
        study=study,
        manifest=manifest,
    )

    assert result is study
    assert study.status == "reader_validated"
    assert study.evidence_source == "live"
    assert len(study.report_json["response_evidence_hash"]) == 64
    assert study.report_json["report"]["reader_ready"] is True
