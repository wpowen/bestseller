from __future__ import annotations

from types import SimpleNamespace

import pytest

from bestseller.services.book_lifecycle_evidence import (
    build_book_lifecycle_evidence_from_project_state,
)

pytestmark = pytest.mark.unit


def _character(
    name: str,
    *,
    gender: str = "male",
    pronoun: str = "他",
    rich: bool = True,
) -> SimpleNamespace:
    metadata = {
        "gender": gender,
        "pronoun_set_zh": pronoun,
    }
    if rich:
        metadata.update(
            {
                "ip_anchor": {"core_wound": "旧案"},
                "psych_profile": {"desire": "查清真相"},
                "independent_life": "有自己的生活压力",
            }
        )
    return SimpleNamespace(
        name=name,
        metadata_json=metadata,
        goal="查清真相" if rich else None,
        fear="失去证据" if rich else None,
        flaw="过度自责" if rich else None,
        strength="冷静" if rich else None,
    )


def test_lifecycle_evidence_collects_volume_and_character_metrics() -> None:
    project = SimpleNamespace(
        slug="book-a",
        target_chapters=100,
        metadata_json={
            "category_key": "suspense-mystery",
            "premium_volume_plan": [
                {
                    "chapter_range": "1-50",
                    "conflict_phase": "entry",
                    "primary_force_name": "镜债",
                    "core_payoff": "封门",
                    "reader_hook_to_next": "井口开账",
                },
                {
                    "chapter_range": "51-100",
                    "conflict_phase": "identity",
                    "primary_force_name": "镜影",
                    "core_payoff": "夺回青囊",
                    "reader_hook_to_next": "新债主出现",
                },
            ],
            "identity_manifest_status": "locked",
            "identity_manifest": [
                {"name": "林渊", "role": "protagonist", "pronoun_set_zh": "他"},
                {"name": "镜影林渊", "role": "antagonist", "pronoun_set_zh": "他"},
            ],
            "character_drama_map": {"version": 1},
            "premium_cast_spec": {"protagonist": {"name": "林渊"}},
        },
    )

    evidence = build_book_lifecycle_evidence_from_project_state(
        project,  # type: ignore[arg-type]
        [_character("林渊"), _character("镜影林渊")],  # type: ignore[list-item]
    )

    assert evidence["planning_report"]["planned_chapters"] == 100  # type: ignore[index]
    character_report = evidence["character_report"]  # type: ignore[index]
    assert character_report["identity_manifest_count"] == 2  # type: ignore[index]
    assert character_report["character_gate_report"]["passed"] is True  # type: ignore[index]


def test_lifecycle_evidence_ignores_aliases_that_collide_with_manifest_names() -> None:
    project = SimpleNamespace(
        slug="book-a",
        target_chapters=100,
        metadata_json={
            "identity_manifest_status": "locked",
            "identity_manifest": [
                {"name": "林正淳", "aliases": ["林正淳（镜影/声音）"]},
                {"name": "林正淳（镜影/声音）", "aliases": []},
                {"name": "镜影", "aliases": ["镜影林渊"]},
                {"name": "镜影林渊", "aliases": ["镜影"]},
                {"name": "镜影林渊", "aliases": ["伪林渊"]},
            ],
            "character_drama_map": {"version": 1},
            "premium_cast_spec": {"protagonist": {"name": "林渊"}},
        },
    )

    evidence = build_book_lifecycle_evidence_from_project_state(
        project,  # type: ignore[arg-type]
        [
            _character("林正淳"),
            _character("林正淳（镜影/声音）"),
            _character("镜影"),
            _character("镜影林渊"),
        ],  # type: ignore[list-item]
    )
    gate = evidence["character_report"]["character_gate_report"]  # type: ignore[index]

    assert gate["metrics"]["identity_manifest_duplicate_count"] == 0  # type: ignore[index]
    assert evidence["character_report"]["identity_manifest_count"] == 4  # type: ignore[index]
    assert gate["passed"] is True


def test_lifecycle_evidence_blocks_sparse_identity_rows() -> None:
    project = SimpleNamespace(
        slug="book-a",
        target_chapters=100,
        metadata_json={
            "identity_manifest_status": "locked",
            "identity_manifest": [{"name": "林渊", "role": "protagonist"}],
            "character_drama_map": {"version": 1},
            "premium_cast_spec": {"protagonist": {"name": "林渊"}},
        },
    )
    characters = [
        _character("林渊"),
        _character("张启", gender="", pronoun="", rich=False),
        _character("镜中复制体", gender="", pronoun="", rich=False),
    ]

    evidence = build_book_lifecycle_evidence_from_project_state(
        project,  # type: ignore[arg-type]
        characters,  # type: ignore[arg-type]
    )
    gate = evidence["character_report"]["character_gate_report"]  # type: ignore[index]

    assert gate["passed"] is False
    assert {
        finding["code"] for finding in gate["findings"]  # type: ignore[index]
    } == {
        "character_identity_coverage_below_bar",
        "character_personhood_coverage_below_bar",
    }
