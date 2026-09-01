from __future__ import annotations

from contextlib import asynccontextmanager
from copy import deepcopy
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from bestseller.domain.story_engine import StoryEngineMaturity, canonical_json_hash
from bestseller.infra.db.models import StoryEngineCanaryCampaignModel
from bestseller.services.story_engine_canary import (
    CanaryEvidenceSource,
    StoryEngineCanaryCampaignManifest,
    StoryEngineCanaryCellObservation,
    StoryEngineCanaryCellSpec,
    create_story_engine_canary_campaign,
    execute_story_engine_canary_campaign,
    finalize_story_engine_e2_campaign,
)


def _receipt(chapter: int) -> dict[str, object]:
    pre_state = {"pressure": {"category": "exposure", "value": chapter - 1}}
    post_state = {"pressure": {"category": "exposure", "value": chapter}}
    return {
        "verdict": "matched",
        "blocking_codes": [],
        "chapter_number": chapter,
        "pre_state_hash": canonical_json_hash(pre_state),
        "post_state_hash": canonical_json_hash(post_state),
        "replay_passed": True,
        "receipt": {
            "observed_transitions": [
                {
                    "key": "pressure",
                    "operator": "set",
                    "before": chapter - 1,
                    "after": chapter,
                }
            ],
            "opponent_counteraction": f"对手反制-{chapter}",
            "new_obligations": [],
            "fingerprint": f"choice-{chapter}",
        },
    }


def _manifest() -> StoryEngineCanaryCampaignManifest:
    cells = tuple(
        StoryEngineCanaryCellSpec(
            genre=genre,
            seed=seed,
            legacy_project_id=uuid4(),
            engine_project_id=uuid4(),
        )
        for genre in ("玄幻", "都市", "悬疑")
        for seed in ("seed-1", "seed-2")
    )
    return StoryEngineCanaryCampaignManifest(
        campaign_key="story-engine-e1-20260831",
        experiment="E1",
        model="test-model-fixed",
        generation_unit="chapter",
        budget_tokens_per_variant=120_000,
        chapter_count=10,
        cells=cells,
    )


class _WriteSession:
    def __init__(self) -> None:
        self.scalar_results: list[object | None] = [None]
        self.added: list[object] = []
        self.flush_count = 0

    async def scalar(self, _statement: object) -> object | None:
        return self.scalar_results.pop(0)

    def add(self, value: object) -> None:
        self.added.append(value)

    async def flush(self) -> None:
        self.flush_count += 1
        for value in self.added:
            if getattr(value, "id", None) is None:
                value.id = uuid4()

    @asynccontextmanager
    async def begin_nested(self):
        yield


def test_campaign_manifest_requires_three_genres_and_two_seeds() -> None:
    manifest = _manifest()

    with pytest.raises(ValueError, match="three genres"):
        StoryEngineCanaryCampaignManifest(
            campaign_key=manifest.campaign_key,
            experiment="E1",
            model=manifest.model,
            generation_unit="chapter",
            budget_tokens_per_variant=manifest.budget_tokens_per_variant,
            chapter_count=10,
            cells=manifest.cells[:4],
        )


@pytest.mark.asyncio
async def test_campaign_create_is_idempotent_and_rejects_manifest_drift() -> None:
    manifest = _manifest()
    existing = StoryEngineCanaryCampaignModel(
        id=uuid4(),
        campaign_key=manifest.campaign_key,
        experiment="E1",
        status="planned",
        evidence_source="pending",
        manifest_hash=manifest.stable_hash(),
        manifest_json=manifest.to_mapping(),
        report_json={},
    )
    session = AsyncMock()
    session.scalar = AsyncMock(return_value=existing)

    reused = await create_story_engine_canary_campaign(session, manifest=manifest)
    assert reused is existing

    drifted = deepcopy(manifest.to_mapping())
    drifted["model"] = "different-model"
    existing.manifest_json = drifted
    with pytest.raises(ValueError, match="different manifest"):
        await create_story_engine_canary_campaign(session, manifest=manifest)


@pytest.mark.asyncio
async def test_execute_fixture_campaign_persists_report_without_canary_claim() -> None:
    manifest = _manifest()
    session = _WriteSession()
    campaign = await create_story_engine_canary_campaign(session, manifest=manifest)
    runner = AsyncMock(
        side_effect=[
            StoryEngineCanaryCellObservation(
                evidence_source=CanaryEvidenceSource.FIXTURE,
                receipts=tuple(_receipt(chapter) for chapter in range(1, 11)),
                engine_prompt_tokens=10_000,
                legacy_prompt_tokens=10_000,
                engine_hard_failures=0,
                legacy_hard_failures=0,
            )
            for _ in manifest.cells
        ]
    )

    result = await execute_story_engine_canary_campaign(
        session,
        campaign=campaign,
        manifest=manifest,
        run_cell=runner,
    )

    assert result is campaign
    assert runner.await_count == 6
    assert campaign.status == "fixture_validated"
    assert campaign.evidence_source == CanaryEvidenceSource.FIXTURE.value
    assert campaign.report_json["cohort"]["canary_ready"] is False
    assert campaign.report_json["cohort"]["maturity"] == (
        StoryEngineMaturity.SHADOW_VALIDATED.value
    )
    assert campaign.report_json["manifest_hash"] == manifest.stable_hash()


@pytest.mark.asyncio
async def test_execute_complete_live_campaign_reaches_canary_validated() -> None:
    manifest = _manifest()
    session = _WriteSession()
    campaign = await create_story_engine_canary_campaign(session, manifest=manifest)
    runner = AsyncMock(
        side_effect=[
            StoryEngineCanaryCellObservation(
                evidence_source=CanaryEvidenceSource.LIVE,
                receipts=tuple(_receipt(chapter) for chapter in range(1, 11)),
                engine_prompt_tokens=10_000,
                legacy_prompt_tokens=10_000,
                engine_hard_failures=0,
                legacy_hard_failures=0,
            )
            for _ in manifest.cells
        ]
    )

    await execute_story_engine_canary_campaign(
        session,
        campaign=campaign,
        manifest=manifest,
        run_cell=runner,
    )

    assert campaign.status == "canary_validated"
    assert campaign.evidence_source == CanaryEvidenceSource.LIVE.value
    assert campaign.report_json["cohort"]["release_status"] == "PASS_LIVE_CANARY"
    assert campaign.report_json["cohort"]["canary_ready"] is True


@pytest.mark.asyncio
async def test_execute_campaign_records_block_before_propagating_runner_failure() -> None:
    manifest = _manifest()
    session = _WriteSession()
    campaign = await create_story_engine_canary_campaign(session, manifest=manifest)
    runner = AsyncMock(side_effect=RuntimeError("provider unavailable"))

    with pytest.raises(RuntimeError, match="provider unavailable"):
        await execute_story_engine_canary_campaign(
            session,
            campaign=campaign,
            manifest=manifest,
            run_cell=runner,
        )

    assert campaign.status == "blocked"
    assert campaign.evidence_source == "unavailable"
    assert campaign.report_json["blocking_codes"] == [
        "CANARY_CELL_EXECUTION_FAILED"
    ]
    assert campaign.report_json["failed_cell"] == {
        "genre": "玄幻",
        "seed": "seed-1",
    }


@pytest.mark.asyncio
async def test_e2_campaign_cannot_run_through_e1_receipt_evaluator() -> None:
    base = _manifest()
    manifest = StoryEngineCanaryCampaignManifest(
        campaign_key="story-engine-e2-20260901",
        experiment="E2",
        model=base.model,
        generation_unit="paired",
        budget_tokens_per_variant=base.budget_tokens_per_variant,
        chapter_count=base.chapter_count,
        cells=base.cells,
    )
    session = _WriteSession()
    campaign = await create_story_engine_canary_campaign(session, manifest=manifest)

    with pytest.raises(ValueError, match="E2 generation-mode report"):
        await execute_story_engine_canary_campaign(
            session,
            campaign=campaign,
            manifest=manifest,
            run_cell=AsyncMock(),
        )

    assert campaign.status == "blocked"
    assert campaign.report_json["blocking_codes"] == [
        "E2_REQUIRES_GENERATION_MODE_REPORT"
    ]


@pytest.mark.asyncio
async def test_e2_campaign_persists_live_conclusive_generation_mode_report() -> None:
    base = _manifest()
    manifest = StoryEngineCanaryCampaignManifest(
        campaign_key="story-engine-e2-live-20260901",
        experiment="E2",
        model=base.model,
        generation_unit="paired",
        budget_tokens_per_variant=base.budget_tokens_per_variant,
        chapter_count=base.chapter_count,
        cells=base.cells,
    )
    session = _WriteSession()
    campaign = await create_story_engine_canary_campaign(session, manifest=manifest)
    report = {
        "decision": "chapter_first",
        "e2_gate": {
            "release_status": "PASS_E2_CHAPTER_FIRST",
            "recommended_mode": "chapter_first",
            "blocking_codes": [],
        },
    }

    await finalize_story_engine_e2_campaign(
        session,
        campaign=campaign,
        manifest=manifest,
        evidence_source=CanaryEvidenceSource.LIVE,
        generation_mode_report=report,
    )

    assert campaign.status == "canary_validated"
    assert campaign.evidence_source == "live"
    assert campaign.report_json["e2_gate"]["release_status"] == (
        "PASS_E2_CHAPTER_FIRST"
    )
