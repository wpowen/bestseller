"""Guard: the reader channel signal resolves, but NO persona sheet enters prompts.

History: 2026-07-22 added a reader-persona block to outline prompts so the
channel reached the outline. 2026-08-01 product ruling reversed the mechanism:
hardcoded persona tables must not exist in prompts — the channel is carried by
the user's frozen contract (audience/tone), and genre fidelity is enforced by
output-side detectors, not injected reader sheets.
"""

from __future__ import annotations

import inspect

from types import SimpleNamespace

import pytest

from bestseller.services import planner
from bestseller.services.planner import _planner_channel_key

pytestmark = pytest.mark.unit


def _project(*, channel: str | None, genre: str = "玄幻", audience: str | None = None):
    metadata: dict = {}
    if channel is not None:
        metadata["genre_intent_contract"] = {"channel_key": channel}
    return SimpleNamespace(
        genre=genre, sub_genre=genre, audience=audience, metadata_json=metadata
    )


class TestChannelResolution:
    def test_reads_channel_from_the_frozen_contract(self) -> None:
        assert _planner_channel_key(_project(channel="male")) == "male"

    def test_falls_back_to_project_audience_column(self) -> None:
        assert _planner_channel_key(_project(channel=None, audience="男频")) == "男频"

    def test_none_when_no_signal(self) -> None:
        assert _planner_channel_key(_project(channel=None)) is None


class TestPersonaSheetIsGone:
    def test_persona_block_function_is_deleted(self) -> None:
        assert not hasattr(planner, "_planner_reader_persona_block")

    def test_outline_injection_line_carries_no_persona_sheet(self) -> None:
        source = inspect.getsource(planner)
        assert "目标读者画像" not in source
        assert "resolve_persona(" not in source
