from __future__ import annotations

from types import SimpleNamespace

import pytest

from bestseller.services.identity_guard import CharacterIdentity
from bestseller.services.repair import (
    _heal_chapter_gate_state_before_repair,
    _release_resolved_identity_write_safety_block,
)
from bestseller.settings import load_settings


pytestmark = pytest.mark.unit


class FakeSession:
    def __init__(self, scalar_result: object | None = None) -> None:
        self.scalar_result = scalar_result

    async def scalar(self, stmt: object) -> object | None:
        return self.scalar_result


def test_release_resolved_identity_write_safety_block_clears_stale_pronoun_block() -> None:
    chapter = SimpleNamespace(
        chapter_number=152,
        production_state="blocked",
        metadata_json={
            "blocked_by_write_safety_gate": True,
            "write_safety_block_code": "pronoun_mismatch",
            "write_safety_hint": "Kade Mercer: expected he/him, found she",
            "auto_repair_exhausted": True,
            "auto_repair_in_progress": True,
            "auto_repair_last_block_codes": ["pronoun_mismatch"],
        },
    )
    draft = SimpleNamespace(
        content_md=(
            "Kade found Mira Vance in the operations room. "
            "She didn't look up when he entered."
        )
    )

    released = _release_resolved_identity_write_safety_block(
        chapter,
        draft,
        identity_registry=[
            CharacterIdentity(name="Kade Mercer", aliases=("Kade",), gender="male", pronoun_set_en="he/him"),
            CharacterIdentity(name="Mira Vance", aliases=("Mira",), gender="female", pronoun_set_en="she/her"),
        ],
        language="en",
    )

    assert released is True
    assert chapter.production_state == "ok"
    assert "write_safety_block_code" not in chapter.metadata_json
    assert "auto_repair_exhausted" not in chapter.metadata_json
    assert chapter.metadata_json["resolved_write_safety_block"] == {
        "code": "pronoun_mismatch",
        "resolved_by": "identity_guard_revalidation",
        "previous_hint": "Kade Mercer: expected he/him, found she",
    }


def test_release_resolved_identity_write_safety_block_keeps_active_pronoun_violation() -> None:
    chapter = SimpleNamespace(
        chapter_number=152,
        production_state="blocked",
        metadata_json={
            "blocked_by_write_safety_gate": True,
            "write_safety_block_code": "pronoun_mismatch",
            "write_safety_hint": "Kade Mercer: expected he/him, found she",
        },
    )
    draft = SimpleNamespace(content_md="Kade Mercer stopped in the doorway. She opened the file.")

    released = _release_resolved_identity_write_safety_block(
        chapter,
        draft,
        identity_registry=[
            CharacterIdentity(name="Kade Mercer", aliases=("Kade",), gender="male", pronoun_set_en="he/him"),
        ],
        language="en",
    )

    assert released is False
    assert chapter.production_state == "blocked"
    assert chapter.metadata_json["write_safety_block_code"] == "pronoun_mismatch"


@pytest.mark.asyncio
async def test_heal_chapter_gate_state_releases_stale_nonblocking_quality_block() -> None:
    chapter = SimpleNamespace(
        id="chapter-441",
        chapter_number=441,
        production_state="blocked",
        metadata_json={"auto_repair_last_block_codes": ["CHAPTER_LENGTH_BLOCK_LOW"]},
    )
    draft = SimpleNamespace(content_md="current draft", word_count=2388)
    report = SimpleNamespace(blocks_write=False, report_json={"blocking_codes": []})

    still_blocked = await _heal_chapter_gate_state_before_repair(
        FakeSession(report),
        chapter,
        draft,
        project=SimpleNamespace(id="project-1"),
        settings=load_settings(env={}),
        identity_registry=[],
        language="en",
    )

    assert still_blocked is False
    assert chapter.production_state == "ok"
    assert "auto_repair_last_block_codes" not in chapter.metadata_json
    assert chapter.metadata_json["resolved_quality_gate_block"]["resolved_by"] == (
        "nonblocking_quality_report_revalidation"
    )


@pytest.mark.asyncio
async def test_heal_chapter_gate_state_standardizes_legacy_repair_audit_length_block() -> None:
    chapter = SimpleNamespace(
        id="chapter-215",
        chapter_number=215,
        production_state="blocked",
        metadata_json={
            "blocked_by_repair_audit": "current_chapter_length_out_of_range",
            "repair_audit_min_words": 1800,
            "repair_audit_max_words": 3000,
        },
    )
    draft = SimpleNamespace(content_md="current draft", word_count=4030)

    still_blocked = await _heal_chapter_gate_state_before_repair(
        FakeSession(None),
        chapter,
        draft,
        project=SimpleNamespace(id="project-1"),
        settings=load_settings(env={}),
        identity_registry=[],
        language="zh-CN",
    )

    assert still_blocked is True
    assert chapter.production_state == "blocked"
    assert chapter.metadata_json["production_block_code"] == "CHAPTER_LENGTH_BLOCK_HIGH"
    assert chapter.metadata_json["write_safety_block_code"] == "CHAPTER_LENGTH_BLOCK_HIGH"
    assert chapter.metadata_json["quality_gate_block_source"] == "legacy_repair_audit"


@pytest.mark.asyncio
async def test_heal_chapter_gate_state_releases_resolved_legacy_repair_audit_length_block() -> None:
    chapter = SimpleNamespace(
        id="chapter-222",
        chapter_number=222,
        production_state="blocked",
        metadata_json={
            "blocked_by_repair_audit": "current_chapter_length_out_of_range",
            "repair_audit_min_words": 1800,
            "repair_audit_max_words": 3000,
        },
    )
    draft = SimpleNamespace(content_md="current draft", word_count=2788)

    still_blocked = await _heal_chapter_gate_state_before_repair(
        FakeSession(None),
        chapter,
        draft,
        project=SimpleNamespace(id="project-1"),
        settings=load_settings(env={}),
        identity_registry=[],
        language="zh-CN",
    )

    assert still_blocked is False
    assert chapter.production_state == "ok"
    assert "blocked_by_repair_audit" not in chapter.metadata_json
    assert chapter.metadata_json["resolved_quality_gate_block"]["resolved_by"] == (
        "repair_audit_length_revalidation"
    )


@pytest.mark.asyncio
async def test_heal_chapter_gate_state_normalizes_legacy_length_aliases() -> None:
    chapter = SimpleNamespace(
        id="chapter-458",
        chapter_number=458,
        production_state="blocked",
        metadata_json={"auto_repair_last_block_codes": ["LENGTH_OVER"]},
    )
    draft = SimpleNamespace(content_md="current draft", word_count=3301)

    still_blocked = await _heal_chapter_gate_state_before_repair(
        FakeSession(None),
        chapter,
        draft,
        project=SimpleNamespace(id="project-1"),
        settings=load_settings(env={}),
        identity_registry=[],
        language="zh-CN",
    )

    assert still_blocked is True
    assert chapter.production_state == "blocked"
    assert chapter.metadata_json["production_block_code"] == "CHAPTER_LENGTH_BLOCK_HIGH"
    assert chapter.metadata_json["quality_gate_block_code"] == "CHAPTER_LENGTH_BLOCK_HIGH"
