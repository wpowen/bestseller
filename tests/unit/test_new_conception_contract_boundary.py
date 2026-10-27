from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from bestseller.domain.project import ProjectCreate
from bestseller.services import projects
from bestseller.services.concept_contract import (
    ConceptContractError,
    require_conception_contract_for_target,
    require_valid_concept_contract,
)


@pytest.mark.parametrize("chapters", [30, 35, 36])
@pytest.mark.parametrize("contract", [None, {}])
def test_new_ranking_length_book_requires_contract(chapters, contract):
    with pytest.raises(ConceptContractError, match=str(chapters)):
        require_conception_contract_for_target(contract, target_chapters=chapters)


def test_short_conception_and_unmarked_existing_projects_remain_compatible():
    assert require_conception_contract_for_target(None, target_chapters=29) is None
    assert require_valid_concept_contract({}, target_chapters=30) is None
    assert require_valid_concept_contract({}, target_chapters=100) is None


@pytest.mark.parametrize(
    "metadata",
    [
        {"concept_contract_version": "2"},
        {"concept_contract_required": True},
        {"concept_contract": {}},
    ],
)
async def test_declared_new_project_rejects_missing_contract_before_rows(monkeypatch, metadata):
    monkeypatch.setattr(projects, "get_project_by_slug", AsyncMock(return_value=None))
    monkeypatch.setattr(
        projects, "_find_duplicate_fanqie_short_project", AsyncMock(return_value=None)
    )
    # Any attempt beyond the contract boundary must fail this test explicitly.
    monkeypatch.setattr(
        projects,
        "clear_project_delete_tombstone",
        Mock(side_effect=AssertionError("passed creation gate")),
    )
    session = SimpleNamespace(add=Mock())
    payload = ProjectCreate(
        slug="boundary-fixture",
        title="Fixture",
        genre="现实",
        target_word_count=75000,
        target_chapters=30,
        metadata=metadata,
    )
    with pytest.raises(ConceptContractError):
        await projects.create_project(session, payload, None)
    session.add.assert_not_called()


def test_web_rechecks_missing_conception_result_before_legacy_metadata():
    from pathlib import Path
    import textwrap

    source = Path("src/bestseller/web/server.py").read_text()
    marker = '                project_metadata: dict[str, object] = {"premise": effective_premise}'
    end = source.index(marker)
    start = source.rindex("                if run_conception:", 0, end)
    branch = textwrap.dedent(source[start:end])
    namespace = {
        "run_conception": True,
        "conception_contract": None,
        "payload": {"target_chapters": 30},
    }
    with pytest.raises(ConceptContractError):
        exec(branch, namespace)
    # This boundary must not rewrite an existing legacy resume's contract.
    namespace["run_conception"] = False
    exec(branch, namespace)
    assert namespace["conception_contract"] is None
