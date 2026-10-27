"""空白用户创意时，Story Architect 产物只保留审计，不污染主池。"""

from __future__ import annotations

import inspect
from types import SimpleNamespace

import pytest

from bestseller.domain.facets import StoryFacets
from bestseller.services import concept_tournament, conception

pytestmark = pytest.mark.unit


def _facets(**overrides: object) -> StoryFacets:
    data: dict[str, object] = {
        "primary_genre": "xianxia",
        "language": "zh-CN",
        "setting": "守山杂役误触残碑后，能听见护山阵法正在求救",
        "tone": "lighthearted",
        "power_system": "阵法共鸣",
        "relationship_mode": "found-family",
        "narrative_drive": "face-slap",
        "trope_tags": ("宗门成长", "身份反差"),
    }
    data.update(overrides)
    return StoryFacets(**data)


def test_story_facets_render_a_concrete_automatic_seed() -> None:
    block = conception._build_automatic_story_seed(_facets())

    assert "系统自动故事种子" in block
    assert "守山杂役误触残碑" in block
    assert "阵法共鸣" in block
    assert "face-slap" in block
    assert "lighthearted" in block
    assert "第一次不可逆选择" in block


def test_blank_setting_does_not_invent_an_automatic_seed() -> None:
    assert conception._build_automatic_story_seed(_facets(setting="")) == ""


def test_conception_does_not_feed_automatic_seed_to_the_tournament() -> None:
    source = inspect.getsource(conception.run_conception_pipeline)
    call = source[source.index("run_concept_tournament(") :]
    head = call[: call.index("retry_feedback=")]

    assert "_automatic_seed_for_attempt" not in head
    assert "creation_intent_block=_creation_intent_prompt_block(ctx)" in head
    assert "seed_concept=(\n                        _user_owned_story_seed" in head


def test_dry_tournament_does_not_bypass_the_winner_gate_with_automatic_seed() -> None:
    source = inspect.getsource(conception.run_conception_pipeline)
    start = source.index("Concept tournament produced no winner")
    dry_branch = source[start : source.index("except Exception as exc:", start)]

    assert "_has_substantive_story_seed" in dry_branch
    substantive_definition = dry_branch[
        dry_branch.index("_has_substantive_story_seed") : dry_branch.index(
            "if chapter_count", dry_branch.index("_has_substantive_story_seed")
        )
    ]
    assert "automatic_story_seed" not in substantive_definition
    assert "automatic_story_seed_retained" not in dry_branch


def test_automatic_seed_is_present_in_the_raw_idea_prompt() -> None:
    block = conception._build_automatic_story_seed(_facets())
    system, user = concept_tournament._build_raw_idea_pool_messages(
        genre="仙侠",
        sub_genre="仙侠",
        count=4,
        seed_concept=block,
        creation_intent_block=block,
    )

    assert "系统自动故事种子" in system + user
    assert "守山杂役误触残碑" in system + user
    assert "无关故事" in system


def test_automatic_story_seed_only_anchors_the_first_attempt() -> None:
    automatic = conception._automatic_story_seed_for_tournament_attempt(
        "九层试炼塔里藏着会说话的残魂",
        attempt=1,
    )
    reroll = conception._automatic_story_seed_for_tournament_attempt(
        "九层试炼塔里藏着会说话的残魂",
        attempt=2,
    )

    assert automatic
    assert reroll == ""


def test_automatic_story_seed_does_not_shrink_seedless_sampling_budget() -> None:
    source = inspect.getsource(conception.run_conception_pipeline)
    start = source.index("_seed_for_attempt = bool(")
    end = source.index("attempt_config =", start)
    budget_gate = source[start:end]

    assert "automatic_story_seed" not in budget_gate


def test_retry_prompt_cannot_reinject_the_first_attempt_automatic_seed() -> None:
    source = inspect.getsource(conception.run_conception_pipeline)
    start = source.index("for concept_attempt in range")
    end = source.index("retry_feedback=concept_retry_feedback", start)
    tournament_call = source[start:end]

    assert "_automatic_seed_for_attempt" not in tournament_call
    assert "creation_intent_block=_creation_intent_prompt_block(ctx)" in tournament_call


@pytest.mark.asyncio
async def test_zero_input_real_conception_entry_keeps_story_architect_out_of_pool(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class _StopAfterTournament(BaseException):
        pass

    async def _stop_tournament(*_args: object, **kwargs: object):
        captured.update(kwargs)
        raise _StopAfterTournament

    async def _noop(*_args: object, **_kwargs: object) -> None:
        return None

    async def _names(*_args: object, **_kwargs: object) -> list[str]:
        return []

    monkeypatch.setattr(conception, "_attach_concept_methodology", _noop)
    monkeypatch.setattr(conception, "_prefetch_market_competitors", _noop)
    monkeypatch.setattr(conception, "_attach_mechanism_dedup", _noop)
    monkeypatch.setattr(conception, "_recent_cast_names", _names)
    monkeypatch.setattr(concept_tournament, "run_concept_tournament", _stop_tournament)

    with pytest.raises(_StopAfterTournament):
        await conception.run_conception_pipeline(
            None,
            SimpleNamespace(
                hook_engine=SimpleNamespace(enabled=False),
                pipeline=SimpleNamespace(),
            ),
            genre_key="xianxia",
            chapter_count=20,
            story_facets=_facets(),
            user_hints={},
        )

    assert captured["seed_concept"] == ""
    assert "守山杂役误触残碑" not in str(captured["creation_intent_block"])
    assert "阵法共鸣" not in str(captured["creation_intent_block"])


@pytest.mark.asyncio
async def test_explicit_seed_real_conception_entry_reaches_tournament(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class _StopAfterTournament(BaseException):
        pass

    async def _stop_tournament(*_args: object, **kwargs: object):
        captured.update(kwargs)
        raise _StopAfterTournament

    async def _noop(*_args: object, **_kwargs: object) -> None:
        return None

    async def _names(*_args: object, **_kwargs: object) -> list[str]:
        return []

    async def _empty_effect_contract(*_args: object, **_kwargs: object):
        return {}, []

    monkeypatch.setattr(conception, "_attach_concept_methodology", _noop)
    monkeypatch.setattr(conception, "_prefetch_market_competitors", _noop)
    monkeypatch.setattr(conception, "_attach_mechanism_dedup", _noop)
    monkeypatch.setattr(conception, "_recent_cast_names", _names)
    monkeypatch.setattr(conception, "_llm_call_json", _empty_effect_contract)
    monkeypatch.setattr(concept_tournament, "run_concept_tournament", _stop_tournament)

    seed = "修复师听见失踪者录音，封锁档案前必须交出证据。"
    with pytest.raises(_StopAfterTournament):
        await conception.run_conception_pipeline(
            None,
            SimpleNamespace(
                hook_engine=SimpleNamespace(enabled=False),
                pipeline=SimpleNamespace(),
            ),
            genre_key="xianxia",
            chapter_count=20,
            story_facets=_facets(),
            user_hints={"concept_seed": seed},
        )

    assert captured["seed_concept"] == seed
    assert seed in str(captured["creation_intent_block"]) or seed == captured["seed_concept"]
