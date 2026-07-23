from __future__ import annotations

from pydantic import ValidationError
import pytest

from bestseller.domain.enums import (
    ConceptionMode,
    IntentDiffSeverity,
    IntentFieldSource,
)
from bestseller.services.creation_intent_contract import (
    ConceptionAttemptInput,
    CreationIntentContract,
    build_creation_intent_contract,
    diff_creation_intents,
)
from bestseller.services.genre_intent_contract import contract_from_selection

pytestmark = pytest.mark.unit


def _contract(**values: object) -> CreationIntentContract:
    return build_creation_intent_contract(
        contract_from_selection({"channel": "male", "genre": "xianxia"}),
        field_sources={"chapter_count": IntentFieldSource.EXPLICIT},
        **values,
    )


def test_creation_contract_composes_genre_contract_and_tracks_sources() -> None:
    contract = _contract(chapter_count=120, audience_orientation="male")

    assert contract.genre_key == "xianxia"
    assert contract.prompt_pack_key == "xianxia-upgrade-core"
    assert contract.source_for("chapter_count") is IntentFieldSource.EXPLICIT
    assert contract.source_for("genre_intent") is IntentFieldSource.EXPLICIT
    assert contract.source_for("language") is IntentFieldSource.DEFAULT
    assert len(contract.contract_hash()) == 64


def test_creation_contract_is_frozen_at_model_boundary() -> None:
    contract = _contract()

    with pytest.raises(ValidationError):
        contract.chapter_count = 10  # type: ignore[misc]
    with pytest.raises(TypeError):
        contract.field_sources["chapter_count"] = IntentFieldSource.LEGACY  # type: ignore[index]


def test_revision_requires_complete_v1_reference() -> None:
    with pytest.raises(ValidationError, match="reason"):
        ConceptionAttemptInput(
            conception_mode=ConceptionMode.REVISION,
            contract=_contract(),
            attempt_id="attempt-2",
            idempotency_key="intent-v2",
        )

    attempt = ConceptionAttemptInput(
        conception_mode=ConceptionMode.REVISION,
        contract=_contract(),
        reason="扩写第一卷并增加支线",
        base_snapshot_id="snapshot-v1",
        attempt_id="attempt-2",
        idempotency_key="intent-v2",
        input_payload={"premise": "完整 V1 输入"},
    )
    assert attempt.input_hash() == attempt.input_hash()


def test_diff_marks_identity_and_taxonomy_changes_hard_but_additive_options_soft() -> None:
    old = _contract(chapter_count=50)
    new = old.model_copy(
        update={
            "genre_intent": contract_from_selection({"channel": "male", "genre": "xuanhuan"}),
            "chapter_count": 80,
        }
    )

    diff = diff_creation_intents(old, new)
    assert diff.has_hard_conflicts
    assert any(
        item.path == "genre_intent.genre_key"
        and item.severity is IntentDiffSeverity.HARD
        for item in diff.items
    )
    assert any(
        item.path == "chapter_count"
        and item.severity is IntentDiffSeverity.SOFT
        for item in diff.items
    )
