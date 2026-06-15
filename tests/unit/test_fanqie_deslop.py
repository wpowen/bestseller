"""Whole-piece 去AI味 pass wired into the short-story pipeline.

Short-story segments already get cinematic_pov + gate + deslop per-segment via
``run_chapter_pipeline``; this asserts the *assembled* piece gets one more
去AI味 pass at finalize (cross-segment 车轱辘 / 句号变体 / finalize residue),
soft and non-blocking.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

import bestseller.services.fanqie_short_pipeline as fsp

pytestmark = pytest.mark.unit

_PROJECT = SimpleNamespace(slug="t-short", id="pid", language="zh-CN")

# Discourse tells that trip needs_deslop_revise (info_narration + 不是X而是Y).
_DIRTY = (
    "那是他师父留下的旧伤。没人看见他三年来每夜的苦练。"
    "这不是一柄普通的剑，而是斩断因果的凶器。他翻开旧册。废种动了。不是发芽。是皮壳先裂开。"
)
_CLEAN = "他翻开旧册，指腹压在卷边那道折痕上，压了三年，折痕里还嵌着干墨。"


def _run(text: str) -> str:
    return asyncio.run(
        fsp._deslop_whole_short_story(None, object(), text, project=_PROJECT, progress=None)
    )


def test_dirty_short_story_gets_deslopped(monkeypatch) -> None:
    calls = []

    async def fake_revise(_s, _set, *, content, **kw):
        calls.append(content)
        return _CLEAN

    monkeypatch.setattr(
        "bestseller.services.deslop_revise.revise_prose_deslop", fake_revise
    )
    out = _run(_DIRTY)
    assert out == _CLEAN
    assert len(calls) == 1


def test_clean_short_story_skips_revise(monkeypatch) -> None:
    calls = []

    async def fake_revise(_s, _set, *, content, **kw):
        calls.append(content)
        return "SHOULD NOT BE USED"

    monkeypatch.setattr(
        "bestseller.services.deslop_revise.revise_prose_deslop", fake_revise
    )
    clean = "他翻开旧册，指腹在卷角那道折痕上停了停，借着灯影把封皮压平，又抬眼看了看门口。"
    out = _run(clean)
    assert out == clean
    assert calls == []  # gate clean → no rewrite


def test_revise_failure_is_non_fatal(monkeypatch) -> None:
    async def boom(*a, **k):
        raise RuntimeError("model down")

    monkeypatch.setattr("bestseller.services.deslop_revise.revise_prose_deslop", boom)
    out = _run(_DIRTY)
    assert out == _DIRTY  # keeps original, never blocks publication
