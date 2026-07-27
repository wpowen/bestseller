"""The audience/channel anchor must survive every prompt path — repair included.

Field evidence (2026-07-24, custom-xuanhuan-1784899694): the user selected 男频
on the create form. The anchor was correctly threaded payload →
user_hints('男频') → conception ctx → tournament, and the PRIMARY kernel and
candidate prompts all carried "频道/受众：男频…频道错位即废稿". But
``_build_engine_kernel_repair_messages`` — the once-per-card JSON-structure
repair — rebuilt the kernel prompt WITHOUT forwarding ``audience_orientation``,
so any premise card that needed structural repair regenerated with no channel
anchor. llm_runs shows attempt 2 of that run made two kernel calls for its one
lane (21:31:18 + 21:31:23 — the second being the repair), and its candidate
came back female-lead for a 男频 request, which the judge then killed; the
tournament went dry and the user concluded "选项没生效".

The conception call site's own comment states the contract: "受众必须传到
内核/蒸钩/候选三层 prompt". These tests pin it for the repair layer too.
"""

from __future__ import annotations

import inspect

import pytest

from bestseller.services.concept_tournament import (
    _build_candidate_messages,
    _build_engine_kernel_messages,
    _build_engine_kernel_repair_messages,
)


pytestmark = pytest.mark.unit


def test_primary_kernel_prompt_carries_the_channel_anchor() -> None:
    system, user = _build_engine_kernel_messages(
        genre="玄幻",
        sub_genre="玄幻",
        lane="资源分配",
        chapter_count=50,
        audience_orientation="男频",
    )

    assert "男频" in system + user


def test_repair_kernel_prompt_carries_the_channel_anchor_too() -> None:
    """THE regression: structural repair must not strip the channel anchor.

    The repair docstring says "without changing the premise itself" — dropping
    the audience anchor changes something more fundamental than the premise:
    who the book is for.
    """

    system, user = _build_engine_kernel_repair_messages(
        genre="玄幻",
        sub_genre="玄幻",
        lane="资源分配",
        chapter_count=50,
        seed_concept="x",
        card={},
        missing_fields=["mechanism"],
        audience_orientation="男频",
    )

    assert "男频" in system + user


def test_candidate_prompt_still_carries_the_channel_anchor() -> None:
    system, user = _build_candidate_messages(
        genre="玄幻",
        sub_genre="玄幻",
        dimension="资源分配",
        chapter_count=50,
        banned=(),
        avoid_mechanisms_block="",
        audience_orientation="男频",
    )

    assert "男频" in system + user


def test_raw_idea_pool_prompt_carries_the_channel_anchor() -> None:
    """蒸钩 (raw-idea) layer: the third leg of the documented contract.

    The conception call site's comment requires the anchor in 内核/蒸钩/候选 —
    yet the raw-idea builders never accepted the parameter at all. These seeds
    are what every downstream layer expands; a channel-mismatched seed puts the
    kernel/candidate anchors permanently on the back foot.
    """

    from bestseller.services.concept_tournament import _build_raw_idea_pool_messages

    system, user = _build_raw_idea_pool_messages(
        genre="玄幻",
        sub_genre="玄幻",
        count=6,
        audience_orientation="男频",
    )

    assert "男频" in system + user


def test_raw_idea_rank_prompt_carries_the_channel_anchor() -> None:
    """The seed ranker must know the channel too, or it happily promotes
    channel-mismatched seeds over fitting ones."""

    from bestseller.services.concept_tournament import _build_raw_idea_rank_messages

    system, user = _build_raw_idea_rank_messages(
        genre="玄幻",
        sub_genre="玄幻",
        ideas=[("资源分配", "某个种子")],
        audience_orientation="男频",
    )

    assert "男频" in system + user


def test_raw_idea_call_sites_forward_the_anchor() -> None:
    from bestseller.services import concept_tournament

    source = inspect.getsource(concept_tournament.run_concept_tournament)

    pool_idx = source.index("_build_raw_idea_pool_messages(")
    assert "audience_orientation=audience_orientation" in source[pool_idx : pool_idx + 500]

    rank_idx = source.index("_build_raw_idea_rank_messages(")
    assert "audience_orientation=audience_orientation" in source[rank_idx : rank_idx + 500]


def test_repair_call_site_forwards_the_anchor() -> None:
    """A builder accepting the parameter is worthless if the one call site
    omits it — that exact gap shipped the bug."""

    from bestseller.services import concept_tournament

    source = inspect.getsource(concept_tournament.run_concept_tournament)
    idx = source.index("_build_engine_kernel_repair_messages(")
    call_region = source[idx : idx + 600]

    assert "audience_orientation=audience_orientation" in call_region, (
        "the repair call must forward the channel anchor it received"
    )
