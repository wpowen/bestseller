from __future__ import annotations

# ruff: noqa: RUF001 -- Chinese punctuation is part of the name-boundary fixtures.
from types import SimpleNamespace

import pytest

from bestseller.services.book_design import (
    _protagonist_name_from_text,
    ensure_project_book_design_snapshot,
)

pytestmark = pytest.mark.unit


def _project(metadata: dict) -> SimpleNamespace:
    return SimpleNamespace(
        genre="玄幻",
        target_chapters=50,
        target_word_count=130_000,
        metadata_json=metadata,
    )


def _metadata(*, protagonist: str, source: str = "original_premise") -> dict:
    return {
        "creation_protagonist_name": protagonist,
        "creation_protagonist_source": source,
        "premise": "野符摊学徒纪年揣借来的四两银子盘下半间铺面。",
        "story_spine": {"who": protagonist, "why_now": "月底前必须开张。"},
        "identity_manifest": [{"name": protagonist, "role": "protagonist"}],
        "genre_intent_contract": {
            "genre_key": "xuanhuan",
            "genre_label": "玄幻",
            "channel_key": "male",
            "tone_preference": "light",
        },
    }


def test_occupation_boundary_does_not_absorb_verb_phrase() -> None:
    premise = "野符摊学徒纪年揣借来的四两银子盘下半间铺面。"
    assert _protagonist_name_from_text(premise) == ""


def test_explicit_four_character_name_with_punctuation_is_preserved() -> None:
    assert _protagonist_name_from_text("矿工欧阳明日，守着最后一座矿井。") == "欧阳明日"


def test_original_premise_lock_repairs_only_with_manifest_and_spine_support() -> None:
    project = _project(_metadata(protagonist="纪年揣借"))
    stale = ensure_project_book_design_snapshot(project)
    assert stale.protagonist.name == "纪年揣借"

    project.metadata_json["identity_manifest"] = [{"name": "纪年", "role": "protagonist"}]
    project.metadata_json["story_spine"] = {
        "who": "纪年揣着借来的四两银子盘下铺面",
        "why_now": "月底前一天，纪年站在正街铺面前。",
    }

    repaired = ensure_project_book_design_snapshot(project)

    assert repaired.protagonist.name == "纪年"
    assert project.metadata_json["book_design_snapshot_superseded"][-1][
        "replacement_name"
    ] == "纪年"
    assert project.metadata_json["creation_protagonist_name"] == "纪年"
    assert project.metadata_json["creation_protagonist_source"] == "legacy_boundary_repair"

    again = ensure_project_book_design_snapshot(project)
    assert again.snapshot_id == repaired.snapshot_id
    assert project.metadata_json["creation_protagonist_name"] == "纪年"


def test_original_premise_lock_is_not_repaired_from_one_source() -> None:
    project = _project(_metadata(protagonist="纪年揣借"))
    stale = ensure_project_book_design_snapshot(project)
    project.metadata_json["creation_protagonist_name"] = "纪年"
    project.metadata_json["identity_manifest"] = [{"name": "纪年", "role": "protagonist"}]
    project.metadata_json["story_spine"] = {"who": "只在这里出现的纪年"}

    unchanged = ensure_project_book_design_snapshot(project)

    assert unchanged.snapshot_id == stale.snapshot_id
    assert "book_design_snapshot_superseded" not in project.metadata_json


def test_stale_only_story_occurrences_do_not_count_as_support() -> None:
    project = _project(_metadata(protagonist="纪年揣借"))
    stale = ensure_project_book_design_snapshot(project)
    project.metadata_json["identity_manifest"] = [{"name": "纪年", "role": "protagonist"}]
    project.metadata_json["story_spine"] = {
        "who": "纪年揣借来的四两银子。",
        "why_now": "月底前必须处理纪年揣借来的债。",
    }

    unchanged = ensure_project_book_design_snapshot(project)

    assert unchanged.snapshot_id == stale.snapshot_id


def test_user_name_cannot_be_overwritten_by_boundary_repair() -> None:
    project = _project(_metadata(protagonist="纪年揣借", source="user"))
    stale = ensure_project_book_design_snapshot(project)
    project.metadata_json["identity_manifest"] = [{"name": "纪年", "role": "protagonist"}]
    project.metadata_json["story_spine"] = {
        "who": "纪年揣借来的铺面，纪年必须开张。",
        "why_now": "纪年月底前必须开张。",
    }

    unchanged = ensure_project_book_design_snapshot(project)

    assert unchanged.snapshot_id == stale.snapshot_id


def test_two_manifest_protagonist_names_hard_block_repair() -> None:
    project = _project(_metadata(protagonist="纪年揣借"))
    stale = ensure_project_book_design_snapshot(project)
    project.metadata_json["creation_protagonist_name"] = "纪年"
    project.metadata_json["identity_manifest"] = [
        {"name": "纪年", "role": "protagonist"},
        {"name": "孙砚", "role": "protagonist"},
    ]
    project.metadata_json["story_spine"] = {"who": "纪年与孙砚共同开张。"}

    unchanged = ensure_project_book_design_snapshot(project)

    assert unchanged.snapshot_id == stale.snapshot_id


def test_unrelated_manifest_protagonist_vetoes_even_with_two_short_name_fields() -> None:
    project = _project(_metadata(protagonist="纪年揣借"))
    stale = ensure_project_book_design_snapshot(project)
    project.metadata_json["identity_manifest"] = [
        {"name": "纪年", "role": "protagonist"},
        {"name": "孙砚", "role": "protagonist"},
    ]
    project.metadata_json["story_spine"] = {
        "who": "纪年站在铺面前。",
        "why_now": "纪年必须在月底开张。",
    }

    unchanged = ensure_project_book_design_snapshot(project)

    assert unchanged.snapshot_id == stale.snapshot_id


def test_punctuation_delimited_four_character_name_wins_over_short_prefix() -> None:
    project = _project(_metadata(protagonist="欧阳"))
    project.metadata_json["premise"] = "矿工欧阳明日，守着最后一座矿井。"
    stale = ensure_project_book_design_snapshot(project)
    assert stale.protagonist.name == "欧阳"

    repaired = ensure_project_book_design_snapshot(project)

    assert repaired.protagonist.name == "欧阳明日"


def test_valid_locked_four_character_name_is_never_shortened() -> None:
    project = _project(_metadata(protagonist="欧阳明日"))
    project.metadata_json["premise"] = "矿工欧阳明日，守着最后一座矿井。"
    stale = ensure_project_book_design_snapshot(project)
    project.metadata_json["identity_manifest"] = [{"name": "欧阳", "role": "protagonist"}]
    project.metadata_json["story_spine"] = {"who": "欧阳守矿。", "why_now": "欧阳必须护住矿井。"}
    assert ensure_project_book_design_snapshot(project).snapshot_id == stale.snapshot_id


def test_boundary_repaired_identity_mismatch_remains_blocking() -> None:
    from bestseller.services.book_design import validate_project_book_design

    project = _project(_metadata(protagonist="纪年", source="legacy_boundary_repair"))
    ensure_project_book_design_snapshot(project)
    project.metadata_json["identity_manifest"] = [{"name": "孙砚", "role": "protagonist"}]
    assert validate_project_book_design(project).blocks_production


@pytest.mark.parametrize("explicit_name_boundary", [False, True])
def test_audited_partial_migration_rechecks_evidence_and_syncs_cache(
    explicit_name_boundary: bool,
) -> None:
    project = _project(_metadata(protagonist="纪年揣借"))
    ensure_project_book_design_snapshot(project)
    project.metadata_json["identity_manifest"] = [{"name": "纪年", "role": "protagonist"}]
    project.metadata_json["story_spine"] = {"who": "纪年站在铺面前。", "why_now": "纪年必须开张。"}
    repaired = ensure_project_book_design_snapshot(project)
    project.metadata_json["creation_protagonist_name"] = "纪年揣借"
    project.metadata_json["creation_protagonist_source"] = "original_premise"
    if explicit_name_boundary:
        project.metadata_json["premise"] = "野符摊学徒纪年，带着纪年揣借来的银子开张。"
    assert ensure_project_book_design_snapshot(project).snapshot_id == repaired.snapshot_id
    assert project.metadata_json["creation_protagonist_name"] == "纪年"
    assert project.metadata_json["creation_protagonist_source"] == "legacy_boundary_repair"
