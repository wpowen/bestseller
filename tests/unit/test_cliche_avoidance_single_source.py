"""Default cliché references grant neither prompt nor elimination authority.

The legacy builder arguments remain compatible, while the inventory is kept
for post-generation audit. Actual user constraints use their existing inputs.
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
        assert block == ""

    def test_reference_library_does_not_impose_a_substitute_opening(self) -> None:
        """Removing a ban must not replace it with a fixed living-protagonist rule."""

        block = ct.render_cliche_avoidance_block(_banned())
        assert "在世主角" not in block and "原创开局约束" not in block
        assert block == ""

    def test_stays_compact(self) -> None:
        """Dumping 20+ phrases would itself become a death-motif menu."""

        block = ct.render_cliche_avoidance_block(_banned())
        assert len(block) < 400


class TestReachesBothGenerators:
    def test_kernel_generator_is_unchanged_by_default_references(self) -> None:
        observed = ct._build_engine_kernel_messages(
            genre="玄幻",
            sub_genre="玄幻",
            lane="action-progression",
            chapter_count=100,
            banned=_banned(),
        )
        baseline = ct._build_engine_kernel_messages(
            genre="玄幻", sub_genre="玄幻", lane="action-progression", chapter_count=100,
            banned=(),
        )
        assert observed == baseline
        assert "原创开局约束" not in observed[1]

    def test_hook_generator_is_unchanged_by_default_references(self) -> None:
        observed = ct._build_hook_from_engine_messages(
            genre="玄幻",
            sub_genre="玄幻",
            kernel={"protagonist_identity": "少年"},
            banned=_banned(),
        )
        baseline = ct._build_hook_from_engine_messages(
            genre="玄幻", sub_genre="玄幻", kernel={"protagonist_identity": "少年"},
            banned=(),
        )
        assert observed == baseline
        assert "原创开局约束" not in observed[1]

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
    """Legacy call compatibility remains; the renderer itself grants no authority."""

    def test_kernel_and_hook_call_sites_thread_banned(self) -> None:
        source = inspect.getsource(ct.run_concept_tournament)
        # Existing builder signatures/call sites stay compatible with the audit inventory.
        assert source.count("banned=banned,") >= 3

    def test_ban_list_is_the_same_one_the_judge_uses(self) -> None:
        """Audit matching still resolves the original inventory from a single source."""

        source = inspect.getsource(ct.run_concept_tournament)
        assert "banned = resolve_banned_cliches" in source
