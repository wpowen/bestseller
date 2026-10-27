"""Pure optional UI custody: identical prose never selects a legacy unlock."""

from copy import deepcopy
import socket
from types import SimpleNamespace
from uuid import uuid4

import pytest

from bestseller.domain.fanqie_short import FanqieShortBeatSheet
from bestseller.domain.workflow import ChapterOutlineBatchInput
from bestseller.services.fanqie_short_plan_resume import _legacy_unlock
from bestseller.services.fanqie_short_planner import build_fanqie_segment_outline_batch

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Legacy UI custody tests forbid PG/provider/network")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)


def case():
    project = SimpleNamespace(
        id=uuid4(),
        target_chapters=4,
        target_word_count=8000,
        metadata_json={},
    )
    sheet = FanqieShortBeatSheet(
        beats=[
            {"segment_number": n, "beat_role": "observation", "purpose": f"Compare interval {n}."}
            for n in range(1, 5)
        ],
        unlock_milestone_segment=2,
    )
    alternate = sheet.model_copy(update={"unlock_milestone_segment": 4}, deep=True)
    candidate = build_fanqie_segment_outline_batch(project, sheet)
    other = build_fanqie_segment_outline_batch(project, alternate)
    # UI metadata differs; neither compiler may create different prose for it.
    assert ChapterOutlineBatchInput.model_validate(
        candidate
    ) == ChapterOutlineBatchInput.model_validate(other)
    artifacts = [
        SimpleNamespace(
            id=uuid4(),
            project_id=project.id,
            artifact_type="fanqie_beat_sheet",
            status="approved",
            scope_ref_id=None,
            content=s.model_dump(mode="json"),
            version_no=v,
        )
        for s, v in ((sheet, 1), (alternate, 99))
    ]
    foundation = {key: SimpleNamespace(content={}) for key in ("book_spec", "cast_spec")}
    return project, candidate, artifacts, foundation


@pytest.mark.parametrize("missing", ["meta", "projection", "hash", "unmatched_hash"])
def test_missing_or_unmatched_ui_custody_routes_all_chapters_without_approval(missing):
    project, candidate, artifacts, foundation = case()
    if missing == "meta":
        candidate.pop("_meta")
    elif missing == "projection":
        candidate["_meta"].pop("segment_projection")
    elif missing == "hash":
        candidate["_meta"]["segment_projection"].pop("typed_beat_sheet_sha256")
    else:
        candidate["_meta"]["segment_projection"]["typed_beat_sheet_sha256"] = "0" * 64
    before = deepcopy((candidate, [vars(a) for a in artifacts]))
    result = _legacy_unlock(project, list(reversed(artifacts)), candidate, foundation)
    assert result["legacy_beat_bound"] is False
    assert result["legacy_beat_artifact_ids"] == []
    assert result["unbound_unlock_routing"] == "all_existing_chapters"
    assert result["unlock_segment"] == project.target_chapters
    assert result["beat_paid_or_source_approval_asserted"] is False
    assert (candidate, [vars(a) for a in artifacts]) == before


def test_exact_typed_beat_hash_selects_only_ui_custody_without_latest_or_prose_grant():
    project, candidate, artifacts, foundation = case()
    before = deepcopy((candidate, [vars(a) for a in artifacts]))
    result = _legacy_unlock(project, list(reversed(artifacts)), candidate, foundation)
    assert result["legacy_beat_artifact_ids"] == [str(artifacts[0].id)]
    assert result["unlock_segment"] == 2 and result["legacy_beat_bound"] is True
    assert result["beat_paid_or_source_approval_asserted"] is False
    assert candidate["_meta"]["segment_projection"]["grade_asserted"] is False
    assert candidate["_meta"]["segment_projection"]["write_authorized"] is False
    assert (candidate, [vars(a) for a in artifacts]) == before
