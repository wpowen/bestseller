from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from uuid import uuid4

import pytest
from typer.testing import CliRunner

from bestseller.cli import main as cli
from bestseller.domain.project import ProjectCreate
from bestseller.services.creation_intent_contract import CreationIntentContract

pytestmark = pytest.mark.unit
runner = CliRunner()


@pytest.fixture
def captured_requests(monkeypatch: pytest.MonkeyPatch) -> list[ProjectCreate]:
    captured: list[ProjectCreate] = []

    @asynccontextmanager
    async def fake_session_scope(settings):
        yield object()

    async def fake_create_project(session, payload, settings):
        captured.append(payload)
        return SimpleNamespace(id=uuid4(), slug=payload.slug)

    monkeypatch.setattr(cli, "session_scope", fake_session_scope)
    monkeypatch.setattr(cli, "load_settings", lambda: object())
    monkeypatch.setattr(cli, "create_project", fake_create_project)
    return captured


def _invoke(*options: str, genre: str = "东方玄幻"):
    return runner.invoke(
        cli.app,
        ["project", "create", "cli-cost-source", "CLI source capture", genre,
         "120000", "60", *options],
    )


def _contract(payload: ProjectCreate) -> CreationIntentContract:
    return CreationIntentContract.model_validate(payload.metadata["creation_intent_contract"])


@pytest.mark.parametrize(
    ("options", "cost_style", "source"),
    [
        ((), "standard", "default"),
        (("--cost-style", "standard"), "standard", "explicit"),
        (("--cost-style", "external"), "external", "explicit"),
        (("--cost-style", "minimal"), "minimal", "explicit"),
    ],
)
def test_cli_captures_cost_only_source_before_create(
    captured_requests, options, cost_style, source,
):
    result = _invoke(*options)
    assert result.exit_code == 0, result.output
    assert len(captured_requests) == 1
    payload = captured_requests[0]
    contract = _contract(payload)
    sources = contract.hook_spec["_story_enhancer_input_sources"]
    assert contract.story_enhancers.cost_style == cost_style
    assert contract.story_enhancers.effect_skills == ()
    assert contract.genre_intent.explicit_enhancers == contract.story_enhancers
    assert sources["cost_style"] == source
    assert contract.field_sources["story_enhancers"] == source
    assert contract.field_sources["hook_spec"] == "default"
    assert contract.chapter_count == 60
    assert contract.genre_intent.genre_key == "xuanhuan"
    receipt = payload.metadata["creation_input_receipt"]
    assert receipt["contract_hash"] == contract.contract_hash()
    assert receipt["story_enhancer_field_sources"] == sources
    if cost_style in {"minimal", "external"}:
        assert payload.metadata["story_enhancers"]["cost_style"] == cost_style


def test_cli_explicit_standard_receipt_is_bound_by_canonical_hash(captured_requests):
    omitted = _invoke()
    explicit = _invoke("--cost-style", "standard")
    assert omitted.exit_code == explicit.exit_code == 0
    default_contract, explicit_contract = map(_contract, captured_requests)
    assert default_contract.story_enhancers == explicit_contract.story_enhancers
    assert default_contract.contract_hash() != explicit_contract.contract_hash()
    captured_requests[1].metadata["creation_input_receipt"] = {}
    reloaded = _contract(captured_requests[1])
    assert reloaded.hook_spec["_story_enhancer_input_sources"]["cost_style"] == "explicit"
    assert reloaded.contract_hash() == explicit_contract.contract_hash()


def test_cli_other_enhancer_does_not_claim_default_cost_was_explicit(captured_requests):
    result = _invoke("--enhancer-skills", "comedy_engine")
    assert result.exit_code == 0, result.output
    contract = _contract(captured_requests[0])
    assert contract.field_sources["story_enhancers"] == "explicit"
    assert contract.story_enhancers.effect_skills == ("comedy_engine",)
    assert contract.hook_spec["_story_enhancer_input_sources"]["cost_style"] == "default"


def test_cli_direct_none_keeps_cost_default(captured_requests):
    cli.project_create(
        slug="cli-cost-source", title="CLI source capture", genre="东方玄幻",
        target_words=120000, target_chapters=60, profile_file=None, prompt_pack=None,
        enhancer_skills=None, brainhole=False, wild_concept=False, cost_style=None,
        concept_lab=False, creativity_direction=None,
    )
    contract = _contract(captured_requests[0])
    assert contract.story_enhancers.cost_style == "standard"
    assert contract.hook_spec["_story_enhancer_input_sources"]["cost_style"] == "default"


@pytest.mark.parametrize("genre", ["personal-fable", "自定义" * 60])
def test_cli_preserves_freeform_genre_with_complete_legacy_contract(captured_requests, genre):
    result = _invoke("--cost-style", "minimal", genre=genre)
    assert result.exit_code == 0, result.output
    payload = captured_requests[0]
    contract = _contract(payload)
    assert payload.genre == genre
    assert contract.genre_intent.source == "legacy_inferred"
    assert contract.field_sources["genre_intent"] == "legacy"
    assert contract.genre_intent.genre_key == "legacy-freeform"
    assert contract.genre_intent.genre_label == genre[:120]
    assert contract.genre_intent.prompt_pack_key == "legacy-unmapped"
    assert contract.hook_spec["_story_enhancer_input_sources"]["cost_style"] == "explicit"


def test_cli_invalid_cost_rejected_before_service(captured_requests):
    result = _invoke("--cost-style", "not-a-cost-style")
    assert result.exit_code != 0
    assert "--cost-style" in result.output
    assert captured_requests == []
