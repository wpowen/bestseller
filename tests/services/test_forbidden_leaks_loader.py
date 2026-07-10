from __future__ import annotations

from pathlib import Path

import pytest

from bestseller.services.forbidden_leaks_loader import load_forbidden_leaks_for_chapter

pytestmark = pytest.mark.unit


def _write_policy(project_dir: Path) -> None:
    policy_dir = project_dir / "story-bible"
    policy_dir.mkdir(parents=True)
    (policy_dir / "forbidden-leaks-policy.yaml").write_text(
        """permanent_forbidden:\n  - 林正淳\nstaged_forbidden:\n  - chapter_range: [1, 3]\n    terms: [困魂镜]\n    reason: 核心道具尚未在正文解锁\ncontextual_exceptions:\n  - context: 章末钩子最后一句\n    allowed_during_staged_block: [困魂镜]\n""",
        encoding="utf-8",
    )


def test_policy_allows_family_titles_but_blocks_core_terms(tmp_path: Path) -> None:
    _write_policy(tmp_path)
    decision = load_forbidden_leaks_for_chapter(
        tmp_path,
        chapter_number=1,
    )
    assert "困魂镜" in decision.forbidden_terms
    assert "林正淳" in decision.forbidden_terms
    assert "爷爷" not in decision.forbidden_terms
    assert "祖父" not in decision.forbidden_terms


def test_contextual_exception_removes_allowed_term(tmp_path: Path) -> None:
    _write_policy(tmp_path)
    decision = load_forbidden_leaks_for_chapter(
        tmp_path,
        chapter_number=1,
        context_tag="章末钩子最后一句",
    )
    assert "困魂镜" not in decision.forbidden_terms
    assert "困魂镜" in decision.excepted_terms
