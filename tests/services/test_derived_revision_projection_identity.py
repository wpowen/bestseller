from __future__ import annotations

import pytest

from bestseller.services.derived_revision_projection import (
    DerivedProjectionError,
    _recover_cast_from_approved_sources,
    _source_name_evidence,
    _validate_projection_cast_identity,
)

pytestmark = pytest.mark.unit


def test_projection_identity_gate_rejects_same_person_in_two_roles() -> None:
    with pytest.raises(
        DerivedProjectionError,
        match="DERIVED_PROJECTION_CAST_IDENTITY_COLLISION",
    ):
        _validate_projection_cast_identity(
            {
                "protagonist": {"name": "纪年", "role": "protagonist"},
                "antagonist": {"name": "纪年", "role": "antagonist"},
            },
            expected_protagonist="纪年",
        )


def test_recovery_source_evidence_ignores_control_and_metadata_text() -> None:
    evidence = _source_name_evidence(
        "孙砚",
        premise="",
        book={"_meta": {"history": "孙砚"}, "control": {"note": "孙砚"}},
        world={"metadata": {"note": "孙砚"}, "rules": {"text": "纪年"}},
    )

    assert evidence == []


def test_recovery_keeps_compiled_slots_and_audits_legacy_description_without_import() -> None:
    compiled = {
        "protagonist": {"name": "纪年", "role": "protagonist"},
        "antagonist": None,
        "supporting_cast": [],
    }
    legacy = {
        "protagonist": {
            "name": "纪年",
            "role": "protagonist",
            "goal": "保留主角已有目标",
        },
        "antagonist": {
            "name": "纪年",
            "role": "antagonist",
            "goal": "这条身份与主角冲突，不能恢复",
        },
        "supporting_cast": [
            {
                "name": "孙砚",
                "role": "rival",
                "goal": "保留已经批准的县衙把柄簿压力",
            }
        ],
    }

    recovered, audit = _recover_cast_from_approved_sources(
        compiled,
        legacy,
        premise="野符摊学徒纪年要在县衙对质，孙砚掌着把柄簿。",
        book={"protagonist": {"name": "纪年"}, "stakes": {"antagonist": "孙砚"}},
        world={"factions": [{"name": "县衙"}]},
        legacy_artifact_id="legacy-cast-1",
    )

    assert recovered["protagonist"]["name"] == "纪年"
    assert recovered["antagonist"] is None
    assert recovered["supporting_cast"] == []
    assert audit["legacy_cast_artifact_id"] == "legacy-cast-1"
    assert audit["promotion_status"] == "needs_independent_source_review"
    assert any(
        row["name"] == "孙砚"
        and row["decision"] == "legacy_preserved_unverified"
        and row["field_diff"]["applied"] is False
        for row in audit["entries"]
    )
    assert any(row["name"] == "纪年" and row["decision"] == "candidate_only_requires_source_review" for row in audit["entries"])


def test_recovery_does_not_map_legacy_protagonist_description_to_same_named_antagonist() -> None:
    compiled = {
        "protagonist": {"name": "纪年", "role": "protagonist"},
        "antagonist": {
            "name": "孙砚",
            "role": "antagonist",
            "goal": "权威反派目标",
        },
        "supporting_cast": [],
    }
    legacy = {
        "protagonist": {
            "name": "孙砚",
            "role": "protagonist",
            "goal": "旧主角描述，不能迁移给反派",
        },
        "antagonist": None,
        "supporting_cast": [],
    }

    recovered, audit = _recover_cast_from_approved_sources(
        compiled,
        legacy,
        premise="纪年在县衙对质，孙砚负责收账。",
        book={"protagonist": {"name": "纪年"}},
        world={"factions": [{"name": "县衙"}]},
        legacy_artifact_id="legacy-cast-role-mismatch",
    )

    assert recovered["antagonist"]["goal"] == "权威反派目标"
    row = audit["entries"][0]
    assert row["reason"] == "legacy_role_conflicts_with_authoritative_role"
