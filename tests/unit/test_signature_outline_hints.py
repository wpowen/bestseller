"""R25 — derive signature-mandate targets from the DB chapter outline."""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from bestseller.services.signature_outline_hints import load_chapter_outline_hints

pytestmark = pytest.mark.unit


class _FakeScalarsSession:
    """Returns canned result lists for successive ``scalars`` calls."""

    def __init__(self, results: list[list[object]]) -> None:
        self._results = list(results)

    async def scalars(self, stmt: object) -> list[object]:
        return self._results.pop(0)


def _chapter(number: int, *, title: str = "", goal: str = "") -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid4(),
        chapter_number=number,
        title=title,
        chapter_goal=goal,
    )


def _scene(chapter_id: object, metadata: dict | None) -> SimpleNamespace:
    return SimpleNamespace(chapter_id=chapter_id, metadata_json=metadata)


@pytest.mark.asyncio
async def test_load_hints_builds_title_goal_and_signature_images() -> None:
    ch1 = _chapter(1, title="镜中无人", goal="主角发现倒影缺失")
    ch2 = _chapter(2, title="", goal="第二章目标")
    session = _FakeScalarsSession(
        [
            [ch1, ch2],
            [
                _scene(ch1.id, {"signature_image": "铜镜里缺失的倒影"}),
                _scene(
                    ch1.id,
                    {"methodology_contract": {"signature_image": "义庄的长明灯"}},
                ),
                _scene(ch2.id, {}),
            ],
        ]
    )

    hints = await load_chapter_outline_hints(session, uuid4())

    assert hints[1] == {
        "title": "镜中无人",
        "goal": "主角发现倒影缺失",
        "signature_images": ["铜镜里缺失的倒影", "义庄的长明灯"],
    }
    assert hints[2] == {"goal": "第二章目标"}


@pytest.mark.asyncio
async def test_load_hints_returns_empty_when_no_chapters() -> None:
    session = _FakeScalarsSession([[]])

    assert await load_chapter_outline_hints(session, uuid4()) == {}


@pytest.mark.asyncio
async def test_load_hints_dedupes_images_and_drops_empty_chapters() -> None:
    ch1 = _chapter(1, title="", goal="")
    ch2 = _chapter(2, title="有目标", goal="目标")
    session = _FakeScalarsSession(
        [
            [ch1, ch2],
            [
                _scene(ch2.id, {"signature_image": "重复意象"}),
                _scene(ch2.id, {"signature_image": "重复意象"}),
                _scene(uuid4(), {"signature_image": "孤儿场景"}),
            ],
        ]
    )

    hints = await load_chapter_outline_hints(session, uuid4())

    # ch1 contributed nothing concrete → dropped entirely
    assert 1 not in hints
    assert hints[2]["signature_images"] == ["重复意象"]
