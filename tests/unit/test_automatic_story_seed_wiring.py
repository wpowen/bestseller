"""空白用户创意时，Story Architect 产物必须真正进入构思主链。"""

from __future__ import annotations

import inspect

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


def test_conception_feeds_the_automatic_seed_to_the_tournament() -> None:
    source = inspect.getsource(conception.run_conception_pipeline)
    call = source[source.index("run_concept_tournament(") :]
    head = call[: call.index("retry_feedback=")]

    assert "_automatic_seed_for_attempt" in head
    assert "creation_intent_block=" in head


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

    assert "_automatic_seed_for_attempt" in tournament_call
    assert "_creation_intent_prompt_block(ctx) + _automatic_seed_for_attempt" in tournament_call
