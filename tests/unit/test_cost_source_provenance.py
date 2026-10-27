"""Direct creation choices retain provenance; cached style labels cannot mandate it."""

from copy import deepcopy
import inspect

import pytest

from bestseller.services import conception
from bestseller.services.creation_intent_contract import build_creation_intent_contract
from bestseller.services.genre_intent_contract import contract_from_selection
from bestseller.services.story_enhancers import (
    capture_story_enhancer_input,
    reader_contract_labels,
    resolve_cost_style,
    resolve_explicit_story_enhancers,
)


def creation(raw):
    selection, sources = capture_story_enhancer_input(raw)
    genre = contract_from_selection({"channel": "male", "genre": "xianxia"}, enhancers=selection)
    contract = build_creation_intent_contract(
        genre,
        story_enhancers=selection,
        field_sources={
            "story_enhancers": "explicit"
            if not selection.is_default() or sources["cost_style"] == "explicit"
            else "default"
        },
        hook_spec={"_story_enhancer_input_sources": sources},
    )
    return contract


@pytest.mark.parametrize("style", ["standard", "external", "minimal"])
def test_explicit_cost_survives_serialization_and_conflicting_cache(style):
    contract = creation({"cost_style": style, "effect_skills": ["comedy_engine"]})
    metadata = {
        "creation_intent_contract": contract.model_dump(mode="json"),
        "story_enhancers": {"cost_style": "minimal" if style != "minimal" else "standard"},
    }
    before = deepcopy(metadata)
    assert resolve_cost_style(metadata) == style
    assert resolve_explicit_story_enhancers(metadata).cost_style == style
    assert "轻松" in reader_contract_labels(metadata)
    assert ("不虐主角" in reader_contract_labels(metadata)) == (style == "minimal")
    assert metadata == before


@pytest.mark.parametrize(
    "raw",
    [None, {}, {"cost_style": None}, {"cost_style": ""}, {"effect_skills": ["comedy_engine"]}],
)
def test_omitted_cost_does_not_become_explicit_standard(raw):
    contract = creation(raw)
    metadata = {"creation_intent_contract": contract.model_dump(mode="json")}
    assert resolve_cost_style(metadata) == ""
    assert contract.hook_spec["_story_enhancer_input_sources"]["cost_style"] == "default"
    assert "不虐主角" not in reader_contract_labels(metadata)


@pytest.mark.parametrize("raw", ["invalid", True, 7])
def test_invalid_explicit_cost_rejected_before_any_model(raw):
    with pytest.raises(ValueError, match="cost_style"):
        capture_story_enhancer_input({"cost_style": raw})


@pytest.mark.parametrize(
    "metadata",
    [
        {
            "story_enhancers": {
                "cost_style": "minimal",
                "effect_skills": ["comedy_engine"],
                "source": "user",
            }
        },
        {
            "genre_intent_contract": {
                "source": "user",
                "explicit_enhancers": {"cost_style": "minimal"},
            }
        },
        {
            "creation_intent_contract": {
                "field_sources": {"story_enhancers": "legacy"},
                "story_enhancers": {"cost_style": "minimal"},
            }
        },
    ],
)
def test_unknown_legacy_cache_is_not_cost_or_marketing_authority(metadata):
    before = deepcopy(metadata)
    assert resolve_cost_style(metadata) == ""
    assert reader_contract_labels(metadata) == ()
    assert conception._cost_style_block_for_ctx(metadata, is_en=False) == ""
    assert metadata == before


@pytest.mark.parametrize("style", ["standard", "external", "minimal"])
def test_full_author_entry_reaches_preproject_context_and_checkpoint(style):
    contract = creation({"cost_style": style, "effect_skills": ["comedy_engine"]})
    ctx = conception._build_genre_context(
        "xianxia",
        3,
        genre_intent_contract=contract.genre_intent,
        creation_intent_contract=contract.model_dump(mode="json"),
    )
    assert ctx["creation_intent_contract"] == contract.model_dump(mode="json")
    assert resolve_cost_style(ctx) == style
    assert "轻松" in reader_contract_labels(ctx)
    block = conception._cost_style_block_for_ctx(ctx, is_en=False)
    assert bool(block) == (style != "standard")
    restored = conception._json_conception_review_state({"ctx": ctx})["ctx"]
    assert resolve_cost_style(restored) == style
    assert restored["creation_intent_contract"] == ctx["creation_intent_contract"]


@pytest.mark.parametrize(
    "raw",
    [
        None,
        {"cost_style": None},
        {"cost_style": "standard"},
        {"cost_style": "external"},
        {"cost_style": "minimal"},
    ],
)
def test_web_capture_freezes_cost_before_conception(monkeypatch, raw):
    from bestseller.web import server

    captured = {}

    def capture(_self, payload):
        captured.update(payload)
        return {"task_id": "cost-source-offline-test"}

    monkeypatch.setattr(server.WebTaskManager, "create_autowrite_task", capture)
    request = {"genre_key": "xianxia-upgrade", "chapter_count": 3}
    if raw is not None:
        request["story_enhancers"] = raw
    server.WebTaskManager().create_quickstart_task(request)
    expected = (raw or {}).get("cost_style") or ""
    assert resolve_cost_style(captured) == expected
    contract = captured["creation_intent_contract"]
    assert contract["hook_spec"]["_story_enhancer_input_sources"]["cost_style"] == (
        "explicit" if expected else "default"
    )
    assert 'creation_intent_contract.model_dump(mode="json")' in inspect.getsource(
        server.WebTaskManager._run_autowrite_worker
    )


@pytest.mark.parametrize("style", [None, "standard", "external", "minimal"])
def test_tournament_admission_uses_the_same_cost_source(monkeypatch, style):
    from types import SimpleNamespace

    from bestseller.services import concept_tournament

    contract = creation({"cost_style": style} if style is not None else None)
    ctx = {
        "creation_intent_contract": contract.model_dump(mode="json"),
        "genre_intent_contract": {"explicit_enhancers": {"cost_style": "minimal"}},
    }
    captured = {}

    def inspect_policy(_text, **kwargs):
        captured.update(kwargs)
        return []

    monkeypatch.setattr(concept_tournament, "_creation_intent_content_violations", inspect_policy)
    candidate = SimpleNamespace(to_dict=lambda: {"premise": "港口修理一座钟楼"})
    conception._tournament_candidate_pollution_reasons(
        candidate, ctx=ctx, explicit_seed="", is_last_attempt=False
    )
    assert captured["cost_style"] == (style or "")
