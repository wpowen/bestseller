from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from bestseller.services.ranking_capability_profile import (
    apply_ranking_capability_profile_to_context,
    build_ranking_capability_profile_block,
    load_ranking_capability_profile_text,
)

pytestmark = pytest.mark.unit


def test_ranking_profile_prefers_project_metadata_over_output_file(tmp_path: Path) -> None:
    profile_path = tmp_path / "book-a" / "story-bible" / "ranking-capability-profile.md"
    profile_path.parent.mkdir(parents=True)
    profile_path.write_text("disk profile should not win", encoding="utf-8")

    text = load_ranking_capability_profile_text(
        project_slug="book-a",
        project_metadata={"ranking_capability_profile_block": "metadata profile wins"},
        output_base_dir=tmp_path,
    )

    assert text == "metadata profile wins"


def test_ranking_profile_loads_output_file_for_recovered_tasks(tmp_path: Path) -> None:
    profile_path = tmp_path / "book-a" / "story-bible" / "ranking-capability-profile.md"
    profile_path.parent.mkdir(parents=True)
    profile_path.write_text(
        "# 榜单级能力 Profile\n\n- 固定入口: 港口秘境。",
        encoding="utf-8",
    )

    block = build_ranking_capability_profile_block(
        project_slug="book-a",
        output_base_dir=tmp_path,
    )

    assert "【榜单级能力 Profile】" in block
    assert "港口秘境" in block
    assert "正文不得复述" in block


def test_apply_ranking_profile_to_context_is_idempotent(tmp_path: Path) -> None:
    profile_path = tmp_path / "book-a" / "story-bible" / "ranking-capability-profile.md"
    profile_path.parent.mkdir(parents=True)
    profile_path.write_text("first profile", encoding="utf-8")
    context = SimpleNamespace(ranking_capability_profile_block=None)

    assert apply_ranking_capability_profile_to_context(
        context,
        project_slug="book-a",
        output_base_dir=tmp_path,
    )
    assert "first profile" in context.ranking_capability_profile_block

    profile_path.write_text("second profile", encoding="utf-8")
    assert not apply_ranking_capability_profile_to_context(
        context,
        project_slug="book-a",
        output_base_dir=tmp_path,
    )
    assert "second profile" not in context.ranking_capability_profile_block
