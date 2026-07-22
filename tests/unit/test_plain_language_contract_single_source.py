"""Guard: the plain_language contract must reach the generator, not just the judge.

Root cause (2026-07-21, book creation blocked in production): every tournament
candidate scored 4.0 on ``plain_language`` and the catastrophe floor (5.0)
killed the whole field, so conception produced no champion and the book failed
to create.

The judge was enforcing a detailed contract — no invented institution names, no
terms that need looking up, genre-common words are fine — that the production
hook generator had never been shown. It was told only "一遍就懂" while being
asked for "只有本书才有的异常事实" plus "删除抽象词，除非是场景中摸得到的物件".
The only way to satisfy all three is to invent a concrete proper noun
(缉牒队 / 引魂针), which is exactly what the judge caps at 4.

The detailed rule *did* exist — in ``_build_lean_candidate_messages``, which is
dead code under the production ``engine_first`` route. Same shape as the
anti-AI discipline before it got a single source.
"""

from __future__ import annotations

import inspect

import pytest

from bestseller.services import concept_tournament as ct

pytestmark = pytest.mark.unit


_KERNEL = {"protagonist": "游方郎中", "anomaly": "能借针换命", "goal": "活下去"}


def _hook_user(retry_feedback: str = "") -> str:
    _system, user = ct._build_hook_from_engine_messages(
        genre="武侠",
        sub_genre="江湖",
        kernel=_KERNEL,
        seed_concept="顶替死人身份",
        retry_feedback=retry_feedback,
    )
    return user


class TestContractIsSingleSourced:
    """Two copies of a contract drift; that drift is what blocked the book."""

    def test_writer_and_judge_share_the_same_core(self) -> None:
        core = ct._PLAIN_LANGUAGE_CORE
        assert core in ct.render_plain_language_writer_rule()
        assert core in ct.render_plain_language_judge_rule()

    def test_judge_prompt_renders_from_the_shared_source(self) -> None:
        """A judge prompt that re-inlines the rule can drift from the writer's."""

        source = inspect.getsource(ct._build_judge_messages)
        assert "render_plain_language_judge_rule()" in source

    def test_judge_keeps_its_scoring_instruction(self) -> None:
        """Refactoring to a shared source must not drop the 4-point cap, or the
        axis silently stops discriminating."""

        assert "不得超过4分" in ct.render_plain_language_judge_rule()


class TestProductionGeneratorSeesTheContract:
    """``engine_first`` is the production route (config/concept_tournament.yaml);
    the lean route that used to carry this rule never runs."""

    def test_production_route_is_engine_first(self) -> None:
        from bestseller.services.concept_tournament import load_concept_tournament_config

        cfg = load_concept_tournament_config()
        assert str(cfg.get("candidate_prompt_mode") or "") == "engine_first"

    def test_hook_prompt_bans_invented_proper_nouns(self) -> None:
        assert "不要为了独特而生造机构名" in _hook_user()

    def test_hook_prompt_carries_the_genre_common_word_allowance(self) -> None:
        """Without the whitelist the writer over-corrects into blandness."""

        user = _hook_user()
        assert "本来就懂的常识词不算术语" in user
        assert "报废晶圆" in user

    def test_hook_prompt_offers_a_substitution_not_only_a_ban(self) -> None:
        """Bare prohibitions prime what they forbid (2026-07-18 arena)."""

        user = _hook_user()
        assert "官府的缉捕队" in user
        assert "三根银针" in user


class TestRetryFeedbackLoopIsLive:
    """The retry loop was decorative under engine_first: the caller threaded
    ``retry_feedback`` only into the non-production branch, so a tournament
    could fail the same axis three rounds running without the generator ever
    learning why."""

    def test_builder_accepts_retry_feedback(self) -> None:
        assert "retry_feedback" in inspect.signature(
            ct._build_hook_from_engine_messages
        ).parameters

    def test_feedback_reaches_the_prompt(self) -> None:
        user = _hook_user("上一轮被拒：大白话 4.0 —— 缉牒队属生造机构名")
        assert "上一轮为什么被拒" in user
        assert "缉牒队属生造机构名" in user

    def test_no_feedback_injects_nothing(self) -> None:
        assert "上一轮为什么被拒" not in _hook_user()

    def test_production_call_site_passes_feedback(self) -> None:
        """A builder that accepts feedback nobody passes is the same bug."""

        source = inspect.getsource(ct)
        # The call site is the invocation (not the `def`), identified by the
        # keyword form the caller uses.
        call = source.index("system, user = _build_hook_from_engine_messages(")
        window = source[call : call + 500]
        assert "retry_feedback=retry_feedback" in window


class TestChannelNeutrality:
    """The shared contract must not smuggle reader-persona framing into a
    channel-less judge prompt — a paired test in test_concept_tournament.py
    treats '划走' as the marker that channel priming was injected."""

    def test_shared_core_is_channel_neutral(self) -> None:
        assert "划走" not in ct._PLAIN_LANGUAGE_CORE
        assert "划走" not in ct.render_plain_language_judge_rule()
