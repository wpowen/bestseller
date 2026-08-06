"""Guard: the cliché ban list must reach the generator, not only the judge.

Root cause (2026-07-22, book creation blocked): the judge eliminates any
candidate matching ``cliche_seeds`` *before* scoring, but the production
``engine_first`` generators never received that list — ``_build_native_candidate
_messages`` ``del``s it, and the kernel/hook builders were never given it. On a
cold start with no premise the model regressed to the genre's most worn openings
(师父坟前 / 遗物 / 借尸还魂 / 替死人还债), which is exactly what the seed list
bans, so every candidate was KO'd and the tournament produced no champion. The
blurb then scored below the appeal bar and the whole conception failed.

Same 生成端↔判官端不同源 shape as plain_language and the anti-AI discipline.
"""

from __future__ import annotations

import inspect

import pytest

from bestseller.services import concept_tournament as ct

pytestmark = pytest.mark.unit


def _banned() -> tuple[str, ...]:
    cfg = ct.load_concept_tournament_config()
    return ct.resolve_banned_cliches("玄幻", "玄幻", cfg)


class TestAvoidanceBlock:
    def test_empty_ban_list_injects_nothing(self) -> None:
        assert ct.render_cliche_avoidance_block(()) == ""

    def test_never_quotes_the_ban_corpus(self) -> None:
        block = ct.render_cliche_avoidance_block(_banned())
        assert all(item not in block for item in _banned())
        assert "禁用文本不进入" in block

    def test_frames_as_substitution_not_bare_enumeration(self) -> None:
        """Bare prohibitions prime what they forbid; the block must redirect."""

        block = ct.render_cliche_avoidance_block(_banned())
        assert "在世主角" in block

    def test_stays_compact(self) -> None:
        """Dumping 20+ phrases would itself become a death-motif menu."""

        block = ct.render_cliche_avoidance_block(_banned())
        assert len(block) < 400


class TestReachesBothGenerators:
    def test_kernel_generator_carries_the_avoidance(self) -> None:
        _system, user = ct._build_engine_kernel_messages(
            genre="玄幻",
            sub_genre="玄幻",
            lane="action-progression",
            chapter_count=100,
            banned=_banned(),
        )
        assert "原创开局约束" in user

    def test_hook_generator_carries_the_avoidance(self) -> None:
        _system, user = ct._build_hook_from_engine_messages(
            genre="玄幻",
            sub_genre="玄幻",
            kernel={"protagonist_identity": "少年"},
            banned=_banned(),
        )
        assert "原创开局约束" in user

    def test_generators_omit_it_without_a_ban_list(self) -> None:
        """No-op guard: a caller that passes no ban list gets no block."""

        _s1, k = ct._build_engine_kernel_messages(
            genre="玄幻", sub_genre="玄幻", lane="action-progression", chapter_count=100
        )
        _s2, h = ct._build_hook_from_engine_messages(
            genre="玄幻", sub_genre="玄幻", kernel={"protagonist_identity": "少年"}
        )
        assert "原创开局约束" not in k
        assert "原创开局约束" not in h


class TestProductionCallSitesPassBanned:
    """A generator that accepts a ban list nobody passes is the same bug."""

    def test_kernel_and_hook_call_sites_thread_banned(self) -> None:
        source = inspect.getsource(ct.run_concept_tournament)
        # Both engine-first generator invocations must forward the resolved list.
        assert source.count("banned=banned,") >= 3

    def test_ban_list_is_the_same_one_the_judge_uses(self) -> None:
        """The generator and judge must share one resolved list, or the block
        drifts from what actually eliminates candidates."""

        source = inspect.getsource(ct.run_concept_tournament)
        assert "banned = resolve_banned_cliches" in source
