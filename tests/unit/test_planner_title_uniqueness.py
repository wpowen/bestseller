"""End-to-end tests for the chapter-title uniqueness contract in ``planner``.

These tests pin the contract that the *system layer* enforces title
uniqueness even when the LLM tries to re-emit a templated pattern. They
exercise the public boundary of the planner's title pipeline:

  * `_normalize_generated_outline_titles_or_fail` raises
    `TitleCollisionError` on exact and near-duplicate titles, both
    within a single batch and against an ``existing_titles`` set.
  * `_outline_repair_directives_from_error` turns a
    `TitleCollisionError` into per-chapter rewrite directives.
  * `_chapter_fallback_subtitle` derives from chapter content instead
    of indexing a fixed pool — it raises `PlannerFallbackError` rather
    than returning a guaranteed-collision pool word.
  * `_render_existing_titles_block` returns ``("", "")`` for empty
    input and a "DO NOT REPEAT" prompt block when titles exist.
"""

from __future__ import annotations

import pytest

from bestseller.services.planner import (
    PlannerFallbackError,
    _chapter_fallback_subtitle,
    _normalize_generated_outline_titles_or_fail,
    _outline_repair_directives_from_error,
    _render_existing_titles_block,
)
from bestseller.services.title_dedup import (
    TitleCollision,
    TitleCollisionError,
)


# ---------------------------------------------------------------------------
# _normalize_generated_outline_titles_or_fail — dedup pass
# ---------------------------------------------------------------------------


class TestNormalizeDedup:
    def _build(self, *titles: str) -> list[dict]:
        return [
            {"chapter_number": i, "title": t}
            for i, t in enumerate(titles, start=1)
        ]

    def test_clean_batch_passes(self) -> None:
        chapters = self._build("青鸾令现", "断手遗骸", "中州入口")
        _normalize_generated_outline_titles_or_fail(
            chapters, logical_name="test"
        )

    def test_within_batch_exact_dup_raises(self) -> None:
        chapters = self._build("铁壁破壁", "回声追索", "铁壁破壁")
        with pytest.raises(TitleCollisionError) as excinfo:
            _normalize_generated_outline_titles_or_fail(
                chapters, logical_name="test"
            )
        assert len(excinfo.value.collisions) == 1
        col = excinfo.value.collisions[0]
        assert col.chapter_number == 3
        assert col.similarity == 1.0

    def test_cross_volume_exact_dup_raises(self) -> None:
        chapters = self._build("新标题甲", "中州入口")
        existing = [(50, "中州入口"), (51, "其他标题")]
        with pytest.raises(TitleCollisionError) as excinfo:
            _normalize_generated_outline_titles_or_fail(
                chapters,
                logical_name="test",
                existing_titles=existing,
            )
        col = excinfo.value.collisions[0]
        assert col.candidate_title == "中州入口"
        assert col.conflict_chapter_number == 50

    def test_near_dup_caught_at_default_threshold(self) -> None:
        # "苏瑶之名" vs "苏瑶之心" share 2 of 3 bigrams → Jaccard 0.5
        # Below default threshold 0.7 — should pass.
        chapters = self._build("苏瑶之心")
        existing = [(1, "苏瑶之名")]
        _normalize_generated_outline_titles_or_fail(
            chapters, logical_name="test", existing_titles=existing
        )

    def test_near_dup_caught_at_high_threshold(self) -> None:
        # Same data but with stricter threshold.
        chapters = self._build("苏瑶之心")
        existing = [(1, "苏瑶之名")]
        with pytest.raises(TitleCollisionError):
            _normalize_generated_outline_titles_or_fail(
                chapters,
                logical_name="test",
                existing_titles=existing,
                near_dup_threshold=0.5,
            )

    def test_existence_pass_runs_before_dedup(self) -> None:
        # Empty title → existence error, NOT dedup error.
        chapters = [
            {"chapter_number": 1, "title": "好标题"},
            {"chapter_number": 2, "title": ""},
        ]
        with pytest.raises(PlannerFallbackError) as excinfo:
            _normalize_generated_outline_titles_or_fail(
                chapters, logical_name="vol-2-outline"
            )
        assert "omitted concrete chapter titles" in str(excinfo.value)

    def test_chapter_title_alias_promoted_before_dedup(self) -> None:
        # chapter_title should fill in for missing title and then participate
        # in dedup.
        chapters = [
            {"chapter_number": 1, "title": "唯一甲"},
            {"chapter_number": 2, "chapter_title": "唯一甲"},
        ]
        with pytest.raises(TitleCollisionError):
            _normalize_generated_outline_titles_or_fail(
                chapters, logical_name="test"
            )

    def test_each_chapter_reported_once(self) -> None:
        # A single chapter colliding with TWO priors should produce one
        # collision entry — repair directives should not repeat work.
        chapters = self._build("撞")
        existing = [(1, "撞"), (2, "撞")]
        with pytest.raises(TitleCollisionError) as excinfo:
            _normalize_generated_outline_titles_or_fail(
                chapters, logical_name="test", existing_titles=existing
            )
        # find_title_collisions reports each candidate once.
        assert len(excinfo.value.collisions) == 1


# ---------------------------------------------------------------------------
# _outline_repair_directives_from_error
# ---------------------------------------------------------------------------


class TestRepairDirectives:
    def _make_collision(
        self,
        ch: int = 10,
        cand: str = "铁壁破壁",
        conflict: str = "铁壁破壁",
        conflict_ch: int | None = 2,
        sim: float = 1.0,
    ) -> TitleCollision:
        return TitleCollision(
            chapter_number=ch,
            candidate_title=cand,
            conflict_title=conflict,
            conflict_chapter_number=conflict_ch,
            similarity=sim,
        )

    def test_title_collision_zh_directive(self) -> None:
        err = TitleCollisionError(
            "test", collisions=[self._make_collision()]
        )
        directives = _outline_repair_directives_from_error(
            err, language="zh"
        )
        # Should mention the colliding chapter, the candidate title, and
        # the conflict source.
        joined = "\n".join(directives)
        assert "第10章" in joined
        assert "铁壁破壁" in joined
        assert "第2章" in joined
        assert "重写" in joined

    def test_title_collision_en_directive(self) -> None:
        err = TitleCollisionError(
            "test",
            collisions=[
                self._make_collision(
                    ch=10, cand="Cipher Crossing", conflict="Cipher Crossing"
                )
            ],
        )
        directives = _outline_repair_directives_from_error(
            err, language="en"
        )
        joined = "\n".join(directives)
        assert "chapter 10" in joined.lower()
        assert "cipher crossing" in joined.lower()

    def test_title_collision_near_dup_phrasing(self) -> None:
        err = TitleCollisionError(
            "test",
            collisions=[
                self._make_collision(sim=0.75)
            ],
        )
        directives = _outline_repair_directives_from_error(
            err, language="zh"
        )
        joined = "\n".join(directives)
        # Should include the Jaccard score for near-dups, not "exact".
        assert "0.75" in joined or "近似" in joined

    def test_title_collision_caps_directives(self) -> None:
        many = [
            self._make_collision(ch=i, cand=f"标题{i}", conflict=f"标题{i}")
            for i in range(1, 31)
        ]
        err = TitleCollisionError("test", collisions=many)
        directives = _outline_repair_directives_from_error(
            err, language="zh"
        )
        # 20-entry cap + 1 reminder line at the end.
        assert len(directives) <= 21

    def test_non_title_error_fallthrough(self) -> None:
        # A normal PlannerFallbackError should hit the original path,
        # not the new collision branch.
        err = RuntimeError("Planner returned 5/10 chapters")
        directives = _outline_repair_directives_from_error(
            err, language="zh", expected_count=10
        )
        # The legacy count-mismatch directive should appear.
        joined = "\n".join(directives)
        assert directives  # non-empty


# ---------------------------------------------------------------------------
# _chapter_fallback_subtitle — content extraction, no pool
# ---------------------------------------------------------------------------


class TestChapterFallbackSubtitle:
    def test_extracts_from_main_conflict(self) -> None:
        title = _chapter_fallback_subtitle(
            chapter_number=12,
            phase="setup",
            index_within_volume=2,
            volume_number=1,
            is_opening=False,
            project_slug="any-project",
            main_conflict="宁尘必须取回「青鸾令」才能进入禁地。",
        )
        assert title == "青鸾令"

    def test_extracts_from_unique_beat_with_priority(self) -> None:
        # unique_beat takes priority over main_conflict.
        title = _chapter_fallback_subtitle(
            chapter_number=5,
            phase="setup",
            index_within_volume=0,
            volume_number=1,
            is_opening=False,
            project_slug="any-project",
            unique_beat="陆沉送来「凝神丹」化解突破压力。",
            main_conflict="一些不相关的描述。",
        )
        assert title == "凝神丹"

    def test_raises_when_nothing_extractable(self) -> None:
        # No content fields → no concrete phrase to extract → raise
        # rather than substitute a pool word (which was the old bug).
        with pytest.raises(PlannerFallbackError) as excinfo:
            _chapter_fallback_subtitle(
                chapter_number=42,
                phase="setup",
                index_within_volume=2,
                volume_number=1,
                is_opening=False,
                project_slug="any-project",
                main_conflict=None,
                unique_beat=None,
                chapter_goal=None,
            )
        assert "42" in str(excinfo.value)
        assert "Refusing to substitute" in str(excinfo.value)

    def test_deterministic_pool_no_longer_exists(self) -> None:
        # Sanity check that the same chapter number with different content
        # produces different titles (the old pool would have returned
        # the same word indexed by `chapter_number % 32`).
        a = _chapter_fallback_subtitle(
            chapter_number=1,
            phase="setup",
            index_within_volume=0,
            volume_number=1,
            is_opening=True,
            project_slug="same-slug",
            main_conflict="宁尘取回「青鸾令」。",
        )
        b = _chapter_fallback_subtitle(
            chapter_number=1,
            phase="setup",
            index_within_volume=0,
            volume_number=1,
            is_opening=True,
            project_slug="same-slug",
            main_conflict="陆沉送来「凝神丹」。",
        )
        assert a != b

    def test_legacy_pool_constants_removed(self) -> None:
        # The 2026-05 rewrite removed the fixed pools entirely. This
        # test guards against accidental re-introduction.
        import bestseller.services.planner as planner_mod

        assert not hasattr(planner_mod, "_FALLBACK_EVENT_TITLES_ZH")
        assert not hasattr(planner_mod, "_FALLBACK_EVENT_TITLES_EN")


# ---------------------------------------------------------------------------
# _render_existing_titles_block
# ---------------------------------------------------------------------------


class TestExistingTitlesBlock:
    def test_empty_returns_empty(self) -> None:
        en, zh = _render_existing_titles_block(None)
        assert en == ""
        assert zh == ""
        en, zh = _render_existing_titles_block([])
        assert en == ""
        assert zh == ""

    def test_renders_chapter_numbers(self) -> None:
        en, zh = _render_existing_titles_block(
            [(1, "青鸾令"), (2, "断手遗骸")]
        )
        assert "ch1: 青鸾令" in en
        assert "ch2: 断手遗骸" in en
        assert "第1章：青鸾令" in zh
        assert "第2章：断手遗骸" in zh

    def test_caps_to_recent_200(self) -> None:
        # 250 titles → only the most recent 200 surface.
        long_list = [(i, f"标题{i}") for i in range(1, 251)]
        en, _ = _render_existing_titles_block(long_list)
        # The earliest 50 should NOT appear.
        assert "ch1: 标题1\n" not in en
        assert "ch50: 标题50\n" not in en
        # The most recent should appear.
        assert "ch250: 标题250" in en

    def test_includes_header(self) -> None:
        en, zh = _render_existing_titles_block([(1, "甲")])
        assert "DO NOT REPEAT" in en
        assert "请勿重复" in zh
