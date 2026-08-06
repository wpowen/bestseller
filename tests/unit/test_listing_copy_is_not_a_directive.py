"""The listing subtitle must not be the order we gave the generator.

``bundle.reader_promise`` feeds the listing ``subtitle``, ``short_intro`` and
``promo_copy``. Its first source was
``preset.writing_profile_overrides["market"]["reader_promise"]`` — which holds a
directive aimed at the writer:

    开篇快速亮出主角差异化优势、当前利益、即时危险和连载钩子，持续维持强追读。

One field name doing two jobs. Measured across the shipped preset catalogue,
13 of 62 genres (21%) put an instruction or a piece of trade jargon into the
shop window. This module pins that number at zero.
"""

from __future__ import annotations

import pytest

from bestseller.services.concept_lab import (
    build_concept_lab_catalog,
    concept_lab_listing_overrides,
)
from bestseller.services.copy_flavor import detect_copy_flavor, pick_reader_facing
from bestseller.services.genre_creativity import get_genre_creativity_pack
from bestseller.services.hype_engine import hype_scheme_from_preset_overrides
from bestseller.services.writing_presets import list_genre_presets

pytestmark = pytest.mark.unit


_REAL_DIRECTIVE = "开篇快速亮出主角差异化优势、当前利益、即时危险和连载钩子，持续维持强追读。"
_REAL_COPY = "少年被灵根碑判了死路，却盯上刚欺负过他的师兄——打赢就拿走对方的功法。"


class TestPromiseSelection:
    def test_a_directive_loses_to_real_copy(self) -> None:
        assert pick_reader_facing(_REAL_DIRECTIVE, _REAL_COPY) == _REAL_COPY

    def test_preference_order_is_otherwise_untouched(self) -> None:
        """Clean first candidate still wins — this is a filter, not a reorder."""

        first = "他捡到一块会说话的石头，石头只肯替他杀人。"
        assert pick_reader_facing(first, _REAL_COPY) == first

    def test_all_directives_degrades_to_the_most_copy_like_source(self) -> None:
        """Never ship an empty subtitle.

        Candidates are ordered directive-first, copy-last, so when nothing is
        clean the last one is the least bad — that is the old behaviour, not a
        blank field.
        """

        result = pick_reader_facing(_REAL_DIRECTIVE, "每章必须推进线索，不能只靠氛围空转。")
        assert result == "每章必须推进线索，不能只靠氛围空转。"

    @pytest.mark.parametrize("empty", [(), ("",), ("", "   ")])
    def test_no_candidates_yields_empty(self, empty: tuple[str, ...]) -> None:
        assert pick_reader_facing(*empty) == ""


class TestShippedCatalogue:
    def test_no_genre_puts_a_directive_in_the_shop_window(self) -> None:
        offenders: list[tuple[str, float, str]] = []
        for preset in list_genre_presets():
            try:
                catalog = build_concept_lab_catalog(preset.key, count=1)
            except Exception:  # pragma: no cover - genre without a creativity pack
                continue
            for bundle in catalog.bundles[:1]:
                overrides = concept_lab_listing_overrides(bundle.model_dump(mode="json"))
                for candidate in overrides.get("title_candidates") or []:
                    report = detect_copy_flavor(candidate.get("subtitle"))
                    if not report.clean:
                        offenders.append((preset.key, report.score, str(candidate.get("subtitle"))))

        assert not offenders, f"上架副标题仍在说指令/黑话: {offenders[:5]}"

    def test_the_defect_was_real_before_the_fix(self) -> None:
        """Guards the fix by re-running the expression it replaced.

        Without this, a future refactor could quietly restore the old
        ``hype.reader_promise or ...`` chain and the test above would still be
        green only because the presets happened to change.
        """

        dirty = 0
        total = 0
        for preset in list_genre_presets():
            try:
                direction = get_genre_creativity_pack(preset.key).directions[0]
            except Exception:  # pragma: no cover - genre without a creativity pack
                continue
            hype = hype_scheme_from_preset_overrides(preset.writing_profile_overrides)
            old = hype.reader_promise or preset.trend_summary or direction.logline
            if not old:
                continue
            total += 1
            if not detect_copy_flavor(old).clean:
                dirty += 1

        assert total > 0
        assert dirty > 0, (
            "旧表达式已经不脏了——要么预设被改过，要么这个修复不再有对象，"
            "两种情况都该重新确认 _reader_facing_promise 是否还有存在意义"
        )
